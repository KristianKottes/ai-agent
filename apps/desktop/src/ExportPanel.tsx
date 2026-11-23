import { useState } from "react";
import { useMutation, useMutationState, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Film, X } from "lucide-react";
import { api } from "./services/api";
import { SceneGenerator } from "./SceneGenerator";
import type { BackgroundMusic, DirectorPlan, DraftScene, ExportDraft, ExportScene, Narration, SceneAsset, VideoExportRequest } from "./types";

const fps = 24;
const maxScenes = 50;
const engines = ["musetalk", "illustrated"] as const;
const seconds = (frames: number) => (frames / fps).toFixed(2);

type SceneOption = Omit<DraftScene, "key" | "frames" | "transition">;
const emptyDraft: ExportDraft = {
  format: "landscape", quality: "standard", captions: true, scenes: [], music_id: null, music_volume: 0.12,
};

async function readMedia(file: File, description: string) {
  if (!file.size || file.size > 32 * 1024 * 1024) throw new Error(`Choose ${description} up to 32 MiB.`);
  const content_base64 = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(new Error("The selected file could not be read."));
    reader.readAsDataURL(file);
  });
  return { name: file.name.slice(0, 160), content_base64 };
}

async function readAsset(file: File) {
  const suffix = file.name.split(".").pop()?.toLowerCase();
  const kind = suffix === "png" ? "image" : suffix === "mp4" ? "video" : undefined;
  if (!kind) throw new Error("Choose a PNG or MP4 up to 32 MiB.");
  return { ...await readMedia(file, "a PNG or MP4"), kind } as const;
}

export function ExportPanel({ scriptId, narration, changed, director }: {
  scriptId: string;
  narration: Narration | null | undefined;
  changed: boolean;
  director?: DirectorPlan | null;
}) {
  const cache = useQueryClient();
  const [source, setSource] = useState("");
  const [selectedExport, setSelectedExport] = useState<string>();
  const [previewError, setPreviewError] = useState(false);
  const assetKey = ["scene-assets", scriptId];
  const musicKey = ["scene-music", scriptId];
  const exportKey = ["video-exports", scriptId];
  const draftKey = ["export-draft", scriptId];
  const saveKey = ["save-export-draft", scriptId];
  const assets = useQuery({ queryKey: assetKey, queryFn: () => api.sceneAssets(scriptId) });
  const music = useQuery({ queryKey: musicKey, queryFn: () => api.backgroundMusic(scriptId) });
  const exports = useQuery({ queryKey: exportKey, queryFn: () => api.videoExports(scriptId) });
  const draftQuery = useQuery({
    queryKey: draftKey, queryFn: () => api.exportDraft(scriptId), staleTime: Infinity, networkMode: "always",
  });
  const saveDraft = useMutation({
    mutationKey: saveKey,
    scope: { id: `export-draft:${scriptId}` },
    networkMode: "always",
    mutationFn: (draft: ExportDraft) => api.saveExportDraft(scriptId, draft),
  });
  const saves = useMutationState({
    filters: { mutationKey: saveKey, exact: true },
    select: ({ state }) => ({ status: state.status, error: state.error }),
  });
  const lastSave = saves[saves.length - 1];
  const presenters = useQueries({ queries: engines.map((engine) => ({
    queryKey: [engine === "musetalk" ? "portrait-render" : "avatar-render", narration?.id],
    queryFn: () => api.latestAvatar(narration!.id, engine),
    enabled: Boolean(narration),
  })) });
  const preview = useQuery({
    queryKey: ["export-preview", selectedExport],
    queryFn: () => api.exportPreview(selectedExport!),
    enabled: Boolean(selectedExport),
  });

  const draft = draftQuery.data ?? emptyDraft;
  const { scenes, format, quality, captions, music_id, music_volume } = draft;
  const budget = Math.min(Math.floor((narration?.duration_seconds ?? 0) * fps + 1e-6), 600 * fps);
  const total = scenes.reduce((sum, scene) => sum + scene.frames, 0);
  const surplus = budget - total;
  const hasPresenter = scenes[0]?.source === "presenter";
  const stalePresenter = hasPresenter && narration && scenes[0].presenter_narration_id !== narration.id;
  const missingAsset = assets.isSuccess && scenes.some((scene) =>
    scene.source === "asset" && !assets.data.some((asset) => asset.id === scene.id));
  const missingMusic = music_volume > 0 && music_id && music.isSuccess && !music.data.some((item) => item.id === music_id);
  const tooShortForFade = scenes.some((scene, index) => scene.frames <=
    (scene.transition === "fade_black" ? 6 : 0) + (scenes[index + 1]?.transition === "fade_black" ? 6 : 0));

  function option(asset: SceneAsset): SceneOption {
    return {
      id: asset.id, source: "asset", name: asset.name, max_frames: asset.max_frames, presenter_narration_id: null,
    };
  }

  const options: SceneOption[] = (assets.data ?? []).map(option);
  for (const [index, query] of presenters.entries()) {
    if (!hasPresenter && narration && query.data) options.push({
      id: query.data.id, source: "presenter",
      name: index === 0 ? "Saved portrait clip" : "Saved illustrated clip",
      max_frames: Math.floor(query.data.duration_seconds * fps + 1e-6),
      presenter_narration_id: narration.id,
    });
  }
  const selected = options.find((item) => item.id === source);

  function updateDraft(update: (current: ExportDraft) => ExportDraft) {
    const current = cache.getQueryData<ExportDraft>(draftKey);
    if (!current) return;
    const next = update(current);
    if (next === current) return;
    cache.setQueryData(draftKey, next);
    saveDraft.mutate(next);
  }

  function add(item: SceneOption) {
    updateDraft((current) => {
      const remaining = budget - current.scenes.reduce((sum, scene) => sum + scene.frames, 0);
      const frames = Math.min(item.max_frames ?? budget, 3 * fps, remaining);
      const addingPresenter = item.source === "presenter";
      if (frames < 1 || current.scenes.length >= maxScenes ||
          (addingPresenter && current.scenes[0]?.source === "presenter")) return current;
      const key = Math.max(-1, ...current.scenes.map((scene) => scene.key)) + 1;
      const scene: DraftScene = { ...item, frames, key, transition: "cut" };
      return { ...current, scenes: addingPresenter ? [scene, ...current.scenes] : [...current.scenes, scene] };
    });
    setSource("");
  }

  const importAsset = useMutation({
    mutationFn: async (file: File) => api.importSceneAsset(scriptId, await readAsset(file)),
    onSuccess: (asset) => {
      cache.setQueryData<SceneAsset[]>(assetKey, (current) => [...(current ?? []), asset]);
      void cache.invalidateQueries({ queryKey: assetKey });
      add(option(asset));
    },
  });
  const importMusic = useMutation({
    mutationFn: async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".wav")) throw new Error("Choose a WAV file up to 32 MiB.");
      return api.importBackgroundMusic(scriptId, await readMedia(file, "a WAV file"));
    },
    onSuccess: (item) => {
      cache.setQueryData<BackgroundMusic[]>(musicKey, (current) => [...(current ?? []), item]);
      void cache.invalidateQueries({ queryKey: musicKey });
      updateDraft((current) => ({ ...current, music_id: item.id }));
    },
  });
  const exportVideo = useMutation({
    mutationFn: (input: VideoExportRequest) => api.exportVideo(scriptId, input),
    onSuccess: (result) => {
      void cache.invalidateQueries({ queryKey: exportKey });
      setPreviewError(false);
      setSelectedExport(result.id);
    },
  });
  const busy = !draftQuery.data || exportVideo.isPending || importAsset.isPending || importMusic.isPending;
  const error = exportVideo.error ?? importAsset.error ?? importMusic.error ?? assets.error ?? music.error ?? exports.error
    ?? preview.error ?? draftQuery.error ?? presenters.find((query) => query.isError)?.error;

  function move(index: number, offset: number) {
    const reordered = [...scenes];
    [reordered[index], reordered[index + offset]] = [reordered[index + offset], reordered[index]];
    applyOrder(reordered);
  }

  function applyOrder(reordered: DraftScene[]) {
    updateDraft((current) => ({
      ...current, scenes: reordered.map((scene, index) => index === 0 ? { ...scene, transition: "cut" } : scene),
    }));
  }

  function edit(key: number, change: Partial<Pick<DraftScene, "frames" | "transition">>) {
    updateDraft((current) => ({
      ...current, scenes: current.scenes.map((scene) => scene.key === key ? { ...scene, ...change } : scene),
    }));
  }

  return <section className="avatar-panel export-panel" aria-labelledby="export-heading">
    <div className="voice-heading">
      <Film size={19} />
      <div><h3 id="export-heading">Video export</h3><p>Arrange scenes over your saved narration.</p></div>
    </div>
    <SceneGenerator scriptId={scriptId} director={changed ? undefined : director} />
    {draftQuery.isPending && <p role="status">Loading timeline…</p>}
    {draftQuery.isError && <button type="button" className="secondary-button" onClick={() => draftQuery.refetch()}>
      Retry loading timeline
    </button>}
    {(!narration || changed) && <p>Save the script and generate its narration to export a new video.</p>}
    {narration && <>
      <div className="voice-controls">
        <label>Import scene · PNG or MP4
          <input type="file" accept=".png,.mp4,image/png,video/mp4" disabled={busy || assets.isPending}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) importAsset.mutate(file);
              event.target.value = "";
            }} />
        </label>
        <label>Saved scene or presenter
          <select value={source} disabled={busy} onChange={(event) => setSource(event.target.value)}>
            <option value="">Choose a source</option>
            {options.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <button type="button" className="secondary-button" disabled={!selected || busy || surplus < 1 || scenes.length >= maxScenes}
          onClick={() => selected && add(selected)}>Add scene</button>
      </div>
      {importAsset.isPending && <p role="status">Importing scene…</p>}
      {scenes.length > 0 && <ol className="export-scenes">{scenes.map((scene, index) => <li key={scene.key}>
        <div><strong>{scene.name}</strong><small>{seconds(scene.frames)} s</small></div>
        <label>Duration
          <input type="range" min={1} max={Math.max(scene.frames, Math.min(scene.max_frames ?? budget, budget))}
            value={scene.frames} disabled={busy}
            aria-label={`Duration of ${scene.name}, scene ${index + 1}`}
            aria-valuetext={`${seconds(scene.frames)} seconds`}
            onChange={(event) => edit(scene.key, { frames: Number(event.target.value) })} />
        </label>
        {index > 0 && <label>Transition
          <select value={scene.transition} disabled={busy}
            onChange={(event) => edit(scene.key, { transition: event.target.value as ExportScene["transition"] })}>
            <option value="cut">Cut</option>
            <option value="fade_black">Fade through black</option>
          </select>
        </label>}
        <div className="export-actions">
          <button type="button" className="icon-action" aria-label={`Move scene ${index + 1} up`}
            disabled={busy || index === 0 || scenes[index - 1].source === "presenter"} onClick={() => move(index, -1)}><ArrowUp size={14} /></button>
          <button type="button" className="icon-action" aria-label={`Move scene ${index + 1} down`}
            disabled={busy || index === scenes.length - 1 || scene.source === "presenter"} onClick={() => move(index, 1)}><ArrowDown size={14} /></button>
          <button type="button" className="icon-action" aria-label={`Remove scene ${index + 1}`}
            disabled={busy} onClick={() => applyOrder(scenes.filter((item) => item.key !== scene.key))}><X size={14} /></button>
        </div>
      </li>)}</ol>}
      {hasPresenter && !stalePresenter && <p>The presenter opens the video so its lip sync matches the start of the narration.</p>}
      {stalePresenter && <p role="status">Remove the presenter from the previous narration before exporting. You can add a new presenter clip for this narration.</p>}
      {missingAsset && <p role="status">Remove the missing scene before exporting, or import its file again.</p>}
      <p>{seconds(total)} s of {seconds(budget)} s narration used.</p>
      {surplus < 0 && <p role="status">Shorten the timeline to fit the narration.</p>}
      {tooShortForFade && scenes.length > 0 && <p role="status">Lengthen the scenes around the fade or choose Cut.</p>}
      <div className="voice-controls">
        <label>Background WAV
          <input type="file" accept=".wav,audio/wav" disabled={busy || music.isPending} onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) importMusic.mutate(file);
            event.target.value = "";
          }} />
        </label>
        <label>Music
          <select value={music_id ?? ""} disabled={busy} onChange={(event) =>
            updateDraft((current) => ({ ...current, music_id: event.target.value || null }))}>
            <option value="">No music</option>
            {missingMusic && <option value={music_id!}>Missing track</option>}
            {music.data?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        {music_id && <label>Music level · {Math.round(music_volume * 100)}%
          <input type="range" aria-label="Background music level" min={0} max={35} value={Math.round(music_volume * 100)}
            disabled={busy} onChange={(event) => updateDraft((current) => ({
              ...current, music_volume: Number(event.target.value) / 100,
            }))} />
        </label>}
      </div>
      {missingMusic && <p role="status">Choose another background track or select No music.</p>}
      <div className="voice-controls">
        <label>Format
          <select value={format} disabled={busy}
            onChange={(event) => updateDraft((current) => ({ ...current, format: event.target.value as typeof format }))}>
            <option value="landscape">Landscape · 1920 × 1080</option>
            <option value="vertical">Vertical · 1080 × 1920</option>
            <option value="square">Square · 1080 × 1080</option>
          </select>
        </label>
        <label>Quality
          <select value={quality} disabled={busy}
            onChange={(event) => updateDraft((current) => ({ ...current, quality: event.target.value as typeof quality }))}>
            <option value="draft">Draft</option><option value="standard">Standard</option><option value="high">High</option>
          </select>
        </label>
        <label className="export-caption-toggle"><input type="checkbox" checked={captions} disabled={busy}
          onChange={(event) => updateDraft((current) => ({ ...current, captions: event.target.checked }))} /> English captions</label>
        <button type="button" className="primary-button"
          disabled={busy || changed || !scenes.length || surplus < 0 || tooShortForFade || Boolean(stalePresenter) || missingAsset || Boolean(missingMusic)}
          onClick={() => exportVideo.mutate({
            narration_id: narration.id, format, quality, captions, music_id, music_volume,
            scenes: scenes.map(({ id, source: kind, frames, transition }) => ({ id, source: kind, frames, transition })),
          })}>
          <Film size={14} /> {exportVideo.isPending ? "Exporting…" : "Export MP4"}
        </button>
      </div>
      {exportVideo.isPending && <p role="status">Rendering your scenes and checking the MP4. Keep the app open.</p>}
    </>}
    {lastSave?.status === "pending" && <p role="status">Saving timeline…</p>}
    {lastSave?.status === "success" && <p role="status">Timeline saved.</p>}
    {lastSave?.status === "error" && <div className="voice-result">
      <p className="settings-error" role="alert">The timeline could not be saved. {lastSave.error?.message}</p>
      <button type="button" className="secondary-button" onClick={() => saveDraft.mutate(draft)}>Retry saving timeline</button>
    </div>}
    {error && <p className="settings-error" role="alert">{error.message}</p>}
    {Boolean(exports.data?.length) && <div className="export-history">
      <h4>Saved exports</h4>
      {exports.data?.map((result, index) => <div className="voice-result" key={result.id}>
        <p><strong>{index === 0 ? "Latest MP4" : "MP4"}</strong> · {result.width} × {result.height} · {result.duration_seconds.toFixed(1)} s
          {result.caption_count > 0 && ` · ${result.caption_count} captions`}</p>
        <span className="voice-path">{result.path}</span>
        {Object.entries(result.subtitle_files ?? {}).map(([format, path]) => <span className="voice-path" key={format}>
          {format.toUpperCase()} · {path}
        </span>)}
        {result.id !== selectedExport && <button type="button" className="secondary-button" onClick={() => {
          setPreviewError(false); setSelectedExport(result.id);
        }}>Load export</button>}
        {result.id === selectedExport && preview.isFetching && <p role="status">Loading export…</p>}
        {result.id === selectedExport && preview.isError && <button type="button" className="secondary-button"
          onClick={() => preview.refetch()}>Try loading again</button>}
        {result.id === selectedExport && preview.data && <video controls playsInline key={preview.data} src={preview.data}
          aria-label="Saved video export" onError={() => setPreviewError(true)} />}
        {result.id === selectedExport && previewError && <p role="alert">The preview failed. Open the MP4 using its saved path.</p>}
      </div>)}
    </div>}
  </section>;
}
