"""Saved speech preferences for Morning Inspiration and snapshots for queued videos."""

import os
from typing import Optional
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.studio.audio import StudioError
from backend.studio.speech import TextRequest
from backend.ai.settings_scope import load_scoped, settings_directory


class SpeechSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    revision: int = Field(default=0, ge=0)
    pronunciation: dict[str, str] = Field(default_factory=dict, max_length=50)
    speed: float = Field(default=1, ge=.75, le=1.5)
    pause_ms: int = Field(default=165, ge=0, le=2000)
    paragraph_pause_ms: Optional[int] = Field(default=None, ge=0, le=10000)
    title_pause_ms: Optional[int] = Field(default=None, ge=0, le=10000)
    block_pause_ms: Optional[int] = Field(default=None, ge=0, le=10000)

    @field_validator("pronunciation")
    @classmethod
    def dictionary(cls, value):
        cleaned = {key.strip(): replacement.strip() for key, replacement in value.items()}
        TextRequest.validate_pronunciation(cleaned)
        if len({key.casefold() for key in cleaned}) != len(value):
            raise ValueError("Pronunciation entries must have distinct spellings.")
        return cleaned


def load_settings(data: Path, project=None):
    return load_scoped(data, "morning-speech-settings.json", SpeechSettings, project)


def save_settings(data: Path, settings: SpeechSettings, project=None):
    import fcntl

    inherited = load_settings(data, project)
    data = settings_directory(data, project)
    data.mkdir(parents=True, exist_ok=True)
    with (data / ".morning-speech-settings.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load_settings(data) if (data / "morning-speech-settings.json").is_file() else inherited
        if current.revision != settings.revision:
            raise StudioError("Pronunciation settings changed in another window. Reload before saving.", 409)
        saved = settings.model_copy(update={"revision": settings.revision + 1})
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=data, prefix=".morning-speech-", delete=False) as file:
                temporary = Path(file.name)
                file.write(saved.model_dump_json(indent=2) + "\n")
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(data / "morning-speech-settings.json")
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return saved
