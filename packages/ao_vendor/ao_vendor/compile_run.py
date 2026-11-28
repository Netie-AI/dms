# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run the vendored catalog and semantic steps. Returns a proposal dict.

Called only when ``DMS_AO_COMPILE=1``. The proposal status is ``proposed``.
This module does not serve an answer and does not write a store.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ao_vendor import VENDOR_SHA
from ao_vendor.catalog.extract import extract_duckdb
from ao_vendor.catalog.parsers.sqlglot_extractor import extract_tables_and_columns
from ao_vendor.llm.ov_client import (
    REFUSE_KEY_MISSING,
    REFUSE_NO_CALLS,
    REFUSE_OUTPUT_INVALID,
    REFUSE_UNAUTHORIZED,
    AoCompileRefused,
    AoLlmError,
    OpenVaultOntologyClient,
    bind_client,
    bind_deadline,
    reset_client,
    reset_deadline,
    resilient_enabled,
)
from ao_vendor.semantic.bridge_tables import propose_bridge_attributes
from ao_vendor.semantic.semantic_fk import resolve_inferred_fks
from ao_vendor.semantic.visit_enter import process_table
from ao_vendor.utils.sql_identifiers import quoted_identifier

logger = logging.getLogger(__name__)

_STICKY = frozenset(
    {
        REFUSE_KEY_MISSING,
        REFUSE_UNAUTHORIZED,
        "openvault_url_missing",
        "model_key_refused",
    }
)


def compile_catalog(
    conn: Any,
    *,
    transport: Any = None,
    tables: Sequence[str] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Extract ``conn`` and propose terms, attributes, and inferred FKs.

    Flag off raises :class:`AoCompileRefused` when any model call failed, or
    when no call succeeded for a catalog that has tables. ``tables`` and
    ``deadline`` are ignored, so the payload bytes stay the ones this function
    has always returned.

    Flag on compiles ``tables`` when that list is passed (``None`` still means
    every table on ``conn``). A failed table is a gap. The payload is
    ``partial`` when some tables compiled and ``refused`` only when none did.
    """
    if resilient_enabled():
        token = bind_deadline(deadline)
        try:
            return _compile_resilient(conn, transport=transport, tables=tables)
        finally:
            reset_deadline(token)
    client = OpenVaultOntologyClient(transport=transport)
    token = bind_client(client)
    try:
        catalog = extract_duckdb(conn)
        by_table = _group(catalog)
        terms: list[dict[str, Any]] = []
        attributes: list[dict[str, Any]] = []
        suggestions: list[dict[str, Any]] = []
        junctions: list[dict[str, Any]] = []
        unkeyed: list[str] = []
        for table in by_table:
            compiled = process_table(table["table"], table["ctx"], conn=conn)
            key = _term_key(
                table["table"].get("pk") or [],
                compiled.pk_column_names,
                table["ctx"].get("columns") or [],
            )
            if key:
                for term in compiled.terms:
                    term["key"] = list(key)
                terms.extend(compiled.terms)
            elif compiled.terms:
                name = str(table["table"].get("name") or "").strip()
                if name and name not in unkeyed:
                    unkeyed.append(name)
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
        _require_clean_ai_pass(client, table_count=len(by_table))
        return {
            "status": "proposed",
            "vendor_sha": VENDOR_SHA,
            "model_calls": client.calls_ok,
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
            "unkeyed": unkeyed,
        }
    except AoLlmError as exc:
        raise AoCompileRefused(exc.code) from None
    finally:
        reset_client(token)


def _require_clean_ai_pass(client: OpenVaultOntologyClient, *, table_count: int) -> None:
    """A fatal failure wins the name. Else bad output. Else no call at all."""
    if client.failures:
        fatal = [code for code in client.failures if code != REFUSE_OUTPUT_INVALID]
        raise AoCompileRefused(fatal[0] if fatal else REFUSE_OUTPUT_INVALID)
    if table_count and client.calls_ok == 0:
        raise AoCompileRefused(REFUSE_NO_CALLS)


def _term_key(
    declared: list[Any],
    model_names: list[Any],
    columns: list[dict[str, Any]],
) -> list[str]:
    """Declared catalog PK, else PK columns from the compile's FkAndPkResult.

    The model list is the reading ``process_table`` already made. This does
    not call again. A name that is not a column of this table is dropped.
    """
    keys = [str(name).strip() for name in declared if str(name).strip()]
    if keys:
        return keys
    known = {str(col.get("name")) for col in columns if col.get("name")}
    out: list[str] = []
    for raw in model_names:
        name = str(raw).strip()
        if name and name in known and name not in out:
            out.append(name)
    return out


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


def _compile_resilient(
    conn: Any,
    *,
    transport: Any,
    tables: Sequence[str] | None,
) -> dict[str, Any]:
    """Per-table compile. One table's failure is a gap, not a whole refusal."""
    client = OpenVaultOntologyClient(transport=transport)
    token = bind_client(client)
    try:
        catalog = extract_duckdb(conn)
        if tables is not None:
            catalog = _limit_catalog(catalog, tables)
        by_table = _group(catalog)
        if tables is not None and not by_table:
            return _empty_payload(
                catalog,
                client,
                status="refused",
                gaps=[_gap("", "ao_no_granted_tables", "", ())],
            )
        terms: list[dict[str, Any]] = []
        attributes: list[dict[str, Any]] = []
        suggestions: list[dict[str, Any]] = []
        junctions: list[dict[str, Any]] = []
        unkeyed: list[str] = []
        gaps: list[dict[str, Any]] = []
        compiled_ok = 0
        for table in by_table:
            name = str(table["table"].get("name") or "").strip()
            fail_before = len(client.failures)
            try:
                compiled = process_table(table["table"], table["ctx"], conn=conn)
            except AoLlmError as exc:
                if exc.code in _STICKY:
                    raise AoCompileRefused(exc.code) from None
                gaps.append(_gap_at(name, client, fail_before, fallback=exc.code))
                continue
            except Exception as exc:
                logger.error("ao table %s failed: %s", name, type(exc).__name__)
                gaps.append(_gap(name, "ao_compile_failed", "", ()))
                continue
            if len(client.failures) > fail_before:
                gaps.append(
                    _gap_at(name, client, fail_before, fallback=REFUSE_OUTPUT_INVALID)
                )
                continue
            compiled_ok += 1
            key = _term_key(
                table["table"].get("pk") or [],
                compiled.pk_column_names,
                table["ctx"].get("columns") or [],
            )
            if key:
                for term in compiled.terms:
                    term["key"] = list(key)
                terms.extend(compiled.terms)
            elif compiled.terms:
                if name and name not in unkeyed:
                    unkeyed.append(name)
            attributes.extend(compiled.attributes)
            suggestions.extend(compiled.fk_suggestions)
            if compiled.is_junction:
                junctions.append(_junction_candidate(table, compiled.fk_suggestions))
        if compiled_ok == 0:
            if not gaps:
                gaps.append(_gap("", REFUSE_NO_CALLS, "", ()))
            return _empty_payload(catalog, client, status="refused", gaps=gaps)
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
        payload: dict[str, Any] = {
            "status": "partial" if gaps else "proposed",
            "vendor_sha": VENDOR_SHA,
            "model_calls": client.calls_ok,
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
            "unkeyed": unkeyed,
        }
        if gaps:
            payload["gaps"] = gaps
        return payload
    except AoLlmError as exc:
        raise AoCompileRefused(exc.code) from None
    finally:
        reset_client(token)


def _empty_payload(
    catalog: dict[str, Any],
    client: OpenVaultOntologyClient,
    *,
    status: str,
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": status,
        "vendor_sha": VENDOR_SHA,
        "model_calls": client.calls_ok,
        "served": False,
        "database_name": catalog.get("database_name") or "",
        "terms": [],
        "attributes": [],
        "inferred_fks": [],
        "declared_fks": [],
        "joins": [],
        "bridge_attributes": [],
        "unkeyed": [],
        "gaps": gaps,
    }


def _gap(table: str, code: str, step: str, fields: tuple[str, ...]) -> dict[str, Any]:
    return {"table": table, "code": code, "step": step, "fields": list(fields)}


def _gap_at(
    table: str,
    client: OpenVaultOntologyClient,
    index: int,
    *,
    fallback: str,
) -> dict[str, Any]:
    code = client.failures[index] if index < len(client.failures) else fallback
    step = client.failure_steps[index] if index < len(client.failure_steps) else ""
    fields = client.failure_fields[index] if index < len(client.failure_fields) else ()
    return _gap(table, code, step, fields)


def _limit_catalog(catalog: dict[str, Any], tables: Sequence[str]) -> dict[str, Any]:
    """Keep relations the Space granted. Match is the identifier, not a phrase."""
    allow = tuple(dict.fromkeys(str(name).strip() for name in tables if str(name).strip()))
    kept_keys: set[tuple[str, str]] = set()
    kept_tables = []
    for table in catalog["tables"]:
        schema = str(table.get("table_schema") or "")
        name = str(table.get("table_name") or "")
        if _granted_name(schema, name, allow):
            kept_tables.append(table)
            kept_keys.add((schema, name))
    kept_bare = {name.casefold() for _, name in kept_keys}

    def _row_kept(row: dict[str, Any]) -> bool:
        schema = str(row.get("table_schema") or "")
        name = str(row.get("table_name") or "")
        return (schema, name) in kept_keys

    def _target_kept(target: str) -> bool:
        text = str(target or "").strip()
        if not text:
            return False
        tail = text.rsplit(".", 1)[-1].casefold()
        return tail in kept_bare or text.casefold() in kept_bare

    fks = [
        fk
        for fk in catalog["fks"]
        if _row_kept(fk) and _target_kept(str(fk.get("target_table") or ""))
    ]
    return {
        "database_name": catalog.get("database_name") or "",
        "tables": kept_tables,
        "columns": [row for row in catalog["columns"] if _row_kept(row)],
        "views": [
            row
            for row in catalog.get("views") or []
            if _granted_name(
                str(row.get("table_schema") or ""),
                str(row.get("table_name") or ""),
                allow,
            )
        ],
        "pks": [row for row in catalog["pks"] if _row_kept(row)],
        "fks": fks,
        "queries": [],
    }


def _granted_name(schema: str, table: str, allow: tuple[str, ...]) -> bool:
    if not table:
        return False
    candidates = {table.casefold()}
    if schema:
        candidates.add(f"{schema}.{table}".casefold())
    for granted in allow:
        folded = granted.casefold()
        tail = folded.rsplit(".", 1)[-1]
        if folded in candidates or tail == table.casefold():
            return True
    return False
