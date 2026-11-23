# AI Blogger Studio

AI Blogger Studio is a local-first desktop application for automated video production. The writing workflow covers topics, scripts, and director plans. On Apple Silicon, the script editor can also generate offline narration. See [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md) for verification and open work.

## Image Studio and Video Creator

Use **Image Studio** to generate or edit images locally with PyTorch and Diffusers. **Models & Tools → Native image models** installs the Python runtime and your chosen checkpoint. Use **Video Creator** to turn project images into MP4 or MOV with transitions, narration, music and captions. Video Creator also works with uploaded media before an image model is installed. [Workspace guide and developer notes](MEDIA_STUDIOS.md)

## Morning Inspiration videos

In the desktop app, open **Morning Inspiration** and select a project. **Generate 5 topics & texts** adds a persistent background job that discovers Bible-based topics in Ukrainian or English and writes full narration for each one. Each completed draft is saved immediately. You can navigate away while it runs; progress, cancellation, and retry are in **Jobs**. After a restart, unfinished jobs resume without duplicating saved drafts.

The project topic library searches titles, Bible passages, and full text. Filter **All topics**, **Unused**, or **Used**. **Preview & edit** shows the complete narration and lets you change the title, Bible reference, reflection angle, narration, captions, block types, and illustration descriptions. Save with **Save topic & text**. You can mark a topic used or unused and delete it with **Undo delete**. Successful video generation automatically marks the topic used.

Select a saved topic, choose a narrator and visual style, then caption style, resolution, and scene count. Assigned and authorized voice clones appear when their model supports the language. Use **Back** or the step buttons to review choices, then **Generate video**. The queue stores a copy of the saved text; later edits do not alter videos already queued. Rendering uses that narration without another writing or editing pass. Completed videos include a preview, transcript, thumbnail, and **Save to Downloads**.

Open **Preferences → Generation defaults**, choose a project under **Project settings for**, and save its language, word range, narrator, visual style, captions, resolution, duration, and scene count. Voice delivery, pauses, pronunciation, and MamayLM rules/examples are also saved separately for each project. Projects without saved settings inherit your existing application defaults until you save each section. Opening settings from the wizard selects that project automatically. The **Voice & pauses** tab saves pronunciation, speed, and sentence/block pauses; **Writing rules & examples** configures MamayLM. Each section has its own save button. Existing drafts keep their choices; use **Use saved defaults** in the wizard to reload them. Queued videos keep their original settings.

Video duration is **Auto** and follows the saved narration and configured pauses. Before generating topics, set text length to **Word range**, for example **300–400 words**, in the project's **Generation defaults**. The inclusive range covers the spoken title, Bible text, and reflection; MamayLM corrects text outside it, including after editing. Minimum and maximum must be between 50 and 1000. Saved text can then be edited manually. With automatic text length, a target duration in project defaults guides the initial draft's approximate length. A different scene count in the video wizard rearranges the saved words without rewriting them; complete Bible and poem blocks stay together.

In the **Voice** step, use **Pronunciation and delivery** to set speed and sentence pauses, and enter corrections such as `Ісус=Ісу́с`, one per line. **Save pronunciation defaults** retains these settings for future videos in the current project; the current settings apply to this video even before saving. Each queued video keeps its own snapshot. ZONOS3-Mini uses its native speaking pace. HolosTTS adds Ukrainian stress automatically and preserves explicit accent marks when you select an assigned Ukrainian voice profile.

**Preview spoken text** shows expanded Ukrainian Bible references and pronunciation corrections. **Generate voice preview** reads the selected title, Bible reference, and editable test text using the selected voice. Switch voices to compare up to four recent samples before generating the video. Previews use the speech queue and appear here when ready. Corrections affect speech while captions retain their original spelling. Bible references such as `Матвія 6:34` are read as chapter and verse; ordinary clock times remain times.

Set separate pauses **After the title**, **After a Bible or poem block**, and **After a paragraph** in the Voice step, up to 10,000 ms each. These values replace the sentence pause at those boundaries. Paragraphs are separated by blank lines; Bible and poem blocks are marked during writing and retain their boundaries during editing. The preview's **Test block type** lets you hear paragraph or poem pacing. Configured block pauses are preserved in the video instead of being compressed to meet a duration target. The automatic video length includes this silence, without adding another scene hold. Save pronunciation defaults to reuse the pauses in future videos.

Open **Generation defaults → Writing rules & examples** to edit voice, sentence rhythm, Bible grounding, main structure, topic rules, and editing instructions. The default structure is **title → Bible text → Bible reference → main text**: the title, Bible passage, and book/chapter/verse reference are saved separately. The preview shows these as distinct blocks, and the voiceover reads the title, then the passage, then its reference, followed by the reflection. The reference always follows the passage. Edit **Topic title**, **Bible text**, and **Bible reference (after the passage)** independently in the saved topic editor. The title is included in the generated voiceover and transcript. The editor is instructed to preserve the spoken title, excerpt, and section order. Add Ukrainian and English examples separately. The editing pass is enabled by default and refines narration after drafting. Saved writing rules apply to new topic and text generations in the selected project. Existing saved drafts retain their text until you edit them. Each video retains its writing settings when retried.

Edit **Titles and variety** in MamayLM configuration to control titles for both topic discovery and scripts. Defaults favor natural titles of 2–5 words while allowing questions, complete sentences, and other lengths. Topic batches should mix forms instead of repeating a phrase followed by a colon and subtitle.

Choose **Biblical cinematic** for detailed biblical scenes and symbols, vivid colors, and golden sunrise light. Every new Morning Inspiration video includes a generated 1280 × 720 thumbnail with its title in the selected language. Thumbnail prompts follow the scene's subject, allowing landscapes, objects, animals, or symbols without adding a person. When people are present, the prompt calls for relaxed expressions and gently closed mouths. Preview and save the PNG under **Saved videos**.

Generation requires Apple Silicon with Metal access, macOS 26 or later, and FFmpeg with libx264 and FFprobe. Install **FLUX.2 Klein 4B** and a voice such as **Piper Lada** or **Kokoro** in **Models & Tools**. Install the 7.3 GB MamayLM writing model from **Morning Inspiration** or with the command below. [MamayLM model card and license](https://huggingface.co/INSAIT-Institute/MamayLM-Gemma-3-12B-IT-v2.0)

```sh
backend/.venv/bin/python -B -m backend.scenes.illustrated_video --install-writing-model
```

Give the pipeline a topic and an output directory.

```sh
backend/.venv/bin/python -B -m backend.scenes.illustrated_video \
  'Ранкова молитва: як віддати Богові перші хвилини дня' \
  --seconds 60 --output outputs/ranok-z-molytvy
```

The pipeline writes the script in Ukrainian, voices it with Piper Lada, and generates a pencil-and-watercolor illustration for each scene with FLUX. It reveals handwritten phrases over the narration and exports `video.mp4` in 1280 × 720 at 30 fps. `--seconds` sets the script's word budget and guides the timing after narration is generated. The pipeline can adjust the tempo by −8% to +12% while preserving pitch. When Piper Lada narration would still run long, it can omit whole middle sentences and keeps each scene's opening and ending. The export may still run longer or shorter than the target.

`--model` accepts another local chat-capable GGUF, and `--font` accepts a font file with Ukrainian glyphs. To use the optional ZONOS3-Mini narration engine, install its model in **Models & Tools**, set `AI_BLOGGER_ZONOS_BIN_DIR` to the bundled directory containing `zonos3-cli` and `spk-encoder-cli`, and pass `--voice zonos`. Run with `--help` for all options.

The output directory holds the draft script, the words spoken in the export in `transcript.txt`, per-scene images, narration, and render checkpoints. If interrupted, rerun the same command to finish the remaining scenes. Use a new output directory for a different topic or settings.

## Files to keep in GitHub

The Git repository root is this project directory, containing `apps/`, `backend/`, and `tests/`.

| Commit | Keep local or distribute separately |
| --- | --- |
| React/TypeScript UI, Rust/Tauri host, and Python backend source | `node_modules`, Python environments, Cargo/Rust toolchains, and package caches |
| `package.json`, `package-lock.json`, `Cargo.toml`, `Cargo.lock`, and pinned Python requirements | `dist`, `target`, `.macos-build`, generated Tauri schemas, and compiled runtime binaries |
| Build/setup scripts, Tauri configuration, capabilities, and app icons | Packaged `.app` bundles and installers; use release artifacts for distribution |
| Model catalogs and pinned download manifests | Downloaded model weights and checkpoints |
| Tests, the fictional portrait fixture, bundled Noto Sans font, and their licenses | Generated media in `outputs`, local databases, logs, and saved project preferences |
| Project documentation, `.gitignore`, and `.gitattributes` | `.env` files, private keys, signing credentials, service-account files, and OS/editor metadata |

The root and desktop `.gitignore` files apply these exclusions. Keep required source images and fonts in Git; there is no blanket media-file exclusion that would hide test fixtures or app assets. Environment examples ending in `.example` or `.template` are allowed and should contain placeholders only. Built-in generation defaults live in the source; personal settings saved by the app stay local.

Review the files selected for the first commit from the project root:

```sh
git status --short
git ls-files --others --exclude-standard
git add --dry-run .
```

## Development

Prerequisites: Node.js 20.19+ and Rust/Cargo for the Tauri host. The Tauri scripts use Cargo from your terminal or fall back to the project-local `.cargo` and `.rustup` installation when present. Python dependencies are isolated in `backend/.venv`.

```sh
python3 -m venv backend/.venv
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  export CMAKE_ARGS='-DGGML_METAL=on -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_APPLE_SILICON_PROCESSOR=arm64'
  export CMAKE_BUILD_PARALLEL_LEVEL=4
fi
backend/.venv/bin/python -m pip install --cache-dir backend/.pip-cache -r backend/requirements-dev.txt
cd apps/desktop
npm install --cache ../../.npm-cache
npm run tauri:dev
```

On Apple Silicon, `tauri:dev` uses the staged Python 3.12 runtime after a macOS app build. HolosTTS and OpenVoice need Python 3.12. You can set `AI_BLOGGER_PYTHON` to use another interpreter with the backend dependencies installed.

The Tauri host starts the authenticated FastAPI sidecar and stores its SQLite data and downloaded model weights in the operating system's app-data directory. The Discover Topics screen requires a separate explicit license acceptance before downloading the SHA-256-pinned Qwen2.5 0.5B Q4_K_M weights (about 491 MB / 469 MiB). Topic, script, and director results stay local. Cross-platform runtime and model verification remains open.

Open a saved script in Discover Topics and use **Voice & narration** to install Kokoro (103 MB), preview an English voice, or generate a WAV. WAVs are stored under `voice/narration` in the app data directory. The panel shows the latest render for the current saved text and its file path. Refresh the backend requirements above if you set up the workspace before Phase 5.

In the same script editor, open **Avatar Library** to preview an illustrated presenter, adjust its appearance, and save a reusable preset. Generate narration to choose the voice for a clip, then render up to 30 seconds to MP4. The Apple Silicon development workflow uses FFmpeg with `libx264` and FFprobe. Clips are stored under `avatar` in app data.

The experimental **Avatar Library → Portrait** mode uses MuseTalk to lip-sync a still photo or a video from saved narration. In an Apple Silicon development checkout, run `sh backend/avatar/portrait_runtime/setup.sh` from the project root to prepare its separate Python environment. Use the app to approve and download the 1.51 GB model, import a JPEG, PNG, MP4, or MOV, then save the face framing. For a photo, **Add subtle head motion** applies a gentle 2D tilt and drift, with blinks when both eyes are detected. For video, choose a continuous shot with a front-facing subject and room around the face box. The first 10 seconds preserve movement from the source and play the saved narration. Portraits, framing, and clips are stored locally.

See [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md), [COMPATIBILITY_MATRIX.md](COMPATIBILITY_MATRIX.md), and [LICENSE_AUDIT.md](LICENSE_AUDIT.md) before making platform or component compatibility claims.

In **Discover Topics**, open a saved script with narration and find **Video export** below Avatar Library. Import PNG stills or MP4 clips up to 32 MiB each, or add a saved presenter for the opening. Set scene lengths, choose cuts or fades through black, then select the output format and quality. **Export MP4** saves the verified file under the app data directory and lists it under **Saved exports** for playback. The caption option aligns English from the saved script using PocketSphinx. If alignment fails, check the script or turn off captions for that export.

Timeline edits save automatically with the script. Reopen the script to recover the scene order, lengths, transitions, format, quality, and caption setting. If you generate new narration, remove or replace any presenter clip rendered for the previous narration before exporting.

Use **Generate a scene** to enter a prompt or choose one from the scene plan. On the verified Apple Silicon setup, FLUX produces a PNG and Wan produces a three-second preview MP4. Approve any model download before it starts. Completed scenes appear in the script's saved scenes. You can cancel an active job and reuse the prompt afterward.

Import a **Background WAV** to mix under the narration. The selected track and level save with the timeline. Short tracks loop to fill the video. A captioned export includes SRT and ASS files alongside the MP4. Use **Link saved script** to attach the script to a dashboard project, then use **Open project** to recover that script, draft, and exports.

On Apple Silicon, select a topic and project in **Discover Topics** and choose **Generate video**. The preset targets 30 seconds with an illustrated opening, a short Wan clip, and a FLUX still. It uses the linked 30-second script for the same topic if present. Install Qwen and Kokoro and prepare both scene models in their existing panels before starting. The job saves each completed step. Use **Resume job** after stopping or reopening the app. A successful job links the script and saves an MP4 and an editable timeline when the script has no saved timeline yet.

## Build the self-contained Apple Silicon app

The build machine needs the development tools above, uv 0.11.2, and arm64 FFmpeg and FFprobe with libx264 available. From the repository root:

```sh
backend/.venv/bin/python -m pip install --cache-dir backend/.pip-cache uv==0.11.2
cd apps/desktop
npm run tauri:build -- --bundles app
```

The app is written to `apps/desktop/src-tauri/target/release/bundle/macos/AI Blogger Studio.app`. It launches the bundled Python 3.12 sidecar and media tools after the app is moved to another directory. It uses the system's native app-data paths and keeps model downloads opt-in. The bundle includes the illustrated presenter. MuseTalk still uses its separate experimental development runtime. Developer ID signing, notarization, and redistribution review remain open.

To include the optional ZONOS3-Mini engine, run `sh apps/desktop/scripts/prepare-zonos-runtime.sh` from the repository root before building the macOS app. This prepares the pinned Metal tools and licenses using CMake and Cargo. The model weights are installed separately in **Models & Tools** with user confirmation. **Voice Cloning** lets you save up to eight recordings per profile. HolosTTS and ZONOS3-Mini combine their independently encoded references, and OpenVoice and Pocket TTS use the first recording.

## Media engine development

For Phase 7 development on Apple Silicon, the local scene benchmark runs FLUX.2 Klein and Wan 2.1 separately. Review the pinned sources in [LICENSE_AUDIT.md](LICENSE_AUDIT.md) before opting into the 5.21 GB image or 4.49 GB video download. Both share a 35 MB Metal runtime. The 384 × 224 video profile uses the TAEHV preview decoder and requires FFmpeg with libx264 and FFprobe.

```sh
backend/.venv/bin/python -B -m backend.scenes.benchmark image --work-dir /tmp/ai-blogger-scenes --download
backend/.venv/bin/python -B -m backend.scenes.benchmark video --work-dir /tmp/ai-blogger-scenes --download
```

Each successful run saves the output, runtime log, and a JSON report with the model checksums, elapsed time, process peak resident memory, and output validation. Use the same directory and omit `--download` for an offline rerun with cached weights. Press Ctrl+C to cancel.

The development composer renders a JSON timeline using local PNGs, MP4s, and a saved narration WAV. Put the assets next to the timeline or in a subdirectory and use relative paths. Each scene specifies its duration in frames at 24 fps. The format can be `landscape`, `vertical`, or `square`. An incoming `fade_black` transition fades the two neighboring scenes through black without shifting the narration. An optional `transcript` aligns the full English narration to produce a selectable subtitle track. Timed `captions` with `start_ms`, `end_ms`, and `text` can be supplied in its place.

```json
{
  "format": "landscape",
  "quality": "standard",
  "narration": "narration.wav",
  "scenes": [
    {"kind": "video", "asset": "presenter.mp4", "frames": 72},
    {"kind": "image", "asset": "scene.png", "frames": 72, "transition": "fade_black"}
  ]
}
```

```sh
backend/.venv/bin/python -B -m backend.scenes.compose path/to/timeline.json path/to/new-export.mp4
```
