"""T12 promote — contract gate, quarantine, idempotent dedup, _src join, infer."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_core.pipelines import GoldMetricDef
from dms_executor.bronze import write_bronze_rows
from dms_executor.contract_infer import infer_contract
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.pipeline_loader import (
    PipelineLoadError,
    load_pipeline_yaml,
)
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
        actor="steward_1",
    )
    assert signed.is_signed
    assert signed.ledger_entry_id == "led_test_1"
    receipt = run_promote(pipe, path=wh, gold_metric=signed)
    assert receipt.passed >= 1


def test_ledger_actor_is_never_taken_from_caller_supplied_metric_data():
    """A-0005 regression: the signer must be resolved server-side, never from the metric.

    sign_gold_metric used to declare ``actor: str | None = None`` and then append with
    ``actor=actor or metric.steward_id``. metric.steward_id arrives from a request body
    (GoldSignBody.steward_id, and the gold_metric dict on POST /v1/pipelines/run), so a
    caller named the actor written onto a tamper-evident record.

    The fallback is the binding where caller data became identity. This asserts it is
    gone at the binding rather than at the one route that exposed it (R-0004), so the
    /run path is covered by the same assertion.
    """
    seen: dict[str, object] = {}

    class _Resp:
        entry_id = "led_actor_1"
        entry_hash = "hash_actor_1"

    def _capture(**kw):
        seen.update(kw)
        return _Resp()

    metric = GoldMetricDef(
        metric_id="m_x",
        name="X",
        sql="SELECT 1 AS total",
        steward_id="ceo@victim.example",
    )

    # A resolved actor is used verbatim and does not defer to the metric.
    sign_gold_metric(metric, cortex_append=_capture, actor="svc_dms_steward")
    assert seen["actor"] == "svc_dms_steward"
    assert seen["actor"] != metric.steward_id

    # And with no resolvable actor, NOTHING is appended. The old binding reached this
    # line and wrote "ceo@victim.example" onto the chain; failing closed is the fix.
    seen.clear()
    with pytest.raises(ValueError):
        sign_gold_metric(metric, cortex_append=_capture, actor="")
    assert seen == {}, (
        f"an append happened with no resolved actor: {seen.get('actor')!r} - A-0005 is back"
    )


def test_signing_without_a_resolved_actor_is_refused():
    """No actor means no authority to write a name onto the chain. Fail closed."""

    class _Resp:
        entry_id = "led_x"
        entry_hash = "hash_x"

    metric = GoldMetricDef(
        metric_id="m_y",
        name="Y",
        sql="SELECT 1 AS total",
        steward_id="steward_1",
    )

    with pytest.raises(ValueError, match="server-resolved actor"):
        sign_gold_metric(metric, cortex_append=lambda **kw: _Resp(), actor="")

    with pytest.raises(TypeError):
        sign_gold_metric(metric, cortex_append=lambda **kw: _Resp())  # type: ignore[call-arg]


def test_gold_sign_body_cannot_carry_a_steward():
    """The request schema itself must not offer a field that names the signer.

    Asserted on the customer-facing contract (R-0001) rather than on the internal call:
    if the field is absent from the model, no client can send it and pydantic will not
    silently accept it.
    """
    from dms_api.routes.pipelines import GoldSignBody

    assert "steward_id" not in GoldSignBody.model_fields, (
        "GoldSignBody re-declared steward_id - a caller can name the ledger actor again"
    )


def test_example_pipeline_yaml_loads():
    root = Path(__file__).resolve().parents[1] / "pipelines" / "silver_sales.yaml"
    text = root.read_text(encoding="utf-8")
    pipe = load_pipeline_yaml(text, path=str(root))
    assert pipe.target == "silver.sales"
    assert pipe.lineage == "propagate"


def test_unmatched_join_rows_are_quarantined_not_silently_dropped(wh: Path):
    """An INNER JOIN removed unmatched rows upstream of both counters.

    Three A rows against two B rows used to report passed=2, quarantined=0 -
    one row gone with no reason code, while promote.py's docstring promises
    rows that fail the contract land in quarantine and are never dropped.
    """
    cols_a = ["invoice_no", "line_no", "invoice_date", "amount", "region"]
    cols_b = ["invoice_no", "line_no", "sku"]
    rows_a = [
        ["INV-1", "1", "2026-07-01", "10.00", "North"],
        ["INV-2", "1", "2026-07-02", "20.00", "South"],
        ["INV-3", "1", "2026-07-03", "30.00", "East"],  # no partner in B
    ]
    rows_b = [
        ["INV-1", "1", "SKU-A"],
        ["INV-2", "1", "SKU-B"],
    ]
    write_bronze_rows(table="unm_a", columns=cols_a, rows=rows_a, path=wh, ref_id="ref-a")
    write_bronze_rows(table="unm_b", columns=cols_b, rows=rows_b, path=wh, ref_id="ref-b")
    yaml_text = """
target: silver.unmatched_demo
sources: [bronze.unm_a, bronze.unm_b]
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
    receipt = run_promote(load_pipeline_yaml(yaml_text), path=wh)

    assert receipt.source_rows == 3, "every row that set out must be counted"
    assert receipt.passed == 2
    assert receipt.quarantined == 1, "the unmatched row must be visible, not gone"
    assert receipt.counts_by_reason.get("join_unmatched") == 1
    assert receipt.reconciled, "passed + quarantined must account for every source row"

    con = duckdb.connect(str(wh), read_only=True)
    try:
        row = con.execute(
            "SELECT invoice_no, reason FROM quarantine.silver_unmatched_demo "
            "WHERE reason = 'join_unmatched'"
        ).fetchone()
        assert row is not None and row[0] == "INV-3"
    finally:
        con.close()
