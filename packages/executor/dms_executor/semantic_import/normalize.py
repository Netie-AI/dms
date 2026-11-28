"""One internal shape for a semantic-model file.

Parsers copy documented keys into this shape. They do not read question
text and they do not decide grants. ``canonical`` is the equality form
used by the golden files and the OSI round trip.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import yaml

FORMATS = ("snowflake", "osi", "genie", "dbt", "wren")


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def rows(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def blank(fmt: str) -> dict[str, Any]:
    return {
        "format": fmt,
        "name": "",
        "description": "",
        "instructions": [],
        "entities": [],
        "relationships": [],
        "measures": [],
        "filters": [],
        "verified_queries": [],
        "onboarding": [],
        "benchmarks": [],
        "unsupported": [],
    }


def synonyms_of(node: Mapping[str, Any]) -> list[str]:
    found = string_list(node.get("synonyms"))
    if found:
        return found
    ctx = mapping(node.get("ai_context"))
    found = string_list(ctx.get("synonyms"))
    if found:
        return found
    props = mapping(node.get("properties"))
    return string_list(props.get("synonyms"))


def description_of(node: Mapping[str, Any]) -> str:
    raw = text(node.get("description"))
    if raw:
        return raw
    return text(mapping(node.get("properties")).get("description"))


def instructions_of(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, dict):
        out: list[str] = []
        for key in ("instructions", "sql_generation", "question_categorization"):
            out.extend(instructions_of(value.get(key)))
        return out
    return []


def expr_of(node: Mapping[str, Any]) -> str:
    """SQL text from ``expr``, ``sql``, or an expression object. No rewrite."""
    for key in ("expr", "expression", "sql"):
        raw = node.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            sql = raw.get("sql")
            if isinstance(sql, str) and sql.strip():
                return sql.strip()
            for item in rows(raw.get("dialects")):
                if isinstance(item, dict):
                    part = item.get("expression")
                    if isinstance(part, str) and part.strip():
                        return part.strip()
    return ""


def column(
    name: str,
    *,
    expr: str,
    description: str,
    synonyms: list[str],
    data_type: str,
    role: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "expr": expr or name,
        "description": description,
        "synonyms": list(synonyms),
        "data_type": data_type,
        "role": role,
    }


def entity(
    name: str,
    table: str,
    *,
    description: str,
    primary_key: list[str],
    columns: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "name": name,
        "table": table or name,
        "description": description,
        "primary_key": list(primary_key),
        "columns": columns,
    }


def key_columns(value: Any) -> list[str]:
    """A primary key as a list of names. A list of lists uses the first key."""
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, dict):
        return key_columns(value.get("columns"))
    if isinstance(value, list) and value and isinstance(value[0], list):
        return string_list(value[0])
    return string_list(value)


def qualified(parts: list[Any]) -> str:
    names = [text(part) for part in parts]
    return ".".join(part for part in names if part)


def canonical(model: Mapping[str, Any]) -> dict[str, Any]:
    """Sorted JSON shape. Two parses compare equal when their keys match."""
    out = blank(text(model.get("format")))
    out["name"] = text(model.get("name"))
    out["description"] = text(model.get("description"))
    out["instructions"] = sorted(string_list(model.get("instructions")))
    out["onboarding"] = sorted(string_list(model.get("onboarding")))
    out["entities"] = [_canon_entity(row) for row in _sorted(rows(model.get("entities")))]
    out["relationships"] = [_canon_rel(row) for row in _sorted(rows(model.get("relationships")))]
    out["measures"] = [
        _canon_named(row, ("table",)) for row in _sorted(rows(model.get("measures")))
    ]
    out["filters"] = [_canon_named(row, ("table",)) for row in _sorted(rows(model.get("filters")))]
    out["verified_queries"] = [
        _canon_query(row) for row in _sorted(rows(model.get("verified_queries")))
    ]
    out["benchmarks"] = [_canon_bench(row) for row in _sorted(rows(model.get("benchmarks")))]
    out["unsupported"] = [_canon_gap(row) for row in _sorted(rows(model.get("unsupported")))]
    return json.loads(json.dumps(out, sort_keys=True))


def load_document(raw: bytes) -> dict[str, Any]:
    try:
        payload = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("import_not_utf8") from exc
    try:
        doc = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise ValueError("import_not_a_document") from exc
    if not isinstance(doc, dict):
        raise ValueError("import_not_a_document")
    return doc


def detect_format(doc: Mapping[str, Any]) -> str:
    """Pick a parser from documented keys. Empty when none of the five match."""
    body = mapping(doc.get("serialized_space")) or doc
    if "semantic_models" in body:
        return "dbt"
    if "models" in body and "datasets" not in body and "semantic_model" not in body:
        return "wren"
    tables = rows(body.get("tables"))
    first = tables[0] if tables and isinstance(tables[0], dict) else {}
    snowflake_table = any(
        key in first for key in ("base_table", "dimensions", "time_dimensions", "facts")
    )
    if snowflake_table or "verified_queries" in body or "module_custom_instructions" in body:
        return "snowflake"
    genie_keys = (
        "example_question_sqls",
        "join_specs",
        "metric_views",
        "sample_questions",
        "sql_snippets",
        "text_instructions",
        "benchmarks",
    )
    if any(key in body for key in genie_keys) or "identifier" in first:
        return "genie"
    if "semantic_model" in body or "datasets" in body:
        return "osi"
    if isinstance(doc.get("serialized_space"), dict):
        return "genie"
    return ""


def _sorted(items: list[Any]) -> list[dict[str, Any]]:
    dicts = [row for row in items if isinstance(row, dict)]
    return sorted(dicts, key=lambda row: text(row.get("name")))


def _canon_entity(row: Mapping[str, Any]) -> dict[str, Any]:
    columns = [_canon_column(col) for col in _sorted(rows(row.get("columns")))]
    return {
        "name": text(row.get("name")),
        "table": text(row.get("table")),
        "description": text(row.get("description")),
        "primary_key": sorted(string_list(row.get("primary_key"))),
        "columns": columns,
    }


def _canon_column(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": text(row.get("name")),
        "expr": text(row.get("expr")),
        "description": text(row.get("description")),
        "synonyms": sorted(string_list(row.get("synonyms"))),
        "data_type": text(row.get("data_type")),
        "role": text(row.get("role")) or "dimension",
    }


def _canon_rel(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": text(row.get("name")),
        "from_table": text(row.get("from_table")),
        "from_columns": string_list(row.get("from_columns")),
        "to_table": text(row.get("to_table")),
        "to_columns": string_list(row.get("to_columns")),
        "cardinality": text(row.get("cardinality")) or "many_to_one",
        "join_type": text(row.get("join_type")),
    }


def _canon_named(row: Mapping[str, Any], extra: tuple[str, ...]) -> dict[str, Any]:
    out = {
        "name": text(row.get("name")),
        "description": text(row.get("description")),
        "expr": text(row.get("expr")),
        "synonyms": sorted(string_list(row.get("synonyms"))),
    }
    for key in extra:
        out[key] = text(row.get(key))
    return out


def _canon_query(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": text(row.get("name")),
        "question": text(row.get("question")),
        "sql": text(row.get("sql")),
        "onboarding": bool(row.get("onboarding")),
    }


def _canon_bench(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": text(row.get("name")),
        "question": text(row.get("question")),
        "sql": text(row.get("sql")),
    }


def _canon_gap(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": text(row.get("name")),
        "reason": text(row.get("reason")),
        "path": text(row.get("path")),
    }
