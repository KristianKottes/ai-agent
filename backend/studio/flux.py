"""Use the pinned scene engine and its shared model cache from Image Studio."""

import platform

from backend.scenes.benchmark import BenchmarkError, SceneCancelled, benchmark, model_cache, prepare_models
from backend.scenes.jobs import register_model, supported_host
from backend.scenes.profiles import ENGINE, ENGINE_REVISION, IMAGE_WEIGHTS
from backend.studio.audio import StudioError
from backend.studio.export import Cancelled

ID = "flux2-klein-4b"
NAME = "FLUX.2 Klein 4B"
MODEL_URL = "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B"
ARTIFACTS = (ENGINE, *IMAGE_WEIGHTS)
STEPS = 4


def available():
    major = platform.mac_ver()[0].split(".")[0]
    return supported_host() and major.isdigit() and int(major) >= 26


def installed(directory):
    try:
        return directory.resolve() == directory and all(
            path.is_file() and not path.is_symlink() and path.stat().st_size == artifact.size
            for artifact in ARTIFACTS for path in (directory / artifact.name,)
        )
    except OSError:
        return False


def prepare(directory, connect, download, cancel, progress):
    with model_cache(directory):
        prepare_models(directory, ARTIFACTS, download, cancel=cancel, progress=progress,
                       verified=lambda part, path: register_model(connect, part, path))


def generate(studio, id, work, request, prompt, seed, reference):
    class Cancellation:
        @staticmethod
        def is_set():
            return studio.stop.is_set() or studio.user_cancel.is_set()

    def progress(stage, ratio):
        if stage == "preparing":
            studio.update(id, "running", .2, "Loading FLUX on Metal")
        elif stage == "generating":
            studio.update(id, "running", .2 + .65 * (ratio or 0), "Generating with FLUX")
        elif stage == "validating":
            studio.update(id, "running", .9, "Checking FLUX image")

    studio.update(id, "running", .05, "Verifying pinned FLUX files")
    try:
        report = benchmark("image", studio.data / "scene_models", False, run_dir=work,
                           prompt=prompt, seed=seed, image_size=(request.width, request.height),
                           image_steps=request.steps, reference=reference, cancel=Cancellation(), progress=progress)
    except SceneCancelled as error:
        raise Cancelled() from error
    except BenchmarkError as error:
        raise StudioError(str(error).split(". See ", 1)[0], 502) from error
    except OSError as error:
        raise StudioError("The FLUX Metal runtime could not start.", 503) from error
    (work / "scene.png").replace(work / "result-00.png")
    return {"device": "metal", "engine": ENGINE_REVISION, "weight_hashes": report["weights"]}
