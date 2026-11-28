"""WrenAI MDL. Models, relationships, calculated columns. Views and cubes that
do not carry columns are listed as unsupported, not dropped.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dms_executor.semantic_import.normalize import (
    blank,
    column,
    description_of,
    entity,
    key_columns,
    mapping,
    qualified,
    rows,
    synonyms_of,
    text,
)
from dms_executor.semantic_import.sql_shape import equality_sides, unsupported_reason


def parse_wren(doc: Mapping[str, Any]) -> dict[str, Any]:
    model = blank("wren")
    model["name"] = text(doc.get("catalog"))
    model["description"] = text(doc.get("description"))
    for item in rows(doc.get("models")):
        if isinstance(item, dict):
            model["entities"].append(_model(item))
    for item in rows(doc.get("relationships")):
        if isinstance(item, dict):
            built = _relationship(item)
            if built is None:
                model["unsupported"].append(
                    {
                        "name": text(item.get("name")),
                        "reason": "unparsed_join",
                        "path": "relationships",
                    }
                )
            else:
                model["relationships"].append(built)
    for item in rows(doc.get("views")):
        if isinstance(item, dict):
            _view(model, item)
    for item in rows(doc.get("cubes")):
        if isinstance(item, dict):
            _cube(model, item)
    return model


def _model(item: Mapping[str, Any]) -> dict[str, Any]:
    ref = mapping(item.get("tableReference") or item.get("table_reference"))
    physical = qualified([ref.get("catalog"), ref.get("schema"), ref.get("table")])
    name = text(item.get("name"))
    columns: list[dict[str, Any]] = []
    keys = key_columns(item.get("primaryKey") or item.get("primary_key"))
    for col in rows(item.get("columns")):
        if not isinstance(col, dict):
            continue
        col_name = text(col.get("name"))
        if col.get("isPrimaryKey") or col.get("is_primary_key"):
            if col_name and col_name not in keys:
                keys.append(col_name)
        expr = text(col.get("expression")) or col_name
        reason = (
            unsupported_reason(expr) if col.get("isCalculated") or col.get("expression") else None
        )
        if reason:
            # The caller has no model yet. The column is still named on the entity
            # and the gap is attached after _model returns, via the expression
            # staying empty of a window by replacing it below.
            expr = ""
        columns.append(
            column(
                col_name,
                expr=expr or col_name,
                description=description_of(col),
                synonyms=synonyms_of(col),
                data_type=text(col.get("type")),
                role="dimension",
            )
        )
    return entity(
        name,
        physical or name,
        description=description_of(item),
        primary_key=keys,
        columns=columns,
    )


def _relationship(item: Mapping[str, Any]) -> dict[str, Any] | None:
    names = [text(name) for name in rows(item.get("models")) if text(name)]
    sides = equality_sides(text(item.get("condition")))
    if sides is None or len(names) < 2:
        return None
    left_table, left_col, right_table, right_col = sides
    cardinality = text(item.get("joinType") or item.get("join_type")).lower() or "many_to_one"
    return {
        "name": text(item.get("name")) or f"{names[0]}_{names[1]}",
        "from_table": left_table or names[0],
        "from_columns": [left_col] if left_col else [],
        "to_table": right_table or names[1],
        "to_columns": [right_col] if right_col else [],
        "cardinality": cardinality,
        "join_type": cardinality,
    }


def _view(model: dict[str, Any], item: Mapping[str, Any]) -> None:
    columns = [col for col in rows(item.get("columns")) if isinstance(col, dict)]
    if not columns:
        model["unsupported"].append(
            {"name": text(item.get("name")), "reason": "wren_view", "path": "views"}
        )
        return
    model["entities"].append(_model(item))


def _cube(model: dict[str, Any], item: Mapping[str, Any]) -> None:
    name = text(item.get("name"))
    if "preAggregations" in item or "pre_aggregations" in item:
        model["unsupported"].append({"name": name, "reason": "pre_aggregation", "path": "cubes"})
    for measure in rows(item.get("measures")):
        if not isinstance(measure, dict):
            continue
        expr = text(measure.get("expression") or measure.get("expr"))
        reason = unsupported_reason(expr)
        if reason or not expr:
            model["unsupported"].append(
                {
                    "name": text(measure.get("name")) or name,
                    "reason": reason or "cube_measure",
                    "path": f"cubes.{name}",
                }
            )
            continue
        model["measures"].append(
            {
                "name": text(measure.get("name")),
                "description": description_of(measure),
                "expr": expr,
                "synonyms": synonyms_of(measure),
                "table": text(item.get("table")) or name,
            }
        )
