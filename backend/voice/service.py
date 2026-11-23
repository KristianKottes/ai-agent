"""Install a pinned Kokoro package and render local PCM narration."""

from __future__ import annotations

import array
import hashlib
import logging
import math
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import threading
import urllib.error
import urllib.request
import uuid
import wave
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from backend.runtime.downloader import sha256_file

MODEL_ID = "kokoro-int8-en-v0_19"
MODEL_BYTES = 103_248_205
MODEL_SHA256 = "c9f0dd393615805b0bab050c340834d5e684e732aec91c0e860cd30e982c08bd"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{MODEL_ID}.tar.bz2"
MODEL_LICENSE_URL = "https://huggingface.co/hexgrad/kLegacy/tree/main/v0.19"
SAMPLE_RATE = 24_000
VOICE_NAMES = (
    ("af", "American blend"), ("af_bella", "Bella"), ("af_nicole", "Nicole"),
    ("af_sarah", "Sarah"), ("af_sky", "Sky"), ("am_adam", "Adam"),
    ("am_michael", "Michael"), ("bf_emma", "Emma"), ("bf_isabella", "Isabella"),
    ("bm_george", "George"), ("bm_lewis", "Lewis"),
)
PREVIEW_TEXT = "Every good story starts with a question. What could change if you looked at an ordinary day a little differently?"
MODEL_FILES = ("model.int8.onnx", "voices.bin", "tokens.txt", "LICENSE")
LOGGER = logging.getLogger(__name__)


class VoiceError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 409):
        super().__init__(detail)
        self.status_code = status_code


@lru_cache(maxsize=1)
def _runtime():
    try:
        if version("sherpa-onnx") != "1.13.6" or version("sherpa-onnx-core") != "1.13.6":
            raise ImportError("The selected Kokoro package requires the pinned phonemizer.")
        import sherpa_onnx
    except (ImportError, OSError, PackageNotFoundError) as error:
        raise VoiceError("Install the pinned Sherpa-ONNX packages in the backend environment.", 503) from error
    return sherpa_onnx


def narration_chunks(text: str, limit: int = 240):
    """Prefer sentence boundaries and keep longer sentences within the model budget."""
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        remaining = " ".join(paragraph.split())
        while len(remaining) > limit:
            window = remaining[:limit + 1]
            sentence = list(re.finditer(r'[.!?]["”\']?\s+', window))
            boundary = sentence[-1].end() - 1 if sentence else window.rfind(" ")
            if boundary <= 0:
                boundary = limit
            yield remaining[:boundary].strip()
            remaining = remaining[boundary:].lstrip()
        if remaining:
            yield remaining


def _engine(model_dir: Path):
    runtime = _runtime()
    config = runtime.OfflineTtsConfig(
        model=runtime.OfflineTtsModelConfig(
            kokoro=runtime.OfflineTtsKokoroModelConfig(
                model=str(model_dir / "model.int8.onnx"),
                voices=str(model_dir / "voices.bin"),
                tokens=str(model_dir / "tokens.txt"),
                data_dir=str(model_dir / "espeak-ng-data"),
            ),
            provider="cpu",
            num_threads=min(4, os.cpu_count() or 1),
        )
    )
    if not config.validate():
        raise VoiceError("The Kokoro model files failed the runtime configuration check.", 503)
    return runtime.OfflineTts(config)


def _render(engine, text: str, sid: int, speed: float, destination) -> float:
    samples = array.array("f")
    gap = array.array("f", [0.0]) * (SAMPLE_RATE * 120 // 1_000)
    for chunk in narration_chunks(text):
        audio = engine.generate(chunk, sid=sid, speed=speed)
        section = audio.samples
        if audio.sample_rate != SAMPLE_RATE or len(section) == 0:
            raise VoiceError("Kokoro did not produce valid 24 kHz speech for a section.", 502)
        if samples:
            samples.extend(gap)
        samples.extend(section)

    if not samples or not all(map(math.isfinite, samples)):
        raise VoiceError("Kokoro returned empty or invalid speech.", 502)
    peak = max(map(abs, samples))
    if peak < 0.0001:
        raise VoiceError("Kokoro returned silent audio.", 502)
    gain = (32767 * 10 ** (-1 / 20)) / peak

    with wave.open(destination, "wb") as wav:
        wav.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for offset in range(0, len(samples), SAMPLE_RATE * 10):
            pcm = array.array("h", (round(value * gain) for value in samples[offset:offset + SAMPLE_RATE * 10]))
            if sys.byteorder != "little":
                pcm.byteswap()
            wav.writeframesraw(pcm.tobytes())
    return round(len(samples) / SAMPLE_RATE, 3)


def _extract_package(archive: Path, staging: Path, model_id=MODEL_ID, limit=230_000_000):
    remaining = limit
    with tarfile.open(archive, "r:bz2") as package:
        for member in package:
            name = PurePosixPath(member.name)
            if not name.parts or name.parts[0] != model_id or ".." in name.parts:
                raise VoiceError("The voice package contains an unexpected path.", 502)
            if "\\" in member.name or ":" in member.name or not (member.isdir() or member.isfile()):
                raise VoiceError("The voice package contains an unsupported entry.", 502)
            target = staging.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            remaining -= member.size
            if remaining < 0:
                raise VoiceError("The extracted voice package exceeded its size limit.", 502)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with package.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


class VoiceService:
    def __init__(self, data: Path, database: Path):
        self.data = data
        self.database = database
        self.root = data / "voice"
        self.model_dir = self.root / MODEL_ID
        self.partial = self.root / f"{MODEL_ID}.tar.bz2.part"
        self.narration_dir = self.root / "narration"
        self.lock = threading.RLock()
        self.synthesis = threading.Lock()
        self.cancel = threading.Event()
        self.worker = None
        self.state = "available"
        self.message = ""
        self.downloaded = self._partial_bytes()
        self.engine = None
        self.enabled = True

    @staticmethod
    def platform_verified() -> bool:
        return platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64")

    def _prepare(self, path: Path):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink() or not path.resolve().is_relative_to(self.data.resolve()):
            raise VoiceError("The application voice directory is unsafe.", 500)

    def _installed(self) -> bool:
        return (
            not self.root.is_symlink() and not self.model_dir.is_symlink()
            and all((self.model_dir / name).is_file() and not (self.model_dir / name).is_symlink()
                    for name in MODEL_FILES)
            and not (self.model_dir / "espeak-ng-data").is_symlink()
            and (self.model_dir / "espeak-ng-data" / "phondata").is_file()
        )

    def _partial_bytes(self) -> int:
        if self.root.is_symlink() or self.partial.is_symlink() or not self.partial.is_file():
            return 0
        try:
            return self.partial.stat().st_size
        except FileNotFoundError:
            return 0

    def status(self):
        try:
            _runtime()
            runtime_ready = True
        except VoiceError:
            runtime_ready = False
        supported = self.platform_verified()
        with self.lock:
            installed = self._installed()
            state = "available" if self.state == "installed" and not installed else self.state
            return {
                "state": "installed" if installed else state,
                "ready": installed and supported and runtime_ready and self.enabled,
                "enabled": self.enabled,
                "platform_verified": supported,
                "runtime_installed": runtime_ready,
                "download_bytes": MODEL_BYTES,
                "downloaded_bytes": MODEL_BYTES if installed else self.downloaded,
                "message": self.message,
                "license_url": MODEL_LICENSE_URL,
                "voices": [
                    {"id": voice, "name": name, "accent": "American English" if voice[0] == "a" else "British English"}
                    for voice, name in VOICE_NAMES
                ],
            }

    def install(self, accepted_license: bool, *, allow_unverified=False):
        if not accepted_license:
            raise VoiceError("Review and accept the Kokoro model and voice license first.", 422)
        if not self.platform_verified() and not allow_unverified:
            raise VoiceError("Voice synthesis is currently verified on Apple Silicon Macs.")
        _runtime()
        with self.lock:
            if self._installed() or (self.worker and self.worker.is_alive()):
                return self.status()
            self._prepare(self.root)
            if self.model_dir.exists() or self.model_dir.is_symlink():
                raise VoiceError("An incomplete Kokoro directory already exists. Check the application’s voice directory.")
            if shutil.disk_usage(self.root).free < 350_000_000:
                raise VoiceError("Free at least 350 MB of space before installing Kokoro.")
            self.downloaded = self._partial_bytes()
            self.message = "Downloading the Kokoro voice package…"
            self.state = "downloading"
            self.cancel.clear()
            self.worker = threading.Thread(target=self._install_worker, name="kokoro-install", daemon=True)
            self.worker.start()
            return self.status()

    def cancel_install(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                self.cancel.set()
                self.message = "Cancelling installation…"
            return self.status()

    def _install_worker(self):
        try:
            with TemporaryDirectory(prefix=".kokoro-", dir=self.root) as work:
                staging = Path(work)
                self._download()
                self._check_cancelled()
                with self.lock:
                    self.state = "installing"
                    self.message = "Checking the voice package and generating a short sample…"
                _extract_package(self.partial, staging)
                model = staging / MODEL_ID
                # eSpeak keeps the initial data directory for the life of its process.
                # Probe staged files in a child so synthesis can open the promoted path.
                result = subprocess.run(
                    [sys.executable, "-m", "backend.voice.probe", str(model), str(staging / "check.wav")],
                    capture_output=True, timeout=60, check=False,
                )
                if result.returncode != 0:
                    raise VoiceError("Kokoro could not generate speech with the downloaded voice package.", 502)
                files = [path for path in model.rglob("*") if path.is_file()]
                entries = [
                    ("voice", f"{MODEL_ID}/{path.relative_to(model).as_posix()}", "voice_model", MODEL_ID,
                     sha256_file(path), path.stat().st_size, datetime.now(timezone.utc).isoformat())
                    for path in files
                ]
                self._check_cancelled()
                with self.lock, closing(sqlite3.connect(self.database)) as connection, connection:
                    self._check_cancelled()
                    connection.executemany(
                        """INSERT OR REPLACE INTO managed_files
                           (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""", entries
                    )
                    model.rename(self.model_dir)
                    try:
                        connection.commit()
                    except sqlite3.Error:
                        self.model_dir.rename(model)
                        raise
                    self.state = "installed"
                    self.message = "Kokoro is ready for offline narration."
                self.partial.unlink(missing_ok=True)
        except Exception as error:
            if not self.cancel.is_set():
                LOGGER.exception("Kokoro installation failed")
            with self.lock:
                self.state = "cancelled" if self.cancel.is_set() else "failed"
                self.message = (
                    "Installation cancelled. Retry to resume the download." if self.cancel.is_set()
                    else str(error) if isinstance(error, VoiceError)
                    else "Could not install the Kokoro package. Check your connection and available space, then retry."
                )

    def _download(self):
        if self.partial.is_symlink() or (self.partial.exists() and not self.partial.is_file()):
            raise VoiceError("The partial voice download path is unsafe.", 500)
        offset = self._partial_bytes()
        if offset > MODEL_BYTES:
            self.partial.unlink()
            offset = 0
        if offset < MODEL_BYTES:
            headers = {"User-Agent": "AI-Blogger-Studio"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(MODEL_URL, headers=headers)
            try:
                source = urllib.request.urlopen(request, timeout=20)
            except urllib.error.HTTPError as error:
                if error.code == 416 and offset:
                    error.close()
                    self.partial.unlink()
                    return self._download()
                raise
            with source:
                if source.status == 200:
                    offset = 0
                elif source.status != 206 or source.headers.get("Content-Range") != f"bytes {offset}-{MODEL_BYTES - 1}/{MODEL_BYTES}":
                    raise VoiceError("The download server sent an unexpected byte range.", 502)
                with self.partial.open("ab" if offset else "wb") as target:
                    while True:
                        self._check_cancelled()
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        if target.tell() + len(block) > MODEL_BYTES:
                            raise VoiceError("The Kokoro package exceeded its pinned download size.", 502)
                        target.write(block)
                        with self.lock:
                            self.downloaded = target.tell()
        if self._partial_bytes() != MODEL_BYTES:
            raise VoiceError("The download ended early. Retry to resume it.", 502)
        if sha256_file(self.partial) != MODEL_SHA256:
            self.partial.unlink()
            with self.lock:
                self.downloaded = 0
            raise VoiceError("The Kokoro package failed its pinned checksum. Retry to download a fresh copy.", 502)

    def _check_cancelled(self):
        if self.cancel.is_set():
            raise VoiceError("Installation cancelled.")

    def render(self, text: str, voice_id: str, speed: float, destination) -> float:
        if not self.status()["ready"]:
            raise VoiceError("Install the local voice model before generating speech.")
        try:
            sid = next(index for index, (voice, _) in enumerate(VOICE_NAMES) if voice == voice_id)
        except StopIteration as error:
            raise VoiceError("Choose an available English voice.", 422) from error
        if not self.synthesis.acquire(blocking=False):
            raise VoiceError("Another voice request is running. Try again when it finishes.")
        try:
            if self.engine is None:
                self.engine = _engine(self.model_dir)
            return _render(self.engine, text, sid, speed, destination)
        except (OSError, RuntimeError) as error:
            if isinstance(error, VoiceError):
                raise
            raise VoiceError("The local voice engine could not generate this narration.", 502) from error
        finally:
            self.synthesis.release()

    def save_narration(self, script_id: str, text: str, voice_id: str, speed: float, *, target_seconds=None):
        self._prepare(self.root)
        self._prepare(self.narration_dir)
        narration_id = str(uuid.uuid4())
        filename = f"{narration_id}.wav"
        destination = self.narration_dir / filename
        with TemporaryDirectory(prefix=".render-", dir=self.narration_dir) as work:
            staged = Path(work) / filename
            duration = self.render(text, voice_id, speed, str(staged))
            if target_seconds is not None:
                if abs(duration - target_seconds) > 3:
                    adjusted_speed = max(0.75, min(1.5, speed * duration / target_seconds))
                    if adjusted_speed != speed:
                        speed = adjusted_speed
                        duration = self.render(text, voice_id, speed, str(staged))
                if abs(duration - target_seconds) > 6:
                    raise VoiceError(f"Narration measured {duration:.0f} seconds. Use a script that can be spoken in "
                                     f"about {target_seconds} seconds and start a new job.", 422)
            created_at = datetime.now(timezone.utc).isoformat()
            size = staged.stat().st_size
            digest = sha256_file(staged)
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(
                    """INSERT INTO narrations
                       (id, script_id, script_hash, voice_id, speed, duration_seconds, size_bytes, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (narration_id, script_id, self._script_hash(text), voice_id, speed, duration, size, created_at),
                )
                connection.execute(
                    """INSERT INTO managed_files
                       (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("voice", f"narration/{filename}", "narration", script_id, digest, size, created_at),
                )
                staged.rename(destination)
                try:
                    connection.commit()
                except sqlite3.Error:
                    destination.unlink()
                    raise
        return self._narration_metadata(narration_id, voice_id, speed, duration, size)

    @staticmethod
    def _script_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _narration_metadata(self, narration_id, voice_id, speed, duration, size):
        filename = f"{narration_id}.wav"
        return {"id": narration_id, "voice_id": voice_id, "speed": speed,
                "duration_seconds": duration, "size_bytes": size,
                "filename": filename, "path": str(self.narration_dir / filename)}

    def latest_narration(self, script_id: str, text: str):
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                """SELECT id, voice_id, speed, duration_seconds, size_bytes FROM narrations
                   WHERE script_id=? AND script_hash=? ORDER BY created_at DESC LIMIT 1""",
                (script_id, self._script_hash(text)),
            ).fetchone()
        if row is None:
            return None
        try:
            self.narration_file(uuid.UUID(row[0]))
        except VoiceError as error:
            if error.status_code == 404:
                return None
            raise
        return self._narration_metadata(*row)

    def narration_file(self, narration_id: uuid.UUID) -> Path:
        filename = f"{narration_id}.wav"
        with closing(sqlite3.connect(self.database)) as connection:
            recorded = connection.execute(
                "SELECT 1 FROM managed_files WHERE root_key='voice' AND relative_path=? AND owner_type='narration'",
                (f"narration/{filename}",),
            ).fetchone()
        path = self.narration_dir / filename
        if recorded is None or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.data.resolve()):
            raise VoiceError("The narration WAV could not be found.", 404)
        return path
