"""The bench must be able to fail, and must not launder a gap into a pass.

A generated benchmark that reports 100 pct is worth exactly as much as its
ability to report less. Three ways this one could be theatre, and a test for
each:

  * it never fails - so the bench is run against a compiler with a real
    regression injected (an inner join in place of a left join, which silently
    drops fact rows whose key is absent from a dimension) and must report it;
  * it counts a gap as a pass - a filter value matching no dimension row
    returns a NULL sum, indistinguishable from a matched value with no fact
    rows. The bench must count those apart, not as passes;
  * its oracle shares the compiler's mistake - both sides read the same
    manifest, so a corrupted join column fools both. The bench carries its own
    corrupted-link self-check, and that self-check must itself detect.

The fixture is built here rather than read from the lake (R-0002): this runs on
a machine with no AdventureWorks, no SQL Server and no Docker.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ontology_bench as bench  # noqa: E402


@pytest.fixture()
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """orders -> customer -> region: one two-hop chain, every link many-to-one.

    One order points at a customer that does not exist, so an inner join in
    place of a left join loses it - the regression this bench must catch.
    """
    import duckdb

    (tmp_path / "db").mkdir()
    c = duckdb.connect(":memory:")
    c.execute(
        "COPY (SELECT * FROM (VALUES (1, 1, 100.0), (2, 1, 50.0), (3, 2, 25.0), "
        "(4, 99, 7.0)) AS t(order_id, cust_id, amount)) TO '"
        + (tmp_path / "db" / "S.Orders.parquet").as_posix() + "' (FORMAT PARQUET)"
    )
    c.execute(
        "COPY (SELECT * FROM (VALUES (1, 10, 'Ann'), (2, 20, 'Bob')) "
        "AS t(cust_id, region_id, name)) TO '"
        + (tmp_path / "db" / "S.Customer.parquet").as_posix() + "' (FORMAT PARQUET)"
    )
    c.execute(
        "COPY (SELECT * FROM (VALUES (10, 'North'), (20, 'South')) AS t(region_id, label)) TO '"
        + (tmp_path / "db" / "S.Region.parquet").as_posix() + "' (FORMAT PARQUET)"
    )
    manifest = {
        "database": "Fixture",
        "tables": [
            {"schema": "S", "table": "Orders", "declared_rows": 4, "extracted_rows": 4,
             "columns": 3, "path": "db/S.Orders.parquet"},
            {"schema": "S", "table": "Customer", "declared_rows": 2, "extracted_rows": 2,
             "columns": 3, "path": "db/S.Customer.parquet"},
            {"schema": "S", "table": "Region", "declared_rows": 2, "extracted_rows": 2,
             "columns": 2, "path": "db/S.Region.parquet"},
        ],
        "skipped": [],
        "primary_keys": {"S.Orders": ["order_id"], "S.Customer": ["cust_id"],
                         "S.Region": ["region_id"]},
        "foreign_keys": [
            {"name": "FK_O_C", "from_table": "S.Orders", "from_column": "cust_id",
             "to_table": "S.Customer", "to_column": "cust_id"},
            {"name": "FK_C_R", "from_table": "S.Customer", "from_column": "region_id",
             "to_table": "S.Region", "to_column": "region_id"},
        ],
    }
    monkeypatch.setattr(bench, "ROOT", tmp_path)
    try:
        yield c, manifest
    finally:
        c.close()


def test_the_bench_generates_multi_hop_cases_and_passes_a_sound_compiler(lake) -> None:  # noqa: ANN001
    c, manifest = lake
    r = bench.generate_and_run(c, manifest, max_cases=200)
    assert "error" not in r, r.get("error")
    assert r["failed"] == 0, r.get("failures")
    assert r["passed"] > 0
    by_depth = {int(d): v for d, v in r["by_depth"].items()}
    assert 2 in by_depth, f"no two-hop case was generated: {r['by_depth']}"
    assert by_depth[2]["group"] >= 1 and by_depth[2]["filter"] >= 1, (
        "a two-hop chain must be exercised as both a grouping and a filter"
    )


def test_the_bench_reports_an_inner_join_regression(lake, monkeypatch) -> None:  # noqa: ANN001
    """The defect a conservation check alone cannot see: rows quietly lost.

    Order 4 points at a customer that is not there. A left join keeps it under
    a NULL label; an inner join drops it and every total is short by 7.
    """
    from ontology import Ontology

    c, manifest = lake
    real = Ontology._join_chain

    def inner(self, path, aliases, joins, notes, *, root="f"):  # noqa: ANN001, ANN202
        before = len(joins)
        out = real(self, path, aliases, joins, notes, root=root)
        for k in range(before, len(joins)):
            joins[k] = joins[k].replace("LEFT JOIN", "JOIN", 1)
        return out

    monkeypatch.setattr(Ontology, "_join_chain", inner)
    r = bench.generate_and_run(c, manifest, max_cases=200)
    assert r["failed"] > 0, "an inner join dropped a fact row and the bench said nothing"
    assert any("None" in f["detail"] or "differ" in f["detail"] for f in r["failures"])


def test_a_value_matching_no_dimension_row_is_a_known_gap_not_a_pass(lake) -> None:  # noqa: ANN001
    """A NULL sum from a no-match filter looks exactly like a true zero.

    Counting it as a pass would inflate the denominator of the error bound with
    cases that proved nothing.
    """
    c, manifest = lake
    r = bench.generate_and_run(c, manifest, max_cases=200)
    assert r["known_gaps"] > 0, "no no-match probe was generated"
    assert r["known_gap_details"], "a gap counted but never named is not a gap"
    assert r["cases"] == (
        r["passed"] + r["failed"] + r["known_gaps"] + r["refused_ok"] + r["refusal_failures"]
    ), "cases must account for every outcome exactly once"


def test_the_corrupted_link_self_check_detects(lake) -> None:  # noqa: ANN001
    """The bench's oracle reads the same manifest the compiler does.

    So the bench corrupts a join column itself and confirms it notices. If that
    self-check ever stops detecting, the agreement between the two legs means
    less than it appears to, and the run says so.
    """
    c, manifest = lake
    r = bench.generate_and_run(c, manifest, max_cases=200)
    sc = r.get("self_check")
    assert sc, "the bench ran no corrupted-link self-check"
    assert sc.get("caught") is True, sc
    assert sc.get("oracle_leg") == "disagreed", sc


def test_shapes_are_counted_apart_from_cases(lake) -> None:  # noqa: ANN001
    """Five near-duplicate cases over one link are not five independent trials.

    The rule of three on cases flatters the bound; the honest denominator is
    distinct shapes, so both are reported and shapes is never larger.
    """
    c, manifest = lake
    r = bench.generate_and_run(c, manifest, max_cases=200)
    assert r["n_shapes"] >= 1
    assert r["n_shapes"] <= r["cases"], "shapes can never exceed cases"


def test_skipped_pairs_are_counted_with_a_reason(lake) -> None:  # noqa: ANN001
    """Silent truncation reads as full coverage. Say what was not sampled."""
    c, manifest = lake
    r = bench.generate_and_run(c, manifest, max_cases=200)
    pairs = r["pairs"]
    assert pairs["used"] + len(pairs["skipped"]) == pairs["total"]
    assert isinstance(pairs["skipped_counts"], dict)
    for skip in pairs["skipped"]:
        assert skip["reason"], "a skipped pair must say why it was skipped"
