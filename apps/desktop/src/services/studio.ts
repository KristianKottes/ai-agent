import { convertFileSrc } from "@tauri-apps/api/core";
import { command } from "./api";
import type { ImageInput } from "./media";

export type Language = "en" | "uk";
export type ToolAction = "install" | "cancel" | "test" | "enable" | "disable" | "remove";
export type StudioTool = {
  id: string; category: string; name: string; version: string; languages: Language[];
  license: string; license_url?: string; runtime_license?: string; state: string; message?: string;
  enabled: boolean; ready: boolean; compatible: boolean; compatibility_message?: string; health?: string;
  ready_languages?: Language[]; reference_languages?: Language[];
  required_models?: Partial<Record<Language, string>>;
  default_sample_rate?: number;
  download_bytes?: number; downloaded_bytes?: number; disk_bytes?: number; platform?: string; backend?: string;
  research_only?: boolean; ram_bytes?: number; features?: string[];
  voices: { id: string; name: string; locale: string }[];
};

export const languageName = { en: "English", uk: "Українська" };
export type VisualStyle = "spectrum" | "waveform" | "bars" | "particles" | "vinyl" | "ambient" | "motion";
export type LayerProperty = "x" | "y" | "scale" | "rotation" | "opacity" | "blur" | "glow" | "reveal";
type Easing = "linear" | "ease_in" | "ease_out" | "ease_in_out";
export type Layer = Record<LayerProperty, number> & {
  id: string; kind: "text" | "ring" | "diamond"; text: string; color: string | null;
  start: number; end: number | null;
  keyframes: Partial<Record<LayerProperty, { time: number; value: number; easing: Easing }[]>>;
  colors: { time: number; value: string; easing: Easing }[];
  bindings: { source: "bass" | "treble" | "rms" | "peak"; property: LayerProperty; strength: number }[];
};
export type Composition = {
  version: 1; style: VisualStyle; width: number; height: number; fps: number; background: string; primary: string;
  secondary: string; bar_count: number; mirrored: boolean; filled: boolean; peaks: boolean; intensity: number;
  glow: number; captions: boolean; camera: boolean; motion_blur: boolean; layers: Layer[]; artwork: string | null; backdrop: string | null;
};
export type Settings = { format: "mp4" | "webm" | "png"; transparent: boolean };
export type Draft = { asset_id: string | null; composition: Composition; settings: Settings };
export type Preset = { id: string; name: string; composition: Composition; settings?: Settings; custom?: boolean };
export type Asset = {
  id: string; kind: "audio" | "export" | "image" | "background"; name: string; created_at: string;
  duration_seconds?: number; sample_rate?: number; extension?: "mp4" | "mov" | "webm" | "zip"; source_id?: string;
  cues?: { start: number; end: number; text: string }[]; language?: Language; research_only?: boolean; cloned?: boolean;
  width?: number; height?: number; fps?: number; source_type?: string; preview?: boolean;
  generation?: ImageInput; seed?: number; license?: string; model_id?: string; thumbnail?: boolean;
  collection?: string; tags?: string[]; favorite?: boolean; approval?: "approved" | "rejected" | "pending";
  peak_rss_bytes?: number; requested_format?: "mp3" | "wav";
  transcript?: string;
};
export type VoiceProfile = {
  id: string; name: string; language: Language; languages: Language[]; assigned: boolean;
  reference_count?: number; reference_duration_seconds?: number;
  quality: { duration_seconds: number; peak: number; rms: number; clipped_fraction: number }; waveform: number[];
};
export type Job = { id: string; kind: "speech" | "render" | "video" | "image" | "illustrated_video" | "illustrated_model" | "illustrated_topics"; state: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number; message: string; topic?: string | null };
export type Workspace = { draft: Draft | null; assets: Asset[]; jobs: Job[]; profiles: VoiceProfile[] };
type TextInput = { text: string; language: Language; pronunciation?: Record<string, string> };
export type Speech = TextInput & {
  model_id: string; voice: string; name: string; speed: number; pause_ms: number; sample_rate: number;
  normalize_audio: boolean; format: "wav" | "mp3"; normalized_text?: string; noncommercial_confirmed?: boolean;
  paragraph_pause_ms?: number | null; title_pause_ms?: number | null; block_pause_ms?: number | null;
  blocks?: { text: string; kind: "title" | "scripture" | "poem" | "paragraph"; pause_after_ms?: number }[];
  trailing_pause?: boolean;
};
type Media = { name: string; content_base64: string };
type Recording = Media & { start: number; end: number | null; trim_silence: boolean; denoise: boolean };
type Reference = Recording & {
  language: Language; authorization_confirmed: boolean; additional_recordings?: Recording[];
};
type FileKind = "original" | "wav" | "mp3" | "srt" | "ass" | "vtt" | "thumbnail" | "reference";

const request = <T>(action: string, options: { project?: string; id?: string; input?: unknown } = {}) =>
  command<T>("studio_request", { action, ...options });

export const studio = {
  tools: () => request<StudioTool[]>("tools"),
  tool: (id: string, action: ToolAction, acceptedLicense = false) =>
    request<StudioTool[]>("tool_action", { id, input: { action, accepted_license: acceptedLicense } }),
  workspace: (project: string) => request<Workspace>("workspace", { project }),
  normalize: (input: TextInput) => request<{ text: string; mixed_language: boolean }>("normalize", { input }),
  speech: (project: string, input: Speech[]) => request<Job[]>("speech", { project, input }),
  draft: (project: string, input: Draft) => request<Draft>("save_draft", { project, input }),
  clearDraft: (project: string) => request("clear_draft", { project }),
  copyComposition: (project: string, input: Draft & { title: string }) => request<{ id: string; title: string }>("copy_composition", { project, input }),
  presets: () => request<Preset[]>("presets"),
  savePreset: (input: Omit<Preset, "id">) => request<Preset>("save_preset", { input }),
  deletePreset: (id: string) => request("delete_preset", { id }),
  import: (project: string, kind: "audio" | "image" | "background", input: Media) =>
    request<Asset>(`import_${kind}`, { project, input }),
  adopt: (project: string, id: string) => request<Asset>("adopt_narration", { project, id }),
  createProfile: (project: string, input: Reference) => request<VoiceProfile>("create_profile", { project, input }),
  record: (project: string, seconds: number) => request<{ content_base64: string }>("record", { project, input: { seconds } }),
  assignProfile: (project: string, id: string, assigned: boolean) =>
    request(assigned ? "assign_profile" : "unassign_profile", { project, id }),
  deleteProfile: (id: string) => request("delete_profile", { id }),
  waveform: (project: string, id: string) =>
    request<{ waveform: number[]; duration_seconds: number }>("analysis", { project, id }),
  preview: (project: string, input: Draft & { time: number }) =>
    request<{ image: string; time: number }>("preview", { project, input }),
  render: (project: string, input: Draft) => request<Job>("render", { project, input }),
  cancel: (project: string, id: string) => request<Job>("cancel", { project, id }),
  media: async (project: string | undefined, id: string, kind: FileKind = "original") =>
    convertFileSrc(await command<string>("studio_media", { project, id, kind })),
  download: (project: string | undefined, id: string, kind: FileKind, name: string) =>
    command<string>("studio_save_file", { project, id, kind, name }),
};

export async function readMedia(file: File): Promise<Media> {
  if (file.size > 32 * 1024 * 1024) throw new Error("Choose a file under 32 MiB.");
  const data = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.readAsDataURL(file);
  });
  return { name: file.name, content_base64: data };
}
