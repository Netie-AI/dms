"""T12 promote — contract gate, quarantine, idempotent dedup, _src join, infer."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import yaml

from dms_core.pipelines import GoldMetricDef
from dms_executor.bronze import write_bronze_rows
from dms_executor.contract_infer import infer_contract
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.lake_schema import ensure_lake_schemas
from dms_executor.pipeline_loader import PipelineLoadError, load_pipeline_yaml, validate_pipeline_dict
from dms_executor.promote import run_promote, sign_gold_metric


@pytest.fixture()
def wh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "lake.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    return db


def _seed_sales(wh: Path, *, bad_frac: float = 0.1, n: int = 100) -> None:
    cols = ["invoice_no", "line_no", "invoice_date", "amount", "region"]
    rows: list[list[str]] = []
    bad_n = int(n * bad_frac)
    for i in range(n):
        inv = f"INV-{i // 2:04d}"
        line = str((i % 2) + 1)
        date = "2026-07-01"
        amount = "10.00"
        region = "North"
        if i < bad_n:
            # 10% bad: negative amount
            amount = "-5.00"
        rows.append([inv, line, date, amount, region])
    write_bronze_rows(table="sales_raw", columns=cols, rows=rows, path=wh)


PIPE_YAML = """
target: silver.sales
sources: [bronze.sales_raw]
lineage: propagate
contract:
  columns:
    invoice_date: {type: date, required: true}
    amount: {type: "decimal(18,2)", required: true, min: 0}
    region: {type: text, required: true}
    invoice_no: {type: text, required: true}
    line_no: {type: integer, required: true}
  dedup_key: [invoice_no, line_no]
  expectations:
    - amount_not_null_rate: ">= 0.99"
"""


def test_promote_10pct_bad_rows_quarantine(wh: Path):
    _seed_sales(wh, bad_frac=0.1, n=100)
    pipe = load_pipeline_yaml(PIPE_YAML)
    receipt = run_promote(pipe, path=wh)
    assert receipt.passed == 90
    assert receipt.quarantined == 10
    assert receipt.counts_by_reason.get("below_min") == 10

    con = duckdb.connect(str(wh), read_only=True)
    try:
        silver_n = int(con.execute("SELECT COUNT(*) FROM silver.sales").fetchone()[0])
        q_n = int(con.execute("SELECT COUNT(*) FROM quarantine.silver_sales").fetchone()[0])
        assert silver_n == 90
        assert q_n == 10
        reasons = con.execute(
            "SELECT DISTINCT reason FROM quarantine.silver_sales"
        ).fetchall()
        assert ("below_min",) in reasons
        # _src survived
        src_len = con.execute(
            "SELECT len(_src) FROM silver.sales LIMIT 1"
        ).fetchone()[0]
        assert int(src_len) == 1
    finally:
        con.close()


def test_promote_idempotent_on_dedup(wh: Path):
    _seed_sales(wh, bad_frac=0.0, n=20)
    pipe = load_pipeline_yaml(PIPE_YAML)
    r1 = run_promote(pipe, path=wh)
    r2 = run_promote(pipe, path=wh)
    assert r1.passed == 20
    assert r2.passed == 20
    con = duckdb.connect(str(wh), read_only=True)
    try:
        n = int(con.execute("SELECT COUNT(*) FROM silver.sales").fetchone()[0])
        assert n == 20
    finally:
        con.close()


def test_two_source_join_src_array(wh: Path):
    cols_a = ["invoice_no", "line_no", "invoice_date", "amount", "region"]
    cols_b = ["invoice_no", "line_no", "sku"]
    rows_a = [
        ["INV-1", "1", "2026-07-01", "10.00", "North"],
        ["INV-2", "1", "2026-07-02", "20.00", "South"],
    ]
    rows_b = [
        ["INV-1", "1", "SKU-A"],
        ["INV-2", "1", "SKU-B"],
    ]
    write_bronze_rows(table="sales_a", columns=cols_a, rows=rows_a, path=wh, ref_id="ref-a")
    write_bronze_rows(table="sales_b", columns=cols_b, rows=rows_b, path=wh, ref_id="ref-b")
    yaml_text = """
target: silver.sales_joined
sources: [bronze.sales_a, bronze.sales_b]
lineage: propagate
join_on: [invoice_no, line_no]
contract:
  columns:
    invoice_date: {type: date, required: true}
    amount: {type: "decimal(18,2)", required: true, min: 0}
    region: {type: text, required: true}
    invoice_no: {type: text, required: true}
    line_no: {type: integer, required: true}
    sku: {type: text, required: true}
  dedup_key: [invoice_no, line_no]
"""
    pipe = load_pipeline_yaml(yaml_text)
    receipt = run_promote(pipe, path=wh)
    assert receipt.passed == 2
    con = duckdb.connect(str(wh), read_only=True)
    try:
        row = con.execute(
            "SELECT len(_src), _src[1].ref_id, _src[2].ref_id FROM silver.sales_joined LIMIT 1"
        ).fetchone()
        assert int(row[0]) == 2
        assert row[1] == "ref-a"
        assert row[2] == "ref-b"
    finally:
        con.close()


def test_missing_lineage_fails_load():
    bad = """
target: silver.sales
sources: [bronze.sales_raw]
contract:
  columns:
    amount: {type: "decimal(18,2)"}
"""
    with pytest.raises(PipelineLoadError, match="lineage"):
        load_pipeline_yaml(bad)


def test_aggregate_requires_reason():
    bad = """
target: silver.sales_agg
sources: [bronze.sales_raw]
lineage: aggregate
"""
    with pytest.raises(PipelineLoadError, match="lineage_reason"):
        load_pipeline_yaml(bad)


def test_infer_contract_propose_only(wh: Path):
    _seed_sales(wh, bad_frac=0.0, n=10)
    proposal = infer_contract("bronze.sales_raw", path=wh)
    assert proposal.row_count == 10
    assert "amount" in proposal.columns
    assert proposal.note.startswith("proposal only")
    # Does not create silver
    con = duckdb.connect(str(wh), read_only=True)
    try:
        n = con.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'silver' AND table_name = 'sales'
            """
        ).fetchone()[0]
        assert int(n) == 0
    finally:
        con.close()


def test_gold_requires_signed_metric(wh: Path):
    _seed_sales(wh, bad_frac=0.0, n=5)
    # promote silver first
    run_promote(load_pipeline_yaml(PIPE_YAML), path=wh)
    gold_yaml = """
target: gold.sales_total
sources: [silver.sales]
lineage: aggregate
lineage_reason: "metric aggregate — row-level _src not retained by design"
"""
    pipe = load_pipeline_yaml(gold_yaml)
    with pytest.raises(PipelineLoadError, match="steward-signed"):
        run_promote(pipe, path=wh)

    class _Fake:
        entry_id = "led_test_1"
        entry_hash = "hash_test_1"

    signed = sign_gold_metric(
        GoldMetricDef(
            metric_id="m_sales_total",
            name="Sales total",
            sql="SELECT SUM(amount) AS total FROM silver.sales",
            steward_id="steward_1",
        ),
        cortex_append=lambda **kw: _Fake(),
    )
    assert signed.is_signed
    assert signed.ledger_entry_id == "led_test_1"
    receipt = run_promote(pipe, path=wh, gold_metric=signed)
    assert receipt.passed >= 1


def test_example_pipeline_yaml_loads():
    root = Path(__file__).resolve().parents[1] / "pipelines" / "silver_sales.yaml"
    text = root.read_text(encoding="utf-8")
    pipe = load_pipeline_yaml(text, path=str(root))
    assert pipe.target == "silver.sales"
    assert pipe.lineage == "propagate"
