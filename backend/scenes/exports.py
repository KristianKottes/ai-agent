"""Manage imported scene clips and completed script exports."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import sqlite3
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Optional

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.avatar.encoder import FPS, AvatarError
from backend.avatar.service import _binary
from backend.runtime.downloader import sha256_file
from backend.scenes.compose import CaptionAlignmentError, CompositionError, Scene, Timeline, render_timeline, run_media, stream_info

MAX_IMPORT_BYTES = 32 * 1024 * 1024
MAX_FRAMES = 600 * FPS
_EXPORT_LOCK = threading.Lock()


class ExportError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class MediaImport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    content_base64: str = Field(min_length=1, max_length=((MAX_IMPORT_BYTES + 2) // 3) * 4)


class AssetImport(MediaImport):
    kind: Literal["image", "video"]


class ExportScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["asset", "presenter"]
    id: uuid.UUID
    frames: int = Field(strict=True, ge=1, le=MAX_FRAMES)
    transition: Literal["cut", "fade_black"] = "cut"


class ExportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["landscape", "vertical", "square"] = "landscape"
    quality: Literal["draft", "standard", "high"] = "standard"
    captions: bool = True
    music_id: Optional[uuid.UUID] = None
    music_volume: float = Field(default=0.12, ge=0, le=0.35)


class ExportRequest(ExportSettings):
    narration_id: uuid.UUID
    scenes: list[ExportScene] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def presenter_opening(self):
        if any(scene.source == "presenter" for scene in self.scenes[1:]):
            raise ValueError("A saved presenter clip can be used once, at the start of its narration.")
        return self


class DraftScene(ExportScene):
    key: int = Field(strict=True, ge=0)
    name: str = Field(min_length=1, max_length=160)
    max_frames: Optional[int] = Field(default=None, strict=True, ge=1, le=MAX_FRAMES)
    presenter_narration_id: Optional[uuid.UUID] = None

    @model_validator(mode="after")
    def presenter_binding(self):
        if (self.source == "presenter") != (self.presenter_narration_id is not None):
            raise ValueError("A presenter scene must retain its original narration ID.")
        return self


class ExportDraft(ExportSettings):
    scenes: list[DraftScene] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def scene_order(self):
        if len({scene.key for scene in self.scenes}) != len(self.scenes):
            raise ValueError("Draft scene keys must be unique.")
        if any(scene.source == "presenter" for scene in self.scenes[1:]):
            raise ValueError("The presenter can appear once, at the start of the draft.")
        return self


class ExportLibrary:
    def __init__(self, data: Path, database_connection):
        self.data = data
        self.database_connection = database_connection

    def load_draft(self, script_id: str) -> ExportDraft:
        with self.database_connection() as db:
            row = db.execute("SELECT draft_json FROM video_drafts WHERE script_id=?", (script_id,)).fetchone()
        return ExportDraft.model_validate_json(row["draft_json"]) if row else ExportDraft()

    def save_draft(self, script_id: str, draft: ExportDraft) -> ExportDraft:
        with self.database_connection() as db:
            db.execute(
                """INSERT INTO video_drafts (script_id, draft_json) VALUES (?, ?)
                   ON CONFLICT(script_id) DO UPDATE SET draft_json=excluded.draft_json""",
                (script_id, draft.model_dump_json()),
            )
        return draft

    def _folder(self, key: str, *, create: bool = False) -> Path:
        folder = self.data / key
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise ExportError("The media storage folder is unavailable.", 500)
        if create:
            folder.mkdir(mode=0o700, exist_ok=True)
        return folder

    def _path(self, key: str, record_id: str, suffix: str) -> Path | None:
        path = self._folder(key) / f"{record_id}{suffix}"
        if path.is_symlink() or not path.is_file():
            return None
        return path

    def _publish(self, key: str, owner: str, record_id: str, source: Path, destination: Path, insert, sidecars=()):
        files = [(src, target, sha256_file(src), src.stat().st_size)
                 for src, target in ((source, destination), *sidecars)]
        linked = []
        try:
            with self.database_connection() as db:
                now = datetime.now(timezone.utc).isoformat()
                db.executemany(
                    """INSERT INTO managed_files
                       (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [(key, dst.name, owner, record_id, digest, size, now) for _, dst, digest, size in files],
                )
                insert(db)
                for src, dst, _, _ in files:
                    os.link(src, dst)
                    linked.append(dst)
        except (sqlite3.Error, OSError):
            for path in linked:
                path.unlink(missing_ok=True)
            raise

    def _registered(self, key: str, owner: str, record_id: str, filename: str, db) -> bool:
        return db.execute(
            """SELECT 1 FROM managed_files
               WHERE root_key=? AND owner_type=? AND owner_id=? AND relative_path=?""",
            (key, owner, record_id, filename),
        ).fetchone() is not None

    def import_asset(self, script_id: str, payload: AssetImport) -> dict:
        suffix = ".png" if payload.kind == "image" else ".mp4"
        folder = self._folder("scene_assets", create=True)
        with TemporaryDirectory(prefix=".import-", dir=folder) as temp:
            source = Path(temp) / f"source{suffix}"
            try:
                content = base64.b64decode(payload.content_base64, validate=True)
                if not 0 < len(content) <= MAX_IMPORT_BYTES:
                    raise ExportError("Choose a PNG or MP4 up to 32 MiB.")
                source.write_bytes(content)
                if payload.kind == "image":
                    with Image.open(source, formats=("PNG",)) as image:
                        if max(image.size) > 8192 or getattr(image, "n_frames", 1) != 1:
                            raise ExportError("Choose a still PNG up to 8192 pixels per side.")
                        image.verify()
                    frames = None
                else:
                    ffprobe = _binary("ffprobe")
                    if not ffprobe:
                        raise ExportError("FFprobe is required to import MP4 clips.", 503)
                    info = stream_info(ffprobe, source, "mov", "V:0")
                    duration = float(info["duration"])
                    if not math.isfinite(duration) or duration < 1 / FPS or max(info["width"], info["height"]) > 8192:
                        raise ExportError("Choose an MP4 with a playable video stream.")
                    frames = min(MAX_FRAMES, math.floor(duration * FPS + 1e-6))
            except (binascii.Error, Image.DecompressionBombError, OSError, ValueError, KeyError,
                    TypeError, CompositionError, subprocess.SubprocessError) as error:
                raise ExportError("The selected file could not be read as a PNG or MP4.") from error
            return self.publish_scene(script_id, source, payload.name, payload.kind, frames)

    def publish_scene(self, script_id: str, source: Path, name: str, kind: str, frames, on_publish=None) -> dict:
        asset_id = str(uuid.uuid4())
        suffix = ".png" if kind == "image" else ".mp4"
        target = self._folder("scene_assets", create=True) / f"{asset_id}{suffix}"

        def insert(db):
            db.execute(
                "INSERT INTO scene_assets (id, script_id, name, kind, max_frames) VALUES (?, ?, ?, ?, ?)",
                (asset_id, script_id, name, kind, frames),
            )
            if on_publish:
                on_publish(db, asset_id)

        self._publish("scene_assets", "scene_asset", asset_id, source, target, insert)
        return {"id": asset_id, "name": name, "kind": kind, "max_frames": frames}

    def list_assets(self, script_id: str) -> list[dict]:
        with self.database_connection() as db:
            rows = db.execute(
                "SELECT id, name, kind, max_frames FROM scene_assets WHERE script_id=? ORDER BY rowid",
                (script_id,),
            ).fetchall()
        return [dict(row) for row in rows if self._path(
            "scene_assets", row["id"], ".png" if row["kind"] == "image" else ".mp4",
        )]

    def import_music(self, script_id: str, payload: MediaImport) -> dict:
        audio_id = str(uuid.uuid4())
        ffprobe = _binary("ffprobe")
        if not ffprobe:
            raise ExportError("FFprobe is required to import background audio.", 503)
        folder = self._folder("scene_music", create=True)
        target = folder / f"{audio_id}.wav"
        with TemporaryDirectory(prefix=".music-", dir=folder) as temp:
            source = Path(temp) / target.name
            try:
                content = base64.b64decode(payload.content_base64, validate=True)
                if not 0 < len(content) <= MAX_IMPORT_BYTES:
                    raise ExportError("Choose a WAV file up to 32 MiB.")
                source.write_bytes(content)
                audio = stream_info(ffprobe, source, "wav", "a:0")
                duration = float(audio["duration"])
                if not math.isfinite(duration) or duration <= 0:
                    raise ExportError("The WAV contains no playable audio.")
            except (binascii.Error, OSError, ValueError, KeyError, CompositionError, subprocess.SubprocessError) as error:
                raise ExportError("The file could not be read as a WAV.") from error
            self._publish("scene_music", "scene_music", audio_id, source, target, lambda db: db.execute(
                "INSERT INTO scene_music (id, script_id, name, duration_seconds) VALUES (?, ?, ?, ?)",
                (audio_id, script_id, payload.name, duration),
            ))
        return {"id": audio_id, "name": payload.name, "duration_seconds": duration}

    def list_music(self, script_id: str) -> list[dict]:
        with self.database_connection() as db:
            rows = db.execute("SELECT id, name, duration_seconds FROM scene_music WHERE script_id=? ORDER BY rowid",
                              (script_id,)).fetchall()
        return [dict(row) for row in rows if self._path("scene_music", row["id"], ".wav")]

    def _music_path(self, script_id: str, audio_id: str) -> Path:
        path = self._path("scene_music", audio_id, ".wav")
        with self.database_connection() as db:
            belongs = db.execute("SELECT 1 FROM scene_music WHERE id=? AND script_id=?", (audio_id, script_id)).fetchone()
            if path is None or not belongs or not self._registered("scene_music", "scene_music", audio_id, path.name, db):
                raise ExportError("The selected background WAV is missing or belongs to a different script.", 404)
        return path

    def _source(self, script_id: str, narration_id: str, scene: ExportScene) -> tuple[str, Path]:
        scene_id = str(scene.id)
        with self.database_connection() as db:
            if scene.source == "presenter":
                key, owner, kind = "avatar", "avatar_render", "video"
                row = db.execute(
                    "SELECT id FROM avatar_renders WHERE id=? AND narration_id=?", (scene_id, narration_id),
                ).fetchone()
            else:
                key, owner = "scene_assets", "scene_asset"
                row = db.execute(
                    "SELECT kind FROM scene_assets WHERE id=? AND script_id=?", (scene_id, script_id),
                ).fetchone()
                kind = row["kind"] if row else "video"
            suffix = ".png" if kind == "image" else ".mp4"
            path = self._path(key, scene_id, suffix) if row else None
            if path is None or not self._registered(key, owner, scene_id, path.name, db):
                raise ExportError("A selected clip is missing or belongs to a different script or narration.", 404)
        return kind, path

    def render(self, script_id: str, payload: ExportRequest, wav: Path, transcript: str, *, on_publish=None) -> dict:
        if not _EXPORT_LOCK.acquire(blocking=False):
            raise ExportError("Another MP4 is exporting. Try again when it finishes.", 409)
        try:
            return self._render(script_id, payload, wav, transcript, on_publish)
        finally:
            _EXPORT_LOCK.release()

    def _render(self, script_id: str, payload: ExportRequest, wav: Path, transcript: str, on_publish) -> dict:
        folder = self._folder("exports", create=True)
        export_id = str(uuid.uuid4())
        narration_id = str(payload.narration_id)
        target = folder / f"{export_id}.mp4"
        with TemporaryDirectory(prefix=".export-", dir=folder) as temp:
            root = Path(temp)
            scenes = []
            for index, entry in enumerate(payload.scenes):
                kind, asset = self._source(script_id, narration_id, entry)
                alias = f"scene-{index}{asset.suffix}"
                os.link(asset, root / alias)
                scenes.append(Scene(kind=kind, asset=alias, frames=entry.frames, transition=entry.transition))
            os.link(wav, root / "narration.wav")
            music = None
            if payload.music_id and payload.music_volume:
                music = "background.wav"
                os.link(self._music_path(script_id, str(payload.music_id)), root / music)
            try:
                timeline = Timeline(
                    format=payload.format, quality=payload.quality, narration="narration.wav",
                    scenes=scenes, transcript=transcript if payload.captions else None,
                    background_music=music, music_volume=payload.music_volume,
                )
                output = root / target.name
                metrics = render_timeline(timeline, root, output)
                sidecars = []
                if metrics["caption_count"]:
                    ffmpeg = _binary("ffmpeg")
                    for extension in ("srt", "ass"):
                        captions = output.with_suffix(f".{extension}")
                        run_media([ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
                                   "-i", str(output), "-map", "0:s:0", "-c:s", extension, str(captions)], 30)
                        sidecars.append((captions, target.with_suffix(f".{extension}")))
            except ValidationError as error:
                raise ExportError(error.errors()[0]["msg"]) from error
            except AvatarError as error:
                raise ExportError(str(error), error.status_code) from error
            except CaptionAlignmentError as error:
                raise ExportError("English captions could not be aligned. Check the saved script or turn off English captions.") from error
            except CompositionError as error:
                raise ExportError(str(error)) from error
            except (KeyError, ValueError) as error:
                raise ExportError("A selected asset could not be decoded.") from error
            except subprocess.TimeoutExpired as error:
                raise ExportError("The export timed out. Try a shorter timeline.", 504) from error
            result = {
                **metrics, "path": str(target), "id": export_id, "format": payload.format,
                "size_bytes": output.stat().st_size, "narration_id": narration_id,
                "subtitle_files": {path.suffix[1:]: str(path) for _, path in sidecars},
            }
            def register(db):
                db.execute("INSERT INTO video_exports (id, script_id, narration_id, result_json) VALUES (?, ?, ?, ?)",
                           (export_id, script_id, narration_id, json.dumps(result)))
                if on_publish is not None:
                    on_publish(db, result)

            self._publish("exports", "video_export", export_id, output, target, register, sidecars)
        return result

    def list_exports(self, script_id: str) -> list[dict]:
        with self.database_connection() as db:
            rows = db.execute(
                "SELECT id, result_json FROM video_exports WHERE script_id=? ORDER BY rowid DESC", (script_id,),
            ).fetchall()
        return [json.loads(row["result_json"]) for row in rows if self.export_file(row["id"]) is not None]

    def export_file(self, export_id: str) -> Path | None:
        path = self._path("exports", export_id, ".mp4")
        if path is None:
            return None
        with self.database_connection() as db:
            if self._registered("exports", "video_export", export_id, path.name, db):
                return path
        return None
