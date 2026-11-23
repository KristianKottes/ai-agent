"""Install, registry, storage, and removal operations for trusted bundled components."""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterator, List, Optional, Tuple

from pydantic import ValidationError

from backend.platform.paths import ApplicationDirectories

from .catalog import CatalogIntegrityError, load_catalog
from .downloader import DownloadError, DownloadManager, sha256_file
from .models import Catalog, ComponentManifest
from .supervisor import RuntimeProcessError, RuntimeSupervisor, runtime_supervisor

_RUNTIME_LOCK = threading.RLock()
_ROOT_KEYS = {"components", "cache", "logs", "temp"}


class RuntimeManagerError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host_platform() -> Tuple[str, str]:
    os_name = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(platform.system(), "unknown")
    machine = platform.machine().lower()
    architecture = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "i386": "x86",
        "i686": "x86",
    }.get(machine, machine)
    return os_name, architecture


def _safe_relative(value: str) -> PurePosixPath:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise RuntimeManagerError("A managed file path was invalid.", 500)
    return PurePosixPath(value)


class RuntimeManager:
    def __init__(
        self,
        directories: ApplicationDirectories,
        database_path: Path,
        supervisor: RuntimeSupervisor = runtime_supervisor,
    ) -> None:
        self.directories = directories
        self.database_path = database_path
        self.supervisor = supervisor
        self.components_root = directories.data / "components"
        self.staging_root = self.components_root / ".staging"
        self.logs_root = directories.logs / "components"
        self.bundle_root = Path(__file__).parent

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _catalog(self) -> Catalog:
        try:
            return load_catalog()
        except CatalogIntegrityError as error:
            raise RuntimeManagerError(str(error), 500) from error

    def _manifest(self, component_id: str) -> ComponentManifest:
        for manifest in self._catalog().components:
            if manifest.id == component_id:
                return manifest
        raise RuntimeManagerError("Component was not found in the authenticated local catalog.", 404)

    def _root(self, root_key: str) -> Path:
        if root_key == "components":
            return self.components_root
        if root_key == "cache":
            return self.directories.cache
        if root_key == "logs":
            return self.directories.logs
        if root_key == "temp":
            return self.directories.temp
        raise RuntimeManagerError("Unknown managed-file root.", 500)

    def _prepare_roots(self) -> None:
        for root in (self.components_root, self.staging_root, self.directories.cache, self.logs_root, self.directories.temp):
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if root.is_symlink() or not root.is_dir():
                raise RuntimeManagerError("An application-managed directory is unsafe.", 500)
            if os.name != "nt":
                os.chmod(root, 0o700)

    def _operation(self, component_id: str, operation: str, state: str, details: str = "") -> str:
        operation_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO component_operations
                   (id, component_id, operation, state, details, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (operation_id, component_id, operation, state, details[:500], _now(), _now()),
            )
        return operation_id

    def _set_operation(self, operation_id: str, state: str, details: str = "") -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE component_operations SET state=?, details=?, updated_at=? WHERE id=?",
                (state, details[:500], _now(), operation_id),
            )

    def _register_file(self, root_key: str, path: Path, owner_type: str, owner_id: str) -> str:
        if root_key not in _ROOT_KEYS:
            raise RuntimeManagerError("Unknown managed-file root.", 500)
        root = self._root(root_key).resolve(strict=True)
        if path.is_symlink() or not path.is_file():
            raise RuntimeManagerError("Only regular files can be registered as managed assets.", 500)
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise RuntimeManagerError("Managed file escaped its application-owned directory.", 500)
        relative = resolved.relative_to(root).as_posix()
        _safe_relative(relative)
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO managed_files
                   (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(root_key, relative_path) DO UPDATE SET
                     owner_type=excluded.owner_type, owner_id=excluded.owner_id,
                     sha256=excluded.sha256, size_bytes=excluded.size_bytes,
                     registered_at=excluded.registered_at""",
                (root_key, relative, owner_type, owner_id, sha256_file(resolved), resolved.stat().st_size, _now()),
            )
        return relative

    def _remove_registered_file(self, root_key: str, relative_path: str) -> int:
        safe = _safe_relative(relative_path)
        root = self._root(root_key).resolve(strict=True)
        target = root.joinpath(*safe.parts)
        if target.is_symlink():
            raise RuntimeManagerError("Refusing to remove a registered symbolic link.", 409)
        parent = target.parent.resolve(strict=False)
        if not parent.is_relative_to(root):
            raise RuntimeManagerError("Refusing to remove a file outside its application-owned directory.", 409)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT size_bytes FROM managed_files WHERE root_key=? AND relative_path=?",
                (root_key, relative_path),
            ).fetchone()
            if row is None:
                return 0
            size_bytes = int(row["size_bytes"])
            if target.exists():
                if not target.is_file():
                    raise RuntimeManagerError("A registered file path is no longer a regular file.", 409)
                target.unlink()
            connection.execute(
                "DELETE FROM managed_files WHERE root_key=? AND relative_path=?",
                (root_key, relative_path),
            )
        return size_bytes

    def _registered_files(self, owner_type: Optional[str] = None, owner_id: Optional[str] = None) -> List[sqlite3.Row]:
        sql = "SELECT root_key, relative_path, owner_type, owner_id, sha256, size_bytes FROM managed_files"
        filters: List[str] = []
        values: List[str] = []
        if owner_type is not None:
            filters.append("owner_type=?")
            values.append(owner_type)
        if owner_id is not None:
            filters.append("owner_id=?")
            values.append(owner_id)
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        with self._connection() as connection:
            return connection.execute(sql + " ORDER BY root_key, relative_path", values).fetchall()

    def _verify_component_directory(self, component_id: str) -> None:
        target = self.components_root / component_id
        if not target.exists():
            raise RuntimeManagerError("Installed component files are missing.", 409)
        if target.is_symlink() or not target.is_dir():
            raise RuntimeManagerError("Installed component directory is unsafe.", 409)
        registered = {
            row["relative_path"]
            for row in self._registered_files("component", component_id)
            if row["root_key"] == "components"
        }
        actual = set()
        for current, dirs, files in os.walk(target, followlinks=False):
            current_path = Path(current)
            for directory in list(dirs):
                item = current_path / directory
                if item.is_symlink():
                    raise RuntimeManagerError("Installed component contains an unexpected symbolic link.", 409)
            for filename in files:
                item = current_path / filename
                if item.is_symlink() or not item.is_file():
                    raise RuntimeManagerError("Installed component contains an unsafe file.", 409)
                actual.add(item.relative_to(self.components_root).as_posix())
        if actual != registered:
            raise RuntimeManagerError("Installed files differ from the managed-file registry; refusing removal or execution.", 409)
        for relative_path in registered:
            row = next((entry for entry in self._registered_files("component", component_id) if entry["relative_path"] == relative_path), None)
            if row is None:
                raise RuntimeManagerError("Installed component file registry is incomplete.", 409)
            path = self._root("components") / Path(*PurePosixPath(relative_path).parts)
            if sha256_file(path) != row["sha256"]:
                raise RuntimeManagerError("Installed component checksum changed; refusing to execute or remove it.", 409)

    def recover_interrupted_operations(self) -> None:
        """Recover a verified atomic promotion or clean registered staging files after a crash."""
        with _RUNTIME_LOCK:
            self._prepare_roots()
            manifests = {manifest.id: manifest for manifest in self._catalog().components}
            with self._connection() as connection:
                operations = connection.execute(
                    """SELECT id, component_id, state FROM component_operations
                       WHERE operation='install' AND state NOT IN ('installed', 'failed', 'cancelled')
                       ORDER BY created_at, rowid"""
                ).fetchall()

            for operation in operations:
                operation_id = operation["id"]
                component_id = operation["component_id"]
                manifest = manifests.get(component_id)
                if manifest is None:
                    self._set_operation(operation_id, "failed", "Interrupted install no longer exists in the trusted catalog; files were left untouched.")
                    continue
                stage_dir = self.staging_root / operation_id
                target = self.components_root / component_id
                with self._connection() as connection:
                    installed = connection.execute(
                        "SELECT 1 FROM installed_components WHERE component_id=?", (component_id,)
                    ).fetchone()
                if installed:
                    self._set_operation(operation_id, "installed", "Recovered the completed registry commit after restart.")
                    continue

                if target.is_dir() and not target.is_symlink() and not stage_dir.exists():
                    try:
                        expected = {artifact.target_path for artifact in manifest.artifacts}
                        actual = set()
                        for current, dirs, files in os.walk(target, followlinks=False):
                            current_path = Path(current)
                            if any((current_path / directory).is_symlink() for directory in dirs):
                                raise RuntimeManagerError("Promoted install contains a symbolic link.", 409)
                            for filename in files:
                                item = current_path / filename
                                if item.is_symlink() or not item.is_file():
                                    raise RuntimeManagerError("Promoted install contains an unsafe file.", 409)
                                actual.add(item.relative_to(target).as_posix())
                        if actual != expected:
                            raise RuntimeManagerError("Promoted install inventory does not match its manifest.", 409)
                        for artifact in manifest.artifacts:
                            path = target.joinpath(*PurePosixPath(artifact.target_path).parts)
                            if path.stat().st_size != artifact.size_bytes or sha256_file(path) != artifact.sha256:
                                raise RuntimeManagerError("Promoted install failed recovery verification.", 409)
                        with self._connection() as connection:
                            for artifact in manifest.artifacts:
                                relative = f"{component_id}/{artifact.target_path}"
                                connection.execute(
                                    """INSERT INTO managed_files
                                       (root_key, relative_path, owner_type, owner_id, sha256, size_bytes, registered_at)
                                       VALUES ('components', ?, 'component', ?, ?, ?, ?)
                                       ON CONFLICT(root_key, relative_path) DO UPDATE SET
                                         owner_type='component', owner_id=excluded.owner_id,
                                         sha256=excluded.sha256, size_bytes=excluded.size_bytes,
                                         registered_at=excluded.registered_at""",
                                    (relative, component_id, artifact.sha256, artifact.size_bytes, _now()),
                                )
                            connection.execute(
                                "DELETE FROM managed_files WHERE root_key='components' AND owner_type='operation' AND owner_id=?",
                                (operation_id,),
                            )
                            connection.execute(
                                """INSERT OR IGNORE INTO installed_components
                                   (component_id, version, state, installation_path, manifest_json, installed_at)
                                   VALUES (?, ?, 'installed', ?, ?, ?)""",
                                (component_id, manifest.version, str(target), manifest.model_dump_json(), _now()),
                            )
                        self._set_operation(operation_id, "installed", "Recovered and verified the atomic promotion after restart.")
                    except (OSError, RuntimeManagerError, sqlite3.Error) as error:
                        self._set_operation(operation_id, "failed", f"Recovery refused the promoted files: {error}")
                    continue

                if stage_dir.exists() and stage_dir.is_dir() and not stage_dir.is_symlink():
                    self._remove_operation_stage(operation_id, stage_dir, manifest)
                self._set_operation(operation_id, "failed", "Interrupted install was rolled back; verified download cache files may be resumed.")

    def _compatibility(self, manifest: ComponentManifest) -> Tuple[str, str]:
        os_name, architecture = _host_platform()
        for support in manifest.platforms:
            if support.os == os_name and architecture in support.architectures:
                if support.verification == "verified":
                    return "verified", f"Verified for {os_name} {architecture}."
                return "not_tested", f"{os_name} {architecture} is listed but has not been validated."
        return "unsupported", f"No verified manifest entry exists for {os_name} {architecture}."

    def _read_log_tail(self, component_id: str) -> str:
        log_path = self.logs_root / f"{component_id}.log"
        try:
            if log_path.is_symlink() or not log_path.resolve(strict=True).is_relative_to(self.directories.logs.resolve(strict=True)):
                return ""
            with log_path.open("rb") as file:
                file.seek(0, os.SEEK_END)
                size = file.tell()
                file.seek(max(0, size - 4096), os.SEEK_SET)
                return file.read(4096).decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _license_text(self, manifest: ComponentManifest) -> str:
        artifact = next((item for item in manifest.artifacts if item.target_path == manifest.license.license_file), None)
        if artifact is None:
            raise RuntimeManagerError("The component license file is not listed in its manifest.", 500)
        path = (self.bundle_root / Path(*PurePosixPath(artifact.bundle_path).parts)).resolve(strict=True)
        if not path.is_relative_to(self.bundle_root.resolve(strict=True)) or sha256_file(path) != artifact.sha256:
            raise RuntimeManagerError("The component license file failed its integrity check.", 500)
        return path.read_text(encoding="utf-8")

    def _storage(self) -> Dict[str, object]:
        rows = self._registered_files()
        by_root: Dict[str, int] = {}
        for row in rows:
            by_root[row["root_key"]] = by_root.get(row["root_key"], 0) + int(row["size_bytes"])
        try:
            disk = shutil.disk_usage(self.directories.data)
            available, total = disk.free, disk.total
        except OSError:
            available = total = None
        return {
            "registered_file_count": len(rows),
            "registered_bytes": sum(by_root.values()),
            "component_bytes": by_root.get("components", 0),
            "download_cache_bytes": by_root.get("cache", 0),
            "log_bytes": by_root.get("logs", 0),
            "temporary_bytes": by_root.get("temp", 0),
            "voice_bytes": by_root.get("voice", 0),
            "avatar_bytes": by_root.get("avatar", 0),
            "export_bytes": by_root.get("scene_assets", 0) + by_root.get("exports", 0) + by_root.get("scene_music", 0),
            "scene_model_bytes": by_root.get("scene_models", 0),
            "available_bytes": available,
            "volume_total_bytes": total,
            "paths": {
                "components": str(self.components_root),
                "cache": str(self.directories.cache),
                "logs": str(self.logs_root),
                "temporary": str(self.directories.temp),
            },
        }

    def snapshot(self) -> Dict[str, object]:
        self._prepare_roots()
        catalog = self._catalog()
        os_name, architecture = _host_platform()
        with self._connection() as connection:
            installed_rows = {
                row["component_id"]: row
                for row in connection.execute("SELECT * FROM installed_components").fetchall()
            }
            latest_ops = {}
            for row in connection.execute(
                "SELECT component_id, operation, state, details, updated_at FROM component_operations ORDER BY updated_at DESC, rowid DESC"
            ).fetchall():
                latest_ops.setdefault(row["component_id"], row)

        components = []
        for manifest in catalog.components:
            compatibility, reason = self._compatibility(manifest)
            installed = installed_rows.get(manifest.id)
            running = self.supervisor.is_running(manifest.id)
            operation = latest_ops.get(manifest.id)
            state = "running" if running else "installed" if installed else operation["state"] if operation else "available"
            update_available = bool(installed and tuple(map(int, manifest.version.split("."))) > tuple(map(int, installed["version"].split("."))))
            if installed and update_available and not running:
                state = "update_available"
            components.append(
                {
                    **manifest.model_dump(),
                    "license_text": self._license_text(manifest),
                    "download_size_bytes": manifest.download_size_bytes,
                    "installed_size_bytes": manifest.installed_size_bytes,
                    "compatibility": compatibility,
                    "compatibility_reason": reason,
                    "installation_state": state,
                    "installed_version": installed["version"] if installed else None,
                    "installed_at": installed["installed_at"] if installed else None,
                    "update_available": update_available,
                    "process_id": self.supervisor.process_id(manifest.id) if running else None,
                    "log_tail": self._read_log_tail(manifest.id) if installed else "",
                    "last_operation": {
                        "operation": operation["operation"],
                        "state": operation["state"],
                        "details": operation["details"],
                        "updated_at": operation["updated_at"],
                    } if operation else None,
                }
            )
        return {
            "catalog_version": catalog.catalog_version,
            "platform": os_name,
            "architecture": architecture,
            "components": components,
            "storage": self._storage(),
            "managed_files": [dict(row) for row in self._registered_files()],
            "updates_supported": False,
            "remote_downloads_supported": False,
        }

    def _register_partial(self, component_id: str, path: Path, _size: int) -> None:
        if path.exists():
            self._register_file("cache", path, "download", component_id)
        else:
            try:
                relative = path.resolve(strict=False).relative_to(self.directories.cache.resolve(strict=True)).as_posix()
            except ValueError as error:
                raise RuntimeManagerError("Partial download path escaped the application cache.", 500) from error
            _safe_relative(relative)
            with self._connection() as connection:
                connection.execute("DELETE FROM managed_files WHERE root_key='cache' AND relative_path=?", (relative,))

    def install(self, component_id: str, accepted_license: bool) -> Dict[str, object]:
        if not accepted_license:
            raise RuntimeManagerError("Review and accept the component license before installation.", 422)
        manifest = self._manifest(component_id)
        compatibility, reason = self._compatibility(manifest)
        if compatibility != "verified":
            raise RuntimeManagerError(f"Installation is blocked: {reason}", 409)

        with _RUNTIME_LOCK:
            self._prepare_roots()
            with self._connection() as connection:
                installed = connection.execute(
                    "SELECT version FROM installed_components WHERE component_id=?", (component_id,)
                ).fetchone()
                if installed:
                    if installed["version"] == manifest.version:
                        return {"component_id": component_id, "state": "installed", "details": "This version is already installed."}
                    raise RuntimeManagerError("A different version is installed. Remove it before installing a replacement.", 409)
                for dependency in manifest.dependencies:
                    if not connection.execute(
                        "SELECT 1 FROM installed_components WHERE component_id=?", (dependency,)
                    ).fetchone():
                        raise RuntimeManagerError(f"Required component {dependency} is not installed.", 409)
            target = self.components_root / component_id
            if target.exists():
                raise RuntimeManagerError("An unregistered installation directory already exists; refusing to overwrite it.", 409)
            disk = shutil.disk_usage(self.components_root)
            if disk.free < manifest.minimum_disk_bytes + manifest.installed_size_bytes:
                raise RuntimeManagerError("There is not enough free space for the component installation.", 507)
            cache_disk = shutil.disk_usage(self.directories.cache)
            if cache_disk.free < manifest.download_size_bytes:
                raise RuntimeManagerError("There is not enough free space in the application cache for the component download.", 507)
            if manifest.minimum_ram_bytes:
                from backend.platform.detect import collect_system_report

                memory_total = collect_system_report().memory_total_bytes
                if memory_total is None or memory_total < manifest.minimum_ram_bytes:
                    raise RuntimeManagerError("Required memory is unavailable or below the component minimum.", 409)

            operation_id = self._operation(component_id, "install", "checking")
            stage_dir = self.staging_root / operation_id
            promoted = False
            committed = False
            try:
                self._set_operation(operation_id, "downloading", "Copying the bundled, pinned test artifacts into the app cache.")
                downloads = DownloadManager(self.bundle_root, self.directories.cache)
                cache_files = []
                for artifact in manifest.artifacts:
                    cached = downloads.fetch_bundled(
                        component_id,
                        manifest.version,
                        artifact,
                        on_partial=lambda path, size: self._register_partial(component_id, path, size),
                    )
                    self._register_file("cache", cached, "download", component_id)
                    cache_files.append((artifact, cached))

                self._set_operation(operation_id, "verifying", "Checking SHA256 digests and artifact sizes.")
                for artifact, cached in cache_files:
                    if cached.stat().st_size != artifact.size_bytes or sha256_file(cached) != artifact.sha256:
                        raise RuntimeManagerError("Cached artifact failed the pinned SHA256 or size check.", 422)

                self._set_operation(operation_id, "installing", "Copying verified files into an isolated staging directory.")
                stage_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
                staged_paths = []
                for artifact, cached in cache_files:
                    staged_path = stage_dir.joinpath(*PurePosixPath(artifact.target_path).parts)
                    staged_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copyfile(cached, staged_path)
                    if os.name != "nt":
                        os.chmod(staged_path, 0o600)
                    relative = staged_path.relative_to(self.components_root).as_posix()
                    self._register_file("components", staged_path, "operation", operation_id)
                    staged_paths.append((artifact, relative, staged_path))

                self._set_operation(operation_id, "testing", "Validating the trusted Python entrypoint without executing it.")
                entrypoint = stage_dir.joinpath(*PurePosixPath(manifest.entrypoint).parts)
                source = entrypoint.read_text(encoding="utf-8")
                compile(source, manifest.entrypoint, "exec")
                license_path = stage_dir.joinpath(*PurePosixPath(manifest.license.license_file).parts)
                if not license_path.is_file() or not license_path.read_text(encoding="utf-8").startswith("MIT License"):
                    raise RuntimeManagerError("Bundled license file did not match the manifest.", 422)

                os.replace(stage_dir, target)
                promoted = True
                with self._connection() as connection:
                    for artifact, staged_relative, _staged_path in staged_paths:
                        final_relative = f"{component_id}/{artifact.target_path}"
                        updated = connection.execute(
                            """UPDATE managed_files SET owner_type='component', owner_id=?, relative_path=?,
                               sha256=?, size_bytes=?, registered_at=?
                               WHERE root_key='components' AND relative_path=? AND owner_type='operation' AND owner_id=?""",
                            (
                                component_id,
                                final_relative,
                                artifact.sha256,
                                artifact.size_bytes,
                                _now(),
                                staged_relative,
                                operation_id,
                            ),
                        )
                        if updated.rowcount != 1:
                            raise sqlite3.IntegrityError("Staged artifact was missing from the managed-file registry.")
                    connection.execute(
                        """INSERT INTO installed_components
                           (component_id, version, state, installation_path, manifest_json, installed_at)
                           VALUES (?, ?, 'installed', ?, ?, ?)""",
                        (component_id, manifest.version, str(target), manifest.model_dump_json(), _now()),
                    )
                committed = True
                self._set_operation(operation_id, "installed", "Verified files were atomically promoted and registered.")
                return {"component_id": component_id, "state": "installed", "details": "Installed and verified."}
            except (RuntimeManagerError, DownloadError, OSError, sqlite3.Error, ValidationError, SyntaxError) as error:
                if promoted and not committed and target.exists():
                    try:
                        os.replace(target, stage_dir)
                    except OSError:
                        pass
                if not committed:
                    self._remove_operation_stage(operation_id, stage_dir, manifest)
                details = error.detail if isinstance(error, RuntimeManagerError) else str(error)
                self._set_operation(operation_id, "failed", details)
                if isinstance(error, RuntimeManagerError):
                    raise
                raise RuntimeManagerError(f"Installation failed: {details}", 422) from error

    def _remove_operation_stage(self, operation_id: str, stage_dir: Path, manifest: ComponentManifest) -> None:
        stage_root = stage_dir.resolve(strict=False)
        for artifact in manifest.artifacts:
            relative = f".staging/{operation_id}/{artifact.target_path}"
            path = stage_dir.joinpath(*PurePosixPath(artifact.target_path).parts)
            if path.is_symlink():
                continue
            if path.exists() and path.is_file():
                try:
                    if path.resolve(strict=True).is_relative_to(stage_root):
                        path.unlink()
                except OSError:
                    pass
            try:
                self._remove_registered_file("components", relative)
            except RuntimeManagerError:
                continue
        if stage_dir.exists() and stage_dir.is_dir() and not stage_dir.is_symlink():
            for directory, _subdirs, _files in os.walk(stage_dir, topdown=False):
                try:
                    Path(directory).rmdir()
                except OSError:
                    pass

    def start(self, component_id: str) -> Dict[str, object]:
        manifest = self._manifest(component_id)
        compatibility, reason = self._compatibility(manifest)
        if compatibility != "verified":
            raise RuntimeManagerError(f"Start is blocked: {reason}", 409)
        with _RUNTIME_LOCK:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT version, installation_path, manifest_json FROM installed_components WHERE component_id=?", (component_id,)
                ).fetchone()
            if row is None:
                raise RuntimeManagerError("Install this component before starting it.", 409)
            if row["version"] != manifest.version:
                raise RuntimeManagerError("The installed version no longer matches the trusted catalog.", 409)
            try:
                installed_manifest = ComponentManifest.model_validate_json(row["manifest_json"])
            except ValidationError as error:
                raise RuntimeManagerError("The installed manifest record is invalid; refusing to execute it.", 409) from error
            if installed_manifest.model_dump() != manifest.model_dump():
                raise RuntimeManagerError("The installed manifest differs from the trusted catalog; reinstall before starting.", 409)
            self._verify_component_directory(component_id)
            operation_id = self._operation(component_id, "start", "checking")
            log_path = self.logs_root / f"{component_id}.log"
            try:
                self._set_operation(operation_id, "testing", "Starting the verified entrypoint and waiting for its ready health record.")
                process_id = self.supervisor.start(manifest, Path(row["installation_path"]), log_path)
                self._register_file("logs", log_path, "component", component_id)
                self._set_operation(operation_id, "running", f"Process {process_id} passed the ready-line health check.")
                return {"component_id": component_id, "state": "running", "process_id": process_id}
            except (RuntimeProcessError, RuntimeManagerError) as error:
                details = error.detail if isinstance(error, RuntimeManagerError) else str(error)
                self._set_operation(operation_id, "failed", details)
                raise RuntimeManagerError(details, 422) from error

    def stop(self, component_id: str) -> Dict[str, object]:
        self._manifest(component_id)
        with _RUNTIME_LOCK:
            with self._connection() as connection:
                installed = connection.execute(
                    "SELECT 1 FROM installed_components WHERE component_id=?", (component_id,)
                ).fetchone()
            if installed is None:
                raise RuntimeManagerError("Component is not installed.", 404)
            operation_id = self._operation(component_id, "stop", "checking")
            try:
                code = self.supervisor.stop(component_id)
                log_path = self.logs_root / f"{component_id}.log"
                if log_path.is_file():
                    self._register_file("logs", log_path, "component", component_id)
                self._set_operation(operation_id, "stopped", "Process stopped." if code is not None else "Process was already stopped.")
                return {"component_id": component_id, "state": "installed", "exit_code": code}
            except RuntimeProcessError as error:
                self._set_operation(operation_id, "failed", str(error))
                raise RuntimeManagerError(str(error), 422) from error

    def _verify_no_unregistered_files(self, component_id: str) -> None:
        target = self.components_root / component_id
        registered = {
            row["relative_path"]
            for row in self._registered_files("component", component_id)
            if row["root_key"] == "components"
        }
        actual = set()
        if target.exists():
            for current, dirs, files in os.walk(target, followlinks=False):
                base = Path(current)
                if any((base / directory).is_symlink() for directory in dirs):
                    raise RuntimeManagerError("Unexpected symbolic link in installed component; refusing removal.", 409)
                for filename in files:
                    item = base / filename
                    if item.is_symlink() or not item.is_file():
                        raise RuntimeManagerError("Unexpected file type in installed component; refusing removal.", 409)
                    actual.add(item.relative_to(self.components_root).as_posix())
        if actual != registered:
            raise RuntimeManagerError("Installed directory contains unregistered files; refusing removal.", 409)

    def remove(self, component_id: str, confirmation: str) -> Dict[str, object]:
        self._manifest(component_id)
        if confirmation != component_id:
            raise RuntimeManagerError(f"Confirm removal by entering {component_id}.", 422)
        with _RUNTIME_LOCK:
            with self._connection() as connection:
                installed = connection.execute(
                    "SELECT 1 FROM installed_components WHERE component_id=?", (component_id,)
                ).fetchone()
            if installed is None:
                raise RuntimeManagerError("Component is not installed.", 404)
            self._verify_no_unregistered_files(component_id)
            operation_id = self._operation(component_id, "remove", "removing")
            self.supervisor.stop(component_id)
            rows = self._registered_files("component", component_id)
            removed = 0
            try:
                for row in rows:
                    removed += self._remove_registered_file(row["root_key"], row["relative_path"])
                target = self.components_root / component_id
                if target.exists():
                    for directory, _subdirs, _files in os.walk(target, topdown=False):
                        Path(directory).rmdir()
                with self._connection() as connection:
                    connection.execute("DELETE FROM installed_components WHERE component_id=?", (component_id,))
                self._set_operation(operation_id, "removed", f"Removed {removed} registered bytes; project data was retained.")
                return {"component_id": component_id, "state": "removed", "removed_bytes": removed}
            except (OSError, sqlite3.Error, RuntimeManagerError) as error:
                self._set_operation(operation_id, "failed", str(error))
                if isinstance(error, RuntimeManagerError):
                    raise
                raise RuntimeManagerError(f"Removal failed: {error}", 422) from error

    def clear_registered_cache(self) -> Dict[str, object]:
        return self._clear_root("cache")

    def clear_registered_temporary_files(self) -> Dict[str, object]:
        return self._clear_root("temp")

    def _clear_root(self, root_key: str) -> Dict[str, object]:
        self._prepare_roots()
        rows = [row for row in self._registered_files() if row["root_key"] == root_key]
        removed_bytes = 0
        removed_files = 0
        for row in rows:
            removed_bytes += self._remove_registered_file(root_key, row["relative_path"])
            removed_files += 1
        root = self._root(root_key)
        bounded_parent = root / "downloads" if root_key == "cache" else root
        if bounded_parent.exists():
            for current, dirs, _files in os.walk(bounded_parent, topdown=False):
                for directory in dirs:
                    try:
                        (Path(current) / directory).rmdir()
                    except OSError:
                        pass
            if root_key == "cache":
                try:
                    bounded_parent.rmdir()
                except OSError:
                    pass
        return {"root": root_key, "removed_files": removed_files, "removed_bytes": removed_bytes}

    def uninstall_tools_keep_projects(self, confirmation: str) -> Dict[str, object]:
        if confirmation != "KEEP PROJECTS AND REMOVE TOOLS":
            raise RuntimeManagerError("Type KEEP PROJECTS AND REMOVE TOOLS to confirm this cleanup.", 422)
        with self._connection() as connection:
            component_ids = [row["component_id"] for row in connection.execute("SELECT component_id FROM installed_components").fetchall()]
        removed_components = []
        for component_id in component_ids:
            result = self.remove(component_id, component_id)
            removed_components.append(result)
        cache_result = self.clear_registered_cache()
        temp_result = self.clear_registered_temporary_files()
        return {
            "state": "tools_removed_projects_kept",
            "components": removed_components,
            "cache": cache_result,
            "temporary": temp_result,
            "project_data_removed": False,
        }

    def storage_snapshot(self) -> Dict[str, object]:
        self._prepare_roots()
        return self._storage()

