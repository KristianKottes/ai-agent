use reqwest::header::{AUTHORIZATION, CONTENT_TYPE};
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, State};
use uuid::Uuid;

mod platform;

struct BackendState {
    base_url: String,
    token: String,
    child: Mutex<Option<Child>>,
}

impl Drop for BackendState {
    fn drop(&mut self) {
        if let Ok(child) = self.child.get_mut() {
            if let Some(process) = child.as_mut() {
                if process.try_wait().ok().flatten().is_none() {
                    request_backend_shutdown(&self.base_url, &self.token);
                    let deadline = Instant::now() + Duration::from_secs(5);
                    while Instant::now() < deadline {
                        match process.try_wait() {
                            Ok(Some(_)) => return,
                            Ok(None) => thread::sleep(Duration::from_millis(100)),
                            Err(_) => break,
                        }
                    }
                    let _ = process.kill();
                    let _ = process.wait();
                }
            }
        }
    }
}

fn request_backend_shutdown(base_url: &str, token: &str) {
    let Some(authority) = base_url.strip_prefix("http://") else {
        return;
    };
    let Ok(address) = authority.parse::<SocketAddr>() else {
        return;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(500)) else {
        return;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
    let request = format!(
        "POST /api/internal/shutdown HTTP/1.1\r\nHost: {authority}\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}"
    );
    if stream.write_all(request.as_bytes()).is_ok() {
        let mut response = [0_u8; 256];
        let _ = stream.read(&mut response);
    }
}

#[derive(Debug, Deserialize, Serialize)]
struct Health {
    status: String,
    backend_version: String,
    schema_version: i64,
}

#[derive(Debug, Deserialize, Serialize)]
struct Project {
    id: String,
    title: String,
    created_at: String,
    script_id: Option<String>,
    topic_id: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
struct AppSettings {
    data_directory: String,
    cache_directory: String,
    config_directory: String,
    logs_directory: String,
    temp_directory: String,
    backend_version: String,
    schema_version: i64,
    authentication_enabled: bool,
}

#[derive(Debug, Deserialize, Serialize)]
struct ProjectCreate {
    title: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct LicenseAcceptance {
    accepted_license: bool,
}

#[derive(Debug, Deserialize, Serialize)]
struct ComponentRemoval {
    confirmation: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct RuntimeCleanup {
    confirmation: String,
}

fn project_root() -> Result<PathBuf, String> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .map_err(|error| format!("Could not locate the development project root: {error}"))
}

fn launch_backend(app: &tauri::App) -> Result<BackendState, String> {
    let resource_dir = app.path().resource_dir()
        .map_err(|error| format!("Could not locate the application's runtime resources: {error}"))?;
    let packaged = platform::bundled_runtime(&resource_dir)?;
    let (root, python) = if let Some(runtime) = &packaged {
        (runtime.backend.clone(), runtime.python.clone())
    } else {
        let root = project_root()?;
        let python = platform::python_executable(&root)?;
        (root, python)
    };
    let dirs = platform::application_directories(app)?;
    platform::prepare_application_directories(&dirs)?;
    let log_path = dirs.logs.join("backend.log");
    let log_file = File::create(&log_path)
        .map_err(|error| format!("Could not open backend log: {error}"))?;
    platform::restrict_permissions(&log_path, 0o600)?;

    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|error| format!("Could not allocate a local backend port: {error}"))?;
    let port = listener.local_addr().map_err(|error| error.to_string())?.port();
    drop(listener);
    let base_url = format!("http://127.0.0.1:{port}");
    let token = Uuid::new_v4().simple().to_string();
    let mut command = Command::new(python);
    if let Some(runtime) = packaged {
        command
            .args(["-B", "-E", "-s"])
            .env_remove("PYTHONHOME")
            .env_remove("PYTHONPATH")
            .env_remove("AI_BLOGGER_ZONOS_BIN_DIR")
            .env("PYTHONNOUSERSITE", "1")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("AI_BLOGGER_MEDIA_BIN_DIR", runtime.media_bin)
            .env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin");
    }
    command
        .args([
            "-m",
            "backend.runner",
            "--shutdown-on-stdin-close",
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
        ])
        .current_dir(&root)
        .env("AI_BLOGGER_DATA_DIR", &dirs.data)
        .env("AI_BLOGGER_CACHE_DIR", &dirs.cache)
        .env("AI_BLOGGER_CONFIG_DIR", &dirs.config)
        .env("AI_BLOGGER_LOG_DIR", &dirs.logs)
        .env("AI_BLOGGER_TEMP_DIR", &dirs.temp)
        .env("AI_BLOGGER_LOCAL_TOKEN", &token)
        .stdin(Stdio::piped())
        .stdout(Stdio::from(log_file.try_clone().map_err(|error| error.to_string())?))
        .stderr(Stdio::from(log_file));
    platform::configure_background_process(&mut command);
    let mut child = command.spawn()
        .map_err(|error| format!("Could not start the FastAPI backend: {error}"))?;

    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(700))
        .build()
        .map_err(|error| format!("Could not initialize local API client: {error}"))?;
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut ready = false;
    while Instant::now() < deadline {
        if let Ok(response) = client
            .get(format!("{base_url}/api/health"))
            .header(AUTHORIZATION, format!("Bearer {token}"))
            .send()
        {
            if response.status().is_success() {
                ready = true;
                break;
            }
        }
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("FastAPI backend exited during startup ({status}). See the application backend log."));
        }
        thread::sleep(Duration::from_millis(150));
    }
    if !ready {
        let _ = child.kill();
        let _ = child.wait();
        return Err("FastAPI backend did not become ready within 15 seconds. See the application backend log.".to_string());
    }

    Ok(BackendState {
        base_url,
        token,
        child: Mutex::new(Some(child)),
    })
}

async fn get_json<T: for<'de> Deserialize<'de>>(state: &BackendState, path: &str) -> Result<T, String> {
    let response = reqwest::Client::new()
        .get(format!("{}{path}", state.base_url))
        .header(AUTHORIZATION, format!("Bearer {}", state.token))
        .send()
        .await
        .map_err(|error| format!("Local backend request failed: {error}"))?;
    let response = checked_response(response).await?;
    response.json::<T>().await.map_err(|error| format!("Invalid local backend response: {error}"))
}

async fn checked_response(response: reqwest::Response) -> Result<reqwest::Response, String> {
    if response.status().is_success() {
        return Ok(response);
    }
    let status = response.status();
    let body = response.text().await.unwrap_or_default();
    let detail = serde_json::from_str::<serde_json::Value>(&body)
        .ok()
        .and_then(|value| value.get("detail").and_then(serde_json::Value::as_str).map(str::to_string))
        .unwrap_or_else(|| "The local backend rejected the request.".to_string());
    Err(format!("Local backend returned HTTP {status}: {detail}"))
}

#[tauri::command]
async fn backend_health(state: State<'_, BackendState>) -> Result<Health, String> {
    get_json(&state, "/api/health").await
}

#[tauri::command]
async fn list_projects(state: State<'_, BackendState>) -> Result<Vec<Project>, String> {
    get_json(&state, "/api/projects").await
}

#[tauri::command]
async fn get_saved_script(state: State<'_, BackendState>, script_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    get_json(&state, &format!("/api/scripts/{script_id}")).await
}

#[tauri::command]
async fn link_project_script(state: State<'_, BackendState>, project_id: String, script_id: String) -> Result<Project, String> {
    validate_record_id(&project_id)?;
    validate_record_id(&script_id)?;
    send_json(&state, reqwest::Method::PUT, &format!("/api/projects/{project_id}/script"),
              &serde_json::json!({ "script_id": script_id })).await
}

#[tauri::command]
async fn get_settings(state: State<'_, BackendState>) -> Result<AppSettings, String> {
    get_json(&state, "/api/settings").await
}

#[tauri::command]
async fn get_system_report(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    get_json(&state, "/api/system").await
}

#[tauri::command]
async fn get_runtime_snapshot(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    get_json(&state, "/api/runtime").await
}

#[tauri::command]
async fn get_local_model_status(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    get_json(&state, "/api/ai/model").await
}

#[tauri::command]
async fn install_local_model(state: State<'_, BackendState>, accepted_license: bool) -> Result<serde_json::Value, String> {
    post_json(
        &state,
        "/api/ai/model/install",
        &serde_json::json!({ "accepted_license": accepted_license }),
    )
    .await
}

#[tauri::command]
async fn cancel_local_model_install(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    post_json::<serde_json::Value, serde_json::Value>(&state, "/api/ai/model/cancel", &serde_json::json!({})).await
}

#[tauri::command]
async fn list_topics(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    get_json(&state, "/api/topics").await
}

#[tauri::command]
async fn generate_topics(
    state: State<'_, BackendState>,
    count: u32,
    category: Option<String>,
) -> Result<serde_json::Value, String> {
    post_json(
        &state,
        "/api/topics/generate",
        &serde_json::json!({ "count": count, "category": category }),
    )
    .await
}

fn validate_record_id(record_id: &str) -> Result<(), String> {
    Uuid::parse_str(record_id).map(|_| ()).map_err(|_| "Invalid local record identifier".to_string())
}

#[tauri::command]
async fn update_topic(
    state: State<'_, BackendState>,
    topic_id: String,
    is_favorite: Option<bool>,
    is_used: Option<bool>,
) -> Result<serde_json::Value, String> {
    validate_record_id(&topic_id)?;
    patch_json(
        &state,
        &format!("/api/topics/{topic_id}"),
        &serde_json::json!({ "is_favorite": is_favorite, "is_used": is_used }),
    )
    .await
}

#[tauri::command]
async fn regenerate_topic(state: State<'_, BackendState>, topic_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&topic_id)?;
    post_json::<serde_json::Value, serde_json::Value>(
        &state,
        &format!("/api/topics/{topic_id}/regenerate"),
        &serde_json::json!({}),
    )
    .await
}

#[tauri::command]
async fn generate_script(
    state: State<'_, BackendState>,
    topic_id: String,
    duration_seconds: u32,
) -> Result<serde_json::Value, String> {
    validate_record_id(&topic_id)?;
    post_json(
        &state,
        &format!("/api/topics/{topic_id}/scripts"),
        &serde_json::json!({ "duration_seconds": duration_seconds }),
    )
    .await
}

#[tauri::command]
async fn get_latest_script_for_topic(state: State<'_, BackendState>, topic_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&topic_id)?;
    get_json(&state, &format!("/api/topics/{topic_id}/scripts/latest")).await
}

#[tauri::command]
async fn edit_script(
    state: State<'_, BackendState>,
    script_id: String,
    full_narration: String,
) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    patch_json(
        &state,
        &format!("/api/scripts/{script_id}"),
        &serde_json::json!({ "full_narration": full_narration }),
    )
    .await
}

#[tauri::command]
async fn generate_director(state: State<'_, BackendState>, script_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    post_json::<serde_json::Value, serde_json::Value>(
        &state,
        &format!("/api/scripts/{script_id}/director"),
        &serde_json::json!({}),
    )
    .await
}

#[tauri::command]
async fn get_director_plan(state: State<'_, BackendState>, script_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    get_json(&state, &format!("/api/scripts/{script_id}/director")).await
}

#[tauri::command]
async fn get_voice_status(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    get_json(&state, "/api/voice").await
}

#[tauri::command]
async fn install_voice(state: State<'_, BackendState>, accepted_license: bool) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/voice/install", &serde_json::json!({ "accepted_license": accepted_license })).await
}

#[tauri::command]
async fn cancel_voice_install(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/voice/install/cancel", &serde_json::json!({})).await
}

#[derive(Deserialize, Serialize)]
struct VoiceInput {
    voice_id: String,
    speed: f64,
}

#[tauri::command]
async fn render_narration(state: State<'_, BackendState>, script_id: String, input: VoiceInput) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    post_json(&state, &format!("/api/scripts/{script_id}/narration"), &input).await
}

async fn voice_audio(state: &BackendState, path: &str, input: Option<&VoiceInput>) -> Result<tauri::ipc::Response, String> {
    let client = reqwest::Client::new();
    let url = format!("{}{path}", state.base_url);
    let request = match input {
        Some(input) => client.post(url).json(input),
        None => client.get(url),
    };
    let response = request.bearer_auth(&state.token).send().await
        .map_err(|error| format!("Local audio request failed: {error}"))?;
    let bytes = checked_response(response).await?.bytes().await
        .map_err(|error| format!("Could not read local audio: {error}"))?;
    Ok(tauri::ipc::Response::new(bytes.to_vec()))
}

#[tauri::command]
async fn preview_voice(state: State<'_, BackendState>, input: VoiceInput) -> Result<tauri::ipc::Response, String> {
    voice_audio(&state, "/api/voice/preview", Some(&input)).await
}

#[tauri::command]
async fn get_narration_audio(state: State<'_, BackendState>, narration_id: String) -> Result<tauri::ipc::Response, String> {
    validate_record_id(&narration_id)?;
    voice_audio(&state, &format!("/api/voice/narration/{narration_id}"), None).await
}

#[tauri::command]
async fn get_latest_narration(state: State<'_, BackendState>, script_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    get_json(&state, &format!("/api/scripts/{script_id}/narration/latest")).await
}

#[tauri::command]
async fn get_avatar_library(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    get_json(&state, "/api/avatar").await
}

#[tauri::command]
async fn preview_avatar(state: State<'_, BackendState>, input: serde_json::Value) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/avatar/preview", &input).await
}

#[tauri::command]
async fn save_avatar_preset(state: State<'_, BackendState>, input: serde_json::Value) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/avatar/presets", &input).await
}

#[tauri::command]
async fn render_avatar(state: State<'_, BackendState>, input: serde_json::Value) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/avatar/renders", &input).await
}

#[tauri::command]
async fn get_latest_avatar(state: State<'_, BackendState>, narration_id: String, engine: Option<String>) -> Result<serde_json::Value, String> {
    validate_record_id(&narration_id)?;
    let engine = match engine.as_deref() {
        Some("musetalk") => "musetalk",
        None | Some("illustrated") => "illustrated",
        _ => return Err("Unknown avatar engine.".to_string()),
    };
    get_json(&state, &format!("/api/avatar/renders/latest/{narration_id}?engine={engine}")).await
}

#[tauri::command]
async fn portrait_model_status(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    get_json(&state, "/api/avatar/portrait-model").await
}

#[tauri::command]
async fn install_portrait_model(state: State<'_, BackendState>, accepted_license: bool) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/avatar/portrait-model/install", &serde_json::json!({ "accepted_license": accepted_license })).await
}

#[tauri::command]
async fn cancel_portrait_model(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/avatar/portrait-model/cancel", &serde_json::json!({})).await
}

#[tauri::command]
async fn list_portraits(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    get_json(&state, "/api/avatar/portraits").await
}

#[tauri::command]
async fn get_portrait(state: State<'_, BackendState>, portrait_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&portrait_id)?;
    get_json(&state, &format!("/api/avatar/portraits/{portrait_id}")).await
}

#[tauri::command]
async fn import_portrait(state: State<'_, BackendState>, input: serde_json::Value) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/avatar/portraits", &input).await
}

#[tauri::command]
async fn save_portrait_crop(state: State<'_, BackendState>, portrait_id: String, input: serde_json::Value) -> Result<serde_json::Value, String> {
    validate_record_id(&portrait_id)?;
    patch_json(&state, &format!("/api/avatar/portraits/{portrait_id}"), &input).await
}

#[tauri::command]
async fn render_portrait(state: State<'_, BackendState>, input: serde_json::Value) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/avatar/portrait-renders", &input).await
}

#[tauri::command]
async fn get_avatar_video(state: State<'_, BackendState>, render_id: String) -> Result<tauri::ipc::Response, String> {
    validate_record_id(&render_id)?;
    let response = reqwest::Client::new()
        .get(format!("{}/api/avatar/renders/{render_id}", state.base_url))
        .bearer_auth(&state.token)
        .send().await.map_err(|error| format!("Local video request failed: {error}"))?;
    let bytes = checked_response(response).await?.bytes().await
        .map_err(|error| format!("Could not read local video: {error}"))?;
    Ok(tauri::ipc::Response::new(bytes.to_vec()))
}

#[tauri::command]
async fn latest_project_video(state: State<'_, BackendState>, project_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&project_id)?;
    get_json(&state, &format!("/api/projects/{project_id}/video-jobs/latest")).await
}

#[tauri::command]
async fn start_project_video(
    state: State<'_, BackendState>, project_id: String, input: serde_json::Value,
) -> Result<serde_json::Value, String> {
    validate_record_id(&project_id)?;
    post_json(&state, &format!("/api/projects/{project_id}/video-jobs"), &input).await
}

#[tauri::command]
async fn resume_project_video(
    state: State<'_, BackendState>, project_id: String, job_id: String, input: serde_json::Value,
) -> Result<serde_json::Value, String> {
    validate_record_id(&project_id)?;
    validate_record_id(&job_id)?;
    post_json(&state, &format!("/api/projects/{project_id}/video-jobs/{job_id}/resume"), &input).await
}

#[tauri::command]
async fn cancel_project_video(
    state: State<'_, BackendState>, project_id: String, job_id: String,
) -> Result<serde_json::Value, String> {
    validate_record_id(&project_id)?;
    validate_record_id(&job_id)?;
    post_json(&state, &format!("/api/projects/{project_id}/video-jobs/{job_id}/cancel"), &serde_json::json!({})).await
}

#[tauri::command]
async fn list_scene_assets(state: State<'_, BackendState>, script_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    get_json(&state, &format!("/api/scripts/{script_id}/scene-assets")).await
}

#[tauri::command]
async fn get_scene_models(state: State<'_, BackendState>) -> Result<serde_json::Value, String> {
    get_json(&state, "/api/scene-models").await
}

#[tauri::command]
async fn latest_scene_job(state: State<'_, BackendState>, script_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    get_json(&state, &format!("/api/scripts/{script_id}/scene-jobs/latest")).await
}

#[tauri::command]
async fn start_scene_job(state: State<'_, BackendState>, script_id: String, input: serde_json::Value) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    post_json(&state, &format!("/api/scripts/{script_id}/scene-jobs"), &input).await
}

#[tauri::command]
async fn cancel_scene_job(state: State<'_, BackendState>, script_id: String, job_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    validate_record_id(&job_id)?;
    post_json(&state, &format!("/api/scripts/{script_id}/scene-jobs/{job_id}/cancel"), &serde_json::json!({})).await
}

#[tauri::command]
async fn import_scene_asset(
    state: State<'_, BackendState>, script_id: String, input: serde_json::Value,
) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    post_json(&state, &format!("/api/scripts/{script_id}/scene-assets"), &input).await
}

#[tauri::command]
async fn list_video_exports(state: State<'_, BackendState>, script_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    get_json(&state, &format!("/api/scripts/{script_id}/exports")).await
}

#[tauri::command]
async fn load_export_draft(state: State<'_, BackendState>, script_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    get_json(&state, &format!("/api/scripts/{script_id}/export-draft")).await
}

#[tauri::command]
async fn list_background_music(state: State<'_, BackendState>, script_id: String) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    get_json(&state, &format!("/api/scripts/{script_id}/background-music")).await
}

#[tauri::command]
async fn import_background_music(state: State<'_, BackendState>, script_id: String, input: serde_json::Value) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    post_json(&state, &format!("/api/scripts/{script_id}/background-music"), &input).await
}

#[tauri::command]
async fn save_export_draft(
    state: State<'_, BackendState>, script_id: String, input: serde_json::Value,
) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    send_json(&state, reqwest::Method::PUT, &format!("/api/scripts/{script_id}/export-draft"), &input).await
}

#[tauri::command]
async fn render_video_export(
    state: State<'_, BackendState>, script_id: String, input: serde_json::Value,
) -> Result<serde_json::Value, String> {
    validate_record_id(&script_id)?;
    post_json(&state, &format!("/api/scripts/{script_id}/exports"), &input).await
}

#[derive(Deserialize)]
struct ExportPreview {
    path: PathBuf,
}

#[tauri::command]
async fn video_export_preview(
    app: tauri::AppHandle, state: State<'_, BackendState>, export_id: String,
) -> Result<String, String> {
    validate_record_id(&export_id)?;
    let record: ExportPreview = get_json(&state, &format!("/api/exports/{export_id}/preview")).await?;
    let root = app.path().app_data_dir().map_err(|error| error.to_string())?;
    let root = root.canonicalize().map_err(|error| error.to_string())?;
    let expected = root.join("exports").join(format!("{export_id}.mp4"));
    let path = record.path.canonicalize().map_err(|error| error.to_string())?;
    if path != expected || !path.is_file() {
        return Err("The video file is outside the app's export folder.".into());
    }
    app.asset_protocol_scope().allow_file(&path).map_err(|error| error.to_string())?;
    path.into_os_string().into_string().map_err(|_| "The video path is not valid UTF-8.".into())
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum StudioAction {
    Workspace, Tools, ToolAction, Normalize, Presets, SavePreset, DeletePreset,
    SaveDraft, ClearDraft, CopyComposition, Speech, Record, CreateProfile, DeleteProfile, AssignProfile, UnassignProfile,
    ImportAudio, ImportImage, ImportBackground, AdoptNarration, Analysis, Preview, Render, Cancel,
    VideoTimeline, SaveVideo, VideoRender, VideoEncoders, ImageCatalog, ImageModel, ImageGenerate, ImageDetails,
    ImageProfiles, ImageProfileHistory, ImageProfileSave, RetryStudioJob,
    IllustratedStatus, IllustratedTopics, IllustratedNarrators, IllustratedRender, IllustratedInstall,
    SavedMorningTopics, SaveMorningTopic, FlagMorningTopic,
    MamaySettings, SaveMamaySettings,
    MorningSpeechSettings, SaveMorningSpeechSettings,
    GenerationDefaults, SaveGenerationDefaults,
}

#[tauri::command]
async fn studio_request(
    state: State<'_, BackendState>, action: StudioAction, project: Option<String>,
    id: Option<String>, input: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    use StudioAction::*;
    use reqwest::Method;

    let project = project.as_deref().unwrap_or("");
    let id = id.as_deref().unwrap_or("");
    let project_path = || -> Result<String, String> {
        validate_record_id(project)?;
        Ok(format!("/api/studio/{project}"))
    };
    let resource = || -> Result<&str, String> {
        validate_record_id(id)?;
        Ok(id)
    };
    let (method, path) = match action {
        Tools => (Method::GET, "/api/studio/tools".into()),
        ToolAction => {
            // The backend accepts only identifiers from its studio catalog.
            if id.is_empty() || id.len() > 80 || !id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_') {
                return Err("Invalid studio component identifier.".into());
            }
            (Method::POST, format!("/api/studio/tools/{id}"))
        }
        Normalize => (Method::POST, "/api/studio/normalize".into()),
        Presets => (Method::GET, "/api/studio/presets".into()),
        SavePreset => (Method::POST, "/api/studio/presets".into()),
        DeletePreset => (Method::DELETE, format!("/api/studio/presets/{}", resource()?)),
        DeleteProfile => (Method::DELETE, format!("/api/studio/profiles/{}", resource()?)),
        Workspace => (Method::GET, format!("{}/workspace", project_path()?)),
        SaveDraft => (Method::PUT, format!("{}/draft", project_path()?)),
        ClearDraft => (Method::DELETE, format!("{}/draft", project_path()?)),
        CopyComposition => (Method::POST, format!("{}/copy", project_path()?)),
        Speech => (Method::POST, format!("{}/speech", project_path()?)),
        CreateProfile => (Method::POST, format!("{}/profiles", project_path()?)),
        Record => (Method::POST, format!("{}/record", project_path()?)),
        AssignProfile => (Method::PUT, format!("{}/profiles/{}/assign", project_path()?, resource()?)),
        UnassignProfile => (Method::PUT, format!("{}/profiles/{}/unassign", project_path()?, resource()?)),
        ImportAudio => (Method::POST, format!("{}/import/audio", project_path()?)),
        ImportImage => (Method::POST, format!("{}/import/image", project_path()?)),
        ImportBackground => (Method::POST, format!("{}/import/background", project_path()?)),
        AdoptNarration => (Method::POST, format!("{}/narration/{}", project_path()?, resource()?)),
        Analysis => (Method::GET, format!("{}/assets/{}/analysis", project_path()?, resource()?)),
        Preview => (Method::POST, format!("{}/preview", project_path()?)),
        Render => (Method::POST, format!("{}/render", project_path()?)),
        Cancel => (Method::POST, format!("{}/jobs/{}/cancel", project_path()?, resource()?)),
        VideoTimeline => (Method::GET, format!("{}/video", project_path()?)),
        SaveVideo => (Method::PUT, format!("{}/video", project_path()?)),
        VideoRender => (Method::POST, format!("{}/video/render", project_path()?)),
        VideoEncoders => (Method::GET, "/api/studio/video/encoders".into()),
        IllustratedStatus => (Method::GET, "/api/studio/illustrated/status".into()),
        MamaySettings => (Method::GET, format!("{}/illustrated/writing-settings", project_path()?)),
        SaveMamaySettings => (Method::PUT, format!("{}/illustrated/writing-settings", project_path()?)),
        MorningSpeechSettings => (Method::GET, format!("{}/illustrated/speech-settings", project_path()?)),
        SaveMorningSpeechSettings => (Method::PUT, format!("{}/illustrated/speech-settings", project_path()?)),
        GenerationDefaults => (Method::GET, format!("{}/illustrated/defaults", project_path()?)),
        SaveGenerationDefaults => (Method::PUT, format!("{}/illustrated/defaults", project_path()?)),
        IllustratedTopics => (Method::POST, format!("{}/illustrated/topics", project_path()?)),
        SavedMorningTopics => (Method::GET, format!("{}/illustrated/topics", project_path()?)),
        SaveMorningTopic => (Method::PUT, format!("{}/illustrated/topics/{}", project_path()?, resource()?)),
        FlagMorningTopic => (Method::PATCH, format!("{}/illustrated/topics/{}", project_path()?, resource()?)),
        IllustratedNarrators => (Method::GET, format!("{}/illustrated/narrators", project_path()?)),
        IllustratedRender => (Method::POST, format!("{}/illustrated/render", project_path()?)),
        IllustratedInstall => (Method::POST, format!("{}/illustrated/install", project_path()?)),
        ImageCatalog => (Method::GET, "/api/studio/image/catalog".into()),
        ImageModel => (Method::POST, "/api/studio/image/model".into()),
        ImageDetails => (Method::PATCH, format!("{}/image/{}", project_path()?, resource()?)),
        ImageProfiles => (Method::GET, format!("{}/image-profiles", project_path()?)),
        ImageProfileHistory => (Method::GET, format!("{}/image-profiles/{}", project_path()?, resource()?)),
        ImageProfileSave => (Method::POST, format!("{}/image-profiles", project_path()?)),
        ImageGenerate => (Method::POST, format!("{}/image/generate", project_path()?)),
        RetryStudioJob => (Method::POST, format!("{}/jobs/{}/retry", project_path()?, resource()?)),
    };
    if method == Method::GET {
        get_json(&state, &path).await
    } else {
        send_json(&state, method, &path, &input.unwrap_or(serde_json::Value::Null)).await
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum StudioFileKind { Original, Wav, Mp3, Reference, Srt, Ass, Vtt, Thumbnail }

async fn studio_media_path(
    app: &tauri::AppHandle, state: &BackendState, project: Option<&str>, id: &str, kind: &StudioFileKind,
) -> Result<PathBuf, String> {
    validate_record_id(id)?;
    let root = app.path().app_data_dir().map_err(|error| error.to_string())?;
    let root = root.canonicalize().map_err(|error| error.to_string())?.join("studio");
    let (route, allowed) = if matches!(kind, StudioFileKind::Reference) {
        (format!("/api/studio/profiles/{id}/reference"), vec![root.join("profiles").join(id).join("reference.wav")])
    } else {
        let project = project.ok_or("Choose a project.")?;
        validate_record_id(project)?;
        let base = root.join(project);
        let (format, allowed) = match kind {
            StudioFileKind::Original => ("original", vec![
                base.join("audio").join(format!("{id}.wav")),
                base.join("export").join(format!("{id}.mp4")),
                base.join("export").join(format!("{id}.webm")),
                base.join("export").join(format!("{id}.zip")),
                base.join("export").join(format!("{id}.mov")),
                base.join("image").join(format!("{id}.png")),
                base.join("background").join(format!("{id}.mp4")),
            ]),
            StudioFileKind::Thumbnail => ("thumbnail", vec![
                base.join("thumbnail").join(format!("{id}.png")),
                base.join("image").join(format!("{id}.png")),
            ]),
            StudioFileKind::Wav => ("wav", vec![base.join("audio").join(format!("{id}.wav"))]),
            StudioFileKind::Mp3 => ("mp3", vec![base.join("audio").join(format!("{id}.mp3"))]),
            _ => return Err("This format is not a media file.".into()),
        };
        (format!("/api/studio/{project}/assets/{id}/file/{format}"), allowed)
    };
    let record: ExportPreview = get_json(state, &route).await?;
    let path = record.path.canonicalize().map_err(|error| error.to_string())?;
    if !allowed.contains(&path) || !path.is_file() {
        return Err("This file is outside the project media folder.".into());
    }
    Ok(path)
}

#[tauri::command]
async fn studio_media(
    app: tauri::AppHandle, state: State<'_, BackendState>, project: Option<String>, id: String, kind: StudioFileKind,
) -> Result<String, String> {
    let path = studio_media_path(&app, &state, project.as_deref(), &id, &kind).await?;
    app.asset_protocol_scope().allow_file(&path).map_err(|error| error.to_string())?;
    path.into_os_string().into_string().map_err(|_| "The media path is not valid UTF-8.".into())
}

#[derive(Deserialize)]
struct StudioCaption {
    text: String,
}

#[tauri::command]
async fn studio_save_file(
    app: tauri::AppHandle, state: State<'_, BackendState>, project: Option<String>, id: String, kind: StudioFileKind, name: String,
) -> Result<String, String> {
    use std::io::Write;

    let (extension, source, text) = if matches!(kind, StudioFileKind::Srt | StudioFileKind::Ass | StudioFileKind::Vtt) {
        let project = project.as_deref().ok_or("Choose a project.")?;
        validate_record_id(project)?;
        validate_record_id(&id)?;
        let ext = match kind { StudioFileKind::Srt => "srt", StudioFileKind::Vtt => "vtt", _ => "ass" };
        let caption: StudioCaption = get_json(&state, &format!("/api/studio/{project}/assets/{id}/subtitles/{ext}")).await?;
        (ext.into(), None, Some(caption.text))
    } else {
        let source = studio_media_path(&app, &state, project.as_deref(), &id, &kind).await?;
        let ext = source.extension().and_then(|value| value.to_str()).ok_or("Missing file extension.")?;
        (ext.to_string(), Some(source), None)
    };
    let directory = app.path().download_dir().map_err(|error| error.to_string())?;
    let clean: String = name.chars().filter(|c| !c.is_control() && !"/\\:<>?\"|*".contains(*c)).take(70).collect();
    let stem = clean.trim_matches(|c: char| c == '.' || c.is_whitespace());
    let stem = if stem.is_empty() { "Studio export" } else { stem };
    for index in 0..1000 {
        let filename = if index == 0 { format!("{stem}.{extension}") } else { format!("{stem} ({index}).{extension}") };
        let target = directory.join(filename);
        match std::fs::OpenOptions::new().write(true).create_new(true).open(&target) {
            Ok(mut output) => {
                let result = if let Some(source) = &source {
                    std::fs::File::open(source).and_then(|mut file| std::io::copy(&mut file, &mut output).map(|_| ()))
                } else if let Some(text) = &text {
                    output.write_all(text.as_bytes())
                } else {
                    unreachable!("media route must yield text or a source file")
                };
                if let Err(error) = result {
                    drop(output);
                    let _ = std::fs::remove_file(&target);
                    return Err(format!("Could not save to Downloads: {error}"));
                }
                return Ok(target.to_string_lossy().into_owned());
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("Could not save to Downloads: {error}")),
        }
    }
    Err("Too many exports share this name in Downloads.".into())
}

fn validate_component_id(component_id: &str) -> Result<(), String> {
    if component_id.is_empty()
        || component_id.len() > 80
        || !component_id
            .bytes()
            .all(|character| character.is_ascii_lowercase() || character.is_ascii_digit() || character == b'-')
        || component_id.starts_with('-')
        || component_id.ends_with('-')
    {
        return Err("Invalid component identifier".to_string());
    }
    Ok(())
}

async fn post_json<T, U>(state: &BackendState, path: &str, input: &U) -> Result<T, String>
where
    T: for<'de> Deserialize<'de>,
    U: Serialize,
{
    send_json(state, reqwest::Method::POST, path, input).await
}

async fn send_json<T, U>(state: &BackendState, method: reqwest::Method, path: &str, input: &U) -> Result<T, String>
where
    T: for<'de> Deserialize<'de>,
    U: Serialize,
{
    let response = reqwest::Client::new()
        .request(method, format!("{}{path}", state.base_url))
        .header(AUTHORIZATION, format!("Bearer {}", state.token))
        .header(CONTENT_TYPE, "application/json")
        .json(input)
        .send()
        .await
        .map_err(|error| format!("Local backend request failed: {error}"))?;
    let response = checked_response(response).await?;
    response.json::<T>().await.map_err(|error| format!("Invalid local backend response: {error}"))
}

async fn patch_json<T, U>(state: &BackendState, path: &str, input: &U) -> Result<T, String>
where
    T: for<'de> Deserialize<'de>,
    U: Serialize,
{
    send_json(state, reqwest::Method::PATCH, path, input).await
}

async fn delete_json<T, U>(state: &BackendState, path: &str, input: &U) -> Result<T, String>
where
    T: for<'de> Deserialize<'de>,
    U: Serialize,
{
    let response = reqwest::Client::new()
        .delete(format!("{}{path}", state.base_url))
        .header(AUTHORIZATION, format!("Bearer {}", state.token))
        .header(CONTENT_TYPE, "application/json")
        .json(input)
        .send()
        .await
        .map_err(|error| format!("Local backend request failed: {error}"))?;
    let response = checked_response(response).await?;
    response.json::<T>().await.map_err(|error| format!("Invalid local backend response: {error}"))
}

#[tauri::command]
async fn install_component(
    state: State<'_, BackendState>,
    component_id: String,
    accepted_license: bool,
) -> Result<serde_json::Value, String> {
    validate_component_id(&component_id)?;
    post_json(
        &state,
        &format!("/api/runtime/components/{component_id}/install"),
        &LicenseAcceptance { accepted_license },
    )
    .await
}

#[tauri::command]
async fn start_component(state: State<'_, BackendState>, component_id: String) -> Result<serde_json::Value, String> {
    validate_component_id(&component_id)?;
    post_json::<serde_json::Value, serde_json::Value>(
        &state,
        &format!("/api/runtime/components/{component_id}/start"),
        &serde_json::json!({}),
    )
    .await
}

#[tauri::command]
async fn stop_component(state: State<'_, BackendState>, component_id: String) -> Result<serde_json::Value, String> {
    validate_component_id(&component_id)?;
    post_json::<serde_json::Value, serde_json::Value>(
        &state,
        &format!("/api/runtime/components/{component_id}/stop"),
        &serde_json::json!({}),
    )
    .await
}

#[tauri::command]
async fn remove_component(
    state: State<'_, BackendState>,
    component_id: String,
    confirmation: String,
) -> Result<serde_json::Value, String> {
    validate_component_id(&component_id)?;
    delete_json(
        &state,
        &format!("/api/runtime/components/{component_id}"),
        &ComponentRemoval { confirmation },
    )
    .await
}

#[tauri::command]
async fn clear_runtime_cache(state: State<'_, BackendState>, confirmation: String) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/runtime/cleanup/cache", &RuntimeCleanup { confirmation }).await
}

#[tauri::command]
async fn clear_runtime_temporary(state: State<'_, BackendState>, confirmation: String) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/runtime/cleanup/temporary", &RuntimeCleanup { confirmation }).await
}

#[tauri::command]
async fn uninstall_runtime_tools(state: State<'_, BackendState>, confirmation: String) -> Result<serde_json::Value, String> {
    post_json(&state, "/api/runtime/uninstall", &RuntimeCleanup { confirmation }).await
}

#[tauri::command]
async fn create_project(state: State<'_, BackendState>, input: ProjectCreate) -> Result<Project, String> {
    let response = reqwest::Client::new()
        .post(format!("{}/api/projects", state.base_url))
        .header(AUTHORIZATION, format!("Bearer {}", state.token))
        .header(CONTENT_TYPE, "application/json")
        .json(&input)
        .send()
        .await
        .map_err(|error| format!("Local backend request failed: {error}"))?;
    let response = checked_response(response).await?;
    response.json::<Project>().await.map_err(|error| format!("Invalid local backend response: {error}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let backend = launch_backend(app).map_err(std::io::Error::other)?;
            app.manage(backend);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_health,
            list_projects,
            get_saved_script,
            link_project_script,
            latest_project_video,
            start_project_video,
            resume_project_video,
            cancel_project_video,
            get_settings,
            get_system_report,
            get_runtime_snapshot,
            get_local_model_status,
            install_local_model,
            cancel_local_model_install,
            list_topics,
            generate_topics,
            update_topic,
            regenerate_topic,
            generate_script,
            get_latest_script_for_topic,
            edit_script,
            generate_director,
            get_director_plan,
            studio_request,
            studio_media,
            studio_save_file,
            get_voice_status,
            install_voice,
            cancel_voice_install,
            render_narration,
            preview_voice,
            get_narration_audio,
            get_latest_narration,
            get_avatar_library,
            preview_avatar,
            save_avatar_preset,
            render_avatar,
            get_latest_avatar,
            get_avatar_video,
            portrait_model_status,
            install_portrait_model,
            cancel_portrait_model,
            list_portraits,
            get_portrait,
            import_portrait,
            save_portrait_crop,
            render_portrait,
            list_scene_assets,
            get_scene_models,
            latest_scene_job,
            start_scene_job,
            cancel_scene_job,
            import_scene_asset,
            list_video_exports,
            load_export_draft,
            list_background_music,
            import_background_music,
            save_export_draft,
            render_video_export,
            video_export_preview,
            create_project,
            install_component,
            start_component,
            stop_component,
            remove_component,
            clear_runtime_cache,
            clear_runtime_temporary,
            uninstall_runtime_tools
        ])
        .run(tauri::generate_context!())
        .expect("error while running AI Blogger Studio");
}
