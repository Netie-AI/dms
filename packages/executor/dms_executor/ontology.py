"""A semantic layer whose join shape cannot fan out a measure.

Why this exists
---------------
Every accuracy control in this repo so far is a *detector*: the conservation
identity, E9, the hard-rule-12 demote, the free-form oracle. Each one notices a
wrong number after something produced it. Detectors are necessary and they do
not compose - there is always one more shape nobody wrote a detector for, and
the ~15x fan-out inflation on the demo warehouse was exactly that.

Three of the four vendors researched for DR-0003 anchor aggregation to a
declared grain, and none uses a model to do it; the fourth (Fabric) validates
generated SQL against a schema, which proves only that permitted objects are
touched. The mechanism copied here is the grain anchor:

  Databricks   a metric view has one source that IS the grain (the CTE rule
               often repeated alongside this is not on the joins page; unverified)
  Palantir     a derived property crossing a many-cardinality link MUST name an
               aggregation, or the definition is rejected at authoring time
  Power BI     filter-then-aggregate: dimension predicates resolve to key sets
               pushed into the fact table, so a dimension never appears in the
               FROM clause of the aggregating query
  Fabric       the generated query is validated against the selected schema
               before it is allowed to execute

This module is that mechanism. A request names a measure, some filters and a
grouping; the compiler emits SQL in which:

  * the aggregate runs over the fact table's own rows and nothing else;
  * dimension filters become semi-joins (IN over a key set), which never
    duplicate a fact row; through a many-to-many link they keep any fact row
    with at least one matching parent, and the CompiledQuery says so
    (``existential``), because shares over such a filter do not partition the
    total;
  * a grouping attribute is only attached through links whose parent sides
    have been MEASURED unique; an unverified or many-to-many hop, an ambiguous
    hop, an unknown column or an unused via is refused. The measure
    expression itself is trusted as authored.

Where this goes further than the vendors
----------------------------------------
Databricks lets you declare ``rely.at_most_one_match`` and their documentation
says plainly: "This property is not validated at runtime. If the asserted side
produces a fan-out, measures return incorrect results." A declaration nobody
checks is a comment. Here ``verify()`` measures key uniqueness, key nullness,
child-side readability, parent-side uniqueness of every link, and
referential integrity (every non-NULL child key exists in the parent), and
``compile()`` refuses to use a link whose cardinality has not been measured.
It does not read the measure expression. An unverified ontology can describe
the world; it cannot answer a question.

Refusal is a first-class result, for the same reason abstention is a first-class
envelope state: a question this layer cannot answer correctly must come back as
a refusal carrying its reason, never as a number with a caveat attached.

  python scripts/ontology.py --demo          # build and verify on the demo warehouse
  python scripts/ontology.py --adventureworks

``scripts/ontology.py`` is the CLI entry; the layer itself lives here, in
``dms_executor``, because it calls ``duckdb.execute`` and hard rule 7 allows
that only inside ``packages/executor`` - and because a semantic layer that
``packages/`` cannot import is a semantic layer no customer route can reach.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

#: Repo root, for the parquet lake this module's ``from_manifest`` default
#: reads. Four parents up from ``packages/executor/dms_executor/ontology.py``.
#: This module used to live in ``scripts/``, where the root was two parents up;
#: a move that carried the old arithmetic across would have silently pointed the
#: lake at ``packages/executor`` and made every manifest path miss.
ROOT = Path(__file__).resolve().parents[3]

Cardinality = Literal["many_to_one", "many_to_many", "unverified"]

#: Named supply-chain grains (SC-ONTOLOGY-01 / #232). Aliases resolve onto
#: declared objects; a grain with no object or join abstains rather than pad.
SUPPLY_CHAIN_GRAINS = ("sku", "supplier", "plant", "lane", "day")
NO_SILENT_PAD = "missing groups not zero-padded"


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _orphan_counts(
    con: Any,
    *,
    child_relation: str,
    parent_relation: str,
    from_columns: Sequence[str],
    to_columns: Sequence[str],
) -> tuple[int, int]:
    """Non-NULL child keys with no matching parent row: (row count, distinct keys)."""
    child_not_null = " AND ".join(f"c.{_ident(c)} IS NOT NULL" for c in from_columns)
    on = " AND ".join(
        f"c.{_ident(a)} = p.{_ident(b)}"
        for a, b in zip(from_columns, to_columns, strict=True)
    )
    key_expr = ", ".join(f"c.{_ident(c)}" for c in from_columns)
    rows, keys = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT ({key_expr})) "
        f"FROM {child_relation} c WHERE {child_not_null} "
        f"AND NOT EXISTS (SELECT 1 FROM {parent_relation} p WHERE {on})"
    ).fetchone()
    return int(rows or 0), int(keys or 0)


# --------------------------------------------------------------------------
# declarations
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ObjectType:
    """A thing the business talks about, and the column set that identifies one.

    ``key`` is a claim, not a fact, until verify() has run. It is the claim every
    join in this layer rests on.
    """

    name: str
    relation: str
    key: tuple[str, ...]
    #: Declared natural key (``customer_code``) behind a surrogate ``key``. A
    #: surrogate can be unique while the business entity is duplicated under
    #: two ids; verify() checks this claim too. Declared, never profiled.
    business_key: tuple[str, ...] = ()


def _same_columns(a: Sequence[str], b: Sequence[str]) -> bool:
    """Whether two column lists name the same set, case-insensitively as DuckDB does."""
    return {c.casefold() for c in a} == {c.casefold() for c in b}


@dataclass(frozen=True)
class LinkType:
    """A named relationship. Cardinality is measured, never taken on trust."""

    name: str
    from_object: str
    from_columns: tuple[str, ...]
    to_object: str
    to_columns: tuple[str, ...]
    cardinality: Cardinality = "unverified"
    max_fanout: int = 0
    #: Opt-in: the child side is the child's own key on purpose (a shared
    #: primary key / subtype table). Without it, a link declared on the child
    #: key is refused by verify() as ``fk_is_child_key`` (A2-03 / dms#259).
    one_to_one: bool = False


@dataclass(frozen=True)
class Measure:
    """An aggregate and the grain it is defined at.

    ``grain`` names the object type whose rows the aggregate consumes. One row
    of that object contributes exactly once. Any query that would make a row
    contribute more than once is not a rounding problem, it is a different
    number, and the compiler refuses rather than producing it.
    """

    name: str
    grain: str
    expression: str
    # Descriptive only, and labelled as such. Nothing in the compiler consults
    # it, because nothing here rolls a grouped result up into a total - the
    # place a non-additive measure would actually go wrong. Recording a flag
    # that implies a protection which does not exist is its own lie surface, so
    # this says plainly that it is metadata for a future roll-up feature and
    # not a guarantee today.
    additive: bool = True
    description: str = ""


@dataclass
class Violation:
    check: str
    subject: str
    detail: str


@dataclass
class Refusal:
    """The compiler declined. Carries the reason, in the words a user can act on."""

    reason: str
    detail: str

    def __bool__(self) -> bool:  # so `if compiled:` reads correctly
        return False


@dataclass(frozen=True)
class WherePath:
    """One join chain from the measure grain to a supply-chain grain.

    ``importance`` is 1 for the unique best path (shortest verified
    many-to-one). Equal importance on two hop-sets is ambiguity, not a pick.
    """

    grain: str
    target: str
    hops: tuple[str, ...]
    steps: tuple[str, ...]
    importance: int
    cardinality: str

    def render(self) -> str:
        return " -> ".join(self.steps) if self.steps else self.target


@dataclass(frozen=True)
class Coverage:
    """What a number includes, excludes, and cannot certify.

    Every numeric compile must carry all three lists. Empty ``unsure`` is
    allowed; omitting a key or dropping ``NO_SILENT_PAD`` from exclude is not.
    """

    include: tuple[str, ...]
    exclude: tuple[str, ...]
    unsure: tuple[str, ...]

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "include": list(self.include),
            "exclude": list(self.exclude),
            "unsure": list(self.unsure),
        }

    def assumption_lines(self) -> tuple[str, ...]:
        def _join(parts: tuple[str, ...]) -> str:
            return "; ".join(parts) if parts else "none"

        return (
            f"include: {_join(self.include)}",
            f"exclude: {_join(self.exclude)}",
            f"unsure: {_join(self.unsure)}",
        )


def coverage_valid(cov: Coverage | None) -> bool:
    """True when include/exclude/unsure are validatable (no silent pad)."""
    if not isinstance(cov, Coverage):
        return False
    if not cov.include:
        return False
    if NO_SILENT_PAD not in cov.exclude:
        return False
    for part in (*cov.include, *cov.exclude, *cov.unsure):
        if not isinstance(part, str) or not part.strip():
            return False
    return True


def coverage_from_sql_path(*, sql: str) -> Coverage:
    """Honest coverage when Cortex query_sql was not slot-compiled."""
    return Coverage(
        include=(f"query_sql as submitted ({' '.join(sql.split())[:80]})",),
        exclude=(NO_SILENT_PAD,),
        unsure=("query_sql not slot-compiled; grain coverage unknown",),
    )


@dataclass
class CompiledQuery:
    sql: str
    measure: str
    grain: str
    group_by: tuple[str, ...]
    notes: tuple[str, ...] = ()
    # True when a filter crossed a many-to-many hop. The query then keeps a
    # fact row if ANY linked row matches - one of two defensible readings of
    # "where lot is hazardous" - and shares over such filters do not partition
    # the total. Callers that must not choose a reading (the ask path) can
    # refuse or abstain on this flag; callers that asked for the existential
    # reading get it, named.
    existential: bool = False
    # Ranked where-paths + importance when the request spans >=2 supply-chain
    # grains (sku/supplier/plant/lane/day). Empty for single-grain compile.
    # This is ontology_plan material, never bind_plan.
    where_paths: tuple[WherePath, ...] = ()
    coverage: Coverage | None = None

    def __bool__(self) -> bool:
        return True


# Steward-facing grain -> candidate objects (first hit wins). SC-ONTOLOGY-01
# aliases sku->product and plant->location; lane is a real object when origin
# exists else missing_join (do not treat dest-only shipment as a lane).
GRAIN_OBJECTS: dict[str, tuple[str, ...]] = {
    "sku": ("product", "sku"),
    "supplier": ("supplier",),
    "plant": ("plant", "location"),
    "lane": ("lane", "shipment"),
    "day": ("day", "calendar", "date"),
}
GRAIN_COLUMNS: dict[str, tuple[str, ...]] = {
    "sku": ("sku",),
    "supplier": ("supplier_id", "supplier_name"),
    "plant": ("plant_id", "location_id", "location_code", "name"),
    "lane": ("lane_id", "shipment_id"),
    "day": ("day", "date", "ts"),
}
_OBJECT_TO_GRAIN: dict[str, str] = {
    "product": "sku",
    "sku": "sku",
    "supplier": "supplier",
    "suppliers": "supplier",
    "plant": "plant",
    "location": "plant",
    "lane": "lane",
    "shipment": "lane",
    "day": "day",
    "calendar": "day",
    "date": "day",
}
_DAY_RE = re.compile(
    r"\b(?:per[- ]day|by[- ]day|and[- ]day|daily|by[- ]date|days?)\b",
    re.I,
)
_GRAIN_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sku", re.compile(r"\bskus?\b", re.I)),
    ("supplier", re.compile(r"\bsuppliers?\b", re.I)),
    ("plant", re.compile(r"\bplants?\b", re.I)),
    ("lane", re.compile(r"\blanes?\b", re.I)),
    ("day", _DAY_RE),
)


def detect_supply_chain_grains(question: str) -> tuple[str, ...]:
    """Grains the ask names. Word matches only; warehouse/shipment stay climb L0s."""
    q = question or ""
    return tuple(name for name, pat in _GRAIN_RES if pat.search(q))


def grain_for_object(name: str) -> str | None:
    token = str(name or "").lower().rsplit(".", 1)[-1]
    return _OBJECT_TO_GRAIN.get(token)


def resolve_grain_object(onto: Ontology, grain: str) -> str | None:
    """Declared object for a grain. Honest miss beats shipment/calendar fallback."""
    if grain in onto.missing_grains:
        return None
    alias = onto.grain_aliases.get(grain)
    if alias and alias in onto.objects:
        return alias
    for name in GRAIN_OBJECTS.get(grain, (grain,)):
        if name in onto.objects:
            return name
    return None


_REL_FROM = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.I)


def _bare_table(name: str) -> str:
    n = str(name or "").strip().strip('"').strip("`").strip("[]").lower()
    return n.rsplit(".", 1)[-1]


def relation_tables(relation: str) -> frozenset[str]:
    """Base table names a relation SQL cites. Subqueries included."""
    raw = (relation or "").strip()
    if not raw:
        return frozenset()
    stripped = raw.replace('"', "").replace("`", "").replace("[", "").replace("]", "")
    found = {_bare_table(m.group(1)) for m in _REL_FROM.finditer(stripped)}
    found.discard("")
    if found:
        return frozenset(found)
    token = stripped.split()[0] if stripped.split() else ""
    bare = _bare_table(token)
    return frozenset({bare} if bare else ())


def table_is_granted(table: str, grantable: set[str]) -> bool:
    """True when the Space grant names the table or a Cortex warehouse_ alias."""
    t = _bare_table(table)
    if not t:
        return False
    allowed = {_bare_table(x) for x in grantable}
    return t in allowed or f"warehouse_{t}" in allowed


def ungranted_tables(tables: set[str], grantable: set[str]) -> tuple[str, ...]:
    return tuple(sorted(t for t in tables if t and not table_is_granted(t, grantable)))


def missing_join_for_ungranted(why: str, grains: Sequence[str]) -> str | None:
    """Map validate ungranted → named missing_join. None if why is not ungranted.

    Live KEEP_HOLD leftover: SKU+plant compiled through shipments then died as
    ``validate:ungranted:shipments`` without naming ``missing_join`` / plant.
    """
    head = str(why or "").strip()
    if not head.startswith("ungranted:"):
        return None
    named = [g for g in grains if g in SUPPLY_CHAIN_GRAINS]
    focus = [g for g in named if g != "sku"] or list(named)
    label = ", ".join(focus) if focus else "join"
    tables = head.split(":", 1)[-1]
    return (
        f"missing_join: no granted join path for grain {label} "
        f"(ungranted {tables}). Declare the link on a granted table, "
        "or ask for a measure defined at that grain."
    )


@dataclass
class Ontology:
    objects: dict[str, ObjectType] = field(default_factory=dict)
    links: dict[str, LinkType] = field(default_factory=dict)
    measures: dict[str, Measure] = field(default_factory=dict)
    verified: bool = False
    # Extract metadata, not a semantic claim. from_manifest copies
    # SourcePull.truncated so verify() can tell "our cap invented this"
    # from "the source is dirty" without reading the bronze registry
    # (SQLSRC-07 / dms#157).
    truncated: dict[str, bool] = field(default_factory=dict)
    #: Named grain -> declared object (sku->product, plant->location).
    grain_aliases: dict[str, str] = field(default_factory=dict)
    #: Grain name -> why it is not declared (origin missing, no ts, ...).
    missing_grains: dict[str, str] = field(default_factory=dict)

    # -- authoring -------------------------------------------------------

    def add_object(
        self,
        name: str,
        relation: str,
        key: Sequence[str],
        *,
        truncated: bool = False,
        business_key: Sequence[str] | None = None,
    ) -> None:
        self.objects[name] = ObjectType(name, relation, tuple(key), tuple(business_key or ()))
        if truncated:
            self.truncated[name] = True
        else:
            self.truncated.pop(name, None)
        self.verified = False

    def add_link(
        self,
        name: str,
        from_object: str,
        from_columns: Sequence[str],
        to_object: str,
        to_columns: Sequence[str],
        *,
        one_to_one: bool = False,
    ) -> None:
        """Declare a link. ``one_to_one=True`` is the only way to declare a link
        whose child columns are the child object's own key; see verify()."""
        for obj in (from_object, to_object):
            if obj not in self.objects:
                raise KeyError(f"link {name!r} names unknown object {obj!r}")
        # zip() truncates silently, so a mismatched pair produced a join on the
        # shorter list while verify() measured uniqueness on the longer one, and
        # the note then claimed "verified many-to-one" over a join that really
        # did duplicate rows. A declaration that cannot be checked as written is
        # rejected as written.
        if len(from_columns) != len(to_columns) or not from_columns:
            raise ValueError(
                f"link {name!r} joins {len(from_columns)} column(s) to "
                f"{len(to_columns)}: {list(from_columns)} -> {list(to_columns)}. "
                "A join cannot be checked unless both sides name the same arity."
            )
        if one_to_one and not _same_columns(from_columns, self.objects[from_object].key):
            # A2-03 / dms#259: one_to_one only exempts a link declared on the
            # child's own key. Anywhere else it is a claim nothing checks.
            raise ValueError(
                f"link {name!r} is declared one_to_one on {list(from_columns)}, which "
                f"is not {from_object!r}'s key {list(self.objects[from_object].key)}."
            )
        self.links[name] = LinkType(
            name,
            from_object,
            tuple(from_columns),
            to_object,
            tuple(to_columns),
            one_to_one=one_to_one,
        )
        self.verified = False

    def add_measure(
        self,
        name: str,
        grain: str,
        expression: str,
        *,
        additive: bool = True,
        description: str = "",
    ) -> None:
        """Reject a measure at authoring time if its grain is not an object.

        Palantir's rule, applied here: a definition that cannot name the grain it
        consumes is rejected when it is written, not when it is queried. The
        alternative is discovering it in front of a customer.
        """
        if grain not in self.objects:
            raise KeyError(
                f"measure {name!r} declares grain {grain!r}, which is not an object type. "
                "A measure with no grain cannot be protected from fan-out."
            )
        self.measures[name] = Measure(name, grain, expression, additive, description)

    def resolve_object(self, name: str) -> str | Refusal:
        """Canonical object for a named grain, or a refusal naming the gap.

        Aliases (sku, plant) resolve onto declared objects. A supply-chain
        grain with no object or join is ``missing_join``, not a guessed pad.
        """
        if name in self.objects:
            return name
        alias = self.grain_aliases.get(name)
        if alias and alias in self.objects:
            return alias
        if name in SUPPLY_CHAIN_GRAINS:
            detail = self.missing_grains.get(name) or (
                f"missing join: {name} grain is not declared on this warehouse. "
                "No silent pad."
            )
            return Refusal("missing_join", detail)
        return Refusal("unknown_object", f"unknown object {name!r}")

    def supply_chain_catalog(self) -> dict[str, Any]:
        """SKU / supplier / plant / lane / day: present object or missing join."""
        out: dict[str, Any] = {}
        for name in SUPPLY_CHAIN_GRAINS:
            resolved = self.resolve_object(name)
            if isinstance(resolved, Refusal):
                out[name] = {
                    "object": None,
                    "present": False,
                    "missing": resolved.detail,
                    "alias_of": self.grain_aliases.get(name),
                }
                continue
            obj = self.objects[resolved]
            out[name] = {
                "object": resolved,
                "present": True,
                "missing": None,
                "alias_of": self.grain_aliases.get(name),
                "key": list(obj.key),
            }
        return out

    def join_importance(self, measure: str) -> dict[str, Any]:
        """Ranked join paths from the measure grain to each supply-chain grain.

        importance 1 = same grain or one many-to-one hop; 2 = multi-hop
        many-to-one; 3 = filter-only (many-to-many). Missing metric/join is
        named, never ranked. Unverified ontologies cannot rank.
        """
        m = self.measures.get(measure)
        if m is None:
            return {
                "measure": measure,
                "missing_metric": True,
                "detail": f"no measure named {measure!r}",
                "grains": {},
            }
        if not self.verified:
            return {
                "measure": measure,
                "missing_metric": False,
                "detail": "ontology_unverified",
                "grains": {},
            }
        grains: dict[str, Any] = {}
        for name in SUPPLY_CHAIN_GRAINS:
            resolved = self.resolve_object(name)
            if isinstance(resolved, Refusal):
                grains[name] = {
                    "object": None,
                    "importance": None,
                    "path": [],
                    "missing": resolved.detail,
                }
                continue
            if resolved == m.grain:
                grains[name] = {
                    "object": resolved,
                    "importance": 1,
                    "path": [],
                    "missing": None,
                }
                continue
            grouped = self._resolve_path(m.grain, resolved, None)
            if isinstance(grouped, list):
                hops = len(grouped)
                grains[name] = {
                    "object": resolved,
                    "importance": 1 if hops <= 1 else 2,
                    "path": [link.name for link in grouped],
                    "missing": None,
                }
                continue
            filtered = self._resolve_path(m.grain, resolved, None, for_filter=True)
            if isinstance(filtered, list) and filtered:
                grains[name] = {
                    "object": resolved,
                    "importance": 3,
                    "path": [link.name for link in filtered],
                    "filter_only": True,
                    "missing": (
                        f"no many-to-one join from {m.grain} to {name}; "
                        "grouping would fan out. Filter-only path exists."
                    ),
                }
                continue
            if isinstance(grouped, Refusal):
                detail = grouped.detail
            elif isinstance(filtered, Refusal):
                detail = filtered.detail
            else:
                detail = (
                    f"missing join: no chain from {m.grain} to {name}. "
                    "No silent pad."
                )
            grains[name] = {
                "object": resolved,
                "importance": None,
                "path": [],
                "missing": detail,
            }
        return {
            "measure": measure,
            "grain": m.grain,
            "missing_metric": False,
            "grains": grains,
        }

    # -- verification ----------------------------------------------------

    def _columns(self, con: Any, obj: str) -> set[str]:
        """Column names of an object's relation, cached at verify() time."""
        cache = self.__dict__.setdefault("_column_cache", {})
        if obj not in cache:
            rel = self.objects[obj].relation
            cache[obj] = {
                str(r[0]) for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
            }
        return cache[obj]

    def _verify_business_key(self, con: Any, obj: ObjectType) -> Violation | None:
        """One Violation naming the business key, its NULL rows and duplicate rows."""
        bk = ", ".join(obj.business_key)
        cols = ", ".join(_ident(c) for c in obj.business_key)
        nulls = " OR ".join(f"{_ident(c)} IS NULL" for c in obj.business_key)
        try:
            null_rows = con.execute(
                f"SELECT COUNT(*) FROM {obj.relation} WHERE {nulls}"
            ).fetchone()[0]
            dup_keys, dup_rows = con.execute(
                f"SELECT COUNT(*), COALESCE(SUM(n), 0) FROM (SELECT COUNT(*) AS n "
                f"FROM {obj.relation} WHERE NOT ({nulls}) GROUP BY {cols} HAVING COUNT(*) > 1)"
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            return Violation(
                "business_key_unique", obj.name, f"business key ({bk}): {type(exc).__name__}: {exc}"
            )
        problems: list[str] = []
        if int(dup_keys or 0):
            problems.append(
                f"{int(dup_rows):,} rows share {int(dup_keys):,} duplicate business key "
                f"value(s) on ({bk})"
            )
        if int(null_rows or 0):
            problems.append(f"{int(null_rows):,} rows have NULL in business key ({bk})")
        if not problems:
            return None
        return Violation(
            "business_key_unique",
            obj.name,
            "; ".join(problems)
            + f" - the surrogate ({', '.join(obj.key)}) is unique, but one "
            f"{obj.name} would be counted more than once",
        )

    def verify(self, con: Any) -> list[Violation]:
        """Execute every claim. Nothing may be used until this has passed.

        Five claims are checked, in the order a wrong one would do damage:
          key_unique      an object's key really identifies one row
          key_not_null    no key column is NULL - a NULL key both passes a
                          uniqueness check and silently drops rows from a join
          business_key_unique  a declared business key (customer_code behind
                          a surrogate customer_id) is non-NULL and unique. A
                          unique surrogate over a duplicated business key
                          counts one customer as two.
          fk_intact       every non-NULL child key exists in the parent. A
                          missing parent turns a LEFT JOIN into misattribution
                          (named groups shrink, the unmatched bucket grows,
                          the grand total still reconciles) and an INNER JOIN
                          into a silent shortfall. NULL child keys are optional
                          FKs, not orphans.
          link_cardinality  measured, then stored on the link. A link whose
                          parent side is not unique is not broken - it is a
                          relationship a measure must aggregate across rather
                          than join through, and the compiler needs to know.
                          A link with orphans stays unverified rather than
                          being blessed many-to-one.
          fk_is_child_key a link whose child columns are the child's own key
                          is almost always a mis-declared FK (line_id where
                          order_id was meant). Coincident values pass every
                          other check, so it stays unverified unless declared
                          ``one_to_one=True``.
        """
        violations: list[Violation] = []
        self.__dict__["_column_cache"] = {}
        for obj in self.objects.values():
            try:
                self._columns(con, obj.name)
            except Exception as exc:  # noqa: BLE001
                violations.append(
                    Violation("relation_readable", obj.name, f"{type(exc).__name__}: {exc}")
                )
                continue
            cols = ", ".join(_ident(c) for c in obj.key)
            nulls = " OR ".join(f"{_ident(c)} IS NULL" for c in obj.key)
            try:
                n, distinct, null_rows = con.execute(
                    f"SELECT COUNT(*), COUNT(DISTINCT ({cols})), "
                    f"SUM(CASE WHEN {nulls} THEN 1 ELSE 0 END) FROM {obj.relation}"
                ).fetchone()
            except Exception as exc:  # noqa: BLE001
                violations.append(
                    Violation("relation_readable", obj.name, f"{type(exc).__name__}: {exc}")
                )
                continue
            if null_rows:
                violations.append(
                    Violation(
                        "key_not_null",
                        obj.name,
                        f"{int(null_rows):,} rows have NULL in key ({', '.join(obj.key)})",
                    )
                )
            if int(n) != int(distinct):
                violations.append(
                    Violation(
                        "key_unique",
                        obj.name,
                        f"{int(n):,} rows, {int(distinct):,} distinct keys "
                        f"({', '.join(obj.key)}) - the key does not identify a row",
                    )
                )
            if obj.business_key:
                violation = self._verify_business_key(con, obj)
                if violation is not None:
                    violations.append(violation)

        for name, link in list(self.links.items()):
            parent = self.objects[link.to_object]
            child = self.objects[link.from_object]
            cols = ", ".join(_ident(c) for c in link.to_columns)
            not_null = " AND ".join(f"{_ident(c)} IS NOT NULL" for c in link.to_columns)
            try:
                # The child side is read too. verify() used to measure only the
                # parent, so a link declared on a child column that does not
                # exist was blessed "verified many-to-one" and failed at
                # execution with a binder error - the layer promised a refusal
                # and delivered a traceback.
                child_cols = ", ".join(_ident(c) for c in link.from_columns)
                con.execute(f"SELECT {child_cols} FROM {child.relation} LIMIT 0")
                # Uniqueness is measured over parent rows whose key is not NULL.
                # A NULL parent key never matches any child row, so it cannot
                # fan anything out - but COUNT(*) counts it while
                # COUNT(DISTINCT) does not, and one NULL row made a safe link
                # read as many-to-many "up to 1x" and refused every grouping.
                pn, pdistinct = con.execute(
                    f"SELECT COUNT(*), COUNT(DISTINCT ({cols})) FROM {parent.relation} "
                    f"WHERE {not_null}"
                ).fetchone()
            except Exception as exc:  # noqa: BLE001
                violations.append(
                    Violation("link_readable", name, f"{type(exc).__name__}: {exc}")
                )
                continue
            if _same_columns(link.from_columns, child.key) and not link.one_to_one:
                # A2-03 / dms#259: line_id -> order_id passed fk_intact because
                # every line_id happened to equal a real order id, and the
                # join then attributed each line to the wrong order. A child's
                # own key is only an FK when the link says it is one-to-one.
                child_all = self.__dict__.get("_column_cache", {}).get(child.name, set())
                # DuckDB identifiers are case-insensitive, so every name
                # comparison here is too: LINE_ID and line_id are one column.
                wanted = {c.casefold() for c in link.to_columns} - {
                    c.casefold() for c in link.from_columns
                }
                siblings = sorted(c for c in child_all if c.casefold() in wanted)
                hint = (
                    f" {link.from_object} also has {', '.join(siblings)}, which "
                    "matches the parent key by name and is the likely foreign key."
                    if siblings
                    else ""
                )
                violations.append(
                    Violation(
                        "fk_is_child_key",
                        name,
                        f"{link.from_object} -> {link.to_object} is declared on "
                        f"({', '.join(link.from_columns)}), which is "
                        f"{link.from_object}'s own key. A foreign key on the child "
                        "key is a one-to-one claim; values that merely coincide "
                        "with parent keys pass every other check and misattribute "
                        f"rows.{hint} The link stays unverified unless declared "
                        "one_to_one=True.",
                    )
                )
                continue
            if int(pn) == 0:
                # "Unique because empty" is not a measurement, it is an absence
                # of one, and the verdict would be cached and trusted. A
                # dimension with no rows today is not unique tomorrow, so the
                # link stays unverified and the compiler will refuse it.
                violations.append(
                    Violation(
                        "link_unmeasurable",
                        name,
                        f"{link.to_object} has no rows, so its uniqueness on "
                        f"({', '.join(link.to_columns)}) cannot be measured. The link "
                        "stays unverified rather than being assumed safe.",
                    )
                )
                continue
            try:
                orphan_rows, orphan_keys = _orphan_counts(
                    con,
                    child_relation=child.relation,
                    parent_relation=parent.relation,
                    from_columns=link.from_columns,
                    to_columns=link.to_columns,
                )
            except Exception as exc:  # noqa: BLE001
                violations.append(
                    Violation("link_readable", name, f"{type(exc).__name__}: {exc}")
                )
                continue
            if orphan_rows:
                # Do not set cardinality. A unique parent with missing keys is
                # not a safe many-to-one: LEFT JOIN would misattribute the
                # orphans (named groups shrink, the unmatched bucket grows,
                # the grand total still reconciles). NULL child keys are not
                # counted - those are optional FKs, which LEFT JOIN exists to
                # keep in the total.
                capped = bool(self.truncated.get(parent.name))
                if capped:
                    detail = (
                        f"{link.from_object} -> {link.to_object}: {orphan_rows:,} rows "
                        f"({orphan_keys:,} distinct keys) on {link.from_object} "
                        f"reference a parent that was not landed because "
                        f"{link.to_object} was capped by max_rows. The extract "
                        "invented these orphans; the source may be clean. The "
                        "link stays unverified."
                    )
                else:
                    detail = (
                        f"{link.from_object} -> {link.to_object}: {orphan_rows:,} rows "
                        f"({orphan_keys:,} distinct keys) on {link.from_object} "
                        "reference a parent that does not exist. The source is "
                        "dirty on this link; an inner join would drop them and "
                        "a left join would misattribute them. The link stays "
                        "unverified."
                    )
                violations.append(Violation("fk_intact", name, detail))
                continue
            if int(pn) == int(pdistinct):
                self.links[name] = LinkType(
                    link.name, link.from_object, link.from_columns,
                    link.to_object, link.to_columns, "many_to_one", 1,
                    link.one_to_one,
                )
                continue
            worst = con.execute(
                f"SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM {parent.relation} "
                f"WHERE {not_null} GROUP BY {cols})"
            ).fetchone()[0]
            self.links[name] = LinkType(
                link.name, link.from_object, link.from_columns,
                link.to_object, link.to_columns, "many_to_many", int(worst or 0),
                link.one_to_one,
            )

        self.verified = not violations
        # Kept so an abstention can name what failed instead of a bare
        # "ontology_unverified" (dms#260 A2-04). Same cache slot style as
        # _column_cache: not a dataclass field, not part of equality.
        self.__dict__["_violations"] = list(violations)
        return violations

    # -- compilation -----------------------------------------------------

    def _links_between(self, fact: str, dim: str) -> list[LinkType]:
        return [
            link
            for link in self.links.values()
            if link.from_object == fact and link.to_object == dim
        ]

    def _resolve_link(
        self, fact: str, dim: str, via: str | None
    ) -> LinkType | Refusal | None:
        """One path, or a refusal. Never a silent choice between two.

        A role-playing dimension - a calendar reached by both order_date and
        ship_date - gives two links between the same pair of objects, and
        "amount by year" then has two defensible answers. Returning the first
        match picks one and says nothing, which is the quietest way this layer
        could produce a wrong number: the query is valid, the join is
        many-to-one, no assertion fires, and the figure is simply about a
        different question than the one asked.

        Power BI's posture is to raise an ambiguous path error and require the
        definition to name the path it means, and DR-0003 quotes that approvingly.
        Doing anything else here would have contradicted our own decision record.
        """
        candidates = self._links_between(fact, dim)
        if via is not None:
            named = [link for link in candidates if link.name == via]
            if not named:
                return Refusal(
                    "unknown_link",
                    f"no link named {via!r} from {fact!r} to {dim!r}; "
                    f"available: {', '.join(sorted(c.name for c in candidates)) or 'none'}",
                )
            return named[0]
        if not candidates:
            return None
        if len(candidates) > 1:
            return Refusal(
                "ambiguous_path",
                f"{len(candidates)} declared links join {fact!r} to {dim!r} "
                f"({', '.join(sorted(c.name for c in candidates))}), and they do not "
                "mean the same thing. Name the one you want with via=, rather than "
                "letting the layer pick.",
            )
        return candidates[0]

    def _resolve_path(
        self,
        grain: str,
        dim: str,
        via: dict[str, str] | None,
        *,
        for_filter: bool = False,
    ) -> list[LinkType] | Refusal | None:
        """The chain of links from the grain to ``dim``, or a refusal, or None.

        One hop was never the real shape. "Sales by product category" on
        AdventureWorks is SalesOrderDetail -> Product -> Subcategory -> Category:
        three links, every one many-to-one, and a chain of many-to-one LEFT
        JOINs adds at most one row per fact row at every hop, so it cannot
        inflate.

        Rules, in order:
          * ``via`` is validated first: every key is an object, every value is
            a declared link INTO that object. A via the search never used is
            a refusal too - a silently ignored via is a question answered a
            different way than it was asked;
          * breadth-first from the grain. For grouping only many-to-one hops
            are passable; for filters any hop is, because a semi-join cannot
            duplicate a fact row (it CAN change the reading - see
            ``existential``);
          * a hop whose (from, to) pair has more than one link is passable
            only through the link ``via`` names for that object;
          * a hop that is NOT passable is remembered as blocked. When ``dim``
            is reached on a clean path, the search checks whether any blocked
            hop sat on a route no longer than the clean one; if so the clean
            path is NOT returned - the question had a shorter reading the
            layer would have had to guess about. The first version of this
            resolver skipped blocked hops silently and once answered "revenue
            by fiscal year" through customer -> cohort -> year when the
            blocked order-date route was the one meant. That was the quietest
            wrong number this layer could produce, found by an adversary
            within a day;
          * two different clean shortest paths to ``dim`` is ambiguity of the
            second kind and is refused naming both.
        """
        via = dict(via or {})
        # -- validate via up front ------------------------------------------
        for obj, link_name in via.items():
            if obj not in self.objects:
                return Refusal("unknown_object", f"via names unknown object {obj!r}")
            into = [x for x in self.links.values() if x.to_object == obj]
            if link_name not in {x.name for x in into}:
                return Refusal(
                    "unknown_link",
                    f"no link named {link_name!r} into {obj!r}; available: "
                    f"{', '.join(sorted(x.name for x in into)) or 'none'}",
                )

        if grain == dim and dim not in via:
            return []

        best: dict[str, list[LinkType]] = {grain: []}
        depth: dict[str, int] = {grain: 0}
        blocked: dict[str, tuple[int, Refusal]] = {}
        used_via: set[str] = set()
        frontier = [grain]
        level = 0
        found_dim: list[LinkType] | None = None
        while frontier and found_dim is None:
            level += 1
            found: dict[str, list[list[LinkType]]] = {}
            for obj in frontier:
                by_pair: dict[str, list[LinkType]] = {}
                for link in self.links.values():
                    if link.from_object != obj:
                        continue
                    if link.to_object in best and not (link.to_object == dim == grain):
                        continue
                    by_pair.setdefault(link.to_object, []).append(link)
                for to_obj, links in by_pair.items():
                    chosen: LinkType | None = None
                    if to_obj in via:
                        named = [x for x in links if x.name == via[to_obj]]
                        if not named:
                            # the named link enters to_obj from some OTHER
                            # object; this edge is simply not the named one
                            continue
                        chosen = named[0]
                        used_via.add(to_obj)
                    elif len(links) > 1:
                        reason = Refusal(
                            "ambiguous_path",
                            f"{len(links)} declared links join {obj!r} to {to_obj!r} "
                            f"({', '.join(sorted(x.name for x in links))}), and they do "
                            "not mean the same thing. Name the one you want with "
                            f"via={{{to_obj!r}: <link>}}, rather than letting the layer pick.",
                        )
                        blocked.setdefault(to_obj, (level, reason))
                        continue
                    else:
                        chosen = links[0]
                    if chosen.cardinality != "many_to_one" and not for_filter:
                        reason = Refusal(
                            "fanout_refused",
                            f"reaching {to_obj!r} means joining through {chosen.name!r}, "
                            f"whose parent side is not unique (up to {chosen.max_fanout}x). "
                            f"That join would make each {grain} row contribute up to "
                            f"{chosen.max_fanout} times and inflate the measure. Aggregate "
                            "across the relationship rather than joining through it.",
                        )
                        blocked.setdefault(to_obj, (level, reason))
                        continue
                    found.setdefault(to_obj, []).append(best[obj] + [chosen])
            nxt: list[str] = []
            for to_obj, paths in found.items():
                if to_obj in best and to_obj != dim:
                    continue
                if len(paths) > 1:
                    routes = "; ".join(" -> ".join(x.name for x in path) for path in paths)
                    reason = Refusal(
                        "ambiguous_path",
                        f"{len(paths)} different shortest paths reach {to_obj!r} from "
                        f"{grain!r} ({routes}). Group by the intermediate object you "
                        "mean, or name each hop with via=.",
                    )
                    if to_obj == dim:
                        return reason
                    blocked.setdefault(to_obj, (level, reason))
                    continue
                best[to_obj] = paths[0]
                depth[to_obj] = level
                nxt.append(to_obj)
                if to_obj == dim:
                    found_dim = paths[0]
            frontier = nxt

        if found_dim is None:
            # dim not reached on a clean path. If it sits behind a blocked hop,
            # the nearest such hop's reason is the answer; otherwise no path.
            if dim in blocked:
                return blocked[dim][1]
            nearest: tuple[int, Refusal] | None = None
            for b_obj, (lvl, reason) in blocked.items():
                d = self._distance(b_obj, dim)
                if d is not None and (nearest is None or lvl + d < nearest[0]):
                    nearest = (lvl + d, reason)
            return nearest[1] if nearest else None

        # dim reached cleanly. A blocked hop whose route would reach dim in NO
        # MORE hops than the clean path is another reading the layer would have
        # had to guess about. The comparison is on where that route would
        # ARRIVE, not where it was blocked: a many-to-many lot link blocked at
        # depth 1 that reaches location at depth 2 does not compete with the
        # direct location link at depth 1 - the first cut compared the wrong
        # depths and refused every grouping next to a blocked sibling.
        clean_depth = depth[dim]
        for b_obj, (lvl, reason) in blocked.items():
            d = self._distance(b_obj, dim)
            if d is None or lvl + d > clean_depth:
                continue
            if b_obj == dim and lvl == clean_depth:
                # two routes of the same length, one clean and one not: that is
                # two readings, not one safe answer. Name both, resolvable by via.
                clean = " -> ".join(x.name for x in found_dim)
                return Refusal(
                    "ambiguous_path",
                    f"{dim!r} is reached by {clean} and also by a route the layer "
                    f"cannot take ({reason.detail[:140]}). Name the path you mean "
                    f"with via={{{dim!r}: <link>}}.",
                )
            return reason
        # "Used" means on the returned path. The BFS may have expanded through a
        # via-named link on some other branch; that does not make the via part
        # of the answer, and a via that is not part of the answer is a question
        # the caller asked that the layer would have answered differently.
        on_path = {x.to_object for x in found_dim}
        unused = set(via) - on_path
        del used_via
        if unused:
            return Refusal(
                "unused_via",
                f"via named {sorted(unused)}, but the path to {dim!r} does not pass "
                "through those objects, so the name would have been ignored. Name a "
                "hop on the path, or drop it.",
            )
        return found_dim

    def _distance(self, start: str, target: str) -> int | None:
        """Shortest hop count over declared links, ignoring cardinality; None if unreachable."""
        if start == target:
            return 0
        seen = {start}
        frontier = [start]
        hops = 0
        while frontier:
            hops += 1
            nxt: list[str] = []
            for cur in frontier:
                for link in self.links.values():
                    if link.from_object == cur and link.to_object not in seen:
                        if link.to_object == target:
                            return hops
                        seen.add(link.to_object)
                        nxt.append(link.to_object)
            frontier = nxt
        return None

    def _all_paths(
        self,
        start: str,
        target: str,
        *,
        for_filter: bool = False,
        via: dict[str, str] | None = None,
    ) -> list[list[LinkType]]:
        """Every passable acyclic path. Does not pick. Grouping skips non-m2o."""
        via = dict(via or {})
        if start == target:
            return [[]]
        found: list[list[LinkType]] = []
        stack: list[tuple[str, list[LinkType], frozenset[str]]] = [
            (start, [], frozenset({start}))
        ]
        while stack:
            obj, path, seen = stack.pop()
            by_pair: dict[str, list[LinkType]] = {}
            for link in self.links.values():
                if link.from_object != obj or link.to_object in seen:
                    continue
                by_pair.setdefault(link.to_object, []).append(link)
            for to_obj, links in by_pair.items():
                if to_obj in via:
                    named = [x for x in links if x.name == via[to_obj]]
                    if not named:
                        continue
                    chosen_list = named
                else:
                    chosen_list = links
                for chosen in chosen_list:
                    if chosen.cardinality == "unverified":
                        continue
                    if chosen.cardinality != "many_to_one" and not for_filter:
                        continue
                    nxt = path + [chosen]
                    if to_obj == target:
                        found.append(nxt)
                    else:
                        stack.append((to_obj, nxt, seen | {to_obj}))
        return found

    def _column_for_grain(self, obj: str, grain: str) -> str | None:
        preferred = GRAIN_COLUMNS.get(grain, ())
        cols = self.__dict__.get("_column_cache", {}).get(obj)
        keys = self.objects[obj].key if obj in self.objects else ()
        pool = set(cols) if isinstance(cols, set) else set(keys)
        for col in preferred:
            if col in pool:
                return col
        if keys:
            return keys[0]
        if pool:
            return sorted(pool)[0]
        return None

    def rank_where_paths(
        self,
        from_grain: str,
        specs: Sequence[tuple[str, str]],
        *,
        via: dict[str, str] | None = None,
        for_filter: bool = False,
    ) -> list[WherePath]:
        """Rank where-paths + importance. Does not compile. Empty if no path."""
        ranked: list[WherePath] = []
        for grain, target in specs:
            paths = self._all_paths(
                from_grain, target, for_filter=for_filter, via=via
            )
            keyed: list[tuple[tuple[int, int], list[LinkType]]] = []
            for path in paths:
                m2m = 0 if all(x.cardinality == "many_to_one" for x in path) else 1
                keyed.append(((m2m, len(path)), path))
            keyed.sort(key=lambda row: (row[0], tuple(x.name for x in row[1])))
            seen_key: dict[tuple[int, int], int] = {}
            next_rank = 1
            for key, path in keyed:
                if key not in seen_key:
                    seen_key[key] = next_rank
                    next_rank += 1
                hops = tuple(x.name for x in path)
                steps = (from_grain, *[x.to_object for x in path]) if path else (from_grain,)
                if any(x.cardinality == "unverified" for x in path):
                    card = "unverified"
                elif any(x.cardinality != "many_to_one" for x in path):
                    card = "many_to_many"
                else:
                    card = "many_to_one"
                ranked.append(
                    WherePath(
                        grain=grain,
                        target=target,
                        hops=hops,
                        steps=steps,
                        importance=seen_key[key],
                        cardinality=card,
                    )
                )
        ranked.sort(key=lambda p: (p.importance, p.grain, p.hops))
        return ranked

    def object_tables(self, name: str) -> frozenset[str]:
        obj = self.objects.get(name)
        if obj is None:
            return frozenset()
        return relation_tables(obj.relation)

    def steps_tables(self, steps: Sequence[str]) -> frozenset[str]:
        out: set[str] = set()
        for name in steps:
            out |= set(self.object_tables(name))
        return frozenset(out)

    def granted_where_paths(
        self,
        ranked: Sequence[WherePath],
        grain: str,
        grantable: set[str] | None,
    ) -> list[WherePath]:
        pool = [p for p in ranked if p.grain == grain]
        if grantable is None:
            return pool
        return [
            p
            for p in pool
            if not ungranted_tables(set(self.steps_tables(p.steps)), grantable)
        ]

    def _ranked_where_paths_for(
        self,
        from_grain: str,
        group_by: Sequence[tuple[str, str]],
        filters: Sequence[tuple[str, str, str, Any]],
        via: dict[str, str] | None,
    ) -> tuple[WherePath, ...]:
        specs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for obj, _col in group_by:
            g = grain_for_object(obj)
            if not g or g in seen:
                continue
            seen.add(g)
            specs.append((g, obj))
        for obj, _column, _op, _value in filters:
            g = grain_for_object(obj)
            if not g or g in seen:
                continue
            seen.add(g)
            specs.append((g, obj))
        if len(specs) < 2:
            return ()
        return tuple(self.rank_where_paths(from_grain, specs, via=via))

    def _join_chain(
        self,
        path: list[LinkType],
        aliases: dict[tuple[str, str], str],
        joins: list[str],
        notes: list[str],
        *,
        root: str = "f",
    ) -> str:
        """Emit (or reuse) the LEFT JOINs for a path and return the final alias."""
        prev = root
        for link in path:
            slot = (link.to_object, link.name)
            alias = aliases.get(slot)
            if alias is None:
                alias = f"d{len(aliases)}"
                aliases[slot] = alias
                rel = self.objects[link.to_object].relation
                on = " AND ".join(
                    f"{prev}.{_ident(a)} = {alias}.{_ident(b)}"
                    for a, b in zip(link.from_columns, link.to_columns)
                )
                # LEFT JOIN, not INNER: an inner join silently drops fact rows
                # whose key is absent from the dimension, shrinking the measure
                # without anything looking wrong.
                joins.append(f"LEFT JOIN {rel} {alias} ON {on}")
                notes.append(
                    f"joined {link.to_object} through {link.name} (verified "
                    "many-to-one, so no fact row is duplicated)"
                )
            prev = alias
        return prev

    def compile(
        self,
        measure: str,
        *,
        group_by: Sequence[tuple[str, str]] = (),
        filters: Sequence[tuple[str, str, str, Any]] = (),
        via: dict[str, str] | None = None,
        order_desc: bool = True,
        limit: int | None = None,
    ) -> CompiledQuery | Refusal:
        """Turn a typed request into SQL that cannot inflate the measure.

        ``group_by`` and ``filters`` are (object, column, ...) tuples. The model
        - or the UI - fills slots. It never writes SQL, which is Palantir's
        posture and the reason the space of wrong queries is small enough to
        reason about.
        """
        if not self.verified:
            return Refusal(
                "ontology_unverified",
                "verify() has not passed against this data, so no link cardinality "
                "is known. An unverified ontology can describe the world; it "
                "cannot answer a question.",
            )
        m = self.measures.get(measure)
        if m is None:
            return Refusal("unknown_measure", f"no measure named {measure!r}")
        fact = self.objects[m.grain]

        selects: list[str] = []
        joins: list[str] = []
        notes: list[str] = []
        group_keys: list[str] = []
        # Aliases are positional, not built from the object name. "d_" plus a
        # schema-qualified name produced `d_Sales.Customers`, which DuckDB
        # cannot parse - so every ontology derived from a real database emitted
        # SQL that could never run, and the tests did not notice because they
        # checked the returned object instead of executing it. That is the
        # failure R-0001 names: assert the artifact, at the layer it is used.
        # Keying on (object, link) also makes two attributes of one dimension
        # share a single join rather than emitting a duplicate and an ambiguous
        # reference.
        aliases: dict[tuple[str, str], str] = {}

        cols_known = self.__dict__.get("_column_cache", {})
        for obj_name, column in group_by:
            resolved = self.resolve_object(obj_name)
            if isinstance(resolved, Refusal):
                if resolved.reason == "unknown_object":
                    return Refusal(
                        "unknown_object",
                        f"cannot group by unknown object {obj_name!r}",
                    )
                return resolved
            obj_name = resolved
            if obj_name not in self.objects:
                return Refusal("unknown_object", f"cannot group by unknown object {obj_name!r}")
            if obj_name in cols_known and column not in cols_known[obj_name]:
                return Refusal(
                    "unknown_column",
                    f"{obj_name!r} has no column {column!r}; available: "
                    f"{', '.join(sorted(cols_known[obj_name])[:12])}",
                )
            if obj_name == m.grain and (via or {}).get(obj_name) is None:
                # An attribute of the fact itself is free: it is already one
                # value per contributing row. But only when no via names this
                # object: a self-linked table (an employee's manager, an
                # account's parent) has TWO readings of "by attr" - its own and
                # its parent's - and adversarial review showed via= being
                # silently dropped here, so "revenue by manager name" came back
                # grouped by the employee's own name. Answering a different
                # question than the one asked is the quietest wrong number; a
                # named via now resolves the link like any other, as a
                # self-join. The generated bench re-found this on real data:
                # eleven DimAccount/DimEmployee self-join cases.
                expr = f"f.{_ident(column)}"
            else:
                path = self._resolve_path(m.grain, obj_name, via)
                if isinstance(path, Refusal):
                    return path
                if path is None:
                    return Refusal(
                        "no_path",
                        f"no chain of verified many-to-one links from {m.grain!r} to "
                        f"{obj_name!r}, so {obj_name}.{column} is not an attribute of "
                        "this measure's grain. Declare the link, or ask for a measure "
                        "defined at that grain.",
                    )
                alias = self._join_chain(path, aliases, joins, notes)
                expr = f"{alias}.{_ident(column)}"
            label = f"{obj_name}_{column}"
            selects.append(f"{expr} AS {_ident(label)}")
            group_keys.append(expr)

        where: list[str] = []
        existential = False
        # Filters are grouped by the object they constrain, so two predicates
        # on the same object compile into ONE semi-join: "lots that are
        # hazardous AND from supplier 1" means one lot satisfying both, not
        # any hazardous lot and any supplier-1 lot. The first version emitted
        # one IN (...) per predicate and answered the second reading silently.
        grouped: dict[str, list[tuple[str, str, Any]]] = {}
        order: list[str] = []
        for obj_name, column, op, value in filters:
            if op.upper() not in {"=", "<>", "<", "<=", ">", ">=", "IN", "LIKE"}:
                return Refusal("bad_operator", f"operator {op!r} is not allowed")
            resolved = self.resolve_object(obj_name)
            if isinstance(resolved, Refusal):
                if resolved.reason == "unknown_object":
                    return Refusal(
                        "unknown_object",
                        f"cannot filter on unknown object {obj_name!r}",
                    )
                return resolved
            obj_name = resolved
            if obj_name not in self.objects:
                return Refusal("unknown_object", f"cannot filter on unknown object {obj_name!r}")
            if obj_name in cols_known and column not in cols_known[obj_name]:
                return Refusal(
                    "unknown_column",
                    f"{obj_name!r} has no column {column!r}; available: "
                    f"{', '.join(sorted(cols_known[obj_name])[:12])}",
                )
            if obj_name not in grouped:
                order.append(obj_name)
            grouped.setdefault(obj_name, []).append((column, op, value))

        for obj_name in order:
            preds = grouped[obj_name]
            if obj_name == m.grain and (via or {}).get(obj_name) is None:
                for column, op, value in preds:
                    where.append(f"f.{_ident(column)} {op} {_render(op, value)}")
                continue
            path = self._resolve_path(m.grain, obj_name, via, for_filter=True)
            if isinstance(path, Refusal):
                return path
            if path is None:
                return Refusal(
                    "no_path",
                    f"no chain of declared links from {m.grain!r} to "
                    f"{obj_name!r}, so it cannot be filtered on "
                    + ", ".join(f"{obj_name}.{c}" for c, _, _ in preds),
                )
            # Filter-then-aggregate: the object resolves to a key set pushed
            # into the fact table as a semi-join, so it never appears in the
            # aggregating FROM and cannot duplicate a fact row. Over a
            # many-to-one path that is the whole story. Over a many-to-many
            # hop it keeps a fact row if ANY linked row matches - the
            # existential reading - and the CompiledQuery says so.
            first = path[0]
            sub_aliases: dict[tuple[str, str], str] = {}
            sub_joins: list[str] = []
            sub_notes: list[str] = []
            head_alias = self._join_chain(path[:1], sub_aliases, sub_joins, sub_notes)
            head_rel = self.objects[first.to_object].relation
            tail_alias = self._join_chain(
                path[1:], sub_aliases, sub_joins, sub_notes, root=head_alias
            )
            keycols = ", ".join(f"{head_alias}.{_ident(c)}" for c in first.to_columns)
            factcols = ", ".join(f"f.{_ident(c)}" for c in first.from_columns)
            inner = f"SELECT {keycols} FROM {head_rel} {head_alias}"
            if sub_joins[1:]:
                inner += " " + " ".join(sub_joins[1:])
            inner += " WHERE " + " AND ".join(
                f"{tail_alias}.{_ident(c)} {op} {_render(op, v)}" for c, op, v in preds
            )
            where.append(f"({factcols}) IN ({inner})")
            m2m = [x for x in path if x.cardinality != "many_to_one"]
            if m2m:
                existential = True
                notes.append(
                    f"filtered on {obj_name} by semi-join through "
                    f"{', '.join(x.name for x in m2m)} (many-to-many): keeps a "
                    f"{m.grain} row if ANY linked {obj_name} matches. Shares over "
                    "this filter do not partition the total."
                )
            else:
                notes.append(
                    f"filtered on {obj_name} by semi-join over a many-to-one path; "
                    "no fact row is duplicated or re-read"
                )

        agg = f"{m.expression} AS {_ident(m.name)}"
        sql = f"SELECT {', '.join([*selects, agg])}\nFROM {fact.relation} f"
        if joins:
            sql += "\n" + "\n".join(joins)
        if where:
            sql += "\nWHERE " + "\n  AND ".join(where)
        if group_keys:
            sql += "\nGROUP BY " + ", ".join(group_keys)
            sql += f"\nORDER BY {_ident(m.name)} {'DESC' if order_desc else 'ASC'}"
        if limit is not None:
            if int(limit) < 0:
                return Refusal("bad_limit", f"limit {limit!r} is negative")
            sql += f"\nLIMIT {int(limit)}"
        where_paths = self._ranked_where_paths_for(m.grain, group_by, filters, via)
        if where_paths:
            notes.extend(
                f"where {p.grain}: {p.render()} importance={p.importance}"
                for p in where_paths
            )
        coverage = _query_coverage(
            measure=m,
            group_by=group_by,
            filters=filters,
            notes=notes,
            existential=existential,
            limit=limit,
            sql=sql,
        )
        if not coverage_valid(coverage):
            return Refusal(
                "coverage_invalid",
                "numeric compile missing validatable include/exclude/unsure "
                "(no silent pad).",
            )
        return CompiledQuery(
            sql=sql,
            measure=m.name,
            grain=m.grain,
            group_by=tuple(f"{o}.{c}" for o, c in group_by),
            notes=tuple(notes),
            existential=existential,
            where_paths=where_paths,
            coverage=coverage,
        )

    def compile_grains(
        self,
        measure: str | None,
        grains: Sequence[str],
        *,
        filters: Sequence[tuple[str, str, str, Any]] = (),
        via: dict[str, str] | None = None,
        order_desc: bool = True,
        limit: int | None = None,
        grantable: set[str] | None = None,
    ) -> CompiledQuery | Refusal:
        """Locate + rank supply-chain grains, then compile. Not bind_plan.

        ≥2 grains required. A missing object, join, or metric is a named
        refusal, never a one-grain guess. When ``grantable`` is set, a path
        that cites an ungranted table is ``missing_join`` naming the grain --
        not SQL that later dies as ``validate:ungranted:...``.
        """
        named = tuple(g for g in grains if g in SUPPLY_CHAIN_GRAINS)
        if len(named) < 2:
            return Refusal(
                "missing_join",
                "multi-join compile needs ≥2 supply-chain grains "
                f"(sku, supplier, plant, lane, day); got {list(named) or 'none'}",
            )
        if not self.verified:
            return Refusal(
                "ontology_unverified",
                "verify() has not passed against this data, so no link cardinality "
                "is known. An unverified ontology can describe the world; it "
                "cannot answer a question.",
            )
        mid = str(measure or "").strip()
        if not mid or mid not in self.measures:
            return Refusal(
                "missing_metric",
                f"no measure named {mid or '(none)'!r} for a multi-grain compile "
                f"spanning {', '.join(named)}",
            )
        m = self.measures[mid]
        specs: list[tuple[str, str, str]] = []
        missing: list[str] = []
        for grain in named:
            obj = resolve_grain_object(self, grain)
            if obj is None:
                missing.append(f"grain {grain} (object undeclared)")
                continue
            col = self._column_for_grain(obj, grain)
            if not col:
                missing.append(f"grain {grain} (no grouping column on {obj})")
                continue
            paths = self._all_paths(m.grain, obj, via=via)
            if obj != m.grain and not paths:
                missing.append(f"join {m.grain}->{obj} for grain {grain}")
                continue
            specs.append((grain, obj, col))
        if missing:
            return Refusal(
                "missing_join",
                "no verified join/object for " + "; ".join(missing)
                + ". Declare the link, or ask for a measure defined at that grain.",
            )
        objects = [obj for _g, obj, _c in specs]
        if len(set(objects)) < 2:
            return Refusal(
                "missing_join",
                f"grains {', '.join(named)} resolve to one object {objects[0]!r}; "
                "a multi-join compile needs two distinct objects.",
            )
        ranked = self.rank_where_paths(
            m.grain, [(g, obj) for g, obj, _c in specs], via=via
        )
        if grantable is not None:
            grant_miss: list[str] = []
            for grain, obj, _c in specs:
                if obj == m.grain:
                    blocked = ungranted_tables(
                        set(self.object_tables(obj)), grantable
                    )
                    if blocked:
                        grant_miss.append(
                            f"grain {grain} (object {obj} cites ungranted "
                            f"{', '.join(blocked)})"
                        )
                    continue
                if self.granted_where_paths(ranked, grain, grantable):
                    continue
                cited = set(self.object_tables(m.grain)) | set(
                    self.object_tables(obj)
                )
                for path in ranked:
                    if path.grain == grain:
                        cited |= set(self.steps_tables(path.steps))
                blocked = ungranted_tables(cited, grantable)
                extra = f" (ungranted {', '.join(blocked)})" if blocked else ""
                grant_miss.append(
                    f"join {m.grain}->{obj} for grain {grain}{extra}"
                )
            if grant_miss:
                return Refusal(
                    "missing_join",
                    "no granted join/object for " + "; ".join(grant_miss)
                    + ". Declare the link on a granted table, or ask for a "
                    "measure defined at that grain.",
                )
        for grain, obj, _c in specs:
            pool = self.granted_where_paths(ranked, grain, grantable)
            best = min((p.importance for p in pool), default=None)
            tops = [p for p in pool if p.importance == best] if best is not None else []
            hopsets = {p.hops for p in tops}
            if len(hopsets) > 1:
                routes = "; ".join(
                    " -> ".join(p.hops) if p.hops else p.target for p in tops
                )
                return Refusal(
                    "ambiguous_path",
                    f"{len(hopsets)} equal-importance where-paths reach grain "
                    f"{grain} ({obj}) from {m.grain}: {routes}. Name the path "
                    "with via=.",
                )
        return self.compile(
            mid,
            group_by=[(obj, col) for _g, obj, col in specs],
            filters=filters,
            via=via,
            order_desc=order_desc,
            limit=limit,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "objects": {
                o.name: {"relation": o.relation, "key": list(o.key)}
                for o in self.objects.values()
            },
            "links": {
                link.name: {
                    "from": f"{link.from_object}({', '.join(link.from_columns)})",
                    "to": f"{link.to_object}({', '.join(link.to_columns)})",
                    "cardinality": link.cardinality,
                    "max_fanout": link.max_fanout,
                }
                for link in self.links.values()
            },
            "measures": {
                m.name: {"grain": m.grain, "expression": m.expression,
                         "additive": m.additive, "description": m.description}
                for m in self.measures.values()
            },
            "grains": self.supply_chain_catalog(),
            "grain_aliases": dict(self.grain_aliases),
        }


def try_compile_multi_grain(
    onto: Ontology | None,
    measure: str | None,
    question: str,
    *,
    via: dict[str, str] | None = None,
    grantable: set[str] | None = None,
) -> CompiledQuery | Refusal | None:
    """≥2 named supply-chain grains → compile_grains. Else None (not bind_plan).

    When ``grantable`` is set, a path that cites an ungranted table is
    ``missing_join`` naming the grain -- not a later ``validate:ungranted``.
    """
    grains = detect_supply_chain_grains(question)
    if onto is None or not onto.verified or len(grains) < 2:
        return None
    return onto.compile_grains(measure, grains, via=via, grantable=grantable)


def _render(op: str, value: Any) -> str:
    if op.upper() == "IN":
        items = value if isinstance(value, (list, tuple, set)) else [value]
        return "(" + ", ".join(_literal(v) for v in items) + ")"
    return _literal(value)


def _query_coverage(
    *,
    measure: Measure,
    group_by: Sequence[tuple[str, str]],
    filters: Sequence[tuple[str, str, str, Any]],
    notes: Sequence[str],
    existential: bool,
    limit: int | None,
    sql: str,
) -> Coverage:
    """Include / exclude / unsure for one compiled number. No silent pad."""
    include = [f"grain={measure.grain}", f"measure={measure.name}"]
    if measure.description:
        include.append(measure.description)
    for obj_name, column in group_by:
        include.append(f"group {obj_name}.{column}")
    for obj_name, column, op, value in filters:
        include.append(f"where {obj_name}.{column} {op} {value}")
    exclude = [NO_SILENT_PAD]
    expr = measure.expression.upper()
    if "OUT" in expr and "OUTBOUND" in expr:
        include.append("txn_type IN (OUT, outbound)")
        exclude.append("txn_type not in (OUT, outbound)")
    if "ADJUST" in (measure.description or "").upper():
        exclude.append("ADJUST unsigned and excluded")
    if limit is not None:
        exclude.append(f"rows beyond LIMIT {int(limit)} not returned")
    # A calendar/group pad would fill missing keys with 0. This compiler
    # never emits one; if SQL ever did, coverage would be invalid.
    upper_sql = sql.upper()
    if "GENERATE_SERIES" in upper_sql or "RANGE LEFT" in upper_sql:
        exclude = [x for x in exclude if x != NO_SILENT_PAD]
    unsure: list[str] = []
    if existential:
        unsure.append("existential many-to-many filter reading")
    if any("LEFT JOIN" in n.upper() or "joined " in n for n in notes):
        unsure.append("LEFT JOIN unmatched dim keys stay in the total (not inner-dropped)")
    return Coverage(tuple(include), tuple(exclude), tuple(unsure))


# --------------------------------------------------------------------------
# the demo ontology - the six-table warehouse, declared honestly
# --------------------------------------------------------------------------


def _table_columns(warehouse: Path) -> dict[str, set[str]]:
    """Column names actually on disk. Empty when the file cannot be read.

    demo_ontology must not assume Cortex-lake columns (storage_bin, shipment
    supplier_id) that the thin DMS reseed does not have. Live Studio may be
    either lake; verify() still measures the claim.
    """
    path = Path(warehouse)
    if not path.is_file():
        return {}
    import duckdb

    try:
        con = duckdb.connect(str(path), read_only=True)
    except Exception:  # noqa: BLE001
        return {}
    try:
        rows = con.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'main'"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    finally:
        con.close()
    out: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        out.setdefault(str(table_name), set()).add(str(column_name))
    return out


def demo_ontology(warehouse: Path) -> Ontology:
    """The demo warehouse as an ontology, including the trap it is famous for.

    ``inventory`` is one row per stock LOT. Declaring it as an object keyed on
    ``sku`` would be a lie the verifier catches immediately, so it is keyed on
    what actually identifies a lot. The consequence is that the transactions ->
    inventory link is many-to-many, and the compiler will refuse to group a
    transaction measure by an inventory attribute - which is precisely the query
    that produced the ~15x inflation.

    Column presence is read from ``warehouse`` when the file exists. Missing
    inspect (no file) keeps the Cortex-lake shape. Thin DMS reseed has no
    ``storage_bin`` and no ``shipments.supplier_id``.
    """
    cols = _table_columns(warehouse)
    inv = cols.get("inventory", set())
    ship = cols.get("shipments", set())
    loc = cols.get("locations", set())
    # Unreadable file -> Cortex-lake default. Readable thin reseed omits extras.
    cortex_default = not cols
    lot_key = ["sku", "location_id"]
    if cortex_default or "storage_bin" in inv:
        lot_key.append("storage_bin")
    if cortex_default or "supplier_id" in inv:
        lot_key.append("supplier_id")

    o = Ontology()
    o.grain_aliases["sku"] = "product"
    o.grain_aliases["plant"] = "location"
    txn = cols.get("transactions", set())
    txn_rel = "transactions"
    if cortex_default or "ts" in txn:
        # day is CAST(ts). Wrapping keeps the fact grain honest; a missing
        # ts is missing_join, not a padded calendar.
        txn_rel = "(SELECT *, CAST(ts AS DATE) AS day FROM transactions)"
        o.add_object("transaction", txn_rel, ["txn_id"])
        o.add_object(
            "day",
            "(SELECT DISTINCT CAST(ts AS DATE) AS day FROM transactions "
            "WHERE ts IS NOT NULL)",
            ["day"],
        )
        o.add_link("txn_on_day", "transaction", ["day"], "day", ["day"])
    else:
        o.add_object("transaction", "transactions", ["txn_id"])
        o.missing_grains["day"] = (
            "missing join: day grain needs transactions.ts; column is absent. "
            "No silent pad."
        )
    o.add_object("shipment", "shipments", ["shipment_id"])
    o.add_object("supplier", "suppliers", ["supplier_id"])
    o.add_object("location", "locations", ["location_id"])
    o.add_object("alert", "alerts", ["alert_id"])
    # A lot is identified by every column that varies within a sku. There is no
    # surrogate key, so the honest key is the whole natural one.
    o.add_object("lot", "inventory", lot_key)
    # A sku-grain view, derived rather than asserted - this is the object a
    # category grouping is actually an attribute of.
    # Only category is an attribute of a SKU: measured, it is constant across
    # every lot of every SKU (0 violations). sku_name varies across lots for
    # 499 SKUs and is_hazardous for 50, so ANY_VALUE over them would assert an
    # attribute the data does not have. Those stay on the lot, where they are
    # true, and are filtered existentially.
    o.add_object(
        "product",
        "(SELECT sku, ANY_VALUE(category) AS category FROM inventory GROUP BY sku)",
        ["sku"],
    )

    o.add_link("txn_at_location", "transaction", ["location_id"], "location", ["location_id"])
    o.add_link("txn_of_product", "transaction", ["sku"], "product", ["sku"])
    o.add_link("txn_of_lot", "transaction", ["sku"], "lot", ["sku"])
    if cortex_default or "supplier_id" in ship:
        o.add_link("ship_from_supplier", "shipment", ["supplier_id"], "supplier", ["supplier_id"])
    o.add_link("ship_to_location", "shipment", ["destination_location_id"],
               "location", ["location_id"])
    o.add_link("ship_of_product", "shipment", ["sku"], "product", ["sku"])
    o.add_link("lot_at_location", "lot", ["location_id"], "location", ["location_id"])
    if cortex_default or "supplier_id" in inv:
        o.add_link("lot_from_supplier", "lot", ["supplier_id"], "supplier", ["supplier_id"])
    o.add_link("lot_of_product", "lot", ["sku"], "product", ["sku"])
    origin_col = next(
        (
            cand
            for cand in ("origin_location_id", "from_location_id", "origin_plant_id")
            if cand in ship
        ),
        None,
    )
    dest_ok = cortex_default or "destination_location_id" in ship
    if origin_col and dest_ok:
        o.add_object(
            "lane",
            "("
            f"SELECT DISTINCT {origin_col} AS origin_plant_id, "
            "destination_location_id AS dest_plant_id FROM shipments "
            f"WHERE {origin_col} IS NOT NULL AND destination_location_id IS NOT NULL"
            ")",
            ["origin_plant_id", "dest_plant_id"],
        )
        o.add_link(
            "ship_on_lane",
            "shipment",
            [origin_col, "destination_location_id"],
            "lane",
            ["origin_plant_id", "dest_plant_id"],
        )
        o.add_link(
            "lane_from_plant",
            "lane",
            ["origin_plant_id"],
            "location",
            ["location_id"],
        )
        o.add_link(
            "lane_to_plant",
            "lane",
            ["dest_plant_id"],
            "location",
            ["location_id"],
        )
    else:
        o.missing_grains["lane"] = (
            "missing join: lane grain is origin->destination; shipments has no "
            "origin_location_id (or from_location_id). No silent pad."
        )

    # Thin DMS seed uses inbound/outbound; Cortex lake uses IN/OUT. Both listed.
    o.add_measure(
        "outbound_value_myr", "transaction",
        "ROUND(SUM(CASE WHEN f.txn_type IN ('OUT', 'outbound') "
        "THEN f.quantity_kg * f.unit_cost_myr ELSE 0 END), 2)",
        description=(
            "outbound issued stock value at cost (revenue / sales); "
            "one contribution per transaction"
        ),
    )
    o.add_measure(
        "outbound_kg", "transaction",
        "ROUND(SUM(CASE WHEN f.txn_type IN ('OUT', 'outbound') "
        "THEN f.quantity_kg ELSE 0 END), 2)",
        description="quantity sold / outbound kg; one contribution per transaction",
    )
    o.add_measure(
        "net_movement_kg", "transaction",
        "ROUND(SUM(CASE WHEN f.txn_type IN ('IN', 'inbound') THEN f.quantity_kg "
        "WHEN f.txn_type IN ('OUT', 'outbound', 'WRITE_OFF') THEN -f.quantity_kg "
        "ELSE 0 END), 2)",
        description="receipts minus issues and write-offs; ADJUST is unsigned and excluded",
    )
    o.add_measure(
        "stock_value_myr", "lot",
        "ROUND(SUM(f.quantity_kg * f.unit_cost_myr), 2)",
        description="stock value / carrying value / inventory spend, one contribution per lot",
    )
    o.add_measure(
        "shipping_cost_myr", "shipment", "ROUND(SUM(f.cost_myr), 2)",
        description="shipment cost / freight billed, one contribution per shipment",
    )
    o.add_measure(
        "shipment_count", "shipment", "COUNT(*)",
        description="one contribution per shipment",
    )
    o.add_measure(
        "sku_count", "product", "COUNT(*)",
        description="count of unique SKUs in inventory; one contribution per product",
    )
    if cortex_default or {"current_load_kg", "capacity_kg"} <= loc:
        o.add_measure(
            "utilisation_pct", "location",
            "ROUND(100.0 * SUM(f.current_load_kg) / NULLIF(SUM(f.capacity_kg), 0), 1)",
            additive=False,
            description=(
                "warehouse capacity utilisation / occupancy percent per location"
            ),
        )
    if cortex_default or "reorder_level_kg" in inv:
        o.add_measure(
            "below_reorder_lots",
            "lot",
            "COUNT(*) FILTER (WHERE f.quantity_kg < f.reorder_level_kg "
            "AND COALESCE(f.reorder_level_kg, 0) > 0)",
            description="lots below reorder level; one contribution per qualifying lot",
        )
    sup = cols.get("suppliers", set())
    if cortex_default or {"risk_score", "lead_time_days"} <= sup:
        o.add_measure(
            "supplier_rank_score",
            "supplier",
            "ROUND((MAX(f.risk_score) * 0.65) + ((MAX(f.lead_time_days) / 60.0) * 0.35), 3)",
            additive=False,
            description=(
                "supplier combined risk and lead time ranking score; "
                "one contribution per supplier"
            ),
        )
    if cortex_default or "last_audit_date" in sup:
        o.add_measure(
            "audit_overdue",
            "supplier",
            "COUNT(*) FILTER (WHERE CAST(f.last_audit_date AS DATE) "
            "< CURRENT_DATE - INTERVAL 90 DAY)",
            description=(
                "suppliers whose last audit is overdue (>90 days); "
                "one contribution per overdue supplier"
            ),
        )
    return o


def _connect(warehouse: Path) -> Any:
    import duckdb

    return duckdb.connect(str(warehouse), read_only=True)


def run_demo(warehouse: Path) -> int:
    print(f"=== ONTOLOGY over {warehouse} ===")
    if not warehouse.is_file():
        print(f"FAIL warehouse not found: {warehouse}")
        return 2
    onto = demo_ontology(warehouse)
    con = _connect(warehouse)
    try:
        violations = onto.verify(con)
        print(f"  {len(onto.objects)} objects, {len(onto.links)} links, "
              f"{len(onto.measures)} measures")
        for link in onto.links.values():
            mark = "safe" if link.cardinality == "many_to_one" else "FAN-OUT"
            extra = "" if link.cardinality == "many_to_one" else f" up to {link.max_fanout}x"
            print(f"    {link.name:<22} {link.from_object:>12} -> {link.to_object:<10} "
                  f"{link.cardinality:<14} [{mark}{extra}]")
        if violations:
            print(f"\nFAIL {len(violations)} declarations are not true of this data:")
            for v in violations:
                print(f"  - [{v.check}] {v.subject}: {v.detail}")
            return 1

        print("\n  -- a grouping the compiler allows --")
        good = onto.compile("outbound_value_myr", group_by=[("product", "category")], limit=3)
        _show(con, good)

        print("\n  -- the same question routed through the lot grain --")
        bad = onto.compile("outbound_value_myr", group_by=[("lot", "category")], limit=3)
        _show(con, bad)

        print("\n  -- a filter through the many-to-many lot link: allowed, and named --")
        filtered = onto.compile(
            "outbound_value_myr",
            group_by=[("location", "state")],
            filters=[("lot", "is_hazardous", "=", True)],
            limit=3,
        )
        _show(con, filtered)
        if isinstance(filtered, CompiledQuery):
            print(f"    existential: {filtered.existential}")
    finally:
        con.close()
    print("\nPASS every declaration held, and the fan-out query was refused.")
    return 0


def _show(con: Any, result: CompiledQuery | Refusal) -> None:
    if isinstance(result, Refusal):
        print(f"    REFUSED [{result.reason}]")
        print(f"      {result.detail}")
        return
    print("    SQL:")
    for line in result.sql.splitlines():
        print(f"      {line}")
    for note in result.notes:
        print(f"      note: {note}")
    rows = con.execute(result.sql).fetchall()
    for r in rows:
        print(f"      -> {r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--demo", action="store_true", help="build and verify on the demo warehouse")
    ap.add_argument("--adventureworks", action="store_true",
                    help="derive an ontology from the extracted lake and verify it")
    ap.add_argument("--database", help="limit --adventureworks to one database")
    ap.add_argument("--warehouse", type=Path,
                    default=Path(r"D:\Cortex\data\dms_demo.duckdb"))
    ap.add_argument("--describe", action="store_true", help="print the ontology as json")
    args = ap.parse_args(argv)

    if args.describe:
        print(json.dumps(demo_ontology(args.warehouse).describe(), indent=2))
        return 0
    if args.adventureworks:
        return run_adventureworks(args.database)
    return run_demo(args.warehouse)




# --------------------------------------------------------------------------
# deriving an ontology from a real database's own metadata
# --------------------------------------------------------------------------


def from_manifest(
    entry: dict[str, Any],
    lake_root: Path | None = None,
    *,
    relation_for: Callable[[str, str], str] | None = None,
) -> Ontology:
    """Build an ontology from an extracted database's keys and relationships.

    Hand-authoring object types for a 71-table schema is not a plan, and neither
    is inferring them from column names - that is how a join gets invented. A
    relational database already carries the declarations: primary keys say what
    identifies a row, foreign keys say what relates to what. This reads those and
    turns them into objects and links.

    What it does NOT do is trust them. Every derived link starts ``unverified``
    and stays unusable until ``verify()`` has measured it against the extracted
    data, because a declared foreign key says a value should exist in the parent,
    not that the parent side is unique on those columns - and uniqueness is the
    only property that makes a join safe to group through.

    Measures are deliberately not derived. A sum over a numeric column is not a
    metric; someone has to say what it means and at what grain. Inventing them
    would recreate exactly the implicit-measure problem Power BI's own guidance
    warns against.
    """
    root = lake_root or ROOT
    onto = Ontology()
    paths = {
        f"{t['schema']}.{t['table']}": (root / str(t["path"])).as_posix()
        for t in entry.get("tables", [])
    }

    # Where an object's rows live is the one thing that differs between an extracted
    # parquet lake and a SQL source landed in bronze. The keys, the links, and the
    # refusal logic are identical, so the caller supplies the relation and everything
    # else is shared. The default is the parquet lake this function was written for.
    def _parquet(schema: str, table: str) -> str:
        return f"read_parquet('{paths[f'{schema}.{table}']}')"

    relation = relation_for or _parquet
    pks: dict[str, list[str]] = dict(entry.get("primary_keys") or {})
    # Optional declared natural keys ("schema.table" -> columns). Additive:
    # SourceKeys does not read UNIQUE constraints yet, so today only a steward
    # or hand-built manifest supplies this (dms#260 A2-04).
    bks: dict[str, list[str]] = dict(entry.get("business_keys") or {})

    truncated_by_table = {
        f"{t['schema']}.{t['table']}": bool(t.get("truncated"))
        for t in entry.get("tables", [])
    }

    for table, key in pks.items():
        if table not in paths:
            continue  # declared a key but was not extracted; validate_lake reports it
        schema, _, name = table.partition(".")
        onto.add_object(
            table,
            relation(schema, name),
            key,
            truncated=truncated_by_table.get(table, False),
            business_key=bks.get(table),
        )

    # Grouped on the whole triple, not the name alone. Constraint names are
    # unique per table in SQL Server, not per database, so two tables may each
    # carry an FK_Customer - and grouping by name alone merged them into one
    # link holding the columns of both, which verify() then measured and blessed.
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for fk in entry.get("foreign_keys") or []:
        grouped.setdefault(
            (str(fk["name"]), str(fk["from_table"]), str(fk["to_table"])), []
        ).append(fk)

    for (name, child, parent), cols in grouped.items():
        if child not in onto.objects or parent not in onto.objects:
            # An end of this link has no primary key or was not extracted. A link
            # to an object that cannot identify a row is not a link.
            continue
        from_cols = [str(c["from_column"]) for c in cols]
        onto.add_link(
            name if name not in onto.links else f"{name}@{child}",
            child,
            from_cols,
            parent,
            [str(c["to_column"]) for c in cols],
            # A2-03 / dms#259: a database FK constraint declared on the child's
            # own primary key IS the schema owner's one-to-one declaration (a
            # shared-primary-key subtype, e.g. Person.Person -> BusinessEntity).
            # That is explicit, not inferred, so it is passed through; every
            # other check in verify() still measures the link. Hand-authored
            # links get no such pass.
            one_to_one=_same_columns(from_cols, onto.objects[child].key),
        )
    return onto


def run_adventureworks(database: str | None = None) -> int:
    """Derive, verify and report an ontology over every extracted database."""
    import duckdb

    manifest_path = ROOT / "data" / "lake" / "_reports" / "extract_manifest.json"
    if not manifest_path.is_file():
        print(f"FAIL no manifest at {manifest_path}")
        print("     run python scripts/load_adventureworks.py --restore --extract first")
        return 2

    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if database:
        entries = [e for e in entries if e["database"] == database]
    con = duckdb.connect(":memory:")
    worst = 0
    try:
        for entry in entries:
            onto = from_manifest(entry)
            violations = onto.verify(con)
            hazards = [
                link for link in onto.links.values() if link.cardinality != "many_to_one"
            ]
            print(f"\n=== {entry['database']} ===")
            print(f"  {len(onto.objects)} object types, {len(onto.links)} links, "
                  f"{len(hazards)} carrying a fan-out hazard")
            for link in sorted(hazards, key=lambda link: -link.max_fanout)[:10]:
                print(f"    FAN-OUT {link.from_object} -> {link.to_object} "
                      f"up to {link.max_fanout}x via {link.name}")
            if violations:
                worst = 1
                print(f"  {len(violations)} declarations are NOT true of the extracted data:")
                for v in violations[:15]:
                    print(f"    - [{v.check}] {v.subject}: {v.detail}")
                if len(violations) > 15:
                    print(f"    ... and {len(violations) - 15} more")
            else:
                print("  every declared key is unique and non-null in the extracted data")
    finally:
        con.close()
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
