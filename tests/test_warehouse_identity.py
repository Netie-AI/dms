"""S4 — ingest landing in a DB chat cannot see must fail the identity check.

No live Cortex. Two temp DuckDBs stand in for DMS_WAREHOUSE_DB vs the engine
file (TAS-DMS §6: 18 tables vs 6, uploaded sheet unreachable).
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import duckdb
import pytest

# This test must run without cortex-contract (CI installs it; this file does
# not need it). Importing dms_executor normally executes __init__.py, which
# pulls Cortex. Register the package as a namespace first when the wheel is
# absent so only bronze / identity modules load.
try:
    import cortex_contract  # noqa: F401
except ModuleNotFoundError:
    _pkg = types.ModuleType("dms_executor")
    _pkg.__path__ = [
        str(Path(__file__).resolve().parents[1] / "packages" / "executor" / "dms_executor")
    ]
    sys.modules["dms_executor"] = _pkg

from dms_executor.batch_ingest import ingest_batch
from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse
from dms_executor.warehouse_identity import (
    bronze_missing_from_serving,
    identity_check,
    list_bronze_readonly,
    sync_bronze_to_serving,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_bronze_to_serving.py"
XLSX = ROOT / "tests" / "fixtures" / "ingest" / "15_q3_sales_export.xlsx"


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    ingest = tmp_path / "ingest.duckdb"
    serving = tmp_path / "serving.duckdb"
    ensure_demo_warehouse(ingest)
    ensure_demo_warehouse(serving)
    # Extra table only on serving — sync must not drop it (engine has ~18 tables).
    con = duckdb.connect(str(serving))
    try:
        con.execute("CREATE TABLE extra_engine_only (id INTEGER)")
        con.execute("INSERT INTO extra_engine_only VALUES (1)")
    finally:
        con.close()
    return ingest, serving


def _extra_count(serving: Path) -> int:
    con = duckdb.connect(str(serving), read_only=True)
    try:
        return int(con.execute("SELECT COUNT(*) FROM extra_engine_only").fetchone()[0])
    finally:
        con.close()


def test_identity_fails_when_xlsx_lands_only_in_ingest(pair: tuple[Path, Path]) -> None:
    ingest, serving = pair
    receipt = ingest_batch(
        [("15_q3_sales_export.xlsx", XLSX.read_bytes())], path=ingest
    )
    assert receipt.ingested == 1
    table = receipt.files[0].table
    assert table

    missing = bronze_missing_from_serving(ingest, serving)
    assert table in missing
    ok, detail = identity_check(ingest, serving)
    assert ok is False
    assert table in detail


def test_sync_makes_serving_see_the_upload(pair: tuple[Path, Path]) -> None:
    ingest, serving = pair
    receipt = ingest_batch(
        [("15_q3_sales_export.xlsx", XLSX.read_bytes())], path=ingest
    )
    table = receipt.files[0].table
    assert table

    result = sync_bronze_to_serving(ingest=ingest, serving=serving)
    assert result.ok
    assert table in result.copied
    assert bronze_missing_from_serving(ingest, serving) == []
    ok, _ = identity_check(ingest, serving)
    assert ok is True

    bronze = {t["table"]: t["row_count"] for t in list_bronze_readonly(serving)}
    assert bronze[table] == 4
    assert _extra_count(serving) == 1
    for name in DEMO_TABLES:
        con = duckdb.connect(str(serving), read_only=True)
        try:
            n = int(con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        finally:
            con.close()
        assert n > 0


def test_same_path_is_aligned(tmp_path: Path) -> None:
    one = tmp_path / "one.duckdb"
    ensure_demo_warehouse(one)
    ingest_batch([("units.csv", b"sku,qty\nA,1\n")], path=one)
    assert bronze_missing_from_serving(one, one) == []
    ok, detail = identity_check(one, one)
    assert ok is True
    assert "single warehouse" in detail
    result = sync_bronze_to_serving(ingest=one, serving=one)
    assert result.status == "same_file"


def test_check_script_exits_1_when_paths_diverge(pair: tuple[Path, Path]) -> None:
    ingest, serving = pair
    ingest_batch([("units.csv", b"sku,qty\nA,1\nB,2\n")], path=ingest)
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--check",
        "--ingest",
        str(ingest),
        "--serving",
        str(serving),
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "bronze" in proc.stdout.lower() or "bronze" in proc.stderr.lower()

    sync = subprocess.run(
        [sys.executable, str(SCRIPT), "--ingest", str(ingest), "--serving", str(serving)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert sync.returncode == 0, sync.stdout + sync.stderr

    again = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert again.returncode == 0, again.stdout + again.stderr


@pytest.mark.parametrize("name", sorted((*DEMO_TABLES, "meta")))
def test_a_customer_table_named_like_a_demo_table_is_not_silently_dropped(
    pair: tuple[Path, Path], name: str
) -> None:
    """The P0 this file exists to prevent, in its most likely real-world shape.

    ``transactions``, ``inventory``, ``locations``, ``suppliers``, ``shipments``,
    ``alerts`` and ``meta`` are names real customer schemas are full of. The sync
    used to skip exactly those, silently: ingest reported ``ingested=3``, the sync
    reported ``copied``, ``identity_check`` reported aligned, and the table was
    absent from the file chat reads. Chat then answered from the 15-row synthetic
    demo table in ``main`` under a green badge — a plausible number that no
    downstream check can catch.

    The demo seed is in ``main``; the sync writes ``bronze``. They cannot collide.
    """
    ingest, serving = pair
    csv = b"txn_id,amount_myr\nT1,1000000\nT2,2500000\nT3,4000000\n"
    receipt = ingest_batch([(f"{name}.csv", csv)], path=ingest)
    assert receipt.ingested == 1
    table = receipt.files[0].table
    assert table == f"bronze.{name}"

    # It must be listed on the ingest side — Studio shows what it lists.
    assert table in {t["table"] for t in list_bronze_readonly(ingest)}

    result = sync_bronze_to_serving(ingest=ingest, serving=serving)
    assert result.skipped == [], f"a skip must never be silent: {result.skipped}"
    assert result.ok
    assert table in result.copied

    # The rows chat reads are the customer's three, not the demo seed's.
    assert bronze_missing_from_serving(ingest, serving) == []
    con = duckdb.connect(str(serving), read_only=True)
    try:
        assert int(con.execute(f'SELECT COUNT(*) FROM bronze."{name}"').fetchone()[0]) == 3
        assert int(
            con.execute(f'SELECT SUM(CAST(amount_myr AS BIGINT)) FROM bronze."{name}"').fetchone()[
                0
            ]
        ) == 7_500_000
        # The demo seed in main is untouched — that is why the filter was wrong.
        if name in DEMO_TABLES:
            assert int(con.execute(f'SELECT COUNT(*) FROM main."{name}"').fetchone()[0]) > 0
    finally:
        con.close()
