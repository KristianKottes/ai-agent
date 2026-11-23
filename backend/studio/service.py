"""Project assets, reusable voice profiles, and the persistent studio work queue."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import signal
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from backend.studio import audio, export, holos, illustrated, images, motion, openvoice, topic_library, video, zonos
from backend.studio.audio import StudioError
from backend.studio.contracts import Draft, IMPORT_LIMIT, RenderRequest
from backend.studio.speech import POCKET, PROVIDERS, SpeechRequest, worker_command, worker_environment
from backend.studio.tools import StudioTools
from backend.ai.mamay import load_settings

LOGGER = logging.getLogger(__name__)
JOB_FIELDS = """id, kind, state, progress, message, created_at,
    CASE WHEN kind = 'illustrated_video' THEN json_extract(input_json, '$.topic') END AS topic"""


def migrate(db):
    for command in [
        """CREATE TABLE IF NOT EXISTS studio_video_timelines (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, timeline_json TEXT NOT NULL,
            revision INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS studio_image_profiles (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, id TEXT NOT NULL,
            version INTEGER NOT NULL, profile_json TEXT NOT NULL,
            PRIMARY KEY(project_id, id, version)
        )""",
        """CREATE TABLE IF NOT EXISTS studio_assets (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, name TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS studio_drafts (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, draft_json TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS studio_presets (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, preset_json TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS studio_jobs (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, input_json TEXT NOT NULL, state TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS studio_profiles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, language TEXT NOT NULL, metadata_json TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS studio_project_profiles (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            profile_id TEXT NOT NULL REFERENCES studio_profiles(id) ON DELETE CASCADE,
            PRIMARY KEY(project_id, profile_id)
        )""",
        """CREATE TABLE IF NOT EXISTS studio_tools (
            id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, health TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS studio_job_queue_idx ON studio_jobs(state, created_at)",
        "CREATE INDEX IF NOT EXISTS studio_jobs_project_idx ON studio_jobs(project_id, created_at)",
        "CREATE INDEX IF NOT EXISTS studio_assets_project_idx ON studio_assets(project_id, created_at)",
        """CREATE TABLE IF NOT EXISTS studio_morning_topics (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            batch_id TEXT NOT NULL REFERENCES studio_jobs(id) ON DELETE CASCADE, ordinal INTEGER NOT NULL,
            language TEXT NOT NULL, metadata_json TEXT NOT NULL, script_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
            used INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0, deleted_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(batch_id, ordinal)
        )""",
        "CREATE INDEX IF NOT EXISTS studio_morning_topics_project_idx ON studio_morning_topics(project_id, created_at)",
    ]:
        db.execute(command)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _record(row):
    value = dict(row)
    value.update(json.loads(value.pop("metadata_json")))
    return value


def _decode(value):
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise StudioError("The uploaded file could not be read.") from error
    if not raw or len(raw) > IMPORT_LIMIT:
        raise StudioError("Upload a file under 32 MiB.")
    return raw


def _media_name(name, extensions):
    clean = name.strip()
    if any(char in clean for char in "/\\\x00\r\n") or Path(clean).suffix.lower() not in extensions:
        raise StudioError("Choose a file with one of these extensions: " + ", ".join(extensions))
    return clean


class ProjectStudio:
    def __init__(self, data, connect, voice):
        self.data, self.connect, self.voice = data, connect, voice
        self.root = data / "studio"
        self.tools = StudioTools(voice, connect)
        self.images = images.ImageModels(data, self.tools)
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.queue_ready = threading.Event()
        self.user_cancel = threading.Event()
        self.active_job = None
        self.thread = threading.Thread(target=self._worker, name="studio-queue", daemon=True)
        self.preview_lock = threading.Lock()
        self.recording_lock = threading.Lock()
        self.previews = OrderedDict()

    def start(self):
        from backend.voice.service import MODEL_ID
        self.voice.enabled = self.tools.preference(MODEL_ID)[0]
        self.thread.start()

    def close(self):
        self.images.close()
        self.tools.cancel.set()
        with self.condition:
            self.stop.set()
            self.condition.notify_all()
        self.thread.join(timeout=8)
        if self.tools.worker:
            self.tools.worker.join(timeout=2)

    def project(self, project):
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM projects WHERE id=?", (project,)).fetchone()
        if not exists:
            raise StudioError("Project was not found.", 404)

    def directory(self, project, group):
        root = self.root / project / group
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.resolve() != root:
            raise StudioError("The project media directory is unsafe.", 500)
        return root

    def record_asset(self, project, id, kind, name, metadata, source, suffix, *, thumbnail=None):
        target = self.directory(project, kind) / (id + suffix)
        files = [(source, target)]
        if thumbnail is not None:
            files.append((thumbnail, self.directory(project, "thumbnail") / (id + ".png")))
        moved = []
        try:
            with self.connect() as db:
                db.execute("INSERT INTO studio_assets VALUES (?, ?, ?, ?, ?, ?)",
                           (id, project, kind, name, json.dumps(metadata, ensure_ascii=False), _now()))
                for source, destination in files:
                    os.replace(source, destination)
                    moved.append(destination)
        except BaseException:
            for destination in moved:
                destination.unlink(missing_ok=True)
            raise
        return self.asset(project, id)

    def assets(self, project):
        with self.connect() as db:
            return [_record(row) for row in db.execute(
                "SELECT * FROM studio_assets WHERE project_id=? ORDER BY created_at DESC", (project,))]

    def asset(self, project, id, kind=None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM studio_assets WHERE project_id=? AND id=?", (project, id)).fetchone()
        if row is None or kind and row["kind"] != kind:
            raise StudioError("The media asset was not found in this project.", 404)
        return _record(row)

    def asset_path(self, project, id, kind=None):
        asset = self.asset(project, id, kind)
        suffix = {"audio": ".wav", "image": ".png", "background": ".mp4",
                  "export": "." + asset.get("extension", "mp4")}[asset["kind"]]
        path = self.root / project / asset["kind"] / (id + suffix)
        if path.resolve() != path or not path.is_file():
            raise StudioError("The media file is unavailable.", 404)
        return path

    def import_media(self, project, kind, payload):
        self.project(project)
        extensions = {"audio": (".wav", ".mp3", ".flac", ".m4a", ".ogg"), "image": (".png", ".jpg", ".jpeg", ".webp"),
                      "background": (".mp4", ".mov", ".webm")}[kind]
        name = _media_name(payload.name, extensions)
        raw = _decode(payload.content_base64)
        with TemporaryDirectory(dir=self.directory(project, "work")) as stage:
            work = Path(stage)
            source = work / ("source" + Path(name).suffix.lower())
            source.write_bytes(raw)
            if kind == "audio":
                self.tools.require("ffmpeg")
                target = work / "audio.wav"
                audio.convert(source, target, rate=48_000, channels=2)
                return self.record_asset(project, str(uuid4()), kind, name,
                                         {**audio.metadata(target), "source_type": "upload"}, target, target.suffix)
            if kind == "background":
                self.tools.require("ffmpeg")
                target = work / "background.mp4"
                command = [audio.media_binary(), "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe",
                           "-format_whitelist", "mov,matroska,webm", "-i", str(source), "-map", "0:v:0",
                           "-t", "20", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2",
                           "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", str(target)]
                try:
                    subprocess.run(command, capture_output=True, check=True, timeout=120)
                except (OSError, subprocess.SubprocessError) as error:
                    raise StudioError("The background video could not be decoded.") from error
                return self.record_asset(project, str(uuid4()), kind, name, {}, target, target.suffix)
            return self.publish_image(project, str(uuid4()), name, source, work, {"source_type": "upload"})

    @staticmethod
    def normalize_image(source, target, metadata, thumbnail=None, preserve=False):
        from backend.runtime.downloader import sha256_file
        from PIL import Image, ImageOps

        try:
            with Image.open(source, formats=["PNG", "JPEG", "WEBP"]) as opened:
                if opened.width * opened.height > 32_000_000:
                    raise StudioError("Choose an image under 32 megapixels.")
                original_dimensions = list(opened.size)
                image_format = opened.format
                image = ImageOps.exif_transpose(opened).convert("RGBA")
                dimensions = image.size
            if preserve:
                if image_format != "PNG":
                    raise StudioError("The native image worker must publish PNG outputs.")
                shutil.copyfile(source, target)
            else:
                image.save(target)
            if thumbnail is not None:
                image.thumbnail((360, 360))
                image.save(thumbnail)
        except (Image.DecompressionBombError, OSError) as error:
            raise StudioError("This image could not be decoded.") from error
        digest = sha256_file(target)
        return {**metadata, "width": dimensions[0], "height": dimensions[1], "original_dimensions": original_dimensions,
                "aspect_ratio": dimensions[0] / dimensions[1], "digest": digest, "thumbnail": thumbnail is not None}

    def publish_image(self, project, id, name, source, work, metadata):
        target = work / "normalized.png"
        record = self.normalize_image(source, target, metadata)
        with self.condition:
            for asset in self.assets(project):
                if asset.get("digest") == record["digest"] and asset.get("source_type") == "upload":
                    try:
                        self.asset_path(project, asset["id"], "image")
                    except StudioError:
                        continue
                    return asset
            return self.record_asset(project, id, "image", name, record, target, ".png")

    def publish_generated_images(self, project, records, work):
        staged = []
        directory = self.directory(project, "image")
        thumbnails = self.directory(project, "thumbnail")
        for id, name, source, metadata in records:
            self.check()
            target = work / (id + ".png")
            thumbnail = work / ("thumb-" + id + ".png")
            metadata = self.normalize_image(source, target, metadata, thumbnail=thumbnail, preserve=True)
            files = [(target, directory / target.name), (thumbnail, thumbnails / target.name)]
            staged.append((id, name, files, metadata))
        published = []
        try:
            with self.condition, self.connect() as db:
                self.check()
                for id, name, files, metadata in staged:
                    db.execute("INSERT INTO studio_assets VALUES (?, ?, 'image', ?, ?, ?)",
                               (id, project, name, json.dumps(metadata, ensure_ascii=False), _now()))
                    for source, target in files:
                        os.replace(source, target)
                        published.append(target)
        except BaseException:
            for target in published:
                target.unlink(missing_ok=True)
            raise

    def image_thumbnail(self, project, id):
        asset = self.asset(project, id)
        if asset["kind"] not in ("image", "export"):
            raise StudioError("This asset has no thumbnail.", 404)
        if not asset.get("thumbnail"):
            if asset["kind"] != "image":
                raise StudioError("This video has no thumbnail.", 404)
            return self.asset_path(project, id, "image")
        path = self.root / project / "thumbnail" / (id + ".png")
        if path.resolve() != path or not path.is_file():
            raise StudioError("The image thumbnail is unavailable.", 404)
        return path

    def update_image(self, project, id, details):
        values = details.model_dump(exclude_none=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT name, metadata_json FROM studio_assets WHERE project_id=? AND id=? AND kind='image'",
                             (project, id)).fetchone()
            if not row:
                raise StudioError("The image is unavailable.", 404)
            name = values.pop("name", row["name"]).strip()
            metadata = json.loads(row["metadata_json"])
            metadata.update(values)
            db.execute("UPDATE studio_assets SET name=?, metadata_json=? WHERE project_id=? AND id=?",
                       (name, json.dumps(metadata, ensure_ascii=False), project, id))
        return self.asset(project, id, "image")

    @staticmethod
    def profile_row(row):
        return {**json.loads(row["profile_json"]), "version": row["version"]}

    def image_profiles(self, project):
        self.project(project)
        with self.connect() as db:
            return [self.profile_row(row) for row in db.execute(
                """SELECT current.profile_json, current.version FROM studio_image_profiles AS current
                   WHERE current.project_id=? AND current.version=(
                       SELECT MAX(version) FROM studio_image_profiles
                       WHERE project_id=current.project_id AND id=current.id
                   ) ORDER BY current.id""", (project,))]

    def image_profile_history(self, project, id):
        self.project(project)
        with self.connect() as db:
            rows = [self.profile_row(row) for row in db.execute(
                """SELECT profile_json, version FROM studio_image_profiles WHERE project_id=? AND id=?
                   ORDER BY version DESC""", (project, id))]
        if not rows:
            raise StudioError("The image profile is unavailable.", 404)
        return rows

    def image_profile(self, project, id, version=None):
        with self.connect() as db:
            row = db.execute("""SELECT profile_json, version FROM studio_image_profiles
                                WHERE project_id=? AND id=? AND (version=? OR ? IS NULL)
                                ORDER BY version DESC LIMIT 1""", (project, id, version, version)).fetchone()
        if not row:
            raise StudioError("The image profile version is unavailable.", 404)
        return self.profile_row(row)

    def save_image_profile(self, project, profile):
        self.project(project)
        for id in profile.reference_ids:
            self.asset_path(project, str(id), "image")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            former = db.execute("""SELECT profile_json, version FROM studio_image_profiles
                                    WHERE project_id=? AND id=? ORDER BY version DESC LIMIT 1""",
                                (project, str(profile.id))).fetchone()
            if former and json.loads(former["profile_json"])["kind"] != profile.kind:
                raise StudioError("A profile cannot change category.")
            version = former["version"] + 1 if former else 1
            db.execute("INSERT INTO studio_image_profiles VALUES (?, ?, ?, ?)",
                       (project, str(profile.id), version, profile.model_dump_json()))
        return {**profile.model_dump(mode="json"), "version": version}

    def video_timeline(self, project):
        self.project(project)
        with self.connect() as db:
            row = db.execute("SELECT timeline_json FROM studio_video_timelines WHERE project_id=?", (project,)).fetchone()
        return video.Timeline.model_validate_json(row["timeline_json"]) if row else video.Timeline()

    def save_video(self, project, timeline):
        self.project(project)
        for scene in timeline.scenes:
            if scene.image_id:
                self.asset(project, str(scene.image_id), "image")
            if scene.narration_id:
                self.asset(project, str(scene.narration_id), "audio")
        for clip in timeline.audio:
            self.asset(project, str(clip.asset_id), "audio")
        updated = timeline.model_copy(update={"revision": timeline.revision + 1})
        with self.connect() as db:
            result = db.execute(
                """INSERT INTO studio_video_timelines (project_id, timeline_json, revision)
                   SELECT ?, ?, ? WHERE ? = 0 OR EXISTS (
                       SELECT 1 FROM studio_video_timelines WHERE project_id=?
                   )
                   ON CONFLICT(project_id) DO UPDATE SET timeline_json=excluded.timeline_json,
                       revision=excluded.revision WHERE studio_video_timelines.revision=?""",
                (project, updated.model_dump_json(), updated.revision, timeline.revision, project, timeline.revision),
            )
            if not result.rowcount:
                raise StudioError("This timeline changed in another editor. Reopen the project to load those changes.", 409)
        return updated

    def retry(self, project, id):
        with self.condition, self.connect() as db:
            row = db.execute(
                "SELECT kind, input_json, state FROM studio_jobs WHERE project_id=? AND id=?", (project, id)
            ).fetchone()
            if not row or row["kind"] not in ("video", "image", illustrated.VIDEO_JOB, illustrated.MODEL_JOB, illustrated.TOPIC_JOB):
                raise StudioError("The media job was not found.", 404)
            if row["state"] not in ("failed", "cancelled"):
                raise StudioError("Only failed or cancelled jobs can be retried.", 409)
            if row["kind"] in (illustrated.VIDEO_JOB, illustrated.MODEL_JOB, illustrated.TOPIC_JOB):
                self.require_queue()
                if row["kind"] == illustrated.MODEL_JOB:
                    self.require_writing_model_slot(db)
                db.execute("UPDATE studio_jobs SET state='queued', progress=0, message='' WHERE id=?", (id,))
                self.condition.notify_all()
        if row["kind"] in (illustrated.VIDEO_JOB, illustrated.MODEL_JOB, illustrated.TOPIC_JOB):
            return self.job(project, id)
        schema = video.VideoRequest if row["kind"] == "video" else images.ImageRequest
        return self.enqueue(project, row["kind"], [schema.model_validate_json(row["input_json"])])[0]

    def adopt_narration(self, project, narration):
        from uuid import UUID

        with self.connect() as db:
            row = db.execute(
                """SELECT narrations.id FROM narrations JOIN project_scripts USING (script_id)
                   WHERE project_scripts.project_id=? AND narrations.id=?""",
                (project, narration)).fetchone()
        if not row:
            raise StudioError("Link this narration’s script to the project first.", 404)
        source = self.voice.narration_file(UUID(narration))
        with TemporaryDirectory(dir=self.directory(project, "work")) as stage:
            target = Path(stage) / "narration.wav"
            shutil.copyfile(source, target)
            return self.record_asset(project, str(uuid4()), "audio", "Script narration",
                                     audio.metadata(target), target, ".wav")

    def draft(self, project):
        self.project(project)
        with self.connect() as db:
            row = db.execute("SELECT draft_json FROM studio_drafts WHERE project_id=?", (project,)).fetchone()
        return Draft.model_validate_json(row["draft_json"]) if row else None

    def save_draft(self, project, draft):
        self.project(project)
        self.validate_scene(project, draft)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO studio_drafts VALUES (?, ?)", (project, draft.model_dump_json()))
        return draft

    def clear_draft(self, project):
        self.project(project)
        with self.connect() as db:
            db.execute("DELETE FROM studio_drafts WHERE project_id=?", (project,))
        return {"deleted": True}

    def copy_composition(self, project, title, draft):
        self.project(project)
        self.validate_scene(project, draft)
        project_id = str(uuid4())
        references = {}
        composition = draft.composition
        with self.condition:
            try:
                with self.connect() as db:
                    db.execute("INSERT INTO projects (id, title, created_at) VALUES (?, ?, ?)",
                               (project_id, title, _now()))
                    for id in dict.fromkeys(filter(None, (str(draft.asset_id) if draft.asset_id else None,
                                                          composition.artwork, composition.backdrop))):
                        asset = self.asset(project, id)
                        source = self.asset_path(project, id)
                        clone = str(uuid4())
                        references[id] = clone
                        destination = self.directory(project_id, asset["kind"]) / (clone + source.suffix)
                        shutil.copyfile(source, destination)
                        metadata = {k: v for k, v in asset.items() if k not in ("id", "project_id", "kind", "name", "created_at")}
                        db.execute("INSERT INTO studio_assets VALUES (?, ?, ?, ?, ?, ?)",
                                   (clone, project_id, asset["kind"], asset["name"],
                                    json.dumps(metadata, ensure_ascii=False), _now()))
                    copied = Draft.model_validate({
                        **draft.model_dump(), "asset_id": references.get(str(draft.asset_id)),
                        "composition": {**composition.model_dump(), "artwork": references.get(composition.artwork),
                                        "backdrop": references.get(composition.backdrop)},
                    })
                    db.execute("INSERT INTO studio_drafts VALUES (?, ?)", (project_id, copied.model_dump_json()))
            except BaseException:
                path = self.root / project_id
                if path.is_dir() and path.resolve() == path:
                    shutil.rmtree(path)
                raise
        return {"id": project_id, "title": title}

    def validate_scene(self, project, draft):
        if draft.asset_id:
            self.asset(project, str(draft.asset_id), "audio")
        if draft.composition.artwork:
            self.asset(project, draft.composition.artwork, "image")
        if draft.composition.backdrop and self.asset(project, draft.composition.backdrop)["kind"] not in ("image", "background"):
            raise StudioError("Select a still image or video for the background.")

    def presets(self):
        with self.connect() as db:
            return motion.templates() + [dict(id=row["id"], custom=True, **json.loads(row["preset_json"]))
                                         for row in db.execute("SELECT * FROM studio_presets ORDER BY name")]

    def save_preset(self, payload):
        # Reusable presets keep the timeline and leave project-owned media references in the project draft.
        clean = payload.model_copy(update={"composition": payload.composition.model_copy(
            update={"artwork": None, "backdrop": None})})
        id = str(uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO studio_presets VALUES (?, ?, ?)", (id, clean.name, clean.model_dump_json()))
        return dict(id=id, custom=True, **clean.model_dump())

    def delete_preset(self, id):
        with self.connect() as db:
            db.execute("DELETE FROM studio_presets WHERE id=?", (id,))
        return {"deleted": True}

    def profiles(self, project=None):
        with self.connect() as db:
            if project:
                rows = db.execute(
                    """SELECT p.*, x.project_id IS NOT NULL AS assigned FROM studio_profiles p
                       LEFT JOIN studio_project_profiles x ON p.id = x.profile_id AND x.project_id = ?
                       ORDER BY name""", (project,))
            else:
                rows = db.execute("SELECT * FROM studio_profiles ORDER BY name")
            profiles = [_record(row) for row in rows]
        ukrainian_ready = "uk" in self.tools.ready_languages(holos.MODEL_ID)
        transfer_languages = self.tools.ready_languages(openvoice.MODEL_ID) + self.tools.ready_languages(zonos.MODEL_ID)
        for profile in profiles:
            if ukrainian_ready and profile["language"] == "uk":
                profile.update(model_id=holos.MODEL_ID, languages=["uk"])
            profile["languages"] = sorted(set(profile["languages"]) | set(transfer_languages))
        return profiles

    def reference_path(self, id):
        path = self.root / "profiles" / id / "reference.wav"
        if path.resolve() != path or not path.is_file():
            raise StudioError("The saved voice reference is missing.", 404)
        return path

    def additional_references(self, id):
        with self.connect() as db:
            row = db.execute("SELECT metadata_json FROM studio_profiles WHERE id=?", (id,)).fetchone()
        if not row:
            raise StudioError("The saved voice profile is missing.", 404)
        count = json.loads(row[0]).get("reference_count", 1)
        paths = [self.root / "profiles" / id / "recordings" / f"{index}.wav" for index in range(2, count + 1)]
        if any(path.resolve() != path or not path.is_file() for path in paths):
            raise StudioError("A recording in this voice profile is missing.", 404)
        return paths

    def record(self, project, seconds):
        self.project(project)
        if not self.recording_lock.acquire(blocking=False):
            raise StudioError("Finish the current microphone recording first.", 409)
        try:
            blob = audio.record_microphone(seconds, self.stop)
            return {"content_base64": base64.b64encode(blob).decode("ascii")}
        finally:
            self.recording_lock.release()

    def create_profile(self, project, payload):
        self.project(project)
        self.tools.require("ffmpeg", "audio-analysis")
        name = payload.name.strip()
        if not name:
            raise StudioError("Name your voice profile.")
        with TemporaryDirectory(dir=self.directory(project, "work")) as stage:
            stage = Path(stage)
            recordings = [payload, *payload.additional_recordings]
            qualities = []
            total_bytes = 0
            for index, clip in enumerate(recordings, 1):
                source, processed = stage / f"original-{index}", stage / f"{index}.wav"
                try:
                    content = _decode(clip.content_base64)
                    total_bytes += len(content)
                    if total_bytes > IMPORT_LIMIT:
                        raise StudioError("The recordings must fit within 32 MiB in total.")
                    source.write_bytes(content)
                    trim = (clip.start, clip.end if clip.end is not None else clip.start + 60.001)
                    audio.convert(source, processed, trim=trim, denoise=clip.denoise)
                    samples, rate, quality = audio.reference_quality(processed, clip.trim_silence)
                    audio.write_wave(processed, samples, rate)
                    qualities.append(quality)
                except StudioError as error:
                    raise StudioError(f"Recording {index}. {error}", error.status_code) from error
            id = str(uuid4())
            destination = self.directory("profiles", id)
            original = destination / "original"
            reference = destination / "reference.wav"
            profile = dict(
                id=id, name=name, language=payload.language, model_id=POCKET if payload.language == "en" else None,
                languages=["en"] if payload.language == "en" else [], created_at=_now(), authorization_confirmed=True,
                reference_audio=f"studio/profiles/{id}/reference.wav",
                settings={"start": payload.start, "end": payload.end, "trim_silence": payload.trim_silence,
                          "denoise": payload.denoise}, quality=qualities[0],
                reference_count=len(recordings), reference_duration_seconds=sum(q["duration_seconds"] for q in qualities),
            )
            try:
                (stage / "original-1").replace(original)
                (stage / "1.wav").replace(reference)
                if len(recordings) > 1:
                    extra = destination / "recordings"
                    extra.mkdir(mode=0o700)
                    for index in range(2, len(recordings) + 1):
                        for filename in (f"original-{index}", f"{index}.wav"):
                            (stage / filename).replace(extra / filename)
                profile["waveform"] = audio.analyze(reference).waveform.tolist()
                with self.connect() as db:
                    db.execute("INSERT INTO studio_profiles VALUES (?, ?, ?, ?)",
                               (id, name, payload.language, json.dumps(profile, ensure_ascii=False)))
                    db.execute("INSERT INTO studio_project_profiles VALUES (?, ?)", (project, id))
            except BaseException:
                shutil.rmtree(destination)
                raise
        return next(item for item in self.profiles(project) if item["id"] == id)

    def assign_profile(self, project, id, assigned):
        self.project(project)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM studio_profiles WHERE id=?", (id,)).fetchone():
                raise StudioError("Voice profile was not found.", 404)
            if assigned:
                db.execute("INSERT OR IGNORE INTO studio_project_profiles VALUES (?, ?)", (project, id))
            else:
                db.execute("DELETE FROM studio_project_profiles WHERE project_id=? AND profile_id=?", (project, id))
        return {"assigned": assigned}

    def delete_profile(self, id):
        with self.condition:
            with self.connect() as db:
                jobs = db.execute("SELECT input_json FROM studio_jobs WHERE kind='speech' AND state IN ('running','queued')")
                if any(json.loads(row[0])["voice"] == id for row in jobs):
                    raise StudioError("Cancel or finish the queued speech using this profile first.", 409)
                path = self.root / "profiles" / id
                if path.exists() and path.resolve() == path:
                    shutil.rmtree(path)
                db.execute("DELETE FROM studio_profiles WHERE id=?", (id,))
        return {"deleted": True}

    def verify_speech(self, project, request):
        provider = PROVIDERS[request.model_id]
        if request.language not in provider.languages:
            raise StudioError("This model does not support the selected language.")
        if provider.research_only and not request.noncommercial_confirmed:
            raise StudioError("Confirm that this generation is for noncommercial evaluation.")
        if provider.uses_reference(request.voice):
            profile = next((p for p in self.profiles(project)
                            if p["id"] == request.voice and p["assigned"] and p["language"] in provider.reference_languages), None)
            if not profile or not profile["authorization_confirmed"]:
                raise StudioError("Select an assigned, authorized voice profile for this language.")
        elif request.voice not in dict(provider.voices):
            raise StudioError("Choose a voice offered by this model.")
        entry = next(item for item in self.tools.snapshot() if item["id"] == provider.model_id)
        if request.language not in entry["ready_languages"]:
            base = entry["required_models"].get(request.language)
            if base and entry["state"] == "installed" and entry["enabled"] and entry["compatible"]:
                raise StudioError(f"Install and enable {base} in Models & Tools for this output language.", 409)
            raise StudioError("Install and enable the selected voice model in Models & Tools.", 409)

    def require_queue(self):
        if not self.queue_ready.is_set() or self.stop.is_set():
            raise StudioError("The local job queue is unavailable. Restart AI Blogger Studio and try again.", 503)

    def require_writing_model_slot(self, db):
        active = db.execute(
            "SELECT 1 FROM studio_jobs WHERE kind=? AND state IN ('queued', 'running') LIMIT 1",
            (illustrated.MODEL_JOB,),
        ).fetchone()
        if active:
            raise StudioError("A MamayLM model download is already queued or running.", 409)

    def enqueue(self, project, kind, requests):
        self.project(project)
        if not self.queue_ready.wait(timeout=1.2) and self.thread.is_alive():
            raise StudioError("Another AI Blogger Studio instance may be using the job queue. Quit it and try again.", 409)
        if kind == "video":
            for request in requests:
                video.validate(self, project, request)
        if kind == "image":
            for request in requests:
                images.validate_request(self, project, request)
        if kind == illustrated.VIDEO_JOB:
            requests = [illustrated.apply_defaults(self.data, request, project) for request in requests]
            requests = [topic_library.snapshot_topic(self, project, request) for request in requests]
            for request in requests:
                if request.writing_settings is None:
                    request.writing_settings = load_settings(self.data, project)
                if request.speech_settings is None:
                    request.speech_settings = illustrated.load_speech_settings(self.data, project)
                illustrated.validate(self, request, project)
        elif kind == illustrated.MODEL_JOB:
            illustrated.require_host()
        elif kind == illustrated.TOPIC_JOB:
            requests = [topic_library.prepare_batch(self, project, request) for request in requests]
        with self.condition, self.connect() as db:
            self.require_queue()
            jobs = []
            for request in requests:
                if kind == illustrated.MODEL_JOB:
                    self.require_writing_model_slot(db)
                elif kind == "speech":
                    self.verify_speech(project, request)
                elif kind == "render":
                    self.validate_scene(project, request)
                id = str(uuid4())
                db.execute("INSERT INTO studio_jobs (id, project_id, kind, input_json, state, created_at) VALUES (?, ?, ?, ?, 'queued', ?)",
                           (id, project, kind, request.model_dump_json(), _now()))
                jobs.append(id)
            self.condition.notify_all()
        return [self.job(project, id) for id in jobs]

    def job(self, project, id):
        with self.connect() as db:
            row = db.execute(
                f"SELECT {JOB_FIELDS} FROM studio_jobs WHERE project_id=? AND id=?",
                (project, id)).fetchone()
        if not row:
            raise StudioError("This project job was not found.", 404)
        return dict(row)

    def jobs(self, project):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                f"""SELECT {JOB_FIELDS} FROM studio_jobs
                   WHERE project_id=? ORDER BY created_at DESC LIMIT 100""", (project,))]

    def cancel(self, project, id):
        with self.condition:
            job = self.job(project, id)
            if job["state"] == "queued":
                self.update(id, "cancelled", 0)
            if job["state"] == "running" and self.active_job == id:
                self.user_cancel.set()
        return self.job(project, id)

    def update(self, id, state, progress, message=""):
        with self.connect() as db:
            db.execute("UPDATE studio_jobs SET state=?, progress=?, message=? WHERE id=?", (state, progress, message, id))

    def check(self):
        if self.stop.is_set() or self.user_cancel.is_set():
            raise export.Cancelled()

    @contextmanager
    def queue_lease(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / ".queue.lock"
        if path.resolve() != path:
            raise StudioError("The studio queue lock path is unsafe.", 500)
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

    def _worker(self):
        while not self.stop.is_set():
            try:
                with self.queue_lease():
                    with self.connect() as db:
                        db.execute("UPDATE studio_jobs SET state='queued', progress=0 WHERE state='running'")
                    self.queue_ready.set()
                    try:
                        self._run_queue()
                    finally:
                        self.queue_ready.clear()
                    return
            except BlockingIOError:
                self.stop.wait(timeout=1)
            except OSError as error:
                if os.name == "nt" and error.errno == 13:
                    self.stop.wait(timeout=1)
                else:
                    LOGGER.exception("The persistent studio queue stopped")
                    return

    def _run_queue(self):
        while True:
            with self.condition, self.connect() as db:
                if self.stop.is_set():
                    return
                row = db.execute("SELECT * FROM studio_jobs WHERE state='queued' ORDER BY created_at, id LIMIT 1").fetchone()
                if row is None:
                    self.condition.wait(timeout=1)
                    continue
                self.user_cancel.clear()
                self.active_job = row["id"]
                db.execute("UPDATE studio_jobs SET state='running', progress=0, message='' WHERE id=?", (self.active_job,))
            try:
                existing = next((asset for asset in self.assets(row["project_id"]) if asset["id"] == row["id"]), None)
                if existing is None:
                    self._run(row)
                if row["kind"] == illustrated.VIDEO_JOB:
                    topic_library.mark_used(self, row["project_id"], json.loads(row["input_json"]).get("topic_id"))
                self.update(row["id"], "completed", 1)
                if row["kind"] == illustrated.TOPIC_JOB:
                    topic_library.resume_pending_batch(self, row["id"])
            except export.Cancelled:
                self.update(row["id"], "queued" if self.stop.is_set() else "cancelled", 0)
            except Exception as error:
                LOGGER.exception("Studio job %s failed", row["id"])
                self.update(row["id"], "failed", 0, str(error) if isinstance(error, StudioError)
                            else "The local job failed. See the backend log for details.")
            finally:
                with self.condition:
                    self.active_job = None

    def _run(self, row):
        project, id = row["project_id"], row["id"]
        if row["kind"] == illustrated.TOPIC_JOB:
            topic_library.generate_batch(self, project, id, topic_library.BatchRequest.model_validate_json(row["input_json"]))
            return
        if row["kind"] == illustrated.VIDEO_JOB:
            illustrated.generate(self, project, id, illustrated.IllustratedRequest.model_validate_json(row["input_json"]))
            return
        if row["kind"] == illustrated.MODEL_JOB:
            illustrated.install(self, id)
            return
        with TemporaryDirectory(dir=self.directory(project, "work")) as stage:
            stage = Path(stage)
            if row["kind"] == "speech":
                request = SpeechRequest.model_validate_json(row["input_json"])
                self.synthesize(project, id, request, stage)
                return
            if row["kind"] == "video":
                video.render(self, project, id, video.VideoRequest.model_validate_json(row["input_json"]), stage)
                return
            if row["kind"] == "image":
                images.generate(self, project, id, images.ImageRequest.model_validate_json(row["input_json"]), stage)
                return
            request = RenderRequest.model_validate_json(row["input_json"])
            self.tools.require("audio-analysis", "motion-engine", "ffmpeg")
            source = self.asset_path(project, str(request.asset_id), "audio")
            meta = self.asset(project, str(request.asset_id), "audio")
            suffix = "zip" if request.settings.format == "png" else request.settings.format
            target = stage / ("export." + suffix)
            with self.renderer(project, request, source, meta) as renderer:
                export.export(target, renderer, source, meta["duration_seconds"], request.settings,
                              self.check, lambda ratio: self.update(id, "running", ratio))
            self.check()
            self.record_asset(project, id, "export", meta["name"], {
                "extension": suffix, "source_id": str(request.asset_id), "duration_seconds": meta["duration_seconds"],
                "research_only": meta.get("research_only", False),
                "composition": request.composition.model_dump(), "size_bytes": target.stat().st_size,
            }, target, "." + suffix)

    def synthesize(self, project, id, request, stage):
        self.verify_speech(project, request)
        provider = PROVIDERS[request.model_id]
        reference = self.reference_path(request.voice) if provider.uses_reference(request.voice) else None
        additional = self.additional_references(request.voice) if reference and provider.multi_reference else ()
        with self.tools.inference(request.model_id) as model:
            self.check()
            command = worker_command(model, request, stage, reference, additional)
            separate_group = os.name == "posix" and provider.native_subprocesses
            with (stage / "worker.log").open("wb") as errors, subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=errors,
                env=worker_environment(request.model_id), start_new_session=separate_group,
            ) as process:
                deadline = time.monotonic() + 1200
                try:
                    while process.poll() is None:
                        self.check()
                        if time.monotonic() >= deadline:
                            raise StudioError("Speech generation timed out. Try a smaller batch.", 502)
                        try:
                            process.wait(timeout=.25)
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    if process.poll() is None:
                        if separate_group:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        else:
                            process.kill()
                        process.wait()
                if process.returncode:
                    message = stage / "error.txt"
                    if message.is_file():
                        raise StudioError(message.read_text(encoding="utf-8"), 502)
                    LOGGER.warning("Speech worker exited with code %s: %s", process.returncode,
                                   (stage / "worker.log").read_text(errors="replace")[-2000:])
                    raise StudioError("The selected model could not generate speech.", 502)
        target = stage / "audio.wav"
        meta = audio.metadata(target)
        measured = json.loads((stage / "result.json").read_text(encoding="utf-8"))
        self.check()
        self.record_asset(project, id, "audio", request.name, {
            **meta, **measured, "language": request.language, "model_id": request.model_id,
            "research_only": PROVIDERS[request.model_id].research_only,
            "cloned": reference is not None,
            "requested_format": request.format, "voice": request.voice, "speed": request.speed,
        }, target, ".wav")

    def renderer(self, project, request, path, meta, preview=False):
        self.validate_scene(project, request)
        composition = request.composition
        artwork = self.asset_path(project, composition.artwork, "image") if composition.artwork else None
        backdrop = self.asset_path(project, composition.backdrop) if composition.backdrop else None
        return motion.Renderer(composition, audio.analyze(path), meta.get("cues", ()), artwork=artwork,
                               backdrop=backdrop, transparent=request.settings.transparent, preview=preview)

    def preview(self, project, request):
        self.tools.require("audio-analysis", "motion-engine", "ffmpeg")
        path = self.asset_path(project, str(request.asset_id), "audio")
        meta = self.asset(project, str(request.asset_id), "audio")
        index = min(int(request.time * request.composition.fps),
                    max(0, int(meta["duration_seconds"] * request.composition.fps - 0.000001)))
        time = index / request.composition.fps
        key = (project, str(request.asset_id), time, request.composition.model_dump_json(), request.settings.transparent)
        with self.preview_lock:
            if key not in self.previews:
                with self.renderer(project, request, path, meta, preview=True) as renderer:
                    buffer = BytesIO()
                    renderer.frame(time).save(buffer, format="PNG")
                self.previews[key] = base64.b64encode(buffer.getvalue()).decode("ascii")
                if len(self.previews) > 60:
                    self.previews.popitem(last=False)
            self.previews.move_to_end(key)
            return {"image": "data:image/png;base64," + self.previews[key], "time": time}

    def download_audio(self, project, id, format):
        source = self.asset_path(project, id, "audio")
        if format == "wav":
            return source
        self.tools.require("ffmpeg")
        directory = self.directory(project, "audio")
        target = directory / (id + ".mp3")
        if not target.is_file():
            with TemporaryDirectory(dir=directory) as stage:
                temporary = Path(stage) / "audio.mp3"
                metadata = audio.metadata(source)
                audio.convert(source, temporary, rate=metadata["sample_rate"], channels=metadata["channels"])
                os.replace(temporary, target)
        return target

    def subtitle_file(self, project, id, format):
        asset = self.asset(project, id)
        composition = None
        if asset["kind"] == "export":
            composition = motion.Composition.model_validate(asset["composition"])
            asset = self.asset(project, asset["source_id"], "audio")
        if not asset.get("cues"):
            raise StudioError("This asset has no timed speech captions.", 404)
        return export.subtitles(asset["cues"], format, composition)
