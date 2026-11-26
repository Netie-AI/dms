"""FDE-BULK-LAND-01: columnar bronze landing lands exactly what executemany landed.

The row-at-a-time ``executemany`` was about 90% of a SQL-source pull (measured
~6k rows/s on PostgreSQL 17, 20k rows in 3.4 s), so a table at the 500k-row cap
took over a minute before any typing ran. The columnar path must not change one
landed character, one NULL, or one ``_src`` row number.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_executor import bronze
from dms_executor.demo_warehouse import ensure_demo_warehouse


@pytest.fixture()
def wh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "lake.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    return db


def _sample_rows(n: int) -> list[list[Any]]:
    r = random.Random(11)
    rows: list[list[Any]] = []
    for i in range(n):
        rows.append(
            [
                i,
                r.choice([None, f"name {i}", "héllo, \"quoted\"", ""]),
                Decimal(str(round(r.uniform(-1e6, 1e6), r.randint(0, 6)))),
                r.choice([Decimal("-0.00"), Decimal("0"), Decimal("12.50"), None]),
                date(2025, 1, 1) + timedelta(days=r.randint(0, 4000)),
                datetime(2025, 1, 1)
                + timedelta(seconds=r.randint(0, 10**8), microseconds=r.choice([0, 123000, 5])),
                r.choice([r.uniform(-1e6, 1e6), 0.1, 1.0, None]),
                r.randint(-(2**62), 2**62),
            ]
        )
    return rows


COLS = ["id", "name", "amount", "zeroish", "d", "ts", "f", "big"]


def _land(wh: Path, table: str, rows: list[list[Any]]) -> list[tuple[Any, ...]]:
    landed = bronze.write_bronze_rows(
        table=table, columns=COLS, rows=rows, ref_id="r", ingest_id="i", path=wh
    )
    con = duckdb.connect(str(wh), read_only=True)
    try:
        schema, name = landed.split(".")
        return con.execute(
            f'SELECT *, _src[1].row AS _row FROM "{schema}"."{name}" ORDER BY _row'
        ).fetchall()
    finally:
        con.close()


def test_columnar_landing_equals_executemany(wh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _sample_rows(3000)
    assert bronze._bulk_text_columns(rows, len(COLS)) is not None
    fast = _land(wh, "bulk_fast", rows)
    monkeypatch.setattr(bronze, "_bulk_text_columns", lambda rows, width: None)
    slow = _land(wh, "bulk_slow", rows)
    assert fast == slow
    assert len(fast) == 3000


@pytest.mark.parametrize(
    "odd",
    [
        b"\x00bytes",
        uuid.UUID(int=7),
        datetime(2025, 1, 1, tzinfo=UTC),
        True,
        float("nan"),
        1e20,
        Decimal("1E+2"),
        2**70,
        {"json": 1},
    ],
)
def test_unproven_shapes_keep_executemany(odd: Any) -> None:
    rows = [[1, "a"], [2, odd]]
    assert bronze._bulk_text_columns(rows, 2) is None


def test_negative_zero_decimal_lands_as_duckdb_does() -> None:
    assert bronze._varchar_text(Decimal("-0.00")) == "0.00"
    assert bronze._varchar_text(datetime(2025, 1, 2, 3, 4, 5, 120000)) == "2025-01-02 03:04:05.12"


def test_ragged_rows_keep_executemany() -> None:
    assert bronze._bulk_text_columns([[1, 2], [3]], 2) is None
