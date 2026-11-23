from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.avatar import service


def test_frame_sampling_tracks_speech_boundaries_and_gaps():
    cues = [(0, 5, "closed"), (10, 15, "open"), (15, 22, "round")]
    assert list(service.frame_poses(cues, 7)) == ["closed", "rest", "open", "open", "round", "rest", "rest"]


def test_video_validation_checks_end_alignment(monkeypatch, tmp_path):
    streams = [
        {"codec_type": "video", "codec_name": "h264", "start_time": "0", "duration": "5", "nb_frames": "120"},
        {"codec_type": "audio", "codec_name": "aac", "start_time": "0", "duration": "5"},
    ]
    monkeypatch.setattr(service, "_run", lambda *args: json.dumps({"streams": streams}).encode())
    assert service.validate_video("ffprobe", tmp_path / "clip.mp4", 5) == {
        "frame_count": 120, "av_start_offset_ms": 0, "av_end_offset_ms": 0,
    }
    streams[0]["duration"] = "5.06"
    streams[1]["duration"] = "4.94"
    with pytest.raises(service.AvatarError, match="timing check"):
        service.validate_video("ffprobe", tmp_path / "clip.mp4", 5)


def test_avatar_routes_validate_input_and_persist_presets(monkeypatch, tmp_path):
    for name in ("DATA", "CACHE", "CONFIG", "LOG", "TEMP"):
        monkeypatch.setenv(f"AI_BLOGGER_{name}_DIR", str(tmp_path / name.lower()))
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "avatar-api-test")
    headers = {"Authorization": "Bearer avatar-api-test"}
    input_data = {"name": "  My presenter ", "look": "eli", "backdrop": "plum", "outfit": "sage", "motion": False}

    with TestClient(app) as client:
        assert client.get("/api/avatar").status_code == 401
        assert client.post("/api/avatar/preview", headers=headers, json={"look": "unrecognized"}).status_code == 422
        created = client.post("/api/avatar/presets", headers=headers, json=input_data)
        assert created.status_code == 200
        assert created.json()["name"] == "My presenter"
        assert client.post("/api/avatar/presets", headers=headers, json=input_data).status_code == 409
        assert client.post("/api/avatar/renders", headers=headers, json={"narration_id": str(uuid.uuid4()), "seconds": 5}).status_code == 404
        assert client.get(f"/api/avatar/renders/{uuid.uuid4()}", headers=headers).status_code == 404

    with TestClient(app) as client:
        assert client.get("/api/avatar", headers=headers).json()["presets"] == [created.json()]
