"""Stage checksum-pinned studio models and their optional Apple Silicon runtimes."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import PurePosixPath
from zipfile import ZipFile

from backend.runtime.downloader import sha256_file
from backend.studio.audio import StudioError


class InstallCancelled(Exception):
    pass


def host_requirement():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return "This model requires the Apple Silicon version of AI Blogger Studio on a Mac."
    macos = platform.mac_ver()[0].split(".")[0]
    if not macos.isdigit() or int(macos) < 14:
        return "This model requires macOS 14 or newer."
    if sys.version_info[:2] != (3, 12):
        major, minor = sys.version_info[:2]
        return (f"This app is using Python {major}.{minor}. This model requires Python 3.12. "
                "Open the packaged macOS app to use its bundled runtime.")
    return ""


def compatible_host():
    return not host_requirement()


def offline_environment():
    return {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
            "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1"}


@lru_cache(maxsize=2)
def manifest(path):
    return json.loads(path.read_text(encoding="utf-8"))


def download_bytes(path):
    specification = manifest(path)
    return sum(part["size"] for key in ("assets", "wheels") for part in specification[key])


def download(artifact, target, on_progress, cancelled):
    size, digest = artifact["size"], artifact["sha256"]
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise StudioError("The model download path is unsafe.", 500)
    offset = target.stat().st_size if target.is_file() else 0
    if offset == size and sha256_file(target) == digest:
        on_progress(size)
        return
    if offset >= size:
        target.unlink(missing_ok=True)
        offset = 0
    if cancelled():
        raise InstallCancelled()

    headers = {"User-Agent": "AI-Blogger-Studio"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(artifact["url"], headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=25)
    except urllib.error.HTTPError as error:
        if error.code == 416 and offset:
            error.close()
            target.unlink()
            return download(artifact, target, on_progress, cancelled)
        raise
    with response:
        if response.status == 200:
            offset = 0
        elif response.status != 206 or response.headers.get("Content-Range") != f"bytes {offset}-{size-1}/{size}":
            raise StudioError("The model server sent an unexpected byte range.", 502)
        with target.open("ab" if offset else "wb") as output:
            while not cancelled():
                data = response.read(1024 * 1024)
                if not data:
                    break
                if output.tell() + len(data) > size:
                    raise StudioError("A download exceeded its pinned size.", 502)
                output.write(data)
                on_progress(output.tell())
    if cancelled():
        raise InstallCancelled()
    if target.stat().st_size != size:
        raise StudioError("A model download was interrupted. Retry to resume it.", 502)
    if sha256_file(target) != digest:
        target.unlink()
        raise StudioError("A model file failed checksum verification. Retry to download a fresh copy.", 502)


def unpack_wheel(source, runtime, cancelled):
    with ZipFile(source) as wheel:
        if sum(member.file_size for member in wheel.infolist()) > 850_000_000:
            raise StudioError("A runtime wheel exceeds the extraction limit.", 502)
        for member in wheel.infolist():
            if cancelled():
                raise InstallCancelled()
            path = PurePosixPath(member.filename)
            parts = path.parts
            if (not parts or path.is_absolute() or ".." in parts or "\\" in member.filename
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise StudioError("A runtime wheel contains an unsupported file path.", 502)
            if parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in ("purelib", "platlib"):
                    continue
                parts = parts[2:]
            target = runtime.joinpath(*parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with wheel.open(member) as content, target.open("xb") as output:
                shutil.copyfileobj(content, output)


def stage(path, cache, target, on_progress, on_unpack, cancelled):
    specification = manifest(path)
    completed = 0
    for part in specification["assets"] + specification["wheels"]:
        # Use owned, flat cache names while retaining the upstream hash in the manifest.
        cache_name = hashlib.sha256(part["path"].encode()).hexdigest()
        download(part, cache / cache_name, lambda received: on_progress(completed + received), cancelled)
        completed += part["size"]
    on_unpack()
    for part in specification["assets"]:
        if cancelled():
            raise InstallCancelled()
        cached = cache / hashlib.sha256(part["path"].encode()).hexdigest()
        destination = target / part["path"]
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.link(cached, destination)
    runtime = target / "runtime"
    for part in specification["wheels"]:
        cached = cache / hashlib.sha256(part["path"].encode()).hexdigest()
        unpack_wheel(cached, runtime, cancelled)
