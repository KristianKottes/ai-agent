"""Prepare a user portrait and render it with local MuseTalk."""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.avatar.encoder import FPS, AvatarError
from backend.avatar.portrait_model import REPO, PortraitModel, runtime_python
from backend.avatar.service import _binary, _run, owned_directory, validate_video

SIZE = 640
MAX_PORTRAIT_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
MAX_PORTRAIT_SECONDS = 10
_LOG = logging.getLogger(__name__)
_RENDER_LOCK = threading.Lock()


class FaceCrop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(default=112, ge=0, le=SIZE - 160)
    y: int = Field(default=60, ge=0, le=SIZE - 160)
    side: int = Field(default=416, ge=160, le=SIZE)

    @model_validator(mode="after")
    def contained(self):
        if max(self.x, self.y) + self.side > SIZE:
            raise ValueError("Keep the face box inside the portrait")
        return self


class PortraitImport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=60)
    image_base64: Optional[str] = Field(default=None, min_length=1, max_length=((MAX_PORTRAIT_BYTES + 2) // 3) * 4)
    video_base64: Optional[str] = Field(default=None, min_length=1, max_length=((MAX_REFERENCE_BYTES + 2) // 3) * 4)

    @model_validator(mode="after")
    def one_source(self):
        if (self.image_base64 is None) == (self.video_base64 is None):
            raise ValueError("Choose one photo or reference video")
        return self


class PortraitRender(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portrait_id: uuid.UUID
    narration_id: uuid.UUID
    seconds: int = Field(default=5, ge=3, le=MAX_PORTRAIT_SECONDS)
    animate_head: bool = False


def portrait_directory(data: Path) -> Path:
    directory = owned_directory(data) / "portraits"
    if directory.is_symlink():
        raise AvatarError("The portrait library directory is unsafe.", 500)
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def prepare_image(encoded: str) -> bytes:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        source = base64.b64decode(encoded, validate=True)
        if not 1 <= len(source) <= MAX_PORTRAIT_BYTES:
            raise AvatarError("Choose an image up to 8 MiB.", 422)
        with Image.open(io.BytesIO(source), formats=("JPEG", "PNG")) as image:
            if image.width * image.height > 24_000_000 or min(image.size) < 256:
                raise AvatarError("Choose an image between 256 pixels per side and 24 megapixels.", 422)
            oriented = ImageOps.exif_transpose(image).convert("RGB")
            square = ImageOps.fit(oriented, (SIZE, SIZE), method=Image.Resampling.LANCZOS)
            output = io.BytesIO()
            square.save(output, "JPEG", quality=93, optimize=True)
            return output.getvalue()
    except (binascii.Error, UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise AvatarError("Choose a valid PNG or JPEG portrait.", 422) from error


def prepare_reference_video(encoded: str, staging: Path, video: Path, preview: Path) -> int:
    ffmpeg, ffprobe = _binary("ffmpeg"), _binary("ffprobe")
    if not ffmpeg or not ffprobe:
        raise AvatarError("FFmpeg and FFprobe are required to import a reference video.", 503)
    try:
        data = base64.b64decode(encoded, validate=True)
        if not 1 <= len(data) <= MAX_REFERENCE_BYTES:
            raise ValueError("Video exceeds the upload limit")
        source = staging / "source.mov"
        source.write_bytes(data)
        probe = [
            ffprobe, "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
            "-select_streams", "V:0", "-of", "json",
        ]
        streams = json.loads(_run([*probe, "-show_entries", "stream=width,height", str(source)], timeout=10))["streams"]
        width, height = streams[0]["width"], streams[0]["height"]
        if min(width, height) < 256 or max(width, height) > 4096:
            raise ValueError("Video dimensions are outside the supported range")
        _run([
            ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-f", "mov", "-i", str(source),
            "-map", "0:V:0", "-an", "-sn", "-dn", "-map_metadata", "-1",
            "-vf", f"setpts=PTS-STARTPTS,fps={FPS},scale={SIZE}:{SIZE}:force_original_aspect_ratio=increase,crop={SIZE}:{SIZE},setsar=1",
            "-t", str(MAX_PORTRAIT_SECONDS), "-pix_fmt", "yuv420p", "-c:v", "libx264",
            "-preset", "fast", "-crf", "17", "-threads", "2", str(video),
        ])
        normalized = json.loads(_run([*probe, "-show_entries", "stream=nb_frames", str(video)], timeout=10))["streams"]
        frames = int(normalized[0]["nb_frames"])
        if not FPS <= frames <= MAX_PORTRAIT_SECONDS * FPS:
            raise ValueError("Invalid prepared video duration")
        _run([ffmpeg, "-nostdin", "-v", "error", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(preview)])
        return frames
    except (AvatarError, binascii.Error, IndexError, KeyError, OSError, TypeError, ValueError) as error:
        raise AvatarError("Choose a readable MP4 or MOV with at least one second of video, up to 16 MiB and 4096 pixels per side.", 422) from error


def render_portrait(
    model: PortraitModel, photo: Path, crop: FaceCrop, wav: Path, target: Path, seconds: int,
    reference: Optional[Path] = None, reference_frames: Optional[int] = None, *, animate_head: bool = False,
) -> dict:
    if not model.status()["ready"]:
        raise AvatarError("Install the portrait runtime and model before rendering.", 409)
    if not _RENDER_LOCK.acquire(blocking=False):
        raise AvatarError("A portrait clip is already rendering.")
    try:
        ffmpeg, ffprobe = _binary("ffmpeg"), _binary("ffprobe")
        pcm = _run([
            ffmpeg, "-nostdin", "-v", "error", "-i", str(wav), "-t", str(seconds),
            "-vn", "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1",
        ])
        if reference_frames is not None:
            pcm = pcm[:2 * (reference_frames * 16_000 // FPS)]
        duration = len(pcm) / 32_000
        if duration < 0.5:
            raise AvatarError("The clip needs at least half a second of narration.", 422)
        pcm_path = target.with_suffix(".pcm")
        pcm_path.write_bytes(pcm)
        command = [
            str(runtime_python()), "-B", "-m", "backend.avatar.portrait_runtime.worker",
            "--model", str(model.root), "--portrait", str(photo),
            "--pcm", str(pcm_path), "--wav", str(wav), "--output", str(target),
            "--ffmpeg", ffmpeg, "--crop", str(crop.x), str(crop.y), str(crop.side),
        ]
        if reference is not None:
            command.extend(["--reference-video", str(reference)])
        elif animate_head:
            command.append("--animate-head")
        try:
            subprocess.run(
                command, cwd=REPO, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=240,
            )
        except subprocess.CalledProcessError as error:
            detail = error.stderr.decode("utf-8", errors="replace")
            _LOG.warning("MuseTalk worker failed: %s", detail[-3000:])
            if "FaceTrackingError:" in detail:
                raise AvatarError("The face could not be tracked throughout the reference. Try a steady, front-facing clip and reframe the first frame.", 422) from error
            raise AvatarError("MuseTalk could not render this portrait. See the desktop backend log.", 502) from error
        except subprocess.TimeoutExpired as error:
            raise AvatarError("Portrait rendering timed out. Try a shorter clip.", 504) from error
        except OSError as error:
            raise AvatarError("The MuseTalk worker could not start. Check the portrait runtime.", 503) from error
        return {"duration_seconds": round(duration, 3), **validate_video(ffprobe, target, duration)}
    finally:
        _RENDER_LOCK.release()
