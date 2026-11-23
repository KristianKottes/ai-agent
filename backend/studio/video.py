"""Versioned project timelines and deterministic FFmpeg slideshow rendering."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from fractions import Fraction
from functools import lru_cache
from tempfile import TemporaryFile
from typing import Literal, Optional
from uuid import UUID

from pydantic import Field, model_validator

from backend.avatar.service import _binary
from backend.studio.audio import StudioError, media_binary
from backend.studio.export import subtitles, timestamp
from backend.studio.motion import Spec, font, wrap

TRANSITIONS = {
    "crossfade": "fade", "black": "fadeblack", "white": "fadewhite",
    "slide_left": "slideleft", "slide_right": "slideright",
    "zoom": "zoomin", "wipe": "wipeleft", "blur": "hblur",
}
VIDEO_CODECS = {"h264": "libx264", "hevc": "libx265", "vp9": "libvpx-vp9", "av1": "libaom-av1"}
AUDIO_CODECS = {"aac": "aac", "opus": "libopus", "pcm": "pcm_s16le"}


class Cue(Spec):
    start: float = Field(ge=0, le=1200)
    end: float = Field(gt=0, le=1200)
    text: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start or not self.text.strip():
            raise ValueError("Enter caption text and an end time after its start.")
        return self


class AudioClip(Spec):
    asset_id: UUID
    start: float = Field(default=0, ge=0, le=1200)
    trim: float = Field(default=0, ge=0, le=1200)
    length: Optional[float] = Field(default=None, gt=0, le=1200)
    gain: float = Field(default=1, ge=0, le=2)
    fade_in: float = Field(default=0, ge=0, le=30)
    fade_out: float = Field(default=0, ge=0, le=30)
    loop: bool = False
    mute: bool = False
    solo: bool = False
    track: Literal["music", "effects"] = "music"


class Scene(Spec):
    id: UUID
    image_id: Optional[UUID] = None
    duration: float = Field(default=6, ge=.25, le=1200)
    animation: Literal["still", "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up",
                       "pan_down", "ken_burns", "fade"] = "ken_burns"
    transition: Literal["cut", "crossfade", "black", "white", "slide_left", "slide_right",
                        "zoom", "wipe", "blur"] = "cut"
    overlap: float = Field(default=.5, ge=.05, le=5)
    narration_id: Optional[UUID] = None
    narration_pending_id: Optional[UUID] = None
    narration_trim: float = Field(default=0, ge=0, le=1200)
    trim_narration: bool = False
    narration_text: str = Field(default="", max_length=16000)
    image_prompt: str = Field(default="", max_length=8000)
    title: str = Field(default="", max_length=160)
    captions: list[Cue] = Field(default_factory=list, max_length=300)


class Timeline(Spec):
    version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    width: int = Field(default=1920, ge=160, le=3840, multiple_of=2)
    height: int = Field(default=1080, ge=160, le=3840, multiple_of=2)
    fps: int = Field(default=30, ge=12, le=60)
    background: str = Field(default="#141923", pattern=r"^#[0-9a-fA-F]{6}$")
    scenes: list[Scene] = Field(default_factory=list, max_length=100)
    audio: list[AudioClip] = Field(default_factory=list, max_length=32)
    narration_gain: float = Field(default=1, ge=0, le=2)
    duck: bool = False
    duck_amount: float = Field(default=6, ge=1, le=20)
    duck_attack: float = Field(default=20, ge=1, le=500)
    duck_release: float = Field(default=300, ge=5, le=3000)
    duck_threshold: float = Field(default=.04, ge=.001, le=1)
    caption_style: Literal["minimal", "youtube", "bold", "cinematic", "inspirational"] = "youtube"

    @model_validator(mode="after")
    def unique_scenes(self):
        if len({scene.id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("A scene ID may appear only once.")
        return self

    def timing(self):
        starts, lengths, overlaps = [], [], []
        position = 0
        for index, scene in enumerate(self.scenes):
            duration = math.floor(scene.duration * self.fps + .5)
            overlap = math.floor(scene.overlap * self.fps + .5) if index and scene.transition != "cut" else 0
            if index and scene.transition != "cut" and not overlap:
                raise StudioError(f"Lengthen the transition into scene {index + 1} to at least one frame.")
            if overlap >= duration or (index and overlap + overlaps[-1] >= lengths[-1]):
                raise StudioError(f"Shorten the transition into scene {index + 1}.")
            position -= overlap
            starts.append(position)
            lengths.append(duration)
            overlaps.append(overlap)
            position += duration
        if position > self.fps * 1200:
            raise StudioError("Projects can be up to 20 minutes long.")
        return starts, lengths, overlaps, position


class VideoRequest(Spec):
    timeline: Timeline
    scene_id: Optional[UUID] = None
    preview: bool = False
    filename: str = Field(default="Project video", min_length=1, max_length=100)
    container: Literal["mp4", "webm", "mov"] = "mp4"
    video_codec: Literal["h264", "hevc", "vp9", "av1"] = "h264"
    audio_codec: Literal["aac", "opus", "pcm"] = "aac"
    crf: int = Field(default=20, ge=0, le=51)
    audio_bitrate: int = Field(default=192, ge=64, le=320)
    captions: Literal["burn", "separate", "none"] = "burn"

    @model_validator(mode="after")
    def compatible(self):
        combinations = {
            "mp4": ({"h264", "hevc", "av1"}, {"aac"}),
            "mov": ({"h264", "hevc"}, {"aac", "pcm"}),
            "webm": ({"vp9", "av1"}, {"opus"}),
        }
        video, audio = combinations[self.container]
        if self.video_codec not in video or self.audio_codec not in audio:
            raise ValueError("Choose codecs supported by this container.")
        return self

    def selected(self):
        if self.scene_id is None:
            return self.timeline
        for scene, start, frames in zip(self.timeline.scenes, *self.timeline.timing()[:2]):
            if scene.id == self.scene_id:
                start /= self.timeline.fps
                duration = frames / self.timeline.fps
                clips = []
                for clip in self.timeline.audio:
                    elapsed = max(0, start - clip.start)
                    remaining = (clip.length - elapsed) if clip.length else None
                    if clip.start >= start + duration or (remaining is not None and remaining <= 0):
                        continue
                    clips.append(clip.model_copy(update={"start": max(0, clip.start - start),
                                                        "trim": clip.trim + elapsed, "length": remaining}))
                return self.timeline.model_copy(update={"scenes": [scene], "audio": clips})
        raise StudioError("The selected scene is no longer in the timeline.")


def caption_cues(timeline):
    starts, lengths, _, _ = timeline.timing()
    cues = []
    for scene, start, frames in zip(timeline.scenes, starts, lengths):
        offset = start / timeline.fps
        duration = frames / timeline.fps
        for cue in scene.captions:
            if cue.start < duration:
                cues.append({"start": offset + cue.start, "end": offset + min(cue.end, duration), "text": cue.text})
    return sorted(cues, key=lambda cue: cue["start"])


def subtitle_text(cues, format):
    if format == "vtt":
        return "WEBVTT\n\n" + "\n\n".join(
            f"{timestamp(cue['start'], 3, '.')} --> {timestamp(cue['end'], 3, '.')}\n"
            + cue["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            for cue in cues
        )
    return subtitles(cues, format)


def validate(studio, project, request):
    studio.tools.require("ffmpeg")
    timeline = request.selected()
    _, lengths, _, total = timeline.timing()
    if not total:
        raise StudioError("Add an image scene before rendering.")
    for index, (scene, frames) in enumerate(zip(timeline.scenes, lengths), 1):
        if not scene.image_id:
            raise StudioError(f"Choose an image for scene {index}.")
        studio.asset_path(project, str(scene.image_id), "image")
        if scene.narration_id:
            studio.asset_path(project, str(scene.narration_id), "audio")
            asset = studio.asset(project, str(scene.narration_id), "audio")
            duration = asset["duration_seconds"] - scene.narration_trim
            if duration <= 0:
                raise StudioError(f"Scene {index} narration begins beyond the end of its recording.")
            if not scene.trim_narration and duration > frames / timeline.fps + .001:
                raise StudioError(f"Scene {index} narration is longer than the scene. Extend the scene or explicitly trim narration.")
    for clip in timeline.audio:
        studio.asset_path(project, str(clip.asset_id), "audio")
        if request.scene_id is None and not clip.loop and clip.trim >= studio.asset(project, str(clip.asset_id), "audio")["duration_seconds"]:
            raise StudioError("An audio clip starts beyond the end of its recording.")
    for codec in ("libx264", VIDEO_CODECS[request.video_codec], AUDIO_CODECS[request.audio_codec]):
        if codec not in encoders():
            raise StudioError(f"The installed FFmpeg does not provide {codec}. See Models & Tools.")
    return timeline


def encoders():
    return _encoders(media_binary())



@lru_cache(maxsize=4)
def _encoders(binary):
    try:
        listing = subprocess.check_output([binary, "-hide_banner", "-encoders"], timeout=8, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise StudioError("FFmpeg encoder discovery failed.", 503) from error
    return {line.split()[1] for line in listing.splitlines() if line.startswith((" V", " A")) and len(line.split()) > 2}


def run(work, args, check, *, seconds=0, update=None):
    progress = work / "ffmpeg-progress"
    with TemporaryFile() as error_log:
        command = [media_binary(), "-hide_banner", "-v", "error", "-nostdin", "-y",
                   "-filter_threads", "2", "-filter_complex_threads", "2"]
        if update and seconds:
            progress.unlink(missing_ok=True)
            command += ["-stats_period", "0.25", "-progress", str(progress)]
        try:
            with subprocess.Popen([*command, *args], cwd=work, stdout=subprocess.DEVNULL, stderr=error_log) as process:
                try:
                    while process.poll() is None:
                        check()
                        try:
                            process.wait(timeout=.25)
                        except subprocess.TimeoutExpired:
                            if update and seconds and progress.is_file():
                                for line in reversed(progress.read_text(errors="replace").splitlines()):
                                    if line.startswith("out_time_us=") and line[12:].isdigit():
                                        update(min(1, int(line[12:]) / (1_000_000 * seconds)))
                                        break
                    if process.returncode:
                        error_log.seek(0)
                        detail = error_log.read(3000).decode(errors="replace").strip()
                        raise StudioError(f"FFmpeg could not complete the render. {detail}", 502)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
        except OSError as error:
            raise StudioError("FFmpeg could not start.", 502) from error


def animation_filter(animation, width, height, fps, frames, color):
    size = f"{width}x{height}"
    if animation in ("still", "fade"):
        fit = (f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x{color[1:]},setsar=1")
        if animation == "fade":
            length = min(.5, frames / fps / 3)
            fit += f",fade=t=in:d={length}:color=0x{color[1:]},fade=t=out:st={frames/fps-length}:d={length}:color=0x{color[1:]}"
        return fit
    p = f"on/{max(1, frames - 1)}"
    z, x, y = "1.16", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
    if animation in ("zoom_in", "ken_burns"):
        z = f"1.02+0.16*{p}"
    if animation == "zoom_out":
        z = f"1.18-0.16*{p}"
    if animation == "ken_burns":
        x = f"(iw-iw/zoom)*(0.25+0.5*{p})"
    if animation in ("pan_left", "pan_right"):
        x = f"(iw-iw/zoom)*({'1-' if animation == 'pan_left' else ''}{p})"
    if animation in ("pan_up", "pan_down"):
        y = f"(ih-ih/zoom)*({'1-' if animation == 'pan_up' else ''}{p})"
    return (f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase:force_divisible_by=2,"
            f"crop={width*2}:{height*2},setsar=1,zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={size}:fps={fps}")


def scene_file(studio, project, scene, spec, frames, work, check):
    from PIL import Image, ImageOps

    source = studio.asset_path(project, str(scene.image_id), "image")
    stat = source.stat()
    key = hashlib.sha256(json.dumps([2, source.name, stat.st_mtime_ns, stat.st_size, spec.width, spec.height,
                                     spec.fps, spec.background, frames, scene.animation]).encode()).hexdigest()
    cache = studio.directory(project, "video-cache")
    target = cache / (key + ".mp4")
    if target.is_file() and target.resolve() == target:
        os.utime(target, None)
        return target
    stage = work / "scene.mp4"
    flat = work / "scene.png"
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGBA")
        layer = Image.new("RGBA", image.size, spec.background)
        layer.alpha_composite(image)
        layer.convert("RGB").save(flat)
    filter_ = animation_filter(scene.animation, spec.width, spec.height, spec.fps, frames, spec.background)
    args = ["-framerate", str(spec.fps)]
    if scene.animation in ("still", "fade"):
        args += ["-loop", "1"]
    run(work, [*args, "-i", str(flat), "-vf", filter_, "-an", "-frames:v", str(frames),
               "-c:v", "libx264", "-threads", "3", "-preset", "veryfast", "-crf", "16",
               "-pix_fmt", "yuv420p", "-video_track_timescale", str(spec.fps * 1000), str(stage)], check)
    check()
    os.replace(stage, target)
    return target


def caption_input(work, spec, frames, cues, check):
    from PIL import Image, ImageDraw

    starts = {0, frames}
    entries = []
    for cue in cues:
        left = min(frames, math.ceil(cue["start"] * spec.fps - 1e-8))
        right = min(frames, math.ceil(cue["end"] * spec.fps - 1e-8))
        if right > left:
            starts.update((left, right))
            entries.append((left, right, cue))
    events = sorted(starts)
    files = {}
    manifest = ["ffconcat version 1.0"]
    for left, right in zip(events, events[1:]):
        check()
        text = tuple(item["text"] for start, end, item in entries if start <= left < end)
        if text not in files:
            name = f"caption-{len(files)}.png"
            files[text] = name
            image = Image.new("RGBA", (spec.width, spec.height))
            if text:
                draw = ImageDraw.Draw(image)
                scale, anchor, color = {
                    "minimal": (.033, .94, "#ffffff"), "youtube": (.042, .91, "#ffffff"),
                    "bold": (.058, .84, "#ffe78a"), "cinematic": (.038, .89, "#f3eadd"),
                    "inspirational": (.047, .79, "#ffe78a"),
                }[spec.caption_style]
                face = font(max(12, round(min(spec.width, spec.height) * scale)))
                label = wrap(draw, "\n".join(text), face, spec.width * .85)
                point = (spec.width // 2, round(spec.height * anchor))
                border = max(1, face.size // 20)
                if spec.caption_style in ("youtube", "bold"):
                    box = draw.multiline_textbbox(point, label, font=face, anchor="md", align="center")
                    pad = face.size // 3
                    draw.rounded_rectangle((box[0]-pad, box[1]-pad, box[2]+pad, box[3]+pad),
                                           radius=pad, fill=(10, 15, 25, 210))
                draw.multiline_text(point, label, font=face, anchor="md", align="center",
                                    fill=color, stroke_width=border, stroke_fill="#111318")
            image.save(work / name)
        manifest += [f"file {files[text]}", f"option framerate {spec.fps}", f"duration {(right-left)/spec.fps:.9f}"]
    manifest += [f"file {files[text]}", f"option framerate {spec.fps}"]
    path = work / "captions.ffconcat"
    path.write_text("\n".join(manifest) + "\n", encoding="utf-8")
    return path


def audio_graph(studio, project, spec, start_index, starts, lengths, total):
    inputs, graph = [], []
    tracks = {"voice": [], "music": [], "effects": []}
    duration = total / spec.fps

    def add(asset, track, start, trim, length, gain, loop=False, fade_in=0, fade_out=0):
        meta = studio.asset(project, str(asset), "audio")
        source_length = meta["duration_seconds"]
        if loop:
            trim %= source_length
        length = min(length, duration - start, length if loop else source_length - trim)
        if length <= 0 or gain == 0:
            return
        index = start_index + len(tracks["voice"]) + len(tracks["music"]) + len(tracks["effects"])
        if loop:
            inputs.extend(["-stream_loop", "-1"])
        inputs.extend(["-i", str(studio.asset_path(project, str(asset), "audio"))])
        label = f"a{index}"
        chain = (f"[{index}:a]atrim=start={trim}:duration={length},asetpts=PTS-STARTPTS,"
                 f"aresample=48000,aformat=channel_layouts=stereo,volume={gain}")
        if fade_in:
            chain += f",afade=t=in:d={min(fade_in, length)}"
        if fade_out:
            size = min(fade_out, length)
            chain += f",afade=t=out:st={length-size}:d={size}"
        graph.append(chain + f",adelay={round(start*48000)}S:all=1[{label}]")
        tracks[track].append(f"[{label}]")

    solo = any(clip.solo and not clip.mute for clip in spec.audio)
    if not solo:
        for scene, start, frames in zip(spec.scenes, starts, lengths):
            if scene.narration_id:
                add(scene.narration_id, "voice", start/spec.fps, scene.narration_trim,
                    frames/spec.fps, spec.narration_gain)
    for clip in spec.audio:
        if not clip.mute and (not solo or clip.solo):
            add(clip.asset_id, clip.track, clip.start, clip.trim, clip.length or duration,
                clip.gain, clip.loop, clip.fade_in, clip.fade_out)

    mix = []
    for track, labels in tracks.items():
        if labels:
            graph.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0,apad=whole_dur={duration}[{track}]")
            mix.append(f"[{track}]")
    if spec.duck and tracks["voice"] and tracks["music"]:
        graph += ["[voice]asplit[voice_mix][sidechain]",
                  f"[music][sidechain]sidechaincompress=threshold={spec.duck_threshold}:ratio={spec.duck_amount}:"
                  f"attack={spec.duck_attack}:release={spec.duck_release}[ducked]"]
        mix = ["[voice_mix]", "[ducked]"] + (["[effects]"] if tracks["effects"] else [])
    if mix:
        graph.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0,"
                     f"alimiter=limit=.92:level=false:latency=true,atrim=duration={duration}[audio]")
    else:
        graph.append(f"anullsrc=r=48000:cl=stereo:d={duration}[audio]")
    return inputs, graph


def prune_cache(directory, budget=512 * 1024 * 1024):
    files = sorted(directory.glob("*.mp4"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    size = 0
    for path in files:
        size += path.stat().st_size
        if size > budget:
            path.unlink(missing_ok=True)


def render(studio, project, id, request, work):
    spec = validate(studio, project, request)
    if request.preview:
        scale = min(1, 640 / max(spec.width, spec.height))
        spec = spec.model_copy(update={"width": max(2, round(spec.width * scale / 2) * 2),
                                       "height": max(2, round(spec.height * scale / 2) * 2)})
    starts, lengths, overlaps, frames = spec.timing()
    duration = frames / spec.fps
    inputs, graph = [], []
    for index, (scene, length) in enumerate(zip(spec.scenes, lengths)):
        studio.check()
        clip = scene_file(studio, project, scene, spec, length, work, studio.check)
        inputs += ["-threads", "1", "-i", str(clip)]
        graph.append(f"[{index}:v]fps={spec.fps},settb=1/{spec.fps},setpts=PTS-STARTPTS,format=yuv444p[s{index}]")
        studio.update(id, "running", .5 * (index + 1) / len(lengths), f"Preparing scene {index + 1} of {len(lengths)}")
    previous = "s0"
    for index in range(1, len(lengths)):
        output = f"v{index}"
        pair = f"[{previous}][s{index}]"
        if overlaps[index]:
            transition = TRANSITIONS[spec.scenes[index].transition]
            effect = f"xfade=transition={transition}:duration={overlaps[index]/spec.fps}:offset={starts[index]/spec.fps}"
        else:
            effect = "concat=n=2:v=1:a=0"
        graph.append(f"{pair}{effect},fps={spec.fps},settb=1/{spec.fps}[{output}]")
        previous = output
    cues = caption_cues(spec)
    titles = [{"start": start/spec.fps, "end": (start + min(length, spec.fps * 3))/spec.fps,
               "text": scene.title} for scene, start, length in zip(spec.scenes, starts, lengths) if scene.title.strip()]
    index = len(lengths)
    visible = titles + (cues if request.captions == "burn" else [])
    if visible:
        manifest = caption_input(work, spec, frames, visible, studio.check)
        inputs += ["-safe", "0", "-f", "concat", "-i", str(manifest)]
        graph += [f"[{index}:v]fps={spec.fps},format=rgba[caption]",
                  f"[{previous}][caption]overlay=format=auto[composite]"]
        previous = "composite"
        index += 1
    audio_inputs, audio_filters = audio_graph(studio, project, spec, index, starts, lengths, frames)
    graph += audio_filters
    target = work / ("video." + request.container)
    encoder = VIDEO_CODECS[request.video_codec]
    video_options = {"libx264": ["-preset", "fast"], "libx265": ["-preset", "fast", "-tag:v", "hvc1"],
                     "libvpx-vp9": ["-b:v", "0", "-deadline", "good", "-cpu-used", "4"],
                     "libaom-av1": ["-b:v", "0", "-cpu-used", "6"]}[encoder]
    mux_options = ["-movflags", "+faststart"] if request.container in ("mp4", "mov") else []
    audio_options = [] if request.audio_codec == "pcm" else ["-b:a", f"{request.audio_bitrate}k"]
    run(work, [*inputs, *audio_inputs, "-filter_complex", ";".join(graph), "-map", f"[{previous}]",
               "-map", "[audio]", "-t", str(duration), "-frames:v", str(frames), "-r", str(spec.fps),
               "-c:v", encoder, "-threads", "4", "-pix_fmt", "yuv420p", "-crf", str(request.crf),
               *video_options, "-c:a", AUDIO_CODECS[request.audio_codec], *audio_options, *mux_options, str(target)],
        studio.check, seconds=duration, update=lambda ratio: studio.update(id, "running", .5 + .45*ratio, "Encoding video"))
    verify(target, spec, request, duration)
    studio.check()
    prune_cache(studio.directory(project, "video-cache"))
    audio_ids = {str(clip.asset_id) for clip in spec.audio}
    audio_ids.update(str(scene.narration_id) for scene in spec.scenes if scene.narration_id)
    restricted = any(studio.asset(project, asset, "audio").get("research_only") for asset in audio_ids)
    return studio.record_asset(project, id, "export", request.filename, {
        "extension": request.container, "source_type": "video_creator", "preview": request.preview,
        "duration_seconds": duration, "width": spec.width, "height": spec.height, "fps": spec.fps,
        "cues": cues if request.captions != "none" else [], "size_bytes": target.stat().st_size,
        "research_only": restricted,
    }, target, target.suffix)


def verify(target, spec, request, duration):
    probe = _binary("ffprobe")
    if not probe:
        raise StudioError("FFprobe is required to verify exports.", 503)
    try:
        info = json.loads(subprocess.check_output(
            [probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(target)], timeout=20))
        video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
        audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
        valid = (
            (video["width"], video["height"]) == (spec.width, spec.height)
            and Fraction(video["avg_frame_rate"]) == spec.fps
            and video["codec_name"] == request.video_codec
            and audio["codec_name"] == AUDIO_CODECS[request.audio_codec].removeprefix("lib")
            and abs(float(info["format"]["duration"]) - duration) <= max(.075, 2/spec.fps)
        )
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        raise StudioError("FFprobe could not read the exported audio and video streams.", 502) from error
    if not valid:
        raise StudioError("The exported video failed duration, codec, frame rate, or resolution verification.", 502)
