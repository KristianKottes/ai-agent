"""Read saved narration and render an illustrated presenter without network access."""

from __future__ import annotations

import base64
import io
import json
import math
import os
import platform
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.avatar.encoder import FPS, AvatarError, write_video
from backend.avatar.portrait import BACKDROPS, LOOKS, OUTFITS, SIZE, mouth_pose, portrait
from backend.voice.alignment import align_english

MAX_SECONDS = 30
_RENDER_LOCK = threading.Lock()


class Appearance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    look: Literal["maya", "eli"] = "maya"
    backdrop: Literal["ocean", "plum", "ember"] = "ocean"
    outfit: Literal["ink", "sage", "rose"] = "ink"
    motion: bool = True


class Preset(Appearance):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=60)


class RenderRequest(Appearance):
    narration_id: uuid.UUID
    seconds: int = Field(default=10, ge=3, le=MAX_SECONDS)


def _binary(name: str) -> Optional[str]:
    media_bin = os.environ.get("AI_BLOGGER_MEDIA_BIN_DIR")
    if media_bin is not None:
        return shutil.which(name, path=media_bin)
    return shutil.which(name) or shutil.which(name, path="/opt/homebrew/bin:/usr/local/bin:/usr/bin")


def availability():
    if platform.system() != "Darwin" or platform.machine().lower() not in ("arm64", "aarch64"):
        return False, "The illustrated presenter engine has been verified on Apple Silicon Macs."
    try:
        import PIL.Image
        import pocketsphinx
    except (ImportError, OSError):
        return False, "Install the avatar packages from the backend requirements, then restart the app."
    if not _binary("ffmpeg") or not _binary("ffprobe"):
        return False, "Install FFmpeg and FFprobe to render presenter videos."
    return True, "Available"


def catalog():
    ready, detail = availability()
    return {
        "ready": ready,
        "detail": detail,
        "looks": [{"id": key, "name": value[0]} for key, value in LOOKS.items()],
        "backdrops": list(BACKDROPS),
        "outfits": list(OUTFITS),
        "max_seconds": MAX_SECONDS,
    }


def preview(appearance: Appearance) -> dict:
    try:
        output = io.BytesIO()
        portrait(appearance.look, appearance.backdrop, appearance.outfit, motion=False).save(output, format="PNG")
    except (ImportError, OSError) as error:
        raise AvatarError("The avatar preview requires Pillow.", 503) from error
    blob = base64.b64encode(output.getvalue()).decode("ascii")
    return {"image_url": "data:image/png;base64," + blob}


def _run(command: list[str], timeout=45) -> bytes:
    try:
        result = subprocess.run(command, check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        return result.stdout
    except (subprocess.SubprocessError, OSError) as error:
        raise AvatarError("The local media tool could not process the avatar clip.", 502) from error


def _phonemes(pcm: bytes, text: str, full_narration: bool):
    from pocketsphinx import Decoder, get_model_path

    def decode(decoder):
        decoder.start_utt()
        decoder.process_raw(pcm, full_utt=True)
        decoder.end_utt()

    # Align the transcript only when it describes the entire supplied audio.
    # Audio-only decoding also handles excerpts, numbers, and unknown words.
    if full_narration:
        alignment = align_english(pcm, text)
        if alignment:
            return [(start, end, mouth_pose(name)) for word in alignment for start, end, name in word.phones], "transcript"

    model = Path(get_model_path()) / "en-us" / "en-us-phone.lm.bin"
    decoder = Decoder(lm=None, allphone=str(model), loglevel="ERROR")
    decode(decoder)
    segments = decoder.seg()
    return [
        (item.start_frame, item.end_frame + 1, mouth_pose(item.word))
        for item in (segments or [])
    ], "audio"


def frame_poses(cues, frames: int):
    index = 0
    for frame in range(frames):
        time_cs = (frame + 0.5) * 100 / FPS
        while index < len(cues) and cues[index][1] <= time_cs:
            index += 1
        if index < len(cues) and cues[index][0] <= time_cs:
            yield cues[index][2]
        else:
            yield "rest"


def validate_video(ffprobe: str, path: Path, expected: float):
    output = _run([
        ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,start_time,duration,nb_frames",
        "-of", "json", str(path),
    ])
    try:
        streams = {item["codec_type"]: item for item in json.loads(output)["streams"]}
        video = streams["video"]
        audio = streams["audio"]
        starts = [float(stream["start_time"]) for stream in (video, audio)]
        durations = [float(stream["duration"]) for stream in (video, audio)]
        if (video["codec_name"], audio["codec_name"]) != ("h264", "aac"):
            raise ValueError("Unexpected codecs")
        if any(not math.isfinite(value) or abs(value) > 1 / FPS for value in starts):
            raise ValueError("Stream start is outside one frame")
        if any(not math.isfinite(value) or abs(value - expected) > 2 / FPS for value in durations):
            raise ValueError("Stream duration differs from the requested narration")
        end_offset = abs(starts[0] + durations[0] - starts[1] - durations[1])
        if end_offset > 2 / FPS:
            raise ValueError("Audio and video end more than two frames apart")
        frame_count = int(video["nb_frames"])
        if abs(frame_count - math.ceil(expected * FPS)) > 1:
            raise ValueError("Video has an unexpected number of frames")
        return {
            "frame_count": frame_count,
            "av_start_offset_ms": round(abs(starts[0] - starts[1]) * 1000, 2),
            "av_end_offset_ms": round(end_offset * 1000, 2),
        }
    except (KeyError, ValueError, TypeError) as error:
        raise AvatarError("The rendered MP4 failed its audio and video timing check.", 502) from error


def render(wav: Path, target: Path, request: RenderRequest, text: str, source_duration: float):
    ready, detail = availability()
    if not ready:
        raise AvatarError(detail, 503)
    if not _RENDER_LOCK.acquire(blocking=False):
        raise AvatarError("Another avatar clip is rendering. Try again when it finishes.")
    try:
        ffmpeg, ffprobe = _binary("ffmpeg"), _binary("ffprobe")
        pcm = _run([
            ffmpeg, "-nostdin", "-v", "error", "-i", str(wav), "-t", str(request.seconds),
            "-vn", "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1",
        ])
        duration = len(pcm) / 32_000
        if duration < 0.5:
            raise AvatarError("The clip needs at least half a second of speech.", 422)
        cues, alignment = _phonemes(pcm, text, source_duration <= request.seconds + 0.02)
        frame_count = math.ceil(duration * FPS)
        poses = list(frame_poses(cues, frame_count))
        if sum(pose != "rest" for pose in poses) < 3 or len(set(poses) - {"rest"}) < 2:
            raise AvatarError("The audio did not contain sufficiently clear English speech to animate.", 422)
        frames = (
            portrait(request.look, request.backdrop, request.outfit, pose, index / FPS, request.motion).tobytes()
            for index, pose in enumerate(poses)
        )
        write_video(ffmpeg, wav, target, frames, SIZE, duration)
        timing = validate_video(ffprobe, target, duration)
        return {
            "duration_seconds": round(duration, 3),
            "voice_cues": sum(pose != "rest" for _, _, pose in cues),
            "mouth_poses": len(set(poses) - {"rest"}),
            "alignment": alignment,
            **timing,
        }
    finally:
        _RENDER_LOCK.release()


def owned_directory(data: Path) -> Path:
    folder = data / "avatar"
    if folder.is_symlink():
        raise AvatarError("The avatar data folder is unsafe.", 500)
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder
