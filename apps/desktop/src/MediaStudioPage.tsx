import { Activity, FolderOpen } from "lucide-react";
import { ImageStudio } from "./ImageStudio";
import { IllustratedVideo } from "./IllustratedVideo";
import { useProjectMedia } from "./MediaParts";
import { StudioError } from "./StudioParts";
import { VideoCreator } from "./VideoCreator";
import { useNavigation, type MediaScreen } from "./stores/navigation";
import type { Project } from "./types";
import "./media.css";

export function MediaStudioPage({ desktop, projects, openCreate }: { desktop: boolean; projects: Project[]; openCreate: () => void }) {
  const { screen, mediaProject, openMediaStudio } = useNavigation();
  const mode = screen === "images" || screen === "illustrated" ? screen : "video";
  const project = projects.find((item) => item.id === mediaProject) ?? projects[0];
  const projectPicker = <select value={project?.id ?? ""} onChange={(e) => openMediaStudio(mode, e.target.value)}>
    {!project && <option value="">No project yet</option>}
    {projects.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
  </select>;
  if (mode === "illustrated") return <div className="page-content discover-page morning-page">
    <h1 className="settings-title">Morning<br /><span>Inspiration.</span></h1>
    <p className="settings-subtitle">Discover Bible-based morning reflections and turn them into narrated videos.</p>
    {!desktop ? <div className="notice notice-warn"><Activity size={17} />
      <div><strong>Desktop runtime required</strong><span>Open the desktop app to generate topics and videos.</span></div>
    </div> : project ? <>
      <div className="topic-toolbar"><label><span>Project</span>{projectPicker}</label></div>
      <MediaWorkspace key={project.id} project={project.id} mode={mode} />
    </> : <div className="empty-state">
      <div className="empty-icon"><FolderOpen size={20} /></div><h3>Choose a home for your videos</h3>
      <p>Create a project to get started.</p>
      <button className="primary-button" onClick={openCreate}>New project</button>
    </div>}
  </div>;

  return <div className="page-content studio-page media-page">
    <div className="studio-page-head">
      <h1>{mode === "images" ? "Image Studio" : "Video Creator"}</h1>
      {desktop && <label>Project{projectPicker}</label>}
    </div>
    {!desktop ? <p>Open the desktop app to access project media.</p> : project ?
      <MediaWorkspace key={`${mode}-${project.id}`} project={project.id} mode={mode} /> :
      <section className="studio-card"><p>Create a project to save your media and exports.</p>
        <button className="primary-button" onClick={openCreate}>New project</button>
      </section>}
  </div>;
}

function MediaWorkspace({ project, mode }: { project: string; mode: MediaScreen }) {
  const { mediaAsset, mediaScene, mediaPrompt, openMediaStudio, clearMediaAsset, setScreen } = useNavigation();
  const query = useProjectMedia(project);
  if (!query.data) return query.error ? <StudioError error={query.error} /> : <p role="status">Opening project…</p>;
  if (mode === "illustrated") return <IllustratedVideo project={project} workspace={query.data} openTools={() => setScreen("runtime")} />;
  return mode === "images" ? <ImageStudio project={project} workspace={query.data}
    openTools={() => setScreen("runtime")} initialPrompt={mediaPrompt} onAddVideo={(id) => openMediaStudio("video", project, id, mediaScene)} /> :
    <VideoCreator project={project} workspace={query.data} handoff={mediaAsset} handoffScene={mediaScene} onHandoff={clearMediaAsset}
      openImage={(scene, prompt) => openMediaStudio("images", project, undefined, scene, prompt)} />;
}
