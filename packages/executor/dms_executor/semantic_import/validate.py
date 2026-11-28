"""Map a parsed model onto the Space grant list and the ask-path SQL gates.

A table or column the Space does not grant is ``needs_grant``. It is not
dropped and it is not granted. SQL that fails a gate is a typed reason with
the statement omitted. A pass is executed once, read-only, and the rows are
not returned.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.semantic_import.normalize import rows, text

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_READS = {
    "snowflake": ("snowflake", SERVING_DIALECT, "ansi"),
    "genie": ("databricks", "spark", SERVING_DIALECT, "ansi"),
    "osi": (SERVING_DIALECT, "ansi", "snowflake"),
    "dbt": (SERVING_DIALECT, "ansi"),
    "wren": (SERVING_DIALECT, "ansi"),
}
_GRANT_HEADS = frozenset({"ungranted", "sql_relation_not_granted"})


def review(
    model: Mapping[str, Any],
    *,
    grantable: set[str],
    warehouse: Path,
    dialect: str,
    space_id: str,
) -> dict[str, Any]:
    granted = {name.lower(): name for name in grantable}
    catalog = _catalog(warehouse, set(granted.values()))
    items: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    benchmarks: list[dict[str, Any]] = []
    matched = _entities(model, granted, catalog, space_id, items, proposals)
    _relationships(model, matched, space_id, items, proposals)
    _measures(model, matched, warehouse, dialect, space_id, items, proposals)
    _filters(model, matched, warehouse, dialect, space_id, items, proposals)
    _queries(
        model,
        "verified_query",
        rows(model.get("verified_queries")),
        warehouse,
        dialect,
        space_id,
        grantable,
        items,
        examples,
    )
    _queries(
        model,
        "benchmark",
        rows(model.get("benchmarks")),
        warehouse,
        dialect,
        space_id,
        grantable,
        items,
        benchmarks,
    )
    for gap in rows(model.get("unsupported")):
        if isinstance(gap, dict):
            items.append(
                {
                    "kind": "unsupported",
                    "name": text(gap.get("name")),
                    "status": "unsupported",
                    "reason": text(gap.get("reason")),
                    "path": text(gap.get("path")),
                }
            )
    for ask in rows(model.get("onboarding")):
        if isinstance(ask, str) and ask.strip():
            items.append({"kind": "onboarding", "name": ask.strip(), "status": "proposed"})
    for line in rows(model.get("instructions")):
        if isinstance(line, str) and line.strip():
            items.append({"kind": "instruction", "name": "instruction", "status": "proposed"})
    return {
        "items": items,
        "proposals": proposals,
        "examples": examples,
        "benchmarks": benchmarks,
    }


def _entities(
    model: Mapping[str, Any],
    granted: dict[str, str],
    catalog: dict[str, set[str] | None],
    space_id: str,
    items: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
) -> dict[str, str]:
    """Logical name and physical name to the granted table. Missing stays out."""
    matched: dict[str, str] = {}
    for raw in rows(model.get("entities")):
        if not isinstance(raw, dict):
            continue
        name = text(raw.get("name"))
        physical = text(raw.get("table")) or name
        table = _match(physical, granted)
        if table is None:
            items.append(
                {"kind": "entity", "name": name, "status": "needs_grant", "table": physical}
            )
            for col in rows(raw.get("columns")):
                if isinstance(col, dict):
                    items.append(
                        {
                            "kind": "column",
                            "name": text(col.get("name")),
                            "status": "needs_grant",
                            "table": physical,
                        }
                    )
            continue
        known = catalog.get(table)
        if known is None:
            items.append(
                {
                    "kind": "entity",
                    "name": name,
                    "status": "unsupported",
                    "reason": "table_absent",
                    "table": table,
                }
            )
            continue
        matched[name.lower()] = table
        matched[physical.lower()] = table
        matched[table.lower()] = table
        kept, key = _columns(raw, table, known, items)
        payload_key = key or [col["source_column"] for col in kept[:1]]
        if not payload_key:
            items.append(
                {
                    "kind": "entity",
                    "name": name,
                    "status": "unsupported",
                    "reason": "no_primary_key",
                    "table": table,
                }
            )
            continue
        items.append({"kind": "entity", "name": name, "status": "proposed", "table": table})
        proposals.append(
            {
                "kind": "object",
                "payload": {
                    "name": name,
                    "relation": table,
                    "key": payload_key,
                    "description": text(raw.get("description")),
                    "attributes": kept,
                    "filters": [],
                    "space_id": space_id,
                    "model_name": text(model.get("name")),
                    "model_description": text(model.get("description")),
                    "instructions": [
                        line for line in rows(model.get("instructions")) if isinstance(line, str)
                    ],
                    "onboarding": [
                        line for line in rows(model.get("onboarding")) if isinstance(line, str)
                    ],
                },
            }
        )
    return matched


def _columns(
    raw: Mapping[str, Any],
    table: str,
    known: set[str] | None,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    kept: list[dict[str, Any]] = []
    keys = [text(name) for name in rows(raw.get("primary_key")) if text(name)]
    for col in rows(raw.get("columns")):
        if not isinstance(col, dict):
            continue
        col_name = text(col.get("name"))
        if known is not None and col_name.lower() not in known:
            items.append(
                {"kind": "column", "name": col_name, "status": "needs_grant", "table": table}
            )
            keys = [key for key in keys if key.lower() != col_name.lower()]
            continue
        items.append({"kind": "column", "name": col_name, "status": "proposed", "table": table})
        kept.append(
            {
                "source_column": col_name,
                "display_name": col_name,
                "description": text(col.get("description")),
                "synonyms": [name for name in rows(col.get("synonyms")) if isinstance(name, str)],
                "expr": text(col.get("expr")),
                "data_type": text(col.get("data_type")),
                "role": text(col.get("role")) or "dimension",
            }
        )
    kept_names = {col["source_column"].lower() for col in kept}
    key = [name for name in keys if name.lower() in kept_names] or keys
    if known is not None:
        key = [name for name in key if name.lower() in known]
    return kept, key


def _relationships(
    model: Mapping[str, Any],
    matched: dict[str, str],
    space_id: str,
    items: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
) -> None:
    for raw in rows(model.get("relationships")):
        if not isinstance(raw, dict):
            continue
        name = text(raw.get("name"))
        left = matched.get(text(raw.get("from_table")).lower())
        right = matched.get(text(raw.get("to_table")).lower())
        if left is None or right is None:
            items.append({"kind": "relationship", "name": name, "status": "needs_grant"})
            continue
        left_cols = [text(col) for col in rows(raw.get("from_columns")) if text(col)]
        right_cols = [text(col) for col in rows(raw.get("to_columns")) if text(col)]
        cardinality = text(raw.get("cardinality")) or "many_to_one"
        items.append({"kind": "relationship", "name": name, "status": "proposed"})
        proposals.append(
            {
                "kind": "link",
                "payload": {
                    "name": name,
                    "from_object": left,
                    "from_columns": left_cols,
                    "to_object": right,
                    "to_columns": right_cols,
                    "one_to_one": cardinality == "one_to_one",
                    "cardinality": cardinality,
                    "join_type": text(raw.get("join_type")),
                    "space_id": space_id,
                },
            }
        )


def _measures(
    model: Mapping[str, Any],
    matched: dict[str, str],
    warehouse: Path,
    dialect: str,
    space_id: str,
    items: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
) -> None:
    fmt = text(model.get("format"))
    for raw in rows(model.get("measures")):
        if not isinstance(raw, dict):
            continue
        _scalar(
            raw,
            kind="measure",
            matched=matched,
            warehouse=warehouse,
            dialect=dialect,
            fmt=fmt,
            space_id=space_id,
            items=items,
            proposals=proposals,
            wrap="select",
        )


def _filters(
    model: Mapping[str, Any],
    matched: dict[str, str],
    warehouse: Path,
    dialect: str,
    space_id: str,
    items: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
) -> None:
    fmt = text(model.get("format"))
    for raw in rows(model.get("filters")):
        if not isinstance(raw, dict):
            continue
        _scalar(
            raw,
            kind="filter",
            matched=matched,
            warehouse=warehouse,
            dialect=dialect,
            fmt=fmt,
            space_id=space_id,
            items=items,
            proposals=proposals,
            wrap="where",
        )


def _scalar(
    raw: Mapping[str, Any],
    *,
    kind: str,
    matched: dict[str, str],
    warehouse: Path,
    dialect: str,
    fmt: str,
    space_id: str,
    items: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    wrap: str,
) -> None:
    name = text(raw.get("name"))
    expr = text(raw.get("expr"))
    table = matched.get(text(raw.get("table")).lower())
    if text(raw.get("table")) and table is None:
        items.append({"kind": kind, "name": name, "status": "needs_grant"})
        return
    statement = _wrap(expr, table or "", dialect, wrap)
    if statement is None:
        items.append(
            {"kind": kind, "name": name, "status": "unsupported", "reason": "measure_without_table"}
        )
        return
    checked = check_sql(
        statement,
        ask_text=name,
        grantable=set(matched.values()),
        warehouse=warehouse,
        dialect=dialect,
        space_id=space_id,
        fmt=fmt,
    )
    status = str(checked["status"])
    if status != "candidate":
        items.append(
            {"kind": kind, "name": name, "status": status, "reason": checked.get("reason")}
        )
        return
    items.append({"kind": kind, "name": name, "status": "proposed", "table": table or ""})
    if kind == "filter":
        _attach_filter(proposals, table, raw)
        return
    proposals.append(
        {
            "kind": "measure",
            "payload": {
                "name": name,
                "expr": expr,
                "description": text(raw.get("description")),
                "synonyms": [item for item in rows(raw.get("synonyms")) if isinstance(item, str)],
                "table": table or "",
                "space_id": space_id,
            },
        }
    )


def _attach_filter(
    proposals: list[dict[str, Any]], table: str | None, raw: Mapping[str, Any]
) -> None:
    payload = {
        "name": text(raw.get("name")),
        "expr": text(raw.get("expr")),
        "description": text(raw.get("description")),
        "synonyms": [item for item in rows(raw.get("synonyms")) if isinstance(item, str)],
    }
    for item in proposals:
        body = item.get("payload")
        if item.get("kind") != "object" or not isinstance(body, dict):
            continue
        if table and body.get("relation") != table and body.get("name") != table:
            continue
        filters = body.get("filters")
        if isinstance(filters, list):
            filters.append(payload)
            return
    if proposals:
        body = proposals[0].get("payload")
        if isinstance(body, dict) and isinstance(body.get("filters"), list):
            body["filters"].append(payload)


def _queries(
    model: Mapping[str, Any],
    kind: str,
    raw_rows: list[Any],
    warehouse: Path,
    dialect: str,
    space_id: str,
    grantable: set[str],
    items: list[dict[str, Any]],
    sink: list[dict[str, Any]],
) -> None:
    fmt = text(model.get("format"))
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        name = text(raw.get("name"))
        ask = text(raw.get("question"))
        checked = check_sql(
            text(raw.get("sql")),
            ask_text=ask or name,
            grantable=grantable,
            warehouse=warehouse,
            dialect=dialect,
            space_id=space_id,
            fmt=fmt,
        )
        status = str(checked["status"])
        if status != "candidate":
            item: dict[str, Any] = {"kind": kind, "name": name, "status": status}
            if checked.get("reason"):
                item["reason"] = checked["reason"]
            items.append(item)
            continue
        shown = "benchmark" if kind == "benchmark" else "candidate"
        items.append(
            {
                "kind": kind,
                "name": name,
                "status": shown,
                "provenance": f"import:{fmt}",
                "row_count": checked.get("row_count", 0),
            }
        )
        sink.append({"name": name, "question": ask, "sql": checked.get("sql") or ""})


def check_sql(
    sql: str,
    *,
    ask_text: str,
    grantable: set[str],
    warehouse: Path,
    dialect: str,
    space_id: str,
    fmt: str,
) -> dict[str, Any]:
    """One statement through transpile, grants, the ask gates, and one read."""
    transpiled = transpile_sql(sql, write=dialect, fmt=fmt)
    if transpiled is None:
        return {"status": "transpile_failed"}
    from dms_executor.grant_struct import serve_gap

    gap = serve_gap(transpiled, grantable=set(grantable), dialect=dialect)
    if gap:
        return _gap(gap)
    from dms_executor.generative_ask import validated_sql_reason

    blocked = validated_sql_reason(
        transpiled, question=ask_text, warehouse=warehouse, grantable=set(grantable)
    )
    if blocked:
        return _gap(blocked)
    join_why = _join_reason(transpiled, space_id, warehouse, grantable)
    if join_why:
        return _gap(join_why)
    from dms_executor.generative_ask import validate_compiled_sql

    compiled = validate_compiled_sql(
        transpiled, grantable=set(grantable), warehouse=warehouse, dialect=dialect
    )
    if compiled:
        return _gap(compiled)
    scored = _scored_write(transpiled, warehouse)
    if scored:
        return scored
    from dms_executor.sql_loop import run_readonly

    fetched, err = run_readonly(transpiled, warehouse)
    if err or fetched is None:
        return {"status": "exec_error"}
    return {"status": "candidate", "sql": transpiled, "row_count": len(fetched)}


def _scored_write(sql: str, warehouse: Path) -> dict[str, Any] | None:
    """Refuse a listed pack statement before it is executed or stored."""
    from dms_executor.skills_quarantine import reject_scored_write

    try:
        reject_scored_write({"sql": sql}, warehouse=warehouse)
    except ValueError as exc:
        code = (
            exc.args[0]
            if exc.args and isinstance(exc.args[0], str)
            else "scored_pack_write_blocked"
        )
        return {"status": f"gate_refused:{code}"}
    return None


def transpile_sql(sql: str, *, write: str, fmt: str) -> str | None:
    statement = sql.strip()
    if not statement:
        return None
    reads: list[str] = [write]
    reads.extend(_READS.get(fmt, ("ansi",)))
    seen: list[str] = []
    for src in reads:
        if not src or src in seen:
            continue
        seen.append(src)
        try:
            parts = sqlglot.transpile(statement, read=src, write=write)
        except Exception:  # noqa: BLE001 - the next read dialect may parse it
            continue
        if len(parts) == 1 and parts[0].strip():
            return parts[0].strip()
    return None


def _gap(reason: str) -> dict[str, Any]:
    head = reason.split(":", 1)[0]
    if head in _GRANT_HEADS:
        return {"status": "needs_grant"}
    return {"status": f"gate_refused:{reason}"}


def _join_reason(sql: str, space_id: str, warehouse: Path, grantable: set[str]) -> str | None:
    from dms_executor.space_ontology import check_sql_against_space, load_space_ontology

    try:
        onto = load_space_ontology(space_id)
    except Exception:  # noqa: BLE001 - the ask path names an unreadable store
        return "unverified_join:check_unavailable"
    if onto is None:
        return None
    return check_sql_against_space(sql, onto, warehouse, sorted(grantable))


def _match(physical: str, granted: dict[str, str]) -> str | None:
    raw = physical.strip().strip('"')
    if not raw:
        return None
    if raw.lower() in granted:
        return granted[raw.lower()]
    bare = raw.split(".")[-1].strip().strip('"')
    return granted.get(bare.lower())


def _catalog(warehouse: Path, tables: set[str]) -> dict[str, set[str] | None]:
    """Granted table to its column names. None means the table is not in the file."""
    if not tables or not warehouse.is_file():
        return {table: None for table in tables}
    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(warehouse)
    try:
        fetched = con.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            """
        ).fetchall()
    finally:
        con.close()
    found: dict[str, set[str]] = {}
    for table_name, column_name in fetched:
        found.setdefault(str(table_name).lower(), set()).add(str(column_name).lower())
    out: dict[str, set[str] | None] = {}
    for table in tables:
        out[table] = found.get(table.lower())
    return out


def _wrap(expr: str, table: str, dialect: str, wrap: str) -> str | None:
    if not expr.strip():
        return None
    try:
        tree = sqlglot.parse_one(expr, read=dialect)
    except Exception:  # noqa: BLE001
        tree = None
    if isinstance(tree, exp.Query):
        return expr
    if tree is not None and tree.find(exp.Window) is not None:
        return None
    if not table or not _IDENT.fullmatch(table):
        return None
    if wrap == "where":
        return f"SELECT 1 AS hit FROM {table} WHERE ({expr})"
    return f"SELECT ({expr}) AS metric_value FROM {table}"
