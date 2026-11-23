"""Run the illustrated-video pipeline in the persistent studio queue."""

import re
import shutil
from secrets import randbelow
from types import SimpleNamespace
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.avatar.service import _binary
from backend.ai.generation import GenerationError, _parse_json
from backend.ai.mamay import MamaySettings, load_settings
from backend.ai.length import WordRange
from backend.studio.morning_defaults import Narrator, load_defaults
from backend.scenes import illustrated_video as pipeline
from backend.scenes.illustrated_render import FPS, RATE, RESOLUTIONS, narration_samples
from backend.studio import flux, holos, openvoice, video, zonos
from backend.studio.audio import StudioError
from backend.studio.speech import LADA, PROVIDERS, SpeechRequest
from backend.studio.pronunciation import SpeechSettings, load_settings as load_speech_settings
from backend.voice.service import MODEL_ID as KOKORO

VIDEO_JOB = "illustrated_video"
MODEL_JOB = "illustrated_model"
TOPIC_JOB = "illustrated_topics"
MODEL_TERMS = "https://ai.google.dev/gemma/terms"
Language = Literal["uk", "en"]
VOICE_MODELS = {LADA: "Piper Lada", KOKORO: "Kokoro", holos.MODEL_ID: "HolosTTS",
                openvoice.MODEL_ID: "OpenVoice", zonos.MODEL_ID: "ZONOS3-Mini"}


class TopicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Language


class MorningTopic(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=5, max_length=110)
    passage: str = Field(min_length=4, max_length=70)
    angle: str = Field(min_length=12, max_length=240)

    @field_validator("passage")
    @classmethod
    def chapter_and_verse(cls, value):
        if not re.search(r"[0-9]+:[0-9]+", value):
            raise ValueError("Use a Bible chapter and verse reference.")
        return value


class MorningTopics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[MorningTopic] = Field(min_length=5, max_length=5)


class IllustratedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, max_length=500)
    seconds: Optional[int] = Field(default=None, ge=15, le=120)
    scenes: Optional[int] = Field(default=None, ge=2, le=15)
    text_words: Optional[int] = Field(default=None, ge=50, le=1000)
    text_range: Optional[WordRange] = None
    voice: Literal["lada", "zonos"] = "lada"
    language: Language = "uk"
    narrator: Optional[Narrator] = None
    style: Literal["watercolor", "ink", "cinematic", "biblical"] = "watercolor"
    resolution: Literal["720p", "1080p", "vertical"] = "720p"
    captions: Literal["handwritten", "clean", "off"] = "handwritten"
    passage: str = Field(default="", max_length=70)
    angle: str = Field(default="", max_length=240)
    writing_settings: Optional[MamaySettings] = None
    speech_settings: Optional[SpeechSettings] = None
    topic_id: Optional[UUID] = None
    topic_revision: Optional[int] = Field(default=None, ge=0)
    script: Optional[pipeline.Script] = None

    @model_validator(mode="after")
    def text_duration(self):
        if self.text_words is not None and self.text_range is not None:
            raise ValueError("Specify a word range or a legacy word target, not both.")
        if (self.text_words is not None or self.text_range is not None) and self.seconds is not None:
            raise ValueError("Use automatic video duration when specifying a text word count.")
        if self.script is not None and not 2 <= len(self.script.scenes) <= 15:
            raise ValueError("Saved narration must contain 2–15 scenes.")
        return self


def apply_defaults(data, request, project=None):
    saved = load_defaults(data, project)
    values = saved.model_dump(exclude={"revision"})
    supplied = request.model_dump(exclude_unset=True)
    if supplied.get("text_range") is not None or supplied.get("text_words") is not None:
        values["seconds"] = None
    if supplied.get("text_words") is not None or supplied.get("seconds") is not None:
        values["text_range"] = None
    if supplied.get("language", saved.language) != saved.language:
        values["narrator"] = None
    return IllustratedRequest.model_validate({**values, **supplied})


class WritingModelInstall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted_license: Literal[True]


def readiness(studio):
    model = pipeline.writing_dir(studio.data) / pipeline.WRITING_MODEL.name
    host = flux.available()
    return {
        "supported": host,
        "writing_model": model.is_file() and model.stat().st_size == pipeline.WRITING_MODEL.size,
        "writing_model_bytes": pipeline.WRITING_MODEL.size,
        "writing_model_terms": MODEL_TERMS,
        "illustrations": host and flux.installed(studio.data / "scene_models"),
        "rendering": bool(host and _binary("ffmpeg") and _binary("ffprobe") and {"libx264", "aac"} <= video.encoders())
        and studio.tools.preference("ffmpeg")[0] and studio.tools.preference("motion-engine")[0],
        "voices": [voice for voice, (model_id, _) in pipeline.NARRATORS.items()
                   if host and "uk" in studio.tools.ready_languages(model_id)],
    }


def require_host():
    if not flux.available():
        raise StudioError("Morning Inspiration requires Apple Silicon and macOS 26 or later.", 409)


def topics(studio, project, request):
    studio.project(project)
    require_host()
    model_path = pipeline.writing_dir(studio.data) / pipeline.WRITING_MODEL.name
    if not model_path.is_file() or model_path.stat().st_size != pipeline.WRITING_MODEL.size:
        raise StudioError("Install MamayLM to generate topics.", 409)
    language = "Ukrainian" if request.language == "uk" else "English"
    rules = getattr(request, "writing_settings", None) or load_settings(studio.data, project)
    prompt = (
        f"Generate five distinct Morning Inspiration video topics in {language}. Write every title, description and Bible book "
        f"name in {language}. Put a real, specific chapter and verse reference in passage, and a one-sentence "
        "reflection in angle.\n\n" + rules.instructions(request.language, topics=True)
    )
    seed = randbelow(2**31 - 3)
    try:
        with pipeline.writing_model(model_path, seed, studio.check) as model:
            for attempt in range(2):
                studio.check()
                result = model.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object", "schema": MorningTopics.model_json_schema()},
                    max_tokens=1000, temperature=.8, seed=seed + attempt,
                )
                studio.check()
                try:
                    batch = MorningTopics.model_validate(_parse_json(result["choices"][0]["message"]["content"]))
                    titles = {topic.title.casefold() for topic in batch.topics}
                    if len(titles) == len(batch.topics) and all(
                        pipeline.matches_language(text, request.language)
                        for topic in batch.topics for text in (topic.title, topic.angle, topic.passage)
                    ):
                        return batch
                except (GenerationError, ValueError):
                    continue
    except (RuntimeError, ValueError, OSError) as error:
        raise StudioError(str(error) or "The writing model could not generate topics.", 502) from error
    raise StudioError("The writing model could not generate five topics in the selected language. Try again.", 502)


def narrators(studio, project):
    studio.project(project)
    if not flux.available():
        return []
    profiles = [profile for profile in studio.profiles(project)
                if profile["assigned"] and profile["authorization_confirmed"]]
    options = []
    for model_id, model_name in VOICE_MODELS.items():
        provider = PROVIDERS[model_id]
        languages = studio.tools.ready_languages(model_id)
        if not languages:
            continue
        for voice, name in provider.voices:
            label = model_name if model_id in (LADA, zonos.MODEL_ID) else f"{name} · {model_name}"
            options.append(dict(model_id=model_id, voice=voice, name=label, languages=languages, cloned=False))
        for profile in profiles:
            if profile["language"] in provider.reference_languages:
                options.append(dict(model_id=model_id, voice=profile["id"], name=f"{profile['name']} · {model_name}",
                                    languages=languages, cloned=True))
    return options


def speech_request(studio, project, request):
    if request.narrator:
        model_id, voice = request.narrator.model_id, request.narrator.voice
        if model_id not in VOICE_MODELS:
            raise StudioError("Choose an available narrator.")
    elif request.language == "en":
        model_id, voice = KOKORO, PROVIDERS[KOKORO].voices[0][0]
    else:
        model_id, voice = pipeline.NARRATORS[request.voice]
    # New jobs snapshot saved defaults at enqueue time; older jobs retain their original delivery.
    settings = request.speech_settings or SpeechSettings()
    speech = SpeechRequest(text=request.topic, language=request.language, model_id=model_id, voice=voice,
                           pronunciation=settings.pronunciation, pause_ms=settings.pause_ms,
                           paragraph_pause_ms=settings.paragraph_pause_ms, title_pause_ms=settings.title_pause_ms,
                           block_pause_ms=settings.block_pause_ms,
                           speed=1 if model_id == zonos.MODEL_ID else settings.speed)
    studio.verify_speech(project, speech)
    return speech


def validate(studio, request, project):
    require_host()
    status = readiness(studio)
    if not status["writing_model"] and request.script is None:
        raise StudioError("Install MamayLM in Morning Inspiration.", 409)
    if not status["illustrations"]:
        raise StudioError("Install FLUX.2 Klein 4B in Models & Tools.", 409)
    if not status["rendering"]:
        raise StudioError("Enable FFmpeg and Pillow in Models & Tools. FFmpeg must support H.264 and AAC.", 409)
    return speech_request(studio, project, request)


def install(studio, job):
    require_host()
    model = pipeline.WRITING_MODEL
    percent = -1

    def progress(downloaded, total):
        nonlocal percent
        current = downloaded * 100 // total
        if current != percent:
            percent = current
            message = "Verifying MamayLM." if current == 100 else f"Downloading MamayLM ({current}%)."
            studio.update(job, "running", min(.99, downloaded / total), message)

    studio.update(job, "running", 0, f"Preparing the {model.size / 10**9:.1f} GB MamayLM download.")
    try:
        pipeline.install_writing_model(studio.data, cancel=pipeline.Cancellation(studio.check), progress=progress)
    except (RuntimeError, ValueError, OSError) as error:
        raise StudioError(str(error) or "The writing model could not be installed.", 502) from error


def generate(studio, project, job, request):
    validated_speech = validate(studio, request, project)
    speech = validated_speech if request.speech_settings is not None or request.narrator or request.language == "en" else None
    references = ()
    if speech and PROVIDERS[speech.model_id].uses_reference(speech.voice):
        references = (studio.reference_path(speech.voice),)
        if PROVIDERS[speech.model_id].multi_reference:
            references += tuple(studio.additional_references(speech.voice))
    directory = studio.directory(project, "work") / f"illustrated-{job}"
    topic = " — ".join(filter(None, (request.topic, request.passage, request.angle)))
    word_target = request.text_range.target if request.text_range is not None else request.text_words
    if request.script is not None:
        default_scenes = len(request.script.scenes)
    else:
        default_scenes = (max(2, min(15, round(word_target / 40))) if word_target is not None else
                          8 if request.seconds is None else round(request.seconds / 8))
    args = SimpleNamespace(**request.model_dump(exclude={"scenes", "topic"}), topic=topic,
                           data_dir=studio.data, output=directory, scenes=request.scenes or default_scenes,
                           model=None, font=None, seed=1024, speech=speech, references=references,
                           morning=bool(request.passage or request.narrator))
    try:
        result = pipeline.produce(args, check=studio.check, voice_lease=studio.tools.inference,
                                  report=lambda message, fraction: studio.update(job, "running", fraction, message))
    except (RuntimeError, ValueError, OSError) as error:
        raise StudioError(str(error) or "The illustrated video could not be generated.", 502) from error

    studio.check()
    script = pipeline.Script.model_validate_json((directory / "script.json").read_text(encoding="utf-8"))
    narration = directory / "narration.wav"
    publish = directory / "publish.mp4"
    shutil.copyfile(result, publish)
    studio.check()
    width, height = RESOLUTIONS[request.resolution]
    studio.record_asset(project, job, "export", script.title, {
        "extension": "mp4", "source_type": VIDEO_JOB, "topic": request.topic,
        "language": request.language, "width": width, "height": height, "fps": FPS,
        "duration_seconds": narration_samples(narration) / RATE,
        "size_bytes": result.stat().st_size,
        "transcript": (directory / "transcript.txt").read_text(encoding="utf-8"),
        "thumbnail": True,
    }, publish, ".mp4", thumbnail=directory / "thumbnail.png")
    # After the MP4 and transcript are published, the project no longer needs the render cache.
    shutil.rmtree(directory, ignore_errors=True)
