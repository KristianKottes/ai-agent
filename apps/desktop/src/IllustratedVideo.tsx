import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowLeft, ArrowRight, Check, Clapperboard, WandSparkles } from "lucide-react";
import { DiscoveryTopicCard } from "./DiscoveryTopicCard";
import { MorningTopicLibrary, narrationWords } from "./MorningTopicLibrary";
import { MorningPronunciation, withPauseDefaults } from "./MorningPronunciation";
import { AssetPreview, MediaJobs } from "./MediaParts";
import { pending, projectStudioKey, StudioError } from "./StudioParts";
import { useNavigation } from "./stores/navigation";
import { generationDefaultsKey, morningTopicsKey, media, type GenerationDefaults, type IllustratedInput, type MorningCaptions, type MorningNarrator, type MorningResolution,
  type MorningSpeechSettings, type MorningStyle } from "./services/media";
import { languageName, studio, type Language, type Workspace } from "./services/studio";

const steps = ["Topic", "Voice", "Style", "Video settings", "Review"];
const styles: { id: MorningStyle; name: string; description: string }[] = [
  { id: "watercolor", name: "Watercolor", description: "Soft color, warm paper, and fine pencil lines." },
  { id: "ink", name: "Ink sketch", description: "Black pen, crisp outlines, and light hatching." },
  { id: "cinematic", name: "Cinematic", description: "Warm sunrise light and lifelike detail." },
  { id: "biblical", name: "Biblical cinematic", description: "Biblical scenes and symbols, vivid colors, and glowing golden skies." },
];
const resolutions: Record<MorningResolution, string> = {
  "720p": "1280 × 720 · Landscape",
  "1080p": "1920 × 1080 · Landscape",
  vertical: "1080 × 1920 · Portrait",
};
const captionNames: Record<MorningCaptions, string> = {
  handwritten: "Handwritten", clean: "Clean sans serif", off: "No captions",
};
const narratorKey = (narrator: MorningNarrator) => `${narrator.model_id}:${narrator.voice}`;

type WizardDraft = {
  step: number; visited: number; language: Language; topicId?: string;
  voice: string; style: MorningStyle; resolution: MorningResolution; captions: MorningCaptions;
  scenes?: string;
  defaultsRevision?: number;
  speechSettings?: MorningSpeechSettings | null;
};
const drafts = new Map<string, WizardDraft>();

export function IllustratedVideo({ project, workspace, openTools }: {
  project: string; workspace: Workspace; openTools: () => void;
}) {
  const cache = useQueryClient();
  const openAudioStudio = useNavigation((state) => state.openAudioStudio);
  const openGenerationSettings = useNavigation((state) => state.openGenerationSettings);
  const [draft] = useState(() => drafts.get(project));
  const [step, setStep] = useState(draft?.step ?? 0);
  const [visited, setVisited] = useState(draft?.visited ?? 0);
  const [language, setLanguage] = useState<Language>(draft?.language ?? "uk");
  const [topicId, setTopicId] = useState<string | undefined>(draft?.topicId);
  const [defaultsApplied, setDefaultsApplied] = useState(Boolean(draft));
  const [defaultsRevision, setDefaultsRevision] = useState(draft?.defaultsRevision ?? -1);
  const [voice, setVoice] = useState(draft?.voice ?? "");
  const [scenes, setScenes] = useState(draft?.scenes ?? "");
  const [speechSettings, setSpeechSettings] = useState<MorningSpeechSettings | null>(draft?.speechSettings ?? null);
  const [speechReset, setSpeechReset] = useState(0);
  const [speechValid, setSpeechValid] = useState(false);
  const [style, setStyle] = useState<MorningStyle>(draft?.style ?? "watercolor");
  const [resolution, setResolution] = useState<MorningResolution>(draft?.resolution ?? "720p");
  const [captions, setCaptions] = useState<MorningCaptions>(draft?.captions ?? "handwritten");
  const [accepted, setAccepted] = useState(false);
  const [selected, setSelected] = useState<string>();
  const defaults = useQuery({ queryKey: generationDefaultsKey(project), queryFn: () => media.generationDefaults(project) });
  function applyVideoDefaults(saved: GenerationDefaults) {
    setScenes("");
    setLanguage(saved.language); setStyle(saved.style); setResolution(saved.resolution); setCaptions(saved.captions);
    setVoice(saved.narrator ? `${saved.narrator.model_id}:${saved.narrator.voice}` : "");
    setDefaultsRevision(saved.revision);
  }
  useEffect(() => {
    if (!defaultsApplied && defaults.data) { applyVideoDefaults(defaults.data); setDefaultsApplied(true); }
  }, [defaultsApplied, defaults.data]);
  const stepper = useRef<HTMLElement>(null);
  useEffect(() => {
    if (defaultsApplied) drafts.set(project, { step, visited, language, topicId, voice, style, resolution, captions, scenes, speechSettings, defaultsRevision });
  }, [project, step, visited, language, topicId, voice, style, resolution, captions, scenes, speechSettings, defaultsRevision, defaultsApplied]);
  const jobs = workspace.jobs.filter((job) => job.kind === "illustrated_video" || job.kind === "illustrated_model" || job.kind === "illustrated_topics");
  const queue = jobs.filter(pending).reverse();
  const videoQueued = queue.some((job) => job.kind === "illustrated_video");
  const modelJob = jobs.find((job) => job.kind === "illustrated_model");
  const installing = modelJob !== undefined && pending(modelJob);
  const status = useQuery({
    queryKey: ["illustrated-status", modelJob?.id, modelJob?.state],
    queryFn: media.illustratedStatus,
    refetchInterval: (query) => query.state.data?.supported && !query.state.data.writing_model ? 4000 : false,
  });
  const voices = useQuery({
    queryKey: ["morning-narrators", project],
    queryFn: () => media.morningNarrators(project),
    enabled: status.data?.supported === true,
  });
  const libraryJobStates = jobs.filter((job) => job.kind !== "illustrated_model").map((job) => `${job.id}:${job.state}`).join(",");
  const topicGenerating = queue.some((job) => job.kind === "illustrated_topics");
  const savedTopics = useQuery({ queryKey: morningTopicsKey(project), queryFn: () => media.savedMorningTopics(project),
    refetchInterval: topicGenerating ? 1200 : false });
  useEffect(() => { if (libraryJobStates) void cache.invalidateQueries({ queryKey: morningTopicsKey(project) }); }, [cache, project, libraryJobStates]);
  const topic = savedTopics.data?.find((item) => item.id === topicId && item.language === language);
  const refresh = () => cache.invalidateQueries({ queryKey: projectStudioKey(project) });
  const render = useMutation({ mutationFn: (input: IllustratedInput) => media.illustratedRender(project, input), onSuccess: refresh });
  const install = useMutation({ mutationFn: () => media.illustratedInstall(project), onSuccess: refresh });
  const cancel = useMutation({ mutationFn: (id: string) => studio.cancel(project, id), onSuccess: refresh });
  const retry = useMutation({ mutationFn: (id: string) => media.retry(project, id), onSuccess: refresh });
  const restoreDefaults = useMutation({ mutationFn: async () => {
    const [video, speech] = await Promise.all([media.generationDefaults(project), media.morningSpeechSettings(project)]);
    return { video, speech };
  }, onSuccess: ({ video, speech }) => {
    if (video.language !== language) changeLanguage(video.language);
    applyVideoDefaults(video); setSpeechSettings(withPauseDefaults(speech)); setDefaultsApplied(true);
    setSpeechReset((value) => value + 1);
    cache.setQueryData(generationDefaultsKey(project), video);
  } });
  const files = workspace.assets.filter((asset) => asset.source_type === "illustrated_video");
  const asset = files.find((file) => file.id === selected) ?? files[0];
  const download = useMutation({ mutationFn: () => studio.download(project, asset.id, "original", asset.name) });
  const thumbnail = useQuery({
    queryKey: ["morning-thumbnail", project, asset?.id],
    queryFn: () => studio.media(project, asset.id, "thumbnail"),
    enabled: Boolean(asset?.thumbnail),
  });
  const saveThumbnail = useMutation({
    mutationFn: () => studio.download(project, asset.id, "thumbnail", `${asset.name} — thumbnail`),
  });

  const available = status.data;
  const compatible = (voices.data ?? []).filter((option) => option.languages.includes(language));
  const narrator = compatible.find((option) => narratorKey(option) === voice);
  const ready = available?.supported && (Boolean(topic?.script) || available.writing_model) && available.illustrations && available.rendering;
  const pendingAction = render.isPending || install.isPending || cancel.isPending || retry.isPending;
  const sceneCount = scenes ? Number(scenes) : topic?.script?.scenes.length ?? null;
  const validSettings = Boolean(topic?.status === "ready" && topic.script && Number.isInteger(sceneCount) && sceneCount && sceneCount >= 2 && sceneCount <= 15);
  const complete = [validSettings, Boolean(narrator) && speechValid, true, validSettings];

  function moveTo(next: number) {
    setVisited(Math.max(visited, next));
    setStep(next);
    stepper.current?.scrollIntoView({ block: "nearest" });
  }

  function changeLanguage(nextLanguage: Language) {
    if (nextLanguage !== language) {
      setLanguage(nextLanguage);
      setStep(0);
      setVisited(0);
      setTopicId(undefined);
      setVoice("");
    }
  }

  function generate() {
    if (step !== 4 || !defaultsApplied || !ready || !topic || !narrator || !speechSettings || !speechValid || !validSettings || pendingAction) return;
    setSelected(undefined);
    render.mutate({ topic_id: topic.id, topic_revision: topic.revision, topic: topic.title, passage: topic.passage, angle: topic.angle, language,
      narrator: { model_id: narrator.model_id, voice: narrator.voice }, style, resolution, captions,
      seconds: null, scenes: sceneCount, text_range: null, speech_settings: speechSettings });
  }

  return <div className="illustrated-workspace">
    <section aria-label="Create a Morning Inspiration video">
      <div className="illustrated-actions morning-defaults-actions">
        <button type="button" className="secondary-button" onClick={() => openGenerationSettings(project)}>Generation defaults</button>
        <button type="button" className="text-button" disabled={restoreDefaults.isPending || defaults.isPending}
          onClick={() => restoreDefaults.mutate()}>{restoreDefaults.isPending ? "Loading defaults…" : "Use saved defaults"}</button>
        {defaults.data && defaultsRevision !== defaults.data.revision && defaultsApplied && <span>Saved defaults have changed.</span>}
      </div>
      <StudioError error={defaults.error || restoreDefaults.error} />
      {status.isPending && <div className="loading-row" role="status"><span className="spinner" /> Checking local tools…</div>}
      <StudioError error={status.error} />
      {available && !available.supported && <div className="notice notice-warn" role="status"><Activity size={17} />
        <div><strong>Generation unavailable on this device</strong><span>An Apple Silicon Mac with macOS 26 or later is required.</span></div>
      </div>}
      {available?.supported && <>
        <nav className="illustrated-stepper" aria-label="Video creation steps" ref={stepper}>
          <ol>{steps.map((name, index) => <li key={name}>
            <button type="button" className="filter-toggle" disabled={index > step && (index > visited || !complete.slice(0, index).every(Boolean))}
              aria-current={index === step ? "step" : undefined} onClick={() => moveTo(index)}><span>{index + 1}</span> {name}</button>
          </li>)}</ol>
        </nav>
        {step === 0 && <section className="topic-toolbar-section">
          <section className="model-install-card">
            <div className="model-card-main">
              <div className="model-card-icon"><WandSparkles size={19} /></div>
              <div className="model-card-title"><h2>MamayLM writing model</h2>
                <p>{(available.writing_model_bytes / 1e9).toFixed(1)} GB · Local topic and script generation</p>
              </div>
              <span className={`model-state-chip ${available.writing_model ? "ready" : installing ? "working" : ""}`}><span />
                {available.writing_model ? "READY OFFLINE" : installing ? "INSTALLING" : "NOT INSTALLED"}
              </span>
            </div>
            <div className="model-card-actions">
              {available.writing_model ? <>
                <span className="model-ready-note"><Check size={14} /> Inference runs on this device.</span>
                <button type="button" className="secondary-button" onClick={() => openGenerationSettings(project)}>Writing rules & defaults</button>
              </>
                : <>
                  <label className="model-license-accept">
                    <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} disabled={installing} />
                    <span>I accept the <a href={available.writing_model_terms} target="_blank" rel="noreferrer">Gemma terms of use</a>.</span>
                  </label>
                  <button type="button" className="primary-button" disabled={!accepted || installing || pendingAction}
                    onClick={() => install.mutate()}>{installing ? "Installing MamayLM…" : "Download and verify model"} <ArrowRight size={14} /></button>
                </>}
            </div>
          </section>
          <StudioError error={savedTopics.error} />
          <MorningTopicLibrary project={project} language={language} topics={savedTopics.data ?? []} selected={topic?.id}
            generating={topicGenerating} canGenerate={defaultsApplied && available.writing_model}
            onLanguage={changeLanguage} onSelect={(item) => setTopicId(item?.id)} />
        </section>}

        <section className="script-workspace illustrated-step" style={{ display: step === 1 ? undefined : "none" }}>
          <div className="section-heading"><h2>Choose a narrator</h2></div>
          {voices.isPending && <div className="loading-row" role="status"><span className="spinner" /> Loading voices…</div>}
          <StudioError error={voices.error} />
          <div className="script-editor-card">
            <div className="topic-toolbar"><label><span>Voice</span><select value={voice} onChange={(event) => setVoice(event.target.value)}>
              <option value="">Select a voice</option>
              {[false, true].map((cloned) => {
                const options = compatible.filter((option) => option.cloned === cloned);
                return options.length > 0 && <optgroup key={String(cloned)} label={cloned ? "Cloned voices" : "Built-in voices"}>
                  {options.map((option) => <option key={narratorKey(option)} value={narratorKey(option)}>{option.name}</option>)}
                </optgroup>;
              })}
            </select></label></div>
            {!voices.isPending && !compatible.length && <p>Install and enable a voice for {languageName[language]}.</p>}
            {!voices.isPending && voice && !narrator && <p>The saved voice is unavailable for this project or language. Choose a voice above.</p>}
            <div className="illustrated-actions">
              <button type="button" className="secondary-button" onClick={openTools}>Models &amp; Tools</button>
              <button type="button" className="secondary-button" onClick={() => openAudioStudio("cloning", project)}>Voice Cloning</button>
            </div>
            <p>Assigned, authorized voice clones for this project appear in the voice list.</p>
            <MorningPronunciation key={speechReset} project={project} workspace={workspace} language={language} topic={topic} narrator={narrator}
              value={speechSettings} onChange={setSpeechSettings} onValidityChange={setSpeechValid} />
          </div>
        </section>

        {step === 2 && <section className="script-workspace illustrated-step">
          <div className="section-heading"><h2>Choose a video style</h2></div>
          <div className="topic-grid" role="group" aria-label="Illustration style">
            {styles.map((item) => <DiscoveryTopicCard key={item.id} title={item.name} description={item.description}
              selected={style === item.id} actions={<button type="button" className="primary-button"
                aria-label={`Select ${item.name} style`} aria-pressed={style === item.id} onClick={() => setStyle(item.id)}>
                {style === item.id ? <><Check size={14} /> Selected</> : "Select style"}
              </button>} />)}
          </div>
        </section>}

        {step === 3 && <section className="script-workspace illustrated-step">
          <div className="section-heading"><h2>Set the video parameters</h2></div>
          <div className="topic-toolbar script-editor-card illustrated-options">
            <div><strong>Saved narration</strong><p>{narrationWords(topic?.script)} words · {sceneCount} scenes</p>
              <button type="button" className="text-button" onClick={() => moveTo(0)}>Preview or edit topic text</button></div>
            <label><span>Caption style</span><select value={captions} onChange={(event) => setCaptions(event.target.value as MorningCaptions)}>
              {Object.entries(captionNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select></label>
            <label><span>Resolution</span><select value={resolution} onChange={(event) => setResolution(event.target.value as MorningResolution)}>
              {Object.entries(resolutions).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select></label>
            <div><strong>Duration</strong><p>Auto · Based on saved narration and pauses</p></div>
            <label><span>Scenes (2–15)</span><input type="number" min={2} max={15} value={scenes}
              placeholder={`Auto · ${topic?.script?.scenes.length ?? 0} saved scenes`} onChange={(event) => setScenes(event.target.value)} /></label>
          </div>
          <p>Set writing length in project defaults before generating topics. Changing the scene count rearranges the saved words;
            complete Bible and poem blocks stay together. Your reviewed text is preserved.</p>
        </section>}

        {step === 4 && <section className="script-workspace illustrated-step">
          <div className="section-heading"><h2>Review your video</h2></div>
          <dl className="script-editor-card illustrated-review">
            <dt>Topic</dt><dd lang={language}>{topic?.title} · {topic?.passage}</dd>
            <dt>Language</dt><dd>{languageName[language]}</dd>
            <dt>Voice delivery</dt><dd>{narrator?.model_id === "zonos3-mini" ? "Natural pace" : `${speechSettings?.speed ?? 1}× speed`}
              {` · ${speechSettings?.pause_ms ?? 165} ms pause · ${Object.keys(speechSettings?.pronunciation ?? {}).length} pronunciation corrections`}</dd>
            <dt>Narrator</dt><dd>{narrator?.name ?? "Choose a narrator"}</dd>
            <dt>Style</dt><dd>{styles.find((item) => item.id === style)?.name}</dd>
            <dt>Captions</dt><dd>{captionNames[captions]}</dd>
            <dt>Resolution</dt><dd>{resolutions[resolution]}</dd>
            <dt>Duration</dt><dd>{"Auto · Based on saved narration"}</dd>
            <dt>Scenes</dt><dd>{sceneCount ?? "Auto"}</dd>
            <dt>Text length</dt><dd>{`${narrationWords(topic?.script)} saved words`}</dd>
            <dt>Block pauses</dt><dd>{`Title ${speechSettings?.title_pause_ms ?? 600} ms · Bible/poem ${speechSettings?.block_pause_ms ?? 900} ms · Paragraph ${speechSettings?.paragraph_pause_ms ?? 330} ms`}</dd>
          </dl>
          {(!available.illustrations || !available.rendering) && <div className="script-editor-card">
            {!available.illustrations && <p>Install FLUX.2 Klein 4B to generate illustrations.</p>}
            {!available.rendering && <p>Enable FFmpeg and Pillow. FFmpeg must support H.264 and AAC.</p>}
            <button type="button" className="secondary-button" onClick={openTools}>Models &amp; Tools</button>
          </div>}
        </section>}

        <div className="illustrated-controls">
          {step > 0 && <button className="secondary-button" onClick={() => moveTo(step - 1)}><ArrowLeft size={14} /> Back</button>}
          {step < 4 ? <button className="primary-button" disabled={!complete[step]} onClick={() => moveTo(step + 1)}>Next <ArrowRight size={14} /></button>
            : <button className="primary-button" disabled={!defaultsApplied || !ready || !topic || !narrator || !speechValid || !validSettings || pendingAction}
              onClick={generate}><Clapperboard size={15} /> {render.isPending ? "Adding to queue…" : videoQueued ? "Add to queue" : "Generate video"}</button>}
        </div>
        {step === 4 && render.isSuccess && <div className="topic-batch-note" role="status">
          <Check size={14} /><span>Added “{render.variables.topic}” to the video queue.</span>
        </div>}
      </>}
      <StudioError error={render.error || install.error || cancel.error || retry.error} />
    </section>

    <MediaJobs className="script-editor-card media-history" jobs={[...queue, ...jobs.filter((job) => !pending(job))]}
      onCancel={(id) => { if (!pendingAction) cancel.mutate(id); }}
      onRetry={(id) => { if (!pendingAction) retry.mutate(id); }} />

    {asset && <section className="script-workspace">
      <div className="section-heading"><h2>Saved videos</h2></div>
      <div className="script-editor-card illustrated-export">
        <div className="topic-toolbar"><label><span>Video</span><select value={asset.id} onChange={(event) => {
          setSelected(event.target.value); download.reset(); saveThumbnail.reset();
        }}>
          {files.map((file) => <option key={file.id} value={file.id}>{file.name} · {file.duration_seconds?.toFixed(1)} s</option>)}
        </select></label></div>
        <AssetPreview key={asset.id} project={project} asset={asset} />
        <button type="button" className="secondary-button" disabled={download.isPending} onClick={() => download.mutate()}>
          Save to Downloads
        </button>
        {download.data && <p role="status">Saved to {download.data}</p>}
        <StudioError error={download.error} />
        {asset.thumbnail && <div className="illustrated-thumbnail">
          <h3>Video thumbnail</h3>
          {thumbnail.data && <img src={thumbnail.data} alt={`Thumbnail for ${asset.name}`} />}
          {thumbnail.isPending && <p role="status">Loading thumbnail…</p>}
          <StudioError error={thumbnail.error} />
          <button type="button" className="secondary-button" disabled={saveThumbnail.isPending}
            onClick={() => saveThumbnail.mutate()}>Save thumbnail PNG</button>
          {saveThumbnail.data && <p role="status">Saved to {saveThumbnail.data}</p>}
          <StudioError error={saveThumbnail.error} />
        </div>}
        {asset.transcript && <details><summary>Spoken transcript</summary>
          <p lang={asset.language ?? "uk"} className="illustrated-transcript">{asset.transcript}</p>
        </details>}
      </div>
    </section>}
  </div>;
}
