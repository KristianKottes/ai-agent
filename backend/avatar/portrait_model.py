"""Install the pinned MuseTalk model pack under the application data directory."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sqlite3
import threading
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from backend.avatar.encoder import AvatarError
from backend.avatar.service import _binary, owned_directory
from backend.runtime.downloader import sha256_file

REVISION = "ff57bd4b785b3813f43be689390c3d7494258a35"
MODEL_SOURCE = "https://huggingface.co/mlx-community/MuseTalk-1.5-q4"
WEIGHTS = {
    "config.json": (227, "ba919fbdecb9834fc1c3e0b08234c8e8dce53910a22cb1c43e006038c69255b9"),
    "unet.safetensors": (1_325_610_215, "7a66b9f65d98e29eb7ecd4dac034331075d125ba00e1cdc704999ef404b60f1b"),
    "vae.safetensors": (167_335_279, "2a7670782dbe0ab66c40833dfd431b193375f0bdfc7c2daf76f5bc4e59070f98"),
    "whisper_encoder.safetensors": (16_423_174, "c04baf7220bbc73393eeba74b6224398ce56caad60e4e0a0f4c28af3180836d3"),
}
TOTAL_BYTES = sum(size for size, _ in WEIGHTS.values())
RUNTIME_NAME = "musetalk-mlx-q4"
REPO = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger(__name__)


def runtime_python() -> Path:
    return REPO / "backend" / ".portrait-venv" / "bin" / "python"


def runtime_ready() -> bool:
    python = runtime_python()
    return python.is_file() and (python.parent.parent / ".ready-v1").is_file()


def supported_host() -> bool:
    return platform.system() == "Darwin" and platform.machine().lower() == "arm64"


class PortraitModel:
    def __init__(self, data: Path, database: Path):
        self.data = data
        self.database = database
        self.root = data / "avatar" / RUNTIME_NAME
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self._state = "available"
        self._message = ""
        self._downloaded = 0
        self._verified = {}

    def _safe_directory(self) -> bool:
        return not self.root.is_symlink() and not self.root.parent.is_symlink()

    def _valid_file(self, filename: str) -> bool:
        size, digest = WEIGHTS[filename]
        path = self.root / filename
        if path.is_symlink() or not path.is_file():
            return False
        stat = path.stat()
        key = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
        if stat.st_size != size:
            return False
        if self._verified.get(filename) != key:
            if sha256_file(path) != digest:
                return False
            self._verified[filename] = key
        return True

    def installed(self) -> bool:
        with self._lock:
            if not self._safe_directory() or not all(self._valid_file(name) for name in WEIGHTS):
                return False
            with closing(sqlite3.connect(self.database)) as db:
                registered = db.execute(
                    """SELECT relative_path, sha256, size_bytes FROM managed_files
                       WHERE root_key='avatar' AND owner_type='portrait_model' AND owner_id=?""",
                    (RUNTIME_NAME,),
                ).fetchall()
            return set(registered) == {
                (f"{RUNTIME_NAME}/{name}", digest, size) for name, (size, digest) in WEIGHTS.items()
            }

    def status(self) -> dict:
        with self._lock:
            runtime = runtime_ready()
            tools = bool(_binary("ffmpeg") and _binary("ffprobe"))
            host = supported_host()
            installed = self.installed()
            state = "installed" if installed else "available" if self._state == "installed" else self._state
            message = self._message
            if not runtime and os.environ.get("AI_BLOGGER_MEDIA_BIN_DIR"):
                message = "This app bundle includes the illustrated presenter. MuseTalk requires its separate experimental runtime."
            return {
                "state": state,
                "platform_eligible": host,
                "runtime_installed": runtime,
                "tools_available": tools,
                "ready": installed and runtime and tools and host,
                "download_bytes": TOTAL_BYTES,
                "downloaded_bytes": TOTAL_BYTES if installed else self._downloaded,
                "message": message,
                "source_url": MODEL_SOURCE,
            }

    def start_install(self, accepted: bool) -> dict:
        if not accepted:
            raise AvatarError("Review the model licenses and approve the download first.", 422)
        if not supported_host() or not runtime_ready():
            raise AvatarError("Set up the portrait runtime on an Apple Silicon Mac before installing the model.")
        with self._lock:
            if self.installed() or (self._thread and self._thread.is_alive()):
                return self.status()
            owned_directory(self.data)
            if not self._safe_directory():
                raise AvatarError("The portrait model directory is unsafe.", 500)
            self.root.mkdir(mode=0o700, exist_ok=True)
            existing = sum(self._cached_bytes(name) for name in WEIGHTS)
            if shutil.disk_usage(self.root).free < TOTAL_BYTES - existing + 250_000_000:
                raise AvatarError("Free more disk space for the portrait model and its render files.")
            self._cancel.clear()
            self._downloaded = existing
            self._state = "downloading"
            self._message = "Downloading and verifying MuseTalk."
            self._thread = threading.Thread(target=self._install, name="musetalk-install", daemon=True)
            self._thread.start()
            return self.status()

    def cancel(self) -> dict:
        self._cancel.set()
        return self.status()

    def _cached_bytes(self, name: str) -> int:
        if self._valid_file(name):
            return WEIGHTS[name][0]
        partial = self.root / (name + ".part")
        if partial.is_symlink() or not partial.is_file():
            return 0
        return min(partial.stat().st_size, WEIGHTS[name][0])

    def _check_cancel(self):
        if self._cancel.is_set():
            raise AvatarError("Installation cancelled. Retry to resume.")

    def _fetch(self, name: str):
        size, digest = WEIGHTS[name]
        partial = self.root / (name + ".part")
        target = self.root / name
        if partial.is_symlink() or target.is_symlink():
            raise AvatarError("The model download path is unsafe.", 500)
        if self._valid_file(name):
            return
        if partial.is_file() and partial.stat().st_size > size:
            partial.unlink()
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset < size:
            headers = {"User-Agent": "AI-Blogger-Studio"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            url = f"{MODEL_SOURCE}/resolve/{REVISION}/{name}"
            request = urllib.request.Request(url, headers=headers)
            try:
                response = urllib.request.urlopen(request, timeout=25)
            except urllib.error.HTTPError as error:
                if error.code != 416 or not offset:
                    raise
                error.close()
                partial.unlink()
                return self._fetch(name)
            with response:
                if response.status == 200:
                    offset = 0
                elif response.status != 206 or response.headers.get("Content-Range") != f"bytes {offset}-{size - 1}/{size}":
                    raise AvatarError("The model server sent an unexpected resume range.", 502)
                other = sum(self._cached_bytes(key) for key in WEIGHTS if key != name)
                with partial.open("ab" if offset else "wb") as destination:
                    while True:
                        self._check_cancel()
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        if destination.tell() + len(block) > size:
                            raise AvatarError("The model file exceeded its pinned size.", 502)
                        destination.write(block)
                        with self._lock:
                            self._downloaded = other + destination.tell()

        if partial.stat().st_size != size:
            raise AvatarError("The download ended early. Retry to resume.", 502)
        if sha256_file(partial) != digest:
            partial.unlink()
            raise AvatarError("A model file failed its pinned checksum. Retry to fetch it again.", 502)
        self._check_cancel()
        with self._lock:
            os.replace(partial, target)
            stat = target.stat()
            self._verified[name] = (stat.st_size, stat.st_mtime_ns, stat.st_ino)

    def _install(self):
        try:
            for name in WEIGHTS:
                self._check_cancel()
                self._fetch(name)
            with self._lock:
                self._check_cancel()
                now = datetime.now(timezone.utc).isoformat()
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.executemany(
                        """INSERT OR REPLACE INTO managed_files
                           (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
                           VALUES ('avatar', ?, 'portrait_model', ?, ?, ?, ?)""",
                        [(f"{RUNTIME_NAME}/{name}", RUNTIME_NAME, digest, size, now)
                         for name, (size, digest) in WEIGHTS.items()],
                    )
                self._state = "installed"
                self._message = "Model files passed SHA-256 verification."
        except Exception as error:
            if not self._cancel.is_set():
                LOGGER.exception("Portrait model installation failed")
            with self._lock:
                self._state = "cancelled" if self._cancel.is_set() else "failed"
                self._message = (
                    "Installation cancelled. Retry to resume."
                    if self._cancel.is_set()
                    else str(error) if isinstance(error, AvatarError)
                    else "Could not download the model. Check your connection and retry."
                )
