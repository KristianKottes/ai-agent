"""Persistent topic batches, editing, cancellation, and reviewed video narration."""

import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from backend.scenes import illustrated_video as pipeline
from backend.studio import export, flux, illustrated, topic_library
from backend.studio.audio import write_wave
from backend.studio.service import ProjectStudio
from test_studio import client, new_project, studio_env, wait_for_asset


def draft(title, count=2):
    first = pipeline.Scene(narration=title + ".\n\nГосподь — мій Пастир. Псалом 23:1.", caption="Господь мій Пастир",
                           illustration="An open Bible on a wooden table at sunrise.", section="scripture")
    reflection = pipeline.Scene(narration="Почни цей день із довіри. Бог поруч у простих щоденних справах.",
                                caption="Почни цей день", illustration="Golden sunlight falling on a quiet lake.")
    return pipeline.Script(title=title, scenes=[first, *[reflection.model_copy() for _ in range(count - 1)]])


@pytest.fixture
def writing(studio_env, monkeypatch):
    monkeypatch.setattr(illustrated, "require_host", lambda: None)
    monkeypatch.setattr(pipeline, "WRITING_MODEL", SimpleNamespace(name="test.gguf", size=1))
    model = pipeline.writing_dir(studio_env) / "test.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"1")
    discoveries = []

    def topics(studio, project, request):
        discoveries.append((project, request))
        return illustrated.MorningTopics(topics=[illustrated.MorningTopic(
            title=f"Ранкова довіра {i + 1}", passage="Псалом 23:1", angle="Почати новий день з довіри Богові.") for i in range(5)])

    monkeypatch.setattr(illustrated, "topics", topics)
    monkeypatch.setattr(pipeline, "write_script", lambda topic, seconds, count, *_args, **_options: draft(topic.split(" — ")[0], count))
    return discoveries


def wait_job(api, project, id, terminal=("completed", "failed", "cancelled")):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = next(job for job in api.get(project + "/workspace").json()["jobs"] if job["id"] == id)
        if job["state"] in terminal:
            return job
        time.sleep(.02)
    pytest.fail("Background topic job did not finish.")


def generate(api, project):
    result = api.post(project + "/illustrated/topics", json={"language": "uk"})
    assert result.status_code == 202, result.text
    assert wait_job(api, project, result.json()["id"])["state"] == "completed"
    return api.get(project + "/illustrated/topics").json()


def edit_input(topic, **changes):
    return {key: changes.get(key, topic[key]) for key in ("title", "passage", "angle", "revision", "script")}


@pytest.mark.parametrize("target", [2, 3, 6])
def test_scene_pacing_changes_preserve_every_word_and_bible_block(target):
    script = draft("Ранкова надія", 4)
    arranged = topic_library.arrange_scenes(script, target)
    assert len(arranged.scenes) == target
    assert arranged.scenes[0] == script.scenes[0]
    assert " ".join(scene.narration for scene in arranged.scenes).split() == " ".join(scene.narration for scene in script.scenes).split()


def test_poem_blocks_are_not_split_to_add_more_scenes():
    script = draft("Ранкова надія")
    script.scenes[1].section = "poem"
    from backend.studio.audio import StudioError
    with pytest.raises(StudioError, match="too short"):
        topic_library.arrange_scenes(script, 3)


def test_background_returns_immediately_saves_partial_text_and_survives_reopen(studio_env, monkeypatch, writing):
    started, release = threading.Event(), threading.Event()
    seen = []

    def write(topic, seconds, count, *_args, **options):
        seen.append(options["writing_settings"].examples_uk)
        started.set()
        assert release.wait(5)
        options["check"]()
        return draft(topic.split(" — ")[0], count)

    monkeypatch.setattr(pipeline, "write_script", write)
    with client() as api:
        project, other = new_project(api), new_project(api, "Other")
        rules_path = project + "/illustrated/writing-settings"
        original = api.get(rules_path).json()["settings"]
        assert api.put(rules_path, json=dict(original, examples_uk="Перший приклад")).status_code == 200
        response = api.post(project + "/illustrated/topics", json={"language": "uk"})
        assert response.status_code == 202
        try:
            assert started.wait(5)
            pending = api.get(project + "/illustrated/topics").json()
            assert len(pending) == 5 and all(topic["status"] == "pending" for topic in pending)
            assert api.get(other + "/illustrated/topics").json() == []
            assert api.put(rules_path, json=dict(api.get(rules_path).json()["settings"], examples_uk="Новий приклад")).status_code == 200
        finally:
            release.set()
        assert wait_job(api, project, response.json()["id"])["state"] == "completed"
        saved = api.get(project + "/illustrated/topics").json()
        assert len(saved) == 5 and all(topic["status"] == "ready" and topic["script"] for topic in saved)
        assert seen == ["Перший приклад"] * 5
    with client() as api:
        assert api.get(project + "/illustrated/topics").json() == saved


def test_edit_used_delete_restore_conflicts_and_project_isolation(studio_env, writing):
    with client() as api:
        project, other = new_project(api), new_project(api, "Other")
        topic = generate(api, project)[0]
        path = project + "/illustrated/topics/" + topic["id"]
        script = topic["script"]
        script["scenes"][1]["narration"] = "У цьому дні є місце для радості. Довіряй Богові в кожному кроці."
        saved = api.put(path, json=edit_input(topic, title="Надія кожного ранку", script=script))
        assert saved.status_code == 200, saved.text
        changed = saved.json()
        assert changed["script"]["title"] == changed["title"] == "Надія кожного ранку"
        assert changed["script"]["scenes"][0]["narration"].startswith("Надія кожного ранку.")
        assert topic["title"] not in changed["script"]["scenes"][0]["narration"]
        assert changed["script"]["scenes"][1]["narration"] == script["scenes"][1]["narration"]
        assert api.put(path, json=edit_input(topic)).status_code == 409
        for used in (True, False):
            flagged = api.patch(path, json={"revision": changed["revision"], "used": used})
            assert flagged.status_code == 200 and flagged.json()["used"] is used
            changed = flagged.json()
        assert api.patch(other + "/illustrated/topics/" + topic["id"], json={"revision": changed["revision"], "used": True}).status_code == 404
        deleted = api.patch(path, json={"revision": changed["revision"], "deleted": True}).json()
        assert deleted["deleted_at"] and len(api.get(project + "/illustrated/topics").json()) == 4
        assert api.put(path, json=edit_input(deleted)).status_code == 404
        restored = api.patch(path, json={"revision": deleted["revision"], "deleted": False})
        assert restored.status_code == 200 and restored.json()["deleted_at"] is None
        assert len(api.get(project + "/illustrated/topics").json()) == 5


def test_retry_keeps_completed_and_deleted_topics_and_retries_only_missing_text(studio_env, monkeypatch, writing):
    attempts = []

    def write(topic, seconds, count, *_args, **_options):
        attempts.append(topic)
        if len(attempts) == 1:
            raise ValueError("Simulated text failure")
        return draft(topic.split(" — ")[0], count)

    monkeypatch.setattr(pipeline, "write_script", write)
    with client() as api:
        project = new_project(api)
        result = api.post(project + "/illustrated/topics", json={"language": "uk"}).json()
        assert wait_job(api, project, result["id"])["state"] == "failed"
        topics = api.get(project + "/illustrated/topics").json()
        assert [topic["status"] for topic in topics].count("ready") == 4
        ready = next(topic for topic in topics if topic["status"] == "ready")
        deleted = api.patch(project + "/illustrated/topics/" + ready["id"], json={"revision": ready["revision"], "deleted": True})
        assert deleted.status_code == 200
        assert api.post(project + "/jobs/" + result["id"] + "/retry").status_code == 202
        assert wait_job(api, project, result["id"])["state"] == "completed"
        final = api.get(project + "/illustrated/topics").json()
        assert len(final) == 4 and all(topic["status"] == "ready" for topic in final)
        assert len(attempts) == 6 and len(writing) == 1


def test_cancel_and_retry_resume_the_same_batch_without_duplicating_topics(studio_env, monkeypatch, writing):
    started, release = threading.Event(), threading.Event()

    def write(topic, seconds, count, *_args, **options):
        started.set()
        assert release.wait(5)
        options["check"]()
        return draft(topic.split(" — ")[0], count)

    monkeypatch.setattr(pipeline, "write_script", write)
    with client() as api:
        project = new_project(api)
        result = api.post(project + "/illustrated/topics", json={"language": "uk"}).json()
        try:
            assert started.wait(5)
            ids = [topic["id"] for topic in api.get(project + "/illustrated/topics").json()]
            assert api.post(project + "/jobs/" + result["id"] + "/cancel").status_code == 200
        finally:
            release.set()
        assert wait_job(api, project, result["id"])["state"] == "cancelled"
        assert api.post(project + "/jobs/" + result["id"] + "/retry").status_code == 202
        assert wait_job(api, project, result["id"])["state"] == "completed"
        assert [topic["id"] for topic in api.get(project + "/illustrated/topics").json()] == ids
        assert len(writing) == 1


def test_undo_pending_deletion_finishes_restored_text_after_batch_completion(studio_env, monkeypatch, writing):
    started, release = threading.Event(), threading.Event()

    def write(topic, seconds, count, *_args, **_options):
        started.set()
        assert release.wait(5)
        return draft(topic.split(" — ")[0], count)

    monkeypatch.setattr(pipeline, "write_script", write)
    with client() as api:
        project = new_project(api)
        job = api.post(project + "/illustrated/topics", json={"language": "uk"}).json()
        try:
            assert started.wait(5)
            topic = api.get(project + "/illustrated/topics").json()[-1]
            path = project + "/illustrated/topics/" + topic["id"]
            deleted = api.patch(path, json={"revision": topic["revision"], "deleted": True}).json()
        finally:
            release.set()
        assert wait_job(api, project, job["id"])["state"] == "completed"
        assert len(api.get(project + "/illustrated/topics").json()) == 4
        restored = api.patch(path, json={"revision": deleted["revision"], "deleted": False})
        assert restored.status_code == 200
        assert wait_job(api, project, job["id"])["state"] == "completed"
        rows = api.get(project + "/illustrated/topics").json()
        assert len(rows) == 5 and all(row["status"] == "ready" for row in rows)
        assert len(writing) == 1


def test_video_snapshots_reviewed_text_uses_no_writer_and_marks_topic_used(studio_env, monkeypatch, writing):
    started, release = threading.Event(), threading.Event()
    original_produce = pipeline.produce
    monkeypatch.setattr(illustrated, "validate", lambda *_: None)
    monkeypatch.setattr(flux, "available", lambda: True)
    monkeypatch.setattr(flux, "installed", lambda _: True)

    def synthesize(directory, scene, *_args, **_options):
        axis = np.arange(12000) / 24000
        write_wave(directory / "narration.wav", .3 * np.sin(2 * np.pi * 440 * axis), 24000)
        return [{"text": scene.narration, "start": 0, "end": .5}]

    def image(directory, *_args, **_options):
        Image.new("RGB", (1024, 576), "#fff2d8").save(directory / "scene.png")

    def cover(output, *_args, **_options):
        Image.new("RGB", (1280, 720), "#fff2d8").save(output / "thumbnail.png")
        return output / "thumbnail.png"

    def produce(args, **options):
        started.set()
        assert release.wait(5)
        return original_produce(args, **options)

    monkeypatch.setattr(pipeline, "synthesize", synthesize)
    monkeypatch.setattr(pipeline, "illustrate", image)
    monkeypatch.setattr(pipeline, "generate_thumbnail", cover)
    monkeypatch.setattr(pipeline, "produce", produce)
    with client() as api:
        project, other = new_project(api), new_project(api, "Other")
        defaults = api.get(project + "/illustrated/defaults").json()
        assert api.put(project + "/illustrated/defaults", json=dict(defaults, scenes=2)).status_code == 200
        topic = generate(api, project)[0]
        monkeypatch.setattr(pipeline, "write_script", lambda *_a, **_k: pytest.fail("Saved video text must not be rewritten"))
        values = {"topic": "Ignored client title", "topic_id": topic["id"], "topic_revision": topic["revision"], "language": "uk"}
        assert api.post(other + "/illustrated/render", json=values).status_code == 404
        assert api.post(project + "/illustrated/render", json=dict(values, topic_revision=0)).status_code == 409
        response = api.post(project + "/illustrated/render", json=values)
        assert response.status_code == 202, response.text
        try:
            assert started.wait(5)
            changed = api.put(project + "/illustrated/topics/" + topic["id"], json=edit_input(topic, title="Інша назва після черги"))
            assert changed.status_code == 200, changed.text
        finally:
            release.set()
        asset = wait_for_asset(api, project, response.json()["id"])
        assert asset["name"] == topic["title"]
        assert "Інша назва" not in asset["transcript"]
        for scene in topic["script"]["scenes"]:
            assert scene["narration"] in asset["transcript"]
        saved = next(row for row in api.get(project + "/illustrated/topics").json() if row["id"] == topic["id"])
        assert saved["used"] and saved["title"] == "Інша назва після черги"
