import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, ArrowRight, AudioLines, Bookmark, Check, Clapperboard, FileText, FolderOpen, Mic,
  HardDrive, Heart, ImageIcon, LayoutDashboard, Lightbulb, Package, Plus, RefreshCw, Save,
  Settings2, ShieldCheck, Sparkles, WandSparkles, X,
} from "lucide-react";
import { api, isDesktopRuntime } from "./services/api";
import { useNavigation } from "./stores/navigation";
import { VoicePanel } from "./VoicePanel";
import { StudioPage } from "./StudioPage";
import { StudioToolsPanel } from "./StudioParts";
import { MediaStudioPage } from "./MediaStudioPage";
import { DiscoveryTopicCard } from "./DiscoveryTopicCard";
import { NativeImageTools } from "./ImageStudio";
import { GenerationSettings } from "./GenerationSettings";
import "./studio.css";
import { ProjectVideoPanel } from "./ProjectVideoPanel";
import type { LocalModelStatus, Project, RuntimeComponent, RuntimeOperation, SystemReport, TopicCategory, VideoTopic } from "./types";

function App() {
  const { screen, setScreen, openAudioStudio } = useNavigation();
  const audioStudio = screen === "voice" || screen === "cloning" || screen === "visualizer";
  const screenTitles = { dashboard: "Dashboard", discover: "Discover topics", runtime: "Models & Tools", settings: "Settings",
    voice: "Voice Studio", cloning: "Voice Cloning", visualizer: "Audio Visualizer", images: "Image Studio", video: "Video Creator",
    illustrated: "Morning Inspiration", generation_settings: "Generation defaults" };
  const desktop = isDesktopRuntime();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [projectTitle, setProjectTitle] = useState("");
  const [openedProject, setOpenedProject] = useState<Project | null>(null);
  const contentRef = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0;
  }, [screen, openedProject?.id]);
  const client = useQueryClient();
  const health = useQuery({ queryKey: ["backend-health"], queryFn: api.health, enabled: desktop });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects, enabled: desktop });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, enabled: desktop && screen === "settings" });
  const system = useQuery({ queryKey: ["system-report"], queryFn: api.systemReport, enabled: desktop && screen === "settings" });
  const createProject = useMutation({
    mutationFn: api.createProject,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["projects"] });
      setProjectTitle("");
      setDialogOpen(false);
    },
  });

  function submitProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (projectTitle.trim()) createProject.mutate(projectTitle.trim());
  }

  function openStudio(project: Project | null = null) {
    setOpenedProject(project);
    setScreen("discover");
  }

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark"><Clapperboard size={19} strokeWidth={2.2} /></div>
          <div><div className="brand-name">Blogger Studio</div><div className="brand-caption">LOCAL CREATIVE SUITE</div></div>
        </div>
        <div className="sidebar-label">WORKSPACE</div>
        <nav className="nav-list" aria-label="Main navigation">
          <button className={`nav-item ${screen === "dashboard" ? "active" : ""}`} onClick={() => setScreen("dashboard")}>
            <LayoutDashboard size={17} /><span>Dashboard</span>
          </button>
          <button className={`nav-item ${screen === "discover" ? "active" : ""}`} onClick={() => openStudio()}>
            <Lightbulb size={17} /><span>Discover topics</span>
          </button>
          <button className={`nav-item ${screen === "images" ? "active" : ""}`} onClick={() => setScreen("images")}>
            <ImageIcon size={17} /><span>Image Studio</span>
          </button>
          <button className={`nav-item ${screen === "video" ? "active" : ""}`} onClick={() => setScreen("video")}>
            <Clapperboard size={17} /><span>Video Creator</span>
          </button>
          <button className={`nav-item ${screen === "illustrated" ? "active" : ""}`} onClick={() => setScreen("illustrated")}>
            <WandSparkles size={17} /><span>Morning Inspiration</span>
          </button>
          <button className={`nav-item ${screen === "voice" ? "active" : ""}`} onClick={() => setScreen("voice")}>
            <AudioLines size={17} /><span>Voice Studio</span>
          </button>
          <button className={`nav-item ${screen === "cloning" ? "active" : ""}`} onClick={() => setScreen("cloning")}>
            <Mic size={17} /><span>Voice Cloning</span>
          </button>
          <button className={`nav-item ${screen === "visualizer" ? "active" : ""}`} onClick={() => setScreen("visualizer")}>
            <Clapperboard size={17} /><span>Audio Visualizer</span>
          </button>
          <button className={`nav-item ${screen === "runtime" ? "active" : ""}`} onClick={() => setScreen("runtime")}>
            <Package size={17} /><span>Models &amp; Tools</span>
          </button>
        </nav>
        <div className="sidebar-label tools-label">PREFERENCES</div>
        <nav className="nav-list" aria-label="Preferences">
          <button className={`nav-item ${screen === "generation_settings" ? "active" : ""}`} onClick={() => setScreen("generation_settings")}>
            <WandSparkles size={17} /><span>Generation defaults</span>
          </button>
          <button className={`nav-item ${screen === "settings" ? "active" : ""}`} onClick={() => setScreen("settings")}>
            <Settings2 size={17} /><span>Settings</span>
          </button>
        </nav>
        <div className="sidebar-spacer" />
        <div className="side-status">
          <div className="side-status-top"><span className={`status-dot ${health.isSuccess ? "online" : ""}`} />Local backend</div>
          <div className="side-status-copy">{desktop ? health.isSuccess ? "Connected and ready" : "Waiting for connection" : "Desktop runtime required"}</div>
          <div className="local-badge"><span /> LOCAL FIRST</div>
        </div>
        <div className="sidebar-version">AI BLOGGER STUDIO <span>0.1.0</span></div>
      </aside>

      <div className="main-area">
        <header className="topbar">
          <div className="crumb"><span>Studio</span><span className="crumb-divider">/</span><strong>{screenTitles[screen]}</strong></div>
          <div className="topbar-right">
            <div className="secure-label"><span /> Your workspace stays on this device</div>
            <div className="avatar-circle">AB</div>
          </div>
        </header>

        <main className="main-content" ref={contentRef}>
          {screen === "dashboard" ? (
            <div className="page-content">
              <section className="welcome-row">
                <div>
                  <div className="eyebrow"><span className="eyebrow-line" /> YOUR CREATIVE WORKSPACE</div>
                  <h1>Good ideas start<br /><span>right here.</span></h1>
                  <p className="welcome-copy">Discover ideas, write a script, and plan the scenes.<br />Your writing model runs locally on this device.</p>
                </div>
                <div className="hero-art" aria-hidden="true">
                  <div className="hero-orbit orbit-one" /><div className="hero-orbit orbit-two" />
                  <div className="hero-glow" /><div className="hero-card hero-card-back"><div className="hero-card-lines" /></div>
                  <div className="hero-card hero-card-front"><div className="hero-play"><span /></div><div className="hero-card-foot"><span>LOCAL WORKFLOW</span><span>01 — 04</span></div></div>
                  <div className="hero-spark spark-a">✳</div><div className="hero-spark spark-b">✦</div>
                </div>
              </section>

              {!desktop && <div className="notice notice-warn"><Activity size={17} /><div><strong>Desktop runtime required</strong><span>Open this screen inside the Tauri desktop app to connect to local storage and the backend.</span></div></div>}
              {desktop && health.isError && <div className="notice notice-error"><Activity size={17} /><div><strong>Local backend unavailable</strong><span>{health.error instanceof Error ? health.error.message : "The backend did not respond."}</span></div><button className="text-button" onClick={() => health.refetch()}>Retry</button></div>}

              <section className="feature-strip">
                <div className="feature-intro"><div className="feature-icon"><Sparkles size={18} /></div><div><strong>Local writing workflow</strong><span>Turn an idea into a scene plan</span></div></div>
                <div className="pipeline-steps"><span className="pipeline-step current"><i>01</i>Topic discovery</span><span className="step-connector" /><span className="pipeline-step"><i>02</i>Script &amp; director</span><span className="step-connector" /><span className="pipeline-step"><i>03</i>Video export</span></div>
                <button className="phase-chip phase-button" onClick={() => openStudio()}>OPEN TOPIC STUDIO <ArrowRight size={11} /></button>
              </section>

              <section className="section-heading">
                <div><div className="eyebrow">YOUR LIBRARY</div><h2>Recent projects <span className="count-pill">{projects.data?.length ?? "—"}</span></h2></div>
                <button className="primary-button" onClick={() => setDialogOpen(true)} disabled={!desktop || !health.isSuccess}><Plus size={16} /> New project</button>
              </section>

              {projects.isLoading && <div className="loading-row"><span className="spinner" /> Loading projects…</div>}
              {projects.isError && <div className="notice notice-error"><FolderOpen size={17} /><div><strong>Projects could not be loaded</strong><span>{projects.error instanceof Error ? projects.error.message : "Try again when the local backend is available."}</span></div><button className="text-button" onClick={() => projects.refetch()}>Retry</button></div>}
              {projects.isSuccess && projects.data.length > 0 && <div className="project-grid">{projects.data.map((project) =>
                <ProjectCard key={project.id} project={project} onOpen={() => openStudio(project)} onVoice={() => openAudioStudio("voice", project.id)} />)}</div>}
              {projects.isSuccess && projects.data.length === 0 && <div className="empty-state">
                <div className="empty-icon"><FolderOpen size={21} /></div><div className="empty-kicker">A FRESH CANVAS</div>
                <h3>No projects yet</h3><p>Create a project space, then use the local writing studio to discover a topic and prepare its script.</p>
                <button className="secondary-button" onClick={() => setDialogOpen(true)} disabled={!desktop || !health.isSuccess}><Plus size={15} /> Create a project</button>
              </div>}

              <footer className="page-footer"><span>BUILT FOR IDEAS THAT TRAVEL</span><span><span className="footer-dot" /> PRIVATE BY DEFAULT</span></footer>
            </div>
          ) : screen === "discover" ? (
            <DiscoverTopicsPage key={openedProject?.id ?? "studio"} desktop={desktop} project={openedProject} />
          ) : screen === "runtime" ? (
            <RuntimeManagerPage desktop={desktop} />
          ) : screen === "generation_settings" ? (
            <GenerationSettings desktop={desktop} projects={projects.data ?? []} />
          ) : screen === "images" || screen === "video" || screen === "illustrated" ? (
            <MediaStudioPage desktop={desktop} projects={projects.data ?? []} openCreate={() => setDialogOpen(true)} />
          ) : audioStudio ? (
            <StudioPage desktop={desktop} projects={projects.data ?? []} openCreate={() => setDialogOpen(true)} />
          ) : (
            <div className="page-content settings-page">
              <div className="eyebrow"><span className="eyebrow-line" /> PREFERENCES</div><h1 className="settings-title">Settings</h1><p className="settings-subtitle">Platform and hardware information detected on this device.</p>
              <section className="settings-card"><div className="settings-card-head"><div><h2>Application status</h2><p>Connection and storage for this desktop workspace.</p></div><div className={`connection-chip ${health.isSuccess ? "connected" : ""}`}><span />{health.isSuccess ? "Connected" : desktop ? "Unavailable" : "Desktop only"}</div></div>
                <div className="settings-row"><div><strong>Local backend</strong><span>FastAPI sidecar · authenticated loopback connection</span></div><div className="setting-value">{health.data ? `v${health.data.backend_version}` : "—"}</div></div>
                <div className="settings-row"><div><strong>Database schema</strong><span>Local project and application state</span></div><div className="setting-value">{settings.data ? `v${settings.data.schema_version}` : "—"}</div></div>
                <div className="settings-row"><div><strong>Application data</strong><span>Managed by AI Blogger Studio</span></div><div className="setting-value path-value">{settings.data?.data_directory ?? "Connect from desktop to view"}</div></div>
                <div className="settings-row"><div><strong>Application logs</strong><span>Backend diagnostic log</span></div><div className="setting-value path-value">{settings.data?.logs_directory ?? "Connect from desktop to view"}</div></div>
                {settings.isError && <div className="settings-error">{settings.error instanceof Error ? settings.error.message : "Settings could not be loaded."}</div>}
              </section>
              <SystemInformation report={system.data} loading={system.isLoading} error={system.error} available={desktop} onRetry={() => system.refetch()} />
              <section className="settings-card limits-card"><div className="settings-card-head"><div><h2>Still in development</h2></div></div><div className="limit-tags"><span>Remote component catalog</span><span>Component updates</span><span>Resource monitoring</span><span>Windows and Linux verification</span><span>Complete app uninstaller</span></div></section>
              <footer className="page-footer"><span>BUILD 0.1.0</span><span><span className="footer-dot" /> LOCAL-FIRST FOUNDATION</span></footer>
            </div>
          )}
        </main>
      </div>

      {dialogOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDialogOpen(false); }}><form className="project-modal" onSubmit={submitProject}>
        <button className="modal-close" type="button" aria-label="Close" onClick={() => setDialogOpen(false)}><X size={17} /></button><div className="modal-icon"><Clapperboard size={19} /></div><div className="eyebrow">NEW WORKSPACE</div><h2>Name your project</h2><p>Save scripts, voices, visualizers, and exports together.</p>
        <label htmlFor="project-title">Project name</label><input autoFocus id="project-title" maxLength={120} value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="e.g. Ideas for the weekend" />
        {createProject.isError && <div className="form-error">{createProject.error instanceof Error ? createProject.error.message : "Project could not be saved."}</div>}
        <div className="modal-actions"><button type="button" className="secondary-button" onClick={() => setDialogOpen(false)}>Cancel</button><button type="submit" className="primary-button" disabled={!projectTitle.trim() || createProject.isPending}>{createProject.isPending ? "Saving…" : "Create project"}<ArrowRight size={15} /></button></div>
      </form></div>}
    </div>
  );
}

function ProjectCard({ project, onOpen, onVoice }: { project: Project; onOpen: () => void; onVoice: () => void }) {
  const date = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(project.created_at));
  return <article className="project-card"><div className="project-cover"><div className="cover-grid" /><div className="cover-mark"><Clapperboard size={18} /></div><span className="cover-type">PROJECT</span></div><div className="project-meta"><div><h3>{project.title}</h3><span>Created {date}</span></div></div><div className="project-card-status"><span className="project-status-dot" /> {project.script_id ? "Saved script linked" : "Ready to link a script"}</div>
    <button type="button" className="secondary-button project-open" onClick={onOpen}>Open project</button> <button type="button" className="secondary-button" onClick={onVoice}>Voice &amp; video</button></article>;
}

const TOPIC_CATEGORIES: TopicCategory[] = [
  "Personal Thoughts", "Modern Life", "Self Improvement", "Interesting Facts",
  "Lifestyle", "Motivation", "Technology", "Human Psychology",
];

function DiscoverTopicsPage({ desktop, project }: { desktop: boolean; project: Project | null }) {
  const client = useQueryClient();
  const [licenseAccepted, setLicenseAccepted] = useState(false);
  const [category, setCategory] = useState<"all" | TopicCategory>("all");
  const [search, setSearch] = useState("");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [usedFilter, setUsedFilter] = useState("all");
  const [sort, setSort] = useState("recent");
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(project?.topic_id ?? null);
  const [pinnedScript, setPinnedScript] = useState<string | null>(project?.script_id ?? null);
  const [projectId, setProjectId] = useState(project?.id ?? "");
  const [duration, setDuration] = useState(60);
  const [activeScript, setActiveScript] = useState<import("./types").ScriptDocument | null>(null);
  const [scriptDraft, setScriptDraft] = useState("");
  const [batchNotice, setBatchNotice] = useState<string | null>(null);

  const model = useQuery({
    queryKey: ["local-model"],
    queryFn: api.localModel,
    enabled: desktop,
    refetchInterval: (query) => query.state.data?.state === "downloading" ? 900 : false,
  });
  const topicList = useQuery({ queryKey: ["topics"], queryFn: api.topics, enabled: desktop });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects, enabled: desktop });
  const targetProject = projects.data?.find((item) => item.id === projectId);
  const selectedTopic = topicList.data?.find((topic) => topic.id === selectedTopicId) ?? null;
  const latestScript = useQuery({
    queryKey: pinnedScript ? ["saved-script", pinnedScript] : ["latest-script", selectedTopicId],
    queryFn: () => pinnedScript ? api.savedScript(pinnedScript)
      : selectedTopicId ? api.latestScriptForTopic(selectedTopicId) : Promise.resolve(null),
    enabled: desktop && Boolean(selectedTopicId),
  });
  const director = useQuery({
    queryKey: ["director", activeScript?.id],
    queryFn: () => activeScript ? api.directorPlan(activeScript.id) : Promise.resolve(null),
    enabled: desktop && Boolean(activeScript?.id),
  });
  const linkScript = useMutation({
    mutationFn: ({ projectId, scriptId }: { projectId: string; scriptId: string }) => api.linkProjectScript(projectId, scriptId),
    onSuccess: (updated) => cacheProject(updated),
  });

  function cacheProject(updated: Project) {
    client.setQueryData<Project[]>(["projects"], (items) => items?.map((item) => item.id === updated.id ? updated : item));
  }

  useEffect(() => {
    if (!selectedTopicId) {
      setActiveScript(null);
      setScriptDraft("");
      return;
    }
    if (latestScript.isSuccess) {
      setActiveScript(latestScript.data);
      setScriptDraft(latestScript.data?.full_narration ?? "");
    }
  }, [selectedTopicId, latestScript.data, latestScript.isSuccess]);

  useEffect(() => {
    if (selectedTopic && project?.script_id && activeScript?.id === project.script_id) {
      document.getElementById("script-workspace")?.scrollIntoView({ block: "start" });
    }
  }, [selectedTopic?.id, project?.script_id, activeScript?.id]);

  const installModel = useMutation({
    mutationFn: () => api.installLocalModel(true),
    onSuccess: async (result) => {
      client.setQueryData(["local-model"], result);
      await client.invalidateQueries({ queryKey: ["local-model"] });
    },
  });
  const cancelInstall = useMutation({
    mutationFn: api.cancelLocalModelInstall,
    onSuccess: (result) => client.setQueryData(["local-model"], result),
  });
  const generateTopics = useMutation({
    mutationFn: () => api.generateTopics(30, category === "all" ? undefined : category),
    onSuccess: async (result) => {
      setBatchNotice(`Saved ${result.created_count} new topics; filtered ${result.duplicate_count} duplicates. ${result.note}`);
      await client.invalidateQueries({ queryKey: ["topics"] });
    },
  });
  const updateTopic = useMutation({
    mutationFn: ({ topicId, patch }: { topicId: string; patch: { isFavorite?: boolean; isUsed?: boolean } }) => api.updateTopic(topicId, patch),
    onSuccess: () => client.invalidateQueries({ queryKey: ["topics"] }),
  });
  const regenerateTopic = useMutation({
    mutationFn: api.regenerateTopic,
    onSuccess: async () => {
      setBatchNotice("A fresh alternative was added to topic history.");
      await client.invalidateQueries({ queryKey: ["topics"] });
    },
  });
  const generateScript = useMutation({
    mutationFn: () => selectedTopic ? api.generateScript(selectedTopic.id, duration) : Promise.reject(new Error("Select a topic first.")),
    onSuccess: async (result) => {
      if (pinnedScript) {
        client.setQueryData(["saved-script", result.id], result);
        setPinnedScript(result.id);
      }
      setActiveScript(result);
      setScriptDraft(result.full_narration);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["topics"] }),
        client.invalidateQueries({ queryKey: ["latest-script", selectedTopicId] }),
        client.invalidateQueries({ queryKey: ["director", result.id] }),
      ]);
    },
  });
  const saveScript = useMutation({
    mutationFn: () => activeScript ? api.editScript(activeScript.id, scriptDraft) : Promise.reject(new Error("Generate or load a script first.")),
    onSuccess: async (result) => {
      client.setQueryData(pinnedScript ? ["saved-script", result.id] : ["latest-script", selectedTopicId], result);
      setActiveScript(result);
      setScriptDraft(result.full_narration);
      await client.invalidateQueries({ queryKey: ["director", result.id] });
    },
  });
  const createDirector = useMutation({
    mutationFn: () => activeScript ? api.generateDirector(activeScript.id) : Promise.reject(new Error("Generate or load a script first.")),
    onSuccess: (result) => client.setQueryData(["director", result.script_id], result),
  });

  const topics = (topicList.data ?? []).filter((topic) => {
    const query = search.trim().toLowerCase();
    const textMatch = !query || `${topic.title} ${topic.description} ${topic.category} ${topic.opening_hook} ${topic.target_audience}`.toLowerCase().includes(query);
    return textMatch
      && (category === "all" || topic.category === category)
      && (!favoritesOnly || topic.is_favorite)
      && (usedFilter === "all" || topic.is_used === (usedFilter === "used"));
  }).sort((left, right) => {
    if (sort === "originality") return right.originality_score - left.originality_score;
    if (sort === "title") return left.title.localeCompare(right.title);
    return right.created_at.localeCompare(left.created_at);
  });

  const modelStatus: LocalModelStatus | undefined = model.data;
  const hasModel = Boolean(modelStatus?.inference_ready);
  const scriptChanged = Boolean(activeScript && scriptDraft.trim() !== activeScript.full_narration);

  return <div className="page-content discover-page">
    <div className="eyebrow"><span className="eyebrow-line" /> PHASE 4 · LOCAL WRITING STUDIO</div>
    <h1 className="settings-title">Find the story<br /><span>worth telling.</span></h1>
    {project && <p className="settings-subtitle">Project · {project.title}</p>}
    <p className="settings-subtitle">Generate ideas, shape a presenter-ready script, and plan each scene with private on-device inference.</p>

    {!desktop && <div className="notice notice-warn"><Activity size={17} /><div><strong>Desktop runtime required</strong><span>Open this screen in the Tauri app to install the model and run local inference.</span></div></div>}
    {model.isError && <div className="notice notice-error"><Activity size={17} /><div><strong>Model status unavailable</strong><span>{model.error instanceof Error ? model.error.message : "Could not read local model status."}</span></div><button className="text-button" onClick={() => model.refetch()}>Retry</button></div>}
    {installModel.isError && <div className="notice notice-error"><Activity size={17} /><div><strong>Model install could not start</strong><span>{installModel.error.message}</span></div><button className="text-button" onClick={() => installModel.reset()}>Dismiss</button></div>}
    {generateTopics.isError && <div className="notice notice-error"><Sparkles size={17} /><div><strong>Topic generation failed</strong><span>{generateTopics.error.message}</span></div><button className="text-button" onClick={() => generateTopics.reset()}>Dismiss</button></div>}

    <section className="model-install-card">
      <div className="model-card-main">
        <div className="model-card-icon"><WandSparkles size={19} /></div>
        <div className="model-card-title"><div className="eyebrow">LOCAL LANGUAGE MODEL</div><h2>{modelStatus?.name ?? "Qwen2.5 0.5B Instruct · Q4_K_M"}</h2><p>491 MB / 469 MiB · Apache-2.0 weights · llama.cpp inference</p></div>
        <span className={`model-state-chip ${hasModel ? "ready" : modelStatus?.state === "downloading" ? "working" : ""}`}><span />{hasModel ? "READY OFFLINE" : modelStatus?.state === "downloading" ? "INSTALLING" : modelStatus?.state === "installed" ? "WEIGHTS INSTALLED" : "NOT INSTALLED"}</span>
      </div>
      <div className="model-card-detail">
        <div><strong>Runs on this device</strong><span>{modelStatus?.platform ?? "Checking platform…"} · {modelStatus?.platform_verification === "verified" ? "validated for this host" : "host not validated yet"}</span></div>
        <div><strong>Model license</strong><span><a href="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF" target="_blank" rel="noreferrer">Official Qwen model page</a> · source code runtime MIT</span></div>
      </div>
      {modelStatus && modelStatus.state !== "installed" && modelStatus.downloaded_bytes > 0 && <div className="model-progress-wrap"><div className="model-progress-head"><span>{modelStatus.message}</span><strong>{modelStatus.progress_percent.toFixed(0)}% · {formatBytes(modelStatus.downloaded_bytes)} / {formatBytes(modelStatus.expected_size_bytes)}</strong></div><div className="model-progress-track"><div style={{ width: `${modelStatus.progress_percent}%` }} /></div></div>}
      <div className="model-card-actions">
        {!hasModel && modelStatus?.state !== "downloading" && modelStatus?.state !== "installed" && <label className="model-license-accept"><input type="checkbox" checked={licenseAccepted} onChange={(event) => setLicenseAccepted(event.target.checked)} /><span>I reviewed the Apache-2.0 model license and approve downloading the 491 MB / 469 MiB weights.</span></label>}
        {modelStatus?.state === "downloading" ? <button className="secondary-button" onClick={() => cancelInstall.mutate()} disabled={cancelInstall.isPending}>{cancelInstall.isPending ? "Stopping…" : "Cancel download"}</button>
          : hasModel ? <span className="model-ready-note"><Check size={14} /> Inference stays offline after installation.</span>
            : modelStatus?.state === "installed" ? <span className="model-ready-note pending"><Activity size={14} /> Add the local runtime dependency to enable inference.</span>
              : <button className="primary-button" onClick={() => installModel.mutate()} disabled={!licenseAccepted || !modelStatus?.install_enabled || installModel.isPending}>{installModel.isPending ? "Starting…" : !modelStatus?.install_enabled ? "Unavailable on this host" : modelStatus.downloaded_bytes > 0 ? `Resume · ${formatBytes(modelStatus.downloaded_bytes)} saved` : "Download and verify model"}<ArrowRight size={14} /></button>}
      </div>
      {modelStatus && <div className={`model-status-note ${hasModel ? "" : "muted"}`}>{modelStatus.message}{modelStatus.state === "failed" && <button className="text-button" onClick={() => { installModel.reset(); installModel.mutate(); }}>Retry</button>}</div>}
    </section>

    <section className="topic-toolbar-section">
      <div className="section-heading topic-heading">
        <div><div className="eyebrow">AUTOMATIC TOPIC DISCOVERY</div><h2>Topic library <span className="count-pill">{topicList.data?.length ?? "—"}</span></h2></div>
        <button className="primary-button" onClick={() => generateTopics.mutate()} disabled={!hasModel || generateTopics.isPending}>
          {generateTopics.isPending ? <><span className="spinner dark-spinner" /> Generating 30 ideas…</> : <><Sparkles size={15} /> Generate 30 topics</>}
        </button>
      </div>
      <div className="topic-toolbar">
        <label className="topic-search"><span>Search history</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search titles, hooks, or descriptions" /></label>
        <label><span>Category</span><select value={category} onChange={(event) => setCategory(event.target.value as "all" | TopicCategory)}><option value="all">All categories</option>{TOPIC_CATEGORIES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label><span>Usage</span><select value={usedFilter} onChange={(event) => setUsedFilter(event.target.value)}><option value="all">All topics</option><option value="new">Not used</option><option value="used">Used</option></select></label>
        <label><span>Sort by</span><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="recent">Newest</option><option value="originality">Idea signal</option><option value="title">Title</option></select></label>
        <button className={`filter-toggle ${favoritesOnly ? "selected" : ""}`} onClick={() => setFavoritesOnly((value) => !value)}><Heart size={14} fill={favoritesOnly ? "currentColor" : "none"} /> Favorites</button>
      </div>
      {batchNotice && <div className="topic-batch-note"><Check size={14} /><span>{batchNotice}</span><button className="text-button" onClick={() => setBatchNotice(null)}>Dismiss</button></div>}
      {topicList.isLoading && <div className="loading-row"><span className="spinner" /> Loading your topic history…</div>}
      {topicList.isError && <div className="notice notice-error"><FolderOpen size={17} /><div><strong>Topic library unavailable</strong><span>{topicList.error instanceof Error ? topicList.error.message : "Could not read topic history."}</span></div><button className="text-button" onClick={() => topicList.refetch()}>Retry</button></div>}
      {topicList.isSuccess && topics.length > 0 && <div className="topic-grid">{topics.map((topic) => <TopicCard
        key={topic.id}
        topic={topic}
        selected={topic.id === selectedTopicId}
        busy={regenerateTopic.isPending || updateTopic.isPending}
        onSelect={() => { setPinnedScript(null); setSelectedTopicId(topic.id); }}
        onFavorite={() => updateTopic.mutate({ topicId: topic.id, patch: { isFavorite: !topic.is_favorite } })}
        onUse={() => updateTopic.mutate({ topicId: topic.id, patch: { isUsed: !topic.is_used } })}
        onRegenerate={() => regenerateTopic.mutate(topic.id)}
      />)}</div>}
      {topicList.isSuccess && topics.length === 0 && <div className="empty-state topic-empty"><div className="empty-icon"><Lightbulb size={20} /></div><div className="empty-kicker">A LOCAL IDEA ENGINE</div><h3>{topicList.data.length ? "No topics match these filters" : "Start with 30 fresh ideas"}</h3><p>{topicList.data.length ? "Adjust search or filters to return to your topic history." : hasModel ? "Choose a category or generate across all eight default categories." : "Install the verified local model above to begin."}</p><button className="secondary-button" onClick={() => { setSearch(""); setCategory("all"); setFavoritesOnly(false); setUsedFilter("all"); }} disabled={!topicList.data.length}>Clear filters</button></div>}
    </section>

    {desktop && (selectedTopic || projectId) && <section className="script-workspace" aria-label="Project video">
      {projects.data?.length ? <>
        <div className="voice-controls project-script-controls">
          <label>Project
            <select value={projectId} disabled={linkScript.isPending} onChange={(event) => setProjectId(event.target.value)}>
              <option value="">Choose a project</option>
              {projects.data.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}
            </select>
          </label>
        </div>
        {targetProject && <ProjectVideoPanel key={targetProject.id} project={targetProject} topic={selectedTopic}
          topics={topicList.data ?? []} onOpenScript={(topicId, scriptId) => {
            setSelectedTopicId(topicId);
            setPinnedScript(scriptId);
            requestAnimationFrame(() => document.getElementById("script-workspace")?.scrollIntoView({ block: "start" }));
          }} />}
      </> : projects.isSuccess && <p>Create a project on the Dashboard to generate a video for this topic.</p>}
    </section>}
    {selectedTopic && <section className="script-workspace" id="script-workspace">
      <div className="section-heading"><div><div className="eyebrow">SCRIPT WORKSHOP</div><h2>Build from “{selectedTopic.title}”</h2></div><span className="selected-topic-category">{selectedTopic.category}</span></div>
      <div className="script-controls">
        <div><strong>Choose a target duration</strong><span>The model drafts English narration, paragraph timings, visuals, on-screen text, and voice direction.</span></div>
        <label><span>Duration</span><select value={duration} onChange={(event) => setDuration(Number(event.target.value))}><option value={30}>30 seconds</option><option value={60}>60 seconds</option><option value={180}>3 minutes</option><option value={300}>5 minutes</option><option value={600}>10 minutes</option></select></label>
        <button className="primary-button" onClick={() => generateScript.mutate()} disabled={!hasModel || generateScript.isPending}>{generateScript.isPending ? <><span className="spinner dark-spinner" /> Writing…</> : <><FileText size={15} /> {activeScript ? "Regenerate script" : "Generate script"}</>}</button>
      </div>
      {!activeScript && latestScript.isLoading && <div className="loading-row"><span className="spinner" /> Checking saved scripts…</div>}
      {generateScript.isError && <div className="settings-error">{generateScript.error.message}</div>}
      {activeScript && <div className="script-editor-card">
        <div className="script-editor-head"><div><h3>{activeScript.title}</h3><span>{formatDuration(activeScript.requested_duration_seconds)} · {activeScript.quality.word_count} words · {activeScript.quality.target_word_range}</span></div><span className={`quality-chip ${activeScript.quality.status}`}>{activeScript.quality.status === "pass" ? "PACING CHECK PASSED" : "REVIEW PACING"}</span></div>
        {activeScript.quality.warnings.map((warning) => <div className="script-quality-warning" key={warning}>{warning}</div>)}
        <label className="script-narration-label" htmlFor="script-narration">FULL NARRATION · EDIT BEFORE PLANNING</label>
        <textarea id="script-narration" className="script-narration" value={scriptDraft} onChange={(event) => setScriptDraft(event.target.value)} />
        <div className="script-editor-actions"><span>{activeScript.paragraphs.length} paragraph divisions · estimated speech {formatDuration(activeScript.quality.estimated_spoken_duration_seconds)}</span><button className="secondary-button" onClick={() => saveScript.mutate()} disabled={!scriptChanged || saveScript.isPending || !scriptDraft.trim()}><Save size={14} />{saveScript.isPending ? "Saving…" : "Save edits"}</button><button className="primary-button" onClick={() => createDirector.mutate()} disabled={!hasModel || createDirector.isPending || scriptChanged}>{createDirector.isPending ? <><span className="spinner dark-spinner" /> Planning scenes…</> : <><WandSparkles size={14} /> Plan scenes</>}</button></div>
        {saveScript.isError && <div className="settings-error">{saveScript.error.message}</div>}
        {Boolean(projects.data?.length) && <div className="voice-controls project-script-controls">
          <span>{targetProject?.title ?? "Choose a project in the video panel above"}</span>
          <button type="button" className="secondary-button"
            disabled={!targetProject || linkScript.isPending || scriptChanged || targetProject.script_id === activeScript.id}
            onClick={() => linkScript.mutate({ projectId, scriptId: activeScript.id })}>
            {targetProject?.script_id === activeScript.id ? "Script linked" : linkScript.isPending ? "Linking…" :
              targetProject?.script_id ? "Replace linked script" : "Link saved script"}
          </button>
        </div>}
        {linkScript.error && <p className="settings-error" role="alert">{linkScript.error.message}</p>}
        {desktop && <VoicePanel key={activeScript.id} scriptId={activeScript.id} narrationText={activeScript.full_narration}
          scriptChanged={scriptChanged} director={director.data} projectId={targetProject?.script_id === activeScript.id ? targetProject.id : undefined} />}
        {createDirector.isError && <div className="settings-error">{createDirector.error.message}</div>}
        {director.data && <div className="director-plan">
          <div className="director-plan-head"><div><div className="eyebrow">STRUCTURED DIRECTOR TIMELINE</div><h3>{director.data.scenes.length} scenes · {formatDuration(director.data.total_duration_seconds)}</h3></div><span>{director.data.visual_style}</span></div>
          <div className="director-scene-list">{director.data.scenes.map((scene) => <article className="director-scene" key={scene.scene_id}>
            <div className="director-scene-time"><strong>{scene.scene_id.replace("scene-", "SCENE ")}</strong><span>{formatDuration(scene.start_seconds)} – {formatDuration(scene.start_seconds + scene.duration_seconds)}</span><span>{scene.scene_type.replaceAll("_", " ")}</span></div>
            <div className="director-scene-copy"><p>{scene.narration}</p><div><strong>Camera</strong> {scene.camera_angle} · {scene.camera_movement}</div><div><strong>Visual</strong> {scene.visual_description}</div><div><strong>Light / background</strong> {scene.lighting} · {scene.background}</div><details><summary>Generation prompt, subtitles &amp; audio</summary><p>{scene.generation_prompt}</p><p>{scene.subtitles}</p><span>{scene.audio_requirements.join(" · ")}</span></details></div>
          </article>)}</div>
          <div className="director-footnote"><ShieldCheck size={13} /> Choose these prompts in Generate a scene.</div>
        </div>}
        {director.isError && <div className="settings-error">{director.error.message}</div>}
      </div>}
      {activeScript && activeScript.paragraphs.length > 0 && <div className="script-paragraph-grid">{activeScript.paragraphs.map((paragraph) => <article className="script-paragraph-card" key={paragraph.paragraph_id}><div><strong>{paragraph.section.replaceAll("_", " ")}</strong><span>{formatDuration(paragraph.start_seconds)} · {formatDuration(paragraph.duration_seconds)}</span></div><p>{paragraph.text}</p><small>VISUAL · {paragraph.visual_suggestion}</small><small>ON SCREEN · {paragraph.on_screen_text}</small><small>VOICE · {paragraph.voice_direction}</small></article>)}</div>}
    </section>}
    <footer className="page-footer"><span><span className="footer-dot" /> SCRIPTS AND NARRATION STAY ON THIS DEVICE</span></footer>
  </div>;
}

function TopicCard({ topic, selected, busy, onSelect, onFavorite, onUse, onRegenerate }: {
  topic: VideoTopic;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  onFavorite: () => void;
  onUse: () => void;
  onRegenerate: () => void;
}) {
  return <DiscoveryTopicCard category={topic.category} title={topic.title} description={topic.description} selected={selected}
    topAction={<button className={`icon-action ${topic.is_favorite ? "favorite" : ""}`} onClick={onFavorite} aria-label={topic.is_favorite ? "Remove from favorites" : "Add to favorites"} disabled={busy}><Heart size={15} fill={topic.is_favorite ? "currentColor" : "none"} /></button>}
    actions={<><button className="primary-button" onClick={onSelect}><FileText size={14} />{selected ? "Selected for script" : "Create script"}</button><button className={`topic-used-button ${topic.is_used ? "used" : ""}`} onClick={onUse}><Bookmark size={13} fill={topic.is_used ? "currentColor" : "none"} />{topic.is_used ? "Used" : "Mark used"}</button><button className="topic-regenerate" onClick={onRegenerate} disabled={busy} aria-label="Regenerate an alternative topic"><RefreshCw size={14} /></button></>}>
    <blockquote>{topic.opening_hook}</blockquote>
    <div className="topic-facts"><div><span>Audience</span><strong>{topic.target_audience}</strong></div><div><span>Duration</span><strong>{formatDuration(topic.estimated_duration_seconds)}</strong></div><div><span>Idea distinctiveness</span><strong>{topic.originality_score}/100 <small>· model estimate</small></strong></div></div>
    <div className="topic-visual"><strong>THUMBNAIL CONCEPT</strong><span>{topic.thumbnail_concept}</span></div>
    <details className="topic-potential"><summary>Script potential</summary><p>{topic.script_potential}</p></details>
  </DiscoveryTopicCard>;
}

function formatDuration(seconds: number): string {
  const rounded = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return minutes ? `${minutes}:${remainder.toString().padStart(2, "0")}` : `${remainder}s`;
}

type RuntimeAction =
  | { kind: "install"; componentId: string }
  | { kind: "start"; componentId: string }
  | { kind: "stop"; componentId: string }
  | { kind: "remove"; componentId: string; confirmation: string }
  | { kind: "cache"; confirmation: string }
  | { kind: "temporary"; confirmation: string }
  | { kind: "uninstall"; confirmation: string };

type ConfirmationTarget =
  | { kind: "remove"; component: RuntimeComponent }
  | { kind: "cache" }
  | { kind: "temporary" }
  | { kind: "uninstall" };

function RuntimeManagerPage({ desktop }: { desktop: boolean }) {
  const client = useQueryClient();
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: api.runtime, enabled: desktop, refetchInterval: desktop ? 2500 : false });
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [platformFilter, setPlatformFilter] = useState("all");
  const [compatibility, setCompatibility] = useState("all");
  const [licenseAccepted, setLicenseAccepted] = useState<Record<string, boolean>>({});
  const [confirmationTarget, setConfirmationTarget] = useState<ConfirmationTarget | null>(null);
  const [confirmationText, setConfirmationText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const action = useMutation<RuntimeOperation, Error, RuntimeAction>({
    mutationFn: async (input) => {
      if (input.kind === "install") return api.installComponent(input.componentId);
      if (input.kind === "start") return api.startComponent(input.componentId);
      if (input.kind === "stop") return api.stopComponent(input.componentId);
      if (input.kind === "remove") return api.removeComponent(input.componentId, input.confirmation);
      if (input.kind === "cache") {
        const result = await api.clearRuntimeCache(input.confirmation);
        return { component_id: "", state: "cache_cleared", details: `Removed ${result.removed_files} registered cache files.` };
      }
      if (input.kind === "temporary") {
        const result = await api.clearRuntimeTemporary(input.confirmation);
        return { component_id: "", state: "temporary_files_cleared", details: `Removed ${result.removed_files} registered temporary files.` };
      }
      const result = await api.uninstallRuntimeTools(input.confirmation);
      return { component_id: "", state: result.state, details: "Installed tools removed. Project data was kept." };
    },
    onSuccess: async (result) => {
      setNotice(result.details ?? `Action completed: ${result.state}.`);
      setConfirmationTarget(null);
      setConfirmationText("");
      setLicenseAccepted({});
      await client.invalidateQueries({ queryKey: ["runtime"] });
    },
    onError: async (error) => {
      setNotice(error.message);
      await client.invalidateQueries({ queryKey: ["runtime"] });
    },
  });

  const components = runtime.data?.components ?? [];
  const categories = [...new Set(components.map((component) => component.category))].sort();
  const visibleComponents = components.filter((component) => {
    const query = search.trim().toLowerCase();
    const matchesText = !query || `${component.name} ${component.description} ${component.category}`.toLowerCase().includes(query);
    const matchesCategory = category === "all" || component.category === category;
    const matchesPlatform = platformFilter === "all" || component.platforms.some((item) => item.os === platformFilter);
    const matchesCompatibility = compatibility === "all"
      || (compatibility === "verified" && component.compatibility === "verified")
      || (compatibility === "installed" && ["installed", "running", "update_available"].includes(component.installation_state));
    return matchesText && matchesCategory && matchesPlatform && matchesCompatibility;
  });

  function expectedConfirmation(target: ConfirmationTarget | null): string {
    if (!target) return "";
    if (target.kind === "remove") return target.component.id;
    if (target.kind === "cache") return "CLEAR MANAGED CACHE";
    if (target.kind === "temporary") return "CLEAR TEMPORARY FILES";
    return "KEEP PROJECTS AND REMOVE TOOLS";
  }

  function submitConfirmation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!confirmationTarget) return;
    if (confirmationTarget.kind === "remove") {
      action.mutate({ kind: "remove", componentId: confirmationTarget.component.id, confirmation: confirmationText });
    } else if (confirmationTarget.kind === "cache") {
      action.mutate({ kind: "cache", confirmation: confirmationText });
    } else if (confirmationTarget.kind === "temporary") {
      action.mutate({ kind: "temporary", confirmation: confirmationText });
    } else {
      action.mutate({ kind: "uninstall", confirmation: confirmationText });
    }
  }

  return <div className="page-content runtime-page">
    <div className="eyebrow"><span className="eyebrow-line" /> LOCAL COMPONENT CATALOG</div>
    <h1 className="settings-title">Models &amp; Tools</h1>
    <p className="settings-subtitle">Manage local speech models, media tools, and runtime components.</p>
    {!desktop && <div className="notice notice-warn"><Activity size={17} /><div><strong>Desktop runtime required</strong><span>Open AI Blogger Studio on desktop to manage local components.</span></div></div>}
    <StudioToolsPanel desktop={desktop} />
    <NativeImageTools desktop={desktop} />
    {runtime.isLoading && <div className="loading-row"><span className="spinner" /> Loading the verified local catalog…</div>}
    {runtime.isError && <div className="notice notice-error"><Activity size={17} /><div><strong>Catalog unavailable</strong><span>{runtime.error instanceof Error ? runtime.error.message : "The local runtime catalog could not be loaded."}</span></div><button className="text-button" onClick={() => runtime.refetch()}>Retry</button></div>}
    {action.error && <div className="notice notice-error"><Activity size={17} /><div><strong>Runtime action failed</strong><span>{action.error.message}</span></div><button className="text-button" onClick={() => { action.reset(); setNotice(null); }}>Dismiss</button></div>}
    {notice && !action.error && <div className="notice runtime-notice"><ShieldCheck size={17} /><div><strong>Runtime manager</strong><span>{notice}</span></div><button className="text-button" onClick={() => setNotice(null)}>Dismiss</button></div>}

    {runtime.data && <>
      <section className="settings-card runtime-storage-card">
        <div className="settings-card-head"><div><h2><HardDrive size={14} /> Managed runtime storage</h2><p>Registered runtime files only. Project database and exports are retained separately.</p></div><div className="runtime-volume">{formatBytes(runtime.data.storage.available_bytes)} free</div></div>
        <div className="runtime-storage-grid">
          <div><span>Installed tools</span><strong>{formatBytes(runtime.data.storage.component_bytes)}</strong></div>
          <div><span>Voice model &amp; narration</span><strong>{formatBytes(runtime.data.storage.voice_bytes)}</strong></div>
          <div><span>Avatars and presenter clips</span><strong>{formatBytes(runtime.data.storage.avatar_bytes)}</strong></div>
          <div><span>Scene assets and exports</span><strong>{formatBytes(runtime.data.storage.export_bytes)}</strong></div>
          <div><span>Scene models</span><strong>{formatBytes(runtime.data.storage.scene_model_bytes)}</strong></div>
          <div><span>Download cache</span><strong>{formatBytes(runtime.data.storage.download_cache_bytes)}</strong></div>
          <div><span>Tool logs</span><strong>{formatBytes(runtime.data.storage.log_bytes)}</strong></div>
          <div><span>Registered files</span><strong>{runtime.data.storage.registered_file_count}</strong></div>
        </div>
        <div className="runtime-cleanup-actions">
          <button className="secondary-button" disabled={!desktop || action.isPending || runtime.data.storage.download_cache_bytes === 0} onClick={() => { setConfirmationTarget({ kind: "cache" }); setConfirmationText(""); }}>Clear managed cache</button>
          <button className="secondary-button" disabled={!desktop || action.isPending || runtime.data.storage.temporary_bytes === 0} onClick={() => { setConfirmationTarget({ kind: "temporary" }); setConfirmationText(""); }}>Clear temporary files</button>
          <button className="danger-button" disabled={!desktop || action.isPending || components.every((component) => !["installed", "running", "update_available"].includes(component.installation_state))} onClick={() => { setConfirmationTarget({ kind: "uninstall" }); setConfirmationText(""); }}>Remove all tools · keep projects</button>
        </div>
      </section>

      <div className="runtime-toolbar">
        <label className="runtime-search"><span>Search</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a component" /></label>
        <label className="runtime-filter"><span>Category</span><select value={category} onChange={(event) => setCategory(event.target.value)}><option value="all">All categories</option>{categories.map((item) => <option key={item} value={item}>{item.replaceAll("-", " ")}</option>)}</select></label>
        <label className="runtime-filter"><span>Platform</span><select value={platformFilter} onChange={(event) => setPlatformFilter(event.target.value)}><option value="all">All platforms</option><option value="macos">macOS</option><option value="windows">Windows</option><option value="linux">Linux</option></select></label>
        <label className="runtime-filter"><span>Show</span><select value={compatibility} onChange={(event) => setCompatibility(event.target.value)}><option value="all">All components</option><option value="verified">Verified here</option><option value="installed">Installed</option></select></label>
      </div>

      {visibleComponents.length ? <div className="runtime-grid">{visibleComponents.map((component) => <RuntimeComponentCard
        key={component.id}
        component={component}
        licenseAccepted={licenseAccepted[component.id] ?? false}
        busy={action.isPending}
        onAcceptLicense={(accepted) => setLicenseAccepted((current) => ({ ...current, [component.id]: accepted }))}
        onInstall={() => action.mutate({ kind: "install", componentId: component.id })}
        onStart={() => action.mutate({ kind: "start", componentId: component.id })}
        onStop={() => action.mutate({ kind: "stop", componentId: component.id })}
        onRemove={() => { setConfirmationTarget({ kind: "remove", component }); setConfirmationText(""); }}
      />)}</div> : <div className="empty-state runtime-empty"><div className="empty-icon"><Package size={20} /></div><div className="empty-kicker">NO MATCHES</div><h3>No components match these filters</h3><p>Clear a filter or change the search text to see local catalog entries.</p></div>}

      <div className="runtime-footer-note"><ShieldCheck size={14} /> Catalog integrity is pinned in the app. Every install is checksum-verified and registered before use. No model or external runtime is included.</div>
    </>}

    {confirmationTarget && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setConfirmationTarget(null); }}><form className="project-modal runtime-confirm-modal" onSubmit={submitConfirmation}>
      <button className="modal-close" type="button" aria-label="Close" onClick={() => setConfirmationTarget(null)}><X size={17} /></button>
      <div className="modal-icon"><ShieldCheck size={18} /></div>
      <div className="eyebrow">CONFIRM RUNTIME ACTION</div>
      <h2>{confirmationTarget.kind === "remove" ? `Remove ${confirmationTarget.component.name}?` : confirmationTarget.kind === "cache" ? "Clear managed cache?" : confirmationTarget.kind === "temporary" ? "Clear temporary files?" : "Remove all installed tools?"}</h2>
      <p>{confirmationTarget.kind === "remove" ? "Only registered files belonging to this component will be removed. Project data stays in place." : confirmationTarget.kind === "cache" ? "Only checksum-registered runtime download files will be removed. Other files in the cache directory are left alone." : confirmationTarget.kind === "temporary" ? "Only registered temporary files are removed. Active projects and installed tools remain." : "Installed tools, registered download cache, and registered temporary files will be removed. Projects and exports will be kept."}</p>
      <label htmlFor="runtime-confirmation">Type <strong>{expectedConfirmation(confirmationTarget)}</strong> to continue</label>
      <input autoFocus id="runtime-confirmation" value={confirmationText} onChange={(event) => setConfirmationText(event.target.value)} />
      <div className="modal-actions"><button type="button" className="secondary-button" onClick={() => setConfirmationTarget(null)}>Cancel</button><button type="submit" className="danger-button" disabled={action.isPending || confirmationText !== expectedConfirmation(confirmationTarget)}>{action.isPending ? "Working…" : "Confirm"}</button></div>
    </form></div>}
    <footer className="page-footer"><span>PHASE 3 · RUNTIME FOUNDATION</span><span><span className="footer-dot" /> LOCAL CATALOG ONLY</span></footer>
  </div>;
}

function RuntimeComponentCard({ component, licenseAccepted, busy, onAcceptLicense, onInstall, onStart, onStop, onRemove }: {
  component: RuntimeComponent;
  licenseAccepted: boolean;
  busy: boolean;
  onAcceptLicense: (accepted: boolean) => void;
  onInstall: () => void;
  onStart: () => void;
  onStop: () => void;
  onRemove: () => void;
}) {
  const installed = ["installed", "running", "update_available"].includes(component.installation_state);
  const running = component.installation_state === "running";
  const statusClass = component.compatibility === "verified" ? "verified" : component.compatibility === "not_tested" ? "untested" : "unsupported";
  const platformSummary = component.platforms.map((item) => `${item.os} ${item.architectures.join(", ")} · ${item.verification.replaceAll("_", " ")}`).join("; ");
  return <article className="runtime-component-card">
    <div className="runtime-card-top"><div className="runtime-component-icon"><Package size={18} /></div><div className="runtime-card-title"><h2>{component.name}</h2><span>{component.category.replaceAll("-", " ")} · v{component.version}</span></div>{component.experimental && <span className="runtime-experimental">EXPERIMENTAL</span>}<span className={`runtime-state ${component.installation_state.replaceAll("_", "-")}`}>{component.installation_state.replaceAll("_", " ")}</span></div>
    <p className="runtime-description">{component.description}</p>
    <div className="runtime-compatibility"><span className={`compatibility-mark ${statusClass}`}><ShieldCheck size={13} /> {component.compatibility.replaceAll("_", " ")}</span><span>{component.compatibility_reason}</span></div>
    <div className="runtime-detail-grid">
      <div><span>Source code license</span><strong>{component.license.source_code} · commercial use {component.license.commercial_use}</strong></div>
      <div><span>Model weights</span><strong>{component.license.model_weights}</strong></div>
      <div><span>Download / install size</span><strong>{formatBytes(component.download_size_bytes)} / {formatBytes(component.installed_size_bytes)}</strong></div>
      <div><span>Requirements</span><strong>{component.minimum_ram_bytes ? `${formatBytes(component.minimum_ram_bytes)} RAM` : "RAM not specified"} · GPU {component.gpu_requirements}</strong></div>
      <div><span>Platform entries</span><strong>{platformSummary}</strong></div>
      <div><span>Acceleration / dependencies</span><strong>{component.backends.join(", ") || "None"} · {component.dependencies.length ? component.dependencies.join(", ") : "No dependencies"}</strong></div>
      <div><span>Updates</span><strong>{component.update_available ? "Update available" : "No update listed"}</strong></div>
      <div><span>Official source</span><strong>{component.source}</strong></div>
    </div>
    <details className="runtime-license"><summary>License details · {component.license.license_file}</summary><pre>{component.license_text}</pre><span>Attribution required: {component.license.attribution_required ? "Yes" : "No"}</span></details>
    {installed && <details className="runtime-log"><summary>Process log{component.process_id ? ` · PID ${component.process_id}` : ""}</summary><pre>{component.log_tail || "No process output yet."}</pre></details>}
    <div className="runtime-component-actions">
      {!installed ? <>
        <label className="runtime-license-accept"><input type="checkbox" checked={licenseAccepted} onChange={(event) => onAcceptLicense(event.target.checked)} /><span>I reviewed and accept the license terms shown above.</span></label>
        <button className="primary-button" onClick={onInstall} disabled={busy || !licenseAccepted || component.compatibility !== "verified"}>{busy ? "Working…" : component.compatibility === "verified" ? "Install test utility" : "Unavailable on this device"}<ArrowRight size={14} /></button>
      </> : <>
        <button className="primary-button" onClick={running ? onStop : onStart} disabled={busy || (component.compatibility !== "verified" && !running)}>{busy ? "Working…" : running ? "Stop component" : "Start component"}<Activity size={14} /></button>
        <button className="danger-button" onClick={onRemove} disabled={busy}>Remove</button>
      </>}
    </div>
    {component.last_operation && <div className="runtime-last-operation">Last {component.last_operation.operation}: {component.last_operation.state} · {component.last_operation.details}</div>}
  </article>;
}

function SystemInformation({ report, loading, error, available, onRetry }: { report?: SystemReport; loading: boolean; error: unknown; available: boolean; onRetry: () => void }) {
  return <>
    <section className="settings-card">
      <div className="settings-card-head"><div><h2>System &amp; hardware</h2><p>Detected device details; model-specific compatibility is not assessed yet.</p></div></div>
      {loading && <div className="loading-row"><span className="spinner" /> Reading system information…</div>}
      {!available && <div className="settings-error">Open AI Blogger Studio in its desktop app to read system information.</div>}
      {error != null && <div className="settings-error">{error instanceof Error ? error.message : "System information could not be loaded."} <button className="text-button" onClick={onRetry}>Retry</button></div>}
      {report && <>
        <div className="settings-row"><div><strong>Platform</strong><span>Operating system and CPU architecture</span></div><div className="setting-value">{report.os_name} {report.os_version ?? ""} · {report.architecture}</div></div>
        <div className="settings-row"><div><strong>Processor</strong><span>{report.logical_cpu_count ?? "Unknown"} logical cores</span></div><div className="setting-value">{report.cpu_model ?? "Model not reported"}</div></div>
        <div className="settings-row"><div><strong>Memory</strong><span>{report.memory_available_method ?? "Available memory estimate unavailable"}</span></div><div className="setting-value">{formatBytes(report.memory_available_bytes)} free · {formatBytes(report.memory_total_bytes)} total</div></div>
        {report.gpus.length ? report.gpus.map((gpu, index) => <div className="settings-row" key={`${gpu.name}-${index}`}><div><strong>{gpu.vendor ? `${gpu.vendor} graphics` : "Graphics device"}</strong><span>{gpu.memory_kind ?? "GPU memory not reported"}{gpu.driver_version ? ` · Driver ${gpu.driver_version}` : ""}</span></div><div className="setting-value">{gpu.name}{gpu.memory_bytes != null ? ` · ${formatBytes(gpu.memory_bytes)}` : ""}</div></div>) : <div className="settings-row"><div><strong>Graphics devices</strong><span>The platform adapter did not return a GPU inventory.</span></div><div className="setting-value">Unknown</div></div>}
        <div className="accelerator-wrap"><div className="accelerator-title">ACCELERATION DETECTION</div><div className="limit-tags">{report.accelerators.map((accelerator) => <span key={accelerator.name} title={`${accelerator.evidence} Inference: ${accelerator.inference_compatibility}`}>{accelerator.name}: {accelerator.detection_status.replaceAll("_", " ")}</span>)}</div><p>{report.compatibility_note}</p></div>
      </>}
    </section>
    {report && <section className="settings-card">
      <div className="settings-card-head"><div><h2>Storage</h2><p>Capacity is measured on the volume containing the application data directory.</p></div></div>
      <div className="settings-row"><div><strong>Available space</strong><span>{formatBytes(report.storage.available_bytes)} available of {formatBytes(report.storage.total_bytes)}</span></div><div className="setting-value">{formatBytes(report.storage.used_bytes)} used</div></div>
      <div className="settings-row"><div><strong>Data directory</strong><span>Projects and application state</span></div><div className="setting-value path-value">{report.storage.data_directory}</div></div>
      <div className="settings-row"><div><strong>Cache directory</strong><span>Temporary and regenerable files</span></div><div className="setting-value path-value">{report.storage.cache_directory}</div></div>
      <div className="settings-row"><div><strong>Configuration directory</strong><span>Application preferences</span></div><div className="setting-value path-value">{report.storage.config_directory}</div></div>
      <div className="settings-row"><div><strong>Temporary files</strong><span>Application-managed temporary workspace</span></div><div className="setting-value path-value">{report.storage.temp_directory}</div></div>
    </section>}
  </>;
}

function formatBytes(value: number | null): string {
  if (value == null) return "Unknown";
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let amount = value / 1024;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(1)} ${units[unit]}`;
}

export default App;
