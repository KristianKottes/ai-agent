from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from backend.api.main import app, database_path, initialize_database


def configure_application_paths(monkeypatch, tmp_path):
    for key, name in (
        ("AI_BLOGGER_DATA_DIR", "data"),
        ("AI_BLOGGER_CACHE_DIR", "cache"),
        ("AI_BLOGGER_CONFIG_DIR", "config"),
        ("AI_BLOGGER_LOG_DIR", "logs"),
        ("AI_BLOGGER_TEMP_DIR", "temp"),
    ):
        monkeypatch.setenv(key, str(tmp_path / name))


def test_api_requires_launch_token(monkeypatch, tmp_path):
    configure_application_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "launch-secret")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/system").status_code == 401
        assert client.get("/api/runtime").status_code == 401
        response = client.get("/api/health", headers={"Authorization": "Bearer launch-secret"})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_projects_persist_and_validate(monkeypatch, tmp_path):
    configure_application_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "launch-secret")
    headers = {"Authorization": "Bearer launch-secret"}
    with TestClient(app) as client:
        created = client.post("/api/projects", headers=headers, json={"title": "First project"})
        assert created.status_code == 201
        project = created.json()
        assert project["title"] == "First project"
        assert client.get("/api/projects", headers=headers).json() == [project]
        assert client.post("/api/projects", headers=headers, json={"title": "  "}).status_code == 422
        settings = client.get("/api/settings", headers=headers)
        assert settings.status_code == 200
        assert settings.json()["schema_version"] == 15
        assert settings.json()["temp_directory"] == str(tmp_path / "temp")
        system = client.get("/api/system", headers=headers)
        assert system.status_code == 200
        assert system.json()["storage"]["data_directory"] == str(tmp_path / "data")
        assert system.json()["storage"]["temp_directory"] == str(tmp_path / "temp")
    with TestClient(app) as restarted_client:
        assert restarted_client.get("/api/projects", headers=headers).json() == [project]
    assert (tmp_path / "data" / "ai-blogger-studio.sqlite3").is_file()


def test_database_migrates_existing_phase_one_projects(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_BLOGGER_DATA_DIR", str(tmp_path))
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00+00:00')")
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL)")
        connection.execute("INSERT INTO projects VALUES ('existing', 'Keep me', '2026-01-01T00:00:00+00:00')")

    initialize_database()

    with sqlite3.connect(path) as connection:
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        components = connection.execute("SELECT title FROM projects WHERE id='existing'").fetchone()
        runtime_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='managed_files'"
        ).fetchone()
    assert versions == set(range(1, 16))
    assert components == ("Keep me",)
    assert runtime_table == ("managed_files",)
