"""Native application directory resolution shared by backend adapters."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

APP_ID = "com.aibloggerstudio.app"


@dataclass(frozen=True)
class ApplicationDirectories:
    data: Path
    cache: Path
    config: Path
    logs: Path
    temp: Path


def _xdg_path(variable: str, default: Path) -> Path:
    value = os.environ.get(variable)
    return Path(value).expanduser() if value else default


def application_directories() -> ApplicationDirectories:
    """Use paths supplied by Tauri, with native development fallbacks."""
    system = platform.system()
    home = Path.home()

    if system == "Darwin":
        app_support = home / "Library" / "Application Support" / APP_ID
        defaults = ApplicationDirectories(
            data=app_support,
            cache=home / "Library" / "Caches" / APP_ID,
            config=app_support,
            logs=home / "Library" / "Logs" / APP_ID,
            temp=home / "Library" / "Caches" / APP_ID / "tmp",
        )
    elif system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / APP_ID
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / APP_ID
        defaults = ApplicationDirectories(local, local / "Cache", roaming, local / "logs", local / "Cache" / "tmp")
    else:
        data_root = _xdg_path("XDG_DATA_HOME", home / ".local" / "share") / APP_ID
        cache_root = _xdg_path("XDG_CACHE_HOME", home / ".cache") / APP_ID
        config_root = _xdg_path("XDG_CONFIG_HOME", home / ".config") / APP_ID
        defaults = ApplicationDirectories(data_root, cache_root, config_root, data_root / "logs", cache_root / "tmp")

    return ApplicationDirectories(
        data=Path(os.environ.get("AI_BLOGGER_DATA_DIR", defaults.data)).expanduser(),
        cache=Path(os.environ.get("AI_BLOGGER_CACHE_DIR", defaults.cache)).expanduser(),
        config=Path(os.environ.get("AI_BLOGGER_CONFIG_DIR", defaults.config)).expanduser(),
        logs=Path(os.environ.get("AI_BLOGGER_LOG_DIR", defaults.logs)).expanduser(),
        temp=Path(os.environ.get("AI_BLOGGER_TEMP_DIR", defaults.temp)).expanduser(),
    )
