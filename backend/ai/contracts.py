"""Validated contracts for local topic, script, and director generation."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


TopicCategory = Literal[
    "Personal Thoughts",
    "Modern Life",
    "Self Improvement",
    "Interesting Facts",
    "Lifestyle",
    "Motivation",
    "Technology",
    "Human Psychology",
]

ScriptSection = Literal[
    "opening_hook",
    "introduction",
    "main_content",
    "example",
    "development",
    "conclusion",
    "call_to_action",
]

SceneType = Literal[
    "talking_head",
    "ai_generated_video",
    "ai_generated_background",
    "supporting_footage",
    "b_roll",
    "text_animation",
    "closing_scene",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TopicDraft(StrictModel):
    title: str = Field(min_length=8, max_length=120)
    description: str = Field(min_length=30, max_length=500)
    category: TopicCategory
    opening_hook: str = Field(min_length=12, max_length=240)
    target_audience: str = Field(min_length=8, max_length=180)
    estimated_duration_seconds: int = Field(ge=30, le=600)
    thumbnail_concept: str = Field(min_length=12, max_length=240)
    script_potential: str = Field(min_length=20, max_length=300)
    originality_score: int = Field(ge=0, le=100)

    @field_validator("thumbnail_concept", mode="before")
    @classmethod
    def make_thumbnail_concept_actionable(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        cleaned = value.strip().replace("_", " ").replace("-", " ")
        if len(cleaned.split()) >= 5:
            return cleaned
        title = info.data.get("title", "the video topic")
        concept = " ".join(word.capitalize() for word in cleaned.split()) or "Video"
        return "{} scene showing {}, with a clear focal subject".format(concept, title)

    @field_validator("originality_score", mode="before")
    @classmethod
    def normalize_originality_score(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        if isinstance(value, float):
            if 0 <= value <= 10:
                value *= 10
            return round(value)
        return value


class TopicBatchDraft(StrictModel):
    topics: List[TopicDraft] = Field(min_length=1, max_length=50)


class TopicRecord(TopicDraft):
    id: str
    is_favorite: bool
    is_used: bool
    created_at: datetime


class TopicBatchRequest(StrictModel):
    count: int = Field(default=30, ge=30, le=50)
    category: Optional[TopicCategory] = None


class TopicBatchResult(StrictModel):
    topics: List[TopicRecord]
    requested_count: int
    created_count: int
    duplicate_count: int
    validation_rejected_count: int
    note: str


class TopicUpdate(StrictModel):
    is_favorite: Optional[bool] = None
    is_used: Optional[bool] = None


class ScriptParagraphDraft(StrictModel):
    section: ScriptSection
    text: str = Field(min_length=10, max_length=4000)
    visual_suggestion: str = Field(min_length=8, max_length=300)
    on_screen_text: str = Field(min_length=1, max_length=120)
    voice_direction: str = Field(min_length=8, max_length=180)


class ScriptDraft(StrictModel):
    title: str = Field(min_length=5, max_length=140)
    paragraphs: List[ScriptParagraphDraft] = Field(min_length=5, max_length=32)

    @model_validator(mode="after")
    def has_opening_and_conclusion(self) -> "ScriptDraft":
        sections = [paragraph.section for paragraph in self.paragraphs]
        if sections[0] != "opening_hook":
            raise ValueError("script must begin with an opening hook")
        if "conclusion" not in sections:
            raise ValueError("script must include a conclusion")
        return self


class ScriptParagraph(StrictModel):
    paragraph_id: int = Field(ge=0)
    section: ScriptSection
    text: str
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    visual_suggestion: str
    on_screen_text: str
    voice_direction: str


class ScriptQuality(StrictModel):
    status: Literal["pass", "review"]
    word_count: int = Field(ge=0)
    target_word_range: str
    estimated_spoken_duration_seconds: int = Field(ge=0)
    warnings: List[str]


class ScriptDocument(StrictModel):
    id: str
    topic_id: str
    title: str
    requested_duration_seconds: int
    full_narration: str
    paragraphs: List[ScriptParagraph]
    quality: ScriptQuality
    created_at: datetime


class ScriptRequest(StrictModel):
    duration_seconds: Literal[30, 60, 180, 300, 600]


class ScriptEdit(StrictModel):
    full_narration: str = Field(min_length=10, max_length=50000)


class DirectorSceneDraft(StrictModel):
    scene_type: SceneType
    paragraph_indexes: List[int] = Field(min_length=1, max_length=8)
    presenter_present: bool
    camera_angle: str = Field(min_length=3, max_length=120)
    camera_movement: str = Field(min_length=3, max_length=120)
    lighting: str = Field(min_length=3, max_length=160)
    background: str = Field(min_length=3, max_length=200)
    visual_description: str = Field(min_length=12, max_length=400)
    generation_prompt: str = Field(min_length=12, max_length=500)
    transition: str = Field(min_length=3, max_length=100)
    audio_requirements: List[str] = Field(min_length=1, max_length=8)


class DirectorDraft(StrictModel):
    visual_style: str = Field(min_length=10, max_length=240)
    scenes: List[DirectorSceneDraft] = Field(min_length=3, max_length=16)


class DirectorScene(StrictModel):
    scene_id: str
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    scene_type: SceneType
    narration: str
    presenter_present: bool
    camera_angle: str
    camera_movement: str
    lighting: str
    background: str
    visual_description: str
    generation_prompt: str
    transition: str
    subtitles: str
    audio_requirements: List[str]


class DirectorDocument(StrictModel):
    id: str
    script_id: str
    visual_style: str
    total_duration_seconds: int
    scenes: List[DirectorScene]
    created_at: datetime


class ModelStatus(StrictModel):
    model_id: str
    name: str
    publisher: str
    license: str
    source_url: str
    runtime: str
    platform: str
    platform_verification: Literal["verified", "not_tested"]
    install_enabled: bool
    runtime_installed: bool
    state: Literal["available", "downloading", "installed", "failed", "cancelled"]
    expected_size_bytes: int
    downloaded_bytes: int
    sha256: str
    progress_percent: float
    message: str
    inference_ready: bool


class ModelInstallRequest(StrictModel):
    accepted_license: bool

