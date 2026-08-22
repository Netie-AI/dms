"""A semantic layer whose measures cannot fan out, because the SQL cannot express it.

Why this exists
---------------
Every accuracy control in this repo so far is a *detector*: the conservation
identity, E9, the hard-rule-12 demote, the free-form oracle. Each one notices a
wrong number after something produced it. Detectors are necessary and they do
not compose - there is always one more shape nobody wrote a detector for, and
the ~15x fan-out inflation on the demo warehouse was exactly that.

All four vendors researched for DR-0003 solve this the same way, and not one of
them uses a model to do it. The measure is anchored to a declared grain, and the
aggregation is not permitted to see a table finer than that grain:

  Databricks   a metric view has one source that IS the grain, and the object
               cannot be joined to directly - it must materialise as a CTE first
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
  * dimension filters become semi-joins (IN over a key set), which cannot
    duplicate a fact row no matter what the dimension's cardinality is;
  * a grouping attribute is only attached through a link whose parent side has
    been MEASURED unique - anything else is refused, not guessed.

Where this goes further than the vendors
----------------------------------------
Databricks lets you declare ``rely.at_most_one_match`` and their documentation
says plainly it is "not validated at runtime. If the join produces a fan-out,
measures return incorrect results." A declaration nobody checks is a comment.
Here ``verify()`` executes every claim against the data, and ``compile()``
refuses to use a link whose cardinality has not been verified. An unverified
ontology can describe the world; it cannot answer a question.

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
        for obj in self.objects.values():
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
            cols = ", ".join(_ident(c) for c in link.to_columns)
            try:
                pn, pdistinct = con.execute(
                    f"SELECT COUNT(*), COUNT(DISTINCT ({cols})) FROM {parent.relation}"
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
                f"GROUP BY {cols})"
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

        for obj_name, column in group_by:
            if obj_name not in self.objects:
                return Refusal("unknown_object", f"cannot group by unknown object {obj_name!r}")
            if obj_name == m.grain:
                # An attribute of the fact itself is free: it is already one
                # value per contributing row.
                expr = f"f.{_ident(column)}"
            else:
                link = self._resolve_link(m.grain, obj_name, (via or {}).get(obj_name))
                if isinstance(link, Refusal):
                    return link
                if link is None:
                    return Refusal(
                        "no_path",
                        f"no declared link from {m.grain!r} to {obj_name!r}, so "
                        f"{obj_name}.{column} is not an attribute of this measure's "
                        "grain. Declare the link, or ask for a measure defined at "
                        "that grain.",
                    )
                if link.cardinality != "many_to_one":
                    return Refusal(
                        "fanout_refused",
                        f"grouping by {obj_name}.{column} requires joining through "
                        f"{link.name!r}, whose parent side is not unique (up to "
                        f"{link.max_fanout}x). That join would make each "
                        f"{m.grain} row contribute up to {link.max_fanout} times "
                        f"and inflate {m.name}. Aggregate across the relationship "
                        "rather than joining through it.",
                    )
                slot = (obj_name, link.name)
                alias = aliases.get(slot)
                if alias is None:
                    alias = f"d{len(aliases)}"
                    aliases[slot] = alias
                    dim = self.objects[obj_name]
                    on = " AND ".join(
                        f"f.{_ident(a)} = {alias}.{_ident(b)}"
                        for a, b in zip(link.from_columns, link.to_columns)
                    )
                    # LEFT JOIN, not INNER: an inner join silently drops fact
                    # rows whose key is absent from the dimension, shrinking the
                    # measure without anything looking wrong.
                    joins.append(f"LEFT JOIN {dim.relation} {alias} ON {on}")
                    notes.append(
                        f"joined {obj_name} through {link.name} (verified "
                        "many-to-one, so no fact row is duplicated)"
                    )
                expr = f"{alias}.{_ident(column)}"
            label = f"{obj_name}_{column}"
            selects.append(f"{expr} AS {_ident(label)}")
            group_keys.append(expr)

        where: list[str] = []
        for obj_name, column, op, value in filters:
            if op.upper() not in {"=", "<>", "<", "<=", ">", ">=", "IN", "LIKE"}:
                return Refusal("bad_operator", f"operator {op!r} is not allowed")
            if obj_name == m.grain:
                where.append(f"f.{_ident(column)} {op} {_render(op, value)}")
                continue
            link = self._resolve_link(m.grain, obj_name, (via or {}).get(obj_name))
            if isinstance(link, Refusal):
                return link
            if link is None:
                return Refusal(
                    "no_path",
                    f"no declared link from {m.grain!r} to {obj_name!r}, so it "
                    f"cannot be filtered on {obj_name}.{column}",
                )
            dim = self.objects[obj_name]
            # Filter-then-aggregate. The dimension resolves to a key set that is
            # pushed into the fact table as a semi-join, so the dimension never
            # appears in the aggregating FROM and its cardinality is irrelevant.
            # This is why a filter is always safe while a grouping is not.
            keycols = ", ".join(_ident(c) for c in link.to_columns)
            factcols = ", ".join(f"f.{_ident(c)}" for c in link.from_columns)
            where.append(
                f"({factcols}) IN (SELECT {keycols} FROM {dim.relation} "
                f"WHERE {_ident(column)} {op} {_render(op, value)})"
            )
            notes.append(
                f"filtered on {obj_name}.{column} by semi-join, which cannot "
                "duplicate a fact row whatever the cardinality"
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
    o.add_object(
        "product",
        "(SELECT sku, ANY_VALUE(sku_name) AS sku_name, ANY_VALUE(category) AS category, "
        "ANY_VALUE(is_hazardous) AS is_hazardous FROM inventory GROUP BY sku)",
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

        print("\n  -- a filter on a many-to-many dimension is still safe --")
        filtered = onto.compile(
            "outbound_value_myr",
            group_by=[("location", "state")],
            filters=[("product", "is_hazardous", "=", True)],
            limit=3,
        )
        _show(con, filtered)
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
