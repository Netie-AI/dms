"""GRAIN-GUARD-01: the answer's grain and columns must match the question.

L2_VALIDATED used to certify any SQL that parsed, validated and executed. The
15 oracle-wrong answers on the curated pack all carried it: grouped by the
measure's own input (``GROUP BY capacity_kg`` for "capacity utilisation"),
one row per SKU where a single total was asked, or a list ask ("which
locations ...") answered with an aggregate the question never named.

Fail closed, like Cortex ``manifest.py``: a query this gate cannot fully
analyse is one it cannot prove has the question's grain, so it abstains
``grain_unanalysable:<why>``. The shapes it can analyse:

- one outermost SELECT (no set operation, no window function);
- GROUP BY keys that are bare columns, or a time bucket (date_trunc /
  strftime / extract / year ...) over a bare column, named directly, by
  output alias or by position -- not GROUP BY ALL, ROLLUP / CUBE / GROUPING
  SETS, an out-of-range position, or any other expression;
- CTEs and subqueries that neither group nor aggregate, except the entity
  dedup ``SELECT key, ANY_VALUE(attr) ... GROUP BY key`` (one row per key,
  no measure), or ``SELECT DISTINCT <bare columns>``, which the outer query
  may count and group by but never aggregate over;
- no LIMIT / OFFSET / DISTINCT / QUALIFY inside a derived table, no
  TABLESAMPLE, no outer OFFSET: each decides which rows the answer is
  computed over.

A scalar ask's outputs may not be row pickers (ANY_VALUE / FIRST / ARG_MAX:
one row's value), and a "total" needs a SUM or COUNT. After execution, a
result that fills the outer LIMIT on a question with no top-N abstains
``grain_mismatch:truncated``.

On an analysable query: a scalar ask needs an all-aggregate, ungrouped
outermost SELECT and exactly one executed row; every GROUP BY column must be
named by the question (directly or through the ontology spine's
``column_vocabulary``), or be the entity key on an ask with no by/per/each
dimension; a which/list/show ask may not carry a figure it never named.
Anything else is a named ABSTAIN. No rows are ever dropped to make a shape
fit: the figure that shows a list is unfiltered is the evidence, not noise.

sqlglot lives here and in sql_currency. Swap: a Cortex HTTP grain-check behind
the same functions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from sqlglot import exp, parse_one

from dms_executor.semantic_retrieve import load_ontology_spine

_DIALECT = "duckdb"

REASON_UNREQUESTED_GRAIN = "unrequested_grain"
REASON_GRAIN_MISMATCH = "grain_mismatch"
REASON_UNREQUESTED_MEASURE = "unrequested_measure"
REASON_UNREQUESTED_COLUMN = "unrequested_column"
REASON_UNANALYSABLE = "grain_unanalysable"
SCALAR_EXPECTED = f"{REASON_GRAIN_MISMATCH}:scalar_expected"
TOTAL_NOT_SUMMED = f"{REASON_GRAIN_MISMATCH}:total_not_summed"
TRUNCATED = f"{REASON_GRAIN_MISMATCH}:truncated"

GRAIN_REASONS = frozenset(
    {
        REASON_UNREQUESTED_GRAIN,
        REASON_GRAIN_MISMATCH,
        REASON_UNREQUESTED_MEASURE,
        REASON_UNREQUESTED_COLUMN,
        REASON_UNANALYSABLE,
    }
)

# A single figure is asked for.
_SCALAR_CUE = re.compile(
    r"\b(total|how\s+many|how\s+much|overall|in\s+total|sum\s+of|number\s+of|"
    r"count\s+of|altogether|average|avg|mean|median)\b",
    re.I,
)
# A sum is asked for: MAX / MIN / a picked row is not a total.
_TOTAL_CUE = re.compile(r"\b(total|sum\s+of|altogether)\b", re.I)
_NOT_SUM_CUE = re.compile(
    r"\b(average|avg|mean|median|max\w*|min\w*|highest|lowest|largest|smallest|"
    r"biggest|peak|most|least)\b",
    re.I,
)
# The question itself asks for only the first N rows.
_RANK_CUE = re.compile(
    r"\b(top|bottom|first|highest|lowest|most|least|largest|smallest|biggest|"
    r"best|worst|rank\w*|limit)\b",
    re.I,
)
# One row's value dressed as an aggregate.
_PICKERS: tuple[type[exp.Expression], ...] = (
    exp.AnyValue,
    exp.First,
    exp.Last,
    exp.ArgMax,
    exp.ArgMin,
    exp.FirstValue,
    exp.LastValue,
)
# Clauses that change which rows a derived table holds.
_ROW_SET_CLAUSES = ("limit", "offset", "fetch", "qualify", "distinct", "sample")
# The question names a breakdown, a ranking, a list or a comparison.
# "across all X" is a scope, not a breakdown.
_BREAKDOWN_CUE = re.compile(
    r"\b(by|per|each|every|which|list|top|bottom|rank\w*|breakdown|split|"
    r"across(?!\s+(?:all|the\s+whole|every)\b)|group(?:ed)?|vs|versus|compar\w*|"
    r"trend\w*|over\s+time|daily|weekly|monthly|quarterly|yearly|annual\w*|"
    r"what\s+are|show\s+all)\b",
    re.I,
)
# An explicit dimension: the words after by / per / each / every.
_BY_PHRASE = re.compile(r"\b(?:by|per|each|every)\s+((?:[a-z0-9-]+\s*){1,3})", re.I)
_SCOPE_WORDS = frozenset(
    {"in", "for", "at", "of", "on", "from", "with", "during", "where", "that", "which",
     "who", "over", "across", "within", "inside", "under", "to"}
)
# A time grain the question asks for.
_TIME_CUE = re.compile(
    r"\b(daily|weekly|monthly|quarterly|yearly|annual\w*|trend\w*|over\s+time|"
    r"timeline|history|(?:by|per|each|every)\s+(?:day|date|week|month|quarter|year))\b",
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
# Unit / plumbing tokens carry no meaning on their own in a name.
_UNIT_TOKENS = frozenset(
    {
        "pct", "percent", "myr", "usd", "eur", "sgd", "kg", "kgs", "qty", "amt",
        "sum", "total", "avg", "cnt", "num", "val", "f", "d0", "d1", "d2",
    }
)
# Numeric measure-looking plain columns (not keys, not labels).
_MEASURE_SUFFIX = re.compile(r"_(kg|myr|usd|eur|sgd|pct|days|score|qty|amount|cost|value)$", re.I)
# Entity keys / labels: the natural grain of an entity list.
_KEY_COLUMN = re.compile(r"(^|_)(id|code|name|sku|no|number|key|label|title)$", re.I)
_TIME_COLUMN = re.compile(r"(^|_)(day|date|week|month|quarter|year|ts|period|time|at)$", re.I)
_TIME_BUCKETS: tuple[type[exp.Expression], ...] = (
    exp.DateTrunc,
    exp.TimestampTrunc,
    exp.DatetimeTrunc,
    exp.TimeTrunc,
    exp.TimeToStr,
    exp.Extract,
    exp.Year,
    exp.Quarter,
    exp.Month,
    exp.Week,
    exp.Day,
    exp.DayOfWeek,
)
_WORD = re.compile(r"[a-z0-9]+")


# --- words -------------------------------------------------------------------


def _sing(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


@lru_cache(maxsize=1)
def _vocabulary() -> tuple[tuple[tuple[str, ...], str], ...]:
    """(phrase words, column token) from the ontology spine, longest phrase first."""
    raw = (load_ontology_spine() or {}).get("column_vocabulary") or {}
    out: list[tuple[tuple[str, ...], str]] = []
    if isinstance(raw, dict):
        for token, phrases in raw.items():
            for phrase in phrases or []:
                words = tuple(_sing(w) for w in _WORD.findall(str(phrase).lower()))
                if words:
                    out.append((words, str(token).lower()))
    out.sort(key=lambda item: -len(item[0]))
    return tuple(out)


def _expand(words: Sequence[str]) -> list[str]:
    """Question words plus the column tokens the spine vocabulary maps them to.

    A multi-word phrase consumes its words, so "product type" adds category
    and leaves no "product" behind to name a SKU grain.
    """
    ws = [_sing(w) for w in words]
    used = [False] * len(ws)
    extra: list[str] = []
    for phrase, token in _vocabulary():
        n = len(phrase)
        for i in range(len(ws) - n + 1):
            if any(used[i : i + n]) or tuple(ws[i : i + n]) != phrase:
                continue
            extra.append(token)
            if n > 1:
                for j in range(i, i + n):
                    used[j] = True
    return [w for w, u in zip(ws, used, strict=True) if not u] + extra


def _q_words(question: str) -> list[str]:
    return _expand(_WORD.findall(question.lower()))


def _tokens(name: str) -> list[str]:
    return [
        _sing(t)
        for t in re.split(r"[^a-z0-9]+", name.lower())
        if len(t) >= 3 and t not in _UNIT_TOKENS
    ]


def _word_match(tok: str, w: str) -> bool:
    return w == tok or (len(tok) >= 5 and w.startswith(tok[:5])) or (
        len(w) >= 5 and tok.startswith(w[:5])
    )


def _overlaps(name: str, words: Sequence[str]) -> bool:
    """A name token matches a question word on a shared 5-char stem or whole."""
    return any(_word_match(tok, w) for tok in _tokens(name) for w in words)


def _by_words(question: str) -> list[str]:
    """The dimension words after by/per/each/every, up to a scope preposition.

    "value by category in warehouse A" names category; "in warehouse A" is a
    filter, not a second dimension.
    """
    out: list[str] = []
    for m in _BY_PHRASE.finditer(question.lower()):
        for w in _WORD.findall(m.group(1)):
            if w in _SCOPE_WORDS:
                break
            out.append(w)
    return _expand(out)


def _head_words(question: str) -> list[str]:
    """Words before the first by/per/each/every: the entity the ask leads with."""
    m = _BY_PHRASE.search(question.lower())
    head = question.lower()[: m.start()] if m else question.lower()
    return _expand(_WORD.findall(head))


# --- SQL shape -----------------------------------------------------------------


def _parse(sql: str) -> exp.Expression | None:
    try:
        return parse_one(sql, read=_DIALECT)
    except Exception:  # noqa: BLE001 -- unparseable is unanalysable, reported by name
        return None


def _unalias(proj: exp.Expression) -> exp.Expression:
    return proj.this if isinstance(proj, exp.Alias) else proj


def _out_name(proj: exp.Expression) -> str:
    if isinstance(proj, exp.Alias):
        return proj.alias
    if isinstance(proj, exp.Column) and not isinstance(proj.this, exp.Star):
        return proj.name
    return proj.sql(dialect=_DIALECT)


def _is_agg(node: exp.Expression) -> bool:
    return node.find(exp.AggFunc) is not None


def _dedup_outputs(select: exp.Select) -> set[str] | None:
    """ANY_VALUE output names when ``select`` is an entity dedup, else None.

    ``SELECT sku, ANY_VALUE(category) AS category FROM inventory GROUP BY sku``:
    bare-column keys, every other output ANY_VALUE of a bare column, no
    HAVING, nothing nested. One row per key and no measure.
    """
    group = select.args.get("group")
    if group is None or select.args.get("having") is not None:
        return None
    if any(group.args.get(k) for k in ("all", "rollup", "cube", "grouping_sets")):
        return None
    keys = set()
    for key in group.expressions:
        if not isinstance(key, exp.Column):
            return None
        keys.add(key.name.lower())
    if any(s is not select for s in select.find_all(exp.Select)):
        return None
    out: set[str] = set()
    for proj in select.expressions:
        inner = _unalias(proj)
        if isinstance(inner, exp.Column) and inner.name.lower() in keys:
            continue
        if isinstance(inner, exp.IgnoreNulls):
            inner = inner.this
        if isinstance(inner, exp.AnyValue) and isinstance(inner.this, exp.Column):
            out.add(proj.alias_or_name.lower())
            continue
        return None
    return out


def _distinct_outputs(select: exp.Select) -> set[str] | None:
    """Output names when ``select`` is ``SELECT DISTINCT <bare columns>``, else None.

    The same entity dedup as the ANY_VALUE form (a dimension to join or
    count), so the outer query may count and group by it but never aggregate
    its values: summing DISTINCT values drops equal lots.
    """
    if not select.args.get("distinct") or select.args["distinct"].args.get("on"):
        return None
    if select.args.get("group") is not None or _is_agg(select):
        return None
    if any(s is not select for s in select.find_all(exp.Select)):
        return None
    out: set[str] = set()
    for proj in select.expressions:
        inner = _unalias(proj)
        if not isinstance(inner, exp.Column) or isinstance(inner.this, exp.Star):
            return None
        out.add(proj.alias_or_name.lower())
    return out


def _dedup_alias(select: exp.Select) -> str:
    parent = select.parent
    if isinstance(parent, (exp.Subquery, exp.CTE)):
        return parent.alias_or_name.lower()
    return ""


def _time_bucket_column(node: exp.Expression) -> exp.Column | None:
    if not isinstance(node, _TIME_BUCKETS):
        return None
    cols = list(node.find_all(exp.Column))
    return cols[0] if len(cols) == 1 else None


def _group_keys(select: exp.Select) -> tuple[list[tuple[str, bool]], str | None]:
    """(column name, is time bucket) per GROUP BY key, or an unanalysable reason."""
    group = select.args.get("group")
    if group is None:
        return [], None
    if group.args.get("all"):
        return [], "group_by_all"
    for kind in ("rollup", "cube", "grouping_sets"):
        if group.args.get(kind):
            return [], kind
    aliases = {
        proj.alias.lower(): proj.this
        for proj in select.expressions
        if isinstance(proj, exp.Alias)
    }
    projs = list(select.expressions)
    out: list[tuple[str, bool]] = []
    for key in group.expressions:
        node: exp.Expression = key
        if isinstance(key, exp.Literal):
            # GROUP BY 1 is analysable only as the projection it points at;
            # that projection must itself be a bare column or a time bucket.
            idx = int(key.this) - 1 if key.is_int else -1
            if not 0 <= idx < len(projs):
                return [], "positional_group_key"
            node = _unalias(projs[idx])
        elif isinstance(key, exp.Column) and not key.table and key.name.lower() in aliases:
            node = aliases[key.name.lower()]
        if isinstance(node, exp.Column) and not isinstance(node.this, exp.Star):
            out.append((node.name.lower(), False))
            continue
        bucket = _time_bucket_column(node)
        if bucket is not None:
            out.append((bucket.name.lower(), True))
            continue
        return [], "expression_group_key"
    return out, None


def _unanalysable(tree: exp.Expression | None) -> str | None:
    """Why the gate cannot analyse this SQL's grain, or None."""
    if tree is None:
        return "parse"
    if not isinstance(tree, exp.Select) or tree.find(exp.SetOperation) is not None:
        return "set_operation"
    if tree.find(exp.Window) is not None:
        return "window_function"
    if tree.find(exp.TableSample) is not None:
        return "sample"
    if tree.args.get("offset") is not None or tree.args.get("fetch") is not None:
        return "offset"
    dedup_names: set[str] = set()
    dedup_aliases: set[str] = set()
    for inner in tree.find_all(exp.Select):
        if inner is tree:
            continue
        # A LIMIT / OFFSET / DISTINCT inside a derived table decides which
        # rows the answer is computed over; the gate cannot prove that is the
        # question's row set.
        distinct = _distinct_outputs(inner)
        for clause in _ROW_SET_CLAUSES:
            if inner.args.get(clause) and not (clause == "distinct" and distinct):
                return f"nested_{clause}"
        if distinct:
            names: set[str] | None = distinct
        elif inner.args.get("group") is None and not _is_agg(inner):
            continue
        else:
            names = _dedup_outputs(inner)
        if names is None:
            return "nested_grouping"
        dedup_names |= names
        alias = _dedup_alias(inner)
        if alias:
            dedup_aliases.add(alias)
    # The outer query may count dedup rows and group by their attributes,
    # but aggregating an ANY_VALUE pick is summing an arbitrary lot.
    for agg in tree.find_all(exp.AggFunc):
        if agg.find_ancestor(exp.Select) is not tree:
            continue
        for col in agg.find_all(exp.Column):
            if col.name.lower() in dedup_names and (
                not col.table or col.table.lower() in dedup_aliases
            ):
                return "nested_grouping"
    if any(isinstance(p, exp.Star) or (
        isinstance(p, exp.Column) and isinstance(p.this, exp.Star)
    ) for p in tree.expressions):
        return "star_projection"
    _keys, why = _group_keys(tree)
    return why


def _agg_inputs_of(proj: exp.Expression) -> set[str]:
    return {c.name.lower() for agg in proj.find_all(exp.AggFunc) for c in agg.find_all(exp.Column)}


def _all_aggregate(select: exp.Select) -> bool:
    """Every output is an aggregate and no bare column sits outside one."""
    for proj in select.expressions:
        inner = _unalias(proj)
        if not _is_agg(inner) or inner.find(*_PICKERS) is not None:
            # ANY_VALUE / FIRST / ARG_MAX is one row's value, not an aggregate.
            return False
        if any(c.find_ancestor(exp.AggFunc) is None for c in inner.find_all(exp.Column)):
            return False
    return True


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


def _entity_ask(question: str) -> bool:
    """The question names a breakdown, ranking or list: an entity key may be its grain."""
    return bool(_BREAKDOWN_CUE.search(question) or _LIST_HEAD.search(question))


def _group_key_reason(
    col: str, *, is_bucket: bool, question: str, words: Sequence[str], inputs: set[str]
) -> str | None:
    by_words = _by_words(question)
    if _overlaps(col, by_words):
        return None
    # With an explicit by/per/each dimension, a key must be that dimension or
    # the entity the ask leads with ("Top 5 SKUs by revenue"); a word in a
    # trailing scope ("... in warehouse A") names a filter, not a grain.
    named = _head_words(question) if by_words else words
    if is_bucket or _TIME_COLUMN.search(col):
        # A time grain nobody asked for is a breakdown too. (A time grain
        # asked for and missing is QUAL-GUARD-01's.)
        if _TIME_CUE.search(question) or _overlaps(col, named):
            return None
        return f"{REASON_UNREQUESTED_GRAIN}:{col}"
    if col in inputs:
        return f"{REASON_UNREQUESTED_GRAIN}:{col}"
    if _overlaps(col, named):
        return None
    # An entity key with no dimension named is the grain of an entity ask
    # only ("Which SKUs ..."); on "What is the average unit cost?" a per-SKU
    # key is a breakdown nobody asked for.
    if _KEY_COLUMN.search(col) and not by_words and _entity_ask(question):
        return None
    return f"{REASON_UNREQUESTED_GRAIN}:{col}"


def grain_mismatch_reason(question: str, sql: str) -> str | None:
    """Named reason when the SQL's grain or projection is not the question's.

    SQL-only; runs before submit. ``None`` means the gate analysed the whole
    query and found the question's grain and columns.
    """
    tree = _parse(sql)
    why = _unanalysable(tree)
    if why:
        return f"{REASON_UNANALYSABLE}:{why}"
    assert isinstance(tree, exp.Select)
    select = tree
    words = _q_words(question)
    keys, _ = _group_keys(select)

    # (a) a single figure asked: all aggregates, no GROUP BY. A grouped query
    # is a breakdown whatever the row count (ORDER BY 2 DESC LIMIT 1 over
    # per-SKU sums is one SKU, not the total); a bare column is one row's
    # value, not an aggregate.
    if scalar_asked(question):
        if select.args.get("group") is not None or not _all_aggregate(select):
            return SCALAR_EXPECTED
        if (
            _TOTAL_CUE.search(question)
            and not _NOT_SUM_CUE.search(question)
            and select.find(exp.Sum, exp.Count) is None
        ):
            # "total" answered by MAX / MIN / AVG is one lot, not the sum.
            return TOTAL_NOT_SUMMED
        return None

    # (b) a dimension nobody asked for: the measure's own input (capacity_kg
    # for "capacity utilisation"), or a column the question never names.
    inputs = {c for p in select.expressions for c in _agg_inputs_of(p)}
    for col, is_bucket in keys:
        grain_why = _group_key_reason(
            col, is_bucket=is_bucket, question=question, words=words, inputs=inputs
        )
        if grain_why:
            return grain_why

    # (c) list ask: the answer is entities. An aggregate output is requested
    # by an aggregation cue, or by name ("show" also by its input column). A
    # COUNT without a count cue and no HAVING is COUNT(*) FILTER, the
    # predicate turned into a tally: its zero rows are entities the predicate
    # excludes. A ride-along figure on "which / list" is the only sign the
    # row set is unfiltered, so it abstains -- nothing is trimmed.
    head = _list_head(question)
    if not head:
        return None
    where_cols = _where_columns(select)
    having = select.args.get("having") is not None
    for proj in select.expressions:
        inner = _unalias(proj)
        name = _out_name(proj)
        if _is_agg(inner):
            if inner.find(exp.Count) is not None and not _COUNT_CUE.search(question) and not having:
                return f"{REASON_UNREQUESTED_MEASURE}:{name}"
            if _overlaps(name, words):
                continue
            if head == "show" and any(_overlaps(c, words) for c in _agg_inputs_of(inner)):
                continue
            return f"{REASON_UNREQUESTED_MEASURE}:{name}"
        if isinstance(inner, exp.Column):
            col = inner.name.lower()
            if (
                _MEASURE_SUFFIX.search(col)
                and col not in where_cols
                and not _overlaps(col, words)
                and not _overlaps(name, words)
            ):
                return f"{REASON_UNREQUESTED_COLUMN}:{col}"
    return None


def scalar_rows_reason(question: str, rows: Sequence[Any]) -> str | None:
    """A single figure was asked; anything but exactly one row is not it."""
    if scalar_asked(question) and len(rows) != 1:
        return SCALAR_EXPECTED
    return None


def _outer_limit(sql: str) -> int | None:
    tree = _parse(sql)
    limit = tree.args.get("limit") if isinstance(tree, exp.Select) else None
    node = limit.args.get("expression") if isinstance(limit, exp.Limit) else None
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    return None


def rows_mismatch_reason(question: str, sql: str, rows: Sequence[Any]) -> str | None:
    """Post-execution grain check on the rows the answer would show.

    A scalar ask needs exactly one row. A result that fills the outer LIMIT
    on a question that asked for no top-N may be missing groups: the gate
    cannot prove it is the whole answer, so it abstains.
    """
    why = scalar_rows_reason(question, rows)
    if why:
        return why
    limit = _outer_limit(sql)
    if limit is not None and len(rows) >= limit and not _RANK_CUE.search(question):
        return TRUNCATED
    return None


def grain_abstain_text(reason: str) -> str:
    """Rendered ABSTAIN text for a grain reason. States no figure."""
    head, _, detail = str(reason).partition(":")
    if head == REASON_UNREQUESTED_GRAIN:
        what = f"it was broken down by {detail}, which you did not ask for"
    elif reason == SCALAR_EXPECTED:
        what = "you asked for a single figure and the query did not return exactly one total"
    elif reason == TOTAL_NOT_SUMMED:
        what = "you asked for a total and the query did not add the figures up"
    elif reason == TRUNCATED:
        what = "the query cut the result off at its row limit, so some of it may be missing"
    elif head == REASON_UNREQUESTED_MEASURE:
        what = f"it adds a figure ({detail}) that you did not ask for"
    elif head == REASON_UNREQUESTED_COLUMN:
        what = f"it adds a column ({detail}) that you did not ask for"
    elif head == REASON_UNANALYSABLE:
        what = (
            "its shape could not be fully checked against your question "
            f"({detail.replace('_', ' ')}), so I cannot prove its grain"
        )
    else:
        what = "its shape does not match what you asked"
    return (
        "I cannot certify that answer: "
        f"{what} (gap: {reason}). I am not showing it as a validated result."
    )
