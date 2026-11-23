"""Generate a narrated Ukrainian watercolor video from one topic on Apple Silicon."""

from __future__ import annotations

import argparse
import json
import re
from threading import Lock
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.ai.generation import GenerationError, _parse_json
from backend.ai.mamay import MamaySettings, load_settings
from backend.ai.length import WordRange, bounds
from backend.avatar.service import _binary
from backend.platform.paths import application_directories
from backend.scenes.benchmark import BenchmarkError, benchmark, model_cache, prepare
from backend.scenes.illustrated_render import (
    FPS, RESOLUTIONS, SCENE_HOLD, assemble, caption_font, fit_narration, narration_tempo, render_scene, shorten_narrations,
)
from backend.scenes.profiles import weights
from backend.scenes.illustrated_thumbnail import compose_thumbnail, thumbnail_prompt
from backend.studio import flux, zonos
from backend.studio.speech import LADA, PACKAGES, SpeechBlock, SpeechRequest, generate
from backend.studio.text import BIBLE_REFERENCE

NARRATORS = {"lada": (LADA, "lada"), "zonos": (zonos.MODEL_ID, "narrator")}
VISUAL_STYLES = {
    "watercolor": "Vintage storybook vignette with fine brown pencil lines, soft warm watercolor and textured ivory paper.",
    "ink": "Black-ink devotional sketch on white paper, with crisp outlines, pen hatching and minimal shading.",
    "cinematic": "Cinematic devotional illustration with warm sunrise light, atmospheric depth and lifelike detail.",
    "biblical": (
        "Epic biblical digital illustration with richly detailed natural materials and a premium three-dimensional rendered "
        "finish, luminous golden sunrise rays, amber rim lighting, a blue and peach sky, saturated colors, "
        "and cinematic depth. Biblical-era landscapes, objects, symbols, and clothing. "
        "When the scene includes people, render natural human proportions, lifelike skin and hair, "
        "flowing fabric, and understated, natural expressions and gestures."
    ),
}
WRITER_LOCK = Lock()
WRITING_MODEL = weights(
    "INSAIT-Institute/MamayLM-Gemma-3-12B-IT-v2.0-GGUF",
    "a916e05fe7f400e48e79519e22598e802c58cc82",
    "MamayLM-Gemma-3-12B-IT-v2.0-Q4_K_M.gguf",
    7_300_778_784,
    "aeb8e3676b5af63e84bd9ecbd0e705ab252f44f749d34baab1ee511e786b2863",
)


def writing_dir(data):
    return data / "models" / "illustrated-video"


def install_writing_model(data, *, cancel=None, progress=None):
    directory = writing_dir(data)
    with model_cache(directory):
        return prepare(directory, WRITING_MODEL, download=True, cancel=cancel, progress=progress)


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    narration: str = Field(min_length=25, max_length=8000)
    caption: str = Field(min_length=4, max_length=86)
    illustration: str = Field(min_length=12, max_length=310)
    section: Literal["scripture", "poem", "paragraph"] = "paragraph"

    @field_validator("caption")
    @classmethod
    def normalize_spacing(cls, text):
        return " ".join(text.split())

    @field_validator("narration")
    @classmethod
    def keep_paragraphs(cls, text):
        return "\n\n".join("\n".join(" ".join(line.split()) for line in paragraph.splitlines())
                            for paragraph in re.split(r"\n\s*\n", text.strip()))

    @field_validator("caption")
    @classmethod
    def caption_length(cls, caption):
        if not 2 <= len(caption.split()) <= 10:
            raise ValueError("On-screen text should contain two to ten words.")
        return caption


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=4, max_length=100)
    bible_text: Optional[str] = Field(default=None, min_length=1, max_length=6000)
    bible_reference: Optional[str] = Field(default=None, min_length=4, max_length=70)
    scenes: list[Scene]


def with_spoken_title(script):
    """Keep the title in the narration sent to TTS, including after an editor pass."""
    if not script.scenes:
        return script
    title = " ".join(script.title.split())
    opening = script.scenes[0]
    if script.bible_text is not None and script.bible_reference is not None:
        spoken_title = title if title[-1] in ".!?…:;—–" else title + "."
        first = Scene.model_validate({**opening.model_dump(), "section": "scripture",
            "narration": "\n\n".join((spoken_title, script.bible_text, script.bible_reference))})
        return script.model_copy(update={"scenes": [first, *script.scenes[1:]]})
    if re.match(re.escape(title) + r"(?=$|[\s.,!?…:;—–])", opening.narration, flags=re.IGNORECASE):
        return script
    separator = " " if title[-1] in ".!?…:;—–" else ". "
    first = Scene.model_validate({**opening.model_dump(), "narration": title + separator.rstrip() + "\n\n" + opening.narration})
    return script.model_copy(update={"scenes": [first, *script.scenes[1:]]})


def separate_bible_opening(script, reference):
    """Expose legacy saved openings as separate fields without inventing or rewriting Scripture."""
    if not script.scenes or script.scenes[0].section != "scripture":
        return script
    if script.bible_text is not None and script.bible_reference is not None:
        return with_spoken_title(script)
    if not reference:
        return script
    text = re.sub(r"^" + re.escape(script.title) + r"(?=$|[\s.,!?…:;—–])[.!?…:;—–]*\s*", "",
                  script.scenes[0].narration, count=1, flags=re.IGNORECASE).strip()
    pattern = re.escape(reference).replace(r"\ ", r"\s+")
    match = re.search(pattern + r"(?!\d)", text, flags=re.IGNORECASE)
    if match is None:
        match = BIBLE_REFERENCE.search(text)
        if match:
            reference = match.group()
    tail = ""
    if match:
        if not text[:match.start()].strip(" \n\t(—–-:"):
            text = text[match.end():].lstrip(" \n\t).—–-:")
        else:
            tail = text[match.end():].lstrip(" \n\t).—–-:")
            text = text[:match.start()].rstrip(" \n\t(—–-:")
    if not text:
        return script
    scenes = [scene.model_copy(deep=True) for scene in script.scenes]
    if tail:
        if len(scenes) < 2:
            return script
        scenes[1].narration = tail + "\n\n" + scenes[1].narration
    structured = Script.model_validate({**script.model_dump(), "bible_text": text,
                                       "bible_reference": reference, "scenes": [scene.model_dump() for scene in scenes]})
    return with_spoken_title(structured)


def write_json(path, value):
    temporary = path.with_suffix(".pending.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def job_lease(directory):
    import fcntl

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("A pipeline run is already using this output directory.") from error
        yield


def matches_language(text, language):
    if language == "en":
        return bool(re.search("[A-Za-z]", text) and not re.search("[А-Яа-яІіЇїЄєҐґ]", text))
    return bool(re.search("[А-Яа-яІіЇїЄєҐґ]", text) and not re.search("[ыэъёЫЭЪЁ]", text))


@contextmanager
def writing_model(model_path, seed, check=lambda: None, context_size=8192):
    from llama_cpp import Llama

    while not WRITER_LOCK.acquire(timeout=.2):
        check()
    try:
        check()
        try:
            model = Llama(model_path=str(model_path), n_ctx=context_size, n_threads=6, n_gpu_layers=-1,
                          n_batch=128, n_ubatch=128,
                          seed=seed, verbose=False)
        except (OSError, ValueError) as error:
            raise ValueError("Could not load the local writing model. Run on Apple Silicon with Metal access.") from error
        with closing(model):
            yield model
    finally:
        WRITER_LOCK.release()


def script_response(model, schema, prompt, *, seed, check, max_tokens, temperature=.5, progress=None):
    check()
    available = model.n_ctx() - len(model.tokenize(prompt.encode("utf-8"))) - 256
    if available < 256:
        raise ValueError("MamayLM's context is full. Reduce the examples, rules, or scene count.")
    response = model.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object", "schema": schema},
        stream=True, temperature=temperature, top_p=.9,
        max_tokens=min(available, max_tokens), seed=seed,
    )
    content = []
    finish_reason = None
    with closing(response) as stream:
        for chunk in stream:
            check()
            for choice in chunk["choices"]:
                content.append(choice["delta"].get("content") or "")
                finish_reason = choice["finish_reason"]
                if progress and len(content) % 64 == 0:
                    progress("".join(content))
    if finish_reason == "length":
        raise ValueError("MamayLM's response was cut off. Reduce the examples or scene count.")
    return _parse_json("".join(content))


def read_script(value, scene_count, language):
    # A model can put an English image description in caption despite the schema's
    # character limit. Derive invalid captions from the actual spoken words.
    if isinstance(value, dict) and isinstance(value.get("scenes"), list):
        value = {**value, "scenes": [dict(scene) if isinstance(scene, dict) else scene for scene in value["scenes"]]}
        for scene in value["scenes"]:
            if not isinstance(scene, dict) or not isinstance(scene.get("narration"), str):
                continue
            caption = scene.get("caption")
            normalized = " ".join(caption.split()) if isinstance(caption, str) else ""
            if 4 <= len(normalized) <= 86 and 2 <= len(normalized.split()) <= 10 and matches_language(normalized, language):
                continue
            illustration = scene.get("illustration")
            if usable_illustration(normalized) and (not isinstance(illustration, str)
                                                    or not usable_illustration(illustration) or len(illustration) < 12):
                scene["illustration"] = normalized
            words = scene["narration"].split()
            for count in range(min(8, len(words)), 1, -1):
                candidate = " ".join(words[:count])
                if 4 <= len(candidate) <= 86:
                    scene["caption"] = candidate
                    break
    script = Script.model_validate(value)
    if len(script.scenes) != scene_count:
        raise ValueError(f"Expected {scene_count} scenes.")
    if any(not matches_language(text, language) for scene in script.scenes for text in (scene.narration, scene.caption)):
        raise ValueError("The script did not match the selected language.")
    if script.bible_text is not None and not matches_language(script.bible_text, language):
        raise ValueError("The Bible text did not match the selected language.")
    return script


def word_count(script):
    return sum(len(scene.narration.split()) for scene in script.scenes)


def scene_progress(report, label, scene_count):
    last = 0

    def update(partial):
        nonlocal last
        current = min(scene_count, len(re.findall(r'"narration"\s*:', partial)))
        if current > last:
            report(f"{label}: scene {current} of {scene_count}.")
            last = current
    return update


def usable_illustration(text):
    return (len(text.split()) >= 2 and not re.search(r"[{}<>`\n\rА-Яа-яІіЇїЄєҐґ]", text)
            and bool(re.search(r"[A-Za-z]", text)))


def repair_illustrations(model, script, seed, check, report):
    """Keep leaked chat/JSON formatting out of prompts passed to the image model."""
    scenes = list(script.scenes)
    schema = {"type": "object", "properties": {"illustration": {"type": "string"}},
              "required": ["illustration"], "additionalProperties": False}
    for index, scene in enumerate(scenes):
        if usable_illustration(scene.illustration):
            continue
        report(f"Repairing the illustration description for scene {index + 1} of {len(scenes)}.")
        prompt = ("Return one JSON object containing illustration. In that field, write one short, complete English "
                  "sentence describing a concrete visible action, object, landscape or biblical symbol suitable "
                  "for this narration. Use 8–35 words. Describe the subject and setting, without lettering, captions, "
                  "code, Markdown or dialogue.\nNarration: " + scene.narration)
        last_error = ""
        for attempt in range(2):
            try:
                result = script_response(model, schema, prompt, seed=seed + index + attempt, check=check,
                                         max_tokens=180, temperature=.3)
                repaired = Scene.model_validate({**scene.model_dump(), "illustration": result["illustration"]})
                if not usable_illustration(repaired.illustration):
                    raise ValueError("Use a single English illustration sentence without code or formatting.")
                scenes[index] = repaired
                break
            except (GenerationError, KeyError, TypeError, ValueError) as error:
                last_error = str(error)
                prompt += "\nCorrection: " + last_error
        else:
            raise ValueError(f"Could not repair the illustration description for scene {index + 1}: {last_error}")
    return script.model_copy(update={"scenes": scenes})


def resize_script(model, script, schema, settings, language, target, seed, check, report, limits=None):
    """Revise the closest draft instead of discarding it and repeating the same request."""
    best = script
    lower, upper = bounds(target, limits)
    locked_opening = script.scenes[0].section == "scripture" and len(script.scenes) > 1
    first_editable = 1 if locked_opening else 0
    last_error = ""
    for attempt in range(2):
        actual = word_count(best)
        direction = "expand" if actual < target else "shorten"
        report(f"Draft has {actual} words; adjusting to about {target} words (pass {attempt + 1} of 2).")
        fixed = sum(len(scene.narration.split()) for scene in best.scenes[:first_editable])
        editable = best.scenes[first_editable:]
        weights = [len(scene.narration.split()) for scene in editable]
        remaining = max(len(editable), target - fixed)
        shares = [(remaining - len(editable)) * weight / sum(weights) for weight in weights]
        goals = [1 + int(share) for share in shares]
        for index in sorted(range(len(shares)), key=lambda i: shares[i] - int(shares[i]), reverse=True)[:remaining - sum(goals)]:
            goals[index] += 1
        plan = "; ".join(f"scene {index + first_editable + 1}: about {goal} words"
                         for index, goal in enumerate(goals))
        prompt = (
            settings.instructions(language) + "\n\n"
            f"Revise the supplied draft to reach {target} spoken words total ({lower}–{upper}). "
            f"It currently contains {actual} spoken words. You must {direction} the narration substantially. "
            "Count only narration, including the spoken title. Keep the title, scene count, section tags and "
            "illustration descriptions unchanged. Keep the selected language and biblical meaning. "
            "Develop the existing ideas with meaningful detail when expanding, and remove redundant thoughts "
            "when shortening. Avoid padding, repetition, and invented Bible quotations. Preserve paragraph breaks. "
            "Update each caption to a short phrase from that scene's narration. Return the complete revised JSON.\n"
            + (f"Keep the opening Bible scene unchanged; it already has {fixed} words. " if locked_opening else "")
            + f"Use this word allocation for the editable scenes: {plan}.\n"
            + "The supplied examples guide tone only; the required word count takes priority over their length.\n"
            + ("Previous correction: " + last_error + "\n" if last_error else "")
            + "Draft to revise:\n" + best.model_dump_json()
        )
        try:
            result = script_response(model, schema, prompt, seed=seed + attempt, check=check,
                                     max_tokens=max(360 * len(best.scenes), target * 6 + 200 * len(best.scenes)),
                                     temperature=.35, progress=scene_progress(report, "Adjusting narration", len(best.scenes)))
            proposed = read_script(result, len(best.scenes), language)
            scenes = [before if index < first_editable else Scene(
                narration=after.narration, caption=after.caption, illustration=before.illustration, section=before.section)
                for index, (before, after) in enumerate(zip(best.scenes, proposed.scenes))]
            revised = with_spoken_title(script.model_copy(update={"scenes": scenes}))
            count = word_count(revised)
            if lower <= count <= upper:
                report(f"Narration length ready: {count} words (target {target}).")
                return revised
            last_error = f"The revised narration contained {count} words; {lower}–{upper} are required."
            if abs(count - target) < abs(actual - target):
                best = revised
        except (GenerationError, ValueError) as error:
            last_error = str(error)
    raise ValueError(f"MamayLM did not reach the requested {target}-word text length "
                     f"(closest draft: {word_count(best)} words). {last_error}")


def edit_script(model, script, schema, settings, request, language, seed, check, word_budget=None, report=print, limits=None):
    if not settings.editor_enabled:
        return script
    prompt = (
        settings.instructions(language) + "\n\n" + settings.editor_rules + "\n\n"
        "Edit this draft. Return the same JSON structure and scene count. Preserve the title and illustration "
        "descriptions. Keep the original title spoken once at the start of the first narration. "
        "Preserve section tags, paragraph boundaries, and poem line breaks. "
        "Edit narration and captions in the selected language.\n"
        + request + "\n\nDraft\n" + script.model_dump_json()
    )
    last_error = None
    for attempt in range(2):
        try:
            result = script_response(model, schema, prompt, seed=seed + attempt, check=check,
                                     max_tokens=max(360 * len(script.scenes), (word_budget or 0) * 6 + 200 * len(script.scenes)),
                                     temperature=.35, progress=scene_progress(report, "Editing narration", len(script.scenes)))
            edited = read_script(result, len(script.scenes), language)
            scenes = [Scene(narration=after.narration, caption=after.caption, illustration=before.illustration,
                            section=before.section)
                      for before, after in zip(script.scenes, edited.scenes)]
            edited = with_spoken_title(script.model_copy(update={"scenes": scenes}))
            if word_budget is not None:
                actual = sum(len(scene.narration.split()) for scene in edited.scenes)
                lower, upper = bounds(word_budget, limits)
                if not lower <= actual <= upper:
                    report(f"Editing changed the length to {actual} words; restoring the {word_budget}-word target.")
                    return resize_script(model, edited, schema, settings, language, word_budget,
                                         seed + 30, check, report, limits=limits)
            return edited
        except (GenerationError, ValueError) as error:
            last_error = error
            prompt += "\nCorrection required: " + str(error)
    raise ValueError(f"MamayLM could not finish the editing pass. {last_error}") from last_error


def write_script(topic, seconds, scene_count, model_path, seed, *, language="uk", morning=False,
                 style="watercolor", check=lambda: None, writing_settings=None, report=print, text_words=None, text_range=None):
    rules = writing_settings or MamaySettings()
    schema = Script.model_json_schema()
    schema["properties"]["scenes"].update(minItems=scene_count, maxItems=scene_count)
    # The default voice spoke about 2.4 words per second in the demo. Leave time for scene holds.
    requested_range = WordRange.model_validate(text_range) if text_range is not None else None
    if requested_range is not None:
        text_words = requested_range.target
    limits = (requested_range.min_words, requested_range.max_words) if requested_range else None
    budget = text_words if text_words is not None else None if seconds is None else max(
        8 * scene_count, min(45 * scene_count, round((seconds - SCENE_HOLD * scene_count) * 2.4))
    )
    tolerance = max(3, round(budget * .12)) if budget is not None else 0
    lower, upper = limits or ((budget - tolerance, budget + tolerance) if budget is not None else (0, 0))
    instructions = (
        "Напиши сценарій короткого відео українською. "
        "У title дай коротку українську назву. У narration напиши текст озвучення. "
        "У caption дослівно наведи коротку виразну фразу (2–8 слів) із початку narration. "
        "In illustration, describe one visible action or still life in English. "
        "Use different objects and settings across scenes. Avoid lettering in all illustrations."
    )
    request = (f"Тема відео: {json.dumps(topic, ensure_ascii=False)}. Поверни один об'єкт JSON. "
               f"Кількість сцен: {scene_count}. ")
    if language == "en":
        instructions = (
            "Write an English short-video script. "
            "Write a short title, narration, and a 2–8 word caption taken from the narration. "
            "Describe one visible action or still life in English for each illustration. Avoid lettering in illustrations."
        )
        request = f"Topic: {json.dumps(topic)}. Return one JSON object with {scene_count} scenes. "
    if budget is None:
        request += ("Write two or three concise sentences of narration per scene." if language == "en"
                    else "Напиши два або три короткі речення озвучення на сцену.")
    elif language == "uk":
        request += (f"Цільова кількість слів у всіх полях narration разом — {budget}. "
                    f"Вкладися у {lower}–{upper} слів. "
                    f"Розподіли їх рівномірно, приблизно по {round(budget / scene_count)} слів на сцену.")
    else:
        request += (f"Use {lower}–{upper} spoken words total, "
                    f"about {round(budget / scene_count)} per scene.")
    if morning:
        instructions += " This video belongs to Morning Inspiration and reflects on the selected Bible passage."
    if style == "biblical":
        instructions += (" For illustration only, depict biblical-era scenes or symbols related to the selected "
                         "passage: ancient robes, shepherds, boats, hills, lilies or a cross. Keep the narration "
                         "relevant to the viewer's life. Each illustration should describe a concrete biblical subject.")
    instructions += "\n\n" + rules.instructions(language)
    instructions += ("\nInclude the exact title once at the start of the first scene's narration for the voiceover. "
                     "Leave room for it within the first narration's character limit.")
    instructions += ("\nPreserve paragraph breaks as double newlines in narration. Set section to scripture for a "
                     "Bible passage, poem for a poem or verse block, and paragraph for the main reflection. "
                     "Keep each complete Bible or poem block together in one scene. "
                     "Use these section names only as JSON metadata, never as words to speak.")
    if morning:
        instructions += (
            " Mandatory opening layout: title, then Bible text, then Bible reference, then the main reflection. "
            "Return bible_text containing only the Bible passage (or a clearly labeled paraphrase), without "
            "the video title or its reference. Return bible_reference containing only its book, chapter and verse. "
            "The opening narration must contain the title, a blank line, bible_text, another blank line, "
            "and bible_reference, in that order. Never put the reference before the passage. "
            "Set the opening section to scripture. Begin the reflection in the second scene."
        )
        schema["properties"]["bible_text"] = {"type": "string", "minLength": 1}
        schema["properties"]["bible_reference"] = {"type": "string", "minLength": 4, "maxLength": 70}
        schema["required"] = [*schema["required"], "bible_text", "bible_reference"]
    else:
        for key in ("bible_text", "bible_reference"):
            schema["properties"].pop(key, None)
    instructions += (
        " In every scene, narration and caption must use the selected spoken language. "
        "The caption must contain only 2–8 words copied from that narration. "
        "English image descriptions belong only in illustration, never in caption."
    )
    if text_words is None:
        schema["$defs"]["Scene"]["properties"]["narration"]["maxLength"] = 360
    else:
        # The bundled grammar compiler expands bounded strings into nested rules. Large
        # bounds can crash its native parser; validate the 8,000-character limit after parsing.
        schema["$defs"]["Scene"]["properties"]["narration"].pop("maxLength", None)
        request += (f" This word target includes the spoken title, Bible passage and main reflection together. "
                    f"Aim for {text_words} words total. Use as many sentences and paragraphs as needed.")
        if requested_range:
            request += f" The final narration must stay within {lower}–{upper} words, including after editing."
    # Short drafts fit in 8K; avoid reserving the larger Gemma SWA cache unnecessarily.
    context_size = 16384 if text_words is not None and (text_words > 200 or scene_count > 4) else 8192
    with writing_model(model_path, seed, check, context_size) as model:
        def finish(script):
            if rules.editor_enabled:
                report("Editing the narration for natural delivery.")
            edited = edit_script(model, script, schema, rules, request, language, seed + 10, check,
                                 word_budget=text_words, report=report, limits=limits)
            return repair_illustrations(model, edited, seed + 60, check, report)

        if model.metadata.get("general.architecture") == "qwen3":
            request += " /no_think"
        best_script = None
        best_error = float("inf")
        feedback = ""
        last_error = ""
        for attempt in range(3):
            report(f"Writing narration{f' toward {text_words} words' if text_words else ''} (attempt {attempt + 1} of 3).")
            try:
                response = script_response(model, schema, instructions + "\n\n" + request + feedback,
                                           check=check, seed=seed + attempt,
                                           max_tokens=max(360 * scene_count, (text_words or 0) * 6 + 200 * scene_count),
                                           temperature=.5 + attempt * .07,
                                           progress=scene_progress(report, "Writing narration", scene_count))
                script = with_spoken_title(read_script(response, scene_count, language))
                if morning and script.scenes:
                    script.scenes[0].section = "scripture"
            except (GenerationError, ValueError) as error:
                last_error = str(error)
                feedback = " Correct the previous draft error: " + last_error
            else:
                if budget is None:
                    return finish(script)
                actual = sum(len(scene.narration.split()) for scene in script.scenes)
                report(f"Draft length: {actual} words; target {budget}.")
                difference = abs(actual - budget)
                if lower <= actual <= upper:
                    return finish(script)
                if difference < best_error:
                    best_script, best_error = script, difference
                if text_words is not None:
                    break
                direction = "shorter" if actual > budget else "longer"
                feedback = f" The previous draft used {actual} spoken words. Make the narration {direction}."
            if attempt < 2:
                print("Retrying the script to fit the scene and narration budget.", flush=True)
        if best_script is not None:
            final_lower, final_upper = bounds(text_words, limits) if text_words is not None else (0, 0)
            if text_words is not None and not final_lower <= word_count(best_script) <= final_upper:
                best_script = resize_script(model, best_script, schema, rules, language, text_words,
                                            seed + 20, check, report, limits=limits)
            return finish(best_script)
        raise ValueError(f"The writing model did not produce a usable script. {last_error}")


def background_prompt(scene, side, style="watercolor", portrait=False, captions="handwritten"):
    art = "right" if side == "left" else "left"
    layout = "Use the full frame." if captions == "off" else (
        "Place the subject in the upper half. Leave the lower third quiet for captions."
        if portrait else f"Draw on the {art} half and leave the {side} half quiet for captions."
    )
    return (
        f"{'Vertical 9:16' if portrait else 'Wide 16:9'} composition. {VISUAL_STYLES[style]} {layout} "
        f"The illustration shows: {scene.illustration} No captions, readable words, logos, or watermarks."
    )


def scene_blocks(scene, title=None, *, bible_text=None, bible_reference=None):
    text = scene.narration
    blocks = []
    if title:
        match = re.match(re.escape(" ".join(title.split())) + r"(?=$|[\s.,!?…:;—–])[.!?…:;—–]*", text, re.I)
        if match:
            blocks.append(SpeechBlock(text=text[:match.end()], kind="title"))
            text = text[match.end():].strip()
    if bible_text is not None and bible_reference is not None:
        blocks.append(SpeechBlock(text=bible_text + "\n\n" + bible_reference, kind="scripture"))
        return blocks
    if text:
        # A writer can put the reflection after a quoted passage in the opening scene.
        # Retain a real boundary after that passage instead of delaying its block pause.
        quoted = re.match(r'(?s)^[^\n«“"]{0,120}?(?:«[^»]+»|“[^”]+”|"[^"]+")[.!?…]?', text) if scene.section == "scripture" else None
        if quoted:
            end = quoted.end()
            tail = text[end:]
            leading = re.match(r"^[\s(—–-]*", tail).end()
            reference = BIBLE_REFERENCE.match(tail, leading)
            if reference:
                end += reference.end()
                ending = re.match(r"^[).!?…]*", text[end:])
                end += ending.end()
            blocks.append(SpeechBlock(text=text[:end], kind="scripture"))
            text = text[end:].strip()
            blocks.extend(SpeechBlock(text=paragraph, kind="paragraph") for paragraph in re.split(r"\n\s*\n", text)
                          if paragraph.strip())
            return blocks
        paragraphs = re.split(r"\n\s*\n", text) if scene.section == "paragraph" else [text]
        blocks.extend(SpeechBlock(text=paragraph, kind=scene.section) for paragraph in paragraphs if paragraph.strip())
    return blocks


def structured_pauses(speech):
    return speech is not None and any(getattr(speech, key) is not None for key in
                                     ("title_pause_ms", "block_pause_ms", "paragraph_pause_ms"))


def synthesize(directory, scene, voice, data, *, voice_lease=nullcontext, speech=None, references=(), title=None,
               bible_text=None, bible_reference=None):
    from backend.studio.speech import PINNED_MODELS

    model, speaker = NARRATORS[voice]
    request = (speech.model_copy(update={"text": scene.narration}) if speech else
               SpeechRequest(text=scene.narration, language="uk", model_id=model, voice=speaker, pause_ms=165))
    if structured_pauses(speech):
        request = SpeechRequest.model_validate({**request.model_dump(), "blocks": scene_blocks(scene, title,
                                               bible_text=bible_text, bible_reference=bible_reference),
                                               "trailing_pause": True})
    model = request.model_id
    wav = directory / "narration.wav"
    timings = directory / "timings.json"
    if wav.is_file() and timings.is_file():
        return json.loads(timings.read_text(encoding="utf-8"))
    model_dir = data / "voice" / model
    files = PINNED_MODELS[model].FILES if model in PINNED_MODELS else PACKAGES.get(model, (None, None, ()))[2]
    if any(not (model_dir / name).is_file() for name in files):
        raise ValueError(f"Install {model} in Models & Tools before generating narration.")
    pending = directory / "narration.pending.wav"
    with voice_lease(model):
        cues = generate(model_dir, request, pending, references[0] if references else None, references[1:])
    pending.replace(wav)
    write_json(timings, cues)
    return cues


class Cancellation:
    def __init__(self, check):
        self.check = check

    def is_set(self):
        self.check()
        return False


def illustrate(directory, scene, side, data, seed, *, check, progress, style="watercolor",
               portrait=False, captions="handwritten"):
    if (directory / "scene.png").is_file():
        return
    with TemporaryDirectory(prefix="flux-", dir=directory) as temporary:
        work = Path(temporary)
        try:
            report = benchmark("image", data / "scene_models", False, run_dir=work,
                               prompt=background_prompt(scene, side, style, portrait, captions), seed=seed,
                               image_size=(576, 1024) if portrait else (1024, 576),
                               cancel=Cancellation(check), progress=progress)
        except BenchmarkError:
            if (work / "engine.log").is_file():
                (work / "engine.log").replace(directory / "image-error.log")
            raise
        write_json(directory / "image.json", {key: report[key] for key in ("prompt", "seed", "weights")})
        (work / "scene.png").replace(directory / "scene.png")


def print_progress(message, _fraction):
    print(message, flush=True)


def generate_thumbnail(output, script, args, *, check, report):
    target = output / "thumbnail.png"
    if target.is_file():
        return target
    check()
    report("Generating the video thumbnail.", .97)
    work = output / "cover"
    work.mkdir(exist_ok=True)
    background = work / "scene.png"
    if not background.is_file():
        result = benchmark("image", args.data_dir.resolve() / "scene_models", False, run_dir=work,
                           prompt=thumbnail_prompt(script.scenes[0],
                                                   VISUAL_STYLES[getattr(args, "style", "watercolor")]),
                           seed=args.seed + 100, image_size=(1024, 576), cancel=Cancellation(check))
        write_json(work / "image.json", {key: result[key] for key in ("prompt", "seed", "weights")})
    check()
    compose_thumbnail(background, script.title, target)
    return target


def produce(args, *, report=print_progress, check=lambda: None, voice_lease=nullcontext):
    output = args.output.resolve()
    data = args.data_dir.resolve()
    model = (args.model or writing_dir(data) / WRITING_MODEL.name).resolve()
    scene_count = args.scenes
    font = caption_font(args.font)
    language = getattr(args, "language", "uk")
    style = getattr(args, "style", "watercolor")
    resolution = getattr(args, "resolution", "720p")
    captions = getattr(args, "captions", "handwritten")
    speech = getattr(args, "speech", None)
    block_pauses = structured_pauses(speech)
    text_words = getattr(args, "text_words", None)
    text_range = getattr(args, "text_range", None)
    provided_script = getattr(args, "script", None)
    morning = getattr(args, "morning", False)
    size = RESOLUTIONS[resolution]
    settings = {
        "version": 3,
        "topic": args.topic,
        "seconds": args.seconds,
        "scenes": scene_count,
        "voice": args.voice,
        "seed": args.seed,
        "model": str(model),
        "font": str(font.resolve()),
    }
    if speech or morning or (style, resolution, captions, language) != ("watercolor", "720p", "handwritten", "uk"):
        settings.update(version=4, language=language, style=style, resolution=resolution, captions=captions,
                        morning=morning, narrator=[speech.model_id, speech.voice] if speech else None)
    if speech and (speech.speed != 1 or speech.pause_ms != 165 or speech.pronunciation):
        settings["speech_options"] = {"speed": speech.speed, "pause_ms": speech.pause_ms,
                                      "pronunciation": speech.pronunciation}
    if block_pauses:
        settings["block_pauses"] = {key: getattr(speech, key) for key in
                                    ("title_pause_ms", "block_pause_ms", "paragraph_pause_ms")}
    if text_words is not None:
        settings["text_words"] = text_words
    if text_range is not None:
        settings["text_range"] = WordRange.model_validate(text_range).model_dump()
    if provided_script is not None:
        provided_script = Script.model_validate(provided_script)
        if provided_script.bible_text is not None and provided_script.bible_reference is not None:
            provided_script = with_spoken_title(provided_script)
        if len(provided_script.scenes) != scene_count:
            raise ValueError("The saved narration does not match the requested scene count.")
        settings["saved_script"] = provided_script.model_dump(exclude_none=True)
    with job_lease(output):
        manifest = output / "job.json"
        if manifest.exists():
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            compatible = [settings] if settings["version"] == 4 else [dict(settings, version=v) for v in (1, 2, 3)]
            if saved not in compatible:
                raise ValueError("Use the same options to resume or choose a new output directory.")
            settings = saved
        elif any(path.name not in (".lock", "job.pending.json") for path in output.iterdir()):
            raise ValueError("Choose a new or empty output directory.")
        else:
            write_json(manifest, settings)
        final = output / "video.mp4"
        if final.is_file():
            script = Script.model_validate_json((output / "script.json").read_text(encoding="utf-8"))
            generate_thumbnail(output, script, args, check=check, report=report)
            return final
        if not _binary("ffmpeg") or not _binary("ffprobe"):
            raise ValueError("Install FFmpeg with libx264 and FFprobe.")
        if not flux.available() or not flux.installed(data / "scene_models"):
            raise ValueError("Install FLUX.2 Klein 4B in Models & Tools on an Apple Silicon Mac.")
        script_path = output / "script.json"
        if script_path.is_file():
            script = Script.model_validate_json(script_path.read_text(encoding="utf-8"))
            if len(script.scenes) != scene_count:
                raise ValueError("The cached script does not match the scene count in job.json.")
            bad = [index for index, scene in enumerate(script.scenes, 1) if not usable_illustration(scene.illustration)]
            if bad:
                with writing_model(model, args.seed, check) as writer:
                    script = repair_illustrations(writer, script, args.seed + 60, check,
                                                  lambda message: report(message, .12))
                write_json(script_path, script.model_dump())
                for index in bad:
                    for filename in ("scene.png", "image.json", "render.json", "clip.mp4"):
                        (output / f"scene-{index:02d}" / filename).unlink(missing_ok=True)
        elif provided_script is not None:
            # Use the reviewed words verbatim; do not run another writing or editing pass.
            script = provided_script
            write_json(script_path, script.model_dump())
        else:
            if not model.is_file():
                raise ValueError(f"The writing model is missing: {model}. Run --install-writing-model or supply --model.")
            snapshot = output / "writing-settings.json"
            if snapshot.is_file():
                rules = MamaySettings.model_validate_json(snapshot.read_text(encoding="utf-8"))
            else:
                configured = getattr(args, "writing_settings", None)
                rules = MamaySettings.model_validate(configured) if configured is not None else load_settings(data)
                write_json(snapshot, rules.model_dump())
            report(f"Writing {scene_count} scenes.", .03)
            script = write_script(args.topic, args.seconds, scene_count, model, args.seed, language=language,
                                  morning=morning, style=style, check=check, writing_settings=rules,
                                  report=lambda message: report(message, .12), text_words=text_words, text_range=text_range)
            script = with_spoken_title(script)
            write_json(script_path, script.model_dump())

        prepared = []
        for index, scene in enumerate(script.scenes, 1):
            check()
            directory = output / f"scene-{index:02d}"
            directory.mkdir(exist_ok=True)
            if not (directory / "timings.json").is_file() or not (directory / "narration.wav").is_file():
                report(f"Generating narration for scene {index} of {scene_count}.", .16 + .12 * (index - 1) / scene_count)
            cues = synthesize(directory, scene, args.voice, data, voice_lease=voice_lease, speech=speech,
                              references=getattr(args, "references", ()), title=script.title if index == 1 else None,
                              bible_text=script.bible_text if index == 1 else None,
                              bible_reference=script.bible_reference if index == 1 else None)
            prepared.append((directory, scene, cues))

        check()
        sources = [directory / "narration.wav" for directory, _, _ in prepared]
        cues_by_scene = [cues for _, _, cues in prepared]
        audio = list(zip(sources, cues_by_scene))
        if settings["version"] >= 3 and args.seconds is not None and not block_pauses:
            audio = shorten_narrations(sources, cues_by_scene, args.seconds)
            if any(original != effective for original, (effective, _) in zip(sources, audio)):
                print("Shortened the narration at sentence boundaries.", flush=True)
        if settings["version"] >= 3:
            transcript = ("\n\n".join(block.text for index, (_, scene, _) in enumerate(prepared)
                                      for block in scene_blocks(scene, script.title if index == 0 else None,
                                          bible_text=script.bible_text if index == 0 else None,
                                          bible_reference=script.bible_reference if index == 0 else None))
                          if block_pauses else
                          "\n\n".join(" ".join(cue["text"] for cue in cues) for _, cues in audio))
            (output / "transcript.txt").write_text(transcript + "\n", encoding="utf-8")
        tempo = 1.0
        if settings["version"] >= 2 and args.seconds is not None and not block_pauses:
            tempo = narration_tempo([source for source, _ in audio], args.seconds)
        if tempo != 1.0:
            print(f"Fitting narration to the requested duration at {tempo:.1%} tempo.", flush=True)

        lengths, narrations = [], []
        for index, ((directory, scene, _), (source, cues)) in enumerate(zip(prepared, audio), 1):
            check()
            narration, cues = fit_narration(directory, source, tempo, cues, check=check)
            narrations.append(narration)
            state = directory / "render.json"
            if state.is_file() and (directory / "clip.mp4").is_file():
                lengths.append(json.loads(state.read_text(encoding="utf-8"))["frames"])
                print(f"Scene {index}/{scene_count} ready.", flush=True)
                continue
            side = "left" if index % 3 != 2 else "right"
            start, span = .30 + .64 * (index - 1) / scene_count, .64 / scene_count
            message = f"Illustrating scene {index} of {scene_count}."
            report(message, start)

            def image_progress(stage, fraction):
                if stage == "generating" and fraction:
                    report(message, start + .8 * span * fraction)

            illustrate(directory, scene, side, data, args.seed + index, check=check, progress=image_progress,
                       style=style, portrait=size[1] > size[0], captions=captions)
            report(f"Animating scene {index} of {scene_count}.", start + .8 * span)
            frames = render_scene(directory, scene.caption, side, font, cues, narration=narration, check=check,
                                  size=size, captions=captions, hold_seconds=0 if block_pauses else SCENE_HOLD)
            write_json(state, {"frames": frames})
            lengths.append(frames)

        report(f"Exporting the {sum(lengths) / FPS:.2f}-second MP4.", .95)
        assemble(output, lengths, script.title, narrations=narrations, check=check, size=size, language=language)
        generate_thumbnail(output, script, args, check=check, report=report)
        return final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", nargs="?", help="Video topic in Ukrainian")
    parser.add_argument("--output", type=Path, help="New job directory or directory to resume")
    parser.add_argument("--install-writing-model", action="store_true",
                        help="Download and verify the 7.3 GB MamayLM GGUF")
    parser.add_argument("--seconds", type=int, default=60, help="Approximate running time (15–120, default 60)")
    parser.add_argument("--scenes", type=int, help="Number of illustrations (2–15, inferred from seconds by default)")
    parser.add_argument("--voice", choices=NARRATORS, default="lada", help="Offline Ukrainian narration engine")
    parser.add_argument("--seed", type=int, default=1024)
    parser.add_argument("--data-dir", type=Path, default=application_directories().data, help="AI Blogger Studio data directory")
    parser.add_argument("--model", type=Path, help="Optional chat-capable GGUF for script generation")
    parser.add_argument("--font", type=Path, help="Optional font with Ukrainian glyphs")
    args = parser.parse_args()
    if args.install_writing_model:
        if args.topic or args.output or args.model:
            parser.error("Run --install-writing-model separately from a video job.")
        try:
            print(install_writing_model(args.data_dir.resolve()), flush=True)
        except (BenchmarkError, OSError) as error:
            parser.exit(1, f"{error}\n")
        except KeyboardInterrupt:
            parser.exit(130, "\nStopped. Rerun --install-writing-model to resume.\n")
        return
    if args.topic is None or args.output is None:
        parser.error("Provide a topic and --output.")
    args.topic = args.topic.strip()
    if not 1 <= len(args.topic) <= 500 or not 15 <= args.seconds <= 120:
        parser.error("Provide a topic of 1–500 characters and a duration of 15–120 seconds.")
    if args.scenes is not None and not 2 <= args.scenes <= 15:
        parser.error("Choose between 2 and 15 scenes.")
    if args.scenes is None:
        args.scenes = round(args.seconds / 8)
    if not 0 <= args.seed <= 2**31 - 17:
        parser.error("Use a seed between 0 and 2147483631.")
    try:
        print(produce(args), flush=True)
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "\nStopped. Run the same command to resume.\n")


if __name__ == "__main__":
    main()
