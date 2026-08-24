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
child-side readability and parent-side uniqueness of every link, and
``compile()`` refuses to use a link whose cardinality has not been measured.
It does not read the measure expression. An unverified ontology can describe
the world; it cannot answer a question.

Refusal is a first-class result, for the same reason abstention is a first-class
envelope state: a question this layer cannot answer correctly must come back as
a refusal carrying its reason, never as a number with a caveat attached.

  python scripts/ontology.py --demo          # build and verify on the demo warehouse
  python scripts/ontology.py --adventureworks
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]

Cardinality = Literal["many_to_one", "many_to_many", "unverified"]


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

    def __bool__(self) -> bool:
        return True


@dataclass
class Ontology:
    objects: dict[str, ObjectType] = field(default_factory=dict)
    links: dict[str, LinkType] = field(default_factory=dict)
    measures: dict[str, Measure] = field(default_factory=dict)
    verified: bool = False

    # -- authoring -------------------------------------------------------

    def add_object(self, name: str, relation: str, key: Sequence[str]) -> None:
        self.objects[name] = ObjectType(name, relation, tuple(key))
        self.verified = False

    def add_link(
        self,
        name: str,
        from_object: str,
        from_columns: Sequence[str],
        to_object: str,
        to_columns: Sequence[str],
    ) -> None:
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
        self.links[name] = LinkType(
            name, from_object, tuple(from_columns), to_object, tuple(to_columns)
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

    def verify(self, con: Any) -> list[Violation]:
        """Execute every claim. Nothing may be used until this has passed.

        Three claims are checked, in the order a wrong one would do damage:
          key_unique      an object's key really identifies one row
          key_not_null    no key column is NULL - a NULL key both passes a
                          uniqueness check and silently drops rows from a join
          link_cardinality  measured, then stored on the link. A link whose
                          parent side is not unique is not broken - it is a
                          relationship a measure must aggregate across rather
                          than join through, and the compiler needs to know.
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
            if int(pn) == int(pdistinct):
                self.links[name] = LinkType(
                    link.name, link.from_object, link.from_columns,
                    link.to_object, link.to_columns, "many_to_one", 1,
                )
                continue
            worst = con.execute(
                f"SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM {parent.relation} "
                f"WHERE {not_null} GROUP BY {cols})"
            ).fetchone()[0]
            self.links[name] = LinkType(
                link.name, link.from_object, link.from_columns,
                link.to_object, link.to_columns, "many_to_many", int(worst or 0),
            )

        self.verified = not violations
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
        return CompiledQuery(
            sql=sql,
            measure=m.name,
            grain=m.grain,
            group_by=tuple(f"{o}.{c}" for o, c in group_by),
            notes=tuple(notes),
            existential=existential,
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
        }


def _render(op: str, value: Any) -> str:
    if op.upper() == "IN":
        items = value if isinstance(value, (list, tuple, set)) else [value]
        return "(" + ", ".join(_literal(v) for v in items) + ")"
    return _literal(value)


# --------------------------------------------------------------------------
# the demo ontology - the six-table warehouse, declared honestly
# --------------------------------------------------------------------------


def demo_ontology(warehouse: Path) -> Ontology:
    """The demo warehouse as an ontology, including the trap it is famous for.

    ``inventory`` is one row per stock LOT. Declaring it as an object keyed on
    ``sku`` would be a lie the verifier catches immediately, so it is keyed on
    what actually identifies a lot. The consequence is that the transactions ->
    inventory link is many-to-many, and the compiler will refuse to group a
    transaction measure by an inventory attribute - which is precisely the query
    that produced the ~15x inflation.
    """
    o = Ontology()
    o.add_object("transaction", "transactions", ["txn_id"])
    o.add_object("shipment", "shipments", ["shipment_id"])
    o.add_object("supplier", "suppliers", ["supplier_id"])
    o.add_object("location", "locations", ["location_id"])
    o.add_object("alert", "alerts", ["alert_id"])
    # A lot is identified by every column that varies within a sku. There is no
    # surrogate key, so the honest key is the whole natural one.
    o.add_object("lot", "inventory", ["sku", "location_id", "storage_bin", "supplier_id"])
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
    o.add_link("ship_from_supplier", "shipment", ["supplier_id"], "supplier", ["supplier_id"])
    o.add_link("ship_to_location", "shipment", ["destination_location_id"],
               "location", ["location_id"])
    o.add_link("ship_of_product", "shipment", ["sku"], "product", ["sku"])
    o.add_link("lot_at_location", "lot", ["location_id"], "location", ["location_id"])
    o.add_link("lot_from_supplier", "lot", ["supplier_id"], "supplier", ["supplier_id"])

    o.add_measure(
        "outbound_value_myr", "transaction",
        "ROUND(SUM(CASE WHEN f.txn_type = 'OUT' THEN f.quantity_kg * f.unit_cost_myr "
        "ELSE 0 END), 2)",
        description="value at cost of stock issued, one contribution per transaction",
    )
    o.add_measure(
        "net_movement_kg", "transaction",
        "ROUND(SUM(CASE WHEN f.txn_type = 'IN' THEN f.quantity_kg "
        "WHEN f.txn_type IN ('OUT', 'WRITE_OFF') THEN -f.quantity_kg ELSE 0 END), 2)",
        description="receipts minus issues and write-offs; ADJUST is unsigned and excluded",
    )
    o.add_measure(
        "stock_value_myr", "lot",
        "ROUND(SUM(f.quantity_kg * f.unit_cost_myr), 2)",
        description="carrying value, one contribution per stock lot",
    )
    o.add_measure(
        "shipping_cost_myr", "shipment", "ROUND(SUM(f.cost_myr), 2)",
        description="freight billed, one contribution per shipment",
    )
    o.add_measure(
        "shipment_count", "shipment", "COUNT(*)",
        description="one contribution per shipment",
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


def from_manifest(entry: dict[str, Any], lake_root: Path | None = None) -> Ontology:
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
    pks: dict[str, list[str]] = dict(entry.get("primary_keys") or {})

    for table, key in pks.items():
        path = paths.get(table)
        if path is None:
            continue  # declared a key but was not extracted; validate_lake reports it
        onto.add_object(table, f"read_parquet('{path}')", key)

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
        onto.add_link(
            name if name not in onto.links else f"{name}@{child}",
            child,
            [str(c["from_column"]) for c in cols],
            parent,
            [str(c["to_column"]) for c in cols],
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
