# Development Status

Updated: 2026-11-16

## Environment inventory

- Host: macOS 27.0.1, MacBook Pro 14,9, Apple M2 Pro, arm64, 32 GB unified memory.
- Storage: workspace volume 460 GiB total; 17 GiB available at initial inspection and approximately 12 GiB before the Phase 4 runtime/model install.
- Host tools: Node.js 20.19.4, npm 10.8.2, Python 3.9.6, FFmpeg 8.0.1, SQLite CLI, Xcode command line tools.
- Initially missing Rust/Cargo and Python API packages; Rust 1.100.0 is now installed under the ignored project-local `.rustup`/`.cargo` directories. FastAPI, Uvicorn, Pydantic, HTTPX, and pytest are installed in ignored `backend/.venv`.
- No project files or repository instructions were present at task start.
- npm's default cache has root-owned entries; installs should use a task-local cache rather than modifying that external cache.

## Phase 1 status

| Area | Status | Evidence / next action |
|---|---|---|
| Architecture, plan, status, compatibility, license audit | Implemented | The license audit lists direct packages; full transitive/distribution review remains open. |
| React/TypeScript/Tailwind shell | Implemented | Dashboard, Settings, local project creation, and explicit Phase 1 limitations; production frontend build passes. |
| Rust/Tauri desktop host | Development and Apple Silicon bundle | Tauri launches the authenticated FastAPI sidecar. The Apple Silicon release build resolves its Python and media tools from the app bundle. |
| FastAPI sidecar | Implemented | Health, settings, and project endpoints run with Pydantic validation and launch-token auth. |
| SQLite persistence | Implemented | Schema initializes idempotently; API tests verify settings, validation, and persistence after backend restart. |
| macOS desktop acceptance | Verified on the development host | The relocated Apple Silicon release test bundle exercised native project, model, and video commands. A clean-machine install remains open. |
| Windows/Linux acceptance | NOT VERIFIED | No target systems available in this task. |

## Phase 2 status

| Area | Status | Evidence / next action |
|---|---|---|
| OS and architecture | Implemented | Python report normalizes macOS, Windows, Linux names and common CPU architecture aliases. Current host reports macOS 27.0.1 arm64. |
| CPU and memory | Implemented | macOS uses System Profiler, `sysctl`, and a labeled `vm_stat` estimate; Windows uses `GlobalMemoryStatusEx` and CIM; Linux reads `/proc`. Host reports Apple M2 Pro, 10 logical cores, 32 GiB total, and an approximate available-memory estimate. |
| GPU and acceleration evidence | Implemented conservatively | macOS System Profiler; Windows CIM; Linux DRM sysfs/optional `lspci`; optional NVIDIA inventory via fixed-path `nvidia-smi`. Model inference compatibility is always `not_tested`. |
| Native application paths | Implemented | Rust resolves Tauri data/cache/config/log directories and creates an app-managed temp folder under cache. Python development fallbacks use macOS, Windows, and XDG conventions; Tauri passes paths through explicit environment variables. |
| Process and environment handling | Implemented for the development sidecar | Rust launches FastAPI with a per-launch token and explicit paths, retains the child handle, and terminates it on shutdown; Windows launch suppresses a console window. A general runtime supervisor is Phase 3. |
| Storage inventory | Implemented | System report shows capacity/used/available for the volume containing the application data path. |
| Platform verification | PARTIALLY VERIFIED | macOS host report observed; Windows/Linux path and parser logic tested with mocked data only. Native validation on those systems remains open. |

## Phase 3 status — PARTIALLY IMPLEMENTED

| Area | Status | Evidence / next action |
|---|---|---|
| Catalog and manifests | Implemented for the bundled catalog | Typed Pydantic contracts reject unknown fields and unsafe paths. Exact catalog bytes have a source-controlled SHA-256 pin. One first-party MIT test utility is included; there is no remote catalog. |
| Downloader and installer | Implemented for bundled assets | Copies into app cache with resumable `.part` files, checks size/SHA-256 and disk requirements, stages under app data, compiles the entrypoint without running it, atomically promotes and registers files. Startup recovery handles interrupted promotions and rolls back incomplete staging. |
| Runtime supervisor | Implemented for the smoke utility | Starts only registered files with the project backend Python, waits for the utility's JSON ready record, captures logs, stops the process, and handles authenticated graceful backend shutdown. This is not an isolated AI runtime. |
| Marketplace / Runtime UI | Implemented for the local catalog | Search and category/platform/compatibility filters; license text and explicit acceptance; install/start/stop/remove actions; storage and confirmed cleanup controls. The screen clearly labels the entry as experimental. |
| Storage and cleanup | Implemented for runtime-managed files | Registry covers installed component files, cached artifacts, and tool logs. Cache/temp cleanup removes registered files only; “remove tools” explicitly keeps project data. Project/export accounting is still open. |
| Platform validation | PARTIALLY VERIFIED | Lifecycle runs and passes on macOS arm64. Manifest marks Windows/Linux x86_64 as `not_tested`; the app blocks installation there until native validation is performed. |

## Phase 4 status — IMPLEMENTED, MACOS ARM64 ACCEPTANCE PASSED

| Area | Status | Evidence / next action |
|---|---|---|
| Model and license selection | Implemented | Official Qwen2.5 0.5B Instruct Q4_K_M GGUF; Apache-2.0 weights; official file SHA-256 is pinned. `llama-cpp-python==0.3.36` is pinned (MIT). See [LICENSE_AUDIT.md](LICENSE_AUDIT.md). |
| Model installation | Verified on macOS arm64 | `llama-cpp-python==0.3.36` compiled with Metal. The app installer downloaded the 491,400,032-byte official GGUF into a temporary acceptance data directory and verified its pinned SHA-256. Explicit license acceptance and install/status flow were exercised. |
| Topic discovery | Verified on macOS arm64 | The authenticated API generated and persisted 30 topics across eight categories. A follow-up four-topic inference validated distinct subject-specific titles after category prompts were refined. Favorites, used tracking, history filtering, and regeneration remain covered by API/contract checks. |
| Script generation | Verified on macOS arm64 | A real 30-second script was generated, edited, saved, and reloaded. Other duration contracts (60 sec, 3 min, 5 min, 10 min) are implemented but were not individually measured with real inference. |
| AI Director | Verified on macOS arm64 | A real six-scene, 30-second plan was generated, saved, and reloaded. The output covers all script paragraphs in order; weak grouping and overlong prompt fields are normalized before persistence. |
| API and desktop UI | Implemented; builds pass | Authenticated Tauri commands, local API, Discover Topics view, license gate, progress/cancel, generation workflow, library management, script editor, and scene timeline display. Frontend production build and Rust compile pass. |
| Model inference on host | VERIFIED | End-to-end inference ran on the declared Apple M2 Pro/macOS arm64 host with Metal. Installation and acceptance data were kept in a temporary app-data path, outside the source tree. |
| Windows/Linux model support | NOT TESTED | UI disables model installation except on macOS arm64; perform native runtime and inference checks before enabling more platforms. |

## Phase 5 status — Apple Silicon backend acceptance passed

- Kokoro v0.19 int8 provides 11 American and British English voices, speed control from 0.75× to 1.5×, and previews. Sherpa-ONNX 1.13.6 retains the phonemizer required by this model. The engine exposes speed only.
- The app downloads the 103 MB voice archive after consent, supports cancellation and partial download retries, verifies its size and SHA-256, and generates a sample before promotion. The checksum, extraction and real synthesis were tested on an Apple M2 Pro using an isolated data directory.
- The script editor renders saved text in sections and peak-normalizes mono 24 kHz PCM WAV to −1 dBFS. The authenticated API produced 5.54-second and 6.44-second previews from two voices. The 1.3× preview of the first voice was 4.76 seconds. A 23.65-second narration rendered in about 24 seconds and its saved file and metadata reloaded after an API restart. Editing script text hides the prior render from the current-script view.
- SQLite schema 4 tracks WAV metadata and the script content hash. The managed-file registry records installed voice files and generated WAVs. Earlier scripts and script edits remain compatible.
- Desktop production build and Rust check pass. In a native macOS test bundle, WebKit loaded the saved 23.65-second WAV through Tauri and sought to 1.25 seconds. Playback through the native controls with sound remains unverified.

## Phase 6 status — illustrated presenter, still portrait, and moving reference verified on Apple Silicon

- The script editor can preview Maya and Eli, choose an outfit and backdrop, toggle head movement and blinking, and save named presets. It uses the voice already selected for the latest saved narration.
- PocketSphinx 5.1.1 maps English speech to phoneme timings. The renderer uses transcript alignment when the saved text matches the complete recording and the dictionary covers it. Short excerpts and out-of-dictionary text use acoustic phoneme recognition. Pillow draws the original presenters and FFmpeg encodes up to 30 seconds as a 480 × 480, 24 fps MP4.
- The Apple M2 Pro rendered a five-second excerpt in about three seconds and the full 23.65-second sample in about ten seconds. Both MP4 streams started at 0. A decoded-frame check matched the expected mouth pose on all 45 sampled frames. The muxed excerpt's decoded audio correlation with the source was 0.99993. These checks verify the timing pipeline and encoded output.
- The authenticated API saved and reloaded a preset and the latest MP4 after restart. SQLite schema 5 stores render metadata and registers clips under the application data directory.
- The experimental Portrait mode uses MuseTalk 1.5 Q4 with MLX in a separate Python 3.12.12 environment. It stores imported portraits and their face framing in SQLite schema 6. The 1,509,368,895-byte model downloaded through the local API, passed pinned SHA-256 checks, and was registered in temporary acceptance app data.
- On the Apple M2 Pro, a three-second portrait using the fictional test image rendered in about 20 seconds. FFprobe reported 72 frames at 24 fps and zero start or end offset between the MP4 streams. The decoded audio correlated 0.99993 with the source excerpt. Decoded frames showed movement in the mouth and a stable upper face. A restart recovered the saved framing and clip.
- Video references use OpenCV tracking to follow the first frame's face box. The renderer replaces the lower face while retaining the rest of each original frame. Import accepts up to 16 MiB of MP4 or MOV and stores the first 10 seconds without source audio. Schema 7 preserves earlier photo records and adds a reference frame count to video records.
- A three-second moving fixture generated from the fictional portrait exercised translation, scale, and rotation. The Apple M2 Pro rendered 72 frames in about 32 seconds. The audio and video offsets were both 0 ms. Comparing decoded pixels against the normalized input showed a mean luma difference of 1.85 of 255 in the upper face and 15.44 in the mouth region. The original narration and decoded output audio had a correlation of 0.99993. The reference, face framing, and render survived a restart.
- Still portraits can optionally add a gentle 2D tilt and drift and blink if both eyes are detected. The Apple M2 Pro rendered a three-second, 72-frame sample in 17.04 seconds. Both stream offsets were 0 ms, and decoded audio correlated 0.99992 with the saved narration. Decoded frames showed a blink and a median upper-face shift of 6.66 pixels, with a mean corner luma change of 0.02 of 255. The authenticated API reloaded the framing and motion clip after restart.
- Both avatar modes use the same video preview. It offers a retry if loading fails and points to the saved MP4 if playback reports an error. A native macOS test bundle fetched the 3-second, 640 × 640 portrait clip through the Tauri command and decoded distinct frames after seeking. FFprobe independently confirmed the encoded AAC track. WebKit rejected a scripted request to play with sound, and the automation tool could not reach the native Play control.
- The experiments used one fictional portrait and controlled movement. Broader poses and expressions, native playback with sound, and generation on other platforms remain open.

## Phase 7 status — desktop image and preview video generation on Apple Silicon

- The scene benchmark uses a pinned stable-diffusion.cpp Metal build and downloads only the selected profile on request. It verifies each file's SHA-256, preserves partial downloads for retry, and saves the generated media, engine log, and a JSON report in a separate working directory.
- FLUX.2 Klein 4B Q4 generated a 512 × 512 lighthouse scene from a text prompt on the M2 Pro. Four steps took 36.41 seconds including model loading and PNG output. The engine process reported a peak resident size of 4.79 GiB. The image decoded successfully and was visually reviewed.
- The first Wan 2.1 T2V 1.3B attempt used 512 × 288, 49 frames, and 20 steps. Denoising took 769.48 seconds. Its standard decoder then spent 577.06 seconds on the first of six tiles. The run was cancelled and did not produce an MP4. The revised profile uses 384 × 224, eight steps, and the 22.6 MB TAEHV preview decoder. The benchmark checks that the H.264 output decodes with a full sequence of varied frames.
- The revised Wan profile generated a 3.0625-second coastal scene on the M2 Pro in 172.09 seconds, including model loading and frame output. The engine reported a peak resident size of 5.19 GiB. FFprobe and decoded frame hashes verified 49 distinct frames at 16 fps. Visual inspection confirmed motion in the water.
- An offline FLUX run with cached weights was interrupted after denoising started. Ctrl+C terminated the Metal worker and removed the temporary runtime without publishing a success report.
- The script editor can generate a scene from an entered prompt or a director scene, with a seed, explicit download approval, progress, and cancellation. Schema 10 records jobs and generated assets. Real authenticated jobs generated a FLUX PNG in 73.62 seconds and a Wan preview MP4 in 175.27 seconds. Both assets and the cancelled job state survived restart. A native Tauri test also started and saved a real FLUX job. Interrupted jobs retain their prompt for retry.

## Phase 8 status — desktop script export verified on macOS

- The composer accepts a JSON timeline, resolves local PNG and MP4 assets, normalizes them to 24 fps and the chosen aspect ratio, and exports H.264/AAC MP4 after checking frame count, dimensions, and audio/video timing. Landscape, vertical, and square formats are available.
- Captions can come from explicit timings or English transcript alignment with PocketSphinx. The MP4 contains selectable timed text. The Mac test exported a nine-second 1080p MP4 from the saved MuseTalk portrait, Wan video, FLUX image, and Kokoro narration. It contained 216 frames and six automatically timed captions, with zero measured audio/video offset. Decoded audio correlated 0.99968 with the source and all six captions could be extracted.
- The desktop script editor can import PNG or MP4 scene files, add a presenter to the opening, choose scene lengths and order, and select format, quality, and captions. Cuts and fades through black preserve the narration timing. Schema 8 registers imported assets and completed exports. Saved exports remain accessible after edits, and a new export requires current narration.
- The isolated native bundle imported a generated image and clip through Tauri, exported a nine-second 1080p MP4 with six captions, and sought to 4.25 seconds through the scoped asset protocol. A fresh native app process loaded and sought the same saved MP4. FFprobe reported 216 H.264 frames, synchronized AAC, and a selectable timed text stream.
- Schema 9 autosaves each script's timeline, including its scene order, durations, transitions, and export settings. A fresh macOS test app process loaded the same three-scene draft through Tauri and found the existing MP4 after migration. Regenerating narration preserves the draft and flags an older presenter clip before export.
- Schema 11 stores imported background WAVs and the selected music level. Short tracks loop under the narration. Captioned desktop exports write SRT and ASS alongside the MP4. The native macOS test reopened generated scenes, exported a nine-second 1080p MP4 with stereo AAC and six captions, and loaded it in WebKit. FFprobe reported zero audio/video offset; both separate subtitle files contained all six cues.
- Manual playback with sound and advanced subtitle styling remain open.

## Phase 9 status — resumable 30-second preset on Apple Silicon

- Schema 12 links a dashboard project to an exact script. Opening the project loads that script's draft timeline, media, and exports. Backend tests verified that the link survives restart, and the native Tauri test linked and fetched the exact script.
- Schema 13 stores project video settings and the completed script, scene plan, narration, illustrated presenter, FLUX still, Wan clip, and MP4 IDs. Jobs resume from saved steps after a stop or interruption. The export, project link, and initial editable timeline are committed together. Existing manual timelines are preserved.
- Narration targets 30 seconds with a bounded voice speed adjustment and a duration check before scene generation. The preset requires the local models to be installed. Landscape, vertical, and square exports and optional English captions are available.
- An Apple Silicon run generated a fresh script, narration, presenter, and still, then stopped at the Wan stage. A restarted backend reused those outputs and exported a 30.125-second vertical MP4. FFprobe measured matching H.264 and AAC start and end times. The export included 18 English captions and separate SRT and ASS files.
- The isolated macOS app opened the saved project, resumed the export from the generated media, and loaded and sought the new MP4 to eight seconds in WebKit. The editable three-scene timeline was available through Tauri.

## Phase 10 status — self-contained Apple Silicon app bundle

- The release build packages Python 3.12.12, the core backend and its Python dependencies, and FFmpeg/FFprobe with libx264. It rewrites host library references and signs the modified binaries ad hoc. The roughly 285 MiB app bundle keeps model weights as separate, user-approved downloads.
- A relocated release test app started with a system-only PATH and invalid external Python settings. It used saved local weights to generate a new Qwen script and a Kokoro voice preview, exported a 10-second vertical MP4, and loaded the preview in native WebKit. It also read existing projects and saved a new SQLite record through Tauri.
- An audit of 129 Mach-O files in the standard app bundle found no absolute loader paths to non-system libraries. A clean-machine install, signing and notarization, update and uninstall, Windows/Linux bundles, and optional MuseTalk runtime packaging remain open.

## Explicitly not implemented

- Native Windows/Linux model installation and inference, and real inference checks for all five script durations.
- Larger pose changes and facial expressions beyond basic blinking from a still portrait. Video generation beyond the short preview profile and other hardware profiles remain open.
- Automatic presets for longer videos and independent end-to-end verification on Windows and Linux.
- Remote component catalog feeds/signatures and general component downloads; the Phase 4 model installer accepts one official model URL and requires its pinned SHA-256.
- Component updates, version replacement, update rollback, dependency package installation, and isolated per-component AI environments.
- Full storage accounting for project assets/exports and OS-level uninstallers.
- Resource scheduling and model-specific hardware compatibility testing.
- Production signing and notarization, cross-platform runtime packaging, app installers, updater, and complete desktop-application uninstaller. The Apple Silicon bundle includes the core sidecar and media tools. Packaging the experimental MuseTalk runtime remains open.
- Full schema migration framework and project asset recovery.
- Cross-platform release support.

## Phase 1 completion checklist

- [x] Frontend dependencies installed and locked.
- [x] Frontend typecheck/build pass.
- [x] Backend dependencies installed in isolated environment.
- [x] Backend tests pass, including auth and persistence.
- [x] Rust/Tauri compiles and development host launches the FastAPI backend.
- [x] Native window commands and project persistence verified on Apple Silicon.
- [ ] Production runtime packaging and launch verified across the target platforms.
- [ ] Windows and Linux native smoke tests completed.

## Latest verification

- `npm run build` — passed (TypeScript project build and Vite production bundle).
- `PYTHONPATH=. backend/.venv/bin/python -B -m pytest -q` passed 48 tests with one loopback test skipped in the sandbox. The suite covers model verification, cancellation of a live worker, looped background audio, script-scoped assets, and project, timeline, and export recovery after restart.
- `RUSTUP_HOME="$PWD/.rustup" CARGO_HOME="$PWD/.cargo" .cargo/bin/cargo check --locked --manifest-path apps/desktop/src-tauri/Cargo.toml` — passed on macOS arm64 from the project root.
- Direct macOS system report — returned Apple M2 Pro, 10 logical cores, 32 GiB unified memory, Metal support evidence, storage volume totals, and native app paths. Qwen2.5 0.5B Metal inference was separately verified; CUDA remains unknown.
- Runtime integration — the bundled lifecycle test utility installed, reported ready, stopped, and removed through the authenticated API; its project was preserved. The Windows/Linux manifest entries remain disabled as untested.
- Desktop-runner shutdown integration — separately passed outside the sandbox after the sandbox denied local loopback binding; authenticated shutdown stopped the managed utility before the sidecar exited.
- npm install audit — 0 vulnerabilities reported for the resolved frontend graph.
- `npm run tauri:dev` — launched. Rust starts the authenticated FastAPI sidecar, waits for its health response, and initializes SQLite under the macOS app-data directory. The browser preview rendered the expected non-desktop notice. The Apple Silicon release test app separately exercised project creation through Tauri.
- Python source syntax and API/AI tests — passed after the structured-output and normalization changes.
- Phase 4 local acceptance — installed the pinned binding and model with checksum verification; generated/persisted 30 topics; generated, edited, and reloaded a 30-second script; generated/reloaded a six-scene director plan; ran a focused four-topic category-prompt check.

## Next actionable work

Verify playback with sound using the native Play control and benchmark supported engines natively on Windows and Linux. The general runtime manager, longer portrait and script benchmarks, broader expressions, and production packaging remain open.


## 2026-11-16 — Voice and Audio Visualizer Phase 1 extension

| Milestone | Files and completed work | Verification and remaining work |
|---|---|---|
| Audit and shared audio | Extended the existing Tauri/React/FastAPI/SQLite app. `backend/studio/{api,audio,contracts,service}.py` adds schema 14, WAV/MP3 import, project audio, waveform summaries, reference preprocessing, and project persistence. | Real WAV and MP3 imports, FFT/RMS/onset/beat checks, same-project asset access, and restart recovery tested. |
| English and Ukrainian speech | `backend/studio/{speech,text,tools}.py` connects Kokoro and Piper Lada, supports bounded text batches, pronunciation overrides, normalized text preview, sample rates, pauses and WAV/MP3 export. `StudioVoice.tsx` adds the Voice Studio. | Both real models generated offline and produced synchronized MP4. The Ukrainian integration test includes all five brief sentences. The normalizer preserves explicit acute marks. Their effect in Piper, along with pronunciation and stress, still needs a native-speaker review. |
| Reference profiles | `StudioVoice.tsx`, `audio.py`, `speech.py`, and `service.py` add authorization, recording/import, trim, noise reduction, private original and processed audio, project assignment, preview and deletion. | Actual English Pocket reference conditioning and video export passed on a synthetic reference. Exact archive terms limit it to noncommercial evaluation. Identity and listening evaluation remain open. The later HolosTTS and OpenVoice milestones document the current cloning options. |
| Motion and export | `motion.py`, `export.py`, `StudioVisualizer.tsx`, and `studio.css` provide seven styles, ten templates, timed layers, transforms, masks, color tracks, signal bindings, artwork, backgrounds, and editor controls. | Deterministic frames verified for every style. Preview and matching transparent PNG output checked byte-for-byte. Cyrillic title and caption glyphs inspected in a decoded MP4 frame. |
| Desktop and model management | `App.tsx`, `VoicePanel.tsx`, `StudioPage.tsx`, `StudioParts.tsx`, `services/studio.ts`, and the Tauri bridge expose project switching, narration handoff, downloads, and the integrated Models & Tools page. `Info.plist` includes the microphone purpose. | TypeScript/Vite, Rust, and the Apple Silicon release bundle built successfully. Packaged Python, Piper Lada, FFmpeg, and Cyrillic typography rendered a 3.68-second MP4 offline. Desktop inspection was blocked by computer-use permissions. Native playback, microphone capture, and Windows/Linux acceptance remain open. |
| Testing and performance | `tests/test_studio.py` adds real-render and opt-in checkpoint integration tests; `tests/test_api.py` asserts migration from pre-existing data. `pytest.ini` keeps collection away from generated app bundles. | The first unscoped pytest discovery attempted to collect bundled Python dependencies. The project suite with real checkpoints passed 58 tests and skipped the existing shutdown test because of localhost restrictions. That shutdown test passed separately with localhost access. Studio tests covered render cancellation and interrupted-job recovery, looping video backgrounds, transparent WebM, and PNG frames. |

### Apple Silicon benchmark

Apple M2 Pro, 32 GB, CPU inference and a 480 × 270, 24 fps H.264/AAC motion export. Inference timings include starting and unloading the worker. RSS is the speech worker peak, excluding the desktop shell.

| Sample | Audio | Speech job | Peak RSS | MP4 render |
|---|---:|---:|---:|---:|
| Kokoro English | 2.95 s | 4.09 s | 440 MB | 3.13 s |
| Piper Lada Ukrainian, five acceptance prompts | 19.28 s | 1.48 s | 235 MB | 17.56 s |
| Pocket TTS English clone, synthetic reference | 2.47 s | 1.40 s | 710 MB | Completed with synchronized streams |

### Running the checks

`PYTHONPATH=. backend/.venv/bin/python -B -m pytest -q` runs the project suite. Set `AI_BLOGGER_STUDIO_TEST_MODELS` to a directory containing installed checkpoint folders to enable the real model tests. Tests run in temporary application data and leave the app’s project library alone.

The renderer uses section timestamps measured from synthesized samples. Word highlighting and automatic caption alignment for imported audio are pending. Speech pause trimming is restricted to reference preprocessing and editable inter-section gaps. The preview samples playback at about 8 fps and scopes exported video settings to the saved composition. The catalog installs pinned versions and does not offer remote version updates.

### Repeatable listening and identity evaluation

Use the five Ukrainian sentences in `tests/test_studio.py` and the fixed English sentence “A clear voice makes a good story easier to follow.” Render at 1×, 24 kHz, and the default sentence gap. Log the model version and the pronunciation overrides. A native listener checks every prompt for omitted words, names, Ukrainian stress, dates, times, and currency expansions. For cloning, record two authorized speakers reading unrelated 5–10 second excerpts. Generate the same test sentences for both profiles and repeat after restarting the app. Present the outputs in randomized order with the two reference recordings. Record speaker-match judgments, intelligibility, artifacts, rhythm, and loudness. The current real clone test establishes successful reference-conditioned synthesis and persistence. A human evaluation of speaker similarity is still pending.

### Ukrainian voice cloning milestone

- `backend/studio/holos.py`, `model_install.py`, and `holos_manifest.json` add CPU inference and a resumable installer for the locked model and Python dependencies. Models & Tools requests consent for the 934 MB download. It requires about 2.8 GB free for setup and an Apple Silicon macOS 14+ runtime with Python 3.12. The release bundle includes that interpreter. Models are installed separately.
- `speech.py` and `service.py` connect saved Ukrainian references to HolosTTS through the existing queue and save the generated audio, measured cues, and visualizer handoff. Existing authorized Ukrainian profiles become available after installation. No preset voice asset is installed or used. The speech worker keeps the reference vector in memory.
- `StudioVoice.tsx`, `StudioPage.tsx`, and Models & Tools show the compatible Ukrainian model and refresh saved-profile eligibility after installation. The workflow starts with **Models & Tools → HolosTTS Ukrainian → Install**, then **Voice Cloning → Українська → select a saved authorized profile**.
- The M2 Pro test used the exact checksum-verified model files and wheels with the bundled Python interpreter. The installer verified and unpacked the filled cache offline. Its Test action produced speech from a synthetic Ukrainian reference saved before installation. The speech queue produced a 10.07-second WAV in 13.14 seconds, with about 1.73 GB peak worker RSS. The visualizer produced a synchronized H.264/AAC MP4 within one frame.
- Dictionary-based stress is available. Ambiguous stress can still require a pronunciation override. Human listening and speaker similarity assessment, native microphone/playback, and Windows/Linux Holos packaging remain open.
- The separate Python 3.12 API integration test passed from the bundled interpreter, including profile reuse after restart, Models & Tools Test, cloning, and synchronized export. The Python 3.9 regression run passed 60 tests. Its Holos hardware case ran separately on 3.12, and the existing localhost shutdown case was skipped. The Holos installer unit checks also passed, including consent, preset exclusion, archive containment, and byte-range retry.

### English and cross-language voice cloning milestone

- `backend/studio/openvoice.py` and `openvoice_manifest.json` add the MIT-licensed OpenVoice V2 tone converter to the existing voice queue. `model_install.py` shares the verified download and install path with HolosTTS. The 222 MB install uses the packaged Apple Silicon Python 3.12 runtime on macOS 14+. Kokoro supplies English speech and Piper Lada supplies Ukrainian speech.
- `tools.py`, `speech.py`, `service.py`, and the React voice editor distinguish the recording’s language from the output language. OpenVoice accepts English and Ukrainian references. The form preserves the selected reference when switching languages where the model supports it. The catalog checks each language’s base model. HolosTTS remains the default for Ukrainian profiles when installed.
- The M2 Pro acceptance suite passed English-to-English cloning and both cross-language directions with synthetic references. Both cross-language outputs exported to synchronized MP4. An additional test on identical source audio and seed confirmed that changing the reference changed the resulting waveform. Human evaluation of speaker similarity and pronunciation remains open.
- The English-reference-to-Ukrainian test produced 5.26 seconds of audio in 4.97 seconds at about 1.20 GB peak worker RSS. The Ukrainian-reference-to-English test produced 6.15 seconds of audio in 11.25 seconds at about 1.33 GB. Times include loading the converter and base model. The installer verified and installed the pinned files from the filled cache, then loaded the converter offline in its isolated runtime.
- The Python 3.9 regression suite passed 61 tests. The HolosTTS and OpenVoice model-backed cases passed separately under Python 3.12, and the localhost shutdown case was skipped in the restricted environment. The frontend build passed.

## Native Image Studio and Video Creator

The native studios are connected to the desktop project picker, Models & Tools, Studio asset inventory and job queue. The new Video Creator renders uploaded or generated stills with narration, music and captions. Its timeline and image profile versions persist in SQLite. See [MEDIA_STUDIOS.md](MEDIA_STUDIOS.md) for setup, test coverage and compatibility details.
