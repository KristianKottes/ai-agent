import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Headphones, Wrench } from "lucide-react";
import { languageName, studio, type Asset, type Job, type Language, type StudioTool, type ToolAction } from "./services/studio";

export const toolKey = ["studio-tools"];
export const projectStudioKey = (project: string) => ["project-studio", project];
export const presetsKey = ["studio-presets"];
export const pending = (job: Job) => job.state === "queued" || job.state === "running";
export function useStudioTools(enabled = true) {
  return useQuery({
    queryKey: toolKey, queryFn: studio.tools, enabled,
    refetchInterval: (q) => q.state.data?.some((tool) => ["downloading", "installing"].includes(tool.state)) ? 750 : false,
  });
}
export function StudioError({ error }: { error?: Error | null }) {
  return error ? <p className="settings-error" role="alert">{error.message}</p> : null;
}

export function toolStatus(tool: StudioTool, language?: Language) {
  if (!tool.compatible) return tool.download_bytes ? "Requirements unmet" : "Unavailable";
  switch (tool.state) {
    case "available": return "Install required";
    case "downloading": return "Downloading";
    case "installing": return "Installing";
    case "failed": return "Install failed";
    case "cancelled": return "Install cancelled";
    case "installed":
    case "bundled": {
      if (!tool.enabled) return "Disabled";
      const languages = tool.ready_languages ?? [];
      if (language) return languages.includes(language) ? "Ready" : "Needs base model";
      if (!tool.ready) return "Needs base model";
      if (languages.length && languages.length < tool.languages.length) {
        return `Ready for ${languages.map((item) => languageName[item]).join(", ")}`;
      }
      return "Ready";
    }
    default: return "Unavailable";
  }
}

export function StudioToolsPanel({ desktop }: { desktop: boolean }) {
  const client = useQueryClient();
  const query = useStudioTools(desktop);
  const action = useMutation({
    mutationFn: ({ id, action, consent }: { id: string; action: ToolAction; consent: boolean }) => studio.tool(id, action, consent),
    onSuccess: (data) => { client.setQueryData(toolKey, data); void client.invalidateQueries({ queryKey: ["voice-status"] }); },
  });
  if (!desktop) return null;
  const categories = [...new Set(query.data?.map((tool) => tool.category))];
  return <section className="studio-tools">
    <StudioError error={query.error ?? action.error} />
    {query.isPending && <p role="status">Loading models and media tools…</p>}
    {categories.map((category) => <section key={category}>
      <h2>{category}</h2>
      <div className="studio-tools-grid">
        {query.data?.filter((tool) => tool.category === category).map((tool) =>
          <ToolCard key={tool.id} tool={tool} busy={action.isPending} onAction={(kind, consent) => action.mutate({ id: tool.id, action: kind, consent: Boolean(consent) })} />)}
      </div>
    </section>)}
  </section>;
}

function ToolCard({ tool, busy, onAction }: { tool: StudioTool; busy: boolean; onAction: (action: ToolAction, consent?: boolean) => void }) {
  const [consent, setConsent] = useState(false);
  const installing = ["downloading", "installing"].includes(tool.state);
  const installed = ["installed", "bundled"].includes(tool.state);
  const externalModel = Boolean(tool.download_bytes);
  return <article className="studio-tool-card">
    <div className="studio-tool-title"><Wrench size={16} /><h3>{tool.name}</h3><span>{toolStatus(tool)}</span></div>
    <p>{[tool.version, ...tool.languages.map((l) => languageName[l]), tool.backend].filter(Boolean).join(" · ")}</p>
    <p>{tool.license_url ? <a href={tool.license_url} target="_blank" rel="noreferrer">{tool.license}</a> : tool.license}</p>
    {tool.runtime_license && <p>{tool.runtime_license}</p>}
    {tool.required_models && Object.entries(tool.required_models).map(([language, model]) =>
      <p key={language}>{languageName[language as keyof typeof languageName]} output uses {model}</p>)}
    {tool.platform && <p>{tool.platform}{tool.features?.length ? ` · ${tool.features.join(", ")}` : ""}</p>}
    {tool.ram_bytes && <p>Planning allowance · {Math.ceil(tool.ram_bytes / 1_000_000)} MB RAM · {Math.ceil((tool.disk_bytes ?? 0) / 1_000_000)} MB free disk during setup</p>}
    {tool.research_only && <p>Noncommercial evaluation only · disabled by default</p>}
    {tool.message && <p role="status">{tool.message}</p>}
    {installed && !tool.compatible && <p>{tool.compatibility_message}</p>}
    {tool.health && <p>{tool.health}</p>}
    {installing && <div className="studio-actions">
      <progress aria-label="Model download" max={tool.download_bytes} value={tool.downloaded_bytes} />
      <button className="secondary-button" disabled={busy || tool.state === "installing"} onClick={() => onAction("cancel")}>Cancel</button>
    </div>}
    {externalModel && !installed && !installing && <>
      {tool.compatible ? <div className="studio-actions">
        <label className="studio-check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />Accept model terms</label>
        <button className="primary-button" disabled={busy || !consent} onClick={() => onAction("install", consent)}>
          Install · {Math.ceil((tool.download_bytes ?? 0) / 1_000_000)} MB
        </button>
      </div> : <p>{tool.compatibility_message}</p>}
    </>}
    {installed && <div className="studio-actions">
      <button className="secondary-button" disabled={busy || !tool.ready} onClick={() => onAction("test")}>Test</button>
      <button className="secondary-button" disabled={busy} onClick={() => {
        if (tool.enabled) onAction("disable");
        else if (!tool.research_only || window.confirm("Enable this checkpoint for noncommercial evaluation only?")) onAction("enable", tool.research_only);
      }}>{tool.enabled ? "Disable" : "Enable"}</button>
      {externalModel && <button className="danger-button" disabled={busy} onClick={() => {
        if (window.confirm(`Remove ${tool.name} from this device?`)) onAction("remove");
      }}>Remove</button>}
    </div>}
  </article>;
}

export function Waveform({ values }: { values: number[] }) {
  const width = Math.max(1, values.length);
  return <svg className="studio-waveform" viewBox={`0 0 ${width} 48`} preserveAspectRatio="none" aria-label="Audio waveform" role="img">
    {values.map((value, index) => <line key={index} x1={index + .5} x2={index + .5} y1={24 - Math.max(1, value * 22)}
      y2={24 + Math.max(1, value * 22)} stroke="currentColor" strokeWidth=".85" />)}
  </svg>;
}

export function StudioJobs({ project, jobs, kind }: { project: string; jobs: Job[]; kind?: Job["kind"] }) {
  const client = useQueryClient();
  const cancel = useMutation({ mutationFn: (id: string) => studio.cancel(project, id),
    onSuccess: () => client.invalidateQueries({ queryKey: projectStudioKey(project) }) });
  const visible = jobs.filter((job) => (!kind || job.kind === kind) && job.state !== "completed").slice(0, 12);
  return visible.length ? <section className="studio-jobs" aria-label="Job queue">
    <h3>{kind === "render" ? "Render queue" : "Speech queue"}</h3>
    {visible.map((job) => <div className="studio-job" key={job.id}>
      <span>{job.kind === "speech" ? "Speech" : "Video"} · {job.state}</span>
      {job.state === "running" && <progress aria-label="Render progress" max={1} value={job.progress || undefined} />}
      {job.message && <span role="status">{job.message}</span>}
      {pending(job) && <button className="secondary-button" disabled={cancel.isPending} onClick={() => cancel.mutate(job.id)}>Cancel</button>}
    </div>)}
    <StudioError error={cancel.error} />
  </section> : null;
}

export function FileExports({ project, asset, reference }: { project?: string; asset: Pick<Asset, "id" | "name" | "kind" | "cues" | "extension" | "requested_format">; reference?: boolean }) {
  const save = useMutation({ mutationFn: (format: Parameters<typeof studio.download>[2]) =>
    studio.download(project, asset.id, format, asset.name.replace(/\.(wav|mp3)$/i, "")) });
  const preferred = "requested_format" in asset && asset.requested_format === "mp3";
  const formats = reference ? ["reference" as const] : asset.kind === "audio"
    ? (preferred ? ["mp3", "wav"] as const : ["wav", "mp3"] as const) : ["original" as const];
  return <>
    <div className="studio-actions">
      {[...formats, ...(asset.cues?.length ? ["srt", "ass"] as const : [])].map((format) =>
        <button key={format} className="secondary-button" disabled={save.isPending} onClick={() => save.mutate(format)}>
          <Download size={13} />{format === "reference" ? "Reference WAV" : format === "original" ? (asset.extension ?? "file").toUpperCase() : format.toUpperCase()}
        </button>)}
    </div>
    {save.data && <small className="studio-status" role="status">Saved to Downloads · {save.data.split(/[\\/]/).pop()}</small>}
    <StudioError error={save.error} />
  </>;
}

export function AssetPreview({ project, asset }: { project: string; asset: Asset }) {
  const [active, setActive] = useState(false);
  const source = useQuery({ queryKey: ["studio-media", project, asset.id], queryFn: () => studio.media(project, asset.id), enabled: active });
  if (source.data && asset.kind === "audio") return <audio controls src={source.data} aria-label={asset.name} />;
  if (source.data && asset.kind === "export") return <video controls src={source.data} aria-label={asset.name} />;
  return <>
    <button className="secondary-button" onClick={() => setActive(true)} disabled={source.isFetching}>
      <Headphones size={14} />{source.isFetching ? "Loading…" : "Preview"}
    </button>
    <StudioError error={source.error} />
  </>;
}
