"""The ``dms_payload`` a contract ask may carry (cortex-contract 1.5.0, WIRE-01b).

Cortex runs its plan-then-SQL ladder when an ask carries a payload: the
tables, columns and joins the question may use. This module builds that
payload from what the Space may read. It is pure: it reads the serving
catalog (names, types, keys; no table rows) and the ontology, and returns a
plain dict shaped as ``AskPayload``, or None. Nothing here puts it on the
wire; the ask site decides that.

- Tables: only names the signed grant lists (``readable``, the manifest's
  ``row_predicates`` keys) that the serving catalog holds, compared lowercase
  as Cortex compares them. A payload can only narrow the grant; Cortex
  refuses a table outside it before any model call.
- Columns: a person-classified column (``column_is_pii``, by name, fails
  closed) is left out. Cortex samples distinct values of payload columns and
  sends them to its model unmasked, so leaving the column out is the one
  control DMS has. Text and boolean columns come first, because Cortex
  samples only the first columns in payload order (ASSUMPTION: this helps
  value grounding; measure it).
- Joins: only when the ontology passed ``verify()``, only many-to-one links,
  only between two relations that are each one physical table row for row,
  and only on key columns the payload carries. A derived relation (GROUP BY,
  DISTINCT, WHERE, JOIN, LIMIT, an aggregate) is never offered as a join on
  its base table: that is the lot fan-out the grain checks exist to refuse.
- Size: at most ``MAX_TABLES`` tables, kept by trigram similarity to the
  question when there are more, and ``MAX_COLUMNS`` columns per table.

Any error returns None and logs its class, so the ask goes out as it does
without a payload.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from dms_core.pii import column_is_pii
from sqlglot import exp, parse_one

from dms_executor.ontology import Ontology
from dms_executor.schema_context import _cosine, _ident, _trigrams, read_catalog

_LOG = logging.getLogger(__name__)

MAX_TABLES = 24
MAX_COLUMNS = 60
_DESCRIPTION_CHARS = 400

#: Clauses that make a SELECT something other than its table's rows.
_NOT_ROWWISE = ("where", "group", "having", "qualify", "distinct", "limit", "offset", "joins")


def _first(type_name: str) -> int:
    """0 for text and boolean columns, 1 for the rest."""
    text = str(type_name or "").upper()
    return 0 if text.startswith(("VARCHAR", "TEXT", "STRING", "CHAR", "BOOL", "ENUM")) else 1


def physical_table(relation: str) -> str | None:
    """The one table a relation is row for row, else None.

    ``inventory`` and ``(SELECT *, CAST(ts AS DATE) AS day FROM t)`` are
    their table. ``(SELECT sku, ANY_VALUE(category) ... GROUP BY sku)`` is
    not: joining on ``inventory`` there would repeat each SKU once per lot.
    """
    rel = str(relation or "").strip()
    if _ident(rel):
        return rel
    try:
        node: Any = parse_one(rel, read="duckdb")
    except Exception:  # noqa: BLE001 - unreadable is not row for row
        return None
    while isinstance(node, exp.Subquery) and not node.alias:
        node = node.this
    if not isinstance(node, exp.Select) or node.args.get("with"):
        return None
    if any(node.args.get(k) for k in _NOT_ROWWISE):
        return None
    source = node.args.get("from")
    table = source.this if isinstance(source, exp.From) else None
    if not isinstance(table, exp.Table) or not _ident(table.name) or table.args.get("db"):
        return None
    if any(p.find(exp.AggFunc) is not None for p in node.expressions):
        return None
    return str(table.name)


def _measure_notes(ontology: Ontology, table: str) -> str:
    """Measures defined at this table's grain, by name and description."""
    notes = []
    for measure in ontology.measures.values():
        obj = ontology.objects.get(measure.grain)
        if obj is None or physical_table(obj.relation) != table:
            continue
        text = measure.description or measure.expression
        notes.append(f"{measure.name}: {' '.join(str(text).split())}")
    return "; ".join(notes)[:_DESCRIPTION_CHARS]


def _joins(ontology: Ontology, columns: Mapping[str, set[str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for link in ontology.links.values():
        if link.cardinality != "many_to_one":
            continue
        left = ontology.objects.get(link.from_object)
        right = ontology.objects.get(link.to_object)
        if left is None or right is None:
            continue
        a = physical_table(left.relation)
        b = physical_table(right.relation)
        if a is None or b is None or a == b or a not in columns or b not in columns:
            continue
        pairs = list(zip(link.from_columns, link.to_columns, strict=False))
        if not pairs or len(link.from_columns) != len(link.to_columns):
            continue
        if not all(f.lower() in columns[a] and t.lower() in columns[b] for f, t in pairs):
            continue
        for f, t in pairs:
            out.append(
                {
                    "left_table": a,
                    "left_column": f,
                    "right_table": b,
                    "right_column": t,
                    "relation": f"{link.name} {link.cardinality}",
                }
            )
    return out


def build_dms_payload(
    question: str,
    *,
    serving: Path | None,
    readable: Iterable[str],
    ontology: Ontology | None = None,
) -> dict[str, Any] | None:
    """``AskPayload`` for this question, or None when no readable table survives."""
    try:
        return _build(question, serving=serving, readable=readable, ontology=ontology)
    except Exception as exc:  # noqa: BLE001 - the ask must not fail on its payload
        _LOG.warning("dms_payload_unbuilt %s", type(exc).__name__)
        return None


def _build(
    question: str,
    *,
    serving: Path | None,
    readable: Iterable[str],
    ontology: Ontology | None,
) -> dict[str, Any] | None:
    granted = {str(name) for name in readable if str(name or "").strip()}
    exact = {name.lower() for name in granted}
    if not exact:
        return None
    _fingerprint, catalog = read_catalog(serving, granted, "duckdb")
    tables: list[dict[str, Any]] = []
    for dataset in catalog.get("datasets") or []:
        name = str(dataset.get("name") or "")
        if name.lower() not in exact:
            continue
        cols = [
            (index, col)
            for index, col in enumerate(dataset.get("columns") or [])
            if str(col.get("name") or "") and not column_is_pii(str(col["name"]), (), table=name)
        ]
        cols.sort(key=lambda item: (_first(str(item[1].get("type") or "")), item[0]))
        tables.append(
            {
                "name": name,
                "columns": [
                    {"name": str(col["name"]), "type": str(col.get("type") or "") or None}
                    for _, col in cols[:MAX_COLUMNS]
                ],
            }
        )
    tables = [t for t in tables if t["columns"]]
    if not tables:
        return None
    if len(tables) > MAX_TABLES:
        asked = _trigrams(question or "")

        def score(table: Mapping[str, Any]) -> float:
            words = " ".join([table["name"], *(c["name"] for c in table["columns"])])
            return _cosine(asked, _trigrams(words))

        keep = {id(t) for t in sorted(tables, key=score, reverse=True)[:MAX_TABLES]}
        tables = [t for t in tables if id(t) in keep]
    payload: dict[str, Any] = {"tables": tables}
    if ontology is not None:
        for table in tables:
            notes = _measure_notes(ontology, table["name"])
            if notes:
                table["description"] = notes
        if ontology.verified:
            columns = {t["name"]: {c["name"].lower() for c in t["columns"]} for t in tables}
            joins = _joins(ontology, columns)
            if joins:
                payload["joins"] = joins
    return payload


__all__ = ["MAX_COLUMNS", "MAX_TABLES", "build_dms_payload", "physical_table"]
