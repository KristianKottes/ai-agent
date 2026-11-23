import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CloneStudio, SpeechEditor } from "./StudioVoice";
import { Visualizer } from "./StudioVisualizer";
import { pending, presetsKey, projectStudioKey, StudioError, useStudioTools } from "./StudioParts";
import { studio } from "./services/studio";
import { useNavigation } from "./stores/navigation";
import type { Project } from "./types";

export function StudioPage({ desktop, projects, openCreate }: { desktop: boolean; projects: Project[]; openCreate: () => void }) {
  const { screen, studioProject, studioAsset, openAudioStudio } = useNavigation();
  const selected = projects.find((project) => project.id === studioProject) ?? projects[0];
  return <div className="page-content studio-page">
    <div className="studio-page-head">
      <h1>{screen === "visualizer" ? "Audio Visualizer" : screen === "cloning" ? "Voice Cloning" : "Voice Studio"}</h1>
      {desktop && <label>Project<select value={selected?.id ?? ""} onChange={(e) => openAudioStudio(screen === "visualizer" ? "visualizer" : screen === "cloning" ? "cloning" : "voice", e.target.value)}>
        {!selected && <option value="">No project yet</option>}
        {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
      </select></label>}
    </div>
    {!desktop ? <p>Open the desktop app to access local voice models and project media.</p>
      : !selected ? <div className="studio-card"><p>Create a project to save speech, visualizer settings, and exports in one place.</p>
        <button className="primary-button" onClick={openCreate}>New project</button></div>
      : <StudioWorkspace key={selected.id} project={selected.id} handoff={studioProject === selected.id ? studioAsset : undefined} />}
  </div>;
}

function StudioWorkspace({ project, handoff }: { project: string; handoff?: string }) {
  const { screen, openAudioStudio } = useNavigation();
  const client = useQueryClient();
  const tools = useStudioTools();
  const readyCloningModels = tools.data?.filter((tool) => tool.ready && tool.category === "Voice Cloning Models")
    .map((tool) => `${tool.id}:${tool.ready_languages?.join(",")}`).join(" ");
  const presets = useQuery({ queryKey: presetsKey, queryFn: studio.presets });
  const workspace = useQuery({
    queryKey: projectStudioKey(project), queryFn: () => studio.workspace(project),
    refetchInterval: (q) => q.state.data?.jobs.some(pending) ? 700 : false,
  });
  useEffect(() => {
    if (readyCloningModels !== undefined) void client.invalidateQueries({ queryKey: projectStudioKey(project) });
  }, [client, project, readyCloningModels]);
  const onVisualize = (asset: string) => openAudioStudio("visualizer", project, asset);
  return <>
    <StudioError error={workspace.error ?? tools.error ?? presets.error} />
    {workspace.isPending && <p role="status">Opening project…</p>}
    {workspace.data && <>
      <div hidden={screen !== "voice"}>
        <SpeechEditor project={project} workspace={workspace.data} tools={tools.data ?? []} onVisualize={onVisualize} />
      </div>
      <div hidden={screen !== "cloning"}>
        <CloneStudio project={project} workspace={workspace.data} tools={tools.data ?? []} onVisualize={onVisualize} />
      </div>
      {presets.data?.length && <div hidden={screen !== "visualizer"}>
        <Visualizer project={project} workspace={workspace.data} presets={presets.data} handoff={handoff} active={screen === "visualizer"} />
      </div>}
    </>}
  </>;
}
