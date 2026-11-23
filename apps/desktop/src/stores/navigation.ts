import { create } from "zustand";

type AudioScreen = "voice" | "cloning" | "visualizer";
export type MediaScreen = "images" | "video" | "illustrated";
type Screen = "dashboard" | "discover" | "runtime" | "settings" | "generation_settings" | AudioScreen | MediaScreen;

interface NavigationStore {
  screen: Screen;
  studioProject?: string;
  studioAsset?: string;
  mediaProject?: string;
  mediaAsset?: string;
  mediaScene?: string;
  mediaPrompt?: string;
  generationProject?: string;
  openGenerationSettings: (project: string) => void;
  openMediaStudio: (screen: MediaScreen, project: string, asset?: string, scene?: string, prompt?: string) => void;
  clearMediaAsset: () => void;
  setScreen: (screen: Screen) => void;
  openAudioStudio: (screen: AudioScreen, project: string, asset?: string) => void;
}

export const useNavigation = create<NavigationStore>((set) => ({
  screen: "dashboard",
  setScreen: (screen) => set({ screen }),
  openGenerationSettings: (generationProject) => set({ screen: "generation_settings", generationProject }),
  openMediaStudio: (screen, mediaProject, mediaAsset, mediaScene, mediaPrompt) => set({ screen, mediaProject, mediaAsset, mediaScene, mediaPrompt }),
  clearMediaAsset: () => set({ mediaAsset: undefined, mediaScene: undefined, mediaPrompt: undefined }),
  openAudioStudio: (screen, studioProject, studioAsset) => set({ screen, studioProject, studioAsset }),
}));
