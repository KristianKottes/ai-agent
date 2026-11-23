"""Local HolosTTS inference from a project-owned reference recording."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from backend.studio.audio import StudioError

MODEL_ID = "holos-tts-uk-04"
MODEL_LICENSE_URL = "https://huggingface.co/patriotyk/HolosTTS/blob/5d40fbaa99e55ebd3359a161bc86728ffdbd5c5f/README.md#license"
MANIFEST_PATH = Path(__file__).with_name("holos_manifest.json")
FILES = ("holos.pt", "voice_encoder.pt", "LICENSE", "runtime/holos/models.py",
         "runtime/ukrainian_word_stress/__init__.py", "runtime/ipa_uk/__init__.py",
         "runtime/torch/__init__.py")


@dataclass
class GeneratedAudio:
    samples: object
    sample_rate: int = 24_000


def imports(directory):
    sys.path.insert(0, str(directory / "runtime"))
    import torch
    from holos.models import HolosTTS, VoiceEncoder
    from holos.modules import Tokenizer
    from ipa_uk import ipa
    from ukrainian_word_stress import Stressifier, StressSymbol

    return torch, HolosTTS, VoiceEncoder, Tokenizer, ipa, Stressifier, StressSymbol


class LocalHolos:
    def __init__(self, directory, reference, additional=()):
        if reference is None or not reference.is_file():
            raise StudioError("Choose a saved Ukrainian reference recording.")
        for filename in FILES:
            path = directory / filename
            if not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
                raise StudioError("The installed HolosTTS package is incomplete. Reinstall it in Models & Tools.", 503)

        torch, HolosTTS, VoiceEncoder, Tokenizer, ipa, Stressifier, StressSymbol = imports(directory)
        torch.set_num_threads(4)
        self.phonemes = ipa
        self.stressify = Stressifier(stress_symbol=StressSymbol.CombiningAcuteAccent)

        # Build the two upstream architectures without resolving a repository or loading preset voices.
        encoder = VoiceEncoder(weights_path=None, device="cpu")
        checkpoint = torch.load(directory / "voice_encoder.pt", map_location="cpu", weights_only=True, mmap=True)
        state = checkpoint.get("state_dict", checkpoint)
        state = {name.removeprefix("module."): tensor for name, tensor in state.items()}
        missing, _ = encoder.load_state_dict(state, strict=False)
        if any(name.startswith(("style_encoder.", "prosodic_style_encoder.")) for name in missing):
            raise StudioError("The HolosTTS reference encoder weights are incomplete.", 503)
        del checkpoint, state
        voices = []
        for index, clip in enumerate((reference, *additional), 1):
            voice = encoder(clip)
            if voice.shape != (1, 256) or not torch.isfinite(voice).all():
                raise StudioError(f"HolosTTS could not encode recording {index}.")
            voices.append(voice)
        self.voice = voices[0] if len(voices) == 1 else torch.stack(voices).mean(dim=0)

        self.model = HolosTTS(weights_path=None, device="cpu")
        checkpoint = torch.load(directory / "holos.pt", map_location="cpu", weights_only=True, mmap=True)
        self.model.tokenizer = Tokenizer(checkpoint["vocab"])
        self.model.load_state_dict(checkpoint["state_dict"], strict=True)

    def synthesize(self, text, speed):
        # Keep explicit stress in names and homographs while filling it in elsewhere.
        spans = re.split(r"([\w'’ʼ\-\u0301]*\u0301[\w'’ʼ\-\u0301]*)", text)
        phonemes = self.phonemes("".join(span if index % 2 else self.stressify(span)
                                         for index, span in enumerate(spans)))
        output = self.model.generate(phonemes, self.voice, speed=speed)
        return GeneratedAudio(output.reshape(-1).detach().cpu().numpy())


if __name__ == "__main__":
    # Readiness probe executed in the isolated worker by Models & Tools.
    root = Path(sys.argv[1])
    _, _, _, _, ipa, Stressifier, StressSymbol = imports(root)
    stressify = Stressifier(stress_symbol=StressSymbol.CombiningAcuteAccent)
    assert ipa(stressify("Українська мова звучить.")).strip()
    print(json.dumps({"ready": True}))
