"""Collect host information without making model compatibility claims."""

from __future__ import annotations

import os
import platform
import shutil
from typing import Any, Dict, Optional

from . import linux, macos, windows
from .models import StorageInfo, SystemReport
from .paths import application_directories


def _architecture() -> str:
    machine = platform.machine().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "i386": "x86",
        "i686": "x86",
    }
    return aliases.get(machine, machine or "unknown")


def _os_name() -> str:
    return {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(platform.system(), platform.system())


def _os_version() -> Optional[str]:
    if platform.system() == "Darwin":
        return platform.mac_ver()[0] or platform.release() or None
    if platform.system() == "Windows":
        return platform.version() or platform.release() or None
    return platform.release() or None


def _native_report() -> Dict[str, Any]:
    system = platform.system()
    if system == "Darwin":
        return macos.collect()
    if system == "Windows":
        return windows.collect()
    if system == "Linux":
        return linux.collect()
    return {
        "cpu_model": None,
        "memory_total_bytes": None,
        "memory_available_bytes": None,
        "memory_available_method": None,
        "gpus": [],
        "accelerators": [],
    }


def collect_system_report() -> SystemReport:
    directories = application_directories()
    hardware = _native_report()
    total_bytes = used_bytes = available_bytes = None
    try:
        usage = shutil.disk_usage(directories.data)
        total_bytes, used_bytes, available_bytes = usage.total, usage.used, usage.free
    except OSError:
        pass

    storage = StorageInfo(
        data_directory=str(directories.data),
        cache_directory=str(directories.cache),
        config_directory=str(directories.config),
        temp_directory=str(directories.temp),
        total_bytes=total_bytes,
        used_bytes=used_bytes,
        available_bytes=available_bytes,
    )
    return SystemReport(
        os_name=_os_name(),
        os_version=_os_version(),
        architecture=_architecture(),
        cpu_model=hardware.get("cpu_model"),
        logical_cpu_count=os.cpu_count(),
        memory_total_bytes=hardware.get("memory_total_bytes"),
        memory_available_bytes=hardware.get("memory_available_bytes"),
        memory_available_method=hardware.get("memory_available_method"),
        gpus=hardware.get("gpus", []),
        accelerators=hardware.get("accelerators", []),
        storage=storage,
    )
