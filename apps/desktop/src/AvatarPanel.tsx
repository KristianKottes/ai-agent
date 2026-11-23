import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserRound, Video } from "lucide-react";
import { api } from "./services/api";
import { AvatarVideo } from "./AvatarVideo";
import { PortraitPanel } from "./PortraitPanel";
import type { AvatarAppearance, Narration } from "./types";

const defaultAppearance: AvatarAppearance = { look: "maya", backdrop: "ocean", outfit: "ink", motion: true };
const libraryKey = ["avatar-library"];

function capitalize(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

type AvatarPanelProps = { narration: Narration | null | undefined; changed: boolean };

export function AvatarPanel(props: AvatarPanelProps) {
  const [mode, setMode] = useState<"illustrated" | "portrait">("illustrated");
  return <section className="avatar-panel" aria-labelledby="avatar-library-title">
    <div className="voice-heading">
      <UserRound size={19} />
      <div><h3 id="avatar-library-title">Avatar Library</h3><p>Presenters with lip sync from saved narration</p></div>
    </div>
    <div className="avatar-mode" role="group" aria-label="Presenter type">
      <button type="button" className="secondary-button" aria-pressed={mode === "illustrated"}
        onClick={() => setMode("illustrated")}>Illustrated</button>
      <button type="button" className="secondary-button" aria-pressed={mode === "portrait"}
        onClick={() => setMode("portrait")}>Portrait · Experimental</button>
    </div>
    {mode === "illustrated" ? <IllustratedAvatarPanel {...props} /> : <PortraitPanel {...props} />}
  </section>;
}

function IllustratedAvatarPanel({ narration, changed }: AvatarPanelProps) {
  const client = useQueryClient();
  const [appearance, setAppearance] = useState(defaultAppearance);
  const [presetName, setPresetName] = useState("");
  const [seconds, setSeconds] = useState(10);
  const [videoId, setVideoId] = useState<string>();
  const library = useQuery({ queryKey: libraryKey, queryFn: api.avatarLibrary });
  const data = library.data;
  const preview = useQuery({
    queryKey: ["avatar-preview", appearance],
    queryFn: () => api.previewAvatar(appearance),
    enabled: data?.ready === true,
  });
  const save = useMutation({
    mutationFn: () => api.saveAvatarPreset({ ...appearance, name: presetName.trim() }),
    onSuccess: async () => {
      setPresetName("");
      await client.invalidateQueries({ queryKey: libraryKey });
    },
  });
  const latest = useQuery({
    queryKey: ["avatar-render", narration?.id],
    queryFn: () => narration ? api.latestAvatar(narration.id) : Promise.resolve(null),
    enabled: Boolean(narration),
  });
  const render = useMutation({
    mutationFn: (narrationId: string) => api.renderAvatar({ ...appearance, narration_id: narrationId, seconds }),
    onSuccess: (result, narrationId) => {
      client.setQueryData(["avatar-render", narrationId], result);
      setVideoId(result.id);
    },
  });
  const video = latest.data && latest.data.id === videoId ? latest.data : undefined;
  function savePreset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (presetName.trim()) save.mutate();
  }

  const failure = library.error ?? preview.error ?? save.error ?? render.error ?? latest.error;
  return <div className="illustrated-avatar">
    {library.isPending && <p role="status">Checking the local presenter engine…</p>}
    {data && !data.ready && <p>{data.detail}</p>}
    {data?.ready && <>
      <div className="avatar-workspace">
        <div className="avatar-portrait">
          {preview.data && <img src={preview.data.image_url} alt={`${data.looks.find((look) => look.id === appearance.look)?.name} preview`} />}
        </div>
        <div className="avatar-options">
          {data.presets.length > 0 && <label>Saved preset
            <select defaultValue="" onChange={(event) => {
              const preset = data.presets.find((item) => item.id === event.target.value);
              if (preset) {
                const { look, backdrop, outfit, motion } = preset;
                setAppearance({ look, backdrop, outfit, motion });
              }
              event.target.value = "";
            }}>
              <option value="">Choose a preset</option>
              {data.presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}
            </select>
          </label>}
          <label>Presenter
            <select value={appearance.look} onChange={(event) => setAppearance({ ...appearance, look: event.target.value })}>
              {data.looks.map((look) => <option key={look.id} value={look.id}>{look.name}</option>)}
            </select>
          </label>
          <label>Backdrop
            <select value={appearance.backdrop} onChange={(event) => setAppearance({ ...appearance, backdrop: event.target.value })}>
              {data.backdrops.map((backdrop) => <option key={backdrop} value={backdrop}>{capitalize(backdrop)}</option>)}
            </select>
          </label>
          <label>Outfit
            <select value={appearance.outfit} onChange={(event) => setAppearance({ ...appearance, outfit: event.target.value })}>
              {data.outfits.map((outfit) => <option key={outfit} value={outfit}>{capitalize(outfit)}</option>)}
            </select>
          </label>
          <label className="avatar-toggle">
            <input type="checkbox" checked={appearance.motion}
              onChange={(event) => setAppearance({ ...appearance, motion: event.target.checked })} />
            Head movement and blinking
          </label>
          <form onSubmit={savePreset} className="avatar-preset">
            <label>Preset name
              <input value={presetName} maxLength={60} onChange={(event) => setPresetName(event.target.value)} />
            </label>
            <button className="secondary-button" disabled={!presetName.trim() || save.isPending}>Save preset</button>
          </form>
        </div>
      </div>
      <div className="voice-controls">
        <label>Clip length
          <select value={seconds} onChange={(event) => setSeconds(Number(event.target.value))}>
            {[5, 10, 20, 30].filter((length) => length <= data.max_seconds).map((length) =>
              <option key={length} value={length}>Up to {length} seconds</option>)}
          </select>
        </label>
        <button className="primary-button" disabled={!narration || changed || render.isPending}
          onClick={() => { if (narration) render.mutate(narration.id); }}>
          <Video size={14} /> {render.isPending ? "Rendering…" : "Render presenter"}
        </button>
      </div>
      {!narration && <p>Generate narration above to choose its voice and render a presenter clip.</p>}
      {changed && <p>Save your edits and generate updated narration before rendering.</p>}
      {render.isPending && <p role="status">Aligning speech and encoding video…</p>}
    </>}
    {latest.data && <div className="voice-result">
      <p><strong>Saved MP4</strong> · {latest.data.duration_seconds.toFixed(1)} s</p>
      <span className="voice-path">{latest.data.path}</span>
      {!video && <button className="secondary-button" onClick={() => setVideoId(latest.data!.id)}>Load video</button>}
      {video && <AvatarVideo key={video.id} renderId={video.id} label="Rendered presenter" />}
    </div>}
    {failure && <p className="settings-error" role="alert">{failure.message}</p>}
  </div>;
}
