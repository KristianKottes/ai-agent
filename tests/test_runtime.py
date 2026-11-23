from __future__ import annotations

import json
import os
import signal
import socket
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.main import app
from backend.api.main import database_path
from backend.runtime.catalog import BUILTIN_CATALOG_SHA256, CATALOG_PATH, CatalogIntegrityError, load_catalog
from backend.runtime.downloader import DownloadManager, sha256_file
from backend.runtime.downloader import DownloadError
from backend.runtime.manager import RuntimeManager
from backend.runtime.models import ComponentManifest
from backend.runtime.supervisor import RuntimeProcessError, RuntimeSupervisor
from backend.platform.paths import application_directories

COMPONENT_ID = "runtime-smoke-test"


def configure_test_runtime(monkeypatch, tmp_path):
    for key, child in (
        ("AI_BLOGGER_DATA_DIR", "data"),
        ("AI_BLOGGER_CACHE_DIR", "cache"),
        ("AI_BLOGGER_CONFIG_DIR", "config"),
        ("AI_BLOGGER_LOG_DIR", "logs"),
        ("AI_BLOGGER_TEMP_DIR", "temp"),
    ):
        monkeypatch.setenv(key, str(tmp_path / child))
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "runtime-test-token")
    monkeypatch.setattr("backend.runtime.manager._host_platform", lambda: ("macos", "arm64"))
    return {"Authorization": "Bearer runtime-test-token"}


def test_bundled_catalog_is_pinned_and_typed(tmp_path):
    catalog = load_catalog()
    assert catalog.catalog_version == 1
    assert catalog.components[0].id == COMPONENT_ID
    assert len(BUILTIN_CATALOG_SHA256) == 64

    changed = tmp_path / "catalog.json"
    changed.write_text(CATALOG_PATH.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(CatalogIntegrityError):
        load_catalog(changed)

    manifest = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["components"][0]
    manifest["artifacts"][0]["target_path"] = "../escape.py"
    with pytest.raises(ValidationError):
        ComponentManifest.model_validate(manifest)


def test_bundled_downloader_resumes_partial_file(tmp_path):
    manifest = load_catalog().components[0]
    artifact = manifest.artifacts[0]
    bundle_root = CATALOG_PATH.parent
    cache_root = tmp_path / "cache"
    destination = cache_root / "downloads" / manifest.id / manifest.version / artifact.target_path
    partial = destination.with_name(destination.name + ".part")
    partial.parent.mkdir(parents=True)
    source = bundle_root / artifact.bundle_path
    partial.write_bytes(source.read_bytes()[:40])

    downloaded = DownloadManager(bundle_root, cache_root).fetch_bundled(manifest.id, manifest.version, artifact)
    assert downloaded == destination
    assert downloaded.stat().st_size == artifact.size_bytes
    assert sha256_file(downloaded) == artifact.sha256
    assert not partial.exists()


def test_downloader_rejects_symlinked_cache_parent_and_does_not_follow_partial(tmp_path):
    manifest = load_catalog().components[0]
    artifact = manifest.artifacts[0]
    bundle_root = CATALOG_PATH.parent
    cache_root = tmp_path / "cache"
    external = tmp_path / "outside.bin"
    external.write_bytes(b"keep this file")
    cache_root.mkdir()
    (cache_root / "downloads").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(DownloadError):
        DownloadManager(bundle_root, cache_root).fetch_bundled(manifest.id, manifest.version, artifact)
    assert not (tmp_path / manifest.id).exists()

    (cache_root / "downloads").unlink()
    destination = cache_root / "downloads" / manifest.id / manifest.version / artifact.target_path
    destination.parent.mkdir(parents=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        partial.symlink_to(external)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")
    downloaded = DownloadManager(bundle_root, cache_root).fetch_bundled(manifest.id, manifest.version, artifact)
    assert downloaded.is_file()
    assert external.read_bytes() == b"keep this file"


def test_runtime_supervisor_refuses_symlinked_log_path(tmp_path):
    manifest = load_catalog().components[0]
    installation = tmp_path / "installed"
    installation.mkdir()
    source = CATALOG_PATH.parent / manifest.artifacts[0].bundle_path
    (installation / manifest.entrypoint).write_bytes(source.read_bytes())
    external_log = tmp_path / "outside.log"
    external_log.write_text("keep this log", encoding="utf-8")
    log_path = tmp_path / "logs" / f"{manifest.id}.log"
    log_path.parent.mkdir()
    try:
        log_path.symlink_to(external_log)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    with pytest.raises(RuntimeProcessError):
        RuntimeSupervisor().start(manifest, installation, log_path)
    assert external_log.read_text(encoding="utf-8") == "keep this log"


def test_runtime_component_install_start_stop_remove_and_project_retention(monkeypatch, tmp_path):
    headers = configure_test_runtime(monkeypatch, tmp_path)
    with TestClient(app) as client:
        project_response = client.post("/api/projects", headers=headers, json={"title": "Keep this project"})
        assert project_response.status_code == 201

        snapshot = client.get("/api/runtime", headers=headers)
        assert snapshot.status_code == 200
        component = snapshot.json()["components"][0]
        assert component["id"] == COMPONENT_ID
        assert component["compatibility"] == "verified"
        assert component["installation_state"] == "available"
        assert component["license"]["source_code"] == "MIT"

        unaccepted = client.post(
            f"/api/runtime/components/{COMPONENT_ID}/install",
            headers=headers,
            json={"accepted_license": False},
        )
        assert unaccepted.status_code == 422

        installed = client.post(
            f"/api/runtime/components/{COMPONENT_ID}/install",
            headers=headers,
            json={"accepted_license": True},
        )
        assert installed.status_code == 200
        assert installed.json()["state"] == "installed"

        running = client.post(f"/api/runtime/components/{COMPONENT_ID}/start", headers=headers)
        assert running.status_code == 200
        assert running.json()["process_id"] > 0
        running_snapshot = client.get("/api/runtime", headers=headers).json()
        assert running_snapshot["components"][0]["installation_state"] == "running"
        assert '"status": "ready"' in running_snapshot["components"][0]["log_tail"]

        stopped = client.post(f"/api/runtime/components/{COMPONENT_ID}/stop", headers=headers)
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "installed"

        wrong_confirmation = client.request(
            "DELETE",
            f"/api/runtime/components/{COMPONENT_ID}",
            headers=headers,
            json={"confirmation": "wrong"},
        )
        assert wrong_confirmation.status_code == 422

        removed = client.request(
            "DELETE",
            f"/api/runtime/components/{COMPONENT_ID}",
            headers=headers,
            json={"confirmation": COMPONENT_ID},
        )
        assert removed.status_code == 200
        assert removed.json()["state"] == "removed"

        assert client.get("/api/projects", headers=headers).json() == [project_response.json()]
        final_snapshot = client.get("/api/runtime", headers=headers).json()
        assert final_snapshot["components"][0]["installation_state"] == "removed"
        assert not (Path(tmp_path) / "data" / "components" / COMPONENT_ID).exists()


def test_runtime_uninstall_requires_explicit_keep_projects_confirmation(monkeypatch, tmp_path):
    headers = configure_test_runtime(monkeypatch, tmp_path)
    with TestClient(app) as client:
        project = client.post("/api/projects", headers=headers, json={"title": "Preserved"}).json()
        install = client.post(
            f"/api/runtime/components/{COMPONENT_ID}/install",
            headers=headers,
            json={"accepted_license": True},
        )
        assert install.status_code == 200
        not_confirmed = client.post("/api/runtime/uninstall", headers=headers, json={"confirmation": ""})
        assert not_confirmed.status_code == 422
        response = client.post(
            "/api/runtime/uninstall",
            headers=headers,
            json={"confirmation": "KEEP PROJECTS AND REMOVE TOOLS"},
        )
        assert response.status_code == 200
        assert response.json()["project_data_removed"] is False
        assert client.get("/api/projects", headers=headers).json() == [project]


def test_recovery_registers_a_promoted_install_after_crash(monkeypatch, tmp_path):
    configure_test_runtime(monkeypatch, tmp_path)
    with TestClient(app):
        manager = RuntimeManager(application_directories(), database_path())
        manager._prepare_roots()
        manifest = load_catalog().components[0]
        operation_id = manager._operation(COMPONENT_ID, "install", "testing")
        stage = manager.staging_root / operation_id
        stage.mkdir()
        for artifact in manifest.artifacts:
            path = stage / artifact.target_path
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CATALOG_PATH.parent / artifact.bundle_path, path)
            manager._register_file("components", path, "operation", operation_id)
        os.replace(stage, manager.components_root / COMPONENT_ID)

        manager.recover_interrupted_operations()
        component = manager.snapshot()["components"][0]
        assert component["installation_state"] == "installed"
        assert len([row for row in manager.snapshot()["managed_files"] if row["owner_type"] == "component"]) == 2


def test_recovery_removes_registered_incomplete_stage(monkeypatch, tmp_path):
    configure_test_runtime(monkeypatch, tmp_path)
    with TestClient(app):
        manager = RuntimeManager(application_directories(), database_path())
        manager._prepare_roots()
        manifest = load_catalog().components[0]
        operation_id = manager._operation(COMPONENT_ID, "install", "installing")
        stage = manager.staging_root / operation_id
        stage.mkdir()
        artifact = manifest.artifacts[0]
        path = stage / artifact.target_path
        shutil.copyfile(CATALOG_PATH.parent / artifact.bundle_path, path)
        manager._register_file("components", path, "operation", operation_id)

        manager.recover_interrupted_operations()
        operation = manager._registered_files("operation", operation_id)
        component = manager.snapshot()["components"][0]
        assert operation == []
        assert component["installation_state"] == "failed"
        assert not stage.exists()


def test_runtime_cache_cleanup_only_removes_registered_downloads(monkeypatch, tmp_path):
    headers = configure_test_runtime(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install = client.post(
            f"/api/runtime/components/{COMPONENT_ID}/install",
            headers=headers,
            json={"accepted_license": True},
        )
        assert install.status_code == 200
        user_file = tmp_path / "cache" / "user-kept.bin"
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_bytes(b"unregistered")

        no_confirmation = client.post("/api/runtime/cleanup/cache", headers=headers, json={"confirmation": "no"})
        assert no_confirmation.status_code == 422
        cleared = client.post(
            "/api/runtime/cleanup/cache",
            headers=headers,
            json={"confirmation": "CLEAR MANAGED CACHE"},
        )
        assert cleared.status_code == 200
        assert cleared.json()["removed_files"] == 2
        assert user_file.read_bytes() == b"unregistered"


def test_failed_download_leaves_no_install_and_records_failure(monkeypatch, tmp_path):
    headers = configure_test_runtime(monkeypatch, tmp_path)

    def fail_download(*_args, **_kwargs):
        raise DownloadError("simulated interrupted artifact copy")

    monkeypatch.setattr("backend.runtime.manager.DownloadManager.fetch_bundled", fail_download)
    with TestClient(app) as client:
        response = client.post(
            f"/api/runtime/components/{COMPONENT_ID}/install",
            headers=headers,
            json={"accepted_license": True},
        )
        assert response.status_code == 422
        assert "interrupted" in response.json()["detail"]
        component = client.get("/api/runtime", headers=headers).json()["components"][0]
        assert component["installation_state"] == "failed"
        assert not (tmp_path / "data" / "components" / COMPONENT_ID).exists()


def test_unverified_platform_is_visible_but_cannot_install(monkeypatch, tmp_path):
    headers = configure_test_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr("backend.runtime.manager._host_platform", lambda: ("windows", "x86_64"))
    with TestClient(app) as client:
        component = client.get("/api/runtime", headers=headers).json()["components"][0]
        assert component["compatibility"] == "not_tested"
        response = client.post(
            f"/api/runtime/components/{COMPONENT_ID}/install",
            headers=headers,
            json={"accepted_license": True},
        )
        assert response.status_code == 409
        assert not (tmp_path / "data" / "components" / COMPONENT_ID).exists()


@pytest.mark.parametrize("host_disconnect", [False, True])
def test_desktop_runner_shutdown_stops_running_component(tmp_path, host_disconnect):
    token = "runner-shutdown-test-token"
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for key, name in (
        ("AI_BLOGGER_DATA_DIR", "data"),
        ("AI_BLOGGER_CACHE_DIR", "cache"),
        ("AI_BLOGGER_CONFIG_DIR", "config"),
        ("AI_BLOGGER_LOG_DIR", "logs"),
        ("AI_BLOGGER_TEMP_DIR", "temp"),
    ):
        env[key] = str(tmp_path / name)
    env["AI_BLOGGER_LOCAL_TOKEN"] = token
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", 0))
        except PermissionError as error:
            pytest.skip(f"Sandbox does not allow loopback binding: {error}")
        port = listener.getsockname()[1]

    command = [sys.executable, "-m", "backend.runner", "--host", "127.0.0.1", "--port", str(port)]
    if host_disconnect:
        command.append("--shutdown-on-stdin-close")
    backend = subprocess.Popen(
        command,
        cwd=root,
        env=env,
        stdin=subprocess.PIPE if host_disconnect else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )

    def request(path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        with urllib.request.urlopen(req, timeout=2) as response:
            return json.loads(response.read())

    managed_process_id = None
    shutdown_confirmed = False
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                request("/api/health")
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if backend.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError("Local runner did not become healthy")
                time.sleep(0.05)

        install = request(f"/api/runtime/components/{COMPONENT_ID}/install", {"accepted_license": True})
        assert install["state"] == "installed"
        running = request(f"/api/runtime/components/{COMPONENT_ID}/start", {})
        process_id = running["process_id"]
        managed_process_id = process_id
        if host_disconnect:
            backend.stdin.close()
        else:
            shutdown = request("/api/internal/shutdown", {})
            assert shutdown["status"] == "shutting_down"
        assert backend.wait(timeout=10) == 0

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Supervised component remained alive after the backend shut down")
        shutdown_confirmed = True
    finally:
        if backend.poll() is None:
            try:
                request("/api/internal/shutdown", {})
                backend.wait(timeout=10)
            except (urllib.error.URLError, subprocess.TimeoutExpired):
                backend.kill()
                backend.wait(timeout=5)
        if managed_process_id is not None and not shutdown_confirmed:
            try:
                os.kill(managed_process_id, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
            except ProcessLookupError:
                pass
