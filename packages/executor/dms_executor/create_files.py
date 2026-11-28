"""Write the confirmed artifact to pptx, pdf, and html.

The provider chose the words. This only lays them out.
"""

from __future__ import annotations

import base64
import io
import json
import re
import textwrap
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# ponytail: 45 wrapped lines per page, then another page. A larger budget is a
# layout change. It is not a reason to drop lines.
_LINES_PER_PAGE = 45
_CHARTS_PER_SLIDE = 5
_WRAP = 90
_HEX = re.compile(r"#([0-9A-Fa-f]{6})")
_DEFAULT_PALETTE = (
    (27, 77, 62),
    (196, 163, 90),
    (47, 79, 79),
    (176, 96, 80),
    (90, 122, 140),
    (120, 90, 140),
)


def one_page_requested(text: str) -> bool:
    folded = text.casefold().replace("-", " ")
    return "one page" in folded or "single page" in folded


def render_artifact(
    folder: Path,
    artifact: Mapping[str, Any],
    *,
    kind: str,
    one_page: bool,
    brand: Mapping[str, Any] | None = None,
) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "artifact.json").write_text(
        json.dumps(artifact, indent=2, default=str), encoding="utf-8"
    )
    names = ["artifact.json"]
    kit = _kit(brand, artifact)
    images = _images(artifact, kit)
    single = one_page and kind == "doc"
    html_name = f"{kind or 'artifact'}.html"
    (folder / html_name).write_text(_html(artifact, images, kit, one_page=single), encoding="utf-8")
    names.append(html_name)
    pdf_name = f"{kind or 'artifact'}.pdf"
    (folder / pdf_name).write_bytes(_pdf(_pages(artifact, images, one_page=single)))
    names.append(pdf_name)
    if kind == "slides":
        (folder / "slides.pptx").write_bytes(_pptx(artifact, kit))
        names.append("slides.pptx")
    return names


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
    return " | ".join(f"{key} {_fmt(value)}" for key, value in row.items())


def _parts(artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [part for part in artifact.get("parts") or [] if isinstance(part, Mapping)]


def _charts(part: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [chart for chart in part.get("charts") or [] if isinstance(chart, Mapping)]


def _images(
    artifact: Mapping[str, Any], kit: Mapping[str, str]
) -> dict[int, tuple[bytes, bytes, int, int]]:
    found: dict[int, tuple[bytes, bytes, int, int]] = {}
    for part in _parts(artifact):
        for chart in _charts(part):
            drawn = _chart_bitmap(chart, _palette(kit))
            if drawn is not None:
                found[id(chart)] = drawn
    return found


def _frame(
    chart: Mapping[str, Any],
) -> tuple[str, list[str], list[tuple[str, list[float]]]] | None:
    rows = [row for row in chart.get("rows") or [] if isinstance(row, dict) and row]
    if not rows:
        return None
    keys: list[str] = []
    for row in rows:
        for key in row:
            if str(key) not in keys:
                keys.append(str(key))

    def numeric(key: str) -> bool:
        seen = False
        for row in rows:
            value = row.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            seen = True
        return seen

    measures = [key for key in keys if numeric(key)]
    labels = [key for key in keys if key not in measures]
    if not measures:
        return None
    cat_key = labels[0] if labels else ""
    categories: list[str] = []
    for index, row in enumerate(rows, start=1):
        raw = row.get(cat_key) if cat_key else index
        categories.append(str(raw) if raw not in (None, "") else str(index))
    series: list[tuple[str, list[float]]] = []
    for key in measures:
        values: list[float] = []
        for row in rows:
            raw = row.get(key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                values.append(float(raw))
            else:
                values.append(0.0)
        series.append((key, values))
    return cat_key or "Category", categories, series


def _chart_bitmap(
    chart: Mapping[str, Any], palette: list[tuple[int, int, int]]
) -> tuple[bytes, bytes, int, int] | None:
    frame = _frame(chart)
    if frame is None:
        return None
    cat_name, categories, series = frame
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=80)
    count = len(series)
    width = 0.8 / count
    slots = list(range(len(categories)))
    for index, (name, values) in enumerate(series):
        offset = (index - (count - 1) / 2) * width
        colour = _hex(palette[index % len(palette)])
        ax.bar([slot + offset for slot in slots], values, width=width, label=name, color=colour)
    ax.set_xticks(slots, categories, rotation=30 if len(categories) > 6 else 0)
    ax.set_xlabel(cat_name)
    ax.set_ylabel("Value")
    ax.set_title(str(chart.get("label") or "Chart"))
    if count > 1:
        ax.legend()
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


def _pages(
    artifact: Mapping[str, Any],
    images: Mapping[int, tuple[bytes, bytes, int, int]],
    *,
    one_page: bool,
) -> list[tuple[list[str], tuple[bytes, int, int] | None]]:
    """Text pages, then one page per chart image. Nothing is sliced away."""
    title = str(artifact.get("title") or "")
    if one_page:
        lines = [title] if title else []
        for part in _parts(artifact):
            lines.extend(_part_lines(part))
        pages = _flow(lines or [title or "Empty"])
    else:
        pages = _flow([title]) if title else []
        for part in _parts(artifact):
            pages.extend(_flow(_part_lines(part)))
        if not pages:
            pages = [[title or "Empty"]]
    out: list[tuple[list[str], tuple[bytes, int, int] | None]] = [(lines, None) for lines in pages]
    for part in _parts(artifact):
        for chart in _charts(part):
            drawn = images.get(id(chart))
            if drawn is None:
                continue
            _png, rgb, width, height = drawn
            label = str(chart.get("label") or "Chart")
            out.append(([label], (rgb, width, height)))
    return out


def _part_lines(part: Mapping[str, Any]) -> list[str]:
    lines = [str(part.get("title") or ""), str(part.get("body") or "")]
    note = _trunc_line(part.get("truncated"))
    if note:
        lines.append(note)
    for row in part.get("rows") or []:
        if isinstance(row, dict):
            lines.append(_row_line(row))
    for chart in _charts(part):
        lines.append(str(chart.get("label") or "Chart"))
        note = _trunc_line(chart.get("truncated"))
        if note:
            lines.append(note)
        for row in chart.get("rows") or []:
            if isinstance(row, dict):
                lines.append(_row_line(row))
    return [line for line in lines if line]


def _flow(lines: list[str]) -> list[list[str]]:
    wrapped: list[str] = []
    for line in lines:
        pieces = line.splitlines() or [""]
        for piece in pieces:
            wrapped.extend(textwrap.wrap(piece, width=_WRAP) or [""])
    if not wrapped:
        wrapped = [""]
    return [
        wrapped[index : index + _LINES_PER_PAGE]
        for index in range(0, len(wrapped), _LINES_PER_PAGE)
    ]


def _html(
    artifact: Mapping[str, Any],
    images: Mapping[int, tuple[bytes, bytes, int, int]],
    kit: Mapping[str, str],
    *,
    one_page: bool,
) -> str:
    palette = _palette(kit)
    accent = _hex(palette[0])
    font = _esc(_font_name(kit))
    blocks = [f"<h1>{_esc(str(artifact.get('title') or ''))}</h1>"]
    for part in _parts(artifact):
        style = "" if one_page else ' style="page-break-before: always"'
        chunks = [
            f"<section{style}>",
            f"<h2>{_esc(str(part.get('title') or ''))}</h2>",
            f"<p>{_esc(str(part.get('body') or ''))}</p>",
        ]
        note = _trunc_line(part.get("truncated"))
        if note:
            chunks.append(f"<p>{_esc(note)}</p>")
        rows = [row for row in part.get("rows") or [] if isinstance(row, dict)]
        if rows:
            chunks.append(_table(rows))
        for chart in _charts(part):
            chunks.append(_chart_html(chart, images.get(id(chart))))
        chunks.append("</section>")
        blocks.append("\n".join(chunks))
    css = (
        f"body{{font-family:{font},sans-serif;color:#1a1a1a;margin:2rem}}"
        f"h1,h2{{color:{accent}}}"
        "table{border-collapse:collapse;margin:1rem 0}"
        f"th{{background:{accent};color:#fff;text-align:left}}"
        "td,th{border:1px solid #ddd;padding:0.4rem 0.6rem}"
        "img{max-width:100%;height:auto}"
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>{_esc(str(artifact.get('title') or ''))}</title>"
        f"<style>{css}</style></head><body>\n" + "\n".join(blocks) + "\n</body></html>\n"
    )


def _chart_html(chart: Mapping[str, Any], image: tuple[bytes, bytes, int, int] | None) -> str:
    label = _esc(str(chart.get("label") or "Chart"))
    note = _trunc_line(chart.get("truncated"))
    note_html = f"<p>{_esc(note)}</p>" if note else ""
    img = ""
    if image is not None:
        encoded = base64.b64encode(image[0]).decode("ascii")
        img = f'<img alt="{label}" src="data:image/png;base64,{encoded}">'
    rows = [row for row in chart.get("rows") or [] if isinstance(row, dict)]
    table = _table(rows) if rows else ""
    return f"<figure><figcaption>{label}</figcaption>{note_html}{img}{table}</figure>"


def _table(rows: Sequence[Mapping[str, Any]]) -> str:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if str(key) not in keys:
                keys.append(str(key))
    head = "".join(f"<th>{_esc(key)}</th>" for key in keys)
    body = []
    for row in rows:
        cells = "".join(f"<td>{_esc(_fmt(row.get(key, '')))}</td>" for key in keys)
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf(
    pages: list[tuple[list[str], tuple[bytes, int, int] | None]],
) -> bytes:
    """One page object per entry. A short one-page doc stays a single page."""
    cursor = 3
    plan: list[tuple[int | None, int, int]] = []
    for _lines, image in pages:
        image_id = None
        if image is not None:
            image_id = cursor
            cursor += 1
        content_id = cursor
        cursor += 1
        page_id = cursor
        cursor += 1
        plan.append((image_id, content_id, page_id))
    font_id = cursor
    objects: list[bytes] = [b"", b""]
    kids: list[str] = []
    for (lines, image), (image_id, content_id, page_id) in zip(pages, plan, strict=True):
        if image is not None:
            objects.append(_image_obj(*image))
        objects.append(_content(lines, image is not None))
        objects.append(_page_obj(content_id, image_id, font_id))
        kids.append(f"{page_id} 0 R")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids_text = " ".join(kids)
    objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids_text}] >>".encode("ascii")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    return _pack(objects)


def _page_obj(content_id: int, image_id: int | None, font_id: int) -> bytes:
    resources = f"/Font << /F1 {font_id} 0 R >>"
    if image_id is not None:
        resources += f" /XObject << /Im0 {image_id} 0 R >>"
    return (
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        f"/Contents {content_id} 0 R /Resources << {resources} >> >>"
    ).encode("ascii")


def _content(lines: list[str], image: bool) -> bytes:
    commands = ["BT", "/F1 11 Tf", "72 760 Td"]
    first = True
    for line in lines:
        if not first:
            commands.append("0 -14 Td")
        first = False
        commands.append(f"({_pdf_text(line)}) Tj")
    commands.append("ET")
    if image:
        commands.extend(["q", "468 0 0 280 72 400 cm", "/Im0 Do", "Q"])
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    header = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
    return header + stream + b"\nendstream"


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


def _pptx(artifact: Mapping[str, Any], kit: Mapping[str, str]) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    palette = _palette(kit)
    font = _font_name(kit)
    deck = Presentation()
    blank = deck.slide_layouts[6]
    for part in _parts(artifact):
        chunks = _slide_chunks(_charts(part))
        for index, charts in enumerate(chunks):
            slide = deck.slides.add_slide(blank)
            title = str(part.get("title") or artifact.get("title") or "")
            if index:
                title = f"{title} ({index + 1})"
            box = slide.shapes.add_textbox(Inches(0.4), Inches(0.2), Inches(9.2), Inches(0.5))
            box.text_frame.text = title
            _face(box.text_frame, font, 22, bold=True)
            if index == 0:
                body = slide.shapes.add_textbox(Inches(0.4), Inches(0.7), Inches(9.2), Inches(0.8))
                body.text_frame.word_wrap = True
                body.text_frame.text = str(part.get("body") or "")
                _face(body.text_frame, font, 14, bold=False)
            note = _trunc_line(part.get("truncated"))
            if note and index == 0:
                flag = slide.shapes.add_textbox(Inches(0.4), Inches(1.45), Inches(9.2), Inches(0.3))
                flag.text_frame.text = note
                _face(flag.text_frame, font, 12, bold=False)
            if charts:
                width = int(Inches(9.2) / len(charts))
                top = Inches(1.8)
                for slot, chart in enumerate(charts):
                    left = int(Inches(0.4)) + (width * slot)
                    _add_chart(slide, chart, left, width, top, palette, font)
    buf = io.BytesIO()
    deck.save(buf)
    return buf.getvalue()


def _slide_chunks(charts: list[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    if not charts:
        return [[]]
    return [
        charts[index : index + _CHARTS_PER_SLIDE]
        for index in range(0, len(charts), _CHARTS_PER_SLIDE)
    ]


def _face(frame: Any, font: str, size: int, *, bold: bool) -> None:
    from pptx.util import Pt

    for paragraph in frame.paragraphs:
        paragraph.font.name = font
        paragraph.font.size = Pt(size)
        paragraph.font.bold = bold


def _add_chart(
    slide: Any,
    chart: Mapping[str, Any],
    left: int,
    width: int,
    top: Any,
    palette: list[tuple[int, int, int]],
    font: str,
) -> None:
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Emu, Inches

    frame = _frame(chart)
    if frame is None:
        box = slide.shapes.add_textbox(Emu(left), top, Emu(width), Inches(1.0))
        box.text_frame.text = str(chart.get("label") or "")
        _face(box.text_frame, font, 12, bold=False)
        return
    cat_name, categories, series = frame
    data = CategoryChartData()
    data.categories = categories
    for name, values in series:
        data.add_series(name, values)
    graphic = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Emu(left),
        top,
        Emu(max(width - int(Inches(0.15)), int(Inches(1.2)))),
        Inches(4.4),
        data,
    )
    drawn = graphic.chart
    label = str(chart.get("label") or "Chart")
    drawn.has_title = True
    drawn.chart_title.text_frame.text = label
    _face(drawn.chart_title.text_frame, font, 12, bold=True)
    drawn.category_axis.has_title = True
    drawn.category_axis.axis_title.text_frame.text = cat_name
    _face(drawn.category_axis.axis_title.text_frame, font, 10, bold=False)
    drawn.value_axis.has_title = True
    drawn.value_axis.axis_title.text_frame.text = series[0][0] if len(series) == 1 else "Value"
    _face(drawn.value_axis.axis_title.text_frame, font, 10, bold=False)
    for index, item in enumerate(drawn.series):
        colour = RGBColor(*palette[index % len(palette)])
        _paint(item.format, colour)
        for point in item.points:
            _paint(point.format, colour)


def _paint(fmt: Any, colour: Any) -> None:
    fmt.fill.solid()
    fmt.fill.fore_color.rgb = colour
