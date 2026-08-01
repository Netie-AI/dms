"""Every row that enters a promotion ends up in the target or in quarantine.

This is the question a warehouse exists to answer, and until now it could not
even be asked: ``PromoteReceipt`` carried no input count, so
``passed + quarantined == source_rows`` was not merely unasserted, it was
unwritable.

The defect it hides: multi-source promotion joined with ``INNER JOIN`` and the
result was the only input to staging, so unmatched rows vanished *upstream* of
both counters. A run of 1000 rows against 997 reported ``passed=997,
quarantined=0, counts_by_reason={}`` — three rows gone, no reason code, receipt
looking complete. That contradicts promote.py's own module docstring: "Rows that
fail the contract land in quarantine - never dropped."

Fan-out is the same defect wearing the opposite sign: a duplicated key on the
right multiplies rows, and every amount downstream with them.
"""

from __future__ import annotations

import duckdb
import pytest
from dms_core.pipelines import PromoteReceipt


def test_receipt_can_express_conservation() -> None:
    """The instrument itself. Without source_rows nothing below is checkable."""
    r = PromoteReceipt(
        run_id="r", target="silver.s", sources=["bronze.a"],
        source_rows=1000, passed=997, quarantined=3, unmatched=0,
    )
    assert r.reconciled
    assert r.to_dict()["source_rows"] == 1000
    assert r.to_dict()["reconciled"] is True


def test_the_1000_vs_997_case_is_not_reported_as_clean() -> None:
    """The exact shape the old code produced, now refused as unreconciled."""
    old_behaviour = PromoteReceipt(
        run_id="r", target="silver.s", sources=["bronze.a", "bronze.b"],
        source_rows=1000, passed=997, quarantined=0, unmatched=3,
    )
    assert not old_behaviour.reconciled, (
        "997 passed + 0 quarantined out of 1000 rows must never read as complete"
    )


def test_fan_out_is_also_unreconciled() -> None:
    """A duplicated join key produces MORE rows than it consumed."""
    fanned = PromoteReceipt(
        run_id="r", target="silver.s", sources=["bronze.a", "bronze.b"],
        source_rows=1000, passed=1400, quarantined=0, unmatched=-400,
    )
    assert not fanned.reconciled


def test_a_receipt_without_an_input_count_is_unreconciled_not_assumed_fine() -> None:
    """Absence of evidence is not evidence of conservation."""
    legacy = PromoteReceipt(
        run_id="r", target="silver.s", sources=["bronze.a"], passed=10, quarantined=0
    )
    assert legacy.source_rows is None
    assert not legacy.reconciled


# --------------------------------------------------------------- join semantics


def _join_counts(join_sql: str) -> tuple[int, int]:
    """(left_rows, joined_rows) for 1000 left rows against 997 right rows."""
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE a AS SELECT range AS k FROM range(1000)")
        con.execute("CREATE TABLE b AS SELECT range AS k FROM range(997)")
        left = con.execute("SELECT COUNT(*) FROM a").fetchone()[0]
        joined = con.execute(
            f"SELECT COUNT(*) FROM a {join_sql} b ON a.k = b.k"
        ).fetchone()[0]
        return int(left), int(joined)
    finally:
        con.close()


def test_inner_join_loses_rows_before_anything_can_count_them() -> None:
    """Characterises the old behaviour, so the fix below is demonstrably a fix."""
    left, joined = _join_counts("INNER JOIN")
    assert left == 1000
    assert joined == 997, "INNER JOIN silently drops the three unmatched rows"


def test_left_join_keeps_every_row_for_the_scorer_to_quarantine() -> None:
    """The fix: unmatched rows survive into staging and can be given a reason."""
    left, joined = _join_counts("LEFT JOIN")
    assert left == 1000
    assert joined == 1000, "every source row must reach the contract scorer"


def test_left_join_marks_which_rows_were_unmatched() -> None:
    """The marker the scorer turns into a `join_unmatched` quarantine reason."""
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE a AS SELECT range AS k FROM range(1000)")
        con.execute("CREATE TABLE b AS SELECT range AS k FROM range(997)")
        unmatched = con.execute(
            "SELECT COUNT(*) FROM (SELECT (b.k IS NOT NULL) AS m FROM a LEFT JOIN b ON a.k=b.k) "
            "WHERE NOT m"
        ).fetchone()[0]
        assert int(unmatched) == 3
    finally:
        con.close()


@pytest.mark.parametrize("dupes", [2, 5])
def test_fan_out_is_detectable_as_a_cardinality_change(dupes: int) -> None:
    """More rows out than in - caught by source_rows vs staged_rows."""
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE a AS SELECT range AS k FROM range(10)")
        con.execute(
            f"CREATE TABLE b AS SELECT range % 10 AS k FROM range({10 * dupes})"
        )
        src = int(con.execute("SELECT COUNT(*) FROM a").fetchone()[0])
        staged = int(
            con.execute("SELECT COUNT(*) FROM a LEFT JOIN b ON a.k=b.k").fetchone()[0]
        )
        assert staged == src * dupes
        assert src - staged < 0, "fan-out must show as a negative unmatched"
    finally:
        con.close()
