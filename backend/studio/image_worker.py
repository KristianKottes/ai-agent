"""Isolated PyTorch/Diffusers process. Inference uses only local safetensors."""

import gc
import json
import sys
from pathlib import Path


class InvalidPixels(ValueError):
    pass


def sdxl_image(processor, pixels):
    import numpy as np

    # Converting NaNs to 8-bit pixels can silently turn a failed decode black.
    if not np.isfinite(pixels).all():
        raise InvalidPixels("SDXL could not decode the image. Try again or select CPU in the Device menu.")
    return processor.numpy_to_pil(pixels)[0]


def prompt_with_style(tokenizer, prompt, style):
    if not style:
        return prompt
    # Leave most of CLIP's context for the shot while ensuring that a long
    # subject prompt cannot push the saved style beyond the truncation point.
    budget = (tokenizer.model_max_length - tokenizer.num_special_tokens_to_add()) // 3
    tokens = tokenizer.encode(style, add_special_tokens=False, truncation=True, max_length=budget)
    prefix = tokenizer.decode(tokens, skip_special_tokens=True).strip()
    return f"{prefix}, {prompt}" if prefix else prompt


class DiffusersProvider:
    def __init__(self, model, device):
        import torch
        self.torch = torch
        self.model = model
        devices = available_devices(torch)
        self.device = devices[0] if device == "auto" else device
        if self.device not in devices:
            raise ValueError(f"{self.device.upper()} is unavailable in this Python runtime.")
        self.pipeline = None

    def load_model(self, request):
        import diffusers

        mode = request["mode"]

        sd15 = self.model["family"] == "sd15"
        prefix = "StableDiffusion" if sd15 else "StableDiffusionXL"
        suffix = {"text": "", "variation": "Img2Img", "inpaint": "Inpaint"}[mode]
        base = getattr(diffusers, prefix + "Pipeline")
        cls = getattr(diffusers, prefix + suffix + "Pipeline")
        dtype = self.torch.float32 if self.device == "cpu" else self.torch.float16
        options = {"local_files_only": True, "torch_dtype": dtype}
        if self.model.get("checkpoint"):
            components = {}
            if sd15:
                from diffusers.pipelines.stable_diffusion.safety_checker import StableDiffusionSafetyChecker

                checker = Path(self.model["path"]) / "safety_checker"
                components["safety_checker"] = StableDiffusionSafetyChecker.from_pretrained(
                    str(checker), variant="fp16", use_safetensors=True, **options)
            pipeline = base.from_single_file(self.model["checkpoint"], config=self.model["path"], **options, **components)
            if cls is not base:
                pipeline = cls.from_pipe(pipeline)
        else:
            variant = self.model.get("variant")
            pipeline = cls.from_pretrained(self.model["path"], variant=variant, use_safetensors=True, **options)
        if request.get("control_image"):
            path = self.model.get("controlnet")
            if not path:
                raise ValueError("The selected model has no ControlNet model.")
            guide = diffusers.ControlNetModel.from_pretrained(path, use_safetensors=True, **options)
            control_cls = getattr(diffusers, prefix + "ControlNetPipeline")
            pipeline = control_cls.from_pipe(pipeline, controlnet=guide)
        if request.get("adapter_image"):
            from transformers import CLIPImageProcessor, CLIPVisionModelWithProjection

            adapter = Path(self.model["ip_adapter"])
            encoder = CLIPVisionModelWithProjection.from_pretrained(
                self.model["image_encoder"], use_safetensors=True, **options)
            pipeline.register_modules(image_encoder=encoder, feature_extractor=CLIPImageProcessor())
            pipeline.load_ip_adapter(str(adapter.parent), weight_name=adapter.name, image_encoder_folder=None,
                                     use_safetensors=True, local_files_only=True)
            pipeline.set_ip_adapter_scale(request["adapter_scale"])
        pipeline.set_progress_bar_config(disable=True)
        # SDXL upcasts both the VAE and its latent input at decode time when
        # force_upcast is enabled. Casting only the VAE here would make the
        # pipeline cast it back to the latents' fp16 dtype on Metal.
        self.pipeline = pipeline.to(self.device)
        if self.model.get("lora"):
            adapter = Path(self.model["lora"])
            self.pipeline.load_lora_weights(str(adapter.parent), weight_name=adapter.name,
                                            use_safetensors=True, local_files_only=True)

    def generate(self, request, work, update):
        from PIL import Image, ImageOps

        width, height = request["width"], request["height"]
        image_args = {}
        mode = request["mode"]
        if mode in ("variation", "inpaint"):
            with Image.open(request["reference"]) as source:
                image_args["image"] = ImageOps.fit(source.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
            image_args["strength"] = request["strength"]
        if mode == "inpaint":
            with Image.open(request["mask"]) as source:
                image_args["mask_image"] = ImageOps.fit(source.convert("L"), (width, height), method=Image.Resampling.NEAREST)
            image_args["width"], image_args["height"] = width, height
        if mode == "text":
            image_args["width"], image_args["height"] = width, height
            if request.get("adapter_image"):
                with Image.open(request["adapter_image"]) as source:
                    image_args["ip_adapter_image"] = source.convert("RGB")
            if request.get("control_image"):
                with Image.open(request["control_image"]) as source:
                    image_args["image"] = ImageOps.fit(source.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
                image_args["controlnet_conditioning_scale"] = request["control_scale"]
        total = request["batch"]
        steps = request["steps"]
        effective_steps = steps if mode == "text" else max(1, int(steps * request["strength"]))
        is_sdxl = self.model["family"] == "sdxl"
        style = request.get("style_prompt", "")
        tokenizer = self.pipeline.tokenizer or (self.pipeline.tokenizer_2 if is_sdxl else None)
        prompt = prompt_with_style(tokenizer, request["prompt"], style)
        if is_sdxl and style:
            image_args["prompt_2"] = prompt_with_style(self.pipeline.tokenizer_2, request["prompt"], style)

        for index in range(total):
            generator = self.torch.Generator(device="cpu").manual_seed(request["seed"] + index)

            def progress(_, step, timestep, args):
                update((index + min(1, (step + 1) / effective_steps)) / total, "Generating image")
                return args

            with self.torch.inference_mode():
                result = self.pipeline(
                    prompt=prompt, negative_prompt=request["negative"] or None,
                    num_inference_steps=steps, guidance_scale=request["guidance"], generator=generator,
                    callback_on_step_end=progress, output_type="np" if is_sdxl else "pil", **image_args,
                )
            image = sdxl_image(self.pipeline.image_processor, result.images) if is_sdxl else result.images[0]
            image.save(work / f"result-{index:02d}.png")
            update((index + 1) / total, "Writing image")

    def unload_model(self):
        self.pipeline = None
        gc.collect()
        if self.device == "mps":
            self.torch.mps.empty_cache()
        elif self.device == "cuda":
            self.torch.cuda.empty_cache()


def available_devices(torch):
    devices = []
    if torch.cuda.is_available():
        devices.append("cuda")
    if torch.backends.mps.is_available():
        devices.append("mps")
    return devices + ["cpu"]


def write_json(path, value):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)


def run(work):
    payload = json.loads((work / "request.json").read_text(encoding="utf-8"))
    request = payload["request"]

    def update(value, message):
        write_json(work / "progress.json", {"progress": value, "message": message})

    provider = DiffusersProvider(payload["model"], request["device"])
    provider.torch.set_num_threads(4)
    try:
        update(0, "Loading image model")
        provider.load_model(request)
        provider.generate(request, work, update)
        write_json(work / "result.json", {"device": provider.device, "torch": provider.torch.__version__})
    finally:
        provider.unload_model()


if __name__ == "__main__":
    if sys.argv[1] == "probe":
        import psutil
        import torch
        from diffusers import StableDiffusionPipeline, StableDiffusionXLInpaintPipeline
        print(json.dumps({"devices": available_devices(torch), "torch": torch.__version__,
                          "memory_bytes": psutil.virtual_memory().total}))
    else:
        work = Path(sys.argv[1])
        try:
            run(work)
        except Exception as error:
            message = str(error).lower()
            if "out of memory" in message or "allocation failed" in message or isinstance(error, MemoryError):
                user_message = "The image model ran out of memory. Try a smaller resolution or a smaller checkpoint."
            elif isinstance(error, InvalidPixels) or (isinstance(error, ValueError) and "is unavailable" in message):
                user_message = str(error)
            elif isinstance(error, (ImportError, ModuleNotFoundError)):
                user_message = "The native runtime is incomplete. Check its Python dependencies in Models & Tools."
            else:
                user_message = "The native image worker could not run this checkpoint. Check the model files and device, or select CPU."
            write_json(work / "error.json", {"message": user_message})
            raise
