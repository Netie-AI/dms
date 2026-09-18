"""INSIGHTS-EXPORT-01 — serialize a real ask envelope to .xlsx.

Copies envelope fields and rows as received. Does not ask Cortex, does not
query DuckDB, does not add total/rank rows. Gate is envelope shape, not a
parallel invent path. Not EPIC-016 FRTR (#29).
"""

from __future__ import annotations

import json
from typing import Any

from dms_core.xlsx_ooxml import xlsx_workbook_bytes

ALLOWED_BADGES = frozenset(
    {
        "L0_CERTIFIED",
        "L1_GOVERNED_METRIC",
        "L2_VALIDATED",
        "L2_ANOMALOUS",
        "ABSTAIN",
    }
)

_COVER_KEYS = (
    "answer_id",
    "badge",
    "abstained",
    "ask_mode",
    "as_of",
    "audit_id",
    "space_id",
    "text",
    "sql_used",
)


class EnvelopeExportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def xlsx_download_name(answer_id: str) -> str:
    safe = (
        str(answer_id)
        .replace("\\", "_")
        .replace("/", "_")
    )
    out = []
    for ch in safe:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
        else:
            out.append("_")
    compact = "".join(out)
    while ".." in compact:
        compact = compact.replace("..", ".")
    compact = compact.strip("._-")[:80]
    return f"dms_answer_{compact or 'export'}.xlsx"


def column_order(rows: list[dict[str, Any]]) -> list[str]:
    cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key in seen:
                continue
            seen.add(key)
            cols.append(key)
    return cols


def _src_ref(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    ref = item.get("ref_id", item.get("ref", ""))
    row = item.get("row", item.get("line", ""))
    if ref == "" or row == "":
        return None
    return f"{ref}:{row}"


def cell_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return "[]"
        refs = [_src_ref(item) for item in value]
        if refs and all(r is not None for r in refs):
            return ", ".join(refs)  # type: ignore[arg-type]
        return ", ".join(str(cell_value(item)) for item in value)
    if isinstance(value, dict):
        ref = _src_ref(value)
        if ref:
            return ref
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            return "[unserializable]"
    return str(value)


def refuse_envelope_export(envelope: object) -> str | None:
    if not isinstance(envelope, dict):
        return "envelope_required"
    answer_id = str(envelope.get("answer_id") or "").strip()
    badge = envelope.get("badge")
    if not answer_id or badge not in ALLOWED_BADGES:
        return "envelope_required"
    rows = envelope.get("rows")
    if rows is not None and not isinstance(rows, list):
        return "envelope_required"
    if isinstance(rows, list) and any(not isinstance(row, dict) for row in rows):
        return "envelope_required"
    values = envelope.get("values")
    if values is not None and not isinstance(values, list):
        return "envelope_required"
    if isinstance(values, list) and any(
        item is not None and not isinstance(item, dict) for item in values
    ):
        return "envelope_required"
    return None


def _cover_grid(envelope: dict[str, Any]) -> list[list[object]]:
    grid: list[list[object]] = [["field", "value"]]
    for key in _COVER_KEYS:
        if key not in envelope or envelope[key] is None:
            continue
        grid.append([key, cell_value(envelope[key])])
    return grid


def _values_grid(values: list[dict[str, Any]]) -> list[list[object]]:
    preferred = ("id", "value", "unit", "label")
    extras: list[str] = []
    seen = set(preferred)
    for item in values:
        for key in item:
            if key in seen:
                continue
            seen.add(key)
            extras.append(key)
    cols = [c for c in preferred if any(c in item for item in values)] + extras
    if not cols:
        cols = list(preferred)
    grid: list[list[object]] = [list(cols)]
    for item in values:
        grid.append([cell_value(item.get(c)) for c in cols])
    return grid


def _rows_grid(rows: list[dict[str, Any]]) -> list[list[object]]:
    cols = column_order(rows)
    grid: list[list[object]] = [list(cols)]
    for row in rows:
        grid.append([cell_value(row.get(c)) for c in cols])
    return grid


def envelope_sheets(envelope: dict[str, Any]) -> list[tuple[str, list[list[object]]]]:
    sheets: list[tuple[str, list[list[object]]]] = [("Cover", _cover_grid(envelope))]
    values = [item for item in (envelope.get("values") or []) if isinstance(item, dict)]
    if values:
        sheets.append(("Values", _values_grid(values)))
    rows = [row for row in (envelope.get("rows") or []) if isinstance(row, dict)]
    if rows:
        sheets.append(("Rows", _rows_grid(rows)))
    return sheets


def export_envelope_xlsx(envelope: object) -> tuple[bytes, str]:
    """Return (xlsx_bytes, filename). Raises EnvelopeExportError if ungated."""
    code = refuse_envelope_export(envelope)
    if code:
        raise EnvelopeExportError(
            code,
            "Excel export needs a real ask envelope (answer_id + badge); "
            "it does not invent rows.",
        )
    assert isinstance(envelope, dict)
    data = xlsx_workbook_bytes(envelope_sheets(envelope))
    return data, xlsx_download_name(str(envelope.get("answer_id") or ""))
