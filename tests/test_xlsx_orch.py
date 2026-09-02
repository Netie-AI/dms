"""EPIC-016 #30-32: DMS pack cross-check, extract store, FRTR golden.

Does not open Excel or paste into Copilot. Pointer still owns paste.
Tests that can fail: Summary theater, MCP producer, missing sheets, wrong avg,
export-count disagreement, missing Pointer artifact named as awaiting.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
from dms_core.xlsx_orch import evaluate_frtr_golden
from fastapi.testclient import TestClient

_FIXTURE = Path(__file__).resolve().parents[1] / "scripts" / "make_xlsx_fixture.py"
_spec = importlib.util.spec_from_file_location("make_xlsx_fixture", _FIXTURE)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
write_xlsx_sheets = _mod.write_xlsx_sheets

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _candidate_pack(*, source_sheet: str = "Carriers") -> dict:
    return {
        "ok": True,
        "ask": "OnTime=true average cost + export xlsx + chart for PPT",
        "workbook": {
            "title": "frtr_00027_supply-chain-regional.xlsx",
            "path": "",
            "source_sheet": source_sheet,
            "ontime_col": "OnTime",
            "cost_col": "Cost",
            "n_rows": 3,
        },
        "steps": [
            {"n": 1, "sheet": "Cover", "intent": "Cover provenance"},
            {"n": 2, "sheet": "OnTime Export", "intent": "FILTER OnTime=TRUE"},
            {"n": 3, "sheet": "Analysis", "intent": "AVERAGEIF OnTime TRUE"},
            {"n": 4, "sheet": "Presentation Chart", "intent": "chart for PPT"},
        ],
        "expected_result_sheets": [
            "Cover",
            "OnTime Export",
            "Analysis",
            "Presentation Chart",
        ],
        "paste_owner": "pointer",
        "cross_check_owner": "dms",
        "not_doing": ["pointer_paste", "excel_copilot_drive", "mcp_user_excel_primary"],
    }


def _source_xlsx(path: Path) -> Path:
    dest = path / "frtr_00027_supply-chain-regional.xlsx"
    write_xlsx_sheets(
        dest,
        [
            (
                "Carriers",
                [
                    ["Carrier", "Region", "OnTime", "Cost"],
                    ["Acme", "APAC", "TRUE", 310.0],
                    ["Beta", "EMEA", "FALSE", 200.0],
                    ["Gamma", "APAC", "TRUE", 290.0],
                ],
            )
        ],
    )
    return dest


def _result_xlsx(path: Path, *, analysis: list[list[object]], export_rows: int) -> Path:
    export = [["OnTime", "Cost"]] + [["TRUE", 300.27] for _ in range(export_rows)]
    dest = path / "pointer_result.xlsx"
    write_xlsx_sheets(
        dest,
        [
            ("Cover", [["source", "frtr_00027"], ["filter", "OnTime=TRUE"]]),
            ("OnTime Export", export),
            ("Analysis", analysis),
            ("Presentation Chart", [["note", "chart placeholder"]]),
        ],
    )
    return dest


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        studio_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "wh.duckdb"))
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    _gate_allows(monkeypatch)
    from dms_api.app import create_app

    return TestClient(create_app())


def test_crosscheck_refuses_pack_without_workbook(client: TestClient) -> None:
    resp = client.post("/v1/studio/xlsx-orch/crosscheck", json={"pack": _candidate_pack()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "rejected"
    assert body["reason"] == "workbook_unverified"


def test_crosscheck_refuses_summary_theater(client: TestClient, tmp_path: Path) -> None:
    src = tmp_path / "summary_only.xlsx"
    write_xlsx_sheets(
        src,
        [("Summary", [["KPI", "Value"], ["OnTimeAvg", 12.0]])],
    )
    pack = _candidate_pack(source_sheet="Summary")
    resp = client.post(
        "/v1/studio/xlsx-orch/crosscheck",
        json={"pack": pack, "workbook_path": str(src)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "theater" in str(body.get("reason") or "") or "ontime" in str(
        body.get("error") or ""
    ).lower()


def test_crosscheck_accepts_frtr_class_pack(client: TestClient, tmp_path: Path) -> None:
    src = _source_xlsx(tmp_path)
    pack = _candidate_pack()
    pack["workbook"]["path"] = str(src)
    resp = client.post(
        "/v1/studio/xlsx-orch/crosscheck",
        json={"pack": pack, "workbook_path": str(src), "pack_id": "orch_test_frtr"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, body
    assert body["status"] == "awaiting_pointer_receipt"
    assert body["paste_owner"] == "pointer"
    strengthened = body["strengthened_pack"]
    assert "Do not use a Summary" in strengthened["steps"][0]["intent"]
    oracle = body.get("source_oracle") or {}
    assert oracle.get("ok") is True
    assert oracle["ontime_count"] == 2
    assert oracle["total_count"] == 3
    assert abs(float(oracle["avg_cost"]) - 300.0) < 0.01


def test_extract_without_path_stays_awaiting_pointer(client: TestClient) -> None:
    resp = client.post(
        "/v1/studio/xlsx-orch/extract",
        json={"pack_id": "orch_wait", "space_id": FINANCE, "producer": "pointer_copilot"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == "awaiting_pointer_receipt"


def test_extract_refuses_mcp_producer(client: TestClient, tmp_path: Path) -> None:
    result = _result_xlsx(
        tmp_path,
        analysis=[["avg_cost", 300.27], ["ontime_count", 2], ["total_count", 3]],
        export_rows=2,
    )
    resp = client.post(
        "/v1/studio/xlsx-orch/extract",
        json={
            "pack_id": "orch_mcp",
            "space_id": FINANCE,
            "producer": "mcp_primary",
            "result_path": str(result),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "forbidden_producer" in str(body.get("reason") or "")


def test_extract_is_byte_faithful(client: TestClient, tmp_path: Path) -> None:
    result = _result_xlsx(
        tmp_path,
        analysis=[["avg_cost", 300.27], ["ontime_count", 2], ["total_count", 3]],
        export_rows=2,
    )
    raw = result.read_bytes()
    resp = client.post(
        "/v1/studio/xlsx-orch/extract",
        json={
            "pack_id": "orch_store",
            "space_id": FINANCE,
            "producer": "pointer_copilot",
            "result_path": str(result),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, body
    stored = Path(body["path"])
    assert stored.is_file()
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == hashlib.sha256(raw).hexdigest()
    assert body["sha256"] == hashlib.sha256(raw).hexdigest()
    names = [n.lower() for n in body["sheetnames"]]
    assert any("cover" in n for n in names)
    assert any("ontime" in n and "export" in n for n in names)
    assert any("analysis" in n for n in names)
    assert any("chart" in n for n in names)


def test_golden_without_artifact_is_awaiting_not_a_pass(client: TestClient) -> None:
    resp = client.post(
        "/v1/studio/xlsx-orch/golden",
        json={"pack_id": "orch_missing", "space_id": FINANCE},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == "awaiting_pointer_receipt"


def test_golden_refuses_wrong_avg(client: TestClient, tmp_path: Path) -> None:
    result = _result_xlsx(
        tmp_path,
        analysis=[["avg_cost", 12.34], ["ontime_count", 184005], ["total_count", 200000]],
        export_rows=2,
    )
    client.post(
        "/v1/studio/xlsx-orch/extract",
        json={
            "pack_id": "orch_wrong_avg",
            "space_id": FINANCE,
            "producer": "pointer_copilot",
            "result_path": str(result),
        },
    )
    resp = client.post(
        "/v1/studio/xlsx-orch/golden",
        json={"pack_id": "orch_wrong_avg", "space_id": FINANCE},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == "golden_miss"
    assert "avg_cost" in str(body.get("error") or "")


def test_golden_refuses_export_theater() -> None:
    """Analysis claims 184005 but Export has 2 rows - the Summary-theater trap."""
    sheets = [
        ("Cover", [["src", "frtr"]]),
        ("OnTime Export", [["OnTime"], ["TRUE"], ["TRUE"]]),
        (
            "Analysis",
            [["avg_cost", 300.27], ["ontime_count", 184005], ["total_count", 200000]],
        ),
        ("Presentation Chart", [["x"]]),
    ]
    out = evaluate_frtr_golden(sheets, producer="pointer_copilot")
    assert out["ok"] is False
    assert "export_row_count" in str(out.get("error") or "")


def test_golden_refuses_openpyxl_producer() -> None:
    sheets = [
        ("Cover", [["x"]]),
        ("OnTime Export", [["OnTime"], ["TRUE"]]),
        ("Analysis", [["avg_cost", 300.27]]),
        ("Presentation Chart", [["x"]]),
    ]
    out = evaluate_frtr_golden(sheets, producer="openpyxl_primary")
    assert out["ok"] is False
    assert "forbidden_producer" in str(out.get("reason") or "")


def test_frtr_golden_passes_when_analysis_and_export_agree() -> None:
    export = [["OnTime"]] + [["TRUE"] for _ in range(184005)]
    sheets = [
        ("Cover", [["source", "frtr_00027"]]),
        ("OnTime Export", export),
        (
            "Analysis",
            [["avg_cost", 300.27], ["ontime_count", 184005], ["total_count", 200000]],
        ),
        ("Presentation Chart", [["note", "ppt"]]),
    ]
    out = evaluate_frtr_golden(sheets, producer="pointer_copilot")
    assert out["ok"] is True, out
    assert abs(float(out["avg_cost"]) - 300.27) <= 0.05
    assert abs(int(out["ontime_count"]) - 184005) <= 50
    assert abs(int(out["total_count"]) - 200000) <= 50
