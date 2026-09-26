"""INGEST-OPS: bronze bulk load is fast AND lands the same bytes as per-row INSERT.

``write_bronze_rows`` used ``executemany``: ~550 rows/s, so the 1,056,320-row BIRD
``trans`` table spent ~30 min in that loop and a ~1 GB SQL-source ingest took ~2 h
in one request. The fast path must not trade that for fidelity: NULL vs empty
string, quotes, commas, newlines, unicode and non-str values all land exactly as
the per-row INSERT landed them.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_executor import bronze
from dms_executor.bronze import write_bronze_rows

TRICKY = [
    "",
    'a,"b"\nc',
    "NULL",
    "  padded ",
    "\r\nx\r",
    "é漢字",
    '"',
    "\\",
    "a\tb",
    "\\N",
    ",",
    '""',
]


def _read(db: Path, table: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(db))
    try:
        cols = [
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='bronze' AND table_name=? ORDER BY ordinal_position",
                [table],
            ).fetchall()
            if r[0] != "_ingest_id"
        ]
        sel = ", ".join(f'"{c}"' for c in cols if c != "_src")
        return [
            tuple(r)
            for r in con.execute(
                f'SELECT {sel}, _src[1].row FROM bronze."{table}" ORDER BY _src[1].row'
            ).fetchall()
        ]
    finally:
        con.close()


@pytest.fixture()
def wh(tmp_path: Path) -> Path:
    return tmp_path / "bulk.duckdb"


def test_tricky_strings_and_nulls_round_trip(wh: Path) -> None:
    rows: list[list[Any]] = [[v, None, f"k{i}"] for i, v in enumerate(TRICKY)]
    write_bronze_rows(table="tricky", columns=["v", "n", "k"], rows=rows, path=wh)
    got = _read(wh, "tricky")
    assert [(r[0], r[1], r[2]) for r in got] == [tuple(r) for r in rows]
    # Row provenance numbers follow the source order, 1-based, no gaps.
    assert [r[3] for r in got] == list(range(1, len(rows) + 1))


def test_fast_path_matches_per_row_insert(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[list[Any]] = [
        [v, None if i % 3 else "x", str(i)] for i, v in enumerate(TRICKY * 20)
    ]
    write_bronze_rows(table="fast", columns=["a", "b", "c"], rows=rows, path=wh)

    def _slow(con: Any, columns: list[str], rows_: list[list[Any]]) -> None:
        con.executemany(
            f"INSERT INTO _bronze_raw VALUES ({', '.join(['?'] * len(columns))})", rows_
        )

    monkeypatch.setattr(bronze, "_load_raw_rows", _slow)
    write_bronze_rows(table="slow", columns=["a", "b", "c"], rows=rows, path=wh)
    assert _read(wh, "fast") == _read(wh, "slow")


def test_non_str_values_fall_back_to_duckdb_cast(wh: Path) -> None:
    rows: list[list[Any]] = [[1, True, 2.5], [None, False, None]]
    write_bronze_rows(table="typed", columns=["i", "b", "f"], rows=rows, path=wh)
    con = duckdb.connect()
    try:
        expect = [
            tuple(con.execute("SELECT CAST(? AS VARCHAR)", [v]).fetchone()[0] for v in r)
            for r in rows
        ]
    finally:
        con.close()
    assert [r[:3] for r in _read(wh, "typed")] == expect


def test_ragged_row_still_refuses(wh: Path) -> None:
    with pytest.raises(duckdb.Error):
        write_bronze_rows(table="ragged", columns=["a", "b"], rows=[["1", "2"], ["3"]], path=wh)


def test_two_hundred_thousand_rows_land_in_seconds(wh: Path) -> None:
    """Parent: ~550 rows/s (200k rows ~6 min). Bound is loose; the fix is ~100x."""
    n = 200_000
    rows = [[str(r), f"name-{r}", None if r % 7 else ""] for r in range(n)]
    t0 = time.perf_counter()
    write_bronze_rows(table="big", columns=["id", "name", "note"], rows=rows, path=wh)
    elapsed = time.perf_counter() - t0
    con = duckdb.connect(str(wh))
    try:
        count, nulls, empties = con.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE note IS NULL), "
            "COUNT(*) FILTER (WHERE note = '') FROM bronze.big"
        ).fetchone()
    finally:
        con.close()
    assert count == n
    assert empties == len(range(0, n, 7))
    assert nulls == n - empties
    assert elapsed < 60, f"200k rows took {elapsed:.1f}s"
