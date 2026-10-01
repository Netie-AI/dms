"""Structured measures at the ontology seam (dms#283 step 1).

``measure_expression`` is the one place a confirmed measure's SQL text is
built; ``Ontology.compile`` re-derives it from the provenance and refuses on any
difference. The rows asserted are checked against an independent oracle query.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import duckdb
import pytest
from dms_executor.ontology import (
    MEASURE_AGGREGATES,
    CompiledQuery,
    Measure,
    MeasureProvenance,
    Ontology,
    Refusal,
    _ident,
    demo_ontology,
    measure_expression,
)


def _prov(aggregate: str = "sum", column: str = "amount") -> MeasureProvenance:
    return MeasureProvenance(
        measure_id="m1",
        name="revenue_sum",
        grain="sale",
        aggregate=aggregate,
        column=column,
        column_type="DOUBLE",
        definition_hash="0" * 64,
        version_id="v1",
        decided_at="2026-01-01",
        ledger_entry_id="L-1",
        persisted=True,
    )


@pytest.fixture()
def con():  # noqa: ANN201
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE sales (txn_id VARCHAR, region VARCHAR, amount DOUBLE)")
    c.execute("INSERT INTO sales VALUES ('T1','N',100),('T2','S',50.5),('T3','N',NULL)")
    try:
        yield c
    finally:
        c.close()


def _onto(con) -> Ontology:  # noqa: ANN001
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    assert not o.verify(con)
    return o


def test_expression_table() -> None:
    assert measure_expression("count", "*") == "COUNT(*)"
    assert measure_expression("count", "x") == f"COUNT(f.{_ident('x')})"
    assert measure_expression("count_distinct", "x") == 'COUNT(DISTINCT f."x")'
    for agg in ("sum", "avg", "min", "max"):
        assert measure_expression(agg, "amount") == f'{agg.upper()}(f."amount")'
    assert set(MEASURE_AGGREGATES) == {"sum", "avg", "min", "max", "count", "count_distinct"}


@pytest.mark.parametrize(
    ("agg", "col"),
    [
        ("sum", "*"),
        ("avg", "*"),
        ("median", "x"),
        ("sum", ""),
        ("", "x"),
        ("SUM", "x"),
        ("sum(", "x"),
    ],
)
def test_expression_refuses_outside_the_enum(agg: str, col: str) -> None:
    with pytest.raises(ValueError):
        measure_expression(agg, col)


def test_defaults_are_unchanged_for_demo_and_manual_measures() -> None:
    m = Ontology()
    m.add_object("sale", "sales", ["txn_id"])
    m.add_measure("revenue", "sale", "SUM(f.amount)")
    assert m.measures["revenue"].provenance is None
    assert Measure("a", "b", "c").provenance is None
    demo = demo_ontology(Path("/nonexistent-demo-warehouse"))
    assert demo.measures and all(x.provenance is None for x in demo.measures.values())


def test_provenance_measure_compiles_and_rows_equal_the_oracle(con) -> None:  # noqa: ANN001
    o = _onto(con)
    p = _prov()
    o.add_measure("revenue_sum", "sale", measure_expression("sum", "amount"), provenance=p)
    q = o.compile("revenue_sum")
    assert isinstance(q, CompiledQuery)
    got = con.execute(q.sql).fetchall()
    oracle = con.execute("SELECT SUM(amount) FROM sales").fetchall()
    assert got == oracle == [(150.5,)]
    assert o.measures["revenue_sum"].provenance == p


@pytest.mark.parametrize(
    "evil",
    [
        "SUM(f.amount); DROP TABLE sales",
        "SUM(f.amount) * 2",
        "(SELECT 1)",
        'SUM(f."other")',
    ],
)
def test_compile_refuses_a_tampered_expression_and_emits_no_sql(con, evil: str) -> None:  # noqa: ANN001
    o = _onto(con)
    o.add_measure("revenue_sum", "sale", evil, provenance=_prov())
    out = o.compile("revenue_sum")
    assert isinstance(out, Refusal)
    assert out.reason == "measure_expression_invalid"
    assert not hasattr(out, "sql")
    # nothing ran against the lake
    assert con.execute("SELECT COUNT(*) FROM sales").fetchone() == (3,)


def test_compile_refuses_a_provenance_outside_the_enum(con) -> None:  # noqa: ANN001
    o = _onto(con)
    p = dataclasses.replace(_prov(), aggregate="median")
    o.add_measure("revenue_sum", "sale", "SUM(f.amount)", provenance=p)
    out = o.compile("revenue_sum")
    assert isinstance(out, Refusal) and out.reason == "measure_expression_invalid"


def test_hostile_column_is_one_quoted_identifier_that_fails_to_bind(con) -> None:  # noqa: ANN001
    o = _onto(con)
    hostile = 'a"; DROP TABLE sales; --'
    p = _prov(column=hostile)
    o.add_measure("revenue_sum", "sale", measure_expression("sum", hostile), provenance=p)
    q = o.compile("revenue_sum")
    assert isinstance(q, CompiledQuery)
    with pytest.raises(duckdb.Error):
        con.execute(q.sql)
    assert con.execute("SELECT COUNT(*) FROM sales").fetchone() == (3,)
