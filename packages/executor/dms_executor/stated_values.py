"""STATED-VALUE-01: a value the question states must restrict the SQL, or the ask abstains.

``cortex_client.qualifiers`` knows quoted strings and the demo's own vocabulary. On a
customer table it cannot tell that "in North" is a condition, so model SQL that dropped it
executed and the unfiltered total answered. This check is data-backed and structural:

1. The question is NFKC-folded and every contiguous run of its words is normalised the way
   the stored cells are (DuckDB: format characters dropped, ``strip_accents``, ``lower``,
   ``ß``->``ss``, apostrophes and dots dropped, other separators to one space; ``+ % # &``
   are kept, so ``B+`` is not ``B``; a leading minus stays, so ``-5`` is not ``5``). One
   scan per customer relation finds which runs are stored values of which text or integer
   columns. A run made only of function words counts
   only when written in capitals ("in OR", "grade A"). A run inside a longer stored run is
   covered by it ("North" in "North East").
2. Every column of every relation the SQL reads that stores the value must be restricted,
   in every query scope that reads it: a top-level AND conjunct of
   WHERE, an INNER join's ON, or HAVING on a grouped column, of the form ``col = lit`` or
   ``col IN (...)`` (bare, or under ``lower``/``upper``/``trim``). The conjunct may sit on a
   column joined to it by a bare ``a.col = b.col`` conjunct, or one pass-through scope up.
   The literals must select exactly the stored rows of stated values: every spelling of the
   value (rule 12), nothing else, and an ``IN`` of several values only when the question
   lists them ("North and South"). A value the question excludes ("excluding Alder") needs
   ``<>`` / ``NOT IN`` with the same exactness and no NULL rows dropped.
3. Literals, aliases, labels, ``OR``, ``LIKE``, ANTI/SEMI/outer joins, subqueries, column
   alias lists, reads outside any scope (``SUMMARIZE``) and comments never count. A value
   stored only in a relation the SQL does not read abstains, unless it names a relation or
   catalog column the SQL reads. A value every row of a read relation holds needs no filter,
   unless a filter of the SQL names that column.
4. Otherwise the ask abstains ``unhonored_qualifier:named_filter=<stored value>``. A cap
   hit, a lake we cannot read or SQL we cannot analyse abstains ``named_filter=unchecked``
   or ``named_filter=unparsed``. A false refusal is safe. A stated value the SQL ignores is a
   confident wrong figure.

Only customer relations are probed (non-demo; the granted ones when ``grantable`` is given,
plus any the SQL reads); the demo tables keep their own guard. Known gaps: scripts written
without spaces (CJK) form one token; full-width characters inside stored cells are not
folded; an all-lowercase function-word value ("status in") is not probed.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from cortex_client.qualifiers import KIND_NAMED_FILTER, qualifier_reason
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from dms_executor.bronze import quote_ident
from dms_executor.demo_warehouse import DEMO_TABLES, SERVING_DIALECT, connect_serving

_MAX_WORDS = 64
_MAX_TABLES = 256
_MAX_COLUMNS = 4096
_MAX_PASS_THROUGH = 4
_PROBED_TYPES = ("VARCHAR", "TEXT", "STRING", "CHAR", "ENUM", "INT")
_UNPROBED_TYPES = ("[", "STRUCT", "MAP", "UNION", "INTERVAL")
_DEMO = frozenset((*DEMO_TABLES, "meta"))
_WRAPPERS: dict[type[exp.Expression], str] = {
    exp.Lower: "lower",
    exp.Upper: "upper",
    exp.Trim: "trim",
}
_SEP = r"[\s\p{Z},;:!?\"“”()\[\]{}/\\_|·•…–—-]+"

#: Words that never state a value unless written in capitals. "i" is the pronoun.
_FUNCTION_WORDS = frozenset(
    "a an the of in on at for to from by with and or not is are was were be been what which "
    "who how many much do does did show me tell give list find get all any each every per "
    "total sum count number average avg mean min max minimum maximum highest lowest top "
    "there their this that these those than over under between i".split()
)
#: Words that may sit between the members of a stated list ("North and the South").
_CONNECTORS = frozenset({"and", "or", "&", "vs", "versus", "plus", "the"})
_NEGATORS = frozenset(
    "excluding except without ignoring ignore exclude excludes omitting omit minus besides "
    "not nor non excl".split()
)
_NEGATOR_PAIRS = frozenset(
    {("other", "than"), ("apart", "from"), ("aside", "from"), ("leaving", "out"), ("leave", "out")}
)
_RANK_WORDS = frozenset(
    "top bottom first last largest smallest biggest highest lowest best worst greatest most "
    "least".split()
)
_TIME_UNITS = frozenset(
    "day days week weeks month months quarter quarters year years hour hours minute minutes".split()
)


def _norm_sql(expr: str) -> str:
    """The one normalisation, applied in DuckDB to question words and cells alike."""
    return (
        "trim(regexp_replace(regexp_replace(replace(lower(strip_accents(regexp_replace("
        "regexp_replace("
        f"CAST({expr} AS VARCHAR), '\\p{{Cf}}', '', 'g'), '(^|\\s)-(\\p{{N}})', '\\1~\\2', 'g'))), "
        f"'ß', 'ss'), '[''‘’`´.]', '', 'g'), '{_SEP}', ' ', 'g'))"
    )


@dataclass(frozen=True)
class _Hit:
    rel: tuple[str, str]  # (schema, name), lower case
    sql_rel: str  # quoted relation for probing
    col: str
    value: str  # normalised
    stored: str  # one stored cell, as the abstain names it


@dataclass
class _Question:
    words: list[str]
    spans: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    def first(self, value: str) -> tuple[int, int]:
        return min(self.spans[value])


def _question(con: Any, question: str) -> _Question | None:
    raw = unicodedata.normalize("NFKC", question or "").split()
    if len(raw) > _MAX_WORDS:
        return None
    if not raw:
        return _Question([])
    row = con.execute(
        f"SELECT list_transform(?::VARCHAR[], lambda t: {_norm_sql('t')})", [raw]
    ).fetchone()
    words: list[str] = []
    marked: list[bool] = []
    for i, (orig, normed) in enumerate(zip(raw, row[0] if row else [], strict=True)):
        letters = [ch for ch in orig if ch.isalpha()]
        caps = bool(letters) and (
            all(ch.isupper() for ch in letters) or (i > 0 and letters[0].isupper())
        )
        for w in str(normed or "").split():
            words.append(w)
            marked.append(caps and w != "i")
    if len(words) > _MAX_WORDS:
        return None
    q = _Question(words)
    for i in range(len(words)):
        for j in range(i + 1, len(words) + 1):
            if all(w in _FUNCTION_WORDS for w in words[i:j]) and not all(marked[i:j]):
                continue
            q.spans.setdefault(" ".join(words[i:j]), []).append((i, j))
    return q


def _covered(value: str, q: _Question, stored: set[str]) -> bool:
    """Every occurrence of the value sits inside a longer stored run of the question."""
    longer = [s for p in stored if p != value for s in q.spans[p]]
    return all(
        any(a <= s and e <= b and (b - a) > (e - s) for a, b in longer) for s, e in q.spans[value]
    )


def _polarity(values: set[str], q: _Question) -> dict[str, str]:
    """``pos``, ``neg`` or ``mixed`` per value; a list after an excluder is excluded too."""
    occ = sorted((s, e) for v in values for s, e in q.spans[v])
    neg_at: dict[tuple[int, int], bool] = {}
    prev: tuple[int, int] | None = None
    for s, e in occ:
        window = q.words[max(0, s - 3) : s]
        neg = any(w in _NEGATORS for w in window) or any(
            pair in _NEGATOR_PAIRS for pair in zip(window, window[1:], strict=False)
        )
        if not neg and prev is not None and prev[1] <= s and neg_at[prev]:
            neg = all(w in _CONNECTORS for w in q.words[prev[1] : s])
        neg_at[(s, e)] = neg
        prev = (s, e)
    out: dict[str, str] = {}
    for v in values:
        flags = {neg_at[sp] for sp in q.spans[v]}
        out[v] = "mixed" if len(flags) > 1 else ("neg" if flags == {True} else "pos")
    return out


def _listed(values: set[str], q: _Question) -> bool:
    """The question names the values as one list: only connectors between them."""
    occ = sorted(q.first(v) for v in values)
    return all(
        e1 <= s2 and all(w in _CONNECTORS for w in q.words[e1:s2])
        for (_s1, e1), (s2, _e2) in zip(occ, occ[1:], strict=False)
    )


def _key(t: exp.Table) -> tuple[str, str]:
    return ((t.db or "main").lower(), (t.name or "").lower())


def _relations(
    con: Any, sql_read: set[tuple[str, str]], grantable: Iterable[str] | None
) -> dict[tuple[str, str], str]:
    """Customer relations to probe: (schema, name) -> quoted name."""
    rows = con.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() "
        "WHERE database_name = current_database() AND NOT internal "
        "UNION SELECT schema_name, view_name FROM duckdb_views() "
        "WHERE database_name = current_database() AND NOT internal"
    ).fetchall()
    granted = None if grantable is None else {g.lower() for g in grantable}
    out: dict[tuple[str, str], str] = {}
    for schema, name in rows:
        key = (str(schema).lower(), str(name).lower())
        if key[1] in _DEMO or key[1].startswith("_"):
            continue
        if (
            key in sql_read
            or granted is None
            or f"{key[0]}.{key[1]}" in granted
            or key[1] in granted
        ):
            out[key] = f"{quote_ident(str(schema))}.{quote_ident(str(name))}"
    return out


def _probe(
    con: Any, rels: dict[tuple[str, str], str], phrases: list[str]
) -> tuple[list[_Hit], dict[tuple[str, str], set[str]]] | None:
    """Stored-value hits, and every relation's column names. None when a cap is hit."""
    if len(rels) > _MAX_TABLES:
        return None
    hits: list[_Hit] = []
    columns: dict[tuple[str, str], set[str]] = {}
    total = 0
    for key, rel in sorted(rels.items()):
        described = con.execute(f"DESCRIBE {rel}").fetchall()
        columns[key] = {str(r[0]).lower() for r in described}
        cols = [
            str(r[0])
            for r in described
            if any(t in str(r[1]).upper() for t in _PROBED_TYPES)
            and not any(t in str(r[1]).upper() for t in _UNPROBED_TYPES)
            and not str(r[0]).startswith("_")
        ]
        total += len(cols)
        if total > _MAX_COLUMNS:
            return None
        if not cols or not phrases:
            continue
        union = " UNION ALL ".join(
            f"SELECT {i} AS c, {_norm_sql(quote_ident(c))} AS v, "
            f"CAST({quote_ident(c)} AS VARCHAR) AS raw FROM {rel}"
            for i, c in enumerate(cols)
        )
        found = con.execute(
            "WITH cand AS (SELECT unnest(?::VARCHAR[]) AS v) "
            f"SELECT c, v, min(raw) FROM ({union}) u WHERE v IN (SELECT v FROM cand) "
            "GROUP BY c, v",
            [phrases],
        ).fetchall()
        hits += [_Hit(key, rel, cols[int(c)], str(v), str(raw)) for c, v, raw in found]
    return hits, columns


def _conjuncts(cond: exp.Expression | None) -> list[exp.Expression]:
    if cond is None:
        return []
    while isinstance(cond, exp.Paren):
        cond = cond.this
    if isinstance(cond, exp.And):
        return [c for part in cond.flatten() for c in _conjuncts(part)]
    return [cond]


def _unwrap(node: exp.Expression) -> tuple[exp.Column, list[str]] | None:
    """The column under ``lower``/``upper``/``trim`` wrappers, outermost wrapper first."""
    wrappers: list[str] = []
    while True:
        while isinstance(node, exp.Paren):
            node = node.this
        if isinstance(node, exp.Column):
            return node, wrappers
        fn = _WRAPPERS.get(type(node))
        if fn is None or any(k != "this" and v for k, v in node.args.items()):
            return None
        wrappers.append(fn)
        node = node.this


def _ref(col: exp.Column, scope: Scope) -> tuple[str, str] | None:
    """(source alias, column) a column reference names, when it is plain to see."""
    if col.table:
        return col.table.lower(), col.name.lower()
    tables = [a for a, s in scope.sources.items() if isinstance(s, exp.Table)]
    if len(scope.sources) == 1 and tables:
        return tables[0].lower(), col.name.lower()
    return None


def _restrictions(scope: Scope) -> list[exp.Expression]:
    """Top-level AND conjuncts of WHERE, INNER-join ON, and HAVING on grouped columns."""
    sel = scope.expression
    where = sel.args.get("where")
    out = _conjuncts(where.this if where else None)
    for join in sel.args.get("joins") or []:
        if not join.side and (join.kind or "INNER").upper() == "INNER":
            out += _conjuncts(join.args.get("on"))
    having = sel.args.get("having")
    group = sel.args.get("group")
    grouped = {
        c.name.lower() for c in (group.expressions if group else []) if isinstance(c, exp.Column)
    }
    for conj in _conjuncts(having.this if having else None):
        if {c.name.lower() for c in conj.find_all(exp.Column)} <= grouped:
            out.append(conj)
    return out


def _joined_to(
    scope: Scope, conjs: list[exp.Expression], start: tuple[str, str]
) -> set[tuple[str, str]]:
    """Columns a bare ``a.x = b.y`` conjunct chain makes equal to ``start``."""
    edges: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for conj in conjs:
        if (
            isinstance(conj, exp.EQ)
            and isinstance(conj.this, exp.Column)
            and isinstance(conj.expression, exp.Column)
        ):
            a, b = _ref(conj.this, scope), _ref(conj.expression, scope)
            if a and b:
                edges.append((a, b))
    seen = {start}
    grew = True
    while grew:
        grew = False
        for a, b in edges:
            for x, y in ((a, b), (b, a)):
                if x in seen and y not in seen:
                    seen.add(y)
                    grew = True
    return seen


def _literals(nodes: list[exp.Expression]) -> list[str] | None:
    if not nodes or not all(isinstance(n, exp.Literal) for n in nodes):
        return None
    return [n.sql(dialect=SERVING_DIALECT) for n in nodes]


def _filter_shape(conj: exp.Expression, polarity: str) -> tuple[exp.Expression, list[str]] | None:
    """(column side, literal SQL) for ``col = lit`` / ``IN`` or, excluded, ``<>`` / ``NOT IN``."""
    if polarity == "neg" and isinstance(conj, exp.Not):
        inner = conj.this
        while isinstance(inner, exp.Paren):
            inner = inner.this
        return _filter_shape(inner, "pos") if isinstance(inner, exp.In) else None
    pair = exp.EQ if polarity == "pos" else exp.NEQ
    if isinstance(conj, pair):
        for side, other in ((conj.this, conj.expression), (conj.expression, conj.this)):
            lits = _literals([other])
            if lits and not isinstance(side, exp.Literal):
                return side, lits
    if polarity == "pos" and isinstance(conj, exp.In):
        if conj.args.get("query") or conj.args.get("unnest") or conj.args.get("field"):
            return None
        lits = _literals(list(conj.expressions))
        if lits:
            return conj.this, lits
    return None


def _pass_through(scope: Scope, name: str) -> str | None:
    """The output name of column ``name`` when the scope only selects rows from one source."""
    sel = scope.expression
    if not isinstance(sel, exp.Select) or len(scope.sources) != 1:
        return None
    for arg in ("group", "having", "limit", "offset", "distinct", "qualify", "joins", "laterals"):
        if sel.args.get(arg):
            return None
    if any(p.find(exp.AggFunc, exp.Window) for p in sel.expressions):
        return None
    out: list[str] = []
    for p in sel.expressions:
        target = p.this if isinstance(p, exp.Alias) else p
        if isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star)):
            out.append(name)
        elif isinstance(target, exp.Column) and target.name.lower() == name.lower():
            out.append(p.alias_or_name)
        elif p.alias_or_name.lower() == name.lower():
            return None  # another expression takes the column's name
    names = {o.lower() for o in out}
    return out[0] if len(names) == 1 else None


def _parents(scopes: list[Scope]) -> dict[int, list[tuple[Scope, str]]]:
    out: dict[int, list[tuple[Scope, str]]] = {}
    for scope in scopes:
        for alias, src in scope.sources.items():
            if isinstance(src, Scope):
                out.setdefault(id(src.expression), []).append((scope, alias))
    return out


@dataclass
class _Check:
    con: Any
    scopes: list[Scope]
    roots: list[exp.Expression]
    q: _Question
    allowed: dict[tuple[tuple[str, str], str, str], set[str]]  # (rel, col, polarity) -> values
    parents: dict[int, list[tuple[Scope, str]]]

    def exact(self, hit: _Hit, expr_sql: str, lits: list[str], polarity: str) -> bool:
        """The literals select every stored row of the value and only stated values."""
        n = _norm_sql(quote_ident(hit.col))
        inside = f"coalesce(e IN ({', '.join(lits)}), false)"
        row = self.con.execute(
            f"SELECT count(*) FILTER (WHERE n = ? AND NOT {inside}), "
            f"list(DISTINCT n) FILTER (WHERE {inside}), count(*) FILTER (WHERE e IS NULL) "
            f"FROM (SELECT {n} AS n, {expr_sql} AS e FROM {hit.sql_rel})",
            [hit.value],
        ).fetchone()
        if row is None:
            return False
        missing, selected, nulls = row[0], set(row[1] or []), row[2]
        allowed = self.allowed.get((hit.rel, hit.col, polarity), set())
        if missing or hit.value not in selected or not selected <= allowed:
            return False
        if polarity == "neg" and nulls:
            return False
        return len(selected) == 1 or _listed(selected, self.q)

    def restricts(self, scope: Scope, alias: str, name: str, hit: _Hit, polarity: str) -> bool:
        if not isinstance(scope.expression, exp.Select):
            return False
        conjs = _restrictions(scope)
        target = (alias.lower(), name.lower())
        joined = _joined_to(scope, conjs, target)
        for conj in conjs:
            shape = _filter_shape(conj, polarity)
            got = _unwrap(shape[0]) if shape else None
            if shape is None or got is None:
                continue
            col, wrappers = got
            ref = _ref(col, scope)
            if ref is None and not col.table and col.name.lower() == target[1]:
                ref = target
            if ref == target:
                expr_sql = quote_ident(hit.col)
                for fn in reversed(wrappers):
                    expr_sql = f"{fn}({expr_sql})"
            elif ref in joined and not wrappers:
                expr_sql = quote_ident(hit.col)
            else:
                continue
            if self.exact(hit, expr_sql, shape[1], polarity):
                return True
        return False

    def honoured_from(
        self, scope: Scope, alias: str, name: str, hit: _Hit, polarity: str, depth: int = 0
    ) -> bool:
        if self.restricts(scope, alias, name, hit, polarity):
            return True
        out = _pass_through(scope, name)
        parents = self.parents.get(id(scope.expression), [])
        if out is None or not parents or depth >= _MAX_PASS_THROUGH:
            return False
        return all(
            self.honoured_from(p, palias, out, hit, polarity, depth + 1) for p, palias in parents
        )

    def honoured(self, hit: _Hit, polarity: str) -> bool:
        """Every scope that reads the hit's relation restricts it, and no read hides outside."""
        readers = [
            (scope, alias, src)
            for scope in self.scopes
            for alias, src in scope.sources.items()
            if isinstance(src, exp.Table) and _key(src) == hit.rel
        ]
        in_tree = {id(t) for r in self.roots for t in r.find_all(exp.Table) if _key(t) == hit.rel}
        if not readers or {id(src) for _s, _a, src in readers} != in_tree:
            return False
        for scope, alias, src in readers:
            alias_node = src.args.get("alias")
            if alias_node is not None and alias_node.columns:
                return False
            if not self.honoured_from(scope, alias, hit.col, hit, polarity):
                return False
        return True


def _every_row_holds(con: Any, hit: _Hit) -> bool:
    """The column holds the stated value on every row (no NULLs): a filter changes nothing."""
    row = con.execute(
        f"SELECT count(*) FILTER (WHERE {_norm_sql(quote_ident(hit.col))} IS DISTINCT FROM ?) "
        f"FROM {hit.sql_rel}",
        [hit.value],
    ).fetchone()
    return row is not None and row[0] == 0


def _limits(roots: list[exp.Expression]) -> set[str]:
    return {
        str(lim.expression.this)
        for r in roots
        for lim in r.find_all(exp.Limit)
        if isinstance(lim.expression, exp.Literal) and not lim.expression.is_string
    }


def _not_a_value(value: str, q: _Question, limits: set[str]) -> bool:
    """A number that is the ranking cut ("top 5" with LIMIT 5) or a time window ("3 months")."""
    if not value.isdigit():
        return False
    for s, e in q.spans[value]:
        before = q.words[s - 1] if s > 0 else ""
        after = q.words[e] if e < len(q.words) else ""
        ranked = (before in _RANK_WORDS or after in _RANK_WORDS) and value in limits
        if not (ranked or after in _TIME_UNITS):
            return False
    return True


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
        roots = [r for r in sqlglot.parse(sql, read=SERVING_DIALECT) if r is not None]
        scopes = [s for r in roots for s in traverse_scope(r)]
    except Exception:  # noqa: BLE001 - SQL we cannot analyse proves nothing about its filters
        return qualifier_reason(KIND_NAMED_FILTER, "unparsed")
    if not roots or not scopes:
        return qualifier_reason(KIND_NAMED_FILTER, "unparsed")
    sql_read = {_key(t) for r in roots for t in r.find_all(exp.Table)}
    con = connect_serving(Path(warehouse))
    try:
        rels = _relations(con, sql_read, grantable)
        if not rels:
            return None
        q = _question(con, question)
        if q is None:
            return qualifier_reason(KIND_NAMED_FILTER, "unchecked")
        probed = _probe(con, rels, list(q.spans))
        if probed is None:
            return qualifier_reason(KIND_NAMED_FILTER, "unchecked")
        hits, columns = probed
        stored = {h.value for h in hits}
        stated = {v for v in stored if not _covered(v, q, stored)}
        polarity = _polarity(stated, q)
        allowed: dict[tuple[tuple[str, str], str, str], set[str]] = {}
        for h in hits:
            if h.value in stated:
                allowed.setdefault((h.rel, h.col, polarity[h.value]), set()).add(h.value)
        check = _Check(con, scopes, roots, q, allowed, _parents(scopes))
        read = sql_read & set(rels)
        idents = {n for k in read for n in (k[1], k[1].removeprefix("bronze_"), *columns[k])}
        limits = _limits(roots)
        # Columns some filter of the SQL names: there the filter's own rows decide (rule 12).
        filtered = {
            c.name.lower()
            for sc in scopes
            if isinstance(sc.expression, exp.Select)
            for conj in _restrictions(sc)
            for c in conj.find_all(exp.Column)
        }
        for value in sorted(stated, key=lambda v: (-len(v.split()), v)):
            at = [h for h in hits if h.value == value]
            pol = polarity[value]
            read_rels = {h.rel for h in at if h.rel in read}
            if pol == "mixed":
                pass
            elif _not_a_value(value, q, limits):
                continue
            elif read_rels:
                # Every column of a read relation that stores the value restricts it: a value
                # two columns hold ("North" as a name and a region) is ambiguous otherwise.
                if all(
                    (pol == "pos" and h.col.lower() not in filtered and _every_row_holds(con, h))
                    or check.honoured(h, pol)
                    for h in at
                    if h.rel in read_rels
                ):
                    continue
            elif " " not in value and (
                value in idents or value.removesuffix("s") in idents or f"{value}s" in idents
            ):
                # A word naming a relation or catalog column the SQL reads, stored only in
                # relations it does not read, is the question's subject, not a condition.
                continue
            return qualifier_reason(KIND_NAMED_FILTER, min(h.stored for h in at))
    except Exception:  # noqa: BLE001 - a lake we cannot read cannot vouch for the filters
        return qualifier_reason(KIND_NAMED_FILTER, "unchecked")
    finally:
        con.close()
    return None
