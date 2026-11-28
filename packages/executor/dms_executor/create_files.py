"""Write the confirmed artifact to pptx, pdf, and html.

The provider chose the words. This only lays them out.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def one_page_requested(text: str) -> bool:
    folded = text.casefold().replace("-", " ")
    return "one page" in folded or "single page" in folded


def render_artifact(
    folder: Path,
    artifact: Mapping[str, Any],
    *,
    kind: str,
    one_page: bool,
) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "artifact.json").write_text(
        json.dumps(artifact, indent=2, default=str), encoding="utf-8"
    )
    names = ["artifact.json"]
    pages = _pages(artifact, one_page=one_page and kind == "doc")
    html_name = f"{kind or 'artifact'}.html"
    (folder / html_name).write_text(
        _html(artifact, one_page=one_page and kind == "doc"), encoding="utf-8"
    )
    names.append(html_name)
    pdf_name = f"{kind or 'artifact'}.pdf"
    (folder / pdf_name).write_bytes(_pdf(pages))
    names.append(pdf_name)
    if kind == "slides":
        (folder / "slides.pptx").write_bytes(_pptx(artifact))
        names.append("slides.pptx")
    return names


def _pages(artifact: Mapping[str, Any], *, one_page: bool) -> list[list[str]]:
    pages: list[list[str]] = []
    title = str(artifact.get("title") or "")
    if one_page:
        lines = [title] if title else []
        for part in _parts(artifact):
            lines.extend(_part_lines(part))
        return [lines or [title or "Empty"]]
    if title:
        pages.append([title])
    for part in _parts(artifact):
        pages.append(_part_lines(part))
    return pages or [[title or "Empty"]]


def _part_lines(part: Mapping[str, Any]) -> list[str]:
    lines = [str(part.get("title") or ""), str(part.get("body") or "")]
    for chart in part.get("charts") or []:
        if not isinstance(chart, dict):
            continue
        lines.append(str(chart.get("label") or "Chart"))
        for row in chart.get("rows") or []:
            if isinstance(row, dict):
                lines.append(", ".join(f"{key}={value}" for key, value in row.items()))
    return [line for line in lines if line]


def _html(artifact: Mapping[str, Any], *, one_page: bool) -> str:
    blocks = [f"<h1>{_esc(str(artifact.get('title') or ''))}</h1>"]
    for part in _parts(artifact):
        style = "" if one_page else ' style="page-break-before: always"'
        chunks = [
            f"<section{style}>",
            f"<h2>{_esc(str(part.get('title') or ''))}</h2>",
            f"<p>{_esc(str(part.get('body') or ''))}</p>",
        ]
        for chart in part.get("charts") or []:
            if isinstance(chart, dict):
                chunks.append(_chart_html(chart))
        chunks.append("</section>")
        blocks.append("\n".join(chunks))
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{_esc(str(artifact.get('title') or ''))}</title></head><body>\n"
        + "\n".join(blocks)
        + "\n</body></html>\n"
    )


def _chart_html(chart: Mapping[str, Any]) -> str:
    label = _esc(str(chart.get("label") or "Chart"))
    rows = [row for row in chart.get("rows") or [] if isinstance(row, dict)]
    if not rows:
        return f"<figure><figcaption>{label}</figcaption></figure>"
    keys = list(rows[0].keys())
    head = "".join(f"<th>{_esc(str(key))}</th>" for key in keys)
    body = []
    for row in rows:
        cells = "".join(f"<td>{_esc(str(row.get(key, '')))}</td>" for key in keys)
        body.append(f"<tr>{cells}</tr>")
    return (
        f"<figure><figcaption>{label}</figcaption>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></figure>"
    )


def _parts(artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [part for part in artifact.get("parts") or [] if isinstance(part, Mapping)]


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf(pages: list[list[str]]) -> bytes:
    """One PDF page object per entry. A one-page doc is a list of length 1."""
    font_id = 3 + (2 * len(pages))
    objects: list[bytes] = []
    kids: list[str] = []
    for index, lines in enumerate(pages):
        page_id = 3 + (2 * index)
        content_id = page_id + 1
        kids.append(f"{page_id} 0 R")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode("ascii")
        )
        objects.append(_content(lines))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids_text = " ".join(kids)
    page_tree = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids_text}] >>".encode("ascii")
    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    ordered = [catalog, page_tree, *objects]
    return _pack(ordered)


def _content(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 11 Tf", "72 760 Td"]
    first = True
    for line in lines[:50]:
        if not first:
            commands.append("0 -14 Td")
        first = False
        commands.append(f"({_pdf_text(line)}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    header = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
    return header + stream + b"\nendstream"


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


def _pptx(artifact: Mapping[str, Any]) -> bytes:
    import io

    from pptx import Presentation
    from pptx.util import Emu, Inches, Pt

    deck = Presentation()
    blank = deck.slide_layouts[6]
    for part in _parts(artifact):
        slide = deck.slides.add_slide(blank)
        box = slide.shapes.add_textbox(Inches(0.4), Inches(0.2), Inches(9.2), Inches(0.5))
        box.text_frame.text = str(part.get("title") or artifact.get("title") or "")
        box.text_frame.paragraphs[0].font.size = Pt(22)
        box.text_frame.paragraphs[0].font.bold = True
        body = slide.shapes.add_textbox(Inches(0.4), Inches(0.7), Inches(9.2), Inches(0.8))
        body.text_frame.word_wrap = True
        body.text_frame.text = str(part.get("body") or "")
        charts = [chart for chart in part.get("charts") or [] if isinstance(chart, dict)][:5]
        if charts:
            width = int(Inches(9.2) / len(charts))
            for index, chart in enumerate(charts):
                _add_chart(slide, chart, Emu(int(Inches(0.4)) + (width * index)), width)
    buf = io.BytesIO()
    deck.save(buf)
    return buf.getvalue()


def _add_chart(slide: Any, chart: Mapping[str, Any], left: Any, width: int) -> None:
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Emu, Inches

    categories, values = _series(chart)
    if not categories:
        box = slide.shapes.add_textbox(left, Inches(1.7), Emu(width), Inches(1.0))
        box.text_frame.text = str(chart.get("label") or "")
        return
    data = CategoryChartData()
    data.categories = categories
    data.add_series(str(chart.get("label") or "Value"), values)
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        left,
        Inches(1.7),
        Emu(width),
        Inches(4.5),
        data,
    )


def _series(chart: Mapping[str, Any]) -> tuple[list[str], list[float]]:
    rows = [row for row in chart.get("rows") or [] if isinstance(row, dict)]
    if not rows:
        return [], []
    text_key = ""
    number_key = ""
    for key in rows[0]:
        sample = rows[0][key]
        if isinstance(sample, (int, float)) and not isinstance(sample, bool) and not number_key:
            number_key = str(key)
        elif isinstance(sample, str) and not text_key:
            text_key = str(key)
    if not number_key:
        return [], []
    categories: list[str] = []
    values: list[float] = []
    for index, row in enumerate(rows, start=1):
        categories.append(str(row.get(text_key) or index) if text_key else str(index))
        raw = row.get(number_key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            values.append(float(raw))
        else:
            values.append(0.0)
    return categories, values
