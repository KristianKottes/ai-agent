"""Validated inclusive word ranges shared by defaults, jobs and generation."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WordRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_words: int = Field(ge=50, le=1000, strict=True)
    max_words: int = Field(ge=50, le=1000, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.min_words > self.max_words:
            raise ValueError("Minimum words must not exceed maximum words.")
        return self

    @property
    def target(self):
        return (self.min_words + self.max_words) // 2


def bounds(target, limits=None):
    if limits is not None:
        return limits
    tolerance = max(5, round(target * .15))
    return target - tolerance, target + tolerance
