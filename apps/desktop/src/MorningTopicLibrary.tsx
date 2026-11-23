import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, FileText, Sparkles } from "lucide-react";
import { DiscoveryTopicCard } from "./DiscoveryTopicCard";
import { StudioError, projectStudioKey } from "./StudioParts";
import { media, morningTopicsKey, type SavedMorningTopic, type MorningScript } from "./services/media";
import type { Language } from "./services/studio";

export const narrationWords = (script: MorningScript | null | undefined) => script?.scenes
  .reduce((total, scene, index) => total + (index === 0 && script.bible_text != null && script.bible_reference != null
    ? `${script.title}\n\n${script.bible_text}\n\n${script.bible_reference}` : scene.narration)
    .trim().split(/\s+/u).filter(Boolean).length, 0) ?? 0;

export function MorningTopicLibrary({ project, language, topics, selected, generating, canGenerate, onLanguage, onSelect }: {
  project: string; language: Language; topics: SavedMorningTopic[]; selected?: string; generating: boolean;
  canGenerate: boolean; onLanguage: (language: Language) => void; onSelect: (topic?: SavedMorningTopic) => void;
}) {
  const cache = useQueryClient();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [editor, setEditor] = useState<SavedMorningTopic | null>(null);
  const editorPanel = useRef<HTMLElement>(null);
  useEffect(() => { if (editor?.id) editorPanel.current?.scrollIntoView({ block: "start" }); }, [editor?.id]);
  const [lastDeleted, setLastDeleted] = useState<SavedMorningTopic | null>(null);
  const refresh = () => cache.invalidateQueries({ queryKey: morningTopicsKey(project) });
  const generate = useMutation({ mutationFn: () => media.morningTopics(project, language), onSuccess: () => {
    void cache.invalidateQueries({ queryKey: projectStudioKey(project) }); void refresh();
  } });
  const flag = useMutation({ mutationFn: ({ topic, ...change }: { topic: SavedMorningTopic; used?: boolean; deleted?: boolean }) =>
    media.flagMorningTopic(project, topic.id, { revision: topic.revision, ...change }),
    onSuccess: (saved, input) => {
      if (input.deleted) { setLastDeleted(saved); if (selected === saved.id) onSelect(undefined); }
      if (input.deleted === false) setLastDeleted(null);
      void refresh();
    }, onError: () => { void refresh(); } });
  const save = useMutation({ mutationFn: (topic: SavedMorningTopic) => media.saveMorningTopic(project, topic.id, {
    revision: topic.revision, title: topic.title, passage: topic.passage, angle: topic.angle, script: topic.script!,
  }), onSuccess: (saved) => { setEditor(saved); void refresh(); }, onError: () => { void refresh(); } });
  const query = search.trim().toLocaleLowerCase();
  const inLanguage = topics.filter((topic) => topic.language === language);
  const visible = inLanguage.filter((topic) => (filter === "all" || topic.used === (filter === "used"))
    && [topic.title, topic.passage, topic.angle, ...(topic.script?.scenes.map((scene) => scene.narration) ?? [])]
      .some((text) => text.toLocaleLowerCase().includes(query)));
  const busy = generating || generate.isPending;
  function edit(change: Partial<SavedMorningTopic>) { setEditor((value) => value ? { ...value, ...change } : value); save.reset(); }
  function editScene(index: number, change: Partial<MorningScript["scenes"][number]>) {
    if (editor?.script) edit({ script: { ...editor.script,
      scenes: editor.script.scenes.map((scene, i) => i === index ? { ...scene, ...change } : scene) } });
  }

  return <>
    <div className="section-heading topic-heading"><h2>Topic library <span className="count-pill">{inLanguage.length}</span></h2>
      <button type="button" className="primary-button" disabled={!canGenerate || busy} onClick={() => generate.mutate()}>
        {busy ? <><span className="spinner dark-spinner" /> Generating in background…</>
          : <><Sparkles size={15} /> Generate 5 topics & texts</>}
      </button>
    </div>
    <p>Topics and full narration are saved in this project as they finish. You can leave this page while generation runs.</p>
    <div className="topic-toolbar">
      <label className="topic-search"><span>Search saved text</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search titles, passages, or full text" /></label>
      <label><span>Video language</span><select value={language} onChange={(event) => onLanguage(event.target.value as Language)}>
        <option value="uk">Українська</option><option value="en">English</option>
      </select></label>
      <label><span>Topic status</span><select value={filter} onChange={(event) => setFilter(event.target.value)}>
        <option value="all">All topics</option><option value="unused">Unused</option><option value="used">Used</option>
      </select></label>
    </div>
    <StudioError error={generate.error ?? flag.error} />
    {generate.isSuccess && <p role="status">Topic generation was added to the background queue. Progress and retry controls are in Jobs.</p>}
    {lastDeleted && <div className="topic-batch-note" role="status"><span>Deleted “{lastDeleted.title}”.</span>
      <button type="button" className="text-button" disabled={flag.isPending} onClick={() => flag.mutate({ topic: lastDeleted, deleted: false })}>Undo delete</button>
    </div>}
    {visible.length ? <div className="topic-grid" lang={language}>{visible.map((topic) =>
      <DiscoveryTopicCard key={topic.id} category={topic.passage} title={topic.title} description={topic.angle}
        selected={selected === topic.id} topAction={<span className="morning-topic-state">{topic.used ? "Used" : "Unused"} · {topic.status === "ready" ? `${narrationWords(topic.script)} words` : topic.status === "pending" ? "Text pending" : "Text failed"}</span>}
        actions={<>
          <button type="button" className="primary-button" disabled={topic.status !== "ready"} aria-label={`Select topic: ${topic.title}`}
            aria-pressed={selected === topic.id} onClick={() => onSelect(topic)}>
            {selected === topic.id ? <><Check size={14} /> Selected</> : <><FileText size={14} /> Select topic</>}
          </button>
          <button type="button" className="secondary-button" disabled={!topic.script} onClick={() => { setEditor(structuredClone(topic)); save.reset(); }}>Preview & edit</button>
          <button type="button" className="text-button" disabled={flag.isPending} onClick={() => flag.mutate({ topic, used: !topic.used })}>Mark {topic.used ? "unused" : "used"}</button>
          <button type="button" className="text-button" disabled={flag.isPending} onClick={() => flag.mutate({ topic, deleted: true })}>Delete</button>
        </>}>
        {topic.error && <p className="settings-error">{topic.error} Retry this batch in Jobs.</p>}
      </DiscoveryTopicCard>)}</div> : <div className="empty-state topic-empty">
        <h3>{inLanguage.length ? "No topics match these filters" : "Build your topic library"}</h3>
        <p>Generate five Bible-based topics with full text, then preview and edit before making a video.</p>
      </div>}
    {editor?.script && <section ref={editorPanel} className="script-editor-card morning-topic-editor" aria-label="Saved topic editor">
      <div className="script-editor-head"><div><h3>Preview & edit saved text</h3><span>{narrationWords(editor.script)} words · {editor.script.scenes.length} scenes</span></div>
        <button type="button" className="text-button" onClick={() => setEditor(null)}>Close editor</button></div>
      <p>Your saved words are used for the video without another writing pass. Successful videos mark the topic used; you can change that status.</p>
      <details open><summary>Full narration preview</summary><div className="morning-full-text" lang={editor.language}>
        {editor.script.bible_text != null ? <>
          <div className="morning-opening-title"><span className="morning-block-label">Title</span><h4>{editor.title}</h4></div>
          <div><span className="morning-block-label">Bible text</span><p>{editor.script.bible_text}</p></div>
          <div><span className="morning-block-label">Bible reference</span><p>{editor.passage}</p></div>
          <div><span className="morning-block-label">Main text</span>{editor.script.scenes.slice(1).map((scene, index) => <p key={index}>{scene.narration}</p>)}</div>
        </> : editor.script.scenes.map((scene, index) => <p key={index}>{scene.narration}</p>)}
      </div></details>
      <form onSubmit={(event) => { event.preventDefault(); save.mutate(editor); }}>
        <fieldset disabled={save.isPending}>
          <label>Topic title<input required minLength={4} maxLength={100} value={editor.title}
            onChange={(event) => edit({ title: event.target.value, script: { ...editor.script!, title: event.target.value } })} /></label>
          {editor.script.bible_text != null && <label>Bible text<textarea required rows={5} maxLength={6000} value={editor.script.bible_text}
            lang={editor.language} onChange={(event) => edit({ script: { ...editor.script!, bible_text: event.target.value } })} /></label>}
          <label>Bible reference (after the passage)<input required minLength={4} maxLength={70} value={editor.passage}
            onChange={(event) => edit({ passage: event.target.value, script: { ...editor.script!, bible_reference: event.target.value } })} /></label>
          <label>Reflection angle<textarea required minLength={12} maxLength={240} value={editor.angle} onChange={(event) => edit({ angle: event.target.value })} /></label>
          {editor.script.scenes.map((scene, index) => <div className="morning-scene-text" key={index}>
            <h4>Scene {index + 1}</h4>
            <label>Block type · Scene {index + 1}<select value={scene.section} disabled={index === 0 && editor.script!.bible_text != null}
              onChange={(event) => editScene(index, { section: event.target.value as typeof scene.section })}>
              <option value="scripture">Bible text</option><option value="paragraph">Reflection</option><option value="poem">Poem</option>
            </select></label>
            {index === 0 && editor.script!.bible_text != null
              ? <p>Opening scene: title, Bible text, then its reference. Edit these separately above.</p>
              : <label>Narration · Scene {index + 1}<textarea rows={7} required minLength={25} maxLength={8000} value={scene.narration} lang={editor.language}
                  onChange={(event) => editScene(index, { narration: event.target.value })} /></label>}
            <label>Caption · Scene {index + 1}<input required minLength={4} maxLength={86} value={scene.caption}
              onChange={(event) => editScene(index, { caption: event.target.value })} /></label>
            <details><summary>Illustration description</summary><label>Image prompt · Scene {index + 1}<textarea required minLength={12} maxLength={310} value={scene.illustration}
              onChange={(event) => editScene(index, { illustration: event.target.value })} /></label></details>
          </div>)}
          <div className="script-editor-actions"><button className="primary-button" type="submit">{save.isPending ? "Saving…" : "Save topic & text"}</button>
            <button type="button" className="secondary-button" onClick={() => { const current = topics.find((item) => item.id === editor.id);
              if (current) { setEditor(structuredClone(current)); save.reset(); } }}>Reload saved topic</button></div>
        </fieldset>
      </form>
      {save.isSuccess && <p role="status">Topic and full text saved.</p>}
      <StudioError error={save.error} />
    </section>}
  </>;
}
