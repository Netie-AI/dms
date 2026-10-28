# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-table taxonomy compilation: FK detection, Term, ColumnAttributes.

Upstream wrote the graph, embedded terms, and profiled with pandas through
a connector registry. This copy keeps the same order of steps and returns
the terms, attributes, and FK suggestions in memory. Sampling uses the
caller connection. A sample failure skips that column and continues.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ao_vendor.semantic.date_format import infer_date_format, is_date_type
from ao_vendor.semantic.deterministic import column_attribute_specs
from ao_vendor.semantic.domain import DomainSummary
from ao_vendor.semantic.fk_suggester import suggest_potential_foreign_keys
from ao_vendor.semantic.models import ColumnAttributeSpec
from ao_vendor.semantic.term_extractor import apply_display_names_to_specs, extract_term
from ao_vendor.utils.sql_identifiers import qualified_name, quoted_identifier

logger = logging.getLogger(__name__)

# Bounded sample. Upstream kept 1000 rows and a low-cardinality DISTINCT pass.
# ponytail: 1000-row cap, no JSONB unit walk. Upgrade: port calculate_columns_profiling.
_SAMPLE_LIMIT = 1000


@dataclass
class CompiledTable:
    """In-memory result of one table. Nothing here is served."""

    term_names: list[str] = field(default_factory=list)
    attr_names: list[str] = field(default_factory=list)
    terms: list[dict[str, Any]] = field(default_factory=list)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    fk_suggestions: list[dict[str, Any]] = field(default_factory=list)
    is_junction: bool = False
    junction_rationale: str = ""


def _profile(
    conn: Any,
    table: dict[str, Any],
    columns: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    schema = str(table.get("schema_name") or "main")
    table_name = str(table["name"])
    relation = qualified_name(schema, table_name, dialect="duckdb")
    profiling: dict[str, dict[str, Any]] = {}
    for column in columns:
        name = column.get("name")
        if not name:
            continue
        quoted = quoted_identifier(str(name), dialect="duckdb")
        try:
            sample_rows = conn.execute(
                f"SELECT DISTINCT {quoted} FROM {relation} "
                f"WHERE {quoted} IS NOT NULL LIMIT {_SAMPLE_LIMIT}"
            ).fetchall()
            counts = conn.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT {quoted}) FROM {relation}"
            ).fetchone()
        except Exception:
            logger.warning("column sample failed for %s.%s", table_name, name, exc_info=True)
            continue
        values = [row[0] for row in sample_rows if row and row[0] is not None]
        total = int(counts[0]) if counts else 0
        distinct = int(counts[1]) if counts else 0
        entry: dict[str, Any] = {
            "sample_values": values,
            "is_unique": bool(total) and total == distinct,
        }
        if is_date_type(column.get("data_type")):
            notation = infer_date_format(values)
            if notation:
                entry["format"] = notation
        profiling[str(name)] = entry
    return profiling


def process_table(
    table: dict[str, Any],
    ctx: dict[str, Any],
    *,
    conn: Any = None,
    domain_summary: DomainSummary | None = None,
) -> CompiledTable:
    """Build a proposed Term and its attributes for one physical table."""
    table_name = str(table["name"])
    columns = list(ctx.get("columns") or [])
    profiling: dict[str, dict[str, Any]] = {}
    if conn is not None and columns:
        profiling = _profile(conn, table, columns)

    fk_result = suggest_potential_foreign_keys(table, ctx, profiling or None)
    declared = [fk for fk in ctx.get("fks") or [] if fk.get("source_column")]
    declared_names = {str(fk["source_column"]) for fk in declared}
    suggested_names = {item.column_name for item in fk_result.suggestions}
    all_fk_names = declared_names | suggested_names
    is_junction = bool(
        len(columns) > 2 and fk_result.is_junction_table and len(all_fk_names) >= 2
    )

    specs = column_attribute_specs(
        columns,
        declared,
        suggested_fk_columns=all_fk_names,
        columns_profiling_samples=profiling or None,
    )
    compiled = CompiledTable(
        is_junction=is_junction,
        junction_rationale=fk_result.junction_table_rationale,
        fk_suggestions=[
            {
                "table_name": table_name,
                "column_name": item.column_name,
                "rationale": item.rationale,
                "sample_values": (profiling.get(item.column_name) or {}).get("sample_values"),
            }
            for item in fk_result.suggestions
        ],
    )
    if not specs:
        logger.warning("%s: no non-FK columns", table_name)
        return compiled

    term_result = extract_term(table, ctx, specs, domain_summary=domain_summary)
    apply_display_names_to_specs(term_result, specs)
    spec_by_column = {spec.source_column: spec for spec in specs}
    for term in term_result.terms:
        assignments = [item for item in term.attributes if item.source_column in spec_by_column]
        if not assignments:
            continue
        compiled.term_names.append(term.name)
        attributes = []
        for assignment in assignments:
            spec: ColumnAttributeSpec = spec_by_column[assignment.source_column]
            compiled.attr_names.append(spec.display_name or spec.name)
            attributes.append(
                {
                    "table": table_name,
                    "term": term.name,
                    "source_column": spec.source_column,
                    "display_name": spec.display_name or spec.name,
                    "datatype": spec.datatype,
                }
            )
            compiled.attributes.append(attributes[-1])
        compiled.terms.append(
            {
                "name": term.name,
                "table": table_name,
                "description": term.description,
                "synonyms": list(term.synonyms),
                "attributes": attributes,
            }
        )
    return compiled
