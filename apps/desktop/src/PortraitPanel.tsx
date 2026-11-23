import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Video } from "lucide-react";
import { api } from "./services/api";
import { AvatarVideo } from "./AvatarVideo";
import type { FaceCrop, Narration } from "./types";

const statusKey = ["portrait-model"];
const portraitsKey = ["portrait-library"];
const imageSize = 640;

function base64Media(file: File, video: boolean): Promise<string> {
  const limit = video ? 16 : 8;
  if (file.size > limit * 1024 * 1024) return Promise.reject(new Error(`Choose a file up to ${limit} MiB.`));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.readAsDataURL(file);
  });
}

export function PortraitPanel({ narration, changed }: { narration?: Narration | null; changed: boolean }) {
  const client = useQueryClient();
  const [accept, setAccept] = useState(false);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File>();
  const [selected, setSelected] = useState("");
  const [crop, setCrop] = useState<FaceCrop>();
  const [seconds, setSeconds] = useState(5);
  const [animateHead, setAnimateHead] = useState(false);
  const [videoId, setVideoId] = useState<string>();
  const status = useQuery({
    queryKey: statusKey,
    queryFn: api.portraitModel,
    refetchInterval: ({ state }) => state.data?.state === "downloading" ? 1000 : false,
  });
  const install = useMutation({
    mutationFn: api.installPortraitModel,
    onSuccess: (model) => client.setQueryData(statusKey, model),
  });
  const cancel = useMutation({
    mutationFn: api.cancelPortraitModel,
    onSuccess: (model) => client.setQueryData(statusKey, model),
  });
  const portraits = useQuery({ queryKey: portraitsKey, queryFn: api.portraits });
  const portrait = useQuery({
    queryKey: ["portrait", selected],
    queryFn: () => api.portrait(selected),
    enabled: Boolean(selected),
  });
  const add = useMutation({
    mutationFn: async ({ label, media }: { label: string; media: File }) => {
      const video = media.type.startsWith("video/") || /\.(mp4|mov)$/i.test(media.name);
      return api.importPortrait(label, await base64Media(media, video), video);
    },
    onSuccess: async (result) => {
      setSelected(result.id);
      setCrop(undefined);
      setName("");
      setFile(undefined);
      await client.invalidateQueries({ queryKey: portraitsKey });
    },
  });
  const save = useMutation({
    mutationFn: ({ id, face }: { id: string; face: FaceCrop }) => api.savePortraitCrop(id, face),
    onSuccess: (result) => {
      client.setQueryData<typeof portrait.data>(["portrait", result.id], (current) =>
        current ? { ...current, crop: result.crop } : current);
    },
  });
  const latest = useQuery({
    queryKey: ["portrait-render", narration?.id],
    queryFn: () => api.latestAvatar(narration!.id, "musetalk"),
    enabled: Boolean(narration),
  });
  const render = useMutation({
    mutationFn: (input: { portraitId: string; narrationId: string; seconds: number; animateHead: boolean }) =>
      api.renderPortrait(input.portraitId, input.narrationId, input.seconds, input.animateHead),
    onSuccess: (result, { narrationId }) => {
      client.setQueryData(["portrait-render", narrationId], result);
      setVideoId(result.id);
    },
  });
  function importPhoto(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (file && name.trim()) add.mutate({ label: name.trim(), media: file });
  }

  const model = status.data;
  const face = crop ?? portrait.data?.crop;
  const original = portrait.data?.crop;
  const cropChanged = Boolean(face && original &&
    (face.x !== original.x || face.y !== original.y || face.side !== original.side));
  const error = status.error ?? install.error ?? cancel.error ?? portraits.error
    ?? portrait.error ?? add.error ?? save.error ?? latest.error ?? render.error;

  return <div className="portrait-panel">
    <p>Lip-sync a front-facing photo or the first 10 seconds of a video to saved narration. Video references carry through the original head movement and expressions.</p>
    {status.isPending && <p role="status">Checking MuseTalk…</p>}
    {model && !model.platform_eligible && <p>The experimental portrait engine targets Apple Silicon Macs.</p>}
    {model?.platform_eligible && !model.runtime_installed &&
      <p>{model.message || <>Run <code>sh backend/avatar/portrait_runtime/setup.sh</code> from the project directory, then refresh.</>}</p>}
    {model?.platform_eligible && !model.tools_available && <p>FFmpeg and FFprobe are required for rendering.</p>}
    {model?.platform_eligible && model.runtime_installed && !model.ready && <div className="voice-setup">
      {model.state === "downloading" ? <>
        <progress aria-label="MuseTalk download" value={model.downloaded_bytes} max={model.download_bytes} />
        <button type="button" className="secondary-button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>Cancel</button>
      </> : <>
        <label>
          <input type="checkbox" checked={accept} onChange={(event) => setAccept(event.target.checked)} />
          I reviewed the <a href={model.source_url} target="_blank" rel="noreferrer">model license and source</a>.
        </label>
        <button type="button" className="primary-button" onClick={() => install.mutate()}
          disabled={!accept || install.isPending || !model.tools_available}>
          {install.isPending ? "Starting…" : `Install MuseTalk · ${(model.download_bytes / 1_000_000_000).toFixed(2)} GB`}
        </button>
      </>}
      {model.message && <p role="status">{model.message}</p>}
    </div>}
    {model?.platform_eligible && !model.runtime_installed &&
      <button type="button" className="secondary-button" onClick={() => status.refetch()}>Refresh runtime status</button>}
    <form className="portrait-import voice-controls" onSubmit={importPhoto}>
      <label>Portrait name<input maxLength={60} value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label>Photo (PNG, JPEG) or video (MP4, MOV)
        <input type="file" accept="image/png,image/jpeg,video/mp4,video/quicktime,.mp4,.mov" key={add.data?.id}
          onChange={(event) => setFile(event.target.files?.[0])} />
      </label>
      <button className="secondary-button" disabled={add.isPending || !file || !name.trim()}>Import portrait</button>
    </form>
    {Boolean(portraits.data?.length) && <div className="voice-controls">
      <label>Saved portrait
        <select value={selected} onChange={(event) => { setSelected(event.target.value); setCrop(undefined); }}>
          <option value="">Choose a portrait</option>
          {portraits.data?.map((item) => <option key={item.id} value={item.id}>
            {item.name}{item.reference_seconds !== null ? " · Video" : ""}
          </option>)}
        </select>
      </label>
    </div>}
    {portrait.data && face && <div className="avatar-workspace">
      <div className="avatar-portrait portrait-framing">
        <img src={portrait.data.image_url} alt={portrait.data.name} />
        <div className="portrait-crop" style={{
          top: `${100 * face.y / imageSize}%`, left: `${100 * face.x / imageSize}%`,
          width: `${100 * face.side / imageSize}%`, height: `${100 * face.side / imageSize}%`,
        }} />
      </div>
      <div className="portrait-sliders">
        <p>Frame the face from forehead to just below the chin. Keep the mouth in the lower half of the box.</p>
        {portrait.data.reference_seconds !== null && <p>
          The face box follows the first frame through this {portrait.data.reference_seconds.toFixed(1)} second reference.
        </p>}
        <label>Face box size<input type="range" min={160} max={imageSize} value={face.side}
          onChange={(event) => {
            const side = Number(event.target.value);
            setCrop({ side, x: Math.min(face.x, imageSize - side), y: Math.min(face.y, imageSize - side) });
          }} /></label>
        <label>Horizontal position<input type="range" min={0} max={imageSize - face.side} value={face.x}
          onChange={(event) => setCrop({ ...face, x: Number(event.target.value) })} /></label>
        <label>Vertical position<input type="range" min={0} max={imageSize - face.side} value={face.y}
          onChange={(event) => setCrop({ ...face, y: Number(event.target.value) })} /></label>
        <button type="button" className="secondary-button" disabled={!cropChanged || save.isPending}
          onClick={() => save.mutate({ id: selected, face })}>Save face framing</button>
      </div>
    </div>}
    {portrait.data?.reference_seconds === null && <div className="voice-setup">
      <label><input type="checkbox" checked={animateHead} disabled={render.isPending}
        aria-describedby="portrait-motion-note" onChange={(event) => setAnimateHead(event.target.checked)} />
        Add subtle head motion
      </label>
      <p id="portrait-motion-note">Adds a gentle tilt and drift. The presenter blinks when both eyes are detected.</p>
    </div>}
    <div className="voice-controls">
      <label>Clip length
        <select value={seconds} onChange={(event) => setSeconds(Number(event.target.value))}>
          <option value={3}>Up to 3 seconds</option>
          <option value={5}>Up to 5 seconds</option>
          <option value={10}>Up to 10 seconds</option>
        </select>
      </label>
      <button type="button" className="primary-button"
        disabled={!model?.ready || !narration || !portrait.data || cropChanged || changed || render.isPending}
        onClick={() => {
          if (narration && portrait.data) render.mutate({
            portraitId: selected, narrationId: narration.id, seconds,
            animateHead: portrait.data.reference_seconds === null && animateHead,
          });
        }}>
        <Video size={14} /> {render.isPending ? "Rendering…" : "Render portrait"}
      </button>
    </div>
    {portrait.data?.reference_seconds != null && <p>Output ends when the reference or narration ends.</p>}
    {(!narration || changed) && <p>Generate narration for the current saved script to render this portrait.</p>}
    {latest.data && <div className="voice-result">
      <p><strong>Saved portrait MP4</strong> · {latest.data.duration_seconds.toFixed(1)} s
        {latest.data.animate_head && " · Subtle head motion"}</p>
      <span className="voice-path">{latest.data.path}</span>
      {videoId !== latest.data.id && <button type="button" className="secondary-button"
        onClick={() => setVideoId(latest.data!.id)}>Load video</button>}
      {videoId === latest.data.id && <AvatarVideo key={videoId} renderId={videoId} label="Rendered portrait" />}
    </div>}
    {error && <p className="settings-error" role="alert">{error.message}</p>}
  </div>;
}
