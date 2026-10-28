# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve semantic FK targets after a column has been flagged as a key.

Upstream searched a vector index, then asked the LLM to pick a hit, then
probed the warehouse when the LLM abstained. The vector index, the embed
client, and the connector registry are not vendored. Candidates here are
the catalog's primary-key columns. The LLM pick and the sample-membership
SQL are the upstream steps.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlglot import exp

from ao_vendor.llm.messages import HumanMessage, SystemMessage
from ao_vendor.llm.ov_client import get_llm_client, invoke_with_structured_output
from ao_vendor.semantic.models import FkHitSelection, SemanticFkJoinDefaultSelection
from ao_vendor.utils.sample_values import stringify_sample_values

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a database schema expert. You will be given a foreign-key column \
description and a list of candidate primary-key columns retrieved from a \
vector search. Each candidate is prefixed with its unique column_id. \
Your task is to decide which candidate (if any) is the column that the \
foreign key references.

Rules:
- Return the exact column_id of the best matching candidate, or null if none fit.
- Only pick a candidate when you are confident it is the PK being referenced.
- Do NOT guess. If unsure, return null.
"""

_JOIN_DEFAULT_SYSTEM_PROMPT = """\
You are choosing the default foreign-key relationship used to join two database
tables. Several columns on the same source table reference the same target column,
but each column can represent a different business role.

Choose the relationship that most naturally represents an unqualified join between
the source and target tables. Return exactly one supplied source_column_id. Return
null only when the schema context is genuinely insufficient. Never invent an ID.
"""


def resolve_inferred_fks(
    suggestions: list[dict[str, Any]],
    pk_columns: list[dict[str, Any]],
    conn: Any = None,
) -> list[dict[str, Any]]:
    """Pick a target PK for each suggested FK. Nothing is written.

    ``suggestions`` rows carry ``table_name``, ``column_name``, and ``rationale``.
    ``pk_columns`` rows carry ``table_name`` and ``column_name``.
    """
    edges: list[dict[str, Any]] = []
    for suggestion in suggestions:
        source_table = str(suggestion.get("table_name") or "")
        source_column = str(suggestion.get("column_name") or "")
        if not source_table or not source_column:
            continue
        hits = _pk_hits(pk_columns, source_table=source_table)
        if not hits:
            continue
        column = {
            "name": source_column,
            "table_name": source_table,
            "description": suggestion.get("description") or "",
            "sample_values": suggestion.get("sample_values"),
        }
        selected = _llm_pick_hit(column, hits)
        if not selected and conn is not None:
            selected = _match_hit_by_samples(column, hits, conn)
        if not selected:
            continue
        target = _hit_target(hits, selected)
        if target is None:
            continue
        edges.append(
            {
                "source_table": source_table,
                "source_column": source_column,
                "target_table": target["table_name"],
                "target_column": target["column_name"],
                "column_id": selected,
                "rationale": str(suggestion.get("rationale") or ""),
            }
        )
    _apply_join_defaults(edges)
    return edges


def _pk_hits(
    pk_columns: list[dict[str, Any]],
    *,
    source_table: str,
) -> list[dict[str, Any]]:
    """Catalog PK columns on other tables, shaped like the upstream hit list."""
    hits: list[dict[str, Any]] = []
    for column in pk_columns:
        table_name = str(column.get("table_name") or "")
        column_name = str(column.get("column_name") or "")
        if not table_name or not column_name or table_name == source_table:
            continue
        column_id = f"{table_name}.{column_name}"
        hits.append(
            {
                "text": f"{table_name}.{column_name}",
                "metadata": {
                    "id": column_id,
                    "table_name": table_name,
                    "source_column": column_name,
                    "schema_name": column.get("table_schema") or "main",
                },
                "_distance": 1.0,
            }
        )
    return hits


def _hit_target(hits: list[dict[str, Any]], column_id: str) -> dict[str, str] | None:
    for hit in hits:
        metadata = hit.get("metadata") or {}
        if metadata.get("id") == column_id:
            return {
                "table_name": str(metadata.get("table_name") or ""),
                "column_name": str(metadata.get("source_column") or ""),
            }
    return None


def _format_sample_values(raw: Any) -> str:
    values = stringify_sample_values(raw, max_len=30)
    if not values:
        return ""
    return "sample_values: " + ", ".join(values)


def _llm_pick_hit(col: dict[str, Any], hits: list[dict[str, Any]]) -> str | None:
    """Ask which candidate column the foreign key references."""
    col_ctx = (
        f"Foreign-key column: {col.get('name', '')} "
        f"(table: {col.get('table_name', '')})\n"
        f"Description: {col.get('description') or '(none)'}\n"
        f"Sample values: {_format_sample_values(col.get('sample_values')) or '(none)'}"
    )
    lines = []
    for hit in hits:
        column_id = (hit.get("metadata") or {}).get("id", "")
        text = (hit.get("text") or "")[:300]
        lines.append(f"column_id={column_id} | {text}")
    human_text = (
        f"{col_ctx}\n\n"
        f"Candidate PK columns (from vector search):\n" + "\n".join(lines) + "\n\n"
        "Which candidate does this FK column reference? "
        "Return its exact column_id value, or null if none are a confident match."
    )
    result = invoke_with_structured_output(
        get_llm_client(max_tokens=4096),
        [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=human_text)],
        FkHitSelection,
    )
    if result is None or not result.selected_id:
        return None
    valid_ids = {(hit.get("metadata") or {}).get("id") for hit in hits}
    if result.selected_id not in valid_ids:
        logger.warning(
            "resolve_semantic_fks: LLM returned unknown id %r for %s.%s",
            result.selected_id,
            col.get("table_name"),
            col.get("name"),
        )
        return None
    return result.selected_id


def _distinct_samples(raw: Any) -> list[str]:
    values = stringify_sample_values(raw) or []
    return list(dict.fromkeys(values))


def _sample_match_sql(metadata: dict[str, Any], samples: list[str]) -> str | None:
    """Quoted, bounded query for candidate sample membership. Dialect is duckdb."""
    table_name = metadata.get("table_name")
    column_name = metadata.get("source_column")
    if not table_name or not column_name or not samples:
        return None
    schema_name = metadata.get("schema_name")
    table = exp.Table(
        this=exp.to_identifier(str(table_name)),
        db=exp.to_identifier(str(schema_name)) if schema_name else None,
    )
    column = exp.column(str(column_name))
    text_column = exp.cast(column, "TEXT")
    query = (
        exp.select(text_column.copy().as_("matched_value"))
        .distinct()
        .from_(table)
        .where(text_column.copy().isin(*(exp.Literal.string(value) for value in samples)))
        .limit(len(samples))
    )
    try:
        return query.sql(dialect="duckdb", identify=True)
    except ValueError:
        return query.sql(identify=True)


def _match_hit_by_samples(
    col: dict[str, Any],
    hits: list[dict[str, Any]],
    conn: Any,
) -> str | None:
    """When the LLM abstains, keep a same-named PK whose values contain the samples."""
    samples = _distinct_samples(col.get("sample_values"))
    if not samples:
        return None
    source_column = str(col.get("name") or "").lower()
    matching = [
        hit
        for hit in hits
        if str((hit.get("metadata") or {}).get("source_column") or "").lower() == source_column
    ]
    for hit in matching:
        sql = _sample_match_sql(hit.get("metadata") or {}, samples)
        if not sql:
            continue
        try:
            rows = conn.execute(sql).fetchall()
        except Exception:
            logger.warning("semantic FK sample probe failed", exc_info=True)
            continue
        found = {str(row[0]) for row in rows if row and row[0] is not None}
        if set(samples).issubset(found):
            selected = (hit.get("metadata") or {}).get("id")
            if selected:
                return str(selected)
    return None


def _apply_join_defaults(edges: list[dict[str, Any]]) -> None:
    """Mark one edge in a same-target group as the join default. Does not persist."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for edge in edges:
        key = (edge["source_table"], edge["target_table"], edge["target_column"])
        groups.setdefault(key, []).append(edge)
    for group in groups.values():
        for edge in group:
            edge["join_default"] = False
        chosen = group[0]
        if len(group) > 1:
            picked = _select_join_default_with_llm(group)
            if picked is None:
                picked = _deterministic_join_default(group)
            chosen = next(
                (edge for edge in group if edge["source_column"] == picked),
                group[0],
            )
        chosen["join_default"] = True


def _select_join_default_with_llm(group: list[dict[str, Any]]) -> str | None:
    first = group[0]
    candidates = "\n".join(
        (
            f"- source_column_id: {row['source_column']}\n"
            f"  column: {row['source_column']}\n"
            f"  description: {row.get('rationale') or '(none)'}"
        )
        for row in group
    )
    prompt = (
        f"Source table: {first['source_table']}\n"
        f"Target table: {first['target_table']}\n"
        f"Target column: {first['target_column']}\n\n"
        f"Candidate relationships:\n{candidates}\n"
    )
    result = invoke_with_structured_output(
        get_llm_client(),
        [
            SystemMessage(content=_JOIN_DEFAULT_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ],
        SemanticFkJoinDefaultSelection,
    )
    if result is None or not result.selected_column_id:
        return None
    valid = {row["source_column"] for row in group}
    if result.selected_column_id not in valid:
        return None
    return result.selected_column_id


def _deterministic_join_default(group: list[dict[str, Any]]) -> str:
    """Stable fallback when the LLM cannot decide. Lowest column name wins."""
    return min(
        group,
        key=lambda row: (str(row.get("source_column") or "").casefold(), str(row["source_column"])),
    )["source_column"]
