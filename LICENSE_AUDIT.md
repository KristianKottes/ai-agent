# License Audit

Updated: 2026-11-14

This is an initial inventory of direct development dependencies, not a legal approval to redistribute the complete application. Exact frontend packages are pinned in `apps/desktop/package.json` and `package-lock.json`; Python packages are pinned in `backend/requirements*.txt`; Rust crates are resolved in `apps/desktop/src-tauri/Cargo.lock`.

## Direct dependencies observed

| Component | Locked version | License metadata | Source |
|---|---:|---|---|
| Tauri API / CLI | 2.12.4 | Apache-2.0 OR MIT | npm package metadata |
| Tauri Rust crate | 2.12.5 | Apache-2.0 OR MIT | Cargo registry package metadata |
| Tauri build crate | 2.7.4 | Apache-2.0 OR MIT | Cargo registry package metadata |
| React / React DOM | 19.3.1 | MIT | npm package metadata |
| TypeScript | 5.8.3 | Apache-2.0 | npm package metadata |
| Vite | 8.3.8 | MIT | npm package metadata |
| `@vitejs/plugin-react` | 6.3.0 | MIT | npm package metadata |
| Tailwind CSS | 3.4.19 | MIT | npm package metadata |
| TanStack Query | 5.107.1 | MIT | npm package metadata |
| Zustand | 5.1.0 | MIT | npm package metadata |
| Lucide React | 0.468.0 | ISC | npm package metadata |
| FastAPI | 0.115.12 | MIT | Installed metadata and [official tagged project metadata](https://github.com/fastapi/fastapi/blob/0.115.12/pyproject.toml) |
| Pydantic | 2.10.6 | MIT | Installed distribution metadata |
| llama-cpp-python | 0.3.36 | MIT | [Official PyPI project metadata](https://pypi.org/project/llama-cpp-python/0.3.36/) |
| Uvicorn | 0.34.0 | BSD-3-Clause | Installed distribution metadata |
| Reqwest | 0.12.28 | MIT OR Apache-2.0 | Cargo registry package metadata |
| Serde | 1.0.229 | MIT OR Apache-2.0 | Cargo registry package metadata |
| UUID | 1.29.2 | Apache-2.0 OR MIT | Cargo registry package metadata |
| HTTPX / pytest (tests) | 0.28.1 / 8.3.5 | BSD-3-Clause / MIT | Installed distribution metadata |

## Selected Phase 4 model

| Component | Version / quantization | Weights license | Source |
|---|---|---|---|
| Qwen2.5 0.5B Instruct GGUF | Q4_K_M · 491 MB / 469 MiB | Apache-2.0 | [Official Qwen Hugging Face model repository](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF) · [pinned GGUF file and SHA-256](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/blob/main/qwen2.5-0.5b-instruct-q4_k_m.gguf) |

The weights are reviewed separately from the MIT llama.cpp Python binding. The installer checks the expected SHA-256 before marking weights installed. Commercial-use terms still need a fuller redistribution and generated-output review before release.

## Phase 5 voice components

| Component | License evidence | Source |
|---|---|---|
| Kokoro v0.19 model weights | Apache-2.0 in the publisher's legacy model repository | [Kokoro v0.19](https://huggingface.co/hexgrad/kLegacy/tree/main/v0.19) |
| Kokoro v0.19 English voice weights | Original voice assets are published under the same repository's Apache-2.0 declaration. The runtime package combines them into speaker IDs 0–10. | [Upstream voice files](https://huggingface.co/hexgrad/kLegacy/tree/main/v0.19/voices), [speaker mapping](https://k2-fsa.github.io/sherpa/onnx/tts/all/English/kokoro-en-v0_19.html) |
| Sherpa-ONNX Python runtime and core 1.13.6 | Apache-2.0 for the runtime project | [Runtime](https://pypi.org/project/sherpa-onnx/1.13.6/), [core](https://pypi.org/project/sherpa-onnx-core/1.13.6/) |
| eSpeak NG phonemizer and data | eSpeak NG is GPL-3.0-or-later. Assess redistribution obligations for the particular runtime wheel and archived phonemizer data before bundling them. | [eSpeak NG](https://github.com/espeak-ng/espeak-ng), [archive build workflow](https://github.com/k2-fsa/sherpa-onnx/blob/master/.github/workflows/export-kokoro.yaml) |

The development app fetches the checksum-pinned model archive separately. Apache-2.0 permits commercial use of the published weights subject to its terms. No release claim is made about voice likeness rights, generated audio, or the redistribution of the combined app bundle.

## Phase 6 presenter components

| Component | License evidence |
|---|---|
| PocketSphinx 5.1.1 | BSD-style terms in the [versioned license](https://github.com/cmusphinx/pocketsphinx/blob/v5.1.1/LICENSE). |
| Bundled English acoustic model and pronunciation data | [Acoustic model notice](https://github.com/cmusphinx/pocketsphinx/blob/main/model/en-us/en-us/README) and [CMU dictionary license](https://github.com/cmusphinx/cmudict/blob/master/LICENSE). |
| Pillow 11.3.0 | [Historical Permission Notice and Disclaimer](https://pillow.readthedocs.io/en/stable/about.html#license). |
| FFmpeg with libx264 | The local Apple Silicon app build bundles the build host's media tools and their non-system libraries. The bundle records native input filenames and checksums. Review the exact build and its redistributable sources and notices before release. [FFmpeg license information](https://ffmpeg.org/legal.html) |
| Maya and Eli illustration presets | Original programmatically drawn art. No external portrait weights or likeness asset used. |

### Experimental MuseTalk portrait components

| Component | Published license evidence |
|---|---|
| MuseTalk 1.5 code and UNet weights | The upstream project publishes its code under MIT and permits commercial use of its model weights. [MuseTalk](https://github.com/TMElyralab/MuseTalk#license) |
| MLX port and Q4 conversion | MIT is declared in the port and distribution metadata. The app pins the exact source revision and each model file's SHA-256. [Port](https://github.com/xocialize/musetalk-mlx/tree/c6eb30ebd1ed4d043983209813370153de9346bf), [Q4 model](https://huggingface.co/mlx-community/MuseTalk-1.5-q4/tree/ff57bd4b785b3813f43be689390c3d7494258a35) |
| Converted VAE | MIT in the source model card. [SD VAE FT MSE](https://huggingface.co/stabilityai/sd-vae-ft-mse) |
| Converted Whisper audio encoder | Apache-2.0 in the source model card. [Whisper Tiny](https://huggingface.co/openai/whisper-tiny) |
| MLX | MIT. [MLX 0.30.6](https://github.com/ml-explore/mlx/blob/v0.30.6/LICENSE) |
| Eye detector shipped with OpenCV 4.12.0 | The [cascade XML header](https://github.com/opencv/opencv/blob/4.12.0/data/haarcascades/haarcascade_eye_tree_eyeglasses.xml) permits redistribution with conditions. It is loaded from the existing OpenCV wheel; preserve its notices if the wheel is bundled for release. |
| Fictional acceptance portrait | Generated with the built-in imagegen tool for testing. Uploaded user portraits are separate local assets. |

## Phase 7 scene benchmark

| Component | License and provenance |
|---|---|
| stable-diffusion.cpp | MIT. The benchmark pins the [upstream macOS arm64 release](https://github.com/leejet/stable-diffusion.cpp/releases/tag/master-969-8c442fb) and verifies its archive before extracting the CLI and shared library. |
| FLUX.2 Klein 4B and decoder | Apache-2.0 on the [base model](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) and [small decoder](https://huggingface.co/black-forest-labs/FLUX.2-small-decoder). The [pinned image bundle](https://huggingface.co/novelaide/FLUX.2-klein-4B-GGUF/tree/3e0e2b84cb9f0d52e226de8ac4fc181f26c30456) lists the upstream revisions and hashes for the decoder, denoiser conversion, and [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B) text encoder conversion. |
| Wan 2.1 T2V 1.3B | Apache-2.0 on the [base model](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B) and [quantized conversion](https://huggingface.co/off-grid-ai/Wan2.1-T2V-1.3B-GGUF/blob/1ba777b2426d5717dd0fddb1100078604a9d4667/NOTICE). The [pinned companion bundle](https://huggingface.co/off-grid-ai/Video-Companions-GGUF/tree/18b5ae5e93799acd6cd20819f898ff60f4b822d1) contains the GGUF conversion of the [UMT5-XXL](https://huggingface.co/google/umt5-xxl) text encoder and the standard VAE tested in the initial run. |
| TAEHV Wan 2.1 preview decoder | MIT on the [upstream TAEHV repository](https://github.com/madebyollin/taehv/blob/main/LICENSE). The benchmark pins the [byte-identical safetensors mirror](https://huggingface.co/ionden/taew2.1/blob/d6659884fcb62ff8d8beb15eda968b64ff70bc48/taew2_1.safetensors). |

The benchmark downloads only model data and the separately pinned engine release. Preserve upstream notices and review the release archive's bundled dependencies before distributing the engine with the app.

## Not yet audited

- All transitive dependencies and the notices required for a distributable bundle.
- Generated voice/avatar assets, fonts, music, and any additional media or model assets.
- Compatibility of any component with commercial use, redistribution, attribution, or generated-output terms.

Model code and model weights must be evaluated separately. A permissive code license does not establish that weights, voices, avatars, or generated assets may be used commercially. Marketplace commercial-use labels remain **NOT IMPLEMENTED** until those checks are complete.


## Voice and Audio Visualizer studio extension

| Component | Source code or runtime | Weights, data, and decision |
|---|---|---|
| Kokoro v0.19 int8 | Sherpa-ONNX Apache-2.0 and eSpeak NG GPL-3.0-or-later | Publisher declares Apache-2.0 for the legacy weights and voices. Use the existing pinned model archive for English. [Legacy Kokoro](https://huggingface.co/hexgrad/kLegacy/tree/main/v0.19) |
| Piper Lada x_low | The app runs the VITS ONNX voice with Sherpa-ONNX and eSpeak NG. It does not ship the Piper CLI. | Piper Voices declares MIT. The checkpoint card and Speech-UK dataset both declare Apache-2.0 for the Lada training data. Selected for Ukrainian after local generation tests. [Piper weight](https://huggingface.co/rhasspy/piper-voices/blob/main/uk/uk_UA/lada/x_low/uk_UA-lada-x_low.onnx) · [Lada dataset](https://huggingface.co/datasets/speech-uk/opentts-lada) |
| Pocket TTS January 2026 int8 Sherpa archive | Kyutai Pocket TTS MIT; Sherpa-ONNX Apache-2.0 | Kyutai lists CC BY 4.0 for its weights. The exact downloaded ONNX archive includes CC BY 4.0 license text and a README stating noncommercial use. The app applies the stricter distribution instruction, leaves this checkpoint disabled by default, and requires explicit acknowledgment for each speech request. Demo voices in the archive are excluded from the promoted install. [Kyutai weights](https://huggingface.co/kyutai/pocket-tts) · [Exact Sherpa archive and documentation](https://k2-fsa.github.io/sherpa/onnx/tts/pocket.html) |
| Pillow and NumPy | Pillow HPND and NumPy BSD-3-Clause | No inference weights. [Pillow license](https://pillow.readthedocs.io/en/stable/about.html#license) · [NumPy license](https://github.com/numpy/numpy/blob/v2.2.6/LICENSE.txt) |
| Noto Sans Regular | SIL OFL 1.1 | The original font file and license are bundled together for consistent Ukrainian glyphs in offline previews and exports. [Noto Sans Regular](https://github.com/notofonts/noto-fonts/blob/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf) · [OFL](https://github.com/notofonts/noto-fonts/blob/main/LICENSE) |
| FFmpeg and PortAudio | FFmpeg effective licensing varies by compiled libraries. PortAudio is MIT-style and SoundDevice is MIT. | Review the precise macOS FFmpeg build and corresponding sources for redistribution. [FFmpeg licensing](https://ffmpeg.org/legal.html) · [PortAudio license](https://www.portaudio.com/license.html) · [SoundDevice license](https://github.com/spatialaudio/python-sounddevice/blob/master/LICENSE) |

### Candidate review

- **Piper Oleksa high** has an Apache-2.0 checkpoint declaration and Ukrainian data attribution. Local comparison against the smaller Lada checkpoint remains pending. [Oleksa model card](https://huggingface.co/RomanStasyshyn/uk_UA-oleksa-high)
- **RADTTS-UK and ESPnet Ukrainian TTS** document Ukrainian speakers. RADTTS lists Apache-2.0 for released checkpoints. ESPnet’s project uses MIT code and documents dictionary and model-based stress. The weight manifest, accentor chain, and CPU benchmark still need acceptance before adding those options. [RADTTS](https://huggingface.co/Yehor/radtts-uk) · [Ukrainian ESPnet project](https://github.com/robinhad/ukrainian-tts)
- **HolosTTS 0.3** documents real reference conditioning and Ukrainian phonemization using a voice encoder plus synthesis weights. The code and model cards declare MIT. The current checkpoint card says its preset voices lack the owners’ consent. It distinguishes custom references with permission. The app now offers a pinned HolosTTS 0.4 custom-reference installation. The manifest downloads only the synthesis and reference-encoder checkpoints at revision `5d40fbaa99e55ebd3359a161bc86728ffdbd5c5f`, the MIT notice, and the runtime. It excludes preset voice files and their demo recordings. Inference uses the saved authorized reference and explicitly loads tensor weights on CPU. Native-listener pronunciation and speaker similarity evaluation remain open. [HolosTTS 0.3](https://pypi.org/project/holos-tts/0.3/) · [Holos checkpoint card](https://huggingface.co/patriotyk/HolosTTS)
- **XTTS v2** and **F5-TTS** remain unselected. The XTTS card lists the Coqui Public Model License and omits Ukrainian from its languages. F5’s MIT code and CC-BY-NC published weights carry different terms. OpenVoice V2 publishes MIT terms and lists six native languages. The app now uses its standalone converter with its own Ukrainian base engine. Chatterbox-Nano and MOSS-TTS-Nano remain alternative English candidates. [XTTS v2](https://huggingface.co/coqui/XTTS-v2) · [F5-TTS](https://github.com/SWivid/F5-TTS#license) · [OpenVoice](https://github.com/myshell-ai/OpenVoice#license) · [Chatterbox](https://github.com/resemble-ai/chatterbox) · [MOSS-TTS-Nano](https://github.com/OpenMOSS/MOSS-TTS-Nano)

### HolosTTS runtime notices

The pinned HolosTTS 0.4 package and model repository declare MIT. The installation uses PyTorch and TorchAudio, which carry BSD-style terms, plus checksum-pinned dependencies with their wheel notices. The Ukrainian frontend uses the MIT ipa-uk fork at `3faf05ec50fd4880965b6c76f8e479a4f82117f5` and MIT ukrainian-word-stress 2.0.0 in dictionary-only mode. The source lock is `backend/studio/holos_manifest.json`. [HolosTTS package](https://pypi.org/project/holos-tts/0.4/) · [HolosTTS model card](https://huggingface.co/patriotyk/HolosTTS) · [ipa-uk license](https://github.com/patriotyk/ipa-uk/blob/3faf05ec50fd4880965b6c76f8e479a4f82117f5/LICENSE) · [Ukrainian word stress](https://github.com/lang-uk/ukrainian-word-stress)

### OpenVoice V2 English and cross-language conversion

MyShell publishes the OpenVoice V2 code and checkpoint under MIT and explicitly permits commercial use. The app pins the converter weights and configuration to model revision `f36e7edfe1684461a8343844af60babc2efbb727` and loads only tensors from the checkpoint. It pins the converter architecture and MIT notice to source revision `74a1d147b17a8c3092dd5430504bd83ef6c7eb23`. The Models & Tools download includes the converter and its PyTorch runtime, without publisher sample voices. English synthesis uses the existing Apache-2.0 Kokoro checkpoint. Ukrainian synthesis uses Piper Lada under the terms above. Profiles continue to require permission for the reference recording. [OpenVoice license](https://github.com/myshell-ai/OpenVoice#license) · [Pinned model](https://huggingface.co/myshell-ai/OpenVoiceV2/tree/f36e7edfe1684461a8343844af60babc2efbb727)

### ZONOS3-Mini local synthesis and voice cloning

Zyphra publishes the model weights under Apache-2.0 and its reference runtime under MIT. The model card lists Ukrainian and English, documents reference-based cloning and synthesis without a reference, and links an Apple Silicon MLX runtime. The upstream preset list includes English voices. Verify preset provenance before packaging any recordings or voice embeddings. [Model card](https://huggingface.co/Zyphra/ZONOS3-Mini) · [Runtime license and setup](https://github.com/Zyphra/ZONOS3-Mini#license) · [Upstream presets](https://huggingface.co/Zyphra/ZONOS3-Mini/blob/main/gguf/voices/README.md)

The optional Models & Tools installer uses the three core weights from [model revision `15fb29d`](https://huggingface.co/Zyphra/ZONOS3-Mini/tree/15fb29d7787f7cc61d12616341710bcb1d3e9b6c/gguf). The 1,160,861,792-byte download covers speaker-embedding cloning and TTS. It excludes publisher reference voices and the extra weights for raw-reference cloning. The Apple Silicon runtime bundles upstream runtime and dependency notices. [Upstream runtime guide](https://github.com/Zyphra/ZONOS3-Mini/blob/main/servers/ggml/README.md#prepare-models)

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `backbone-ptq.gguf` | 663,150,624 | `66fceb6d73a8c30bde80249e4112bb308380ab7f3384fa11c8e4048115e4ceec` |
| `zac-keep2.gguf` | 452,325,440 | `834e5b70b88294afc3c702a478487e0664dcc6b574c16fc6167d72444aabeafd` |
| `spk-encoder.gguf` | 45,385,728 | `9539f340b70be246005403b9c254732e180e85e0b3c57fb1973f0d6bc6c59bdc` |

The three weights passed size and checksum verification. The official Metal CLI at source revision `96d9e0ec1e39083ed336ac78f24e3dbc93721c0f` generated Ukrainian and English clones on an Apple M2 Pro with 32 GB RAM. The longer Ukrainian clip took 5.37 seconds with ZONOS3-Mini and 14.07 seconds with HolosTTS. English cloning took 3.86 seconds with ZONOS3-Mini and 13.88 seconds with OpenVoice V2. These single-run timings include model loading, reference preparation, and export. Cloning comparisons used one synthetic reference per language.

At seed 42, the default reference-free Ukrainian run produced a silent WAV. Using the same seed with a −20 LUFS loudness condition and an 18 kHz bandwidth condition produced audible Ukrainian and English narration. The evaluation confirms local generation. Native-listener review of Ukrainian word stress, speaker similarity, pacing, and skipped or repeated words remains open before recommending a new default. [Runtime source revision](https://github.com/Zyphra/ZONOS3-Mini/tree/96d9e0ec1e39083ed336ac78f24e3dbc93721c0f)

OmniVoice's code is Apache-2.0. The published model card puts its weights under CC-BY-NC due to training data constraints, so that checkpoint is unsuitable for the commercial-ready catalog. [OmniVoice model license](https://huggingface.co/k2-fsa/OmniVoice#license)

## Native image checkpoints

The application pins the selected Diffusers safetensors and accompanying configuration by model repository revision, byte size, and SHA-256 in `backend/studio/image-models.json`. Runtime libraries and checkpoints are acquired separately after a user action. Generated images retain the model and license metadata.

| Candidate | Exact source and terms | App treatment |
| --- | --- | --- |
| Stable Diffusion 1.5 fp16 Diffusers | [Pinned SD1.5 mirror](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/tree/451f4fe16113bff5a5d2269ed5ad43b0592e9a14), [CreativeML Open RAIL-M](https://huggingface.co/spaces/CompVis/stable-diffusion-license) | Default local checkpoint. Commercial use is subject to the license's terms and use restrictions. |
| SDXL 1.0 Base fp16 Diffusers | [Pinned Stability AI revision](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/462165984030d82259a11f4367a4eed129e94a7b), [CreativeML Open RAIL++-M](https://github.com/Stability-AI/generative-models/blob/main/model_licenses/LICENSE-SDXL1.0) | Optional local model. Commercial use is subject to the license's terms and use restrictions. |
| Stable Diffusion 3.5 Medium | [Stability AI model card and license](https://huggingface.co/stabilityai/stable-diffusion-3.5-medium) | Uses a separate pipeline and Stability AI Community License. Outside the native image catalog. |
| FLUX.2 Klein 4B, Q4_0 | [Black Forest Labs model card](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B), [pinned quantized distribution](https://huggingface.co/novelaide/FLUX.2-klein-4B-GGUF/tree/3e0e2b84cb9f0d52e226de8ac4fc181f26c30456) | Apache 2.0 base model. Image Studio shares the pinned Apple Silicon runtime, Qwen encoder, and decoder with scene generation. |
| FLUX.1 schnell | [Black Forest Labs model card](https://huggingface.co/black-forest-labs/FLUX.1-schnell) | Apache 2.0 weights. Uses a separate pipeline and higher model memory budget. Outside the native image catalog. |

A model from another publisher, a single-file conversion, LoRA, IP-Adapter, or ControlNet can carry its own terms. The app records the license declared by the operator for manual models.
