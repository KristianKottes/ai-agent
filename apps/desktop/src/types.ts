export interface Project {
  id: string;
  title: string;
  created_at: string;
  script_id: string | null;
  topic_id: string | null;
}

export interface BackendHealth {
  status: "ok";
  backend_version: string;
  schema_version: number;
}

export interface AppSettings {
  data_directory: string;
  cache_directory: string;
  config_directory: string;
  logs_directory: string;
  temp_directory: string;
  backend_version: string;
  schema_version: number;
  authentication_enabled: boolean;
}

export interface GPUInfo {
  vendor: string | null;
  name: string;
  memory_bytes: number | null;
  memory_kind: string | null;
  driver_version: string | null;
  detection_source: string;
}

export interface AcceleratorInfo {
  name: string;
  detection_status: "detected" | "not_detected" | "unknown";
  evidence: string;
  inference_compatibility: "not_tested";
}

export interface StorageInfo {
  data_directory: string;
  cache_directory: string;
  config_directory: string;
  temp_directory: string;
  total_bytes: number | null;
  used_bytes: number | null;
  available_bytes: number | null;
}

export interface SystemReport {
  os_name: string;
  os_version: string | null;
  architecture: string;
  cpu_model: string | null;
  logical_cpu_count: number | null;
  memory_total_bytes: number | null;
  memory_available_bytes: number | null;
  memory_available_method: string | null;
  gpus: GPUInfo[];
  accelerators: AcceleratorInfo[];
  storage: StorageInfo;
  compatibility_note: string;
}

export interface RuntimeLicense {
  source_code: string;
  model_weights: string;
  commercial_use: "permitted" | "restricted" | "unknown" | "not_applicable";
  attribution_required: boolean;
  license_file: string;
}

export interface RuntimePlatformSupport {
  os: "macos" | "windows" | "linux";
  architectures: Array<"arm64" | "x86_64" | "x86">;
  verification: "verified" | "not_tested";
}

export interface RuntimeOperation {
  component_id: string;
  state: string;
  details?: string;
  process_id?: number | null;
  exit_code?: number | null;
  removed_bytes?: number;
}

export interface RuntimeComponent {
  id: string;
  name: string;
  version: string;
  category: string;
  description: string;
  experimental: boolean;
  source: string;
  license: RuntimeLicense;
  license_text: string;
  platforms: RuntimePlatformSupport[];
  backends: string[];
  gpu_requirements: string;
  minimum_ram_bytes: number;
  minimum_disk_bytes: number;
  dependencies: string[];
  download_size_bytes: number;
  installed_size_bytes: number;
  compatibility: "verified" | "not_tested" | "unsupported";
  compatibility_reason: string;
  installation_state: string;
  installed_version: string | null;
  installed_at: string | null;
  update_available: boolean;
  process_id: number | null;
  log_tail: string;
  last_operation: { operation: string; state: string; details: string; updated_at: string } | null;
}

export interface RuntimeStorage {
  registered_file_count: number;
  registered_bytes: number;
  component_bytes: number;
  voice_bytes: number;
  avatar_bytes: number;
  export_bytes: number;
  scene_model_bytes: number;
  download_cache_bytes: number;
  log_bytes: number;
  temporary_bytes: number;
  available_bytes: number | null;
  volume_total_bytes: number | null;
  paths: { components: string; cache: string; logs: string; temporary: string };
}

export interface RuntimeSnapshot {
  catalog_version: number;
  platform: string;
  architecture: string;
  components: RuntimeComponent[];
  storage: RuntimeStorage;
  updates_supported: boolean;
  remote_downloads_supported: boolean;
}

export type TopicCategory =
  | "Personal Thoughts"
  | "Modern Life"
  | "Self Improvement"
  | "Interesting Facts"
  | "Lifestyle"
  | "Motivation"
  | "Technology"
  | "Human Psychology";

export interface LocalModelStatus {
  model_id: string;
  name: string;
  publisher: string;
  license: string;
  source_url: string;
  runtime: string;
  platform: string;
  platform_verification: "verified" | "not_tested";
  install_enabled: boolean;
  runtime_installed: boolean;
  state: "available" | "downloading" | "installed" | "failed" | "cancelled";
  expected_size_bytes: number;
  downloaded_bytes: number;
  sha256: string;
  progress_percent: number;
  message: string;
  inference_ready: boolean;
}

export interface VideoTopic {
  id: string;
  title: string;
  description: string;
  category: TopicCategory;
  opening_hook: string;
  target_audience: string;
  estimated_duration_seconds: number;
  thumbnail_concept: string;
  script_potential: string;
  originality_score: number;
  is_favorite: boolean;
  is_used: boolean;
  created_at: string;
}

export interface TopicBatchResult {
  topics: VideoTopic[];
  requested_count: number;
  created_count: number;
  duplicate_count: number;
  validation_rejected_count: number;
  note: string;
}

export interface ScriptParagraph {
  paragraph_id: number;
  section: "opening_hook" | "introduction" | "main_content" | "example" | "development" | "conclusion" | "call_to_action";
  text: string;
  start_seconds: number;
  duration_seconds: number;
  visual_suggestion: string;
  on_screen_text: string;
  voice_direction: string;
}

export interface ScriptQuality {
  status: "pass" | "review";
  word_count: number;
  target_word_range: string;
  estimated_spoken_duration_seconds: number;
  warnings: string[];
}

export interface ScriptDocument {
  id: string;
  topic_id: string;
  title: string;
  requested_duration_seconds: number;
  full_narration: string;
  paragraphs: ScriptParagraph[];
  quality: ScriptQuality;
  created_at: string;
}

export interface DirectorScene {
  scene_id: string;
  start_seconds: number;
  duration_seconds: number;
  scene_type: "talking_head" | "ai_generated_video" | "ai_generated_background" | "supporting_footage" | "b_roll" | "text_animation" | "closing_scene";
  narration: string;
  presenter_present: boolean;
  camera_angle: string;
  camera_movement: string;
  lighting: string;
  background: string;
  visual_description: string;
  generation_prompt: string;
  transition: string;
  subtitles: string;
  audio_requirements: string[];
}

export interface DirectorPlan {
  id: string;
  script_id: string;
  visual_style: string;
  total_duration_seconds: number;
  scenes: DirectorScene[];
  created_at: string;
}
export interface VoiceStatus {
  state: "available" | "downloading" | "installing" | "installed" | "cancelled" | "failed";
  ready: boolean;
  platform_verified: boolean;
  enabled?: boolean;
  runtime_installed: boolean;
  download_bytes: number;
  downloaded_bytes: number;
  message: string;
  license_url: string;
  voices: { id: string; name: string; accent: string }[];
}

export interface Narration {
  id: string;
  voice_id: string;
  speed: number;
  duration_seconds: number;
  size_bytes: number;
  filename: string;
  path: string;
}
export interface AvatarAppearance {
  look: string;
  backdrop: string;
  outfit: string;
  motion: boolean;
}

export interface AvatarPreset extends AvatarAppearance {
  id: string;
  name: string;
}

export interface AvatarLibrary {
  ready: boolean;
  detail: string;
  looks: { id: string; name: string }[];
  backdrops: string[];
  outfits: string[];
  max_seconds: number;
  presets: AvatarPreset[];
}

export interface AvatarRender {
  id: string;
  appearance?: AvatarAppearance;
  portrait_id?: string;
  animate_head?: boolean;
  engine?: "illustrated" | "musetalk";
  duration_seconds: number;
  path: string;
}

export interface SceneAsset {
  id: string;
  name: string;
  kind: "image" | "video";
  max_frames: number | null;
}

export interface BackgroundMusic {
  id: string;
  name: string;
  duration_seconds: number;
}

export interface SceneGenerationRequest {
  kind: "image" | "video";
  prompt: string;
  seed: number;
  accepted_license: boolean;
}

export interface SceneModels {
  platform_eligible: boolean;
  video_tools_available: boolean;
  profiles: Record<SceneGenerationRequest["kind"], { eligible: boolean; cached: boolean; download_bytes: number }>;
}

export interface SceneJob extends Omit<SceneGenerationRequest, "accepted_license"> {
  id: string;
  state: "running" | "cancelling" | "completed" | "cancelled" | "failed" | "interrupted";
  stage: "preparing" | "downloading" | "generating" | "validating" | "completed" | "cancelled" | "failed";
  progress: number | null;
  message: string;
  asset_id: string | null;
}

export interface ExportScene {
  source: "asset" | "presenter";
  id: string;
  frames: number;
  transition: "cut" | "fade_black";
}

interface ExportSettings {
  format: "landscape" | "vertical" | "square";
  quality: "draft" | "standard" | "high";
  captions: boolean;
  music_id: string | null;
  music_volume: number;
}

export interface VideoExportRequest extends ExportSettings {
  narration_id: string;
  scenes: ExportScene[];
}

export interface ProjectVideoRequest {
  topic_id: string;
  voice_id: string;
  look: "maya" | "eli";
  format: ExportSettings["format"];
  captions: boolean;
}

export type ProjectVideoStage = "script" | "director" | "narration" | "presenter" | "image" | "video" | "export";

export interface ProjectVideoJob {
  id: string;
  project_id: string;
  topic_id: string;
  state: SceneJob["state"];
  stage: ProjectVideoStage;
  completed_stages: ProjectVideoStage[];
  script_id: string | null;
  export_id: string | null;
  message: string;
  captions: boolean;
  progress: number | null;
}

export interface DraftScene extends ExportScene {
  key: number;
  name: string;
  max_frames: number | null;
  presenter_narration_id: string | null;
}

export interface ExportDraft extends ExportSettings {
  scenes: DraftScene[];
}

export interface VideoExport {
  id: string;
  path: string;
  format: VideoExportRequest["format"];
  width: number;
  height: number;
  duration_seconds: number;
  caption_count: number;
  size_bytes: number;
  subtitle_files?: Partial<Record<"srt" | "ass", string>>;
}

export interface FaceCrop {
  x: number;
  y: number;
  side: number;
}

export interface SavedPortrait {
  id: string;
  name: string;
  crop: FaceCrop;
  reference_seconds: number | null;
}

export interface PortraitModelStatus {
  state: "available" | "downloading" | "cancelled" | "failed" | "installed";
  platform_eligible: boolean;
  runtime_installed: boolean;
  tools_available: boolean;
  ready: boolean;
  download_bytes: number;
  downloaded_bytes: number;
  message: string;
  source_url: string;
}

