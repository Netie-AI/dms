"""GRAIN-GUARD-01: the answer's grain and columns must match the question.

L2_VALIDATED used to certify any SQL that parsed, validated and executed. The
15 oracle-wrong answers on the curated pack all carried it: grouped by the
measure's own input (``GROUP BY capacity_kg`` for "capacity utilisation"),
one row per SKU where a single total was asked, or a list ask ("which
locations ...") answered with an aggregate the question never named.

Wrong grain, a scalar ask answered with a grouped query, a COUNT tally on a
list ask (no HAVING), or an unrequested figure on a "show the X" ask is a
named ABSTAIN. Pass-through derived tables (``SELECT * FROM (...) t``) are
walked to the select that sets the grain. A "which / list" ask with a
ride-along figure keeps L2 over the requested columns only when a predicate
(keep_gt, or a WHERE / HAVING on what the question names) already selected
the entities; otherwise the figure is the only sign of an unfiltered row set
and the ask abstains. The trimmed SQL is what executes and is audited.

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


# Business words a question uses for a column token (both directions).
_SYNONYMS: dict[str, frozenset[str]] = {
    "quantity": frozenset({"stock", "inventory", "qty", "hand", "units"}),
    "value": frozenset({"worth", "valuation"}),
    "cost": frozenset({"spend", "spent", "expense", "price", "freight"}),
    "outbound": frozenset({"sales", "sold", "selling", "revenue", "sell"}),
    "current": frozenset({"load", "utilisation", "utilization", "full", "occupancy"}),
    "load": frozenset({"utilisation", "utilization", "full", "occupancy"}),
}
# Entity keys / labels: grouping by one of these is naming the entity asked for.
_KEY_COLUMN = re.compile(r"(^|_)(id|code|name|sku|no|number|key|label|title)$", re.I)
# Time buckets: a missing or extra time grain is QUAL-GUARD-01's call.
_TIME_COLUMN = re.compile(
    r"(^|_)(day|date|week|month|quarter|year|ts|period|time|at)$", re.I
)


def _q_words(question: str) -> list[str]:
    return _WORD.findall(question.lower())


def _tokens(name: str) -> list[str]:
    return [
        t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) >= 3 and t not in _UNIT_TOKENS
    ]


def _word_match(tok: str, w: str) -> bool:
    stem = tok[:5]
    return w == tok or (len(stem) >= 5 and w.startswith(stem)) or (
        len(w) >= 5 and tok.startswith(w[:5])
    )


def _overlaps(name: str, words: Sequence[str]) -> bool:
    """A name token matches a question word on a shared 5-char stem, whole, or synonym."""
    for tok in _tokens(name):
        syn = _SYNONYMS.get(tok, frozenset())
        for w in words:
            if _word_match(tok, w) or w in syn:
                return True
    return False


def _requested_by(name: str, question: str) -> bool:
    """``by|per|each|for each <...name tokens...>`` in the question."""
    low = question.lower()
    for tok in _tokens(name):
        if re.search(rf"\b(by|per|each|every)\s+(\w+\s+){{0,2}}{re.escape(tok[:5])}", low):
            return True
    return False


def _parse(sql: str) -> exp.Expression | None:
    try:
        return parse_one(sql, read=_DIALECT)
    except Exception:  # noqa: BLE001 -- not evidence; other gates own parse errors
        return None


def _outer_select(sql: str) -> exp.Select | None:
    tree = _parse(sql)
    return tree if isinstance(tree, exp.Select) else None


def _has_agg(select: exp.Select) -> bool:
    return any(p.find(exp.AggFunc) is not None for p in select.expressions)


def _is_star(select: exp.Select) -> bool:
    return any(
        isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star))
        for p in select.expressions
    )


def _derived_inner(select: exp.Select) -> exp.Select | None:
    """The sole derived table in FROM (no joins), if any."""
    if select.args.get("joins"):
        return None
    frm = select.args.get("from")
    src = frm.this if frm is not None else None
    if isinstance(src, exp.Subquery) and isinstance(src.this, exp.Select):
        return src.this
    return None


def _grain_chain(select: exp.Select) -> list[exp.Select]:
    """Outer select, then each pass-through derived table the grain comes from.

    ``SELECT * FROM (SELECT sku, COUNT(*) FILTER ... GROUP BY sku) t`` has the
    per-SKU tally's grain, not the wrapper's. Descend while the wrapper neither
    aggregates nor groups; stop at the first select that does.
    """
    chain = [select]
    cur = select
    while cur.args.get("group") is None and not _has_agg(cur):
        inner = _derived_inner(cur)
        if inner is None:
            break
        chain.append(inner)
        cur = inner
    return chain


def _effective_projections(chain: list[exp.Select]) -> list[exp.Expression]:
    """Projections of the grain select, limited to what the wrapper(s) pass out."""
    names: set[str] | None = None
    for sel in chain[:-1]:
        if not _is_star(sel):
            here = {p.alias_or_name.lower() for p in sel.expressions}
            names = here if names is None else names & here
    projs = list(chain[-1].expressions)
    if names is None:
        return projs
    return [p for p in projs if p.alias_or_name.lower() in names]


def _agg_inputs_of(proj: exp.Expression) -> set[str]:
    return {c.name.lower() for agg in proj.find_all(exp.AggFunc) for c in agg.find_all(exp.Column)}


def _agg_inputs(select: exp.Select) -> set[str]:
    out: set[str] = set()
    for proj in select.expressions:
        out |= _agg_inputs_of(proj)
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


def _pinned_columns(select: exp.Select) -> set[str]:
    """Columns WHERE pins to one literal (``col = 'WH-A'``): one group, not a breakdown."""
    where = select.args.get("where")
    if where is None:
        return set()
    out: set[str] = set()
    for eq in where.find_all(exp.EQ):
        left, right = eq.this, eq.expression
        if isinstance(left, exp.Column) and isinstance(right, (exp.Literal, exp.Boolean)):
            out.add(left.name.lower())
        elif isinstance(right, exp.Column) and isinstance(left, (exp.Literal, exp.Boolean)):
            out.add(right.name.lower())
    return out


def _where_columns(select: exp.Select) -> set[str]:
    out: set[str] = set()
    for arg in ("where", "having"):
        node = select.args.get(arg)
        if node is not None:
            out.update(c.name.lower() for c in node.find_all(exp.Column))
    return out


def _predicate_names_question(chain: list[exp.Select], words: Sequence[str]) -> bool:
    """A WHERE / HAVING somewhere in the chain filters on what the question names.

    Evidence that the row set is the question's entities, not every entity:
    a predicate column or string literal that shares a stem (or synonym) with
    a question word (``is_cold_storage`` for "cold storage", ``'CHEMICALS'``
    for "chemicals"). A scope filter alone (``location_code = 'WH-A'``) is not.
    """
    for sel in chain:
        for arg in ("where", "having"):
            node = sel.args.get(arg)
            if node is None:
                continue
            for col in node.find_all(exp.Column):
                if _overlaps(col.name, words):
                    return True
            for lit in node.find_all(exp.Literal):
                if lit.is_string and _overlaps(str(lit.this), words):
                    return True
    return False


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
    outer = _outer_select(sql)
    if outer is None:
        return None
    chain = _grain_chain(outer)
    select = chain[-1]
    words = _q_words(question)
    groups = _group_columns(select)
    pinned = _pinned_columns(select)

    # (a) a single total asked, grouped query: a breakdown whatever the row
    # count (``ORDER BY 2 DESC LIMIT 1`` over per-SKU sums is one SKU, not the
    # total). A group pinned to one literal by WHERE is still one figure.
    if scalar_asked(question) and select.args.get("group") is not None:
        if any(col not in pinned for col in groups) or len(groups) < len(
            select.args["group"].expressions
        ):
            return SCALAR_EXPECTED

    # (b) a dimension nobody asked for: the measure's own input
    # (capacity_kg for "capacity utilisation"), or any non-key column the
    # question neither names nor asks a breakdown by (is_cold_storage).
    inputs = _agg_inputs(select)
    where_cols = _where_columns(select)
    for col in groups:
        if _requested_by(col, question):
            continue
        if col in inputs:
            return f"{REASON_UNREQUESTED_GRAIN}:{col}"
        if (
            _has_agg(select)
            and not _KEY_COLUMN.search(col)
            and not _TIME_COLUMN.search(col)
            and col not in where_cols
            and not _overlaps(col, words)
        ):
            return f"{REASON_UNREQUESTED_GRAIN}:{col}"

    # List ask: the answer is entities. An aggregate output is requested by an
    # aggregation cue, by name, or (on "show X") by its input column. A COUNT
    # without a count cue is COUNT(*) FILTER, the predicate turned into a
    # tally: its zero rows are entities the predicate excludes, so trimming
    # cannot fix the row set -- unless HAVING already filters on it.
    head = _list_head(question)
    if head:
        having = select.args.get("having") is not None
        for proj in _effective_projections(chain):
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            name = proj.alias_or_name
            if inner.find(exp.AggFunc) is not None:
                if (
                    inner.find(exp.Count) is not None
                    and not _COUNT_CUE.search(question)
                    and not having
                ):
                    return f"{REASON_UNREQUESTED_MEASURE}:{name}"
                if (
                    head == "show"
                    and not _overlaps(name, words)
                    and not any(_overlaps(c, words) for c in _agg_inputs_of(inner))
                ):
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

    Only non-COUNT aggregates reach here (``grain_mismatch_reason`` abstains
    on a bare COUNT first). Whether dropping them is safe is
    ``trim_is_safe``'s call, not this function's.
    """
    if _list_head(question) not in {"which", "list"}:
        return []
    outer = _outer_select(sql)
    if outer is None:
        return []
    chain = _grain_chain(outer)
    words = _q_words(question)
    out: list[str] = []
    keep = 0
    for proj in _effective_projections(chain):
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if inner.find(exp.AggFunc) is not None and not _overlaps(proj.alias_or_name, words):
            out.append(proj.alias_or_name)
        else:
            keep += 1
    return out if keep else []


def output_names(sql: str) -> list[str] | None:
    """Output column names of the SQL, or ``None`` when a star hides them."""
    outer = _outer_select(sql)
    if outer is None:
        return None
    chain = _grain_chain(outer)
    if all(_is_star(s) for s in chain[:-1]) and len(chain) > 1:
        projs = chain[-1].expressions
    elif _is_star(outer):
        return None
    else:
        projs = _effective_projections(chain)
    if any(isinstance(p, exp.Star) for p in projs):
        return None
    return [p.alias_or_name for p in projs]


def trim_is_safe(question: str, sql: str, *, keep_gt: float | None) -> bool:
    """Dropping the ride-along figure leaves the question's entities only if a
    predicate already selected them: ``keep_gt`` on the measure, or a WHERE /
    HAVING that filters on what the question names. Otherwise the figure is
    the only evidence of an unfiltered row set, and hiding it would certify
    every entity (all five warehouses as "almost full") -- abstain instead.
    """
    if keep_gt is not None:
        return True
    outer = _outer_select(sql)
    if outer is None:
        return False
    return _predicate_names_question(_grain_chain(outer), _q_words(question))


def trimmed_sql(sql: str, keep: Sequence[str], *, where_gt: tuple[str, float] | None = None) -> str:
    """The SQL whose rows are the trimmed rows, so ledger and envelope can rebuild them."""
    body = sql.strip().rstrip(";").strip()
    cols = ", ".join(exp.to_identifier(c, quoted=True).sql(dialect=_DIALECT) for c in keep)
    out = f"SELECT {cols} FROM ({body}) AS grain_trim"
    if where_gt is not None:
        ident = exp.to_identifier(where_gt[0], quoted=True).sql(dialect=_DIALECT)
        out += f" WHERE {ident} > {float(where_gt[1])!r}"
    return out


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
