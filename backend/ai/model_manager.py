"""Explicit, resumable and SHA-256 verified install for the local language model."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import platform
import threading
import urllib.request
from pathlib import Path
from typing import Optional

from .contracts import ModelStatus

MODEL_ID = "qwen2.5-0.5b-instruct-q4_k_m"
MODEL_NAME = "Qwen2.5 0.5B Instruct · Q4_K_M"
MODEL_PUBLISHER = "Qwen"
MODEL_LICENSE = "Apache-2.0"
MODEL_SOURCE = "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF"
MODEL_URL = MODEL_SOURCE + "/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
MODEL_FILE = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
MODEL_SIZE_BYTES = 491_400_032  # Exact size of the SHA-256-pinned official file.
MODEL_SHA256 = "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db"
_CHUNK_SIZE = 1024 * 1024


class ModelInstallError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class LocalModelManager:
    def __init__(self, model_root: Path):
        self.model_root = model_root
        self.model_path = model_root / MODEL_FILE
        self.partial_path = model_root / (MODEL_FILE + ".part")
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = "available"
        self._message = "Model weights are not installed."
        self._downloaded = self.partial_path.stat().st_size if self.partial_path.is_file() else 0
        self._verified_signature: Optional[tuple] = None

    def _prepare_root(self) -> None:
        self.model_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.model_root.is_symlink() or not self.model_root.is_dir():
            raise ModelInstallError("The application model directory is unsafe.", 500)
        if os.name != "nt":
            os.chmod(self.model_root, 0o700)
        for path in (self.model_path, self.partial_path):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ModelInstallError("A model file path is not a regular application-owned file.", 500)

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(_CHUNK_SIZE), b""):
                digest.update(block)
        return digest.hexdigest()

    def _verify_installed(self) -> bool:
        if not self.model_path.is_file() or self.model_path.stat().st_size <= 0:
            self._verified_signature = None
            return False
        stat = self.model_path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if signature == self._verified_signature:
            return True
        if self._digest(self.model_path) == MODEL_SHA256:
            self._verified_signature = signature
            return True
        self._verified_signature = None
        self.model_path.unlink(missing_ok=True)
        return False

    def status(self) -> ModelStatus:
        with self._lock:
            self._prepare_root()
            if self._verify_installed():
                self._state = "installed"
                self._message = "Model weights are installed and checksum verified."
                self._downloaded = self.model_path.stat().st_size
            elif self._thread and self._thread.is_alive():
                pass
            elif self._state not in ("failed", "cancelled"):
                self._state = "available"
                self._downloaded = self.partial_path.stat().st_size if self.partial_path.is_file() else 0
                self._message = (
                    "A partial download is saved and will resume when retried."
                    if self._downloaded
                    else "Model weights are not installed."
                )
            return self._status_value()

    def _status_value(self) -> ModelStatus:
        downloaded = self._downloaded
        if self.model_path.is_file() and self._state == "installed":
            downloaded = self.model_path.stat().st_size
        system = platform.system()
        machine = platform.machine().lower()
        is_verified_host = system == "Darwin" and machine in ("arm64", "aarch64")
        host = "{} {}".format(system, machine)
        runtime_installed = importlib.util.find_spec("llama_cpp") is not None
        inference_ready = self._state == "installed" and runtime_installed
        message = self._message
        if self._state == "installed" and not runtime_installed:
            message = "Weights are checksum verified, but llama-cpp-python is not installed in this backend environment."
        return ModelStatus(
            model_id=MODEL_ID,
            name=MODEL_NAME,
            publisher=MODEL_PUBLISHER,
            license=MODEL_LICENSE,
            source_url=MODEL_SOURCE,
            runtime="llama-cpp-python",
            platform=host,
            platform_verification="verified" if is_verified_host else "not_tested",
            install_enabled=is_verified_host,
            runtime_installed=runtime_installed,
            state=self._state,
            expected_size_bytes=MODEL_SIZE_BYTES,
            downloaded_bytes=downloaded,
            sha256=MODEL_SHA256,
            progress_percent=min(100.0, downloaded * 100.0 / MODEL_SIZE_BYTES),
            inference_ready=inference_ready,
            message=message,
        )

    def start_install(self, accepted_license: bool) -> ModelStatus:
        if not accepted_license:
            raise ModelInstallError("Review and accept the Apache-2.0 model license before installing.", 422)
        with self._lock:
            self._prepare_root()
            if self._verify_installed():
                self._state = "installed"
                self._message = "Model weights are already installed and checksum verified."
                self._downloaded = self.model_path.stat().st_size
                return self._status_value()
            if self._thread and self._thread.is_alive():
                return self._status_value()
            self._cancel.clear()
            self._state = "downloading"
            self._message = "Downloading from the official Qwen model repository."
            self._downloaded = self.partial_path.stat().st_size if self.partial_path.is_file() else 0
            self._thread = threading.Thread(target=self._download, name="qwen-model-download", daemon=True)
            self._thread.start()
            return self._status_value()

    def cancel_install(self) -> ModelStatus:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._cancel.set()
                self._message = "Cancellation requested; the verified partial download will be kept for resume."
            return self._status_value()

    def _download(self) -> None:
        try:
            self._prepare_root()
            offset = self.partial_path.stat().st_size if self.partial_path.is_file() else 0
            if offset > MODEL_SIZE_BYTES:
                self.partial_path.unlink(missing_ok=True)
                offset = 0
            request = urllib.request.Request(
                MODEL_URL,
                headers={
                    "User-Agent": "AI-Blogger-Studio/0.1.0 local-model-installer",
                    **({"Range": "bytes={}-".format(offset)} if offset else {}),
                },
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                status_code = getattr(response, "status", response.getcode())
                if offset and status_code != 206:
                    self.partial_path.unlink(missing_ok=True)
                    offset = 0
                elif not offset and status_code not in (200, 206):
                    raise ModelInstallError("The model server returned an unexpected response.")
                mode = "ab" if offset else "wb"
                with self.partial_path.open(mode) as output:
                    while True:
                        if self._cancel.is_set():
                            with self._lock:
                                self._state = "cancelled"
                                self._message = "Download cancelled. The verified prefix is available to resume."
                                self._downloaded = self.partial_path.stat().st_size
                            return
                        block = response.read(_CHUNK_SIZE)
                        if not block:
                            break
                        output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                        with self._lock:
                            self._downloaded = self.partial_path.stat().st_size

            actual_size = self.partial_path.stat().st_size
            if actual_size > MODEL_SIZE_BYTES + _CHUNK_SIZE:
                raise ModelInstallError("The model download exceeded its pinned maximum size.", 502)
            if self._digest(self.partial_path) != MODEL_SHA256:
                raise ModelInstallError("The downloaded model failed its pinned SHA-256 check; it was discarded.", 502)
            os.replace(self.partial_path, self.model_path)
            stat = self.model_path.stat()
            with self._lock:
                self._verified_signature = (stat.st_size, stat.st_mtime_ns)
                self._state = "installed"
                self._message = "Download complete. The model checksum is verified."
                self._downloaded = self.model_path.stat().st_size
        except Exception as error:
            if isinstance(error, ModelInstallError):
                detail = error.detail
            else:
                detail = "Model download failed. Check the connection and retry; an incomplete prefix can be resumed."
            with self._lock:
                self._state = "failed"
                self._message = detail
                self._downloaded = self.partial_path.stat().st_size if self.partial_path.is_file() else 0

    def model_file(self) -> Path:
        status = self.status()
        if not status.inference_ready:
            raise ModelInstallError("Install the checksum-verified local model before generating content.", 409)
        return self.model_path

