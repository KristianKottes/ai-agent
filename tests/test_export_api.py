import base64
import io
import json
import math
import shutil
import subprocess
import uuid
import wave
from array import array
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api.main import app, database_connection
from backend.voice.service import VoiceService


def test_saved_export_scopes_assets_and_survives_restart(monkeypatch, tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("The export integration requires FFmpeg and FFprobe.")
    for name in ("DATA", "CACHE", "CONFIG", "LOG", "TEMP"):
        monkeypatch.setenv(f"AI_BLOGGER_{name}_DIR", str(tmp_path / name.lower()))
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "export-test")
    headers = {"Authorization": "Bearer export-test"}
    topic_id, script_id, other_script = (str(uuid.uuid4()) for _ in range(3))
    created = "2026-11-15T12:00:00+00:00"
    text = "A short test narration for a scene."
    document = {
        "topic_id": topic_id, "title": "First export", "requested_duration_seconds": 30,
        "full_narration": text, "created_at": created,
        "paragraphs": [{"paragraph_id": 0, "section": "introduction", "text": text, "start_seconds": 0,
                        "duration_seconds": 30, "visual_suggestion": "A still image",
                        "on_screen_text": "", "voice_direction": "Warm"}],
        "quality": {"status": "review", "word_count": 7, "target_word_range": "60–80",
                    "estimated_spoken_duration_seconds": 1, "warnings": []},
    }

    def render_wav(self, _text, _voice_id, _speed, path):
        with wave.open(path, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(48000))
        return 1.0

    monkeypatch.setattr(VoiceService, "render", render_wav)
    with TestClient(app) as client:
        with database_connection() as db:
            db.execute(
                """INSERT INTO topics (id,title,title_key,description,category,opening_hook,target_audience,
                       estimated_duration_seconds,thumbnail_concept,script_potential,originality_score,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (topic_id, "First export", "first export", "Description", "Lifestyle", "Opening hook", "Audience",
                 30, "Thumbnail", "Potential", 50, created),
            )
            for key in (script_id, other_script):
                db.execute("INSERT INTO scripts VALUES (?,?,?,?,?)",
                           (key, topic_id, 30, json.dumps(dict(document, id=key)), created))
        root = f"/api/scripts/{script_id}"
        project = client.post("/api/projects", headers=headers, json={"title": "Linked timeline"}).json()
        project_url = f"/api/projects/{project['id']}/script"
        assert client.put(project_url, headers=headers, json={"script_id": str(uuid.uuid4())}).status_code == 404
        bound = client.put(project_url, headers=headers, json={"script_id": script_id})
        assert bound.status_code == 200
        assert bound.json()["script_id"] == script_id
        assert bound.json()["topic_id"] == topic_id
        narration = client.post(f"{root}/narration", headers=headers, json={"voice_id": "af_bella"}).json()
        image = io.BytesIO()
        Image.new("RGB", (24, 32), "orange").save(image, format="PNG")
        source = {"name": "Local scene", "kind": "image", "content_base64": base64.b64encode(image.getvalue()).decode()}
        asset = client.post(f"/api/scripts/{other_script}/scene-assets", headers=headers, json=source).json()
        payload = {
            "narration_id": narration["id"], "captions": False, "format": "vertical",
            "scenes": [{"source": "asset", "id": asset["id"], "frames": 24}],
        }
        assert client.post(f"{root}/exports", headers=headers, json=payload).status_code == 404
        assert client.post(f"{root}/exports", json=payload).status_code == 401
        asset = client.post(f"{root}/scene-assets", headers=headers, json=source).json()
        music_wav = io.BytesIO()
        with wave.open(music_wav, "wb") as sound:
            sound.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            sound.writeframes(array("h", (int(16000 * math.sin(index * math.tau / 60)) for index in range(6000))).tobytes())
        music_response = client.post(f"{root}/background-music", headers=headers, json={
            "name": "Music test", "content_base64": base64.b64encode(music_wav.getvalue()).decode(),
        })
        assert music_response.is_success, music_response.text
        music = music_response.json()
        draft_path = f"{root}/export-draft"
        assert client.get(draft_path, headers=headers).json()["scenes"] == []
        first = {
            "key": 3, "source": "asset", "id": asset["id"], "name": asset["name"],
            "max_frames": None, "presenter_narration_id": None, "frames": 24, "transition": "cut",
        }
        draft = {
            "format": "vertical", "quality": "high", "captions": False, "music_id": music["id"], "music_volume": 0.2,
            "scenes": [first, dict(first, key=2, frames=18, transition="fade_black")],
        }
        assert client.put(draft_path, json=draft).status_code == 401
        saved_draft = client.put(draft_path, headers=headers, json=draft)
        assert saved_draft.is_success, saved_draft.text
        assert saved_draft.json() == draft
        assert client.put(draft_path, headers=headers, json=dict(draft, scenes=[first, first])).status_code == 422
        payload["scenes"][0]["id"] = asset["id"]
        payload.update(music_id=music["id"], music_volume=0.2)
        exported = client.post(f"{root}/exports", headers=headers, json=payload)
        assert exported.is_success, exported.text
        record = exported.json()
        assert record["frame_count"] == 24
        assert (record["width"], record["height"]) == (1080, 1920)
        assert Path(record["path"]).is_file()
        mixed = subprocess.check_output([
            "ffmpeg", "-nostdin", "-v", "error", "-ss", "0.8", "-i", record["path"], "-t", "0.1",
            "-vn", "-f", "s16le", "pipe:1",
        ])
        # The narration is silent and the track is only 0.25s long, so audio here
        # proves the background has been looped and mixed.
        assert mixed and max(map(abs, array("h", mixed))) > 1000

    with TestClient(app) as client:
        assert client.get("/api/projects", headers=headers).json() == [bound.json()]
        assert client.get(draft_path, headers=headers).json() == draft
        assert client.get(f"/api/scripts/{other_script}/export-draft", headers=headers).json()["scenes"] == []
        assert client.get(f"{root}/exports", headers=headers).json() == [record]
        preview = f"/api/exports/{record['id']}/preview"
        assert client.get(preview, headers=headers).json() == {"path": record["path"]}
        assert client.get(preview).status_code == 401
        client.patch(root, headers=headers, json={"full_narration": "The saved script has changed."}).raise_for_status()
        assert client.post(f"{root}/exports", headers=headers, json=payload).status_code == 409
        assert client.get(draft_path, headers=headers).json() == draft
