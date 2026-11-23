use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::Manager;

pub struct ApplicationDirectories {
    pub data: PathBuf,
    pub cache: PathBuf,
    pub config: PathBuf,
    pub logs: PathBuf,
    pub temp: PathBuf,
}

pub fn application_directories(app: &tauri::App) -> Result<ApplicationDirectories, String> {
    let resolver = app.path();
    let cache = resolver.app_cache_dir().map_err(|error| format!("Could not resolve app cache directory: {error}"))?;
    Ok(ApplicationDirectories {
        data: resolver.app_data_dir().map_err(|error| format!("Could not resolve app data directory: {error}"))?,
        temp: cache.join("tmp"),
        cache,
        config: resolver.app_config_dir().map_err(|error| format!("Could not resolve app config directory: {error}"))?,
        logs: resolver.app_log_dir().map_err(|error| format!("Could not resolve app log directory: {error}"))?,
    })
}

pub fn prepare_application_directories(dirs: &ApplicationDirectories) -> Result<(), String> {
    for path in [&dirs.data, &dirs.cache, &dirs.config, &dirs.logs, &dirs.temp] {
        fs::create_dir_all(path).map_err(|error| format!("Could not create app directory {}: {error}", path.display()))?;
        restrict_permissions(path, 0o700)?;
    }
    Ok(())
}

pub struct BundledRuntime {
    pub backend: PathBuf,
    pub python: PathBuf,
    pub media_bin: PathBuf,
}

pub fn bundled_runtime(resources: &Path) -> Result<Option<BundledRuntime>, String> {
    let root = resources.join("studio-runtime");
    if !root.exists() && cfg!(debug_assertions) {
        return Ok(None);
    }
    let backend = root.join("app");
    let python = root.join("python/bin/python3.12");
    let media_bin = root.join("media/bin");
    for path in [backend.join("backend/runner.py"), python.clone(),
                 media_bin.join("ffmpeg"), media_bin.join("ffprobe")] {
        if !path.is_file() {
            return Err(format!("The packaged runtime is incomplete. Missing {}.", path.display()));
        }
    }
    Ok(Some(BundledRuntime { backend, python, media_bin }))
}

pub fn python_executable(root: &Path) -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("AI_BLOGGER_PYTHON") {
        return Ok(PathBuf::from(path));
    }
    #[cfg(target_os = "windows")]
    let local_python = root.join("backend/.venv/Scripts/python.exe");
    #[cfg(not(target_os = "windows"))]
    let local_python = root.join("backend/.venv/bin/python3");

    if local_python.is_file() {
        return Ok(local_python);
    }
    if cfg!(debug_assertions) {
        return Ok(PathBuf::from(if cfg!(target_os = "windows") { "python" } else { "python3" }));
    }
    Err("Packaged backend executable is not configured for release builds".to_string())
}

pub fn configure_background_process(command: &mut Command) {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    #[cfg(not(target_os = "windows"))]
    let _ = command;
}

pub fn restrict_permissions(path: &Path, mode: u32) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(mode))
            .map_err(|error| format!("Could not secure application file permissions: {error}"))
    }
    #[cfg(not(unix))]
    {
        let _ = (path, mode);
        Ok(())
    }
}
