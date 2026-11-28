"""Databricks Genie serialized_space, version 2 keys."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dms_executor.semantic_import.normalize import (
    blank,
    column,
    description_of,
    entity,
    expr_of,
    instructions_of,
    mapping,
    rows,
    string_list,
    synonyms_of,
    text,
)
from dms_executor.semantic_import.sql_shape import is_query, unsupported_reason


def parse_genie(doc: Mapping[str, Any]) -> dict[str, Any]:
    body = mapping(doc.get("serialized_space")) or doc
    model = blank("genie")
    model["name"] = text(body.get("name"))
    model["description"] = text(body.get("description"))
    model["instructions"] = instructions_of(body.get("text_instructions"))
    model["onboarding"] = string_list(body.get("sample_questions"))
    for table in rows(body.get("tables")):
        if isinstance(table, dict):
            model["entities"].append(_table(table))
    for metric in rows(body.get("metric_views")):
        if isinstance(metric, dict):
            _metric(model, metric)
    for query in rows(body.get("example_question_sqls")):
        if isinstance(query, dict):
            ask = text(query.get("question"))
            model["verified_queries"].append(
                {
                    "name": text(query.get("name")) or ask,
                    "question": ask,
                    "sql": text(query.get("sql")),
                    "onboarding": False,
                }
            )
    for spec in rows(body.get("join_specs")):
        if isinstance(spec, dict):
            built = _join(spec)
            if built is not None:
                model["relationships"].append(built)
    for snippet in rows(body.get("sql_snippets")):
        if isinstance(snippet, dict):
            _snippet(model, snippet)
    for bench in rows(body.get("benchmarks")):
        if isinstance(bench, dict):
            model["benchmarks"].append(
                {
                    "name": text(bench.get("name")),
                    "question": text(bench.get("question")),
                    "sql": text(bench.get("sql")),
                }
            )
    return model


def _table(table: Mapping[str, Any]) -> dict[str, Any]:
    physical = text(table.get("identifier") or table.get("name"))
    name = physical.split(".")[-1] if physical else ""
    columns = []
    raw_columns = rows(table.get("columns")) or rows(table.get("column_configs"))
    keys: list[str] = []
    for item in raw_columns:
        if not isinstance(item, dict):
            continue
        col_name = text(item.get("name") or item.get("column_name"))
        if item.get("is_primary_key") or item.get("isPrimaryKey"):
            keys.append(col_name)
        columns.append(
            column(
                col_name,
                expr=expr_of(item) or col_name,
                description=description_of(item),
                synonyms=synonyms_of(item),
                data_type=text(item.get("type") or item.get("data_type")),
                role="dimension",
            )
        )
    return entity(
        name,
        physical or name,
        description=description_of(table),
        primary_key=keys,
        columns=columns,
    )


def _metric(model: dict[str, Any], metric: Mapping[str, Any]) -> None:
    name = text(metric.get("name"))
    expr = expr_of(metric)
    reason = unsupported_reason(expr)
    if reason:
        model["unsupported"].append(
            {"name": name, "reason": reason, "path": f"metric_views.{name}"}
        )
        return
    model["measures"].append(
        {
            "name": name,
            "description": description_of(metric),
            "expr": expr,
            "synonyms": synonyms_of(metric),
            "table": text(metric.get("table")),
        }
    )


def _side(node: Any) -> tuple[str, list[str]]:
    if isinstance(node, str):
        return node, []
    body = mapping(node)
    ident = text(body.get("identifier") or body.get("table") or body.get("name"))
    cols = string_list(body.get("columns"))
    one = text(body.get("column"))
    if one:
        cols = [one]
    return ident, cols


def _join(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    if text(spec.get("left_table")) and text(spec.get("right_table")):
        left, left_cols = text(spec.get("left_table")), string_list(spec.get("left_columns"))
        right, right_cols = text(spec.get("right_table")), string_list(spec.get("right_columns"))
    else:
        left, left_cols = _side(spec.get("left"))
        right, right_cols = _side(spec.get("right"))
    if not left or not right or not left_cols or not right_cols:
        return None
    cardinality = text(spec.get("cardinality") or spec.get("relationship_type")) or "many_to_one"
    return {
        "name": text(spec.get("name")) or f"{left}_{right}",
        "from_table": left.split(".")[-1],
        "from_columns": left_cols,
        "to_table": right.split(".")[-1],
        "to_columns": right_cols,
        "cardinality": cardinality,
        "join_type": text(spec.get("join_type") or spec.get("type")),
    }


def _snippet(model: dict[str, Any], snippet: Mapping[str, Any]) -> None:
    name = text(snippet.get("name"))
    expr = text(snippet.get("sql") or snippet.get("expression"))
    if not expr:
        model["unsupported"].append(
            {"name": name, "reason": "unparsed_snippet", "path": "sql_snippets"}
        )
        return
    reason = unsupported_reason(expr)
    if reason:
        model["unsupported"].append(
            {"name": name, "reason": reason, "path": f"sql_snippets.{name}"}
        )
        return
    if is_query(expr):
        model["verified_queries"].append(
            {"name": name, "question": name, "sql": expr, "onboarding": False}
        )
        return
    model["filters"].append(
        {
            "name": name,
            "description": description_of(snippet),
            "expr": expr,
            "synonyms": synonyms_of(snippet),
            "table": text(snippet.get("table")),
        }
    )
