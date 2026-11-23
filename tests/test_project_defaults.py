"""Settings isolation across projects and project-aware generation snapshots."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.ai.mamay import MamaySettings, save_settings
from backend.scenes import illustrated_video as pipeline
from backend.studio import illustrated
from backend.studio.service import ProjectStudio
from test_studio import client, new_project, studio_env


@pytest.mark.parametrize("section, first, second", [
    ("defaults", {"text_range": {"min_words": 300, "max_words": 400}, "style": "biblical"},
     {"text_range": {"min_words": 600, "max_words": 700}, "style": "ink"}),
    ("writing-settings", {"examples_uk": "Мир починається з довіри."}, {"examples_uk": "Радість у малих речах."}),
    ("speech-settings", {"title_pause_ms": 1000, "pronunciation": {"Ісус": "Ісу́с"}},
     {"title_pause_ms": 2000, "pronunciation": {}}),
])
def test_project_settings_inherit_then_save_independently(studio_env, section, first, second):
    def settings(response):
        assert response.status_code == 200, response.text
        value = response.json()
        return value["settings"] if section == "writing-settings" else value

    global_path = "/api/studio/illustrated/" + section
    with client() as api:
        a = new_project(api, "First") + "/illustrated/" + section
        b = new_project(api, "Second") + "/illustrated/" + section
        original = settings(api.get(global_path))
        global_first = api.put(global_path, json=dict(original, **first))
        assert global_first.status_code == 200
        inherited_a, inherited_b = settings(api.get(a)), settings(api.get(b))
        assert inherited_a["revision"] == inherited_b["revision"] == 0
        assert all(inherited_a[key] == value for key, value in first.items())
        saved_a = api.put(a, json=inherited_a)
        assert saved_a.status_code == 200 and saved_a.json()["revision"] == 1
        assert api.put(a, json=inherited_a).status_code == 409
        global_second = api.put(global_path, json=dict(global_first.json(), **second))
        assert global_second.status_code == 200
        assert settings(api.get(a)) == saved_a.json()
        assert all(settings(api.get(b))[key] == value for key, value in second.items())
        saved_b = api.put(b, json=settings(api.get(b)))
        assert saved_b.status_code == 200 and saved_b.json()["revision"] == 1
        assert api.put(global_path, json=dict(global_second.json(), **first)).status_code == 200
        assert settings(api.get(b)) == saved_b.json()
        missing = "/api/studio/" + str(uuid4()) + "/illustrated/" + section
        assert api.get(missing).status_code == 404
        assert api.put(missing, json=inherited_a).status_code == 404
    with client() as api:
        assert settings(api.get(a)) == saved_a.json()
        assert settings(api.get(b)) == saved_b.json()


def test_jobs_snapshot_each_projects_video_writing_and_speech_defaults(studio_env, monkeypatch):
    monkeypatch.setattr(illustrated, "validate", lambda *_: None)
    monkeypatch.setattr(ProjectStudio, "_run_queue", lambda self: self.stop.wait(timeout=10))
    with client() as api:
        projects = [new_project(api, title) for title in ("First", "Second")]
        for project, minimum, style, pause in zip(projects, (300, 600), ("biblical", "ink"), (1000, 2000)):
            path = project + "/illustrated/"
            defaults = api.get(path + "defaults").json()
            saved = api.put(path + "defaults", json=dict(defaults, text_range={"min_words": minimum, "max_words": minimum + 100}, style=style))
            assert saved.status_code == 200
            writing = api.get(path + "writing-settings").json()["settings"]
            assert api.put(path + "writing-settings", json=dict(writing, examples_uk=f"Приклад проєкту {minimum}")).status_code == 200
            speech = api.get(path + "speech-settings").json()
            assert api.put(path + "speech-settings", json=dict(speech, title_pause_ms=pause)).status_code == 200
            assert api.post(path + "render", json={"topic": "Ранкова надія"}).status_code == 202
        a = projects[0] + "/illustrated/defaults"
        assert api.put(a, json=dict(api.get(a).json(), style="cinematic")).status_code == 200
        # A per-video Auto override does not reintroduce the project's saved range.
        assert api.post(projects[0] + "/illustrated/render", json={"topic": "Нова тема", "text_range": None, "scenes": None}).status_code == 202
        from backend.api.main import database_connection
        with database_connection() as db:
            rows = db.execute("SELECT id, input_json FROM studio_jobs ORDER BY rowid").fetchall()
            db.execute("UPDATE studio_jobs SET state='failed' WHERE id=?", (rows[0]["id"],))
        requests = [illustrated.IllustratedRequest.model_validate_json(row["input_json"]) for row in rows]
        for request, minimum, style, pause in zip(requests, (300, 600), ("biblical", "ink"), (1000, 2000)):
            assert request.text_range.min_words == minimum and request.text_range.max_words == minimum + 100
            assert request.style == style
            assert request.writing_settings.examples_uk == f"Приклад проєкту {minimum}"
            assert request.speech_settings.title_pause_ms == pause
        assert requests[2].text_range is None and requests[2].scenes is None
        assert requests[2].style == "cinematic"
        retry = api.post(projects[0] + "/jobs/" + rows[0]["id"] + "/retry")
        assert retry.status_code == 202, retry.text
        with database_connection() as db:
            retried = db.execute("SELECT input_json FROM studio_jobs WHERE id=?", (rows[0]["id"],)).fetchone()[0]
        assert retried == rows[0]["input_json"]


def test_topic_discovery_uses_selected_projects_rules(tmp_path, monkeypatch):
    a, b = str(uuid4()), str(uuid4())
    save_settings(tmp_path, MamaySettings(topic_rules="RULES FOR FIRST PROJECT"), a)
    save_settings(tmp_path, MamaySettings(topic_rules="RULES FOR SECOND PROJECT"), b)
    model_path = pipeline.writing_dir(tmp_path) / "test.gguf"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"1")
    monkeypatch.setattr(illustrated, "require_host", lambda: None)
    monkeypatch.setattr(pipeline, "WRITING_MODEL", SimpleNamespace(name="test.gguf", size=1))
    prompts = []

    class Model:
        def create_chat_completion(self, **options):
            import json
            prompts.append(options["messages"][0]["content"])
            topics = [{"title": f"Надія сьогодні {i}", "passage": "Псалом 23:1", "angle": "Довіряй Богові сьогодні."} for i in range(5)]
            return {"choices": [{"message": {"content": json.dumps({"topics": topics}, ensure_ascii=False)}}]}

    @contextmanager
    def model(*_args):
        yield Model()

    monkeypatch.setattr(pipeline, "writing_model", model)
    studio = SimpleNamespace(data=tmp_path, project=lambda _: None, check=lambda: None)
    for project in (a, b):
        illustrated.topics(studio, project, illustrated.TopicRequest(language="uk"))
    assert "RULES FOR FIRST PROJECT" in prompts[0] and "RULES FOR SECOND PROJECT" not in prompts[0]
    assert "RULES FOR SECOND PROJECT" in prompts[1] and "RULES FOR FIRST PROJECT" not in prompts[1]
