"""Title, Bible passage, and its following reference remain distinct through editing and speech."""

from contextlib import contextmanager

import pytest

from backend.ai.mamay import MamaySettings
from backend.scenes import illustrated_video as pipeline
from backend.studio import speech, topic_library
from test_morning_topic_library import draft, edit_input, generate, writing
from test_studio import client, new_project, studio_env


@pytest.mark.parametrize("language,title,text,reference", [
    ("uk", "Тиха надія", "Господь — мій Пастир, тому в недостатку не буду.", "Псалом 23:1"),
    ("en", "Quiet hope", "The Lord is my shepherd; I shall not want.", "Psalm 23:1"),
])
def test_structured_opening_has_three_separate_parts_in_the_right_order(language, title, text, reference):
    script = draft(title)
    script.bible_text, script.bible_reference = text, reference
    expected = title + ".\n\n" + text + "\n\n" + reference
    result = pipeline.with_spoken_title(script)
    assert result.scenes[0].narration == expected
    assert pipeline.with_spoken_title(result) == result
    assert result.scenes[0].narration.count(title) == result.scenes[0].narration.count(reference) == 1
    blocks = pipeline.scene_blocks(result.scenes[0], title, bible_text=text, bible_reference=reference)
    assert [block.kind for block in blocks] == ["title", "scripture"]
    assert blocks[1].text == text + "\n\n" + reference
    speech.SpeechRequest(text=expected, language=language, model_id=speech.LADA, voice="lada", blocks=blocks)


def test_multi_paragraph_bible_text_stays_together_before_its_reference():
    script = draft("Тиха надія")
    script.bible_text = "«Перший рядок уривка».\n\n«Наступний рядок уривка»."
    script.bible_reference = "Псалом 23:1–2"
    script = pipeline.with_spoken_title(script)
    blocks = pipeline.scene_blocks(script.scenes[0], script.title, bible_text=script.bible_text,
                                   bible_reference=script.bible_reference)
    assert len(blocks) == 2 and blocks[1].text.endswith("\n\nПсалом 23:1–2")
    assert "Наступний рядок" in blocks[1].text


@pytest.mark.parametrize("prefix", [True, False])
def test_legacy_saved_opening_moves_reference_after_bible_text_without_duplication(prefix):
    script = draft("Тиха надія")
    quote, reference = "«Господь — мій Пастир, тому в недостатку не буду»." , "Псалом 23:1"
    script.scenes[0].narration = "Тиха надія. " + (reference + ". " + quote if prefix else quote + " " + reference + ".")
    original = script.model_dump()
    result = pipeline.separate_bible_opening(script, reference)
    assert script.model_dump() == original
    assert result.bible_text == quote and result.bible_reference == reference
    assert result.scenes[0].narration == "Тиха надія.\n\n" + quote + "\n\n" + reference
    assert pipeline.separate_bible_opening(result, reference) == result


def test_legacy_opening_extracts_existing_reference_instead_of_adding_a_conflicting_one():
    script = draft("Тиха надія")
    result = pipeline.separate_bible_opening(script, "Матвія 6:34")
    assert result.bible_reference == "Псалом 23:1"
    assert "Псалом 23:1" not in result.bible_text
    assert "Матвія 6:34" not in result.scenes[0].narration


def test_writer_uses_bounded_batches_for_the_short_draft_context(monkeypatch):
    import sys
    from types import SimpleNamespace
    options = {}

    class Model:
        def __init__(self, **kwargs): options.update(kwargs)
        def close(self): pass

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=Model))
    with pipeline.writing_model("test.gguf", 10, context_size=8192):
        pass
    assert options["n_ctx"] == 8192 and options["n_batch"] == options["n_ubatch"] == 128


@pytest.mark.parametrize("editing", [False, True])
def test_generated_opening_is_separate_and_survives_editor_reordering(monkeypatch, editing):
    original = draft("Тиха надія")
    original.bible_text = "Господь — мій Пастир, тому в недостатку не буду."
    original.bible_reference = "Псалом 23:1"
    # A model may still put the reference first in narration. Structured fields determine the order.
    original.scenes[0].narration = "Псалом 23:1. " + original.bible_text
    opening_words = pipeline.word_count(pipeline.with_spoken_title(original)) - len(original.scenes[1].narration.split())
    original.scenes[1].narration = " ".join(["надія"] * (55 - opening_words))
    reordered = original.model_copy(deep=True)
    reordered.bible_text = "Помилковий переказ, якого не було в початковому тексті."
    reordered.bible_reference = "Псалом 23:2"
    reordered.scenes[0].narration = "Псалом 23:2. Тиха надія. Помилково змінений уривок."
    prompts, contexts = [], []

    @contextmanager
    def model(*_args):
        contexts.append(_args[3])
        yield type("FakeModel", (), {"metadata": {}})()

    def response(_model, schema, prompt, **_options):
        assert {"bible_text", "bible_reference"} <= set(schema["required"])
        assert schema["properties"]["bible_text"]["type"] == "string"
        assert "maxLength" not in schema["properties"]["bible_text"]
        prompts.append(prompt)
        return (original if len(prompts) == 1 else reordered).model_dump()

    monkeypatch.setattr(pipeline, "writing_model", model)
    monkeypatch.setattr(pipeline, "script_response", response)
    result = pipeline.write_script("Тиха надія · Псалом 23:1", None, 2, None, 10, morning=True,
                 text_range={"min_words": 50, "max_words": 60}, writing_settings=MamaySettings(editor_enabled=editing))
    assert result.bible_text == original.bible_text and result.bible_reference == original.bible_reference
    assert result.scenes[0].narration.split("\n\n") == ["Тиха надія.", original.bible_text, original.bible_reference]
    assert pipeline.word_count(result) == 55
    assert "Never put the reference before the passage" in prompts[0]
    assert len(prompts) == (2 if editing else 1)
    assert contexts == [8192]


def test_saved_topic_edits_title_bible_text_and_reference_independently(studio_env, writing):
    with client() as api:
        project = new_project(api)
        topic = generate(api, project)[0]
        assert topic["script"]["bible_reference"] == topic["passage"]
        assert topic["title"] not in topic["script"]["bible_text"]
        assert topic["passage"] not in topic["script"]["bible_text"]
        script = topic["script"]
        script["bible_text"] = "Окремий біблійний уривок, який користувач перевірив і зберіг."
        path = project + "/illustrated/topics/" + topic["id"]
        response = api.put(path, json=edit_input(topic, title="Новий тихий ранок", passage="Псалом 23:2", script=script))
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["script"]["scenes"][0]["narration"].split("\n\n") == [
            "Новий тихий ранок.", script["bible_text"], "Псалом 23:2"]
        arranged = topic_library.arrange_scenes(pipeline.Script.model_validate(saved["script"]), 3)
        assert arranged.bible_text == script["bible_text"] and arranged.bible_reference == "Псалом 23:2"
        assert arranged.scenes[0].narration == saved["script"]["scenes"][0]["narration"]
    with client() as api:
        assert next(item for item in api.get(project + "/illustrated/topics").json() if item["id"] == topic["id"])["script"] == saved["script"]


def test_legacy_queued_script_still_matches_its_original_render_manifest():
    old = draft("Тиха надія").model_dump(exclude={"bible_text", "bible_reference"})
    assert pipeline.Script.model_validate(old).model_dump(exclude_none=True) == old


def test_misfiled_image_description_becomes_an_image_prompt_and_caption_uses_spoken_words():
    script = draft("Тиха надія")
    raw = script.model_dump()
    description = "A single soft ray of sunlight falling on a small wooden table."
    raw["scenes"][0].update(caption=description, illustration="Тиха надія поруч")
    corrected = pipeline.read_script(raw, 2, "uk")
    assert corrected.scenes[0].narration == script.scenes[0].narration
    assert corrected.scenes[0].illustration == description
    assert corrected.scenes[0].caption in " ".join(corrected.scenes[0].narration.split())
    assert 2 <= len(corrected.scenes[0].caption.split()) <= 8
    assert raw["scenes"][0]["caption"] == description
