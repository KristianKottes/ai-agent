import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Copy, Plus, Redo2, Scissors, Trash2, Undo2 } from "lucide-react";
import { AssetPreview, ImportMedia, MediaJobs } from "./MediaParts";
import { pending, projectStudioKey, StudioError, useStudioTools } from "./StudioParts";
import { media, newScene, type AudioClip, type Cue, type Scene, type Timeline, type VideoSettings } from "./services/media";
import { languageName, studio, type Asset, type Language, type StudioTool, type Workspace } from "./services/studio";

const formats = [
  ["16:9 · YouTube", 16, 9], ["9:16 · Shorts, TikTok, Reels", 9, 16], ["1:1 · Square", 1, 1],
  ["4:5 · Social", 4, 5], ["21:9 · Cinematic", 21, 9],
] as const;
const animations = { still: "Still", ken_burns: "Ken Burns", zoom_in: "Zoom in", zoom_out: "Zoom out",
  pan_left: "Pan left", pan_right: "Pan right", pan_up: "Pan up", pan_down: "Pan down", fade: "Fade in and out" };
const transitions = { cut: "Cut", crossfade: "Crossfade", black: "Fade to black", white: "Fade to white",
  slide_left: "Slide left", slide_right: "Slide right", zoom: "Zoom", wipe: "Wipe", blur: "Blur" };
const captionStyles = { minimal: "Minimal", youtube: "YouTube", bold: "Bold social captions",
  cinematic: "Cinematic", inspirational: "Inspirational" };

function options(items: Record<string, string>) {
  return Object.entries(items).map(([value, label]) => <option key={value} value={value}>{label}</option>);
}

function NumberField({ label, value, min, max, step = 1, change }: {
  label: string; value: number | null; min: number; max: number; step?: number; change: (value: number) => void;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  useEffect(() => setText(value === null ? "" : String(value)), [value]);
  return <label>{label}<input type="number" min={min} max={max} step={step} value={text}
    onChange={(e) => setText(e.target.value)}
    onBlur={() => {
      if (text !== "" && Number.isFinite(Number(text)) && Number(text) >= min && Number(text) <= max) {
        if (Number(text) !== value) change(Number(text));
      } else setText(value === null ? "" : String(value));
    }}
    onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} /></label>;
}

function AssetSelect({ title, list, value, change, empty }: {
  title: string; list: Asset[]; value: string | null; change: (id: string | null) => void; empty: string;
}) {
  return <label>{title}<select value={value ?? ""} onChange={(e) => change(e.target.value || null)}>
    <option value="">{empty}</option>
    {value && !list.some((asset) => asset.id === value) && <option value={value}>Missing asset</option>}
    {list.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
  </select></label>;
}

function timing(timeline: Timeline) {
  let frame = 0;
  const starts = [];
  for (const [index, scene] of timeline.scenes.entries()) {
    if (index && scene.transition !== "cut") frame -= Math.round(scene.overlap * timeline.fps);
    starts.push(frame / timeline.fps);
    frame += Math.round(scene.duration * timeline.fps);
  }
  return { starts, duration: frame / timeline.fps };
}

function useTimeline(project: string, initial: Timeline) {
  const [state, setState] = useState(initial);
  const [saveState, setSaveState] = useState("Saved");
  const [error, setError] = useState<Error | null>(null);
  const current = useRef(initial);
  const revision = useRef(initial.revision);
  const previous = useRef<Timeline[]>([]);
  const next = useRef<Timeline[]>([]);
  const cache = useQueryClient();
  const wanted = useRef<Timeline | undefined>(undefined);
  const saving = useRef(false);

  const flush = useCallback(async () => {
    if (saving.current || !wanted.current) return;
    saving.current = true;
    setSaveState("Saving…");
    setError(null);
    try {
      while (wanted.current) {
        const draft = wanted.current;
        wanted.current = undefined;
        try {
          const saved = await media.save(project, { ...draft, revision: revision.current });
          revision.current = saved.revision;
          cache.setQueryData(["video-timeline", project], saved);
        } catch (reason) {
          wanted.current ??= draft;
          throw reason;
        }
      }
      setSaveState("Saved");
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)));
      setSaveState("Unsaved changes");
    } finally {
      saving.current = false;
    }
  }, [cache, project]);

  const publish = useCallback((timeline: Timeline) => {
    current.current = timeline;
    setState(timeline);
    wanted.current = timeline;
    void flush();
  }, [flush]);
  const edit = useCallback((update: (timeline: Timeline) => Timeline) => {
    previous.current = [...previous.current.slice(-49), current.current];
    next.current = [];
    publish(update(current.current));
  }, [publish]);
  const undo = useCallback(() => {
    const value = previous.current.pop();
    if (!value) return;
    next.current.push(current.current);
    publish(value);
  }, [publish]);
  const redo = useCallback(() => {
    const value = next.current.pop();
    if (!value) return;
    previous.current.push(current.current);
    publish(value);
  }, [publish]);
  return { state, edit, undo, redo, saveState, error, flush, canUndo: previous.current.length > 0, canRedo: next.current.length > 0 };
}

function timedCaptionImport(raw: string): Cue[] {
  const time = (text: string) => {
    const parts = text.replace(",", ".").split(":").map(Number);
    return parts.reduce((sum, part) => sum * 60 + part, 0);
  };
  const result: Cue[] = [];
  for (const block of raw.replace(/^\uFEFF/, "").replace(/\r/g, "").split(/\n[ \t]*\n/)) {
    const lines = block.split("\n");
    const index = lines.findIndex((line) => line.includes("-->"));
    if (index < 0) continue;
    const match = lines[index].match(/^(\d{1,3}:\d{2}(?::\d{2})?[.,]\d{1,3})\s+-->\s+(\d{1,3}:\d{2}(?::\d{2})?[.,]\d{1,3})/);
    if (!match) throw new Error("A subtitle has invalid timestamps.");
    const start = time(match[1]), end = time(match[2]);
    const text = lines.slice(index + 1).join("\n").trim();
    if (end <= start || end > 1200 || text.length > 500) throw new Error("Check caption duration or length.");
    if (text) result.push({ start, end, text });
  }
  if (!result.length || result.length > 300) throw new Error("Choose an SRT or WebVTT file with up to 300 captions.");
  return result.sort((a, b) => a.start - b.start);
}

type VideoProps = {
  project: string; workspace: Workspace; handoff?: string; handoffScene?: string;
  onHandoff: () => void; openImage: (scene: string, prompt: string) => void;
};

export function VideoCreator({ project, workspace, handoff, handoffScene, onHandoff, openImage }: VideoProps) {
  const load = useQuery({ queryKey: ["video-timeline", project], queryFn: () => media.timeline(project), refetchOnMount: false });
  if (!load.data) return load.error ? <StudioError error={load.error} /> : <p role="status">Opening timeline…</p>;
  return <VideoEditor project={project} workspace={workspace} handoff={handoff} handoffScene={handoffScene} onHandoff={onHandoff} initial={load.data} openImage={openImage} />;
}

function VideoEditor({ project, workspace, initial, handoff, handoffScene, onHandoff, openImage }: VideoProps & { initial: Timeline }) {
  const { state, edit, undo, redo, canUndo, canRedo, saveState, error: saveError, flush } = useTimeline(project, initial);
  const [selected, select] = useState(initial.scenes[0]?.id);
  const appliedHandoff = useRef<string | undefined>(undefined);
  const [drag, setDrag] = useState<string>();
  const [zoom, setZoom] = useState(28);
  const [script, setScript] = useState("");
  const [splitAt, setSplitAt] = useState<number | null>(null);
  const [play, setPlay] = useState<string>();
  const [filename, setFilename] = useState("Project video");
  const [container, setContainer] = useState<VideoSettings["container"]>("mp4");
  const [codec, setCodec] = useState("h264");
  const [audioCodec, setAudioCodec] = useState("aac");
  const [quality, setQuality] = useState(20);
  const [bitrate, setBitrate] = useState(192);
  const [captions, setCaptions] = useState<VideoSettings["captions"]>("burn");
  const [aspect, setAspect] = useState(() => {
    const index = formats.findIndex(([, w, h]) => Math.abs(initial.width / initial.height - w / h) < .004);
    return index >= 0 ? String(index) : "custom";
  });
  const [size, setSize] = useState(() => {
    const edge = Math.min(initial.width, initial.height);
    return [720, 1080, 1440, 2160].reduce((best, next) => Math.abs(edge - next) < Math.abs(edge - best) ? next : best);
  });
  const [error, setError] = useState<Error | null>(null);
  const [savedPath, setSavedPath] = useState("");
  const cache = useQueryClient();
  const encoder = useQuery({ queryKey: ["video-encoders"], queryFn: media.encoders });
  const tools = useStudioTools();
  const refresh = () => cache.invalidateQueries({ queryKey: projectStudioKey(project) });
  const pictures = workspace.assets.filter((asset) => asset.kind === "image");
  const audio = workspace.assets.filter((asset) => asset.kind === "audio");
  const renders = workspace.assets.filter((asset) => asset.source_type === "video_creator");
  const rendered = renders.find((asset) => asset.id === play);
  const jobs = workspace.jobs.filter((job) => job.kind === "video" || job.kind === "speech");
  const scene = state.scenes.find((item) => item.id === selected) ?? state.scenes[0];
  const index = scene ? state.scenes.findIndex((item) => item.id === scene.id) : -1;
  const image = pictures.find((asset) => asset.id === scene?.image_id);
  const recording = audio.find((asset) => asset.id === scene?.narration_id);
  const { starts, duration } = timing(state);

  function patch(update: Partial<Timeline>) {
    edit((old) => ({ ...old, ...update }));
  }
  const patchScene = useCallback((id: string, update: Partial<Scene>) => {
    edit((old) => ({ ...old, scenes: old.scenes.map((item) => item.id === id ? { ...item, ...update } : item) }));
  }, [edit]);
  const add = useCallback((added: Scene[]) => {
    edit((old) => ({ ...old, scenes: [...old.scenes, ...added].slice(0, 100) }));
    select(added[0]?.id);
    setSplitAt(null);
  }, [edit]);
  useEffect(() => {
    if (!handoff) {
      appliedHandoff.current = undefined;
      return;
    }
    if (appliedHandoff.current === handoff) return;
    appliedHandoff.current = handoff;
    if (handoffScene && initial.scenes.some((item) => item.id === handoffScene)) {
      patchScene(handoffScene, { image_id: handoff });
      select(handoffScene);
    } else add([newScene(handoff)]);
    onHandoff();
  }, [handoff, handoffScene, initial.scenes, add, patchScene, onHandoff]);
  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z") return;
      if (event.target instanceof HTMLElement && event.target.closest("textarea,input,[contenteditable]")) return;
      event.preventDefault();
      if (event.shiftKey) redo(); else undo();
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [undo, redo]);

  function move(id: string, to: number) {
    edit((old) => {
      const scenes = [...old.scenes];
      const from = scenes.findIndex((item) => item.id === id);
      if (from < 0 || to === from) return old;
      scenes.splice(to, 0, ...scenes.splice(from, 1));
      return { ...old, scenes };
    });
  }

  function split() {
    if (!scene || state.scenes.length >= 100) return;
    const split = Math.round((splitAt ?? scene.duration / 2) * state.fps) / state.fps;
    if (split < .25 || scene.duration - split < .25) {
      setError(new Error("Leave at least 0.25 seconds on both sides of the split."));
      return;
    }
    const tail = { ...scene, id: crypto.randomUUID(), duration: scene.duration - split, transition: "cut",
      narration_pending_id: null, narration_trim: scene.narration_trim + split, trim_narration: true,
      narration_id: recording && (recording.duration_seconds ?? 0) > scene.narration_trim + split ? recording.id : null,
      captions: scene.captions.filter((cue) => cue.end > split).map((cue) =>
        ({ ...cue, start: Math.max(0, cue.start - split), end: cue.end - split })) };
    const head = { ...scene, duration: split, trim_narration: true, captions: scene.captions
      .filter((cue) => cue.start < split).map((cue) => ({ ...cue, end: Math.min(split, cue.end) })) };
    edit((old) => ({ ...old, scenes: [...old.scenes.slice(0, index), head, tail, ...old.scenes.slice(index + 1)] }));
    select(tail.id);
    setSplitAt(null);
  }

  function setDimensions(preset: string, edge: number) {
    setAspect(preset);
    setSize(edge);
    const ratio = formats[Number(preset)];
    if (!ratio || preset === "custom") return;
    const [, w, h] = ratio;
    const scale = Math.min(edge / Math.min(w, h), 3840 / Math.max(w, h), 2160 / Math.min(w, h));
    patch({ width: Math.round(w * scale / 2) * 2, height: Math.round(h * scale / 2) * 2 });
  }

  const render = useMutation({
    mutationFn: ({ preview, scene_id }: { preview?: boolean; scene_id?: string }) =>
      media.render(project, { timeline: state, preview, scene_id, filename: preview ? "Preview" : filename,
        container: preview ? "mp4" : container, video_codec: preview ? "h264" : codec,
        audio_codec: preview ? "aac" : audioCodec, crf: quality, audio_bitrate: bitrate, captions }),
    onSuccess: (job) => { setPlay(job.id); return refresh(); },
  });
  const control = useMutation({ mutationFn: ({ id, cancel }: { id: string; cancel: boolean }) =>
    cancel ? studio.cancel(project, id) : media.retry(project, id), onSuccess: (job) => {
      if (job.kind === "video" && pending(job) && job.state === "queued") setPlay(job.id);
      return refresh();
    } });
  const saveFile = useMutation({ mutationFn: ({ asset, kind }: { asset: Asset; kind: "original" | "srt" | "ass" | "vtt" }) =>
    studio.download(project, asset.id, kind, asset.name), onSuccess: setSavedPath });
  const active = jobs.some((job) => job.kind === "video" && pending(job));
  const canRender = state.scenes.length > 0 && !render.isPending && !active;

  return <div className="media-editor">
    <div className="studio-card media-toolbar">
      <div className="media-actions">
        <ImportMedia project={project} kind="image" onImported={(items) => add(items.map((asset) => newScene(asset.id)))} />
        <button className="secondary-button" disabled={state.scenes.length >= 100} onClick={() => add([newScene()])}><Plus size={15} /> Add scene</button>
        <button className="icon-button" title="Undo" aria-label="Undo" disabled={!canUndo} onClick={undo}><Undo2 size={17} /></button>
        <button className="icon-button" title="Redo" aria-label="Redo" disabled={!canRedo} onClick={redo}><Redo2 size={17} /></button>
      </div>
      <span role="status">{saveState}</span>
      {saveError && <button className="secondary-button" onClick={() => void flush()}>Retry save</button>}
    </div>
    <StudioError error={saveError ?? error ?? render.error ?? control.error ?? saveFile.error ?? encoder.error} />
    <details className="studio-card">
      <summary>Plan scenes from a script</summary>
      <p>Separate scenes with blank lines. Each paragraph becomes editable narration and an image prompt.</p>
      <label>Script<textarea value={script} onChange={(event) => setScript(event.target.value)} rows={5} /></label>
      <button className="secondary-button" disabled={!script.trim()} onClick={() => {
        const parts = script.trim().split(/\n[\t ]*\n+/);
        add(parts.map((part) => ({ ...newScene(), narration_text: part.slice(0, 16000),
          image_prompt: part.slice(0, 8000), duration: Math.min(1200, Math.max(3, Math.ceil(part.trim().split(/\s+/).length / 2.4))) })));
        setScript("");
      }}>Add planned scenes</button>
    </details>
    <div className="media-edit-grid">
      <section className="studio-card media-stage" aria-label="Video preview">
        <div className="media-stage-image" style={{ aspectRatio: state.width / state.height }}>
          {rendered ? <AssetPreview project={project} asset={rendered} /> : image ? <AssetPreview project={project} asset={image} />
            : <span>Select an image to preview the scene.</span>}
        </div>
        <div className="media-actions">
          <button className="secondary-button" disabled={!canRender || !scene?.image_id}
            onClick={() => render.mutate({ preview: true, scene_id: scene?.id })}>Preview scene</button>
          <button className="secondary-button" disabled={!canRender}
            onClick={() => render.mutate({ preview: true })}>Preview video</button>
          {play && <button className="text-button" onClick={() => setPlay(undefined)}>Show scene image</button>}
        </div>
      </section>
      {scene && <section className="studio-card media-inspector" aria-label="Selected scene">
        <div className="media-heading"><h2>Scene {index + 1}</h2>
          <div className="media-actions">
            <button className="icon-button" aria-label="Move scene earlier" disabled={index === 0}
              onClick={() => move(scene.id, index - 1)}><ArrowUp size={16} /></button>
            <button className="icon-button" aria-label="Move scene later" disabled={index === state.scenes.length - 1}
              onClick={() => move(scene.id, index + 1)}><ArrowDown size={16} /></button>
            <button className="icon-button" aria-label="Duplicate scene" disabled={state.scenes.length >= 100}
              onClick={() => {
                const copy = { ...scene, id: crypto.randomUUID(), narration_pending_id: null };
                edit((old) => ({ ...old, scenes: [...old.scenes.slice(0, index + 1), copy, ...old.scenes.slice(index + 1)] }));
                select(copy.id);
              }}><Copy size={16} /></button>
            <button className="icon-button" aria-label="Delete scene" onClick={() => {
              patch({ scenes: state.scenes.filter((item) => item.id !== scene.id) }); setSplitAt(null);
            }}><Trash2 size={16} /></button>
          </div>
        </div>
        <AssetSelect title="Image" list={pictures} value={scene.image_id}
          change={(image_id) => { patchScene(scene.id, { image_id }); setPlay(undefined); }} empty="Choose a project image" />
        <details><summary>Generate scene image</summary>
          <label>Image prompt<textarea rows={2} value={scene.image_prompt} maxLength={8000}
            onChange={(e) => patchScene(scene.id, { image_prompt: e.target.value })} /></label>
          <button className="secondary-button" disabled={saveState !== "Saved"}
            onClick={() => openImage(scene.id, scene.image_prompt)}>Open Image Studio</button>
        </details>
        <div className="media-fields">
          <NumberField label="Duration (s)" value={scene.duration} min={.25} max={1200} step={.05}
            change={(duration) => patchScene(scene.id, { duration })} />
          <label>Animation<select value={scene.animation} onChange={(e) => patchScene(scene.id, { animation: e.target.value })}>
            {options(animations)}</select></label>
          <label>Transition in<select value={scene.transition} disabled={!index} onChange={(e) => patchScene(scene.id, { transition: e.target.value })}>
            {options(transitions)}</select></label>
          {Boolean(index) && scene.transition !== "cut" && <NumberField label="Transition (s)" value={scene.overlap}
            min={.05} max={5} step={.05} change={(overlap) => patchScene(scene.id, { overlap })} />}
        </div>
        <label>Title<input value={scene.title} maxLength={160} onChange={(e) => patchScene(scene.id, { title: e.target.value })} /></label>
        <details><summary>Split scene</summary>
          <div className="media-fields"><NumberField label="Split at (s)" value={splitAt ?? scene.duration / 2} min={.25}
            max={Math.max(.25, scene.duration - .25)} step={.01} change={setSplitAt} />
            <button className="secondary-button" disabled={scene.duration < .5 || state.scenes.length >= 100} onClick={split}>
              <Scissors size={15} /> Split</button></div>
        </details>
        <details open><summary>Narration</summary>
          <Narration key={scene.id} project={project} scene={scene} audio={audio} workspace={workspace}
            tools={tools.data ?? []} fps={state.fps} change={(update) => patchScene(scene.id, update)} />
        </details>
        <details><summary>Captions ({scene.captions.length})</summary>
          <div className="media-fields">
            <button className="secondary-button" disabled={scene.captions.length >= 300}
              onClick={() => patchScene(scene.id, { captions: [...scene.captions, { start: 0, end: Math.min(2, scene.duration), text: "New caption" }] })}>Add caption</button>
            {recording?.cues?.length ? <button className="secondary-button" onClick={() => {
              const cues = (recording.cues ?? []).filter((cue) => cue.end > scene.narration_trim).slice(0, 300);
              patchScene(scene.id, { captions: cues.map((cue) => ({ ...cue, start: Math.max(0, cue.start - scene.narration_trim),
                end: cue.end - scene.narration_trim })) });
            }}>Use narration timings</button> : null}
            <label className="secondary-button media-file">Import captions
              <input type="file" accept=".srt,.vtt" onChange={async (e) => {
                const file = e.target.files?.[0]; e.target.value = "";
                if (!file) return;
                try {
                  if (file.size > 512 * 1024) throw new Error("Choose a subtitle file under 512 KiB.");
                  patchScene(scene.id, { captions: timedCaptionImport(await file.text()) });
                  setError(null);
                } catch (reason) { setError(reason as Error); }
              }} /></label>
          </div>
          {scene.captions.map((cue, position) => <CaptionItem key={`${scene.id}-${position}`} cue={cue} change={(changed) =>
            patchScene(scene.id, { captions: scene.captions.flatMap((item, i) => i === position ? (changed ? [changed] : []) : [item]) })} />)}
        </details>
      </section>}
    </div>
    <section className="studio-card media-timeline" aria-label="Timeline">
      <div className="media-heading"><h2>Timeline <span>{Math.max(0, duration).toFixed(2)} s</span></h2>
        <label>Zoom<input type="range" min="5" max="100" value={zoom} onChange={(e) => setZoom(Number(e.target.value))} /></label></div>
      <div className="media-track-scroll">
        <div style={{ minWidth: Math.max(480, duration * zoom + 102) }}>
          <Track name="Scenes">{state.scenes.map((item, i) => <button key={item.id} draggable title={`Scene ${i + 1} · ${item.duration} s`}
            className={`media-clip media-scene-clip ${item.id === scene?.id ? "chosen" : ""}`}
            style={{ left: Math.max(0, starts[i] * zoom), width: Math.max(18, item.duration * zoom) }}
            onClick={() => { select(item.id); setSplitAt(null); setPlay(undefined); }}
            onDragStart={(event) => { setDrag(item.id); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", item.id); }}
            onDragEnd={() => setDrag(undefined)} onDragOver={(event) => { if (drag) event.preventDefault(); }}
            onDrop={(event) => { event.preventDefault(); if (drag) move(drag, i); setDrag(undefined); }}>
            {i + 1} · {pictures.find((asset) => asset.id === item.image_id)?.name ?? "Choose image"}
          </button>)}</Track>
          <Track name="Titles / captions">{state.scenes.map((item, i) => (item.title || item.captions.length > 0) &&
            <span key={item.id} className="media-clip text-clip" style={{ left: Math.max(0, starts[i] * zoom),
              width: Math.max(18, item.duration * zoom) }}>{item.captions.length || "Title"}</span>)}</Track>
          <Track name="Narration">{state.scenes.map((item, i) => item.narration_id &&
            <span key={item.id} className="media-clip voice-clip" style={{ left: Math.max(0, starts[i] * zoom),
              width: Math.max(18, Math.min(item.duration, (audio.find((asset) => asset.id === item.narration_id)?.duration_seconds ?? 0) - item.narration_trim) * zoom) }}>
              {audio.find((asset) => asset.id === item.narration_id)?.name ?? "Audio"}</span>)}</Track>
          {(["music", "effects"] as const).map((track) => <Track key={track} name={track === "music" ? "Music" : "Sound effects"}>
            {state.audio.map((clip, i) => {
              if (clip.track !== track || clip.start >= duration) return null;
              const remaining = (audio.find((asset) => asset.id === clip.asset_id)?.duration_seconds ?? 0) - clip.trim;
              const length = Math.min(duration - clip.start, clip.length ?? duration, clip.loop ? duration : remaining);
              return length > 0 && <span key={i} className="media-clip music-clip"
                style={{ left: clip.start * zoom, width: Math.max(18, length * zoom) }}>
                {audio.find((asset) => asset.id === clip.asset_id)?.name ?? "Audio"}
              </span>;
            })}
          </Track>)}
        </div>
      </div>
    </section>
    <details className="studio-card" open><summary>Music and sound effects</summary>
      <div className="media-fields">
        <ImportMedia project={project} kind="audio" />
        <AssetSelect title="Add audio clip" list={audio} value={null} empty="Choose an audio asset"
          change={(asset_id) => { if (asset_id) edit((old) => ({ ...old, audio: [...old.audio, {
            asset_id, track: "music", start: 0, trim: 0, length: null, gain: .25, fade_in: 0,
            fade_out: 0, mute: false, solo: false, loop: false,
          }].slice(0, 32) as AudioClip[] })); }} />
        <NumberField label="Narration volume" value={state.narration_gain} min={0} max={2} step={.05}
          change={(narration_gain) => patch({ narration_gain })} />
      </div>
      {state.audio.map((clip, i) => <AudioEditor key={i} clip={clip} assets={audio} change={(update) =>
        patch({ audio: state.audio.map((item, j) => i === j ? { ...item, ...update } : item) })}
        remove={() => patch({ audio: state.audio.filter((_, j) => j !== i) })} />)}
      <label className="media-check"><input type="checkbox" checked={state.duck} onChange={(e) => patch({ duck: e.target.checked })} /> Reduce music under narration</label>
      {state.duck && <div className="media-fields">
        <NumberField label="Ducking ratio" value={state.duck_amount} min={1} max={20} step={.1} change={(duck_amount) => patch({ duck_amount })} />
        <NumberField label="Attack (ms)" value={state.duck_attack} min={1} max={500} step={1} change={(duck_attack) => patch({ duck_attack })} />
        <NumberField label="Release (ms)" value={state.duck_release} min={5} max={3000} step={5} change={(duck_release) => patch({ duck_release })} />
        <NumberField label="Threshold" value={state.duck_threshold} min={.001} max={1} step={.001} change={(duck_threshold) => patch({ duck_threshold })} />
      </div>}
    </details>
    <section className="studio-card"><h2>Export video</h2>
      <div className="media-fields">
        <label>Aspect ratio<select value={aspect} onChange={(e) => setDimensions(e.target.value, size)}>
          {formats.map(([label], i) => <option key={label} value={i}>{label}</option>)}<option value="custom">Custom</option>
        </select></label>
        {aspect !== "custom" && <label>Resolution<select value={size} onChange={(e) => setDimensions(aspect, Number(e.target.value))}>
          {[720, 1080, 1440, 2160].map((value) => <option key={value} value={value}>{value === 2160 ? "4K" : `${value}p`}</option>)}</select></label>}
        <NumberField label="Width" value={state.width} min={160} max={3840} step={2} change={(width) => {
          setAspect("custom"); patch({ width: Math.round(width / 2) * 2 });
        }} />
        <NumberField label="Height" value={state.height} min={160} max={3840} step={2} change={(height) => {
          setAspect("custom"); patch({ height: Math.round(height / 2) * 2 });
        }} />
        <label>Frame rate<select value={state.fps} onChange={(e) => patch({ fps: Number(e.target.value) })}>
          {[24, 25, 30, 60].map((fps) => <option key={fps}>{fps}</option>)}</select></label>
        <label>Background<input type="color" value={state.background} onChange={(e) => patch({ background: e.target.value })} /></label>
        <label>Filename<input value={filename} maxLength={100} onChange={(e) => setFilename(e.target.value)} /></label>
        <label>Container<select value={container} onChange={(e) => {
          setContainer(e.target.value as VideoSettings["container"]); setCodec("h264"); setAudioCodec("aac");
        }}><option value="mp4">MP4</option><option value="mov">MOV</option></select></label>
        <label>Video codec<select value={codec} onChange={(e) => setCodec(e.target.value)}>
          {["h264", "hevc", ...(container === "mp4" ? ["av1"] : [])].filter((id) => encoder.data?.video.includes(id))
            .map((id) => <option key={id} value={id}>{id.toUpperCase()}</option>)}</select></label>
        <label>Audio codec<select value={audioCodec} onChange={(e) => setAudioCodec(e.target.value)}>
          {["aac", ...(container === "mov" ? ["pcm"] : [])].filter((id) => encoder.data?.audio.includes(id))
            .map((id) => <option key={id} value={id}>{id.toUpperCase()}</option>)}</select></label>
        <NumberField label="Quality (CRF)" value={quality} min={0} max={51} change={setQuality} />
        {audioCodec !== "pcm" && <NumberField label="Audio (kbps)" value={bitrate} min={64} max={320} change={setBitrate} />}
        <label>Captions<select value={captions} onChange={(e) => setCaptions(e.target.value as VideoSettings["captions"])}>
          <option value="burn">Burn into video</option><option value="separate">Separate subtitle file</option><option value="none">None</option>
        </select></label>
        {captions === "burn" && <label>Caption style<select value={state.caption_style} onChange={(e) => patch({ caption_style: e.target.value })}>
          {options(captionStyles)}</select></label>}
      </div>
      <button className="primary-button" disabled={!canRender || !filename.trim() || !encoder.data?.video.includes(codec)
        || !encoder.data?.audio.includes(audioCodec)} onClick={() => render.mutate({})}>Render {container.toUpperCase()}</button>
      <button className="secondary-button" disabled={!canRender || !scene?.image_id || !filename.trim()}
        onClick={() => render.mutate({ scene_id: scene?.id })}>Render selected scene</button>
      {renders.filter((asset) => !asset.preview).map((asset) => <div key={asset.id} className="media-export">
        <div><strong>{asset.name}</strong><small>{asset.extension?.toUpperCase()} · {asset.width} × {asset.height} · {asset.fps} FPS</small>
          {asset.research_only && <small>Contains noncommercial evaluation audio</small>}</div>
        <button className="secondary-button" onClick={() => setPlay(asset.id)}>Play</button>
        <button className="secondary-button" disabled={saveFile.isPending} onClick={() => saveFile.mutate({ asset, kind: "original" })}>Save to Downloads</button>
        {asset.cues?.length ? (["srt", "ass", "vtt"] as const).map((kind) =>
          <button key={kind} className="secondary-button" disabled={saveFile.isPending} onClick={() => saveFile.mutate({ asset, kind })}>{kind.toUpperCase()}</button>) : null}
      </div>)}
      {savedPath && <p role="status">Saved to {savedPath}</p>}
    </section>
    <MediaJobs jobs={jobs} onCancel={(id) => control.mutate({ id, cancel: true })} onRetry={(id) => control.mutate({ id, cancel: false })} />
  </div>;
}

function Track({ name, children }: { name: string; children: ReactNode }) {
  return <div className="media-track"><span>{name}</span><div className="media-track-clips">{children}</div></div>;
}

function Narration({ project, scene, workspace, audio, tools, fps, change }: {
  project: string; scene: Scene; workspace: Workspace; audio: Asset[]; tools: StudioTool[];
  fps: number; change: (update: Partial<Scene>) => void;
}) {
  const [language, setLanguage] = useState<Language>("uk");
  const [modelId, setModel] = useState("");
  const [voiceId, setVoice] = useState("");
  const [pause, setPause] = useState(120);
  const cache = useQueryClient();
  const eligible = tools.filter((tool) => tool.ready_languages?.includes(language) && !tool.research_only);
  const model = eligible.find((tool) => tool.id === modelId) ??
    eligible.find((tool) => tool.id === (language === "uk" ? "holos-tts-uk-04" : "kokoro-int8-en-v0_19")) ?? eligible[0];
  const profileVoices = workspace.profiles.filter((profile) => profile.assigned && model?.reference_languages?.includes(profile.language));
  const voices = model?.category === "Voice Cloning Models" && !model.voices.length ? profileVoices : model?.voices ?? [];
  const voice = voices.find((item) => item.id === voiceId)?.id ?? voices[0]?.id ?? "";
  const chosen = audio.find((asset) => asset.id === scene.narration_id);
  const length = chosen?.duration_seconds ?? 0;
  const job = workspace.jobs.find((item) => item.id === scene.narration_pending_id);
  const speak = useMutation({
    mutationFn: () => studio.speech(project, [{ text: scene.narration_text, language, model_id: model.id,
      name: scene.title ? `${scene.title.slice(0, 100)} narration` : "Scene narration", voice,
      speed: 1, pause_ms: pause, sample_rate: model.default_sample_rate ?? 24000, normalize_audio: true, format: "wav" }]),
    onSuccess: ([job]) => { change({ narration_pending_id: job.id }); return cache.invalidateQueries({ queryKey: projectStudioKey(project) }); },
  });
  const waiting = scene.narration_pending_id && !job || job && pending(job);
  return <div className="media-voice">
    <AssetSelect title="Existing audio" list={audio} value={scene.narration_id}
      change={(narration_id) => change({ narration_id, narration_trim: 0, trim_narration: false, narration_pending_id: null })} empty="No narration" />
    {chosen && <>
      <AssetPreview project={project} asset={chosen} />
      <div className="media-fields">
        <button className="secondary-button" onClick={() => change({ duration: Math.max(.25, Math.ceil((length - scene.narration_trim) * fps) / fps) })}>Fit scene to narration</button>
        <NumberField label="Trim start (s)" value={scene.narration_trim} min={0} max={Math.max(0, length - .01)} step={.01}
          change={(narration_trim) => change({ narration_trim })} />
        {length - scene.narration_trim > Math.round(scene.duration * fps) / fps + .001 && <label className="media-check">
          <input type="checkbox" checked={scene.trim_narration} onChange={(e) => change({ trim_narration: e.target.checked })} />
          Trim narration at scene end</label>}
      </div>
    </>}
    <label>Narration text<textarea value={scene.narration_text} maxLength={16000}
      onChange={(e) => change({ narration_text: e.target.value })} rows={3} /></label>
    <div className="media-fields">
      <label>Language<select value={language} onChange={(e) => setLanguage(e.target.value as Language)}>{options(languageName)}</select></label>
      <label>Voice model<select value={model?.id ?? ""} onChange={(e) => setModel(e.target.value)}>
        {!eligible.length && <option value="">No installed model for this language</option>}
        {eligible.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>Voice<select value={voice} onChange={(e) => setVoice(e.target.value)}>
        {!voices.length && <option value="">Choose an assigned voice profile</option>}
        {voices.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <NumberField label="Pauses (ms)" value={pause} min={0} max={2000} step={10} change={setPause} />
    </div>
    {!eligible.length && <p>Install a voice model in Models &amp; Tools.</p>}
    {model && !voices.length && <p>Assign an eligible profile to this project in Voice Cloning.</p>}
    <div className="media-actions">
      <button className="secondary-button" disabled={!voice || !scene.narration_text.trim() || speak.isPending || Boolean(waiting)}
        onClick={() => speak.mutate()}>{waiting ? "Generating narration…" : "Generate narration"}</button>
      {job?.state === "completed" && <button className="primary-button" onClick={() => {
        const generated = audio.find((item) => item.id === job.id);
        if (!generated) return;
        change({ narration_id: job.id, narration_trim: 0, narration_pending_id: null, trim_narration: false,
          duration: Math.max(.25, Math.ceil((generated.duration_seconds ?? scene.duration) * fps) / fps),
          captions: (generated.cues ?? []).slice(0, 300) });
      }}>Attach and fit scene</button>}
    </div>
    <StudioError error={speak.error} />
  </div>;
}

function CaptionItem({ cue, change }: { cue: Cue; change: (value: Cue | null) => void }) {
  const [text, setText] = useState(cue.text);
  useEffect(() => setText(cue.text), [cue.text]);
  return <div className="media-caption">
    <textarea aria-label="Caption text" value={text} maxLength={500} rows={2} onChange={(e) => setText(e.target.value)}
      onBlur={() => { if (!text.trim()) setText(cue.text); else if (text !== cue.text) change({ ...cue, text }); }} />
    <div className="media-fields">
      <NumberField label="Start (s)" value={cue.start} min={0} max={cue.end - .01} step={.01} change={(start) => change({ ...cue, start })} />
      <NumberField label="End (s)" value={cue.end} min={cue.start + .01} max={1200} step={.01} change={(end) => change({ ...cue, end })} />
      <button className="icon-button" aria-label="Delete caption" onClick={() => change(null)}><Trash2 size={16} /></button>
    </div>
  </div>;
}

function AudioEditor({ clip, assets, change, remove }: {
  clip: AudioClip; assets: Asset[]; change: (patch: Partial<AudioClip>) => void; remove: () => void;
}) {
  return <div className="media-audio-clip">
    <div className="media-fields">
      <AssetSelect title="Audio" list={assets} value={clip.asset_id} empty="Choose audio"
        change={(asset_id) => { if (asset_id) change({ asset_id, trim: 0 }); }} />
      <label>Track<select value={clip.track} onChange={(e) => change({ track: e.target.value as AudioClip["track"] })}>
        <option value="music">Music</option><option value="effects">Sound effects</option></select></label>
      <NumberField label="Timeline start (s)" value={clip.start} min={0} max={1200} step={.01} change={(start) => change({ start })} />
      <NumberField label="Trim start (s)" value={clip.trim} min={0} max={1200} step={.01} change={(trim) => change({ trim })} />
      <NumberField label="Maximum length (s)" value={clip.length} min={.01} max={1200} step={.01} change={(length) => change({ length })} />
      {clip.length !== null && <button className="text-button" onClick={() => change({ length: null })}>Full length</button>}
      <NumberField label="Volume" value={clip.gain} min={0} max={2} step={.05} change={(gain) => change({ gain })} />
      <NumberField label="Fade in (s)" value={clip.fade_in} min={0} max={30} step={.05} change={(fade_in) => change({ fade_in })} />
      <NumberField label="Fade out (s)" value={clip.fade_out} min={0} max={30} step={.05} change={(fade_out) => change({ fade_out })} />
    </div>
    <div className="media-fields">
      <label className="media-check"><input type="checkbox" checked={clip.loop} onChange={(e) => change({ loop: e.target.checked })} /> Loop</label>
      <label className="media-check"><input type="checkbox" checked={clip.mute} onChange={(e) => change({ mute: e.target.checked })} /> Mute</label>
      <label className="media-check"><input type="checkbox" checked={clip.solo} onChange={(e) => change({ solo: e.target.checked })} /> Solo</label>
      <button className="icon-button" aria-label="Remove audio clip" onClick={remove}><Trash2 size={16} /></button>
    </div>
  </div>;
}
