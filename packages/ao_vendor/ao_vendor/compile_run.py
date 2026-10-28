# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run the vendored catalog and semantic steps. Returns a proposal dict.

Called only when ``DMS_AO_COMPILE=1``. The proposal status is ``proposed``.
This module does not serve an answer and does not write a store.
"""

from __future__ import annotations

from typing import Any

from ao_vendor import VENDOR_SHA
from ao_vendor.catalog.extract import extract_duckdb
from ao_vendor.catalog.parsers.sqlglot_extractor import extract_tables_and_columns
from ao_vendor.llm.ov_client import (
    OpenVaultOntologyClient,
    bind_client,
    reset_client,
)
from ao_vendor.semantic.bridge_tables import propose_bridge_attributes
from ao_vendor.semantic.semantic_fk import resolve_inferred_fks
from ao_vendor.semantic.visit_enter import process_table
from ao_vendor.utils.sql_identifiers import quoted_identifier


def compile_catalog(conn: Any, *, transport: Any = None) -> dict[str, Any]:
    """Extract ``conn`` and propose terms, attributes, and inferred FKs."""
    client = OpenVaultOntologyClient(transport=transport)
    token = bind_client(client)
    try:
        catalog = extract_duckdb(conn)
        by_table = _group(catalog)
        terms: list[dict[str, Any]] = []
        attributes: list[dict[str, Any]] = []
        suggestions: list[dict[str, Any]] = []
        junctions: list[dict[str, Any]] = []
        for table in by_table:
            compiled = process_table(table["table"], table["ctx"], conn=conn)
            terms.extend(compiled.terms)
            attributes.extend(compiled.attributes)
            suggestions.extend(compiled.fk_suggestions)
            if compiled.is_junction:
                junctions.append(_junction_candidate(table, compiled.fk_suggestions))
        inferred = resolve_inferred_fks(suggestions, _pk_columns(catalog), conn)
        bridges = propose_bridge_attributes(junctions)
        declared = [
            {
                "source_table": fk["table_name"],
                "source_column": fk["source_column"],
                "target_table": fk["target_table"],
                "target_column": fk["target_column"],
            }
            for fk in catalog["fks"]
            if fk.get("source_column") and fk.get("target_table")
        ]
        return {
            "status": "proposed",
            "vendor_sha": VENDOR_SHA,
            "served": False,
            "database_name": catalog["database_name"],
            "terms": terms,
            "attributes": attributes,
            "inferred_fks": [
                {
                    "source_table": edge["source_table"],
                    "source_column": edge["source_column"],
                    "target_table": edge["target_table"],
                    "target_column": edge["target_column"],
                }
                for edge in inferred
            ],
            "declared_fks": declared,
            "joins": _joins_from_declared(declared),
            "bridge_attributes": bridges,
        }
    finally:
        reset_client(token)


def _group(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    pk_by_table: dict[tuple[str, str], list[str]] = {}
    for pk in catalog["pks"]:
        pk_by_table.setdefault((pk["table_schema"], pk["table_name"]), []).append(pk["column_name"])
    fk_by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for fk in catalog["fks"]:
        fk_by_table.setdefault((fk["table_schema"], fk["table_name"]), []).append(fk)
    columns_by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for column in catalog["columns"]:
        key = (str(column["table_schema"]), str(column["table_name"]))
        columns_by_table.setdefault(key, []).append(
            {
                "name": column["column_name"],
                "data_type": column["data_type"] or "",
                "description": column.get("description") or "",
            }
        )
    grouped = []
    for table in catalog["tables"]:
        key = (str(table["table_schema"]), str(table["table_name"]))
        grouped.append(
            {
                "table": {
                    "name": table["table_name"],
                    "schema_name": table["table_schema"],
                    "id": f"{table['table_schema']}.{table['table_name']}",
                    "description": table.get("description") or "",
                    "pk": pk_by_table.get(key, []),
                },
                "ctx": {
                    "columns": columns_by_table.get(key, []),
                    "fks": [
                        {
                            "source_column": fk["source_column"],
                            "target_table": fk["target_table"],
                            "target_column": fk.get("target_column") or "",
                        }
                        for fk in fk_by_table.get(key, [])
                    ],
                },
            }
        )
    return grouped


def _pk_columns(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "table_schema": pk["table_schema"],
            "table_name": pk["table_name"],
            "column_name": pk["column_name"],
        }
        for pk in catalog["pks"]
    ]


def _junction_candidate(
    table: dict[str, Any],
    suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    declared = table["ctx"]["fks"]
    pairs = [
        {
            "source_column": fk["source_column"],
            "target_table": fk["target_table"],
            "target_column": fk.get("target_column") or "",
            "target_schema": table["table"].get("schema_name") or "",
        }
        for fk in declared
    ]
    for item in suggestions:
        pairs.append(
            {
                "source_column": item["column_name"],
                "target_table": "",
                "target_column": "",
                "target_schema": "",
            }
        )
    return {
        "schema_name": table["table"].get("schema_name") or "",
        "table_name": table["table"]["name"],
        "fk_pairs": pairs,
        "target_terms": [],
    }


def _joins_from_declared(declared: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Parse one equi-join per declared FK with the vendored sqlglot extractor."""
    joins: list[dict[str, str]] = []
    for fk in declared:
        source = quoted_identifier(fk["source_table"], dialect="duckdb")
        target = quoted_identifier(fk["target_table"], dialect="duckdb")
        source_column = quoted_identifier(fk["source_column"], dialect="duckdb")
        target_column = quoted_identifier(fk["target_column"], dialect="duckdb")
        sql = (
            f"SELECT 1 FROM {source} JOIN {target} "
            f"ON {source}.{source_column} = {target}.{target_column}"
        )
        extracted = extract_tables_and_columns(sql, ["duckdb"], {})
        for pair in extracted.joins:
            joins.append(
                {
                    "left_table": pair.left_table,
                    "left_column": pair.left_column,
                    "right_table": pair.right_table,
                    "right_column": pair.right_column,
                }
            )
    return joins
