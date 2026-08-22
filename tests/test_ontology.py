"""The ontology must refuse the query that inflates, and must not refuse the one that does not.

R-0007 again, on the layer that is supposed to make fan-out unreachable rather
than merely detectable. Two failure modes, and the second is the one that gets
a control removed:

  * it permits a join that duplicates fact rows, in which case it is decoration
    over the same bug the conservation identity had to catch after the fact;
  * it refuses a query that is perfectly safe, in which case people route around
    it - R-0005, and the reason a control that cries wolf does not survive.

The fixture is built here rather than read from a warehouse: the check must run
on any machine and must never skip (R-0002). It carries the exact shape that
produced the ~15x inflation - a dimension at a finer grain than the fact table -
small enough that the right answer can be worked out by hand and read in the
assertions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ontology import (  # noqa: E402
    CompiledQuery,
    Ontology,
    Refusal,
)


@pytest.fixture()
def con():  # noqa: ANN201
    """Two products, one stocked in three lots and one in a single lot.

    sales holds 100 for SKU-1 and 50 for SKU-2, so the truth is ALPHA=100,
    BETA=50 and the total is 150. Joining sales to lots counts SKU-1 three
    times: ALPHA=300, total 350. Every number below is that arithmetic.
    """
    import duckdb

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE lots (lot_id VARCHAR, sku VARCHAR, category VARCHAR, qty DOUBLE)")
    c.execute(
        "INSERT INTO lots VALUES "
        "('L1','SKU-1','ALPHA',10),('L2','SKU-1','ALPHA',20),"
        "('L3','SKU-1','ALPHA',30),('L4','SKU-2','BETA',40)"
    )
    c.execute("CREATE TABLE sales (txn_id VARCHAR, sku VARCHAR, region VARCHAR, amount DOUBLE)")
    c.execute("INSERT INTO sales VALUES ('T1','SKU-1','North',100),('T2','SKU-2','South',50)")
    c.execute("CREATE TABLE regions (region VARCHAR, country VARCHAR)")
    c.execute("INSERT INTO regions VALUES ('North','MY'),('South','MY')")
    try:
        yield c
    finally:
        c.close()


def _ontology() -> Ontology:
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    o.add_object("lot", "lots", ["lot_id"])
    o.add_object("region", "regions", ["region"])
    o.add_object(
        "product",
        "(SELECT sku, ANY_VALUE(category) AS category FROM lots GROUP BY sku)",
        ["sku"],
    )
    o.add_link("sale_of_lot", "sale", ["sku"], "lot", ["sku"])
    o.add_link("sale_of_product", "sale", ["sku"], "product", ["sku"])
    o.add_link("sale_in_region", "sale", ["region"], "region", ["region"])
    o.add_measure("revenue", "sale", "SUM(f.amount)")
    return o


# --------------------------------------------------------------------------
# verification measures the world instead of trusting the declaration
# --------------------------------------------------------------------------


def test_verify_measures_cardinality_rather_than_believing_it(con) -> None:  # noqa: ANN001
    o = _ontology()
    assert o.links["sale_of_lot"].cardinality == "unverified"
    assert not o.verify(con)
    assert o.links["sale_of_lot"].cardinality == "many_to_many"
    assert o.links["sale_of_lot"].max_fanout == 3, "SKU-1 is stocked in three lots"
    assert o.links["sale_of_product"].cardinality == "many_to_one"
    assert o.links["sale_in_region"].cardinality == "many_to_one"


def test_verify_catches_a_key_that_does_not_identify_a_row(con) -> None:  # noqa: ANN001
    """Declaring lots keyed on sku is the lie the whole layer would rest on."""
    o = Ontology()
    o.add_object("lot", "lots", ["sku"])
    violations = o.verify(con)
    assert [v.check for v in violations] == ["key_unique"]
    assert "does not identify a row" in violations[0].detail
    assert not o.verified


def test_verify_catches_a_null_key(con) -> None:  # noqa: ANN001
    con.execute("INSERT INTO sales VALUES (NULL,'SKU-1','North',5)")
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    checks = {v.check for v in o.verify(con)}
    assert "key_not_null" in checks


def test_a_measure_cannot_be_declared_without_a_grain() -> None:
    """Palantir's rule: reject at authoring time, not at query time."""
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    with pytest.raises(KeyError) as exc:
        o.add_measure("revenue", "no_such_object", "SUM(f.amount)")
    assert "cannot be protected from fan-out" in str(exc.value)


# --------------------------------------------------------------------------
# the refusal, and the arithmetic behind it
# --------------------------------------------------------------------------


def test_grouping_through_a_many_to_many_link_is_refused(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", group_by=[("lot", "category")])
    assert isinstance(got, Refusal)
    assert got.reason == "fanout_refused"
    assert "3x" in got.detail, "the refusal must quantify the inflation it prevented"
    assert not got, "a Refusal must be falsy so `if compiled:` reads correctly"


def test_the_refused_query_really_would_have_inflated(con) -> None:  # noqa: ANN001
    """The refusal is only worth having if the thing it refuses is wrong.

    A control that blocks a query nobody would have got wrong is theatre. This
    executes the join the compiler declined and shows the number it produces.
    """
    truth = con.execute(
        "SELECT ROUND(SUM(amount), 2) FROM sales"
    ).fetchone()[0]
    inflated = con.execute(
        "SELECT ROUND(SUM(s.amount), 2) FROM sales s JOIN lots l ON s.sku = l.sku"
    ).fetchone()[0]
    assert truth == 150.0
    assert inflated == 350.0, "the declined join more than doubles revenue"


def test_the_allowed_grouping_returns_the_truth(con) -> None:  # noqa: ANN001
    """And the permitted route gives the number the refused one would have missed."""
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", group_by=[("product", "category")])
    assert isinstance(got, CompiledQuery)
    rows = dict(con.execute(got.sql).fetchall())
    assert rows == {"ALPHA": 100.0, "BETA": 50.0}
    assert sum(rows.values()) == 150.0, "the grouped total must conserve"


def test_grouping_by_a_fact_attribute_needs_no_join(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", group_by=[("sale", "region")])
    assert isinstance(got, CompiledQuery)
    assert "JOIN" not in got.sql.upper()
    assert dict(con.execute(got.sql).fetchall()) == {"North": 100.0, "South": 50.0}


# --------------------------------------------------------------------------
# filter-then-aggregate: safe through a link that grouping is not
# --------------------------------------------------------------------------


def test_a_filter_through_a_many_to_many_link_is_allowed_and_does_not_inflate(
    con,  # noqa: ANN001
) -> None:
    """Power BI's posture: a dimension predicate becomes a key set, not a join.

    Grouping by lot.category is refused because it must attach an attribute to
    every fact row. Filtering on it is safe, because the dimension only ever
    contributes a set of keys - it never reaches the FROM clause of the
    aggregate, so its cardinality cannot matter.
    """
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", filters=[("lot", "category", "=", "ALPHA")])
    assert isinstance(got, CompiledQuery)
    assert " IN (SELECT " in got.sql, "the filter must compile to a semi-join"
    assert con.execute(got.sql).fetchone()[0] == 100.0, "SKU-1 sold once, not three times"


def test_the_naive_filter_join_would_have_tripled_it(con) -> None:  # noqa: ANN001
    inflated = con.execute(
        "SELECT SUM(s.amount) FROM sales s JOIN lots l ON s.sku = l.sku "
        "WHERE l.category = 'ALPHA'"
    ).fetchone()[0]
    assert inflated == 300.0


# --------------------------------------------------------------------------
# refusals that protect the layer's own promises
# --------------------------------------------------------------------------


def test_nothing_compiles_before_verification(con) -> None:  # noqa: ANN001
    o = _ontology()
    got = o.compile("revenue", group_by=[("product", "category")])
    assert isinstance(got, Refusal)
    assert got.reason == "ontology_unverified"


def test_verification_is_invalidated_by_a_new_declaration(con) -> None:  # noqa: ANN001
    """Adding a link after verifying must not inherit the old verdict."""
    o = _ontology()
    o.verify(con)
    assert o.verified
    o.add_link("sale_of_lot_again", "sale", ["sku"], "lot", ["sku"])
    assert not o.verified
    assert isinstance(o.compile("revenue"), Refusal)


def test_grouping_by_an_object_with_no_declared_link_is_refused(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.add_object("orphan", "regions", ["region"])
    o.verify(con)
    got = o.compile("revenue", group_by=[("orphan", "country")])
    assert isinstance(got, Refusal)
    assert got.reason == "no_path"


def test_an_unknown_measure_is_refused_not_guessed(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    assert isinstance(o.compile("profit"), Refusal)


def test_operators_are_allow_listed(con) -> None:  # noqa: ANN001
    """The model fills slots; it never supplies a fragment of SQL."""
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", filters=[("sale", "region", "; DROP TABLE sales--", "x")])
    assert isinstance(got, Refusal)
    assert got.reason == "bad_operator"
    assert con.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 2


def test_a_string_literal_with_a_quote_is_escaped_not_interpolated(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", filters=[("sale", "region", "=", "North' OR '1'='1")])
    assert isinstance(got, CompiledQuery)
    assert con.execute(got.sql).fetchone()[0] in (None, 0.0), (
        "an injected predicate must match nothing, not everything"
    )


def test_a_dimension_join_is_left_not_inner(con) -> None:  # noqa: ANN001
    """An inner join silently drops fact rows whose key is absent from the dim.

    That shrinks a total with nothing looking wrong, which is the same class of
    defect as inflating it and is far harder to notice.
    """
    con.execute("INSERT INTO sales VALUES ('T3','SKU-9','Nowhere',7)")
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", group_by=[("region", "country")])
    assert isinstance(got, CompiledQuery)
    assert "LEFT JOIN" in got.sql.upper()
    total = sum(v for _, v in con.execute(got.sql).fetchall())
    assert total == 157.0, "the unmatched sale must still contribute to the measure"


# --------------------------------------------------------------------------
# deriving an ontology from a real database's own metadata
# --------------------------------------------------------------------------


@pytest.fixture()
def lake(tmp_path: Path):  # noqa: ANN201
    """A two-table extract manifest with parquet beside it.

    Stands in for AdventureWorks so this path is covered on a machine with no
    SQL Server and no Docker (R-0002 - the check must never skip). It carries the
    same shape that matters: a child table whose foreign key points at a parent
    that is NOT unique on the referenced columns, which a declared FK cannot tell
    you and only measurement can.
    """
    import duckdb

    con = duckdb.connect(":memory:")
    (tmp_path / "db").mkdir()
    con.execute(
        "COPY (SELECT * FROM (VALUES ('O1','C1',10.0),('O2','C1',20.0),('O3','C2',30.0)) "
        "AS t(order_id, cust_ref, amount)) TO '"
        + (tmp_path / "db" / "Sales.Orders.parquet").as_posix()
        + "' (FORMAT PARQUET)"
    )
    # Two rows per cust_ref: a real customer table with one row per (id, version).
    con.execute(
        "COPY (SELECT * FROM (VALUES ('C1',1,'North'),('C1',2,'North'),('C2',1,'South')) "
        "AS t(cust_ref, version, region)) TO '"
        + (tmp_path / "db" / "Sales.Customers.parquet").as_posix()
        + "' (FORMAT PARQUET)"
    )
    con.close()

    manifest = {
        "database": "Fixture",
        "tables": [
            {"schema": "Sales", "table": "Orders", "declared_rows": 3,
             "extracted_rows": 3, "columns": 3, "path": "db/Sales.Orders.parquet"},
            {"schema": "Sales", "table": "Customers", "declared_rows": 3,
             "extracted_rows": 3, "columns": 3, "path": "db/Sales.Customers.parquet"},
        ],
        "skipped": [],
        "primary_keys": {
            "Sales.Orders": ["order_id"],
            "Sales.Customers": ["cust_ref", "version"],
        },
        "foreign_keys": [
            {"name": "FK_Orders_Customers", "from_table": "Sales.Orders",
             "from_column": "cust_ref", "to_table": "Sales.Customers",
             "to_column": "cust_ref"},
        ],
    }
    return manifest, tmp_path


def test_objects_and_links_are_derived_from_the_database_declarations(lake) -> None:  # noqa: ANN001
    from ontology import from_manifest

    manifest, root = lake
    o = from_manifest(manifest, lake_root=root)
    assert set(o.objects) == {"Sales.Orders", "Sales.Customers"}
    assert o.objects["Sales.Customers"].key == ("cust_ref", "version")
    assert set(o.links) == {"FK_Orders_Customers"}
    assert not o.measures, "a sum over a numeric column is not a metric; nobody declared one"


def test_a_derived_link_is_unusable_until_it_has_been_measured(lake) -> None:  # noqa: ANN001
    """A declared foreign key says a value exists in the parent.

    It does not say the parent side is unique on those columns, and uniqueness is
    the only property that makes a join safe to group through. So a derived link
    starts unverified and compile() refuses it.
    """
    import duckdb
    from ontology import from_manifest

    manifest, root = lake
    o = from_manifest(manifest, lake_root=root)
    assert o.links["FK_Orders_Customers"].cardinality == "unverified"
    o.add_measure("revenue", "Sales.Orders", "SUM(f.amount)")
    assert isinstance(o.compile("revenue"), Refusal)

    con = duckdb.connect(":memory:")
    try:
        assert not o.verify(con)
    finally:
        con.close()
    link = o.links["FK_Orders_Customers"]
    assert link.cardinality == "many_to_many", (
        "the FK is declared and valid, and the parent side is still not unique"
    )
    assert link.max_fanout == 2


def test_the_measured_hazard_blocks_the_grouping_a_declared_fk_would_have_allowed(
    lake,  # noqa: ANN001
) -> None:
    """This is the whole point: the schema permits it and the data does not.

    Grouping revenue by Customers.region reads as an ordinary star-schema query.
    Because C1 has two customer rows, the join doubles O1 and O2 - revenue goes
    from 60 to 90 - and nothing about the foreign key would have warned anyone.
    """
    import duckdb
    from ontology import from_manifest

    manifest, root = lake
    o = from_manifest(manifest, lake_root=root)
    o.add_measure("revenue", "Sales.Orders", "SUM(f.amount)")
    con = duckdb.connect(":memory:")
    try:
        o.verify(con)
        got = o.compile("revenue", group_by=[("Sales.Customers", "region")])
        assert isinstance(got, Refusal)
        assert got.reason == "fanout_refused"
        assert "2x" in got.detail

        orders = (root / "db" / "Sales.Orders.parquet").as_posix()
        customers = (root / "db" / "Sales.Customers.parquet").as_posix()
        truth = con.execute(f"SELECT SUM(amount) FROM read_parquet('{orders}')").fetchone()[0]
        inflated = con.execute(
            f"SELECT SUM(o.amount) FROM read_parquet('{orders}') o "
            f"JOIN read_parquet('{customers}') c ON o.cust_ref = c.cust_ref"
        ).fetchone()[0]
        assert truth == 60.0
        assert inflated == 90.0, "the refused join inflates revenue by half"

        # And the filter form of the same question is still permitted, because a
        # semi-join cannot duplicate an order however many customer rows exist.
        allowed = o.compile("revenue", filters=[("Sales.Customers", "region", "=", "North")])
        assert isinstance(allowed, CompiledQuery)
        assert con.execute(allowed.sql).fetchone()[0] == 30.0
    finally:
        con.close()


def test_a_table_with_no_primary_key_becomes_no_object(lake) -> None:  # noqa: ANN001
    """A thing that cannot identify one of itself is not an object type."""
    from ontology import from_manifest

    manifest, root = lake
    manifest = {**manifest, "primary_keys": {"Sales.Orders": ["order_id"]}}
    o = from_manifest(manifest, lake_root=root)
    assert set(o.objects) == {"Sales.Orders"}
    assert not o.links, "a link to an object that cannot identify a row is not a link"


# --------------------------------------------------------------------------
# one path or none: the quietest way this layer could be wrong
# --------------------------------------------------------------------------


@pytest.fixture()
def roles():  # noqa: ANN201
    """A calendar reached two ways - the role-playing dimension every model has."""
    import duckdb

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE cal (d DATE, yr INTEGER)")
    c.execute("INSERT INTO cal VALUES (DATE '2026-01-01', 2026), (DATE '2027-01-01', 2027)")
    c.execute("CREATE TABLE ord (id INTEGER, order_date DATE, ship_date DATE, amt DOUBLE)")
    c.execute("INSERT INTO ord VALUES (1, DATE '2026-01-01', DATE '2027-01-01', 100)")
    o = Ontology()
    o.add_object("ord", "ord", ["id"])
    o.add_object("cal", "cal", ["d"])
    o.add_link("ordered_on", "ord", ["order_date"], "cal", ["d"])
    o.add_link("shipped_on", "ord", ["ship_date"], "cal", ["d"])
    o.add_measure("amount", "ord", "SUM(f.amt)")
    o.verify(c)
    try:
        yield c, o
    finally:
        c.close()


def test_two_links_to_the_same_object_is_refused_not_silently_resolved(roles) -> None:  # noqa: ANN001
    """"Amount by year" has two defensible answers, and picking one says nothing.

    Both links are individually many-to-one, so no assertion fires and the query
    is valid. The figure is simply about a different question than the one asked
    - the quietest wrong number this layer could produce.
    """
    _, o = roles
    got = o.compile("amount", group_by=[("cal", "yr")])
    assert isinstance(got, Refusal)
    assert got.reason == "ambiguous_path"
    assert "ordered_on" in got.detail and "shipped_on" in got.detail


def test_the_two_paths_really_do_disagree(roles) -> None:  # noqa: ANN001
    """A refusal is only justified while the two readings differ."""
    c, o = roles
    by_order = o.compile("amount", group_by=[("cal", "yr")], via={"cal": "ordered_on"})
    by_ship = o.compile("amount", group_by=[("cal", "yr")], via={"cal": "shipped_on"})
    assert c.execute(by_order.sql).fetchall() == [(2026, 100.0)]
    assert c.execute(by_ship.sql).fetchall() == [(2027, 100.0)]


def test_naming_the_path_resolves_the_ambiguity(roles) -> None:  # noqa: ANN001
    _, o = roles
    got = o.compile("amount", group_by=[("cal", "yr")], via={"cal": "shipped_on"})
    assert isinstance(got, CompiledQuery)
    assert "shipped_on" in " ".join(got.notes)


def test_naming_a_path_that_does_not_exist_is_refused(roles) -> None:  # noqa: ANN001
    _, o = roles
    got = o.compile("amount", group_by=[("cal", "yr")], via={"cal": "invoiced_on"})
    assert isinstance(got, Refusal)
    assert got.reason == "unknown_link"
    assert "ordered_on" in got.detail, "the refusal must list what is available"


def test_ambiguity_is_refused_on_filters_too(roles) -> None:  # noqa: ANN001
    _, o = roles
    got = o.compile("amount", filters=[("cal", "yr", "=", 2026)])
    assert isinstance(got, Refusal)
    assert got.reason == "ambiguous_path"


def test_a_self_link_via_is_not_silently_dropped() -> None:
    """"By manager name" is not "by own name". Dropping via= answers the other one.

    A self-linked grain (employee.manager_id -> employee.id) has two readings of
    group_by employee.name: the row's own name, and the parent reached through
    the named link. The fact-attribute shortcut must not fire when via names
    this object, or the compiler returns a valid query about the wrong person.
    """
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute(
            "CREATE TABLE emp (id INTEGER, name VARCHAR, manager_id INTEGER, amt DOUBLE)"
        )
        c.execute(
            "INSERT INTO emp VALUES (1, 'Ava', 2, 100), (2, 'Ben', NULL, 50)"
        )
        o = Ontology()
        o.add_object("emp", "emp", ["id"])
        o.add_link("managed_by", "emp", ["manager_id"], "emp", ["id"])
        o.add_measure("amount", "emp", "SUM(f.amt)")
        o.verify(c)
        own = o.compile("amount", group_by=[("emp", "name")])
        via_mgr = o.compile(
            "amount", group_by=[("emp", "name")], via={"emp": "managed_by"}
        )
        assert isinstance(own, CompiledQuery)
        assert "JOIN" not in own.sql.upper()
        assert dict(c.execute(own.sql).fetchall()) == {"Ava": 100.0, "Ben": 50.0}
        assert isinstance(via_mgr, CompiledQuery)
        assert "JOIN" in via_mgr.sql.upper()
        assert dict(c.execute(via_mgr.sql).fetchall()) == {"Ben": 100.0, None: 50.0}
        own_f = o.compile("amount", filters=[("emp", "name", "=", "Ben")])
        via_f = o.compile(
            "amount", filters=[("emp", "name", "=", "Ben")], via={"emp": "managed_by"}
        )
        assert c.execute(own_f.sql).fetchone()[0] == 50.0
        assert c.execute(via_f.sql).fetchone()[0] == 100.0
    finally:
        c.close()


# --------------------------------------------------------------------------
# an absence of measurement is not a measurement
# --------------------------------------------------------------------------


def test_a_link_to_an_empty_dimension_is_not_declared_safe() -> None:
    """"Unique because empty" is a verdict that stops being true when rows arrive.

    COUNT(*) = COUNT(DISTINCT) = 0 satisfies the uniqueness test trivially. The
    verdict would then be cached and trusted for every later query.
    """
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE f (id INTEGER, k INTEGER, amt DOUBLE)")
        c.execute("INSERT INTO f VALUES (1, 1, 10)")
        c.execute("CREATE TABLE d (k INTEGER, label VARCHAR)")
        o = Ontology()
        o.add_object("f", "f", ["id"])
        o.add_object("d", "d", ["k"])
        o.add_link("f_to_d", "f", ["k"], "d", ["k"])
        o.add_measure("amt", "f", "SUM(f.amt)")

        violations = o.verify(c)
        assert [v.check for v in violations] == ["link_unmeasurable"]
        assert o.links["f_to_d"].cardinality == "unverified"
        assert not o.verified
        assert isinstance(o.compile("amt", group_by=[("d", "label")]), Refusal)
    finally:
        c.close()


def test_the_same_link_verifies_once_the_dimension_has_rows() -> None:
    """And it must not stay refused forever - that would be R-0005."""
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE f (id INTEGER, k INTEGER, amt DOUBLE)")
        c.execute("INSERT INTO f VALUES (1, 1, 10)")
        c.execute("CREATE TABLE d (k INTEGER, label VARCHAR)")
        c.execute("INSERT INTO d VALUES (1, 'A')")
        o = Ontology()
        o.add_object("f", "f", ["id"])
        o.add_object("d", "d", ["k"])
        o.add_link("f_to_d", "f", ["k"], "d", ["k"])
        o.add_measure("amt", "f", "SUM(f.amt)")
        assert not o.verify(c)
        assert o.links["f_to_d"].cardinality == "many_to_one"
        got = o.compile("amt", group_by=[("d", "label")])
        assert isinstance(got, CompiledQuery)
        assert c.execute(got.sql).fetchall() == [("A", 10.0)]
    finally:
        c.close()


def test_grouping_by_two_verified_dimensions_at_once_is_allowed(con) -> None:  # noqa: ANN001
    """Each many-to-one join adds at most one row, so the combination is safe."""
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", group_by=[("product", "category"), ("region", "country")])
    assert isinstance(got, CompiledQuery)
    rows = con.execute(got.sql).fetchall()
    assert sum(r[-1] for r in rows) == 150.0, "two joins must still conserve the total"


# --------------------------------------------------------------------------
# execute the derived path, do not merely type-check it
# --------------------------------------------------------------------------


def test_a_derived_ontology_emits_sql_that_actually_runs(lake) -> None:  # noqa: ANN001
    """The test that was missing, and the reason a parse error shipped.

    Every other test of the derived path asserted `isinstance(got, CompiledQuery)`
    and stopped. The alias was built as "d_" plus the object name, so a
    schema-qualified table produced `d_Sales.Customers` - which DuckDB cannot
    parse at all. Twenty-nine tests passed over SQL that could never run,
    because none of them ran it. R-0001: assert the artifact, at the layer it is
    used.
    """
    import duckdb
    from ontology import from_manifest

    manifest, root = lake
    # Make the parent unique so the grouping is permitted and reaches execution.
    manifest = {
        **manifest,
        "primary_keys": {
            "Sales.Orders": ["order_id"],
            "Sales.Customers": ["cust_ref"],
        },
    }
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            "COPY (SELECT * FROM (VALUES ('C1','North'),('C2','South')) AS t(cust_ref, region)) "
            "TO '" + (root / "db" / "Sales.Customers.parquet").as_posix()
            + "' (FORMAT PARQUET)"
        )
        o = from_manifest(manifest, lake_root=root)
        o.add_measure("revenue", "Sales.Orders", "SUM(f.amount)")
        assert not o.verify(con)

        got = o.compile("revenue", group_by=[("Sales.Customers", "region")])
        assert isinstance(got, CompiledQuery)
        assert "d_Sales." not in got.sql, "a dotted alias cannot be parsed"
        rows = dict(con.execute(got.sql).fetchall())
        assert rows == {"North": 30.0, "South": 30.0}
        assert sum(rows.values()) == 60.0, "the derived join must conserve the total"
    finally:
        con.close()


def test_two_attributes_of_one_dimension_share_a_single_join(con) -> None:  # noqa: ANN001
    """Emitting the join twice produced an ambiguous reference, not a wrong number.

    Still a defect: the query does not run, and the failure surfaces at execute
    time rather than as a refusal that says what to do.
    """
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", group_by=[("region", "region"), ("region", "country")])
    assert isinstance(got, CompiledQuery)
    assert got.sql.upper().count("LEFT JOIN") == 1
    assert len(got.notes) == 1
    rows = con.execute(got.sql).fetchall()
    assert sum(r[-1] for r in rows) == 150.0


def test_a_link_whose_sides_differ_in_arity_is_rejected_at_authoring() -> None:
    """zip() truncated, so the join used one column and verify() measured two."""
    o = Ontology()
    o.add_object("a", "sales", ["txn_id"])
    o.add_object("b", "lots", ["lot_id"])
    with pytest.raises(ValueError) as exc:
        o.add_link("bad", "a", ["sku"], "b", ["sku", "category"])
    assert "same arity" in str(exc.value)
    with pytest.raises(ValueError):
        o.add_link("empty", "a", [], "b", [])


def test_two_constraints_sharing_a_name_stay_two_links(lake) -> None:  # noqa: ANN001
    """Constraint names are unique per table, not per database.

    Grouping by name alone merged an HR foreign key into a Sales one, producing
    a single link holding the columns of both - which verify() then measured and
    blessed as many-to-one.
    """
    from ontology import from_manifest

    manifest, root = lake
    manifest = {
        **manifest,
        "tables": [
            *manifest["tables"],
            {"schema": "HR", "table": "Staff", "declared_rows": 1, "extracted_rows": 1,
             "columns": 2, "path": "db/Sales.Orders.parquet"},
        ],
        "primary_keys": {**manifest["primary_keys"], "HR.Staff": ["order_id"]},
        "foreign_keys": [
            *manifest["foreign_keys"],
            {"name": "FK_Orders_Customers", "from_table": "HR.Staff",
             "from_column": "cust_ref", "to_table": "Sales.Customers",
             "to_column": "cust_ref"},
        ],
    }
    o = from_manifest(manifest, lake_root=root)
    assert len(o.links) == 2, f"two constraints collapsed into {len(o.links)}"
    froms = {link.from_object for link in o.links.values()}
    assert froms == {"Sales.Orders", "HR.Staff"}


def test_limit_zero_means_zero_and_a_negative_limit_is_refused(con) -> None:  # noqa: ANN001
    """`if limit:` treated 0 as unlimited, which is the opposite of what it says."""
    o = _ontology()
    o.verify(con)
    zero = o.compile("revenue", group_by=[("product", "category")], limit=0)
    assert isinstance(zero, CompiledQuery)
    assert "LIMIT 0" in zero.sql
    assert con.execute(zero.sql).fetchall() == []
    assert isinstance(o.compile("revenue", limit=-5), Refusal)


# --------------------------------------------------------------------------
# multi-hop: a chain of many-to-one links is still one row per fact row
# --------------------------------------------------------------------------


@pytest.fixture()
def chain():  # noqa: ANN201
    """sales -> product -> subcategory -> category, every hop many-to-one.

    Two lines of 100 and 50 on two products in different subcategories that
    roll up to the same category, plus a third in another category. So by
    category the truth is Bikes=150, Clothing=25, total 175, and any fan-out or
    row loss breaks that arithmetic.
    """
    import duckdb

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE sales (txn_id INTEGER, product_id INTEGER, amt DOUBLE)")
    c.execute("INSERT INTO sales VALUES (1, 10, 100), (2, 11, 50), (3, 12, 25)")
    c.execute("CREATE TABLE product (id INTEGER, subcat_id INTEGER, name VARCHAR)")
    c.execute("INSERT INTO product VALUES (10, 100, 'Road'), (11, 101, 'MTB'), (12, 102, 'Jersey')")
    c.execute("CREATE TABLE subcat (id INTEGER, cat_id INTEGER, name VARCHAR)")
    c.execute("INSERT INTO subcat VALUES (100, 1, 'Road Bikes'), (101, 1, 'Mountain Bikes'), "
              "(102, 2, 'Jerseys')")
    c.execute("CREATE TABLE category (id INTEGER, name VARCHAR)")
    c.execute("INSERT INTO category VALUES (1, 'Bikes'), (2, 'Clothing')")
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    o.add_object("product", "product", ["id"])
    o.add_object("subcat", "subcat", ["id"])
    o.add_object("category", "category", ["id"])
    o.add_link("sale_product", "sale", ["product_id"], "product", ["id"])
    o.add_link("product_subcat", "product", ["subcat_id"], "subcat", ["id"])
    o.add_link("subcat_category", "subcat", ["cat_id"], "category", ["id"])
    o.add_measure("revenue", "sale", "SUM(f.amt)")
    assert not o.verify(c)
    try:
        yield c, o
    finally:
        c.close()


def test_a_three_hop_grouping_compiles_executes_and_conserves(chain) -> None:  # noqa: ANN001
    """The AdventureWorks shape: sales by product category is three links away."""
    c, o = chain
    got = o.compile("revenue", group_by=[("category", "name")])
    assert isinstance(got, CompiledQuery), got
    assert got.sql.upper().count("LEFT JOIN") == 3
    rows = dict(c.execute(got.sql).fetchall())
    assert rows == {"Bikes": 150.0, "Clothing": 25.0}
    assert sum(rows.values()) == 175.0, "three hops must still conserve the total"


def test_a_two_hop_filter_is_a_semi_join_over_the_chain(chain) -> None:  # noqa: ANN001
    """Filter-then-aggregate survives the path: the fact only ever sees IN (keys)."""
    c, o = chain
    got = o.compile("revenue", filters=[("category", "name", "=", "Bikes")])
    assert isinstance(got, CompiledQuery), got
    assert " IN (" in got.sql and "LEFT JOIN" not in got.sql.split("WHERE")[0].upper()
    assert c.execute(got.sql).fetchone()[0] == 150.0


def test_a_many_to_many_hop_anywhere_in_the_chain_is_refused() -> None:
    """The last hop joins on a column that is not unique; the path is refused.

    The first version of this test duplicated a category ROW, which broke the
    category object's own key and made the whole ontology unverified - the
    refusal came back as ontology_unverified, which proves nothing about
    fan-out. The hazard has to live on the LINK while every key stays unique:
    subcat -> category joins on category.code, and two categories share a code.
    """
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE sales (txn_id INTEGER, product_id INTEGER, amt DOUBLE)")
        c.execute("INSERT INTO sales VALUES (1, 10, 100), (2, 11, 50), (3, 12, 25)")
        c.execute("CREATE TABLE product (id INTEGER, subcat_id INTEGER)")
        c.execute("INSERT INTO product VALUES (10, 100), (11, 101), (12, 102)")
        c.execute("CREATE TABLE subcat (id INTEGER, cat_code VARCHAR)")
        c.execute("INSERT INTO subcat VALUES (100, 'B'), (101, 'B'), (102, 'C')")
        c.execute("CREATE TABLE category (id INTEGER, code VARCHAR, name VARCHAR)")
        # two categories share code B - keys unique, link parent side not
        c.execute("INSERT INTO category VALUES (1, 'B', 'Bikes'), (3, 'B', 'Bikes EU'), "
                  "(2, 'C', 'Clothing')")
        o = Ontology()
        o.add_object("sale", "sales", ["txn_id"])
        o.add_object("product", "product", ["id"])
        o.add_object("subcat", "subcat", ["id"])
        o.add_object("category", "category", ["id"])
        o.add_link("sale_product", "sale", ["product_id"], "product", ["id"])
        o.add_link("product_subcat", "product", ["subcat_id"], "subcat", ["id"])
        o.add_link("subcat_category", "subcat", ["cat_code"], "category", ["code"])
        o.add_measure("revenue", "sale", "SUM(f.amt)")
        assert not o.verify(c), "every key is unique; only the link fans out"
        assert o.links["subcat_category"].cardinality == "many_to_many"

        got = o.compile("revenue", group_by=[("category", "name")])
        assert isinstance(got, Refusal)
        assert got.reason == "fanout_refused"
        # the refused join really would have inflated: Bikes rows counted twice
        inflated = c.execute(
            "SELECT SUM(s.amt) FROM sales s JOIN product p ON s.product_id = p.id "
            "JOIN subcat sc ON p.subcat_id = sc.id JOIN category k ON sc.cat_code = k.code"
        ).fetchone()[0]
        assert inflated == 325.0, "150 of Bikes revenue counted twice plus 25"
        # and the filter form is still safe through the same hazard
        flt = o.compile("revenue", filters=[("category", "name", "=", "Bikes")])
        assert isinstance(flt, CompiledQuery)
        assert c.execute(flt.sql).fetchone()[0] == 150.0
    finally:
        c.close()


def test_two_shortest_paths_to_the_same_object_are_refused_naming_both() -> None:
    """sales -> store -> region and sales -> customer -> region: which region?"""
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE sales (id INTEGER, store_id INTEGER, cust_id INTEGER, amt DOUBLE)")
        c.execute("INSERT INTO sales VALUES (1, 1, 1, 100)")
        c.execute("CREATE TABLE store (id INTEGER, region_id INTEGER)")
        c.execute("INSERT INTO store VALUES (1, 1)")
        c.execute("CREATE TABLE customer (id INTEGER, region_id INTEGER)")
        c.execute("INSERT INTO customer VALUES (1, 2)")
        c.execute("CREATE TABLE region (id INTEGER, name VARCHAR)")
        c.execute("INSERT INTO region VALUES (1, 'North'), (2, 'South')")
        o = Ontology()
        o.add_object("sale", "sales", ["id"])
        o.add_object("store", "store", ["id"])
        o.add_object("customer", "customer", ["id"])
        o.add_object("region", "region", ["id"])
        o.add_link("sale_store", "sale", ["store_id"], "store", ["id"])
        o.add_link("sale_customer", "sale", ["cust_id"], "customer", ["id"])
        o.add_link("store_region", "store", ["region_id"], "region", ["id"])
        o.add_link("customer_region", "customer", ["region_id"], "region", ["id"])
        o.add_measure("revenue", "sale", "SUM(f.amt)")
        assert not o.verify(c)
        got = o.compile("revenue", group_by=[("region", "name")])
        assert isinstance(got, Refusal)
        assert got.reason == "ambiguous_path"
        assert "sale_store" in got.detail and "sale_customer" in got.detail
        # the two readings genuinely disagree, so the refusal is justified
        by_store = c.execute(
            "SELECT r.name FROM sales s JOIN store st ON s.store_id = st.id "
            "JOIN region r ON st.region_id = r.id"
        ).fetchone()[0]
        by_cust = c.execute(
            "SELECT r.name FROM sales s JOIN customer cu ON s.cust_id = cu.id "
            "JOIN region r ON cu.region_id = r.id"
        ).fetchone()[0]
        assert by_store != by_cust
        # grouping by the intermediate object you mean is the way through
        via_store = o.compile("revenue", group_by=[("store", "region_id")])
        assert isinstance(via_store, CompiledQuery)
    finally:
        c.close()


def test_via_resolves_a_role_playing_hop_in_the_middle_of_a_chain() -> None:
    """order -> calendar(order_date | ship_date) -> fiscal_period: name the hop."""
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE ord (id INTEGER, order_date DATE, ship_date DATE, amt DOUBLE)")
        c.execute("INSERT INTO ord VALUES (1, DATE '2026-01-01', DATE '2026-07-01', 100)")
        c.execute("CREATE TABLE cal (d DATE, period_id INTEGER)")
        c.execute("INSERT INTO cal VALUES (DATE '2026-01-01', 1), (DATE '2026-07-01', 2)")
        c.execute("CREATE TABLE period (id INTEGER, name VARCHAR)")
        c.execute("INSERT INTO period VALUES (1, 'H1'), (2, 'H2')")
        o = Ontology()
        o.add_object("ord", "ord", ["id"])
        o.add_object("cal", "cal", ["d"])
        o.add_object("period", "period", ["id"])
        o.add_link("ordered_on", "ord", ["order_date"], "cal", ["d"])
        o.add_link("shipped_on", "ord", ["ship_date"], "cal", ["d"])
        o.add_link("cal_period", "cal", ["period_id"], "period", ["id"])
        o.add_measure("amount", "ord", "SUM(f.amt)")
        assert not o.verify(c)
        assert isinstance(o.compile("amount", group_by=[("period", "name")]), Refusal)
        h1 = o.compile("amount", group_by=[("period", "name")], via={"cal": "ordered_on"})
        h2 = o.compile("amount", group_by=[("period", "name")], via={"cal": "shipped_on"})
        assert isinstance(h1, CompiledQuery) and isinstance(h2, CompiledQuery)
        assert dict(c.execute(h1.sql).fetchall()) == {"H1": 100.0}
        assert dict(c.execute(h2.sql).fetchall()) == {"H2": 100.0}
    finally:
        c.close()


def test_an_unreachable_object_is_still_no_path(chain) -> None:  # noqa: ANN001
    c, o = chain
    o.add_object("orphan", "category", ["id"])
    o.verify(c)
    got = o.compile("revenue", group_by=[("orphan", "name")])
    assert isinstance(got, Refusal)
    assert got.reason == "no_path"


# --------------------------------------------------------------------------
# the adversarial round on the resolver: a blocked hop is never skipped silently
# --------------------------------------------------------------------------


def test_a_blocked_shorter_route_is_refused_not_bypassed_by_a_longer_clean_one() -> None:
    """"Revenue by fiscal year" has an order-date reading two hops away.

    The first resolver skipped the ambiguous sale -> cal hop and carried on to
    sale -> customer -> cohort -> fy, three clean hops, and answered the
    customer-cohort year with no refusal and no note. A valid query about a
    different question - the quietest wrong number this layer can produce.
    """
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE sales (id INTEGER, order_date DATE, ship_date DATE, "
                  "cust_id INTEGER, amt DOUBLE)")
        c.execute("INSERT INTO sales VALUES (1, DATE '2025-03-01', DATE '2025-03-05', 1, 100), "
                  "(2, DATE '2026-03-01', DATE '2026-03-05', 1, 50)")
        c.execute("CREATE TABLE cal (d DATE, fy_year INTEGER)")
        c.execute("INSERT INTO cal VALUES (DATE '2025-03-01', 2025), (DATE '2025-03-05', 2025), "
                  "(DATE '2026-03-01', 2026), (DATE '2026-03-05', 2026)")
        c.execute("CREATE TABLE customer (id INTEGER, cohort_id INTEGER)")
        c.execute("INSERT INTO customer VALUES (1, 10)")
        c.execute("CREATE TABLE cohort (id INTEGER, fy_year INTEGER)")
        c.execute("INSERT INTO cohort VALUES (10, 2024)")
        c.execute("CREATE TABLE fy (year INTEGER, label VARCHAR)")
        c.execute("INSERT INTO fy VALUES (2024, 'FY24'), (2025, 'FY25'), (2026, 'FY26')")
        o = Ontology()
        for name, rel, key in [("sale", "sales", ["id"]), ("cal", "cal", ["d"]),
                               ("customer", "customer", ["id"]), ("cohort", "cohort", ["id"]),
                               ("fy", "fy", ["year"])]:
            o.add_object(name, rel, key)
        o.add_link("ordered_on", "sale", ["order_date"], "cal", ["d"])
        o.add_link("shipped_on", "sale", ["ship_date"], "cal", ["d"])
        o.add_link("cal_fy", "cal", ["fy_year"], "fy", ["year"])
        o.add_link("sale_customer", "sale", ["cust_id"], "customer", ["id"])
        o.add_link("customer_cohort", "customer", ["cohort_id"], "cohort", ["id"])
        o.add_link("cohort_fy", "cohort", ["fy_year"], "fy", ["year"])
        o.add_measure("rev", "sale", "SUM(f.amt)")
        assert not o.verify(c)

        got = o.compile("rev", group_by=[("fy", "label")])
        assert isinstance(got, Refusal), got
        assert got.reason == "ambiguous_path"
        assert "ordered_on" in got.detail and "shipped_on" in got.detail
        # the filter form must refuse too - it matched nothing before
        flt = o.compile("rev", filters=[("fy", "label", "=", "FY25")])
        assert isinstance(flt, Refusal)
        # naming the hop gives the order-date reading
        by_order = o.compile("rev", group_by=[("fy", "label")], via={"cal": "ordered_on"})
        assert isinstance(by_order, CompiledQuery)
        assert dict(c.execute(by_order.sql).fetchall()) == {"FY25": 100.0, "FY26": 50.0}
    finally:
        c.close()


def test_a_blocked_hop_unrelated_to_the_target_does_not_refuse_a_clean_path() -> None:
    """R-0005: a role-playing calendar must not block "revenue by store region"."""
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE sales (id INTEGER, d1 DATE, d2 DATE, store_id INTEGER, amt DOUBLE)")
        c.execute("INSERT INTO sales VALUES (1, DATE '2025-01-01', DATE '2025-01-02', 1, 100)")
        c.execute("CREATE TABLE cal (d DATE, yr INTEGER)")
        c.execute("INSERT INTO cal VALUES (DATE '2025-01-01', 2025), (DATE '2025-01-02', 2025)")
        c.execute("CREATE TABLE store (id INTEGER, region VARCHAR)")
        c.execute("INSERT INTO store VALUES (1, 'North')")
        o = Ontology()
        o.add_object("sale", "sales", ["id"])
        o.add_object("cal", "cal", ["d"])
        o.add_object("store", "store", ["id"])
        o.add_link("on_d1", "sale", ["d1"], "cal", ["d"])
        o.add_link("on_d2", "sale", ["d2"], "cal", ["d"])
        o.add_link("sale_store", "sale", ["store_id"], "store", ["id"])
        o.add_measure("rev", "sale", "SUM(f.amt)")
        assert not o.verify(c)
        got = o.compile("rev", group_by=[("store", "region")])
        assert isinstance(got, CompiledQuery), got
        assert dict(c.execute(got.sql).fetchall()) == {"North": 100.0}
    finally:
        c.close()


def test_via_works_on_a_diamond_and_the_bare_diamond_is_refused_naming_both() -> None:
    """via was matched only against the current frontier node's links, so the
    documented remedy "name each hop with via=" never worked on a diamond."""
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE f (id INTEGER, a_id INTEGER, b_id INTEGER, amt DOUBLE)")
        c.execute("INSERT INTO f VALUES (1, 1, 2, 100), (2, 2, 1, 50)")
        c.execute("CREATE TABLE a (id INTEGER, d_id INTEGER)")
        c.execute("INSERT INTO a VALUES (1, 10), (2, 20)")
        c.execute("CREATE TABLE b (id INTEGER, d_id INTEGER)")
        c.execute("INSERT INTO b VALUES (1, 10), (2, 20)")
        c.execute("CREATE TABLE d (id INTEGER, name VARCHAR)")
        c.execute("INSERT INTO d VALUES (10, 'ten'), (20, 'twenty')")
        o = Ontology()
        for name, key in [("F", ["id"]), ("A", ["id"]), ("B", ["id"]), ("D", ["id"])]:
            o.add_object(name, name.lower(), key)
        o.add_link("f_a", "F", ["a_id"], "A", ["id"])
        o.add_link("f_b", "F", ["b_id"], "B", ["id"])
        o.add_link("a_d", "A", ["d_id"], "D", ["id"])
        o.add_link("b_d", "B", ["d_id"], "D", ["id"])
        o.add_measure("amt", "F", "SUM(f.amt)")
        assert not o.verify(c)
        bare = o.compile("amt", group_by=[("D", "name")])
        assert isinstance(bare, Refusal) and bare.reason == "ambiguous_path"
        assert "a_d" in bare.detail and "b_d" in bare.detail
        via_a = o.compile("amt", group_by=[("D", "name")], via={"D": "a_d"})
        assert isinstance(via_a, CompiledQuery), via_a
        assert dict(c.execute(via_a.sql).fetchall()) == {"ten": 100.0, "twenty": 50.0}
        via_b = o.compile("amt", group_by=[("D", "name")], via={"D": "b_d"})
        assert isinstance(via_b, CompiledQuery), via_b
        assert dict(c.execute(via_b.sql).fetchall()) == {"twenty": 100.0, "ten": 50.0}
    finally:
        c.close()


def test_a_diamond_with_one_unsafe_branch_is_ambiguous_and_via_picks_the_safe_one() -> None:
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE f (id INTEGER, a_id INTEGER, b_id INTEGER, amt DOUBLE)")
        c.execute("INSERT INTO f VALUES (1, 1, 1, 100)")
        c.execute("CREATE TABLE a (id INTEGER, d_code VARCHAR)")
        c.execute("INSERT INTO a VALUES (1, 'X')")
        c.execute("CREATE TABLE b (id INTEGER, d_code VARCHAR)")
        c.execute("INSERT INTO b VALUES (1, 'X')")
        # d is not unique on code -> the b_d link below fans out; a_d joins on id
        c.execute("CREATE TABLE d (id INTEGER, code VARCHAR, name VARCHAR)")
        c.execute("INSERT INTO d VALUES (10, 'X', 'ten'), (11, 'X', 'ten-bis')")
        c.execute("ALTER TABLE a ADD COLUMN d_id INTEGER; UPDATE a SET d_id = 10")
        o = Ontology()
        for name, key in [("F", ["id"]), ("A", ["id"]), ("B", ["id"]), ("D", ["id"])]:
            o.add_object(name, name.lower(), key)
        o.add_link("f_a", "F", ["a_id"], "A", ["id"])
        o.add_link("f_b", "F", ["b_id"], "B", ["id"])
        o.add_link("a_d", "A", ["d_id"], "D", ["id"])
        o.add_link("b_d", "B", ["d_code"], "D", ["code"])
        o.add_measure("amt", "F", "SUM(f.amt)")
        assert not o.verify(c)
        assert o.links["b_d"].cardinality == "many_to_many"
        bare = o.compile("amt", group_by=[("D", "name")])
        assert isinstance(bare, Refusal) and bare.reason == "ambiguous_path"
        safe = o.compile("amt", group_by=[("D", "name")], via={"D": "a_d"})
        assert isinstance(safe, CompiledQuery), safe
        assert dict(c.execute(safe.sql).fetchall()) == {"ten": 100.0}
    finally:
        c.close()


def test_a_null_parent_key_does_not_make_a_unique_link_many_to_many() -> None:
    """COUNT(*) counts a NULL key row, COUNT(DISTINCT) does not; a NULL parent
    key never matches a child, so it cannot fan out. One NULL row made a safe
    link read 'many_to_many up to 1x' and refused every grouping through it."""
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE f (id INTEGER, k INTEGER, amt DOUBLE)")
        c.execute("INSERT INTO f VALUES (1, 1, 100), (2, 2, 50), (3, 9, 25)")
        c.execute("CREATE TABLE d (k INTEGER, name VARCHAR)")
        c.execute("INSERT INTO d VALUES (1, 'ex'), (2, 'why'), (NULL, 'ghost')")
        o = Ontology()
        o.add_object("f", "f", ["id"])
        o.add_object("d", "d", ["name"])
        o.add_link("f_d", "f", ["k"], "d", ["k"])
        o.add_measure("amt", "f", "SUM(f.amt)")
        assert not o.verify(c)
        assert o.links["f_d"].cardinality == "many_to_one"
        got = o.compile("amt", group_by=[("d", "name")])
        assert isinstance(got, CompiledQuery), got
        rows = dict(c.execute(got.sql).fetchall())
        assert rows == {"ex": 100.0, "why": 50.0, None: 25.0}
        assert sum(rows.values()) == 175.0
    finally:
        c.close()


def test_an_unknown_column_is_refused_not_emitted(con) -> None:  # noqa: ANN001
    """The layer promised a refusal and delivered a binder error at execution."""
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", group_by=[("product", "categorie")])
    assert isinstance(got, Refusal)
    assert got.reason == "unknown_column"
    assert "category" in got.detail, "the refusal must list what is available"
    flt = o.compile("revenue", filters=[("region", "contry", "=", "MY")])
    assert isinstance(flt, Refusal) and flt.reason == "unknown_column"


def test_a_link_on_a_missing_child_column_fails_verification() -> None:
    """verify() read only the parent side, so a bad child column was blessed."""
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE f (id INTEGER, k INTEGER)")
        c.execute("CREATE TABLE d (k INTEGER, name VARCHAR)")
        c.execute("INSERT INTO d VALUES (1, 'x')")
        o = Ontology()
        o.add_object("f", "f", ["id"])
        o.add_object("d", "d", ["k"])
        o.add_link("bad", "f", ["no_such_col"], "d", ["k"])
        v = o.verify(c)
        assert [x.check for x in v] == ["link_readable"]
        assert not o.verified
    finally:
        c.close()


def test_two_filters_on_one_many_to_many_object_mean_the_same_linked_row(con) -> None:  # noqa: ANN001
    """"lots that are ALPHA and qty > 25" - one lot satisfying both, not any-each.

    Two separate IN (...) clauses answered any-lot-each silently. SKU-1 has lots
    of 10, 20, 30 - all ALPHA; SKU-2 has one BETA lot of 40. One semi-join with
    both predicates keeps SKU-1 (lot 30 is ALPHA and > 25) and not SKU-2.
    """
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", filters=[("lot", "category", "=", "ALPHA"), ("lot", "qty", ">", 25)])
    assert isinstance(got, CompiledQuery), got
    assert got.sql.count(" IN (") == 1, "two predicates on one object must be one semi-join"
    assert got.existential, "a filter through the lot link is existential and must say so"
    assert con.execute(got.sql).fetchone()[0] == 100.0
    # a pair no single lot satisfies returns nothing, not the any-each union
    none = o.compile("revenue", filters=[("lot", "category", "=", "BETA"), ("lot", "qty", "<", 35)])
    assert isinstance(none, CompiledQuery)
    assert con.execute(none.sql).fetchone()[0] in (None, 0.0)


def test_a_filter_over_a_many_to_one_path_is_not_flagged_existential(con) -> None:  # noqa: ANN001
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", filters=[("region", "country", "=", "MY")])
    assert isinstance(got, CompiledQuery)
    assert not got.existential


def test_an_unused_via_is_refused_not_ignored(con) -> None:  # noqa: ANN001
    """A via the path never passed through would have been silently dropped."""
    o = _ontology()
    o.verify(con)
    got = o.compile("revenue", group_by=[("region", "country")], via={"product": "sale_of_product"})
    assert isinstance(got, Refusal)
    assert got.reason == "unused_via"
    typo = o.compile("revenue", group_by=[("region", "country")], via={"product": "no_such"})
    assert isinstance(typo, Refusal) and typo.reason == "unknown_link"
