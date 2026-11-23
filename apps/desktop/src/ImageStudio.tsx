import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownToLine, Clapperboard, ImagePlus, Sparkles, Star } from "lucide-react";
import { AssetPreview, ImportMedia, MediaJobs } from "./MediaParts";
import { pending, projectStudioKey, StudioError } from "./StudioParts";
import { media, type ImageCatalog, type ImageDetails, type ImageInput, type ImageMode, type ImageModel, type Profile } from "./services/media";
import { studio, type Asset, type Workspace } from "./services/studio";

const catalogKey = ["image-catalog"];
const profileKey = (project: string) => ["image-profiles", project];
const modeLabels: Record<ImageMode, string> = {
  text: "Text to image", variation: "Image to image", inpaint: "Inpaint with mask", upscale: "Upscale 2× (Lanczos)",
};
const initial: ImageInput = {
  model_id: "sd15", mode: "text", device: "auto", width: 512, height: 512, steps: 24, guidance: 7, seed: null,
  strength: .65, batch: 1, prompt: "", negative: "", reference_id: null, mask_id: null,
  adapter_image_id: null, adapter_scale: .65, control_image_id: null, control_scale: .8,
  character_id: null, environment_id: null, style_id: null, preset_style: null,
};

function working(catalog?: ImageCatalog) {
  return catalog?.install?.state === "downloading" || catalog?.install?.state === "validating";
}

function useCatalog(desktop = true) {
  return useQuery({ queryKey: catalogKey, queryFn: media.catalog, enabled: desktop,
    refetchInterval: (query) => working(query.state.data) ? 900 : false });
}

function ModelCard({ model, catalog, busy, update }: {
  model: ImageModel; catalog: ImageCatalog; busy: boolean; update: (action: string, consent?: boolean) => void;
}) {
  const [consent, setConsent] = useState(false);
  const task = catalog.install?.model_id === model.id ? catalog.install : undefined;
  const readyRuntime = model.provider === "metal" ? model.available : catalog.runtime_ready;
  const repair = model.provider === "metal" && model.installed && task?.state === "failed";
  return <article className="studio-tool-card">
    <div className="studio-tool-title"><Sparkles size={16} /><h3>{model.name}</h3>
      <span>{model.available === false ? "Unavailable" : model.installed ? model.enabled ? "Installed" : "Disabled" : "Available"}</span>
    </div>
    <p>{model.provider === "local_checkpoint" ? "Local checkpoint" : `Pinned revision ${model.version.slice(0, 8)}`}
      {model.estimated_bytes ? ` · ${(model.estimated_bytes / 1e9).toFixed(2)} GB` : ""}</p>
    <p>{[...model.modes.map((mode) => modeLabels[mode]), ...(model.lora ? ["LoRA"] : []),
      ...(model.ip_adapter ? ["IP-Adapter"] : []), ...(model.control_guide ? ["ControlNet"] : [])].join(" · ")}</p>
    <p>{model.license_url ? <a href={model.license_url} target="_blank" rel="noreferrer">{model.license}</a> : model.license}</p>
    {model.memory_bytes && <p>Memory planning estimate · {(model.memory_bytes / 1024**3).toFixed(0)} GiB</p>}
    {model.provider === "metal" && <p>Apple Silicon · macOS 26 or later · Metal</p>}
    {model.health && <p>{model.health}</p>}
    {task && <p role="status">{task.message}</p>}
    {task && working(catalog) && <div className="studio-actions">
      <progress max={1} value={task.progress ?? undefined} aria-label="Model operation progress" />
      <button className="secondary-button" onClick={() => update("cancel")}>Cancel</button>
    </div>}
    {model.available !== false && (!model.installed || repair) && !working(catalog) && <div className="studio-actions">
      <label className="studio-check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> Review and accept model terms</label>
      <button className="primary-button" disabled={busy || !consent} onClick={() => update("install", consent)}>
        {repair ? "Repair model" : "Download model"}
      </button>
    </div>}
    {model.available !== false && model.installed && <div className="studio-actions">
      <button className="secondary-button" disabled={busy || working(catalog) || !readyRuntime} onClick={() => update("test")}>Validate</button>
      <button className="secondary-button" disabled={busy} onClick={() => update(model.enabled ? "disable" : "enable")}>
        {model.enabled ? "Disable" : "Enable"}</button>
      {model.provider === "diffusers" && <button className="danger-button" disabled={busy || working(catalog)} onClick={() => {
        if (window.confirm(`Remove ${model.name} from this device?`)) update("remove");
      }}>Remove</button>}
    </div>}
  </article>;
}

export function NativeImageTools({ desktop }: { desktop: boolean }) {
  const query = useCatalog(desktop);
  const cache = useQueryClient();
  const change = useMutation({ mutationFn: ({ id, action, consent }: { id: string; action: string; consent?: boolean }) =>
    media.model(id, action, consent), onSuccess: (catalog) => cache.setQueryData(catalogKey, catalog) });
  const catalog = query.data;
  const runtimeTask = catalog?.install?.model_id === "native-runtime" ? catalog.install : undefined;
  if (!desktop) return null;
  return <section className="studio-tools native-image-tools" aria-label="Native image models">
    <h2>Native image models</h2>
    <StudioError error={query.error ?? change.error} />
    {!catalog && query.isPending && <p role="status">Checking image runtime…</p>}
    {catalog && <>
      <article className="studio-tool-card">
        <div className="studio-tool-title"><h3>PyTorch and Diffusers</h3><span>{catalog.runtime_ready ? "Ready" : "Setup required"}</span></div>
        {catalog.runtime_ready ? <p>PyTorch {catalog.torch} · {catalog.device.map((item) => item.toUpperCase()).join(", ")}
          {catalog.memory_bytes ? ` · ${(catalog.memory_bytes / 1024**3).toFixed(0)} GiB system memory` : ""}</p>
          : <p>{catalog.runtime_message} {catalog.runtime_installable ? "" : "Use the Python 3.12 desktop build or configure an external image environment."}</p>}
        {runtimeTask?.message && <p role="status">{runtimeTask.message}</p>}
        <div className="studio-actions">
          {runtimeTask && working(catalog) ? <>
            <progress aria-label="Installing native image runtime" />
            <button className="secondary-button" disabled={change.isPending} onClick={() => change.mutate({ id: "native-runtime", action: "cancel" })}>Cancel</button>
          </> : <>
            {!catalog.runtime_ready && catalog.runtime_installable && <button className="primary-button" disabled={change.isPending || working(catalog)}
              onClick={() => change.mutate({ id: "native-runtime", action: "install" })}>Install native runtime</button>}
            <button className="secondary-button" disabled={change.isPending || working(catalog)}
              onClick={() => change.mutate({ id: "native-runtime", action: "test" })}>Check runtime</button>
          </>}
        </div>
      </article>
      <div className="studio-tools-grid">
        {catalog.models.map((model) => <ModelCard key={model.id} model={model} catalog={catalog} busy={change.isPending}
          update={(action, consent) => change.mutate({ id: model.id, action, consent })} />)}
      </div>
      <details className="studio-tool-card">
        <summary>Use a local checkpoint</summary>
        <p>Place a safetensors Diffusers pipeline in a <code>local-*</code> folder under <code>{catalog.directory}</code>. Add <code>studio-model.json</code>
          { " " }with the model name, family (<code>sd15</code> or <code>sdxl</code>), and license.
          For a single checkpoint file, add its filename under <code>checkpoint</code> and install the matching base model for its configuration.</p>
        <button className="secondary-button" onClick={() => void query.refetch()}>Refresh models</button>
      </details>
    </>}
  </section>;
}

function ImageSelect({ label, value, assets, change, empty = "Choose an image" }: {
  label: string; value: string | null; assets: Asset[]; change: (value: string | null) => void; empty?: string;
}) {
  return <label>{label}<select value={value ?? ""} onChange={(e) => change(e.target.value || null)}>
    <option value="">{empty}</option>
    {value && !assets.some((asset) => asset.id === value) && <option value={value}>Missing image</option>}
    {assets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
  </select></label>;
}

export function ImageStudio({ project, workspace, openTools, onAddVideo, initialPrompt }: {
  project: string; workspace: Workspace; initialPrompt?: string; openTools: () => void; onAddVideo: (id: string) => void;
}) {
  const cache = useQueryClient();
  const catalog = useCatalog();
  const profileQuery = useQuery({ queryKey: profileKey(project), queryFn: () => media.profiles(project) });
  const [draft, setDraft] = useState({ ...initial, prompt: initialPrompt ?? "" });
  const [selected, select] = useState<string>();
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [savePath, setSavePath] = useState("");
  const [styleReferenceId, setStyleReferenceId] = useState<string | null>(null);
  const models = catalog.data?.models.filter((model) => model.installed && model.enabled && model.available !== false) ?? [];
  const model = models.find((item) => item.id === draft.model_id) ?? models[0];
  const metal = model?.provider === "metal";
  const modelReady = metal ? Boolean(model.available) : Boolean(catalog.data?.runtime_ready);
  const dimensionStep = model?.dimension_step ?? 8;
  const active = { ...draft,
    device: metal ? "mps" as const : draft.device,
    steps: draft.model_id !== model?.id ? model?.default_steps ?? draft.steps : draft.steps,
    negative: model?.negative === false ? "" : draft.negative,
    guidance: model?.guidance === false ? 1 : draft.guidance,
    batch: Math.min(draft.batch, model?.max_batch ?? 4), model_id: model?.id ?? draft.model_id,
    reference_id: draft.mode === "text" ? null : draft.reference_id,
    mask_id: draft.mode === "inpaint" ? draft.mask_id : null,
    adapter_image_id: draft.mode === "text" && model?.ip_adapter ? draft.adapter_image_id : null,
    control_image_id: draft.mode === "text" && model?.control_guide ? draft.control_image_id : null };
  const upscale = draft.mode === "upscale";
  const modes = model?.modes ?? ["text"];
  const assets = workspace.assets.filter((asset) => asset.kind === "image");
  const focused = assets.find((asset) => asset.id === selected);
  const jobs = workspace.jobs.filter((job) => job.kind === "image");
  const profiles = profileQuery.data ?? [];
  const currentStyle = profiles.find((profile) => profile.kind === "style" && profile.id === draft.style_id);
  const styleVersion = draft.profile_versions?.style;
  const useSavedVersion = Boolean(currentStyle && styleVersion && currentStyle.version !== styleVersion);
  const styleHistory = useQuery({ queryKey: ["image-profile-history", project, draft.style_id],
    queryFn: () => media.profileHistory(project, draft.style_id!), enabled: useSavedVersion });
  const selectedStyle = useSavedVersion ? styleHistory.data?.find((profile) => profile.version === styleVersion) : currentStyle;
  const styleImages = assets.filter((asset) => selectedStyle?.reference_ids.includes(asset.id));
  const styleReference = styleImages.find((asset) => asset.id === styleReferenceId)
    ?? styleImages.find((asset) => asset.id === selectedStyle?.approved_id)
    ?? styleImages.find((asset) => asset.id === selectedStyle?.reference_ids[0])
    ?? styleImages[0];
  const gallery = assets.filter((asset) => (filter === "all" || (filter === "favorites" ? asset.favorite : asset.source_type === filter))
    && [asset.name, asset.collection, ...(asset.tags ?? [])].some((part) => part?.toLowerCase().includes(search.trim().toLowerCase())));
  const refresh = () => cache.invalidateQueries({ queryKey: projectStudioKey(project) });
  const generate = useMutation({ mutationFn: (input: ImageInput) => media.generate(project, input),
    onSuccess: (job) => { select(job.id); return refresh(); } });
  const control = useMutation({ mutationFn: ({ id, stop }: { id: string; stop: boolean }) =>
    stop ? studio.cancel(project, id) : media.retry(project, id), onSuccess: () => refresh() });
  const save = useMutation({ mutationFn: (asset: Asset) => studio.download(project, asset.id, "original", asset.name), onSuccess: setSavePath });
  const details = useMutation({ mutationFn: ({ id, patch }: { id: string; patch: ImageDetails }) => media.updateImage(project, id, patch),
    onSuccess: () => refresh() });

  function patch(value: Partial<ImageInput>) {
    setDraft((before) => ({ ...before, ...value }));
  }
  function restore(asset: Asset) {
    if (!asset.generation) return;
    setDraft({ ...initial, ...asset.generation });
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    generate.mutate(upscale ? { ...initial, mode: "upscale", reference_id: draft.reference_id } : active);
  }
  function selectProfile(kind: Profile["kind"], value: string | null) {
    const profile = profiles.find((item) => item.id === value && item.kind === kind);
    const field = kind === "character" ? "character_id" : kind === "environment" ? "environment_id" : "style_id";
    const profileVersions = { ...draft.profile_versions };
    delete profileVersions[kind];
    const metadata: Partial<ImageInput> = { [field]: value, profile_versions: profileVersions };
    if (kind === "character" && profile?.approved_id) {
      if (!draft.reference_id && draft.mode !== "text") metadata.reference_id = profile.approved_id;
      if (draft.mode === "text" && model?.ip_adapter && !draft.control_image_id) metadata.adapter_image_id = profile.approved_id;
    }
    if (kind === "style") {
      setStyleReferenceId(null);
      metadata.preset_style = null;
    }
    patch(metadata);
  }
  const ready = upscale || Boolean(model && modelReady && modes.includes(draft.mode));
  return <div className="media-editor">
    <StudioError error={catalog.error ?? profileQuery.error ?? styleHistory.error ?? generate.error ?? control.error ?? save.error ?? details.error} />
    <div className="image-edit-grid">
      <form className="studio-card image-form" onSubmit={submit}>
        <div className="media-heading"><h2>Generate an image</h2><button className="text-button" type="button" onClick={openTools}>Models &amp; Tools</button></div>
        {!metal && !catalog.data?.runtime_ready && <p>Stable Diffusion needs the Python image runtime.</p>}
        {!upscale && model?.memory_bytes && catalog.data?.memory_bytes && model.memory_bytes > catalog.data.memory_bytes &&
          <p>This model’s estimated memory use exceeds system memory. Select a smaller checkpoint or image size.</p>}
        {catalog.data && !models.length && <p>Install and enable an image checkpoint in Models &amp; Tools.</p>}
        <div className="media-fields">
          <label>Task<select value={draft.mode} onChange={(e) => patch({ mode: e.target.value as ImageMode })}>
            {[...new Set<ImageMode>([...(modes as ImageMode[]), "upscale"])].map((mode) =>
              <option key={mode} value={mode}>{modeLabels[mode]}</option>)}
          </select></label>
          {!upscale && <>
            <label>Model<select value={model?.id ?? ""} onChange={(e) => {
              const chosen = models.find((item) => item.id === e.target.value);
              if (chosen) patch({ model_id: chosen.id, width: chosen.recommended_size, height: chosen.recommended_size,
                steps: chosen.default_steps ?? (metal ? initial.steps : draft.steps),
                mode: chosen.modes.includes(draft.mode) ? draft.mode : "text" });
            }}>
              {!models.length && <option value="">No installed model</option>}
              {models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
            {metal ? <p>Runs locally on Metal</p> : <label>Device<select value={draft.device} onChange={(e) => patch({ device: e.target.value as ImageInput["device"] })}>
              <option value="auto">Automatic</option>
              {(catalog.data?.device ?? []).map((device) => <option key={device} value={device}>{device.toUpperCase()}</option>)}
            </select></label>}
          </>}
        </div>
        {draft.mode !== "text" && <ImageSelect label="Reference image" value={draft.reference_id} assets={assets}
          change={(reference_id) => patch({ reference_id })} />}
        {metal && draft.mode === "variation" && <p>Describe what you want to change in the reference image.</p>}
        {draft.mode === "inpaint" && <>
          <ImageSelect label="Mask image" value={draft.mask_id} assets={assets} change={(mask_id) => patch({ mask_id })} />
          <p>Use a mask aligned with the reference. White regions are regenerated, and black regions are retained.</p>
        </>}
        {!upscale && <>
          {draft.mode === "text" && model?.ip_adapter && <div className="media-fields">
            <ImageSelect label="IP-Adapter reference" value={draft.adapter_image_id} assets={assets}
              change={(adapter_image_id) => patch({ adapter_image_id, control_image_id: null })} empty="No appearance guide" />
            {draft.adapter_image_id && <ImageNumber label="Appearance strength" value={draft.adapter_scale} min={0} max={1} step={.05}
              change={(adapter_scale) => patch({ adapter_scale })} />}
          </div>}
          {draft.mode === "text" && model?.control_guide && <div className="media-fields">
            <ImageSelect label={model.control_guide === "other" ? "ControlNet guide" : `ControlNet ${model.control_guide} guide`}
              value={draft.control_image_id} assets={assets}
              change={(control_image_id) => patch({ control_image_id, adapter_image_id: null })} empty="No ControlNet guide" />
            {draft.control_image_id && <ImageNumber label="Control strength" value={draft.control_scale} min={0} max={2} step={.05}
              change={(control_scale) => patch({ control_scale })} />}
          </div>}
          <label>Prompt<textarea required rows={4} maxLength={8000} value={draft.prompt} onChange={(e) => patch({ prompt: e.target.value })} /></label>
          {model?.negative && <label>Negative prompt<textarea rows={2} maxLength={8000} value={draft.negative}
            onChange={(e) => patch({ negative: e.target.value })} /></label>}
          <div className="media-fields">
            <label>Aspect ratio<select value={draft.width === draft.height ? "square" :
              Math.abs(draft.width / draft.height - 16/9) < .025 ? "wide" : Math.abs(draft.height / draft.width - 16/9) < .025 ? "tall" : "custom"}
              onChange={(e) => {
                const long = model?.recommended_size === 768 ? 1024 : 640;
                const short = Math.round(long * 9 / 16 / dimensionStep) * dimensionStep;
                if (e.target.value === "square") patch({ width: model?.recommended_size ?? 512, height: model?.recommended_size ?? 512 });
                if (e.target.value === "wide") patch({ width: long, height: short });
                if (e.target.value === "tall") patch({ width: short, height: long });
              }}><option value="square">1:1 · Square</option><option value="wide">16:9 · Landscape</option>
              <option value="tall">9:16 · Portrait</option><option value="custom">Custom</option></select></label>
            <ImageNumber label="Width" value={draft.width} min={256} max={1536} step={dimensionStep} change={(width) => patch({ width })} />
            <ImageNumber label="Height" value={draft.height} min={256} max={1536} step={dimensionStep} change={(height) => patch({ height })} />
          </div>
          <div className="media-fields">
            <ImageNumber label="Steps" value={active.steps} min={1} max={100}
              change={(steps) => patch({ steps, model_id: active.model_id })} />
            {model?.guidance !== false && <ImageNumber label="Guidance" value={draft.guidance} min={0} max={25} step={.1} change={(guidance) => patch({ guidance })} />}
            {model?.max_batch !== 1 && <ImageNumber label="Batch size" value={draft.batch} min={1} max={4} change={(batch) => patch({ batch })} />}
            {draft.mode !== "text" && model?.strength !== false && <ImageNumber label="Image strength" value={draft.strength} min={.01} max={1} step={.01}
              change={(strength) => patch({ strength })} />}
          </div>
          <div className="media-fields">
            <label className="media-check"><input type="checkbox" checked={draft.seed === null}
              onChange={(e) => patch({ seed: e.target.checked ? null : 1 })} /> Random seed</label>
            {draft.seed !== null && <ImageNumber label="Fixed seed" value={draft.seed} min={0} max={Number.MAX_SAFE_INTEGER - 4}
              change={(seed) => patch({ seed })} />}
          </div>
          <div className="media-fields">
            {(["character", "environment", "style"] as const).map((kind) => <label key={kind}>
              {kind[0].toUpperCase() + kind.slice(1)}
              <select value={kind === "style" ? draft.style_id ?? (draft.preset_style ? `preset:${draft.preset_style}` : "") :
                kind === "character" ? draft.character_id ?? "" : draft.environment_id ?? ""}
                onChange={(e) => e.target.value.startsWith("preset:") ? patch({ preset_style: e.target.value.slice(7), style_id: null, profile_versions: { ...draft.profile_versions, style: undefined } })
                  : selectProfile(kind, e.target.value || null)}>
                <option value="">None</option>
                {kind === "style" && Object.keys(catalog.data?.styles ?? {}).map((style) => <option key={style} value={`preset:${style}`}>{style}</option>)}
                {profiles.filter((profile) => profile.kind === kind).map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
              </select>
            </label>)}
          </div>
          {selectedStyle && <div className="media-style-reference">
            {selectedStyle.description.trim() && <p>Style description: {selectedStyle.description}</p>}
            {styleReference && <>
              <p>{draft.mode === "variation" && draft.reference_id === styleReference.id
                ? "This style image is the starting frame for generation."
                : draft.mode === "text" && model?.ip_adapter && draft.adapter_image_id === styleReference.id
                  ? "This style image is active as an appearance guide."
                  : "Select a saved style image to use as the starting frame."}</p>
              <div className="media-fields">
                <ImageSelect label="Saved style image" value={styleReference.id} assets={styleImages} change={setStyleReferenceId} />
                {model?.modes.includes("variation") && <button type="button" className="secondary-button"
                  onClick={() => patch({ mode: "variation", reference_id: styleReference.id,
                    strength: draft.mode === "variation" ? draft.strength : .2 })}>Use as image reference</button>}
                {draft.mode === "text" && model?.ip_adapter && <button type="button" className="secondary-button"
                  onClick={() => patch({ adapter_image_id: styleReference.id, control_image_id: null })}>Use as appearance guide</button>}
              </div>
            </>}
          </div>}
        </>}
        <button className="primary-button" type="submit" disabled={!ready || generate.isPending ||
          (!upscale && !draft.prompt.trim()) || (draft.mode !== "text" && !draft.reference_id) || (draft.mode === "inpaint" && !draft.mask_id)}>
          <Sparkles size={16} /> {upscale ? "Upscale image" : "Generate image"}</button>
      </form>
      <section className="studio-card image-selected">
        <h2>Preview</h2>
        <div className="image-preview">
          {focused ? <AssetPreview project={project} asset={focused} /> :
            <div className="image-empty"><ImagePlus size={28} /><span>{selected && jobs.some((job) => job.id === selected && pending(job))
              ? "Generating selected image…" : "Select an image from the library."}</span></div>}
        </div>
        {focused && <>
          <p>{focused.width} × {focused.height}{focused.seed !== undefined && ` · Seed ${focused.seed}`}
            {focused.generation && ` · ${modeLabels[focused.generation.mode]}`}</p>
          {focused.license && <p>{focused.license}</p>}
          <div className="media-actions">
            <button className="primary-button" onClick={() => onAddVideo(focused.id)}><Clapperboard size={15} /> Add to video</button>
            <button className="secondary-button" disabled={save.isPending} onClick={() => save.mutate(focused)}>
              <ArrowDownToLine size={15} /> Download</button>
            {focused.generation && <>
              <button className="secondary-button" onClick={() => restore(focused)}>Reuse settings</button>
              {focused.generation.mode !== "upscale" && models.some((item) => item.id === focused.generation?.model_id && (item.provider === "metal" || catalog.data?.runtime_ready)) &&
                <button className="secondary-button" disabled={generate.isPending}
                  onClick={() => generate.mutate({ ...initial, ...focused.generation })}>Regenerate</button>}
            </>}
            {model?.modes.includes("variation") && <button className="secondary-button" onClick={() => patch({ mode: "variation", reference_id: focused.id,
              prompt: focused.generation?.prompt ?? draft.prompt, seed: null })}>Create variation</button>}
          </div>
          <ImageMetadata key={focused.id} asset={focused} save={(value) => details.mutate({ id: focused.id, patch: value })} />
        </>}
        {savePath && <p role="status">Saved to {savePath}</p>}
      </section>
    </div>
    <section className="studio-card">
      <div className="media-heading"><h2>Project images</h2><ImportMedia project={project} kind="image" onImported={([asset]) => select(asset.id)} /></div>
      <div className="media-fields">
        <label>Search images<input type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
        <label>Filter<select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All images</option><option value="generated">Generated</option><option value="upload">Uploaded</option>
          <option value="favorites">Favorites</option>
        </select></label>
      </div>
      {gallery.length ? <div className="image-gallery">
        {gallery.map((asset) => <article key={asset.id} className={asset.id === selected ? "selected" : undefined}>
          <div className="image-tile" role="button" tabIndex={0} aria-label={asset.name} aria-pressed={asset.id === selected}
            onClick={() => select(asset.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(asset.id); } }}>
            <AssetPreview project={project} asset={asset} thumbnail />
          </div>
          <div><span title={asset.name}>{asset.name}</span>
            <button className="icon-button" aria-label={asset.favorite ? "Remove favorite" : "Add favorite"}
              onClick={() => details.mutate({ id: asset.id, patch: { favorite: !asset.favorite } })}>
              <Star size={15} fill={asset.favorite ? "currentColor" : "none"} /></button>
          </div>
        </article>)}
      </div> : <p>{assets.length ? "No images match your filters." : "Import an image or generate one to start your library."}</p>}
    </section>
    <ProfileEditor project={project} profiles={profiles} assets={assets} />
    <MediaJobs jobs={jobs} onCancel={(id) => control.mutate({ id, stop: true })} onRetry={(id) => control.mutate({ id, stop: false })} />
  </div>;
}

function ImageNumber({ label, value, min, max, step = 1, change }: {
  label: string; value: number; min: number; max: number; step?: number; change: (value: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return <label>{label}<input type="number" value={text} min={min} max={max} step={step} onChange={(e) => {
    setText(e.target.value);
    if (e.target.validity.valid && e.target.value !== "") change(Number(e.target.value));
  }} onBlur={() => setText(String(value))} /></label>;
}

function ImageMetadata({ asset, save }: { asset: Asset; save: (input: ImageDetails) => void }) {
  const [name, setName] = useState(asset.name);
  const [collection, setCollection] = useState(asset.collection ?? "");
  const [tags, setTags] = useState((asset.tags ?? []).join(", "));
  return <details>
    <summary>Image details</summary>
    <div className="media-fields">
      <label>Name<input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></label>
      <label>Collection<input value={collection} maxLength={100} onChange={(e) => setCollection(e.target.value)} /></label>
      <label>Tags<input value={tags} maxLength={500} onChange={(e) => setTags(e.target.value)} placeholder="Separate tags with commas" /></label>
    </div>
    <div className="media-actions">
      <button className="secondary-button" disabled={!name.trim()} onClick={() =>
        save({ name, collection, tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean).slice(0, 20) })}>Save details</button>
      <label>Appearance<select value={asset.approval ?? "pending"} onChange={(e) => save({ approval: e.target.value as ImageDetails["approval"] })}>
        <option value="pending">Unreviewed</option><option value="approved">Approved</option><option value="rejected">Rejected</option>
      </select></label>
    </div>
  </details>;
}

function ProfileEditor({ project, profiles, assets }: { project: string; profiles: Profile[]; assets: Asset[] }) {
  const [record, setRecord] = useState<Profile>();
  const [reference, setReference] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const cache = useQueryClient();
  const history = useQuery({ queryKey: ["image-profile-history", project, record?.id],
    queryFn: () => media.profileHistory(project, record!.id), enabled: Boolean(showHistory && record?.version) });
  const mutation = useMutation({ mutationFn: (input: Profile) => {
    const { version: _version, ...profile } = input;
    return media.saveProfile(project, profile);
  }, onSuccess: (saved) => {
    setRecord(saved);
    void cache.invalidateQueries({ queryKey: profileKey(project) });
    void cache.invalidateQueries({ queryKey: ["image-profile-history", project, saved.id] });
  } });
  function patch(value: Partial<Profile>) {
    setRecord((before) => before ? { ...before, ...value } : before);
  }
  return <details className="studio-card">
    <summary>Characters, environments and styles</summary>
    <div className="media-fields">
      <label>Profile<select value={record?.version ? record.id : ""} onChange={(e) => {
        setRecord(profiles.find((profile) => profile.id === e.target.value)); setShowHistory(false);
      }}><option value="">Choose a profile</option>
        {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.kind}</option>)}
      </select></label>
      <button className="secondary-button" onClick={() => {
        setRecord({ id: crypto.randomUUID(), kind: "character", name: "", description: "", clothing: "", reference_ids: [], approved_id: null, version: 0 });
        setShowHistory(false);
      }}>New profile</button>
    </div>
    {record && <>
      <div className="media-fields">
        <label>Type<select disabled={Boolean(record.version)} value={record.kind}
          onChange={(e) => patch({ kind: e.target.value as Profile["kind"] })}>
          <option value="character">Character</option><option value="environment">Environment</option><option value="style">Visual style</option>
        </select></label>
        <label>Name<input value={record.name} maxLength={120} onChange={(e) => patch({ name: e.target.value })} /></label>
      </div>
      <label>{record.kind === "style" ? "Style description for generation" : "Description"}
        <textarea value={record.description} maxLength={1500} rows={3} onChange={(e) => patch({ description: e.target.value })} /></label>
      {record.kind === "character" && <label>Clothing and accessories<input value={record.clothing} maxLength={500}
        onChange={(e) => patch({ clothing: e.target.value })} /></label>}
      <div className="media-fields">
        <ImageSelect label="Add reference" value={reference} assets={assets.filter((asset) => !record.reference_ids.includes(asset.id))}
          change={setReference} />
        <button className="secondary-button" disabled={!reference || record.reference_ids.length >= 12} onClick={() => {
          if (reference) patch({ reference_ids: [...record.reference_ids, reference] }); setReference(null);
        }}>Add reference</button>
        <ImageSelect label="Approved appearance" value={record.approved_id}
          assets={assets.filter((asset) => record.reference_ids.includes(asset.id))} change={(approved_id) => patch({ approved_id })}
          empty="None approved" />
      </div>
      <div className="media-actions">{record.reference_ids.map((id) => <button key={id} className="secondary-button" onClick={() =>
        patch({ reference_ids: record.reference_ids.filter((value) => value !== id), approved_id: record.approved_id === id ? null : record.approved_id })}>
        Remove {assets.find((asset) => asset.id === id)?.name ?? "reference"}</button>)}</div>
      <div className="media-actions">
        <button className="primary-button" disabled={mutation.isPending || !record.name.trim()} onClick={() => mutation.mutate(record)}>Save profile</button>
        {record.version > 0 && <button className="secondary-button" onClick={() => setShowHistory(!showHistory)}>
          {showHistory ? "Hide version history" : "Version history"}</button>}
      </div>
      {showHistory && history.data?.map((revision) => <div key={revision.version} className="media-job">
        <strong>Version {revision.version}</strong><span>{revision.name}</span><small>{revision.description}</small>
        <button className="secondary-button" onClick={() => setRecord({ ...revision, version: record.version })}>Use this version</button>
      </div>)}
    </>}
    <StudioError error={mutation.error ?? history.error} />
  </details>;
}
