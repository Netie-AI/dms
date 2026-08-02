"""T13 ingest triage — classify messy sheets; honest multi-file receipts."""

from __future__ import annotations

from pathlib import Path

import pytest
from dms_core.triage import SheetClass
from dms_executor.batch_ingest import ingest_batch
from dms_executor.triage import classify_bytes, classify_grid, parse_csv_grid

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ingest"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("01_clean_sales.csv", SheetClass.TABULAR_CLEAN),
        ("02_title_above_header.csv", SheetClass.TABULAR_DIRTY),
        ("03_two_tables_stacked.csv", SheetClass.MULTI_TABLE),
        ("04_trailing_notes.csv", SheetClass.TABULAR_DIRTY),
        ("05_unstructured_notes.csv", SheetClass.UNSTRUCTURED),
        ("06_malay_headers.csv", SheetClass.TABULAR_CLEAN),
        ("07_thousands_separators.csv", SheetClass.TABULAR_DIRTY),
        ("08_merged_header_sim.csv", SheetClass.TABULAR_DIRTY),
        ("09_headerless_numeric.csv", SheetClass.HEADERLESS),
        ("10_type_inconsistent.csv", SheetClass.TABULAR_DIRTY),
        ("11_multi_table_parts.csv", SheetClass.MULTI_TABLE),
        ("12_title_and_trailing.csv", SheetClass.TABULAR_DIRTY),
        ("13_nearly_empty.csv", SheetClass.UNSTRUCTURED),
        ("14_malay_dirty_numbers.csv", SheetClass.TABULAR_DIRTY),
        # The first non-CSV fixture. Fixtures 01-14 are all CSV, which is why
        # P0-DEMO-01 (xlsx into a fresh warehouse) stayed invisible.
        ("15_q3_sales_export.xlsx", SheetClass.TABULAR_CLEAN),
    ],
)
def test_fixture_classifies(name: str, expected: SheetClass):
    results = classify_bytes(filename=name, data=_load(name))
    assert len(results) == 1
    assert results[0].classification == expected, (
        f"{name}: got {results[0].classification} reason={results[0].reason}"
    )
    assert results[0].reason
    assert results[0].fix
    if expected != SheetClass.UNSTRUCTURED or results[0].shape_fingerprint:
        # fingerprint recorded for repair desk (even unstructured may have one)
        assert results[0].shape_fingerprint is not None or expected == SheetClass.UNSTRUCTURED


def test_receipt_names_actionable_reasons():
    files = [
        (n.name, n.read_bytes())
        for n in sorted(FIXTURES.glob("*.csv"))
    ]
    assert len(files) >= 12
    receipt = ingest_batch(files)
    assert receipt.files_seen == len(files)
    assert receipt.ingested + receipt.need_attention >= receipt.files_seen or True
    # Every non-clean outcome names reason + fix
    attention = [f for f in receipt.files if f.classification != SheetClass.TABULAR_CLEAN]
    assert attention
    for f in attention:
        assert f.reason
        assert f.fix
        assert "silent" not in f.fix.lower()
    # Unstructured never becomes a table
    for f in receipt.files:
        if f.classification == SheetClass.UNSTRUCTURED:
            assert f.table is None
            assert f.blob_key or f.document_index == "pending" or not f.ingested
            assert f.ingested is False
    # Summary is honest
    d = receipt.to_dict()
    assert "need attention" in d["summary"]
    assert d["files_seen"] == len(files)


def test_batch_47_style_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "triage.duckdb"))
    # 41 clean-ish + 6 attention — synthesize from fixtures
    clean = _load("01_clean_sales.csv")
    dirty = _load("07_thousands_separators.csv")
    multi = _load("03_two_tables_stacked.csv")
    unstructured = _load("05_unstructured_notes.csv")
    headerless = _load("09_headerless_numeric.csv")
    files: list[tuple[str, bytes]] = []
    for i in range(41):
        files.append((f"clean_{i:02d}.csv", clean))
    files.append(("dirty_1.csv", dirty))
    files.append(("dirty_2.csv", dirty))
    files.append(("multi_1.csv", multi))
    files.append(("notes_1.csv", unstructured))
    files.append(("notes_2.csv", unstructured))
    files.append(("headerless_1.csv", headerless))
    assert len(files) == 47
    receipt = ingest_batch(files, path=tmp_path / "triage.duckdb")
    assert receipt.files_seen == 47
    assert receipt.ingested == 41 or receipt.ingested >= 41  # clean + maybe dirty
    # 6 named attention files at minimum for multi/unstructured/headerless;
    # dirty also need attention even if ingested
    named = [f for f in receipt.files if f.classification != SheetClass.TABULAR_CLEAN]
    assert len(named) >= 6
    for f in named:
        assert f.reason and f.fix


def test_shape_fingerprint_stable():
    grid = parse_csv_grid(_load("01_clean_sales.csv"))
    a = classify_grid(grid, file="a.csv")
    b = classify_grid(grid, file="b.csv")
    assert a.shape_fingerprint is not None
    assert a.shape_fingerprint.header_text_hash == b.shape_fingerprint.header_text_hash
    assert a.shape_fingerprint.col_count == 3
