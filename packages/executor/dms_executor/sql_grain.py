"""GRAIN-GUARD-01: the answer's grain and columns must match the question.

L2_VALIDATED used to certify any SQL that parsed, validated and executed. The
15 oracle-wrong answers on the curated pack all carried it: grouped by the
measure's own input (``GROUP BY capacity_kg`` for "capacity utilisation"),
one row per SKU where a single total was asked, or a list ask ("which
locations ...") answered with an aggregate the question never named.

Wrong grain, a scalar ask answered with a breakdown, a COUNT tally on a list
ask, or an unrequested figure on a "show the X" ask is a named ABSTAIN. A
"which / list" ask whose rows are the right entities with a ride-along figure
keeps L2 over the requested columns only (the figure is dropped).

Fail closed only on clear evidence. An unparseable or set-operation SQL is not
evidence and passes to the gates that already exist. The missing-dimension
case ("by month" with no month bucket) is QUAL-GUARD-01's, not this module's.

sqlglot lives here and in sql_currency. Swap: a Cortex HTTP grain-check behind
the same two functions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sqlglot import exp, parse_one

_DIALECT = "duckdb"

REASON_UNREQUESTED_GRAIN = "unrequested_grain"
REASON_GRAIN_MISMATCH = "grain_mismatch"
REASON_UNREQUESTED_MEASURE = "unrequested_measure"
REASON_UNREQUESTED_COLUMN = "unrequested_column"
SCALAR_EXPECTED = f"{REASON_GRAIN_MISMATCH}:scalar_expected"

GRAIN_REASONS = frozenset(
    {
        REASON_UNREQUESTED_GRAIN,
        REASON_GRAIN_MISMATCH,
        REASON_UNREQUESTED_MEASURE,
        REASON_UNREQUESTED_COLUMN,
    }
)

# A single figure is asked for.
_SCALAR_CUE = re.compile(
    r"\b(total|how\s+many|how\s+much|overall|in\s+total|sum\s+of|number\s+of|"
    r"count\s+of|altogether)\b",
    re.I,
)
# The question names a breakdown, a ranking, a list or a comparison.
_BREAKDOWN_CUE = re.compile(
    r"\b(by|per|each|every|which|list|top|bottom|rank\w*|breakdown|split|"
    r"across|group(?:ed)?|vs|versus|compar\w*|trend\w*|over\s+time|"
    r"daily|weekly|monthly|quarterly|yearly|annual\w*|what\s+are|show\s+all)\b",
    re.I,
)
# The question asks for entities, not a figure.
_LIST_HEAD = re.compile(r"^\s*(which|list|show|what\s+are)\b", re.I)
# Any of these makes an aggregate output a plausible part of the answer.
_AGG_CUE = re.compile(
    r"\b(total|sum|count|how\s+many|how\s+much|number\s+of|average|avg|mean|"
    r"median|highest|lowest|most|least|largest|smallest|biggest|top|bottom|"
    r"rank\w*|max\w*|min\w*|by|per|each)\b",
    re.I,
)
_COUNT_CUE = re.compile(r"\b(count|how\s+many|number\s+of|tally)\b", re.I)
# Unit / plumbing tokens carry no meaning on their own in an alias.
_UNIT_TOKENS = frozenset(
    {
        "pct", "percent", "myr", "usd", "eur", "sgd", "kg", "kgs", "qty", "amt",
        "sum", "total", "avg", "cnt", "num", "val", "f", "d0", "d1", "d2",
    }
)
# Numeric measure-looking plain columns (not keys, not labels).
_MEASURE_SUFFIX = re.compile(r"_(kg|myr|usd|eur|sgd|pct|days|score|qty|amount|cost|value)$", re.I)
_WORD = re.compile(r"[a-z0-9]+")


def _q_words(question: str) -> list[str]:
    return _WORD.findall(question.lower())


def _tokens(name: str) -> list[str]:
    return [
        t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) >= 3 and t not in _UNIT_TOKENS
    ]


def _overlaps(name: str, words: Sequence[str]) -> bool:
    """A name token matches a question word on a shared 5-char stem (or whole)."""
    for tok in _tokens(name):
        stem = tok[:5]
        for w in words:
            if w == tok or (len(stem) >= 5 and w.startswith(stem)) or (
                len(w) >= 5 and tok.startswith(w[:5])
            ):
                return True
    return False


def _requested_by(name: str, question: str) -> bool:
    """``by|per|each|for each <...name tokens...>`` in the question."""
    low = question.lower()
    for tok in _tokens(name):
        if re.search(rf"\b(by|per|each|every)\s+(\w+\s+){{0,2}}{re.escape(tok[:5])}", low):
            return True
    return False


def _outer_select(sql: str) -> exp.Select | None:
    try:
        tree = parse_one(sql, read=_DIALECT)
    except Exception:  # noqa: BLE001 -- not evidence; other gates own parse errors
        return None
    return tree if isinstance(tree, exp.Select) else None


def _agg_inputs(select: exp.Select) -> set[str]:
    out: set[str] = set()
    for proj in select.expressions:
        for agg in proj.find_all(exp.AggFunc):
            for col in agg.find_all(exp.Column):
                out.add(col.name.lower())
    return out


def _projection_by_alias(select: exp.Select) -> dict[str, exp.Expression]:
    out: dict[str, exp.Expression] = {}
    for proj in select.expressions:
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        out[proj.alias_or_name.lower()] = inner
    return out


def _group_columns(select: exp.Select) -> list[str]:
    group = select.args.get("group")
    if group is None:
        return []
    by_alias = _projection_by_alias(select)
    projs = list(select.expressions)
    out: list[str] = []
    for key in group.expressions:
        node: exp.Expression = key
        if isinstance(key, exp.Literal) and key.is_int:
            idx = int(key.this) - 1
            if 0 <= idx < len(projs):
                p = projs[idx]
                node = p.this if isinstance(p, exp.Alias) else p
        elif isinstance(key, exp.Column) and not key.table and key.name.lower() in by_alias:
            node = by_alias[key.name.lower()]
        if isinstance(node, exp.Column):
            out.append(node.name.lower())
    return out


def _where_columns(select: exp.Select) -> set[str]:
    out: set[str] = set()
    for arg in ("where", "having"):
        node = select.args.get(arg)
        if node is not None:
            out.update(c.name.lower() for c in node.find_all(exp.Column))
    return out


def _list_head(question: str) -> str:
    """which | list | show when the ask wants entities and names no aggregate."""
    m = _LIST_HEAD.search(question)
    if m is None or _AGG_CUE.search(question):
        return ""
    word = m.group(1).lower()
    return "list" if word.startswith("what") else word


def scalar_asked(question: str) -> bool:
    return bool(_SCALAR_CUE.search(question)) and not _BREAKDOWN_CUE.search(question)


def grain_mismatch_reason(question: str, sql: str) -> str | None:
    """Named reason when the SQL's grain or projection is not the question's.

    SQL-only; runs before submit. ``None`` means no clear evidence.
    """
    select = _outer_select(sql)
    if select is None:
        return None
    words = _q_words(question)

    # (b) grouped by the measure's own input: a dimension nobody asked for.
    inputs = _agg_inputs(select)
    for col in _group_columns(select):
        if col in inputs and not _requested_by(col, question):
            return f"{REASON_UNREQUESTED_GRAIN}:{col}"

    # List ask: the answer is entities. An aggregate output is requested only
    # by an aggregation cue or by name; a COUNT only by a count cue (COUNT(*)
    # FILTER is the predicate turned into a tally: its zero rows are entities
    # the predicate excludes, so trimming it cannot fix the row set).
    head = _list_head(question)
    if head:
        where_cols = _where_columns(select)
        for proj in select.expressions:
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            name = proj.alias_or_name
            if inner.find(exp.AggFunc) is not None:
                if inner.find(exp.Count) is not None and not _COUNT_CUE.search(question):
                    return f"{REASON_UNREQUESTED_MEASURE}:{name}"
                if head == "show" and not _overlaps(name, words):
                    return f"{REASON_UNREQUESTED_MEASURE}:{name}"
            elif isinstance(inner, exp.Column):
                col = inner.name.lower()
                if (
                    _MEASURE_SUFFIX.search(col)
                    and col not in where_cols
                    and not _overlaps(col, words)
                    and not _overlaps(name, words)
                ):
                    return f"{REASON_UNREQUESTED_COLUMN}:{col}"
    return None


def unrequested_measure_outputs(question: str, sql: str) -> list[str]:
    """Aggregate output names a which/list ask never requested.

    The row set is the grouped entities, already filtered by WHERE / HAVING /
    keep_gt; the extra figure only rides along. Dropping it leaves exactly the
    entities asked for. Only non-COUNT aggregates reach here
    (``grain_mismatch_reason`` abstains on a COUNT first).
    """
    if _list_head(question) not in {"which", "list"}:
        return []
    select = _outer_select(sql)
    if select is None:
        return []
    words = _q_words(question)
    out: list[str] = []
    keep = 0
    for proj in select.expressions:
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if inner.find(exp.AggFunc) is not None and not _overlaps(proj.alias_or_name, words):
            out.append(proj.alias_or_name)
        else:
            keep += 1
    return out if keep else []


def scalar_rows_reason(question: str, rows: Sequence[Any]) -> str | None:
    """(c) a single total was asked but more than one row came back."""
    if scalar_asked(question) and len(rows) > 1:
        return SCALAR_EXPECTED
    return None


def grain_abstain_text(reason: str) -> str:
    """Rendered ABSTAIN text for a grain reason. States no figure."""
    head, _, detail = str(reason).partition(":")
    if head == REASON_UNREQUESTED_GRAIN:
        what = f"it was broken down by {detail}, which you did not ask for"
    elif reason == SCALAR_EXPECTED:
        what = "you asked for a single total and the query returned a breakdown"
    elif head == REASON_UNREQUESTED_MEASURE:
        what = f"it adds a figure ({detail}) that you did not ask for"
    elif head == REASON_UNREQUESTED_COLUMN:
        what = f"it adds a column ({detail}) that you did not ask for"
    else:
        what = "its shape does not match what you asked"
    return (
        "I cannot certify that answer: "
        f"{what} (gap: {reason}). I am not showing it as a validated result."
    )
