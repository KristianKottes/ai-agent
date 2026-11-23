"""Local image model catalog, profile contracts and native inference job orchestration."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Literal, Optional
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from backend.scenes.benchmark import BenchmarkError, SceneCancelled
from backend.scenes.profiles import FLUX_REVISION
from backend.studio import flux, model_install
from backend.studio.audio import StudioError
from backend.studio.motion import Spec

LOGGER = logging.getLogger(__name__)
WORKER = Path(__file__).with_name("image_worker.py")
STYLE_PRESETS = {
    "Cinematic": "cinematic realism, carefully composed lighting",
    "Photorealistic": "photorealistic photography, natural details",
    "3D animation": "stylized 3D animation, expressive shapes",
    "Cartoon": "cartoon illustration, clean contours",
    "Anime": "anime illustration, expressive composition",
    "Watercolor": "watercolor illustration, textured paper",
    "Biblical": "biblical illustration, historically inspired setting",
    "Minimalist": "minimalist illustration, restrained palette",
    "Children's animation": "children's storybook animation, warm colors",
    "Documentary": "documentary still photography, natural light",
}
MODES = ("text", "variation", "inpaint")


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    family: str
    version: str
    license: str
    license_url: str
    model_url: str
    recommended_size: int


CATALOG = {
    "sd15": Model("sd15", "Stable Diffusion 1.5", "sd15", "451f4fe16113bff5a5d2269ed5ad43b0592e9a14",
                  "CreativeML Open RAIL-M", "https://huggingface.co/spaces/CompVis/stable-diffusion-license",
                  "https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5", 512),
    "sdxl": Model("sdxl", "Stable Diffusion XL 1.0 Base", "sdxl", "462165984030d82259a11f4367a4eed129e94a7b",
                  "CreativeML Open RAIL++-M", "https://github.com/Stability-AI/generative-models/blob/main/model_licenses/LICENSE-SDXL1.0",
                  "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0", 768),
}


@lru_cache(maxsize=1)
def manifests():
    return json.loads(Path(__file__).with_name("image-models.json").read_text(encoding="utf-8"))


class ImageRequest(Spec):
    model_id: str = Field(default="sd15", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    mode: Literal["text", "variation", "inpaint", "upscale"] = "text"
    device: Literal["auto", "mps", "cuda", "cpu"] = "auto"
    prompt: str = Field(default="", max_length=8000)
    negative: str = Field(default="", max_length=8000)
    width: int = Field(default=512, ge=256, le=1536, multiple_of=8)
    height: int = Field(default=512, ge=256, le=1536, multiple_of=8)
    steps: int = Field(default=24, ge=1, le=100)
    guidance: float = Field(default=7, ge=0, le=25)
    seed: Optional[int] = Field(default=None, ge=0, le=2**53 - 5)
    batch: int = Field(default=1, ge=1, le=4)
    strength: float = Field(default=.65, gt=0, le=1)
    reference_id: Optional[UUID] = None
    mask_id: Optional[UUID] = None
    adapter_image_id: Optional[UUID] = None
    adapter_scale: float = Field(default=.65, ge=0, le=1)
    control_image_id: Optional[UUID] = None
    control_scale: float = Field(default=.8, ge=0, le=2)
    character_id: Optional[UUID] = None
    environment_id: Optional[UUID] = None
    style_id: Optional[UUID] = None
    preset_style: Optional[str] = Field(default=None, max_length=40)
    profile_versions: dict[Literal["character", "environment", "style"], int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_mode(self):
        if self.model_id == flux.ID and "steps" not in self.model_fields_set:
            self.steps = flux.STEPS
        if (self.adapter_image_id or self.control_image_id) and self.mode != "text":
            raise ValueError("IP-Adapter and ControlNet guides are available for text-to-image.")
        if self.adapter_image_id and self.control_image_id:
            raise ValueError("Use one conditioning guide at a time.")
        if self.mode != "upscale" and not self.prompt.strip():
            raise ValueError("Enter an image prompt.")
        if self.mode != "text" and not self.reference_id:
            raise ValueError("Select a reference image.")
        if self.mode == "inpaint" and not self.mask_id:
            raise ValueError("Select a mask image. White means regenerate and black means retain.")
        if self.model_id != flux.ID and self.mode in ("inpaint", "variation") and int(self.steps * self.strength) < 1:
            raise ValueError("Increase the number of steps or image strength.")
        if self.preset_style and self.preset_style not in STYLE_PRESETS:
            raise ValueError("Choose a supported style preset.")
        return self


class ModelAction(Spec):
    model_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    action: Literal["install", "cancel", "enable", "disable", "remove", "test"]
    accepted_license: bool = False


class CustomModel(Spec):
    name: str = Field(min_length=1, max_length=100)
    family: Literal["sd15", "sdxl"]
    license: str = Field(min_length=1, max_length=260)
    variant: Optional[Literal["fp16"]] = None
    version: Optional[str] = Field(default=None, max_length=120)
    checkpoint: Optional[str] = Field(default=None, pattern=r"^[^/\\]+\.safetensors$", max_length=160)
    lora: Optional[str] = Field(default=None, pattern=r"^[^/\\]+\.safetensors$", max_length=160)
    ip_adapter: Optional[str] = Field(default=None, pattern=r"^[^/\\]+\.safetensors$", max_length=160)
    image_encoder: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    controlnet: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    control_guide: Literal["pose", "edges", "depth", "other"] = "other"

    @model_validator(mode="after")
    def paired_adapter(self):
        if bool(self.ip_adapter) != bool(self.image_encoder):
            raise ValueError("IP-Adapter needs both safetensors weights and a local image encoder.")
        return self



class Profile(Spec):
    id: UUID
    kind: Literal["character", "environment", "style"]
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1500)
    clothing: str = Field(default="", max_length=500)
    reference_ids: list[UUID] = Field(default_factory=list, max_length=12)
    approved_id: Optional[UUID] = None

    @model_validator(mode="after")
    def approved_reference(self):
        if not self.name.strip() or (self.approved_id and self.approved_id not in self.reference_ids):
            raise ValueError("Name the profile and choose an approved appearance from its references.")
        return self


class ImageDetails(Spec):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    collection: Optional[str] = Field(default=None, max_length=100)
    tags: Optional[list[str]] = Field(default=None, max_length=20)
    favorite: Optional[bool] = None
    approval: Optional[Literal["approved", "rejected", "pending"]] = None

    @model_validator(mode="after")
    def valid(self):
        if self.name is not None and not self.name.strip():
            raise ValueError("Name the image.")
        if self.tags and any(not 1 <= len(tag.strip()) <= 50 for tag in self.tags):
            raise ValueError("Use tags of 1 to 50 characters.")
        return self


def environment(data, offline=True):
    values = {**os.environ, "PYTHONNOUSERSITE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
              "PYTHONDONTWRITEBYTECODE": "1", "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1",
              "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
    dependencies = os.getenv("AI_BLOGGER_IMAGE_SITE")
    values.pop("PYTHONPATH", None)
    if dependencies or (data / "image-runtime").is_dir():
        values["PYTHONPATH"] = dependencies or str(data / "image-runtime")
    if offline:
        values.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    values.pop("HF_TOKEN", None)
    values.pop("HUGGING_FACE_HUB_TOKEN", None)
    return values


def python():
    return os.getenv("AI_BLOGGER_IMAGE_PYTHON") or sys.executable


@lru_cache(maxsize=8)
def runtime_probe(executable, search, revision):
    values = dict(os.environ)
    values.pop("PYTHONPATH", None)
    if search:
        values["PYTHONPATH"] = search
    values.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    try:
        checked = subprocess.run([executable, str(WORKER), "probe"], capture_output=True, timeout=20,
                                 text=True, env=values, check=True)
        return json.loads(checked.stdout.splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {"devices": []}


class ImageModels:
    def __init__(self, data, tools):
        self.data, self.tools = data, tools
        self.root = data / "image-models"
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.cancel = threading.Event()
        self.worker = None
        self.install = None
        self.active_model = None

    def close(self):
        self.stop.set()
        self.cancel.set()
        if self.worker:
            self.worker.join(timeout=2)

    @contextmanager
    def model_lease(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.model_root(".model.lock")
        try:
            with path.open("a+b") as lock:
                if os.name == "nt":
                    import msvcrt
                    if not lock.tell():
                        lock.write(b"0")
                        lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
        except BlockingIOError as error:
            raise StudioError("Another Studio instance is using the image models.", 409) from error

    def probe(self, refresh=False):
        search = environment(self.data).get("PYTHONPATH", "")
        if refresh:
            runtime_probe.cache_clear()
        try:
            revised = Path(search.split(os.pathsep)[0]).stat().st_mtime_ns if search else 0
        except OSError:
            revised = 0
        return runtime_probe(python(), search, revised)

    def model_root(self, model_id):
        path = self.root / model_id
        if path.resolve() != path:
            raise StudioError("The image model path is unsafe.")
        return path

    def installed(self, model_id):
        model = CATALOG[model_id]
        path = self.model_root(model_id)
        try:
            return (path / ".verified").read_text(encoding="utf-8") == model.version and all(
                (path / asset["path"]).is_file()
                and (path / asset["path"]).stat().st_size == asset["size"]
                and (path / asset["path"]).resolve().is_relative_to(path)
                for asset in manifests()[model_id]
            )
        except OSError:
            return False

    def custom(self, model_id):
        if not re.fullmatch(r"local-[a-zA-Z0-9_-]{1,50}", model_id):
            raise StudioError("Choose an available image model.", 404)
        path = self.model_root(model_id)
        try:
            metadata_file = path / "studio-model.json"
            if metadata_file.is_symlink() or metadata_file.stat().st_size > 20_000:
                raise ValueError()
            record = CustomModel.model_validate_json(metadata_file.read_text(encoding="utf-8"))
            for name in (record.checkpoint, record.lora, record.ip_adapter):
                if name and (not (path / name).is_file() or (path / name).resolve() != path / name):
                    raise ValueError()
            config = self.model_root(record.family) if record.checkpoint else path
            if record.checkpoint and not self.installed(record.family):
                raise StudioError("Install the corresponding base model first for offline checkpoint configuration.")
            self.validate_config(config, record.family)
            if record.checkpoint:
                self.validate_single_file(path / record.checkpoint)
            for component in (record.image_encoder, record.controlnet):
                if component:
                    folder = path / component
                    if folder.resolve() != folder or not (folder / "config.json").is_file():
                        raise ValueError()
                    if not any(folder.glob("*.safetensors")):
                        raise ValueError()
                    self.check_local_files(folder)
        except (ValueError, OSError) as error:
            raise StudioError("The local checkpoint directory or studio-model.json is invalid.", 422) from error
        return path, config, record

    @staticmethod
    def validate_single_file(path):
        with path.open("rb") as stream:
            prefix = stream.read(8)
            size = struct.unpack("<Q", prefix)[0] if len(prefix) == 8 else 0
            if not 2 <= size <= min(16 * 1024 * 1024, path.stat().st_size - 8):
                raise ValueError("Invalid safetensors header.")
            header = json.loads(stream.read(size))
        weight = header.get("model.diffusion_model.input_blocks.0.0.weight", {}) if isinstance(header, dict) else {}
        shape = weight.get("shape", []) if isinstance(weight, dict) else []
        if not isinstance(shape, list) or len(shape) != 4 or shape[1] != 4:
            raise ValueError("Local checkpoint must be a compatible four-channel Stable Diffusion base.")

    @staticmethod
    def validate_config(path, family):
        config_path = path / "model_index.json"
        if not config_path.is_file() or config_path.stat().st_size > 100_000 or config_path.resolve() != config_path:
            raise ValueError("Invalid local pipeline configuration.")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        expected = {"sd15": "StableDiffusionPipeline", "sdxl": "StableDiffusionXLPipeline"}[family]
        allowed = {
            "scheduler": {"PNDMScheduler", "DDIMScheduler", "EulerDiscreteScheduler", "EulerAncestralDiscreteScheduler",
                          "DPMSolverMultistepScheduler", "LMSDiscreteScheduler", "UniPCMultistepScheduler"},
            "text_encoder": {"CLIPTextModel"}, "text_encoder_2": {"CLIPTextModelWithProjection"},
            "tokenizer": {"CLIPTokenizer", "CLIPTokenizerFast"}, "tokenizer_2": {"CLIPTokenizer", "CLIPTokenizerFast"},
            "unet": {"UNet2DConditionModel"}, "vae": {"AutoencoderKL"},
            "feature_extractor": {"CLIPImageProcessor", "CLIPFeatureExtractor"},
            "safety_checker": {"StableDiffusionSafetyChecker"}, "image_encoder": {"CLIPVisionModelWithProjection"},
        }
        if config.get("_class_name") != expected:
            raise ValueError("Local checkpoint does not match the selected pipeline family.")
        for key, value in config.items():
            if key.startswith("_") or isinstance(value, (bool, type(None))):
                continue
            if (not isinstance(value, list) or len(value) != 2
                    or (value != [None, None] and (value[0] not in ("diffusers", "transformers", "stable_diffusion")
                        or value[1] not in allowed.get(key, set())))):
                raise ValueError("Local model uses an unsupported component.")
        ImageModels.check_local_files(path)
        unet = path / "unet" / "config.json"
        if not unet.is_file() or unet.stat().st_size > 100_000:
            raise ValueError("Missing UNet configuration.")
        if json.loads(unet.read_text(encoding="utf-8")).get("in_channels") != 4:
            raise ValueError("Use a compatible four-channel Stable Diffusion base model.")

    @staticmethod
    def check_local_files(path):
        for file in path.rglob("*"):
            if file.is_symlink() or file.suffix in (".py", ".bin", ".pt", ".pth", ".ckpt"):
                raise ValueError("Use a local safetensors-only model directory without symbolic links.")

    def models(self):
        result = []
        for model in CATALOG.values():
            enabled, health = self.tools.preference("image-" + model.id)
            result.append({**model.__dict__, "installed": self.installed(model.id), "enabled": enabled, "health": health,
                           "estimated_bytes": sum(asset["size"] for asset in manifests()[model.id]),
                           "provider": "diffusers", "modes": list(MODES), "negative": True,
                           "memory_bytes": (8 if model.family == "sd15" else 16) * 1024**3})
        enabled, health = self.tools.preference("image-" + flux.ID)
        result.append({"id": flux.ID, "name": flux.NAME, "version": FLUX_REVISION,
                       "license": "Apache-2.0", "license_url": flux.MODEL_URL, "model_url": flux.MODEL_URL,
                       "installed": flux.installed(self.data / "scene_models"), "enabled": enabled, "health": health,
                       "provider": "metal", "available": flux.available(), "modes": ["text", "variation"],
                       "negative": False, "guidance": False, "strength": False, "max_batch": 1,
                       "dimension_step": 16, "default_steps": flux.STEPS, "recommended_size": 768,
                       "estimated_bytes": sum(part.size for part in flux.ARTIFACTS)})
        for path in sorted(self.root.glob("local-*")) if self.root.is_dir() else ():
            try:
                root, _, record = self.custom(path.name)
            except StudioError:
                continue
            enabled, health = self.tools.preference("image-" + path.name)
            result.append({"id": path.name, "name": record.name, "license": record.license,
                           "version": record.version or str((root / (record.checkpoint or "model_index.json")).stat().st_mtime_ns),
                           "ip_adapter": bool(record.ip_adapter), "lora": bool(record.lora),
                           "control_guide": record.control_guide if record.controlnet else None,
                           "installed": True, "enabled": enabled, "health": health, "modes": list(MODES),
                           "negative": True, "provider": "local_checkpoint", "recommended_size": CATALOG[record.family].recommended_size})
        return result

    def snapshot(self):
        probe = self.probe()
        with self.lock:
            install = dict(self.install) if self.install else None
        return {"runtime_ready": bool(probe["devices"]), "device": probe["devices"], "torch": probe.get("torch"),
                "memory_bytes": probe.get("memory_bytes"),
                "runtime_message": "" if probe["devices"] else "Set up the isolated Python image runtime to generate images.",
                "models": self.models(), "styles": STYLE_PRESETS, "install": install, "directory": str(self.root),
                "runtime_installable": sys.version_info[:2] == (3, 12) and not (
                    os.getenv("AI_BLOGGER_IMAGE_PYTHON") or os.getenv("AI_BLOGGER_IMAGE_SITE"))}

    def action(self, payload):
        id, action = payload.model_id, payload.action
        if id == "native-runtime":
            return self.runtime_action(action)
        if id == flux.ID:
            return self.flux_action(payload)
        model = CATALOG.get(id)
        if not model:
            self.custom(id)
        key = "image-" + id
        with self.lock:
            enabled, _ = self.tools.preference(key)
            if action in ("enable", "disable"):
                self.tools.set_preference(key, action == "enable", "")
            elif action == "cancel":
                if self.worker and self.worker.is_alive() and self.install["model_id"] == id:
                    self.cancel.set()
            elif action == "test":
                probe = self.probe(refresh=True)
                if not probe["devices"]:
                    raise StudioError("The local Diffusers runtime could not load. Check its Python environment.", 503)
                if model and not self.installed(id):
                    raise StudioError("Install the model before validating its files.")
                if model:
                    if self.active_model or self.worker and self.worker.is_alive():
                        raise StudioError("Wait for the current image model job to finish.", 409)
                    self.start_model_task(model, "validate")
                else:
                    self.tools.set_preference(key, enabled, f"Configuration validated · PyTorch {probe['torch']}")
            elif action in ("install", "remove"):
                if not model:
                    raise StudioError("Manage manually added checkpoints in the local model directory.")
                if self.active_model or self.worker and self.worker.is_alive():
                    raise StudioError("Wait for the current image model operation to finish.", 409)
                if action == "remove":
                    path = self.model_root(id)
                    with self.model_lease():
                        if path.is_dir():
                            shutil.rmtree(path)
                    self.tools.set_preference(key, enabled, "")
                elif not payload.accepted_license:
                    raise StudioError("Review and accept this checkpoint’s license before downloading.")
                elif not self.installed(id):
                    self.start_model_task(model, "install")
        return self.snapshot()

    def flux_action(self, payload):
        if not flux.available():
            raise StudioError("This FLUX runtime requires Apple Silicon and macOS 26 or later.", 503)
        action = payload.action
        key = "image-" + flux.ID
        with self.lock:
            if action in ("enable", "disable"):
                self.tools.set_preference(key, action == "enable", "")
            elif action == "cancel":
                if self.worker and self.worker.is_alive() and self.install["model_id"] == flux.ID:
                    self.cancel.set()
            elif action in ("install", "test"):
                if action == "install" and not payload.accepted_license:
                    raise StudioError("Review and accept the FLUX model terms before downloading.")
                if action == "test" and not flux.installed(self.data / "scene_models"):
                    raise StudioError("Install FLUX before validating its files.")
                if self.active_model or (self.worker and self.worker.is_alive()):
                    raise StudioError("Wait for the current image model operation to finish.", 409)
                self.start_model_operation(flux.ID, action == "install", self.prepare_flux, action == "install")
            else:
                raise StudioError("FLUX files are shared with scene generation.")
        return self.snapshot()

    def prepare_flux(self, download):
        def progress(value):
            with self.lock:
                self.install.update(progress=value)

        try:
            flux.prepare(self.data / "scene_models", self.tools.connect, download, self.cancel, progress)
            enabled, _ = self.tools.preference("image-" + flux.ID)
            self.tools.set_preference("image-" + flux.ID, enabled, "Pinned model and Metal engine verified")
            status, message = "completed", "FLUX is ready on Metal"
        except SceneCancelled:
            status, message = "cancelled", "Cancelled. A partial download can be resumed."
        except (BenchmarkError, OSError) as error:
            status = "failed"
            message = str(error) if isinstance(error, BenchmarkError) else "Could not prepare FLUX. Check your connection and free disk space."
        with self.lock:
            self.install.update(state=status, message=message)

    def runtime_action(self, action):
        with self.lock:
            if action == "cancel":
                if self.worker and self.worker.is_alive() and self.install["model_id"] == "native-runtime":
                    self.cancel.set()
            elif action == "test":
                self.probe(refresh=True)
            elif action == "install":
                if sys.version_info[:2] != (3, 12) or os.getenv("AI_BLOGGER_IMAGE_PYTHON") or os.getenv("AI_BLOGGER_IMAGE_SITE"):
                    raise StudioError("Use the packaged Python 3.12 app or configure an external image environment.")
                if self.active_model or (self.worker and self.worker.is_alive()):
                    raise StudioError("Wait for the current image model operation to finish.", 409)
                self.cancel.clear()
                self.install = {"model_id": "native-runtime", "state": "downloading", "progress": None,
                                "message": "Installing the isolated Python environment"}
                self.worker = threading.Thread(target=self.leased_task, args=(self.install_runtime,), daemon=True, name="image-runtime")
                self.worker.start()
            else:
                raise StudioError("Unsupported image runtime action.")
        return self.snapshot()

    def install_runtime(self):
        target = self.data / "image-runtime"
        requirements = WORKER.parent.parent / "requirements-image.txt"
        try:
            if target.resolve() != target:
                raise StudioError("The image runtime directory is unsafe.")
            with TemporaryDirectory(prefix="image-install-", dir=self.data) as temporary:
                work = Path(temporary)
                stage = work / "packages"
                log = work / "pip.log"
                command = [python(), "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
                           "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--no-cache-dir",
                           "--target", str(stage), "-r", str(requirements)]
                values = environment(self.data, offline=False)
                values.pop("PYTHONPATH", None)
                with log.open("wb") as output, subprocess.Popen(command, env=values, stdout=output,
                                                               stderr=subprocess.STDOUT) as process:
                    try:
                        deadline = time.monotonic() + 1800
                        while process.poll() is None:
                            if self.cancel.wait(.3):
                                raise model_install.InstallCancelled()
                            if time.monotonic() > deadline:
                                raise StudioError("The runtime installation timed out.")
                        if process.returncode:
                            LOGGER.warning("Image environment installation failed: %s", log.read_text(errors="replace")[-4000:])
                            raise StudioError("The image runtime could not be installed. Check your connection and Python platform.")
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                if not runtime_probe(python(), str(stage), stage.stat().st_mtime_ns)["devices"]:
                    raise StudioError("The installed image environment could not load PyTorch and Diffusers.")
                with self.lock:
                    if self.cancel.is_set():
                        raise model_install.InstallCancelled()
                    backup = work / "previous"
                    if target.is_dir():
                        os.replace(target, backup)
                    try:
                        os.replace(stage, target)
                    except OSError:
                        if backup.is_dir():
                            os.replace(backup, target)
                        raise
                    runtime_probe.cache_clear()
            status, message = "completed", "Native image runtime is ready"
        except model_install.InstallCancelled:
            status, message = "cancelled", "Runtime installation cancelled"
        except Exception as error:
            LOGGER.exception("Native image runtime installation failed")
            status, message = "failed", str(error) if isinstance(error, StudioError) else "Could not install the native image runtime."
        with self.lock:
            self.install.update(state=status, message=message)

    def leased_task(self, callback, *args):
        try:
            with self.model_lease():
                callback(*args)
        except (StudioError, OSError) as error:
            LOGGER.warning("Unable to acquire the image model lease: %s", error)
            with self.lock:
                self.install.update(state="failed", message="The model directory is in use or unavailable.")

    def start_model_task(self, model, action):
        self.start_model_operation(model.id, action == "install", self.model_task, model, action)

    def start_model_operation(self, model_id, download, callback, *args):
        self.cancel.clear()
        self.install = {"model_id": model_id, "state": "downloading" if download else "validating",
                        "progress": 0, "message": "Preparing local model"}
        self.worker = threading.Thread(target=self.leased_task, args=(callback, *args), daemon=True, name="image-model")
        self.worker.start()

    def model_task(self, model, action):
        from backend.runtime.downloader import sha256_file

        stage = self.model_root("." + model.id + "-partial")
        target = self.model_root(model.id)
        files = manifests()[model.id]
        total = sum(asset["size"] for asset in files)
        complete = 0

        def progress(bytes_done, message):
            with self.lock:
                self.install.update(progress=(complete + bytes_done) / total, message=message)

        try:
            if action == "validate":
                for asset in files:
                    if self.cancel.is_set():
                        raise model_install.InstallCancelled()
                    if sha256_file(target / asset["path"]) != asset["sha256"]:
                        (target / ".verified").unlink(missing_ok=True)
                        raise StudioError("A model file failed checksum verification. Remove and reinstall the model.")
                    complete += asset["size"]
                    progress(0, "Verifying model files")
            else:
                stage.mkdir(parents=True, exist_ok=True, mode=0o700)
                for asset in files:
                    name = PurePosixPath(asset["path"])
                    output = stage.joinpath(*name.parts)
                    if output.resolve() != output:
                        raise StudioError("A model file destination is unsafe.")
                    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    model_install.download(asset, output, lambda size: progress(size, "Downloading " + name.name),
                                           self.cancel.is_set)
                    complete += asset["size"]
                with self.lock:
                    if self.cancel.is_set():
                        raise model_install.InstallCancelled()
                    marker = stage / ".verified"
                    if marker.is_symlink():
                        raise StudioError("The model verification path is unsafe.")
                    marker.write_text(model.version, encoding="utf-8")
                    if target.is_dir():
                        shutil.rmtree(target)
                    os.replace(stage, target)
            enabled, _ = self.tools.preference("image-" + model.id)
            self.tools.set_preference("image-" + model.id, enabled, "Pinned model files verified")
            status, message = "completed", "Model files verified"
        except model_install.InstallCancelled:
            status, message = "cancelled", "Cancelled. A partial download can be resumed."
        except Exception as error:
            LOGGER.exception("Image model operation failed for %s", model.id)
            status, message = "failed", str(error) if isinstance(error, StudioError) else "The image model operation failed. Check your connection and free disk space."
        with self.lock:
            self.install.update(state=status, message=message)

    def model_spec(self, model_id):
        if model_id == flux.ID:
            if not flux.available() or not flux.installed(self.data / "scene_models"):
                raise StudioError("Install FLUX on a compatible Mac in Models & Tools.", 409)
            return {"id": flux.ID, "family": "flux2", "version": FLUX_REVISION,
                    "license": "Apache-2.0", "license_url": flux.MODEL_URL}
        if model_id in CATALOG:
            model = CATALOG[model_id]
            if not self.installed(model_id):
                raise StudioError("Install the selected image model in Models & Tools.", 409)
            return {"id": model_id, "family": model.family, "version": model.version, "license": model.license,
                    "license_url": model.license_url, "path": str(self.model_root(model_id)), "variant": "fp16"}
        root, config, custom = self.custom(model_id)
        from backend.runtime.downloader import sha256_file
        version = sha256_file(root / custom.checkpoint) if custom.checkpoint else (
            custom.version or str((root / "model_index.json").stat().st_mtime_ns))
        return {"id": model_id, "family": custom.family, "version": version, "license": custom.license,
                "path": str(config), "variant": custom.variant,
                "checkpoint": str(root / custom.checkpoint) if custom.checkpoint else None,
                "lora": str(root / custom.lora) if custom.lora else None,
                "ip_adapter": str(root / custom.ip_adapter) if custom.ip_adapter else None,
                "image_encoder": str(root / custom.image_encoder) if custom.image_encoder else None,
                "controlnet": str(root / custom.controlnet) if custom.controlnet else None}

    @contextmanager
    def inference(self, model_id):
        with self.lock:
            if not self.tools.preference("image-" + model_id)[0]:
                raise StudioError("Enable the selected image model in Models & Tools.")
            if self.worker and self.worker.is_alive():
                raise StudioError("Wait for the image model download or validation to finish.", 409)
            self.active_model = model_id
        try:
            with self.model_lease():
                yield self.model_spec(model_id)
        finally:
            with self.lock:
                self.active_model = None


def validate_request(studio, project, request):
    for id in (request.reference_id, request.mask_id, request.adapter_image_id, request.control_image_id):
        if id:
            studio.asset_path(project, str(id), "image")
    for field, kind in (("character_id", "character"), ("environment_id", "environment"), ("style_id", "style")):
        id = getattr(request, field)
        revision = request.profile_versions.get(kind)
        if revision is not None and (not id or revision < 1):
            raise StudioError("Choose a valid saved profile version.")
        if id and studio.image_profile(project, str(id), revision)["kind"] != kind:
            raise StudioError(f"Choose a {kind} profile.")
    if request.mode == "upscale":
        return
    if request.mode == "inpaint":
        reference = studio.asset(project, str(request.reference_id), "image")
        mask = studio.asset(project, str(request.mask_id), "image")
        if (reference.get("width"), reference.get("height")) != (mask.get("width"), mask.get("height")):
            raise StudioError("Upload a mask with the same dimensions as the reference image.")
    if not studio.images.tools.preference("image-" + request.model_id)[0]:
        raise StudioError("Enable the selected image model in Models & Tools.")
    if request.model_id == flux.ID:
        studio.images.model_spec(flux.ID)
        if request.device not in ("auto", "mps"):
            raise StudioError("Select Metal or Automatic for FLUX.")
        if request.mode not in ("text", "variation") or request.adapter_image_id or request.control_image_id:
            raise StudioError("This FLUX model supports text to image and generation from a reference image.")
        if request.width % 16 or request.height % 16:
            raise StudioError("Choose FLUX dimensions that are multiples of 16 pixels.")
        if request.batch != 1 or request.negative.strip():
            raise StudioError("This FLUX model generates one image per job and uses a positive prompt.")
        return
    if request.model_id in CATALOG:
        if request.adapter_image_id or request.control_image_id:
            raise StudioError("Choose a locally configured model with the matching image guide.")
        if not studio.images.installed(request.model_id):
            raise StudioError("Install the selected image model in Models & Tools.", 409)
    else:
        _, _, model = studio.images.custom(request.model_id)
        if (request.adapter_image_id and not model.ip_adapter) or (request.control_image_id and not model.controlnet):
            raise StudioError("The selected checkpoint does not have the requested image conditioner.")
    devices = studio.images.probe()["devices"]
    if not devices:
        raise StudioError("The native image runtime is unavailable. See Models & Tools.", 503)
    if request.device != "auto" and request.device not in devices:
        raise StudioError("The requested image device is unavailable.")


def run_worker(studio, id, work, payload):
    (work / "request.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with (work / "worker.log").open("wb") as errors:
        try:
            with subprocess.Popen([python(), str(WORKER), str(work)], env=environment(studio.data),
                                  stdout=subprocess.DEVNULL, stderr=errors) as process:
                deadline = time.monotonic() + 3600
                try:
                    last = None
                    progress = work / "progress.json"
                    while process.poll() is None:
                        studio.check()
                        if time.monotonic() > deadline:
                            raise StudioError("The native image generation exceeded one hour.")
                        if progress.is_file():
                            current = json.loads(progress.read_text(encoding="utf-8"))
                            if current != last:
                                studio.update(id, "running", .05 + .86 * current["progress"], current["message"])
                                last = current
                        studio.stop.wait(.25)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
        except OSError as error:
            raise StudioError("The native image Python process could not start.", 503) from error
    if process.returncode:
        path = work / "error.json"
        LOGGER.warning("Image job %s exited %s: %s", id, process.returncode,
                       (work / "worker.log").read_text(errors="replace")[-4500:])
        raise StudioError(json.loads(path.read_text())["message"] if path.is_file()
                          else "The image process stopped unexpectedly. Try a smaller model or CPU.", 502)
    return json.loads((work / "result.json").read_text(encoding="utf-8"))


def generate(studio, project, id, request, work):
    from PIL import Image

    studio.check()
    generation = request.model_dump(mode="json")
    seed = secrets.randbits(52) if request.seed is None else request.seed
    generation["seed"] = seed if request.mode != "upscale" else None
    reference = studio.asset_path(project, str(request.reference_id), "image") if request.reference_id else None
    mask = studio.asset_path(project, str(request.mask_id), "image") if request.mask_id else None
    adapter = studio.asset_path(project, str(request.adapter_image_id), "image") if request.adapter_image_id else None
    control = studio.asset_path(project, str(request.control_image_id), "image") if request.control_image_id else None
    profiles = []
    for kind in ("character", "environment", "style"):
        profile_id = getattr(request, kind + "_id")
        if profile_id:
            profiles.append(studio.image_profile(project, str(profile_id), request.profile_versions.get(kind)))
    generation["profile_versions"] = {record["kind"]: record["version"] for record in profiles}
    if request.mode == "upscale":
        with Image.open(reference) as image:
            if image.width * image.height * 4 > 32_000_000:
                raise StudioError("This image is too large to upscale 2× within the 32 megapixel asset limit.")
            image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS).save(work / "result-00.png")
        original = studio.asset(project, str(request.reference_id))
        model = {"id": "lanczos", "version": "1", "license": original.get("license", "")}
        execution, batch = {"device": "cpu"}, 1
    else:
        prompt = ", ".join(filter(None, [request.prompt.strip(),
                            *(part.strip() for profile in profiles if profile["kind"] != "style"
                              for part in (profile["description"], profile["clothing"]))]))
        style_prompt = ", ".join(filter(None, [STYLE_PRESETS.get(request.preset_style, ""),
                                  *(profile["description"].strip() for profile in profiles if profile["kind"] == "style")]))
        with studio.images.inference(request.model_id) as model:
            if model["id"] == flux.ID:
                generation["guidance"] = 1.0
                if "\0" in prompt or "\0" in style_prompt:
                    raise StudioError("Remove null characters from the prompt.")
                flux_prompt = ", ".join(filter(None, [style_prompt, prompt]))
                execution = flux.generate(studio, id, work, request, flux_prompt, seed, reference)
            else:
                execution = run_worker(studio, id, work, {"model": model, "request": {
                    **generation, "prompt": prompt, "style_prompt": style_prompt,
                    "reference": str(reference) if reference else None,
                    "mask": str(mask) if mask else None,
                    "adapter_image": str(adapter) if adapter else None,
                    "control_image": str(control) if control else None,
                }})
        batch = request.batch

    records = []
    for index in range(batch):
        studio.check()
        upscale = request.mode == "upscale"
        name = ("Upscaled " + original["name"][:78]) if upscale else (
            request.prompt.strip()[:88] + (f" ({index + 1})" if batch > 1 else ""))
        output_seed = seed + index if not upscale else None
        saved_input = {**generation, "seed": output_seed, "batch": 1}
        records.append((id if index == 0 else str(uuid5(UUID(id), str(index))), name,
                        work / f"result-{index:02d}.png", {
            "source_type": "generated", "generation": saved_input, **({"seed": output_seed} if not upscale else {}),
            "model_id": model["id"], "model_version": model["version"], "license": model["license"],
            "license_url": model.get("license_url"), "inference": execution,
            "reference_ids": [str(asset) for asset in (
                request.reference_id, request.mask_id, request.adapter_image_id, request.control_image_id) if asset],
            "profiles": [{"id": p["id"], "version": p["version"]} for p in profiles],
        }))
    studio.update(id, "running", .93, "Saving original images and thumbnails")
    studio.publish_generated_images(project, records, work)
