"""Land-path row log — accepted, dropped, and failed rows stay auditable (dms#347).

``ingest_batch`` trims titles, blank bands, and trailing notes before bronze,
and refuses headerless sheets. The file receipt does not name which source row
was accepted, dropped, or failed.

The log is ``bronze._land_row_log`` on the ingest warehouse: id (1-based source
row), source, status, reason, plus the batch ``ingest_id``. A new connection
after ``ingest_batch`` returns must see it. The header line is the schema
DuckDB consumes, not a data row, so it is not a log row.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from dms_executor.batch_ingest import ingest_batch
from dms_executor.bronze import list_bronze_tables

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ingest"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _log(wh: Path, ingest_id: str) -> list[tuple[int, str, str, str]]:
    """Read the log from a new connection. Missing table fails the test."""
    con = duckdb.connect(str(wh), read_only=True)
    try:
        found = con.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'bronze' AND table_name = '_land_row_log'
            """
        ).fetchone()
        assert found and int(found[0]) == 1, "bronze._land_row_log was not written"
        cols = {
            str(r[0])
            for r in con.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'bronze' AND table_name = '_land_row_log'
                """
            ).fetchall()
        }
        assert {"id", "source", "status", "reason", "ingest_id"} <= cols
        return [
            (int(row_id), str(source), str(status), str(reason))
            for row_id, source, status, reason in con.execute(
                """
                SELECT id, source, status, reason
                  FROM bronze._land_row_log
                 WHERE ingest_id = ?
                 ORDER BY source, id
                """,
                [ingest_id],
            ).fetchall()
        ]
    finally:
        con.close()


def _by_source(
    rows: list[tuple[int, str, str, str]], source: str
) -> dict[int, tuple[str, str]]:
    return {row_id: (status, reason) for row_id, src, status, reason in rows if src == source}


def test_a_land_path_batch_writes_a_durable_row_log(tmp_path: Path) -> None:
    """WHEN a land-path batch runs THE SYSTEM SHALL record each data row."""
    wh = tmp_path / "land_row_log.duckdb"
    names = [
        "01_clean_sales.csv",
        "02_title_above_header.csv",
        "03_two_tables_stacked.csv",
        "04_trailing_notes.csv",
        "05_unstructured_notes.csv",
        "09_headerless_numeric.csv",
        "12_title_and_trailing.csv",
    ]
    receipt = ingest_batch([(name, _load(name)) for name in names], path=wh)
    rows = _log(wh, receipt.ingest_id)

    assert rows, "the batch wrote no row log"
    for row_id, source, status, reason in rows:
        assert row_id >= 1
        assert source
        assert status in {"accepted", "dropped", "failed"}
        assert reason

    clean = _by_source(rows, "01_clean_sales.csv")
    assert clean == {
        2: ("accepted", "landed"),
        3: ("accepted", "landed"),
    }

    titled = _by_source(rows, "02_title_above_header.csv")
    assert titled[1] == ("dropped", "above_header")
    assert titled[3][0] == "accepted" and titled[4][0] == "accepted"
    assert 2 not in titled

    stacked = _by_source(rows, "03_two_tables_stacked.csv")
    assert stacked[1] == ("dropped", "above_header")
    assert stacked[3][0] == "accepted" and stacked[4][0] == "accepted"
    assert stacked[5] == ("dropped", "blank_row")
    assert stacked[6][0] == "dropped" and stacked[6][1] == "section_break"
    assert stacked[9][0] == "dropped"
    assert 2 not in stacked

    # Whole-file land (header on row 1) tried to load the note and the sniff
    # failed. Those rows are failed, not silently dropped and not accepted.
    notes_file = next(f for f in receipt.files if f.file == "04_trailing_notes.csv")
    notes = _by_source(rows, "04_trailing_notes.csv")
    assert set(notes) == {2, 3, 4, 5}
    assert "parse_error" in notes_file.reason
    assert all(
        status == "failed" and reason == notes_file.reason for status, reason in notes.values()
    )

    prose_file = next(f for f in receipt.files if f.file == "05_unstructured_notes.csv")
    prose = _by_source(rows, "05_unstructured_notes.csv")
    assert set(prose) == {1, 2, 3, 4}
    assert prose_file.reason
    assert all(
        status == "dropped" and reason == prose_file.reason for status, reason in prose.values()
    )

    headerless = _by_source(rows, "09_headerless_numeric.csv")
    assert headerless == {
        1: ("failed", "no_header_row_scored"),
        2: ("failed", "no_header_row_scored"),
        3: ("failed", "no_header_row_scored"),
    }

    trimmed = _by_source(rows, "12_title_and_trailing.csv")
    assert trimmed[1] == ("dropped", "above_header")
    assert trimmed[3] == ("accepted", "landed")
    assert trimmed[4] == ("accepted", "landed")
    assert trimmed[5] == ("dropped", "trailing_note")
    assert 2 not in trimmed

    # Accepted log rows are the rows the warehouse holds (R-0001).
    by_file = {entry.file: entry for entry in receipt.files}
    con = duckdb.connect(str(wh), read_only=True)
    try:
        for name in (
            "01_clean_sales.csv",
            "02_title_above_header.csv",
            "03_two_tables_stacked.csv",
            "12_title_and_trailing.csv",
        ):
            entry = by_file[name]
            assert entry.table
            schema, table = entry.table.split(".", 1)
            landed = int(
                con.execute(f'SELECT COUNT(*) FROM {schema}."{table}"').fetchone()[0]
            )
            accepted = sum(
                1 for status, _reason in _by_source(rows, name).values() if status == "accepted"
            )
            assert accepted == landed, f"{name}: log accepted {accepted}, table has {landed}"
        assert by_file["04_trailing_notes.csv"].table is None
        assert by_file["09_headerless_numeric.csv"].table is None
    finally:
        con.close()

    listed = {t["table"] for t in list_bronze_tables(path=wh)}
    assert "bronze._land_row_log" not in listed


def test_a_later_batch_does_not_erase_the_earlier_log(tmp_path: Path) -> None:
    wh = tmp_path / "append.duckdb"
    first = ingest_batch([("01_clean_sales.csv", _load("01_clean_sales.csv"))], path=wh)
    second = ingest_batch(
        [("09_headerless_numeric.csv", _load("09_headerless_numeric.csv"))], path=wh
    )
    assert _log(wh, first.ingest_id)
    assert _log(wh, second.ingest_id)
    assert first.ingest_id != second.ingest_id


def test_an_exception_during_land_marks_rows_failed(tmp_path: Path, monkeypatch) -> None:
    def boom(**_kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr("dms_executor.batch_ingest.ingest_csv_bytes", boom)
    wh = tmp_path / "boom.duckdb"
    receipt = ingest_batch([("01_clean_sales.csv", _load("01_clean_sales.csv"))], path=wh)
    rows = _by_source(_log(wh, receipt.ingest_id), "01_clean_sales.csv")
    assert rows == {
        2: ("failed", "ingest_error:disk full"),
        3: ("failed", "ingest_error:disk full"),
    }
    assert receipt.ingested == 0
    assert receipt.files[0].ingested is False
