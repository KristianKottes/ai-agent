"""MamayLM settings persistence, queued snapshots, and the configurable editing pass."""

from contextlib import contextmanager

import pytest

from backend.ai.mamay import MamaySettings
from backend.scenes import illustrated_video as pipeline
from backend.studio import illustrated
from backend.studio.service import ProjectStudio
from test_studio import client, new_project, studio_env


def test_mamay_settings_validate_persist_and_reject_stale_saves(studio_env):
    path = "/api/studio/illustrated/writing-settings"
    with client() as api:
        response = api.get(path)
        assert response.status_code == 200, response.text
        default = response.json()["settings"]
        assert default["editor_enabled"] and default["revision"] == 0
        assert "title, Bible text, Bible reference, main text" in default["structure_rules"]
        settings = dict(default, voice_rules="Write as a gentle, thoughtful friend.",
                        title_rules="Use varied titles, including short phrases and questions.",
                        structure_rules="Title, then Scripture with a reference, then a personal reflection.",
                        examples_uk="У Твоїх руках цей день. Дякую за нього.",
                        examples_en="I can begin this day with a little trust.")
        saved = api.put(path, json=settings)
        assert saved.status_code == 200 and saved.json()["revision"] == 1
        assert api.put(path, json=settings).status_code == 409
        assert api.put(path, json=dict(saved.json(), examples_uk="a" * 3001)).status_code == 422
    with client() as api:
        assert api.get(path).json()["settings"] == saved.json()


def test_mamay_queue_keeps_rules_from_the_time_of_submission(studio_env, monkeypatch):
    monkeypatch.setattr(illustrated, "validate", lambda *_: None)
    monkeypatch.setattr(ProjectStudio, "_run_queue", lambda self: self.stop.wait(timeout=10))
    with client() as api:
        project = new_project(api)
        path = project + "/illustrated/writing-settings"
        original = api.get(path).json()["settings"]
        first = api.post(project + "/illustrated/render", json={"topic": "Перша тема"})
        assert first.status_code == 202, first.text
        updated = api.put(path, json=dict(original, rhythm_rules="Use a varied, reflective rhythm.",
                                         title_rules="Use short, conversational titles."))
        assert updated.status_code == 200, updated.text
        second = api.post(project + "/illustrated/render", json={"topic": "Друга тема"})
        assert second.status_code == 202, second.text
        from backend.api.main import database_connection

        with database_connection() as db:
            rows = db.execute("SELECT input_json FROM studio_jobs ORDER BY rowid").fetchall()
        first_request, second_request = [illustrated.IllustratedRequest.model_validate_json(row[0]) for row in rows]
        assert first_request.writing_settings.revision == 0
        assert second_request.writing_settings.revision == 1
        assert first_request.writing_settings.rhythm_rules == original["rhythm_rules"]
        assert first_request.writing_settings.title_rules == original["title_rules"]
        assert second_request.writing_settings.title_rules == updated.json()["title_rules"]


def test_mamay_old_settings_gain_default_structure_without_changing_other_rules(tmp_path):
    from backend.ai.mamay import load_settings

    old = MamaySettings(voice_rules="My saved voice", editor_enabled=False).model_dump_json(
        exclude={"structure_rules", "title_rules"})
    (tmp_path / "mamaylm-settings.json").write_text(old, encoding="utf-8")
    settings = load_settings(tmp_path)
    assert settings.voice_rules == "My saved voice" and not settings.editor_enabled
    assert settings.structure_rules == MamaySettings().structure_rules
    assert settings.title_rules == MamaySettings().title_rules
    assert settings.structure_rules in settings.instructions("uk")
    assert settings.structure_rules in settings.instructions("en")
    assert settings.structure_rules not in settings.instructions("uk", topics=True)
    assert settings.title_rules in settings.instructions("uk", topics=True)
    assert settings.title_rules in settings.instructions("en", topics=True)


@pytest.mark.parametrize("language", ["uk", "en"])
@pytest.mark.parametrize("editing", [True, False])
def test_mamay_uses_selected_rules_and_language_examples_then_edits(monkeypatch, language, editing):
    draft_text = ("Дякую за цей новий день. Довіряю його Тобі." if language == "uk"
                  else "Thank you for this new day. I trust it to You.")
    edited_text = ("Цей день ще попереду. Допоможи мені пройти його з Тобою." if language == "uk"
                   else "This day is still ahead of me. Help me walk through it with You.")
    caption = "Цей день" if language == "uk" else "This day"
    draft = pipeline.Script(title="Ранкова молитва" if language == "uk" else "Morning prayer", scenes=[
        pipeline.Scene(narration=draft_text, caption="Дякую за день" if language == "uk" else "A new day",
                       illustration="Sunlight falling on a field at dawn.")
    ])
    after = draft.model_copy(update={"title": "Changed title", "scenes": [
        pipeline.Scene(narration=edited_text, caption=caption, illustration="A different illustration.")
    ]})
    rules = MamaySettings(voice_rules="VOICE RULE", rhythm_rules="RHYTHM RULE", bible_rules="BIBLE RULE",
                          title_rules="TITLE RULE",
                          structure_rules="STRUCTURE RULE",
                          editor_rules="EDITOR RULE", examples_uk="UK EXAMPLE", examples_en="EN EXAMPLE",
                          editor_enabled=editing)
    prompts = []

    @contextmanager
    def model(*_args):
        yield type("FakeModel", (), {"metadata": {}})()

    def response(_model, _schema, prompt, **_options):
        prompts.append(prompt)
        return (draft if len(prompts) == 1 else after).model_dump()

    monkeypatch.setattr(pipeline, "writing_model", model)
    monkeypatch.setattr(pipeline, "script_response", response)
    result = pipeline.write_script("Morning trust · Psalm 23:1", None, 1, None, 1024,
                                   language=language, morning=True, writing_settings=rules)
    assert all(rule in prompts[0] for rule in ("VOICE RULE", "RHYTHM RULE", "TITLE RULE", "BIBLE RULE", "STRUCTURE RULE"))
    assert ("UK EXAMPLE" in prompts[0]) == (language == "uk")
    assert ("EN EXAMPLE" in prompts[0]) == (language == "en")
    assert len(prompts) == (2 if editing else 1)
    assert result.scenes[0].narration == draft.title + ".\n\n" + (edited_text if editing else draft_text)
    assert result.scenes[0].narration.count(draft.title) == 1
    assert result.title == draft.title and result.scenes[0].illustration == draft.scenes[0].illustration
    if editing:
        assert "EDITOR RULE" in prompts[1]
        assert "STRUCTURE RULE" in prompts[1]
        assert "TITLE RULE" in prompts[1]


@pytest.mark.parametrize("title, narration", [
    ("Ранкова довіра", "Ранкова довіра. Цей новий день у Твоїх руках."),
    ("Morning trust", "Morning trust. This new day is in Your hands."),
    ("Morning trust?", "Morning trust? This new day is in Your hands."),
])
def test_spoken_title_is_not_added_twice(title, narration):
    script = pipeline.Script(title=title, scenes=[pipeline.Scene(
        narration=narration, caption="Morning trust", illustration="Sunrise over quiet green hills.")])
    assert pipeline.with_spoken_title(pipeline.with_spoken_title(script)) == script


def test_spoken_title_does_not_confuse_a_word_prefix_with_the_title():
    script = pipeline.Script(title="Peace", scenes=[pipeline.Scene(
        narration="Peaceful mornings give us space to reflect.", caption="Peaceful mornings",
        illustration="Sunrise over quiet green hills.")])
    assert pipeline.with_spoken_title(script).scenes[0].narration == "Peace.\n\n" + script.scenes[0].narration


@pytest.mark.parametrize("language", ["uk", "en"])
@pytest.mark.parametrize("editing", [False, True])
def test_word_target_includes_title_and_survives_editing(monkeypatch, language, editing):
    title = "Ранкова тиха надія" if language == "uk" else "A quiet start"
    word = "надія" if language == "uk" else "hope"
    caption = "Тиха надія" if language == "uk" else "Quiet hope"
    script = pipeline.Script(title=title, scenes=[
        pipeline.Scene(narration=" ".join([word] * 23), caption=caption, illustration="Sunrise over quiet hills.", section="scripture"),
        pipeline.Scene(narration=" ".join([word] * 24), caption=caption, illustration="A still lake at dawn.")])

    @contextmanager
    def model(*_args):
        yield type("FakeModel", (), {"metadata": {}})()

    prompts = []

    def response(_model, _schema, prompt, **_options):
        assert "maxLength" not in _schema["$defs"]["Scene"]["properties"]["narration"]
        prompts.append(prompt)
        return script.model_dump()

    monkeypatch.setattr(pipeline, "writing_model", model)
    monkeypatch.setattr(pipeline, "script_response", response)
    result = pipeline.write_script("Morning hope", None, 2, None, 10, language=language, morning=True, text_words=50,
                                   writing_settings=MamaySettings(editor_enabled=editing))
    assert sum(len(scene.narration.split()) for scene in result.scenes) == 50
    assert result.scenes[0].section == "scripture"
    assert len(prompts) == (2 if editing else 1)


def test_requested_word_count_does_not_silently_return_a_short_draft(monkeypatch):
    @contextmanager
    def model(*_args):
        yield type("FakeModel", (), {"metadata": {}})()

    script = pipeline.Script(title="A quiet start", scenes=[pipeline.Scene(
        narration="This draft is much too short for the requested text length.", caption="A quiet start",
        illustration="Sunrise over quiet hills.")] * 2)
    monkeypatch.setattr(pipeline, "writing_model", model)
    monkeypatch.setattr(pipeline, "script_response", lambda *_args, **_kwargs: script.model_dump())
    with pytest.raises(ValueError, match="requested 200-word"):
        pipeline.write_script("Hope", None, 2, None, 10, language="en", text_words=200,
                              writing_settings=MamaySettings(editor_enabled=False))


@pytest.mark.parametrize("language", ["uk", "en"])
@pytest.mark.parametrize("target, initial_words", [(50, 12), (50, 100), (500, 300), (500, 800)])
def test_word_count_revision_uses_existing_draft_and_preserves_opening(monkeypatch, language, target, initial_words):
    word = "надія" if language == "uk" else "hope"
    title = "Надія поруч" if language == "uk" else "Hope is near"
    caption = "Тиха надія" if language == "uk" else "Quiet hope"
    original = pipeline.Script(title=title, scenes=[
        pipeline.Scene(narration=" ".join([word] * 8), caption=caption, section="scripture",
                       illustration="A small open Bible on a wooden table."),
        pipeline.Scene(narration=" ".join([word] * initial_words), caption=caption,
                       illustration="Golden sunlight on a quiet lake.")])
    opening = pipeline.with_spoken_title(original).scenes[0]
    corrected = original.model_copy(update={"title": "A different title", "scenes": [
        original.scenes[0].model_copy(update={"narration": " ".join([word] * 20)}),
        pipeline.Scene(narration=" ".join([word] * (target - len(opening.narration.split()))), caption=caption,
                       illustration="An unwanted different illustration.")]})
    prompts, messages = [], []

    @contextmanager
    def model(*_args):
        yield type("FakeModel", (), {"metadata": {}})()

    def response(_model, _schema, prompt, **_options):
        prompts.append(prompt)
        return (original if len(prompts) == 1 else corrected).model_dump()

    monkeypatch.setattr(pipeline, "writing_model", model)
    monkeypatch.setattr(pipeline, "script_response", response)
    result = pipeline.write_script("Morning hope", None, 2, None, 10, language=language, morning=True,
                                   text_words=target, report=messages.append,
                                   writing_settings=MamaySettings(editor_enabled=False))
    assert len(prompts) == 2 and "Draft to revise:" in prompts[1]
    assert ("expand" if pipeline.word_count(pipeline.with_spoken_title(original)) < target else "shorten") in prompts[1]
    assert pipeline.word_count(result) == target
    assert result.title == original.title and result.scenes[0] == opening
    assert result.scenes[1].illustration == original.scenes[1].illustration
    assert any(f"{target} words" in message for message in messages)


def test_editor_that_shortens_narration_is_corrected_before_video_generation(monkeypatch):
    original = pipeline.Script(title="A quiet start", scenes=[
        pipeline.Scene(narration=" ".join(["hope"] * 8), caption="Quiet hope", section="scripture",
                       illustration="An open Bible on a wooden table."),
        pipeline.Scene(narration=" ".join(["hope"] * 39), caption="Quiet hope",
                       illustration="Golden sunlight on a quiet lake.")])
    shortened = original.model_copy(update={"scenes": [original.scenes[0],
        original.scenes[1].model_copy(update={"narration": " ".join(["hope"] * 12)})]})
    responses = iter([original, shortened, original])
    messages = []

    @contextmanager
    def model(*_args):
        yield type("FakeModel", (), {"metadata": {}})()

    monkeypatch.setattr(pipeline, "writing_model", model)
    monkeypatch.setattr(pipeline, "script_response", lambda *_args, **_kwargs: next(responses).model_dump())
    result = pipeline.write_script("Morning hope", None, 2, None, 10, language="en", morning=True,
                                   text_words=50, report=messages.append, writing_settings=MamaySettings())
    assert pipeline.word_count(result) == 50
    assert any("Editing changed the length" in message for message in messages)
    assert result.scenes[1].illustration == original.scenes[1].illustration


def test_malformed_illustration_is_repaired_without_rewriting_narration(monkeypatch):
    scene = pipeline.Scene(narration="Love can begin with one small act of kindness.", caption="One small kindness",
                           illustration="A child'model\n```json\n{")
    script = pipeline.Script(title="One small kindness", scenes=[scene])
    description = "A child offering a flower to an elderly neighbor beside a sunny garden."
    monkeypatch.setattr(pipeline, "script_response", lambda *_args, **_kwargs: {"illustration": description})
    repaired = pipeline.repair_illustrations(None, script, 10, lambda: None, lambda _: None)
    assert repaired.scenes[0].illustration == description
    assert repaired.scenes[0].narration == scene.narration
    assert repaired.title == script.title and repaired.scenes[0].caption == scene.caption


def test_malformed_illustration_is_never_sent_to_image_generation(monkeypatch):
    script = pipeline.Script(title="A quiet start", scenes=[pipeline.Scene(
        narration="This morning can begin with a little kindness.", caption="A little kindness",
        illustration="A child'model\n```json\n{")])
    monkeypatch.setattr(pipeline, "script_response", lambda *_args, **_kwargs: {"illustration": "```json { child }"})
    with pytest.raises(ValueError, match="Could not repair the illustration"):
        pipeline.repair_illustrations(None, script, 10, lambda: None, lambda _: None)
