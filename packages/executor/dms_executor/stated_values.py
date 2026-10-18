"""STATED-VALUE-01: a value the question states must restrict the SQL, or the ask abstains.

``cortex_client.qualifiers`` knows quoted strings and the demo's own vocabulary. On a
customer table it cannot tell that "in North" is a condition, so model SQL that dropped it
executed and the unfiltered total answered. This check is data-backed and structural:

1. Every 1-4 word phrase of the question, first word included, is normalised the way the
   stored cells are (DuckDB ``lower`` + ``strip_accents``, runs of non-letters/digits to one
   space). One scan per customer table finds which phrases are stored values of which text
   columns. A phrase inside a longer phrase that is also stored ("North" in "North East") is
   covered by the longer one.
2. A stated value is honoured only when every query block that reads its table carries a
   top-level AND conjunct ``col = 'v'`` or ``col IN (...)`` on that same column (bare, or
   under ``lower``/``upper``/``trim``), and the literal selects stored rows of that value.
   ``IN`` counts only when every listed value is a stated one. Aliases, labels, ``OR``,
   ``<>``, ``LIKE``, subqueries and comments never count.
3. Otherwise the ask abstains ``unhonored_qualifier:named_filter=<phrase>``. A cap hit, a
   lake we cannot read or SQL we cannot analyse abstains ``named_filter=unchecked`` or
   ``named_filter=unparsed``. A false refusal is safe. A stated value the SQL ignores is a
   confident wrong figure.

Only customer relations are probed (``bronze.*``, ``bronze_*`` and any non-demo relation
the SQL reads); the demo tables keep their own guard. Known gap: scripts written without
spaces (CJK) form one token, so a value inside such a question is not isolated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from cortex_client.qualifiers import KIND_NAMED_FILTER, qualifier_reason
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from dms_executor.bronze import quote_ident
from dms_executor.demo_warehouse import DEMO_TABLES, connect_serving

_MAX_WORDS = 64
_MAX_NGRAM = 4
_MAX_TABLES = 64
_MAX_COLUMNS = 512
_TEXT_TYPES = ("VARCHAR", "TEXT", "STRING", "CHAR")
_DEMO = frozenset((*DEMO_TABLES, "meta"))
_WRAPPERS: dict[type[exp.Expression], str] = {
    exp.Lower: "lower",
    exp.Upper: "upper",
    exp.Trim: "trim",
}
_RANK_WORDS = frozenset({"top", "bottom", "first", "last"})


def _norm_sql(expr: str) -> str:
    """The one normalisation, applied in DuckDB to question and cells alike."""
    return (
        f"trim(regexp_replace(lower(strip_accents(CAST({expr} AS VARCHAR))), "
        r"'[^\p{L}\p{N}]+', ' ', 'g'))"
    )


#: Words that never state a value on their own. A phrase made only of these is not probed.
_FUNCTION_WORDS = frozenset(
    "a an the of in on at for to from by with and or not is are was were be been what which "
    "who how many much do does did show me tell give list find get all any each every per "
    "total sum count number average avg mean min max minimum maximum highest lowest top "
    "there their this that these those than over under between".split()
)


@dataclass(frozen=True)
class _Hit:
    rel: tuple[str, str]  # (schema, name), lower case
    sql_rel: str  # quoted relation for probing
    col: str
    value: str  # normalised
    stored: str  # one stored cell, as the abstain names it


def _ngrams(words: list[str]) -> dict[str, list[tuple[int, int]]]:
    """Normalised phrase -> every (start, end) word span it occupies."""
    spans: dict[str, list[tuple[int, int]]] = {}
    for i in range(len(words)):
        for n in range(1, _MAX_NGRAM + 1):
            if i + n > len(words):
                break
            part = words[i : i + n]
            if all(w in _FUNCTION_WORDS for w in part):
                continue
            spans.setdefault(" ".join(part), []).append((i, i + n))
    return spans


def _covered(phrase: str, spans: dict[str, list[tuple[int, int]]], stored: set[str]) -> bool:
    """Every occurrence of the phrase sits inside a longer stored phrase of the question."""
    longer = [s for p in stored if p != phrase for s in spans[p]]
    return all(
        any(a <= s and e <= b and (b - a) > (e - s) for a, b in longer) for s, e in spans[phrase]
    )


def _key(t: exp.Table) -> tuple[str, str]:
    return ((t.db or "main").lower(), (t.name or "").lower())


def _relations(con: object, sql_read: set[tuple[str, str]], grantable: Iterable[str] | None):
    """Customer relations to probe: (schema, name) -> quoted name."""
    rows = con.execute(  # type: ignore[attr-defined]
        "SELECT schema_name, table_name FROM duckdb_tables() "
        "WHERE database_name = current_database() "
        "UNION SELECT schema_name, view_name FROM duckdb_views() "
        "WHERE database_name = current_database() AND NOT internal"
    ).fetchall()
    granted = None if grantable is None else {g.lower() for g in grantable}
    out: dict[tuple[str, str], str] = {}
    for schema, name in rows:
        key = (str(schema).lower(), str(name).lower())
        if key[1] in _DEMO and key[0] == "main":
            continue
        customer = key[0] == "bronze" or key[1].startswith("bronze_")
        if key in sql_read or (
            customer and (granted is None or f"{key[0]}.{key[1]}" in granted or key[1] in granted)
        ):
            out[key] = f"{quote_ident(str(schema))}.{quote_ident(str(name))}"
    return out


def _probe(con: object, rels: dict[tuple[str, str], str], phrases: list[str]) -> list[_Hit] | str:
    hits: list[_Hit] = []
    if len(rels) > _MAX_TABLES:
        return "unchecked"
    total = 0
    for key, rel in sorted(rels.items()):
        cols = [
            str(row[0])
            for row in con.execute(f"DESCRIBE {rel}").fetchall()  # type: ignore[attr-defined]
            if any(t in str(row[1]).upper() for t in _TEXT_TYPES)
            and not str(row[0]).startswith("_")
        ]
        total += len(cols)
        if total > _MAX_COLUMNS:
            return "unchecked"
        if not cols:
            continue
        union = " UNION ALL ".join(
            f"SELECT {i} AS c, {_norm_sql(quote_ident(c))} AS v, {quote_ident(c)} AS raw FROM {rel}"
            for i, c in enumerate(cols)
        )
        found = con.execute(  # type: ignore[attr-defined]
            f"SELECT c, v, min(raw) FROM ({union}) WHERE list_contains(?, v) GROUP BY c, v",
            [phrases],
        ).fetchall()
        hits += [_Hit(key, rel, cols[int(c)], str(v), str(raw)) for c, v, raw in found]
    return hits


def _conjuncts(cond: exp.Expression | None) -> list[exp.Expression]:
    if cond is None:
        return []
    while isinstance(cond, exp.Paren):
        cond = cond.this
    if isinstance(cond, exp.And):
        return [c for part in cond.flatten() for c in _conjuncts(part)]
    return [cond]


def _column_expr(node: exp.Expression, col: str, alias: str) -> str | None:
    """SQL for ``node`` rebuilt over ``col`` when it is that column, bare or under wrappers."""
    wrappers: list[str] = []
    while not isinstance(node, exp.Column):
        while isinstance(node, exp.Paren):
            node = node.this
        fn = _WRAPPERS.get(type(node))
        if fn is None or any(k != "this" and v for k, v in node.args.items()):
            return None
        wrappers.append(fn)
        node = node.this
    if node.name.casefold() != col.casefold():
        return None
    if node.table and node.table.casefold() != alias.casefold():
        return None
    sql = quote_ident(col)
    for fn in reversed(wrappers):
        sql = f"{fn}({sql})"
    return sql


def _restrictions(scope_expr: exp.Expression) -> list[exp.Expression]:
    """Top-level AND conjuncts of WHERE and of inner-join ON clauses."""
    out = _conjuncts(scope_expr.args.get("where") and scope_expr.args["where"].this)
    for join in scope_expr.args.get("joins") or []:
        if not join.side:
            out += _conjuncts(join.args.get("on"))
    return out


def _filter_values(conj: exp.Expression, hit: _Hit, alias: str) -> tuple[str, list[str]] | None:
    """(column SQL, literal list) when ``conj`` is ``col = 'v'`` or ``col IN ('v', ...)``."""
    if isinstance(conj, exp.EQ):
        for side, other in ((conj.this, conj.expression), (conj.expression, conj.this)):
            col_sql = _column_expr(side, hit.col, alias)
            if col_sql and isinstance(other, exp.Literal) and other.is_string:
                return col_sql, [str(other.this)]
    if isinstance(conj, exp.In) and not conj.args.get("query") and not conj.args.get("unnest"):
        col_sql = _column_expr(conj.this, hit.col, alias)
        lits = conj.expressions
        if col_sql and lits and all(isinstance(x, exp.Literal) and x.is_string for x in lits):
            return col_sql, [str(x.this) for x in lits]
    return None


def _selects_value(con: object, hit: _Hit, col_sql: str, literal: str, value: str) -> bool:
    row = con.execute(  # type: ignore[attr-defined]
        f"SELECT 1 FROM {hit.sql_rel} "
        f"WHERE {col_sql} = ? AND {_norm_sql(quote_ident(hit.col))} = ? LIMIT 1",
        [literal, value],
    ).fetchone()
    return row is not None


def _every_row_holds(con: object, hit: _Hit) -> bool:
    """The column holds the stated value on every row (no NULLs): a filter changes nothing."""
    row = con.execute(  # type: ignore[attr-defined]
        f"SELECT count(*) FILTER (WHERE {_norm_sql(quote_ident(hit.col))} IS DISTINCT FROM ?) "
        f"FROM {hit.sql_rel}",
        [hit.value],
    ).fetchone()
    return row is not None and row[0] == 0


def _honoured(con: object, hit: _Hit, scopes: list, stated_here: set[str]) -> bool:
    """Every block that reads the hit's table restricts its column to stated values only."""
    readers = 0
    for scope in scopes:
        for alias, src in scope.sources.items():
            if not isinstance(src, exp.Table) or _key(src) != hit.rel:
                continue
            readers += 1
            ok = False
            for conj in _restrictions(scope.expression):
                got = _filter_values(conj, hit, alias)
                if got is None:
                    continue
                col_sql, lits = got
                matched: set[str] = set()
                for lit in lits:
                    vals = [v for v in stated_here if _selects_value(con, hit, col_sql, lit, v)]
                    if not vals:
                        break
                    matched.update(vals)
                else:
                    if hit.value in matched:
                        ok = True
                        break
            if not ok:
                return False
    return readers > 0


def _identifiers(scopes: list) -> set[str]:
    names: set[str] = set()
    for scope in scopes:
        for src in scope.sources.values():
            if isinstance(src, exp.Table) and src.name:
                names.add(src.name.lower())
        for c in scope.expression.find_all(exp.Column):
            if c.name:
                names.add(c.name.lower())
    return names | {n[len("bronze_") :] for n in names if n.startswith("bronze_")}


def _limits(root: exp.Expression) -> set[str]:
    return {
        str(lim.expression.this)
        for lim in root.find_all(exp.Limit)
        if isinstance(lim.expression, exp.Literal) and not lim.expression.is_string
    }


def dropped_stated_value_reason(
    question: str,
    sql: str,
    warehouse: Path | None,
    *,
    grantable: Iterable[str] | None = None,
) -> str | None:
    """``unhonored_qualifier:named_filter=<value>`` when the SQL leaves a stated value open."""
    if warehouse is None or not Path(warehouse).is_file():
        return None
    try:
        roots = [r for r in sqlglot.parse(sql, read="duckdb") if r is not None]
        scopes = [s for r in roots for s in traverse_scope(r)]
    except Exception:  # noqa: BLE001 - SQL we cannot analyse proves nothing about its filters
        return qualifier_reason(KIND_NAMED_FILTER, "unparsed")
    if not roots or not scopes:
        return qualifier_reason(KIND_NAMED_FILTER, "unparsed")
    sql_read = {_key(s) for sc in scopes for s in sc.sources.values() if isinstance(s, exp.Table)}
    con = connect_serving(Path(warehouse))
    try:
        rels = _relations(con, sql_read, grantable)
        if not rels:
            return None
        row = con.execute(f"SELECT {_norm_sql('?')}", [question or ""]).fetchone()
        words = str(row[0] if row else "").split()
        if len(words) > _MAX_WORDS:
            return qualifier_reason(KIND_NAMED_FILTER, "unchecked")
        spans = _ngrams(words)
        if not spans:
            return None
        hits = _probe(con, rels, list(spans))
        if isinstance(hits, str):
            return qualifier_reason(KIND_NAMED_FILTER, hits)
        stored = {h.value for h in hits}
        stated = {v for v in stored if not _covered(v, spans, stored)}
        idents = _identifiers(scopes)
        limits = _limits(roots[0])
        for value in sorted(stated, key=lambda v: (-len(v.split()), v)):
            at = [h for h in hits if h.value == value]
            # "revenue in USD" over a table whose every row is USD: the unfiltered figure
            # is the filtered one. Only for a table the SQL reads.
            if any(h.rel in sql_read and _every_row_holds(con, h) for h in at):
                continue
            if any(
                _honoured(
                    con,
                    h,
                    scopes,
                    {
                        x.value
                        for x in hits
                        if x.rel == h.rel and x.col == h.col and x.value in stated
                    },
                )
                for h in at
            ):
                continue
            # A word naming a relation or column the SQL reads, stored only in tables it does
            # not read, is the subject of the question, not a condition on it.
            if (
                " " not in value
                and (value in idents or value.rstrip("s") in idents or f"{value}s" in idents)
                and all(h.rel not in sql_read for h in at)
            ):
                continue
            # "top 5": the number is the ranking cut the SQL's LIMIT carries.
            if (
                value.isdigit()
                and value in limits
                and any(s > 0 and words[s - 1] in _RANK_WORDS for s, _ in spans[value])
            ):
                continue
            return qualifier_reason(KIND_NAMED_FILTER, min(h.stored for h in at))
    except Exception:  # noqa: BLE001 - a lake we cannot read cannot vouch for the filters
        return qualifier_reason(KIND_NAMED_FILTER, "unchecked")
    finally:
        con.close()
    return None
