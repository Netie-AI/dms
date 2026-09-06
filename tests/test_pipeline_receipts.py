"""LINEAGE-01 - promote receipts persist and GET /v1/pipelines/receipts reads them."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import duckdb
import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_core.pipelines import GoldMetricDef
from dms_executor.pipeline_loader import load_pipeline_yaml
from dms_executor.promote import run_promote, sign_gold_metric
from fastapi.testclient import TestClient
from test_pipeline_promote import PIPE_YAML, _seed_sales

RECEIPT_KEYS = {
    "run_id",
    "target",
    "sources",
    "source_rows",
    "passed",
    "quarantined",
    "unmatched",
    "reconciled",
    "counts_by_reason",
    "dedup_key",
    "lineage",
    "table",
    "quarantine_table",
}

GOLD_YAML = """
target: gold.sales_total
sources: [silver.sales]
lineage: aggregate
lineage_reason: "metric aggregate — row-level _src not retained by design"
"""


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.pipelines as pipelines_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        pipelines_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )


def _gate_denies(monkeypatch: pytest.MonkeyPatch, reason: str = "gate_denied") -> None:
    import dms_api.routes.pipelines as pipelines_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        pipelines_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=False, reason=reason, action=action
        ),
    )


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    db = tmp_path / "lake.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    dw._SEEDED.clear()
    ensure_demo_warehouse(db)
    get_settings.cache_clear()
    return db


def _client() -> TestClient:
    get_settings.cache_clear()
    return TestClient(create_app())


def test_no_receipt_yet_is_a_state_not_zeros(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    client = _client()
    r = client.get("/v1/pipelines/receipts", params={"target": "silver.sales"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "no_receipt_yet"
    assert body["receipt"] is None
    assert body["runs"] == 0
    assert body["target"] == "silver.sales"
    assert body["scope"] == "company-default"
    assert "recorded_at" not in body


def test_recorded_equals_run_body(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gate_allows(monkeypatch)
    _seed_sales(warehouse, bad_frac=0.1, n=100)
    client = _client()
    run = client.post("/v1/pipelines/run", json={"yaml_text": PIPE_YAML})
    assert run.status_code == 200, run.text
    run_body = run.json()
    got = client.get("/v1/pipelines/receipts", params={"target": "silver.sales"})
    assert got.status_code == 200
    body = got.json()
    assert body["state"] == "recorded"
    assert body["runs"] == 1
    assert body["receipt"] == run_body
    assert set(body["receipt"]) == RECEIPT_KEYS
    assert body["recorded_at"]


def test_second_run_is_latest_with_runs_two(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    _seed_sales(warehouse, bad_frac=0.0, n=20)
    client = _client()
    first = client.post("/v1/pipelines/run", json={"yaml_text": PIPE_YAML})
    assert first.status_code == 200
    first_got = client.get("/v1/pipelines/receipts", params={"target": "silver.sales"})
    first_at = first_got.json()["recorded_at"]
    time.sleep(0.02)
    second = client.post("/v1/pipelines/run", json={"yaml_text": PIPE_YAML})
    assert second.status_code == 200
    got = client.get("/v1/pipelines/receipts", params={"target": "silver.sales"})
    body = got.json()
    assert body["state"] == "recorded"
    assert body["runs"] == 2
    assert body["receipt"] == second.json()
    assert body["receipt"]["run_id"] != first.json()["run_id"]
    assert body["recorded_at"] >= first_at


def test_gold_source_rows_null_unchanged(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    _seed_sales(warehouse, bad_frac=0.0, n=5)
    run_promote(load_pipeline_yaml(PIPE_YAML), path=warehouse)

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
        cortex_verify=lambda: type("V", (), {"ok": True})(),
        actor="steward_1",
    )
    receipt = run_promote(load_pipeline_yaml(GOLD_YAML), path=warehouse, gold_metric=signed)
    assert receipt.source_rows is None
    assert receipt.reconciled is False

    client = _client()
    got = client.get("/v1/pipelines/receipts", params={"target": "gold.sales_total"})
    assert got.status_code == 200
    body = got.json()
    assert body["state"] == "recorded"
    assert body["receipt"]["source_rows"] is None
    assert body["receipt"]["reconciled"] is False


def test_survives_fresh_create_app(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _gate_allows(monkeypatch)
    _seed_sales(warehouse, bad_frac=0.0, n=10)
    client = _client()
    run = client.post("/v1/pipelines/run", json={"yaml_text": PIPE_YAML})
    assert run.status_code == 200
    run_id = run.json()["run_id"]
    client.close()

    fresh = _client()
    got = fresh.get("/v1/pipelines/receipts", params={"target": "silver.sales"})
    assert got.status_code == 200
    body = got.json()
    assert body["state"] == "recorded"
    assert body["receipt"]["run_id"] == run_id


def test_read_posture_without_gate_patch(warehouse: Path) -> None:
    client = _client()
    got = client.get("/v1/pipelines/receipts", params={"target": "silver.sales"})
    assert got.status_code == 200
    assert got.json()["state"] == "no_receipt_yet"
    blocked = client.post("/v1/pipelines/run", json={"yaml_text": PIPE_YAML})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "gate_unavailable"


def test_gate_no_is_403_with_detail(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_denies(monkeypatch, reason="not_this_actor")
    client = _client()
    r = client.get("/v1/pipelines/receipts", params={"target": "silver.sales"})
    assert r.status_code == 403
    assert r.json()["detail"] == "not_this_actor"


#: The lock holder, as source rather than as a function reference.
#:
#: ``multiprocessing.Process(target=...)`` was the obvious way to write this and
#: it made the test flaky in the full suite while passing alone. Windows spawns
#: rather than forks, so the child re-imports the module holding the target -
#: this one - and that pulls in ``dms_api.app``, ``dms_executor`` and FastAPI
#: before it can execute a single statement. Measured at ~2.3 s for
#: ``dms_executor`` alone on an idle machine; under the memory pressure of a full
#: run (the same run that produced a DuckDB ``Out of Memory`` on an unrelated
#: test) it went past the 10 s barrier and the assertion fired with a message
#: about a lock, which is not where the problem was.
#:
#: Raising the timeout would have hidden it. The child only ever needed duckdb,
#: so it now gets only duckdb: no test module, no app, no import of the package
#: under test. It prints a line when the lock is held, which is a readiness
#: signal that cannot outlive the process that sent it.
_LOCK_HOLDER = """
import sys, duckdb
con = duckdb.connect(sys.argv[1])
print("locked", flush=True)
sys.stdin.readline()
con.close()
"""


def test_writer_held_lake_is_busy_not_empty(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    client = _client()
    proc = subprocess.Popen(
        [sys.executable, "-c", _LOCK_HOLDER, str(warehouse)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        # Blocks until the child has the lock, or until the child dies and the
        # pipe closes - which returns "" and fails here rather than hanging.
        assert proc.stdout.readline().strip() == "locked", (
            "lock holder never reported taking the lake lock "
            f"(exit={proc.poll()})"
        )
        r = client.get("/v1/pipelines/receipts", params={"target": "silver.sales"})
        assert r.status_code == 503
        detail = r.json()["detail"]
        assert detail["code"] == "lake_busy"
        assert detail.get("message")
        assert r.json().get("state") != "no_receipt_yet"
    finally:
        if proc.stdin is not None:
            try:
                proc.stdin.write("go\n")
                proc.stdin.flush()
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_library_tree_does_not_show_promote_receipts(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate_allows(monkeypatch)
    _seed_sales(warehouse, bad_frac=0.0, n=10)
    client = _client()
    run = client.post("/v1/pipelines/run", json={"yaml_text": PIPE_YAML})
    assert run.status_code == 200
    tree = client.get("/v1/library/tree")
    assert tree.status_code == 200
    dumped = tree.text
    assert "_promote_receipts" not in dumped
    assert "_ingest_registry" not in dumped


def test_failed_receipt_write_does_not_leave_a_promote(warehouse: Path, monkeypatch) -> None:
    """R-0011: both commit or the route reports failure — no silent silver."""
    _seed_sales(warehouse, bad_frac=0.0, n=10)
    pipe = load_pipeline_yaml(PIPE_YAML)

    def _boom(_con: object, _receipt: object) -> None:
        raise RuntimeError("receipt insert failed")

    monkeypatch.setattr("dms_executor.promote._record_promote_receipt", _boom)
    with pytest.raises(RuntimeError, match="receipt insert failed"):
        run_promote(pipe, path=warehouse)
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        n = con.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'silver' AND table_name = 'sales'
            """
        ).fetchone()
        assert n is not None and int(n[0]) == 0
    finally:
        con.close()
