"""Persisted video-generation defaults; individual jobs retain their own snapshots."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.ai.length import WordRange
from backend.ai.settings_scope import load_scoped, settings_directory
from backend.studio.audio import StudioError


class Narrator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=80)
    voice: str = Field(min_length=1, max_length=48)


class GenerationDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(default=0, ge=0)
    language: Literal["uk", "en"] = "uk"
    text_range: Optional[WordRange] = None
    narrator: Optional[Narrator] = None
    style: Literal["watercolor", "ink", "cinematic", "biblical"] = "watercolor"
    resolution: Literal["720p", "1080p", "vertical"] = "720p"
    captions: Literal["handwritten", "clean", "off"] = "handwritten"
    seconds: Optional[int] = Field(default=None, ge=15, le=120)
    scenes: Optional[int] = Field(default=None, ge=2, le=15)

    @model_validator(mode="after")
    def duration(self):
        if self.text_range is not None and self.seconds is not None:
            raise ValueError("Use automatic video duration with a word range.")
        return self


def load_defaults(data: Path, project=None):
    return load_scoped(data, "morning-generation-defaults.json", GenerationDefaults, project)


def save_defaults(data: Path, settings: GenerationDefaults, project=None):
    import fcntl

    inherited = load_defaults(data, project)
    data = settings_directory(data, project)
    data.mkdir(parents=True, exist_ok=True)
    with (data / ".morning-generation-defaults.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load_defaults(data) if (data / "morning-generation-defaults.json").is_file() else inherited
        if current.revision != settings.revision:
            raise StudioError("Generation defaults changed in another window. Reload before saving.", 409)
        saved = settings.model_copy(update={"revision": settings.revision + 1})
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=data, prefix=".morning-defaults-", delete=False) as file:
                temporary = Path(file.name)
                file.write(saved.model_dump_json(indent=2) + "\n")
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(data / "morning-generation-defaults.json")
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return saved
