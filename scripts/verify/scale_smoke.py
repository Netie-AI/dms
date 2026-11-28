"""Governed-ask timings on a scratch DuckDB. Never the live warehouse file.

Default is 1,000,000 rows. CI passes a small ``--rows``. ``--self-check``
uses 200 rows and checks the harness, not a score.

DMS reseeds the demo tables the first time a process uses
``DMS_WAREHOUSE_DB`` and the path is not already marked seeded. This
harness marks the scratch file seeded after it builds the table. Product
code does not grow a way to skip that reseed.

  python scripts/verify/scale_smoke.py
  python scripts/verify/scale_smoke.py --rows 2000
  python scripts/verify/scale_smoke.py --self-check
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for rel in (
    "apps/api",
    "packages/core",
    "packages/cortex_client",
    "packages/executor",
    "packages/ledger",
):
    entry = str(ROOT / rel)
    if entry not in sys.path:
        sys.path.insert(0, entry)

SQL = "SELECT region, sum(amount) AS total FROM facts GROUP BY region ORDER BY region"
LEVELS = (1, 5, 20)
SAMPLES = 20


def percentile(samples: list[float], p: float) -> float:
    """Nearest-rank percentile. ``p`` is 0.50 or 0.95."""
    ordered = sorted(samples)
    if not ordered:
        raise RuntimeError("no samples")
    rank = min(len(ordered), max(1, math.ceil(p * len(ordered))))
    return ordered[rank - 1]


def _gold_for(n: int) -> list[tuple[str, int]]:
    """Each region is ``n // 4`` because the load uses ``i % 4`` and amount 1."""
    each = n // 4
    return [(f"r{i}", each) for i in range(4)]


def build_scratch(path: Path, n: int) -> list[tuple[str, int]]:
    """Write ``n`` rows with a raw connection, then mark the file seeded.

    The raw connection is closed before any serving cursor opens the file.
    """
    import duckdb
    from dms_executor.demo_warehouse import demo_fixture_path

    if path.resolve() == demo_fixture_path().resolve():
        raise RuntimeError("scratch path is the demo warehouse")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE facts (region VARCHAR, amount INTEGER)")
        con.execute(
            "INSERT INTO facts SELECT 'r' || (i % 4)::VARCHAR, 1 FROM range(?) t(i)",
            [n],
        )
        got = con.execute(SQL).fetchall()
    finally:
        con.close()
    gold = [(str(region), int(total)) for region, total in got]
    if gold != _gold_for(n):
        raise RuntimeError(f"scratch load did not match the gold rows: {gold}")
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.add(str(path.resolve()))
    os.environ["DMS_WAREHOUSE_DB"] = str(path.resolve())
    return gold


def governed_once(path: Path) -> list[tuple[str, int]]:
    """Parse, grant-check, and run one read. The same SQL the smoke times."""
    from dms_executor.demo_warehouse import connect_serving
    from dms_executor.grant_struct import serve_gap
    from dms_executor.sql_parse import one_statement

    tree = one_statement(SQL, "duckdb")
    if tree is None:
        raise RuntimeError("governed SQL did not parse")
    why = serve_gap(SQL, grantable={"facts"}, dialect="duckdb")
    if why:
        raise RuntimeError(why)
    cur = connect_serving(path)
    try:
        rows = cur.execute(SQL).fetchall()
    finally:
        cur.close()
    return [(str(region), int(total)) for region, total in rows]


def governed_timed(path: Path) -> tuple[list[tuple[str, int]], float]:
    t0 = time.perf_counter()
    rows = governed_once(path)
    return rows, (time.perf_counter() - t0) * 1000.0


def run_level(
    path: Path, gold: list[tuple[str, int]], concurrency: int, samples: int
) -> list[float]:
    times: list[float] = []
    left = samples

    def one(_i: int) -> float:
        rows, elapsed = governed_timed(path)
        if rows != gold:
            raise RuntimeError("governed ask rows did not match the scratch load")
        return elapsed

    while left:
        width = min(concurrency, left)
        with ThreadPoolExecutor(max_workers=width) as pool:
            times.extend(pool.map(one, range(width)))
        left -= width
    return times


def run(n: int, *, samples: int = SAMPLES) -> int:
    from dms_executor.demo_warehouse import demo_fixture_path
    from dms_executor.lake_registry import close_lakes, parent_open_count

    demo = demo_fixture_path()
    demo_before = parent_open_count(demo)
    previous = os.environ.get("DMS_WAREHOUSE_DB")
    with tempfile.TemporaryDirectory(prefix="dms-scale-") as folder:
        path = Path(folder) / "scale_scratch.duckdb"
        gold = build_scratch(path, n)
        from dms_executor import demo_warehouse as dw

        if str(path.resolve()) not in dw._SEEDED:
            raise RuntimeError("scratch file was not marked seeded")
        lines: list[str] = []
        try:
            for concurrency in LEVELS:
                times = run_level(path, gold, concurrency, samples)
                p50 = percentile(times, 0.50)
                p95 = percentile(times, 0.95)
                lines.append(
                    f"rows={n} concurrency={concurrency} samples={len(times)} "
                    f"p50_ms={p50:.3f} p95_ms={p95:.3f}"
                )
            opens = parent_open_count(path)
            demo_opens = parent_open_count(demo) - demo_before
        finally:
            close_lakes()
            if previous is None:
                os.environ.pop("DMS_WAREHOUSE_DB", None)
            else:
                os.environ["DMS_WAREHOUSE_DB"] = previous
        if opens != 1:
            raise RuntimeError(f"serving parent opened {opens} times, want 1")
        if demo_opens != 0:
            raise RuntimeError("scale smoke opened the demo warehouse")
        for line in lines:
            print(line)
        print(f"rows={n} parent_opens=1 demo_opens=0")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.rows < 4 or args.rows % 4 != 0:
        raise SystemExit("--rows must be a positive multiple of 4")
    n = 200 if args.self_check else args.rows
    samples = SAMPLES
    try:
        return run(n, samples=samples)
    except Exception as exc:  # noqa: BLE001 - the harness reports the cause
        print(f"scale_smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
