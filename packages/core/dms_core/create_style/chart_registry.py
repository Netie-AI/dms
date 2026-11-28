"""Chart kinds the model may name. Adding a kind is one register() call.

Nothing here chooses a kind from a sentence or a column name. The model
fills ``kind``. A click in the HTML file switches that slot and the same
numbers are drawn again.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw

from dms_core.create_style.errors import CreateError
from dms_core.create_style.theme import Theme

Draw = Callable[[dict[str, Any], int, int, Theme], Image.Image]
_DRAW: dict[str, Draw] = {}


def register(name: str, draw: Draw) -> None:
    """Add a kind. There is no cap. The name is a slot, not a phrase."""
    if not isinstance(name, str) or not name or len(name) > 32:
        raise CreateError("bad_chart")
    if any(not (char.islower() or char.isdigit() or char == "_") for char in name):
        raise CreateError("bad_chart")
    _DRAW[name] = draw


def unregister(name: str) -> None:
    """Drop a kind a test registered. Built-ins stay if you do not pass them."""
    _DRAW.pop(name, None)


def known() -> tuple[str, ...]:
    return tuple(_DRAW)


def render_chart(chart: dict[str, Any], width: int, height: int, theme: Theme) -> bytes:
    if width < 8 or height < 8:
        raise CreateError("layout_overflow")
    draw = _DRAW.get(str(chart.get("kind") or ""))
    if draw is None:
        raise CreateError("bad_chart")
    image = draw(chart, width, height, theme)
    from dms_core.create_style.paint import png_bytes

    return png_bytes(image)


def _rgb(colour: str) -> tuple[int, int, int]:
    text = colour.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _series(chart: dict[str, Any]) -> list[dict[str, Any]]:
    return list(chart["series"])


def _peak(series: list[dict[str, Any]]) -> float:
    peak = 0.0
    for item in series:
        for number in item["values"]:
            peak = max(peak, abs(float(number)))
    return peak or 1.0


def _canvas(
    width: int, height: int, theme: Theme
) -> tuple[Image.Image, ImageDraw.ImageDraw, int, int, int, int]:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    left, right, top, bottom = 8, width - 8, 8, height - 8
    ink = _rgb(theme.colours.line) + (255,)
    draw.line((left, bottom, right, bottom), fill=ink, width=1)
    return image, draw, left, right, top, bottom


def _colours(theme: Theme, count: int) -> list[tuple[int, int, int]]:
    accent = _rgb(theme.colours.accent)
    muted = _rgb(theme.colours.muted)
    return [accent if index == 0 else muted for index in range(count)]


def _bars(chart: dict[str, Any], width: int, height: int, theme: Theme) -> Image.Image:
    image, draw, left, right, top, bottom = _canvas(width, height, theme)
    series = _series(chart)
    count = len(chart["categories"])
    peak = _peak(series)
    plot_w = max(right - left, 1)
    plot_h = max(bottom - top, 1)
    slot = plot_w / count
    group = slot * 0.72
    bar_w = group / max(len(series), 1)
    colours = _colours(theme, len(series))
    for index, item in enumerate(series):
        colour = colours[index] + (230,)
        for pos, number in enumerate(item["values"]):
            x0 = left + slot * pos + (slot - group) / 2 + bar_w * index
            bar_h = (float(number) / peak) * plot_h
            y0 = bottom - bar_h
            draw.rectangle((x0, y0, x0 + bar_w - 1, bottom), fill=colour)
    return image


def _line(chart: dict[str, Any], width: int, height: int, theme: Theme) -> Image.Image:
    image, draw, left, right, top, bottom = _canvas(width, height, theme)
    series = _series(chart)
    count = len(chart["categories"])
    peak = _peak(series)
    plot_w = max(right - left, 1)
    plot_h = max(bottom - top, 1)
    slot = plot_w / max(count, 1)
    colours = _colours(theme, len(series))
    for index, item in enumerate(series):
        colour = colours[index] + (255,)
        points = []
        for pos, number in enumerate(item["values"]):
            x = left + slot * pos + slot / 2
            y = bottom - (float(number) / peak) * plot_h
            points.append((x, y))
        if len(points) >= 2:
            draw.line(points, fill=colour, width=2)
        for point in points:
            draw.ellipse(
                (point[0] - 2, point[1] - 2, point[0] + 2, point[1] + 2), fill=colour
            )
    return image


def _area(chart: dict[str, Any], width: int, height: int, theme: Theme) -> Image.Image:
    image = _line(chart, width, height, theme)
    draw = ImageDraw.Draw(image)
    series = _series(chart)
    count = len(chart["categories"])
    peak = _peak(series)
    left, right, top, bottom = 8, width - 8, 8, height - 8
    plot_h = max(bottom - top, 1)
    slot = max(right - left, 1) / max(count, 1)
    colour = _rgb(theme.colours.accent) + (70,)
    item = series[0]
    points = []
    for pos, number in enumerate(item["values"]):
        x = left + slot * pos + slot / 2
        y = bottom - (float(number) / peak) * plot_h
        points.append((x, y))
    if len(points) >= 2:
        poly = [points[0], *points, (points[-1][0], bottom), (points[0][0], bottom)]
        draw.polygon(poly, fill=colour)
    return image


def _hbar(chart: dict[str, Any], width: int, height: int, theme: Theme) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    series = _series(chart)
    values = [float(number) for number in series[0]["values"]]
    peak = max((abs(number) for number in values), default=1.0) or 1.0
    left, right, top, bottom = 8, width - 8, 8, height - 8
    slot = max(bottom - top, 1) / max(len(values), 1)
    colour = _rgb(theme.colours.accent) + (230,)
    ink = _rgb(theme.colours.line) + (255,)
    draw.line((left, top, left, bottom), fill=ink, width=1)
    for pos, number in enumerate(values):
        y0 = top + slot * pos + slot * 0.2
        y1 = y0 + slot * 0.6
        x1 = left + (number / peak) * max(right - left, 1)
        draw.rectangle((left, y0, x1, y1), fill=colour)
    return image


def _stacked(chart: dict[str, Any], width: int, height: int, theme: Theme) -> Image.Image:
    image, draw, left, right, top, bottom = _canvas(width, height, theme)
    series = _series(chart)
    count = len(chart["categories"])
    totals = []
    for pos in range(count):
        totals.append(sum(abs(float(item["values"][pos])) for item in series))
    peak = max(totals) or 1.0
    plot_w = max(right - left, 1)
    plot_h = max(bottom - top, 1)
    slot = plot_w / max(count, 1)
    bar_w = slot * 0.56
    colours = _colours(theme, len(series))
    for pos in range(count):
        y = float(bottom)
        x0 = left + slot * pos + (slot - bar_w) / 2
        for index, item in enumerate(series):
            bar_h = (abs(float(item["values"][pos])) / peak) * plot_h
            draw.rectangle((x0, y - bar_h, x0 + bar_w, y), fill=colours[index] + (230,))
            y -= bar_h
    return image


def _pie(chart: dict[str, Any], width: int, height: int, theme: Theme) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    values = [max(float(number), 0.0) for number in _series(chart)[0]["values"]]
    total = sum(values) or 1.0
    side = max(min(width, height) - 16, 8)
    box = ((width - side) / 2, (height - side) / 2, (width + side) / 2, (height + side) / 2)
    start = -90.0
    accent = _rgb(theme.colours.accent)
    muted = _rgb(theme.colours.muted)
    for index, number in enumerate(values):
        sweep = 360.0 * number / total
        colour = accent if index == 0 else muted
        draw.pieslice(box, start, start + sweep, fill=colour + (230,))
        start += sweep
    return image


def _scatter(chart: dict[str, Any], width: int, height: int, theme: Theme) -> Image.Image:
    image, draw, left, right, top, bottom = _canvas(width, height, theme)
    series = _series(chart)
    ys = [float(number) for number in series[0]["values"]]
    if len(series) > 1:
        xs = [float(number) for number in series[1]["values"]]
    else:
        xs = [float(index) for index in range(len(ys))]
    peak_x = max((abs(number) for number in xs), default=1.0) or 1.0
    peak_y = max((abs(number) for number in ys), default=1.0) or 1.0
    colour = _rgb(theme.colours.accent) + (230,)
    plot_w = max(right - left, 1)
    plot_h = max(bottom - top, 1)
    for x_value, y_value in zip(xs, ys, strict=False):
        x = left + (abs(x_value) / peak_x) * plot_w
        y = bottom - (y_value / peak_y) * plot_h
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=colour)
    return image


def _waterfall(chart: dict[str, Any], width: int, height: int, theme: Theme) -> Image.Image:
    image, draw, left, right, top, bottom = _canvas(width, height, theme)
    values = [float(number) for number in _series(chart)[0]["values"]]
    running = 0.0
    levels: list[tuple[float, float]] = []
    for number in values:
        levels.append((running, running + number))
        running += number
    peak = max((max(abs(a), abs(b)) for a, b in levels), default=1.0) or 1.0
    plot_w = max(right - left, 1)
    plot_h = max(bottom - top, 1)
    slot = plot_w / max(len(values), 1)
    bar_w = slot * 0.56
    accent = _rgb(theme.colours.accent) + (230,)
    muted = _rgb(theme.colours.muted) + (230,)
    for pos, (start, end) in enumerate(levels):
        x0 = left + slot * pos + (slot - bar_w) / 2
        y0 = bottom - (max(start, end) / peak) * plot_h
        y1 = bottom - (min(start, end) / peak) * plot_h
        draw.rectangle((x0, y0, x0 + bar_w, y1), fill=accent if end >= start else muted)
        if pos:
            prev = levels[pos - 1][1]
            y = bottom - (prev / peak) * plot_h
            px = left + slot * (pos - 1) + (slot - bar_w) / 2 + bar_w
            draw.line((px, y, x0, y), fill=muted, width=1)
    return image


def _register_builtins() -> None:
    register("bar", _bars)
    register("line", _line)
    register("area", _area)
    register("hbar", _hbar)
    register("stacked", _stacked)
    register("pie", _pie)
    register("scatter", _scatter)
    register("waterfall", _waterfall)


_register_builtins()
