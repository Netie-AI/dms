"""A generated benchmark: every verified link becomes test cases, at real n.

Why this exists
---------------
The free-form gate measures 25 cases (19 answerable + 6 must-abstain). By
R-0010, zero errors there bounds the error rate at ~15.8 percent - honest, and
not a number to sell accuracy on. Generated cases scale to the n where the
rule of three starts saying something: n >= 300 before "<1 percent" is a claim
at all. (An earlier version of this docstring attributed "500 hand-written
questions" to Databricks and "test cases generated from the data" to Palantir;
neither was verified against a source and both are withdrawn.)

Cases are generated from the ontology the lake itself declared and verify()
measured:

  one hop      for each verified many-to-one link and each summable fact
               column: a grouped case (SUM by dimension attribute) and a
               filtered case (SUM where attribute = the value carrying the
               most fact rows, hard rule 12 style); per link, one no-match
               probe (a value no dimension row carries);
  multi hop    for each chain of two or three verified many-to-one links that
               is the unique shortest route from the fact to its target over
               EVERY declared link: a grouped case and a filtered case, with
               via= naming any role-playing hop on the chain;
  must-refuse  for each pair of objects joined by MORE than one link, a case
               asserting the compiler refuses until via= names the path, and
               that each named path then executes.

What a pass here does and does not prove
----------------------------------------
Each case is executed twice: once through the ontology compiler, once through
a plain SQL template that shares no compiler code and takes its join columns
from the manifest's declared foreign keys, not from the Ontology object. So
agreement validates the compiler AND the derivation of links from the
manifest; it does not validate the manifest - a foreign key the database
declared on the wrong column fools both sides alike.

Every grouped case also carries a metadata-independent check: the grouped
total must equal the raw ungrouped total of the fact table, computed with no
join at all. Stated exactly:

  conservation CATCHES   fan-out (a duplicated fact row inflates the total)
                         and row loss (an inner join dropping unmatched rows)
  conservation MISSES    a wrong join column (every row still lands in some
                         group, merely the wrong one), a join that matches
                         nothing (one NULL group holding the whole total), and
                         a wrong measure expression (the bench authored it,
                         and the raw total reads the same column)

The first two misses are covered by the oracle leg, and every run proves that
on every database: one passing grouped case is re-run with its link
deliberately corrupted (the fact's join column swapped for another column of
the same type) and the bench must FAIL that case. If it cannot, the output
says so and the run exits non-zero. The third miss is covered by nothing
here: the measure expression is trusted as authored, on both sides.

Comparison is exact where arithmetic is: integer and DECIMAL measures must
match to the unit. DOUBLE measures (this lake stores money as DOUBLE) compare
with relative 1e-9 on the total and absolute 0.011 per group (or relative
1e-9 where a group's magnitude puts 0.011 below double rounding noise).
Tolerance is never scaled by the number of groups.

Counting: near-duplicate cases (same link, another measure, another
attribute) are not independent trials, so the run also counts distinct
SHAPES - (link path, group|filter, measure type family) - and prints the
rule-of-three bound on both. A filter value matching no dimension row and a
matched value with zero fact rows both come back as a NULL SUM from the
compiler; that is recorded as a KNOWN GAP line, not a pass. Skipped pairs and
chains are counted with reasons and written to --json. A compiled SQL that
raises is a FAILED case carrying the exception text; the run continues.

Stated plainly: this bounds the error rate of the deterministic engine layer -
typed request in, correct rows out - for one-hop and multi-hop single-key SUM
and equality-filter requests. Multi-key, multi-filter and non-equality
requests are not sampled. It says nothing about natural-language
understanding, which is measured (at small n) by the free-form gate. The two
bounds multiply into the product's honest claim; neither substitutes for the
other.

The first run of this bench found a real compiler defect on real data: eleven
self-join cases (an account's parent account, an employee's manager) where the
compiler silently answered "by the object's OWN attribute" when ``via=`` had
named the parent link - the quietest wrong number, a different question
answered confidently. It had been reported by adversarial review and half
fixed; the bench caught the other half within minutes of existing.

  python scripts/ontology_bench.py                # all extracted databases
  python scripts/ontology_bench.py --database AdventureWorks2025
  python scripts/ontology_bench.py --json data/lake/_reports/bench.json

Exit 0 only when nothing disagreed, every must-refuse case refused, no
compiled SQL raised, and the corrupted-link self-check was caught.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import traceback
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "lake" / "_reports" / "extract_manifest.json"

sys.path.insert(0, str(ROOT / "scripts"))

INTEGER_TYPES = {"BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT",
                 "UBIGINT", "UINTEGER", "USMALLINT", "UTINYINT"}
DOUBLE_TYPES = {"DOUBLE", "FLOAT", "REAL"}

DOUBLE_REL_TOL = 1e-9      # on a total, and on a group too large for the abs floor
DOUBLE_GROUP_ABS_TOL = 0.011
MAX_DEPTH = 3
NO_MATCH_FALLBACK = "zzz-no-such"


# --------------------------------------------------------------------------
# small helpers: types, execution, comparison
# --------------------------------------------------------------------------


def _measure_kind(typ: str) -> str | None:
    t = str(typ).upper()
    if t in INTEGER_TYPES:
        return "integer"
    if t.startswith("DECIMAL"):
        return "decimal"
    if t in DOUBLE_TYPES:
        return "double"
    return None


def _run(con: Any, sql: str) -> tuple[list[tuple] | None, str | None]:
    """Execute; never raise. A raise is a FAILED case, not a dead run."""
    try:
        return con.execute(sql).fetchall(), None
    except Exception as exc:  # noqa: BLE001
        msg = " ".join(str(exc).split())
        return None, f"{type(exc).__name__}: {msg[:300]}"


def _as_exact(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, float):
        return Decimal(str(v))
    return Decimal(v)


def _values_agree(a: Any, b: Any, kind: str, *, total: bool = False) -> bool:
    """Exact for integer/DECIMAL. DOUBLE: rel 1e-9 on a total; abs 0.011 per
    group, or rel 1e-9 where the group is too large for 0.011 to be meaningful.
    Never scaled by group count."""
    if a is None or b is None:
        return a is None and b is None
    if kind == "double":
        fa, fb = float(a), float(b)
        if math.isnan(fa) or math.isnan(fb):
            return math.isnan(fa) and math.isnan(fb)
        diff = abs(fa - fb)
        rel = DOUBLE_REL_TOL * max(abs(fa), abs(fb))
        if total:
            return diff <= max(rel, DOUBLE_GROUP_ABS_TOL if max(abs(fa), abs(fb)) < 1 else 0.0)
        return diff <= max(DOUBLE_GROUP_ABS_TOL, rel)
    return _as_exact(a) == _as_exact(b)


def _sum_values(values: list[Any], kind: str) -> Any:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    if kind == "double":
        return math.fsum(float(v) for v in vals)
    return sum((_as_exact(v) for v in vals), Decimal(0))


def _rows_agree(compiled: list[tuple], oracle: list[tuple], kind: str) -> str | None:
    """Unordered label -> value comparison. Ties make ORDER BY nondeterministic
    across two different plans, so order is not part of engine-layer truth here."""
    ca = {tuple(r[:-1]): r[-1] for r in compiled}
    ob = {tuple(r[:-1]): r[-1] for r in oracle}
    if len(ca) != len(compiled):
        return "compiled rows repeat a label (GROUP BY emitted duplicate groups)"
    if set(ca) != set(ob):
        only_c = sorted(map(str, set(ca) - set(ob)))[:3]
        only_o = sorted(map(str, set(ob) - set(ca)))[:3]
        return f"group sets differ: compiled-only {only_c}, oracle-only {only_o}"
    for k, v in ca.items():
        if not _values_agree(v, ob[k], kind):
            return f"{k}: compiled {v!r} vs oracle {ob[k]!r}"
    return None


def _lit(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


# --------------------------------------------------------------------------
# the bench
# --------------------------------------------------------------------------


@dataclass
class _Case:
    kind: str                 # group | filter | nomatch | refuse
    fact: str
    path: list[Any]           # LinkType chain, fact-side first
    mcol: str | None
    mkind: str | None
    attr: str | None
    value: Any = None
    via: dict[str, str] | None = None

    @property
    def depth(self) -> int:
        return len(self.path)

    @property
    def dim(self) -> str:
        return self.path[-1].to_object if self.path else self.fact

    @property
    def label(self) -> str:
        route = " -> ".join(x.name for x in self.path)
        head = f"SUM({self.fact}.{self.mcol})" if self.mcol else f"{self.fact}"
        if self.kind == "group":
            s = f"{head} by {self.dim}.{self.attr}"
        elif self.kind in ("filter", "nomatch"):
            s = f"{head} where {self.dim}.{self.attr}={self.value!r}"
        else:
            s = f"{head} by {self.dim}.{self.attr} (ambiguous, no via)"
        if self.depth > 1:
            s += f" [{self.depth}-hop: {route}]"
        elif self.via:
            s += f" via {self.path[0].name}"
        return s

    def shape(self) -> str:
        op = "group" if self.kind in ("group", "refuse") else "filter"
        return "|".join([" -> ".join(x.name for x in self.path), op, str(self.mkind)])


def _empty_depth() -> dict[str, int]:
    return {"group": 0, "filter": 0, "nomatch": 0, "passed": 0, "failed": 0,
            "known_gap": 0}


@dataclass
class _Run:
    con: Any
    onto: Any
    db: str
    paths: dict[str, str]
    fk_cols: dict[tuple[str, str, str], list[tuple[str, str]]]
    max_cases: int
    cases: int = 0
    passed: int = 0
    failed: int = 0
    known_gaps: int = 0
    refused_ok: int = 0
    refusal_failures: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    known_gap_details: list[dict[str, Any]] = field(default_factory=list)
    detail: list[dict[str, Any]] = field(default_factory=list)
    shapes: set[str] = field(default_factory=set)
    by_depth: dict[int, dict[str, int]] = field(default_factory=dict)
    measure_n: int = 0
    passed_group_cases: list[tuple[_Case, str]] = field(default_factory=list)
    _describe_cache: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    _label_cache: dict[str, list[str]] = field(default_factory=dict)
    _numeric_cache: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    # -- metadata probes (never raise) -------------------------------------

    def rel(self, obj: str) -> str:
        return f"read_parquet('{self.paths[obj]}')"

    def describe(self, obj: str) -> list[tuple[str, str]]:
        if obj not in self._describe_cache:
            rows, err = _run(self.con, f"DESCRIBE SELECT * FROM {self.rel(obj)}")
            self._describe_cache[obj] = (
                [] if err else [(str(r[0]), str(r[1]).upper()) for r in rows]
            )
        return self._describe_cache[obj]

    def numeric_cols(self, obj: str) -> list[tuple[str, str]]:
        """(column, kind) pairs a person might sum."""
        if obj not in self._numeric_cache:
            out = []
            for name, typ in self.describe(obj):
                k = _measure_kind(typ)
                if k is not None:
                    out.append((name, k))
            self._numeric_cache[obj] = out
        return self._numeric_cache[obj]

    def label_cols(self, obj: str, *, max_distinct: int = 200) -> list[str]:
        """Low-cardinality VARCHAR columns - the attributes a person groups by."""
        if obj not in self._label_cache:
            out = []
            for name, typ in self.describe(obj):
                if typ != "VARCHAR":
                    continue
                rows, err = _run(
                    self.con,
                    f"SELECT COUNT(DISTINCT {_q(name)}), COUNT({_q(name)}) FROM {self.rel(obj)}",
                )
                if err or not rows:
                    continue
                d, nn = rows[0]
                if 2 <= int(d) <= max_distinct and int(nn) > 0:
                    out.append(name)
            self._label_cache[obj] = out
        return self._label_cache[obj]

    def scalar(self, sql: str) -> tuple[Any, str | None]:
        rows, err = _run(self.con, sql)
        if err:
            return None, err
        return (rows[0][0] if rows else None), None

    # -- oracle SQL: join columns come from the manifest, not the Ontology --

    def manifest_on(self, link: Any, left: str, right: str) -> str | None:
        key = (str(link.name).split("@")[0], link.from_object, link.to_object)
        cols = self.fk_cols.get(key)
        if not cols:
            return None
        return " AND ".join(f"{left}.{_q(a)} = {right}.{_q(b)}" for a, b in cols)

    def oracle_grouped(self, case: _Case) -> str | None:
        joins = []
        prev = "f"
        for i, link in enumerate(case.path):
            alias = f"d{i}"
            on = self.manifest_on(link, prev, alias)
            if on is None:
                return None
            joins.append(f"LEFT JOIN {self.rel(link.to_object)} {alias} ON {on}")
            prev = alias
        return (
            f"SELECT {prev}.{_q(case.attr)}, SUM(f.{_q(case.mcol)}) "
            f"FROM {self.rel(case.fact)} f " + " ".join(joins) +
            f" GROUP BY {prev}.{_q(case.attr)}"
        )

    def oracle_filtered(self, case: _Case) -> str | None:
        first = case.path[0]
        key = (str(first.name).split("@")[0], first.from_object, first.to_object)
        cols = self.fk_cols.get(key)
        if not cols:
            return None
        factcols = ", ".join(f"f.{_q(a)}" for a, _ in cols)
        keycols = ", ".join(f"d0.{_q(b)}" for _, b in cols)
        inner = f"SELECT {keycols} FROM {self.rel(first.to_object)} d0"
        prev = "d0"
        for i, link in enumerate(case.path[1:], start=1):
            alias = f"d{i}"
            on = self.manifest_on(link, prev, alias)
            if on is None:
                return None
            inner += f" JOIN {self.rel(link.to_object)} {alias} ON {on}"
            prev = alias
        inner += f" WHERE {prev}.{_q(case.attr)} = {_lit(case.value)}"
        return (
            f"SELECT SUM(f.{_q(case.mcol)}) FROM {self.rel(case.fact)} f "
            f"WHERE ({factcols}) IN ({inner})"
        )

    def join_chain_sql(self, path: list[Any], *, inner: bool) -> str | None:
        """fact f JOIN d0 JOIN d1 ... - used by the value pickers."""
        out = f"{self.rel(path[0].from_object)} f"
        prev = "f"
        for i, link in enumerate(path):
            alias = f"d{i}"
            on = self.manifest_on(link, prev, alias)
            if on is None:
                return None
            out += f" {'JOIN' if inner else 'LEFT JOIN'} {self.rel(link.to_object)} {alias} ON {on}"
            prev = alias
        return out

    # -- measures ------------------------------------------------------------

    def measure(self, fact: str, mcol: str) -> str:
        name = f"m_{self.measure_n}"
        self.measure_n += 1
        self.onto.add_measure(name, fact, f"SUM(f.{_q(mcol)})")
        self.onto.verified = True  # add_measure does not invalidate; be explicit
        return name

    # -- bookkeeping -----------------------------------------------------------

    def _bump(self, case: _Case) -> None:
        self.cases += 1
        d = self.by_depth.setdefault(case.depth, _empty_depth())
        d[case.kind if case.kind != "refuse" else "group"] += 1
        self.shapes.add(case.shape())

    def _record(self, case: _Case, result: str, detail: str | None = None,
                sql: str | None = None, extra: dict[str, Any] | None = None) -> None:
        d = self.by_depth.setdefault(case.depth, _empty_depth())
        if result == "pass":
            self.passed += 1
            d["passed"] += 1
        elif result == "fail":
            self.failed += 1
            d["failed"] += 1
            self.failures.append({"case": case.label, "detail": detail or "", "sql": sql})
        elif result == "known_gap":
            self.known_gaps += 1
            d["known_gap"] += 1
            self.known_gap_details.append({"case": case.label, "detail": detail or "", "sql": sql})
        rec: dict[str, Any] = {
            "kind": case.kind, "depth": case.depth, "fact": case.fact,
            "path": [x.name for x in case.path], "measure": case.mcol,
            "measure_kind": case.mkind, "attr": case.attr, "value": case.value,
            "via": case.via, "result": result, "detail": detail, "sql": sql,
        }
        if extra:
            rec.update(extra)
        self.detail.append(rec)

    def budget_left(self) -> bool:
        return self.cases < self.max_cases

    # -- the case runners --------------------------------------------------------

    def run_refuse(self, case: _Case, mname: str) -> None:
        from ontology import Refusal

        self._bump(case)
        got = self.onto.compile(mname, group_by=[(case.dim, case.attr)])
        if isinstance(got, Refusal) and got.reason == "ambiguous_path":
            self.refused_ok += 1
            self._record(case, "refused_ok", got.reason)
        else:
            self.refusal_failures += 1
            what = got.reason if isinstance(got, Refusal) else type(got).__name__
            self.failures.append({"case": case.label, "detail":
                                  f"expected ambiguous_path refusal, got {what}", "sql": None})
            self._record(case, "refusal_failure", f"got {what}")

    def run_grouped(self, case: _Case, mname: str, *, record: bool = True
                    ) -> tuple[str | None, bool, str | None]:
        """Returns (oracle_err, conservation_held, compiled_sql).

        ``oracle_err`` is None when compiled and oracle rows agree. The caller
        decides what that means - a real case records pass/fail; the corrupted-
        link self-check expects a disagreement.
        """
        from ontology import CompiledQuery

        if record:
            self._bump(case)
        got = self.onto.compile(mname, group_by=[(case.dim, case.attr)], via=case.via)
        if not isinstance(got, CompiledQuery):
            err = f"refused: {got.reason}: {got.detail[:200]}"
            if record:
                self._record(case, "fail", err)
            return err, True, None
        compiled_rows, exc = _run(self.con, got.sql)
        if exc:
            err = f"compiled SQL raised: {exc}"
            if record:
                self._record(case, "fail", err, got.sql)
            return err, True, got.sql
        oracle_sql = self.oracle_grouped(case)
        if oracle_sql is None:
            err = "oracle has no manifest foreign key for a link on this path"
            if record:
                self._record(case, "fail", err, got.sql)
            return err, True, got.sql
        oracle_rows, exc = _run(self.con, oracle_sql)
        if exc:
            err = f"oracle SQL raised (bench defect): {exc}"
            if record:
                self._record(case, "fail", err, got.sql)
            return err, True, got.sql
        err = _rows_agree(compiled_rows, oracle_rows, case.mkind)

        # metadata-independent leg: no join at all
        raw, exc = self.scalar(f"SELECT SUM({_q(case.mcol)}) FROM {self.rel(case.fact)}")
        conserved = True
        cons_err = None
        if exc:
            cons_err = f"raw total raised: {exc}"
            conserved = False
        else:
            grouped = _sum_values([r[-1] for r in compiled_rows], case.mkind)
            if not _values_agree(grouped, raw, case.mkind, total=True):
                conserved = False
                cons_err = f"conservation: grouped {grouped!r} != raw total {raw!r}"
        if record:
            if err is None and conserved:
                self._record(case, "pass", None, got.sql)
                self.passed_group_cases.append((case, mname))
            else:
                self._record(case, "fail", err or cons_err, got.sql)
        return err if err is not None else (None if conserved else cons_err), conserved, got.sql

    def run_filtered(self, case: _Case, mname: str) -> None:
        from ontology import CompiledQuery

        self._bump(case)
        got = self.onto.compile(mname, filters=[(case.dim, case.attr, "=", case.value)],
                                via=case.via)
        if not isinstance(got, CompiledQuery):
            self._record(case, "fail", f"refused: {got.reason}: {got.detail[:200]}")
            return
        compiled_v, exc = self.scalar(got.sql)
        if exc:
            self._record(case, "fail", f"compiled SQL raised: {exc}", got.sql)
            return
        oracle_sql = self.oracle_filtered(case)
        if oracle_sql is None:
            self._record(case, "fail", "oracle has no manifest foreign key for this path", got.sql)
            return
        oracle_v, exc = self.scalar(oracle_sql)
        if exc:
            self._record(case, "fail", f"oracle SQL raised (bench defect): {exc}", got.sql)
            return
        if compiled_v is None and oracle_v is None:
            # the value was chosen to carry fact rows, so a NULL is a wrong answer
            self._record(case, "fail", "both sides NULL for a value chosen to have fact rows "
                         "(value picker or join is wrong)", got.sql)
        elif _values_agree(compiled_v, oracle_v, case.mkind, total=True):
            self._record(case, "pass", None, got.sql)
        else:
            self._record(case, "fail", f"compiled {compiled_v!r} vs oracle {oracle_v!r}", got.sql)

    def run_nomatch(self, case: _Case, mname: str, true_empty: Any) -> None:
        """A value no dimension row carries. The compiler's answer must be
        distinguishable from a matched value with zero fact rows; today it is
        a NULL SUM both ways, and that is recorded as a KNOWN GAP, not a pass."""
        from ontology import CompiledQuery, Refusal

        self._bump(case)
        got = self.onto.compile(mname, filters=[(case.dim, case.attr, "=", case.value)],
                                via=case.via)
        if isinstance(got, Refusal):
            # a refusal naming the unmatched value would be a distinguishable answer
            self._record(case, "pass", f"refused: {got.reason}")
            return
        if not isinstance(got, CompiledQuery):
            self._record(case, "fail", f"unexpected {type(got).__name__}")
            return
        compiled_v, exc = self.scalar(got.sql)
        if exc:
            self._record(case, "fail", f"compiled SQL raised: {exc}", got.sql)
            return
        oracle_sql = self.oracle_filtered(case)
        oracle_v, oexc = (None, None) if oracle_sql is None else self.scalar(oracle_sql)
        if oexc:
            self._record(case, "fail", f"oracle SQL raised (bench defect): {oexc}", got.sql)
            return
        if compiled_v is not None:
            self._record(case, "fail", f"compiled {compiled_v!r} for a value matching no "
                         f"dimension row (oracle {oracle_v!r})", got.sql)
            return
        # compiled NULL. Is that distinguishable from a true-empty value?
        empty_v: Any = None
        empty_note = "no dimension value with zero fact rows exists for this link"
        if true_empty is not None:
            got2 = self.onto.compile(mname, filters=[(case.dim, case.attr, "=", true_empty)],
                                     via=case.via)
            if isinstance(got2, CompiledQuery):
                empty_v, exc = self.scalar(got2.sql)
                empty_note = f"true-empty value {true_empty!r} also returns {empty_v!r}"
        if true_empty is not None and empty_v is not None:
            self._record(case, "pass", "compiler distinguishes no-match from true-empty", got.sql)
            return
        self._record(
            case, "known_gap",
            f"compiler returns NULL for {case.value!r}, which matches no {case.dim} row; "
            f"{empty_note}. A matched-nothing filter is indistinguishable from a true zero.",
            got.sql, {"true_empty_value": true_empty},
        )


# --------------------------------------------------------------------------
# route analysis over EVERY declared link (any cardinality)
# --------------------------------------------------------------------------


def _shortest_routes(links: list[Any], start: str, max_depth: int) -> dict[str, list[list[Any]]]:
    """All shortest routes from ``start`` to each object reachable within
    ``max_depth`` hops, over every declared link regardless of cardinality.
    Self-links and revisits are not routes. ``routes[start] == [[]]``."""
    routes: dict[str, list[list[Any]]] = {start: [[]]}
    dist: dict[str, int] = {start: 0}
    frontier = [start]
    for level in range(1, max_depth + 1):
        found: dict[str, list[list[Any]]] = {}
        for obj in frontier:
            for link in links:
                if link.from_object != obj or link.to_object in dist:
                    continue
                for r in routes[obj]:
                    found.setdefault(link.to_object, []).append(r + [link])
        for obj, rs in found.items():
            dist[obj] = level
            routes[obj] = rs[:64]
        frontier = list(found)
    return routes


def _chain_choices(routes: list[list[Any]]) -> list[tuple[list[Any], dict[str, str]]] | str:
    """Turn the shortest routes to one target into concrete link chains.

    Returns a skip reason when the routes do not all follow one object
    sequence (the compiler refuses that as ambiguous, correctly) or when a hop
    has no many-to-one link (the compiler refuses that as fan-out, correctly).
    Otherwise every combination of parallel links, with via= naming each
    role-playing hop - the compiler needs it, and the bench exercises it.
    """
    seqs = {tuple(x.to_object for x in r) for r in routes}
    if len(seqs) != 1:
        return "ambiguous_route"
    depth = len(routes[0])
    per_hop: list[list[Any]] = []
    for i in range(depth):
        names: dict[str, Any] = {}
        for r in routes:
            names[r[i].name] = r[i]
        m2o = [link for _, link in sorted(names.items()) if link.cardinality == "many_to_one"]
        if not m2o:
            return "m2m_hop"
        per_hop.append((m2o, len(names) > 1))
    out = []
    for combo in itertools.product(*[links for links, _ in per_hop]):
        via = {link.to_object: link.name
               for link, (_, parallel) in zip(combo, per_hop) if parallel}
        out.append((list(combo), via or None))
    return out


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


def generate_and_run(
    con: Any,
    entry: dict[str, Any],
    *,
    max_cases: int,
    max_measures_per_fact: int = 2,
    max_attrs_per_dim: int = 2,
    max_chains_per_fact: int = 6,
    lake_root: Path | None = None,
) -> dict[str, Any]:
    from ontology import from_manifest

    root = lake_root or ROOT
    db = str(entry["database"])
    onto = from_manifest(entry, lake_root=root)
    violations = onto.verify(con)
    report: dict[str, Any] = {"database": db, "cases": 0, "passed": 0, "failed": 0,
                              "known_gaps": 0, "refused_ok": 0, "refusal_failures": 0,
                              "failures": [], "known_gap_details": [], "shapes": [],
                              "n_shapes": 0, "by_depth": {}, "pairs": {}, "chains": {},
                              "self_check": None, "cases_detail": []}
    if violations:
        report["error"] = f"{len(violations)} verify violations"
        return report

    paths = {f"{t['schema']}.{t['table']}": (root / str(t["path"])).as_posix()
             for t in entry["tables"]}
    fk_cols: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for fk in entry.get("foreign_keys") or []:
        fk_cols.setdefault(
            (str(fk["name"]), str(fk["from_table"]), str(fk["to_table"])), []
        ).append((str(fk["from_column"]), str(fk["to_column"])))

    run = _Run(con=con, onto=onto, db=db, paths=paths, fk_cols=fk_cols, max_cases=max_cases)

    key_cols: dict[str, set[str]] = {name: set(obj.key) for name, obj in onto.objects.items()}
    for link in onto.links.values():
        key_cols.setdefault(link.from_object, set()).update(link.from_columns)

    def measures_of(fact: str) -> list[tuple[str, str]]:
        nums = [(c, k) for c, k in run.numeric_cols(fact) if c not in key_cols.get(fact, set())]
        return nums[:max_measures_per_fact]

    # ---------------- one hop: fact -> dim pairs over direct m2o links --------
    pairs: dict[tuple[str, str], list[Any]] = {}
    for link in onto.links.values():
        if link.cardinality == "many_to_one":
            pairs.setdefault((link.from_object, link.to_object), []).append(link)

    skipped_pairs: list[dict[str, Any]] = []
    pairs_used = 0
    for (fact, dim), links in sorted(pairs.items()):
        if not run.budget_left():
            skipped_pairs.append({"fact": fact, "dim": dim,
                                  "links": [x.name for x in links], "reason": "budget"})
            continue
        measures = measures_of(fact)
        if not measures:
            skipped_pairs.append({"fact": fact, "dim": dim,
                                  "links": [x.name for x in links], "reason": "no_measure"})
            continue
        attrs = run.label_cols(dim)[:max_attrs_per_dim]
        if not attrs:
            skipped_pairs.append({"fact": fact, "dim": dim,
                                  "links": [x.name for x in links], "reason": "no_attr"})
            continue
        pairs_used += 1

        # A self-link (an account's parent, an employee's manager) has two
        # readings of "by attr" - the object's own and its parent's. The bench
        # asks the parent's, so via= always names the link for fact == dim;
        # without it the compiler correctly answers the OTHER reading and the
        # oracle template here would grade it against the wrong question.
        self_linked = fact == dim
        ambiguous = len(links) > 1
        if ambiguous and run.budget_left():
            mcol, mkind = measures[0]
            mname = run.measure(fact, mcol)
            run.run_refuse(_Case("refuse", fact, [links[0]], mcol, mkind, attrs[0]), mname)

        for link in sorted(links, key=lambda x: x.name):
            if not run.budget_left():
                break
            via = {dim: link.name} if (ambiguous or self_linked) else None
            for mcol, mkind in measures:
                if not run.budget_left():
                    break
                mname = run.measure(fact, mcol)
                for attr in attrs:
                    if not run.budget_left():
                        break
                    run.run_grouped(_Case("group", fact, [link], mcol, mkind, attr, via=via), mname)

                # -- filtered scalar case: the value carrying the most fact rows
                if not run.budget_left():
                    break
                attr = attrs[0]
                chain = run.join_chain_sql([link], inner=True)
                if chain is None:
                    continue
                pick, err = run.scalar(
                    f"SELECT d0.{_q(attr)} FROM {chain} WHERE d0.{_q(attr)} IS NOT NULL "
                    f"AND f.{_q(mcol)} IS NOT NULL GROUP BY d0.{_q(attr)} "
                    f"ORDER BY COUNT(*) DESC, d0.{_q(attr)} LIMIT 1"
                )
                if err or pick is None:
                    # the link matches no fact row with a non-null measure: the
                    # filter would be a both-NULL pass, which proves nothing
                    run.detail.append({"kind": "filter", "fact": fact, "path": [link.name],
                                       "measure": mcol, "result": "not_generated",
                                       "detail": err or "no dimension value carries a fact "
                                       "row with a non-null measure"})
                    continue
                run.run_filtered(_Case("filter", fact, [link], mcol, mkind, attr, pick, via), mname)

            # -- per link: one no-match probe (defect 31) -------------------
            if not run.budget_left():
                break
            mcol, mkind = measures[0]
            mname = run.measure(fact, mcol)
            attr = attrs[0]
            chain = run.join_chain_sql([link], inner=True)
            if chain is None:
                continue
            seed, _ = run.scalar(
                f"SELECT d0.{_q(attr)} FROM {chain} WHERE d0.{_q(attr)} IS NOT NULL "
                f"GROUP BY d0.{_q(attr)} ORDER BY COUNT(*) DESC, d0.{_q(attr)} LIMIT 1"
            )
            seed = str(seed) if seed is not None else NO_MATCH_FALLBACK
            nomatch = None
            for cand in (seed.swapcase(), seed + " ", NO_MATCH_FALLBACK):
                if cand == seed:
                    continue
                n, err = run.scalar(
                    f"SELECT COUNT(*) FROM {run.rel(dim)} WHERE {_q(attr)} = {_lit(cand)}"
                )
                if not err and int(n or 0) == 0:
                    nomatch = cand
                    break
            if nomatch is None:
                continue
            # a dimension value that exists but has zero fact rows, if any
            on = run.manifest_on(link, "f", "d")
            true_empty, _ = run.scalar(
                f"WITH hit AS (SELECT DISTINCT d.{_q(attr)} AS a FROM {run.rel(dim)} d "
                f"JOIN {run.rel(fact)} f ON {on}) "
                f"SELECT d.{_q(attr)} FROM {run.rel(dim)} d WHERE d.{_q(attr)} IS NOT NULL "
                f"AND d.{_q(attr)} NOT IN (SELECT a FROM hit WHERE a IS NOT NULL) "
                f"ORDER BY d.{_q(attr)} LIMIT 1"
            )
            run.run_nomatch(_Case("nomatch", fact, [link], mcol, mkind, attr, nomatch, via),
                            mname, true_empty)

    # ---------------- multi hop: chains of verified m2o links -----------------
    all_links = list(onto.links.values())
    skipped_chains: list[dict[str, Any]] = []
    chains_used = 0
    chains_total = 0
    facts = sorted({link.from_object for link in all_links
                    if link.cardinality == "many_to_one"})
    for fact in facts:
        measures = measures_of(fact)
        routes = _shortest_routes(all_links, fact, MAX_DEPTH)
        targets = sorted((t, rs) for t, rs in routes.items() if rs and len(rs[0]) >= 2)
        per_depth_used: dict[int, int] = {}
        for target, rs in sorted(targets, key=lambda tr: (len(tr[1][0]), tr[0])):
            depth = len(rs[0])
            chains_total += 1
            route_names = [" -> ".join(x.name for x in r) for r in rs]
            if not measures:
                skipped_chains.append({"fact": fact, "target": target, "depth": depth,
                                       "routes": route_names, "reason": "no_measure"})
                continue
            choices = _chain_choices(rs)
            if isinstance(choices, str):
                skipped_chains.append({"fact": fact, "target": target, "depth": depth,
                                       "routes": route_names, "reason": choices})
                continue
            attrs = run.label_cols(target)[:1]
            if not attrs:
                skipped_chains.append({"fact": fact, "target": target, "depth": depth,
                                       "routes": route_names, "reason": "no_attr"})
                continue
            if per_depth_used.get(depth, 0) >= max_chains_per_fact:
                skipped_chains.append({"fact": fact, "target": target, "depth": depth,
                                       "routes": route_names, "reason": "per_fact_cap"})
                continue
            if not run.budget_left():
                skipped_chains.append({"fact": fact, "target": target, "depth": depth,
                                       "routes": route_names, "reason": "budget"})
                continue
            per_depth_used[depth] = per_depth_used.get(depth, 0) + 1
            chains_used += 1
            attr = attrs[0]
            for path, via in choices[:2]:
                for mcol, mkind in measures:
                    if not run.budget_left():
                        break
                    mname = run.measure(fact, mcol)
                    run.run_grouped(_Case("group", fact, path, mcol, mkind, attr, via=via), mname)
                    chain = run.join_chain_sql(path, inner=True)
                    if chain is None or not run.budget_left():
                        continue
                    tail = f"d{len(path) - 1}"
                    pick, err = run.scalar(
                        f"SELECT {tail}.{_q(attr)} FROM {chain} "
                        f"WHERE {tail}.{_q(attr)} IS NOT NULL "
                        f"AND f.{_q(mcol)} IS NOT NULL GROUP BY {tail}.{_q(attr)} "
                        f"ORDER BY COUNT(*) DESC, {tail}.{_q(attr)} LIMIT 1"
                    )
                    if err or pick is None:
                        continue
                    run.run_filtered(_Case("filter", fact, path, mcol, mkind, attr, pick, via),
                                     mname)

    # ---------------- corrupted-link self-check (defect 28) --------------------
    report["self_check"] = _self_check(run)

    counts: dict[str, int] = {}
    for s in skipped_pairs:
        counts[s["reason"]] = counts.get(s["reason"], 0) + 1
    ccounts: dict[str, int] = {}
    for s in skipped_chains:
        ccounts[s["reason"]] = ccounts.get(s["reason"], 0) + 1

    report.update({
        "cases": run.cases,
        "passed": run.passed,
        "failed": run.failed,
        "known_gaps": run.known_gaps,
        "refused_ok": run.refused_ok,
        "refusal_failures": run.refusal_failures,
        "failures": run.failures,
        "known_gap_details": run.known_gap_details,
        "shapes": sorted(run.shapes),
        "n_shapes": len(run.shapes),
        "by_depth": {str(k): v for k, v in sorted(run.by_depth.items())},
        "pairs": {"total": len(pairs), "used": pairs_used, "skipped": skipped_pairs,
                  "skipped_counts": counts},
        "chains": {"total": chains_total, "used": chains_used, "skipped": skipped_chains,
                   "skipped_counts": ccounts},
        "cases_detail": run.detail,
    })
    return report


def _self_check(run: _Run) -> dict[str, Any]:
    """Re-run one passing grouped case with its link deliberately corrupted.

    The fact-side join column is swapped for another column of the same type.
    The compiler then joins on the wrong column; the oracle, which takes its
    join columns from the manifest, does not. The bench must FAIL that case -
    and the conservation leg alone would NOT have (every fact row still lands
    in some group), which is exactly what the docstring says it misses.
    """
    from ontology import LinkType

    tried: list[str] = []
    for case, mname in run.passed_group_cases:
        if case.depth != 1 or case.via:
            continue
        link = case.path[0]
        cols = dict(run.describe(case.fact))
        orig = link.from_columns[0]
        t0 = cols.get(orig)
        if t0 is None:
            continue
        candidates = [c for c, t in run.describe(case.fact)
                      if t == t0 and c not in link.from_columns]
        for cand in candidates[:4]:
            differs, err = run.scalar(
                f"SELECT COUNT(*) FROM {run.rel(case.fact)} "
                f"WHERE {_q(orig)} IS DISTINCT FROM {_q(cand)}"
            )
            if err or not int(differs or 0):
                continue  # identical values: not a corruption in effect
            tried.append(f"{link.name}: {orig} -> {cand}")
            corrupted = LinkType(link.name, link.from_object,
                                 (cand,) + tuple(link.from_columns[1:]),
                                 link.to_object, link.to_columns,
                                 link.cardinality, link.max_fanout)
            run.onto.links[link.name] = corrupted
            try:
                err, conserved, sql = run.run_grouped(case, mname, record=False)
            finally:
                run.onto.links[link.name] = link
            result = {
                "case": case.label, "link": link.name, "column": orig,
                "swapped_for": cand, "caught": err is not None,
                "oracle_leg": "disagreed" if err is not None else "agreed",
                "conservation_leg": "held" if conserved else "broke",
                "detail": err, "tried": tried, "sql": sql,
            }
            if err is not None:
                return result
    return {"caught": False, "tried": tried,
            "detail": ("no passing one-hop grouped case had a swappable same-type column"
                       if not tried else "every tried corruption agreed with the oracle")}


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def _depth_line(by_depth: dict[Any, dict[str, int]]) -> str:
    parts = []
    for k in sorted(by_depth, key=int):
        d = by_depth[k]
        s = f"{k}-hop: {d['group']} grouped + {d['filter']} filtered"
        if d.get("nomatch"):
            s += f" + {d['nomatch']} no-match"
        parts.append(s)
    return "; ".join(parts) or "none"


def _fmt_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"


def main(argv: list[str] | None = None) -> int:
    # R-0012: a Windows console defaults to cp1252 and turns every accented
    # label into a replacement glyph. Say what the data says.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    import duckdb

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--database", help="limit to one database")
    ap.add_argument("--max-cases", type=int, default=1000, help="cap per database")
    ap.add_argument("--max-chains-per-fact", type=int, default=6,
                    help="multi-hop chains per fact per depth")
    ap.add_argument("--manifest", type=Path, default=MANIFEST,
                    help="extract manifest (default: the lake's)")
    ap.add_argument("--lake-root", type=Path, default=None,
                    help="root the manifest's table paths are relative to (default: repo root)")
    ap.add_argument("--json", type=Path, help="also write the full report here")
    args = ap.parse_args(argv)

    if not args.manifest.is_file():
        print(f"FAIL no manifest at {args.manifest}; run load_adventureworks.py first")
        return 2
    entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.database:
        entries = [e for e in entries if e["database"] == args.database]
        if not entries:
            print(f"FAIL {args.database} not in the manifest")
            return 2

    con = duckdb.connect(":memory:")
    reports: list[dict[str, Any]] = []
    try:
        for entry in entries:
            try:
                r = generate_and_run(con, entry, max_cases=args.max_cases,
                                     max_chains_per_fact=args.max_chains_per_fact,
                                     lake_root=args.lake_root)
            except Exception:  # noqa: BLE001 - the run must still report and write --json
                r = {"database": str(entry.get("database")), "cases": 0, "passed": 0,
                     "failed": 0, "known_gaps": 0, "refused_ok": 0, "refusal_failures": 0,
                     "failures": [], "error": "bench raised: " + traceback.format_exc()[-1500:]}
            reports.append(r)
            if "error" in r:
                print(f"  {r['database']:<22} ERROR {r['error'].splitlines()[0]}")
                continue
            print(f"  {r['database']:<22} {r['cases']:>4} cases  "
                  f"{r['passed']:>4} passed  {r['failed']:>3} failed  "
                  f"{r['known_gaps']:>2} known-gap  "
                  f"{r['refused_ok']:>2} correct refusals  "
                  f"{r['refusal_failures']:>2} refusal failures  "
                  f"{r['n_shapes']:>3} shapes")
            print(f"  {'':<22} {_depth_line(r['by_depth'])}")
            p, c = r["pairs"], r["chains"]
            print(f"  {'':<22} pairs used {p['used']}/{p['total']} "
                  f"(skipped: {_fmt_counts(p['skipped_counts'])}); "
                  f"chains used {c['used']}/{c['total']} "
                  f"(skipped: {_fmt_counts(c['skipped_counts'])})")
            sc = r.get("self_check") or {}
            if sc.get("caught"):
                print(f"  {'':<22} self-check: corrupted {sc['link']} ({sc['column']} -> "
                      f"{sc['swapped_for']}): oracle leg {sc['oracle_leg']}, "
                      f"conservation leg {sc['conservation_leg']} -> bench FAILED it (good)")
            else:
                print(f"  {'':<22} self-check: NOT CAUGHT - {sc.get('detail')}")
    finally:
        con.close()

    total = sum(r.get("cases", 0) for r in reports)
    passed = sum(r.get("passed", 0) for r in reports)
    failed = sum(r.get("failed", 0) for r in reports)
    gaps = sum(r.get("known_gaps", 0) for r in reports)
    ref_ok = sum(r.get("refused_ok", 0) for r in reports)
    ref_bad = sum(r.get("refusal_failures", 0) for r in reports)
    errors = [r for r in reports if "error" in r]
    answered = passed + failed
    shapes: set[str] = set()
    by_depth: dict[str, dict[str, int]] = {}
    for r in reports:
        for s in r.get("shapes", []):
            shapes.add(f"{r['database']}|{s}")
        for k, d in (r.get("by_depth") or {}).items():
            agg = by_depth.setdefault(k, _empty_depth())
            for kk, vv in d.items():
                agg[kk] = agg.get(kk, 0) + vv
    n_shapes = len(shapes)
    self_missed = [r["database"] for r in reports
                   if "error" not in r and not (r.get("self_check") or {}).get("caught")]

    summary = {
        "cases": total, "answered": answered, "passed": passed, "failed": failed,
        "known_gaps": gaps, "refused_ok": ref_ok, "refusal_failures": ref_bad,
        "n_shapes": n_shapes, "by_depth": by_depth,
        "bound_pct_cases": (300.0 / answered) if answered else None,
        "bound_pct_shapes": (300.0 / n_shapes) if n_shapes else None,
        "self_check_missed": self_missed,
        "errors": [r["database"] for r in errors],
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"summary": summary, "databases": reports},
                                        indent=2, default=str), encoding="utf-8")

    print()
    print(f"  total          {total} generated cases ({answered} answerable, "
          f"{ref_ok + ref_bad} must-refuse, {gaps} known-gap probes)")
    print(f"  shapes         {n_shapes} distinct (link path, group|filter, measure type)")
    print(f"  depth          {_depth_line(by_depth)}")
    if answered:
        print(f"  precision      {100.0 * passed / answered:.2f} pct ({passed}/{answered})")
    if not failed and not ref_bad and answered and not errors:
        print(f"  error bound    0 wrong in {answered} one-hop and multi-hop SUM/filter cases "
              f"over {n_shapes} distinct shapes;")
        print(f"                 by the rule of three about {300.0 / answered:.2f} pct on cases "
              f"and {300.0 / n_shapes:.2f} pct on shapes (R-0010, 95 pct);")
        print("                 multi-key, multi-filter and non-equality requests are not sampled.")
        if n_shapes < 300:
            print(f"  n caveat       {n_shapes} shapes < 300, so '<1 pct' is not"
                  " claimable on shapes")
    if gaps:
        print(f"  KNOWN GAP      {gaps} no-match probes: a filter value matching no dimension row")
        print("                 returns a NULL SUM, indistinguishable from a matched value with")
        print("                 zero fact rows. Counted apart; not a pass.")
        shown = 0
        for r in reports:
            for g in r.get("known_gap_details", []):
                if shown >= 3:
                    break
                print(f"                 - [{r['database']}] {g['case']}")
                shown += 1
    print("  scope          this bounds the deterministic engine layer only -")
    print("                 typed request in, correct rows out. It says nothing about")
    print("                 natural-language understanding; the free-form gate owns that.")

    bad = failed + ref_bad
    if bad:
        print(f"\nFAIL {bad} cases wrong:")
        for r in reports:
            for f in r.get("failures", [])[:12]:
                print(f"  - [{r['database']}] {f['case']}: {f['detail']}")
    if errors:
        print(f"\nFAIL {len(errors)} database(s) did not run: "
              + ", ".join(r["database"] for r in errors))
        for r in errors:
            print(f"  - [{r['database']}] {r['error'][:600]}")
    if self_missed:
        print("\nFAIL self-check: a corrupted join column was not caught on "
              + ", ".join(self_missed) + " (R-0007: a gate that cannot fail certifies nothing)")
    if bad or errors or self_missed:
        return 1
    if not answered:
        print("\nFAIL nothing was generated - the bench measured nothing (R-0002)")
        return 1
    print("\nPASS every generated case agreed with its oracle and conserved, and the")
    print("     corrupted-link self-check was caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
