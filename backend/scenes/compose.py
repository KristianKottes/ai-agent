"""Render a local scene timeline to a captioned H.264/AAC MP4."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.avatar.encoder import FPS, AvatarError
from backend.avatar.service import _binary, validate_video
from backend.voice.alignment import WORDS, align_english

SIZES = {"landscape": (1920, 1080), "vertical": (1080, 1920), "square": (1080, 1080)}
CRF = {"draft": 25, "standard": 19, "high": 16}
SAMPLE_RATE = 48_000
FADE_FRAMES = 6


class CompositionError(RuntimeError):
    pass


class CaptionAlignmentError(CompositionError):
    pass


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["image", "video"]
    asset: str = Field(min_length=1, max_length=255)
    frames: int = Field(strict=True, ge=1, le=600 * FPS)
    transition: Literal["cut", "fade_black"] = "cut"


class Caption(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    start_ms: int = Field(strict=True, ge=0)
    end_ms: int = Field(strict=True, gt=0)
    text: str = Field(min_length=1, max_length=240, pattern=r"^[^\x00-\x1f]+$")


class Timeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["landscape", "vertical", "square"] = "landscape"
    quality: Literal["draft", "standard", "high"] = "standard"
    narration: str = Field(min_length=1, max_length=255)
    scenes: list[Scene] = Field(min_length=1, max_length=50)
    captions: list[Caption] = Field(default_factory=list, max_length=600)
    transcript: Optional[str] = Field(default=None, min_length=1, max_length=30_000)
    background_music: Optional[str] = Field(default=None, min_length=1, max_length=255)
    music_volume: float = Field(default=0.12, ge=0, le=0.35)

    @property
    def frames(self) -> int:
        return sum(scene.frames for scene in self.scenes)

    @model_validator(mode="after")
    def validate_timing(self):
        if self.frames > 600 * FPS:
            raise ValueError("The timeline must be 10 minutes or shorter.")
        if self.captions and self.transcript is not None:
            raise ValueError("Choose timed captions or automatic transcript alignment.")
        if self.scenes[0].transition != "cut":
            raise ValueError("The first scene has no preceding transition.")
        for index, scene in enumerate(self.scenes):
            incoming, outgoing = self.fades(index)
            if incoming + outgoing >= scene.frames:
                raise ValueError("A scene is too short for its fade transitions.")
        end = 0
        for cue in self.captions:
            if not end <= cue.start_ms < cue.end_ms <= self.frames * 1000 // FPS:
                raise ValueError("Captions must be ordered, non-overlapping, and contained in the timeline.")
            end = cue.end_ms
        return self

    def fades(self, index: int) -> tuple[int, int]:
        incoming = self.scenes[index].transition == "fade_black"
        outgoing = index + 1 < len(self.scenes) and self.scenes[index + 1].transition == "fade_black"
        return FADE_FRAMES * incoming, FADE_FRAMES * outgoing


def local_asset(root: Path, name: str, suffix: str) -> Path:
    relative = Path(name)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() != suffix:
        raise CompositionError(f"Expected a local {suffix} asset inside the timeline directory: {name}")
    return path


def run_media(command: list[str], timeout: int) -> bytes:
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, check=True,
                              timeout=timeout).stdout
    except subprocess.CalledProcessError as error:
        raise CompositionError(error.stderr.decode(errors="replace").strip()[-1600:]) from error


def stream_info(ffprobe: str, path: Path, demuxer: str, selector: str) -> dict:
    streams = json.loads(run_media([
        ffprobe, "-v", "error", "-protocol_whitelist", "file", "-f", demuxer,
        "-select_streams", selector, "-show_streams", "-of", "json", str(path),
    ], 30))["streams"]
    if not streams:
        raise CompositionError(f"{path.name} has no matching media stream.")
    return streams[0]


def timecode(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02}:{minute:02}:{second:02},{ms:03}"


def write_captions(path: Path, captions: list[Caption]):
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for index, caption in enumerate(captions, 1):
            file.write(f"{index}\n{timecode(caption.start_ms)} --> {timecode(caption.end_ms)}\n{caption.text}\n\n")


def transcript_captions(ffmpeg: str, narration: Path, transcript: str, clip_ms: int) -> list[Caption]:
    pcm = run_media([
        ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-f", "wav", "-i", str(narration),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
    ], 120)
    normalized = transcript.replace("’", "'")
    aligned = align_english(pcm, normalized)
    if aligned is None:
        raise CaptionAlignmentError("The English transcript could not be aligned. Check the text or supply timed captions.")
    matches = list(WORDS.finditer(normalized))
    words = [word for word in aligned if not word.name.startswith("<") and word.end * 10 <= clip_ms]
    captions = []
    for index in range(0, len(words), 6):
        end = min(index + 6, len(words))
        last = matches[end].start() if end < len(matches) else len(normalized)
        text = " ".join(normalized[matches[index].start():last].split())
        captions.append(Caption(start_ms=words[index].start * 10, end_ms=words[end - 1].end * 10, text=text))
    return captions


def render_timeline(timeline: Timeline, root: Path, output: Path) -> dict:
    from PIL import Image

    ffmpeg, ffprobe = _binary("ffmpeg"), _binary("ffprobe")
    if not ffmpeg or not ffprobe:
        raise CompositionError("Export requires FFmpeg with libx264 and FFprobe.")
    if output.suffix.lower() != ".mp4" or output.exists() or output.is_symlink():
        raise CompositionError("Choose a new MP4 output file.")

    width, height = SIZES[timeline.format]
    duration = timeline.frames / FPS
    narration = local_asset(root, timeline.narration, ".wav")
    audio = stream_info(ffprobe, narration, "wav", "a:0")
    audio_seconds = float(audio["duration"])
    if not math.isfinite(audio_seconds) or audio_seconds < duration - 1 / SAMPLE_RATE:
        raise CompositionError("The saved narration is shorter than the scene timeline.")

    command = [ffmpeg, "-nostdin", "-v", "error", "-filter_complex_threads", "2"]
    filters = []
    for index, scene in enumerate(timeline.scenes):
        if scene.kind == "image":
            asset = local_asset(root, scene.asset, ".png")
            with Image.open(asset) as image:
                if image.format != "PNG" or max(image.size) > 8192:
                    raise CompositionError(f"{asset.name} must be a PNG no larger than 8192 pixels per side.")
                image.verify()
            input_flags = ["-f", "image2", "-pattern_type", "none", "-loop", "1", "-framerate", str(FPS)]
        else:
            asset = local_asset(root, scene.asset, ".mp4")
            video = stream_info(ffprobe, asset, "mov", "v:0")
            video_seconds = float(video["duration"])
            if (not math.isfinite(video_seconds) or video_seconds + 1e-6 < scene.frames / FPS
                    or max(video["width"], video["height"]) > 8192):
                raise CompositionError(f"{asset.name} is too short or too large for its scene.")
            input_flags = ["-f", "mov"]
        command += ["-protocol_whitelist", "file", *input_flags, "-i", str(asset)]
        visual = (
            f"[{index}:v:0]setpts=PTS-STARTPTS,fps={FPS},trim=end_frame={scene.frames},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
        )
        fade_in, fade_out = timeline.fades(index)
        if fade_in:
            visual += f",fade=t=in:s=0:n={fade_in}"
        if fade_out:
            visual += f",fade=t=out:s={scene.frames - fade_out - 1}:n={fade_out}"
        filters.append(f"{visual}[scene{index}]")
    count = len(timeline.scenes)
    inputs = "".join(f"[scene{index}]" for index in range(count))
    filters.append(f"{inputs}concat=n={count}:v=1:a=0[video]")
    samples = timeline.frames * SAMPLE_RATE // FPS
    command += ["-protocol_whitelist", "file", "-f", "wav", "-i", str(narration)]
    voice = f"[{count}:a:0]aresample={SAMPLE_RATE},atrim=end_sample={samples},asetpts=N/SR/TB"
    caption_input = count + 1
    if timeline.background_music and timeline.music_volume:
        music = local_asset(root, timeline.background_music, ".wav")
        audio_stream = stream_info(ffprobe, music, "wav", "a:0")
        music_seconds = float(audio_stream["duration"])
        if not math.isfinite(music_seconds) or music_seconds <= 0:
            raise CompositionError("The background WAV contains no playable audio.")
        gain = timeline.music_volume
        command += ["-protocol_whitelist", "file", "-stream_loop", "-1", "-f", "wav", "-i", str(music)]
        filters += [
            f"{voice},aformat=channel_layouts=stereo,volume={1 - gain:.6f}[voice]",
            f"[{count + 1}:a:0]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,volume={gain:.6f}[music]",
            "[voice][music]amix=inputs=2:duration=first:normalize=0[audio]",
        ]
        caption_input += 1
    else:
        filters.append(f"{voice}[audio]")

    cues = timeline.captions
    if timeline.transcript is not None:
        if audio_seconds > 600:
            raise CompositionError("Automatic captions require a narration of 10 minutes or less.")
        cues = transcript_captions(ffmpeg, narration, timeline.transcript, timeline.frames * 1000 // FPS)

    with TemporaryDirectory(prefix=".compose-", dir=output.parent) as temp:
        stage = Path(temp)
        if cues:
            captions = stage / "captions.srt"
            write_captions(captions, cues)
            command += ["-protocol_whitelist", "file", "-f", "srt", "-i", str(captions)]
        target = stage / "video.mp4"
        command += ["-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]",
                    "-c:v", "libx264", "-threads:v", "2", "-preset", "fast", "-crf", str(CRF[timeline.quality]),
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-map_metadata", "-1"]
        if cues:
            command += ["-map", f"{caption_input}:s:0", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng"]
        run_media([*command, str(target)], 1800)
        metrics = validate_video(ffprobe, target, duration)
        video = stream_info(ffprobe, target, "mov", "v:0")
        if (video["width"], video["height"], video["r_frame_rate"], metrics["frame_count"]) != (
            width, height, f"{FPS}/1", timeline.frames,
        ):
            raise CompositionError("The encoded video dimensions, frame rate, or frame count differ from the timeline.")
        if cues:
            subtitles = stream_info(ffprobe, target, "mov", "s:0")
            if subtitles["codec_name"] != "mov_text":
                raise CompositionError("The encoded video is missing the expected caption format.")
        os.link(target, output)
    return {"path": str(output), "width": width, "height": height, "duration_seconds": duration,
            "caption_count": len(cues), **metrics}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeline", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    timeline_path = args.timeline.resolve()
    output = args.output.absolute()
    try:
        timeline = Timeline.model_validate_json(timeline_path.read_bytes())
        print(json.dumps(render_timeline(timeline, timeline_path.parent, output), indent=2))
    except (AvatarError, CompositionError, KeyError, OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
