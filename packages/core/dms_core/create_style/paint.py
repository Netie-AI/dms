"""Pixels for icons, charts, and square crops. Sentences are not drawn here."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw

from dms_core.create_style.errors import CreateError
from dms_core.create_style.theme import Theme

_ICON = 24


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def png_size(raw: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(raw)) as image:
            return image.size
    except Exception:
        raise CreateError("bad_image") from None


def square_png(raw: bytes, side: int) -> bytes:
    """Center-crop to a square, then scale. The crop is the layout, not a caption."""
    if side < 1:
        raise CreateError("layout_overflow")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            frame = image.convert("RGBA")
    except Exception:
        raise CreateError("bad_image") from None
    width, height = frame.size
    if width < 1 or height < 1:
        raise CreateError("bad_image")
    edge = min(width, height)
    left = (width - edge) // 2
    top = (height - edge) // 2
    cropped = frame.crop((left, top, left + edge, top + edge))
    cropped = cropped.resize((side, side), Image.Resampling.LANCZOS)
    return png_bytes(cropped)


def _rgb(colour: str) -> tuple[int, int, int]:
    text = colour.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def icon_png(kind: str, colour: str) -> bytes:
    """A 24px line icon. Stroke only, one colour, square caps off."""
    image = Image.new("RGBA", (_ICON, _ICON), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ink = _rgb(colour) + (255,)
    width = 2
    if kind == "image":
        draw.rounded_rectangle((3, 4, 20, 19), radius=2, outline=ink, width=width)
        draw.ellipse((7, 7, 11, 11), outline=ink, width=width)
        draw.line((4, 18, 9, 13, 13, 16, 16, 12, 20, 18), fill=ink, width=width)
    elif kind == "columns":
        draw.rounded_rectangle((3, 4, 10, 20), radius=1, outline=ink, width=width)
        draw.rounded_rectangle((13, 4, 20, 20), radius=1, outline=ink, width=width)
    elif kind == "kpi":
        draw.line((5, 20, 5, 12), fill=ink, width=width)
        draw.line((12, 20, 12, 6), fill=ink, width=width)
        draw.line((19, 20, 19, 10), fill=ink, width=width)
        draw.line((3, 20, 21, 20), fill=ink, width=width)
    elif kind == "chart":
        draw.line((4, 4, 4, 20, 21, 20), fill=ink, width=width)
        draw.line((6, 15, 10, 11, 14, 13, 20, 6), fill=ink, width=width)
    elif kind == "table":
        draw.rectangle((3, 4, 21, 20), outline=ink, width=width)
        draw.line((3, 9, 21, 9), fill=ink, width=width)
        draw.line((3, 14, 21, 14), fill=ink, width=width)
        draw.line((10, 4, 10, 20), fill=ink, width=width)
    else:
        draw.ellipse((4, 4, 20, 20), outline=ink, width=width)
        draw.line((8, 12, 16, 12), fill=ink, width=width)
    return png_bytes(image)


def chart_png(chart: dict[str, Any], width: int, height: int, theme: Theme) -> bytes:
    """Draw the kind the model named. No title and no sentence."""
    from dms_core.create_style.chart_registry import render_chart

    return render_chart(chart, width, height, theme)


def page_png(page: Any, theme: Theme) -> bytes:
    """Paint one laid-out page. Same boxes the office files use."""
    image = Image.new("RGBA", (page.w, page.h), _rgb(theme.colours.paper) + (255,))
    draw = ImageDraw.Draw(image)
    font_cache: dict[tuple[str, int, bool, bool], Any] = {}
    for box in page.boxes:
        if box.name == "paper":
            continue
        if box.kind == "rect":
            fill = _rgba(box.fill or theme.colours.card, box.alpha)
            draw.rectangle((box.x, box.y, box.x + box.w - 1, box.y + box.h - 1), fill=fill)
            line = _rgb(theme.colours.line) + (255,)
            draw.rectangle((box.x, box.y, box.x + box.w - 1, box.y + box.h - 1), outline=line)
        elif box.kind == "image" and box.image:
            with Image.open(io.BytesIO(box.image)) as pasted:
                frame = pasted.convert("RGBA").resize((box.w, box.h), Image.Resampling.LANCZOS)
            image.alpha_composite(frame, (box.x, box.y))
        elif box.kind == "text" and box.text:
            face = _font(font_cache, box.font, box.size, box.bold, italic=box.italic)
            lines = box.lines or (box.text,)
            for index, line in enumerate(lines):
                draw.text(
                    (box.x, box.y + index * box.line),
                    line,
                    fill=_rgb(box.color) + (255,),
                    font=face,
                )
    return png_bytes(image)


def _rgba(colour: str, alpha: float) -> tuple[int, int, int, int]:
    red, green, blue = _rgb(colour)
    return red, green, blue, max(0, min(255, int(round(alpha * 255))))


def _font(
    cache: dict[tuple[str, int, bool, bool], Any],
    name: str,
    size: int,
    bold: bool,
    *,
    italic: bool = False,
) -> Any:
    key = (name, size, bold, italic)
    if key in cache:
        return cache[key]
    from dms_core.create_style.faces import face_file

    path = face_file((name, "Inter", "Arial"), bold=bold, italic=italic)
    face: Any = None
    if path:
        try:
            from PIL import ImageFont

            face = ImageFont.truetype(path, size=size)
        except OSError:
            face = None
    if face is None:
        from PIL import ImageFont

        face = ImageFont.load_default()
    cache[key] = face
    return face
