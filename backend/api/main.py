"""Authenticated loopback API for project, platform, and runtime management."""

from __future__ import annotations

import base64
import hmac
import io
import json
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import AsyncIterator, Iterator, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.ai.contracts import (
    DirectorDocument,
    ModelInstallRequest,
    ModelStatus,
    ScriptDocument,
    ScriptEdit,
    ScriptRequest,
    TopicBatchRequest,
    TopicBatchResult,
    TopicRecord,
    TopicUpdate,
)
from backend.ai.generation import GenerationError, LocalCreator
from backend.ai.model_manager import LocalModelManager, ModelInstallError
from backend.avatar import realistic, service as avatar
from backend.avatar.portrait_model import PortraitModel
from backend.platform.detect import collect_system_report
from backend.platform.models import SystemReport
from backend.platform.paths import application_directories
from backend.runtime.downloader import sha256_file
from backend.scenes.exports import AssetImport, ExportDraft, ExportError, ExportLibrary, ExportRequest, MediaImport
from backend.scenes.jobs import SceneJobs, SceneRequest
from backend.scenes.project_video import ProjectVideoJobs, ProjectVideoRequest, ResumeProjectVideo
from backend.studio.api import router as studio_router
from backend.studio.service import ProjectStudio, migrate as migrate_studio
from backend.voice.service import PREVIEW_TEXT, VoiceError, VoiceService

SCHEMA_VERSION = 15
BACKEND_VERSION = "0.1.0"


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)


class Project(BaseModel):
    id: str
    title: str
    created_at: datetime
    script_id: Optional[str] = None
    topic_id: Optional[str] = None


class ProjectScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: uuid.UUID


class Health(BaseModel):
    status: str
    backend_version: str
    schema_version: int


class AppSettings(BaseModel):
    data_directory: str
    cache_directory: str
    config_directory: str
    logs_directory: str
    temp_directory: str
    backend_version: str
    schema_version: int
    authentication_enabled: bool


class LicenseAcceptance(BaseModel):
    accepted_license: bool


class ComponentRemoval(BaseModel):
    confirmation: str = Field(min_length=1, max_length=80)


class RuntimeCleanup(BaseModel):
    confirmation: str = Field(min_length=1, max_length=80)


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str = Field(min_length=1, max_length=24)
    speed: float = Field(default=1.0, ge=0.75, le=1.5)


def data_directory() -> Path:
    return application_directories().data.resolve()


def database_path() -> Path:
    return data_directory() / "ai-blogger-studio.sqlite3"


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(database_path(), timeout=5.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


@contextmanager
def database_connection() -> Iterator[sqlite3.Connection]:
    db = connect()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize_database() -> None:
    root = data_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(root, 0o700)
    path = database_path()
    path.touch(mode=0o600, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o600)
    with database_connection() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 120),
                created_at TEXT NOT NULL
            )"""
        )
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (1, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS installed_components (
                component_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state = 'installed'),
                installation_path TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                installed_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS managed_files (
                root_key TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                registered_at TEXT NOT NULL,
                PRIMARY KEY(root_key, relative_path)
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS component_operations (
                id TEXT PRIMARY KEY,
                component_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                state TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (2, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS topics (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL CHECK(length(title) BETWEEN 8 AND 120),
                title_key TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                opening_hook TEXT NOT NULL,
                target_audience TEXT NOT NULL,
                estimated_duration_seconds INTEGER NOT NULL CHECK(estimated_duration_seconds BETWEEN 30 AND 600),
                thumbnail_concept TEXT NOT NULL,
                script_potential TEXT NOT NULL,
                originality_score INTEGER NOT NULL CHECK(originality_score BETWEEN 0 AND 100),
                is_favorite INTEGER NOT NULL DEFAULT 0 CHECK(is_favorite IN (0, 1)),
                is_used INTEGER NOT NULL DEFAULT 0 CHECK(is_used IN (0, 1)),
                created_at TEXT NOT NULL
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS topics_created_at_idx ON topics(created_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS topics_category_idx ON topics(category)")
        db.execute(
            """CREATE TABLE IF NOT EXISTS scripts (
                id TEXT PRIMARY KEY,
                topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                duration_seconds INTEGER NOT NULL CHECK(duration_seconds IN (30, 60, 180, 300, 600)),
                document_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS scripts_topic_created_idx ON scripts(topic_id, created_at DESC)")
        db.execute(
            """CREATE TABLE IF NOT EXISTS director_plans (
                id TEXT PRIMARY KEY,
                script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
                timeline_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS directors_script_created_idx ON director_plans(script_id, created_at DESC)")
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (3, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS narrations (
                id TEXT PRIMARY KEY,
                script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
                script_hash TEXT NOT NULL,
                voice_id TEXT NOT NULL,
                speed REAL NOT NULL,
                duration_seconds REAL NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS narrations_script_idx ON narrations(script_id, script_hash, created_at DESC)")
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (4, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS avatar_presets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, settings_json TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS avatar_renders (
                id TEXT PRIMARY KEY, narration_id TEXT NOT NULL REFERENCES narrations(id),
                result_json TEXT NOT NULL, created_at TEXT NOT NULL
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS avatar_renders_narration_idx ON avatar_renders(narration_id, created_at DESC)")
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (5, datetime.now(timezone.utc).isoformat()),
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(avatar_renders)")}
        if "engine" not in columns:
            db.execute("ALTER TABLE avatar_renders ADD COLUMN engine TEXT NOT NULL DEFAULT 'illustrated'")
        db.execute(
            """CREATE TABLE IF NOT EXISTS portrait_assets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                crop_json TEXT NOT NULL, created_at TEXT NOT NULL
            )"""
        )
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (6, datetime.now(timezone.utc).isoformat()),
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(portrait_assets)")}
        if "reference_frames" not in columns:
            db.execute("ALTER TABLE portrait_assets ADD COLUMN reference_frames INTEGER")
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (7, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS scene_assets (
                id TEXT PRIMARY KEY, script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
                name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('image', 'video')), max_frames INTEGER
            )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS video_exports (
                id TEXT PRIMARY KEY, script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
                narration_id TEXT NOT NULL REFERENCES narrations(id), result_json TEXT NOT NULL
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS scene_assets_script_idx ON scene_assets(script_id)")
        db.execute("CREATE INDEX IF NOT EXISTS video_exports_script_idx ON video_exports(script_id)")
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (8, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS video_drafts (
                script_id TEXT PRIMARY KEY REFERENCES scripts(id) ON DELETE CASCADE, draft_json TEXT NOT NULL
            )"""
        )
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (9, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS scene_jobs (
                id TEXT PRIMARY KEY, script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, prompt TEXT NOT NULL, seed INTEGER NOT NULL,
                state TEXT NOT NULL, stage TEXT NOT NULL, progress REAL, message TEXT NOT NULL DEFAULT '',
                asset_id TEXT REFERENCES scene_assets(id)
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS scene_jobs_script_idx ON scene_jobs(script_id)")
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (10, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS scene_music (
                id TEXT PRIMARY KEY, script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
                name TEXT NOT NULL, duration_seconds REAL NOT NULL
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS scene_music_script_idx ON scene_music(script_id)")
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (11, datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS project_scripts (
                project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
                script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE
            )"""
        )
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                   (12, datetime.now(timezone.utc).isoformat()))
        db.execute(
            """CREATE TABLE IF NOT EXISTS project_video_jobs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE, settings TEXT NOT NULL,
                checkpoint TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL DEFAULT ''
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS project_video_jobs_project_idx ON project_video_jobs(project_id)")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                   (13, datetime.now(timezone.utc).isoformat()))
        migrate_studio(db)
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                   (14, datetime.now(timezone.utc).isoformat()))
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                   (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from backend.runtime.manager import RuntimeManager
    from backend.runtime.supervisor import runtime_supervisor

    initialize_database()
    RuntimeManager(application_directories(), database_path()).recover_interrupted_operations()
    directories = application_directories()
    app.state.local_creator = LocalCreator(LocalModelManager(directories.data / "models"), database_path())
    app.state.voice = VoiceService(directories.data, database_path())
    app.state.portrait_model = PortraitModel(directories.data, database_path())
    app.state.scene_jobs = SceneJobs(data_directory(), database_connection)
    app.state.scene_jobs.recover()
    app.state.project_videos = ProjectVideoJobs(
        data_directory(), database_connection, app.state.local_creator, app.state.voice,
        app.state.scene_jobs, _render_illustrated_avatar,
    )
    app.state.project_videos.recover()
    app.state.studio = ProjectStudio(data_directory(), database_connection, app.state.voice)
    app.state.studio.start()
    try:
        yield
    finally:
        app.state.studio.close()
        runtime_supervisor.stop_all()
        app.state.voice.cancel_install()
        app.state.portrait_model.cancel()
        app.state.project_videos.close()
        app.state.scene_jobs.close()
        app.state.local_creator = None


app = FastAPI(title="AI Blogger Studio Local Backend", version=BACKEND_VERSION, lifespan=lifespan)


def require_local_token(authorization: Optional[str] = Header(default=None)) -> None:
    expected = os.environ.get("AI_BLOGGER_LOCAL_TOKEN")
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Local backend token is not configured")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


app.include_router(studio_router(require_local_token))


@app.get("/api/health", response_model=Health, dependencies=[Depends(require_local_token)])
def health() -> Health:
    return Health(status="ok", backend_version=BACKEND_VERSION, schema_version=SCHEMA_VERSION)


@app.post("/api/internal/shutdown", dependencies=[Depends(require_local_token)])
def request_backend_shutdown() -> dict:
    request_shutdown = getattr(app.state, "request_shutdown", None)
    if request_shutdown is None:
        raise HTTPException(status_code=503, detail="Graceful shutdown is only available under the desktop supervisor.")
    request_shutdown()
    return {"status": "shutting_down"}


@app.get("/api/settings", response_model=AppSettings, dependencies=[Depends(require_local_token)])
def get_settings() -> AppSettings:
    directories = application_directories()
    return AppSettings(
        data_directory=str(directories.data),
        cache_directory=str(directories.cache),
        config_directory=str(directories.config),
        logs_directory=str(directories.logs),
        temp_directory=str(directories.temp),
        backend_version=BACKEND_VERSION,
        schema_version=SCHEMA_VERSION,
        authentication_enabled=bool(os.environ.get("AI_BLOGGER_LOCAL_TOKEN")),
    )


@app.get("/api/system", response_model=SystemReport, dependencies=[Depends(require_local_token)])
def get_system_report() -> SystemReport:
    return collect_system_report()


def _runtime_manager():
    from backend.runtime.manager import RuntimeManager

    return RuntimeManager(application_directories(), database_path())


def _runtime_call(function, *args, **kwargs):
    from backend.runtime.manager import RuntimeManagerError

    try:
        return function(*args, **kwargs)
    except RuntimeManagerError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


def _creator() -> LocalCreator:
    creator = getattr(app.state, "local_creator", None)
    if creator is None:
        directories = application_directories()
        creator = LocalCreator(LocalModelManager(directories.data / "models"), database_path())
        app.state.local_creator = creator
    return creator


def _ai_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (GenerationError, ModelInstallError) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@app.get("/api/ai/model", response_model=ModelStatus, dependencies=[Depends(require_local_token)])
def get_local_model_status() -> ModelStatus:
    return _ai_call(_creator().status)


@app.post("/api/ai/model/install", response_model=ModelStatus, dependencies=[Depends(require_local_token)])
def install_local_model(payload: ModelInstallRequest) -> ModelStatus:
    return _ai_call(_creator().manager.start_install, payload.accepted_license)


@app.post("/api/ai/model/cancel", response_model=ModelStatus, dependencies=[Depends(require_local_token)])
def cancel_local_model_install() -> ModelStatus:
    return _ai_call(_creator().manager.cancel_install)


@app.post("/api/topics/generate", response_model=TopicBatchResult, dependencies=[Depends(require_local_token)])
def generate_topics(payload: TopicBatchRequest) -> TopicBatchResult:
    return _ai_call(_creator().generate_topics, payload)


@app.get("/api/topics", response_model=list[TopicRecord], dependencies=[Depends(require_local_token)])
def list_topics(
    q: str = Query(default="", max_length=100),
    category: Optional[str] = Query(default=None, max_length=80),
    favorites_only: bool = False,
    used: Optional[bool] = None,
    sort: str = Query(default="recent", max_length=20),
) -> list[TopicRecord]:
    return _ai_call(_creator().list_topics, q, category, favorites_only, used, sort)


@app.patch("/api/topics/{topic_id}", response_model=TopicRecord, dependencies=[Depends(require_local_token)])
def update_topic(topic_id: str, payload: TopicUpdate) -> TopicRecord:
    return _ai_call(_creator().update_topic, topic_id, payload.is_favorite, payload.is_used)


@app.post("/api/topics/{topic_id}/regenerate", response_model=TopicRecord, dependencies=[Depends(require_local_token)])
def regenerate_topic(topic_id: str) -> TopicRecord:
    return _ai_call(_creator().regenerate_topic, topic_id)


@app.post("/api/topics/{topic_id}/scripts", response_model=ScriptDocument, dependencies=[Depends(require_local_token)])
def generate_script(topic_id: str, payload: ScriptRequest) -> ScriptDocument:
    result = _ai_call(_creator().generate_script, topic_id, payload.duration_seconds)
    _ai_call(_creator().update_topic, topic_id, None, True)
    return result


@app.get("/api/topics/{topic_id}/scripts/latest", response_model=Optional[ScriptDocument], dependencies=[Depends(require_local_token)])
def get_latest_script_for_topic(topic_id: str) -> Optional[ScriptDocument]:
    return _ai_call(_creator().latest_script_for_topic, topic_id)


@app.get("/api/scripts/{script_id}", response_model=ScriptDocument, dependencies=[Depends(require_local_token)])
def get_script(script_id: str) -> ScriptDocument:
    return _ai_call(_creator().get_script, script_id)


@app.patch("/api/scripts/{script_id}", response_model=ScriptDocument, dependencies=[Depends(require_local_token)])
def edit_script(script_id: str, payload: ScriptEdit) -> ScriptDocument:
    return _ai_call(_creator().edit_script, script_id, payload)


@app.post("/api/scripts/{script_id}/director", response_model=DirectorDocument, dependencies=[Depends(require_local_token)])
def generate_director(script_id: str) -> DirectorDocument:
    return _ai_call(_creator().generate_director, script_id)


@app.get("/api/scripts/{script_id}/director", response_model=Optional[DirectorDocument], dependencies=[Depends(require_local_token)])
def get_director(script_id: str) -> Optional[DirectorDocument]:
    return _ai_call(_creator().get_director, script_id)


def _voice_call(method, *args):
    try:
        return method(*args)
    except VoiceError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@app.get("/api/voice", dependencies=[Depends(require_local_token)])
def voice_status() -> dict:
    return app.state.voice.status()


@app.post("/api/voice/install", dependencies=[Depends(require_local_token)])
def install_voice(payload: LicenseAcceptance) -> dict:
    return _voice_call(app.state.voice.install, payload.accepted_license)


@app.post("/api/voice/install/cancel", dependencies=[Depends(require_local_token)])
def cancel_voice_install() -> dict:
    return app.state.voice.cancel_install()


@app.post("/api/voice/preview", dependencies=[Depends(require_local_token)])
def preview_voice(payload: VoiceRequest) -> Response:
    output = io.BytesIO()
    _voice_call(app.state.voice.render, PREVIEW_TEXT, payload.voice_id, payload.speed, output)
    return Response(output.getvalue(), media_type="audio/wav")


@app.post("/api/scripts/{script_id}/narration", dependencies=[Depends(require_local_token)])
def render_narration(script_id: uuid.UUID, payload: VoiceRequest) -> dict:
    script = _ai_call(_creator().get_script, str(script_id))
    return _voice_call(
        app.state.voice.save_narration, script.id, script.full_narration, payload.voice_id, payload.speed
    )


@app.get("/api/voice/narration/{narration_id}", dependencies=[Depends(require_local_token)])
def get_narration(narration_id: uuid.UUID) -> FileResponse:
    path = _voice_call(app.state.voice.narration_file, narration_id)
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.get("/api/scripts/{script_id}/narration/latest", dependencies=[Depends(require_local_token)])
def latest_narration(script_id: uuid.UUID):
    script = _ai_call(_creator().get_script, str(script_id))
    return _voice_call(app.state.voice.latest_narration, script.id, script.full_narration)


def _avatar_call(method, *args):
    try:
        return method(*args)
    except avatar.AvatarError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@app.get("/api/avatar", dependencies=[Depends(require_local_token)])
def avatar_library() -> dict:
    with database_connection() as db:
        rows = db.execute("SELECT id, name, settings_json FROM avatar_presets ORDER BY name").fetchall()
    return {**avatar.catalog(), "presets": [
        {"id": row["id"], "name": row["name"], **json.loads(row["settings_json"])} for row in rows
    ]}


@app.post("/api/avatar/preview", dependencies=[Depends(require_local_token)])
def avatar_preview(payload: avatar.Appearance) -> dict:
    return _avatar_call(avatar.preview, payload)


@app.post("/api/avatar/presets", dependencies=[Depends(require_local_token)])
def save_avatar_preset(payload: avatar.Preset) -> dict:
    preset_id = str(uuid.uuid4())
    settings = payload.model_dump(exclude={"name"})
    try:
        with database_connection() as db:
            db.execute("INSERT INTO avatar_presets VALUES (?,?,?)", (preset_id, payload.name, json.dumps(settings)))
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "An avatar preset with that name already exists.") from error
    return {"id": preset_id, "name": payload.name, **settings}


def _current_avatar_narration(narration_id: uuid.UUID, *, script_id: Optional[uuid.UUID] = None):
    with database_connection() as db:
        row = db.execute(
            "SELECT script_id, duration_seconds FROM narrations WHERE id = ?",
            (str(narration_id),),
        ).fetchone()
    if row is None or (script_id is not None and row["script_id"] != str(script_id)):
        raise HTTPException(404, "Saved narration was not found.")
    script = _ai_call(_creator().get_script, row["script_id"])
    current = _voice_call(app.state.voice.latest_narration, script.id, script.full_narration)
    if current is None or current["id"] != str(narration_id):
        raise HTTPException(409, "Use the latest narration for the current saved script.")
    wav = _voice_call(app.state.voice.narration_file, narration_id)
    return wav, script.full_narration, row["duration_seconds"]


def _store_avatar_render(narration_id: uuid.UUID, engine: str, metadata: dict, generator) -> dict:
    folder = avatar.owned_directory(data_directory())
    render_id = str(uuid.uuid4())
    filename = f"{render_id}.mp4"
    destination = folder / filename
    with TemporaryDirectory(prefix=".render-", dir=folder) as temp:
        output = Path(temp) / filename
        metrics = generator(output)
        result = {
            "id": render_id, "narration_id": str(narration_id), "filename": filename,
            "path": str(destination), "size_bytes": output.stat().st_size,
            "engine": engine, **metadata, **metrics,
        }
        digest = sha256_file(output)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with database_connection() as db:
                db.execute(
                    "INSERT INTO avatar_renders (id, narration_id, result_json, created_at, engine) VALUES (?,?,?,?,?)",
                    (render_id, str(narration_id), json.dumps(result), now, engine),
                )
                db.execute(
                    """INSERT INTO managed_files
                       (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
                       VALUES ('avatar', ?, 'avatar_render', ?, ?, ?, ?)""",
                    (filename, render_id, digest, result["size_bytes"], now),
                )
                output.rename(destination)
        except (sqlite3.Error, OSError):
            destination.unlink(missing_ok=True)
            raise
    return result


def _render_illustrated_avatar(payload: avatar.RenderRequest, wav: Path, text: str, duration: float) -> dict:
    return _store_avatar_render(
        payload.narration_id,
        "illustrated",
        {"appearance": payload.model_dump(exclude={"narration_id", "seconds"})},
        lambda output: avatar.render(wav, output, payload, text, duration),
    )


@app.post("/api/avatar/renders", dependencies=[Depends(require_local_token)])
def render_avatar(payload: avatar.RenderRequest) -> dict:
    return _avatar_call(_render_illustrated_avatar, payload, *_current_avatar_narration(payload.narration_id))


@app.get("/api/avatar/portrait-model", dependencies=[Depends(require_local_token)])
def portrait_model_status() -> dict:
    return app.state.portrait_model.status()


@app.post("/api/avatar/portrait-model/install", dependencies=[Depends(require_local_token)])
def install_portrait_model(payload: LicenseAcceptance) -> dict:
    return _avatar_call(app.state.portrait_model.start_install, payload.accepted_license)


@app.post("/api/avatar/portrait-model/cancel", dependencies=[Depends(require_local_token)])
def cancel_portrait_model() -> dict:
    return app.state.portrait_model.cancel()


def _portrait_path(portrait_id: uuid.UUID) -> Path:
    return _avatar_call(realistic.portrait_directory, data_directory()) / f"{portrait_id}.jpg"


def _portrait_record(portrait_id: uuid.UUID):
    with database_connection() as db:
        row = db.execute("SELECT id, name, crop_json, reference_frames FROM portrait_assets WHERE id=?", (str(portrait_id),)).fetchone()
    path = _portrait_path(portrait_id)
    if row is None or path.is_symlink() or not path.is_file():
        raise HTTPException(404, "The portrait was not found.")
    return row, path


def _portrait_metadata(portrait_id: str, name: str, crop: dict, reference_frames: Optional[int]) -> dict:
    return {
        "id": portrait_id, "name": name, "crop": crop,
        "reference_seconds": None if reference_frames is None else round(reference_frames / avatar.FPS, 3),
    }


@app.get("/api/avatar/portraits", dependencies=[Depends(require_local_token)])
def list_portraits() -> list[dict]:
    with database_connection() as db:
        rows = db.execute("SELECT id, name, crop_json, reference_frames FROM portrait_assets ORDER BY name").fetchall()
    return [
        _portrait_metadata(row["id"], row["name"], json.loads(row["crop_json"]), row["reference_frames"])
        for row in rows
    ]


@app.get("/api/avatar/portraits/{portrait_id}", dependencies=[Depends(require_local_token)])
def get_portrait(portrait_id: uuid.UUID) -> dict:
    row, path = _portrait_record(portrait_id)
    image = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        **_portrait_metadata(row["id"], row["name"], json.loads(row["crop_json"]), row["reference_frames"]),
        "image_url": "data:image/jpeg;base64," + image,
    }


@app.post("/api/avatar/portraits", dependencies=[Depends(require_local_token)])
def import_portrait(payload: realistic.PortraitImport) -> dict:
    portrait_id = uuid.uuid4()
    path = _portrait_path(portrait_id)
    crop = realistic.FaceCrop()
    now = datetime.now(timezone.utc).isoformat()
    with TemporaryDirectory(prefix=".portrait-", dir=path.parent) as temp:
        staged = Path(temp) / path.name
        files = [(staged, path)]
        reference_frames = None
        if payload.video_base64 is not None:
            staged_video = staged.with_suffix(".mp4")
            reference_frames = _avatar_call(
                realistic.prepare_reference_video, payload.video_base64, Path(temp), staged_video, staged,
            )
            files.append((staged_video, path.with_suffix(".mp4")))
        else:
            staged.write_bytes(_avatar_call(realistic.prepare_image, payload.image_base64))
        moved = []
        try:
            with database_connection() as db:
                db.execute(
                    """INSERT INTO portrait_assets (id, name, crop_json, created_at, reference_frames)
                       VALUES (?,?,?,?,?)""",
                    (str(portrait_id), payload.name, crop.model_dump_json(), now, reference_frames),
                )
                db.executemany(
                    """INSERT INTO managed_files
                       (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
                       VALUES ('avatar', ?, 'portrait_asset', ?, ?, ?, ?)""",
                    [(f"portraits/{destination.name}", str(portrait_id), sha256_file(source), source.stat().st_size, now)
                     for source, destination in files],
                )
                for source, destination in files:
                    source.rename(destination)
                    moved.append(destination)
        except sqlite3.IntegrityError as error:
            for destination in moved:
                destination.unlink(missing_ok=True)
            raise HTTPException(409, "A portrait with that name is already saved.") from error
        except (sqlite3.Error, OSError):
            for destination in moved:
                destination.unlink(missing_ok=True)
            raise
    return _portrait_metadata(str(portrait_id), payload.name, crop.model_dump(), reference_frames)


@app.patch("/api/avatar/portraits/{portrait_id}", dependencies=[Depends(require_local_token)])
def save_portrait_crop(portrait_id: uuid.UUID, crop: realistic.FaceCrop) -> dict:
    row, _ = _portrait_record(portrait_id)
    with database_connection() as db:
        db.execute("UPDATE portrait_assets SET crop_json=? WHERE id=?", (crop.model_dump_json(), str(portrait_id)))
    return _portrait_metadata(str(portrait_id), row["name"], crop.model_dump(), row["reference_frames"])


@app.post("/api/avatar/portrait-renders", dependencies=[Depends(require_local_token)])
def render_realistic_avatar(payload: realistic.PortraitRender) -> dict:
    row, photo = _portrait_record(payload.portrait_id)
    crop = realistic.FaceCrop.model_validate_json(row["crop_json"])
    reference = photo.with_suffix(".mp4") if row["reference_frames"] is not None else None
    if reference is not None and payload.animate_head:
        raise HTTPException(422, "Subtle head motion is only available for photos.")
    if reference is not None and (reference.is_symlink() or not reference.is_file()):
        raise HTTPException(404, "The saved reference video is missing.")
    wav, _, _ = _current_avatar_narration(payload.narration_id)
    return _avatar_call(
        _store_avatar_render, payload.narration_id, "musetalk",
        {"portrait_id": str(payload.portrait_id), "animate_head": payload.animate_head},
        lambda output: realistic.render_portrait(
            app.state.portrait_model, photo, crop, wav, output, payload.seconds,
            reference=reference, reference_frames=row["reference_frames"], animate_head=payload.animate_head,
        ),
    )


@app.get("/api/avatar/renders/latest/{narration_id}", dependencies=[Depends(require_local_token)])
def latest_avatar(narration_id: uuid.UUID, engine: Literal["illustrated", "musetalk"] = "illustrated"):
    with database_connection() as db:
        row = db.execute(
            "SELECT result_json FROM avatar_renders WHERE narration_id=? AND engine=? ORDER BY created_at DESC LIMIT 1",
            (str(narration_id), engine),
        ).fetchone()
    if row is None:
        return None
    result = json.loads(row["result_json"])
    return result if _registered_avatar_file(uuid.UUID(result["id"])) is not None else None


def _registered_avatar_file(render_id: uuid.UUID) -> Optional[Path]:
    filename = f"{render_id}.mp4"
    folder = data_directory() / "avatar"
    path = folder / filename
    with database_connection() as db:
        registered = db.execute(
            "SELECT 1 FROM managed_files WHERE root_key='avatar' AND relative_path=? AND owner_id=?",
            (filename, str(render_id)),
        ).fetchone()
    if not registered or folder.is_symlink() or path.is_symlink() or not path.is_file():
        return None
    return path


@app.get("/api/avatar/renders/{render_id}", dependencies=[Depends(require_local_token)])
def avatar_video(render_id: uuid.UUID) -> FileResponse:
    path = _registered_avatar_file(render_id)
    if path is None:
        raise HTTPException(404, "The presenter video was not found.")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


def _export_library() -> ExportLibrary:
    return ExportLibrary(data_directory(), database_connection)


def _export_call(method, *args):
    try:
        return method(*args)
    except ExportError as error:
        raise HTTPException(error.status_code, str(error)) from error
    except (OSError, sqlite3.Error) as error:
        raise HTTPException(500, "The local export files could not be read or saved.") from error


@app.get("/api/scene-models", dependencies=[Depends(require_local_token)])
def get_scene_models() -> dict:
    return _export_call(app.state.scene_jobs.capabilities)


@app.get("/api/scripts/{script_id}/scene-jobs/latest", dependencies=[Depends(require_local_token)])
def latest_scene_job(script_id: uuid.UUID):
    return _export_call(app.state.scene_jobs.latest, str(script_id))


@app.post("/api/scripts/{script_id}/scene-jobs", dependencies=[Depends(require_local_token)])
def start_scene_job(script_id: uuid.UUID, payload: SceneRequest) -> dict:
    _ai_call(_creator().get_script, str(script_id))
    return _export_call(app.state.scene_jobs.start, str(script_id), payload)


@app.post("/api/scripts/{script_id}/scene-jobs/{job_id}/cancel", dependencies=[Depends(require_local_token)])
def cancel_scene_job(script_id: uuid.UUID, job_id: uuid.UUID) -> dict:
    return _export_call(app.state.scene_jobs.cancel, str(script_id), str(job_id))


@app.get("/api/scripts/{script_id}/scene-assets", dependencies=[Depends(require_local_token)])
def list_scene_assets(script_id: uuid.UUID) -> list[dict]:
    return _export_call(_export_library().list_assets, str(script_id))


@app.post("/api/scripts/{script_id}/scene-assets", dependencies=[Depends(require_local_token)])
def import_scene_asset(script_id: uuid.UUID, payload: AssetImport) -> dict:
    _ai_call(_creator().get_script, str(script_id))
    return _export_call(_export_library().import_asset, str(script_id), payload)


@app.get("/api/scripts/{script_id}/export-draft", dependencies=[Depends(require_local_token)])
def load_export_draft(script_id: uuid.UUID) -> ExportDraft:
    _ai_call(_creator().get_script, str(script_id))
    return _export_call(_export_library().load_draft, str(script_id))


@app.get("/api/scripts/{script_id}/background-music", dependencies=[Depends(require_local_token)])
def list_background_music(script_id: uuid.UUID) -> list[dict]:
    return _export_call(_export_library().list_music, str(script_id))


@app.post("/api/scripts/{script_id}/background-music", dependencies=[Depends(require_local_token)])
def import_background_music(script_id: uuid.UUID, payload: MediaImport) -> dict:
    _ai_call(_creator().get_script, str(script_id))
    return _export_call(_export_library().import_music, str(script_id), payload)


@app.put("/api/scripts/{script_id}/export-draft", dependencies=[Depends(require_local_token)])
def save_export_draft(script_id: uuid.UUID, payload: ExportDraft) -> ExportDraft:
    _ai_call(_creator().get_script, str(script_id))
    return _export_call(_export_library().save_draft, str(script_id), payload)


@app.get("/api/scripts/{script_id}/exports", dependencies=[Depends(require_local_token)])
def list_video_exports(script_id: uuid.UUID) -> list[dict]:
    return _export_call(_export_library().list_exports, str(script_id))


@app.post("/api/scripts/{script_id}/exports", dependencies=[Depends(require_local_token)])
def render_video_export(script_id: uuid.UUID, payload: ExportRequest) -> dict:
    wav, transcript, _ = _current_avatar_narration(payload.narration_id, script_id=script_id)
    return _export_call(_export_library().render, str(script_id), payload, wav, transcript)


@app.get("/api/exports/{export_id}/preview", dependencies=[Depends(require_local_token)])
def video_export_preview(export_id: uuid.UUID) -> dict:
    path = _export_call(_export_library().export_file, str(export_id))
    if path is None:
        raise HTTPException(404, "The saved export could not be found.")
    return {"path": str(path)}


@app.get("/api/runtime", dependencies=[Depends(require_local_token)])
def get_runtime_snapshot() -> dict:
    return _runtime_call(_runtime_manager().snapshot)


@app.post("/api/runtime/components/{component_id}/install", dependencies=[Depends(require_local_token)])
def install_component(component_id: str, payload: LicenseAcceptance) -> dict:
    return _runtime_call(_runtime_manager().install, component_id, payload.accepted_license)


@app.post("/api/runtime/components/{component_id}/start", dependencies=[Depends(require_local_token)])
def start_component(component_id: str) -> dict:
    return _runtime_call(_runtime_manager().start, component_id)


@app.post("/api/runtime/components/{component_id}/stop", dependencies=[Depends(require_local_token)])
def stop_component(component_id: str) -> dict:
    return _runtime_call(_runtime_manager().stop, component_id)


@app.delete("/api/runtime/components/{component_id}", dependencies=[Depends(require_local_token)])
def remove_component(component_id: str, payload: ComponentRemoval) -> dict:
    return _runtime_call(_runtime_manager().remove, component_id, payload.confirmation)


@app.post("/api/runtime/cleanup/cache", dependencies=[Depends(require_local_token)])
def clear_runtime_cache(payload: RuntimeCleanup) -> dict:
    if payload.confirmation != "CLEAR MANAGED CACHE":
        raise HTTPException(status_code=422, detail="Type CLEAR MANAGED CACHE to confirm.")
    return _runtime_call(_runtime_manager().clear_registered_cache)


@app.post("/api/runtime/cleanup/temporary", dependencies=[Depends(require_local_token)])
def clear_runtime_temporary(payload: RuntimeCleanup) -> dict:
    if payload.confirmation != "CLEAR TEMPORARY FILES":
        raise HTTPException(status_code=422, detail="Type CLEAR TEMPORARY FILES to confirm.")
    return _runtime_call(_runtime_manager().clear_registered_temporary_files)


@app.post("/api/runtime/uninstall", dependencies=[Depends(require_local_token)])
def uninstall_runtime_tools(payload: RuntimeCleanup) -> dict:
    return _runtime_call(_runtime_manager().uninstall_tools_keep_projects, payload.confirmation)


@app.get("/api/projects", response_model=list[Project], dependencies=[Depends(require_local_token)])
def list_projects() -> list[Project]:
    with database_connection() as db:
        rows = db.execute(
            """SELECT projects.id, projects.title, projects.created_at, scripts.id AS script_id, scripts.topic_id
               FROM projects LEFT JOIN project_scripts ON project_scripts.project_id=projects.id
               LEFT JOIN scripts ON scripts.id=project_scripts.script_id
               ORDER BY projects.created_at DESC, projects.id DESC"""
        ).fetchall()
    return [Project(**dict(row)) for row in rows]


@app.get("/api/projects/{project_id}/video-jobs/latest", dependencies=[Depends(require_local_token)])
def latest_project_video(project_id: uuid.UUID) -> Optional[dict]:
    return _export_call(app.state.project_videos.latest, str(project_id))


@app.post("/api/projects/{project_id}/video-jobs", dependencies=[Depends(require_local_token)])
def start_project_video(project_id: uuid.UUID, payload: ProjectVideoRequest) -> dict:
    return _export_call(app.state.project_videos.start, str(project_id), payload)


@app.post("/api/projects/{project_id}/video-jobs/{job_id}/resume", dependencies=[Depends(require_local_token)])
def resume_project_video(project_id: uuid.UUID, job_id: uuid.UUID, payload: ResumeProjectVideo) -> dict:
    return _export_call(app.state.project_videos.resume, str(project_id), str(job_id), payload)


@app.post("/api/projects/{project_id}/video-jobs/{job_id}/cancel", dependencies=[Depends(require_local_token)])
def cancel_project_video(project_id: uuid.UUID, job_id: uuid.UUID) -> dict:
    return _export_call(app.state.project_videos.cancel, str(project_id), str(job_id))


@app.put("/api/projects/{project_id}/script", response_model=Project, dependencies=[Depends(require_local_token)])
def link_project_script(project_id: uuid.UUID, payload: ProjectScript) -> Project:
    project_key, script_key = str(project_id), str(payload.script_id)
    with database_connection() as db:
        project = db.execute("SELECT id, title, created_at FROM projects WHERE id=?", (project_key,)).fetchone()
        script = db.execute("SELECT id, topic_id FROM scripts WHERE id=?", (script_key,)).fetchone()
        if project is None or script is None:
            raise HTTPException(404, "The project or saved script could not be found.")
        db.execute(
            """INSERT INTO project_scripts (project_id, script_id) VALUES (?, ?)
               ON CONFLICT(project_id) DO UPDATE SET script_id=excluded.script_id""",
            (project_key, script_key),
        )
    return Project(**dict(project), script_id=script_key, topic_id=script["topic_id"])


@app.post("/api/projects", response_model=Project, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_local_token)])
def create_project(payload: ProjectCreate) -> Project:
    project = Project(id=str(uuid.uuid4()), title=payload.title, created_at=datetime.now(timezone.utc))
    with database_connection() as db:
        db.execute(
            "INSERT INTO projects(id, title, created_at) VALUES (?, ?, ?)",
            (project.id, project.title, project.created_at.isoformat()),
        )
    return project

