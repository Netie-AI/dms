"""A receipt may never contradict the warehouse (P0-DEMO-01, Netie-AI/dms#4).

The reported symptom: the first ``.xlsx`` into a fresh warehouse returned
``ingested=0, reason="parse_error:... _ingest_registry does not exist"`` while
the rows were sitting in ``bronze.<table>``. ``_ensure_registry`` ran only from
``_claim_table_name``, which is reached only when ``table_name is None`` — and
batch ingest always passes ``table_name`` for xlsx.

It stayed invisible because every one of the 14 ingest fixtures was CSV, and a
CSV ingested first creates the registry that the xlsx path assumed. Fixture 15
is the missing case.

The class (R-0004) is wider than the missing call: a step *after* an
irreversible ``ALTER TABLE RENAME`` could fail, leaving the warehouse in a state
the receipt denies. ``test_a_failure_while_recording...`` pins that, so the class
stays fixed even if the registry call moves again.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor import bronze as bronze_mod
from dms_executor.batch_ingest import ingest_batch
from dms_executor.bronze import ingest_csv_bytes

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ingest"
XLSX = "15_q3_sales_export.xlsx"


@pytest.fixture
def fresh(tmp_path: Path) -> Path:
    """A warehouse with no prior ingest — the condition that exposed the bug."""
    return tmp_path / "fresh.duckdb"


def _count(wh: Path, qualified: str) -> int:
    schema, _, table = qualified.partition(".")
    con = duckdb.connect(str(wh), read_only=True)
    try:
        return int(con.execute(f'SELECT COUNT(*) FROM {schema}."{table}"').fetchone()[0])
    finally:
        con.close()


def test_first_xlsx_into_a_fresh_warehouse_reports_the_truth(fresh: Path) -> None:
    """WHEN a .xlsx is ingested into a warehouse with no prior ingest THE SYSTEM
    SHALL return a receipt naming the created table and the true row count."""
    receipt = ingest_batch([(XLSX, (FIXTURES / XLSX).read_bytes())], path=fresh)

    assert receipt.ingested == 1, (
        f"receipt denies the ingest: {[f.reason for f in receipt.files]}"
    )
    entry = receipt.files[0]
    assert entry.ingested is True
    assert entry.table, "receipt must name the created table"

    # R-0001: assert against what the customer receives, not the parse result.
    assert _count(fresh, entry.table) == 4


def test_no_csv_is_needed_first_to_make_xlsx_work(fresh: Path) -> None:
    """The masking condition: ingesting a CSV first used to create the registry.

    Ingesting the xlsx alone must behave identically to ingesting it after a CSV.
    """
    xlsx_only = ingest_batch([(XLSX, (FIXTURES / XLSX).read_bytes())], path=fresh)
    assert xlsx_only.ingested == 1
    assert xlsx_only.files[0].reason and "parse_error" not in xlsx_only.files[0].reason


def test_receipt_row_count_matches_the_bronze_table(fresh: Path) -> None:
    """The batch path passes table_name explicitly — the path that skipped the registry."""
    receipt = ingest_csv_bytes(
        filename="explicit.csv",
        data=b"sku,qty\nSKU-A,1\nSKU-B,2\n",
        path=fresh,
        table_name="explicit_target",
    )

    assert receipt.table == "bronze.explicit_target"
    assert receipt.quarantined == 0
    assert receipt.ingested == _count(fresh, receipt.table)


def test_a_failure_while_recording_the_ingest_leaves_the_previous_table_intact(
    fresh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root-cause class: nothing may fail after the rename and leave the
    warehouse disagreeing with the receipt.

    The swap and the registry write are one transaction, so a failure in the
    write rolls the rename back. The receipt then says quarantined, and the
    warehouse still holds exactly what the receipt implies it holds.
    """
    first = ingest_csv_bytes(
        filename="sales.csv", data=b"sku,qty\nSKU-A,1\nSKU-A,2\n", path=fresh
    )
    assert first.ingested == 2

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("registry write failed")

    monkeypatch.setattr(bronze_mod, "_record_ingest", boom)

    second = ingest_csv_bytes(
        filename="sales.csv", data=b"sku,qty\nSKU-B,9\n", path=fresh
    )

    # The receipt reports failure...
    assert second.ingested == 0
    assert second.quarantined == 1
    # ...and the warehouse agrees: the previous table is untouched, not replaced
    # by the new rows and not destroyed.
    assert _count(fresh, "bronze.sales") == 2
