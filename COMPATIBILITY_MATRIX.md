# Compatibility Matrix

Status is evidence-based. “Not tested” is not a support claim.

## Development environment observed

| Platform | OS / architecture | Hardware | Development tools | Status |
|---|---|---|---|---|
| macOS | macOS 27.0.1, arm64 | MacBook Pro 14,9; Apple M2 Pro; 32 GB unified memory | Node 20.19.4, npm 10.8.2, Python 3.9.6, FFmpeg 8.0.1, Xcode CLI, Rust 1.100.0 | Host inventory and Tauri development launch observed; native-window commands not fully verified |
| Windows | Not available | Not available | Not inspected | NOT TESTED |
| Linux | Not available | Not available | Not inspected | NOT TESTED |

Available workspace storage was approximately 17 GiB at inspection and approximately 12 GiB after dependency/build work. The selected Qwen2.5 0.5B Q4_K_M model is 491 MB (469 MiB); its checksum-verified install and real Metal inference have been exercised in a temporary acceptance data directory.

## Phase 1 software compatibility

| Component | Planned support | Current evidence | Status |
|---|---|---|---|
| Frontend | Shared React/TypeScript UI | Host Node version meets Vite's documented Node 20.19+ floor ([Vite guide](https://vite.dev/guide/)); current production build passes | macOS build verified; desktop command smoke remains open |
| Tauri 2 host | macOS, Windows, Linux | Local Rust 1.100 toolchain installed; locked crate compiles and the development host launches on macOS. Platform prerequisites differ ([Tauri prerequisites](https://v2.tauri.app/start/prerequisites/)). | macOS compile/dev launch observed; native command smoke and other OSes not verified |
| FastAPI sidecar | Python runtime packaged per platform | The Apple Silicon app build includes relocatable Python 3.12.12 and the core dependencies. The development host uses Python 3.9.6. | Apple Silicon bundle available. Windows/Linux packaging pending. |
| SQLite | Local persistence | Application database initialization, project persistence, and restart behavior covered by API tests | Development persistence verified on macOS |
| FFmpeg | Managed or bundled media tools | Apple Silicon packaging includes build-host FFmpeg/FFprobe with libx264 and relocates their non-system libraries into the app bundle. | Apple Silicon bundle available. Other OS builds and redistribution review pending. |

## Phase 2 adapter evidence

| Adapter | Implemented behavior | Evidence | Validation status |
|---|---|---|---|
| macOS | System Profiler CPU/GPU/Metal fields, `vm_stat` memory estimate, Tauri app directories | Host report: Apple M2 Pro, 10 logical cores, 32 GiB unified memory; Qwen2.5 0.5B GGUF ran through the Metal build | Observed and model inference verified on macOS 27.0.1 arm64 |
| Windows | `GlobalMemoryStatusEx`, CIM CPU/GPU inventory, optional `nvidia-smi`, Tauri app directories | Parser/normalization and Windows path tests with mocked data | NOT TESTED natively |
| Linux | `/proc/cpuinfo`, `/proc/meminfo`, DRM sysfs, optional `lspci` and `nvidia-smi`, XDG paths | Memory/PCI parser and Linux path tests with fixtures | NOT TESTED natively |
| Shared paths/process | Tauri resolves data/cache/config/log paths; dedicated temp under cache; launch token and paths passed to FastAPI; child process retained and killed at shutdown | Rust `cargo check`; Python API/path tests | macOS development path exercised; Windows/Linux lifecycle not tested |
| Storage | Filesystem capacity for the volume containing the application data directory | Host `/api/system` report and API tests | macOS observed; others mocked/not tested |

## Phase 3 runtime evidence

| Capability | Evidence | Validation status |
|---|---|---|
| Bundled catalog and manifest | Catalog SHA-256 is pinned in application source; Pydantic rejects unknown fields and unsafe paths; artifact size and SHA-256 are checked | Unit tested |
| Runtime smoke utility | First-party Python process installs into app data, reports a ready marker, logs output, stops, and removes registered files | Exercised by API integration tests on macOS arm64; not an AI runtime |
| Compatibility gate | Catalog records macOS arm64 as verified and Windows/Linux x86_64 as not tested; install/start reject non-verified platforms | macOS arm64 verified; other platforms intentionally disabled |
| Download and install | Resumable copying of bundled local artifacts, cache registry, staging, entrypoint compile check, atomic promotion, failure cleanup | macOS development path and failure path tested; network download and cross-volume behavior not tested |
| Storage and cleanup | Tracks runtime component, downloaded artifact, and tool log files; cleanup removes only registered cache/temp files; confirmed tool removal keeps projects | macOS API integration tests pass; full project/export accounting and OS-level uninstall remain open |
| Updates and runtime telemetry | No catalog update feed, replacement installer, resource usage sampling, or update rollback yet | NOT IMPLEMENTED |

## AI acceleration matrix

| Runtime / model | Target combination | Evidence | Status |
|---|---|---|---|
| llama-cpp-python 0.3.36 + Qwen2.5 0.5B Instruct Q4_K_M | macOS arm64, Apple M2 Pro, Metal | Metal runtime compiled; official model file downloaded and SHA-256 checked; 30 topic records, a 30-second script, and a six-scene director plan generated through the authenticated local API | VERIFIED ON THIS HOST |
| llama-cpp-python + Qwen2.5 0.5B | Windows/Linux and other architectures | No native package/model install or inference run | NOT TESTED · installation disabled in the model UI |
| Sherpa-ONNX 1.13.6 + Kokoro v0.19 int8 | macOS arm64, Apple M2 Pro, CPU | Model installer health check, two English voice previews, speed adjustment, and persisted 24 kHz WAV passed | VERIFIED ON THIS HOST |
| Sherpa-ONNX + Kokoro | Windows/Linux and Intel macOS | Native runtime and model validation pending | Installation disabled |
| PocketSphinx 5.1.1 + Pillow + FFmpeg | macOS arm64, Apple M2 Pro, CPU | Native source build, audio and transcript alignment, 480 × 480 H.264/AAC MP4, and frame/audio timing checked through local API | ILLUSTRATED RENDER VERIFIED |
| MuseTalk 1.5 Q4 + MLX 0.30.6 | macOS arm64, Apple M2 Pro, Metal | Pinned model install plus a three-second 640 × 640 H.264/AAC render from a fictional still portrait. Decoded mouth movement and stream timing verified. | EXPERIMENTAL STILL PORTRAIT VERIFIED |
| MuseTalk + OpenCV reference tracking | macOS arm64, Apple M2 Pro, Metal | Three-second 640 × 640 H.264/AAC render from a controlled moving reference, upper-face preservation and narration timing checked through the local API | EXPERIMENTAL MOVING REFERENCE VERIFIED |
| MuseTalk + subtle photo motion | macOS arm64, Apple M2 Pro, Metal | Three-second 640 × 640 H.264/AAC render with detectable tilt and blink, narration timing verified through the local API | EXPERIMENTAL PHOTO MOTION VERIFIED |
| Tauri + WebKit saved media preview | macOS arm64, Apple M2 Pro | Fetched the portrait MP4 and narration WAV through the real Tauri commands. Decoded distinct MP4 frames after seeking and sought through the WAV. Playback with sound via the native controls remains open. | NATIVE MEDIA LOADING AND SEEKING VERIFIED |
| stable-diffusion.cpp + FLUX.2 Klein 4B Q4 | macOS arm64 27.0.1, Apple M2 Pro, 32 GB unified memory, Metal | 512 × 512 local text-to-image benchmark, four steps, 36.41 seconds, 4.79 GiB peak engine RSS. PNG decoded and visually reviewed. | EXPERIMENTAL IMAGE BENCHMARK VERIFIED |
| stable-diffusion.cpp + Wan 2.1 T2V 1.3B Q4 + TAEHV | macOS arm64 27.0.1, Apple M2 Pro, 32 GB unified memory, Metal | 384 × 224 local text-to-video benchmark, 49 distinct frames at 16 fps, eight steps, 172.09 seconds, 5.19 GiB peak engine RSS. The full 512 × 288 VAE trial was stopped during decode. | EXPERIMENTAL PREVIEW VIDEO BENCHMARK VERIFIED |
| FFmpeg timeline composer + PocketSphinx | macOS arm64, Apple M2 Pro | Nine-second 1920 × 1080 MP4 from real portrait, image and video scenes, with synchronized AAC and six selectable captions. | STANDALONE EXPORT VERIFIED |
| Desktop script export | macOS arm64, Apple M2 Pro, Tauri and WebKit | Imported real generated scenes and exported a captioned 1080p MP4 through Tauri. A fresh native app process loaded and sought the same MP4. | NATIVE EXPORT AND SEEKING VERIFIED |

The Qwen2.5 0.5B profile is verified only on macOS arm64; other accelerator/model combinations remain untested. The API uses CPU inference outside the verified macOS arm64 target, and the UI blocks model installation there.

## Release claim policy

macOS, Windows, Linux, ARM64, x86_64, and accelerator support can only be labeled supported after native installation, startup, runtime, project persistence, and cleanup tests for that combination.


## Voice and Audio Visualizer studio extension

| Component | Apple Silicon macOS | Windows and Linux |
|---|---|---|
| Kokoro v0.19 int8 with Sherpa-ONNX 1.13.6 | English CPU synthesis, WAV/MP3, and synchronized MP4 tested | Studio can use compatible pinned runtime wheels. Native inference and package acceptance pending. |
| Piper Lada x_low with Sherpa-ONNX 1.13.6 | Ukrainian CPU synthesis tested on the five acceptance texts, with Cyrillic titles and SRT/ASS captions | Compatible Sherpa-ONNX wheels required. Native inference and pronunciation acceptance pending. |
| Pocket TTS January 2026 int8 Sherpa archive | English CPU reference conditioning to MP4 tested with an authorized synthetic sample. Noncommercial evaluation only. | Native inference, quality, and package acceptance pending. |
| HolosTTS 0.4 Ukrainian cloning | CPU verified on Apple M2 Pro using the packaged Python 3.12 runtime, including existing-profile reuse and synchronized MP4. Installer targets Apple Silicon with macOS 14+. | Runtime packaging and native tests pending. |
| OpenVoice V2 English and cross-language cloning | CPU verified on Apple M2 Pro. English-to-English, English-reference-to-Ukrainian, and Ukrainian-reference-to-English use Kokoro or Piper Lada for pronunciation before tone conversion. Installer targets Apple Silicon with macOS 14+ and Python 3.12. | Runtime packaging and native validation pending. |
| NumPy, Pillow, Noto Sans, FFmpeg | Seven 2D styles and ten presets exercised. MP4 needs libx264 and AAC, WebM needs libvpx-vp9 and Opus, and PNG frames export to ZIP. | Same capability requirements. Test encoder availability in Models & Tools. |
| Microphone and desktop playback | PortAudio recording path and macOS microphone usage description included. Automated tests use files and generated WAVs. Native microphone and playback QA pending. | PortAudio and an available input device required. Native microphone and playback QA pending. |

Sherpa-ONNX and Sherpa-ONNX Core use pinned versions in the project’s Python environment. Linux microphone capture also requires a system PortAudio library. No GPU or paid service is required by the tested paths. The HolosTTS acceptance run peaked at about 1.73 GB of worker RSS. OpenVoice cross-language tests peaked at about 1.33 GB. Pocket TTS previously peaked at about 710 MB. [Sherpa-ONNX Core platform wheels](https://pypi.org/project/sherpa-onnx-core/1.13.6/#files)
