"""Stream RGB frames to an H.264/AAC MP4 with the original narration."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

FPS = 24


class AvatarError(RuntimeError):
    def __init__(self, detail: str, status_code=409):
        super().__init__(detail)
        self.status_code = status_code


def write_video(ffmpeg: str, wav: Path, target: Path, frames, size: int, duration: float):
    command = [
        ffmpeg, "-nostdin", "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", f"{size}x{size}", "-framerate", str(FPS), "-i", "pipe:0",
        "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0", "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-preset", "fast", "-threads", "2", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target),
    ]
    try:
        with tempfile.TemporaryFile() as errors, subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors
        ) as encoder:
            try:
                for frame in frames:
                    encoder.stdin.write(frame)
                encoder.stdin.close()
                if encoder.wait(timeout=60) != 0:
                    raise AvatarError("FFmpeg could not encode the avatar video.", 502)
            except BaseException:
                encoder.kill()
                raise
    except (OSError, subprocess.SubprocessError) as error:
        raise AvatarError("FFmpeg failed while encoding the avatar video.", 502) from error
