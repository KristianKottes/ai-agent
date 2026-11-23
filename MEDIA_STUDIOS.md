# Native Image Studio and Video Creator

## Repository audit and architecture

The existing React screens use an allowlisted Tauri bridge to reach the authenticated FastAPI sidecar. SQLite owns projects, speech and render jobs, and media metadata. The Studio service stores files privately under each project and already publishes measured sentence cues with generated speech. HolosTTS, OpenVoice, other speech models, and the Pillow motion engine remain available through their current entry points. The script-linked scene generator has a separate Metal pipeline for its legacy workflows. FFmpeg and FFprobe power the video exports.

Both new screens use the existing project IDs, assets, and Studio queue. Video Creator has its own versioned scene document, stored against the project with optimistic revision checks. Preview and export consume the same timeline. FFmpeg renders cached clips, transitions, audio and captions, followed by FFprobe validation. Image generation runs one isolated Python worker per job. Diffusers loads safetensors from a local directory or a compatible single-file checkpoint, uses MPS or CUDA when available, and supports an explicit CPU mode. Workers unload on exit and publish their output to the existing library. Model downloads are explicit and run separately from offline inference.

## Implementation order

1. Connect shared media imports, timeline persistence, and FFmpeg preview and export to the desktop editor.
2. Connect the existing narration and caption assets.
3. Add the native Diffusers runtime, model catalog, model acquisition, and Image Studio.
4. Test persistence, media streams, cancellation, and offline inference.

## Use the studios

1. Open **Video Creator**, choose an existing project or create one, and import images. Each imported image creates a scene. Existing project images are available in the scene inspector.
2. Set scene lengths, motion, and the incoming transition. Drag scene blocks to reorder them. A transition overlaps both adjacent scenes and reduces the final duration by the overlap.
3. In **Narration**, select a language and an installed voice. Assign a saved HolosTTS profile in **Voice Cloning** first. Generate narration, then choose **Attach and fit scene** to use measured speech duration and captions. You can also attach an existing recording. Fit a longer recording, split the scene, or explicitly enable trimming.
4. Import music or effects and position the clips. Use the clip trim, fades, volume, mute, solo, or looping controls. Enable ducking to reduce music during narration. Captions can be edited per scene, taken from measured speech cues, or imported from a UTF-8 SRT or WebVTT file.
5. Preview a scene or the full timeline. Choose a preset or custom dimensions, 24/25/30/60 FPS, and the MP4 or MOV container. Render the project and use **Save to Downloads**. Rendered files and subtitle downloads use collision-safe names. SRT, ASS, and WebVTT can be saved for any captioned export.

**Plan scenes from a script** turns paragraphs into editable scenes with suggested lengths, prompts, and narration. The scene's **Open Image Studio** action carries its prompt to Image Studio. **Add to video** assigns the selected result back to that scene.

For AI images, open **Models & Tools → Native image models**, install the Python image runtime, then review and accept the terms for a checkpoint before downloading it. The default is Stable Diffusion 1.5 at 512×512. SDXL starts at 768×768. **Check runtime** reports the available devices. Presets, seeds, batch size, and guidance are sent to the native pipeline. Inpainting accepts a same-sized black-and-white mask. **Upscale 2×** uses Lanczos resizing and is available without a checkpoint.

**FLUX.2 Klein 4B** is available in **Models & Tools → Native image models** on Apple Silicon with macOS 26 or later. Downloading it uses about 5.24 GB, including the pinned Metal runtime, and reuses any matching files already installed for scene generation. It runs locally with the shared quantized model. Choose **Text to image** or **Image to image** with one reference. The default is four steps, and both image dimensions must be multiples of 16. FLUX uses fixed guidance and produces one image per job. The prompt describes the change for image-to-image.

Generated PNGs and thumbnails are registered as project images. Image metadata records the actual seed, source image IDs, pinned model revision, and selected profile versions. Character, environment, and styling profiles can be edited independently. When you select a style, Image Studio reserves part of the prompt for its description. Choose one saved style image and select **Use as image reference** to start an image-to-image generation. With a compatible IP-Adapter configured, **Use as appearance guide** applies the selected image in text-to-image mode. Generation and asset operations stay on the device after installation.

## Optional local checkpoints and adapters

The app discovers folders named `local-*` under the image model directory shown in **Models & Tools**. Use a safetensors-only Stable Diffusion 1.5 or SDXL base pipeline. Set `variant` to `fp16` if the pipeline uses fp16 variant filenames. Supply the checkpoint's actual license and a version. For an original single-file Stable Diffusion checkpoint, put the safetensors file in the folder, add `checkpoint` to the manifest, and install the matching managed base model for its offline configuration.

An example `studio-model.json` for a Diffusers pipeline follows.

```json
{
  "name": "My local SDXL model",
  "family": "sdxl",
  "version": "2026-11",
  "license": "CreativeML Open RAIL++-M",
  "variant": "fp16"
}
```

Optional manifest keys accept local paths inside that folder.

| Key | Value |
| --- | --- |
| `checkpoint` | Compatible original `.safetensors` file |
| `lora` | A compatible LoRA `.safetensors` file to apply to this model |
| `ip_adapter` and `image_encoder` | IP-Adapter safetensors filename and matching CLIP vision encoder directory |
| `controlnet` and `control_guide` | A compatible ControlNet Diffusers directory with a safetensors checkpoint, and one of `pose`, `edges`, `depth`, or `other` |

IP-Adapter and ControlNet each take a user-selected guide in text-to-image mode. Pose, edge, and depth maps are supplied as image assets. Use matching adapter files and record their license terms with the local model. **Refresh models** rereads this directory.

## Runtime and verification

The packaged Mac runtime uses Python 3.12. Development setups can install `backend/requirements-image.txt` into a separate Python 3.12 virtual environment and set `AI_BLOGGER_IMAGE_PYTHON` to its interpreter before opening the desktop app. An existing isolated package directory can instead be supplied through `AI_BLOGGER_IMAGE_SITE`. The base FastAPI sidecar and its other features retain their own dependencies.

The Studio queue serializes inference and renders. Model installation and inference use a filesystem lease. Image jobs use a fresh subprocess, report steps, and release model memory at exit. MPS is selected automatically on a compatible Apple Silicon runtime. CPU is always selectable on a working runtime. Codec availability follows the installed FFmpeg build. Rendering uses a CPU pipeline with a bounded scene cache.

Run the primary verification with `backend/.venv/bin/python -m pytest` and the desktop build with `npm --prefix apps/desktop run build`. The opt-in tests in `tests/test_media_studios.py` use `AI_BLOGGER_TEST_NATIVE_IMAGES` for the directory containing the installed image runtime and SD1.5 model. `AI_BLOGGER_STUDIO_TEST_MODELS` points at an existing voice model directory. Run those checks with the Python 3.12 app dependencies. Set `AI_BLOGGER_TEST_FLUX_CACHE` to a verified scene model cache to run the FLUX acceptance test with Metal access.

The video tests exercise a 30-second transition edit, burned-in Cyrillic captions, measured music attenuation, recovery, cancellation, retry, and 1080×1920 MOV. On the Apple M2 Pro with 32 GB unified memory, Stable Diffusion 1.5 passed text-to-image, image-to-image and masked inpainting on CPU and Metal with PyTorch 2.10.0. SDXL Base passed 1024×576 text-to-image on Metal using its built-in float32 decode. The worker rejects non-finite SDXL output before PNG conversion. A saved HolosTTS profile produced Ukrainian narration and timed captions for a vertical export. The installed FFmpeg 8.0.1 and app-bundled media tools both passed the render checks. SDXL image-to-image and inpainting, third-party single-file checkpoints, adapters, and other GPU platforms require separate compatibility testing.
