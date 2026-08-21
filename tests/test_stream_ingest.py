"""Incremental loading must not lose a row, duplicate one, or ever show a partial slice.

The three failure modes, and why each is worse than it looks:

  * a dropped boundary row makes a total quietly too small, forever, and no
    downstream check can see it - the rows that would correct the number are
    simply absent;
  * a duplicated boundary row makes every SUM too big, and it looks exactly like
    growth;
  * a partial slice becoming visible means a metric is computed over a table
    that is 60 percent loaded. The conservation identity, E9 and the free-form
    oracle all read the same half-loaded table, so all three agree on a wrong
    number.

Every fixture is built in the test - no warehouse, no SQL Server, no Docker - so
this runs anywhere and never skips (R-0002).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from stream_ingest import (  # noqa: E402
    BatchRejected,
    Ledger,
    SourceSpec,
    ingest_batch,
    quarantine_late,
    run,
    visible_relation,
)


@pytest.fixture()
def con():  # noqa: ANN201
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        yield c
    finally:
        c.close()


SPEC = SourceSpec("orders", "orders", ("order_id",), "seq")


def _seed(con, rows: list[tuple[int, int, float]]) -> None:  # noqa: ANN001
    con.execute("CREATE OR REPLACE TABLE orders (order_id INTEGER, seq INTEGER, amount DOUBLE)")
    for r in rows:
        con.execute("INSERT INTO orders VALUES (?, ?, ?)", list(r))


def _visible(con, lake: Path) -> tuple[int, float]:  # noqa: ANN001
    rel = visible_relation(lake, "orders")
    if rel is None:
        return 0, 0.0
    n, total = con.execute(f"SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM {rel}").fetchone()
    return int(n), float(total)


# --------------------------------------------------------------------------
# the boundary
# --------------------------------------------------------------------------


def test_incremental_load_equals_the_source_exactly(con, tmp_path: Path) -> None:  # noqa: ANN001
    _seed(con, [(i, i, float(i)) for i in range(1, 51)])
    run(con, SPEC, tmp_path, batch_size=7)
    assert _visible(con, tmp_path) == (50, 1275.0)

    con.execute("INSERT INTO orders SELECT i, i, i FROM range(51, 71) t(i)")
    run(con, SPEC, tmp_path, batch_size=7)
    assert _visible(con, tmp_path) == (70, 2485.0), "the second wave must add exactly its rows"


def test_a_tied_watermark_at_the_boundary_neither_duplicates_nor_drops(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """Five rows share seq=3, straddling a batch edge of 4.

    A batch that cut mid-value would either re-read the tied rows next time or
    skip them. The interval is closed at the high end and the next one starts
    strictly above it, so a value is never split across two batches.
    """
    rows = [(1, 1, 1.0), (2, 2, 1.0)] + [(i, 3, 1.0) for i in range(3, 8)] + [(8, 4, 1.0)]
    _seed(con, rows)
    batches = run(con, SPEC, tmp_path, batch_size=4)
    assert _visible(con, tmp_path) == (8, 8.0)
    covered = [(b.low, b.high, b.rows) for b in batches]
    assert sum(r for _, _, r in covered) == 8, f"rows double counted or lost: {covered}"
    highs = [b.high for b in batches]
    assert highs == sorted(set(highs)), "a watermark value was split across batches"


def test_a_batch_may_exceed_its_size_rather_than_split_a_watermark_value(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """Correctness beats the size hint. Ten rows share one seq and go together."""
    _seed(con, [(i, 1, 1.0) for i in range(1, 11)])
    batches = run(con, SPEC, tmp_path, batch_size=3)
    assert len(batches) == 1
    assert batches[0].rows == 10
    assert _visible(con, tmp_path) == (10, 10.0)


def test_re_running_with_nothing_new_is_a_no_op(con, tmp_path: Path) -> None:  # noqa: ANN001
    """An idle scheduler tick must not move anything."""
    _seed(con, [(i, i, 1.0) for i in range(1, 21)])
    run(con, SPEC, tmp_path, batch_size=5)
    before = _visible(con, tmp_path)
    assert run(con, SPEC, tmp_path, batch_size=5) == []
    assert _visible(con, tmp_path) == before


def test_the_watermark_advances_only_over_rows_the_batch_covered(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    _seed(con, [(i, i * 10, 1.0) for i in range(1, 11)])
    batches = run(con, SPEC, tmp_path, batch_size=4)
    assert batches[0].low is None and batches[0].high == 40
    assert batches[1].low == 40 and batches[1].high == 80
    ledger = Ledger.load(tmp_path / "orders" / "_ledger.json")
    assert ledger.high_watermark("orders") == 100


# --------------------------------------------------------------------------
# nothing partial is ever visible
# --------------------------------------------------------------------------


def test_a_duplicate_key_inside_one_batch_is_rejected(con, tmp_path: Path) -> None:  # noqa: ANN001
    _seed(con, [(1, 1, 5.0), (1, 2, 5.0), (2, 3, 5.0)])
    with pytest.raises(BatchRejected) as exc:
        run(con, SPEC, tmp_path, batch_size=10)
    assert "duplicate keys" in str(exc.value)
    assert "too big" in str(exc.value)


def test_a_key_already_visible_is_rejected_rather_than_double_counted(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """The shape a naive re-ingest takes: same order, higher watermark."""
    _seed(con, [(1, 1, 5.0), (2, 2, 5.0)])
    run(con, SPEC, tmp_path, batch_size=10)
    con.execute("INSERT INTO orders VALUES (1, 99, 5.0)")
    with pytest.raises(BatchRejected) as exc:
        run(con, SPEC, tmp_path, batch_size=10)
    assert "already visible" in str(exc.value)


def test_a_rejected_batch_leaves_the_previous_state_intact(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """The whole point. A failed load must not degrade what was already correct."""
    _seed(con, [(i, i, 2.0) for i in range(1, 11)])
    run(con, SPEC, tmp_path, batch_size=10)
    good = _visible(con, tmp_path)
    assert good == (10, 20.0)

    con.execute("INSERT INTO orders VALUES (3, 50, 2.0)")  # duplicate key, later seq
    with pytest.raises(BatchRejected):
        run(con, SPEC, tmp_path, batch_size=10)

    assert _visible(con, tmp_path) == good, "a rejected batch changed what readers see"
    ledger = Ledger.load(tmp_path / "orders" / "_ledger.json")
    assert len(ledger.batches) == 1
    assert ledger.high_watermark("orders") == 10, "the watermark advanced over a rejected batch"


def test_a_staged_part_is_not_visible_until_the_ledger_names_it(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """Visibility is resolved from the ledger, never from a directory listing.

    A part file sitting in the source directory that no committed batch names
    must be invisible - that is what makes the commit atomic.
    """
    _seed(con, [(i, i, 1.0) for i in range(1, 6)])
    run(con, SPEC, tmp_path, batch_size=10)
    assert _visible(con, tmp_path) == (5, 5.0)

    stray = tmp_path / "orders" / "part-999999.parquet"
    con.execute(
        f"COPY (SELECT 999 AS order_id, 999 AS seq, 1000.0 AS amount) "
        f"TO '{stray.as_posix()}' (FORMAT PARQUET)"
    )
    assert stray.is_file()
    assert _visible(con, tmp_path) == (5, 5.0), "an uncommitted part became visible"


def test_the_ledger_is_replaced_atomically(con, tmp_path: Path) -> None:  # noqa: ANN001
    """A torn ledger loses every batch, which is worse than losing one."""
    _seed(con, [(i, i, 1.0) for i in range(1, 21)])
    run(con, SPEC, tmp_path, batch_size=5)
    path = tmp_path / "orders" / "_ledger.json"
    assert path.is_file()
    assert not path.with_suffix(".tmp").exists(), "the temp ledger was left behind"
    assert len(Ledger.load(path).batches) == 4


# --------------------------------------------------------------------------
# late data
# --------------------------------------------------------------------------


def test_late_rows_are_quarantined_and_counted_not_dropped(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """A row that arrives below a closed interval can never be picked up.

    Silently dropping it makes the total quietly too small. It is written out
    and counted so the shortfall is visible (R-0011).
    """
    _seed(con, [(i, i * 10, 1.0) for i in range(1, 6)])
    run(con, SPEC, tmp_path, batch_size=10)
    assert _visible(con, tmp_path) == (5, 5.0)

    con.execute("INSERT INTO orders VALUES (99, 25, 1.0)")  # below the high mark of 50
    ledger = Ledger.load(tmp_path / "orders" / "_ledger.json")
    n, part = quarantine_late(con, SPEC, ledger, tmp_path)
    assert n == 1
    assert part and Path(part).is_file()
    late = con.execute(f"SELECT order_id FROM read_parquet('{part}')").fetchall()
    assert late == [(99,)]


def test_late_detection_is_by_identity_not_by_counting(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """Deleting a source row upstream must not manufacture a phantom late row.

    An earlier version compared row counts against the committed total, which
    reports late data whenever the source shrinks. Identity does not.
    """
    _seed(con, [(i, i * 10, 1.0) for i in range(1, 6)])
    run(con, SPEC, tmp_path, batch_size=10)
    con.execute("DELETE FROM orders WHERE order_id = 2")
    ledger = Ledger.load(tmp_path / "orders" / "_ledger.json")
    assert quarantine_late(con, SPEC, ledger, tmp_path) == (0, None)


def test_nothing_is_late_before_the_first_batch(con, tmp_path: Path) -> None:  # noqa: ANN001
    _seed(con, [(1, 1, 1.0)])
    ledger = Ledger.load(tmp_path / "orders" / "_ledger.json")
    assert quarantine_late(con, SPEC, ledger, tmp_path) == (0, None)


# --------------------------------------------------------------------------
# empty and edge sources
# --------------------------------------------------------------------------


def test_an_empty_source_produces_no_batch(con, tmp_path: Path) -> None:  # noqa: ANN001
    _seed(con, [])
    assert run(con, SPEC, tmp_path, batch_size=5) == []
    assert visible_relation(tmp_path, "orders") is None


def test_a_single_row_source_is_fully_ingested(con, tmp_path: Path) -> None:  # noqa: ANN001
    _seed(con, [(1, 1, 42.0)])
    assert len(run(con, SPEC, tmp_path, batch_size=100)) == 1
    assert _visible(con, tmp_path) == (1, 42.0)


def test_ingest_batch_returns_none_when_there_is_nothing_pending(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    _seed(con, [(1, 1, 1.0)])
    ledger = Ledger.load(tmp_path / "orders" / "_ledger.json")
    assert ingest_batch(con, SPEC, ledger, tmp_path, batch_size=5) is not None
    assert ingest_batch(con, SPEC, ledger, tmp_path, batch_size=5) is None


def test_many_small_batches_and_one_large_batch_agree(con, tmp_path: Path) -> None:  # noqa: ANN001
    """Batch size is a scheduling knob, never a correctness one."""
    rows = [(i, i, float(i)) for i in range(1, 101)]
    _seed(con, rows)
    run(con, SPEC, tmp_path, batch_size=3)
    small = _visible(con, tmp_path)

    other = tmp_path / "one-shot"
    _seed(con, rows)
    run(con, SPEC, other, batch_size=1000)
    big = con.execute(
        f"SELECT COUNT(*), SUM(amount) FROM {visible_relation(other, 'orders')}"
    ).fetchone()
    assert small == (100, 5050.0)
    assert (int(big[0]), float(big[1])) == small
