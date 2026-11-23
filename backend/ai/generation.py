"""Offline creative generation and persistence backed by llama.cpp."""

from __future__ import annotations

import json
import os
import platform
import re
import sqlite3
import threading
import uuid
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from .contracts import (
    DirectorDocument,
    DirectorDraft,
    DirectorScene,
    ModelStatus,
    ScriptDocument,
    ScriptDraft,
    ScriptEdit,
    ScriptParagraph,
    ScriptParagraphDraft,
    ScriptQuality,
    TopicBatchRequest,
    TopicBatchResult,
    TopicDraft,
    TopicRecord,
)
from .model_manager import LocalModelManager, ModelInstallError

_MODEL_LOCK = threading.RLock()
_DEFAULT_CATEGORIES = [
    "Personal Thoughts",
    "Modern Life",
    "Self Improvement",
    "Interesting Facts",
    "Lifestyle",
    "Motivation",
    "Technology",
    "Human Psychology",
]
_CATEGORY_FOCUS = {
    "Personal Thoughts": "relationships, identity, memory, decisions, and everyday reflections",
    "Modern Life": "work patterns, digital habits, urban routines, and consumer culture",
    "Self Improvement": "focus, practical habits, learning skills, and personal boundaries",
    "Interesting Facts": "surprising science, nature, history, space, and the human body",
    "Lifestyle": "food, home routines, travel, personal style, and everyday wellbeing",
    "Motivation": "goal-setting, recovering from setbacks, discipline, and steady progress",
    "Technology": "useful devices, artificial intelligence, cybersecurity, and computing trends",
    "Human Psychology": "emotions, memory, social behavior, cognitive biases, and decision-making",
}
_CATEGORY_ANGLES = {
    "Personal Thoughts": ["why friendships change in adulthood", "how family rituals carry meaning", "what nostalgia does to memory", "making choices without outside approval", "the difference between privacy and secrecy", "setting boundaries with relatives", "why places can feel like home", "how small routines shape identity"],
    "Modern Life": ["how delivery apps change shopping", "why notifications interrupt focused work", "what hybrid work changes about weekdays", "living with too many subscriptions", "how city noise affects daily routines", "the hidden cost of convenience", "what digital clutter does to attention", "how fast fashion changes shopping habits"],
    "Self Improvement": ["a five-minute weekly planning habit", "how to practice one skill consistently", "ways to make distractions harder to reach", "setting a boundary without overexplaining", "how to restart after missing a habit", "learning a skill through short practice", "building a calmer morning routine", "how to finish tasks with small steps"],
    "Interesting Facts": ["why rain has a distinct smell", "how octopus blood carries oxygen", "what makes ancient Roman concrete durable", "how migratory birds navigate at night", "why some planets have extreme seasons", "how deep-sea animals make light", "what causes the northern lights", "how the human body senses balance"],
    "Lifestyle": ["planning simple meals for a busy week", "starting a small balcony garden", "choosing clothes that mix easily", "organizing a compact entryway", "making travel days less stressful", "creating a screen-free evening routine", "keeping houseplants healthy in winter", "building a practical weekend reset"],
    "Motivation": ["how to restart after a missed goal", "staying steady when progress is slow", "breaking a large goal into weekly steps", "what to do after a public setback", "keeping promises to yourself", "building discipline without relying on mood", "how to celebrate small milestones", "finishing a project when enthusiasm fades"],
    "Technology": ["how passkeys replace passwords", "what on-device AI changes for privacy", "why phone batteries age over time", "how satellite internet reaches remote areas", "the basics of repairing consumer electronics", "how recommendation systems rank content", "what makes a smart home device secure", "how foldable screens work"],
    "Human Psychology": ["why people remember unfinished tasks", "how confirmation bias shapes everyday choices", "what facial mimicry does in conversations", "why familiar songs trigger memories", "how social proof changes decisions", "why people misjudge short waits", "what habits reveal about attention", "how emotions affect risk judgments"],
}


class GenerationError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 502):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _near_duplicate(left: str, right: str) -> bool:
    left_key, right_key = _title_key(left), _title_key(right)
    if left_key == right_key:
        return True
    left_tokens, right_tokens = set(left_key.split()), set(right_key.split())
    if len(left_tokens) < 4 or len(right_tokens) < 4:
        return False
    union = left_tokens | right_tokens
    return SequenceMatcher(None, left_key, right_key).ratio() >= 0.88 or (
        bool(union) and len(left_tokens & right_tokens) / len(union) >= 0.82
    )


def _escape_control_chars_in_json(content: str) -> str:
    output: List[str] = []
    in_string = False
    escaped = False
    for character in content:
        codepoint = ord(character)
        if in_string and codepoint < 0x20:
            output.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(character, "\\u{:04x}".format(codepoint)))
            continue
        output.append(character)
        if not in_string and character == '"':
            in_string = True
        elif in_string and escaped:
            escaped = False
        elif in_string and character == "\\":
            escaped = True
        elif in_string and character == '"':
            in_string = False
    return "".join(output)


def _parse_json(content: str) -> Dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    cleaned = _escape_control_chars_in_json(cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise GenerationError("The local model did not return a JSON object. Try generating again.")
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as error:
            raise GenerationError("The local model returned malformed JSON. Try generating again.") from error
    if not isinstance(parsed, dict):
        raise GenerationError("The local model response was not a JSON object.")
    return parsed


def _validation_summary(error: ValidationError) -> str:
    issues = []
    for item in error.errors()[:8]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "response"
        issues.append("{} ({})".format(location, item.get("type", "invalid")))
    return ", ".join(issues)


def _script_response_schema() -> Dict[str, Any]:
    paragraph = {
        "type": "object",
        "properties": {
            "section": {"type": "string", "enum": ["opening_hook", "introduction", "main_content", "example", "development", "conclusion", "call_to_action"]},
            "text": {"type": "string"},
            "visual_suggestion": {"type": "string"},
            "on_screen_text": {"type": "string"},
            "voice_direction": {"type": "string"},
        },
        "required": ["section", "text", "visual_suggestion", "on_screen_text", "voice_direction"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "paragraphs": {"type": "array", "items": paragraph, "minItems": 5, "maxItems": 10},
        },
        "required": ["title", "paragraphs"],
        "additionalProperties": False,
    }


def _director_response_schema() -> Dict[str, Any]:
    scene = {
        "type": "object",
        "properties": {
            "scene_type": {"type": "string", "enum": ["talking_head", "ai_generated_video", "ai_generated_background", "supporting_footage", "b_roll", "text_animation", "closing_scene"]},
            "paragraph_indexes": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "maxItems": 8},
            "presenter_present": {"type": "boolean"},
            "camera_angle": {"type": "string"},
            "camera_movement": {"type": "string"},
            "lighting": {"type": "string"},
            "background": {"type": "string"},
            "visual_description": {"type": "string"},
            "generation_prompt": {"type": "string"},
            "transition": {"type": "string"},
            "audio_requirements": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
        },
        "required": ["scene_type", "paragraph_indexes", "presenter_present", "camera_angle", "camera_movement", "lighting", "background", "visual_description", "generation_prompt", "transition", "audio_requirements"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "visual_style": {"type": "string"},
            "scenes": {"type": "array", "items": scene, "minItems": 3, "maxItems": 8},
        },
        "required": ["visual_style", "scenes"],
        "additionalProperties": False,
    }


def _normalize_script_fields(value: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(value)
    source_paragraphs = normalized.get("paragraphs")
    if not isinstance(source_paragraphs, list):
        return normalized
    paragraphs = []
    for source in source_paragraphs:
        if not isinstance(source, dict):
            paragraphs.append(source)
            continue
        paragraph = dict(source)
        narration = paragraph.get("text") if isinstance(paragraph.get("text"), str) else ""
        caption = paragraph.get("on_screen_text")
        if not isinstance(caption, str) or not caption.strip():
            paragraph["on_screen_text"] = " ".join(narration.split()[:8]) or "Key idea"
        elif len(caption) > 120:
            paragraph["on_screen_text"] = caption[:117].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        direction = paragraph.get("voice_direction")
        if not isinstance(direction, str) or len(direction.strip()) < 8:
            tone = direction.strip().capitalize() if isinstance(direction, str) and direction.strip() else "Warm"
            paragraph["voice_direction"] = "{} delivery with clear pacing.".format(tone)
        elif len(direction) > 180:
            paragraph["voice_direction"] = direction[:177].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        visual = paragraph.get("visual_suggestion")
        if not isinstance(visual, str) or len(visual.strip()) < 8:
            paragraph["visual_suggestion"] = "Supporting scene illustrating the narration."
        elif len(visual) > 300:
            paragraph["visual_suggestion"] = visual[:297].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        paragraphs.append(paragraph)
    normalized["paragraphs"] = paragraphs
    if paragraphs and isinstance(paragraphs[0], dict):
        paragraphs[0]["section"] = "opening_hook"
    if paragraphs and all(isinstance(paragraph, dict) for paragraph in paragraphs):
        if not any(paragraph.get("section") == "conclusion" for paragraph in paragraphs):
            paragraphs[-1]["section"] = "conclusion"
    return normalized


def _normalize_director_fields(value: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(value)
    style = normalized.get("visual_style")
    if not isinstance(style, str) or len(style.strip()) < 10:
        normalized["visual_style"] = "Warm editorial style with consistent natural lighting."
    elif len(style) > 240:
        normalized["visual_style"] = style[:237].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    scenes = normalized.get("scenes")
    if not isinstance(scenes, list):
        return normalized
    clean_scenes = []
    defaults = {
        "camera_angle": "Eye-level medium shot",
        "camera_movement": "Slow, steady push-in",
        "lighting": "Soft natural key light",
        "background": "Simple, uncluttered neutral setting",
        "visual_description": "A clear composition that illustrates the selected narration.",
        "generation_prompt": "Realistic video composition with a clear subject and consistent natural lighting.",
        "transition": "Soft dissolve",
    }
    limits = {
        "camera_angle": 120,
        "camera_movement": 120,
        "lighting": 160,
        "background": 200,
        "visual_description": 400,
        "generation_prompt": 500,
        "transition": 100,
    }
    for source in scenes:
        if not isinstance(source, dict):
            clean_scenes.append(source)
            continue
        scene = dict(source)
        for field, fallback in defaults.items():
            current = scene.get(field)
            minimum = 12 if field in ("visual_description", "generation_prompt") else 3
            if not isinstance(current, str) or len(current.strip()) < minimum:
                scene[field] = fallback
            elif len(current) > limits[field]:
                limit = limits[field]
                scene[field] = current[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        if not isinstance(scene.get("audio_requirements"), list) or not scene["audio_requirements"]:
            scene["audio_requirements"] = ["Clear narration with subtle room tone"]
        clean_scenes.append(scene)
    normalized["scenes"] = clean_scenes
    return normalized


def _assign_director_paragraphs(draft: DirectorDraft, paragraph_count: int) -> DirectorDraft:
    scene_count = min(len(draft.scenes), paragraph_count, 8)
    if scene_count < 1:
        return draft
    selected = draft.scenes[:scene_count]
    base, extra = divmod(paragraph_count, scene_count)
    cursor = 0
    scenes = []
    for index, scene in enumerate(selected):
        size = base + (1 if index < extra else 0)
        group = list(range(cursor, cursor + size))
        scenes.append(scene.model_copy(update={"paragraph_indexes": group}))
        cursor += size
    return draft.model_copy(update={"scenes": scenes})


class LocalCreator:
    """Lazy model loader and typed generators. Inference stays in this process."""

    def __init__(self, manager: LocalModelManager, database_path: Path):
        self.manager = manager
        self.database_path = database_path
        self._llm = None
        self._llm_path: Optional[Path] = None

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _model(self):
        try:
            model_path = self.manager.model_file()
        except ModelInstallError as error:
            raise GenerationError(error.detail, error.status_code) from error
        with _MODEL_LOCK:
            if self._llm is not None and self._llm_path == model_path:
                return self._llm
            try:
                from llama_cpp import Llama
            except ImportError as error:
                raise GenerationError(
                    "The local inference runtime is not installed. Install the pinned backend dependencies first.",
                    503,
                ) from error
            try:
                cpu_count = os.cpu_count() or 4
                use_metal = platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64")
                self._llm = Llama(
                    model_path=str(model_path),
                    n_ctx=8192,
                    n_threads=max(2, min(cpu_count, 8)),
                    n_gpu_layers=-1 if use_metal else 0,
                    verbose=False,
                )
                self._llm_path = model_path
            except Exception as error:
                raise GenerationError("The local model could not be loaded by llama.cpp.", 503) from error
            return self._llm

    def _generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float = 0.7,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        model = self._model()
        with _MODEL_LOCK:
            try:
                response = model.create_chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    top_p=0.9,
                    max_tokens=max_tokens,
                    response_format={
                        "type": "json_object",
                        **({"schema": response_schema} if response_schema else {}),
                    },
                )
            except TypeError:
                # Older llama-cpp-python builds do not expose response_format.
                response = model.create_chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt + "\nReturn exactly one JSON object and no markdown."},
                    ],
                    temperature=temperature,
                    top_p=0.9,
                    max_tokens=max_tokens,
                )
            except Exception as error:
                raise GenerationError("Local inference failed. Check available memory and try again.", 503) from error
        choices = response.get("choices", [])
        message = choices[0].get("message", {}) if choices else {}
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise GenerationError("The local model returned an empty response. Try again.")
        return _parse_json(content)

    def status(self) -> ModelStatus:
        return self.manager.status()

    def generate_topics(self, request: TopicBatchRequest) -> TopicBatchResult:
        with self._connection() as connection:
            existing_titles = [row[0] for row in connection.execute("SELECT title FROM topics ORDER BY created_at DESC LIMIT 300")]

        seen = list(existing_titles)
        drafts: List[TopicDraft] = []
        duplicate_count = 0
        validation_rejected_count = 0
        requested = request.count
        schedule = (
            [(request.category, requested)]
            if request.category
            else [
                (category, requested // len(_DEFAULT_CATEGORIES) + (1 if index < requested % len(_DEFAULT_CATEGORIES) else 0))
                for index, category in enumerate(_DEFAULT_CATEGORIES)
            ]
        )
        # Category-specific batches keep the output balanced. A JSON schema grammar
        # constrains every required field and the exact array length on compact models.
        for batch_category, category_target in schedule:
            category_created = 0
            for attempt in range(4):
                remaining = category_target - category_created
                if remaining <= 0:
                    break
                batch_size = min(5, remaining + 2)
                excluded = "\nAlready used or newly generated titles to avoid:\n" + "\n".join("- " + title for title in seen[-100:])
                angles = _CATEGORY_ANGLES[batch_category]
                offset = (attempt * batch_size) % len(angles)
                selected_angles = [angles[(offset + index) % len(angles)] for index in range(batch_size)]
                prompt = (
                    "Create exactly {} varied, general-audience English-language video topic ideas. Do not make them about content creation, creativity, or digital art.\n".format(batch_size)
                    + "Set category to exactly {!r} for every item.\n".format(batch_category)
                    + "For this category, explore subject areas such as: {}. Use a different subject and framing for every idea.\n".format(_CATEGORY_FOCUS[batch_category])
                    + "Use these different subject angles, one per item: {}. Start titles with their concrete subject. Never use the generic opener 'Did you know?'.\n".format("; ".join(selected_angles))
                    + "Return JSON with a top-level topics array. Each topic must have title, description, category, opening_hook, target_audience, estimated_duration_seconds, thumbnail_concept, script_potential, originality_score. "
                    + "Keep descriptions concise and make thumbnail_concept a concrete composition of at least six words. Duration must be 30 to 600 seconds. originality_score must be a useful whole-number distinctiveness estimate, usually between 40 and 95 unless the idea is generic; it is never a view prediction."
                    + excluded
                )
                item_schema = TopicDraft.model_json_schema()
                item_schema.pop("title", None)
                item_schema["properties"]["category"]["enum"] = [batch_category]
                response_schema = {
                    "type": "object",
                    "properties": {
                        "topics": {
                            "type": "array",
                            "items": item_schema,
                            "minItems": batch_size,
                            "maxItems": batch_size,
                        }
                    },
                    "required": ["topics"],
                    "additionalProperties": False,
                }
                try:
                    parsed = self._generate_json(
                        "You are a concise English-language video topic editor. Follow the exact requested category and output valid JSON only.",
                        prompt,
                        max_tokens=max(1400, batch_size * 430),
                        temperature=0.85,
                        response_schema=response_schema,
                    )
                except GenerationError as error:
                    if error.status_code != 502:
                        raise
                    validation_rejected_count += batch_size
                    continue
                raw_batch = parsed.get("topics", [])
                if not isinstance(raw_batch, list):
                    raise GenerationError("The local model returned an invalid topics array. Try again.")
                valid_batch: List[TopicDraft] = []
                last_validation: Optional[ValidationError] = None
                for item in raw_batch:
                    if not isinstance(item, dict):
                        validation_rejected_count += 1
                        continue
                    candidate = dict(item)
                    candidate["category"] = batch_category
                    try:
                        valid_batch.append(TopicDraft.model_validate(candidate))
                    except ValidationError as error:
                        validation_rejected_count += 1
                        last_validation = error
                if not valid_batch:
                    if last_validation is not None and attempt == 3:
                        raise GenerationError("The local model's topic fields failed validation: {}. Try again.".format(_validation_summary(last_validation))) from last_validation
                    continue
                for draft in valid_batch:
                    if category_created >= category_target:
                        break
                    if any(_near_duplicate(draft.title, title) for title in seen):
                        duplicate_count += 1
                        continue
                    drafts.append(draft)
                    category_created += 1
                    seen.append(draft.title)

        if not drafts:
            raise GenerationError("The local model did not produce distinct topics. Try again with a different category.")

        created: List[TopicRecord] = []
        now = _now()
        with self._connection() as connection:
            for draft in drafts:
                topic_id = str(uuid.uuid4())
                try:
                    connection.execute(
                        """INSERT INTO topics
                           (id,title,title_key,description,category,opening_hook,target_audience,estimated_duration_seconds,
                            thumbnail_concept,script_potential,originality_score,is_favorite,is_used,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,?)""",
                        (
                            topic_id,
                            draft.title,
                            _title_key(draft.title),
                            draft.description,
                            draft.category,
                            draft.opening_hook,
                            draft.target_audience,
                            draft.estimated_duration_seconds,
                            draft.thumbnail_concept,
                            draft.script_potential,
                            draft.originality_score,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError:
                    duplicate_count += 1
                    continue
                row = connection.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
                created.append(self._topic_record(row))

        note = "Model-generated suggestions can contain factual errors; verify claims before publishing. Distinctness is a rough idea estimate, not a view prediction. Duplicate titles are filtered against topic history."
        if validation_rejected_count:
            note += " {} suggestions were discarded because their fields did not meet the output contract.".format(validation_rejected_count)
        if len(created) < requested:
            note += " The compact model returned {} of {} requested unique topics; regenerate to fill the rest.".format(len(created), requested)
        return TopicBatchResult(
            topics=created,
            requested_count=requested,
            created_count=len(created),
            duplicate_count=duplicate_count,
            validation_rejected_count=validation_rejected_count,
            note=note,
        )

    @staticmethod
    def _topic_record(row: sqlite3.Row) -> TopicRecord:
        return TopicRecord(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            category=row["category"],
            opening_hook=row["opening_hook"],
            target_audience=row["target_audience"],
            estimated_duration_seconds=row["estimated_duration_seconds"],
            thumbnail_concept=row["thumbnail_concept"],
            script_potential=row["script_potential"],
            originality_score=row["originality_score"],
            is_favorite=bool(row["is_favorite"]),
            is_used=bool(row["is_used"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_topics(
        self,
        query: str = "",
        category: Optional[str] = None,
        favorites_only: bool = False,
        used: Optional[bool] = None,
        sort: str = "recent",
    ) -> List[TopicRecord]:
        if sort not in ("recent", "originality", "title"):
            raise GenerationError("Sort must be recent, originality, or title.", 422)
        clauses: List[str] = []
        values: List[Any] = []
        if query.strip():
            like = "%{}%".format(query.strip()[:100])
            clauses.append("(title LIKE ? OR description LIKE ? OR category LIKE ? OR opening_hook LIKE ?)")
            values.extend([like, like, like, like])
        if category:
            clauses.append("category=?")
            values.append(category)
        if favorites_only:
            clauses.append("is_favorite=1")
        if used is not None:
            clauses.append("is_used=?")
            values.append(1 if used else 0)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        ordering = {"recent": "created_at DESC", "originality": "originality_score DESC, created_at DESC", "title": "title COLLATE NOCASE ASC"}[sort]
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM topics" + where + " ORDER BY " + ordering + " LIMIT 500", values).fetchall()
        return [self._topic_record(row) for row in rows]

    def update_topic(self, topic_id: str, is_favorite: Optional[bool], is_used: Optional[bool]) -> TopicRecord:
        fields, values = [], []
        if is_favorite is not None:
            fields.append("is_favorite=?")
            values.append(1 if is_favorite else 0)
        if is_used is not None:
            fields.append("is_used=?")
            values.append(1 if is_used else 0)
        if not fields:
            raise GenerationError("No topic fields were supplied to update.", 422)
        with self._connection() as connection:
            cursor = connection.execute("UPDATE topics SET {} WHERE id=?".format(",".join(fields)), values + [topic_id])
            if cursor.rowcount != 1:
                raise GenerationError("Topic was not found in local history.", 404)
            row = connection.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        return self._topic_record(row)

    def regenerate_topic(self, topic_id: str) -> TopicRecord:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
            existing_titles = [record[0] for record in connection.execute("SELECT title FROM topics ORDER BY created_at DESC LIMIT 300")]
        if row is None:
            raise GenerationError("Topic was not found in local history.", 404)
        prompt = (
            "Write one original video topic in category {!r}. Avoid all these titles: {}. "
            "Return JSON with a topics array containing exactly one object with title, description, category, opening_hook, target_audience, estimated_duration_seconds, thumbnail_concept, script_potential, originality_score. "
            "The score is a rough originality estimate, not a view prediction."
        ).format(row["category"], json.dumps(existing_titles[-100:]))
        item_schema = TopicDraft.model_json_schema()
        item_schema["properties"]["category"]["enum"] = [row["category"]]
        response_schema = {
            "type": "object",
            "properties": {"topics": {"type": "array", "items": item_schema, "minItems": 1, "maxItems": 1}},
            "required": ["topics"],
            "additionalProperties": False,
        }
        parsed = self._generate_json("You create fresh, specific video ideas. Return JSON only.", prompt, 500, 0.9, response_schema)
        values = parsed.get("topics", [])
        if not isinstance(values, list) or not values:
            raise GenerationError("The local model returned no replacement idea.")
        try:
            candidate = dict(values[0])
            candidate["category"] = row["category"]
            draft = TopicDraft.model_validate(candidate)
        except ValidationError as error:
            raise GenerationError("The replacement topic failed structured validation.") from error
        if any(_near_duplicate(draft.title, title) for title in existing_titles):
            raise GenerationError("The replacement topic duplicates topic history. Try regeneration again.")
        topic_id_new, now = str(uuid.uuid4()), _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO topics
                   (id,title,title_key,description,category,opening_hook,target_audience,estimated_duration_seconds,
                    thumbnail_concept,script_potential,originality_score,is_favorite,is_used,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,?)""",
                (topic_id_new, draft.title, _title_key(draft.title), draft.description, draft.category, draft.opening_hook,
                 draft.target_audience, draft.estimated_duration_seconds, draft.thumbnail_concept, draft.script_potential,
                 draft.originality_score, now),
            )
            saved = connection.execute("SELECT * FROM topics WHERE id=?", (topic_id_new,)).fetchone()
        return self._topic_record(saved)

    def _short_script(self, topic):
        fields = {"title": (5, 120), "hook": (45, 150), "example": (235, 390), "closing": (60, 165),
                  "example_visual": (8, 180), "closing_visual": (8, 180)}
        schema = {
            "type": "object",
            "properties": {name: {"type": "string", "minLength": low, "maxLength": high}
                           for name, (low, high) in fields.items()},
            "required": list(fields), "additionalProperties": False,
        }
        prompt = (
            "Write a short English video script. The spoken text of hook, example and closing together must be about "
            "85 words. Use a one-sentence hook, a concrete example of three or four complete sentences, and a closing "
            "that gives the viewer one action to try. Tell one complete story without repeating text. "
            "Write short visual descriptions for the example and closing in their separate fields.\n"
            "Topic: {}\nDescription: {}\nOpening idea: {}\nAudience: {}"
        ).format(topic["title"], topic["description"], topic["opening_hook"], topic["target_audience"])
        for _ in range(2):
            parsed = self._generate_json("You write concise conversational video scripts in JSON.", prompt, 550, 0.55, schema)
            if not all(isinstance(parsed.get(name), str) and low <= len(parsed[name].strip()) <= high
                       for name, (low, high) in fields.items()):
                continue
            spoken = []
            for key in ("hook", "example", "closing"):
                text = parsed[key].strip()
                if not text.endswith((".", "?", "!")):
                    end = max(text.rfind(mark) for mark in (".", "?", "!"))
                    if end >= 0 and len(text) >= fields[key][1] - 8:
                        text = text[:end + 1]
                    else:
                        text += "."
                spoken.append(text)
            sentences = [sentence for text in spoken for sentence in re.split(r"(?<=[.!?])\s+", text)]
            distinct = {_title_key(sentence) for sentence in sentences if _title_key(sentence)}
            words = len(re.findall(r"\b[\w’'-]+\b", " ".join(spoken)))
            if not 48 <= words <= 110 or len(distinct) != len(sentences):
                continue
            return parsed["title"].strip(), [
                ScriptParagraphDraft(section="opening_hook", text=spoken[0],
                                     visual_suggestion="Illustrated presenter facing the camera",
                                     on_screen_text=parsed["title"][:120], voice_direction="Friendly, clear delivery"),
                ScriptParagraphDraft(section="example", text=spoken[1],
                                     visual_suggestion=parsed["example_visual"],
                                     on_screen_text=parsed["title"][:120], voice_direction="Conversational delivery"),
                ScriptParagraphDraft(section="conclusion", text=spoken[2],
                                     visual_suggestion=parsed["closing_visual"],
                                     on_screen_text=parsed["title"][:120], voice_direction="Calm, clear finish"),
            ]
        raise GenerationError("The local model did not write a usable short script. Resume to try again.")

    def generate_script(self, topic_id: str, duration_seconds: int, *, short_form: bool = False) -> ScriptDocument:
        with self._connection() as connection:
            topic = connection.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        if topic is None:
            raise GenerationError("Topic was not found in local history.", 404)
        minutes = duration_seconds / 60
        low, high = round(minutes * 115), round(minutes * 165)
        prompt = (
            "Write a natural conversational English narration for a realistic human presenter. Target {} seconds and roughly {}–{} words.\n"
            "Topic: {}\nDescription: {}\nOpening idea: {}\nAudience: {}\n"
            "Return one JSON object with title and paragraphs. Use 6–14 concise paragraph objects with section, text, visual_suggestion, on_screen_text, voice_direction. "
            "Start with opening_hook, include introduction, main_content, at least one example, development, and conclusion; call_to_action is optional. "
            "Make the paragraphs form a complete script and avoid headings in spoken text."
        ).format(duration_seconds, low, high, topic["title"], topic["description"], topic["opening_hook"], topic["target_audience"])
        token_budget = 900 if duration_seconds <= 60 else 1400 if duration_seconds <= 180 else 2400 if duration_seconds <= 300 else 3600
        if short_form:
            title, draft_paragraphs = self._short_script(topic)
        else:
            parsed = self._generate_json(
                "You are an experienced English-language video scriptwriter. Output valid JSON only.",
                prompt,
                token_budget,
                0.75,
                _script_response_schema(),
            )
            try:
                draft = ScriptDraft.model_validate(_normalize_script_fields(parsed))
            except ValidationError as error:
                raise GenerationError("The local model's script fields failed validation: {}. Regenerate the script.".format(_validation_summary(error))) from error
            title, draft_paragraphs = draft.title, draft.paragraphs

        text_parts = [paragraph.text.strip() for paragraph in draft_paragraphs]
        full_narration = "\n\n".join(text_parts)
        word_count = len(re.findall(r"\b[\w’'-]+\b", full_narration))
        target_low, target_high = round(minutes * 115), round(minutes * 165)
        warnings = []
        if word_count < target_low:
            warnings.append("Narration may be shorter than the requested duration at a conversational speaking pace.")
        elif word_count > target_high:
            warnings.append("Narration may be longer than the requested duration at a conversational speaking pace.")
        if not short_form and len(draft_paragraphs) < 6:
            warnings.append("The script has few scene divisions; review pacing before production.")
        estimated_seconds = round(word_count / 145 * 60)
        quality = ScriptQuality(
            status="review" if warnings else "pass",
            word_count=word_count,
            target_word_range="{}–{} words".format(target_low, target_high),
            estimated_spoken_duration_seconds=estimated_seconds,
            warnings=warnings,
        )
        weights = [max(1, len(re.findall(r"\b[\w’'-]+\b", text))) for text in text_parts]
        total_weight = sum(weights)
        cursor = 0.0
        paragraphs: List[ScriptParagraph] = []
        for index, (source, text, weight) in enumerate(zip(draft_paragraphs, text_parts, weights)):
            segment = duration_seconds - cursor if index == len(weights) - 1 else duration_seconds * weight / total_weight
            paragraphs.append(ScriptParagraph(
                paragraph_id=index,
                section=source.section,
                text=text,
                start_seconds=round(cursor, 2),
                duration_seconds=round(segment, 2),
                visual_suggestion=source.visual_suggestion,
                on_screen_text=source.on_screen_text,
                voice_direction=source.voice_direction,
            ))
            cursor += segment

        script_id, created_at = str(uuid.uuid4()), _now()
        document = ScriptDocument(
            id=script_id,
            topic_id=topic_id,
            title=title,
            requested_duration_seconds=duration_seconds,
            full_narration=full_narration,
            paragraphs=paragraphs,
            quality=quality,
            created_at=datetime.fromisoformat(created_at),
        )
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO scripts(id,topic_id,duration_seconds,document_json,created_at) VALUES(?,?,?,?,?)",
                (script_id, topic_id, duration_seconds, document.model_dump_json(), created_at),
            )
        return document

    @staticmethod
    def _script_from_json(value: str) -> ScriptDocument:
        try:
            return ScriptDocument.model_validate_json(value)
        except ValidationError as error:
            raise GenerationError("Stored script data failed validation.", 500) from error

    def get_script(self, script_id: str) -> ScriptDocument:
        with self._connection() as connection:
            row = connection.execute("SELECT document_json FROM scripts WHERE id=?", (script_id,)).fetchone()
        if row is None:
            raise GenerationError("Script was not found.", 404)
        return self._script_from_json(row["document_json"])

    def latest_script_for_topic(self, topic_id: str) -> Optional[ScriptDocument]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM scripts WHERE topic_id=? ORDER BY created_at DESC LIMIT 1", (topic_id,)
            ).fetchone()
        if row is None:
            return None
        return self._script_from_json(row["document_json"])

    def edit_script(self, script_id: str, edit: ScriptEdit) -> ScriptDocument:
        script = self.get_script(script_id)
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", edit.full_narration) if chunk.strip()]
        if not chunks:
            chunks = [edit.full_narration.strip()]
        weights = [max(1, len(re.findall(r"\b[\w’'-]+\b", chunk))) for chunk in chunks]
        total_weight = sum(weights)
        cursor = 0.0
        paragraphs = []
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            old = script.paragraphs[min(index, len(script.paragraphs) - 1)]
            segment = script.requested_duration_seconds - cursor if index == len(chunks) - 1 else script.requested_duration_seconds * weight / total_weight
            paragraphs.append(old.model_copy(update={
                "paragraph_id": index,
                "text": chunk,
                "start_seconds": round(cursor, 2),
                "duration_seconds": round(segment, 2),
            }))
            cursor += segment
        word_count = len(re.findall(r"\b[\w’'-]+\b", edit.full_narration))
        target_low = round(script.requested_duration_seconds / 60 * 115)
        target_high = round(script.requested_duration_seconds / 60 * 165)
        warnings = []
        if word_count < target_low:
            warnings.append("Narration may be shorter than the requested duration at a conversational speaking pace.")
        if word_count > target_high:
            warnings.append("Narration may be longer than the requested duration at a conversational speaking pace.")
        updated = script.model_copy(update={
            "full_narration": edit.full_narration.strip(),
            "paragraphs": paragraphs,
            "quality": ScriptQuality(
                status="review" if warnings else "pass",
                word_count=word_count,
                target_word_range="{}–{} words".format(target_low, target_high),
                estimated_spoken_duration_seconds=round(word_count / 145 * 60),
                warnings=warnings,
            ),
        })
        with self._connection() as connection:
            connection.execute("UPDATE scripts SET document_json=? WHERE id=?", (updated.model_dump_json(), script_id))
            connection.execute("DELETE FROM director_plans WHERE script_id=?", (script_id,))
        return updated

    def generate_director(self, script_id: str) -> DirectorDocument:
        script = self.get_script(script_id)
        paragraph_lines = ["{}. [{}] {}".format(row.paragraph_id, row.section, row.text) for row in script.paragraphs]
        prompt = (
            "Create a coherent visual plan for this narration. Choose 3 to 8 scenes. Each scene must group consecutive paragraph_indexes, "
            "and the flattened indexes must cover every paragraph exactly once from 0 through {} in order. "
            "Use all required keys for each scene: scene_type, paragraph_indexes, presenter_present, camera_angle, camera_movement, lighting, background, visual_description, generation_prompt, transition, audio_requirements. "
            "Keep a consistent presenter and visual style. Do not rewrite narration; paragraph indexes select the exact narration.\n"
            "Paragraphs:\n{}\nReturn JSON with visual_style and scenes."
        ).format(len(script.paragraphs) - 1, "\n".join(paragraph_lines))
        parsed = self._generate_json(
            "You are a video director who creates practical, internally consistent scene plans. Return valid JSON only.",
            prompt,
            2200,
            0.65,
            _director_response_schema(),
        )
        try:
            draft = DirectorDraft.model_validate(_normalize_director_fields(parsed))
        except ValidationError as error:
            raise GenerationError("The director plan fields failed validation: {}. Generate it again.".format(_validation_summary(error))) from error
        flattened = [index for scene in draft.scenes for index in scene.paragraph_indexes]
        expected = list(range(len(script.paragraphs)))
        if flattened != expected:
            draft = _assign_director_paragraphs(draft, len(script.paragraphs))
            flattened = [index for scene in draft.scenes for index in scene.paragraph_indexes]
            if flattened != expected:
                raise GenerationError("The director plan did not cover narration paragraphs exactly once and in order. Generate it again.")

        scene_rows: List[DirectorScene] = []
        for position, source in enumerate(draft.scenes, start=1):
            selected = [script.paragraphs[index] for index in source.paragraph_indexes]
            start = selected[0].start_seconds
            end = selected[-1].start_seconds + selected[-1].duration_seconds
            narration = "\n\n".join(item.text for item in selected)
            scene_rows.append(DirectorScene(
                scene_id="scene-{:02d}".format(position),
                start_seconds=round(start, 2),
                duration_seconds=round(end - start, 2),
                scene_type=source.scene_type,
                narration=narration,
                presenter_present=source.presenter_present,
                camera_angle=source.camera_angle,
                camera_movement=source.camera_movement,
                lighting=source.lighting,
                background=source.background,
                visual_description=source.visual_description,
                generation_prompt=source.generation_prompt,
                transition=source.transition,
                subtitles=narration,
                audio_requirements=source.audio_requirements,
            ))

        plan_id, created_at = str(uuid.uuid4()), _now()
        plan = DirectorDocument(
            id=plan_id,
            script_id=script_id,
            visual_style=draft.visual_style,
            total_duration_seconds=script.requested_duration_seconds,
            scenes=scene_rows,
            created_at=datetime.fromisoformat(created_at),
        )
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO director_plans(id,script_id,timeline_json,created_at) VALUES(?,?,?,?)",
                (plan_id, script_id, plan.model_dump_json(), created_at),
            )
        return plan

    def get_director(self, script_id: str) -> Optional[DirectorDocument]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT timeline_json FROM director_plans WHERE script_id=? ORDER BY created_at DESC LIMIT 1", (script_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return DirectorDocument.model_validate_json(row["timeline_json"])
        except ValidationError as error:
            raise GenerationError("Stored director data failed validation.", 500) from error

