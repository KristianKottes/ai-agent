"""Validated data contracts for the project studios."""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import Field, model_validator

from backend.studio.motion import Composition, RenderSettings, Spec

IMPORT_LIMIT = 32 * 1024 * 1024
MAX_REFERENCES = 8


class MediaInput(Spec):
    name: str = Field(min_length=1, max_length=240)
    content_base64: str = Field(min_length=1, max_length=4 * ((IMPORT_LIMIT + 2)//3))


class RecordingInput(Spec):
    seconds: int = Field(ge=3, le=30)


class ReferenceClip(MediaInput):
    start: float = Field(default=0, ge=0, lt=1200)
    end: Optional[float] = Field(default=None, gt=0, le=1200)
    trim_silence: bool = True
    denoise: bool = False

    @model_validator(mode="after")
    def validate_trim(self):
        if self.end is not None and not 3 <= self.end - self.start <= 60:
            raise ValueError("Select between 3 and 60 seconds before silence trimming.")
        return self


class ReferenceInput(ReferenceClip):
    language: Literal["en", "uk"]
    authorization_confirmed: bool = Field(strict=True)
    additional_recordings: list[ReferenceClip] = Field(default_factory=list, max_length=MAX_REFERENCES - 1)

    @model_validator(mode="after")
    def validate_recordings(self):
        if not self.authorization_confirmed:
            raise ValueError("Confirm that you have permission to clone the voice in all recordings.")
        return self


class Draft(Spec):
    asset_id: Optional[UUID] = None
    composition: Composition
    settings: RenderSettings = Field(default_factory=RenderSettings)


class CompositionCopy(Draft):
    title: str = Field(min_length=1, max_length=120)


class RenderRequest(Draft):
    asset_id: UUID


class PreviewRequest(RenderRequest):
    time: float = Field(default=0, ge=0, le=1200)


class PresetInput(Spec):
    name: str = Field(min_length=1, max_length=100)
    composition: Composition
    settings: RenderSettings = Field(default_factory=RenderSettings)


class ToolAction(Spec):
    action: Literal["install", "cancel", "test", "enable", "disable", "remove"]
    accepted_license: bool = False
