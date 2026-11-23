"""Draw the original illustration and its speech-driven facial poses."""

from __future__ import annotations

import math
from functools import lru_cache

SIZE = 480
LOOKS = {
    "maya": ("Maya", "#bc795e", "#281b23", "#f2a58e"),
    "eli": ("Eli", "#95634e", "#241915", "#c38a74"),
}
BACKDROPS = {
    "ocean": ("#08192b", "#206665", "#5dc7be"),
    "plum": ("#21132d", "#623c70", "#d6a5f5"),
    "ember": ("#2b181d", "#7c443b", "#f1b38d"),
}
OUTFITS = {"ink": ("#223452", "#364e72"), "sage": ("#284d4b", "#43726b"), "rose": ("#784052", "#a75e6b")}

# Width and opening of the mouth relative to the head. Consonants like M/B/P
# share a closure; phonemes with rounded lips stay visibly distinct.
POSES = {
    "rest": (22, 1), "closed": (22, 0), "wide": (25, 8), "open": (24, 15),
    "round": (12, 14), "pucker": (9, 9), "teeth": (22, 5), "tongue": (21, 8),
}
PHONES = {
    "P": "closed", "B": "closed", "M": "closed",
    "AA": "open", "AE": "open", "AH": "open", "AW": "open",
    "AY": "open", "ER": "open", "EH": "wide", "EY": "wide", "IH": "wide", "IY": "wide",
    "AO": "round", "OW": "round", "OY": "round", "UH": "pucker", "UW": "pucker", "W": "pucker",
    "F": "teeth", "V": "teeth", "S": "teeth", "Z": "teeth",
    "TH": "tongue", "DH": "tongue", "L": "tongue",
    "SIL": "rest", "+SPN+": "rest", "+NSN+": "rest",
}


def mouth_pose(phone: str) -> str:
    return PHONES.get(phone.rstrip("012"), "wide")


@lru_cache(maxsize=3)
def _background(key):
    from PIL import Image, ImageColor, ImageDraw

    dark, light, accent = BACKDROPS[key]
    d, l = ImageColor.getrgb(dark), ImageColor.getrgb(light)
    canvas = Image.new("RGB", (SIZE, SIZE))
    draw = ImageDraw.Draw(canvas)
    for y in range(SIZE):
        p = 0.2 + 0.8 * y / SIZE
        draw.line((0, y, SIZE, y), tuple(round(a * (1 - p) + b * p) for a, b in zip(d, l)))
    detail = Image.new("RGBA", canvas.size)
    draw = ImageDraw.Draw(detail)
    rgb = ImageColor.getrgb(accent)
    draw.ellipse((70, 40, 410, 380), fill=(*rgb, 19))
    draw.ellipse((86, 56, 394, 364), outline=(*rgb, 80), width=2)
    draw.ellipse((107, 77, 373, 343), outline=(*rgb, 43), width=1)
    for x, y, radius in ((74, 151, 3), (388, 121, 4), (412, 282, 3), (99, 303, 2)):
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(*rgb, 140))
    return Image.alpha_composite(canvas.convert("RGBA"), detail)


def portrait(look: str, backdrop: str, outfit: str, pose="rest", t=0.0, motion=True):
    from PIL import Image, ImageDraw

    _, skin, hair, highlight = LOOKS[look]
    cloth, seam = OUTFITS[outfit]
    result = _background(backdrop).copy()
    body = ImageDraw.Draw(result)
    body.ellipse((66, 382, 414, 586), fill="#14232f")
    body.rounded_rectangle((88, 375, 392, 526), radius=92, fill=cloth)
    body.polygon(((128, 480), (171, 398), (196, 384), (242, 422), (284, 384), (311, 396), (351, 480)), fill=seam)
    body.polygon(((189, 388), (241, 421), (290, 388), (259, 480), (223, 480)), fill="#151d2d")
    body.rounded_rectangle((204, 315, 276, 397), 29, fill=skin)

    head = Image.new("RGBA", result.size)
    draw = ImageDraw.Draw(head)
    if look == "maya":
        draw.rounded_rectangle((135, 103, 344, 359), radius=85, fill=hair)
        draw.ellipse((143, 228, 181, 359), fill="#36242c")
    else:
        draw.rounded_rectangle((146, 100, 332, 278), radius=72, fill=hair)
    draw.ellipse((143, 211, 178, 268), fill=skin)
    draw.ellipse((302, 211, 337, 268), fill=skin)
    draw.ellipse((158, 119, 322, 342), fill=skin)
    draw.ellipse((173, 136, 307, 324), fill=highlight)
    draw.ellipse((175, 144, 315, 321), fill=skin)
    if look == "maya":
        draw.polygon(((158, 190), (157, 148), (189, 103), (252, 98), (312, 116),
                      (328, 171), (301, 202), (292, 153), (255, 156), (213, 186)), fill=hair)
        draw.arc((161, 110, 302, 213), 198, 322, fill="#533747", width=5)
        for x in (155, 325):
            draw.ellipse((x-5, 256, x+5, 270), outline="#edc989", width=3)
    else:
        draw.polygon(((158, 187), (148, 151), (164, 109), (213, 89), (286, 90),
                      (326, 117), (333, 173), (298, 148), (246, 152), (203, 144)), fill=hair)
        draw.arc((166, 101, 304, 187), 205, 324, fill="#493126", width=5)
    draw.arc((179, 183, 225, 204), 194, 344, fill=hair, width=4)
    draw.arc((255, 183, 301, 204), 196, 347, fill=hair, width=4)
    blink = motion and (t + 1.2) % 3.8 < 0.12
    for x in (202, 278):
        if blink:
            draw.arc((x-14, 215, x+14, 229), 0, 180, fill=hair, width=3)
        else:
            draw.ellipse((x-14, 212, x+14, 226), fill="#fff0dc")
            draw.ellipse((x-5, 211, x+6, 227), fill="#301b25")
            draw.ellipse((x+1, 214, x+4, 217), fill="#fff0dc")
    draw.line(((242, 216), (234, 250), (244, 253)), fill=highlight, width=3)
    if look == "eli":
        for x in (201, 278):
            draw.rounded_rectangle((x-24, 199, x+26, 233), radius=10, outline="#2b2530", width=3)
        draw.line((227, 211, 254, 211), fill="#2b2530", width=3)

    width, opening = POSES[pose]
    if opening > 1:
        draw.ellipse((240-width, 282-opening//2, 240+width, 282+opening), fill="#422131", outline="#794751", width=2)
        if pose in {"teeth", "wide", "tongue", "open"}:
            draw.rounded_rectangle((245-width, 280-opening//3, 235+width, 284), radius=2, fill="#ffe8d2")
        if pose == "tongue":
            draw.ellipse((231, 284, 249, 290), fill="#df8e8d")
    else:
        draw.arc((240-width, 274, 240+width, 288), 8, 172, fill="#613342", width=3)

    offset = (0, 0)
    if motion:
        head = head.rotate(1.8 * math.sin(t * 1.44), Image.Resampling.BICUBIC, center=(240, 318))
        offset = (round(2 * math.sin(t * 1.2)), round(2 * math.sin(t * 1.8)))
    result.alpha_composite(head, offset)
    return result.convert("RGB")
