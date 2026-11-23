"""Owns only processes launched from verified, installed component entrypoints."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from .models import ComponentManifest


class RuntimeProcessError(RuntimeError):
    """A managed process failed its start or stop health check."""


class RuntimeSupervisor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._processes: Dict[str, subprocess.Popen[bytes]] = {}

    def is_running(self, component_id: str) -> bool:
        with self._lock:
            process = self._processes.get(component_id)
            return process is not None and process.poll() is None

    def process_id(self, component_id: str) -> Optional[int]:
        with self._lock:
            process = self._processes.get(component_id)
            return process.pid if process is not None and process.poll() is None else None

    def start(self, manifest: ComponentManifest, installation_path: Path, log_path: Path) -> int:
        component_id = manifest.id
        entrypoint = installation_path.joinpath(*manifest.entrypoint.split("/"))
        if not entrypoint.is_file() or entrypoint.is_symlink():
            raise RuntimeProcessError("The installed entrypoint is missing or unsafe.")

        with self._lock:
            existing = self._processes.get(component_id)
            if existing is not None and existing.poll() is None:
                return existing.pid
            if existing is not None:
                self._processes.pop(component_id, None)

            log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if log_path.is_symlink() or (log_path.exists() and not log_path.is_file()):
                raise RuntimeProcessError("The component log path is not a regular application-owned file.")
            child_env = {
                key: value
                for key, value in os.environ.items()
                if key in {"HOME", "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE"}
            }
            child_env.update(
                {
                    "AI_BLOGGER_COMPONENT_ID": component_id,
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                }
            )
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs["start_new_session"] = True

            try:
                with log_path.open("wb") as log_file:
                    process = subprocess.Popen(
                        [sys.executable, str(entrypoint)],
                        cwd=installation_path,
                        env=child_env,
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                        shell=False,
                        **kwargs,
                    )
            except OSError as error:
                raise RuntimeProcessError(f"Could not start the managed component: {error}") from error
            self._processes[component_id] = process

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            return_code = process.poll()
            if return_code is not None:
                with self._lock:
                    self._processes.pop(component_id, None)
                raise RuntimeProcessError(f"The component exited during startup with code {return_code}.")
            try:
                for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if message.get("status") == "ready" and message.get("component_id") == component_id:
                        return process.pid
            except OSError:
                pass
            time.sleep(0.05)

        self.stop(component_id)
        raise RuntimeProcessError("The component did not report healthy startup within 5 seconds.")

    def stop(self, component_id: str) -> Optional[int]:
        with self._lock:
            process = self._processes.pop(component_id, None)
        if process is None:
            return None
        if process.poll() is not None:
            return process.returncode

        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            return process.wait(timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                return process.wait(timeout=2.0)
            except subprocess.TimeoutExpired as error:
                raise RuntimeProcessError("The component could not be stopped cleanly.") from error

    def stop_all(self) -> None:
        with self._lock:
            component_ids = list(self._processes)
        for component_id in component_ids:
            try:
                self.stop(component_id)
            except RuntimeProcessError:
                # Continue stopping remaining owned processes during application shutdown.
                continue


runtime_supervisor = RuntimeSupervisor()
