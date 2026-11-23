"""Persisted defaults, explicit job overrides, and inclusive narration ranges."""

from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from backend.ai.mamay import MamaySettings
from backend.scenes import illustrated_video as pipeline
from backend.studio import illustrated
from backend.studio.morning_defaults import GenerationDefaults, save_defaults
from backend.studio.service import ProjectStudio
from test_studio import client, new_project, studio_env


def test_defaults_persist_validate_and_reject_stale_saves(studio_env):
    path = "/api/studio/illustrated/defaults"
    with client() as api:
        defaults = api.get(path).json()
        assert defaults["seconds"] is None and defaults["text_range"] is None
        changed = dict(defaults, text_range={"min_words": 300, "max_words": 400},
                       style="biblical", resolution="1080p", captions="clean", scenes=8)
        saved = api.put(path, json=changed)
        assert saved.status_code == 200, saved.text
        assert saved.json()["revision"] == 1
        assert api.put(path, json=changed).status_code == 409
        for invalid in ({"min_words": 400, "max_words": 300}, {"min_words": 49, "max_words": 400},
                        {"min_words": 300, "max_words": 1001}, {"min_words": 300.5, "max_words": 400}):
            assert api.put(path, json=dict(saved.json(), text_range=invalid)).status_code == 422
        assert api.put(path, json=dict(saved.json(), seconds=60)).status_code == 422
    with client() as api:
        assert api.get(path).json() == saved.json()


def test_explicit_settings_override_defaults_including_auto_and_language(tmp_path):
    save_defaults(tmp_path, GenerationDefaults(language="en", seconds=60, scenes=8,
                  narrator={"model_id": "kokoro", "voice": "af_heart"}, style="biblical"))
    apply = lambda **kw: illustrated.apply_defaults(tmp_path, illustrated.IllustratedRequest(topic="Hope", **kw))
    assert apply().seconds == 60 and apply().style == "biblical"
    automatic = apply(seconds=None, scenes=None, narrator=None, style="ink")
    assert automatic.seconds is None and automatic.scenes is None and automatic.narrator is None
    assert automatic.style == "ink"
    assert apply(language="uk").narrator is None
    ranged = apply(text_range={"min_words": 300, "max_words": 400})
    assert ranged.text_range.target == 350 and ranged.seconds is None
    assert apply(text_words=250).seconds is None


def test_range_defaults_can_be_overridden_by_legacy_jobs_or_auto(tmp_path):
    save_defaults(tmp_path, GenerationDefaults(text_range={"min_words": 300, "max_words": 400}))
    for values in ({"text_range": None}, {"text_words": 250}, {"seconds": 60}):
        request = illustrated.apply_defaults(tmp_path, illustrated.IllustratedRequest(topic="Hope", **values))
        assert request.text_range is None
    old = illustrated.IllustratedRequest.model_validate_json('{"topic":"Hope","text_words":500}')
    assert old.text_words == 500 and old.text_range is None
    with pytest.raises(ValidationError):
        illustrated.IllustratedRequest(topic="Hope", text_words=500, text_range={"min_words": 300, "max_words": 400})


def test_queue_snapshots_defaults_and_preserves_explicit_overrides(studio_env, monkeypatch):
    monkeypatch.setattr(illustrated, "validate", lambda *_: None)
    monkeypatch.setattr(ProjectStudio, "_run_queue", lambda self: self.stop.wait(timeout=10))
    path = "/api/studio/illustrated/defaults"
    with client() as api:
        defaults = api.get(path).json()
        first_defaults = api.put(path, json=dict(defaults, text_range={"min_words": 300, "max_words": 400}, style="biblical"))
        assert first_defaults.status_code == 200
        project = new_project(api)
        assert api.post(project + "/illustrated/render", json={"topic": "Перша тема"}).status_code == 202
        assert api.put(path, json=dict(first_defaults.json(), text_range={"min_words": 600, "max_words": 700}, style="ink")).status_code == 200
        assert api.post(project + "/illustrated/render", json={"topic": "Друга тема", "text_range": None, "seconds": None}).status_code == 202
        from backend.api.main import database_connection
        with database_connection() as db:
            rows = db.execute("SELECT input_json FROM studio_jobs ORDER BY rowid").fetchall()
        first, second = [illustrated.IllustratedRequest.model_validate_json(row[0]) for row in rows]
        assert first.text_range.min_words == 300 and first.text_range.max_words == 400
        assert first.style == "biblical" and second.style == "ink"
        assert second.text_range is None and second.seconds is None


@pytest.mark.parametrize("language", ["uk", "en"])
@pytest.mark.parametrize("outside", [299, 401])
@pytest.mark.parametrize("editing", [False, True])
def test_strict_range_survives_drafting_and_editor_drift(monkeypatch, language, outside, editing):
    title = "Тиха надія" if language == "uk" else "Quiet hope"
    word = "надія" if language == "uk" else "hope"
    caption = title

    def script(count):
        return pipeline.Script(title=title, scenes=[pipeline.Scene(
            narration=title + ". " + " ".join([word] * (count - 2)), caption=caption,
            illustration="Golden sunlight falling on a quiet lake.")])

    responses = iter([script(350), script(outside), script(350)] if editing else [script(outside), script(350)])
    prompts = []

    @contextmanager
    def model(*_args):
        yield type("FakeModel", (), {"metadata": {}})()

    def response(_model, schema, prompt, **_options):
        assert "maxLength" not in schema["$defs"]["Scene"]["properties"]["narration"]
        prompts.append(prompt)
        return next(responses).model_dump()

    monkeypatch.setattr(pipeline, "writing_model", model)
    monkeypatch.setattr(pipeline, "script_response", response)
    result = pipeline.write_script("Morning hope", None, 1, None, 10, language=language,
                 text_range={"min_words": 300, "max_words": 400},
                 writing_settings=MamaySettings(editor_enabled=editing))
    assert pipeline.word_count(result) == 350
    assert len(prompts) == (3 if editing else 2)
    assert "300–400" in prompts[-1] and "Draft to revise:" in prompts[-1]


def test_range_never_silently_accepts_text_outside_its_bounds(monkeypatch):
    @contextmanager
    def model(*_args):
        yield type("FakeModel", (), {"metadata": {}})()

    script = pipeline.Script(title="Quiet hope", scenes=[pipeline.Scene(
        narration="Quiet hope. " + " ".join(["hope"] * 297), caption="Quiet hope",
        illustration="Golden sunlight falling on a quiet lake.")])
    monkeypatch.setattr(pipeline, "writing_model", model)
    monkeypatch.setattr(pipeline, "script_response", lambda *_args, **_options: script.model_dump())
    with pytest.raises(ValueError, match="300–400 are required"):
        pipeline.write_script("Hope", None, 1, None, 10, language="en",
             text_range={"min_words": 300, "max_words": 400}, writing_settings=MamaySettings(editor_enabled=False))
