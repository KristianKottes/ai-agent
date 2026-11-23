import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { pending, projectStudioKey, StudioError } from "./StudioParts";
import { readMedia, studio, type Asset, type Job } from "./services/studio";

export function useProjectMedia(project: string) {
  return useQuery({
    queryKey: projectStudioKey(project), queryFn: () => studio.workspace(project),
    refetchInterval: (query) => query.state.data?.jobs.some(pending) ? 700 : false,
  });
}

export function AssetPreview({ project, asset, thumbnail = false }: { project: string; asset: Asset; thumbnail?: boolean }) {
  const kind = thumbnail && asset.thumbnail ? "thumbnail" : "original";
  const file = useQuery({ queryKey: ["project-media-file", project, asset.id, kind],
    queryFn: () => studio.media(project, asset.id, kind), retry: false });
  if (file.error) return <StudioError error={file.error} />;
  if (!file.data) return <span role="status">Loading {asset.name}…</span>;
  if (asset.kind === "image") return <img src={file.data} alt={asset.name} loading={thumbnail ? "lazy" : undefined} />;
  if (asset.kind === "audio") return <audio controls src={file.data} aria-label={asset.name} />;
  return <video controls playsInline src={file.data} aria-label={asset.name} />;
}

export function ImportMedia({ project, kind, onImported }: { project: string; kind: "image" | "audio"; onImported?: (assets: Asset[]) => void }) {
  const cache = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  async function load(files: File[]) {
    setBusy(true);
    setError(null);
    const assets = [];
    const failures = [];
    for (const file of files) {
      try {
        assets.push(await studio.import(project, kind, await readMedia(file)));
      } catch (reason) {
        failures.push(`${file.name}: ${reason instanceof Error ? reason.message : String(reason)}`);
      }
    }
    if (assets.length) onImported?.(assets);
    if (failures.length) setError(new Error(failures.join("\n")));
    await cache.invalidateQueries({ queryKey: projectStudioKey(project) });
    setBusy(false);
  }
  return <div className="media-import">
    <label className="secondary-button media-file">{busy ? "Importing…" : kind === "image" ? "Import images" : "Import audio"}
      <input type="file" multiple disabled={busy} accept={kind === "image" ? ".png,.jpg,.jpeg,.webp" : ".wav,.mp3,.flac,.m4a,.ogg"}
        onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; if (files.length) void load(files); }} />
    </label>
    <StudioError error={error} />
  </div>;
}

export function MediaJobs({ jobs, onCancel, onRetry, className = "studio-card media-history" }: {
  jobs: Job[]; onCancel: (id: string) => void; onRetry?: (id: string) => void; className?: string;
}) {
  const labels: Record<Job["kind"], string> = {
    speech: "Narration", render: "Render", video: "Video", image: "Image",
    illustrated_video: "Morning Inspiration", illustrated_model: "MamayLM download",
    illustrated_topics: "Topics and saved text",
  };
  if (!jobs.length) return null;
  return <details open={jobs.some(pending)} className={className}>
    <summary>Jobs ({jobs.length})</summary>
    {jobs.filter((job, index) => index < 15 || pending(job)).map((job) => <div key={job.id} className="media-job">
      <span>{job.topic || labels[job.kind]} · {job.state}</span>
      {pending(job) && <progress max={1} value={job.state === "running" ? job.progress : undefined}
        aria-label={job.message || "Job progress"} />}
      {job.message && <small>{job.message}</small>}
      {pending(job) && <button className="secondary-button" onClick={() => onCancel(job.id)}>Cancel</button>}
      {onRetry && job.kind !== "speech" && ["failed", "cancelled"].includes(job.state) &&
        <button className="secondary-button" onClick={() => onRetry(job.id)}>Retry</button>}
    </div>)}
  </details>;
}
