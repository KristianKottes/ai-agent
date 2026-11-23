"""Offline OpenVoice V2 tone conversion for an authorized voice reference."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from backend.studio.audio import StudioError, pcm

MODEL_ID = "openvoice-v2"
MODEL_LICENSE_URL = "https://huggingface.co/myshell-ai/OpenVoiceV2/tree/f36e7edfe1684461a8343844af60babc2efbb727"
MANIFEST_PATH = Path(__file__).with_name("openvoice_manifest.json")
FILES = ("checkpoint.pth", "config.json", "LICENSE", "runtime/torch/__init__.py",
         "runtime/torchaudio/__init__.py", "runtime/openvoice/models.py")


class LocalOpenVoice:
    def __init__(self, directory, reference=None):
        sys.path.insert(0, str(directory / "runtime"))
        import torch
        import torch.nn.functional as functional
        from openvoice.models import SynthesizerTrn
        from torchaudio.functional import resample

        torch.set_num_threads(4)
        self.torch = torch
        self.functional = functional
        self.resample = resample
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        self.data = SimpleNamespace(**config["data"])
        self.sample_rate = self.data.sampling_rate
        self.window = torch.hann_window(self.data.win_length)
        self.model = SynthesizerTrn(
            len(config.get("symbols", [])), self.data.filter_length // 2 + 1,
            n_speakers=self.data.n_speakers, **config["model"],
        ).eval()
        weights = torch.load(directory / "checkpoint.pth", weights_only=True, map_location="cpu", mmap=True)
        self.model.load_state_dict(weights["model"], strict=True)
        if reference is not None:
            samples, rate = pcm(reference)
            with torch.inference_mode():
                self.target_voice = self.embedding(self.spectrum(samples, rate))

    def spectrum(self, samples, rate):
        import numpy as np

        torch, data = self.torch, self.data
        signal = torch.as_tensor(np.asarray(samples, dtype=np.float32))
        if rate != self.sample_rate:
            signal = self.resample(signal, rate, self.sample_rate)
        if signal.numel() < data.filter_length:
            signal = self.functional.pad(signal, (0, data.filter_length - signal.numel()))
        padding = (data.filter_length - data.hop_length) // 2
        padded = self.functional.pad(signal.reshape(1, 1, -1), (padding, padding), mode="reflect").squeeze(1)
        complex_bins = torch.stft(
            padded, data.filter_length, hop_length=data.hop_length, win_length=data.win_length,
            window=self.window, center=False, return_complex=True,
        )
        return (complex_bins.abs().square() + 1e-6).sqrt()

    def embedding(self, spectrum):
        voice = self.model.ref_enc(spectrum.transpose(1, 2)).unsqueeze(-1)
        if not self.torch.isfinite(voice).all():
            raise StudioError("OpenVoice could not extract a usable voice from the audio.")
        return voice

    def convert(self, samples, rate):
        torch = self.torch
        with torch.inference_mode():
            spectrum = self.spectrum(samples, rate)
            length = torch.tensor([spectrum.size(-1)], dtype=torch.long)
            source_voice = self.embedding(spectrum)
            audio, *_ = self.model.voice_conversion(
                spectrum, length, sid_src=source_voice, sid_tgt=self.target_voice, tau=.3,
            )
        return SimpleNamespace(samples=audio[0, 0].cpu().numpy(), sample_rate=self.sample_rate)


if __name__ == "__main__":
    LocalOpenVoice(Path(sys.argv[1]))
