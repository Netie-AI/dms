"""INSIGHTS-EXPORT-02 — Power BI / Superset from a real ask envelope, no invented metrics."""

from __future__ import annotations

import json

from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_core.bi_export import (
    bi_download_name,
    export_envelope_bi,
    power_query_m,
)
from dms_core.xlsx_export import EnvelopeExportError
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"

_ENVELOPE = {
    "answer_id": "ans_export_two",
    "badge": "L2_VALIDATED",
    "text": "Two SKUs by qty.",
    "values": [
        {"id": "v_a", "value": 1, "unit": "ea", "label": "A"},
        {"id": "v_b", "value": 2, "unit": "ea", "label": "B"},
    ],
    "rows": [
        {"sku": "SKU-ALPHA", "qty": 1},
        {"sku": "SKU-BETA", "qty": 2},
    ],
    "as_of": "2026-09-13T00:00:00Z",
    "sql_used": "SELECT sku, qty FROM t",
    "audit_id": "aud_export_two",
    "assumptions": [],
    "contributing_sources": [],
}


def test_bi_name_from_answer_id_not_clock() -> None:
    assert bi_download_name("ans_live_9f3c", "pq") == "dms_answer_ans_live_9f3c.pq"
    assert (
        bi_download_name("ans/live\\9f3c", "superset.json")
        == "dms_answer_ans_live_9f3c.superset.json"
    )
    assert bi_download_name("", "pq") == "dms_answer_export.pq"


def test_refuse_without_real_envelope() -> None:
    try:
        export_envelope_bi({})
    except EnvelopeExportError as exc:
        assert exc.code == "envelope_required"
    else:
        raise AssertionError("expected EnvelopeExportError")
    try:
        export_envelope_bi({"rows": [{"sku": "FAKE", "qty": 99}]})
    except EnvelopeExportError as exc:
        assert exc.code == "envelope_required"
    else:
        raise AssertionError("expected EnvelopeExportError")


def test_unknown_target_refused() -> None:
    try:
        export_envelope_bi(_ENVELOPE, target="tableau")
    except EnvelopeExportError as exc:
        assert exc.code == "target_unknown"
    else:
        raise AssertionError("expected EnvelopeExportError")


def test_copies_rows_and_does_not_invent_metrics() -> None:
    payload = export_envelope_bi(_ENVELOPE)
    assert payload["ok"] is True
    assert payload["complete"] is False
    assert payload["live_connector"] is False
    assert payload["answer_id"] == "ans_export_two"
    assert payload["badge"] == "L2_VALIDATED"
    assert payload["source_table"] == "rows"
    assert payload["row_count"] == 2
    assert payload["columns"] == ["sku", "qty"]
    assert payload["table"] == [
        {"sku": "SKU-ALPHA", "qty": 1},
        {"sku": "SKU-BETA", "qty": 2},
    ]
    blob = json.dumps(payload)
    assert "99.95" not in blob
    assert "COMPLETE" not in blob
    assert "Total" not in blob
    m = payload["targets"]["powerbi"]["power_query_m"]
    assert "SKU-ALPHA" in m
    assert "SKU-BETA" in m
    assert m.count("SKU-") == 2
    assert "CALCULATE" not in m
    assert "SUMX" not in m
    assert "[Total" not in m
    ds = payload["targets"]["superset"]["dataset"]
    assert ds["sqlalchemy_uri"] is None
    assert ds["embed_as_dms_chrome"] is False
    assert ds["rows"] == payload["table"]
    assert "metrics" not in ds
    assert payload["targets"]["powerbi"]["live_connector"] is False
    assert "NEEDS-YOU" in " ".join(payload["targets"]["powerbi"]["needs_you"])
    assert "NEEDS-YOU" in " ".join(payload["targets"]["superset"]["needs_you"])
    assert "password" not in blob.lower()
    assert "ov_" not in blob
    assert "CORTEX_API_KEY" not in blob


def test_power_query_escapes_quotes() -> None:
    m = power_query_m(["sku"], [{"sku": 'SKU-"BETA"'}])
    assert '"SKU-""BETA"""' in m


def test_abstain_cover_only_no_invented_rows() -> None:
    env = {
        "answer_id": "ans_ab",
        "badge": "ABSTAIN",
        "abstained": True,
        "text": "No proof.",
        "values": [],
        "rows": [],
    }
    payload = export_envelope_bi(env, target="powerbi")
    assert payload["complete"] is False
    assert payload["source_table"] == "cover"
    assert payload["table"] == [
        {"field": "answer_id", "value": "ans_ab"},
        {"field": "badge", "value": "ABSTAIN"},
        {"field": "abstained", "value": True},
        {"field": "text", "value": "No proof."},
    ]
    assert "superset" not in payload["targets"]
    assert "qty" not in json.dumps(payload["table"])


def test_http_ask_then_export_bi_demo(monkeypatch) -> None:
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app())
    asked = client.post(
        "/v1/chat/ask",
        json={"question": "What was total revenue?", "space_id": FINANCE},
    )
    assert asked.status_code == 200, asked.text
    env = asked.json()
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"]
    exported = client.post("/v1/chat/export.bi", json={"envelope": env})
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert body["complete"] is False
    assert body["live_connector"] is False
    assert body["answer_id"] == env["answer_id"]
    assert body["badge"] == env["badge"]
    assert body["source_table"] == "rows"
    assert body["row_count"] == len(env["rows"])
    assert body["table"] == [
        {k: row[k] for k in body["columns"]} for row in env["rows"]
    ]
    assert "powerbi" in body["targets"]
    assert "superset" in body["targets"]
    assert env["rows"][0][body["columns"][0]] is not None
    m = body["targets"]["powerbi"]["power_query_m"]
    first = str(env["rows"][0][body["columns"][0]])
    assert first in m
    assert body["targets"]["superset"]["dataset"]["sqlalchemy_uri"] is None


def test_http_export_bi_refuses_without_envelope() -> None:
    client = TestClient(create_app())
    missing = client.post("/v1/chat/export.bi", json={})
    assert missing.status_code == 422
    empty = client.post("/v1/chat/export.bi", json={"envelope": {}})
    assert empty.status_code == 400
    assert empty.json()["detail"]["code"] == "envelope_required"
    invented = client.post(
        "/v1/chat/export.bi",
        json={"envelope": {"rows": [{"sku": "FAKE", "qty": 99}]}},
    )
    assert invented.status_code == 400
    bad_target = client.post(
        "/v1/chat/export.bi",
        json={"envelope": _ENVELOPE, "target": "tableau"},
    )
    assert bad_target.status_code == 422


def test_http_export_bi_does_not_reask(monkeypatch) -> None:
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    get_settings.cache_clear()
    app = create_app()

    def boom(*_a, **_k):
        raise AssertionError("export must not re-ask")

    app.state.ask_service.live_ask = boom
    app.state.ask_service.demo_ask = boom
    client = TestClient(app)
    r = client.post(
        "/v1/chat/export.bi",
        json={"envelope": _ENVELOPE, "target": "superset"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["targets"]["superset"]["dataset"]["rows"] == [
        {"sku": "SKU-ALPHA", "qty": 1},
        {"sku": "SKU-BETA", "qty": 2},
    ]
    assert "powerbi" not in body["targets"]
