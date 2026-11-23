import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, AudioLines, FileUp, Mic } from "lucide-react";
import { useNavigation } from "./stores/navigation";
import { languageName, readMedia, studio, type Asset, type Language, type StudioTool, type Workspace } from "./services/studio";
import { AssetPreview, FileExports, projectStudioKey, StudioError, StudioJobs, toolStatus, Waveform } from "./StudioParts";

const sampleText = {
  en: "A clear voice makes a good story easier to follow.",
  uk: "Доброго ранку! Сьогодні на нас чекає чудовий день.",
};

function parseDictionary(value: string) {
  const entries = value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const [word, replacement, ...rest] = line.split("=").map((part) => part.trim());
    if (!word || !replacement || rest.length) throw new Error("Enter one spelling=pronunciation pair per line.");
    return [word, replacement];
  });
  return Object.fromEntries(entries);
}

export function SpeechEditor({ project, workspace, tools, cloning, profileId, onVisualize }: {
  project: string; workspace: Workspace; tools: StudioTool[]; cloning?: boolean; profileId?: string; onVisualize: (asset: string) => void;
}) {
  const client = useQueryClient();
  const setScreen = useNavigation((state) => state.setScreen);
  const [language, setLanguage] = useState<Language>(() =>
    workspace.profiles.find((profile) => profile.id === profileId)?.language ?? "en");
  const [modelId, setModel] = useState("");
  const [voiceId, setVoice] = useState("");
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [batch, setBatch] = useState<{ name: string; text: string }[]>([]);
  const [dictionary, setDictionary] = useState("");
  const [normalized, setNormalized] = useState<string>();
  const [speed, setSpeed] = useState(1);
  const [pause, setPause] = useState(120);
  const [selectedRate, setRate] = useState<number>();
  const [format, setFormat] = useState<"wav" | "mp3">("wav");
  const [normalizeAudio, setNormalizeAudio] = useState(true);
  const [research, setResearch] = useState(false);
  const [highlighted, setHighlighted] = useState<string>();
  const [error, setError] = useState<Error>();
  const speechModels = tools.filter((tool) => cloning ? tool.category === "Voice Cloning Models"
    : tool.category === "TTS Models" || (tool.category === "Voice Cloning Models" && tool.voices.length > 0));
  const candidates = speechModels.filter((tool) => tool.languages.includes(language));
  const modelOptions = cloning ? speechModels : candidates;
  const isReady = (tool?: StudioTool) => tool?.ready_languages?.includes(language) ?? false;
  const model = candidates.find((tool) => tool.id === modelId)
    ?? (cloning && language === "uk" ? candidates.find((tool) => tool.id === "holos-tts-uk-04" && isReady(tool)) : undefined)
    ?? candidates.find((tool) => isReady(tool) && !tool.research_only)
    ?? candidates.find(isReady) ?? candidates[0];
  const ready = isReady(model);
  const rate = selectedRate ?? model?.default_sample_rate ?? 24000;
  const requiredModel = model?.required_models?.[language];
  const voices = cloning ? workspace.profiles.filter((profile) => profile.assigned
    && model?.reference_languages?.includes(profile.language))
    : (model?.voices ?? []);
  const voice = voices.some((item) => item.id === voiceId) ? voiceId
    : voices.find((item) => item.id === profileId)?.id ?? voices[0]?.id;
  const referenceLanguages = model?.reference_languages?.map((item) => languageName[item]).join(" / ");
  const assetList = workspace.assets.filter((asset) => asset.kind === "audio"
    && (!cloning || (asset.cloned ?? tools.some((tool) => tool.id === asset.model_id && tool.category === "Voice Cloning Models"))));
  const normalize = useMutation({
    mutationFn: () => studio.normalize({ text, language, pronunciation: parseDictionary(dictionary) }),
    onSuccess: (result) => setNormalized(result.text),
  });
  const generate = useMutation({
    mutationFn: (sample: boolean) => {
      if (!model || !voice) throw new Error("Select a voice model and voice.");
      const texts = sample ? [{ name: language === "uk" ? "Зразок голосу" : "Voice sample", text: sampleText[language] }]
        : batch.length ? batch : [{ text, name: name.trim() || (language === "uk" ? "Озвучення" : "Narration") }];
      return studio.speech(project, texts.map((part) => ({
        ...part, language, model_id: model.id, voice, speed, pause_ms: pause, sample_rate: rate,
        pronunciation: parseDictionary(dictionary), normalized_text: !sample && !batch.length ? normalized : undefined,
        format, normalize_audio: normalizeAudio, noncommercial_confirmed: !!model.research_only && research,
      })));
    },
    onSuccess: (jobs) => {
      setHighlighted(jobs[0]?.id);
      return client.invalidateQueries({ queryKey: projectStudioKey(project) });
    },
  });
  function resetNormalization() {
    setNormalized(undefined);
    normalize.reset();
  }
  function selectModel(id: string) {
    const selected = modelOptions.find((tool) => tool.id === id);
    if (!selected) return;
    setModel(id);
    setVoice(cloning ? voice ?? "" : "");
    setResearch(false);
    const outputLanguage = selected.languages[0];
    if (outputLanguage && !selected.languages.includes(language)) {
      setLanguage(outputLanguage);
      resetNormalization();
    }
  }
  async function addTexts(files: FileList) {
    setError(undefined);
    try {
      if (files.length > 16) throw new Error("Select up to 16 text files per batch.");
      const read = await Promise.all(Array.from(files).map(async (file) => {
        const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()).trim();
        if (!text || text.length > 30_000) throw new Error(`${file.name} must contain 1–30,000 characters of UTF-8 text.`);
        return { name: file.name.replace(/\.txt$/i, "").slice(0, 120), text };
      }));
      if (read.length === 1) { setText(read[0].text); setName(read[0].name); setBatch([]); }
      else setBatch(read);
      resetNormalization();
    } catch (problem) {
      setError(problem instanceof Error ? problem : new Error("Could not read these text files."));
    }
  }

  return <>
    <section className="studio-card studio-speech">
      <div className="studio-card-heading"><AudioLines size={20} /><h2>{cloning ? "Generate with a voice profile" : "Text to speech"}</h2></div>
      <div className="studio-form-grid">
        <label>Output language<select value={language} onChange={(e) => {
          setLanguage(e.target.value as Language); setVoice(voice ?? ""); setResearch(false); resetNormalization();
        }}><option value="en">English</option><option value="uk">Українська (uk-UA)</option></select></label>
        {modelOptions.length > 0 && <label>Model<select value={model?.id ?? ""} onChange={(e) => selectModel(e.target.value)}>
          {!model && <option value="" disabled>Choose a model</option>}
          {modelOptions.map((item) => {
            const outputLanguage = item.languages.includes(language) ? language : item.languages[0];
            const status = toolStatus(item, outputLanguage);
            return <option key={item.id} value={item.id}>{item.name}
              {outputLanguage && outputLanguage !== language ? ` · switches to ${languageName[outputLanguage]}` : ""}
              {status === "Ready" ? "" : ` · ${status}`}</option>;
          })}
        </select></label>}
        <label>{cloning ? "Voice profile" : "Voice"}<select value={voice ?? ""} disabled={!voices.length} onChange={(e) => setVoice(e.target.value)}>
          {!voices.length && <option value="">Choose an assigned profile</option>}
          {voices.map((item) => <option value={item.id} key={item.id}>{item.name}{"locale" in item ? (item.locale && ` · ${item.locale}`) : ` · ${languageName[item.language]} reference`}</option>)}
        </select></label>
      </div>
      {!ready && <div className="studio-status">
        {model?.message || (model && !model.compatible ? model.compatibility_message :
          model?.state === "installed" && !model.enabled ? `Enable ${model.name} in Models & Tools.` :
          model?.state === "installed" && requiredModel ? `Install and enable ${requiredModel} in Models & Tools for this output language.` :
          model?.state === "downloading" || model?.state === "installing" ? `${toolStatus(model)} ${model.name}…` :
          model ? `Install ${model.name} in Models & Tools.` : "Choose a compatible local model to generate speech.")}
        <button className="text-button" onClick={() => setScreen("runtime")}>Open Models &amp; Tools <ArrowRight size={13} /></button>
      </div>}
      {cloning && model && !voices.length && referenceLanguages &&
        <p className="studio-status">{model.name} needs a voice profile with a {referenceLanguages} reference recording assigned to this project.</p>}
      <div className="studio-form-grid studio-text-import">
        <label>Audio name<input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="Narration" disabled={Boolean(batch.length)} /></label>
        <label className="studio-file"><FileUp size={15} />Import text files<input type="file" accept=".txt,text/plain" multiple
          onChange={(e) => { if (e.target.files?.length) void addTexts(e.target.files); e.target.value = ""; }} /></label>
      </div>
      {batch.length ? <div className="studio-batch">
        <span>{batch.length} files in this batch</span>
        <ul>{batch.map((file, index) => <li key={index}>{file.name}</li>)}</ul>
        <button className="secondary-button" onClick={() => setBatch([])}>Clear batch</button>
      </div> : <label>Speech text<textarea rows={8} lang={language === "uk" ? "uk-UA" : "en"} value={text} maxLength={30_000}
        placeholder={sampleText[language]} onChange={(e) => { setText(e.target.value); resetNormalization(); }} /></label>}
      <details className="studio-details"><summary>Pronunciation and pauses</summary>
        {model?.id === "holos-tts-uk-04" && <p>Use an accent mark to set Ukrainian word stress, for example «за́мок» or «замо́к».</p>}
        <div className="studio-form-grid">
          <label>{model?.id === "zonos3-mini" ? "Section pause (ms)" : "Sentence pause (ms)"}<input type="number" min="0" max="2000" step="10" value={pause}
            onChange={(e) => setPause(Math.max(0, Math.min(2000, Number(e.target.value))))} /></label>
          <label>Pronunciation dictionary<textarea rows={3} value={dictionary}
            placeholder={language === "uk" ? "за́мок=за́мок" : "Studio=stoo-dee-oh"}
            onChange={(e) => { setDictionary(e.target.value); resetNormalization(); }} /></label>
        </div>
        {!batch.length && <button className="secondary-button" disabled={!text.trim() || normalize.isPending}
          onClick={() => normalize.mutate()}>Preview normalized text</button>}
        {normalize.data?.mixed_language && <p className="studio-status">This text contains Latin and Cyrillic characters. The selected voice uses {languageName[language]} pronunciation.</p>}
        {normalized !== undefined && <label>Normalized text for synthesis<textarea rows={4} value={normalized} lang={language}
          onChange={(e) => setNormalized(e.target.value)} /></label>}
      </details>
      <div className="studio-form-grid">
        {model?.features?.includes("Speed") && <label>Speed · {speed.toFixed(2)}×<input type="range" min=".75" max="1.5" step=".05" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} /></label>}
        <label>Sample rate<select value={rate} onChange={(e) => setRate(Number(e.target.value))}>
          {[16000, 22050, 24000, 44100, 48000].map((value) => <option key={value} value={value}>{value / 1000} kHz</option>)}
        </select></label>
        <label>Preferred export<select value={format} onChange={(e) => setFormat(e.target.value as "wav" | "mp3")}><option value="wav">WAV</option><option value="mp3">MP3</option></select></label>
      </div>
      <label className="studio-check"><input type="checkbox" checked={normalizeAudio} onChange={(e) => setNormalizeAudio(e.target.checked)} />Normalize loudness</label>
      {ready && model?.research_only && <label className="studio-check">
        <input type="checkbox" checked={research} onChange={(e) => setResearch(e.target.checked)} />Use this checkpoint for noncommercial evaluation only
      </label>}
      <div className="studio-actions">
        <button className="primary-button" disabled={!ready || (model?.research_only && !research) || !voice || (!text.trim() && !batch.length) || normalized === "" || generate.isPending}
          onClick={() => generate.mutate(false)}>{generate.isPending ? "Queueing…" : batch.length ? `Generate ${batch.length} files` : "Generate speech"}</button>
        <button className="secondary-button" disabled={!ready || (model?.research_only && !research) || !voice || generate.isPending} onClick={() => generate.mutate(true)}>Generate voice sample</button>
      </div>
      <StudioError error={error ?? normalize.error ?? generate.error} />
    </section>
    <StudioJobs project={project} jobs={workspace.jobs} kind="speech" />
    <AudioHistory project={project} assets={assetList} highlighted={highlighted} onVisualize={onVisualize} />
  </>;
}

function AudioHistory({ project, assets, highlighted, onVisualize }: {
  project: string; assets: Asset[]; highlighted?: string; onVisualize: (asset: string) => void;
}) {
  if (!assets.length) return null;
  return <section className="studio-card studio-history">
    <h2>Audio history</h2>
    {assets.map((asset) => <article key={asset.id} className={highlighted === asset.id ? "studio-history-current" : ""}>
      <div><strong>{asset.name}</strong><p>{asset.language ? languageName[asset.language] + " · " : ""}{asset.duration_seconds?.toFixed(1)} s{asset.research_only ? " · Noncommercial evaluation" : ""}</p></div>
      <div className="studio-actions"><AssetPreview project={project} asset={asset} />
        <button className="primary-button" onClick={() => onVisualize(asset.id)}>Create visualizer <ArrowRight size={14} /></button>
      </div>
      <FileExports project={project} asset={asset} />
    </article>)}
  </section>;
}

function ProfilePreview({ profile, multiple }: { profile: string; multiple: boolean }) {
  const [open, setOpen] = useState(false);
  const media = useQuery({ queryKey: ["studio-reference", profile], queryFn: () => studio.media(undefined, profile, "reference"), enabled: open });
  return media.data ? <audio src={media.data} controls aria-label="Processed voice reference" />
    : <><button className="secondary-button" onClick={() => setOpen(true)} disabled={media.isFetching}>{multiple ? "Preview first recording" : "Preview reference"}</button><StudioError error={media.error} /></>;
}

function decodeReference(base64: string, type: string) {
  const data = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  return new Blob([data], { type });
}

const maxRecordings = 8;
const maxRecordingBytes = 32 * 1024 * 1024;
type VoiceRecording = {
  id: string; name: string; content_base64: string; blob: Blob;
  start: number; end: number | null; trim_silence: boolean; denoise: boolean;
};

function newRecording(name: string, content_base64: string, blob: Blob): VoiceRecording {
  return { id: crypto.randomUUID(), name, content_base64, blob, start: 0, end: null, trim_silence: true, denoise: false };
}

export function CloneStudio({ project, workspace, tools, onVisualize }: {
  project: string; workspace: Workspace; tools: StudioTool[]; onVisualize: (asset: string) => void;
}) {
  const client = useQueryClient();
  const [sources, setSources] = useState<VoiceRecording[]>([]);
  const [selected, setSelected] = useState<string>();
  const source = sources.find((clip) => clip.id === selected) ?? sources[0];
  const sourceBlob = source?.blob;
  const [importing, setImporting] = useState(false);
  const [sourceUrl, setSourceUrl] = useState<string>();
  const [wave, setWave] = useState<number[]>([]);
  const [language, setLanguage] = useState<Language>("en");
  const [name, setName] = useState("");
  const [accept, setAccept] = useState(false);
  const [seconds, setSeconds] = useState(8);
  const [choice, setChoice] = useState<string>();
  const [error, setError] = useState<Error>();
  const refresh = () => client.invalidateQueries({ queryKey: projectStudioKey(project) });
  const totalBytes = sources.reduce((size, clip) => size + clip.blob.size, 0);
  function append(clips: VoiceRecording[]) {
    if (sources.length + clips.length > maxRecordings || totalBytes + clips.reduce((size, clip) => size + clip.blob.size, 0) > maxRecordingBytes) {
      setError(new Error("Use up to 8 recordings and 32 MiB in total."));
      return;
    }
    setSources([...sources, ...clips]);
    setSelected(clips[0].id);
    setAccept(false);
    setError(undefined);
  }
  function updateSource(changes: Partial<VoiceRecording>) {
    setSources((clips) => clips.map((clip) => clip.id === source?.id ? { ...clip, ...changes } : clip));
  }
  async function importRecordings(files: File[]) {
    if (sources.length + files.length > maxRecordings || totalBytes + files.reduce((size, file) => size + file.size, 0) > maxRecordingBytes) {
      setError(new Error("Use up to 8 recordings and 32 MiB in total."));
      return;
    }
    setImporting(true);
    try {
      const clips = await Promise.all(files.map(async (file) => newRecording(file.name, (await readMedia(file)).content_base64, file)));
      append(clips);
    } catch (problem) {
      setError(problem instanceof Error ? problem : new Error("Could not read these recordings."));
    } finally {
      setImporting(false);
    }
  }
  const recorder = useMutation({
    mutationFn: () => studio.record(project, seconds),
    onSuccess: ({ content_base64 }) => {
      append([newRecording(`Microphone recording ${sources.length + 1}`, content_base64, decodeReference(content_base64, "audio/wav"))]);
    },
  });
  const save = useMutation({
    mutationFn: () => {
      const [primary, ...additional] = sources.map(({ name, content_base64, start, end, trim_silence, denoise }) =>
        ({ name, content_base64, start, end, trim_silence, denoise }));
      return studio.createProfile(project, { ...primary, name, language, authorization_confirmed: accept, additional_recordings: additional });
    },
    onSuccess: async (profile) => { setChoice(profile.id); setSources([]); setSelected(undefined); setAccept(false); await refresh(); },
  });
  const assign = useMutation({
    mutationFn: ({ id, assigned }: { id: string; assigned: boolean }) => studio.assignProfile(project, id, assigned),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: studio.deleteProfile, onSuccess: refresh });
  useEffect(() => {
    setWave([]);
    setSourceUrl(undefined);
    if (!sourceBlob) return;
    const url = URL.createObjectURL(sourceBlob);
    setSourceUrl(url);
    let live = true;
    const context = new AudioContext();
    void sourceBlob.arrayBuffer().then((bytes) => context.decodeAudioData(bytes)).then((audio) => {
      if (!live) return;
      const data = audio.getChannelData(0);
      const chunk = Math.max(1, Math.ceil(data.length / 180));
      const levels = [];
      for (let index = 0; index < data.length; index += chunk) {
        let peak = 0;
        for (let sample = index; sample < Math.min(data.length, index + chunk); sample++) peak = Math.max(peak, Math.abs(data[sample]));
        levels.push(peak);
      }
      setWave(levels);
    }).catch(() => { /* The local decoder still validates or imports the recording. */ });
    return () => { live = false; URL.revokeObjectURL(url); void context.close(); };
  }, [sourceBlob]);
  const busy = importing || recorder.isPending || save.isPending;
  return <>
    <section className="studio-card">
      <div className="studio-card-heading"><Mic size={20} /><h2>New voice profile</h2></div>
      <p>Add up to 8 recordings of the same speaker (32 MiB total), with 3–30 seconds of clean speech per clip after trimming. Use a similar microphone setup and the delivery you want to reproduce.</p>
      <div className="studio-form-grid">
        <label>Profile name<input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></label>
        <label>Reference language<select value={language} onChange={(e) => setLanguage(e.target.value as Language)}>
          <option value="en">English</option><option value="uk">Українська</option>
        </select></label>
      </div>
      <div className="studio-actions">
        <label className="studio-file"><FileUp size={15} />Add MP3 or WAV recordings<input type="file" accept=".mp3,.wav" multiple disabled={busy || sources.length >= maxRecordings} onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) void importRecordings(files);
        }} /></label>
        <label className="studio-inline">Record for <input aria-label="Recording length in seconds" type="number" min="3" max="30" value={seconds}
          onChange={(e) => setSeconds(Number(e.target.value))} /> seconds</label>
        <button className="secondary-button" disabled={busy || sources.length >= maxRecordings || seconds < 3 || seconds > 30} onClick={() => recorder.mutate()}>
          <Mic size={14} />{recorder.isPending ? "Recording…" : "Record microphone"}
        </button>
      </div>
      {source && <div className="studio-reference">
        <div className="studio-form-grid">
          <label>Recordings ({sources.length}/{maxRecordings})<select value={source.id} onChange={(e) => setSelected(e.target.value)}>
            {sources.map((clip, index) => <option key={clip.id} value={clip.id}>{index + 1}. {clip.name}</option>)}
          </select></label>
          <button className="secondary-button" disabled={busy} onClick={() => setSources(sources.filter((clip) => clip.id !== source.id))}>Remove selected recording</button>
        </div>
        {sources.length > 1 && <p>HolosTTS and ZONOS3-Mini use all recordings. OpenVoice and Pocket TTS use the first recording.</p>}
        {wave.length > 0 && <Waveform values={wave} />}
        {sourceUrl && <audio key={source.id} controls src={sourceUrl} aria-label="Selected voice recording" />}
        <div className="studio-form-grid">
          <label>Trim start (s)<input type="number" min="0" step=".1" value={source.start} onChange={(e) => updateSource({ start: Number(e.target.value) })} /></label>
          <label>Trim end (s)<input type="number" min="3" step=".1" value={source.end ?? ""} placeholder="End of recording"
            onChange={(e) => updateSource({ end: e.target.value === "" ? null : Number(e.target.value) })} /></label>
        </div>
        <label className="studio-check"><input type="checkbox" checked={source.trim_silence} onChange={(e) => updateSource({ trim_silence: e.target.checked })} />Trim leading and trailing silence</label>
        <label className="studio-check"><input type="checkbox" checked={source.denoise} onChange={(e) => updateSource({ denoise: e.target.checked })} />Reduce steady background noise</label>
        <label className="studio-check"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />I own this voice or have permission to clone it from all recordings.</label>
        <button className="primary-button" disabled={!accept || !name.trim() || busy} onClick={() => save.mutate()}>
          {save.isPending ? "Validating recordings…" : "Save voice profile"}
        </button>
      </div>}
      {language === "uk" && !tools.some((tool) => tool.ready && tool.reference_languages?.includes("uk"))
        && <p className="studio-status">Save your recording now, then install a compatible cloning model in Models &amp; Tools.</p>}
      <StudioError error={error ?? recorder.error ?? save.error ?? assign.error ?? remove.error} />
    </section>
    {workspace.profiles.length > 0 && <section className="studio-card studio-history">
      <h2>Voice profiles</h2>
      {workspace.profiles.map((profile) => <article key={profile.id}>
        <div><strong>{profile.name}</strong><p>{languageName[profile.language]} · {(profile.reference_duration_seconds ?? profile.quality.duration_seconds).toFixed(1)} s
          {profile.reference_count && profile.reference_count > 1 ? ` · ${profile.reference_count} recordings` : ""}
          {" · "}{profile.assigned ? "Assigned to project" : "Available to assign"}</p></div>
        <Waveform values={profile.waveform} />
        <div className="studio-actions">
          <ProfilePreview profile={profile.id} multiple={(profile.reference_count ?? 1) > 1} />
          <button className="secondary-button" disabled={assign.isPending} onClick={() => assign.mutate({ id: profile.id, assigned: !profile.assigned })}>
            {profile.assigned ? "Unassign" : "Assign to project"}
          </button>
          <button className="danger-button" disabled={remove.isPending} onClick={() => {
            if (window.confirm(`Delete the saved voice profile “${profile.name}” and its recordings?`)) remove.mutate(profile.id);
          }}>Delete</button>
        </div>
        <FileExports asset={{ id: profile.id, kind: "audio", name: profile.name }} reference />
      </article>)}
    </section>}
    <SpeechEditor key={choice ?? "voice-cloning"} project={project} workspace={workspace} tools={tools} cloning profileId={choice} onVisualize={onVisualize} />
  </>;
}
