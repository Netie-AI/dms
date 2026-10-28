# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Propose a join-pattern SqlAttribute for a junction table.

Upstream persisted the attribute through the server DAL and ranked the
owner Term with a reranker. Neither is vendored. A proposal is returned
in memory. SQL that sqlglot cannot parse is dropped. The first target
term is the owner when several exist.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot.errors import ParseError, TokenError

from ao_vendor.llm.messages import HumanMessage, SystemMessage
from ao_vendor.llm.ov_client import get_llm_client, invoke_with_structured_output
from ao_vendor.semantic.constants import SQL_ATTR_SOURCE_BRIDGE

logger = logging.getLogger(__name__)


class _BridgeSqlAttributeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    expression: str


def _format_fk_block(fk_pairs: list[dict[str, Any]]) -> str:
    lines = []
    for pair in fk_pairs:
        source = pair.get("source_column") or ""
        target_table = pair.get("target_table") or ""
        target_column = pair.get("target_column") or ""
        lines.append(f"  - {source} -> {target_table}.{target_column}")
    return "\n".join(lines) or "  (none)"


def _generate_bridge_sql_attribute(
    candidate: dict[str, Any],
) -> _BridgeSqlAttributeProposal | None:
    schema_name = candidate.get("schema_name") or ""
    table_name = candidate.get("table_name") or ""
    fk_pairs = candidate.get("fk_pairs") or []
    qualified = f"{schema_name}.{table_name}" if schema_name else table_name
    related = sorted(
        {
            (
                f"{pair['target_schema']}.{pair['target_table']}"
                if pair.get("target_schema")
                else pair["target_table"]
            )
            for pair in fk_pairs
            if pair.get("target_table")
        }
    )
    self_referential = len(related) == 1
    join_hint = (
        "This is a self-referential bridge: both FK columns point at the same "
        "entity table. Alias that table twice in the JOIN so each FK role is clear."
        if self_referential
        else "Join the bridge table to each related entity table using the FK pairs."
    )
    messages = [
        SystemMessage(
            content=(
                "You are an expert data engineer defining reusable join patterns "
                "for many-to-many junction (bridge) tables. Bridge tables connect "
                "entities through foreign keys, including self-referential cases "
                "where multiple columns point at the same target table."
            )
        ),
        HumanMessage(
            content=(
                f"Bridge table: {qualified}\n"
                f"Related tables: {', '.join(related) or '(unknown)'}\n"
                f"Foreign key columns (use these exact join keys):\n"
                f"{_format_fk_block(fk_pairs)}\n\n"
                f"{join_hint}\n\n"
                "Produce one SqlAttribute that captures how to join through this "
                "bridge table to reach all related entities.\n\n"
                "Rules:\n"
                "  - expression must be a valid SELECT ... FROM ... JOIN ... statement\n"
                "  - schema-qualify every table name\n"
                "  - use ONLY the FK pairs listed above - do not invent columns "
                "or filters\n"
                "  - no WHERE clause with literal business filters; structural "
                "joins only\n"
                "  - name - user-friendly Title Case label\n"
                "  - description - what relationship the junction represents\n"
            )
        ),
    ]
    return invoke_with_structured_output(
        get_llm_client(temperature=0.0),
        messages,
        _BridgeSqlAttributeProposal,
    )


def _sql_parses(expression: str) -> bool:
    try:
        sqlglot.parse_one(expression, dialect="duckdb")
    except (ParseError, TokenError, ValueError):
        return False
    return True


def propose_bridge_attributes(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return proposed SqlAttributes. An empty candidate list does no I/O."""
    if not candidates:
        return []
    proposed: list[dict[str, Any]] = []
    for candidate in candidates:
        table_name = str(candidate.get("table_name") or "")
        terms = candidate.get("target_terms") or []
        if not terms:
            logger.warning("Bridge table %s: no target terms", table_name)
            continue
        proposal = _generate_bridge_sql_attribute(candidate)
        if proposal is None or not _sql_parses(proposal.expression):
            logger.warning("Bridge table %s: no usable SQL proposal", table_name)
            continue
        owner = terms[0]
        proposed.append(
            {
                "table": table_name,
                "name": proposal.name,
                "description": proposal.description,
                "expression": proposal.expression,
                "term": owner.get("name") or "",
                "source": SQL_ATTR_SOURCE_BRIDGE,
                "status": "proposed",
            }
        )
    return proposed
