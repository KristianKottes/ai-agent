from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.ai.contracts import DirectorDraft, ScriptDraft, TopicDraft
from backend.ai.generation import _assign_director_paragraphs, _near_duplicate, _normalize_director_fields, _normalize_script_fields, _parse_json, _title_key
from backend.ai.model_manager import MODEL_SHA256, LocalModelManager
from backend.api.main import app


def configure_application_paths(monkeypatch, tmp_path):
    for key, name in (
        ("AI_BLOGGER_DATA_DIR", "data"),
        ("AI_BLOGGER_CACHE_DIR", "cache"),
        ("AI_BLOGGER_CONFIG_DIR", "config"),
        ("AI_BLOGGER_LOG_DIR", "logs"),
        ("AI_BLOGGER_TEMP_DIR", "temp"),
    ):
        monkeypatch.setenv(key, str(tmp_path / name))


def test_topic_contract_enforces_originality_score_and_required_fields():
    valid = {
        "title": "Why quiet mornings feel different",
        "description": "A thoughtful short video about slow starts and attention.",
        "category": "Modern Life",
        "opening_hook": "When did your morning stop feeling like your own?",
        "target_audience": "Adults rebuilding healthier daily habits",
        "estimated_duration_seconds": 180,
        "thumbnail_concept": "A sunrise divided between a phone and a journal",
        "script_potential": "Compare common routines and offer one small experiment.",
        "originality_score": 73,
    }
    assert TopicDraft.model_validate(valid).originality_score == 73
    assert TopicDraft.model_validate({**valid, "originality_score": 5.2}).originality_score == 52
    assert TopicDraft.model_validate({**valid, "thumbnail_concept": "meditation"}).thumbnail_concept == (
        "Meditation scene showing Why quiet mornings feel different, with a clear focal subject"
    )
    assert TopicDraft.model_validate({**valid, "thumbnail_concept": "smart_home_2019"}).thumbnail_concept.startswith(
        "Smart Home 2019 scene showing"
    )
    with pytest.raises(ValidationError):
        TopicDraft.model_validate({**valid, "originality_score": 101})


def test_script_requires_hook_and_conclusion():
    paragraph = {
        "section": "opening_hook",
        "text": "Start with a clear, conversational hook.",
        "visual_suggestion": "Presenter beside a window.",
        "on_screen_text": "Make a small change",
        "voice_direction": "Warm and curious, with a brief pause.",
    }
    with pytest.raises(ValidationError):
        ScriptDraft.model_validate({"title": "A short script", "paragraphs": [paragraph] * 5})


def test_script_field_cleanup_keeps_narration_and_fits_editor_fields():
    payload = {
        "title": "A complete short script",
        "paragraphs": [
            {
                "section": "introduction",
                "text": "Keep the original spoken narration exactly as generated.",
                "visual_suggestion": "Window light",
                "on_screen_text": "A very long caption " * 12,
                "voice_direction": "calm",
            }
            for _ in range(5)
        ],
    }
    normalized = _normalize_script_fields(payload)
    assert normalized["paragraphs"][0]["text"] == payload["paragraphs"][0]["text"]
    assert len(normalized["paragraphs"][0]["on_screen_text"]) <= 120
    assert normalized["paragraphs"][0]["voice_direction"].startswith("Calm delivery")
    assert normalized["paragraphs"][0]["section"] == "opening_hook"
    assert normalized["paragraphs"][-1]["section"] == "conclusion"


def test_director_field_cleanup_adds_missing_style_defaults():
    normalized = _normalize_director_fields({"visual_style": "warm", "scenes": [{"generation_prompt": "very long prompt " * 60}]})
    assert len(normalized["visual_style"]) >= 10
    assert len(normalized["scenes"][0]["generation_prompt"]) <= 500


def test_director_grouping_covers_each_paragraph_in_order():
    scene = {
        "scene_type": "talking_head",
        "paragraph_indexes": [0],
        "presenter_present": True,
        "camera_angle": "Medium shot",
        "camera_movement": "Static frame",
        "lighting": "Soft lighting",
        "background": "Neutral backdrop",
        "visual_description": "Presenter speaks to camera in a calm setting.",
        "generation_prompt": "Medium shot of a presenter speaking calmly in a neutral studio.",
        "transition": "Soft cut",
        "audio_requirements": ["Clear narration"],
    }
    draft = DirectorDraft.model_validate({"visual_style": "Warm editorial style", "scenes": [scene] * 3})
    fixed = _assign_director_paragraphs(draft, 5)
    assert [index for item in fixed.scenes for index in item.paragraph_indexes] == list(range(5))


def test_director_contract_rejects_untyped_scene_data():
    with pytest.raises(ValidationError):
        DirectorDraft.model_validate({"visual_style": "Warm editorial look", "scenes": [{"narration": "hello"}] * 3})


def test_topic_normalization_detects_duplicates():
    assert _title_key("The Quiet, Useful Morning!") == _title_key("the quiet useful morning")
    assert _near_duplicate("A Better Way to Start the Morning", "A Better Way to Start Your Morning")


def test_json_parser_escapes_literal_control_characters_inside_strings():
    assert _parse_json('{"topic":"first line\nsecond line"}') == {"topic": "first line\nsecond line"}


def test_model_status_reports_uninstalled_without_fetching(tmp_path):
    manager = LocalModelManager(tmp_path / "models")
    status = manager.status()
    assert status.state == "available"
    assert status.inference_ready is False
    assert status.sha256 == MODEL_SHA256
    assert not manager.model_path.exists()


def test_model_download_requires_license_acceptance_and_api_auth(monkeypatch, tmp_path):
    configure_application_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "phase-four-token")
    headers = {"Authorization": "Bearer phase-four-token"}
    with TestClient(app) as client:
        status = client.get("/api/ai/model", headers=headers)
        assert status.status_code == 200
        assert status.json()["state"] == "available"
        assert client.get("/api/ai/model").status_code == 401
        assert client.post("/api/ai/model/install", headers=headers, json={"accepted_license": False}).status_code == 422
        assert client.post("/api/topics/generate", headers=headers, json={"count": 30}).status_code == 409
        assert client.post("/api/topics/generate", headers=headers, json={"count": 12}).status_code == 422
