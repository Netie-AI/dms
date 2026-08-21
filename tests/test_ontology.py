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
