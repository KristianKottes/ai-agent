import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { media, type MamaySettings } from "./services/media";
import { StudioError } from "./StudioParts";

const fields = [
  { key: "voice_rules", label: "Voice and tone", limit: 1500 },
  { key: "rhythm_rules", label: "Sentence rhythm", limit: 1500 },
  { key: "title_rules", label: "Titles and variety", limit: 1500 },
  { key: "bible_rules", label: "Bible grounding", limit: 1500 },
  { key: "structure_rules", label: "Main structure: title → Bible text → reference → main text", limit: 1500 },
  { key: "topic_rules", label: "Topic discovery rules", limit: 1500 },
  { key: "editor_rules", label: "Editing instructions", limit: 1500 },
  { key: "examples_uk", label: "Ukrainian writing examples", limit: 3000 },
  { key: "examples_en", label: "English writing examples", limit: 3000 },
] as const;

export function MamayConfiguration({ project, onClose }: { project: string; onClose?: () => void }) {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ["mamay-settings", project], queryFn: () => media.mamaySettings(project) });
  const [draft, setDraft] = useState<MamaySettings | null>(null);
  useEffect(() => {
    if (query.data && draft === null) setDraft(query.data.settings);
  }, [query.data, draft]);
  const save = useMutation({
    mutationFn: (input: MamaySettings) => media.saveMamaySettings(project, input),
    onSuccess: (saved) => {
      setDraft(saved);
      cache.setQueryData(["mamay-settings", project], (current: typeof query.data) =>
        current ? { ...current, settings: saved } : undefined);
    },
  });
  function edit(change: Partial<MamaySettings>) {
    setDraft((current) => current ? { ...current, ...change } : null);
    save.reset();
  }
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(query.data?.settings);
  const characterCount = draft ? fields.reduce((count, field) => count + draft[field.key].length, 0) : 0;

  return <section className="script-editor-card mamay-configuration" aria-labelledby="mamay-configuration-title">
    <div className="script-editor-head">
      <div><h3 id="mamay-configuration-title">MamayLM configuration</h3>
        <span>Writing rules and examples for Morning Inspiration</span></div>
      {onClose && <button type="button" className="text-button" onClick={onClose}>Close</button>}
    </div>
    <p>Saved rules apply to new topic generations and newly queued videos in this project. Examples guide the selected language.</p>
    {query.isPending && <div className="loading-row" role="status"><span className="spinner" /> Loading writing settings…</div>}
    <StudioError error={query.error} />
    {draft && <form onSubmit={(event) => {
      event.preventDefault();
      if (characterCount <= 10000) save.mutate(draft);
    }}>
      <fieldset disabled={save.isPending}>
        <label className="mamay-editor-toggle">
          <input type="checkbox" checked={draft.editor_enabled}
            onChange={(event) => edit({ editor_enabled: event.target.checked })} />
          Edit narration after drafting
        </label>
        <div className="mamay-rule-fields">
          {fields.map(({ key, label, limit }) => <label key={key} className="mamay-rule">
            <span>{label}</span>
            <textarea rows={key.startsWith("examples") ? 5 : 4} value={draft[key]} maxLength={limit}
              lang={key === "examples_uk" ? "uk" : key === "examples_en" ? "en" : undefined}
              onChange={(event) => edit({ [key]: event.target.value })} />
          </label>)}
        </div>
        <div className="script-editor-actions">
          <span>{characterCount.toLocaleString()} / 10,000 characters</span>
          <button type="button" className="secondary-button" disabled={!query.data} onClick={() => {
            if (query.data) edit({ ...query.data.defaults, revision: draft.revision });
          }}>Use defaults</button>
          <button type="button" className="secondary-button" onClick={async () => {
            const result = await query.refetch();
            if (result.data) { setDraft(result.data.settings); save.reset(); }
          }}>Reload saved</button>
          <button type="submit" className="primary-button" disabled={!dirty || characterCount > 10000}>
            {save.isPending ? "Saving…" : "Save writing settings"}
          </button>
        </div>
      </fieldset>
    </form>}
    {save.isSuccess && !dirty && <p role="status" className="model-ready-note">Writing settings saved.</p>}
    <StudioError error={save.error} />
  </section>;
}
