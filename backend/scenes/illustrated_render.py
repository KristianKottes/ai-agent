"""Compose illustrated scenes and animate Ukrainian text directly from font glyphs."""

from __future__ import annotations

import math
import re
import subprocess
import wave
from pathlib import Path
from tempfile import TemporaryFile
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

from backend.studio.audio import media_binary
from backend.studio.video import run, verify

FPS = 30
WIDTH, HEIGHT = 1280, 720
RESOLUTIONS = {"720p": (WIDTH, HEIGHT), "1080p": (1920, 1080), "vertical": (1080, 1920)}
RATE = 24_000
SAMPLES_PER_FRAME = RATE // FPS
SCENE_HOLD = .45
# Keep the voice and its pauses close to the original delivery.
MIN_TEMPO, MAX_TEMPO = .92, 1.12
INK = (68, 52, 35)
CHALKBOARD = Path("/System/Library/Fonts/Supplemental/ChalkboardSE.ttc")
NOTO_SANS = Path(__file__).resolve().parents[1] / "studio" / "NotoSans-Regular.ttf"


def caption_font(path: Path | None = None):
    font = path or (CHALKBOARD if CHALKBOARD.is_file() else NOTO_SANS)
    face = ImageFont.truetype(str(font), 50)
    missing = bytes(face.getmask("\u0378"))
    if any(bytes(face.getmask(letter)) == missing for letter in "ієїґІЄЇҐ’"):
        raise ValueError("Choose a font that supports Ukrainian glyphs, including ї, є, ґ, and і.")
    return font


def caption_rows(caption, font, width):
    remaining = caption.split()
    rows = [[]]
    for word in remaining:
        row = rows[-1]
        if row and font.getlength(" ".join([*row, word])) > width:
            row = []
            rows.append(row)
        row.append(word)
    return rows


def caption_cards(caption, font_path, size, scale=1):
    """Make one mask per line prefix so kerning and punctuation stay fixed during the reveal."""
    width, height = size
    for font_size in range(68, 29, -2):
        font = ImageFont.truetype(str(font_path), round(font_size * scale))
        rows = caption_rows(caption, font, width - 14)
        if len(rows) <= 4 and max(font.getlength(" ".join(row)) for row in rows) <= width - 14:
            break
    else:
        raise ValueError(f"The on-screen phrase is too wide to render: {caption}")

    complete = Image.new("L", size)
    masks = [complete.copy()]
    line_height = round(font_size * scale * 1.23)
    top = (height - len(rows) * line_height) // 2
    for index, row in enumerate(rows):
        position = (6, top + index * line_height)
        for end in range(1, len(row) + 1):
            mask = complete.copy()
            ImageDraw.Draw(mask).text(position, " ".join(row[:end]), font=font, fill=255, anchor="la")
            masks.append(mask)
        complete = masks[-1]
    return masks


def reveal_frames(masks, count, speech_start, speech_end):
    """Stagger the word fades over the scene's spoken phrase."""
    word_count = len(masks) - 1
    start = max(0.10, speech_start + 0.05)
    interval = min(0.46, max(0.12, (speech_end - start - 0.10) / word_count))
    # Give the finished phrase a reading hold before the scene cuts.
    start = min(start, max(0.10, count / FPS - 1.25 - word_count * interval))
    fade = min(0.20, interval)
    card = Image.new("RGBA", masks[0].size, (*INK, 0))
    previous, pixels = None, b""
    for frame in range(count):
        progress = (frame / FPS - start) / interval
        complete = max(0, min(word_count, math.floor(progress)))
        blend = 0
        if 0 <= progress < word_count:
            blend = min(255, round(255 * (progress - complete) * interval / fade))
        if (complete, blend) != previous:
            mask = masks[complete]
            if blend:
                mask = Image.blend(mask, masks[complete + 1], blend / 255)
            card.putalpha(mask)
            previous, pixels = (complete, blend), card.tobytes()
        yield pixels


def narration_samples(narration):
    with wave.open(str(narration), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (RATE, 1, 2):
            raise ValueError("Expected 24 kHz, mono, 16-bit PCM narration.")
        return audio.getnframes()


def narration_tempo(narrations, target_seconds):
    spoken = sum(narration_samples(path) for path in narrations) / RATE
    available = target_seconds - SCENE_HOLD * len(narrations)
    if abs(spoken - available) < 1 / FPS:
        return 1.0
    return max(MIN_TEMPO, min(MAX_TEMPO, spoken / available))


def shorten_narrations(sources, cues_by_scene, target_seconds):
    """If tempo alone cannot fit, remove whole middle sentences, keeping each scene's opening and ending."""
    available = (target_seconds - SCENE_HOLD * len(sources)) * MAX_TEMPO
    excess = sum(narration_samples(source) for source in sources) / RATE - available
    if excess <= 1 / FPS:
        return list(zip(sources, cues_by_scene))
    sentence_end = re.compile(r"""[.!?…][»”"']*$""")
    candidates = [
        (cues[index + 1]["start"] - cues[index]["start"], scene, index)
        for scene, cues in enumerate(cues_by_scene)
        for index in range(1, len(cues) - 1)
        if sentence_end.search(cues[index - 1]["text"]) and sentence_end.search(cues[index]["text"])
    ]
    removed = set()
    while excess > 1 / FPS and candidates:
        duration, scene, index = min(candidates, key=lambda candidate: abs(candidate[0] - excess))
        candidates.remove((duration, scene, index))
        removed.add((scene, index))
        excess -= duration

    return [
        shorten_scene(source, cues, {index for scene, index in removed if scene == number})
        for number, (source, cues) in enumerate(zip(sources, cues_by_scene))
    ]


def shorten_scene(source, cues, removed):
    if not removed:
        return source, cues
    target = source.with_name("narration.shortened.wav")
    pending = target.with_suffix(".pending.wav")
    timings = []
    offset = 0
    with wave.open(str(source), "rb") as original, wave.open(str(pending), "wb") as shortened:
        shortened.setparams(original.getparams())
        for index, cue in enumerate(cues):
            if index in removed:
                continue
            start = round(cue["start"] * RATE)
            end = round(cue["end"] * RATE)
            next_start = round(cues[index + 1]["start"] * RATE) if index + 1 < len(cues) else end
            original.setpos(start)
            shortened.writeframesraw(original.readframes(next_start - start))
            timings.append({**cue, "start": offset / RATE, "end": (offset + end - start) / RATE})
            offset += next_start - start
    pending.replace(target)
    return target, timings


def fit_narration(directory, source, tempo, cues, *, check=lambda: None):
    if tempo == 1.0:
        return source, cues
    target = directory / "narration.fitted.wav"
    if not target.is_file():
        pending = directory / "narration.fitted.pending.wav"
        run(directory, ["-i", str(source), "-af", f"atempo={tempo:.9f}",
                        "-c:a", "pcm_s16le", str(pending)], check)
        if not narration_samples(pending):
            raise ValueError("Tempo adjustment produced empty narration.")
        pending.replace(target)
    scale = narration_samples(target) / narration_samples(source)
    return target, [{**cue, "start": cue["start"] * scale, "end": cue["end"] * scale} for cue in cues]


def spoken_window(caption, cues):
    def words(text):
        return re.findall(r"[^\W_]+", text.casefold())

    phrase = words(caption)
    for cue in cues:
        spoken = words(cue["text"])
        for start in range(len(spoken) - len(phrase) + 1):
            if spoken[start:start + len(phrase)] == phrase:
                seconds_per_word = (cue["end"] - cue["start"]) / len(spoken)
                return (cue["start"] + start * seconds_per_word,
                        cue["start"] + (start + len(phrase)) * seconds_per_word)
    return cues[0]["start"], cues[0]["end"]


def render_scene(directory, caption, side, font, cues, *, narration=None, check=lambda: None,
                 size=(WIDTH, HEIGHT), captions="handwritten", hold_seconds=SCENE_HOLD):
    """Render a silent H.264 scene. Narration is muxed once after joining the scenes."""
    source = narration or directory / "narration.wav"
    frames = math.ceil(narration_samples(source) / SAMPLES_PER_FRAME + hold_seconds * FPS)
    width, height = size
    portrait = height > width
    scale = width / 960 if portrait else height / HEIGHT
    card_size = (round(width * (.85 if portrait else .39)), round(350 * scale))
    x = round(width * .065) if side == "left" or portrait else width - card_size[0] - round(width * .065)
    y = round(height * .76 - card_size[1] / 2) if portrait else (height - card_size[1]) // 2
    if captions == "off":
        card_size = (2, 2)
        pixels = iter([bytes(16)] * frames)
    else:
        masks = caption_cards(caption, NOTO_SANS if captions == "clean" else font, card_size, scale)
        start, end = spoken_window(caption, cues)
        pixels = reveal_frames(masks, frames, start, end)

    # Use a soft paper wash behind the lettering to handle darker image generations.
    paper = directory / "paper.png"
    with Image.open(directory / "scene.png") as source:
        background = ImageOps.fit(source.convert("RGB"), size, Image.Resampling.LANCZOS)
        if captions != "off":
            sample_x = 0 if side == "left" or portrait else width - 70
            sample_y = height - 70 if portrait else 0
            sample = background.crop((sample_x, sample_y, sample_x + 70, sample_y + 70))
            tint = tuple(max(low, min(high, round(channel))) for channel, low, high
                         in zip(ImageStat.Stat(sample).median, (239, 226, 202), (253, 248, 232)))
            if portrait:
                ramp = Image.new("L", (1, height))
                ramp.putdata([round(235 * min(1, max(0, (row / height - .50) / .14))) for row in range(height)])
            else:
                ramp = Image.new("L", (width, 1))
                ramp.putdata([round(220 * min(1, max(0, (0.59 - col / width) / .22))) for col in range(width)])
                if side == "right":
                    ramp = ImageOps.mirror(ramp)
            background.paste(tint, (0, 0, width, height), ramp.resize(size))
        background.save(paper)

    zoom = f"scale={width * 2}:{height * 2},zoompan=z='1+0.025*on/{frames}':" \
           f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={width}x{height}:fps={FPS},setsar=1"
    target = directory / "clip.pending.mp4"
    command = [
        media_binary(), "-hide_banner", "-v", "error", "-y", "-filter_complex_threads", "2", "-i", str(paper),
        "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{card_size[0]}x{card_size[1]}",
        "-framerate", str(FPS), "-i", "pipe:0",
        "-filter_complex", f"[0:v]{zoom}[paper];[paper][1:v]overlay={x}:{y}:shortest=1[v]",
        "-map", "[v]", "-frames:v", str(frames), "-an", "-c:v", "libx264", "-threads", "2",
        "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", str(target),
    ]
    with TemporaryFile() as errors, subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors) as encoder:
        try:
            for frame in pixels:
                check()
                encoder.stdin.write(frame)
            encoder.stdin.close()
            encoder.wait(timeout=60)
        except BrokenPipeError:
            encoder.wait(timeout=60)
        finally:
            if encoder.poll() is None:
                encoder.kill()
                encoder.wait()
        if encoder.returncode:
            errors.seek(0)
            raise RuntimeError(f"Scene rendering failed. {errors.read(2000).decode(errors='replace').strip()}")
    target.replace(directory / "clip.mp4")
    paper.unlink()
    return frames


def assemble(output, scene_frames, title, *, narrations=None, check=lambda: None,
             size=(WIDTH, HEIGHT), language="uk"):
    # Fixed relative paths avoid escaping arbitrary job directory names in the concat manifest.
    clips = output / "clips.ffconcat"
    narration = output / "narration.wav"
    paths = [output / f"scene-{index:02d}" for index in range(1, len(scene_frames) + 1)]
    sources = narrations if narrations is not None else [path / "narration.wav" for path in paths]
    clips.write_text("".join(f"file {path.name}/clip.mp4\nduration {frames / FPS:.6f}\n"
                             for path, frames in zip(paths, scene_frames)), encoding="utf-8")
    with wave.open(str(narration), "wb") as joined:
        joined.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        for source, frames in zip(sources, scene_frames):
            check()
            with wave.open(str(source), "rb") as audio:
                data = audio.readframes(audio.getnframes())
            joined.writeframesraw(data)
            joined.writeframesraw(bytes(frames * SAMPLES_PER_FRAME * 2 - len(data)))

    target = output / "video.pending.mp4"
    duration = sum(scene_frames) / FPS
    run(output, ["-f", "concat", "-safe", "1", "-i", str(clips), "-i", str(narration),
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                 "-metadata", f"title={title}", "-metadata:s:a:0", f"language={'eng' if language == 'en' else 'ukr'}",
                 "-movflags", "+faststart", str(target)], check)
    verify(target, SimpleNamespace(width=size[0], height=size[1], fps=FPS),
           SimpleNamespace(video_codec="h264", audio_codec="aac"), duration)
    target.replace(output / "video.mp4")
