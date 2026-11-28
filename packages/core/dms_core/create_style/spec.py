"""Validate a document the model (or a caller) already wrote.

This module checks shape. It does not write headlines, captions, or commentary.
"""

from __future__ import annotations

import base64
from typing import Any

from dms_core.create_style.errors import CreateError

BLOCK_TYPES = (
    "image_side",
    "image_below",
    "two_column",
    "kpi_strip",
    "chart_commentary",
    "table",
    "chart_page",
)
_REQUIRED = {
    "image_side": ("image_b64", "caption"),
    "image_below": ("image_b64", "caption"),
    "two_column": ("left", "right"),
    "kpi_strip": ("items",),
    "chart_commentary": ("chart", "commentary"),
    "table": ("columns", "rows"),
    "chart_page": ("charts",),
}
# Optional keys the model may send. Empty means the field is omitted.
_OPTIONAL = {
    "image_side": ("label",),
    "image_below": ("label",),
    "two_column": ("left_label", "right_label"),
}
_KINDS = frozenset({"app", "doc", "slides"})
_MAX_BLOCKS = 40
_MAX_TEXT = 20_000
_MAX_ROWS = 5_000
_MAX_COLS = 12
_MAX_ITEMS = 4
_MAX_POINTS = 24
_MAX_IMAGE = 2_000_000


def catalog() -> list[dict[str, Any]]:
    """Block types and the fields each one requires. No sample sentences."""
    rows = []
    for name in BLOCK_TYPES:
        row: dict[str, Any] = {"type": name, "fields": list(_REQUIRED[name])}
        optional = _OPTIONAL.get(name)
        if optional:
            row["optional"] = list(optional)
        rows.append(row)
    return rows


def layout_blocks(kind: str) -> list[dict[str, Any]]:
    """Blocks one Create kind can place.

    ``dms_executor.create_context.layout_blocks`` (DMS-CREATE-CONTEXT-01, still
    open) stays empty until that branch lands. It should return this list.
    ``app``, ``doc``, and ``slides`` share one schema. The flag off returns
    nothing, so Create's style dict is unchanged.
    """
    if kind not in _KINDS:
        return []
    from dms_core.create_style.flag import enabled

    if not enabled():
        return []
    return catalog()


def normalize(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise CreateError("document_required")
    title = document.get("title", "")
    if not isinstance(title, str):
        raise CreateError("bad_title")
    title = _text(title, "bad_title", allow_empty=True)
    blocks = document.get("blocks")
    if not isinstance(blocks, list) or not blocks or len(blocks) > _MAX_BLOCKS:
        raise CreateError("blocks_required")
    return {"title": title, "blocks": [_block(item) for item in blocks]}


def _block(item: object) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise CreateError("bad_block")
    kind = item.get("type")
    if kind not in BLOCK_TYPES:
        raise CreateError("bad_block")
    if kind in ("image_side", "image_below"):
        block = {
            "type": kind,
            "image": _image(item.get("image_b64")),
            "caption": _text(item.get("caption"), "caption_required"),
        }
        _keep(block, "label", _optional(item, "label"))
        return block
    if kind == "two_column":
        block = {
            "type": kind,
            "left": _text(item.get("left"), "text_required"),
            "right": _text(item.get("right"), "text_required"),
        }
        _keep(block, "left_label", _optional(item, "left_label"))
        _keep(block, "right_label", _optional(item, "right_label"))
        return block
    if kind == "kpi_strip":
        return {"type": kind, "items": _items(item.get("items"))}
    if kind == "chart_commentary":
        return {
            "type": kind,
            "chart": _chart(item.get("chart")),
            "commentary": _text(item.get("commentary"), "text_required"),
        }
    if kind == "table":
        return {
            "type": kind,
            "columns": _columns(item.get("columns")),
            "rows": _rows(item.get("rows"), item.get("columns")),
        }
    charts = item.get("charts")
    if not isinstance(charts, list) or len(charts) != 4:
        raise CreateError("chart_page_needs_four")
    return {"type": kind, "charts": [_chart(chart) for chart in charts]}


def _optional(item: dict[str, Any], key: str) -> str:
    if key not in item or item.get(key) in ("", None):
        return ""
    return _text(item.get(key), "bad_label")


def _keep(block: dict[str, Any], key: str, value: str) -> None:
    if value:
        block[key] = value


def _text(value: object, code: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise CreateError(code)
    if len(value) > _MAX_TEXT:
        raise CreateError("text_too_long")
    if not allow_empty and value.strip() == "":
        raise CreateError(code)
    return value


def _image(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise CreateError("image_required")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raise CreateError("bad_image") from None
    if not raw or len(raw) > _MAX_IMAGE:
        raise CreateError("bad_image")
    return raw


def _items(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value or len(value) > _MAX_ITEMS:
        raise CreateError("kpi_required")
    rows = []
    for item in value:
        if not isinstance(item, dict):
            raise CreateError("kpi_required")
        rows.append(
            {
                "headline": _text(item.get("headline"), "kpi_required"),
                "value": _text(item.get("value"), "kpi_required"),
                "source": _text(item.get("source"), "kpi_required"),
            }
        )
    return rows


def _columns(value: object) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > _MAX_COLS:
        raise CreateError("bad_table")
    names = []
    for item in value:
        names.append(_text(item, "bad_table"))
    if len(set(names)) != len(names):
        raise CreateError("bad_table")
    return names


def _rows(value: object, columns: object) -> list[list[object]]:
    if not isinstance(columns, list) or not isinstance(value, list):
        raise CreateError("bad_table")
    if len(value) > _MAX_ROWS:
        raise CreateError("bad_table")
    width = len(columns)
    rows: list[list[object]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != width:
            raise CreateError("bad_table")
        cells: list[object] = []
        for cell in row:
            cells.append(_cell(cell))
        rows.append(cells)
    return rows


def _cell(value: object) -> object:
    if isinstance(value, bool) or value is None:
        raise CreateError("bad_cell")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CreateError("bad_cell")
        return value
    if isinstance(value, str):
        return _text(value, "bad_cell", allow_empty=True)
    raise CreateError("bad_cell")


def _chart(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CreateError("bad_chart")
    kind = value.get("kind")
    if kind not in ("bar", "line"):
        raise CreateError("bad_chart")
    title = value.get("title", "")
    if not isinstance(title, str):
        raise CreateError("bad_chart")
    title = _text(title, "bad_chart", allow_empty=True)
    categories = value.get("categories")
    series = value.get("series")
    if not isinstance(categories, list) or not categories or len(categories) > _MAX_POINTS:
        raise CreateError("bad_chart")
    labels = [_text(item, "bad_chart") for item in categories]
    if not isinstance(series, list) or not series or len(series) > 4:
        raise CreateError("bad_chart")
    parsed = []
    for item in series:
        if not isinstance(item, dict):
            raise CreateError("bad_chart")
        name = _text(item.get("name"), "bad_chart")
        values = item.get("values")
        if not isinstance(values, list) or len(values) != len(labels):
            raise CreateError("bad_chart")
        numbers = [_number(piece) for piece in values]
        parsed.append({"name": name, "values": numbers})
    return {"title": title, "kind": kind, "categories": labels, "series": parsed}


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CreateError("bad_chart")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise CreateError("bad_chart")
    return number


def cell_text(value: object) -> str:
    """The characters to store. A float keeps its own digits. Nothing is cut."""
    if isinstance(value, bool):
        raise CreateError("bad_cell")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = format(value, "f").rstrip("0").rstrip(".")
        return text or "0"
    if isinstance(value, str):
        return value
    raise CreateError("bad_cell")
