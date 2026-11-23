import { command } from "./api";
import type { Job, Language } from "./studio";

export type Cue = { start: number; end: number; text: string };
export type Scene = {
  id: string; image_id: string | null; duration: number; animation: string; transition: string; overlap: number;
  narration_id: string | null; narration_pending_id: string | null; narration_trim: number; trim_narration: boolean; narration_text: string;
  image_prompt: string; title: string; captions: Cue[];
};
export type AudioClip = {
  asset_id: string; track: "music" | "effects"; start: number; trim: number; length: number | null;
  gain: number; fade_in: number; fade_out: number; loop: boolean; mute: boolean; solo: boolean;
};
export type Timeline = {
  version: 1; revision: number; width: number; height: number; fps: number; background: string;
  scenes: Scene[]; audio: AudioClip[]; narration_gain: number; duck: boolean; duck_amount: number;
  duck_attack: number; duck_release: number; duck_threshold: number; caption_style: string;
};
export type VideoSettings = {
  timeline: Timeline; scene_id?: string; preview?: boolean; filename: string; container: "mp4" | "mov";
  video_codec: string; audio_codec: string; crf: number; audio_bitrate: number;
  captions: "burn" | "separate" | "none";
};
export type ImageMode = "text" | "variation" | "inpaint" | "upscale";
export type ImageInput = {
  model_id: string; mode: ImageMode; device: "auto" | "mps" | "cuda" | "cpu";
  prompt: string; negative: string; width: number; height: number; steps: number; guidance: number;
  seed: number | null; batch: number; strength: number; reference_id: string | null; mask_id: string | null;
  adapter_image_id: string | null; adapter_scale: number; control_image_id: string | null; control_scale: number;
  character_id: string | null; environment_id: string | null; style_id: string | null; preset_style: string | null;
  profile_versions?: Partial<Record<Profile["kind"], number>>;
};
export type ImageModel = {
  id: string; name: string; version: string; installed: boolean; estimated_bytes?: number; memory_bytes?: number; license: string;
  license_url?: string; model_url?: string; provider: string; modes: ImageMode[]; negative: boolean;
  available?: boolean; guidance?: boolean; strength?: boolean; max_batch?: number; default_steps?: number; dimension_step?: number;
  recommended_size: number; ip_adapter?: boolean; control_guide?: string | null; lora?: boolean; enabled: boolean; health?: string;
};
export type ImageCatalog = {
  runtime_ready: boolean; runtime_installable: boolean; directory: string; torch?: string; memory_bytes?: number; device: string[]; runtime_message: string; models: ImageModel[];
  styles: Record<string, string>; install?: { model_id: string; state: string; progress: number | null; message: string };
};
export type Profile = {
  id: string; kind: "character" | "environment" | "style"; name: string; description: string;
  clothing: string; reference_ids: string[]; approved_id: string | null; version: number;
};
export type ImageDetails = {
  name?: string; collection?: string; tags?: string[]; favorite?: boolean; approval?: "approved" | "rejected" | "pending";
};
const request = <T>(action: string, options: { project?: string; id?: string; input?: unknown } = {}) =>
  command<T>("studio_request", { action, ...options });

export type MorningTopic = { title: string; passage: string; angle: string };
export type MorningScript = { title: string; bible_text?: string | null; bible_reference?: string | null;
  scenes: { narration: string; caption: string; illustration: string;
  section: "scripture" | "poem" | "paragraph" }[] };
export type SavedMorningTopic = MorningTopic & { id: string; revision: number; language: Language; used: boolean;
  status: "pending" | "ready" | "failed"; error: string; script: MorningScript | null; deleted_at: string | null;
  created_at: string; updated_at: string };
export const morningTopicsKey = (project: string) => ["morning-topics", project];
export const generationDefaultsKey = (project: string) => ["morning-generation-defaults", project];
export type MorningNarrator = { model_id: string; voice: string; name: string; languages: Language[]; cloned: boolean };
export type MorningStyle = "watercolor" | "ink" | "cinematic" | "biblical";
export type MorningResolution = "720p" | "1080p" | "vertical";
export type MorningCaptions = "handwritten" | "clean" | "off";
export type MamaySettings = {
  revision: number; voice_rules: string; rhythm_rules: string; title_rules: string; bible_rules: string; structure_rules: string; topic_rules: string;
  editor_rules: string; examples_uk: string; examples_en: string; editor_enabled: boolean;
};
export type IllustratedInput = {
  topic_id?: string; topic_revision?: number;
  topic: string; passage: string; angle: string; language: Language;
  narrator: Pick<MorningNarrator, "model_id" | "voice">; style: MorningStyle;
  resolution: MorningResolution; captions: MorningCaptions; seconds: number | null; scenes: number | null;
  speech_settings?: MorningSpeechSettings;
  text_words?: number | null;
  text_range?: WordRange | null;
};
export type WordRange = { min_words: number; max_words: number };
export type GenerationDefaults = {
  revision: number; language: Language; text_range: WordRange | null;
  narrator: Pick<MorningNarrator, "model_id" | "voice"> | null;
  style: MorningStyle; resolution: MorningResolution; captions: MorningCaptions;
  seconds: number | null; scenes: number | null;
};
export type MorningSpeechSettings = {
  revision: number; pronunciation: Record<string, string>; speed: number; pause_ms: number;
  paragraph_pause_ms?: number | null; title_pause_ms?: number | null; block_pause_ms?: number | null;
};
type IllustratedStatus = {
  supported: boolean; writing_model: boolean; writing_model_bytes: number; writing_model_terms: string;
  illustrations: boolean; rendering: boolean;
};

export const media = {
  generationDefaults: (project: string) => request<GenerationDefaults>("generation_defaults", { project }),
  saveGenerationDefaults: (project: string, input: GenerationDefaults) => request<GenerationDefaults>("save_generation_defaults", { project, input }),
  morningSpeechSettings: (project: string) => request<MorningSpeechSettings>("morning_speech_settings", { project }),
  saveMorningSpeechSettings: (project: string, input: MorningSpeechSettings) => request<MorningSpeechSettings>("save_morning_speech_settings", { project, input }),
  mamaySettings: (project: string) => request<{ settings: MamaySettings; defaults: MamaySettings }>("mamay_settings", { project }),
  saveMamaySettings: (project: string, input: MamaySettings) => request<MamaySettings>("save_mamay_settings", { project, input }),
  illustratedStatus: () => request<IllustratedStatus>("illustrated_status"),
  morningTopics: (project: string, language: Language) => request<Job>("illustrated_topics", { project, input: { language } }),
  savedMorningTopics: (project: string) => request<SavedMorningTopic[]>("saved_morning_topics", { project }),
  saveMorningTopic: (project: string, id: string, input: MorningTopic & { revision: number; script: MorningScript }) =>
    request<SavedMorningTopic>("save_morning_topic", { project, id, input }),
  flagMorningTopic: (project: string, id: string, input: { revision: number; used?: boolean; deleted?: boolean }) =>
    request<SavedMorningTopic>("flag_morning_topic", { project, id, input }),
  morningNarrators: (project: string) => request<MorningNarrator[]>("illustrated_narrators", { project }),
  illustratedRender: (project: string, input: IllustratedInput) => request<Job>("illustrated_render", { project, input }),
  illustratedInstall: (project: string) => request<Job>("illustrated_install", { project, input: { accepted_license: true } }),
  timeline: (project: string) => request<Timeline>("video_timeline", { project }),
  save: (project: string, input: Timeline) => request<Timeline>("save_video", { project, input }),
  render: (project: string, input: VideoSettings) => request<Job>("video_render", { project, input }),
  encoders: () => request<{ video: string[]; audio: string[] }>("video_encoders"),
  catalog: () => request<ImageCatalog>("image_catalog"),
  model: (model_id: string, action: string, accepted_license = false) =>
    request<ImageCatalog>("image_model", { input: { model_id, action, accepted_license } }),
  generate: (project: string, input: ImageInput) => request<Job>("image_generate", { project, input }),
  updateImage: (project: string, id: string, input: ImageDetails) =>
    request("image_details", { project, id, input }),
  profiles: (project: string) => request<Profile[]>("image_profiles", { project }),
  profileHistory: (project: string, id: string) => request<Profile[]>("image_profile_history", { project, id }),
  saveProfile: (project: string, input: Omit<Profile, "version">) =>
    request<Profile>("image_profile_save", { project, input }),
  retry: (project: string, id: string) => request<Job>("retry_studio_job", { project, id }),
};

export function newScene(image_id: string | null = null): Scene {
  return { id: crypto.randomUUID(), image_id, duration: 6, animation: "ken_burns", transition: "cut",
    overlap: .5, narration_id: null, narration_pending_id: null, narration_trim: 0, trim_narration: false, narration_text: "",
    image_prompt: "", title: "", captions: [] };
}
