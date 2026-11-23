import json
import uuid
import wave
from types import SimpleNamespace

import pytest

from backend.api.main import database_connection, initialize_database
from backend.scenes import project_video
from backend.scenes.exports import ExportError
from backend.scenes.project_video import ProjectVideoJobs, ProjectVideoRequest, ResumeProjectVideo


def test_project_video_restores_checkpoints_and_links_the_export(monkeypatch, tmp_path):
    pytest.importorskip("fcntl")
    monkeypatch.setenv("AI_BLOGGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(project_video, "supported_host", lambda: True)
    monkeypatch.setattr(project_video, "availability", lambda: (True, "Available"))
    initialize_database()
    project, topic, script, plan, narration, presenter, image, video, export = (
        str(uuid.uuid4()) for _ in range(9)
    )
    script_text = "A script for the checkpoint test."
    created = "2026-11-15T12:00:00+00:00"
    with database_connection() as db:
        db.execute("INSERT INTO projects VALUES (?,?,?)", (project, "Test project", created))
        db.execute(
            "INSERT INTO topics VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,?)",
            (topic, "Checkpoint topic", "checkpoint topic", "Description", "Lifestyle", "Hook", "Audience",
             30, "Thumbnail", "Potential", 50, created),
        )
        db.execute("INSERT INTO scripts VALUES (?,?,?,?,?)",
                   (script, topic, 30, json.dumps({"full_narration": script_text}), created))
    calls = []
    wav = tmp_path / "sample.wav"
    with wave.open(str(wav), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(24000 * 2 * 30))

    def record(stage, result):
        calls.append(stage)
        return result

    document = SimpleNamespace(id=script, full_narration=script_text)
    director = SimpleNamespace(id=plan, scenes=[
        SimpleNamespace(presenter_present=True, generation_prompt="The illustrated presenter opens the story."),
        SimpleNamespace(presenter_present=False, generation_prompt="A camera pans across a field of flowers."),
        SimpleNamespace(presenter_present=False, generation_prompt="Sunlight illuminates a red flower."),
    ])
    creator = SimpleNamespace(
        status=lambda: SimpleNamespace(inference_ready=True),
        generate_script=lambda *args, **kwargs: record("script", document),
        get_script=lambda key: document,
        generate_director=lambda key: record("director", director),
    )
    voice = SimpleNamespace(
        status=lambda: {"ready": True},
        save_narration=lambda *args, **kwargs: record("narration", {"id": narration, "duration_seconds": 30}),
        narration_file=lambda key: wav,
    )
    child_jobs = {}

    def start_scene(_script_id, request, *, on_start):
        kind = request.kind
        calls.append(kind)
        child_id = str(uuid.uuid4())
        with database_connection() as db:
            on_start(db, child_id)
        if kind == "video" and calls.count("video") == 1:
            result = {"id": child_id, "state": "failed", "message": "Simulated scene failure"}
        else:
            result = {"id": child_id, "state": "completed", "asset_id": image if kind == "image" else video}
        child_jobs[child_id] = result
        return result

    scenes = SimpleNamespace(
        capabilities=lambda: {"video_tools_available": True, "profiles": {
            kind: {"cached": True} for kind in ("image", "video")}},
        start=start_scene, get_job=lambda _script_id, child_id: child_jobs.get(child_id),
    )

    def render(_script_id, payload, source, transcript, *, on_publish):
        assert source == wav and transcript == script_text
        assert [str(scene.id) for scene in payload.scenes] == [presenter, video, image]
        assert sum(scene.frames for scene in payload.scenes) == 720
        calls.append("export")
        with database_connection() as db:
            on_publish(db, {"id": export})

    def build():
        jobs = ProjectVideoJobs(tmp_path, database_connection, creator, voice, scenes,
                                lambda *args: record("presenter", {"id": presenter}))
        jobs.library = SimpleNamespace(list_assets=lambda key: [{"id": video, "max_frames": 73}], render=render)
        return jobs

    first = build()
    job = first.start(project, ProjectVideoRequest(topic_id=topic))
    first.worker.join(timeout=5)
    failed = first.latest(project)
    assert failed["state"] == "failed" and failed["completed_stages"] == list(project_video.STAGES[:5])
    with database_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM project_scripts").fetchone()[0] == 0
        db.execute("UPDATE project_video_jobs SET state='running' WHERE id=?", (job["id"],))

    restarted = build()
    restarted.recover()
    assert restarted.latest(project)["state"] == "interrupted"
    restarted.resume(project, job["id"], ResumeProjectVideo())
    restarted.worker.join(timeout=5)
    finished = restarted.latest(project)
    assert finished["state"] == "completed" and finished["export_id"] == export
    assert finished["id"] == job["id"]
    assert calls == ["script", "director", "narration", "presenter", "image", "video", "video", "export"]
    with database_connection() as db:
        assert db.execute("SELECT script_id FROM project_scripts WHERE project_id=?", (project,)).fetchone()[0] == script
        draft = json.loads(db.execute("SELECT draft_json FROM video_drafts WHERE script_id=?", (script,)).fetchone()[0])
        assert [scene["id"] for scene in draft["scenes"]] == [presenter, video, image]
