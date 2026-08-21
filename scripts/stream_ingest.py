"""Incremental ingestion where a half-loaded slice is never queryable.

Why this exists
---------------
Batch scheduling and live streaming are the same problem at two clock speeds:
read the rows that arrived since last time, land them, and do not lose or
duplicate any at the boundary. The interesting part is not the loop. It is that
a load which fails halfway leaves a table that still answers questions.

That is the same failure class as everything else here. A metric computed over
a table that is 60 percent loaded returns a number, under a green badge, that no
amount of downstream checking can identify as wrong - the rows that would have
corrected it are simply absent. The conservation identity cannot see it. E9
cannot see it. The oracle cannot see it, because the oracle reads the same
half-loaded table.

So visibility is the control. A batch lands in a staging area, is asserted
against its own declaration, and only then is made visible by a single atomic
manifest write. Until that write, queries read the previous complete state.
There is no moment at which a reader can observe a partial slice. This is what
Delta's atomic commit buys, expressed in the smallest form that actually holds.

Watermark semantics, stated because getting them wrong is silent
---------------------------------------------------------------
Every batch covers a half-open interval ``(low, high]`` - exclusive at the low
end, inclusive at the high end. Both ends are stored. The two classic defects
are exactly the two ways to get this wrong:

  ``[low, high]`` on both ends re-reads the boundary row every run, so a
  duplicate appears in the fact table and every SUM over it is too big.
  ``(low, high)`` on both ends skips the boundary row forever, so a total is
  quietly too small and nothing ever notices.

Ties at the boundary are the reason the interval is not enough on its own: if
two rows share the maximum watermark value, advancing to that value is correct
only because the interval is inclusive at the high end and the next batch starts
strictly above it. The tests assert that a tied boundary neither duplicates nor
drops.

The sweep: identity is primary, the interval is only a hint
-----------------------------------------------------------
Deciding membership by watermark arithmetic alone loses rows, and adversarial
review found four separate ways it did. All four were one defect: the interval
answered "which rows are mine", and the safety net only ran as a side effect of
a batch committing.

  * A NULL watermark vanished entirely. Every interval predicate is
    three-valued, so ``wm > low``, ``wm <= high`` and ``MAX(wm)`` all skipped it,
    and so did the late-data scan. A source could be 30 percent short with a
    clean exit and a late count of zero.
  * A row arriving below a committed mark during a quiet period was invisible
    until unrelated traffic happened to arrive - the same row reported or not
    depending on something else entirely.
  * A row written at exactly a committed high mark was never covered, because
    the next interval starts strictly above it.
  * A rejected batch hid every row behind it, including legitimately new ones.

So after every drain - unconditionally, including when nothing committed and
including after a rejection - ``sweep_unclaimed`` asks a different question, by
identity rather than by arithmetic: which source rows are in no visible part and
no quarantine part? Those are written out and counted. Never silently dropped,
because that is a total quietly too small; never silently merged, because that
is a number nobody asked to have changed (R-0011). Excluding rows already
quarantined is what stops the same row being re-counted on every later batch.

  python scripts/stream_ingest.py --demo
  python scripts/stream_ingest.py --status --source orders --lake data/stream

The library entry points are ``run()`` and ``ingest_batch()``; a scheduler or a
follow loop drives those. The CLI deliberately exposes only the demonstration
and a read-only status report, because an ingest CLI that can be pointed at a
production lake by a slip of the shell is a liability, not a convenience.

Exit 0 only when every committed batch passed its assertions.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAKE = ROOT / "data" / "stream"


class BatchRejected(Exception):
    """A batch failed an assertion, so it was never made visible."""


@dataclass
class SourceSpec:
    """What to read, how to order it, and what identifies a row.

    ``key`` is what makes a re-run idempotent, and ``watermark`` is what makes it
    incremental. A source with no key can still be ingested, but nothing can then
    tell a duplicate from a legitimate repeat, so the key is required.
    """

    name: str
    relation: str
    key: tuple[str, ...]
    watermark: str


@dataclass
class Batch:
    batch_id: int
    source: str
    low: Any
    high: Any
    rows: int
    part: str
    state: str = "committed"
    late_rows: int = 0
    late_part: str | None = None


@dataclass
class Ledger:
    """Every batch that was ever committed, and nothing that was not.

    A batch appears here only after its part file is written and asserted. The
    ledger write is the commit: readers resolve the visible set from this file,
    so a crash before the write leaves the previous complete state intact and a
    crash after it leaves a slice that has already passed its checks.
    """

    path: Path
    batches: list[Batch] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Ledger:
        if not path.is_file():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(path=path, batches=[Batch(**b) for b in raw.get("batches", [])])

    def commit(self, batch: Batch) -> None:
        """Atomic by rename. A torn ledger would be worse than a missing batch.

        The high mark is normalised to text before it is stored. ``json.dumps``
        with ``default=str`` was a one-way cast: a TIMESTAMP or DECIMAL mark went
        out as a string and came back as one, so the next run compared a str from
        the ledger against a datetime from DuckDB and raised TypeError inside
        ``max()``. Storing one type means DuckDB does the comparison, which it
        can do because ``_lit()`` quotes the value and the column type drives the
        cast. The demo only ever used an INTEGER watermark, which is why this was
        green.
        """
        batch = replace(
            batch,
            high=None if batch.high is None else str(batch.high),
            low=None if batch.low is None else str(batch.low),
        )
        self.batches.append(batch)
        tmp = self.path.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps({"batches": [asdict(b) for b in self.batches]}, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def high_watermark(self, source: str) -> Any:
        """The newest committed mark, as text.

        Ordering is by batch order, not by ``max()``: marks are text now, and
        text ordering is not value ordering for a DECIMAL ('5.0' > '10.0') or for
        any format that is not zero-padded. The last committed batch is the
        newest by construction, because a batch only commits after asserting
        that every row it carries lies inside its own interval.
        """
        marks = [b.high for b in self.batches if b.source == source]
        return marks[-1] if marks else None

    def visible_parts(self, source: str) -> list[str]:
        return [b.part for b in self.batches if b.source == source and b.state == "committed"]

    def rows(self, source: str) -> int:
        return sum(b.rows for b in self.batches if b.source == source)

    def quarantine_parts(self, source: str) -> list[str]:
        return [
            b.late_part
            for b in self.batches
            if b.source == source and b.late_part
        ]

    def record_sweep(self, source: str, rows: int, part: str | None) -> None:
        """A sweep result belongs in the ledger even when no batch committed."""
        self.commit(
            Batch(
                batch_id=len(self.batches) + 1,
                source=source,
                low=None,
                high=self.high_watermark(source),
                rows=0,
                part="",
                state="sweep",
                late_rows=rows,
                late_part=part,
            )
        )


def _lit(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _key_match(left: str, right: str, key: tuple[str, ...]) -> str:
    """Join two rows on their key, treating NULL as a value.

    The two duplicate checks disagreed about NULL. ``COUNT(DISTINCT (k))`` skips
    NULL rows, so a batch with one NULL key looked like it had a duplicate and
    was rejected with a message about duplicates it did not have. The
    cross-batch check used ``o.k = n.k``, which is never TRUE for NULL, so a
    re-delivered NULL-keyed row sailed past it and double-counted. One predicate
    now serves both, and NULL keys get their own check with their own message.
    """
    return " AND ".join(
        f"{left}.{_ident(c)} IS NOT DISTINCT FROM {right}.{_ident(c)}" for c in key
    )


def _parts_relation(parts: list[str]) -> str | None:
    if not parts:
        return None
    listed = ", ".join("'" + p.replace("'", "''") + "'" for p in parts)
    return f"read_parquet([{listed}])"


def ingest_batch(
    con: Any,
    spec: SourceSpec,
    ledger: Ledger,
    lake: Path,
    *,
    batch_size: int,
) -> Batch | None:
    """Read one batch, assert it, and only then make it visible.

    Returns None when there is nothing pending. Raises BatchRejected when an
    assertion fails, having written nothing to the ledger - the previous state
    stays the visible one.
    """
    if int(batch_size) < 1:
        raise ValueError(f"batch_size must be at least 1, got {batch_size!r}")
    low = ledger.high_watermark(spec.name)
    wm = _ident(spec.watermark)
    where = "" if low is None else f"WHERE {wm} > {_lit(low)}"

    # The candidate high mark. Taking it before reading, and then reading with
    # `<=` it, is what makes the batch a closed interval rather than "whatever
    # happened to be there when the SELECT ran". A row inserted mid-batch lands
    # in the next interval instead of half in this one.
    picked = con.execute(
        f"SELECT {wm} FROM {spec.relation} {where} "
        f"ORDER BY {wm} LIMIT 1 OFFSET {int(batch_size) - 1}"
    ).fetchone()
    high = (
        picked[0]
        if picked
        else con.execute(f"SELECT MAX({wm}) FROM {spec.relation} {where}").fetchone()[0]
    )
    if high is None:
        return None

    span = f"{wm} <= {_lit(high)}" if low is None else (
        f"{wm} > {_lit(low)} AND {wm} <= {_lit(high)}"
    )
    expected = con.execute(
        f"SELECT COUNT(*) FROM {spec.relation} WHERE {span}"
    ).fetchone()[0]
    if not expected:
        return None

    batch_id = len(ledger.batches) + 1
    staging = lake / spec.name / "_staging"
    staging.mkdir(parents=True, exist_ok=True)
    part = staging / f"part-{batch_id:06d}.parquet"

    con.execute(
        f"COPY (SELECT * FROM {spec.relation} WHERE {span}) "
        f"TO '{part.as_posix()}' (FORMAT PARQUET)"
    )
    _assert_batch(con, spec, ledger, part, low=low, high=high, expected=int(expected))

    # Visible only now. Moving the part out of _staging before the ledger write
    # would expose it to a reader that resolves by directory glob; the ledger is
    # the authority, but the layout should not contradict it either.
    final_dir = lake / spec.name
    final = final_dir / part.name
    part.replace(final)

    batch = Batch(
        batch_id=batch_id,
        source=spec.name,
        low=low,
        high=high,
        rows=int(expected),
        part=final.as_posix(),
    )
    ledger.commit(batch)
    return batch


def _assert_batch(
    con: Any,
    spec: SourceSpec,
    ledger: Ledger,
    part: Path,
    *,
    low: Any,
    high: Any,
    expected: int,
) -> None:
    """Everything that must hold before a reader is allowed to see these rows."""
    rel = f"read_parquet('{part.as_posix()}')"
    keycols = ", ".join(_ident(c) for c in spec.key)
    wm = _ident(spec.watermark)

    null_keys = con.execute(
        f"SELECT COUNT(*) FROM {rel} WHERE "
        + " OR ".join(f"{_ident(c)} IS NULL" for c in spec.key)
    ).fetchone()[0]
    if int(null_keys):
        raise BatchRejected(
            f"{spec.name} batch: {int(null_keys):,} rows have a NULL in the key "
            f"({', '.join(spec.key)}), so identity cannot be established for them "
            "and no later run could tell a redelivery from a new row."
        )
    landed, distinct_keys = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT ({keycols})) FROM {rel}"
    ).fetchone()
    if int(landed) != expected:
        raise BatchRejected(
            f"{spec.name} batch: read {expected:,} rows from the source and landed "
            f"{int(landed):,}. A short write must never become visible."
        )
    if int(landed) != int(distinct_keys):
        raise BatchRejected(
            f"{spec.name} batch: {int(landed) - int(distinct_keys):,} duplicate keys "
            f"({', '.join(spec.key)}) inside one batch. Committing it would make every "
            "SUM over this source too big, with nothing downstream able to tell."
        )

    outside = con.execute(
        f"SELECT COUNT(*) FROM {rel} WHERE {wm} > {_lit(high)}"
        + ("" if low is None else f" OR {wm} <= {_lit(low)}")
    ).fetchone()[0]
    if int(outside):
        raise BatchRejected(
            f"{spec.name} batch: {int(outside):,} rows fall outside the declared "
            f"interval ({low!r}, {high!r}]. The watermark would advance past rows it "
            "does not cover."
        )

    already = _parts_relation(ledger.visible_parts(spec.name))
    if already:
        collisions = con.execute(
            f"SELECT COUNT(*) FROM {rel} n WHERE EXISTS (SELECT 1 FROM {already} o "
            f"WHERE {_key_match('o', 'n', spec.key)})"
        ).fetchone()[0]
        if int(collisions):
            raise BatchRejected(
                f"{spec.name} batch: {int(collisions):,} keys are already visible from an "
                "earlier batch. Re-ingesting them would double their contribution."
            )


def sweep_unclaimed(
    con: Any, spec: SourceSpec, ledger: Ledger, lake: Path
) -> tuple[int, str | None]:
    """Every source row no committed batch ever claimed, found by identity.

    This replaces four separate symptoms with one answer, because they were one
    defect (R-0004). Watermark arithmetic decided membership, and the safety net
    only ran as a side effect of a batch committing, so a row was silently lost
    when:

      * its watermark was NULL - every interval predicate is three-valued, so
        ``wm > low``, ``wm <= high`` and ``MAX(wm)`` all skipped it, and so did
        the late-data scan. Thirty percent of a source could vanish with a clean
        exit and ``late_rows: 0``;
      * it arrived below a committed mark during a quiet tick - the scan lived
        inside ``ingest_batch``, so an idle run swept nothing and the row was
        invisible until some unrelated row happened to arrive;
      * it shared the exact high mark of a committed batch but was written after
        that batch closed - the next interval starts strictly above the mark, so
        nothing would ever cover it.

    Identity does not have those holes: a row is claimed or it is not. Running
    unconditionally at the end of every drain means an idle tick still sweeps,
    and excluding rows already quarantined means each one is counted once rather
    than re-counted and re-written on every later batch.
    """
    visible = _parts_relation(ledger.visible_parts(spec.name))
    if visible is None:
        return 0, None
    claimed = [visible]
    quarantined = _parts_relation(ledger.quarantine_parts(spec.name))
    if quarantined:
        claimed.append(quarantined)

    not_claimed = " AND ".join(
        f"NOT EXISTS (SELECT 1 FROM {rel} v WHERE {_key_match('v', 's', spec.key)})"
        for rel in claimed
    )
    pending = f"SELECT s.* FROM {spec.relation} s WHERE {not_claimed}"
    n = con.execute(f"SELECT COUNT(*) FROM ({pending})").fetchone()[0]
    if not int(n):
        return 0, None
    qdir = lake / spec.name / "_unclaimed"
    qdir.mkdir(parents=True, exist_ok=True)
    part = qdir / f"unclaimed-{len(ledger.batches) + 1:06d}.parquet"
    con.execute(f"COPY ({pending}) TO '{part.as_posix()}' (FORMAT PARQUET)")
    return int(n), part.as_posix()


def quarantine_late(
    con: Any, spec: SourceSpec, ledger: Ledger, lake: Path
) -> tuple[int, str | None]:
    """Rows below a committed mark that no batch ever covered.

    Late data is defined by identity, not by arithmetic: a source row whose
    watermark sits at or below the high mark and whose KEY is not in any visible
    part was never ingested and never will be, because the interval that would
    have covered it is already closed.

    An earlier version compared row counts against the committed total, which is
    a proxy that goes wrong the moment anything is deleted upstream. Counting is
    not identity - R-0004, fixed where the answer is derived rather than where it
    was first noticed.

    They are written to a quarantine part and counted. Never silently dropped,
    because that is a total that is quietly too small; never silently merged,
    because that changes a number nobody asked to have changed (R-0011).
    """
    high = ledger.high_watermark(spec.name)
    if high is None:
        return 0, None
    visible = _parts_relation(ledger.visible_parts(spec.name))
    if visible is None:
        return 0, None
    wm = _ident(spec.watermark)
    on = " AND ".join(f"v.{_ident(c)} = s.{_ident(c)}" for c in spec.key)
    pending = (
        f"SELECT s.* FROM {spec.relation} s WHERE s.{wm} <= {_lit(high)} "
        f"AND NOT EXISTS (SELECT 1 FROM {visible} v WHERE {on})"
    )
    n = con.execute(f"SELECT COUNT(*) FROM ({pending})").fetchone()[0]
    if not int(n):
        return 0, None
    qdir = lake / spec.name / "_late"
    qdir.mkdir(parents=True, exist_ok=True)
    part = qdir / f"late-{len(ledger.batches) + 1:06d}.parquet"
    con.execute(f"COPY ({pending}) TO '{part.as_posix()}' (FORMAT PARQUET)")
    return int(n), part.as_posix()


def run(
    con: Any,
    spec: SourceSpec,
    lake: Path,
    *,
    batch_size: int,
    max_batches: int | None = None,
) -> list[Batch]:
    """Drain everything pending, one asserted batch at a time."""
    ledger = Ledger.load(lake / spec.name / "_ledger.json")
    done: list[Batch] = []
    rejection: BatchRejected | None = None
    while max_batches is None or len(done) < max_batches:
        try:
            batch = ingest_batch(con, spec, ledger, lake, batch_size=batch_size)
        except BatchRejected as exc:
            # A rejection stops this source's drain and is reported, rather than
            # escaping as a traceback. It must still fall through to the sweep:
            # the rows the rejected batch would have carried are unclaimed, and
            # the whole point of the sweep is that unclaimed rows get counted.
            rejection = exc
            break
        if batch is None:
            break
        done.append(batch)
        print(
            f"  batch {batch.batch_id:>4}  ({batch.low!r}, {batch.high!r}]  "
            f"{batch.rows:>6,} rows  committed"
        )

    # Unconditional. An idle tick with nothing pending must still sweep, because
    # a row can be unclaimed without anything new ever arriving.
    swept, part = sweep_unclaimed(con, spec, ledger, lake)
    if swept:
        ledger.record_sweep(spec.name, swept, part)
        print(f"  UNCLAIMED {swept:,} source rows no batch covered -> {part}")
        print("            counted, not dropped. A NULL watermark, a row written")
        print("            behind a closed interval, or a rejected batch's rows.")
    if rejection is not None:
        raise rejection
    return done


def visible_relation(lake: Path, source: str) -> str | None:
    """What a reader may query: committed parts only, resolved from the ledger."""
    ledger = Ledger.load(lake / source / "_ledger.json")
    return _parts_relation(ledger.visible_parts(source))


# --------------------------------------------------------------------------
# demo
# --------------------------------------------------------------------------


def run_demo(lake: Path) -> int:
    """Seed a throwaway lake and drain it. Destructive, and says so.

    It refuses to delete a directory it cannot recognise as its own. A demo that
    can be pointed at a real lake by a slip of the shell is a demo that will
    eventually be.
    """
    import shutil

    import duckdb

    if lake.exists():
        strangers = [
            p.name
            for p in lake.iterdir()
            if p.name not in {"orders", "_marker.demo"}
        ]
        if strangers:
            print(f"FAIL {lake} holds data this demo did not create: "
                  f"{', '.join(sorted(strangers)[:5])}")
            print("     Refusing to delete it. Point --lake somewhere disposable.")
            return 2
        shutil.rmtree(lake)
    lake.mkdir(parents=True, exist_ok=True)
    (lake / "_marker.demo").write_text("created by stream_ingest --demo", encoding="utf-8")
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE orders AS SELECT i AS order_id, "
        "'C' || (i % 7) AS customer, (i * 1.5) AS amount, i AS seq "
        "FROM range(1, 1001) t(i)"
    )
    spec = SourceSpec("orders", "orders", ("order_id",), "seq")

    print("=== STREAM INGEST (batch of 300 over 1,000 rows) ===")
    run(con, spec, lake, batch_size=300)

    rel = visible_relation(lake, "orders")
    got = con.execute(f"SELECT COUNT(*), ROUND(SUM(amount), 2) FROM {rel}").fetchone()
    truth = con.execute("SELECT COUNT(*), ROUND(SUM(amount), 2) FROM orders").fetchone()
    print(f"  visible: {got[0]:,} rows, total {got[1]:,.2f}")
    print(f"  source : {truth[0]:,} rows, total {truth[1]:,.2f}")
    if got != truth:
        print("FAIL the ingested lake does not equal the source")
        return 1

    print("\n=== a second run with nothing new ===")
    again = run(con, spec, lake, batch_size=300)
    print(f"  {len(again)} batches - an idle scheduler tick must be a no-op")
    if again:
        print("FAIL re-running duplicated work")
        return 1

    print("\n=== 250 more rows arrive ===")
    con.execute(
        "INSERT INTO orders SELECT i, 'C' || (i % 7), i * 1.5, i "
        "FROM range(1001, 1251) t(i)"
    )
    run(con, spec, lake, batch_size=300)
    rel = visible_relation(lake, "orders")
    got = con.execute(f"SELECT COUNT(*), ROUND(SUM(amount), 2) FROM {rel}").fetchone()
    truth = con.execute("SELECT COUNT(*), ROUND(SUM(amount), 2) FROM orders").fetchone()
    print(f"  visible: {got[0]:,} rows, total {got[1]:,.2f}")
    print(f"  source : {truth[0]:,} rows, total {truth[1]:,.2f}")
    if got != truth:
        print("FAIL incremental load lost or duplicated rows at the boundary")
        return 1

    print("\nPASS every batch was asserted before it became visible, and the")
    print("     incremental total equals the source exactly.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--demo", action="store_true", help="run the built-in demonstration")
    ap.add_argument("--lake", type=Path, default=DEFAULT_LAKE)
    ap.add_argument("--status", action="store_true", help="print the ledger and stop")
    ap.add_argument("--source", help="source name to report on with --status")
    args = ap.parse_args(argv)

    if args.status:
        if not args.source:
            ap.error("--status needs --source")
        ledger = Ledger.load(args.lake / args.source / "_ledger.json")
        print(f"  {len(ledger.batches)} committed batches, "
              f"{ledger.rows(args.source):,} rows, "
              f"high watermark {ledger.high_watermark(args.source)!r}")
        for b in ledger.batches[-10:]:
            print(f"    {b.batch_id:>4}  ({b.low!r}, {b.high!r}]  {b.rows:>6,} rows")
        return 0
    if args.demo:
        return run_demo(args.lake)
    # Falling through to run_demo here was a data-loss defect, not a usability
    # one: run_demo rmtree's --lake before seeding, so any invocation that was
    # not --demo or --status deleted the lake it was pointed at and exited 0.
    # An unrecognised invocation must do nothing at all.
    ap.error(
        "nothing to do. Use --demo to run the demonstration (which REPLACES --lake), "
        "or --status --source <name> to report on an existing lake."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
