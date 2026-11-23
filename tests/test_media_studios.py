"""Acceptance coverage for the shared image library and Video Creator pipeline."""

import base64
import json
import math
import os
import shutil
import subprocess
import threading
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

from backend.avatar.service import _binary
from backend.scenes.illustrated_render import (
    assemble, caption_font, fit_narration, narration_samples, narration_tempo, render_scene, shorten_narrations,
)
from backend.studio import flux, holos, illustrated, model_install, video
from backend.studio.audio import write_wave
from backend.studio.image_worker import InvalidPixels, prompt_with_style, sdxl_image
from backend.studio.images import ImageRequest
from backend.studio.speech import LADA
from backend.studio.tools import StudioTools
from test_studio import client, installed, media_input, media_path, new_project, studio_env, wait_for_asset


def test_sdxl_rejects_nonfinite_pixels_before_png_conversion():
    processor = Mock()
    pixels = np.full((1, 16, 16, 3), .5, dtype=np.float32)
    processor.numpy_to_pil.return_value = [Image.new("RGB", (16, 16))]
    assert sdxl_image(processor, pixels).size == (16, 16)
    processor.numpy_to_pil.reset_mock()
    pixels[0, 0, 0] = np.nan
    with pytest.raises(InvalidPixels, match="could not decode"):
        sdxl_image(processor, pixels)
    processor.numpy_to_pil.assert_not_called()


def test_style_uses_a_bounded_prefix_before_a_long_prompt():
    tokenizer = Mock(model_max_length=77)
    tokenizer.num_special_tokens_to_add.return_value = 2
    tokenizer.encode.return_value = [23, 24, 25]
    tokenizer.decode.return_value = "golden cinematic lighting"
    shot = "shepherd and flock in the hills " * 40
    assert prompt_with_style(tokenizer, shot, "") == shot
    tokenizer.encode.assert_not_called()
    assert prompt_with_style(tokenizer, shot, "long style") == f"golden cinematic lighting, {shot}"
    tokenizer.encode.assert_called_once_with("long style", add_special_tokens=False, truncation=True, max_length=25)


def test_flux_defaults_to_four_steps_for_text_and_reference():
    assert ImageRequest(model_id=flux.ID, prompt="A hillside").steps == 4
    edited = ImageRequest(model_id=flux.ID, mode="variation", prompt="A hillside", reference_id=uuid4(), strength=.2)
    assert edited.steps == 4
    assert ImageRequest(model_id=flux.ID, prompt="A hillside", steps=8).steps == 8


def image_upload(api, project, index):
    body = BytesIO()
    Image.new("RGB", (640, 360), (55 + index * 25, 85, 115 - index * 15)).save(body, format="PNG")
    request = {"name": f"frame-{index}.png", "content_base64": base64.b64encode(body.getvalue()).decode()}
    result = api.post(project + "/import/image", json=request)
    assert result.status_code == 201, result.text
    return result.json(), request


def audio_upload(api, project, path, frequency, duration):
    times = np.arange(round(duration * 48000)) / 48000
    write_wave(path, .43 * np.sin(2 * np.pi * frequency * times), 48000, normalize=False)
    sent = api.post(project + "/import/audio", json=media_input(path))
    assert sent.status_code == 201, sent.text
    return sent.json()


def ffprobe(path):
    return json.loads(subprocess.check_output([_binary("ffprobe"), "-v", "error",
                                              "-show_streams", "-show_format", "-of", "json", str(path)]))


@pytest.mark.parametrize("target", [None, 3.8])
def test_illustrated_video_reveals_ukrainian_phrase_at_its_cue_and_joins_at_30_fps(tmp_path, target):
    narrations = []
    font = caption_font()
    cue = {"start": .40, "end": 1.45, "text": "Її ґанок у тиші."}
    for index, duration in enumerate((1.54, 1.61), 1):
        scene = tmp_path / f"scene-{index:02d}"
        scene.mkdir()
        Image.new("RGB", (1024, 576), "#fff2d8").save(scene / "scene.png")
        times = np.arange(round(duration * 24000)) / 24000
        source = scene / "narration.wav"
        write_wave(source, .3 * np.sin(2 * np.pi * 440 * times), 24000, normalize=False)
        narrations.append(source)

    tempo = narration_tempo(narrations, target) if target is not None else 1.0
    fitted = [fit_narration(source.parent, source, tempo, [cue]) for source in narrations]
    lengths = [render_scene(source.parent, "Її ґанок", "left", font, cues, narration=source)
               for source, cues in fitted]
    assemble(tmp_path, lengths, "Українська історія", narrations=[source for source, _ in fitted])
    path = tmp_path / "video.mp4"
    info = ffprobe(path)
    streams = info["streams"]
    assert streams[0]["avg_frame_rate"] == "30/1"
    assert int(streams[0]["nb_frames"]) == sum(lengths)
    assert streams[1]["tags"]["language"] == "ukr"
    if target is not None:
        assert float(info["format"]["duration"]) == pytest.approx(target, abs=.08)
        assert all(cues[0]["start"] < cue["start"] for _, cues in fitted)

    def dark_pixels(timestamp):
        data = subprocess.check_output([_binary("ffmpeg"), "-v", "error", "-ss", timestamp, "-i", str(path),
                                        "-vf", "crop=500:350:82:184", "-frames:v", "1",
                                        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"])
        return np.count_nonzero(np.frombuffer(data, dtype=np.uint8) < 100)

    assert dark_pixels("0.20") == 0
    assert dark_pixels("1.30") > 500


def test_illustrated_video_shortens_speech_at_sentence_boundaries(tmp_path):
    source = tmp_path / "narration.wav"
    times = np.arange(6 * 24000) / 24000
    write_wave(source, .3 * np.sin(2 * np.pi * 440 * times), 24000, normalize=False)
    cues = [
        {"start": 0, "end": 1.8, "text": "Початок."},
        {"start": 2, "end": 3.8, "text": "Подробиця."},
        {"start": 4, "end": 6, "text": "Закінчення."},
    ]
    [(shortened, timings)] = shorten_narrations([source], [cues], 4.5)
    assert timings == [cues[0], {"start": 2, "end": 4, "text": "Закінчення."}]
    assert narration_samples(shortened) == 4 * 24000
    assert narration_samples(source) == 6 * 24000


@pytest.mark.parametrize("block_pauses", [False, True])
def test_illustrated_auto_duration_follows_narration(studio_env, monkeypatch, block_pauses):
    pipeline = illustrated.pipeline
    from backend.studio.speech import SpeechRequest
    configured = SpeechRequest(text="Ранкова молитва", language="uk", model_id=LADA, voice="lada",
                               title_pause_ms=600, block_pause_ms=900, paragraph_pause_ms=500) if block_pauses else None
    monkeypatch.setattr(illustrated, "validate", lambda *_: configured)
    monkeypatch.setattr(flux, "available", lambda: True)
    monkeypatch.setattr(flux, "installed", lambda _cache: True)
    model = pipeline.writing_dir(studio_env) / pipeline.WRITING_MODEL.name
    model.parent.mkdir(parents=True)
    model.touch()

    def script(_topic, seconds, scene_count, *_args, **_options):
        assert seconds is None
        assert _options["text_words"] == (200 if block_pauses else None)
        scene = pipeline.Scene(narration="Почни день з молитви. Прислухайся до тиші.",
                               caption="Почни день", illustration="A cup on a windowsill at sunrise")
        return pipeline.Script(title="Ранкова молитва", scenes=[scene] * scene_count)

    def narrate(directory, scene, *_args, **_options):
        if directory.name == "scene-01":
            assert " ".join(scene.narration.split()).startswith("Ранкова молитва. Почни день")
        duration = 1.5 if directory.name == "scene-01" else 2.2
        samples = np.arange(round(duration * 24000)) / 24000
        write_wave(directory / "narration.wav", .3 * np.sin(2 * np.pi * 440 * samples), 24000)
        return [{"text": scene.narration, "start": 0, "end": duration}]

    def illustrate_scene(directory, *_args, **_options):
        Image.new("RGB", (1024, 576), "#fff2d8").save(directory / "scene.png")

    cover_attempts = 0

    def cover(_kind, _cache, _download, *, run_dir, **options):
        nonlocal cover_attempts
        cover_attempts += 1
        if cover_attempts == 1:
            raise RuntimeError("Simulated thumbnail generation failure.")
        assert "Epic biblical" in options["prompt"]
        assert "A cup on a windowsill" in options["prompt"]
        Image.new("RGB", (1024, 576), "#f9db91").save(run_dir / "scene.png")
        return {"prompt": options["prompt"], "seed": options["seed"], "weights": {}}

    monkeypatch.setattr(pipeline, "write_script", script)
    monkeypatch.setattr(pipeline, "synthesize", narrate)
    monkeypatch.setattr(pipeline, "illustrate", illustrate_scene)
    monkeypatch.setattr(pipeline, "benchmark", cover)
    with client() as api:
        project = new_project(api)
        other = new_project(api, "Other project")
        response = api.post(project + "/illustrated/render",
                            json={"topic": "Ранкова молитва", "scenes": 2, "style": "biblical",
                                  "text_words": 200 if block_pauses else None})
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            workspace = api.get(project + "/workspace").json()
            job = next(job for job in workspace["jobs"] if job["id"] == job_id)
            if job["state"] == "failed":
                break
            time.sleep(.03)
        assert job["state"] == "failed" and workspace["assets"] == []
        assert api.post(project + f"/jobs/{job_id}/retry").status_code == 202
        asset = wait_for_asset(api, project, job_id)
        assert cover_attempts == 2
        expected_frames = 111 if block_pauses else 139
        assert asset["duration_seconds"] == pytest.approx(expected_frames / 30)
        assert asset["transcript"].count("Прислухайся до тиші.") == 2
        assert asset["transcript"].startswith("Ранкова молитва.")
        assert asset["transcript"].count("Ранкова молитва.") == 1
        info = ffprobe(media_path(api, project, asset["id"]))
        assert int(info["streams"][0]["nb_frames"]) == expected_frames
        assert asset["thumbnail"]
        thumbnail = media_path(api, project, asset["id"], "thumbnail")
        with Image.open(thumbnail) as image:
            assert image.size == (1280, 720) and image.format == "PNG"
            assert np.count_nonzero(np.asarray(image)[:, 576:, :].mean(axis=2) < 100) > 200
        assert api.get(other + f"/assets/{asset['id']}/file/thumbnail").status_code == 404


def test_illustrated_jobs_can_queue_and_retry_while_a_video_is_running(studio_env, monkeypatch):
    monkeypatch.setattr(illustrated, "validate", lambda *_: None)
    started = threading.Event()
    release = threading.Event()
    rendered = []

    def generate(_studio, _project, _job, request):
        rendered.append(request.topic)
        if request.topic == "First video":
            started.set()
            assert release.wait(timeout=10), "The test never released the running job."

    monkeypatch.setattr(illustrated, "generate", generate)
    with client() as api:
        project = new_project(api)

        def submit(topic):
            response = api.post(project + "/illustrated/render", json={"topic": topic})
            assert response.status_code == 202, response.text
            assert response.json()["topic"] == topic
            return response.json()["id"]

        try:
            first = submit("First video")
            assert started.wait(timeout=5)
            second = submit("Second video")
            third = submit("Third video")
            jobs = {job["id"]: job for job in api.get(project + "/workspace").json()["jobs"]}
            assert [jobs[id]["state"] for id in (first, second, third)] == ["running", "queued", "queued"]
            assert api.post(project + f"/jobs/{second}/cancel").json()["state"] == "cancelled"
            retry = api.post(project + f"/jobs/{second}/retry")
            assert retry.status_code == 202 and retry.json()["state"] == "queued"
        finally:
            release.set()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            jobs = api.get(project + "/workspace").json()["jobs"]
            if all(job["state"] == "completed" for job in jobs):
                break
            time.sleep(.03)
        assert len(jobs) == 3 and all(job["state"] == "completed" for job in jobs)
        assert rendered == ["First video", "Second video", "Third video"]


def test_illustrated_job_retries_with_the_same_checkpoint_and_publishes_to_its_project(studio_env, monkeypatch):
    monkeypatch.setattr(illustrated, "validate", lambda _studio, _request, _project: None)

    def produce(args, *, check, report, voice_lease):
        check()
        args.output.mkdir(exist_ok=True)
        checkpoint = args.output / "script.json"
        if not checkpoint.exists():
            checkpoint.write_text(json.dumps({"title": "Ранкова молитва", "scenes": []}), encoding="utf-8")
            report("Writing Ukrainian scenes.", .03)
            raise RuntimeError("Simulated interruption after saving the script.")
        narration = np.arange(24000) / 24000
        write_wave(args.output / "narration.wav", .3 * np.sin(2 * np.pi * 440 * narration), 24000)
        (args.output / "transcript.txt").write_text("Дякую за цей день.", encoding="utf-8")
        Image.new("RGB", (1280, 720), "#f9db91").save(args.output / "thumbnail.png")
        result = args.output / "video.mp4"
        subprocess.run([_binary("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=s=1280x720:r=30:d=1",
                        "-i", str(args.output / "narration.wav"), "-c:v", "libx264", "-c:a", "aac",
                        str(result)], check=True)
        return result

    monkeypatch.setattr(illustrated.pipeline, "produce", produce)
    with client() as api:
        project, other = new_project(api), new_project(api, "Other project")
        response = api.post(project + "/illustrated/render", json={"topic": "Ранкова молитва", "seconds": 24})
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = api.get(project + "/workspace").json()["jobs"][0]
            if job["state"] == "failed":
                break
            time.sleep(.03)
        assert job["state"] == "failed"
        assert api.post(other + f"/jobs/{job_id}/retry").status_code == 404
        retry = api.post(project + f"/jobs/{job_id}/retry")
        assert retry.status_code == 202 and retry.json()["id"] == job_id
        asset = wait_for_asset(api, project, job_id)
        assert asset["source_type"] == "illustrated_video"
        assert asset["transcript"] == "Дякую за цей день."
        path = media_path(api, project, job_id)
        assert [stream["codec_name"] for stream in ffprobe(path)["streams"]] == ["h264", "aac"]


def test_morning_narrators_list_authorized_clones_only_for_assigned_projects(studio_env, tmp_path, monkeypatch):
    monkeypatch.setattr(flux, "available", lambda: True)
    monkeypatch.setattr(StudioTools, "ready_languages", lambda _self, model: ["uk"] if model == holos.MODEL_ID else [])
    recording = tmp_path / "morning-reference.wav"
    times = np.arange(84_000) / 24000
    write_wave(recording, .3 * np.sin(2 * np.pi * 440 * times), 24000)
    with client() as api:
        project, other = new_project(api), new_project(api, "Other project")
        saved = api.post(project + "/profiles", json=dict(media_input(recording), name="Мій голос",
                         language="uk", authorization_confirmed=True))
        assert saved.status_code == 201, saved.text
        assert api.get(other + "/illustrated/narrators").json() == []
        assert api.get(project + "/illustrated/narrators").json() == [{
            "model_id": holos.MODEL_ID, "voice": saved.json()["id"], "name": "Мій голос · HolosTTS",
            "languages": ["uk"], "cloned": True,
        }]


def queue_video(api, project, timeline, **options):
    queued = api.post(project + "/video/render", json={"timeline": timeline, **options})
    assert queued.status_code == 202, queued.text
    return wait_for_asset(api, project, queued.json()["id"], timeout=120)


def test_five_images_persistent_timeline_burned_ukrainian_captions_and_ducking(studio_env, tmp_path):
    text = "Українська історія · Її ґанок"
    with client() as api:
        project = new_project(api)
        other = new_project(api, "Other project")
        scenes = []
        for index in range(5):
            image, payload = image_upload(api, project, index)
            assert api.post(project + "/import/image", json=payload).json()["id"] == image["id"]
            scenes.append({"id": str(uuid4()), "image_id": image["id"], "animation": "ken_burns",
                           "duration": 6.4, "transition": ["cut", "crossfade", "slide_left", "cut", "wipe"][index],
                           "overlap": 2/3})
        narration = audio_upload(api, project, tmp_path / "voice.wav", 1000, 2.0)
        music = audio_upload(api, project, tmp_path / "music.wav", 440, 2.3)
        scenes[0].update(narration_id=narration["id"], captions=[{"start": .3, "end": 1.8, "text": text}])
        timeline = {"width": 480, "height": 270, "fps": 30, "scenes": scenes,
                    "audio": [{"asset_id": music["id"], "gain": .35, "loop": True}], "duck": True}
        failed = api.put(other + "/video", json=timeline)
        assert failed.status_code == 404, failed.text
        saved = api.put(project + "/video", json=timeline)
        assert saved.status_code == 200, saved.text
        assert saved.json()["revision"] == 1
        assert api.put(project + "/video", json=timeline).status_code == 409
        invalid = {**timeline, "scenes": [{**scenes[0], "duration": 1}]}
        refused = api.post(project + "/video/render", json={"timeline": invalid})
        assert refused.status_code == 422 and "longer than the scene" in refused.json()["detail"]

    with client() as api:
        timeline = api.get(project + "/video").json()
        assert timeline["scenes"][0]["captions"][0]["text"] == text
        asset = queue_video(api, project, timeline)
        path = media_path(api, project, asset["id"])
        streams = ffprobe(path)["streams"]
        video, audio = streams
        assert (video["codec_name"], video["width"], video["height"], video["avg_frame_rate"]) == ("h264", 480, 270, "30/1")
        assert audio["codec_name"] == "aac"
        assert float(video["duration"]) == pytest.approx(30, abs=.035)
        assert float(audio["duration"]) == pytest.approx(30, abs=.06)
        for format in ("srt", "ass", "vtt"):
            result = api.get(project + f"/assets/{asset['id']}/subtitles/{format}")
            assert text in result.json()["text"]
        raw = subprocess.check_output([_binary("ffmpeg"), "-v", "error", "-ss", "1", "-i", str(path),
                                       "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
        pixels = np.frombuffer(raw, dtype=np.uint8).reshape(270, 480, 3)
        assert ((pixels[170:] > 220).all(axis=2)).sum() > 60
        samples = subprocess.check_output([_binary("ffmpeg"), "-v", "error", "-i", str(path), "-vn",
                                           "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"])
        pcm = np.frombuffer(samples, dtype="<f4")
        wave = np.exp(2j * np.pi * 440 * np.arange(48000) / 48000)
        during = abs(np.dot(pcm[24000:72000], wave))
        after = abs(np.dot(pcm[144000:192000], wave))
        assert during < .55 * after
        preview = queue_video(api, project, timeline, preview=True, scene_id=timeline["scenes"][0]["id"])
        assert preview["preview"] and preview["duration_seconds"] == pytest.approx(6.4)
        assert len(tuple((studio_env / "studio" / project.rsplit("/", 1)[-1] / "video-cache").glob("*.mp4"))) == 5


def test_vertical_mov_with_pcm_and_image_profile_history(studio_env):
    with client() as api:
        project = new_project(api)
        image, _ = image_upload(api, project, 0)
        profile_id = str(uuid4())
        payload = {"id": profile_id, "kind": "character", "name": "Олена", "description": "A traveler",
                   "reference_ids": [image["id"]], "approved_id": image["id"]}
        assert api.post(project + "/image-profiles", json=payload).json()["version"] == 1
        assert api.post(project + "/image-profiles", json={**payload, "clothing": "An embroidered coat"}).json()["version"] == 2
        assert api.get(project + f"/image-profiles/{profile_id}").json()[1]["description"] == "A traveler"
        assert api.patch(project + f"/image/{image['id']}", json={"approval": "approved", "favorite": True}).json()["favorite"]
        timeline = {"width": 1080, "height": 1920, "fps": 25,
                    "scenes": [{"id": str(uuid4()), "image_id": image["id"], "duration": .8, "animation": "still"}]}
        asset = queue_video(api, project, timeline, container="mov", audio_codec="pcm", captions="none")
        streams = ffprobe(media_path(api, project, asset["id"]))["streams"]
        assert (streams[0]["width"], streams[0]["height"], streams[0]["avg_frame_rate"]) == (1080, 1920, "25/1")
        assert streams[1]["codec_name"] == "pcm_s16le"


def test_image_upscale_and_thumbnail_without_diffusers(studio_env):
    with client() as api:
        project = new_project(api)
        image, _ = image_upload(api, project, 0)
        reply = api.post(project + "/image/generate", json={"mode": "upscale", "reference_id": image["id"]})
        assert reply.status_code == 202, reply.text
        result = wait_for_asset(api, project, reply.json()["id"])
        assert result["model_id"] == "lanczos" and result["thumbnail"]
        with Image.open(media_path(api, project, result["id"])) as scale:
            assert scale.size == (1280, 720)
        with Image.open(media_path(api, project, result["id"], "thumbnail")) as tile:
            assert tile.size == (360, 203)
        invalid = api.post(project + "/image/generate", json={"mode": "inpaint", "prompt": "Forest",
                                                              "reference_id": image["id"]})
        assert invalid.status_code == 422


def test_cancel_video_job_and_retry(studio_env, monkeypatch):
    scene_file = video.scene_file
    rendering = threading.Event()
    release = threading.Event()

    def slow_scene(studio, project, scene, spec, frames, work, check):
        rendering.set()
        while not release.wait(.03):
            check()
        return scene_file(studio, project, scene, spec, frames, work, check)

    monkeypatch.setattr(video, "scene_file", slow_scene)
    with client() as api:
        project = new_project(api)
        image, _ = image_upload(api, project, 0)
        timeline = {"width": 320, "height": 180, "scenes": [
            {"id": str(uuid4()), "image_id": image["id"], "duration": .5}]}
        response = api.post(project + "/video/render", json={"timeline": timeline})
        assert response.status_code == 202, response.text
        id = response.json()["id"]
        try:
            assert rendering.wait(5)
            assert api.post(project + f"/jobs/{id}/cancel").status_code == 200
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                workspace = api.get(project + "/workspace").json()
                job = next(job for job in workspace["jobs"] if job["id"] == id)
                if job["state"] == "cancelled":
                    break
                time.sleep(.05)
            assert job["state"] == "cancelled" and all(asset["id"] != id for asset in workspace["assets"])
        finally:
            release.set()
        retry = api.post(project + f"/jobs/{id}/retry")
        assert retry.status_code == 202, retry.text
        exported = wait_for_asset(api, project, retry.json()["id"])
        assert exported["duration_seconds"] == .5


@pytest.mark.skipif(not model_install.compatible_host(), reason="HolosTTS requires Python 3.12 on Apple Silicon.")
def test_real_holos_profile_in_video_creator(studio_env):
    installed(studio_env, [LADA, holos.MODEL_ID])
    text = "Українська мова має красиве звучання."
    with client() as api:
        project = new_project(api)
        image, _ = image_upload(api, project, 0)
        sent = api.post(project + "/speech", json=[{"text": (text + " ") * 3, "language": "uk", "model_id": LADA, "voice": "lada"}])
        assert sent.status_code == 202, sent.text
        voice = wait_for_asset(api, project, sent.json()[0]["id"])
        profile = api.post(project + "/profiles", json={**media_input(media_path(api, project, voice["id"])),
                           "name": "Authorized synthetic test sample", "language": "uk", "authorization_confirmed": True})
        assert profile.status_code == 201, profile.text
        sent = api.post(project + "/speech", json=[{"text": text, "language": "uk", "model_id": holos.MODEL_ID,
                                                    "voice": profile.json()["id"]}])
        assert sent.status_code == 202, sent.text
        clone = wait_for_asset(api, project, sent.json()[0]["id"], timeout=180)
        assert clone["cloned"] and clone["cues"][0]["text"] == text
        timeline = {"width": 360, "height": 640, "fps": 30, "scenes": [{"id": str(uuid4()),
          "image_id": image["id"], "narration_id": clone["id"],
          "duration": math.ceil(clone["duration_seconds"] * 30) / 30, "captions": clone["cues"]}]}
        exported = queue_video(api, project, timeline)
        streams = ffprobe(media_path(api, project, exported["id"]))["streams"]
        assert streams[1]["codec_name"] == "aac"
        assert float(streams[1]["duration"]) == pytest.approx(clone["duration_seconds"], abs=.075)


def test_real_native_checkpoint_into_video(studio_env, monkeypatch):
    source = os.getenv("AI_BLOGGER_TEST_NATIVE_IMAGES")
    if not source:
        pytest.skip("Set AI_BLOGGER_TEST_NATIVE_IMAGES to a directory with the pinned SD1.5 model and image runtime.")
    source = Path(source)
    checkpoint = source / "image-models" / "sd15"
    target = studio_env / "image-models" / "sd15"
    target.parent.mkdir(parents=True)
    # Clone files on APFS if available. The fixture remains isolated from the original install.
    try:
        subprocess.run(["cp", "-cR", str(checkpoint), str(target)], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        shutil.copytree(checkpoint, target)
    monkeypatch.setenv("AI_BLOGGER_IMAGE_SITE", str(source / "image-runtime"))
    device = os.getenv("AI_BLOGGER_TEST_NATIVE_DEVICE", "auto")
    with client() as api:
        project = new_project(api)
        status = api.get("/api/studio/image/catalog")
        assert status.json()["runtime_ready"], status.text
        sent = api.post(project + "/image/generate", json={"model_id": "sd15", "device": device, "width": 384, "height": 256,
                           "steps": 6, "seed": 102, "prompt": "Watercolor painting of a mountain cabin by a lake, warm sunset"})
        assert sent.status_code == 202, sent.text
        asset = wait_for_asset(api, project, sent.json()["id"], timeout=600)
        assert asset["seed"] == 102 and asset["thumbnail"]
        expected_device = status.json()["device"][0] if device == "auto" else device
        assert asset["inference"]["device"] == expected_device
        print(f"Verified Stable Diffusion 1.5 text-to-image on {expected_device.upper()}")
        with Image.open(media_path(api, project, asset["id"])) as image:
            assert image.size == (384, 256)
            assert len(set(image.convert("RGB").getdata())) > 100
        scene = {"id": str(uuid4()), "image_id": asset["id"], "duration": 1.1}
        exported = queue_video(api, project, {"width": 640, "height": 360, "scenes": [scene]})
        assert ffprobe(media_path(api, project, exported["id"]))["streams"][0]["codec_name"] == "h264"
        variation = api.post(project + "/image/generate", json={"mode": "variation", "reference_id": asset["id"],
                            "device": device, "width": 256, "height": 256, "steps": 5, "seed": 11,
                            "prompt": "Watercolor landscape, evening light"})
        assert variation.status_code == 202, variation.text
        variation_asset = wait_for_asset(api, project, variation.json()["id"], timeout=600)
        assert variation_asset["reference_ids"] == [asset["id"]]
        mask = BytesIO()
        guide = Image.new("L", (384, 256), 0)
        guide.paste(255, (90, 30, 240, 200))
        guide.save(mask, format="PNG")
        uploaded_mask = api.post(project + "/import/image", json={
            "name": "mask.png", "content_base64": base64.b64encode(mask.getvalue()).decode()})
        assert uploaded_mask.status_code == 201
        mask_id = uploaded_mask.json()["id"]
        inpaint = api.post(project + "/image/generate", json={"mode": "inpaint", "reference_id": asset["id"],
                           "mask_id": mask_id, "device": device, "width": 384, "height": 256, "steps": 5,
                           "seed": 74, "prompt": "Watercolor landscape with a bright white cloud"})
        assert inpaint.status_code == 202, inpaint.text
        inpainted = wait_for_asset(api, project, inpaint.json()["id"], timeout=600)
        assert inpainted["reference_ids"] == [asset["id"], mask_id]
        print(f"Verified image-to-image and masked inpainting on {expected_device.upper()}")


def test_real_flux_text_and_reference_on_metal(studio_env):
    cached = os.getenv("AI_BLOGGER_TEST_FLUX_CACHE")
    if not cached or not flux.available():
        pytest.skip("FLUX acceptance needs cached scene weights and Metal on macOS 26 or later.")
    cache = Path(cached)
    target = studio_env / "scene_models"
    target.mkdir(parents=True)
    for artifact in flux.ARTIFACTS:
        source = cache / artifact.name
        try:
            subprocess.run(["cp", "-c", str(source), str(target / artifact.name)], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            shutil.copyfile(source, target / artifact.name)

    with client() as api:
        project = new_project(api)
        model = next(row for row in api.get("/api/studio/image/catalog").json()["models"] if row["id"] == flux.ID)
        assert model["installed"] and model["available"]
        install = api.post("/api/studio/image/model", json={"model_id": flux.ID, "action": "install"})
        assert install.status_code == 422
        verified = api.post("/api/studio/image/model", json={"model_id": flux.ID, "action": "test"})
        assert verified.status_code == 200, verified.text
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = api.get("/api/studio/image/catalog").json()["install"]
            if state["state"] != "validating":
                break
            time.sleep(.4)
        assert state["state"] == "completed", state

        payload = {"model_id": flux.ID, "prompt": "Wide film still of a shepherd and a flock on a hillside at dawn",
                   "width": 768, "height": 432, "seed": 2252357189192815}
        invalid = api.post(project + "/image/generate", json={**payload, "width": 760})
        assert invalid.status_code == 422
        queued = api.post(project + "/image/generate", json=payload)
        assert queued.status_code == 202, queued.text
        original = wait_for_asset(api, project, queued.json()["id"], timeout=240)
        assert original["generation"]["steps"] == 4 and original["thumbnail"]
        path = media_path(api, project, original["id"])
        with Image.open(path) as image:
            assert image.size == (768, 432)
        edit = api.post(project + "/image/generate", json={**payload, "mode": "variation",
                        "prompt": "Change dawn light to cool overcast daylight", "reference_id": original["id"]})
        assert edit.status_code == 202, edit.text
        variant = wait_for_asset(api, project, edit.json()["id"], timeout=240)
        assert variant["reference_ids"] == [original["id"]]
        assert variant["inference"]["engine"] == original["inference"]["engine"]
        assert variant["digest"] != original["digest"]
