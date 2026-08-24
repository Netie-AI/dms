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
    sweep_unclaimed,  # noqa: F401
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
    # Marks are stored as text so the ledger only ever holds one type - see
    # Ledger.commit. DuckDB casts the literal back against the column type.
    assert batches[0].low is None and str(batches[0].high) == "40"
    assert str(batches[1].low) == "40" and str(batches[1].high) == "80"
    ledger = Ledger.load(tmp_path / "orders" / "_ledger.json")
    assert ledger.high_watermark("orders") == "100"


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
    committed = [b for b in ledger.batches if b.state == "committed"]
    assert len(committed) == 1
    assert ledger.high_watermark("orders") == "10", (
        "the watermark advanced over a rejected batch"
    )


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


# --------------------------------------------------------------------------
# the sweep: identity is primary, the interval is only a batching hint
# --------------------------------------------------------------------------


def _unclaimed(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "orders" / "_unclaimed").glob("*.parquet"))


def test_a_null_watermark_row_is_swept_not_silently_dropped(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """Every interval predicate is three-valued, so a NULL watermark vanished.

    `wm > low`, `wm <= high` and `MAX(wm)` all skip NULL, and so did the old
    late-data scan. Three of ten rows disappeared with a clean exit and
    late_rows 0 - a total 30 percent short, which is exactly the failure this
    module's docstring claims cannot happen.
    """
    con.execute("CREATE OR REPLACE TABLE orders (order_id INTEGER, seq INTEGER, amount DOUBLE)")
    for i in range(1, 8):
        con.execute("INSERT INTO orders VALUES (?, ?, 100.0)", [i, i])
    for i in range(8, 11):
        con.execute("INSERT INTO orders VALUES (?, NULL, 100.0)", [i])

    run(con, SPEC, tmp_path, batch_size=4)
    parts = _unclaimed(tmp_path)
    assert parts, "rows with a NULL watermark vanished with no trace"
    swept = con.execute(
        f"SELECT order_id FROM read_parquet('{parts[0].as_posix()}') ORDER BY order_id"
    ).fetchall()
    assert swept == [(8,), (9,), (10,)]


def test_the_sweep_runs_on_an_idle_tick(con, tmp_path: Path) -> None:  # noqa: ANN001
    """A skewed row was invisible until unrelated traffic happened to arrive.

    The scan lived inside ingest_batch, so a quiet period meant no sweep. The
    same row was invisible in one tick and reported in the next, and the only
    difference was that something else showed up.
    """
    _seed(con, [(i, i * 10, 1.0) for i in range(1, 11)])
    run(con, SPEC, tmp_path, batch_size=100)
    con.execute("INSERT INTO orders VALUES (99, 5, 1.0)")

    assert run(con, SPEC, tmp_path, batch_size=100) == [], "no batch should commit"
    parts = _unclaimed(tmp_path)
    assert parts, "an idle tick swept nothing"
    assert con.execute(
        f"SELECT order_id FROM read_parquet('{parts[0].as_posix()}')"
    ).fetchall() == [(99,)]


def test_a_row_tied_with_a_closed_high_mark_is_swept(con, tmp_path: Path) -> None:  # noqa: ANN001
    """The next interval starts strictly above the mark, so nothing covers it."""
    _seed(con, [(i, i, 1.0) for i in range(1, 11)])
    run(con, SPEC, tmp_path, batch_size=100)
    con.execute("INSERT INTO orders VALUES (77, 10, 1.0)")
    run(con, SPEC, tmp_path, batch_size=100)
    parts = _unclaimed(tmp_path)
    assert parts
    assert con.execute(
        f"SELECT order_id FROM read_parquet('{parts[0].as_posix()}')"
    ).fetchall() == [(77,)]


def test_a_swept_row_is_counted_once_not_on_every_later_batch(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """One late row was re-counted and re-written by every subsequent batch."""
    _seed(con, [(i, i * 10, 1.0) for i in range(1, 6)])
    run(con, SPEC, tmp_path, batch_size=100)
    con.execute("INSERT INTO orders VALUES (99, 5, 1.0)")
    run(con, SPEC, tmp_path, batch_size=100)

    for wave in range(2):
        con.execute(
            "INSERT INTO orders VALUES (?, ?, 1.0)", [200 + wave, 100 + wave * 10]
        )
        run(con, SPEC, tmp_path, batch_size=100)

    ledger = Ledger.load(tmp_path / "orders" / "_ledger.json")
    assert sum(b.late_rows for b in ledger.batches) == 1, "the same row swept repeatedly"


def test_a_timestamp_watermark_survives_the_ledger_round_trip(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """The normal streaming watermark, and it crashed on the second run.

    json.dumps(default=str) was a one-way cast, so a reloaded mark was a str
    while a fresh one was a datetime, and max() over the pair raised TypeError.
    The demo only used INTEGER, which is why this was green.
    """
    con.execute(
        "CREATE TABLE ev AS SELECT i AS id, "
        "TIMESTAMP '2026-01-01 00:00:00' + INTERVAL (i) SECOND AS ts, 1.0 AS amt "
        "FROM range(1, 26) t(i)"
    )
    spec = SourceSpec("ev", "ev", ("id",), "ts")
    run(con, spec, tmp_path, batch_size=10)
    con.execute(
        "INSERT INTO ev SELECT i, TIMESTAMP '2026-01-01 00:00:00' + INTERVAL (i) SECOND, 1.0 "
        "FROM range(26, 41) t(i)"
    )
    run(con, spec, tmp_path, batch_size=10)

    rel = visible_relation(tmp_path, "ev")
    got = con.execute(f"SELECT COUNT(*), SUM(amt) FROM {rel}").fetchone()
    want = con.execute("SELECT COUNT(*), SUM(amt) FROM ev").fetchone()
    assert got == want


def test_the_ledger_holds_exactly_one_type_for_a_mark(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    _seed(con, [(i, i, 1.0) for i in range(1, 11)])
    run(con, SPEC, tmp_path, batch_size=3)
    ledger = Ledger.load(tmp_path / "orders" / "_ledger.json")
    kinds = {type(b.high).__name__ for b in ledger.batches if b.high is not None}
    assert kinds == {"str"}, f"mixed mark types in the ledger: {kinds}"


def test_a_null_key_is_rejected_naming_the_null_not_a_duplicate(
    con,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """COUNT(DISTINCT) skips NULL, so one NULL key looked like a duplicate.

    The two duplicate checks disagreed about NULL: the in-batch one treated it
    as absent (so a lone NULL read as a duplicate), the cross-batch one used
    equality (so a redelivered NULL key sailed past and double-counted).
    """
    _seed(con, [(i, i, 1.0) for i in range(1, 10)])
    con.execute("INSERT INTO orders VALUES (NULL, 10, 1.0)")
    with pytest.raises(BatchRejected) as exc:
        run(con, SPEC, tmp_path, batch_size=100)
    assert "NULL in the key" in str(exc.value)
    assert "duplicate" not in str(exc.value)


def test_a_batch_size_below_one_is_refused(con, tmp_path: Path) -> None:  # noqa: ANN001
    """OFFSET batch_size-1 went negative and DuckDB raised a binder error."""
    _seed(con, [(1, 1, 1.0)])
    with pytest.raises(ValueError) as exc:
        run(con, SPEC, tmp_path, batch_size=0)
    assert "at least 1" in str(exc.value)


def test_a_rejected_batch_still_gets_its_rows_swept(con, tmp_path: Path) -> None:  # noqa: ANN001
    """A rejection must not also make the blocked rows invisible.

    The rows a rejected batch would have carried are unclaimed by definition, so
    the sweep is exactly what should notice them.
    """
    _seed(con, [(i, i, 1.0) for i in range(1, 6)])
    run(con, SPEC, tmp_path, batch_size=100)
    con.execute("INSERT INTO orders VALUES (3, 50, 1.0)")
    con.execute("INSERT INTO orders VALUES (6, 51, 1.0)")
    with pytest.raises(BatchRejected):
        run(con, SPEC, tmp_path, batch_size=100)
    parts = _unclaimed(tmp_path)
    assert parts, "a rejection hid the rows it blocked"
    ids = {
        r[0]
        for r in con.execute(
            f"SELECT order_id FROM read_parquet('{parts[0].as_posix()}')"
        ).fetchall()
    }
    assert 6 in ids, "the legitimately new row was never reported"
