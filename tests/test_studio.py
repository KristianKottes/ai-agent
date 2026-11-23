"""Studio regression tests and opt-in checks backed by real local checkpoints."""

import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api.main import app
from backend.avatar.service import _binary
from backend.studio import holos, model_install, openvoice, zonos
from backend.studio.audio import analyze, write_wave
from backend.studio.motion import Renderer, Composition, templates
from backend.studio.service import ProjectStudio
from backend.studio.speech import LADA, POCKET
from backend.studio.text import mixed_language, normalize, sections
from backend.voice.service import MODEL_ID

UKRAINIAN = (
    "Доброго ранку! Сьогодні на нас чекає чудовий день.",
    "Українська мова має красиве звучання та багату історію.",
    "Зустріч відбудеться 25 листопада о 14:30.",
    "Вартість підписки становить 250 гривень.",
    "Господь — мій Пастир, тому в недостатку не буду.",
)


@pytest.fixture
def studio_env(monkeypatch, tmp_path):
    for name in ("data", "cache", "log", "temp", "config"):
        monkeypatch.setenv(f"AI_BLOGGER_{name.upper()}_DIR", str(tmp_path / name))
    monkeypatch.setenv("AI_BLOGGER_LOCAL_TOKEN", "studio-test")
    if not (_binary("ffmpeg") and _binary("ffprobe")):
        pytest.skip("FFmpeg and FFprobe are required for studio integration tests.")
    return tmp_path / "data"


def client():
    return TestClient(app, headers={"Authorization": "Bearer studio-test"})


def new_project(api, title="Мій проєкт"):
    result = api.post("/api/projects", json={"title": title})
    assert result.status_code == 201, result.text
    return "/api/studio/" + result.json()["id"]


def wait_for_asset(api, project, id, timeout=80):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = api.get(project + "/workspace").json()
        job = next(job for job in result["jobs"] if job["id"] == id)
        if job["state"] not in ("queued", "running"):
            assert job["state"] == "completed", job
            return next(asset for asset in result["assets"] if asset["id"] == id)
        time.sleep(.08)
    pytest.fail("Studio job did not finish in the test budget.")


def media_path(api, project, asset, format="original"):
    result = api.get(project + f"/assets/{asset}/file/{format}")
    assert result.status_code == 200, result.text
    return Path(result.json()["path"])


def media_input(path, name=None):
    return {"name": name or path.name, "content_base64": base64.b64encode(path.read_bytes()).decode("ascii")}


def tone(path, seconds=1):
    time_axis = np.arange(round(seconds * 24_000)) / 24_000
    write_wave(path, .3 * np.sin(2*np.pi*440*time_axis), 24_000, normalize=False)
    return path


def scene(api, id, name="modern-typography"):
    composition = next(p["composition"] for p in api.get("/api/studio/presets").json() if p["id"] == name)
    composition.update(width=480, height=270, fps=24)
    composition["layers"][-1]["text"] = "Моя історія · Її ґанок"
    return {"asset_id": id, "composition": composition}


def render(api, project, draft):
    queued = api.post(project + "/render", json=draft)
    assert queued.status_code == 202, queued.text
    job = wait_for_asset(api, project, queued.json()["id"])
    return job, media_path(api, project, job["id"])


def check_mp4(path, duration, fps=24):
    raw = subprocess.check_output([_binary("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(path)])
    video, audio = json.loads(raw)["streams"]
    assert video["codec_name"] == "h264"
    assert audio["codec_name"] == "aac"
    assert video["codec_type"] == "video" and audio["codec_type"] == "audio"
    assert float(video["start_time"]) == 0
    assert float(audio["start_time"]) == 0
    assert math.isclose(float(video["duration"]), duration, abs_tol=1/fps + .003)
    assert math.isclose(float(audio["duration"]), duration, abs_tol=.004)


def test_ukrainian_normalization_and_long_segmentation():
    text = normalize(" ".join(UKRAINIAN) + " Зустріч 23.11.2026. Вартість 24 грн.", "uk",
                     {"нас": "на\u0301с"})
    assert "на\u0301с" in text
    assert "двадцять п’ятого листопада о чотирнадцятій годині тридцять хвилин" in text
    assert "двісті п'ятдесят гривень" in text
    assert "двадцять третього листопада дві тисячі двадцять шостого року" in text
    assert "двадцять чотири гривні." in text
    assert mixed_language("Студія — Studio")
    assert not mixed_language("Студія")
    long = ("Публікація запланована сьогодні. " * 60 + "\n\nДругий абзац.").strip()
    cuts = list(sections(long))
    assert " ".join(chunk for chunk, _ in cuts) == " ".join(long.split())
    assert max(len(chunk) for chunk, _ in cuts) <= 220
    assert [chunk for chunk, paragraph in cuts if paragraph][-1] == "Другий абзац."
    assert len(list(sections("Пане І. Коваленко, зустріч о 14:30. Чекаємо."))) == 2
    assert list(sections("Доброго ранку! Чекаємо.\n\nДругий абзац.", split_sentences=False)) == [
        ("Доброго ранку! Чекаємо.", True), ("Другий абзац.", True),
    ]


def test_fft_rms_and_beat_analysis(studio_env, tmp_path):
    rate = 24_000
    time_axis = np.arange(5 * rate) / rate
    tone_wave = .45 * np.sin(2*np.pi*440*time_axis)
    clock = (time_axis - .25) % .5
    signal = tone_wave * np.where(clock < .07, np.exp(-clock * 16), 0)
    target = tmp_path / "ритм.wav"
    write_wave(target, signal, rate, normalize=False)
    analysis = analyze(target)
    assert 0.42 < analysis.peak.max() < 0.46
    assert .18 < analysis.feature("rms", .27) < .30
    assert analysis.feature("rms", .52) < .0001
    assert len(analysis.onsets) >= 9
    assert len(analysis.beats) >= 9
    assert np.median(np.diff(analysis.beats)) == pytest.approx(.5, abs=.04)
    assert analysis.silence
    bands, _, _ = analysis.frame(.27)
    assert 20 <= np.argmax(bands) <= 27


def test_import_render_templates_and_reopen(studio_env, tmp_path):
    wav = tone(tmp_path / "український тест.wav")
    mp3 = tmp_path / "ритм.mp3"
    subprocess.run([_binary("ffmpeg"), "-v", "error", "-i", str(wav), str(mp3)], check=True)
    with client() as api:
        assert TestClient(app).get("/api/studio/tools").status_code == 401
        project = new_project(api)
        foreign = new_project(api, "Other project")
        assert api.post(project + "/speech", json=[]).status_code == 422
        assert api.post("/api/studio/normalize", json={"language": "uk", "text": "  "}).status_code == 422
        assert len(api.get("/api/studio/presets").json()) == 10
        audio = api.post(project + "/import/audio", json=media_input(mp3)).json()
        wav_asset = api.post(project + "/import/audio", json=media_input(wav))
        assert wav_asset.status_code == 201
        assert audio["name"] == mp3.name
        assert api.get(foreign + f"/assets/{audio['id']}/analysis").status_code == 404
        assert len(api.get(project + f"/assets/{audio['id']}/analysis").json()["waveform"]) == 240
        draft = scene(api, audio["id"], "neon-spectrum")
        zero = api.post(project + "/preview", json=dict(draft, time=0)).json()
        later_response = api.post(project + "/preview", json=dict(draft, time=.875))
        assert later_response.status_code == 200, later_response.text
        later = later_response.json()
        assert later["image"] != zero["image"]
        assert later["time"] == .875
        assert api.post(project + "/preview", json=dict(draft, time=.875)).json() == later
        clip, path = render(api, project, draft)
        check_mp4(path, audio["duration_seconds"])
        png_draft = dict(draft, settings={"format": "png", "transparent": True})
        _, path = render(api, project, png_draft)
        with ZipFile(path) as frames:
            assert len(frames.namelist()) == math.ceil(audio["duration_seconds"] * 24)
            actual = Image.open(BytesIO(frames.read("frame-000021.png"))).convert("RGBA")
            png_preview = api.post(project + "/preview", json=dict(png_draft, time=.875)).json()
            expected = Image.open(BytesIO(base64.b64decode(png_preview["image"].split(",", 1)[1]))).convert("RGBA")
            assert actual.tobytes() == expected.tobytes()
            assert actual.getchannel("A").getextrema() == (0, 255)
        saved = api.put(project + "/draft", json=draft)
        assert saved.status_code == 200
        preset = api.post("/api/studio/presets", json={"name": "Власний рух", "composition": draft["composition"]}).json()
        assert preset["custom"]
        copy = api.post(project + "/copy", json=dict(draft, title="Копія"))
        assert copy.status_code == 201, copy.text
        copy_path = "/api/studio/" + copy.json()["id"]
        copied = api.get(copy_path + "/workspace").json()
        assert copied["assets"][0]["name"] == audio["name"]
        assert copied["draft"]["asset_id"] != draft["asset_id"]
        assert api.delete(copy_path + "/draft").status_code == 200
    with client() as api:
        reopened = api.get(project + "/workspace").json()
        assert reopened["draft"]["asset_id"] == audio["id"]
        assert any(a["id"] == clip["id"] for a in reopened["assets"])
        assert any(item["id"] == preset["id"] for item in api.get("/api/studio/presets").json())
        assert api.delete("/api/studio/presets/" + preset["id"]).status_code == 200


def test_all_visual_styles_render_deterministically(studio_env, tmp_path):
    analysis = analyze(tone(tmp_path / "pulse.wav"))
    seen = set()
    for template in templates():
        layout = Composition.model_validate({**template["composition"], "width": 320, "height": 240})
        with Renderer(layout, analysis) as renderer:
            start = renderer.frame(0).tobytes()
            later = renderer.frame(.875).tobytes()
            assert start != later
            assert later == renderer.frame(.875).tobytes()
            seen.add(layout.style)
    assert len(seen) == 7


def test_authorized_voice_profiles_persist_and_delete(studio_env, tmp_path):
    sample = media_input(tone(tmp_path / "reference.wav", 3.5))
    sample.update(name="Авторизований голос", language="uk")
    with client() as api:
        project = new_project(api)
        assert api.post(project + "/profiles", json=dict(sample, authorization_confirmed=False)).status_code == 422
        assert api.post(project + "/profiles", json=dict(sample, authorization_confirmed=1)).status_code == 422
        short = media_input(tone(tmp_path / "short.wav", 1))
        invalid = api.post(project + "/profiles", json=dict(short, language="en", authorization_confirmed=True))
        assert invalid.status_code == 422
        result = api.post(project + "/profiles", json=dict(sample, authorization_confirmed=True))
        assert result.status_code == 201, result.text
        profile = result.json()
        assert profile["languages"] == []
        assert profile["assigned"] and profile["authorization_confirmed"]
        assert len(profile["waveform"]) == 240
        other = new_project(api, "Another")
        assert not api.get(other + "/workspace").json()["profiles"][0]["assigned"]
        assert api.put(other + f"/profiles/{profile['id']}/assign").status_code == 200
    with client() as api:
        assert api.get(project + "/workspace").json()["profiles"][0]["name"] == sample["name"]
        assert api.get(other + "/workspace").json()["profiles"][0]["assigned"]
        path = Path(api.get(f"/api/studio/profiles/{profile['id']}/reference").json()["path"])
        with wave.open(str(path), "rb") as wav:
            assert wav.getnframes() == 84_000
        assert api.delete(f"/api/studio/profiles/{profile['id']}").status_code == 200
        assert not path.exists()


def test_model_install_requires_consent_and_excludes_presets(studio_env):
    model = model_install.manifest(holos.MANIFEST_PATH)
    paths = {entry["path"] for entry in model["assets"]}
    assert {"holos.pt", "voice_encoder.pt", "LICENSE"}.issubset(paths)
    assert "voices.pt" not in paths
    assert "voices.npz" not in paths
    with client() as api:
        registered = next(tool for tool in api.get("/api/studio/tools").json() if tool["id"] == holos.MODEL_ID)
        assert registered["state"] == "available" and not registered["ready"]
        assert registered["download_bytes"] == model_install.download_bytes(holos.MANIFEST_PATH)
        denied = api.post("/api/studio/tools/" + holos.MODEL_ID, json={"action": "install", "accepted_license": False})
        assert denied.status_code == 422
        voice = next(tool for tool in api.get("/api/studio/tools").json() if tool["id"] == openvoice.MODEL_ID)
        assert voice["reference_languages"] == ["en", "uk"]
        assert voice["ready_languages"] == [] and not voice["research_only"]
        denied = api.post("/api/studio/tools/" + openvoice.MODEL_ID, json={"action": "install", "accepted_license": False})
        assert denied.status_code == 422
        native = next(tool for tool in api.get("/api/studio/tools").json() if tool["id"] == zonos.MODEL_ID)
        assert native["download_bytes"] == 1_160_861_792 and not native["research_only"]
        assert set(native["reference_languages"]) == {"en", "uk"} and native["voices"][0]["id"] == "narrator"
        assert api.post("/api/studio/tools/" + zonos.MODEL_ID, json={"action": "install"}).status_code == 422


def test_multiple_voice_recordings_validate_and_persist(studio_env, tmp_path):
    clip = media_input(tone(tmp_path / "sample.wav", 5))
    with client() as api:
        project = new_project(api)
        request = dict(clip, name="Ukrainian profile", language="uk", authorization_confirmed=True,
                       end=3.5, additional_recordings=[dict(clip, start=1, end=5)])
        invalid = dict(request, additional_recordings=[dict(clip, content_base64="broken")])
        response = api.post(project + "/profiles", json=invalid)
        assert response.status_code == 422 and "Recording 2" in response.json()["detail"]
        assert api.get(project + "/workspace").json()["profiles"] == []
        response = api.post(project + "/profiles", json=request)
        assert response.status_code == 201, response.text
        profile = response.json()
        assert profile["reference_count"] == 2 and profile["reference_duration_seconds"] == pytest.approx(7.5, abs=.1)
        saved = studio_env / "studio" / "profiles" / profile["id"]
        assert (saved / "reference.wav").is_file() and (saved / "recordings" / "2.wav").is_file()
        assert api.delete("/api/studio/profiles/" + profile["id"]).status_code == 200
        assert not saved.exists()


def test_holos_unpack_rejects_paths_outside_runtime(tmp_path):
    archive = tmp_path / "runtime.whl"
    with ZipFile(archive, "w") as wheel:
        wheel.writestr("//unsafe.py", "print('hello')")
    with pytest.raises(RuntimeError, match="unsupported file path"):
        model_install.unpack_wheel(archive, tmp_path / "runtime", lambda: False)


def test_holos_resume_verifies_the_complete_download(tmp_path, monkeypatch):
    class RangeResponse(BytesIO):
        status = 206
        headers = {"Content-Range": "bytes 3-5/6"}

    def open_range(request, timeout):
        assert request.get_header("Range") == "bytes=3-"
        return RangeResponse(b"def")

    monkeypatch.setattr(model_install.urllib.request, "urlopen", open_range)
    target = tmp_path / "checkpoint.part"
    target.write_bytes(b"abc")
    part = {"url": "https://example.invalid/checkpoint", "size": 6, "sha256": hashlib.sha256(b"abcdef").hexdigest()}
    model_install.download(part, target, lambda _: None, lambda: False)
    assert target.read_bytes() == b"abcdef"


def installed(studio_env, identifiers):
    models = os.environ.get("AI_BLOGGER_STUDIO_TEST_MODELS")
    if not models:
        pytest.skip("Set AI_BLOGGER_STUDIO_TEST_MODELS to the directory of locally installed checkpoints.")
    for id in identifiers:
        source = Path(models) / id
        if not source.is_dir():
            pytest.skip(f"The real local test needs {id}.")
    for id in identifiers:
        shutil.copytree(Path(models) / id, studio_env / "voice" / id)


@pytest.mark.parametrize(("model", "voice", "language", "text"), [
    (MODEL_ID, "af_bella", "en", "A clear voice makes a good story easier to follow."),
    (LADA, "lada", "uk", " ".join(UKRAINIAN)),
])
def test_real_tts_to_synchronized_mp4(studio_env, model, voice, language, text):
    installed(studio_env, [model])
    with client() as api:
        project = new_project(api)
        start = time.monotonic()
        speech = api.post(project + "/speech", json=[dict(text=text, language=language, model_id=model, voice=voice, sample_rate=24000)])
        assert speech.status_code == 202, speech.text
        asset = wait_for_asset(api, project, speech.json()[0]["id"])
        elapsed = time.monotonic() - start
        assert asset["language"] == language
        assert asset["duration_seconds"] > 2
        assert asset["cues"][0]["start"] == 0
        assert asset["cues"][-1]["end"] == pytest.approx(asset["duration_seconds"], abs=1/24_000)
        if sys.platform != "win32":
            assert asset["peak_rss_bytes"] > 0
        assert app.state.voice.engine is None
        placement = {"size": asset["duration_seconds"], "elapsed": elapsed, "peak_rss": asset["peak_rss_bytes"]}
        print(f"{language}: {placement}", flush=True)
        wav = media_path(api, project, asset["id"], "wav")
        assert analyze(wav).rms.max() > .05
        mp3 = media_path(api, project, asset["id"], "mp3")
        assert mp3.stat().st_size > 500
        for format in ("srt", "ass"):
            captions = api.get(project + f"/assets/{asset['id']}/subtitles/{format}").json()["text"]
            for sentence, _ in sections(text):
                assert sentence in captions
        start = time.monotonic()
        clip, path = render(api, project, scene(api, asset["id"]))
        check_mp4(path, asset["duration_seconds"])
        print(f"{language}: MP4 render {time.monotonic()-start:.2f}s", flush=True)
        assert clip["source_id"] == asset["id"]


def test_real_en_clone_reference_to_motion_video(studio_env):
    installed(studio_env, [MODEL_ID, POCKET])
    with client() as api:
        project = new_project(api)
        source_request = dict(text="This synthetic voice makes a good reference. Recordings should have a clear voice and a steady rhythm.",
                              language="en", model_id=MODEL_ID, voice="am_adam")
        speech = api.post(project + "/speech", json=[source_request])
        assert speech.status_code == 202, speech.text
        original = wait_for_asset(api, project, speech.json()[0]["id"])
        path = media_path(api, project, original["id"])
        reference = dict(media_input(path), name="Synthetic acceptance reference", language="en", authorization_confirmed=True)
        saved = api.post(project + "/profiles", json=reference)
        assert saved.status_code == 201, saved.text
        id = saved.json()["id"]
        model = f"/api/studio/tools/{POCKET}"
        assert api.post(model, json={"action": "enable"}).status_code == 422
        assert api.post(model, json={"action": "enable", "accepted_license": True}).status_code == 200
    with client() as api:
        request = dict(text="A clear voice makes a good story easier to follow.", language="en", model_id=POCKET, voice=id)
        assert api.post(project + "/speech", json=[request]).status_code == 422
        start = time.monotonic()
        sent = api.post(project + "/speech", json=[dict(request, noncommercial_confirmed=True)])
        assert sent.status_code == 202, sent.text
        clone = wait_for_asset(api, project, sent.json()[0]["id"])
        assert clone["cloned"] and clone["research_only"] and clone["duration_seconds"] > 2
        print(f"English clone: {time.monotonic()-start:.2f}s for {clone['duration_seconds']:.2f}s, peak RSS {clone['peak_rss_bytes']}", flush=True)
        clip, path = render(api, project, scene(api, clone["id"]))
        assert clip["research_only"]
        check_mp4(path, clone["duration_seconds"])


@pytest.mark.skipif(not model_install.compatible_host(), reason="HolosTTS uses the macOS arm64 Python 3.12 runtime.")
def test_real_uk_clone_existing_profile_to_video(studio_env):
    installed(studio_env, [LADA])
    with client() as api:
        project = new_project(api)
        source_request = dict(text=" ".join(UKRAINIAN[:2]), language="uk", model_id=LADA, voice="lada")
        speech = api.post(project + "/speech", json=[source_request])
        assert speech.status_code == 202, speech.text
        original = wait_for_asset(api, project, speech.json()[0]["id"])
        path = media_path(api, project, original["id"])
        reference = dict(media_input(path), name="Синтетичний зразок", language="uk", authorization_confirmed=True)
        saved = api.post(project + "/profiles", json=reference)
        assert saved.status_code == 201, saved.text
        id = saved.json()["id"]
        assert saved.json()["languages"] == []
    installed(studio_env, [holos.MODEL_ID])
    with client() as api:
        assert api.get(project + "/workspace").json()["profiles"][0]["languages"] == ["uk"]
        test = api.post("/api/studio/tools/" + holos.MODEL_ID, json={"action": "test"})
        assert test.status_code == 200, test.text
        request = dict(text=UKRAINIAN[2], language="uk", model_id=holos.MODEL_ID, voice=id)
        sent = api.post(project + "/speech", json=[request])
        assert sent.status_code == 202, sent.text
        clone = wait_for_asset(api, project, sent.json()[0]["id"])
        assert clone["cloned"] and not clone["research_only"]
        assert clone["cues"][0]["text"] == UKRAINIAN[2]
        clip, path = render(api, project, scene(api, clone["id"]))
        assert not clip["research_only"]
        check_mp4(path, clone["duration_seconds"])


@pytest.mark.skipif(not model_install.compatible_host(), reason="OpenVoice uses the macOS arm64 Python 3.12 runtime.")
def test_real_openvoice_english_and_cross_language_export(studio_env):
    installed(studio_env, [MODEL_ID, openvoice.MODEL_ID])
    tool_path = "/api/studio/tools/" + openvoice.MODEL_ID
    english = "This voice sample demonstrates an authorized local workflow. The speaker has a calm and steady rhythm."

    def speak(api, project, model, voice, language, text):
        response = api.post(project + "/speech", json=[dict(text=text, language=language, model_id=model, voice=voice)])
        assert response.status_code == 202, response.text
        return wait_for_asset(api, project, response.json()[0]["id"])

    def make_profile(api, project, audio, language):
        sample = dict(media_input(media_path(api, project, audio["id"])), language=language,
                      name=f"Synthetic {language} test reference", authorization_confirmed=True)
        response = api.post(project + "/profiles", json=sample)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    with client() as api:
        project = new_project(api)
        english_audio = speak(api, project, MODEL_ID, "am_adam", "en", english)
        english_reference = make_profile(api, project, english_audio, "en")
        blocked = api.post(project + "/speech", json=[dict(text=UKRAINIAN[0], language="uk", model_id=openvoice.MODEL_ID,
                                                         voice=english_reference)])
        assert blocked.status_code == 409 and "Piper Lada" in blocked.json()["detail"]
        clone = speak(api, project, openvoice.MODEL_ID, english_reference, "en", english)
        assert clone["cloned"] and not clone["research_only"]
        assert clone["duration_seconds"] > 4
    installed(studio_env, [LADA])
    with client() as api:
        uk_audio = speak(api, project, LADA, "lada", "uk", " ".join(UKRAINIAN[:2]))
        uk_reference = make_profile(api, project, uk_audio, "uk")
        for profile in api.get(project + "/workspace").json()["profiles"]:
            assert profile["languages"] == ["en", "uk"]
        probe = api.post(tool_path, json={"action": "test"})
        assert probe.status_code == 200, probe.text

        for language, ref, text in [("uk", english_reference, UKRAINIAN[2]), ("en", uk_reference, english)]:
            tick = time.monotonic()
            clone = speak(api, project, openvoice.MODEL_ID, ref, language, text)
            assert clone["cloned"] and clone["language"] == language and clone["voice"] == ref and not clone["research_only"]
            assert clone["duration_seconds"] > 2
            print(f"OpenVoice {language}: {clone['duration_seconds']:.2f}s audio in {time.monotonic()-tick:.2f}s, "
                  f"{clone['peak_rss_bytes']/1e6:.0f} MB RSS", flush=True)
            clip, path = render(api, project, scene(api, clone["id"]))
            assert not clip["research_only"]
            check_mp4(path, clone["duration_seconds"])


def test_jobs_wait_for_this_apps_queue_worker(studio_env, tmp_path):
    with ExitStack() as previous_app:
        previous_app.enter_context(ProjectStudio(studio_env, None, None).queue_lease())
        with client() as api:
            project = new_project(api)
            imported = api.post(project + "/import/audio", json=media_input(tone(tmp_path / "queue.wav", 2)))
            assert imported.status_code == 201, imported.text
            draft = scene(api, imported.json()["id"])
            rejected = api.post(project + "/render", json=draft)
            assert rejected.status_code == 409, rejected.text
            assert "Another AI Blogger Studio instance" in rejected.json()["detail"]
            assert api.get(project + "/workspace").json()["jobs"] == []

            previous_app.close()
            queued = api.post(project + "/render", json=draft)
            assert queued.status_code == 202, queued.text
            wait_for_asset(api, project, queued.json()["id"])


def test_render_queue_recovers_and_cancels(studio_env, tmp_path):
    from backend.api.main import database_connection
    from uuid import uuid4

    with client() as api:
        project = new_project(api)
        response = api.post(project + "/import/audio", json=media_input(tone(tmp_path / "queue.wav", 2)))
        assert response.status_code == 201, response.text
        asset = response.json()
        draft = scene(api, asset["id"])
        large = {**draft, "composition": {**draft["composition"], "width": 1920, "height": 1080}}
        response = api.post(project + "/render", json=large)
        assert response.status_code == 202
        job = response.json()["id"]
        assert api.post(project + f"/jobs/{job}/cancel").status_code == 200
        for _ in range(100):
            state = api.get(project + "/workspace").json()
            if next(j["state"] for j in state["jobs"] if j["id"] == job) == "cancelled":
                break
            time.sleep(.05)
        else:
            pytest.fail("Cancelled render did not reach its terminal state.")
        assert all(asset["id"] != job for asset in state["assets"])
    interrupted = str(uuid4())
    with database_connection() as db:
        db.execute("""INSERT INTO studio_jobs (id, project_id, kind, input_json, state, created_at)
                      VALUES (?, ?, 'render', ?, 'running', ?)""",
                   (interrupted, project.rsplit("/", 1)[1], json.dumps(draft), "2026-11-16T12:00:00Z"))
    with client() as api:
        completed = wait_for_asset(api, project, interrupted)
        check_mp4(media_path(api, project, completed["id"]), asset["duration_seconds"])


def test_webm_alpha_and_looping_video_background(studio_env, tmp_path):
    ffmpeg = _binary("ffmpeg")
    codecs = subprocess.check_output([ffmpeg, "-hide_banner", "-encoders"], text=True)
    if "libvpx-vp9" not in codecs or "libopus" not in codecs:
        pytest.skip("FFmpeg lacks VP9 or Opus.")
    background = tmp_path / "фон.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=0.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(background)], check=True)
    with client() as api:
        project = new_project(api)
        audio = api.post(project + "/import/audio", json=media_input(tone(tmp_path / "music.wav"))).json()
        video = api.post(project + "/import/background", json=media_input(background))
        assert video.status_code == 201, video.text
        draft = scene(api, audio["id"], "minimal-waveform")
        draft["composition"]["backdrop"] = video.json()["id"]
        frame = api.post(project + "/preview", json=dict(draft, time=.9))
        assert frame.status_code == 200, frame.text
        # Drives the background decoder through a loop because audio is twice as long as the clip.
        _, path = render(api, project, draft)
        check_mp4(path, audio["duration_seconds"])
        settings = {"format": "webm", "transparent": True}
        _, webm = render(api, project, dict(draft, settings=settings))
        probe = json.loads(subprocess.check_output([_binary("ffprobe"), "-v", "error", "-show_streams",
                                                   "-of", "json", str(webm)]))
        assert probe["streams"][0]["codec_name"] == "vp9"
        assert any(key.lower() == "alpha_mode" and value == "1" for key, value in probe["streams"][0]["tags"].items())
        assert probe["streams"][1]["codec_name"] == "opus"
