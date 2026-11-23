"""Project settings inherit existing application preferences until first saved."""

from pathlib import Path
from uuid import UUID


def settings_directory(data: Path, project=None):
    return data if project is None else data / "project-settings" / str(UUID(str(project)))


def load_scoped(data, filename, model, project=None):
    path = settings_directory(data, project) / filename
    if path.is_file():
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    if project is not None:
        # Each project's revision starts independently of the inherited preferences.
        return load_scoped(data, filename, model).model_copy(update={"revision": 0})
    return model()
