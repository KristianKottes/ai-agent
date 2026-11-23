import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import type {
  AppSettings, AvatarAppearance, AvatarLibrary, AvatarPreset, AvatarRender, BackendHealth, DirectorPlan,
  FaceCrop, LocalModelStatus, PortraitModelStatus, Project, ProjectVideoJob, ProjectVideoRequest, RuntimeOperation, SavedPortrait,
  Narration, RuntimeSnapshot, ScriptDocument, SystemReport, TopicBatchResult, TopicCategory, VideoTopic, VoiceStatus,
  BackgroundMusic, ExportDraft, SceneAsset, SceneGenerationRequest, SceneJob, SceneModels, VideoExport, VideoExportRequest,
} from "../types";

export const isDesktopRuntime = () =>
  Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);

export async function command<T>(name: string, args?: Record<string, unknown>): Promise<T> {
  try {
    return await invoke<T>(name, args);
  } catch (error) {
    throw new Error(typeof error === "string" ? error : "The local desktop command failed.");
  }
}

export const api = {
  health: () => command<BackendHealth>("backend_health"),
  projects: () => command<Project[]>("list_projects"),
  createProject: (title: string) => command<Project>("create_project", { input: { title } }),
  linkProjectScript: (projectId: string, scriptId: string) => command<Project>("link_project_script", { projectId, scriptId }),
  latestProjectVideo: (projectId: string) => command<ProjectVideoJob | null>("latest_project_video", { projectId }),
  startProjectVideo: (projectId: string, input: ProjectVideoRequest) =>
    command<ProjectVideoJob>("start_project_video", { projectId, input }),
  resumeProjectVideo: (projectId: string, jobId: string, input: { captions?: boolean }) =>
    command<ProjectVideoJob>("resume_project_video", { projectId, jobId, input }),
  cancelProjectVideo: (projectId: string, jobId: string) => command<ProjectVideoJob>("cancel_project_video", { projectId, jobId }),
  savedScript: (scriptId: string) => command<ScriptDocument>("get_saved_script", { scriptId }),
  settings: () => command<AppSettings>("get_settings"),
  systemReport: () => command<SystemReport>("get_system_report"),
  runtime: () => command<RuntimeSnapshot>("get_runtime_snapshot"),
  localModel: () => command<LocalModelStatus>("get_local_model_status"),
  installLocalModel: (acceptedLicense: boolean) => command<LocalModelStatus>("install_local_model", { acceptedLicense }),
  cancelLocalModelInstall: () => command<LocalModelStatus>("cancel_local_model_install"),
  topics: () => command<VideoTopic[]>("list_topics"),
  generateTopics: (count: number, category?: TopicCategory) => command<TopicBatchResult>("generate_topics", { count, category }),
  updateTopic: (topicId: string, update: { isFavorite?: boolean; isUsed?: boolean }) => command<VideoTopic>("update_topic", { topicId, ...update }),
  regenerateTopic: (topicId: string) => command<VideoTopic>("regenerate_topic", { topicId }),
  generateScript: (topicId: string, durationSeconds: number) => command<ScriptDocument>("generate_script", { topicId, durationSeconds }),
  latestScriptForTopic: (topicId: string) => command<ScriptDocument | null>("get_latest_script_for_topic", { topicId }),
  editScript: (scriptId: string, fullNarration: string) => command<ScriptDocument>("edit_script", { scriptId, fullNarration }),
  generateDirector: (scriptId: string) => command<DirectorPlan>("generate_director", { scriptId }),
  directorPlan: (scriptId: string) => command<DirectorPlan | null>("get_director_plan", { scriptId }),
  voiceStatus: () => command<VoiceStatus>("get_voice_status"),
  installVoice: () => command<VoiceStatus>("install_voice", { acceptedLicense: true }),
  cancelVoiceInstall: () => command<VoiceStatus>("cancel_voice_install"),
  previewVoice: (input: { voice_id: string; speed: number }) => command<ArrayBuffer>("preview_voice", { input }),
  renderNarration: (scriptId: string, input: { voice_id: string; speed: number }) =>
    command<Narration>("render_narration", { scriptId, input }),
  narrationAudio: (narrationId: string) => command<ArrayBuffer>("get_narration_audio", { narrationId }),
  latestNarration: (scriptId: string) => command<Narration | null>("get_latest_narration", { scriptId }),
  avatarLibrary: () => command<AvatarLibrary>("get_avatar_library"),
  previewAvatar: (input: AvatarAppearance) => command<{ image_url: string }>("preview_avatar", { input }),
  saveAvatarPreset: (input: AvatarAppearance & { name: string }) => command<AvatarPreset>("save_avatar_preset", { input }),
  renderAvatar: (input: AvatarAppearance & { narration_id: string; seconds: number }) => command<AvatarRender>("render_avatar", { input }),
  latestAvatar: (narrationId: string, engine?: "illustrated" | "musetalk") =>
    command<AvatarRender | null>("get_latest_avatar", { narrationId, engine }),
  avatarVideo: (renderId: string) => command<ArrayBuffer>("get_avatar_video", { renderId }),
  portraitModel: () => command<PortraitModelStatus>("portrait_model_status"),
  installPortraitModel: () => command<PortraitModelStatus>("install_portrait_model", { acceptedLicense: true }),
  cancelPortraitModel: () => command<PortraitModelStatus>("cancel_portrait_model"),
  portraits: () => command<SavedPortrait[]>("list_portraits"),
  portrait: (portraitId: string) => command<SavedPortrait & { image_url: string }>("get_portrait", { portraitId }),
  importPortrait: (name: string, base64: string, video: boolean) =>
    command<SavedPortrait>("import_portrait", { input: { name, [video ? "video_base64" : "image_base64"]: base64 } }),
  savePortraitCrop: (portraitId: string, crop: FaceCrop) =>
    command<SavedPortrait>("save_portrait_crop", { portraitId, input: crop }),
  renderPortrait: (portraitId: string, narrationId: string, seconds: number, animateHead: boolean) =>
    command<AvatarRender>("render_portrait", {
      input: { portrait_id: portraitId, narration_id: narrationId, seconds, animate_head: animateHead },
    }),
  sceneAssets: (scriptId: string) => command<SceneAsset[]>("list_scene_assets", { scriptId }),
  sceneModels: () => command<SceneModels>("get_scene_models"),
  latestSceneJob: (scriptId: string) => command<SceneJob | null>("latest_scene_job", { scriptId }),
  startSceneJob: (scriptId: string, input: SceneGenerationRequest) => command<SceneJob>("start_scene_job", { scriptId, input }),
  cancelSceneJob: (scriptId: string, jobId: string) => command<SceneJob>("cancel_scene_job", { scriptId, jobId }),
  importSceneAsset: (scriptId: string, input: { name: string; kind: SceneAsset["kind"]; content_base64: string }) =>
    command<SceneAsset>("import_scene_asset", { scriptId, input }),
  videoExports: (scriptId: string) => command<VideoExport[]>("list_video_exports", { scriptId }),
  exportDraft: (scriptId: string) => command<ExportDraft>("load_export_draft", { scriptId }),
  backgroundMusic: (scriptId: string) => command<BackgroundMusic[]>("list_background_music", { scriptId }),
  importBackgroundMusic: (scriptId: string, input: { name: string; content_base64: string }) =>
    command<BackgroundMusic>("import_background_music", { scriptId, input }),
  saveExportDraft: (scriptId: string, input: ExportDraft) => command<ExportDraft>("save_export_draft", { scriptId, input }),
  exportVideo: (scriptId: string, input: VideoExportRequest) =>
    command<VideoExport>("render_video_export", { scriptId, input }),
  exportPreview: async (exportId: string) => convertFileSrc(await command<string>("video_export_preview", { exportId })),
  installComponent: (componentId: string) => command<RuntimeOperation>("install_component", { componentId, acceptedLicense: true }),
  startComponent: (componentId: string) => command<RuntimeOperation>("start_component", { componentId }),
  stopComponent: (componentId: string) => command<RuntimeOperation>("stop_component", { componentId }),
  removeComponent: (componentId: string, confirmation: string) => command<RuntimeOperation>("remove_component", { componentId, confirmation }),
  clearRuntimeCache: (confirmation: string) => command<{ removed_files: number; removed_bytes: number }>("clear_runtime_cache", { confirmation }),
  clearRuntimeTemporary: (confirmation: string) => command<{ removed_files: number; removed_bytes: number }>("clear_runtime_temporary", { confirmation }),
  uninstallRuntimeTools: (confirmation: string) => command<{ state: string; project_data_removed: boolean }>("uninstall_runtime_tools", { confirmation }),
};
