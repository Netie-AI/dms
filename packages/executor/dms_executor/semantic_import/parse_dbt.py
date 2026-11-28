"""dbt ``semantic_manifest.json``. An ``osi_document`` delegates to the OSI parser."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dms_executor.semantic_import.normalize import (
    blank,
    column,
    description_of,
    entity,
    key_columns,
    rows,
    text,
)
from dms_executor.semantic_import.parse_osi import parse_osi
from dms_executor.semantic_import.sql_shape import unsupported_reason


def parse_dbt(doc: Mapping[str, Any]) -> dict[str, Any]:
    if "semantic_models" not in doc and ("datasets" in doc or "semantic_model" in doc):
        parsed = parse_osi(doc)
        parsed["format"] = "dbt"
        return parsed
    model = blank("dbt")
    model["name"] = text(doc.get("name"))
    model["description"] = text(doc.get("description"))
    known: dict[str, tuple[str, str]] = {}
    for semantic in rows(doc.get("semantic_models")):
        if isinstance(semantic, dict):
            _semantic(model, semantic, known)
    for metric in rows(doc.get("metrics")):
        if isinstance(metric, dict):
            _metric(model, metric, known)
    for saved in rows(doc.get("saved_queries")):
        if isinstance(saved, dict):
            _saved(model, saved)
    return model


def _relation(value: Any) -> str:
    raw = text(value)
    if not raw:
        return ""
    found: list[str] = []
    buf: list[str] = []
    quote = ""
    for char in raw:
        if quote:
            if char == quote:
                found.append("".join(buf))
                buf = []
                quote = ""
            else:
                buf.append(char)
        elif char in {"'", '"'}:
            quote = char
    return found[-1] if found else raw


def _semantic(
    model: dict[str, Any], semantic: Mapping[str, Any], known: dict[str, tuple[str, str]]
) -> None:
    name = text(semantic.get("name"))
    table = _relation(semantic.get("model")) or name
    columns: list[dict[str, Any]] = []
    keys: list[str] = []
    for item in rows(semantic.get("entities")):
        if not isinstance(item, dict):
            continue
        expr = text(item.get("expr")) or text(item.get("name"))
        if text(item.get("type")) == "primary" and expr:
            keys.append(expr)
        columns.append(
            column(
                text(item.get("name")) or expr,
                expr=expr,
                description=description_of(item),
                synonyms=[],
                data_type="",
                role="dimension",
            )
        )
    for item in rows(semantic.get("dimensions")):
        if not isinstance(item, dict):
            continue
        role = "time_dimension" if text(item.get("type")) == "time" else "dimension"
        columns.append(
            column(
                text(item.get("name")),
                expr=text(item.get("expr")) or text(item.get("name")),
                description=description_of(item),
                synonyms=[],
                data_type=text(item.get("type")),
                role=role,
            )
        )
    if not keys:
        keys = key_columns(semantic.get("primary_key"))
    model["entities"].append(
        entity(
            name,
            table,
            description=description_of(semantic),
            primary_key=keys,
            columns=columns,
        )
    )
    for item in rows(semantic.get("measures")):
        if not isinstance(item, dict):
            continue
        measure_name = text(item.get("name"))
        expr = _agg(item)
        reason = unsupported_reason(expr)
        if reason:
            model["unsupported"].append(
                {"name": measure_name, "reason": reason, "path": f"semantic_models.{name}.measures"}
            )
            continue
        known[measure_name] = (expr, name)
        model["measures"].append(
            {
                "name": measure_name,
                "description": description_of(item),
                "expr": expr,
                "synonyms": [],
                "table": name,
            }
        )


def _agg(item: Mapping[str, Any]) -> str:
    expr = text(item.get("expr")) or text(item.get("name"))
    agg = text(item.get("agg"))
    if agg and expr:
        return f"{agg}({expr})"
    return expr


def _metric(
    model: dict[str, Any], metric: Mapping[str, Any], known: dict[str, tuple[str, str]]
) -> None:
    name = text(metric.get("name"))
    kind = text(metric.get("type")) or "simple"
    raw_params = metric.get("type_params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    pointed = text(params.get("measure"))
    expr = ""
    table = ""
    if pointed and pointed in known:
        expr, table = known[pointed]
    elif text(params.get("expr")):
        expr = text(params.get("expr"))
    if not expr or kind not in {"simple", "derived"}:
        if not expr or kind != "simple":
            model["unsupported"].append(
                {"name": name, "reason": f"dbt_metric:{kind}", "path": "metrics"}
            )
            return
    reason = unsupported_reason(expr)
    if reason:
        model["unsupported"].append({"name": name, "reason": reason, "path": f"metrics.{name}"})
        return
    model["measures"].append(
        {
            "name": name,
            "description": description_of(metric),
            "expr": expr,
            "synonyms": [],
            "table": table,
        }
    )


def _saved(model: dict[str, Any], saved: Mapping[str, Any]) -> None:
    name = text(saved.get("name"))
    sql = text(saved.get("sql") or saved.get("query"))
    if not sql:
        model["unsupported"].append(
            {"name": name, "reason": "saved_query_without_sql", "path": "saved_queries"}
        )
        return
    ask = text(saved.get("description")) or name
    model["verified_queries"].append(
        {"name": name, "question": ask, "sql": sql, "onboarding": False}
    )
