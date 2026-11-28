"""Snowflake Cortex Analyst semantic-model YAML. Keys only."""

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
    key_columns,
    qualified,
    rows,
    synonyms_of,
    text,
)
from dms_executor.semantic_import.sql_shape import unsupported_reason


def parse_snowflake(doc: Mapping[str, Any]) -> dict[str, Any]:
    model = blank("snowflake")
    model["name"] = text(doc.get("name"))
    model["description"] = text(doc.get("description"))
    model["instructions"] = instructions_of(doc.get("module_custom_instructions"))
    for table in rows(doc.get("tables")):
        if isinstance(table, dict):
            _table(model, table)
    for rel in rows(doc.get("relationships")):
        if isinstance(rel, dict):
            built = _relationship(rel)
            if built is not None:
                model["relationships"].append(built)
    for query in rows(doc.get("verified_queries")):
        if isinstance(query, dict):
            _verified(model, query)
    for metric in rows(doc.get("metrics")):
        if isinstance(metric, dict):
            _metric(model, metric, "", "metrics")
    return model


def _table(model: dict[str, Any], table: Mapping[str, Any]) -> None:
    name = text(table.get("name"))
    raw_base = table.get("base_table")
    base: dict[str, Any] = raw_base if isinstance(raw_base, dict) else {}
    physical = qualified([base.get("database"), base.get("schema"), base.get("table")]) or name
    columns: list[dict[str, Any]] = []
    for role, field in (
        ("dimension", "dimensions"),
        ("time_dimension", "time_dimensions"),
        ("fact", "facts"),
    ):
        for item in rows(table.get(field)):
            if not isinstance(item, dict):
                continue
            col_name = text(item.get("name"))
            if "cortex_search_service" in item:
                model["unsupported"].append(
                    {
                        "name": col_name,
                        "reason": "cortex_search_service",
                        "path": f"tables.{name}.{field}.{col_name}",
                    }
                )
            columns.append(
                column(
                    col_name,
                    expr=expr_of(item),
                    description=description_of(item),
                    synonyms=synonyms_of(item),
                    data_type=text(item.get("data_type")),
                    role=role,
                )
            )
    model["entities"].append(
        entity(
            name,
            physical,
            description=description_of(table),
            primary_key=key_columns(table.get("primary_key")),
            columns=columns,
        )
    )
    for metric in rows(table.get("metrics")):
        if isinstance(metric, dict):
            _metric(model, metric, name, f"tables.{name}.metrics")
    for item in rows(table.get("filters")):
        if isinstance(item, dict):
            model["filters"].append(
                {
                    "name": text(item.get("name")),
                    "description": description_of(item),
                    "expr": expr_of(item),
                    "synonyms": synonyms_of(item),
                    "table": name,
                }
            )


def _metric(model: dict[str, Any], metric: Mapping[str, Any], table: str, path: str) -> None:
    name = text(metric.get("name"))
    expr = expr_of(metric)
    reason = unsupported_reason(expr)
    if reason:
        model["unsupported"].append({"name": name, "reason": reason, "path": f"{path}.{name}"})
        return
    model["measures"].append(
        {
            "name": name,
            "description": description_of(metric),
            "expr": expr,
            "synonyms": synonyms_of(metric),
            "table": table,
        }
    )


def _relationship(rel: Mapping[str, Any]) -> dict[str, Any] | None:
    pairs = [pair for pair in rows(rel.get("relationship_columns")) if isinstance(pair, dict)]
    left = [text(pair.get("left_column")) for pair in pairs if text(pair.get("left_column"))]
    right = [text(pair.get("right_column")) for pair in pairs if text(pair.get("right_column"))]
    if not left or not right:
        return None
    cardinality = text(rel.get("relationship_type")) or "many_to_one"
    return {
        "name": text(rel.get("name")),
        "from_table": text(rel.get("left_table")),
        "from_columns": left,
        "to_table": text(rel.get("right_table")),
        "to_columns": right,
        "cardinality": cardinality,
        "join_type": text(rel.get("join_type")),
    }


def _verified(model: dict[str, Any], query: Mapping[str, Any]) -> None:
    ask = text(query.get("question"))
    name = text(query.get("name")) or ask
    onboarding = bool(query.get("use_as_onboarding_question"))
    model["verified_queries"].append(
        {"name": name, "question": ask, "sql": text(query.get("sql")), "onboarding": onboarding}
    )
    if onboarding and ask:
        model["onboarding"].append(ask)
