"""Resumable local topic-to-video jobs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from backend.ai.generation import GenerationError
from backend.ai.model_manager import ModelInstallError
from backend.avatar.encoder import FPS
from backend.avatar.service import AvatarError, RenderRequest, availability
from backend.scenes.exports import DraftScene, ExportDraft, ExportError, ExportLibrary, ExportRequest, ExportScene
from backend.scenes.jobs import ACTIVE, SceneRequest, supported_host
from backend.voice.service import VOICE_NAMES, VoiceError

LOGGER = logging.getLogger(__name__)
STAGES = ("script", "director", "narration", "presenter", "image", "video", "export")
OPENING_SECONDS = 5
TARGET_SECONDS = 30


class ProjectVideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: uuid.UUID
    voice_id: str = "af_bella"
    look: Literal["maya", "eli"] = "maya"
    format: Literal["landscape", "vertical", "square"] = "landscape"
    captions: bool = False


class ResumeProjectVideo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    captions: Optional[bool] = None


class JobCancelled(Exception):
    pass


def narration_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ProjectVideoJobs:
    def __init__(self, data: Path, database, creator, voice, scenes, render_presenter):
        self.data = data
        self.db = database
        self.creator = creator
        self.voice = voice
        self.scenes = scenes
        self.render_presenter = render_presenter
        self.library = ExportLibrary(data, database)
        self.lock = threading.Lock()
        self.worker = None
        self.stop = None
        self.job_id = None

    def _lease(self):
        import fcntl

        path = self.data / ".project-video.lock"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lease = os.fdopen(fd, "wb")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            lease.close()
            raise
        return lease

    def recover(self):
        if not supported_host():
            return
        try:
            lease = self._lease()
        except BlockingIOError:
            return
        except OSError:
            LOGGER.exception("Could not recover project video jobs")
            return
        with lease, self.db() as db:
            db.execute("""UPDATE project_video_jobs SET state='interrupted',
                       message='The previous session stopped. Resume from saved progress.'
                       WHERE state IN ('running', 'cancelling')""")

    @staticmethod
    def _read(db, project_id, job_id=None):
        if job_id:
            return db.execute("SELECT * FROM project_video_jobs WHERE project_id=? AND id=?",
                              (project_id, job_id)).fetchone()
        return db.execute("SELECT * FROM project_video_jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
                          (project_id,)).fetchone()

    def _public(self, row):
        if row is None:
            return None
        checkpoint = json.loads(row["checkpoint"])
        result = {key: row[key] for key in ("id", "project_id", "topic_id", "state", "stage", "message")}
        result["completed_stages"] = [stage for stage in STAGES if stage in checkpoint]
        result["script_id"] = checkpoint.get("script")
        result["export_id"] = checkpoint.get("export")
        result["captions"] = json.loads(row["settings"])["captions"]
        result["progress"] = None
        if row["state"] in ACTIVE and row["stage"] in ("image", "video"):
            scene_id = checkpoint.get(f"{row['stage']}_job")
            if scene_id:
                scene = self.scenes.get_job(checkpoint["script"], scene_id)
                if scene and scene["state"] in ACTIVE:
                    result["progress"] = scene["progress"]
        return result

    def latest(self, project_id):
        with self.db() as db:
            row = self._read(db, project_id)
        return self._public(row)

    @staticmethod
    def _linked(db, project_id):
        return db.execute(
            """SELECT scripts.id, scripts.topic_id, scripts.duration_seconds FROM project_scripts
               JOIN scripts ON scripts.id=project_scripts.script_id WHERE project_scripts.project_id=?""",
            (project_id,),
        ).fetchone()

    @staticmethod
    def _check_link(db, project_id, expected_script):
        linked = ProjectVideoJobs._linked(db, project_id)
        if (linked["id"] if linked else None) != expected_script:
            raise ExportError("The project's linked script changed. Start a new job with the current project.", 409)

    def _check_ready(self, checkpoint):
        profiles = self.scenes.capabilities()
        if not profiles["video_tools_available"]:
            raise ExportError("Install FFmpeg and FFprobe before generating a project video.", 409)
        if "script" not in checkpoint or "director" not in checkpoint:
            try:
                writing_model = self.creator.status()
            except ModelInstallError as error:
                raise ExportError(str(error), error.status_code) from error
            if not writing_model.inference_ready:
                raise ExportError("Install the local Qwen writing model in Discover Topics.", 409)
        if "narration" not in checkpoint and not self.voice.status()["ready"]:
            raise ExportError("Install Kokoro in the script's Narration panel.", 409)
        if "presenter" not in checkpoint:
            ready, detail = availability()
            if not ready:
                raise ExportError(detail, 409)
        for kind in ("image", "video"):
            if kind not in checkpoint and not profiles["profiles"][kind]["cached"]:
                raise ExportError(f"Prepare the {kind} model in Generate a scene by approving its download.", 409)

    def _launch(self, project_id, request, resume_id=None, captions=None):
        if not supported_host():
            raise ExportError("Automatic project video generation is available on Apple Silicon Macs.", 409)
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ExportError("A project video is already running. Wait for it to finish or stop it.", 409)
            try:
                lease = self._lease()
            except BlockingIOError as error:
                raise ExportError("Another app session is generating a project video.", 409) from error
            try:
                with self.db() as db:
                    latest = self._read(db, project_id)
                    if resume_id:
                        if latest is None or latest["id"] != resume_id:
                            raise ExportError("The latest project video job could not be found.", 404)
                        if latest["state"] == "completed":
                            raise ExportError("This project video job cannot be resumed.", 409)
                        checkpoint = json.loads(latest["checkpoint"])
                        request = ProjectVideoRequest.model_validate_json(latest["settings"])
                        if captions is not None and latest["stage"] != "export":
                            raise ExportError("Caption settings can be changed when retrying the export.", 409)
                        if captions is not None:
                            request.captions = captions
                        job_id = resume_id
                    else:
                        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                            raise ExportError("The project could not be found.", 404)
                        if not db.execute("SELECT 1 FROM topics WHERE id=?", (str(request.topic_id),)).fetchone():
                            raise ExportError("The topic could not be found.", 404)
                        if request.voice_id not in dict(VOICE_NAMES):
                            raise ExportError("Choose an available Kokoro voice.")
                        linked = self._linked(db, project_id)
                        if linked and (linked["topic_id"] != str(request.topic_id) or linked["duration_seconds"] != TARGET_SECONDS):
                            raise ExportError("Choose a project with no script or a 30-second script for this topic.", 409)
                        checkpoint = {"original_script": linked["id"] if linked else None}
                        job_id = str(uuid.uuid4())
                self._check_ready(checkpoint)
                with self.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._check_link(db, project_id, checkpoint["original_script"])
                    db.execute("""UPDATE project_video_jobs SET state='interrupted',
                               message='The previous session stopped. Resume from saved progress.'
                               WHERE state IN ('running', 'cancelling')""")
                    if resume_id:
                        db.execute("UPDATE project_video_jobs SET settings=?, state='running', message='' WHERE id=?",
                                   (request.model_dump_json(), resume_id))
                    else:
                        db.execute(
                            """INSERT INTO project_video_jobs
                               (id, project_id, topic_id, settings, checkpoint, state, stage)
                               VALUES (?, ?, ?, ?, ?, 'running', 'script')""",
                            (job_id, project_id, str(request.topic_id), request.model_dump_json(), json.dumps(checkpoint)),
                        )
                self.stop = threading.Event()
                self.job_id = job_id
                self.worker = threading.Thread(target=self._run, daemon=True, name="project-video",
                                               args=(project_id, job_id, request, checkpoint, self.stop, lease))
                self.worker.start()
            except BaseException:
                lease.close()
                raise
        return self.latest(project_id)

    def start(self, project_id, request):
        return self._launch(project_id, request)

    def resume(self, project_id, job_id, request):
        return self._launch(project_id, None, resume_id=job_id, captions=request.captions)

    def cancel(self, project_id, job_id):
        with self.lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._read(db, project_id, job_id)
            if row is None:
                raise ExportError("The project video job could not be found.", 404)
            if row["state"] in ACTIVE:
                if self.job_id != job_id or self.stop is None:
                    raise ExportError("Stop this job from the app session that started it.", 409)
                db.execute("UPDATE project_video_jobs SET state='cancelling' WHERE id=?", (job_id,))
                self.stop.set()
                row = self._read(db, project_id, job_id)
        return self._public(row)

    def close(self):
        with self.lock:
            if self.stop:
                self.stop.set()
            worker = self.worker
        if worker:
            worker.join(timeout=4)

    def _scene(self, db_job, stage, checkpoint, stop):
        script_id = checkpoint["script"]
        key = f"{stage}_job"
        child = self.scenes.get_job(script_id, checkpoint[key]) if key in checkpoint else None
        if child is None or child["state"] not in (*ACTIVE, "completed"):
            def record_start(db, scene_id):
                checkpoint[key] = scene_id
                db.execute("UPDATE project_video_jobs SET checkpoint=? WHERE id=?", (json.dumps(checkpoint), db_job))

            child = self.scenes.start(script_id, SceneRequest(kind=stage, prompt=checkpoint[f"{stage}_prompt"]),
                                      on_start=record_start)
        while child and child["state"] in ACTIVE:
            if stop.wait(0.5):
                child = self.scenes.cancel(script_id, child["id"])
                while child and child["state"] in ACTIVE:
                    time.sleep(0.5)
                    child = self.scenes.get_job(script_id, checkpoint[key])
                break
            child = self.scenes.get_job(script_id, checkpoint[key])
        if child and child["state"] == "completed" and child["asset_id"]:
            return child["asset_id"]
        if stop.is_set():
            raise JobCancelled()
        raise ExportError(child["message"] if child and child["message"] else "The generated scene is unavailable.")

    def _export(self, project_id, job_id, settings, checkpoint, script, wav, stop):
        with wave.open(str(wav), "rb") as audio:
            total = audio.getnframes() * FPS // audio.getframerate()
        opening = OPENING_SECONDS * FPS
        video = next((asset for asset in self.library.list_assets(script.id) if asset["id"] == checkpoint["video"]), None)
        if video is None or not video["max_frames"]:
            raise ExportError("The generated video is missing. Start a new project video job.")
        clip = min(3 * FPS, video["max_frames"])
        payload = ExportRequest(
            narration_id=checkpoint["narration"], format=settings.format, captions=settings.captions,
            scenes=[
                ExportScene(source="presenter", id=checkpoint["presenter"], frames=opening),
                ExportScene(source="asset", id=checkpoint["video"], frames=clip),
                ExportScene(source="asset", id=checkpoint["image"], frames=total - opening - clip),
            ],
        )
        draft = ExportDraft(
            format=payload.format, captions=payload.captions,
            scenes=[
                DraftScene(**payload.scenes[0].model_dump(), key=0, name="Illustrated opening",
                           max_frames=opening, presenter_narration_id=payload.narration_id),
                DraftScene(**payload.scenes[1].model_dump(), key=1, name="Generated Wan clip",
                           max_frames=video["max_frames"]),
                DraftScene(**payload.scenes[2].model_dump(), key=2, name="Generated FLUX still"),
            ],
        )

        def publish(db, result):
            if stop.is_set():
                raise JobCancelled()
            self._check_link(db, project_id, checkpoint["original_script"])
            row = db.execute("SELECT document_json FROM scripts WHERE id=?", (script.id,)).fetchone()
            if row is None or narration_hash(json.loads(row["document_json"])["full_narration"]) != checkpoint["narration_hash"]:
                raise ExportError("The saved script changed during generation. Start a new job.")
            db.execute("INSERT OR IGNORE INTO project_scripts (project_id, script_id) VALUES (?, ?)",
                       (project_id, script.id))
            db.execute("INSERT INTO video_drafts (script_id, draft_json) VALUES (?, ?) ON CONFLICT(script_id) DO NOTHING",
                       (script.id, draft.model_dump_json()))
            checkpoint["export"] = result["id"]
            db.execute("UPDATE project_video_jobs SET state='completed', checkpoint=?, message='' WHERE id=?",
                       (json.dumps(checkpoint), job_id))

        self.library.render(script.id, payload, wav, script.full_narration, on_publish=publish)

    def _run(self, project_id, job_id, settings, checkpoint, stop, lease):
        try:
            for stage in STAGES:
                if stop.is_set():
                    raise JobCancelled()
                if stage in checkpoint:
                    continue
                with self.db() as db:
                    db.execute("UPDATE project_video_jobs SET stage=? WHERE id=?", (stage, job_id))
                if stage == "script":
                    linked = checkpoint["original_script"]
                    script = self.creator.get_script(linked) if linked else self.creator.generate_script(
                        str(settings.topic_id), TARGET_SECONDS, short_form=True,
                    )
                    checkpoint["script"] = script.id
                    checkpoint["narration_hash"] = narration_hash(script.full_narration)
                else:
                    script = self.creator.get_script(checkpoint["script"])
                    if narration_hash(script.full_narration) != checkpoint["narration_hash"]:
                        raise ExportError("The saved script changed during generation. Start a new job.")
                    if stage == "director":
                        plan = self.creator.generate_director(script.id)
                        visuals = [scene for scene in plan.scenes if not scene.presenter_present] or plan.scenes[1:]
                        video_scene = visuals[0]
                        image_scene = visuals[-1] if len(visuals) > 1 else next(
                            (scene for scene in reversed(plan.scenes[1:]) if scene is not video_scene),
                            video_scene,
                        )
                        checkpoint["director"] = plan.id
                        checkpoint["video_prompt"] = video_scene.generation_prompt
                        checkpoint["image_prompt"] = image_scene.generation_prompt
                    elif stage == "narration":
                        narration = self.voice.save_narration(script.id, script.full_narration, settings.voice_id, 1.0,
                                                             target_seconds=TARGET_SECONDS)
                        checkpoint["narration"] = narration["id"]
                        checkpoint["narration_seconds"] = narration["duration_seconds"]
                    elif stage == "presenter":
                        request = RenderRequest(narration_id=checkpoint["narration"], look=settings.look,
                                                seconds=OPENING_SECONDS)
                        wav = self.voice.narration_file(request.narration_id)
                        result = self.render_presenter(request, wav, script.full_narration, checkpoint["narration_seconds"])
                        checkpoint["presenter"] = result["id"]
                    elif stage in ("image", "video"):
                        checkpoint[stage] = self._scene(job_id, stage, checkpoint, stop)
                    elif stage == "export":
                        wav = self.voice.narration_file(uuid.UUID(checkpoint["narration"]))
                        self._export(project_id, job_id, settings, checkpoint, script, wav, stop)
                        return
                with self.db() as db:
                    db.execute("UPDATE project_video_jobs SET checkpoint=? WHERE id=?", (json.dumps(checkpoint), job_id))
        except JobCancelled:
            self._finish(job_id, "cancelled", "Stopped. Resume from the last saved step.")
        except (AvatarError, ExportError, GenerationError, VoiceError) as error:
            self._finish(job_id, "failed", str(error))
        except Exception:
            LOGGER.exception("Project video job %s failed", job_id)
            self._finish(job_id, "failed", "Project video generation failed. Check the local backend log and resume.")
        finally:
            lease.close()

    def _finish(self, job_id, state, message):
        with self.lock, self.db() as db:
            db.execute("UPDATE project_video_jobs SET state=?, message=? WHERE id=? AND state!='completed'",
                       (state, message, job_id))
