"""Bible references, saved pronunciation, and the text actually passed to speech engines."""

from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
import wave
from pydantic import ValidationError

from backend.studio import illustrated, speech
from backend.studio.pronunciation import SpeechSettings
from backend.studio.service import ProjectStudio
from backend.studio.text import normalize, sections
from backend.scenes import illustrated_video as pipeline
from test_studio import client, new_project, studio_env


@pytest.mark.parametrize("text, expected", [
    ("Матвія 6:34", "Матвія, розділ шостий, вірш тридцять четвертий"),
    ("Мт. 6:34", "Матвія, розділ шостий, вірш тридцять четвертий"),
    ("Псалом 23:1–3", "Псалом двадцять третій, вірші від першого до третього"),
    ("Пс. 119:105", "Псалом сто дев'ятнадцятий, вірш сто п'ятий"),
    ("1Кор. 13:4", "Перше послання до Коринтян, розділ тринадцятий, вірш четвертий"),
    ("2 Тимофія 1:7", "Друге послання до Тимофія, розділ перший, вірш сьомий"),
    ("1 Царств 3:10", "Перша книга Царств, розділ третій, вірш десятий"),
])
def test_bible_reference_is_spoken_as_scripture_not_a_time(text, expected):
    assert normalize(text, "uk") == expected


def test_references_preserve_clock_times_and_abbreviation_segmentation():
    original = "Мт. 6:34. Зустрінемось о 06:34."
    parts = list(sections(original))
    assert len(parts) == 2 and parts[0][0] == "Мт. 6:34."
    spoken = normalize(original, "uk")
    assert "розділ шостий, вірш тридцять четвертий" in spoken
    assert "о шостій годині тридцять чотири хвилини" in spoken
    assert normalize("Matthew 6:34", "en") == "Matthew 6:34"


def test_saved_speech_settings_persist_validate_and_snapshot_jobs(studio_env, monkeypatch):
    monkeypatch.setattr(illustrated, "validate", lambda *_: None)
    monkeypatch.setattr(ProjectStudio, "_run_queue", lambda self: self.stop.wait(timeout=10))
    path = "/api/studio/illustrated/speech-settings"
    with client() as api:
        original = api.get(path).json()
        assert original == SpeechSettings().model_dump()
        project = new_project(api)
        first = api.post(project + "/illustrated/render", json={"topic": "Перша тема"})
        assert first.status_code == 202
        custom = dict(original, pronunciation={"Ісус": "Ісу́с"}, speed=.95, pause_ms=220)
        custom.update(title_pause_ms=700, block_pause_ms=1200, paragraph_pause_ms=450)
        saved = api.put(path, json=custom)
        assert saved.status_code == 200, saved.text
        assert saved.json()["revision"] == 1
        assert api.put(path, json=custom).status_code == 409
        assert api.put(path, json=dict(saved.json(), speed=0)).status_code == 422
        assert api.put(path, json=dict(saved.json(), pronunciation={"Ісус": "Ісу́с", "ІСУС": "Ісу́с"})).status_code == 422
        assert api.put(path, json=dict(saved.json(), pronunciation={"word": " "})).status_code == 422
        second = api.post(project + "/illustrated/render", json={"topic": "Друга тема", "text_words": 250})
        assert second.status_code == 202
        from backend.api.main import database_connection
        with database_connection() as db:
            rows = db.execute("SELECT input_json FROM studio_jobs ORDER BY rowid").fetchall()
        requests = [illustrated.IllustratedRequest.model_validate_json(row[0]) for row in rows]
        assert requests[0].speech_settings.pronunciation == {}
        assert requests[1].speech_settings.pronunciation == {"Ісус": "Ісу́с"}
        assert requests[1].speech_settings.speed == .95
        assert requests[1].text_words == 250
        assert requests[1].speech_settings.block_pause_ms == 1200
        assert requests[0].speech_settings.block_pause_ms is None
        assert api.post(project + "/illustrated/render", json={"topic": "Тема", "text_words": 250, "seconds": 30}).status_code == 422
        assert api.post(project + "/illustrated/render", json={"topic": "Тема", "text_words": 1001}).status_code == 422
    with client() as api:
        assert api.get(path).json() == saved.json()


def test_video_speech_request_passes_delivery_settings_to_selected_voice(tmp_path):
    studio = SimpleNamespace(data=tmp_path, verify_speech=lambda *_: None)
    settings = SpeechSettings(pronunciation={"Ісус": "Ісу́с"}, speed=.95, pause_ms=240)
    request = illustrated.IllustratedRequest(topic="Ранкова молитва", speech_settings=settings)
    actual = illustrated.speech_request(studio, "project", request)
    assert actual.model_id == speech.LADA
    assert (actual.speed, actual.pause_ms, actual.pronunciation) == (.95, 240, {"Ісус": "Ісу́с"})
    request.narrator = illustrated.Narrator(model_id="zonos3-mini", voice="narrator")
    assert illustrated.speech_request(studio, "project", request).speed == 1


def test_voice_preview_and_video_keep_caption_spelling_but_apply_pronunciation(tmp_path, monkeypatch):
    received = []

    class FakeProvider:
        research_only = False
        model_id = speech.LADA

        def uses_reference(self, _voice):
            return False

        @contextmanager
        def load(self, *_args, **_kwargs):
            yield None

        def synthesize(self, _engine, text, _voice, speed, _reference):
            received.append((text, speed))
            return SimpleNamespace(samples=np.sin(np.arange(2400) * .1) * .2, sample_rate=24000)

    monkeypatch.setitem(speech.PROVIDERS, speech.LADA, FakeProvider())
    text = "Ранкова молитва.\n\nМт. 6:34.\n\nІсус дарує надію."
    request = speech.SpeechRequest(text=text, language="uk", model_id=speech.LADA, voice="lada",
                                   pronunciation={"Ісус": "Ісу́с"}, speed=.95, pause_ms=220)
    cues = speech.generate(tmp_path, request, tmp_path / "preview.wav")
    assert " ".join(cue["text"] for cue in cues) == "Ранкова молитва. Мт. 6:34. Ісус дарує надію."
    assert any("вірш тридцять четвертий" in text for text, _ in received)
    assert any("Ісу́с дарує надію" in text for text, _ in received)
    assert all(speed == .95 for _, speed in received)
    assert cues[1]["start"] - cues[0]["end"] == pytest.approx(.44)


@pytest.mark.parametrize("model_id", [speech.LADA, "zonos3-mini"])
def test_block_pauses_are_exact_and_do_not_add_paragraph_pause(tmp_path, monkeypatch, model_id):
    spoken = []

    class Provider:
        research_only = False

        def uses_reference(self, _voice):
            return False

        @contextmanager
        def load(self, *_args, **_kwargs):
            yield None

        def synthesize(self, _engine, text, *_args):
            spoken.append(text)
            return SimpleNamespace(samples=np.full(2400, .2), sample_rate=24000)

    provider = Provider()
    provider.model_id = model_id
    monkeypatch.setitem(speech.PROVIDERS, model_id, provider)
    blocks = [speech.SpeechBlock(text="Morning hope.", kind="title"),
              speech.SpeechBlock(text="A passage of Scripture.", kind="scripture"),
              speech.SpeechBlock(text="A quiet poem ends here.", kind="poem", pause_after_ms=1700),
              speech.SpeechBlock(text="One thought.\n\nAnother thought.", kind="paragraph")]
    request = speech.SpeechRequest(text="\n\n".join(block.text for block in blocks), language="en", model_id=model_id,
                                   voice="lada", pause_ms=125, title_pause_ms=650, block_pause_ms=1100,
                                   paragraph_pause_ms=450, blocks=blocks, trailing_pause=True)
    destination = tmp_path / "blocks.wav"
    cues = speech.generate(tmp_path, request, destination)
    assert len(cues) == 5
    expected = [.65, 1.1, 1.7, .45]
    for before, after, pause in zip(cues, cues[1:], expected):
        assert after["start"] - before["end"] == pytest.approx(pause)
    with wave.open(str(destination), "rb") as audio:
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16)
        assert len(samples) / audio.getframerate() == pytest.approx(.5 + sum(expected) + .45)
        for before, after in zip(cues, cues[1:]):
            assert np.count_nonzero(samples[round(before["end"] * 24000):round(after["start"] * 24000)]) == 0
    assert spoken == [cue["text"] for cue in cues]
    assert all(label not in spoken for label in ("title", "scripture", "poem", "paragraph"))


def test_sentences_and_paragraphs_have_independent_pauses(tmp_path, monkeypatch):
    class Provider:
        model_id = speech.LADA
        research_only = False

        def uses_reference(self, _voice):
            return False

        @contextmanager
        def load(self, *_args, **_kwargs):
            yield None

        def synthesize(self, *_args):
            return SimpleNamespace(samples=np.full(2400, .2), sample_rate=24000)

    monkeypatch.setitem(speech.PROVIDERS, speech.LADA, Provider())
    request = speech.SpeechRequest(text="One sentence. Another sentence.\n\nA new paragraph.", language="en",
                                   model_id=speech.LADA, voice="lada", pause_ms=150, paragraph_pause_ms=800)
    cues = speech.generate(tmp_path, request, tmp_path / "paragraphs.wav")
    assert cues[1]["start"] - cues[0]["end"] == pytest.approx(.15)
    assert cues[2]["start"] - cues[1]["end"] == pytest.approx(.8)
    request.pause_ms = 0
    request.paragraph_pause_ms = 0
    cues = speech.generate(tmp_path, request, tmp_path / "continuous.wav")
    assert all(after["start"] == before["end"] for before, after in zip(cues, cues[1:]))


def test_scene_blocks_preserve_title_poem_and_paragraph_breaks():
    scene = pipeline.Scene(narration="A quiet start.\n\nFirst verse.\nSecond verse.\n\nLast verse.", section="poem",
                           caption="A quiet start", illustration="Sunrise over quiet hills.")
    blocks = pipeline.scene_blocks(scene, "A quiet start")
    assert [block.kind for block in blocks] == ["title", "poem"]
    assert blocks[1].text == "First verse.\nSecond verse.\n\nLast verse."
    scene.section = "paragraph"
    assert len(pipeline.scene_blocks(scene, "A quiet start")) == 3
    with pytest.raises(ValidationError, match="same text"):
        speech.SpeechRequest(text="Original text", blocks=[speech.SpeechBlock(text="Different text")],
                             language="en", model_id=speech.LADA, voice="lada")


def test_bible_pause_precedes_reflection_even_when_writer_uses_one_scene():
    scene = pipeline.Scene(narration="Надія поруч. «Не журіться про завтра». Матвія 6:34. Сьогодні ми можемо довіряти Богові.",
                           section="scripture", caption="Надія поруч", illustration="A quiet sunrise over hills.")
    blocks = pipeline.scene_blocks(scene, "Надія поруч")
    assert [block.kind for block in blocks] == ["title", "scripture", "paragraph"]
    assert blocks[1].text == "«Не журіться про завтра». Матвія 6:34."
    assert blocks[2].text == "Сьогодні ми можемо довіряти Богові."
    speech.SpeechRequest(text=scene.narration, blocks=blocks, language="uk", model_id=speech.LADA, voice="lada")
