"""Encode a composition against the source audio and write timed subtitles."""

from __future__ import annotations

import math
import subprocess
import tempfile
from io import BytesIO
from zipfile import ZipFile

from backend.studio.audio import StudioError, media_binary


class Cancelled(Exception):
    pass


def run_encoder(command, target, renderer, total, fps, check, progress):
    with tempfile.TemporaryFile() as errors, subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors,
    ) as process:
        try:
            for index in range(total):
                check()
                frame = renderer.frame(index / fps)
                process.stdin.write(frame.tobytes() if renderer.transparent else frame.convert("RGB").tobytes())
                if index % fps == 0:
                    progress(index / total)
            process.stdin.close()
            while process.poll() is None:
                check()
                try:
                    process.wait(timeout=.25)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                raise StudioError("FFmpeg could not encode the video. Check the selected codec in Models & Tools.", 502)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if not target.is_file() or target.stat().st_size == 0:
        raise StudioError("The encoder did not write a video.", 502)


def export(target, renderer, audio, duration, settings, check, progress):
    fps = renderer.spec.fps
    total = max(1, math.ceil(duration * fps))
    if settings.format == "png":
        with ZipFile(target, "w", allowZip64=True) as archive:
            for index in range(total):
                check()
                frame = BytesIO()
                renderer.frame(index / fps).save(frame, format="PNG")
                archive.writestr(f"frame-{index:06d}.png", frame.getvalue())
                if index % fps == 0:
                    progress(index / total)
        return
    command = [
        media_binary(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt",
        "rgba" if renderer.transparent else "rgb24", "-s", f"{renderer.width}x{renderer.height}",
        "-framerate", str(fps), "-i", "pipe:0", "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
        "-threads", "3",
    ]
    if settings.format == "mp4":
        command.extend(["-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"])
    else:
        command.extend(["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1",
                        "-b:v", "0", "-crf", "29", "-c:a", "libopus",
                        "-pix_fmt", "yuva420p" if settings.transparent else "yuv420p", "-auto-alt-ref", "0"])
    try:
        run_encoder([*command, str(target)], target, renderer, total, fps, check, progress)
    except (OSError, subprocess.SubprocessError) as error:
        raise StudioError("The local video encoder stopped unexpectedly.", 502) from error


def timestamp(seconds, digits, separator, hours=2):
    tick = 10 ** digits
    minutes, fraction = divmod(round(seconds * tick), tick * 60)
    hour, minute = divmod(minutes, 60)
    second, fraction = divmod(fraction, tick)
    return f"{hour:0{hours}d}:{minute:02d}:{second:02d}{separator}{fraction:0{digits}d}"


def subtitles(cues, format, composition=None):
    if format == "srt":
        return "\n".join(
            f"{i}\n{timestamp(cue['start'], 3, ',')} --> {timestamp(cue['end'], 3, ',')}\n{cue['text']}\n"
            for i, cue in enumerate(cues, 1)
        )
    width, height = (composition.width, composition.height) if composition else (1280, 720)
    margin, size = round(width*.07), round(min(width, height)*.041)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans,{size},&H00FFFFFF,&H00FFFFFF,&H00140C09,&H00140C09,0,0,0,0,100,100,0,0,1,2,0,2,{margin},{margin},{round(height*.07)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cue in cues:
        # Prevent pasted text from being interpreted as an ASS override block.
        text = cue["text"].replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", r"\N")
        events.append(f"Dialogue: 0,{timestamp(cue['start'], 2, '.', 1)},"
                      f"{timestamp(cue['end'], 2, '.', 1)},Default,,0,0,0,,{text}\n")
    return header + "".join(events)
