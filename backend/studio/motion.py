"""Deterministic Pillow compositing shared by preview and full export."""

from __future__ import annotations

import math
import os
import random
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.studio.audio import StudioError

Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Style = Literal["spectrum", "waveform", "bars", "particles", "vinyl", "ambient", "motion"]


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Keyframe(Spec):
    time: float = Field(ge=0, le=1200)
    value: float = Field(ge=-3600, le=3600)
    easing: Literal["linear", "ease_in", "ease_out", "ease_in_out"] = "linear"


class ColorKeyframe(Spec):
    time: float = Field(ge=0, le=1200)
    value: Color
    easing: Literal["linear", "ease_in", "ease_out", "ease_in_out"] = "linear"


class Binding(Spec):
    source: Literal["bass", "treble", "rms", "peak"]
    property: Literal["x", "y", "scale", "rotation", "opacity", "blur", "glow", "reveal"]
    strength: float = Field(ge=-360, le=360)


class Layer(Spec):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,48}$")
    kind: Literal["text", "ring", "diamond"] = "text"
    text: str = Field(default="", max_length=240)
    x: float = Field(default=.5, ge=-1, le=2)
    y: float = Field(default=.24, ge=-1, le=2)
    scale: float = Field(default=1, ge=.01, le=4)
    rotation: float = Field(default=0, ge=-3600, le=3600)
    opacity: float = Field(default=1, ge=0, le=1)
    blur: float = Field(default=0, ge=0, le=40)
    glow: float = Field(default=0, ge=0, le=40)
    reveal: float = Field(default=1, ge=0, le=1)
    color: Optional[Color] = None
    start: float = Field(default=0, ge=0, le=1200)
    end: Optional[float] = Field(default=None, gt=0, le=1200)
    keyframes: dict[Literal["x", "y", "scale", "rotation", "opacity", "blur", "glow", "reveal"],
                    list[Keyframe]] = Field(default_factory=dict)
    colors: list[ColorKeyframe] = Field(default_factory=list, max_length=64)
    bindings: list[Binding] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_timeline(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("A layer must end after its start.")
        for track in [*self.keyframes.values(), self.colors]:
            if len(track) > 64 or any(a.time >= b.time for a, b in zip(track, track[1:])):
                raise ValueError("Use up to 64 keyframes in strictly increasing time order.")
        return self


class Composition(Spec):
    version: Literal[1] = 1
    style: Style = "motion"
    width: int = Field(default=1280, ge=240, le=3840, multiple_of=2)
    height: int = Field(default=720, ge=240, le=3840, multiple_of=2)
    fps: int = Field(default=24, ge=12, le=60)
    background: Color = "#101321"
    primary: Color = "#70dbf2"
    secondary: Color = "#9972f2"
    bar_count: int = Field(default=36, ge=8, le=96)
    mirrored: bool = False
    filled: bool = False
    peaks: bool = True
    intensity: float = Field(default=1, ge=0, le=3)
    glow: float = Field(default=.4, ge=0, le=1)
    captions: bool = True
    camera: bool = False
    motion_blur: bool = False
    layers: list[Layer] = Field(default_factory=list, max_length=12)
    artwork: Optional[str] = Field(default=None, pattern=r"^[a-f0-9-]{36}$")
    backdrop: Optional[str] = Field(default=None, pattern=r"^[a-f0-9-]{36}$")

    @model_validator(mode="after")
    def validate_composition(self):
        if self.width * self.height > 8_294_400:
            raise ValueError("Canvas pixel count exceeds 4K UHD.")
        if len({layer.id for layer in self.layers}) != len(self.layers):
            raise ValueError("Layer identifiers must be unique.")
        return self


class RenderSettings(Spec):
    format: Literal["mp4", "webm", "png"] = "mp4"
    transparent: bool = False

    @model_validator(mode="after")
    def validate_transparency(self):
        if self.transparent and self.format == "mp4":
            raise ValueError("Choose WebM or PNG frames for transparency.")
        return self


def _title(text):
    return Layer(id="title", text=text, glow=3,
                 keyframes={"y": [Keyframe(time=0, value=.29), Keyframe(time=1.1, value=.23, easing="ease_out")],
                            "reveal": [Keyframe(time=0, value=0), Keyframe(time=.75, value=1, easing="ease_out")]},
                 bindings=[Binding(source="rms", property="scale", strength=.18)])


def _geometry():
    return Layer(id="orbit", kind="diamond", scale=1.4, opacity=.62, y=.56,
                 keyframes={"rotation": [Keyframe(time=0, value=-30), Keyframe(time=8, value=150)],
                            "opacity": [Keyframe(time=0, value=0), Keyframe(time=.55, value=.55, easing="ease_out")]},
                 colors=[ColorKeyframe(time=0, value="#f07296"), ColorKeyframe(time=6, value="#70dbf2")],
                 bindings=[Binding(source="bass", property="rotation", strength=32),
                           Binding(source="rms", property="glow", strength=18)])


def templates():
    rows = [
        ("Modern Podcast", "bars", "#101725", "#86ddec", "#4478ee"),
        ("Cinematic Inspiration", "motion", "#23132a", "#ffc589", "#f07099"),
        ("Minimal Waveform", "waveform", "#101921", "#a4f1d0", "#47a8aa"),
        ("Neon Spectrum", "spectrum", "#130d25", "#ea8bfe", "#42e3ed"),
        ("Vinyl Music Player", "vinyl", "#21141e", "#f5bf9c", "#e76d77"),
        ("Ambient Meditation", "ambient", "#122724", "#b1e1d1", "#5475a5"),
        ("Modern Typography", "motion", "#15162a", "#f9e4aa", "#f36daa"),
        ("Particle Universe", "particles", "#101329", "#aba6fc", "#698cfd"),
        ("Corporate Motion Graphics", "motion", "#112330", "#79e8de", "#2a9cce"),
        ("Vertical Shorts Visualizer", "spectrum", "#231335", "#ff95b7", "#6755fe"),
    ]
    return [{"id": name.lower().replace(" ", "-"), "name": name, "composition": Composition(
        style=style, background=bg, primary=first, secondary=second,
        width=720 if index == 9 else 1280, height=1280 if index == 9 else 720,
        mirrored=style in ("bars", "waveform"), filled=style == "waveform",
        camera=style == "motion", motion_blur=style == "motion",
        layers=[*([_geometry()] if style == "motion" else []),
                *([Layer(id="halo", kind="ring", y=.56, scale=1.9, opacity=.28, glow=12,
                         bindings=[Binding(source="bass", property="scale", strength=.5)])] if index in (1, 8) else []),
                _title(name)],
    ).model_dump()} for index, (name, style, bg, first, second) in enumerate(rows)]


def eased(x, fn):
    if fn == "ease_in":
        return x ** 2
    if fn == "ease_out":
        return 1 - (1 - x) ** 2
    if fn == "ease_in_out":
        return x * x * (3 - 2 * x)
    return x


def _mix(left, right, ratio):
    return tuple(round(a + (b - a) * ratio) for a, b in zip(left, right))


def _rgb(color):
    return tuple(bytes.fromhex(color[1:]))


def interpolate(keys, time, default, blend):
    if not keys:
        return default
    for left, right in zip(keys, keys[1:]):
        if time < right.time:
            amount = eased(max(0, time - left.time) / (right.time - left.time), right.easing)
            return blend(left.value, right.value, amount)
    return keys[-1].value


def properties(layer, time, analysis, color):
    time -= layer.start
    values = {key: interpolate(layer.keyframes.get(key), time, getattr(layer, key),
                               lambda a, b, t: a + (b - a) * t)
              for key in ("x", "y", "scale", "rotation", "opacity", "blur", "glow", "reveal")}
    for binding in layer.bindings:
        values[binding.property] += binding.strength * analysis.feature(binding.source, time + layer.start)
    values["color"] = _rgb(interpolate(layer.colors, time, layer.color or color,
                                      lambda a, b, t: "#%02x%02x%02x" % _mix(_rgb(a), _rgb(b), t)))
    return values


@lru_cache(maxsize=64)
def font(size):
    from PIL import ImageFont
    bundled = Path(__file__).with_name("NotoSans-Regular.ttf")
    candidates = [bundled, Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                  Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf"]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise StudioError("Install a Cyrillic-capable font for motion typography.", 503)


def wrap(draw, text, face, width):
    lines = []
    for paragraph in text.splitlines():
        line = ""
        for word in paragraph.split():
            proposed = (line + " " + word).lstrip()
            if draw.textlength(proposed, font=face) <= width:
                line = proposed
                continue
            if line:
                lines.append(line)
            line = ""
            for character in word:
                if line and draw.textlength(line + character, font=face) > width:
                    lines.append(line)
                    line = ""
                line += character
        lines.append(line)
    return "\n".join(lines)


@lru_cache(maxsize=4)
def _gradient(width, height, start, end):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (1, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.point((0, y), fill=_mix(start, end, y / height))
    return image.resize((width, height)).convert("RGBA")


@lru_cache(maxsize=8)
def _particles(seed):
    rng = random.Random(seed)
    return [(rng.random(), rng.random(), rng.random(), rng.random()) for _ in range(100)]


class Renderer:
    def __init__(self, composition, analysis, cues=(), *, artwork=None, backdrop=None, transparent=False, preview=False):
        from PIL import Image, ImageOps

        self.spec, self.analysis, self.cues = composition, analysis, cues
        self.cue_starts = [cue["start"] for cue in cues]
        scale = min(1, 640 / max(composition.width, composition.height)) if preview else 1
        self.width, self.height = round(composition.width * scale), round(composition.height * scale)
        self.size = (self.width, self.height)
        self.unit = min(self.size)
        self.transparent = transparent
        self.colors = [_rgb(getattr(composition, key)) for key in ("background", "primary", "secondary")]
        self.artwork, self.backdrop, self.background_video = None, None, None
        self.background_reader, self.background_index = None, -1
        self.preview = preview
        if artwork:
            with Image.open(artwork) as source:
                side = int(.31 * self.unit)
                self.artwork = ImageOps.fit(source.convert("RGBA"), (side, side))
        if backdrop and backdrop.suffix == ".mp4":
            self.background_video = backdrop
        elif backdrop:
            with Image.open(backdrop) as source:
                self.backdrop = ImageOps.fit(source.convert("RGBA"), self.size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.background_reader is not None:
            self.background_reader.kill()
            self.background_reader.wait()
            self.background_reader.stdout.close()
            self.background_reader = None

    def background_frame(self, time):
        from PIL import Image
        from backend.studio.audio import media_binary

        if self.background_video is None:
            return self.backdrop
        fps = self.spec.fps
        index = round(time * fps)
        w, h = self.size
        command = [media_binary(), "-v", "error", "-nostdin", "-stream_loop", "-1", "-i",
                   str(self.background_video), "-an", "-vf",
                   f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                   "-threads", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        if self.background_reader is None:
            if self.preview:
                # Convert to the same output frame grid as the sequential encoder.
                command[-1:-1] = ["-ss", str(index / fps), "-frames:v", "1"]
                self.background_index = index - 1
            self.background_reader = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        while self.background_index < index:
            pixels = self.background_reader.stdout.read(w * h * 3)
            if len(pixels) != w * h * 3:
                raise StudioError("The visualizer could not decode the background video.", 502)
            self.backdrop = Image.frombytes("RGB", self.size, pixels).convert("RGBA")
            self.background_index += 1
        return self.backdrop

    def _visual(self, image, time, bands, wave, rms):
        from PIL import ImageDraw

        spec = self.spec
        w, h = self.size
        unit = self.unit
        x, y = w / 2, h * .56
        first, second = self.colors[1:]
        draw = ImageDraw.Draw(image)
        amount = min(1.7, rms * 3 * spec.intensity)
        colors = lambda i, n: _mix(first, second, i / max(1, n-1))
        if spec.style in ("spectrum", "vinyl", "bars"):
            n = spec.bar_count
            for i in range(n):
                amplitude = bands[round(i * (len(bands)-1) / (n-1))] * spec.intensity
                color = colors(i, n)
                if spec.style == "bars":
                    left = w * (.09 + i * .82 / n)
                    right = left + max(1, w * .59 / n)
                    top = y - max(2, min(h*.26, amplitude * h*.34))
                    bottom = y + (y-top if spec.mirrored else 0)
                    draw.rounded_rectangle((left, top, right, bottom), radius=max(1, int((right-left)*.3)), fill=color)
                    if spec.peaks:
                        draw.line((left, top-4, right, top-4), fill=color, width=2)
                else:
                    angle = i * math.tau / n
                    radius = unit * (.24 + amount*.018)
                    reach = radius + max(2, min(unit*.13, amplitude * unit*.2))
                    draw.line((x+radius*math.cos(angle), y+radius*math.sin(angle),
                               x+reach*math.cos(angle), y+reach*math.sin(angle)), fill=color, width=max(2, round(unit*.009)))
            if spec.style == "vinyl":
                for i in range(10, 21):
                    r = unit * i / 100
                    draw.ellipse((x-r, y-r, x+r, y+r), outline=_mix(self.colors[0], second, .35), width=2)
                    a = time * 1.4
                    draw.arc((x-r, y-r, x+r, y+r), math.degrees(a)-40, math.degrees(a)+10, fill=first, width=2)
                r = unit * .08
                draw.ellipse((x-r, y-r, x+r, y+r), fill=second)
                draw.line((x, y, x+r*.7*math.cos(time*1.4), y+r*.7*math.sin(time*1.4)), fill=first, width=4)
        if spec.style in ("waveform", "ambient", "motion", "vinyl"):
            baseline = h * (.84 if spec.style == "vinyl" else .61)
            paths = []
            for line in range(3 if spec.style in ("ambient", "motion") else 1):
                path = []
                for i, sample in enumerate(wave):
                    fraction = i / (len(wave)-1)
                    height = float(sample) * h * .27 * spec.intensity
                    if spec.style in ("motion", "ambient"):
                        height = height * .4 + math.sin(fraction*math.tau*(1+line*.13) + time*(.55+line*.17)) * h * (.023+amount*.035)
                    height = max(-h*.22, min(h*.22, height))
                    path.append((w * (.08 + fraction*.84), baseline + line*h*.05 - height))
                paths.append(path)
            for j, path in enumerate(paths):
                if spec.filled and spec.style == "waveform":
                    draw.polygon([(path[0][0], baseline), *path, (path[-1][0], baseline)], fill=(*second, 80))
                for i, (a, b) in enumerate(zip(path, path[1:])):
                    color = colors(i, len(path))
                    draw.line((a, b), fill=(*color, max(80, 255-j*54)), width=max(2, round(unit*.006)))
                    if spec.mirrored:
                        draw.line((a[0], 2*baseline-a[1], b[0], 2*baseline-b[1]), fill=(*color, 130),
                                  width=max(1, round(unit*.004)))
        if spec.style in ("particles", "ambient", "motion"):
            bass = float(bands[:15].mean()) * spec.intensity
            treble = float(bands[-14:].mean()) * spec.intensity
            count = 85 if spec.style == "particles" else 36
            for i, (px, py, z, phase) in enumerate(_particles(93)[:count]):
                xx = w * ((px + time * (.012 + z*.016) + bass*.025*math.sin(time+phase*6)) % 1)
                yy = h * ((py - time * (.009 + .022*z) + treble*.075*math.cos(time+phase*6)) % 1)
                r = unit * (.002 + z*.004) * (1 + amount + z*bass)
                draw.ellipse((xx-r, yy-r, xx+r, yy+r), fill=(*colors(i, count), round(60+170*z)))

    def _layer(self, canvas, layer, time, trail=1.0):
        from PIL import Image, ImageDraw, ImageFilter

        w, h = self.size
        spec = properties(layer, time, self.analysis, self.spec.primary)
        opacity = max(0, min(1, spec["opacity"])) * trail
        reveal = max(0, min(1, spec["reveal"]))
        unit = self.unit
        if opacity == 0 or reveal == 0:
            return
        scale = max(.05, min(4, spec["scale"]))
        pad = round(unit*.045)
        if layer.kind == "text":
            face = font(max(10, min(round(unit*.073*scale), 190)))
            temp = Image.new("RGBA", (1, 1))
            draw = ImageDraw.Draw(temp)
            label = wrap(draw, layer.text, face, w*.80)
            x0, y0, x1, y1 = draw.multiline_textbbox((0, 0), label, font=face, align="center")
            if x1 <= x0 or y1 <= y0:
                return
            tile = Image.new("RGBA", (math.ceil(x1-x0)+2*pad, math.ceil(y1-y0)+2*pad))
            ImageDraw.Draw(tile).multiline_text((pad-x0, pad-y0), label, font=face, align="center", fill=spec["color"])
        else:
            side = round(unit*.23*scale)+2*pad
            tile = Image.new("RGBA", (side, side))
            draw = ImageDraw.Draw(tile)
            if layer.kind == "ring":
                draw.ellipse((pad, pad, side-pad, side-pad), outline=spec["color"], width=max(1, unit//100))
            else:
                draw.polygon([(side/2, pad), (side-pad, side/2), (side/2, side-pad), (pad, side/2)],
                             outline=spec["color"], width=max(1, unit//110))
        if reveal < 1:
            tile.paste((0, 0, 0, 0), (round(tile.width*reveal), 0, tile.width, tile.height))
        tile = tile.rotate(-spec["rotation"], expand=True, resample=Image.Resampling.BICUBIC)
        tile.putalpha(tile.getchannel("A").point(lambda p: round(p*opacity)))
        position = (round(w * spec["x"] - tile.width/2), round(h * spec["y"] - tile.height/2))
        blur = max(0, min(unit*.05, spec["blur"] * unit / 720))
        glow = max(0, min(unit*.055, spec["glow"] * unit / 720))
        if glow and trail == 1:
            halo = Image.new("RGBA", tile.size, spec["color"])
            halo.putalpha(tile.getchannel("A").filter(ImageFilter.GaussianBlur(glow)))
            canvas.alpha_composite(halo, position)
        if blur:
            tile = tile.filter(ImageFilter.GaussianBlur(blur))
        canvas.alpha_composite(tile, position)

    def frame(self, time):
        from bisect import bisect_right
        from PIL import Image, ImageDraw, ImageFilter

        w, h = self.size
        bg, first, second = self.colors
        bands, wave, rms = self.analysis.frame(time)
        if self.transparent:
            canvas = Image.new("RGBA", self.size)
        else:
            canvas = _gradient(w, h, _mix(bg, second, .14), bg).copy()
            background = self.background_frame(time)
            if background:
                canvas.alpha_composite(background)
                canvas.alpha_composite(Image.new("RGBA", self.size, (*bg, 120)))
        visual = Image.new("RGBA", self.size)
        self._visual(visual, time, bands, wave, rms)
        if self.spec.camera:
            zoom = 1.025 + min(.05, rms * .10 * self.spec.intensity)
            drift = max(0, round(w * (zoom - 1) / 2))
            rise = max(0, round(h * (zoom - 1) / 2))
            enlarged = visual.resize((w + drift*2, h + rise*2), Image.Resampling.BILINEAR)
            left = round(drift * (1 + .65*math.sin(time*.6)))
            top = round(rise * (1 + .65*math.cos(time*.5)))
            visual = enlarged.crop((left, top, left+w, top+h))
        glow = self.spec.glow
        if glow:
            canvas.alpha_composite(visual.filter(ImageFilter.GaussianBlur(max(2, self.unit*.023*glow))))
        canvas.alpha_composite(visual)
        if self.artwork:
            art = self.artwork.rotate(-(time*45) % 360, resample=Image.Resampling.BICUBIC) if self.spec.style == "vinyl" else self.artwork
            if self.spec.style in ("spectrum", "vinyl"):
                mask = Image.new("L", art.size)
                ImageDraw.Draw(mask).ellipse((0, 0, art.width-1, art.height-1), fill=255)
                art = art.copy()
                from PIL import ImageChops
                art.putalpha(ImageChops.multiply(art.getchannel("A"), mask))
            canvas.alpha_composite(art, (round((w-art.width)/2), round(h*.56-art.height/2)))
        for layer in self.spec.layers:
            if time >= layer.start and (layer.end is None or time < layer.end):
                if self.spec.motion_blur and layer.kind != "text" and time > layer.start:
                    for shutter, opacity in ((.08, .18), (.04, .28)):
                        self._layer(canvas, layer, max(layer.start, time-shutter), trail=opacity)
                self._layer(canvas, layer, time)
        if self.spec.captions and self.cues:
            index = bisect_right(self.cue_starts, time) - 1
            if index >= 0 and time < self.cues[index]["end"]:
                subtitle = self.cues[index]["text"]
                draw = ImageDraw.Draw(canvas)
                size = max(12, round(self.unit*.041))
                face = font(size)
                label = wrap(draw, subtitle, face, w*.86)
                while label.count("\n") > 3 and size > 12:
                    size -= 2
                    face = font(size)
                    label = wrap(draw, subtitle, face, w*.86)
                bottom = round(h*.93)
                box = draw.multiline_textbbox((w/2, bottom), label, font=face, anchor="md", align="center")
                draw.rounded_rectangle((box[0]-9, box[1]-6, box[2]+9, box[3]+6), radius=5, fill=(9, 12, 20, 218))
                draw.multiline_text((w/2, bottom), label, fill="white", font=face, anchor="md", align="center")
        return canvas
