"""Write the confirmed artifact to pptx, pdf, and html.

The provider chose the words, the page count, and the chart spec.
This lays that out and does not add pages the request did not ask for.
"""

from __future__ import annotations

import base64
import io
import json
import math
import re
import struct
import subprocess
import textwrap
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# ponytail: a fitted page shrinks type and charts to stay on that page.
# A larger page budget is a layout change. It is not a reason to drop lines.
_LINES_PER_PAGE = 45
_WRAP = 90
_HEX = re.compile(r"#([0-9A-Fa-f]{6})")
_PAGE_ASK = re.compile(r"\b(\d{1,2})\s+pages?\b")
_DEFAULT_PALETTE = (
    (27, 77, 62),
    (196, 163, 90),
    (47, 79, 79),
    (176, 96, 80),
    (90, 122, 140),
    (120, 90, 140),
)
_FALLBACK_FONT = Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")
_FONT_FILES: dict[str, Path | None] = {}

Page = dict[str, Any]
Bitmap = tuple[bytes, bytes, int, int]


def one_page_requested(text: str) -> bool:
    return page_budget(text) == 1


def page_budget(text: str) -> int | None:
    """The length the request asked for. None means the text may flow."""
    folded = text.casefold().replace("-", " ")
    if "one page" in folded or "single page" in folded:
        return 1
    found = _PAGE_ASK.search(folded)
    if found is None:
        return None
    return max(1, int(found.group(1)))


def render_artifact(
    folder: Path,
    artifact: Mapping[str, Any],
    *,
    kind: str,
    one_page: bool,
    brand: Mapping[str, Any] | None = None,
    page_budget: int | None = None,
) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "artifact.json").write_text(
        json.dumps(artifact, indent=2, default=str), encoding="utf-8"
    )
    names = ["artifact.json"]
    kit, pages = _prepare(
        artifact, kind=kind, one_page=one_page, page_budget=page_budget, brand=brand
    )
    html_name = f"{kind or 'artifact'}.html"
    (folder / html_name).write_text(_html(artifact, _images(artifact, kit), kit), encoding="utf-8")
    names.append(html_name)
    pdf_name = f"{kind or 'artifact'}.pdf"
    (folder / pdf_name).write_bytes(_pdf(pages, kit))
    names.append(pdf_name)
    if kind == "slides":
        (folder / "slides.pptx").write_bytes(_pptx(artifact, kit))
        names.append("slides.pptx")
    return names


def preview_pngs(
    artifact: Mapping[str, Any],
    *,
    kind: str,
    one_page: bool,
    brand: Mapping[str, Any] | None = None,
    page_budget: int | None = None,
) -> list[bytes]:
    """One PNG per page, in order. Tests use this to check the page count."""
    _kit_value, pages = _prepare(
        artifact, kind=kind, one_page=one_page, page_budget=page_budget, brand=brand
    )
    palette = _palette(_kit_value)
    return [_page_png(page, palette[0]) for page in pages]


def _prepare(
    artifact: Mapping[str, Any],
    *,
    kind: str,
    one_page: bool,
    page_budget: int | None,
    brand: Mapping[str, Any] | None,
) -> tuple[dict[str, str], list[Page]]:
    kit = _kit(brand, artifact)
    images = _images(artifact, kit)
    budget = 1 if one_page else page_budget
    if kind == "slides":
        pages = _slide_pages(artifact, images)
    elif budget is not None:
        pages = _budget_pages(artifact, images, budget)
    else:
        pages = _flow_pages(artifact, images)
    count = len(pages) or 1
    for index, page in enumerate(pages, start=1):
        page["index"] = index
        page["count"] = count
    return kit, pages


def _kit(brand: Mapping[str, Any] | None, artifact: Mapping[str, Any]) -> dict[str, str]:
    style = artifact.get("style") if isinstance(artifact.get("style"), dict) else {}
    merged: dict[str, str] = {}
    for key in ("colours", "fonts", "logo", "tone"):
        value = ""
        if isinstance(style, dict):
            value = str(style.get(key) or "")
        if brand and brand.get(key):
            value = str(brand.get(key) or "")
        merged[key] = value
    return merged


def _palette(kit: Mapping[str, str]) -> list[tuple[int, int, int]]:
    found = [
        (int(item[0:2], 16), int(item[2:4], 16), int(item[4:6], 16))
        for item in _HEX.findall(kit.get("colours") or "")
    ]
    return found or list(_DEFAULT_PALETTE)


def _font_name(kit: Mapping[str, str]) -> str:
    raw = (kit.get("fonts") or "").split(",")[0].strip()
    return raw or "Calibri"


def _pdf_font_name(kit: Mapping[str, str]) -> str:
    clean = re.sub(r"[^A-Za-z0-9]", "", _font_name(kit))
    return clean or "Calibri"


def _hex(colour: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*colour)


def _fmt(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _human(name: str) -> str:
    parts = [part for part in re.split(r"[_\s]+", str(name).strip()) if part]
    if not parts:
        return str(name)
    return " ".join(part[:1].upper() + part[1:] for part in parts)


def _tick_label(value: float) -> str:
    """Plain digits. A million is 1,000,000, not 1e6."""
    number = float(value)
    if abs(number) >= 100 or number.is_integer():
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _trunc_line(note: Any) -> str:
    if not isinstance(note, dict):
        return ""
    shown = note.get("shown")
    total = note.get("total")
    if isinstance(shown, bool) or isinstance(total, bool):
        return ""
    if not isinstance(shown, int) or not isinstance(total, int):
        return ""
    return f"Showing {shown} of {total}."


def _row_line(row: Mapping[str, Any]) -> str:
    return " | ".join(f"{_human(str(key))} {_fmt(value)}" for key, value in row.items())


def _parts(artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [part for part in artifact.get("parts") or [] if isinstance(part, Mapping)]


def _charts(part: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [chart for chart in part.get("charts") or [] if isinstance(chart, Mapping)]


def _images(artifact: Mapping[str, Any], kit: Mapping[str, str]) -> dict[int, Bitmap]:
    found: dict[int, Bitmap] = {}
    palette = _palette(kit)
    for part in _parts(artifact):
        for chart in _charts(part):
            drawn = _chart_bitmap(chart, palette, _font_name(kit))
            if drawn is not None:
                found[id(chart)] = drawn
    return found


def _rows(chart: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [row for row in chart.get("rows") or [] if isinstance(row, dict) and row]


def _keys(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    found: list[str] = []
    for row in rows:
        for key in row:
            if str(key) not in found:
                found.append(str(key))
    return found


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric(rows: Sequence[Mapping[str, Any]], key: str) -> bool:
    seen = False
    for row in rows:
        value = row.get(key)
        if value is None or value == "":
            continue
        if not _is_number(value):
            return False
        seen = True
    return seen


def _mark(chart: Mapping[str, Any]) -> str:
    raw = str(chart.get("type") or chart.get("mark") or "bar").strip().lower()
    if raw in {"line", "multi_line"}:
        return "line"
    if raw == "pie":
        return "pie"
    return "bar"


def _show_labels(chart: Mapping[str, Any]) -> bool:
    flag = chart.get("labels")
    if isinstance(flag, bool):
        return flag
    return True


def _frame(
    chart: Mapping[str, Any],
) -> tuple[str, str, list[str], list[tuple[str, list[float]]], bool] | None:
    """(mark, x label, categories, series, data labels).

    A long table (quarter, region, amount) becomes one series per region.
    A chart spec (type, x, series, y) wins over that guess.
    """
    rows = _rows(chart)
    if not rows:
        return None
    keys = _keys(rows)
    measures = [key for key in keys if _numeric(rows, key)]
    labels = [key for key in keys if key not in measures]
    built = _from_spec(chart, rows, keys, measures, labels)
    if built is None:
        built = _from_shape(rows, measures, labels)
    if built is None:
        return None
    x_key, categories, series = built
    if not categories or not series:
        return None
    return _mark(chart), _human(x_key) or "Category", categories, series, _show_labels(chart)


def _from_spec(
    chart: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    keys: list[str],
    measures: list[str],
    labels: list[str],
) -> tuple[str, list[str], list[tuple[str, list[float]]]] | None:
    x = chart.get("x") if isinstance(chart.get("x"), str) else ""
    y = chart.get("y") if isinstance(chart.get("y"), str) else ""
    series = chart.get("series")
    if not any((x, y, isinstance(series, (str, list)))):
        return None
    if (
        isinstance(series, list)
        and series
        and all(isinstance(item, str) and item in measures for item in series)
    ):
        cat = x if x in keys else (labels[0] if labels else "")
        return cat, *_wide(rows, cat, [str(item) for item in series])
    if isinstance(series, str) and series in measures:
        cat = x if x in keys else (labels[0] if labels else "")
        return cat, *_wide(rows, cat, [series])
    pivot = ""
    if isinstance(series, str) and series in labels:
        pivot = series
    elif len(labels) >= 2:
        pivot = next((key for key in labels if key != x), labels[1])
    if pivot:
        cat = x if x in labels and x != pivot else next((key for key in labels if key != pivot), "")
        value = y if y in measures else (measures[0] if measures else "")
        if cat and value:
            categories, pairs = _pivot(rows, cat, pivot, value)
            if isinstance(series, list) and series:
                wanted = {str(item) for item in series}
                picked = [pair for pair in pairs if pair[0] in wanted]
                if picked:
                    pairs = picked
            return cat, categories, pairs
    if x in keys and measures:
        use = [y] if y in measures else measures
        return x, *_wide(rows, x, use)
    return None


def _from_shape(
    rows: Sequence[Mapping[str, Any]],
    measures: list[str],
    labels: list[str],
) -> tuple[str, list[str], list[tuple[str, list[float]]]] | None:
    if len(labels) >= 2 and len(measures) == 1:
        categories, pairs = _pivot(rows, labels[0], labels[1], measures[0])
        return labels[0], categories, pairs
    if not measures:
        return None
    cat = labels[0] if labels else ""
    return cat, *_wide(rows, cat, measures)


def _wide(
    rows: Sequence[Mapping[str, Any]], cat_key: str, measures: list[str]
) -> tuple[list[str], list[tuple[str, list[float]]]]:
    categories: list[str] = []
    for index, row in enumerate(rows, start=1):
        raw = row.get(cat_key) if cat_key else index
        categories.append(str(raw) if raw not in (None, "") else str(index))
    series: list[tuple[str, list[float]]] = []
    for key in measures:
        values = [_as_float(row.get(key)) for row in rows]
        series.append((_human(key), values))
    return categories, series


def _pivot(
    rows: Sequence[Mapping[str, Any]], x_key: str, series_key: str, y_key: str
) -> tuple[list[str], list[tuple[str, list[float]]]]:
    categories: list[str] = []
    names: list[str] = []
    grid: dict[str, dict[str, float]] = {}
    for row in rows:
        category = str(row.get(x_key) if row.get(x_key) not in (None, "") else "")
        name = str(row.get(series_key) if row.get(series_key) not in (None, "") else "")
        if category not in categories:
            categories.append(category)
        if name not in names:
            names.append(name)
        grid.setdefault(name, {})[category] = _as_float(row.get(y_key))
    series = [(name, [grid[name].get(category, 0.0) for category in categories]) for name in names]
    return categories, series


def _as_float(value: Any) -> float:
    if _is_number(value):
        return float(value)
    return 0.0


def _chart_bitmap(
    chart: Mapping[str, Any], palette: list[tuple[int, int, int]], font: str
) -> Bitmap | None:
    frame = _frame(chart)
    if frame is None:
        return None
    mark, cat_name, categories, series, show = frame
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=80)
    fig.patch.set_facecolor("white")
    slots = list(range(len(categories)))
    title_colour = _hex(palette[0])
    if mark == "pie":
        _draw_pie(ax, categories, series[0][1], palette)
    elif mark == "line":
        _draw_lines(ax, slots, series, palette, show)
    else:
        _draw_bars(ax, slots, categories, series, palette, show)
    if mark != "pie":
        ax.set_xticks(slots, categories, rotation=30 if len(categories) > 6 else 0)
        ax.set_xlabel(cat_name)
        ax.set_ylabel(series[0][0] if len(series) == 1 else "Value")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _tick_label(float(value))))
        if len(series) > 1:
            ax.legend()
    ax.set_title(str(chart.get("label") or "Chart"), color=title_colour, fontname=_mpl_font(font))
    fig.tight_layout()
    canvas: Any = fig.canvas
    canvas.draw()
    width_px, height_px = canvas.get_width_height()
    rgba = bytes(canvas.buffer_rgba())
    png = io.BytesIO()
    fig.savefig(png, format="png")
    plt.close(fig)
    rgb = bytearray(width_px * height_px * 3)
    for pixel in range(width_px * height_px):
        start = pixel * 4
        rgb[pixel * 3 : pixel * 3 + 3] = rgba[start : start + 3]
    return png.getvalue(), bytes(rgb), width_px, height_px


def _mpl_font(name: str) -> str:
    path = _font_path(name)
    if path is None:
        return "DejaVu Sans"
    from matplotlib import font_manager

    font_manager.fontManager.addfont(str(path))
    return font_manager.FontProperties(fname=str(path)).get_name()


def _draw_bars(
    ax: Any,
    slots: list[int],
    categories: list[str],
    series: list[tuple[str, list[float]]],
    palette: list[tuple[int, int, int]],
    show: bool,
) -> None:
    del categories
    count = len(series)
    width = 0.8 / max(count, 1)
    for index, (name, values) in enumerate(series):
        offset = (index - (count - 1) / 2) * width
        colour = _hex(palette[index % len(palette)])
        container = ax.bar(
            [slot + offset for slot in slots], values, width=width, label=name, color=colour
        )
        if show:
            ax.bar_label(container, labels=[_tick_label(value) for value in values], fontsize=7)


def _draw_lines(
    ax: Any,
    slots: list[int],
    series: list[tuple[str, list[float]]],
    palette: list[tuple[int, int, int]],
    show: bool,
) -> None:
    for index, (name, values) in enumerate(series):
        colour = _hex(palette[index % len(palette)])
        ax.plot(slots, values, label=name, color=colour, marker="o")
        if show:
            for slot, value in zip(slots, values, strict=True):
                ax.annotate(
                    _tick_label(value),
                    (slot, value),
                    textcoords="offset points",
                    xytext=(0, 4),
                    ha="center",
                    fontsize=7,
                )


def _draw_pie(
    ax: Any,
    categories: list[str],
    values: list[float],
    palette: list[tuple[int, int, int]],
) -> None:
    colours = [_hex(palette[index % len(palette)]) for index in range(len(values))]
    ax.pie(values, labels=categories, colors=colours)


def _slide_pages(artifact: Mapping[str, Any], images: Mapping[int, Bitmap]) -> list[Page]:
    """One page per part. Every chart on that part stays on that page."""
    pages = [
        _fit_page(_slide_lines(artifact, part), _part_images(part, images), fit=True)
        for part in _parts(artifact)
    ]
    if pages:
        return pages
    title = str(artifact.get("title") or "Empty")
    return [_fit_page([title], [], fit=True)]


def _budget_pages(
    artifact: Mapping[str, Any], images: Mapping[int, Bitmap], budget: int
) -> list[Page]:
    """Fit the doc into the number of pages the request named."""
    lines = _wrap(_doc_lines(artifact, chart_rows=False))
    charts = _doc_images(artifact, images)
    budget = max(1, budget)
    if not lines:
        lines = [str(artifact.get("title") or "Empty")]
    size = max(1, math.ceil(len(lines) / budget))
    chunks = [lines[index : index + size] for index in range(0, len(lines), size)]
    chunks = chunks[:budget] or [[""]]
    while len(chunks) < budget:
        chunks.append([""])
    pages: list[Page] = []
    last = len(chunks) - 1
    for index, chunk in enumerate(chunks):
        pages.append(_fit_page(chunk or [""], charts if index == last else [], fit=True))
    return pages


def _flow_pages(artifact: Mapping[str, Any], images: Mapping[int, Bitmap]) -> list[Page]:
    pages: list[Page] = []
    title = str(artifact.get("title") or "")
    if title:
        pages.append(_fit_page(_wrap([title]), [], fit=False))
    for part in _parts(artifact):
        chunks = _flow(_part_lines(part, chart_rows=True))
        charts = _part_images(part, images)
        if not chunks:
            chunks = [[""]]
        last = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            pages.append(_fit_page(chunk, charts if index == last else [], fit=False))
    if pages:
        return pages
    return [_fit_page([title or "Empty"], [], fit=False)]


def _doc_lines(artifact: Mapping[str, Any], *, chart_rows: bool) -> list[str]:
    lines: list[str] = []
    title = str(artifact.get("title") or "")
    if title:
        lines.append(title)
    for part in _parts(artifact):
        lines.extend(_part_lines(part, chart_rows=chart_rows))
    return [line for line in lines if line]


def _doc_images(artifact: Mapping[str, Any], images: Mapping[int, Bitmap]) -> list[Bitmap]:
    found: list[Bitmap] = []
    for part in _parts(artifact):
        found.extend(_part_images(part, images))
    return found


def _slide_lines(artifact: Mapping[str, Any], part: Mapping[str, Any]) -> list[str]:
    lines = [str(part.get("title") or artifact.get("title") or "")]
    body = str(part.get("body") or "")
    if body:
        lines.append(body)
    note = _trunc_line(part.get("truncated"))
    if note:
        lines.append(note)
    for chart in _charts(part):
        label = str(chart.get("label") or "")
        if label:
            lines.append(label)
        note = _trunc_line(chart.get("truncated"))
        if note:
            lines.append(note)
    return _wrap([line for line in lines if line])


def _part_lines(part: Mapping[str, Any], *, chart_rows: bool) -> list[str]:
    lines = [str(part.get("title") or ""), str(part.get("body") or "")]
    note = _trunc_line(part.get("truncated"))
    if note:
        lines.append(note)
    for row in part.get("rows") or []:
        if isinstance(row, dict):
            lines.append(_row_line(row))
    for chart in _charts(part):
        label = str(chart.get("label") or "Chart")
        lines.append(label)
        note = _trunc_line(chart.get("truncated"))
        if note:
            lines.append(note)
        if chart_rows:
            for row in chart.get("rows") or []:
                if isinstance(row, dict):
                    lines.append(_row_line(row))
    return [line for line in lines if line]


def _part_images(part: Mapping[str, Any], images: Mapping[int, Bitmap]) -> list[Bitmap]:
    found: list[Bitmap] = []
    for chart in _charts(part):
        drawn = images.get(id(chart))
        if drawn is not None:
            found.append(drawn)
    return found


def _wrap(lines: list[str]) -> list[str]:
    wrapped: list[str] = []
    for line in lines:
        pieces = line.splitlines() or [""]
        for piece in pieces:
            wrapped.extend(textwrap.wrap(piece, width=_WRAP) or [""])
    return wrapped


def _flow(lines: list[str]) -> list[list[str]]:
    wrapped = _wrap(lines) or [""]
    return [
        wrapped[index : index + _LINES_PER_PAGE]
        for index in range(0, len(wrapped), _LINES_PER_PAGE)
    ]


def _fit_page(lines: list[str], images: list[Bitmap], *, fit: bool) -> Page:
    kept = lines or [""]
    if fit:
        font, leading = _fit_metrics(len(kept), len(images))
    else:
        font, leading = 11, 14
    return {"lines": kept, "images": images, "font": font, "leading": leading}


def _fit_metrics(n_lines: int, n_images: int) -> tuple[int, int]:
    usable = 700
    rows = 0
    if n_images:
        cols = 1 if n_images == 1 else 2 if n_images <= 4 else 3
        rows = math.ceil(n_images / cols)
    chart = 0 if rows == 0 else max(28, min(160, int(usable * 0.55 / rows))) * rows
    room = max(1, usable - chart)
    leading = min(14, max(4, room // max(n_lines, 1)))
    while leading * max(n_lines, 1) + chart > usable and chart > 16 * max(rows, 1):
        chart -= max(rows, 1)
    while leading * max(n_lines, 1) > max(1, usable - chart) and leading > 4:
        leading -= 1
    font = min(11, leading) if leading < 8 else min(11, leading - 2)
    return max(font, 4), leading


def _boxes(page: Page) -> list[tuple[float, float, float, float]]:
    images: list[Bitmap] = page["images"]
    count = len(images)
    if count == 0:
        return []
    text_h = int(page["leading"]) * max(len(page["lines"]), 1)
    top = 760 - text_h - 10
    bottom = 42.0
    avail = max(12.0, top - bottom)
    cols = 1 if count == 1 else 2 if count <= 4 else 3
    rows = math.ceil(count / cols)
    gap = 6.0
    cell_w = (612 - 80 - gap * (cols - 1)) / cols
    cell_h = max(8.0, (avail - gap * (rows - 1)) / rows)
    boxes: list[tuple[float, float, float, float]] = []
    for index in range(count):
        row, col = divmod(index, cols)
        x = 40 + col * (cell_w + gap)
        y = bottom + (rows - 1 - row) * (cell_h + gap)
        boxes.append((x, y, cell_w, cell_h))
    return boxes


def _html(
    artifact: Mapping[str, Any],
    images: Mapping[int, Bitmap],
    kit: Mapping[str, str],
) -> str:
    palette = _palette(kit)
    accent = _hex(palette[0])
    ink = _hex(palette[min(2, len(palette) - 1)])
    font = _esc(_font_name(kit))
    logo = _esc(kit.get("logo") or "")
    blocks = [f"<h1>{_esc(str(artifact.get('title') or ''))}</h1>"]
    if logo:
        blocks.append(f'<p class="logo">{logo}</p>')
    for part in _parts(artifact):
        chunks = [
            "<section>",
            f"<h2>{_esc(str(part.get('title') or ''))}</h2>",
            f"<p>{_esc(str(part.get('body') or ''))}</p>",
        ]
        note = _trunc_line(part.get("truncated"))
        if note:
            chunks.append(f"<p>{_esc(note)}</p>")
        part_rows = [row for row in part.get("rows") or [] if isinstance(row, dict)]
        if part_rows:
            chunks.append(_table(part_rows))
        for chart in _charts(part):
            chart_rows = _rows(chart)
            duplicate = bool(part_rows) and chart_rows == part_rows
            chunks.append(
                _chart_html(chart, images.get(id(chart)), table=bool(chart_rows) and not duplicate)
            )
        chunks.append("</section>")
        blocks.append("\n".join(chunks))
    css = (
        f"body{{font-family:{font},Calibri,sans-serif;color:{ink};background:#fff;margin:2rem}}"
        f"h1,h2,figcaption{{color:{accent}}}"
        f".logo{{color:{accent};font-weight:700}}"
        "table{border-collapse:collapse;margin:1rem 0}"
        f"th{{background:{accent};color:#fff;text-align:left;font-weight:600}}"
        "td,th{border:1px solid #e4e0d8;padding:0.4rem 0.6rem}"
        "td.num{text-align:right;font-variant-numeric:tabular-nums}"
        "img{max-width:100%;height:auto}"
        "figure{margin:1rem 0}"
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>{_esc(str(artifact.get('title') or ''))}</title>"
        f"<style>{css}</style></head><body>\n" + "\n".join(blocks) + "\n</body></html>\n"
    )


def _chart_html(chart: Mapping[str, Any], image: Bitmap | None, *, table: bool) -> str:
    label = _esc(str(chart.get("label") or "Chart"))
    note = _trunc_line(chart.get("truncated"))
    note_html = f"<p>{_esc(note)}</p>" if note else ""
    img = ""
    if image is not None:
        encoded = base64.b64encode(image[0]).decode("ascii")
        img = f'<img alt="{label}" src="data:image/png;base64,{encoded}">'
    rows = _rows(chart)
    grid = _table(rows) if table and rows else ""
    return f"<figure><figcaption>{label}</figcaption>{note_html}{img}{grid}</figure>"


def _table(rows: Sequence[Mapping[str, Any]]) -> str:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if str(key) not in keys:
                keys.append(str(key))
    head = "".join(f"<th>{_esc(_human(key))}</th>" for key in keys)
    numeric = {key for key in keys if any(_is_number(row.get(key)) for row in rows)}
    body = []
    for row in rows:
        cells = []
        for key in keys:
            cls = ' class="num"' if key in numeric else ""
            cells.append(f"<td{cls}>{_esc(_fmt(row.get(key, '')))}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf(pages: list[Page], kit: Mapping[str, str]) -> bytes:
    """One page object per layout page. Charts are drawn on that page."""
    colour = _palette(kit)[0]
    font = _embedded_font(kit)
    cursor = 3
    plan: list[tuple[list[int], int, int]] = []
    for page in pages:
        image_ids: list[int] = []
        for _image in page["images"]:
            image_ids.append(cursor)
            cursor += 1
        content_id = cursor
        cursor += 1
        page_id = cursor
        cursor += 1
        plan.append((image_ids, content_id, page_id))
    font_file_id = font_desc_id = None
    if font is not None:
        font_file_id = cursor
        cursor += 1
        font_desc_id = cursor
        cursor += 1
    font_id = cursor
    objects: list[bytes] = [b"", b""]
    kids: list[str] = []
    logo = kit.get("logo") or ""
    for page, (image_ids, content_id, page_id) in zip(pages, plan, strict=True):
        for image in page["images"]:
            objects.append(_image_obj(image[1], image[2], image[3]))
        objects.append(_content(page, colour, logo))
        objects.append(_page_obj(content_id, image_ids, font_id))
        kids.append(f"{page_id} 0 R")
    if font is not None and font_file_id is not None and font_desc_id is not None:
        objects.append(_font_stream(font["data"]))
        objects.append(_font_desc(font, font_file_id))
    objects.append(_font_obj(font, font_desc_id))
    kids_text = " ".join(kids)
    objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids_text}] >>".encode("ascii")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    return _pack(objects)


def _page_obj(content_id: int, image_ids: list[int], font_id: int) -> bytes:
    resources = f"/Font << /F1 {font_id} 0 R >>"
    if image_ids:
        xobjects = " ".join(f"/Im{index} {obj_id} 0 R" for index, obj_id in enumerate(image_ids))
        resources += f" /XObject << {xobjects} >>"
    return (
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        f"/Contents {content_id} 0 R /Resources << {resources} >> >>"
    ).encode("ascii")


def _content(page: Page, colour: tuple[int, int, int], logo: str) -> bytes:
    red, green, blue = (channel / 255 for channel in colour)
    tint = f"{red:.3f} {green:.3f} {blue:.3f} rg"
    size = int(page["font"])
    leading = int(page["leading"])
    lines: list[str] = page["lines"] or [""]
    commands = [
        tint,
        "0 786 612 6 re f",
        "BT",
        f"/F1 {size} Tf",
        "40 760 Td",
        f"({_pdf_text(lines[0])}) Tj",
        "ET",
    ]
    if len(lines) > 1:
        commands.extend(["0 0 0 rg", "BT", f"/F1 {size} Tf", f"40 {760 - leading} Td"])
        first = True
        for line in lines[1:]:
            if not first:
                commands.append(f"0 -{leading} Td")
            first = False
            commands.append(f"({_pdf_text(line)}) Tj")
        commands.append("ET")
    foot = _wrap([_footer(page, logo)]) or [""]
    commands.extend(["0.35 0.35 0.35 rg", "BT", "/F1 9 Tf", "40 26 Td"])
    for index, line in enumerate(foot):
        if index:
            commands.append("0 -11 Td")
        commands.append(f"({_pdf_text(line)}) Tj")
    commands.append("ET")
    for index, (x, y, width, height) in enumerate(_boxes(page)):
        commands.append(f"q {width:.2f} 0 0 {height:.2f} {x:.2f} {y:.2f} cm /Im{index} Do Q")
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    header = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
    return header + stream + b"\nendstream"


def _footer(page: Page, logo: str) -> str:
    mark = f"{page.get('index', 1)} / {page.get('count', 1)}"
    text = f"{logo}  {mark}".strip() if logo else mark
    return text


def _image_obj(rgb: bytes, width: int, height: int) -> bytes:
    raw = zlib.compress(rgb)
    header = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
        f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
        f"/Length {len(raw)} >>\nstream\n"
    ).encode("ascii")
    return header + raw + b"\nendstream"


def _pdf_text(text: str) -> str:
    raw = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return raw.encode("latin-1", errors="replace").decode("latin-1")


def _pack(objects: list[bytes]) -> bytes:
    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_at = sum(len(chunk) for chunk in chunks)
    xref = [f"xref\n0 {len(objects) + 1}\n", "0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n" for offset in offsets[1:])
    xref.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    )
    chunks.append("".join(xref).encode("ascii"))
    return b"".join(chunks)


def _font_stream(data: bytes) -> bytes:
    raw = zlib.compress(data)
    header = (
        f"<< /Length {len(raw)} /Length1 {len(data)} /Filter /FlateDecode >>\nstream\n"
    ).encode("ascii")
    return header + raw + b"\nendstream"


def _font_desc(font: Mapping[str, Any], file_id: int) -> bytes:
    name = font["name"]
    x_min, y_min, x_max, y_max = font["bbox"]
    return (
        f"<< /Type /FontDescriptor /FontName /{name} /Flags 32 "
        f"/FontBBox [{x_min} {y_min} {x_max} {y_max}] /ItalicAngle 0 "
        f"/Ascent {font['ascent']} /Descent {font['descent']} /CapHeight {font['ascent']} "
        f"/StemV 80 /FontFile2 {file_id} 0 R >>"
    ).encode("ascii")


def _font_obj(font: Mapping[str, Any] | None, desc_id: int | None) -> bytes:
    if font is None or desc_id is None:
        return b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    widths = " ".join(str(item) for item in font["widths"])
    return (
        f"<< /Type /Font /Subtype /TrueType /BaseFont /{font['name']} "
        f"/Encoding /WinAnsiEncoding /FirstChar 32 /LastChar 255 /Widths [{widths}] "
        f"/FontDescriptor {desc_id} 0 R >>"
    ).encode("ascii")


def _embedded_font(kit: Mapping[str, str]) -> dict[str, Any] | None:
    path = _font_path(_font_name(kit))
    if path is None:
        return None
    try:
        data = path.read_bytes()
        widths, bbox, ascent, descent = _ttf_widths(data)
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError):
        return None
    return {
        "name": _pdf_font_name(kit),
        "data": data,
        "widths": widths,
        "bbox": bbox,
        "ascent": ascent,
        "descent": descent,
    }


def _font_path(name: str) -> Path | None:
    key = name or "Calibri"
    if key in _FONT_FILES:
        return _FONT_FILES[key]
    path = _match_font(key)
    _FONT_FILES[key] = path
    return path


def _match_font(name: str) -> Path | None:
    try:
        found = subprocess.run(
            ["fc-match", "-f", "%{file}", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        found = None
    if found is not None and found.returncode == 0:
        candidate = Path(found.stdout.strip())
        if candidate.suffix.lower() == ".ttf" and candidate.is_file():
            return candidate
    if _FALLBACK_FONT.is_file():
        return _FALLBACK_FONT
    return None


def _ttf_tables(data: bytes) -> dict[bytes, bytes]:
    count = int.from_bytes(data[4:6], "big")
    tables: dict[bytes, bytes] = {}
    offset = 12
    for _index in range(count):
        tag = data[offset : offset + 4]
        start = int.from_bytes(data[offset + 8 : offset + 12], "big")
        length = int.from_bytes(data[offset + 12 : offset + 16], "big")
        tables[tag] = data[start : start + length]
        offset += 16
    return tables


def _ttf_widths(data: bytes) -> tuple[list[int], tuple[int, int, int, int], int, int]:
    tables = _ttf_tables(data)
    head = tables[b"head"]
    hhea = tables[b"hhea"]
    units = int.from_bytes(head[18:20], "big") or 1000

    def scaled(blob: bytes, at: int) -> int:
        raw = int.from_bytes(blob[at : at + 2], "big")
        if raw >= 32768:
            raw -= 65536
        return int(raw * 1000 / units)

    bbox = (scaled(head, 36), scaled(head, 38), scaled(head, 40), scaled(head, 42))
    ascent = scaled(hhea, 4)
    descent = scaled(hhea, 6)
    metrics = int.from_bytes(hhea[34:36], "big")
    hmtx = tables[b"hmtx"]
    advances = [int.from_bytes(hmtx[index * 4 : index * 4 + 2], "big") for index in range(metrics)]
    cmap = _cmap4(tables[b"cmap"])
    widths: list[int] = []
    for code in range(32, 256):
        glyph = cmap.get(code, 0)
        advance = advances[glyph] if glyph < len(advances) else (advances[-1] if advances else 500)
        widths.append(int(advance * 1000 / units))
    return widths, bbox, ascent, descent


def _cmap4(cmap: bytes) -> dict[int, int]:
    count = int.from_bytes(cmap[2:4], "big")
    chosen: int | None = None
    for index in range(count):
        at = 4 + index * 8
        platform = int.from_bytes(cmap[at : at + 2], "big")
        encoding = int.from_bytes(cmap[at + 2 : at + 4], "big")
        offset = int.from_bytes(cmap[at + 4 : at + 8], "big")
        if platform == 3 and encoding in {1, 10}:
            chosen = offset
            break
        if platform == 0 and chosen is None:
            chosen = offset
    if chosen is None or int.from_bytes(cmap[chosen : chosen + 2], "big") != 4:
        return {}
    sub = cmap[chosen:]
    seg_count = int.from_bytes(sub[6:8], "big") // 2
    end_at = 14
    ends = [
        int.from_bytes(sub[end_at + 2 * i : end_at + 2 * i + 2], "big") for i in range(seg_count)
    ]
    start_at = end_at + 2 * seg_count + 2
    starts = [
        int.from_bytes(sub[start_at + 2 * i : start_at + 2 * i + 2], "big")
        for i in range(seg_count)
    ]
    delta_at = start_at + 2 * seg_count
    deltas = []
    for index in range(seg_count):
        raw = int.from_bytes(sub[delta_at + 2 * index : delta_at + 2 * index + 2], "big")
        deltas.append(raw - 65536 if raw >= 32768 else raw)
    range_at = delta_at + 2 * seg_count
    ranges = [
        int.from_bytes(sub[range_at + 2 * i : range_at + 2 * i + 2], "big")
        for i in range(seg_count)
    ]
    found: dict[int, int] = {}
    for code in range(32, 256):
        for index, end in enumerate(ends):
            if code > end or code < starts[index]:
                continue
            if ranges[index] == 0:
                found[code] = (code + deltas[index]) & 0xFFFF
            else:
                pos = range_at + 2 * index + ranges[index] + 2 * (code - starts[index])
                glyph = int.from_bytes(sub[pos : pos + 2], "big")
                found[code] = (glyph + deltas[index]) & 0xFFFF if glyph else 0
            break
    return found


def _page_png(page: Page, colour: tuple[int, int, int]) -> bytes:
    width, height = 612, 792
    canvas = bytearray(b"\xff" * width * height * 3)
    for y in range(6):
        for x in range(width):
            at = (y * width + x) * 3
            canvas[at : at + 3] = bytes(colour)
    for box, image in zip(_boxes(page), page["images"], strict=True):
        _blit(canvas, width, height, image[1], image[2], image[3], box)
    return _png(bytes(canvas), width, height)


def _blit(
    canvas: bytearray,
    width: int,
    height: int,
    rgb: bytes,
    src_w: int,
    src_h: int,
    box: tuple[float, float, float, float],
) -> None:
    x, y, box_w, box_h = box
    dest_w = max(1, int(box_w))
    dest_h = max(1, int(box_h))
    left = max(0, int(x))
    top = max(0, height - int(y) - dest_h)
    for row in range(dest_h):
        dest_y = top + row
        if dest_y >= height:
            break
        src_y = min(src_h - 1, row * src_h // dest_h)
        for col in range(dest_w):
            dest_x = left + col
            if dest_x >= width:
                break
            src_x = min(src_w - 1, col * src_w // dest_w)
            src_at = (src_y * src_w + src_x) * 3
            dest_at = (dest_y * width + dest_x) * 3
            canvas[dest_at : dest_at + 3] = rgb[src_at : src_at + 3]


def _png(rgb: bytes, width: int, height: int) -> bytes:
    raw = b"".join(b"\x00" + rgb[row * width * 3 : (row + 1) * width * 3] for row in range(height))
    chunk = zlib.compress(raw)

    def block(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + block(b"IHDR", ihdr) + block(b"IDAT", chunk) + block(b"IEND", b"")


def _pptx(artifact: Mapping[str, Any], kit: Mapping[str, str]) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.oxml import parse_xml
    from pptx.util import Emu, Inches, Pt

    palette = _palette(kit)
    font = _font_name(kit)
    colour = RGBColor(*palette[0])
    logo = kit.get("logo") or ""
    deck = Presentation()
    blank = deck.slide_layouts[6]
    parts = _parts(artifact)
    total = max(len(parts), 1)
    if not parts:
        parts = [{"title": artifact.get("title") or "", "body": "", "charts": []}]
    for index, part in enumerate(parts, start=1):
        slide = deck.slides.add_slide(blank)
        _fade(slide, parse_xml)
        title = str(part.get("title") or artifact.get("title") or "")
        if logo:
            title = f"{logo}   {title}".strip()
        box = slide.shapes.add_textbox(Inches(0.4), Inches(0.16), Inches(9.2), Inches(0.42))
        box.text_frame.text = title
        _face(box.text_frame, font, 22, bold=True, colour=colour)
        body = str(part.get("body") or "")
        if body:
            text = slide.shapes.add_textbox(Inches(0.4), Inches(0.58), Inches(12.4), Inches(0.7))
            text.text_frame.word_wrap = True
            text.text_frame.text = body
            _face(text.text_frame, font, 16 if len(body) < 180 else 12, bold=False, colour=None)
        note = _trunc_line(part.get("truncated"))
        if note:
            flag = slide.shapes.add_textbox(Inches(0.4), Inches(1.22), Inches(12.4), Inches(0.28))
            flag.text_frame.text = note
            _face(flag.text_frame, font, 11, bold=False, colour=None)
        charts = _charts(part)
        if charts:
            _place_charts(slide, charts, deck, palette, font, Emu, Inches, Pt, has_body=bool(body))
        foot = slide.shapes.add_textbox(Inches(0.4), Inches(7.12), Inches(12.4), Inches(0.3))
        foot.text_frame.text = f"{logo}  {index} / {total}".strip()
        _face(foot.text_frame, font, 11, bold=False, colour=colour)
    buf = io.BytesIO()
    deck.save(buf)
    return buf.getvalue()


def _fade(slide: Any, parse_xml: Any) -> None:
    # ponytail: one fade on the slide. A per-shape build is a timing tree.
    xml = (
        '<p:transition xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'spd="med"><p:fade/></p:transition>'
    )
    slide._element.append(parse_xml(xml))


def _place_charts(
    slide: Any,
    charts: list[Mapping[str, Any]],
    deck: Any,
    palette: list[tuple[int, int, int]],
    font: str,
    emu: Any,
    inches: Any,
    pt: Any,
    *,
    has_body: bool,
) -> None:
    del pt
    count = len(charts)
    cols = 1 if count == 1 else 2 if count <= 4 else 3
    rows = math.ceil(count / cols)
    left0 = int(inches(0.4))
    top0 = int(inches(1.35 if has_body else 0.7))
    width = int(deck.slide_width) - int(inches(0.8))
    bottom = int(deck.slide_height) - int(inches(0.48))
    height = max(int(inches(1.2)), bottom - top0)
    gap = int(inches(0.12))
    cell_w = max(int(inches(1.2)), (width - gap * (cols - 1)) // cols)
    cell_h = max(int(inches(1.0)), (height - gap * (rows - 1)) // rows)
    for index, chart in enumerate(charts):
        row, col = divmod(index, cols)
        left = left0 + col * (cell_w + gap)
        top = top0 + row * (cell_h + gap)
        _add_chart(slide, chart, left, cell_w, top, cell_h, palette, font, emu)


def _face(frame: Any, font: str, size: int, *, bold: bool, colour: Any) -> None:
    from pptx.util import Pt

    for paragraph in frame.paragraphs:
        paragraph.font.name = font
        paragraph.font.size = Pt(size)
        paragraph.font.bold = bold
        if colour is not None:
            paragraph.font.color.rgb = colour


def _add_chart(
    slide: Any,
    chart: Mapping[str, Any],
    left: int,
    width: int,
    top: int,
    height: int,
    palette: list[tuple[int, int, int]],
    font: str,
    emu: Any,
) -> None:
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Pt

    frame = _frame(chart)
    if frame is None:
        box = slide.shapes.add_textbox(emu(left), emu(top), emu(width), emu(height))
        box.text_frame.text = str(chart.get("label") or "")
        _face(box.text_frame, font, 12, bold=False, colour=None)
        return
    mark, cat_name, categories, series, show = frame
    data = CategoryChartData()
    data.categories = categories
    for name, values in series:
        data.add_series(name, values, number_format="#,##0")
    kind = {
        "line": XL_CHART_TYPE.LINE,
        "pie": XL_CHART_TYPE.PIE,
    }.get(mark, XL_CHART_TYPE.COLUMN_CLUSTERED)
    graphic = slide.shapes.add_chart(kind, emu(left), emu(top), emu(width), emu(height), data)
    drawn = graphic.chart
    label = str(chart.get("label") or "Chart")
    drawn.has_title = True
    drawn.chart_title.text_frame.text = label
    _face(drawn.chart_title.text_frame, font, 12, bold=True, colour=RGBColor(*palette[0]))
    if mark != "pie":
        drawn.category_axis.has_title = True
        drawn.category_axis.axis_title.text_frame.text = cat_name
        _face(drawn.category_axis.axis_title.text_frame, font, 10, bold=False, colour=None)
        drawn.value_axis.has_title = True
        drawn.value_axis.axis_title.text_frame.text = series[0][0] if len(series) == 1 else "Value"
        _face(drawn.value_axis.axis_title.text_frame, font, 10, bold=False, colour=None)
        drawn.value_axis.tick_labels.number_format = "#,##0"
    if show:
        plot = drawn.plots[0]
        plot.has_data_labels = True
        plot.data_labels.number_format = "#,##0"
        plot.data_labels.font.size = Pt(8)
        plot.data_labels.font.name = font
    for index, item in enumerate(drawn.series):
        colour = RGBColor(*palette[index % len(palette)])
        if mark == "line":
            item.format.line.color.rgb = colour
        else:
            _paint(item.format, colour)
            for point in item.points:
                _paint(point.format, colour)


def _paint(fmt: Any, colour: Any) -> None:
    fmt.fill.solid()
    fmt.fill.fore_color.rgb = colour
