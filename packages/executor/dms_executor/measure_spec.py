"""Structured measure spec and its validator (pure: no lake, no SQL parser).

A measure is an aggregate enum plus ONE landed column of ONE grain object.
There is no field for an expression, filter, cast or arithmetic, so none can be
stored. The SQL text comes from ``ontology.measure_expression`` only.

Rules R1..R8 run at propose, manual entry and confirm time against the latest
snapshot body. Each failure is ``(code, field)``; all are returned together.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dms_core.control_plane.onto_store import measure_definition_hash

from dms_executor.measure_basis import (
    MAX_CONFIRMED_MEASURES,
    MAX_DESCRIPTION_CHARS,
    MAX_PROPOSED_PER_RUN,
)
from dms_executor.ontology import MEASURE_AGGREGATES, demo_ontology
from dms_executor.semantic_retrieve import load_measure_aliases

#: Provenance columns every bronze row carries; never aggregable.
_PROVENANCE = frozenset({"_src", "_ingest_id"})

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")

# Copied verbatim (rule 1 forbids importing it) from the Cortex checkout,
# CortexOS/insights/caller_ontology.py lines 44-54 (_SQLISH). Cortex 4xxs the
# WHOLE ask on one SQL-looking description, so DMS must never write one.
# Drift is pinned by a vector set in tests/test_measure_spec.py.
SQLISH_COPY = re.compile(
    r"\bselect\b[\s\S]*\bfrom\b|\binsert\s+into\b|\bdelete\s+from\b|"
    r"\bdrop\s+(?:table|view|schema|database|macro)\b|\bupdate\s+\w+\s+set\b|"
    r"\battach\s+(?:database\s+)?'|\bcopy\s+\w+\s+(?:to|from)\s+'|"
    r"\bcreate\s+(?:or\s+replace\s+)?(?:table|view|macro|function|secret)\b|"
    r"\bpragma\s+\w+|\bread_(?:csv|parquet|json)\w*\s*\(",
    re.I,
)

_DESC_BANNED = ("`", "{", "}", "<", ">", "$", ";", "--", "/*", "*/")

_NUMERIC_TYPES = frozenset(
    {
        "TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT",
        "UTINYINT", "USMALLINT", "UINTEGER", "UINT", "UBIGINT", "UHUGEINT",
        "FLOAT", "REAL", "DOUBLE", "DECIMAL",
    }
)  # fmt: skip
_FLOATING_TYPES = frozenset({"FLOAT", "REAL", "DOUBLE"})
_TEMPORAL_TYPES = frozenset({"DATE", "TIME", "TIMESTAMP"})


def _base_type(col_type: str) -> str:
    t = str(col_type or "").strip().upper()
    t = t.split("(", 1)[0].strip()
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if t.startswith("TIME"):
        return "TIME"
    return t


def is_numeric_type(col_type: str) -> bool:
    return _base_type(col_type) in _NUMERIC_TYPES


def is_temporal_type(col_type: str) -> bool:
    return _base_type(col_type) in _TEMPORAL_TYPES


def is_floating_type(col_type: str) -> bool:
    return _base_type(col_type) in _FLOATING_TYPES


@dataclass(frozen=True)
class MeasureSpecDraft:
    """The whole of a measure definition. Nothing else can be stated."""

    name: str
    grain: str
    aggregate: str
    column: str
    description: str


def definition_hash(draft: MeasureSpecDraft) -> str:
    return measure_definition_hash(
        draft.name, draft.grain, draft.aggregate, draft.column, draft.description
    )


def definition_text(aggregate: str, column: str, grain: str) -> str:
    """Plain-language definition derived from the spec (never stored, never SQL)."""
    if aggregate == "count":
        if column == "*":
            return f"Count of {grain} rows"
        return f"Count of non-empty {column} per {grain} row"
    if aggregate == "count_distinct":
        return f"Count of distinct {column} per {grain} row"
    word = {"sum": "SUM", "avg": "Average", "min": "Smallest", "max": "Largest"}.get(
        aggregate, aggregate
    )
    return f"{word} of {column} per {grain} row"


def description_failures(description: str) -> bool:
    """True when the description is not safe to store and send to Cortex (R7)."""
    d = description
    if not isinstance(d, str) or not (1 <= len(d) <= MAX_DESCRIPTION_CHARS):
        return True
    if d.strip() == "":
        return True
    if any(
        ord(ch) < 32
        or 0x7F <= ord(ch) <= 0x9F
        or unicodedata.category(ch) in ("Cc", "Zl", "Zp")
        for ch in d
    ):
        return True
    if any(b in d for b in _DESC_BANNED):
        return True
    return SQLISH_COPY.search(d) is not None


def _demo_reserved() -> frozenset[str]:
    names: set[str] = set()
    try:
        names.update(demo_ontology(Path("/nonexistent-demo-warehouse")).measures)
    except Exception:  # noqa: BLE001 - reserved set only ever grows the refusal
        pass
    for k, v in load_measure_aliases().items():
        names.add(k)
        names.add(v)
    return frozenset(n.lower() for n in names)


def _subject_of(v: Any) -> str:
    if isinstance(v, Mapping):
        return str(v.get("subject", ""))
    return str(getattr(v, "subject", ""))


def validate_measure_spec(
    draft: MeasureSpecDraft,
    body: Mapping[str, Any],
    violations: Iterable[Any] = (),
    *,
    taken_names: Iterable[str] = (),
    confirmed_count: int = 0,
    proposed_count: int = 0,
    confirming: bool = False,
) -> list[tuple[str, str]]:
    """R1..R8 against a snapshot ``body`` (objects, links). All failures returned.

    ``taken_names`` are names already used by proposed|confirmed rows of the
    Space (R6 uniqueness). ``confirmed_count`` caps at 80 when ``confirming``;
    ``proposed_count`` caps the proposer at 60 otherwise (R8).
    """
    out: list[tuple[str, str]] = []
    objects: Mapping[str, Any] = body.get("objects") or {}
    links: Mapping[str, Any] = body.get("links") or {}

    # R1
    agg_ok = isinstance(draft.aggregate, str) and draft.aggregate in MEASURE_AGGREGATES
    if not agg_ok:
        out.append(("aggregate_invalid", "aggregate"))

    # R2
    obj = objects.get(draft.grain) if isinstance(draft.grain, str) else None
    if obj is None:
        out.append(("unknown_object", "grain"))
    elif draft.grain in {_subject_of(v) for v in violations}:
        out.append(("grain_unverified", "grain"))

    # R3 + R4 + R5 need a known grain object and a known aggregate
    col = draft.column
    attrs: dict[str, str] = {}
    if obj is not None:
        attrs = {str(a.get("name")): str(a.get("type", "")) for a in obj.get("attributes") or []}
    if col == "*":
        if agg_ok and draft.aggregate != "count":
            out.append(("column_missing", "column"))
    elif not isinstance(col, str) or col in _PROVENANCE or (obj is not None and col not in attrs):
        out.append(("column_missing", "column"))
    elif obj is not None and agg_ok:
        ctype = attrs[col]
        agg = draft.aggregate
        if agg in ("sum", "avg") and not is_numeric_type(ctype):
            out.append(("column_not_numeric", "column"))
        elif agg in ("min", "max") and not (is_numeric_type(ctype) or is_temporal_type(ctype)):
            out.append(("column_type_unsupported", "column"))
        elif agg == "count_distinct" and is_floating_type(ctype):
            out.append(("column_type_unsupported", "column"))
        if agg in ("sum", "avg", "min", "max"):
            keys = {str(k) for k in obj.get("key") or []}
            link_cols = {
                str(c)
                for ln in links.values()
                if ln.get("from") == draft.grain
                for c in ln.get("from_columns") or []
            }
            if col in keys or col in link_cols:
                out.append(("key_column_aggregate", "column"))

    # R6
    name = draft.name
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        out.append(("name_invalid", "name"))
    else:
        low = name.lower()
        reserved = set(_demo_reserved())
        for oname, o in objects.items():
            reserved.add(str(oname).lower())
            reserved.add(str(oname).rsplit(".", 1)[-1].lower())
            for a in o.get("attributes") or []:
                reserved.add(str(a.get("name", "")).lower())
        if low in reserved:
            out.append(("name_reserved", "name"))
        if name in set(taken_names):
            out.append(("name_taken", "name"))

    # R7
    if description_failures(draft.description):
        out.append(("description_invalid", "description"))

    # R8
    if confirming and confirmed_count >= MAX_CONFIRMED_MEASURES:
        out.append(("measure_limit", "state"))
    if not confirming and proposed_count >= MAX_PROPOSED_PER_RUN:
        out.append(("measure_limit", "state"))
    return out
