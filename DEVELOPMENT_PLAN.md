# Development Plan

The product is delivered in verifiable phases. A phase is complete only when its stated acceptance checks pass on the relevant platform. Unsupported or untested capabilities remain explicitly marked **NOT IMPLEMENTED** or **EXPERIMENTAL**.

## Phase 1 — Cross-platform foundation

1. Create the Tauri 2, React, TypeScript, Vite, and Tailwind desktop shell.
2. Add a Rust-owned Python/FastAPI sidecar lifecycle and narrow Tauri commands.
3. Add SQLite initialization and persisted settings/projects.
4. Implement a working dashboard and settings screen.
5. Verify frontend build, backend tests, and Rust/Tauri build when toolchains are available.

Acceptance: frontend typechecks/builds; API tests cover health, auth, persistence, and validation; desktop host compiles and communicates with the backend; database survives restart.

## Phase 2 — Platform abstraction

1. [x] OS, architecture, memory/storage, CPU, GPU, and accelerator inventory adapters for macOS, Windows, and Linux.
2. [x] Tauri-native data/cache/config/log paths; app-managed temp path; backend environment handoff; development sidecar startup/shutdown; disk capacity reporting.
3. [x] Platform parser/normalization tests and a Settings system report.
4. [ ] Native Windows/Linux validation and cross-platform process/path smoke tests.

Acceptance: implementation and mocked tests exist for all three operating systems; macOS host inventory is observed. Phase 2 remains open until native Windows/Linux validation passes. No cross-platform support claim before target testing.

## Phase 3 — Runtime and storage management

1. [x] Typed manifest validation and a versioned built-in catalog with an application-source SHA-256 integrity pin.
2. [x] Bundled artifact copying with resumable partial files, SHA-256 verification, staged install, static entrypoint validation, atomic promotion, and restart recovery.
3. [x] Runtime supervisor with ready-line health check, start/stop, process log capture, and shutdown cleanup.
4. [x] Registered component/cache/log/temp files, safe registered-file cleanup, storage report, and confirmed “remove tools, keep projects” operation.
5. [x] AI Tools screen with search, category/platform/compatibility filters, license text and acceptance, install state, process controls, and cleanup actions.
6. [ ] Native Windows/Linux lifecycle verification; remote HTTPS catalogs/downloads, updates/rollback, and isolated AI runtimes.

Acceptance: a small first-party utility installs, starts, passes its ready health check, stops, and removes on the macOS arm64 development host; API integration tests cover cleanup and project retention. Phase 3 remains open because Windows/Linux native runs, remote download/resume, version updates/rollback, and production isolation have not been implemented.

## Phase 4 — Local language model

1. [x] Select an official Qwen2.5 0.5B Instruct Q4_K_M GGUF (Apache-2.0 weights) and pin its SHA-256; pin llama-cpp-python 0.3.36 (MIT).
2. [x] Add an explicit-license-acceptance model installer with resumable download, progress, cancellation, and checksum validation.
3. [x] Add authenticated local inference endpoints for 30–50 topic batches, five script durations, and Pydantic-validated director scene timelines.
4. [x] Persist topic history, duplicate filtering, favorites, used-topic state, editable scripts, script quality warnings, and director timelines.
5. [x] Add the Discover Topics workflow and install progress to the desktop UI.
6. [x] Install the inference binding and model, run real generation on macOS arm64, and review structured topic, script, and director output.
7. [ ] Validate runtime/model installation on Windows and Linux before enabling those platforms.

Acceptance: real local inference is demonstrated on declared hardware; structured topic, script, and director output validates. macOS arm64 acceptance passes. Windows/Linux validation and real inference checks for the remaining script durations remain open.

## Phase 5 — Voice

1. [x] Install the pinned English Kokoro package through the app and verify it locally.
2. [x] Provide English voice selection, preview, and speed controls in the script editor.
3. [x] Chunk saved narration, normalize it, and persist a 24 kHz PCM WAV.
4. [ ] Verify native installation and generation on other platforms before enabling them.

Acceptance: real English narration generated locally with license and compatibility evidence. Apple M2 Pro CPU generation passed, including a saved WAV and reload after API restart.

## Phase 6 — Avatar

1. [x] Add an Avatar Library with illustrated presenters, appearance controls, preview and persistent presets.
2. [x] Render a talking head from local narration and verify the audio and mouth pose timing on Apple Silicon.
3. [x] Integrate and benchmark MuseTalk lower-face animation from a still portrait on Apple Silicon.
4. [x] Track the face box in a video reference and use its original head and upper-face motion in the rendered portrait.
5. [x] Offer subtle 2D head motion and blinking from a still portrait.
6. [x] Verify that the native macOS WebKit window can load, decode, and seek through a saved portrait clip.
7. [ ] Verify playback with sound through the native Play control and test generation on other platforms.
8. [ ] Evaluate broader head poses and facial expressions from a still portrait.

Acceptance: the illustrated, still portrait, and moving reference workflows passed through the local API on Apple Silicon. The saved still photo motion clip loaded and decoded in the native macOS test app. Playback with sound through the native controls, broader head poses, and support on other platforms remain open.

## Phase 7 — Image and video generation

Benchmark local image and short video generation independently by OS, architecture, accelerator, and memory tier.

FLUX.2 Klein still images and Wan 2.1 preview clips are available inside the desktop script editor after approval of the pinned model downloads. Generated assets are saved to the script. Other hardware profiles and Windows/Linux benchmarks remain open.

Acceptance: actual output, cancellation, and resource behavior verified; unsupported combinations are clearly disabled.

## Phase 8 — Video composition

Add validated timeline rendering, FFmpeg orchestration, subtitles, audio synchronization, MP4 export, and FFprobe validation.

The desktop script editor generated a verified MP4 with narration, mixed background audio, and embedded and separate captions. The macOS test app reopened the saved timeline and loaded the saved export. Advanced subtitle styling and audible playback through the native controls remain open.

Acceptance: reproducible sample project exports playable H.264/AAC MP4 with synchronized audio/subtitles.

## Phase 9 — End-to-end workflow

Persist resumable jobs and connect topic selection through export.

Apple Silicon has a 30-second topic-to-MP4 preset with local Qwen, Kokoro, an illustrated presenter, FLUX, and Wan. The desktop job saves stage checkpoints, resumes after interruptions, and links the result to the dashboard project. Longer presets and native acceptance on other hardware profiles remain open.

Acceptance: complete offline workflow on each declared hardware profile.

## Phase 10 — Production release

Build/sign/notarize installers, complete native installation/uninstall checks, CI, documentation, and release validation.

The Apple Silicon app build now bundles Python, the core backend dependencies, and FFmpeg/FFprobe. Production signing, notarization, clean-machine installation, updates, uninstall, and Windows/Linux packaging remain open.

Acceptance: installation, startup, runtime, project persistence, and uninstall verified on each release platform.


## Voice and Motion Phase 1 extension

1. Reuse the Tauri sidecar, SQLite projects, the Kokoro installer, and bundled FFmpeg. Add a project media and audio analysis layer.
2. Route English to Kokoro and Ukrainian to Piper Lada with no language fallback. Verify both complete speech-to-video workflows on Apple Silicon before treating them as supported.
3. Connect reference import, recording, preprocessing, project assignment, and reuse. Hold commercially eligible clone defaults until both checkpoint terms and reference-conditioned quality pass. The Pocket ONNX archive is available for explicit noncommercial evaluation.
4. Keep preview and export on one Pillow renderer. Provide seven styles, ten starter templates, timeline controls, a recoverable export queue, and Cyrillic titles and timed subtitles.
5. Use the current Models & Tools page for local installation, health tests, enable/disable, and removal. New model releases require a reviewed, pinned catalog change.
6. Validate the Ukrainian HolosTTS reference pipeline and exercise a saved profile through audio and video export. This passed on Apple Silicon using an authorized synthetic reference.
7. Verify an English model with commercial-use terms and test English–Ukrainian transfer in both directions. OpenVoice V2 passed with authorized synthetic references on Apple Silicon.
8. Add reliable word timing and caption alignment for imported recordings. Design a model update path for the pinned catalog. Complete native microphone and playback checks, listening and speaker similarity review, and Windows/Linux acceptance.
