"""INSIGHTS-EXPORT-02 — Power BI / Superset connect stubs from a real ask envelope.

Copies envelope rows (or values, or Cover) as received. Does not ask Cortex,
does not query DuckDB, does not invent DAX/Superset metrics, does not emit a
live SQLAlchemy URI or DuckLake folder connector. Gate is the same envelope
shape as Excel export. Not COMPLETE. Not #108.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from dms_core.xlsx_export import (
    COVER_KEYS,
    EnvelopeExportError,
    cell_value,
    column_order,
    refuse_envelope_export,
    xlsx_download_name,
)

BiTarget = Literal["powerbi", "superset"]
TARGETS: tuple[BiTarget, ...] = ("powerbi", "superset")

PBI_BLOCKED = (
    "live_odbc",
    "semantic_model_publish",
    "ducklake_folder_union",
    "pbix_binary",
)
PBI_NEEDS_YOU = (
    "Power BI Desktop is not in this appliance. "
    "Paste power_query_m into Get Data > Blank Query.",
    "Live DuckDB ODBC, .pbix publish, and Cortex parquet export "
    "are not wired from this envelope path (NEEDS-YOU).",
    "Do not Folder-connect DuckLake data/ (P-DMS-24 double-count). "
    "This table is the ask envelope, not a semantic model.",
)
SUPERSET_BLOCKED = (
    "sqlalchemy_uri",
    "superset_as_dms_chrome",
    "live_database",
)
SUPERSET_NEEDS_YOU = (
    "Apache Superset is not DMS chrome. Import the envelope dataset; "
    "do not embed Superset as product UI.",
    "SQLAlchemy URI / live lake connection is not shipped "
    "(NEEDS-YOU: steward-hosted Superset). A URI would be a secret; it is omitted.",
)


def bi_download_name(answer_id: str, ext: str) -> str:
    base = xlsx_download_name(answer_id)
    if base.endswith(".xlsx"):
        base = base[:-5]
    suffix = ext.lstrip(".")
    return f"{base}.{suffix}"


def _m_text(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _m_literal(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "null"
        return format(value, ".15g")
    return _m_text(str(value))


def power_query_m(columns: list[str], table: list[dict[str, Any]]) -> str:
    col_list = "{" + ", ".join(_m_text(c) for c in columns) + "}"
    if table:
        rows_list = (
            "{"
            + ", ".join(
                "{" + ", ".join(_m_literal(rec.get(c)) for c in columns) + "}"
                for rec in table
            )
            + "}"
        )
    else:
        rows_list = "{}"
    return (
        "// DMS INSIGHTS-EXPORT-02 -- ask envelope table only. No invented measures.\n"
        "// Live Power BI semantic model / ODBC is NEEDS-YOU. Do not Folder-connect DuckLake.\n"
        "let\n"
        f"    Source = #table({col_list}, {rows_list})\n"
        "in\n"
        "    Source\n"
    )


def _values_records(values: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
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
    table = [{c: cell_value(item.get(c)) for c in cols} for item in values]
    return cols, table


def envelope_connect_table(
    envelope: dict[str, Any],
) -> tuple[str, list[str], list[dict[str, Any]]]:
    rows = [row for row in (envelope.get("rows") or []) if isinstance(row, dict)]
    if rows:
        cols = column_order(rows)
        table = [{c: cell_value(row.get(c)) for c in cols} for row in rows]
        return "rows", cols, table
    values = [item for item in (envelope.get("values") or []) if isinstance(item, dict)]
    if values:
        cols, table = _values_records(values)
        return "values", cols, table
    cover: list[dict[str, Any]] = []
    for key in COVER_KEYS:
        if key not in envelope or envelope[key] is None:
            continue
        cover.append({"field": key, "value": cell_value(envelope[key])})
    return "cover", ["field", "value"], cover


def _col_type(name: str, table: list[dict[str, Any]]) -> str:
    kinds: set[str] = set()
    for rec in table:
        value = rec.get(name)
        if value is None:
            continue
        if isinstance(value, bool):
            kinds.add("BOOLEAN")
        elif isinstance(value, int):
            kinds.add("INTEGER")
        elif isinstance(value, float):
            kinds.add("FLOAT")
        else:
            kinds.add("STRING")
    if not kinds:
        return "STRING"
    if kinds <= {"INTEGER", "FLOAT"}:
        return "FLOAT" if "FLOAT" in kinds else "INTEGER"
    if len(kinds) == 1:
        return next(iter(kinds))
    return "STRING"


def _powerbi_stub(
    *,
    answer_id: str,
    columns: list[str],
    table: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "envelope_stub",
        "live_connector": False,
        "blocked": list(PBI_BLOCKED),
        "needs_you": list(PBI_NEEDS_YOU),
        "filename": bi_download_name(answer_id, "pq"),
        "power_query_m": power_query_m(columns, table),
    }


def _superset_stub(
    *,
    answer_id: str,
    columns: list[str],
    table: list[dict[str, Any]],
) -> dict[str, Any]:
    stem = bi_download_name(answer_id, "superset.json").removesuffix(".superset.json")
    return {
        "status": "envelope_stub",
        "live_connector": False,
        "blocked": list(SUPERSET_BLOCKED),
        "needs_you": list(SUPERSET_NEEDS_YOU),
        "filename": bi_download_name(answer_id, "superset.json"),
        "dataset": {
            "database_name": None,
            "sqlalchemy_uri": None,
            "embed_as_dms_chrome": False,
            "table_name": stem,
            "columns": [
                {"column_name": name, "type": _col_type(name, table)} for name in columns
            ],
            "rows": table,
        },
    }


def export_envelope_bi(
    envelope: object,
    *,
    target: BiTarget | str | None = None,
) -> dict[str, Any]:
    """Return a connect stub from a real ask envelope. Raises if ungated."""
    code = refuse_envelope_export(envelope)
    if code:
        raise EnvelopeExportError(
            code,
            "BI export needs a real ask envelope (answer_id + badge); "
            "it does not invent metrics.",
        )
    assert isinstance(envelope, dict)
    if target is not None and target not in TARGETS:
        raise EnvelopeExportError(
            "target_unknown",
            "target must be powerbi, superset, or omitted for both.",
        )
    source_table, columns, table = envelope_connect_table(envelope)
    answer_id = str(envelope.get("answer_id") or "")
    wanted: tuple[BiTarget, ...] = (target,) if target in TARGETS else TARGETS
    targets: dict[str, Any] = {}
    if "powerbi" in wanted:
        targets["powerbi"] = _powerbi_stub(
            answer_id=answer_id, columns=columns, table=table
        )
    if "superset" in wanted:
        targets["superset"] = _superset_stub(
            answer_id=answer_id, columns=columns, table=table
        )
    return {
        "ok": True,
        "complete": False,
        "live_connector": False,
        "answer_id": answer_id,
        "badge": envelope.get("badge"),
        "abstained": bool(envelope.get("abstained")),
        "row_count": len(table),
        "columns": columns,
        "source_table": source_table,
        "table": table,
        "targets": targets,
    }
