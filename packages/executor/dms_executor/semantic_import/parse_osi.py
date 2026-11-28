"""OSI / Ossie 0.1.1. Reads the wrapped ``semantic_model`` and the flat document."""

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
    mapping,
    rows,
    synonyms_of,
    text,
)
from dms_executor.semantic_import.sql_shape import unsupported_reason


def parse_osi(doc: Mapping[str, Any]) -> dict[str, Any]:
    body = mapping(doc.get("semantic_model")) or doc
    model = blank("osi")
    model["name"] = text(body.get("name"))
    model["description"] = text(body.get("description"))
    model["instructions"] = instructions_of(body.get("ai_context"))
    for dataset in rows(body.get("datasets")):
        if isinstance(dataset, dict):
            _dataset(model, dataset)
    for rel in rows(body.get("relationships")):
        if isinstance(rel, dict):
            model["relationships"].append(_relationship(rel))
    for metric in rows(body.get("metrics")):
        if isinstance(metric, dict):
            _metric(model, metric, "")
    for ext in rows(body.get("custom_extensions")) + rows(body.get("nonportable_extensions")):
        if isinstance(ext, dict):
            _extension(model, mapping(ext.get("data")))
    return model


def to_osi(model: Mapping[str, Any]) -> dict[str, Any]:
    """Approved-shape export. Version is the 0.1.1 document the parser reads back."""
    by_table: dict[str, list[dict[str, Any]]] = {}
    root: list[dict[str, Any]] = []
    for metric in rows(model.get("measures")):
        if not isinstance(metric, dict):
            continue
        item = _metric_out(metric)
        table = text(metric.get("table"))
        if table:
            by_table.setdefault(table, []).append(item)
        else:
            root.append(item)
    datasets = []
    for raw in rows(model.get("entities")):
        if not isinstance(raw, dict):
            continue
        name = text(raw.get("name"))
        datasets.append(
            {
                "name": name,
                "source": text(raw.get("table")) or name,
                "description": text(raw.get("description")),
                "primary_key": list(raw.get("primary_key") or []),
                "fields": [
                    _field_out(col) for col in rows(raw.get("columns")) if isinstance(col, dict)
                ],
                "metrics": by_table.get(name, []) + by_table.get(text(raw.get("table")), []),
            }
        )
    relationships = []
    for rel in rows(model.get("relationships")):
        if not isinstance(rel, dict):
            continue
        item = {
            "name": text(rel.get("name")),
            "from": text(rel.get("from_table")),
            "to": text(rel.get("to_table")),
            "from_columns": list(rel.get("from_columns") or []),
            "to_columns": list(rel.get("to_columns") or []),
        }
        cardinality = text(rel.get("cardinality"))
        if cardinality and cardinality != "many_to_one":
            item["relationship_type"] = cardinality
        join_type = text(rel.get("join_type"))
        if join_type:
            item["join_type"] = join_type
        relationships.append(item)
    extension = {
        "filters": [
            {
                "name": text(item.get("name")),
                "description": text(item.get("description")),
                "expr": text(item.get("expr")),
                "synonyms": list(item.get("synonyms") or []),
                "table": text(item.get("table")),
            }
            for item in rows(model.get("filters"))
            if isinstance(item, dict)
        ],
        "verified_queries": [
            {
                "name": text(item.get("name")),
                "question": text(item.get("question")),
                "sql": text(item.get("sql")),
                "onboarding": bool(item.get("onboarding")),
            }
            for item in rows(model.get("verified_queries"))
            if isinstance(item, dict)
        ],
        "onboarding": [text(item) for item in rows(model.get("onboarding")) if text(item)],
        "benchmarks": [
            {
                "name": text(item.get("name")),
                "question": text(item.get("question")),
                "sql": text(item.get("sql")),
            }
            for item in rows(model.get("benchmarks"))
            if isinstance(item, dict)
        ],
        "unsupported": [
            {
                "name": text(item.get("name")),
                "reason": text(item.get("reason")),
                "path": text(item.get("path")),
            }
            for item in rows(model.get("unsupported"))
            if isinstance(item, dict)
        ],
    }
    return {
        "version": "0.1.1",
        "semantic_model": {
            "name": text(model.get("name")),
            "description": text(model.get("description")),
            "ai_context": {"instructions": list(model.get("instructions") or [])},
            "datasets": datasets,
            "relationships": relationships,
            "metrics": root,
            "custom_extensions": [{"vendor_name": "DMS", "data": extension}],
        },
    }


def _dataset(model: dict[str, Any], dataset: Mapping[str, Any]) -> None:
    name = text(dataset.get("name"))
    columns: list[dict[str, Any]] = []
    for field in rows(dataset.get("fields")):
        if not isinstance(field, dict):
            continue
        label = text(field.get("label")).lower()
        if label == "filter":
            model["filters"].append(_filter(field, name))
            continue
        role = "dimension"
        if label == "fact":
            role = "fact"
        dim = mapping(field.get("dimension"))
        if dim.get("is_time") is True:
            role = "time_dimension"
        columns.append(
            column(
                text(field.get("name")),
                expr=expr_of(field),
                description=description_of(field),
                synonyms=synonyms_of(field),
                data_type=text(field.get("datatype") or field.get("data_type")),
                role=role,
            )
        )
    model["entities"].append(
        entity(
            name,
            text(dataset.get("source")) or name,
            description=description_of(dataset),
            primary_key=key_columns(dataset.get("primary_key")),
            columns=columns,
        )
    )
    for metric in rows(dataset.get("metrics")):
        if isinstance(metric, dict):
            _metric(model, metric, name)


def _metric(model: dict[str, Any], metric: Mapping[str, Any], table: str) -> None:
    name = text(metric.get("name"))
    expr = expr_of(metric)
    reason = unsupported_reason(expr)
    if reason:
        model["unsupported"].append({"name": name, "reason": reason, "path": f"metrics.{name}"})
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


def _filter(field: Mapping[str, Any], table: str) -> dict[str, Any]:
    return {
        "name": text(field.get("name")),
        "description": description_of(field),
        "expr": expr_of(field),
        "synonyms": synonyms_of(field),
        "table": table,
    }


def _relationship(rel: Mapping[str, Any]) -> dict[str, Any]:
    cardinality = text(rel.get("relationship_type") or rel.get("cardinality")) or "many_to_one"
    return {
        "name": text(rel.get("name")),
        "from_table": text(rel.get("from")),
        "from_columns": [item for item in rows(rel.get("from_columns")) if isinstance(item, str)],
        "to_table": text(rel.get("to")),
        "to_columns": [item for item in rows(rel.get("to_columns")) if isinstance(item, str)],
        "cardinality": cardinality,
        "join_type": text(rel.get("join_type")),
    }


def _extension(model: dict[str, Any], data: Mapping[str, Any]) -> None:
    for item in rows(data.get("filters")):
        if isinstance(item, dict):
            model["filters"].append(
                {
                    "name": text(item.get("name")),
                    "description": text(item.get("description")),
                    "expr": text(item.get("expr") or item.get("sql")),
                    "synonyms": list(item.get("synonyms") or []),
                    "table": text(item.get("table")),
                }
            )
    for item in rows(data.get("verified_queries")):
        if isinstance(item, dict):
            model["verified_queries"].append(
                {
                    "name": text(item.get("name")),
                    "question": text(item.get("question")),
                    "sql": text(item.get("sql")),
                    "onboarding": bool(item.get("onboarding")),
                }
            )
    for item in rows(data.get("onboarding")):
        if isinstance(item, str) and item.strip():
            model["onboarding"].append(item.strip())
    for item in rows(data.get("benchmarks")):
        if isinstance(item, dict):
            model["benchmarks"].append(
                {
                    "name": text(item.get("name")),
                    "question": text(item.get("question")),
                    "sql": text(item.get("sql")),
                }
            )
    for item in rows(data.get("unsupported")):
        if isinstance(item, dict):
            model["unsupported"].append(
                {
                    "name": text(item.get("name")),
                    "reason": text(item.get("reason")),
                    "path": text(item.get("path")),
                }
            )


def _field_out(col: Mapping[str, Any]) -> dict[str, Any]:
    role = text(col.get("role")) or "dimension"
    field: dict[str, Any] = {
        "name": text(col.get("name")),
        "description": text(col.get("description")),
        "expression": {"sql": text(col.get("expr")) or text(col.get("name"))},
    }
    if text(col.get("data_type")):
        field["datatype"] = text(col.get("data_type"))
    if synonyms_of(col):
        field["ai_context"] = {"synonyms": list(col.get("synonyms") or [])}
    if role == "time_dimension":
        field["dimension"] = {"is_time": True}
    elif role == "fact":
        field["label"] = "fact"
    else:
        field["dimension"] = {"is_time": False}
    return field


def _metric_out(metric: Mapping[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": text(metric.get("name")),
        "description": text(metric.get("description")),
        "expression": {"sql": text(metric.get("expr"))},
    }
    synonyms = [item for item in rows(metric.get("synonyms")) if isinstance(item, str)]
    if synonyms:
        item["ai_context"] = {"synonyms": synonyms}
    return item
