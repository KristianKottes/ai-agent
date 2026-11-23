"""Language-aware offline speech providers and timed PCM assembly."""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal, Optional

from backend.studio.audio import StudioError, write_wave
from backend.studio import holos, model_install, openvoice, zonos
from backend.studio.text import normalize, sections
from backend.voice.service import MODEL_ID, VOICE_NAMES, _engine, _runtime

LADA = "vits-piper-uk_UA-lada-x_low"
POCKET = "sherpa-onnx-pocket-tts-int8-2026-01-26"
PINNED_MODELS = {holos.MODEL_ID: holos, openvoice.MODEL_ID: openvoice, zonos.MODEL_ID: zonos}
OPENVOICE_BASE = {"en": (MODEL_ID, "af_bella"), "uk": (LADA, "lada")}
PACKAGES = {
    LADA: (26_520_908, "7a2228f249299d19016cd6ca170bf6916f8664231bcbd9e61f447b861a039cf5",
           ("uk_UA-lada-x_low.onnx", "tokens.txt", "espeak-ng-data/phondata")),
    POCKET: (98_336_520, "2f3b88823cbbb9bf0b2477ec8ae7b3fec417b3a87b6bb5f256dba66f2ad967cb",
             ("lm_flow.int8.onnx", "lm_main.int8.onnx", "encoder.onnx", "decoder.int8.onnx",
              "text_conditioner.onnx", "vocab.json", "token_scores.json")),
}
Language = Literal["en", "uk"]


class TextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=30_000)
    language: Language
    pronunciation: dict[str, str] = Field(default_factory=dict, max_length=50)

    @field_validator("pronunciation")
    @classmethod
    def validate_pronunciation(cls, mapping):
        if any(not 1 <= len(k.strip()) <= 100 or not 1 <= len(v.strip()) <= 100 for k, v in mapping.items()):
            raise ValueError("Pronunciation entries must contain 1–100 characters.")
        return mapping


class SpeechBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=30_000)
    kind: Literal["title", "scripture", "poem", "paragraph"] = "paragraph"
    pause_after_ms: Optional[int] = Field(default=None, ge=0, le=10000)


class SpeechRequest(TextRequest):
    model_id: Literal["kokoro-int8-en-v0_19", "vits-piper-uk_UA-lada-x_low",
                      "sherpa-onnx-pocket-tts-int8-2026-01-26", "holos-tts-uk-04", "openvoice-v2", "zonos3-mini"]
    voice: str = Field(min_length=1, max_length=48)
    name: str = Field(default="Narration", min_length=1, max_length=120)
    speed: float = Field(default=1, ge=0.75, le=1.5)
    pause_ms: int = Field(default=120, ge=0, le=2000)
    paragraph_pause_ms: Optional[int] = Field(default=None, ge=0, le=10000)
    title_pause_ms: Optional[int] = Field(default=None, ge=0, le=10000)
    block_pause_ms: Optional[int] = Field(default=None, ge=0, le=10000)
    blocks: Optional[list[SpeechBlock]] = Field(default=None, min_length=1, max_length=200)
    trailing_pause: bool = False
    sample_rate: Literal[16000, 22050, 24000, 44100, 48000] = 24000
    normalize_audio: bool = True
    format: Literal["wav", "mp3"] = "wav"
    normalized_text: Optional[str] = Field(default=None, min_length=1, max_length=90_000)
    noncommercial_confirmed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def block_text_matches(self):
        if self.blocks is not None:
            if self.normalized_text is not None:
                raise ValueError("Use pronunciation overrides rather than normalized_text with structured blocks.")
            if " ".join(self.text.split()) != " ".join(" ".join(block.text for block in self.blocks).split()):
                raise ValueError("Speech blocks must contain the same text as the narration, in the same order.")
        return self

    def pause_after(self, kind):
        value = {"title": self.title_pause_ms, "scripture": self.block_pause_ms,
                 "poem": self.block_pause_ms, "paragraph": self.paragraph_pause_ms}.get(kind)
        return self.pause_ms * 2 if value is None else value


@dataclass(frozen=True)
class SpeechProvider:
    model_id: str
    languages: tuple[str, ...]
    voices: tuple[tuple[str, str], ...]
    reference_conditioned: bool = False
    research_only: bool = False
    reference_languages: tuple[str, ...] = ()
    multi_reference: bool = False
    native_subprocesses: bool = False

    def uses_reference(self, voice):
        return self.reference_conditioned and voice not in dict(self.voices)

    @contextmanager
    def load(self, model_dir, reference_path=None, *, language=None, additional=()):
        if self.model_id == MODEL_ID:
            engine = _engine(model_dir)
        elif self.model_id == holos.MODEL_ID:
            engine = holos.LocalHolos(model_dir, reference_path, additional)
        else:
            runtime = _runtime()
            options = {}
            if self.reference_conditioned:
                options["pocket"] = runtime.OfflineTtsPocketModelConfig(
                    **{key: str(model_dir / file) for key, file in zip(
                        ("lm_flow", "lm_main", "encoder", "decoder", "text_conditioner", "vocab_json", "token_scores_json"),
                        PACKAGES[POCKET][2],
                    )}
                )
            else:
                options["vits"] = runtime.OfflineTtsVitsModelConfig(
                    model=str(model_dir / "uk_UA-lada-x_low.onnx"),
                    tokens=str(model_dir / "tokens.txt"), data_dir=str(model_dir / "espeak-ng-data"),
                )
            config = runtime.OfflineTtsConfig(model=runtime.OfflineTtsModelConfig(
                provider="cpu", num_threads=min(4, os.cpu_count() or 1), **options,
            ))
            if not config.validate():
                raise StudioError("The installed voice files failed validation.", 503)
            engine = runtime.OfflineTts(config)
        try:
            yield engine
        finally:
            del engine

    def synthesize(self, engine, text, voice, speed, reference=None):
        if self.model_id == holos.MODEL_ID:
            return engine.synthesize(text, speed)
        if self.reference_conditioned:
            config = _runtime().GenerationConfig()
            config.speed = speed
            config.reference_audio, config.reference_sample_rate = reference
            return engine.generate(text, config)
        sid = next(index for index, (voice_id, _) in enumerate(self.voices) if voice_id == voice)
        return engine.generate(text, sid=sid, speed=speed)


class OpenVoiceProvider(SpeechProvider):
    @contextmanager
    def load(self, model_dir, reference_path=None, *, language=None, additional=()):
        if reference_path is None:
            raise StudioError("Choose an authorized voice profile.")
        base_id, base_voice = OPENVOICE_BASE[language]
        converter = openvoice.LocalOpenVoice(model_dir, reference_path)
        base = PROVIDERS[base_id]
        with base.load(model_dir.parent / base_id) as engine:
            yield converter, base, engine, base_voice

    def synthesize(self, engine, text, voice, speed, reference=None):
        converter, base, base_engine, base_voice = engine
        source = base.synthesize(base_engine, text, base_voice, speed)
        if not len(source.samples):
            raise StudioError("The base voice produced no speech to convert.")
        return converter.convert(source.samples, source.sample_rate)


class ZonosProvider(SpeechProvider):
    @contextmanager
    def load(self, model_dir, reference_path=None, *, language=None, additional=()):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix="zonos-speech-") as directory:
            references = (reference_path, *additional) if reference_path else ()
            yield zonos.LocalZonos(model_dir, Path(directory), references)

    def synthesize(self, engine, text, voice, speed, reference=None):
        return engine.synthesize(text)


PROVIDERS = {
    MODEL_ID: SpeechProvider(MODEL_ID, ("en",), VOICE_NAMES),
    LADA: SpeechProvider(LADA, ("uk",), (("lada", "Lada"),)),
    openvoice.MODEL_ID: OpenVoiceProvider(openvoice.MODEL_ID, ("en", "uk"), (), reference_conditioned=True,
                                          reference_languages=("en", "uk")),
    POCKET: SpeechProvider(POCKET, ("en",), (), reference_conditioned=True, research_only=True,
                          reference_languages=("en",)),
    holos.MODEL_ID: SpeechProvider(holos.MODEL_ID, ("uk",), (), reference_conditioned=True,
                                  reference_languages=("uk",), multi_reference=True),
    zonos.MODEL_ID: ZonosProvider(zonos.MODEL_ID, ("uk", "en"), (("narrator", "Generated voice"),),
                                  reference_conditioned=True, reference_languages=("uk", "en"),
                                  multi_reference=True, native_subprocesses=True),
}


def generate(model_dir, request, destination, reference_path=None, additional=()):
    import numpy as np
    from backend.studio.audio import MAX_SECONDS, pcm

    provider = PROVIDERS[request.model_id]
    if provider.research_only and not request.noncommercial_confirmed:
        raise StudioError("Confirm noncommercial use for the selected checkpoint.")
    if provider.uses_reference(request.voice) and reference_path is None:
        raise StudioError("This model requires an authorized voice reference.")
    # Keep neighboring sentences in the same native generation so it can follow paragraph-level pacing.
    section_options = {"limit": 360, "split_sentences": False} if provider.model_id == zonos.MODEL_ID else {}
    original = list(sections(request.text, **section_options))
    pronunciation = request.normalized_text
    if request.blocks is not None:
        parts = []
        for block in request.blocks:
            fragments = list(sections(block.text, **section_options))
            for index, (label, paragraph) in enumerate(fragments):
                gap = request.pause_after("paragraph") if paragraph else request.pause_ms
                if index == len(fragments) - 1:
                    gap = request.pause_after(block.kind) if block.pause_after_ms is None else block.pause_after_ms
                parts.append((label, normalize(label, request.language, request.pronunciation), gap))
    elif pronunciation is not None:
        spoken = list(sections(pronunciation, **section_options))
        captions = [text for text, _ in original] if len(spoken) == len(original) else [text for text, _ in spoken]
        parts = [(label, text, request.pause_after("paragraph") if paragraph else request.pause_ms)
                 for label, (text, paragraph) in zip(captions, spoken)]
    else:
        parts = [(text, normalize(text, request.language, request.pronunciation),
                  request.pause_after("paragraph") if paragraph else request.pause_ms)
                 for text, paragraph in original]
    chunks, cues = [], []
    offset = 0
    reference = None
    if reference_path and provider.model_id == POCKET:
        samples, rate = pcm(reference_path)
        reference = (samples.tolist(), rate)
    with provider.load(model_dir, reference_path, language=request.language, additional=additional) as engine:
        for label, text, pause in parts:
            result = provider.synthesize(engine, text, request.voice, request.speed, reference)
            samples = np.asarray(result.samples, dtype=np.float32)
            if len(samples) == 0 or not np.all(np.isfinite(samples)) or np.max(np.abs(samples)) < 0.00001:
                raise StudioError("The voice did not synthesize audible speech for a section.")
            if result.sample_rate != request.sample_rate:
                size = max(1, round(len(samples) * request.sample_rate / result.sample_rate))
                samples = np.interp(np.arange(size) * result.sample_rate / request.sample_rate, np.arange(len(samples)), samples)
            if (offset + len(samples)) / request.sample_rate > MAX_SECONDS:
                raise StudioError("This speech would exceed 20 minutes. Split the text into separate assets.")
            cues.append({"start": offset / request.sample_rate, "end": (offset + len(samples)) / request.sample_rate, "text": label})
            chunks.append(samples)
            gap = request.sample_rate * pause // 1000
            if request.trailing_pause and (offset + len(samples) + gap) / request.sample_rate > MAX_SECONDS:
                raise StudioError("This speech and its pauses would exceed 20 minutes.")
            chunks.append(np.zeros(gap, dtype=np.float32))
            offset += len(samples) + gap
        if not chunks:
            raise StudioError("Enter text containing something to pronounce.")
    del engine
    write_wave(destination, np.concatenate(chunks if request.trailing_pause else chunks[:-1]),
               request.sample_rate, request.normalize_audio)
    return cues


def worker_command(model_dir, request, work_dir, reference=None, additional=()):
    payload = work_dir / "request.json"
    payload.write_text(request.model_dump_json(), encoding="utf-8")
    return [sys.executable, "-B", "-m", "backend.studio.speech", str(model_dir), str(payload),
            *(map(str, (reference, *additional)) if reference else ())]


def worker_environment(model):
    return model_install.offline_environment() if model in PINNED_MODELS else None


if __name__ == "__main__":
    model, payload = map(Path, sys.argv[1:3])
    request = SpeechRequest.model_validate_json(payload.read_text(encoding="utf-8"))
    try:
        refs = [Path(path) for path in sys.argv[3:]]
        cues = generate(model, request, payload.parent / "audio.wav", refs[0] if refs else None, refs[1:])
    except StudioError as error:
        (payload.parent / "error.txt").write_text(str(error), encoding="utf-8")
        sys.exit(1)
    peak_rss_bytes = None
    if sys.platform != "win32":
        import resource
        peak_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    (payload.parent / "result.json").write_text(
        json.dumps({"cues": cues, "peak_rss_bytes": peak_rss_bytes}, ensure_ascii=False), encoding="utf-8",
    )
