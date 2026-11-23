"""Run the bundled ZONOS3-Mini native tools using only local model files."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from backend.avatar.service import _binary
from backend.studio.audio import StudioError, pcm

MODEL_ID = "zonos3-mini"
MODEL_LICENSE_URL = "https://huggingface.co/Zyphra/ZONOS3-Mini/tree/15fb29d7787f7cc61d12616341710bcb1d3e9b6c"
MANIFEST_PATH = Path(__file__).with_name("zonos_manifest.json")
FILES = ("backbone-ptq.gguf", "zac-keep2.gguf", "spk-encoder.gguf")
BINARIES = ("zonos3-cli", "spk-encoder-cli")
LOGGER = logging.getLogger(__name__)


def binary_directory():
    configured = os.environ.get("AI_BLOGGER_ZONOS_BIN_DIR") or os.environ.get("AI_BLOGGER_MEDIA_BIN_DIR")
    if configured:
        directory = Path(configured)
        if all((directory / name).is_file() and os.access(directory / name, os.X_OK) for name in BINARIES):
            return directory
    return None


@dataclass
class GeneratedAudio:
    samples: object
    sample_rate: int


class LocalZonos:
    def __init__(self, model, directory, references=()):
        import numpy as np

        self.binaries = binary_directory()
        if self.binaries is None:
            raise StudioError("Open the macOS build with the bundled ZONOS3 runtime.", 503)
        if not all((model / filename).is_file() for filename in FILES):
            raise StudioError("The ZONOS3-Mini model files are incomplete. Reinstall the model in Models & Tools.", 503)
        self.model, self.directory = model, directory
        self.environment = os.environ.copy()
        ffmpeg = _binary("ffmpeg")
        if ffmpeg:
            self.environment["ZONOS3_FFMPEG"] = ffmpeg
        self.seed = 42
        self.speaker = None
        if references:
            embeddings = []
            for index, reference in enumerate(references, 1):
                output = directory / f"speaker-{index}.npy"
                self.run("spk-encoder-cli", [model / "spk-encoder.gguf", "--audio", reference, output, "--gpu"],
                         f"ZONOS3-Mini could not encode recording {index}.")
                embedding = np.load(output, allow_pickle=False)
                if embedding.shape != (192,) or not np.isfinite(embedding).all():
                    raise StudioError(f"ZONOS3-Mini returned an invalid voice embedding for recording {index}.")
                embeddings.append(embedding)
            self.speaker = directory / "speaker.npy"
            np.save(self.speaker, np.mean(embeddings, axis=0, dtype=np.float32), allow_pickle=False)

    def run(self, name, arguments, message):
        try:
            result = subprocess.run([self.binaries / name, *arguments], capture_output=True,
                                    env=self.environment, timeout=75)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StudioError(message, 502) from error
        if result.returncode:
            LOGGER.warning("%s exited with code %s: %s", name, result.returncode,
                           result.stderr.decode(errors="replace")[-1500:])
            raise StudioError(message, 502)

    def synthesize(self, text):
        import numpy as np

        output = self.directory / "section.wav"
        arguments = [self.model / "backbone-ptq.gguf", "--gpu", "--tts", text, output,
                     "--max", "900", "--headroom-db", "6"]
        if self.speaker:
            arguments += ["--speaker-embedding", self.speaker]
        else:
            arguments += ["--loudness", "-20", "--fmax", "18000"]
        for seed in dict.fromkeys((self.seed, 42, 1337, 11)):
            output.unlink(missing_ok=True)
            self.run("zonos3-cli", [*arguments, "--seed", str(seed)], "ZONOS3-Mini could not generate speech for a section.")
            samples, rate = pcm(output)
            if samples.size and np.max(np.abs(samples)) >= 0.005:
                self.seed = seed
                return GeneratedAudio(samples, rate)
        raise StudioError("ZONOS3-Mini returned near-silent audio. Try a different passage or voice.", 502)


if __name__ == "__main__":
    with TemporaryDirectory() as work:
        LocalZonos(Path(sys.argv[1]), Path(work)).synthesize("Доброго ранку! Сьогодні гарний день.")
