"""Ingesting one file must not destroy another, and a failed parse must destroy nothing.

``_safe_table_stem`` keys on ``Path(filename).stem``, so ``2023/sales.csv`` and
``2024/sales.csv`` both resolved to ``bronze.sales`` — and ingest ran an
unconditional ``DROP TABLE IF EXISTS`` before creating. The second upload
destroyed the first while the receipt reported both as ingested, and the shipped
folder picker (``webkitdirectory`` on the Studio upload input) makes that a
single click over a nested folder.

The ``DROP`` was also not paired with the ``CREATE``. A file that failed to parse
left the previous table already gone while the receipt said ``quarantined: 1`` —
the ingest looked rejected and had in fact deleted something.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.bronze import ingest_csv_bytes

A = b"sku,qty\nSKU-A,1\nSKU-A,2\nSKU-A,3\n"
B = b"sku,qty\nSKU-B,9\n"


@pytest.fixture
def wh(tmp_path: Path) -> Path:
    return tmp_path / "wh.duckdb"


def _rows(wh: Path, table: str) -> int:
    con = duckdb.connect(str(wh), read_only=True)
    try:
        return int(con.execute(f'SELECT COUNT(*) FROM bronze."{table}"').fetchone()[0])
    finally:
        con.close()


def _bronze_tables(wh: Path) -> set[str]:
    con = duckdb.connect(str(wh), read_only=True)
    try:
        return {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'bronze'"
            ).fetchall()
        }
    finally:
        con.close()


def test_two_files_with_the_same_stem_do_not_destroy_each_other(wh: Path) -> None:
    """The reported defect: a folder upload collapsing 2023/ and 2024/ into one table."""
    r1 = ingest_csv_bytes(filename="2023/sales.csv", data=A, path=wh)
    r2 = ingest_csv_bytes(filename="2024/sales.csv", data=B, path=wh)

    assert r1.ingested == 3
    assert r2.ingested == 1
    assert r1.table != r2.table, "the second file took over the first file's table"

    first = r1.table.split(".", 1)[1]
    second = r2.table.split(".", 1)[1]
    assert _rows(wh, first) == 3, "the first file's rows were destroyed"
    assert _rows(wh, second) == 1

    # The collision is disclosed, not resolved in silence (R-0011).
    assert any("already holds" in str(x.get("reason", "")) for x in r2.reasons)


def test_re_ingesting_the_same_file_still_replaces_its_own_table(wh: Path) -> None:
    """R-0005: overwriting your own upload is what re-ingest means. Keep it working."""
    first = ingest_csv_bytes(filename="sales.csv", data=A, path=wh)
    again = ingest_csv_bytes(filename="sales.csv", data=B, path=wh)

    assert again.table == first.table, "re-ingest should not fork a new table"
    assert _rows(wh, first.table.split(".", 1)[1]) == 1
    assert not again.reasons


def test_a_failed_parse_leaves_the_previous_table_intact(wh: Path) -> None:
    """DROP ran before CREATE, so a bad file deleted good data and reported quarantine."""
    ok = ingest_csv_bytes(filename="sales.csv", data=A, path=wh)
    table = ok.table.split(".", 1)[1]
    assert _rows(wh, table) == 3

    bad = ingest_csv_bytes(filename="sales.csv", data=b"\x00\x01 not,a\ncsv\x00", path=wh)

    if bad.ingested == 0:
        assert _rows(wh, table) == 3, "a rejected ingest destroyed the previous table"


def test_no_staging_tables_are_left_behind(wh: Path) -> None:
    """The swap must not litter bronze with half-built tables."""
    ingest_csv_bytes(filename="sales.csv", data=A, path=wh)
    ingest_csv_bytes(filename="other.csv", data=B, path=wh)
    leftovers = {t for t in _bronze_tables(wh) if t.startswith("_ing_")}
    assert not leftovers, f"staging tables left behind: {leftovers}"


def test_the_registry_records_which_file_each_table_came_from(wh: Path) -> None:
    """Bronze rows carried _ingest_id but nothing recorded the source file."""
    ingest_csv_bytes(filename="2023/sales.csv", data=A, path=wh)
    con = duckdb.connect(str(wh), read_only=True)
    try:
        rows = con.execute(
            "SELECT table_name, filename FROM bronze._ingest_registry"
        ).fetchall()
    finally:
        con.close()
    assert any(f == "2023/sales.csv" for _, f in rows)
