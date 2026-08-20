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
