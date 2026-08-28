"""HTTP gate for XLSX-ORCH-10. Asserts the customer JSON, not an internal helper."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

try:
    from openpyxl import Workbook
except ImportError as exc:
    raise AssertionError(f"openpyxl required for xlsx orch HTTP gate: {exc}") from exc


def _xlsx(path: Path, title: str = "Carriers") -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = title
    ws.append(["Carrier", "OnTime", "Cost"])
    ws.append(["Acme", True, 310.0])
    wb.save(path)
    wb.close()
    return path


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "wh.duckdb"))

    import dms_api.routes.xlsx_orch as orch_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        orch_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )
    from dms_api.app import create_app

    return TestClient(create_app())


def test_crosscheck_http_returns_awaiting_pointer_receipt(
    client: TestClient, tmp_path: Path
) -> None:
    src = _xlsx(tmp_path / "frtr.xlsx")
    pack = {
        "ok": True,
        "workbook": {
            "path": str(src),
            "source_sheet": "Carriers",
            "ontime_col": "OnTime",
            "cost_col": "Cost",
        },
        "steps": [
            {"n": 1, "sheet": "Cover", "intent": "Cover provenance", "formula_hint": "none"},
            {
                "n": 2,
                "sheet": "OnTime Export",
                "intent": "Export OnTime=TRUE rows",
                "formula_hint": "=FILTER",
            },
            {
                "n": 3,
                "sheet": "Analysis",
                "intent": "AVERAGEIF Cost on OnTime",
                "formula_hint": "=AVERAGEIF",
            },
            {
                "n": 4,
                "sheet": "Presentation Chart",
                "intent": "Chart for PPT",
                "formula_hint": "column chart",
            },
        ],
        "expected_result_sheets": [
            "Cover",
            "OnTime Export",
            "Analysis",
            "Presentation Chart",
        ],
        "paste_owner": "pointer",
        "airgpt_role": "candidate_pack_only",
    }
    resp = client.post("/v1/studio/xlsx-orch/crosscheck", json={"pack": pack})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "awaiting_pointer_receipt"
    assert body["paste_owner"] == "pointer"
    assert body["result_path"] is None
    assert "Cover" in body["paste_text"]


def test_mcp_owner_is_400(client: TestClient, tmp_path: Path) -> None:
    src = _xlsx(tmp_path / "frtr.xlsx")
    pack = {
        "workbook": {
            "path": str(src),
            "ontime_col": "OnTime",
            "cost_col": "Cost",
        },
        "steps": [
            {"n": 1, "sheet": "Cover", "intent": "Cover"},
            {"n": 2, "sheet": "OnTime Export", "intent": "export OnTime FILTER"},
            {"n": 3, "sheet": "Analysis", "intent": "AVERAGEIF"},
            {"n": 4, "sheet": "Presentation Chart", "intent": "chart"},
        ],
        "expected_result_sheets": [
            "Cover",
            "OnTime Export",
            "Analysis",
            "Presentation Chart",
        ],
        "paste_owner": "mcp",
    }
    resp = client.post("/v1/studio/xlsx-orch/crosscheck", json={"pack": pack})
    assert resp.status_code == 400, resp.text
    assert "mcp_as_primary" in resp.text
