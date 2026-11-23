from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.avatar import portrait_model

PHOTO = Path(__file__).parent / "fixtures" / "fictional-presenter.png"


def test_portrait_import_and_framing_persist(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    for name in ("DATA", "CACHE", "CONFIG", "LOG", "TEMP"):
        monkeypatch.setenv(f"AI_BLOGGER_{name}_DIR", str(tmp_path / name.lower()))
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "portrait-test")
    headers = {"Authorization": "Bearer portrait-test"}

    with TestClient(app) as client:
        assert client.get("/api/avatar/portraits").status_code == 401
        assert client.post("/api/avatar/portrait-model/install", headers=headers,
                           json={"accepted_license": False}).status_code == 422
        assert client.post("/api/avatar/portraits", headers=headers,
                           json={"name": "Bad input", "image_base64": "invalid"}).status_code == 422
        image = base64.b64encode(PHOTO.read_bytes()).decode()
        result = client.post("/api/avatar/portraits", headers=headers,
                             json={"name": "Fictional presenter", "image_base64": image})
        assert result.status_code == 200
        path = "/api/avatar/portraits/" + result.json()["id"]
        assert client.patch(path, headers=headers, json={"x": 300, "y": 140, "side": 400}).status_code == 422
        crop = {"x": 153, "y": 140, "side": 337}
        assert client.patch(path, headers=headers, json=crop).json()["crop"] == crop

    with TestClient(app) as client:
        saved = client.get(path, headers=headers).json()
        assert saved["crop"] == crop
        assert saved["reference_seconds"] is None
        assert saved["image_url"].startswith("data:image/jpeg;base64,")
        assert client.get("/api/avatar/portraits", headers=headers).json()[0]["id"] == saved["id"]


def test_video_reference_import_discards_original_audio_and_restores_metadata(monkeypatch, tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("The video import check requires FFmpeg and FFprobe")
    for name in ("DATA", "CACHE", "CONFIG", "LOG", "TEMP"):
        monkeypatch.setenv(f"AI_BLOGGER_{name}_DIR", str(tmp_path / name.lower()))
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "portrait-test")
    headers = {"Authorization": "Bearer portrait-test"}
    clip = tmp_path / "source.mov"
    subprocess.run([
        ffmpeg, "-v", "error", "-loop", "1", "-i", str(PHOTO), "-f", "lavfi",
        "-i", "sine=frequency=400", "-t", "1.25", "-vf", "scale=384:288",
        "-c:v", "mpeg4", "-c:a", "aac", "-threads:v", "2", str(clip),
    ], check=True, capture_output=True)
    encoded = base64.b64encode(clip.read_bytes()).decode()

    with TestClient(app) as client:
        result = client.post("/api/avatar/portraits", headers=headers,
                             json={"name": "Reference", "video_base64": encoded})
        assert result.status_code == 200, result.text
        saved = result.json()
        assert saved["reference_seconds"] == 1.25
        incompatible = client.post("/api/avatar/portrait-renders", headers=headers, json={
            "portrait_id": saved["id"], "narration_id": str(uuid.uuid4()), "animate_head": True,
        })
        assert incompatible.status_code == 422
        assert incompatible.json()["detail"] == "Subtle head motion is only available for photos."
        assert client.post("/api/avatar/portraits", headers=headers,
                           json={"name": "Unreadable", "video_base64": "AAAA"}).status_code == 422
    with TestClient(app) as client:
        assert client.get("/api/avatar/portraits", headers=headers).json() == [saved]
        preview = client.get(f"/api/avatar/portraits/{saved['id']}", headers=headers).json()
        assert preview["image_url"].startswith("data:image/jpeg;base64,")

    normalized = tmp_path / "data" / "avatar" / "portraits" / f"{saved['id']}.mp4"
    probe = subprocess.check_output([
        ffprobe, "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "json", str(normalized),
    ])
    assert json.loads(probe)["streams"] == [{"codec_type": "video", "width": 640, "height": 640}]


def test_portrait_weight_resume_verifies_before_promoting(monkeypatch, tmp_path):
    data = b"abcdefgh"
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(portrait_model, "WEIGHTS", {"model.safetensors": (len(data), digest)})
    model = portrait_model.PortraitModel(tmp_path, tmp_path / "test.sqlite3")
    model.root.mkdir(parents=True)
    partial = model.root / "model.safetensors.part"
    partial.write_bytes(data[:3])

    def open_response(request, *, timeout):
        assert request.get_header("Range") == "bytes=3-"
        response = io.BytesIO(data[3:])
        response.status = 206
        response.headers = {"Content-Range": "bytes 3-7/8"}
        return response

    monkeypatch.setattr(portrait_model.urllib.request, "urlopen", open_response)
    model._fetch("model.safetensors")
    assert model._valid_file("model.safetensors")
    assert not partial.exists()
