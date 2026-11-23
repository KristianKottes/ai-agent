"""Manage the studio components alongside the existing local catalog."""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import sys
import threading
import urllib.request
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from tempfile import TemporaryDirectory
from pathlib import Path
from datetime import datetime, timezone

from backend.runtime.downloader import sha256_file
from backend.studio import holos, model_install, openvoice, zonos
from backend.studio.audio import StudioError
from backend.studio.speech import (
    LADA, OPENVOICE_BASE, PACKAGES, PINNED_MODELS, POCKET, PROVIDERS, SpeechRequest, worker_command, worker_environment,
)
from backend.voice.service import MODEL_BYTES, MODEL_ID, MODEL_LICENSE_URL, _extract_package, _runtime
from backend.avatar.service import _binary

LOGGER = logging.getLogger(__name__)
SAMPLES = {"en": "A clear voice makes a good story easier to follow.",
           "uk": "Доброго ранку! Сьогодні на нас чекає чудовий день."}
METADATA = {
    MODEL_ID: ("Kokoro English", "0.19 int8", "Apache-2.0", MODEL_LICENSE_URL, MODEL_BYTES),
    LADA: ("Piper Lada", "x_low", "MIT weights · Apache-2.0 training dataset",
           "https://huggingface.co/rhasspy/piper-voices/blob/main/uk/uk_UA/lada/x_low/MODEL_CARD", PACKAGES[LADA][0]),
    holos.MODEL_ID: ("HolosTTS Ukrainian", "0.4", "MIT · authorized custom recordings",
                     holos.MODEL_LICENSE_URL, None),
    openvoice.MODEL_ID: ("OpenVoice V2", "2", "MIT · authorized custom recordings",
                        openvoice.MODEL_LICENSE_URL, None),
    POCKET: ("Pocket TTS English", "2026-01-26 int8", "CC BY 4.0 in archive · distribution README restricts use to noncommercial",
             "https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models", PACKAGES[POCKET][0]),
    zonos.MODEL_ID: ("ZONOS3-Mini", "PTQ", "Apache-2.0 model weights", zonos.MODEL_LICENSE_URL, None),
}
TOOLS = {"audio-analysis": ("Audio Analysis", "Audio Processing Tools", "NumPy", "BSD-3-Clause"),
         "motion-engine": ("Pillow Motion Engine", "Motion Graphics Engines", "Pillow", "HPND · OFL-1.1 font"),
         "ffmpeg": ("FFmpeg", "Rendering Tools", None, "Build-dependent LGPL/GPL")}


class StudioTools:
    def __init__(self, voice, connect):
        self.voice, self.connect = voice, connect
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.states = {}
        self.worker = None

    def path(self, model):
        return self.voice.root / model

    def installed(self, model):
        if model == MODEL_ID:
            return self.voice.status()["state"] == "installed"
        root = self.path(model)
        files = PINNED_MODELS[model].FILES if model in PINNED_MODELS else PACKAGES[model][2]
        return not self.voice.root.is_symlink() and not root.is_symlink() and all(
            (root / file).is_file() and (root / file).resolve().is_relative_to(root.resolve())
            for file in files
        )

    def preference(self, key):
        with self.connect() as db:
            row = db.execute("SELECT enabled, health FROM studio_tools WHERE id=?", (key,)).fetchone()
        return (bool(row["enabled"]), row["health"]) if row else (key != POCKET, "")

    def set_preference(self, key, enabled, health):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO studio_tools VALUES (?, ?, ?)", (key, int(enabled), health))

    @staticmethod
    def runtime_available(model=None):
        if model in PINNED_MODELS and not model_install.compatible_host():
            return False
        if model == holos.MODEL_ID:
            return True
        if model == zonos.MODEL_ID:
            return zonos.binary_directory() is not None
        if platform.system() not in ("Darwin", "Windows", "Linux"):
            return False
        try:
            _runtime()
            return True
        except RuntimeError:
            return False

    def base_languages(self, model):
        if model != openvoice.MODEL_ID:
            return list(PROVIDERS[model].languages)
        return [language for language, (base, _) in OPENVOICE_BASE.items()
                if self.preference(base)[0] and self.installed(base)]

    def ready_languages(self, model):
        if not self.runtime_available(model) or not self.preference(model)[0] or not self.installed(model):
            return []
        return self.base_languages(model)

    @staticmethod
    def model_size(model):
        if model in PINNED_MODELS:
            return model_install.download_bytes(PINNED_MODELS[model].MANIFEST_PATH)
        return MODEL_BYTES if model == MODEL_ID else PACKAGES[model][0]

    def snapshot(self):
        sherpa_available = self.runtime_available()
        verified = self.voice.platform_verified()
        with self.lock:
            entries = []
            kokoro = self.voice.status()
            for model, (name, release, license, url, size) in METADATA.items():
                provider = PROVIDERS[model]
                pinned = model in PINNED_MODELS
                native = model == zonos.MODEL_ID
                available = self.runtime_available(model) if pinned else sherpa_available
                size = self.model_size(model) if pinned else size
                enabled, health = self.preference(model)
                installed = self.installed(model)
                ready = self.base_languages(model) if installed and enabled and available else []
                state, downloaded, message = self.states.get(model, ("available", 0, ""))
                if model == MODEL_ID:
                    state, downloaded, message = kokoro["state"], kokoro["downloaded_bytes"], kokoro["message"]
                entries.append(dict(id=model, name=name, version=release, category="Voice Cloning Models"
                                    if provider.reference_conditioned else "TTS Models",
                                    languages=list(provider.languages), reference_languages=list(provider.reference_languages),
                                    ready_languages=ready, license=license, license_url=url,
                                    required_models={language: METADATA[base][0] for language, (base, _) in OPENVOICE_BASE.items()}
                                    if model == openvoice.MODEL_ID else {},
                                    runtime_license=("Zyphra runtime MIT" if native else "PyTorch BSD-3-Clause" if pinned else "Sherpa-ONNX Apache-2.0"
                                        + (" · eSpeak NG GPL-3.0-or-later" if model != POCKET else "")),
                                    download_bytes=size, downloaded_bytes=size if installed else downloaded,
                                    disk_bytes=size * (3 if pinned else 4),
                                    backend="Metal" if native else "CPU", platform=("Apple Silicon · macOS 14+ · Python 3.12" if pinned
                                        else "Apple Silicon tested" if verified else "Platform acceptance pending"),
                                    default_sample_rate=44_100 if native else 24_000,
                                    features=(["Reference-conditioned speech", "Multi-recording profiles", "Generated narration"] if native
                                              else ["Reference-conditioned speech", "Multi-recording profiles", "Speed"] if provider.multi_reference
                                              else ["Reference-conditioned speech", "Speed"] if provider.reference_conditioned else ["Speed"]),
                                    state="installed" if installed else state, enabled=enabled, health=health,
                                    research_only=provider.research_only,
                                    ram_bytes={MODEL_ID: 820_000_000, LADA: 350_000_000, POCKET: 1_024_000_000,
                                               holos.MODEL_ID: 3_000_000_000, openvoice.MODEL_ID: 2_000_000_000,
                                               zonos.MODEL_ID: 4_000_000_000}[model],
                                    compatible=available, ready=bool(ready),
                                    compatibility_message=((model_install.host_requirement() or
                                        ("Open the macOS build with the bundled ZONOS3 runtime." if native and not available else "")) if pinned
                                        else "The pinned Sherpa-ONNX runtime is unavailable on this device."),
                                    message=message, voices=[dict(id=i, name=n, locale=("uk-UA" if model == LADA
                                         else "" if native else "en-US" if i.startswith("a") else "en-GB")) for i, n in provider.voices]))
            for key, (name, category, package, license) in TOOLS.items():
                enabled, health = self.preference(key)
                try:
                    release = version(package) if package else "System or bundled"
                    available = bool(package or _binary("ffmpeg"))
                except PackageNotFoundError:
                    release, available = "", False
                entries.append(dict(id=key, name=name, category=category, version=release, license=license,
                                    languages=[], state="bundled" if available else "missing", enabled=enabled,
                                    compatible=available, ready=available and enabled, health=health, voices=[]))
        return entries

    def require(self, *keys):
        for key in keys:
            if not self.preference(key)[0]:
                raise StudioError(f"{TOOLS[key][0]} is disabled in Models & Tools.", 409)

    @contextmanager
    def inference(self, model):
        # Coordinate with the existing script narration path and release its cached engine.
        with self.voice.synthesis:
            if not self.preference(model)[0] or not self.installed(model):
                raise StudioError("Enable and install this voice model in Models & Tools.", 409)
            if not self.runtime_available(model):
                raise StudioError("This voice model requires a compatible local runtime. See Models & Tools.", 503)
            self.voice.engine = None
            yield self.path(model)

    def action(self, key, request):
        if key not in METADATA and key not in TOOLS:
            raise StudioError("This component has no available actions.", 404)
        action = request.action
        if action == "install":
            if key not in METADATA or not request.accepted_license:
                raise StudioError("Review and accept the model license before downloading.")
            if key == MODEL_ID:
                if not self.runtime_available():
                    raise StudioError("Install the studio’s pinned Sherpa-ONNX runtime.", 503)
                self.voice.install(True, allow_unverified=True)
            else:
                self.install(key)
        elif action == "cancel":
            if key == MODEL_ID:
                self.voice.cancel_install()
            elif self.worker and self.worker.is_alive() and self.states.get(key, ("",))[0] == "downloading":
                self.cancel.set()
        elif action in ("enable", "disable"):
            _, health = self.preference(key)
            enabled = action == "enable"
            if enabled and key == POCKET and not request.accepted_license:
                raise StudioError("Review the checkpoint terms before enabling noncommercial evaluation.")
            self.set_preference(key, enabled, health)
            if key == MODEL_ID:
                self.voice.enabled = enabled
        elif action == "remove":
            if key not in METADATA:
                raise StudioError("Bundled tools can be disabled from this page.")
            with self.lock, self.voice.lock:
                busy = (self.worker and self.worker.is_alive()) or (self.voice.worker and self.voice.worker.is_alive())
                if busy or not self.voice.synthesis.acquire(blocking=False):
                    raise StudioError("Wait for the active model operation to finish.", 409)
                try:
                    self.voice.engine = None
                    self.voice._prepare(self.voice.root)
                    with TemporaryDirectory(prefix=".remove-", dir=self.voice.root) as directory:
                        root = self.path(key)
                        removed = root.rename(f"{directory}/model") if root.is_dir() and not root.is_symlink() else None
                        try:
                            with self.connect() as db:
                                db.execute("DELETE FROM managed_files WHERE root_key='voice' AND owner_id=?", (key,))
                                db.execute("DELETE FROM studio_tools WHERE id=?", (key,))
                        except Exception:
                            if removed:
                                removed.rename(root)
                            raise
                    self.states.pop(key, None)
                    if key == MODEL_ID:
                        self.voice.enabled = True
                finally:
                    self.voice.synthesis.release()
        elif action == "test":
            self.test(key)
        return self.snapshot()

    def test(self, key):
        if key in TOOLS:
            self.require(key)
            if key == "ffmpeg":
                binary = _binary("ffmpeg")
                if not binary:
                    raise StudioError("FFmpeg is missing.", 503)
                result = subprocess.run([binary, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10, check=True)
                health = "Available encoders: " + ", ".join(codec for codec in ("libx264", "libvpx-vp9", "aac", "libopus", "libmp3lame") if codec in result.stdout)
            elif key == "audio-analysis":
                import numpy as np
                assert np.fft.rfft([1, 0, 0, 0]).size == 3
                health = "FFT passed"
            else:
                from backend.studio.motion import font
                if not font(24).getbbox("Українська"):
                    raise StudioError("Could not load the Cyrillic font.", 503)
                health = "Cyrillic font loaded"
        elif key == zonos.MODEL_ID:
            with self.inference(key) as path:
                for language in PROVIDERS[key].languages:
                    self.probe(key, path, language=language)
            health = "Ukrainian and English narration passed"
        elif PROVIDERS[key].reference_conditioned:
            languages = self.base_languages(key)
            if not languages:
                raise StudioError("Install and enable Kokoro or Piper Lada before testing OpenVoice.", 409)
            allowed = PROVIDERS[key].reference_languages
            with self.inference(key) as path:
                with self.connect() as db:
                    profiles = db.execute("SELECT id, language, metadata_json FROM studio_profiles ORDER BY id").fetchall()
                for profile in profiles:
                    reference = self.voice.data / "studio" / "profiles" / profile["id"] / "reference.wav"
                    if (profile["language"] in allowed and json.loads(profile["metadata_json"]).get("authorization_confirmed") is True
                            and reference.is_file() and reference.resolve() == reference):
                        break
                else:
                    raise StudioError("Create an authorized voice profile for one of the model’s reference languages.")
                for language in languages:
                    self.probe(key, path, reference, language=language)
            health = "Reference synthesis passed"
        else:
            with self.inference(key) as path:
                self.probe(key, path)
            health = "Sample synthesis passed"
        self.set_preference(key, self.preference(key)[0], health)

    def probe(self, model, model_dir, reference=None, *, language=None):
        provider = PROVIDERS[model]
        language = language or provider.languages[0]
        voice = provider.voices[0][0] if provider.voices else "reference"
        request = SpeechRequest(text=SAMPLES[language], language=language, voice=voice, model_id=model,
                                noncommercial_confirmed=model == POCKET)
        with TemporaryDirectory(dir=self.voice.root) as directory:
            work = Path(directory)
            command = worker_command(model_dir, request, work, reference)
            result = subprocess.run(command, capture_output=True, timeout=80,
                                    env=worker_environment(model))
            if result.returncode or not (work / "audio.wav").is_file():
                failure = work / "error.txt"
                if failure.is_file():
                    raise StudioError(failure.read_text(encoding="utf-8"), 502)
                LOGGER.warning("Model test failed for %s: %s", model, result.stderr.decode(errors="replace")[-1800:])
                raise StudioError("The model could not synthesize the local test sentence.", 502)

    def install(self, key):
        if not self.runtime_available(key):
            raise StudioError("This voice model requires a compatible local runtime. See Models & Tools.", 503)
        with self.lock:
            if self.installed(key):
                return
            if self.worker and self.worker.is_alive():
                raise StudioError("A model installation is already running.", 409)
            self.voice._prepare(self.voice.root)
            size = self.model_size(key)
            required_space = size * (3 if key in PINNED_MODELS else 4)
            if shutil.disk_usage(self.voice.root).free < required_space:
                raise StudioError("Free more space before installing this checkpoint.", 507)
            self.cancel.clear()
            self.states[key] = ("downloading", 0, "")
            self.worker = threading.Thread(target=self._install, args=(key,), daemon=True)
            self.worker.start()

    def _install(self, key):
        try:
            if key in PINNED_MODELS:
                self._install_pinned(key)
                return
            size, digest, files = PACKAGES[key]
            with TemporaryDirectory(prefix=".speech-", dir=self.voice.root) as directory:
                root = Path(directory)
                archive = root / "checkpoint.tar.bz2"
                url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{key}.tar.bz2"
                request = urllib.request.Request(url, headers={"User-Agent": "AI-Blogger-Studio"})
                with urllib.request.urlopen(request, timeout=20) as source, archive.open("xb") as output:
                    while True:
                        if self.cancel.is_set():
                            return
                        block = source.read(1024*1024)
                        if not block:
                            break
                        if output.tell() + len(block) > size:
                            raise StudioError("The model archive exceeded its pinned size.", 502)
                        output.write(block)
                        with self.lock:
                            self.states[key] = ("downloading", output.tell(), "")
                if self.cancel.is_set():
                    return
                with self.lock:
                    self.states[key] = ("installing", size, "Checking model files…")
                if archive.stat().st_size != size or sha256_file(archive) != digest:
                    raise StudioError("The model download failed its size or checksum check.", 502)
                _extract_package(archive, root, model_id=key, limit=size*4)
                model = root / key
                if not all((model / file).is_file() for file in files):
                    raise StudioError("The model package is incomplete.", 502)
                if key == LADA:
                    with self.voice.synthesis:
                        self.probe(key, model)
                # Retain the distribution notices and omit bundled demonstration voices.
                demo = model / "test_wavs"
                if demo.is_dir():
                    shutil.rmtree(demo)
                self._promote(key, model, "Sample synthesis passed" if key == LADA else "Archive checksum verified")
                with self.lock:
                    self.states[key] = ("installed", size, "")
        except model_install.InstallCancelled:
            pass
        except Exception as error:
            LOGGER.exception("Studio model installation failed")
            with self.lock:
                self.states[key] = ("failed", 0, str(error) if isinstance(error, StudioError) else "Installation failed. Check your connection and retry.")
        finally:
            if self.cancel.is_set():
                with self.lock:
                    self.states[key] = ("cancelled", 0, "")

    def _install_pinned(self, key):
        package = PINNED_MODELS[key]
        size = self.model_size(key)
        # Keep the Holos cache name for installations interrupted in earlier builds.
        cache = self.voice.root / (".holos-downloads" if key == holos.MODEL_ID else f".{key}-downloads")
        self.voice._prepare(cache)

        def progress(received):
            with self.lock:
                self.states[key] = ("downloading", received, "")

        def unpacking():
            with self.lock:
                self.states[key] = ("installing", size, "Verifying and testing the local runtime…")

        with TemporaryDirectory(prefix=".speech-runtime-", dir=self.voice.root) as directory:
            model = Path(directory) / key
            model_install.stage(package.MANIFEST_PATH, cache, model, progress, unpacking, self.cancel.is_set)
            result = subprocess.run([sys.executable, "-B", "-m", package.__name__, str(model)],
                                    capture_output=True, timeout=80, env=worker_environment(key))
            if result.returncode:
                LOGGER.warning("%s runtime check failed: %s", key, result.stderr.decode(errors="replace")[-1800:])
                raise StudioError("The model runtime did not pass its offline check.", 502)
            if self.cancel.is_set():
                raise model_install.InstallCancelled()
            self._promote(key, model, "Offline runtime checked. Test with an authorized voice profile.")
            shutil.rmtree(cache, ignore_errors=True)
            with self.lock:
                self.states[key] = ("installed", size, "")

    def _promote(self, key, model, health):
        entries = [("voice", f"{key}/{file.relative_to(model).as_posix()}", "voice_model", key,
                    sha256_file(file), file.stat().st_size, datetime.now(timezone.utc).isoformat())
                   for file in model.rglob("*") if file.is_file()]
        with self.connect() as db:
            db.executemany(
                """INSERT OR REPLACE INTO managed_files
                   (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""", entries,
            )
            db.execute("INSERT OR REPLACE INTO studio_tools VALUES (?, ?, ?)", (key, int(key != POCKET), health))
            model.rename(self.path(key))
            try:
                db.commit()
            except Exception:
                self.path(key).rename(model)
                raise
