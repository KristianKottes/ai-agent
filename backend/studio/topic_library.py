"""Saved Morning Inspiration drafts and resumable background topic batches."""

import json
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.ai.mamay import MamaySettings, load_settings
from backend.scenes import illustrated_video as pipeline
from backend.studio import illustrated
from backend.studio.audio import StudioError
from backend.studio.morning_defaults import GenerationDefaults, load_defaults


class BatchRequest(illustrated.TopicRequest):
    writing_settings: MamaySettings
    defaults: GenerationDefaults


class TopicEdit(illustrated.MorningTopic):
    title: str = Field(min_length=4, max_length=100)
    revision: int = Field(ge=0)
    script: pipeline.Script

    # Scene and title limits are checked again when this is saved.


class TopicFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    used: Optional[bool] = None
    deleted: Optional[bool] = None


def now():
    return datetime.now(timezone.utc).isoformat()


def record(row):
    value = dict(row)
    value.update(json.loads(value.pop("metadata_json")))
    script = value.pop("script_json")
    value["script"] = None
    if script:
        opening = pipeline.separate_bible_opening(pipeline.Script.model_validate_json(script), value["passage"])
        value["script"] = opening.model_dump()
        value["passage"] = opening.bible_reference or value["passage"]
    value["used"] = bool(value["used"])
    return value


def list_topics(studio, project):
    studio.project(project)
    with studio.connect() as db:
        return [record(row) for row in db.execute(
            "SELECT * FROM studio_morning_topics WHERE project_id=? AND deleted_at IS NULL ORDER BY created_at DESC, ordinal", (project,))]


def get_topic(studio, project, id, *, deleted=False):
    studio.project(project)
    with studio.connect() as db:
        row = db.execute("SELECT * FROM studio_morning_topics WHERE project_id=? AND id=?", (project, id)).fetchone()
    if row is None or (row["deleted_at"] and not deleted):
        raise StudioError("This saved topic was not found in this project.", 404)
    return record(row)


def edit_topic(studio, project, id, payload):
    current = get_topic(studio, project, id)
    if current["status"] != "ready":
        raise StudioError("Wait until this topic's text is ready before editing it.", 409)
    script = payload.script.model_copy(deep=True)
    if not 2 <= len(script.scenes) <= 15:
        raise StudioError("Keep between 2 and 15 scenes in the saved text.")
    old_title = current["script"]["title"]
    script.title = payload.title
    if script.bible_text is not None:
        script.bible_reference = payload.passage
    # Replacing a title must also replace the spoken opening, without repeating it.
    opening = script.scenes[0]
    if old_title != script.title:
        text = re.sub(r"^" + re.escape(old_title) + r"(?=$|[\s.,!?…:;—–])[\s.,!?…:;—–]*", "", opening.narration, count=1, flags=re.IGNORECASE)
        if text != opening.narration:
            opening.narration = text
    try:
        script = pipeline.with_spoken_title(script)
        script = pipeline.Script.model_validate(script.model_dump())
    except ValidationError as error:
        raise StudioError("Keep each narration scene between 25 and 8000 characters and use a title of 4–100 characters.") from error
    if any(not pipeline.matches_language(scene.narration, current["language"]) for scene in script.scenes):
        raise StudioError("Keep narration in the topic's selected language.")
    metadata = payload.model_dump(exclude={"revision", "script"})
    with studio.connect() as db:
        result = db.execute("""UPDATE studio_morning_topics SET metadata_json=?, script_json=?, revision=revision+1,
            updated_at=? WHERE project_id=? AND id=? AND revision=? AND deleted_at IS NULL AND status='ready'""",
            (json.dumps(metadata, ensure_ascii=False), script.model_dump_json(), now(), project, id, payload.revision))
        if not result.rowcount:
            raise StudioError("This topic changed in another window. Reload before saving.", 409)
    return get_topic(studio, project, id)


def set_flags(studio, project, id, payload):
    current = get_topic(studio, project, id, deleted=True)
    with studio.connect() as db:
        result = db.execute("""UPDATE studio_morning_topics SET used=?, deleted_at=?, revision=revision+1, updated_at=?
            WHERE project_id=? AND id=? AND revision=?""",
            (int(current["used"] if payload.used is None else payload.used),
             current["deleted_at"] if payload.deleted is None else now() if payload.deleted else None,
             now(), project, id, payload.revision))
        if not result.rowcount:
            raise StudioError("This topic changed in another window. Reload before saving.", 409)
    if payload.deleted is False:
        resume_pending_batch(studio, current["batch_id"])
    return get_topic(studio, project, id, deleted=True)


def resume_pending_batch(studio, job):
    # Undo can restore a pending topic after its batch has skipped the deleted row.
    with studio.condition, studio.connect() as db:
        pending = db.execute("""SELECT 1 FROM studio_morning_topics
            WHERE batch_id=? AND status='pending' AND deleted_at IS NULL LIMIT 1""", (job,)).fetchone()
        if pending:
            changed = db.execute("""UPDATE studio_jobs SET state='queued', progress=0, message='Finishing restored topic text.'
                WHERE id=? AND kind=? AND state='completed'""", (job, illustrated.TOPIC_JOB))
            if changed.rowcount:
                studio.condition.notify_all()


def mark_used(studio, project, id):
    if id is None:
        return
    with studio.connect() as db:
        db.execute("""UPDATE studio_morning_topics SET used=1, revision=revision+1, updated_at=?
            WHERE project_id=? AND id=? AND used=0 AND deleted_at IS NULL""", (now(), project, str(id)))


def snapshot_topic(studio, project, request):
    if request.topic_id is None:
        return request
    topic = get_topic(studio, project, str(request.topic_id))
    if topic["status"] != "ready" or not topic["script"]:
        raise StudioError("This topic's text is not ready yet.", 409)
    if request.topic_revision != topic["revision"]:
        raise StudioError("The selected topic changed. Reload and review its saved text before generating.", 409)
    if request.language != topic["language"]:
        raise StudioError("Choose a topic in the selected video language.")
    script = pipeline.Script.model_validate(topic["script"])
    if request.scenes is not None and request.scenes != len(script.scenes):
        script = arrange_scenes(script, request.scenes)
    # Reviewed text is authoritative; length correction and editing ran during topic creation.
    return illustrated.IllustratedRequest.model_validate({**request.model_dump(),
        "topic": topic["title"], "passage": topic["passage"], "angle": topic["angle"],
        "script": script.model_dump(), "scenes": len(script.scenes), "seconds": None,
        "text_range": None, "text_words": None})


def arrange_scenes(script, target):
    """Change visual pacing while preserving words and keeping Bible/poem blocks intact."""
    scenes = [scene.model_copy(deep=True) for scene in script.scenes]
    while len(scenes) > target:
        candidates = [i for i in range(1, len(scenes) - 1)
                      if scenes[i].section == scenes[i + 1].section == "paragraph"
                      and len(scenes[i].narration) + len(scenes[i + 1].narration) + 2 <= 8000]
        if not candidates:
            raise StudioError("Use more scenes to keep complete Bible and poem blocks together.")
        index = min(candidates, key=lambda i: len(scenes[i].narration) + len(scenes[i + 1].narration))
        scenes[index].narration += "\n\n" + scenes.pop(index + 1).narration
    while len(scenes) < target:
        choices = []
        for index, scene in enumerate(scenes[1:], 1):
            if scene.section != "paragraph":
                continue
            positions = [match for match in re.finditer(r"\s+", scene.narration)
                         if len(scene.narration[:match.start()].strip()) >= 25
                         and len(scene.narration[match.end():].strip()) >= 25
                         and len(scene.narration[:match.start()].split()) >= 2
                         and len(scene.narration[match.end():].split()) >= 2]
            if positions:
                match = min(positions, key=lambda item: abs(item.start() - len(scene.narration) / 2))
                choices.append((len(scene.narration), index, match))
        if not choices:
            raise StudioError("This saved text is too short for that many scenes. Choose fewer scenes or Auto.")
        _, index, match = max(choices, key=lambda item: item[0])
        before, after = scenes[index].narration[:match.start()], scenes[index].narration[match.end():]
        caption = scenes[index].caption
        for count in range(8, 1, -1):
            candidate = " ".join(after.split()[:count])
            if 4 <= len(candidate) <= 86:
                caption = candidate
                break
        second = pipeline.Scene.model_validate({**scenes[index].model_dump(), "narration": after, "caption": caption})
        scenes[index].narration = before
        scenes.insert(index + 1, second)
    return pipeline.Script.model_validate({**script.model_dump(), "scenes": [scene.model_dump() for scene in scenes]})


def prepare_batch(studio, project, request):
    illustrated.require_host()
    model = pipeline.writing_dir(studio.data) / pipeline.WRITING_MODEL.name
    if not model.is_file() or model.stat().st_size != pipeline.WRITING_MODEL.size:
        raise StudioError("Install MamayLM to generate topics and text.", 409)
    return BatchRequest(language=request.language, writing_settings=load_settings(studio.data, project),
                        defaults=load_defaults(studio.data, project))


def generate_batch(studio, project, job, request):
    studio.update(job, "running", .01, "Discovering five topics and preparing saved text.")
    with studio.connect() as db:
        existing = db.execute("SELECT 1 FROM studio_morning_topics WHERE batch_id=? LIMIT 1", (job,)).fetchone()
    if not existing:
        batch = illustrated.topics(studio, project, request)
        studio.check()
        created = now()
        with studio.connect() as db:
            for index, topic in enumerate(batch.topics):
                db.execute("""INSERT INTO studio_morning_topics
                    (id, project_id, batch_id, ordinal, language, metadata_json, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                    (str(uuid4()), project, job, index, request.language, topic.model_dump_json(), created, created))
    with studio.connect() as db:
        rows = db.execute("SELECT * FROM studio_morning_topics WHERE batch_id=? ORDER BY ordinal", (job,)).fetchall()
    failures = 0
    for index, row in enumerate(rows):
        studio.check()
        if row["status"] == "ready" or row["deleted_at"]:
            continue
        topic = record(row)
        defaults = request.defaults
        target = defaults.text_range.target if defaults.text_range else None
        count = defaults.scenes or (max(2, min(15, round(target / 40))) if target else
                                   8 if defaults.seconds is None else max(2, round(defaults.seconds / 8)))
        report = lambda message, i=index: studio.update(job, "running", .05 + .9 * i / len(rows),
                                    f"Topic {i + 1} of {len(rows)} · {message}")
        try:
            script = pipeline.write_script(" — ".join((topic["title"], topic["passage"], topic["angle"])),
                defaults.seconds, count, pipeline.writing_dir(studio.data) / pipeline.WRITING_MODEL.name,
                1024 + index, language=request.language, morning=True, style=defaults.style,
                check=studio.check, writing_settings=request.writing_settings, report=report,
                text_range=defaults.text_range)
            studio.check()
            script = pipeline.separate_bible_opening(script, script.bible_reference or topic["passage"])
            metadata = {key: topic[key] for key in ("title", "passage", "angle")}
            metadata["title"] = script.title
            metadata["passage"] = script.bible_reference or topic["passage"]
            with studio.connect() as db:
                db.execute("""UPDATE studio_morning_topics SET script_json=?, metadata_json=?, status='ready', error='',
                    revision=revision+1, updated_at=? WHERE id=? AND project_id=? AND deleted_at IS NULL""",
                    (script.model_dump_json(), json.dumps(metadata, ensure_ascii=False), now(), row["id"], project))
        except (RuntimeError, ValueError, OSError) as error:
            failures += 1
            with studio.connect() as db:
                db.execute("UPDATE studio_morning_topics SET status='failed', error=?, updated_at=? WHERE id=? AND deleted_at IS NULL",
                           (str(error), now(), row["id"]))
    if failures:
        raise StudioError(f"{failures} topic texts could not be completed. Ready drafts are saved. Retry to finish the remaining texts.", 502)
