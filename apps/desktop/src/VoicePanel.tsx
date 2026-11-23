import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AudioLines, Headphones } from "lucide-react";
import { api } from "./services/api";
import { studio } from "./services/studio";
import { useNavigation } from "./stores/navigation";
import { projectStudioKey } from "./StudioParts";
import { AvatarPanel } from "./AvatarPanel";
import { ExportPanel } from "./ExportPanel";
import type { DirectorPlan } from "./types";

function useWavUrl(wav: ArrayBuffer | undefined) {
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    const next = wav ? URL.createObjectURL(new Blob([wav], { type: "audio/wav" })) : undefined;
    setUrl(next);
    return () => {
      if (next) URL.revokeObjectURL(next);
    };
  }, [wav]);
  return url;
}

export function VoicePanel({ scriptId, narrationText, scriptChanged, director, projectId }: {
  scriptId: string;
  narrationText: string;
  scriptChanged: boolean;
  director?: DirectorPlan | null;
  projectId?: string;
}) {
  const client = useQueryClient();
  const [consent, setConsent] = useState(false);
  const [voiceId, setVoiceId] = useState("af_bella");
  const [speed, setSpeed] = useState(1);
  const [playingId, setPlayingId] = useState<string>();
  const statusKey = ["voice-status"];
  const narrationKey = ["narration", scriptId, narrationText];
  const statusQuery = useQuery({
    queryKey: statusKey,
    queryFn: api.voiceStatus,
    refetchInterval: ({ state }) =>
      ["downloading", "installing"].includes(state.data?.state ?? "") ? 1_000 : false,
  });
  const install = useMutation({
    mutationFn: api.installVoice,
    onSuccess: (status) => { client.setQueryData(statusKey, status); void client.invalidateQueries({ queryKey: ["studio-tools"] }); },
  });
  const cancel = useMutation({
    mutationFn: api.cancelVoiceInstall,
    onSuccess: (status) => { client.setQueryData(statusKey, status); void client.invalidateQueries({ queryKey: ["studio-tools"] }); },
  });
  const status = statusQuery.data;
  const pendingInstall = status?.state === "downloading" || status?.state === "installing";
  const input = { voice_id: voiceId, speed };
  const preview = useMutation({ mutationFn: api.previewVoice });
  const previewUrl = useWavUrl(preview.data);
  const latest = useQuery({
    queryKey: narrationKey,
    queryFn: () => api.latestNarration(scriptId),
  });
  const render = useMutation({
    mutationFn: ({ options }: { text: string; options: typeof input }) => api.renderNarration(scriptId, options),
    onSuccess: (narration, { text }) => {
      client.setQueryData(["narration", scriptId, text], narration);
      setPlayingId(narration.id);
    },
  });
  const narration = latest.data;
  const openAudioStudio = useNavigation((state) => state.openAudioStudio);
  const setScreen = useNavigation((state) => state.setScreen);
  const visualize = useMutation({
    mutationFn: () => studio.adopt(projectId!, narration!.id),
    onSuccess: async (asset) => {
      await client.invalidateQueries({ queryKey: projectStudioKey(projectId!) });
      openAudioStudio("visualizer", projectId!, asset.id);
    },
  });
  const playNarration = Boolean(narration && playingId === narration.id);
  const savedAudio = useQuery({
    queryKey: ["narration-wav", narration?.id],
    queryFn: () => api.narrationAudio(narration!.id),
    enabled: Boolean(narration && playNarration),
    gcTime: 0,
    staleTime: Infinity,
  });
  const wavUrl = useWavUrl(playNarration ? savedAudio.data : undefined);
  const busy = preview.isPending || render.isPending;
  const error = statusQuery.error ?? install.error ?? cancel.error ?? latest.error
    ?? preview.error ?? render.error ?? savedAudio.error ?? visualize.error;

  return <section className="voice-panel" aria-labelledby="voice-heading">
    <div className="voice-heading">
      <AudioLines size={19} />
      <div><h3 id="voice-heading">Voice &amp; narration</h3><p>Kokoro · English · runs on this device</p></div>
    </div>
    {statusQuery.isPending && <p role="status">Checking the voice engine…</p>}
    {status && !status.platform_verified && <p>Voice generation is currently available on Apple Silicon Macs.</p>}
    {status?.platform_verified && !status.runtime_installed && <p>Install the Sherpa-ONNX dependencies from the project’s backend requirements, then restart the app.</p>}
    {status?.enabled === false && <p>Enable Kokoro in <button type="button" className="text-button" onClick={() => setScreen("runtime")}>Models &amp; Tools</button> to generate narration.</p>}
    {status?.platform_verified && status.runtime_installed && !status.ready && status.enabled !== false && <div className="voice-setup">
      {pendingInstall ? <>
        <progress aria-label="Voice model download" value={status.downloaded_bytes} max={status.download_bytes} />
        <button type="button" className="secondary-button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel install</button>
      </> : <>
        <label>
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          I accept the <a href={status.license_url} target="_blank" rel="noreferrer">Apache 2.0 model and voice license</a>.
        </label>
        <button type="button" className="primary-button" disabled={!consent || install.isPending} onClick={() => install.mutate()}>
          {install.isPending ? "Starting…" : `Install Kokoro · ${Math.round(status.download_bytes / 1_000_000)} MB`}
        </button>
      </>}
      {status.message && <p role="status">{status.message}</p>}
    </div>}
    {status?.ready && <>
      <div className="voice-controls">
        <label><span>English voice</span>
          <select value={voiceId} disabled={busy} onChange={(event) => { setVoiceId(event.target.value); preview.reset(); }}>
            {status.voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name} · {voice.accent}</option>)}
          </select>
        </label>
        <label><span>Speed · {speed.toFixed(2)}×</span>
          <input type="range" min="0.75" max="1.5" step="0.05" value={speed} disabled={busy}
            onChange={(event) => { setSpeed(Number(event.target.value)); preview.reset(); }} />
        </label>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => preview.mutate(input)}>
          <Headphones size={14} /> {preview.isPending ? "Preparing preview…" : "Preview voice"}
        </button>
        <button type="button" className="primary-button" disabled={busy || scriptChanged}
          onClick={() => render.mutate({ text: narrationText, options: input })}>
          {render.isPending ? "Generating narration…" : "Generate narration"}
        </button>
      </div>
      {scriptChanged && <p>Save script edits to generate narration from the updated text.</p>}
      {previewUrl && <audio key={previewUrl} controls src={previewUrl} aria-label="Voice preview" />}
    </>}
    {narration && <div className="voice-result">
      <p><strong>Saved narration</strong> · {narration.duration_seconds.toFixed(1)} s · {narration.speed}×</p>
      <span className="voice-path">{narration.path}</span>
      {!playNarration && <button type="button" className="secondary-button" onClick={() => setPlayingId(narration.id)}>Load saved audio</button>}
      {savedAudio.isFetching && <p role="status">Loading WAV…</p>}
      {projectId && !scriptChanged && <button type="button" className="secondary-button" disabled={visualize.isPending} onClick={() => visualize.mutate()}>Create visualizer</button>}
      {wavUrl && <audio key={wavUrl} controls src={wavUrl} aria-label="Saved narration" />}
    </div>}
    {error && <p className="settings-error" role="alert">{error.message}</p>}
    <AvatarPanel narration={narration} changed={scriptChanged} />
    <ExportPanel scriptId={scriptId} narration={narration} changed={scriptChanged} director={director} />
  </section>;
}
