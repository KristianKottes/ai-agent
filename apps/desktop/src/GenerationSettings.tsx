import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MamayConfiguration } from "./MamayConfiguration";
import { MorningPronunciation } from "./MorningPronunciation";
import { StudioError } from "./StudioParts";
import { generationDefaultsKey, media, type GenerationDefaults, type MorningSpeechSettings } from "./services/media";
import type { Workspace } from "./services/studio";
import type { Project } from "./types";
import { useNavigation } from "./stores/navigation";

const emptyWorkspace: Workspace = { draft: null, assets: [], jobs: [], profiles: [] };

export function GenerationSettings({ desktop, projects }: { desktop: boolean; projects: Project[] }) {
  const { generationProject, mediaProject, openGenerationSettings } = useNavigation();
  const project = projects.find((item) => item.id === (generationProject ?? mediaProject)) ?? projects[0];
  return <div className="page-content discover-page morning-page generation-settings">
    <div className="eyebrow">PREFERENCES</div>
    <h1 className="settings-title">Generation<br /><span>defaults.</span></h1>
    <p className="settings-subtitle">Text, voice, and video defaults for each Morning Inspiration project.
      New drafts use the selected project's saved settings; you can override them in the wizard.
      Queued videos keep their original settings.</p>
    {!desktop ? <p>Open the desktop app to configure generation defaults.</p> : project ? <>
      <div className="topic-toolbar"><label><span>Project settings for</span>
        <select value={project.id} onChange={(event) => openGenerationSettings(event.target.value)}>
          {projects.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
      </label></div>
      <ProjectGenerationSettings key={project.id} project={project} />
    </> : <p>Create a project to configure its generation defaults.</p>}
  </div>;
}

function ProjectGenerationSettings({ project }: { project: Project }) {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: generationDefaultsKey(project.id), queryFn: () => media.generationDefaults(project.id) });
  const [draft, setDraft] = useState<GenerationDefaults | null>(null);
  const [speech, setSpeech] = useState<MorningSpeechSettings | null>(null);
  const [tab, setTab] = useState("video");
  const [, setSpeechValid] = useState(false);
  useEffect(() => { if (draft === null && query.data) setDraft(query.data); }, [query.data, draft]);
  const voices = useQuery({ queryKey: ["morning-narrators", project.id],
    queryFn: () => media.morningNarrators(project.id) });
  const save = useMutation({ mutationFn: (input: GenerationDefaults) => media.saveGenerationDefaults(project.id, input), onSuccess: (saved) => {
    setDraft(saved); cache.setQueryData(generationDefaultsKey(project.id), saved);
  } });
  function edit(change: Partial<GenerationDefaults>) {
    setDraft((value) => value ? { ...value, ...change } : value); save.reset();
  }
  const range = draft?.text_range;
  const valid = Boolean(draft && (!range || (Number.isInteger(range.min_words) && Number.isInteger(range.max_words)
    && range.min_words >= 50 && range.max_words <= 1000 && range.min_words <= range.max_words))
    && (draft.seconds === null || (!range && Number.isInteger(draft.seconds) && draft.seconds >= 15 && draft.seconds <= 120))
    && (draft.scenes === null || (Number.isInteger(draft.scenes) && draft.scenes >= 2 && draft.scenes <= 15)));
  const dirty = Boolean(draft && JSON.stringify(draft) !== JSON.stringify(query.data));
  const available = (voices.data ?? []).filter((voice) => voice.languages.includes(draft?.language ?? "uk"));
  const narratorKey = draft?.narrator ? `${draft.narrator.model_id}:${draft.narrator.voice}` : "";

  return <>
      <div className="generation-settings-tabs" role="tablist" aria-label="Generation settings sections">
        {[['video', 'Text & video'], ['voice', 'Voice & pauses'], ['writing', 'Writing rules & examples']].map(([id, name]) =>
          <button key={id} type="button" role="tab" className="filter-toggle" aria-selected={tab === id}
            aria-controls={`generation-panel-${id}`} onClick={() => setTab(id)}>{name}</button>)}
      </div>
      <div id="generation-panel-video" role="tabpanel" style={{ display: tab === "video" ? undefined : "none" }}>
        {query.isPending && <p role="status">Loading generation defaults…</p>}
        <StudioError error={query.error} />
        {draft && <form className="script-editor-card generation-defaults-form" onSubmit={(event) => {
          event.preventDefault(); if (valid) save.mutate(draft);
        }}>
          <fieldset disabled={save.isPending}>
            <h2>Text defaults</h2>
            <div className="generation-settings-grid">
              <label>Default language<select value={draft.language} onChange={(event) => edit({ language: event.target.value as "uk" | "en", narrator: null })}>
                <option value="uk">Українська</option><option value="en">English</option>
              </select></label>
              <label>Text length<select value={range ? "range" : "auto"} onChange={(event) => edit({
                text_range: event.target.value === "range" ? { min_words: 300, max_words: 400 } : null, seconds: null })}>
                <option value="auto">Auto</option><option value="range">Word range</option>
              </select></label>
              {range && <>
                <label>Minimum words<input type="number" min="50" max="1000" value={range.min_words} required
                  onChange={(event) => edit({ text_range: { ...range, min_words: Number(event.target.value) } })} /></label>
                <label>Maximum words<input type="number" min="50" max="1000" value={range.max_words} required
                  onChange={(event) => edit({ text_range: { ...range, max_words: Number(event.target.value) } })} /></label>
              </>}
            </div>
            <p>A range such as 300–400 includes the spoken title, Bible passage, and reflection. The final text must stay inside your range.</p>
            <h2>Video defaults</h2>
            <div className="generation-settings-grid">
              <label>Video style<select value={draft.style} onChange={(event) => edit({ style: event.target.value as GenerationDefaults['style'] })}>
                <option value="watercolor">Watercolor</option><option value="ink">Ink sketch</option>
                <option value="cinematic">Cinematic</option><option value="biblical">Biblical cinematic</option>
              </select></label>
              <label>Resolution<select value={draft.resolution} onChange={(event) => edit({ resolution: event.target.value as GenerationDefaults['resolution'] })}>
                <option value="720p">1280 × 720 · Landscape</option><option value="1080p">1920 × 1080 · Landscape</option>
                <option value="vertical">1080 × 1920 · Portrait</option>
              </select></label>
              <label>Caption style<select value={draft.captions} onChange={(event) => edit({ captions: event.target.value as GenerationDefaults['captions'] })}>
                <option value="handwritten">Handwritten</option><option value="clean">Clean sans serif</option><option value="off">No captions</option>
              </select></label>
              <label>Duration<select value={draft.seconds === null ? "auto" : "fixed"}
                onChange={(event) => edit({ seconds: event.target.value === "auto" ? null : 60 })}>
                <option value="auto">Auto · Based on narration</option><option value="fixed" disabled={Boolean(range)}>Set a target</option>
              </select></label>
              {draft.seconds !== null && <label>Target seconds<input type="number" min="15" max="120" value={draft.seconds} required
                onChange={(event) => edit({ seconds: Number(event.target.value) })} /></label>}
              <label>Scenes (2–15)<input type="number" min="2" max="15" placeholder="Auto" value={draft.scenes ?? ""}
                onChange={(event) => edit({ scenes: event.target.value ? Number(event.target.value) : null })} /></label>
            </div>
            <h2>Default narrator</h2>
            <div className="generation-settings-grid">
              <label>Default voice<select value={narratorKey} onChange={(event) => {
                const voice = available.find((item) => `${item.model_id}:${item.voice}` === event.target.value);
                edit({ narrator: voice ? { model_id: voice.model_id, voice: voice.voice } : null });
              }}>
                <option value="">Choose in the video wizard</option>
                {narratorKey && !available.some((item) => `${item.model_id}:${item.voice}` === narratorKey)
                  && <option value={narratorKey}>Saved voice · unavailable in this project</option>}
                {available.map((item) => <option key={`${item.model_id}:${item.voice}`} value={`${item.model_id}:${item.voice}`}>
                  {item.name}{item.cloned ? " · Cloned" : ""}</option>)}
              </select></label>
            </div>
            <p>Cloned voices must be assigned and authorized in the project where you generate a video.</p>
            <StudioError error={voices.error} />
            <div className="script-editor-actions">
              <span>{dirty ? "Unsaved text and video defaults" : "Saved defaults"}</span>
              <button type="button" className="secondary-button" onClick={async () => {
                const result = await query.refetch(); if (result.data) { setDraft(result.data); save.reset(); }
              }}>Reload saved</button>
              <button type="submit" className="primary-button" disabled={!valid || !dirty}>
                {save.isPending ? "Saving…" : "Save text & video defaults"}
              </button>
            </div>
          </fieldset>
          {!valid && <p className="settings-error" role="alert">Use an ordered range of 50–1000 words and valid video settings.</p>}
          {save.isSuccess && !dirty && <p role="status" className="model-ready-note">Text and video defaults saved.</p>}
          <StudioError error={save.error} />
        </form>}
      </div>
      <div id="generation-panel-voice" role="tabpanel" style={{ display: tab === "voice" ? undefined : "none" }}>
        <div className="script-editor-card"><MorningPronunciation project={project.id} workspace={emptyWorkspace}
          language={draft?.language ?? "uk"} value={speech} onChange={setSpeech} onValidityChange={setSpeechValid} preferencesOnly /></div>
      </div>
      <div id="generation-panel-writing" role="tabpanel" style={{ display: tab === "writing" ? undefined : "none" }}>
        <MamayConfiguration project={project.id} />
      </div>
  </>;
}
