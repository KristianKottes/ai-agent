# AI Blogger Studio Architecture

## Current scope

This document describes Phases 1–3 and the Phase 4 local writing slice. Phase 4 includes a checksum-pinned model installer, llama.cpp adapter, and structured topic/script/director workflows. Real inference has been verified on the macOS arm64 development host; other operating systems remain untested.

## System shape

```text
React + TypeScript + Vite (apps/desktop)
                 │ Tauri invoke commands
                 ▼
Tauri 2 / Rust desktop host (apps/desktop/src-tauri)
                 │ loopback HTTP + per-launch bearer token
                 ▼
FastAPI sidecar (backend/api)
     ├── SQLite (application data directory)
     ├── Pinned local catalog → staged installer → managed component registry
     ├── Explicit model install → resumable HTTPS download → SHA-256 verification
     └── Local llama.cpp inference → topic/script/director JSON → validated SQLite history
```

- The React application owns presentation and user interaction.
- Rust owns the desktop lifecycle, starts the backend, chooses a loopback port, creates the launch token, and exposes narrow commands to the frontend.
- FastAPI owns application-domain operations and SQLite persistence. It binds only to `127.0.0.1` and requires the launch token.
- SQLite and later project assets belong in the OS-specific application data directory, never in a temporary runtime directory.
- Shared API types are mirrored as Pydantic models in the backend and TypeScript types in the frontend. Contract changes must update both sides.

## Implemented foundation boundaries

Implemented foundation: dashboard, Discover Topics, AI Tools, and Settings screens; backend health, settings, project, system, runtime, and local AI endpoints; SQLite project/topic/script/director persistence and schema migration; authenticated Tauri-to-FastAPI commands; platform inventory adapters; a code-pinned local catalog; and a managed lifecycle test utility.

See [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md) for later features, platform checks, and release work.

## Data and process lifecycle

The Tauri host resolves native data, cache, config, and log paths, creates them with owner-only Unix permissions, allocates a free loopback port, and starts Python with a randomly generated token and application paths. It waits for `/api/health` and retains the child process handle. The Apple Silicon bundle resolves the backend source, Python interpreter, and media binaries from `Contents/Resources/studio-runtime`. The development host resolves Python and the backend from the checkout. On app close Tauri calls an authenticated loopback shutdown endpoint, waits for FastAPI lifespan cleanup to stop managed component processes, and force-kills the backend after a timeout. Temporary files use a dedicated directory under the application cache. Python platform adapters use OS-native fallback paths when launched on their own.

The backend migrates SQLite from schema version 1 through 3, adding installed component, managed-file, operation, topic history, editable script, and director timeline tables. Project and topic writes use parameterized SQL. Generated media assets and pipeline jobs are future schema work.

The local catalog is validated against typed Pydantic manifests and a SHA-256 pin in application source. The first-party lifecycle utility is bundled with the app and its files are individually pinned. Installation copies files to cache using resumable `.part` files, checks hashes, compiles (without executing) the entrypoint, stages under the component directory, atomically promotes the install, and registers each file. Startup recovery revalidates an interrupted promotion or cleans registered staging files after a crash. The supervisor starts only installed files whose digest and stored manifest still match the catalog, waits for a JSON ready record, captures logs, and stops the owned process at app shutdown. This sample uses the backend's current Python interpreter; it is not an isolated production runtime.

## Security decisions

- Backend listener: loopback only.
- Backend calls: random per-launch bearer token; token is not returned to the frontend.
- Frontend access: Tauri commands only; no arbitrary frontend URL or shell execution.
- Database: parameterized SQLite statements.
- Bundled catalog integrity: exact catalog bytes are pinned by a source-controlled SHA-256 digest; component payloads are separately SHA-256 verified.
- Component execution: no shell is used; the only enabled installer adapter handles a bundled Python test utility. It does not execute install scripts.
- Managed cleanup: removal checks the registered inventory and stays inside app-owned component/cache/temp roots. Cache and temp cleanup leaves unregistered files in place.
- Remote catalogs/downloads and AI component installers are disabled until official sources, licenses, platform compatibility, and a signed or otherwise authenticated catalog are reviewed.
- The Phase 4 model downloader is separate from the runtime catalog. It accepts only a fixed official Qwen URL, resumes into one model filename, and verifies the official file's SHA-256 before promotion. The UI requires explicit model-license acceptance and exposes cancellation/progress.
- Model output remains untrusted content. Topic/script/director objects are parsed and Pydantic-validated before persistence; the user can edit scripts before planning scenes.

## Platform strategy

The shared frontend displays a system report from platform-specific Python probes: macOS System Profiler and `vm_stat`; Windows `GlobalMemoryStatusEx` and CIM; Linux `/proc`, DRM sysfs, and optional NVIDIA tooling. That inventory remains model-agnostic; the Qwen2.5 0.5B profile was separately verified with Metal on this Apple M2 Pro host. Disk totals describe the volume containing the data directory. Rust owns Tauri path resolution and the sidecar process lifecycle. The Phase 3 test component is verified only on macOS arm64. Windows/Linux adapters and process behavior remain untested natively. See [COMPATIBILITY_MATRIX.md](COMPATIBILITY_MATRIX.md).


## Voice and Audio Visualizer studios

The Voice Studio, Voice Cloning, and Audio Visualizer screens use the existing React application, Tauri bridge, authenticated FastAPI sidecar, and project IDs. Discover Topics can also send a saved script narration to a linked project’s visualizer. The new routes live under `/api/studio`. Rust validates project and asset identifiers, scopes playback to individual registered media paths, and saves explicit exports to Downloads with collision-safe names.

Schema 14 adds studio audio and visual assets, project drafts, reusable presets, voice profiles and assignments, component state, and a persistent job queue. Project media lives under `studio/<project-id>/` in app data. Reference audio lives under `studio/profiles/<profile-id>/`. The app keeps the original reference and its processed WAV in the owner-only app data directory on Unix. Reference conditioning takes place in the inference worker and no embeddings are saved. A copied composition receives a new project ID and copies its selected source and artwork to new asset IDs.

Speech requests select an explicit language, model, and voice or profile. The queue runs model work sequentially with the existing narration lock. Child processes load Sherpa-ONNX, HolosTTS, or OpenVoice V2 on demand and release allocations on exit. The Ukrainian text pipeline preserves Unicode, normalizes common time, date, number, and currency forms, and applies an editable pronunciation dictionary. Each synthesized section produces a caption cue from its measured sample range. The first-party motion engine uses those cues for sentence-level captions and SRT/ASS export.

NumPy measures short-time RMS, peak, frequency bands, spectral flux, silence, and regular beats. Pillow renders the same versioned layer and keyframe definitions for both seek previews and video frames. FFmpeg muxes the original project audio with the frame stream. The queue persists job inputs, serializes exports through an OS file lock, resumes interrupted work, and terminates the active encoder on cancellation. Audio analysis, preview frames, fonts, and gradient assets have bounded in-memory caches. Voice jobs record peak worker RSS on macOS and Linux.

Models & Tools presents speech checkpoints, analysis, motion, and FFmpeg alongside the existing runtime catalog. Installers demand consent, verify fixed archive sizes and SHA-256 digests, stage privately, and register files in the app’s managed inventory. Exact version and license evidence lives in [LICENSE_AUDIT.md](LICENSE_AUDIT.md).

HolosTTS uses its own checksum-pinned Python libraries under the installed model directory. The macOS app downloads those files and the two permitted model checkpoints only after the user accepts the model terms. Interrupted downloads keep verified or partial files for retry. The installer verifies the files, probes the isolated runtime offline, and promotes the install through the existing Models & Tools inventory. The inference worker builds both architectures from local weights and derives the conditioning tensor from the selected authorized recording. Previously saved Ukrainian profiles become eligible when the model is installed on a compatible runtime.

OpenVoice V2 combines the studio’s existing Kokoro English or Piper Lada Ukrainian synthesis with the publisher’s MIT-licensed tone converter. The worker extracts an embedding from the assigned authorized reference once per job and transfers its tone to each synthesized section. Either reference language can be used with either output language. The selected base engine supplies pronunciation and rhythm. The catalog only marks an output language ready when its base model is installed and enabled. OpenVoice and Holos share the resumable checksum-verifying installer and keep separate model directories.

## Native image and video studios

Schema 15 adds revisioned Video Creator timelines and versioned image profiles to the existing Studio library. Image Studio runs Diffusers in an isolated local process. Video Creator uses FFmpeg to render the same project timeline to previews and exports. Both reuse the authenticated Tauri bridge, the project asset inventory, and the background queue. See [MEDIA_STUDIOS.md](MEDIA_STUDIOS.md).
