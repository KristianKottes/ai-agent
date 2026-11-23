import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { api } from "./services/api";
import type { DirectorPlan, SceneGenerationRequest, SceneJob } from "./types";

const modelKey = ["scene-models"];
const running = (job?: SceneJob | null) => job?.state === "running" || job?.state === "cancelling";

function jobLabel(job: SceneJob) {
  if (job.state === "cancelling") return "Cancelling…";
  if (job.stage === "downloading") return "Downloading and verifying model files…";
  if (job.stage === "generating") return job.progress === 1 ? "Decoding generated frames…" : "Generating on this Mac…";
  if (job.stage === "validating") return "Checking the generated file…";
  return "Preparing the scene engine…";
}

export function SceneGenerator({ scriptId, director }: { scriptId: string; director?: DirectorPlan | null }) {
  const cache = useQueryClient();
  const jobKey = ["scene-job", scriptId];
  const [kind, setKind] = useState<SceneGenerationRequest["kind"]>("image");
  const [prompt, setPrompt] = useState("");
  const [seed, setSeed] = useState("137");
  const [approved, setApproved] = useState(false);
  const models = useQuery({ queryKey: modelKey, queryFn: api.sceneModels, networkMode: "always" });
  const latest = useQuery({
    queryKey: jobKey, queryFn: () => api.latestSceneJob(scriptId), networkMode: "always",
    refetchInterval: ({ state }) => running(state.data) ? 1000 : false,
  });
  const job = latest.data;
  const active = running(job);
  const profile = models.data?.profiles[kind];
  const start = useMutation({
    mutationFn: (input: SceneGenerationRequest) => api.startSceneJob(scriptId, input),
    networkMode: "always",
    onSuccess: (result) => {
      cache.setQueryData(jobKey, result);
      setApproved(false);
    },
  });
  const cancel = useMutation({
    mutationFn: (jobId: string) => api.cancelSceneJob(scriptId, jobId),
    networkMode: "always",
    onSuccess: (result) => cache.setQueryData(jobKey, result),
  });
  useEffect(() => {
    if (job?.state === "completed") void cache.invalidateQueries({ queryKey: ["scene-assets", scriptId] });
    if (job && !active) void cache.invalidateQueries({ queryKey: modelKey });
  }, [cache, scriptId, job?.id, job?.state, active]);

  const busy = active || start.isPending;
  const numericSeed = Number(seed);
  const validSeed = seed.trim() !== "" && Number.isInteger(numericSeed) && numericSeed >= 0 && numericSeed <= 2147483647;
  const error = start.error ?? cancel.error ?? models.error ?? latest.error;

  return <div className="scene-generator">
    <h4><Sparkles size={15} /> Generate a scene</h4>
    {models.isPending && <p role="status">Checking the local scene models…</p>}
    {models.data && !models.data.platform_eligible && <p>Scene generation is available on Apple Silicon Macs.</p>}
    {models.data?.platform_eligible && <>
      <div className="voice-controls">
        <label>Output
          <select value={kind} disabled={busy} onChange={(event) => {
            setKind(event.target.value as typeof kind);
            setApproved(false);
          }}>
            <option value="image">Image · FLUX.2 Klein · 512 × 512</option>
            <option value="video">Video preview · Wan 2.1 · 3 seconds</option>
          </select>
        </label>
        {Boolean(director?.scenes.length) && <label>Use a planned scene prompt
          <select disabled={busy} value="" onChange={(event) => {
            if (event.target.value !== "") setPrompt(director!.scenes[Number(event.target.value)].generation_prompt);
          }}>
            <option value="">Choose a planned scene</option>
            {director?.scenes.map((scene, index) => <option key={scene.scene_id} value={index}>
              Scene {index + 1} · {scene.scene_type.replaceAll("_", " ")}
            </option>)}
          </select>
        </label>}
        <label>Seed<input type="number" min={0} max={2147483647} step={1} value={seed} disabled={busy}
          onChange={(event) => setSeed(event.target.value)} /></label>
      </div>
      <label className="scene-prompt">Scene prompt
        <textarea rows={3} maxLength={1000} value={prompt} disabled={busy}
          onChange={(event) => setPrompt(event.target.value)} />
      </label>
      {!models.data.video_tools_available && kind === "video" && <p>Install FFmpeg and FFprobe to generate video.</p>}
      {profile?.eligible && !profile.cached && !busy && <label className="scene-consent">
        <input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} />
        <span>I reviewed the <a href={kind === "image" ? "https://huggingface.co/novelaide/FLUX.2-klein-4B-GGUF" :
          "https://huggingface.co/off-grid-ai/Wan2.1-T2V-1.3B-GGUF"} target="_blank" rel="noreferrer">model license and sources</a>
          {kind === "video" && <> and the <a href="https://github.com/madebyollin/taehv/blob/main/LICENSE"
            target="_blank" rel="noreferrer">preview decoder license</a></>}. Download {(profile.download_bytes / 1e9).toFixed(2)} GB.</span>
      </label>}
      <button type="button" className="secondary-button"
        disabled={!profile?.eligible || latest.isPending || busy || !prompt.trim() || !validSeed || (!profile.cached && !approved)}
        onClick={() => start.mutate({ kind, prompt: prompt.trim(), seed: numericSeed, accepted_license: approved })}>
        <Sparkles size={14} /> {start.isPending ? "Starting…" : profile?.cached ? "Generate scene" : "Download and generate"}
      </button>
    </>}
    {job && active && <div className="voice-setup">
      <p role="status">{jobLabel(job)}</p>
      {job.progress !== null && <progress aria-label="Scene generation progress" value={job.progress} max={1} />}
      <button type="button" className="secondary-button" disabled={cancel.isPending || job.state === "cancelling"}
        onClick={() => cancel.mutate(job.id)}>Cancel scene</button>
    </div>}
    {job?.state === "completed" && <p role="status">Scene saved. Choose it under Saved scene or presenter to add it to the timeline.</p>}
    {job && !active && job.state !== "completed" && <div className="voice-result">
      <p role={job.state === "failed" ? "alert" : "status"}>{job.message}</p>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => {
        setPrompt(job.prompt); setSeed(String(job.seed)); setKind(job.kind); setApproved(false);
      }}>Reuse prompt</button>
    </div>}
    {error && <p className="settings-error" role="alert">{error.message}</p>}
  </div>;
}
