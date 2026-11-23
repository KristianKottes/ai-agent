import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AssetPreview, MediaJobs } from "./MediaParts";
import { pending, projectStudioKey, StudioError } from "./StudioParts";
import { media, type MorningNarrator, type MorningSpeechSettings, type MorningTopic } from "./services/media";
import { studio, type Language, type Workspace } from "./services/studio";

const spellings = (value: MorningSpeechSettings | null) => Object.entries(value?.pronunciation ?? {})
  .map(([word, pronunciation]) => `${word}=${pronunciation}`).join("\n");
export const withPauseDefaults = (value: MorningSpeechSettings): MorningSpeechSettings => ({ ...value,
  title_pause_ms: value.title_pause_ms ?? 600, block_pause_ms: value.block_pause_ms ?? 900,
  paragraph_pause_ms: value.paragraph_pause_ms ?? value.pause_ms * 2 });
const pauseFields = [
  { key: "title_pause_ms", label: "After the title (ms)" },
  { key: "block_pause_ms", label: "After a Bible or poem block (ms)" },
  { key: "paragraph_pause_ms", label: "After a paragraph (ms)" },
] as const;

function parseDictionary(text: string) {
  const entries = text.split("\n").filter((line) => line.trim()).map((line) => {
    const pair = line.split("=").map((part) => part.trim());
    if (pair.length !== 2 || pair.some((part) => !part || part.length > 100))
      throw new Error("Use one spelling=pronunciation pair per line, up to 100 characters each.");
    return pair;
  });
  if (entries.length > 50) throw new Error("Use up to 50 pronunciation entries.");
  if (new Set(entries.map(([word]) => word.toLocaleLowerCase())).size !== entries.length)
    throw new Error("Each spelling must appear only once.");
  return Object.fromEntries(entries);
}

export function MorningPronunciation({ project, workspace, language, topic, narrator, value, onChange, onValidityChange, preferencesOnly = false }: {
  project: string; workspace: Workspace; language: Language; topic?: MorningTopic; narrator?: MorningNarrator;
  value: MorningSpeechSettings | null; onChange: (value: MorningSpeechSettings) => void;
  onValidityChange: (valid: boolean) => void;
  preferencesOnly?: boolean;
}) {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ["morning-speech-settings", project], queryFn: () => media.morningSpeechSettings(project) });
  const [dictionary, setDictionary] = useState(() => spellings(value));
  const [testTexts, setTestTexts] = useState({ uk: "Ісус Христос дарує надію. Довіряй Йому сьогодні.",
    en: "Jesus Christ brings hope. We can trust Him today." });
  const [samples, setSamples] = useState<{ id: string; label: string; signature: string }[]>([]);
  const [testKind, setTestKind] = useState<"paragraph" | "poem">("paragraph");
  useEffect(() => {
    if (!value && query.data) { onChange(withPauseDefaults(query.data)); setDictionary(spellings(query.data)); }
    else if (value && pauseFields.some(({ key }) => value[key] == null)) onChange(withPauseDefaults(value));
  }, [value, query.data, onChange]);
  let dictionaryError: Error | null = null;
  try { parseDictionary(dictionary); } catch (error) { dictionaryError = error as Error; }
  const valid = Boolean(value && !dictionaryError && Number.isInteger(value.pause_ms)
    && value.pause_ms >= 0 && value.pause_ms <= 2000 && value.speed >= .75 && value.speed <= 1.5
    && pauseFields.every(({ key }) => Number.isInteger(value[key]) && Number(value[key]) >= 0 && Number(value[key]) <= 10000));
  useEffect(() => onValidityChange(valid), [valid, onValidityChange]);
  const nativePace = narrator?.model_id === "zonos3-mini";
  const speed = nativePace ? 1 : value?.speed ?? 1;
  const blocks = [
    ...(topic?.title ? [{ text: topic.title, kind: "title" as const }] : []),
    ...(topic?.passage ? [{ text: topic.passage, kind: "scripture" as const }] : []),
    ...((testKind === "paragraph" ? testTexts[language].split(/\n\s*\n/) : [testTexts[language]])
      .filter((part) => part.trim()).map((part) => ({ text: part, kind: testKind }))),
  ];
  const text = blocks.map((block) => block.text).join("\n\n");
  const signature = JSON.stringify([blocks, language, narrator?.model_id, narrator?.voice, speed, value]);
  const normalize = useMutation({ mutationFn: () => studio.normalize({ text, language, pronunciation: value?.pronunciation }) });
  useEffect(() => { normalize.reset(); }, [text, language, value?.pronunciation]);
  const save = useMutation({ mutationFn: (input: MorningSpeechSettings) => media.saveMorningSpeechSettings(project, input), onSuccess: (saved) => {
    onChange(saved); setDictionary(spellings(saved)); cache.setQueryData(["morning-speech-settings", project], saved);
  } });
  function edit(change: Partial<MorningSpeechSettings>) {
    if (value) onChange({ ...value, ...change });
    save.reset();
  }
  const preview = useMutation({
    mutationFn: async () => {
      if (!narrator || !valid || !value) throw new Error("Choose a voice and valid pronunciation settings.");
      const [job] = await studio.speech(project, [{ text, language, model_id: narrator.model_id, voice: narrator.voice,
        name: `Voice preview · ${narrator.name}`, speed, pause_ms: value.pause_ms, pronunciation: value.pronunciation,
        paragraph_pause_ms: value.paragraph_pause_ms, title_pause_ms: value.title_pause_ms,
        block_pause_ms: value.block_pause_ms, blocks, trailing_pause: true,
        format: "wav", sample_rate: 24000, normalize_audio: true }]);
      return { id: job.id, label: `${narrator.name} · ${speed.toFixed(2)}× · Title ${value.title_pause_ms} ms · Block ${value.block_pause_ms} ms · Paragraph ${value.paragraph_pause_ms} ms`, signature };
    },
    onSuccess: (sample) => {
      setSamples((current) => [sample, ...current].slice(0, 4));
      return cache.invalidateQueries({ queryKey: projectStudioKey(project) });
    },
  });
  const cancel = useMutation({ mutationFn: (id: string) => studio.cancel(project, id),
    onSuccess: () => cache.invalidateQueries({ queryKey: projectStudioKey(project) }) });
  const jobs = workspace.jobs.filter((job) => samples.some((sample) => sample.id === job.id));
  const busy = preview.isPending || jobs.some(pending);
  const dirty = value && JSON.stringify(value) !== JSON.stringify(query.data);

  return <div className="morning-pronunciation">
    <h3>Pronunciation and delivery</h3>
    <p>Bible references are expanded for speech. Corrections affect the voiceover; captions keep their original spelling.</p>
    {query.isPending && !value && <p role="status">Loading pronunciation settings…</p>}
    <StudioError error={query.error} />
    {value && <>
      <div className="studio-form-grid">
        <label>Speed · {speed.toFixed(2)}×<input type="range" min=".75" max="1.5" step=".05"
          value={speed} disabled={nativePace} onChange={(event) => edit({ speed: Number(event.target.value) })} /></label>
        <label>{nativePace ? "Section pause (ms)" : "Sentence pause (ms)"}<input type="number" min="0" max="2000" step="5"
          value={value.pause_ms} onChange={(event) => edit({ pause_ms: Number(event.target.value) })} /></label>
      </div>
      <div className="studio-form-grid">
        {pauseFields.map(({ key, label }) => <label key={key}>{label}<input type="number" min="0" max="10000" step="50"
          value={value[key] ?? ""} onChange={(event) => edit({ [key]: Number(event.target.value) })} /></label>)}
      </div>
      <p>Use blank lines to separate paragraphs. Title and Bible or poem block pauses replace the paragraph pause at that boundary.
        These values control inserted silence; the voice can also have natural pauses.</p>
      {nativePace && <p>ZONOS3-Mini uses its natural speaking pace. Speed settings apply to the other voices.</p>}
      <label>Saved pronunciation dictionary<textarea rows={4} maxLength={10100} value={dictionary}
        placeholder={language === "uk" ? "Ісус=Ісу́с\nЄрусалим=Єрусали́м" : "One spelling=pronunciation pair per line"}
        onChange={(event) => {
          setDictionary(event.target.value);
          try { edit({ pronunciation: parseDictionary(event.target.value) }); } catch { save.reset(); }
        }} /></label>
      {language === "uk" && <p>HolosTTS adds Ukrainian stress automatically and honors explicit accents such as «Ісу́с».
        Other voices may interpret stress marks differently. {preferencesOnly ? "Compare voices in the video wizard." : "Select another voice above to compare previews."}</p>}
      <StudioError error={dictionaryError} />
      <div className="illustrated-actions">
        <button type="button" className="secondary-button" disabled={!valid || !dirty || save.isPending}
          onClick={() => save.mutate(value)}>{save.isPending ? "Saving…" : "Save pronunciation defaults"}</button>
        <button type="button" className="text-button" disabled={save.isPending} onClick={async () => {
          const result = await query.refetch();
          if (result.data) { onChange(withPauseDefaults(result.data)); setDictionary(spellings(result.data)); save.reset(); }
        }}>Reload saved</button>
      </div>
      {save.isSuccess && !dirty && <p role="status">Pronunciation defaults saved for future videos in this project.</p>}
      <StudioError error={save.error} />
      {!preferencesOnly && <><label>Test passage or difficult words<textarea rows={3} maxLength={600} lang={language}
        value={testTexts[language]} onChange={(event) => setTestTexts({ ...testTexts, [language]: event.target.value })} /></label>
      <label>Test block type<select value={testKind} onChange={(event) => setTestKind(event.target.value as "paragraph" | "poem")}>
        <option value="paragraph">Paragraphs</option><option value="poem">Poem or verse block</option>
      </select></label>
      <p>The preview includes your selected title, Bible reference, and test text. Your current settings also apply to the video.</p>
      <div className="illustrated-actions">
        <button type="button" className="secondary-button" disabled={!valid || !topic || normalize.isPending}
          onClick={() => normalize.mutate()}>Preview spoken text</button>
        <button type="button" className="primary-button" disabled={!valid || !topic || !narrator || busy}
          onClick={() => preview.mutate()}>{busy ? "Preparing voice preview…" : "Generate voice preview"}</button>
      </div>
      {normalize.data && <label>Prepared speech text<textarea readOnly rows={4} lang={language} value={normalize.data.text} /></label>}
      <StudioError error={normalize.error || preview.error || cancel.error} />
      <MediaJobs jobs={jobs} className="morning-preview-jobs" onCancel={(id) => cancel.mutate(id)} />
      {samples.map((sample) => {
        const asset = workspace.assets.find((item) => item.id === sample.id);
        return asset ? <div className="morning-voice-sample" key={sample.id}>
          <p>{sample.label}{sample.signature !== signature ? " · Previous selection or settings" : ""}</p>
          <AssetPreview project={project} asset={asset} />
        </div> : null;
      })}</>}
    </>}
  </div>;
}
