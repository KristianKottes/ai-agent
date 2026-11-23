import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clapperboard } from "lucide-react";
import { api } from "./services/api";
import type { Project, ProjectVideoJob, ProjectVideoRequest, ProjectVideoStage, VideoTopic } from "./types";

const stages: Record<ProjectVideoStage, string> = {
  script: "Script", director: "Scene plan", narration: "Narration", presenter: "Presenter",
  image: "FLUX still", video: "Wan clip", export: "MP4",
};

function running(job?: ProjectVideoJob | null) {
  return job?.state === "running" || job?.state === "cancelling";
}

export function ProjectVideoPanel({ project, topic, topics, onOpenScript }: {
  project: Project;
  topic: VideoTopic | null;
  topics: VideoTopic[];
  onOpenScript: (topicId: string, scriptId: string) => void;
}) {
  const cache = useQueryClient();
  const [voice, setVoice] = useState("af_bella");
  const [look, setLook] = useState<ProjectVideoRequest["look"]>("maya");
  const [format, setFormat] = useState<ProjectVideoRequest["format"]>("landscape");
  const [captions, setCaptions] = useState(false);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const jobKey = ["project-video", project.id];
  const query = useQuery({
    queryKey: jobKey, queryFn: () => api.latestProjectVideo(project.id),
    refetchInterval: (current) => running(current.state.data) ? 1000 : false,
    refetchIntervalInBackground: true,
  });
  const voiceStatus = useQuery({ queryKey: ["voice-status"], queryFn: api.voiceStatus });
  const job = query.data;
  const active = running(job);
  const jobTopic = topics.find((item) => item.id === job?.topic_id);
  const canResume = job?.state === "failed" || job?.state === "cancelled" || job?.state === "interrupted";
  const topicMismatch = Boolean(project.topic_id && topic && project.topic_id !== topic.id);
  const activePreview = previewId && previewId === job?.export_id ? previewId : null;
  const preview = useQuery({
    queryKey: ["export-preview", activePreview],
    queryFn: () => api.exportPreview(activePreview!),
    enabled: Boolean(activePreview),
  });
  const change = useMutation({
    mutationFn: (action: "start" | "resume" | "without-captions" | "stop") => {
      if (action === "start" && topic) {
        return api.startProjectVideo(project.id, { topic_id: topic.id, voice_id: voice, look, format, captions });
      }
      if (!job || action === "start") throw new Error("Select a topic for this project.");
      if (action === "stop") return api.cancelProjectVideo(project.id, job.id);
      return api.resumeProjectVideo(project.id, job.id, action === "without-captions" ? { captions: false } : {});
    },
    onSuccess: (updated) => cache.setQueryData(jobKey, updated),
  });

  useEffect(() => {
    if (job?.state !== "completed" || !job.script_id) return;
    for (const key of [
      ["projects"], ["saved-script", job.script_id],
      ["director", job.script_id], ["narration", job.script_id], ["scene-assets", job.script_id],
      ["video-exports", job.script_id], ["export-draft", job.script_id],
    ]) {
      void cache.invalidateQueries({ queryKey: key });
    }
  }, [cache, job?.id, job?.state, job?.script_id]);

  return <div className="project-video-panel">
    <div className="voice-heading"><Clapperboard size={18} /><div><h3>Generate project video</h3>
      <p>The 30-second preset uses an illustrated opening, a short generated clip, and a generated still. It runs locally on Apple Silicon with the models installed.</p>
    </div></div>
    {topic ? <p>Selected topic “{topic.title}”</p> : <p>Select a topic to start a video.</p>}
    {topicMismatch && <p>Choose the topic linked to this project, or select an empty project.</p>}
    <div className="voice-controls">
      <label>Voice
        <select value={voice} onChange={(event) => setVoice(event.target.value)} disabled={active}>
          {voiceStatus.data?.voices.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)
            ?? <option value="af_bella">Bella</option>}
        </select>
      </label>
      <label>Presenter
        <select value={look} onChange={(event) => setLook(event.target.value === "eli" ? "eli" : "maya")} disabled={active}>
          <option value="maya">Maya</option><option value="eli">Eli</option>
        </select>
      </label>
      <label>Format
        <select value={format} onChange={(event) => setFormat(event.target.value as ProjectVideoRequest["format"])} disabled={active}>
          <option value="landscape">Landscape</option><option value="vertical">Vertical</option><option value="square">Square</option>
        </select>
      </label>
      <label className="export-caption-toggle">
        <input type="checkbox" checked={captions} onChange={(event) => setCaptions(event.target.checked)} disabled={active} />
        English captions
      </label>
      <button type="button" className="primary-button" disabled={!topic || topicMismatch || active || query.isPending || change.isPending}
        onClick={() => change.mutate("start")}>{canResume ? "Start new video" : "Generate video"}</button>
    </div>
    {query.error && <p className="settings-error" role="alert">{query.error.message}</p>}
    {change.error && <p className="settings-error" role="alert">{change.error.message}</p>}
    {job && <div className="project-video-job">
      <p role="status">
        {job.state === "running" ? `Working on ${stages[job.stage]}` : job.state === "cancelling" ? "Stopping safely"
          : job.state === "completed" ? "MP4 saved" : `Job ${job.state} at ${stages[job.stage]}`}
        {jobTopic && <> · {jobTopic.title}</>}
      </p>
      <ul aria-label="Generation steps">{Object.entries(stages).map(([key, label]) => {
        const done = job.completed_stages.includes(key as ProjectVideoStage);
        return <li key={key} className={done ? "done" : undefined} aria-current={active && job.stage === key ? "step" : undefined}>
          {done && <Check size={12} aria-label="Complete" />}{label}
        </li>;
      })}</ul>
      {active && <progress aria-label={`${stages[job.stage]} progress`} max={1} value={job.progress ?? undefined} />}
      {job.message && <p>{job.message}</p>}
      <div className="voice-controls">
        {active && <button type="button" className="secondary-button" disabled={change.isPending || job.state === "cancelling"}
          onClick={() => change.mutate("stop")}>Stop job</button>}
        {canResume && <button type="button" className="secondary-button" disabled={change.isPending}
          onClick={() => change.mutate("resume")}>Resume job</button>}
        {canResume && job.stage === "export" && job.captions &&
          <button type="button" className="secondary-button" disabled={change.isPending}
            onClick={() => change.mutate("without-captions")}>Retry export without captions</button>}
        {job.export_id && <button type="button" className="secondary-button"
          onClick={() => setPreviewId(job.export_id)}>Preview MP4</button>}
        {!active && job.script_id && <button type="button" className="secondary-button"
          onClick={() => onOpenScript(job.topic_id, job.script_id!)}>Open saved script</button>}
      </div>
      {preview.isLoading && <p>Loading MP4…</p>}
      {preview.error && <p className="settings-error" role="alert">{preview.error.message}</p>}
      {preview.data && <video src={preview.data} controls playsInline aria-label="Generated project video" />}
    </div>}
  </div>;
}
