"""Resumable copying of integrity-pinned bundled artifacts into app cache."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from .models import Artifact


class DownloadError(RuntimeError):
    """A bundled artifact could not be safely staged in the local cache."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained_file(root: Path, relative: str) -> Path:
    lexical = root / Path(*PurePosixPath(relative).parts)
    if lexical.is_symlink():
        raise DownloadError("Bundled artifact must not be a symbolic link.")
    candidate = lexical.resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
        raise DownloadError("Bundled artifact path is missing or outside the trusted bundle.")
    return candidate


def _prefix_matches(source: Path, partial: Path) -> bool:
    source_size = source.stat().st_size
    partial_size = partial.stat().st_size
    if partial_size > source_size:
        return False
    with source.open("rb") as expected, partial.open("rb") as actual:
        remaining = partial_size
        while remaining:
            amount = min(1024 * 1024, remaining)
            if expected.read(amount) != actual.read(amount):
                return False
            remaining -= amount
    return True


class DownloadManager:
    """Copies shipped test assets; remote sources are intentionally not enabled."""

    def __init__(self, bundle_root: Path, cache_root: Path):
        self.bundle_root = bundle_root
        self.cache_root = cache_root

    def fetch_bundled(
        self,
        component_id: str,
        version: str,
        artifact: Artifact,
        on_partial: Optional[Callable[[Path, int], None]] = None,
    ) -> Path:
        source = _contained_file(self.bundle_root, artifact.bundle_path)
        if source.stat().st_size != artifact.size_bytes:
            raise DownloadError("Bundled artifact size does not match the pinned manifest.")

        relative = Path("downloads") / component_id / version / Path(*PurePosixPath(artifact.target_path).parts)
        destination = self.cache_root / relative
        partial = destination.with_name(destination.name + ".part")
        self.cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.cache_root.is_symlink() or not self.cache_root.is_dir():
            raise DownloadError("Application cache root is not a regular directory.")
        cache_root = self.cache_root.resolve(strict=True)
        current_parent = self.cache_root
        for part in relative.parent.parts:
            current_parent = current_parent / part
            if current_parent.is_symlink():
                raise DownloadError("Artifact cache path contains an unexpected symbolic link.")
            current_parent.mkdir(exist_ok=True, mode=0o700)
            if not current_parent.resolve(strict=True).is_relative_to(cache_root):
                raise DownloadError("Artifact cache path escaped the application cache directory.")
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise DownloadError("Artifact cache destination is not a regular application-owned file.")

        if destination.is_file() and destination.stat().st_size == artifact.size_bytes:
            if sha256_file(destination) == artifact.sha256:
                return destination
            destination.unlink()
        if partial.is_symlink():
            partial.unlink()
            if on_partial:
                on_partial(partial, 0)
        elif partial.exists() and (not partial.is_file() or not _prefix_matches(source, partial)):
            partial.unlink(missing_ok=True)
            if on_partial:
                on_partial(partial, 0)

        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > artifact.size_bytes:
            partial.unlink()
            offset = 0

        try:
            with source.open("rb") as reader, partial.open("ab" if offset else "wb") as writer:
                reader.seek(offset)
                while True:
                    block = reader.read(64 * 1024)
                    if not block:
                        break
                    writer.write(block)
                    writer.flush()
                    os.fsync(writer.fileno())
                    if on_partial:
                        on_partial(partial, partial.stat().st_size)
        except OSError as error:
            if on_partial and partial.exists():
                on_partial(partial, partial.stat().st_size)
            raise DownloadError("Bundled artifact copy failed; its verified prefix can be resumed.") from error

        if partial.stat().st_size != artifact.size_bytes or sha256_file(partial) != artifact.sha256:
            partial.unlink(missing_ok=True)
            if on_partial:
                on_partial(partial, 0)
            raise DownloadError("Bundled artifact failed its SHA256 or size check.")

        os.replace(partial, destination)
        if on_partial:
            on_partial(partial, 0)
        return destination
