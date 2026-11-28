"""Place a Create artifact on the pitch blocks.

The words stay the ones the model already returned. This file only chooses
a block and copies those strings. ``layout_blocks`` in ``spec`` is the schema
``create_context.layout_blocks`` should return once DMS-CREATE-CONTEXT-01 lands.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dms_core.create_style.chart_registry import known
from dms_core.create_style.errors import CreateError
from dms_core.create_style.render import render_document
from dms_core.create_style.spec import BLOCK_TYPES
from dms_core.create_style.theme import Theme, load_theme

_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def theme_for_brand(brand: Mapping[str, Any] | None) -> Theme:
    """Pitch file, or a named theme, then the brand keys Create already stores."""
    named = _named_theme(brand)
    if not brand:
        return load_theme(path=named)
    override: dict[str, Any] = {}
    colours = brand.get("colours")
    if isinstance(colours, str):
        found = _HEX.findall(colours)
        if found:
            override["colours"] = {"accent": found[0]}
    elif isinstance(colours, dict):
        override["colours"] = colours
    fonts = brand.get("fonts")
    if isinstance(fonts, str) and fonts.strip():
        name = fonts.split(",")[0].strip()
        override["fonts"] = {"display": name, "body": name}
    logo = brand.get("logo_b64")
    if isinstance(logo, str) and logo.strip():
        override["logo_b64"] = logo.strip()
    alpha = brand.get("card_alpha")
    if isinstance(alpha, (int, float)) and not isinstance(alpha, bool):
        override["card_alpha"] = alpha
    if not override:
        return load_theme(path=named)
    return load_theme(override, path=named)


def _named_theme(brand: Mapping[str, Any] | None) -> Path | None:
    if not brand:
        return None
    name = brand.get("theme")
    if not isinstance(name, str) or not name.strip():
        return None
    path = Path(__file__).resolve().parent / "themes" / f"{name.strip()}.json"
    return path if path.is_file() else None


def document_from_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Explicit ``blocks`` win. Otherwise each part is placed, not rewritten."""
    title = artifact.get("title") or ""
    if not isinstance(title, str):
        raise CreateError("bad_title")
    blocks = artifact.get("blocks")
    if isinstance(blocks, list) and blocks:
        copied = [dict(item) for item in blocks if isinstance(item, Mapping)]
        return {"title": title, "blocks": copied}
    built: list[dict[str, Any]] = []
    for part in artifact.get("parts") or []:
        if isinstance(part, Mapping):
            built.extend(_from_part(part))
    if not built:
        raise CreateError("blocks_required")
    return {"title": title, "blocks": built}


def styled_names(
    folder: Path,
    artifact: Mapping[str, Any],
    *,
    kind: str,
    brand: Mapping[str, Any] | None,
) -> list[str]:
    """docx, pptx, xlsx, and HTML. The legacy pdf stays off this path."""
    theme = theme_for_brand(brand)
    document = document_from_artifact(artifact)
    stem = kind or "artifact"
    names = {
        "html": f"{stem}.html",
        "docx": f"{stem}.docx",
        "pptx": "slides.pptx" if kind == "slides" else f"{stem}.pptx",
        "xlsx": f"{stem}.xlsx",
    }
    written = []
    for fmt, name in names.items():
        data, _media, _filename = render_document(document, theme, fmt)
        (folder / name).write_bytes(data)
        written.append(name)
    return written


def _from_part(part: Mapping[str, Any]) -> list[dict[str, Any]]:
    kind = part.get("type")
    if kind in BLOCK_TYPES:
        return [dict(part)]
    blocks: list[dict[str, Any]] = []
    title = _string(part.get("title"))
    body = _string(part.get("body"))
    image = part.get("image_b64")
    caption = _string(part.get("caption")) or body
    if isinstance(image, str) and image and caption:
        place = "image_below" if part.get("image_place") == "below" else "image_side"
        block: dict[str, Any] = {"type": place, "image_b64": image, "caption": caption}
        label = _string(part.get("label")) or title
        if label and label != caption:
            block["label"] = label
        blocks.append(block)
        if caption == body:
            body = ""
    items = part.get("items")
    if isinstance(items, list) and items:
        blocks.append({"type": "kpi_strip", "items": items})
    charts: list[dict[str, Any]] = []
    for item in part.get("charts") or []:
        if isinstance(item, Mapping):
            drawn = _chart(item, title)
            if drawn is not None:
                charts.append(drawn)
    if len(charts) == 4 and not body:
        blocks.append({"type": "chart_page", "charts": charts})
        charts = []
    elif charts:
        commentary = body or _string(charts[0].get("title"))
        if commentary:
            blocks.append(
                {"type": "chart_commentary", "chart": charts[0], "commentary": commentary}
            )
            if commentary == body:
                body = ""
            charts = charts[1:]
        while len(charts) >= 4:
            blocks.append({"type": "chart_page", "charts": charts[:4]})
            charts = charts[4:]
        for chart in charts:
            text = _string(chart.get("title"))
            if text:
                blocks.append({"type": "chart_commentary", "chart": chart, "commentary": text})
    rows = [row for row in part.get("rows") or [] if isinstance(row, Mapping)]
    if rows:
        table = _table(rows)
        if table is not None:
            blocks.append(table)
    prose = _prose(title, body)
    if prose is not None:
        blocks.append(prose)
    return blocks


def _prose(title: str, body: str) -> dict[str, Any] | None:
    raw = body.replace("\r\n", "\n").split("\n\n")
    paragraphs = [piece.strip() for piece in raw if piece.strip()]
    if not paragraphs:
        return None
    if len(paragraphs) == 1:
        pieces = _two_pieces(paragraphs[0])
        if pieces is None and title and title != paragraphs[0]:
            pieces = (paragraphs[0], title)
        if pieces is None:
            return None
        left, right = pieces
    else:
        left, right = paragraphs[0], paragraphs[1]
    block: dict[str, Any] = {"type": "two_column", "left": left, "right": right}
    if title and title not in (left, right):
        block["left_label"] = title
    return block


def _two_pieces(text: str) -> tuple[str, str] | None:
    if "\n" in text:
        left, right = text.split("\n", 1)
        if left.strip() and right.strip():
            return left.strip(), right.strip()
    mark = ". "
    if mark in text:
        left, right = text.split(mark, 1)
        if left.strip() and right.strip():
            return left.strip() + ".", right.strip()
    return None


def _table(rows: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    keys: list[str] = []
    for row in rows:
        for key in row:
            name = str(key)
            if name not in keys:
                keys.append(name)
    if not keys:
        return None
    grid = []
    for row in rows:
        grid.append([row.get(key, "") if key in row else "" for key in keys])
    return {"type": "table", "columns": keys, "rows": grid}


def _chart(chart: Mapping[str, Any], fallback: str) -> dict[str, Any] | None:
    if isinstance(chart.get("categories"), list) and isinstance(chart.get("series"), list):
        kind = _kind(chart.get("kind"))
        title = _string(chart.get("title")) or _string(chart.get("label")) or fallback
        return {
            "title": title,
            "kind": kind,
            "categories": list(chart["categories"]),
            "series": chart["series"],
        }
    rows = [row for row in chart.get("rows") or [] if isinstance(row, Mapping)]
    if not rows:
        return None
    value_key = ""
    label_key = ""
    for key in rows[0]:
        sample = rows[0][key]
        if isinstance(sample, (int, float)) and not isinstance(sample, bool) and not value_key:
            value_key = str(key)
        elif not label_key:
            label_key = str(key)
    if not value_key or not label_key:
        return None
    kind = _kind(chart.get("type") or chart.get("kind"))
    title = _string(chart.get("label")) or _string(chart.get("title")) or fallback or value_key
    return {
        "title": title,
        "kind": kind,
        "categories": [str(row.get(label_key, "")) for row in rows],
        "series": [{"name": value_key, "values": [row.get(value_key, 0) for row in rows]}],
    }


def _kind(value: object) -> str:
    """Keep the model's kind when the registry can draw it. Otherwise bar."""
    if isinstance(value, str) and value in known():
        return value
    return "bar"


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""
