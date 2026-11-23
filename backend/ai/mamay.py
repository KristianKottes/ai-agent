"""Editable MamayLM writing instructions shared by topic and video generation."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.ai.settings_scope import load_scoped, settings_directory


class MamaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    revision: int = Field(default=0, ge=0)
    voice_rules: str = Field(default=(
        "Write like a thoughtful friend sharing a morning reflection. Be warm, direct, sincere, and grounded. "
        "Address one listener. Use natural spoken language. Avoid a lecturing tone, slogans, and grand declarations."
    ), max_length=1500)
    rhythm_rules: str = Field(default=(
        "Vary sentence length and structure. Mix short sentences with longer thoughts. "
        "Use occasional questions and natural pauses where they fit. Let the idea develop across the scenes. "
        "Avoid repeated sentence openings, uniform scene formulas, and a forced call to action in every scene."
    ), max_length=1500)
    title_rules: str = Field(default=(
        "Vary title length and construction. Prefer concise titles of 2–5 words when they express the idea well, "
        "but allow other lengths when natural. A title may be a simple phrase, a question, or one complete "
        "sentence. Do not default to the formula 'poetic phrase: explanatory subtitle'; use a colon only when "
        "it genuinely suits the idea. Across a batch of topics, mix title forms instead of repeating one template. "
        "Choose specific, human wording that fits the reflection. Use natural sentence case, plain text, "
        "and no Markdown heading markers."
    ), max_length=1500)
    bible_rules: str = Field(default=(
        "Let the selected Bible passage shape one clear reflection connected to the listener's life. "
        "Preserve the passage's meaning. Paraphrase Scripture carefully and distinguish an original prayer from "
        "a quotation. Avoid invented quotations, verse references, promises, and repeated generic prayers."
    ), max_length=1500)
    structure_rules: str = Field(default=(
        "Structure the script in this order: title, Bible text, Bible reference, main text. "
        "Put a short, natural title in the title field and include that exact title once at the start of the "
        "first scene's narration so it is spoken aloud. Keep the Bible excerpt in bible_text, separate from "
        "the title. Put its book, chapter and verse in bible_reference. In narration, separate the title, "
        "Bible text and reference with blank lines, in that exact order. Place the reference after the "
        "passage, never before it or inside the title. "
        "Quote accurately; if the exact wording is uncertain, "
        "explicitly label a faithful paraphrase instead of presenting it as a quotation. "
        "Begin the main text in the second scene and develop one connected, personal reflection through the "
        "remaining scenes. Keep the Bible text distinct from the reflection. During editing, preserve the "
        "spoken title, Bible excerpt, its reference, and this section order. Do not read the section labels aloud."
    ), max_length=1500)
    topic_rules: str = Field(default=(
        "Generate distinct Morning Inspiration topics about spiritual themes based on the Bible. "
        "Each topic should offer a clear reflection on trust, gratitude, hope, forgiveness, peace, or another "
        "biblical theme. Choose a real chapter and verse reference and explain a specific angle in one sentence. "
        "Use natural, engaging titles without sensational claims."
    ), max_length=1500)
    editor_rules: str = Field(default=(
        "Edit the draft for natural spoken delivery. Remove clichés, repetition, stiff phrasing, and unnecessary "
        "explanations. Improve rhythm and transitions while preserving the thought, biblical meaning, language, "
        "and scene order. Keep the writer's strongest phrasing. Make each caption a short phrase from its narration."
    ), max_length=1500)
    examples_uk: str = Field(default="", max_length=3000)
    examples_en: str = Field(default="", max_length=3000)
    editor_enabled: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def context_budget(self):
        fields = self.model_dump(exclude={"revision", "editor_enabled"})
        if sum(len(value) for value in fields.values()) > 10_000:
            raise ValueError("Keep the rules and examples within 10,000 characters in total.")
        return self

    def instructions(self, language, *, topics=False):
        rules = [self.voice_rules, self.rhythm_rules, self.title_rules, self.bible_rules]
        if topics:
            rules.append(self.topic_rules)
        else:
            rules.append(self.structure_rules)
        examples = self.examples_en if language == "en" else self.examples_uk
        if examples:
            rules.append("Follow the voice and rhythm of these examples, using fresh wording for the selected topic.\n" + examples)
        return "\n\n".join(rule for rule in rules if rule)


class SettingsConflict(ValueError):
    pass


def load_settings(data: Path, project=None):
    return load_scoped(data, "mamaylm-settings.json", MamaySettings, project)


def save_settings(data: Path, settings: MamaySettings, project=None):
    import fcntl

    inherited = load_settings(data, project)
    data = settings_directory(data, project)
    data.mkdir(parents=True, exist_ok=True)
    with (data / ".mamaylm-settings.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load_settings(data) if (data / "mamaylm-settings.json").is_file() else inherited
        if current.revision != settings.revision:
            raise SettingsConflict("MamayLM settings changed in another window. Reload them before saving.")
        saved = settings.model_copy(update={"revision": current.revision + 1})
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=data, prefix=".mamaylm-", delete=False) as file:
                temporary = Path(file.name)
                file.write(saved.model_dump_json(indent=2) + "\n")
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(data / "mamaylm-settings.json")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return saved
