"""Run scene generation in a managed worker with durable job state."""

from __future__ import annotations

import logging
import math
import platform
import threading
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.avatar.encoder import FPS
from backend.avatar.service import _binary
from backend.scenes.benchmark import BenchmarkError, SceneCancelled, benchmark, check_cancel
from backend.scenes.exports import ExportError, ExportLibrary
from backend.scenes.profiles import ENGINE, IMAGE_WEIGHTS, VIDEO_WEIGHTS

LOGGER = logging.getLogger(__name__)
PROFILES = {"image": IMAGE_WEIGHTS, "video": VIDEO_WEIGHTS}
ACTIVE = ("running", "cancelling")


def supported_host() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def register_model(connect, artifact, path):
    with connect() as db:
        db.execute(
            """INSERT OR IGNORE INTO managed_files
               (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
               VALUES ('scene_models', ?, 'scene_model', 'stable-diffusion-cpp', ?, ?, CURRENT_TIMESTAMP)""",
            (path.name, artifact.sha256, artifact.size),
        )


class SceneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["image", "video"]
    prompt: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00]+$")
    seed: int = Field(default=137, strict=True, ge=0, le=2**31 - 1)
    accepted_license: bool = False


class SceneJobs:
    def __init__(self, data: Path, database_connection):
        self.data = data
        self.models = data / "scene_models"
        self.db = database_connection
        self.library = ExportLibrary(data, database_connection)
        self._lock = threading.RLock()
        self._worker = None
        self._cancel = None
        self._id = None

    def _lease(self):
        import fcntl

        if self.models.is_symlink() or (self.models.exists() and not self.models.is_dir()):
            raise ExportError("The scene model directory is unavailable.", 500)
        self.models.mkdir(mode=0o700, exist_ok=True)
        path = self.models / ".scene-jobs.lock"
        if path.is_symlink():
            raise ExportError("The scene job lock is unavailable.", 500)
        file = path.open("a")
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            file.close()
            raise
        return file

    def recover(self):
        if not supported_host():
            return
        try:
            lease = self._lease()
        except BlockingIOError:
            return
        except (ExportError, OSError):
            LOGGER.exception("Could not recover scene jobs")
            return
        with lease, self.db() as db:
            db.execute(
                """UPDATE scene_jobs SET state='interrupted', message='The previous session stopped. Generate again to retry.'
                   WHERE state IN ('running', 'cancelling')"""
            )

    def capabilities(self) -> dict:
        eligible = supported_host()
        ready_video_tools = bool(_binary("ffmpeg") and _binary("ffprobe"))
        available = eligible and not self.models.is_symlink()
        profiles = {}
        for kind, weights in PROFILES.items():
            missing_bytes = 0
            cached = available
            for artifact in (ENGINE, *weights):
                target = self.models / artifact.name
                if available and not target.is_symlink() and target.is_file() and target.stat().st_size == artifact.size:
                    continue
                cached = False
                partial = target.with_name(target.name + ".part")
                cached_bytes = partial.stat().st_size if available and not partial.is_symlink() and partial.is_file() else 0
                missing_bytes += artifact.size - (cached_bytes if cached_bytes <= artifact.size else 0)
            profiles[kind] = {"eligible": available and (kind == "image" or ready_video_tools),
                              "cached": cached, "download_bytes": missing_bytes}
        return {"platform_eligible": eligible, "video_tools_available": ready_video_tools, "profiles": profiles}

    def _get(self, script_id: str, job_id: str | None = None) -> dict | None:
        with self.db() as db:
            if job_id:
                row = db.execute("SELECT * FROM scene_jobs WHERE script_id=? AND id=?", (script_id, job_id)).fetchone()
            else:
                row = db.execute("SELECT * FROM scene_jobs WHERE script_id=? ORDER BY rowid DESC LIMIT 1", (script_id,)).fetchone()
        return dict(row) if row else None

    def latest(self, script_id: str) -> dict | None:
        return self._get(script_id)

    def get_job(self, script_id: str, job_id: str) -> dict | None:
        return self._get(script_id, job_id)

    def _update(self, job_id: str, state: str, stage: str, value, message: str = ""):
        with self.db() as db:
            db.execute("UPDATE scene_jobs SET state=?, stage=?, progress=?, message=? WHERE id=?",
                       (state, stage, value, message, job_id))

    def start(self, script_id: str, request: SceneRequest, *, on_start=None) -> dict:
        profile = self.capabilities()["profiles"][request.kind]
        if not profile["eligible"]:
            raise ExportError("Scene generation requires Apple Silicon. Video also requires FFmpeg and FFprobe.", 409)
        if not profile["cached"] and not request.accepted_license:
            raise ExportError("Review the model licenses and approve the download before generating.", 409)
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                raise ExportError("A scene is already generating. Wait for it to finish or cancel it.", 409)
            try:
                lease = self._lease()
            except BlockingIOError as error:
                raise ExportError("Another app session is generating a scene.", 409) from error
            job_id = str(uuid.uuid4())
            cancel = threading.Event()
            try:
                with self.db() as db:
                    db.execute(
                        """INSERT INTO scene_jobs (id, script_id, kind, prompt, seed, state, stage)
                           VALUES (?, ?, ?, ?, ?, 'running', 'preparing')""",
                        (job_id, script_id, request.kind, request.prompt, request.seed),
                    )
                    if on_start is not None:
                        on_start(db, job_id)
                self._id, self._cancel = job_id, cancel
                self._worker = threading.Thread(
                    target=self._run, args=(script_id, job_id, request, cancel, lease),
                    daemon=True, name="scene-generation",
                )
                self._worker.start()
            except BaseException:
                lease.close()
                raise
            return self._get(script_id, job_id)

    def cancel(self, script_id: str, job_id: str) -> dict:
        with self._lock:
            job = self._get(script_id, job_id)
            if job is None:
                raise ExportError("The scene job could not be found.", 404)
            if job["state"] in ACTIVE:
                if job_id != self._id or self._cancel is None:
                    raise ExportError("Cancel this scene from the app session that started it.", 409)
                self._cancel.set()
                self._update(job_id, "cancelling", job["stage"], job["progress"])
                return self._get(script_id, job_id)
            return job

    def close(self):
        with self._lock:
            if self._cancel:
                self._cancel.set()
            worker = self._worker
        if worker is not None:
            worker.join(timeout=4)

    def _run(self, script_id, job_id, request, cancel, lease):
        last_stage = None
        last_time = 0.0

        def progress(stage, fraction):
            nonlocal last_stage, last_time
            now = time.monotonic()
            if stage == last_stage and now - last_time < 1:
                return
            with self._lock:
                check_cancel(cancel)
                self._update(job_id, "running", stage, fraction)
            last_stage, last_time = stage, now

        try:
            with TemporaryDirectory(prefix=".scene-", dir=self.data) as temp:
                result = benchmark(request.kind, self.models, request.accepted_license, run_dir=Path(temp),
                                   prompt=request.prompt, seed=request.seed, cancel=cancel,
                                   progress=progress, verified=lambda artifact, path: register_model(self.db, artifact, path))
                asset_path = Path(result["output"])
                frames = math.floor(float(result["duration"]) * FPS + 1e-6) if request.kind == "video" else None

                def complete(db, asset_id):
                    db.execute(
                        """UPDATE scene_jobs SET state='completed', stage='completed', progress=1,
                           asset_id=?, message='Scene saved to the script.' WHERE id=?""", (asset_id, job_id),
                    )

                with self._lock:
                    check_cancel(cancel)
                    self.library.publish_scene(script_id, asset_path, f"Generated {request.kind} · {request.prompt[:100]}",
                                               request.kind, frames, on_publish=complete)
        except SceneCancelled as error:
            self._update(job_id, "cancelled", "cancelled", None, str(error))
        except (BenchmarkError, ExportError) as error:
            self._update(job_id, "failed", "failed", None, str(error).split(". See ", 1)[0])
        except Exception:
            LOGGER.exception("Scene job %s failed", job_id)
            self._update(job_id, "failed", "failed", None, "Scene generation failed. Check the local backend log and retry.")
        finally:
            lease.close()
