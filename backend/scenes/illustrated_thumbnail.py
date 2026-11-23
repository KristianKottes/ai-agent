"""Generate a landscape cover with legible Ukrainian or English title typography."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from backend.scenes.illustrated_render import NOTO_SANS, caption_rows

SIZE = (1280, 720)
SERIF = Path("/System/Library/Fonts/Supplemental/Georgia Bold.ttf")


def thumbnail_prompt(scene, style):
    return (
        "Landscape 16:9 devotional illustration with a peaceful, hopeful atmosphere. "
        f"Visual context: {scene.illustration}. "
        "Keep the described subject as the focus. Landscapes, objects, animals, and biblical symbols can be "
        "the entire composition. People are optional: include them only when the visual context calls for them; "
        "keep object and landscape scenes free of added people or portraits. "
        "Any visible person has a natural, relaxed expression, softly resting eyes and brows, and a gently "
        "closed mouth, conveying quiet hope or trust. Avoid frightened or shocked expressions, wide eyes, "
        "gaping mouths, and exaggerated facial acting. "
        f"{style} "
        "Place the main subject in the left half, sized to suit the scene rather than forcing a face close-up. "
        "Leave the right half as an uncluttered, bright sky. "
        "Golden sunrise rays, glowing amber highlights, blue and peach sky, vivid colors. "
        "No written words, letters, signs, captions, logos, or watermarks."
    )


def compose_thumbnail(background, title, target):
    """Add the title with a real font so spelling is independent of image synthesis."""
    width, height = SIZE
    with Image.open(background) as source:
        image = ImageOps.fit(source.convert("RGB"), SIZE, Image.Resampling.LANCZOS)
    font_path = SERIF if SERIF.is_file() else NOTO_SANS
    # Short cover titles remain readable at small sizes.
    words = title.split()
    headline = " ".join(words[:9])
    if len(words) > 9:
        headline += "…"
    headline = headline[:100].rstrip()
    region_width = round(width * .53)
    for font_size in range(104, 23, -2):
        font = ImageFont.truetype(str(font_path), font_size)
        rows = caption_rows(headline, font, region_width - 42)
        if len(rows) <= 4 and max(font.getlength(" ".join(row)) for row in rows) <= region_width - 42:
            break

    # A soft light wash leaves the underlying sky visible and keeps the title legible.
    wash = Image.new("RGBA", SIZE, (255, 243, 214, 0))
    mask = Image.new("L", (width, 1))
    mask.putdata([round(175 * max(0, min(1, (x / width - .38) / .22))) for x in range(width)])
    wash.putalpha(mask.resize(SIZE))
    image = Image.alpha_composite(image.convert("RGBA"), wash)
    draw = ImageDraw.Draw(image)
    x = round(width * .45)
    line_height = round(font_size * 1.25)
    y = max(45, (height - line_height * len(rows)) // 2 - 20)
    for row in rows:
        draw.text((x, y), " ".join(row), font=font, fill="#142c50", stroke_width=2,
                  stroke_fill="#fff2d5", anchor="la")
        y += line_height
    draw.line((x + 10, y + 14, width - 55, y + 14), fill="#d79720", width=8)
    pending = target.with_name("thumbnail.pending.png")
    image.convert("RGB").save(pending, "PNG")
    pending.replace(target)
