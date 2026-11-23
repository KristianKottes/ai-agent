from __future__ import annotations

import io
import hashlib
import math
import tarfile
import wave
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.voice import service


def test_chunks_prefer_sentences_and_keep_all_words():
    text = "First thought. Second thought is a little longer. Third thought.\n\nNew paragraph."
    chunks = list(service.narration_chunks(text, limit=25))
    assert chunks[0] == "First thought."
    assert chunks[-1] == "New paragraph."
    assert " ".join(chunks) == " ".join(text.split())
    assert all(0 < len(chunk) <= 25 for chunk in chunks)


def test_render_normalizes_entire_wav_and_rejects_silence():
    section = [0.1, -0.4, 0.2] * 100
    engine = SimpleNamespace(generate=lambda *a, **kw: SimpleNamespace(sample_rate=24000, samples=section))
    destination = io.BytesIO()
    assert service._render(engine, "First paragraph.\n\nSecond paragraph.", 1, 1, destination) == 0.145
    destination.seek(0)
    with wave.open(destination, "rb") as result:
        assert (result.getnchannels(), result.getsampwidth(), result.getframerate()) == (1, 2, 24000)
        pcm = result.readframes(result.getnframes())
    peak = max(abs(int.from_bytes(pcm[i:i+2], "little", signed=True)) for i in range(0, len(pcm), 2))
    assert math.isclose(20 * math.log10(peak / 32767), -1, abs_tol=0.001)
    section[:] = [0.0]
    with pytest.raises(service.VoiceError, match="silent audio"):
        service._render(engine, "A sample", 1, 1, io.BytesIO())


def test_archive_extraction_rejects_paths_outside_model_directory(tmp_path):
    path = tmp_path / "model.tar.bz2"
    content = b"bad archive"
    member = tarfile.TarInfo(f"{service.MODEL_ID}/../../outside")
    member.size = len(content)
    with tarfile.open(path, "w:bz2") as archive:
        archive.addfile(member, io.BytesIO(content))
    with pytest.raises(service.VoiceError, match="unexpected path"):
        service._extract_package(path, tmp_path / "stage")
    assert not (tmp_path / "outside").exists()


def test_download_resumes_only_matching_bytes(monkeypatch, tmp_path):
    archive = b"0123456789"
    monkeypatch.setattr(service, "MODEL_BYTES", len(archive))
    monkeypatch.setattr(service, "MODEL_SHA256", hashlib.sha256(archive).hexdigest())
    voice = service.VoiceService(tmp_path, tmp_path / "test.sqlite3")
    voice.root.mkdir()
    voice.partial.write_bytes(archive[:3])

    def open_range(request, *, timeout):
        assert request.get_header("Range") == "bytes=3-"
        response = io.BytesIO(archive[3:])
        response.status = 206
        response.headers = {"Content-Range": "bytes 3-9/10"}
        return response

    monkeypatch.setattr(service.urllib.request, "urlopen", open_range)
    voice._download()
    assert voice.partial.read_bytes() == archive
    assert voice.downloaded == len(archive)


def test_voice_routes_require_auth_consent_and_valid_speed(monkeypatch, tmp_path):
    for name in ("data", "cache", "config", "logs", "temp"):
        key = "LOG" if name == "logs" else name.upper()
        monkeypatch.setenv(f"AI_BLOGGER_{key}_DIR", str(tmp_path / name))
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "local-test")
    headers = {"Authorization": "Bearer local-test"}
    with TestClient(app) as client:
        assert client.get("/api/voice").status_code == 401
        status = client.get("/api/voice", headers=headers)
        assert status.json()["state"] == "available"
        assert not status.json()["ready"]
        assert len(status.json()["voices"]) == 11
        assert client.post("/api/voice/install", headers=headers, json={"accepted_license": False}).status_code == 422
        assert client.post("/api/voice/preview", headers=headers, json={"voice_id": "af_bella", "speed": 5}).status_code == 422
        assert client.post("/api/voice/preview", headers=headers, json={"voice_id": "af_bella", "speed": 1}).status_code == 409
