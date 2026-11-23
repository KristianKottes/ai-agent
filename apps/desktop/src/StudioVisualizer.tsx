import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Film, Pause, Play, Redo2, Save, Undo2 } from "lucide-react";
import { useNavigation } from "./stores/navigation";
import { readMedia, studio, type Composition, type Draft, type Layer, type LayerProperty, type Preset, type VisualStyle, type Workspace } from "./services/studio";
import { AssetPreview, FileExports, presetsKey, projectStudioKey, StudioError, StudioJobs, Waveform } from "./StudioParts";

const exportDefaults = { format: "mp4" as const, transparent: false };
const labels: Record<VisualStyle, string> = {
  spectrum: "Circular spectrum", waveform: "Waveform", bars: "Frequency bars", particles: "Particles",
  vinyl: "Vinyl record", ambient: "Minimal ambient", motion: "Motion graphics",
};
const numericFields: { property: LayerProperty; label: string; min: number; max: number; step: number }[] = [
  { property: "x", label: "Horizontal position", min: -1, max: 2, step: .01 },
  { property: "y", label: "Vertical position", min: -1, max: 2, step: .01 },
  { property: "scale", label: "Scale", min: .01, max: 4, step: .01 },
  { property: "rotation", label: "Rotation (°)", min: -3600, max: 3600, step: 1 },
  { property: "opacity", label: "Opacity", min: 0, max: 1, step: .01 },
  { property: "blur", label: "Blur", min: 0, max: 40, step: 1 },
  { property: "glow", label: "Glow", min: 0, max: 40, step: 1 },
  { property: "reveal", label: "Mask reveal", min: 0, max: 1, step: .01 },
];
const dimensions = { "16:9": [1280, 720], "9:16": [720, 1280], "1:1": [1080, 1080] };
const timecode = (seconds: number) => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;

function newLayer(): Layer {
  return { id: `layer-${crypto.randomUUID()}`.slice(0, 48), kind: "text", text: "Title", x: .5, y: .28, scale: 1,
    rotation: 0, opacity: 1, blur: 0, glow: 0, reveal: 1, start: 0, end: null, color: null, keyframes: {}, colors: [], bindings: [] };
}

type History = { past: Draft[]; present: Draft; future: Draft[] };
export function Visualizer({ project, workspace, presets, handoff, active }: {
  project: string; workspace: Workspace; presets: Preset[]; handoff?: string; active: boolean;
}) {
  const client = useQueryClient();
  const openAudioStudio = useNavigation((state) => state.openAudioStudio);
  const [presetId, setPresetId] = useState(presets[0].id);
  const [history, setHistory] = useState<History>(() => ({
    past: [], present: workspace.draft ?? { asset_id: null, composition: presets[0].composition, settings: exportDefaults }, future: [],
  }));
  const [layerId, setLayerId] = useState("title");
  const [presetName, setPresetName] = useState("");
  const [copyTitle, setCopyTitle] = useState("");
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [problem, setProblem] = useState<Error>();
  const mediaRef = useRef<HTMLAudioElement>(null);

  const draft = history.present;
  const composition = draft.composition;
  const audioAssets = workspace.assets.filter((asset) => asset.kind === "audio");
  const input = audioAssets.find((asset) => asset.id === draft.asset_id) ?? audioAssets[0];
  const duration = input?.duration_seconds ?? 0;
  const selectedLayer = composition.layers.find((layer) => layer.id === layerId);
  const selectedPreset = presets.find((preset) => preset.id === presetId);
  const output = { ...draft, asset_id: input?.id ?? null };
  const ratio = Object.keys(dimensions).find((key) => {
    const [w, h] = dimensions[key as keyof typeof dimensions];
    return w * composition.height === h * composition.width;
  }) ?? "custom";

  function apply(update: (state: Draft) => Draft) {
    setHistory((h) => ({ past: [...h.past.slice(-39), h.present], present: update(h.present), future: [] }));
  }
  function update(patch: Partial<Composition>) {
    apply((state) => ({ ...state, composition: { ...state.composition, ...patch } }));
  }
  function undo() {
    setHistory((h) => h.past.length ? { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] } : h);
  }
  function redo() {
    setHistory((h) => h.future.length ? { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) } : h);
  }
  function setAudio(id: string) {
    setTime(0);
    setPlaying(false);
    apply((state) => ({ ...state, asset_id: id }));
  }
  useEffect(() => { if (handoff) setAudio(handoff); }, [handoff]);
  useEffect(() => { if (!active) mediaRef.current?.pause(); }, [active]);
  useEffect(() => {
    if (!active || !playing) return;
    const timer = window.setInterval(() => {
      if (mediaRef.current) setTime(mediaRef.current.currentTime);
    }, 125);
    return () => window.clearInterval(timer);
  }, [playing, active]);

  const refresh = () => client.invalidateQueries({ queryKey: projectStudioKey(project) });
  const saveDraft = useMutation({ mutationFn: () => studio.draft(project, output), onSuccess: refresh });
  const clearDraft = useMutation({ mutationFn: () => studio.clearDraft(project), onSuccess: async () => {
    setHistory({ past: [], present: { asset_id: null, composition: presets[0].composition, settings: exportDefaults }, future: [] });
    setPresetId(presets[0].id);
    setTime(0);
    await refresh();
  } });
  const duplicate = useMutation({
    mutationFn: () => studio.copyComposition(project, { ...output, title: copyTitle.trim() }),
    onSuccess: async ({ id }) => {
      await client.invalidateQueries({ queryKey: ["projects"] });
      openAudioStudio("visualizer", id);
    },
  });
  const render = useMutation({ mutationFn: () => studio.render(project, output), onSuccess: refresh });
  const createPreset = useMutation({
    mutationFn: (name: string) => studio.savePreset({ name, composition, settings: draft.settings }),
    onSuccess: async (preset) => {
      setPresetName(""); setPresetId(preset.id);
      await client.invalidateQueries({ queryKey: presetsKey });
    },
  });
  const deletePreset = useMutation({ mutationFn: studio.deletePreset,
    onSuccess: async () => { setPresetId(presets[0].id); await client.invalidateQueries({ queryKey: presetsKey }); } });
  const importAsset = useMutation({
    mutationFn: async ({ file, kind }: { file: File; kind: "audio" | "image" | "background" }) => studio.import(project, kind, await readMedia(file)),
    onSuccess: async (asset) => {
      if (asset.kind === "audio") setAudio(asset.id);
      else if (asset.kind === "background") update({ backdrop: asset.id });
      await refresh();
    },
  });
  const media = useQuery({
    queryKey: ["studio-media", project, input?.id], queryFn: () => studio.media(project, input!.id), enabled: Boolean(active && input),
  });
  const waveform = useQuery({
    queryKey: ["studio-wave", project, input?.id], queryFn: () => studio.waveform(project, input!.id), enabled: Boolean(active && input),
  });
  const frameTime = Math.min(time, duration);
  const previewRequest = { ...output, time: frameTime };
  const frame = useQuery({
    queryKey: ["studio-preview", project, previewRequest], queryFn: () => studio.preview(project, previewRequest),
    enabled: Boolean(active && input), placeholderData: keepPreviousData, gcTime: 20_000, staleTime: Infinity, retry: false,
  });

  function selectPreset(preset: Preset) {
    setPresetId(preset.id);
    setLayerId(preset.composition.layers.at(-1)?.id ?? "");
    apply((state) => ({ ...state, composition: preset.composition, settings: preset.settings ?? exportDefaults }));
  }
  function upload(event: ChangeEvent<HTMLInputElement>, kind: "audio" | "image" | "background") {
    const file = event.target.files?.[0];
    if (file) importAsset.mutate({ file, kind });
    event.target.value = "";
  }
  function seek(next: number) {
    setTime(next);
    if (mediaRef.current) mediaRef.current.currentTime = next;
  }
  function play() {
    const audio = mediaRef.current;
    if (!audio) return;
    if (audio.paused) void audio.play().catch((e: Error) => setProblem(e));
    else audio.pause();
  }
  function editLayer(patch: Partial<Layer>) {
    update({ layers: composition.layers.map((layer) => layer.id === layerId ? { ...layer, ...patch } : layer) });
  }
  function moveLayer(delta: number) {
    const index = composition.layers.findIndex((layer) => layer.id === layerId);
    const dest = index + delta;
    if (index < 0 || dest < 0 || dest >= composition.layers.length) return;
    const layers = [...composition.layers];
    [layers[index], layers[dest]] = [layers[dest], layers[index]];
    update({ layers });
  }

  return <div className="studio-visualizer">
    <div className="studio-editor-bar">
      <label>Audio source<select value={input?.id ?? ""} onChange={(e) => setAudio(e.target.value)}>
        {!audioAssets.length && <option value="">No project audio yet</option>}
        {audioAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name} · {asset.duration_seconds?.toFixed(1)} s</option>)}
      </select></label>
      <label className="studio-file">Import MP3 or WAV<input type="file" accept=".mp3,.wav" onChange={(e) => upload(e, "audio")} /></label>
      <button className="secondary-button" aria-label="Undo" disabled={!history.past.length} onClick={undo}><Undo2 size={16} /></button>
      <button className="secondary-button" aria-label="Redo" disabled={!history.future.length} onClick={redo}><Redo2 size={16} /></button>
      <button className="secondary-button" disabled={saveDraft.isPending} onClick={() => saveDraft.mutate()}><Save size={15} />Save project</button>
      <button className="primary-button" disabled={!input || render.isPending} onClick={() => render.mutate()}><Film size={15} />Queue render</button>
      {saveDraft.isSuccess && <small role="status">Project saved</small>}
    </div>
    <StudioError error={problem ?? saveDraft.error ?? render.error ?? importAsset.error ?? clearDraft.error ?? duplicate.error ?? createPreset.error ?? deletePreset.error ?? waveform.error ?? frame.error ?? media.error} />
    <div className="studio-editor-grid">
      <aside className="studio-card studio-templates" aria-label="Templates">
        <h2>Templates</h2>
        <div className="studio-template-list">
          {presets.map((preset) => <button key={preset.id} className={preset.id === presetId ? "selected" : ""}
            onClick={() => selectPreset(preset)} aria-pressed={presetId === preset.id}>
            <span style={{ background: `linear-gradient(135deg, ${preset.composition.background}, ${preset.composition.secondary})` }} />
            <span>{preset.name}</span>
          </button>)}
        </div>
        <label>Preset name<input value={presetName} maxLength={100} onChange={(e) => setPresetName(e.target.value)} /></label>
        <button className="secondary-button" disabled={!presetName.trim() || createPreset.isPending} onClick={() => createPreset.mutate(presetName.trim())}>Save preset</button>
        {selectedPreset && <button className="secondary-button" disabled={createPreset.isPending}
          onClick={() => createPreset.mutate((presetName.trim() || selectedPreset.name + " copy").slice(0, 100))}>Duplicate preset</button>}
        {selectedPreset?.custom && <button className="danger-button" disabled={deletePreset.isPending} onClick={() => deletePreset.mutate(selectedPreset.id)}>Delete preset</button>}
      </aside>
      <section className="studio-card studio-stage" aria-label="Composition preview">
        <div className="studio-monitor" style={{ aspectRatio: `${composition.width}/${composition.height}` }}>
          {input && frame.data ? <img src={frame.data.image} alt="Current visualizer frame" /> : <div>
            <Film size={28} /><span>{input ? "Preparing preview…" : "Choose audio to preview this composition"}</span>
          </div>}
        </div>
        {media.data && <audio key={input?.id} src={media.data} ref={mediaRef} preload="auto"
          onPlay={() => setPlaying(true)} onPause={() => { setPlaying(false); if (mediaRef.current) setTime(mediaRef.current.currentTime); }}
          onEnded={() => setPlaying(false)} />}
        <div className="studio-transport">
          <button className="secondary-button" disabled={!media.data} onClick={play} aria-label={playing ? "Pause" : "Play"}>
            {playing ? <Pause size={16} /> : <Play size={16} />}
          </button>
          <span>{timecode(time)} / {timecode(duration)}</span>
          <span>{composition.width} × {composition.height} · {composition.fps} fps</span>
        </div>
      </section>
      <aside className="studio-card studio-properties" aria-label="Composition properties">
        <h2>Properties</h2>
        <label>Style<select value={composition.style} onChange={(e) => update({ style: e.target.value as VisualStyle })}>
          {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <div className="studio-colors">
          {(["background", "primary", "secondary"] as const).map((key) => <label key={key}>{key}
            <input type="color" value={composition[key]} onChange={(e) => update({ [key]: e.target.value })} /></label>)}
        </div>
        <label>Audio reaction<input type="range" min="0" max="3" step=".05" value={composition.intensity} onChange={(e) => update({ intensity: Number(e.target.value) })} /></label>
        <label>Glow<input type="range" min="0" max="1" step=".05" value={composition.glow} onChange={(e) => update({ glow: Number(e.target.value) })} /></label>
        {["bars", "spectrum", "vinyl"].includes(composition.style) && <label>Frequency bars<input type="number" min="8" max="96" value={composition.bar_count}
          onChange={(e) => update({ bar_count: Math.max(8, Math.min(96, Number(e.target.value))) })} /></label>}
        {["bars", "waveform"].includes(composition.style) && <label className="studio-check"><input type="checkbox" checked={composition.mirrored}
          onChange={(e) => update({ mirrored: e.target.checked })} />Mirror</label>}
        {composition.style === "waveform" && <label className="studio-check"><input type="checkbox" checked={composition.filled}
          onChange={(e) => update({ filled: e.target.checked })} />Fill waveform</label>}
        {composition.style === "bars" && <label className="studio-check"><input type="checkbox" checked={composition.peaks}
          onChange={(e) => update({ peaks: e.target.checked })} />Peak indicators</label>}
        <label className="studio-check"><input type="checkbox" checked={composition.camera} onChange={(e) => update({ camera: e.target.checked })} />Camera drift</label>
        <label className="studio-check"><input type="checkbox" checked={composition.motion_blur} onChange={(e) => update({ motion_blur: e.target.checked })} />Shape motion blur</label>
        <label className="studio-check"><input type="checkbox" checked={composition.captions} onChange={(e) => update({ captions: e.target.checked })} />Show timed captions</label>
        <details className="studio-details"><summary>Images and background</summary>
          <label>Logo or artwork<select value={composition.artwork ?? ""} onChange={(e) => update({ artwork: e.target.value || null })}>
            <option value="">None</option>{workspace.assets.filter((item) => item.kind === "image").map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select></label>
          <label>Background<select value={composition.backdrop ?? ""} onChange={(e) => update({ backdrop: e.target.value || null })}>
            <option value="">Gradient</option>{workspace.assets.filter((item) => item.kind === "image" || item.kind === "background").map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select></label>
          <label className="studio-file">Import image<input type="file" accept=".png,.jpg,.jpeg,.webp" onChange={(e) => upload(e, "image")} /></label>
          <label className="studio-file">Import background video<input type="file" accept=".mp4,.mov,.webm" onChange={(e) => upload(e, "background")} /></label>
        </details>
        <details className="studio-details"><summary>Export settings</summary>
          <label>Aspect ratio<select value={ratio} onChange={(e) => {
            const size = dimensions[e.target.value as keyof typeof dimensions];
            if (size) update({ width: size[0], height: size[1] });
            else update({ width: 1024, height: 768 });
          }}><option>16:9</option><option>9:16</option><option>1:1</option><option value="custom">Custom</option></select></label>
          <div className="studio-form-grid">
            <label>Width<input type="number" min="240" max="3840" step="2" value={composition.width} onChange={(e) => update({ width: Number(e.target.value) })} /></label>
            <label>Height<input type="number" min="240" max="3840" step="2" value={composition.height} onChange={(e) => update({ height: Number(e.target.value) })} /></label>
            <label>FPS<input type="number" min="12" max="60" value={composition.fps} onChange={(e) => update({ fps: Number(e.target.value) })} /></label>
          </div>
          <label>Format<select value={draft.settings.format} onChange={(e) => {
            const format = e.target.value as Draft["settings"]["format"];
            apply((state) => ({ ...state, settings: { format, transparent: format !== "mp4" && state.settings.transparent } }));
          }}><option value="mp4">MP4</option><option value="webm">WebM</option><option value="png">PNG frames (ZIP)</option></select></label>
          {draft.settings.format !== "mp4" && <label className="studio-check"><input type="checkbox" checked={draft.settings.transparent}
            onChange={(e) => apply((state) => ({ ...state, settings: { ...state.settings, transparent: e.target.checked } }))} />Transparent background</label>}
        </details>
      </aside>
      <section className="studio-card studio-timeline" aria-label="Timeline and layers">
        <div className="studio-card-heading"><h2>Timeline</h2><span>{timecode(time)}</span><button className="secondary-button" disabled={composition.layers.length >= 12} onClick={() => {
          const layer = newLayer(); update({ layers: [...composition.layers, layer] }); setLayerId(layer.id);
        }}>Add layer</button></div>
        <label className="studio-seek">Seek timeline<input type="range" min="0" max={duration || 1} step=".01" value={Math.min(time, duration)}
          disabled={!duration} onChange={(e) => seek(Number(e.target.value))} /></label>
        <div className="studio-track-grid">
          <span>Audio</span><div className="studio-track">{waveform.data && <Waveform values={waveform.data.waveform} />}</div>
          {composition.layers.map((layer) => <div className="studio-layer-row" key={layer.id}>
            <button className={layerId === layer.id ? "selected" : ""} aria-pressed={layerId === layer.id}
              onClick={() => setLayerId(layer.id)}>{layer.text || layer.kind}</button>
            <div className="studio-track">
              <span className="studio-layer-clip" style={{ left: `${duration ? Math.min(100, 100 * layer.start / duration) : 0}%`,
                width: `${duration ? Math.max(0, 100 * (Math.min(layer.end ?? duration, duration) - layer.start) / duration) : 100}%` }} />
            </div>
          </div>)}
        </div>
        {selectedLayer && <LayerEditor key={selectedLayer.id} layer={selectedLayer} time={time}
          color={composition.primary} edit={editLayer} move={moveLayer} remove={() => {
            update({ layers: composition.layers.filter((layer) => layer.id !== layerId) }); setLayerId("");
          }} />}
      </section>
    </div>
    <details className="studio-card studio-details studio-project-actions">
      <summary>Project actions</summary>
      <div className="studio-actions">
        <label>New project name<input value={copyTitle} maxLength={120} onChange={(e) => setCopyTitle(e.target.value)} /></label>
        <button className="secondary-button" disabled={!copyTitle.trim() || duplicate.isPending}
          onClick={() => duplicate.mutate()}>Copy composition to new project</button>
        <button className="danger-button" disabled={!workspace.draft || clearDraft.isPending} onClick={() => {
          if (window.confirm("Delete the saved composition from this project? Your audio and exports will remain.")) clearDraft.mutate();
        }}>Delete saved composition</button>
      </div>
    </details>
    <StudioJobs project={project} jobs={workspace.jobs} kind="render" />
    {workspace.assets.some((asset) => asset.kind === "export") && <section className="studio-card studio-history">
      <h2>Video exports</h2>
      {workspace.assets.filter((asset) => asset.kind === "export").map((asset) => {
        const source = workspace.assets.find((item) => item.id === asset.source_id);
        return <article key={asset.id}><strong>{asset.name} · {asset.extension?.toUpperCase()}{asset.research_only ? " · Noncommercial evaluation" : ""}</strong>
          {asset.extension !== "zip" && <AssetPreview project={project} asset={asset} />}
          <FileExports project={project} asset={{ ...asset, cues: source?.cues }} /></article>;
      })}
    </section>}
  </div>;
}

function LayerEditor({ layer, time, color, edit, move, remove }: {
  layer: Layer; time: number; color: string; edit: (patch: Partial<Layer>) => void; move: (delta: number) => void; remove: () => void;
}) {
  const [property, setProperty] = useState<LayerProperty | "color">("opacity");
  const [easing, setEasing] = useState<NonNullable<Layer["keyframes"]["opacity"]>[number]["easing"]>("ease_out");
  const track = property === "color" ? layer.colors : layer.keyframes[property] ?? [];
  const binding = layer.bindings[0];
  function setKeyframe() {
    const timestamp = Math.max(0, Math.round((time - layer.start) * 100) / 100);
    if (property === "color") {
      edit({ colors: [...layer.colors.filter((k) => k.time !== timestamp), { time: timestamp, value: layer.color ?? color, easing }].sort((a, b) => a.time - b.time) });
    } else {
      const keys = layer.keyframes[property] ?? [];
      edit({ keyframes: { ...layer.keyframes, [property]: [...keys.filter((k) => k.time !== timestamp), {
        time: timestamp, value: layer[property], easing,
      }].sort((a, b) => a.time - b.time) } });
    }
  }
  function removeKeyframe(timestamp: number) {
    if (property === "color") edit({ colors: layer.colors.filter((k) => k.time !== timestamp) });
    else edit({ keyframes: { ...layer.keyframes, [property]: (layer.keyframes[property] ?? []).filter((k) => k.time !== timestamp) } });
  }

  return <details className="studio-details studio-layer-editor" open>
    <summary>Selected layer</summary>
    <div className="studio-actions">
      <button className="secondary-button" onClick={() => move(-1)} aria-label="Move layer down in stack"><ArrowDown size={14} /></button>
      <button className="secondary-button" onClick={() => move(1)} aria-label="Move layer up in stack"><ArrowUp size={14} /></button>
      <button className="danger-button" onClick={remove}>Remove layer</button>
    </div>
    <div className="studio-form-grid">
      <label>Type<select value={layer.kind} onChange={(e) => edit({ kind: e.target.value as Layer["kind"] })}>
        <option value="text">Text</option><option value="ring">Ring</option><option value="diamond">Diamond</option>
      </select></label>
      {layer.kind === "text" && <label>Title<input value={layer.text} maxLength={240} onChange={(e) => edit({ text: e.target.value })} /></label>}
      <label>Color<input type="color" value={layer.color ?? color} onChange={(e) => edit({ color: e.target.value })} /></label>
      <label>Start (s)<input type="number" min="0" max="1200" step=".1" value={layer.start} onChange={(e) => edit({ start: Number(e.target.value) })} /></label>
      <label>End (s)<input type="number" min={layer.start + .1} max="1200" step=".1" value={layer.end ?? ""}
        onChange={(e) => edit({ end: e.target.value ? Number(e.target.value) : null })} /></label>
    </div>
    <div className="studio-form-grid studio-layer-numbers">
      {numericFields.map((field) => <label key={field.property}>{field.label}<input type="number" min={field.min} max={field.max}
        step={field.step} value={layer[field.property]} onChange={(e) => edit({ [field.property]: Number(e.target.value) })} /></label>)}
    </div>
    <details className="studio-details"><summary>Keyframes and audio binding</summary>
      <div className="studio-form-grid">
        <label>Keyframe property<select value={property} onChange={(e) => setProperty(e.target.value as typeof property)}>
          {numericFields.map((field) => <option key={field.property} value={field.property}>{field.label}</option>)}<option value="color">Color</option>
        </select></label>
        <label>Easing<select value={easing} onChange={(e) => setEasing(e.target.value as typeof easing)}>
          <option value="linear">Linear</option><option value="ease_in">Ease in</option><option value="ease_out">Ease out</option><option value="ease_in_out">Ease in and out</option>
        </select></label>
      </div>
      <button className="secondary-button" disabled={track.length >= 64} onClick={setKeyframe}>Set keyframe at playhead</button>
      <div className="studio-actions">{track.map((key) => <button className="secondary-button" key={key.time}
        aria-label={`Remove keyframe at ${key.time} seconds`} onClick={() => removeKeyframe(key.time)}>{key.time.toFixed(2)} s ×</button>)}</div>
      <div className="studio-form-grid">
        <label>Audio signal<select value={binding?.source ?? ""} onChange={(e) => edit({ bindings: e.target.value ? [{
          source: e.target.value as NonNullable<typeof binding>["source"], property: binding?.property ?? "scale", strength: binding?.strength ?? .2,
        }, ...layer.bindings.slice(1)] : [] })}><option value="">None</option><option value="rms">Loudness (RMS)</option>
          <option value="peak">Peak</option><option value="bass">Bass</option><option value="treble">Treble</option></select></label>
        {binding && <>
          <label>Reactive property<select value={binding.property} onChange={(e) => edit({ bindings: [{
            ...binding, property: e.target.value as LayerProperty,
          }, ...layer.bindings.slice(1)] })}>{numericFields.map((field) => <option key={field.property} value={field.property}>{field.label}</option>)}</select></label>
          <label>Strength<input type="number" min="-360" max="360" step=".05" value={binding.strength} onChange={(e) =>
            edit({ bindings: [{ ...binding, strength: Number(e.target.value) }, ...layer.bindings.slice(1)] })} /></label>
        </>}
      </div>
    </details>
  </details>;
}
