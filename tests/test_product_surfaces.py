"""Ontology / Trust / Runs / Admin / Spaces — the surfaces added for U-series pages.

Two properties matter more than the payloads here:

1. **Cortex down is a rendered state, not a 500.** Every read that crosses to the
   engine answers 200 with ``ok: false`` and a hint, because these pages are what a
   user opens *when* something looks wrong.
2. **No durable store means an empty list plus a reason.** A "what actually
   happened" page that invents rows is worse than no page.
"""

from __future__ import annotations

from typing import Any

import pytest
from dms_api.app import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def gate_allows(monkeypatch):
    """Let mutations through by granting an explicit allow, not by skipping the gate.

    These tests have no Cortex, so ``compliance_gate`` returns
    ``gate_unavailable`` and mutation routes fail closed. That is the correct
    production posture — a write with no compliance decision must not land — so
    the fix is to supply the decision the test is assuming, not to re-open the
    hole the routes used to have.
    """
    from cortex_client.gate import ComplianceDecision

    def allow(*, action: str, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    import dms_api.routes.spaces as spaces_routes

    monkeypatch.setattr(spaces_routes, "compliance_gate", allow)
    return allow


def _stub_cortex(monkeypatch, payload: dict[str, Any] | None, *, ok: bool = True) -> None:
    def fake_get(base_url: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if ok:
            return {"ok": True, "source": "cortex", "url": path, "data": payload}
        return {
            "ok": False,
            "source": "cortex",
            "url": path,
            "error": "unreachable: ConnectError",
            "hint": "Cortex is not answering.",
            "data": None,
        }

    import dms_api.routes.ontology as ontology_routes
    import dms_api.routes.trust as trust_routes

    monkeypatch.setattr(ontology_routes, "cortex_get", fake_get)
    monkeypatch.setattr(trust_routes, "cortex_get", fake_get)


# ------------------------------------------------------------------- ontology

ONTOLOGY_FIXTURE = {
    "pack": "dms",
    "counts": {"object_types": 2, "link_types": 1, "metrics": 3, "sensitive_properties": 3},
    "objects_without_metrics": ["alerts"],
}


def test_ontology_summary_passes_the_engine_payload_through(client, monkeypatch):
    _stub_cortex(monkeypatch, ONTOLOGY_FIXTURE)
    body = client.get("/v1/ontology").json()
    assert body["ok"] is True
    assert body["pack"] == "dms"
    assert body["counts"]["object_types"] == 2


def test_ontology_sections_are_an_allowlist_not_a_proxy(client, monkeypatch):
    _stub_cortex(monkeypatch, {"object_types": []})
    for section in ("objects", "links", "actions", "functions", "metrics", "graph"):
        assert client.get(f"/v1/ontology/{section}").status_code == 200
    assert client.get("/v1/ontology/../health").status_code in (404, 405)
    assert client.get("/v1/ontology/secrets").status_code == 404


def test_ontology_renders_a_hint_when_cortex_is_down(client, monkeypatch):
    _stub_cortex(monkeypatch, None, ok=False)
    body = client.get("/v1/ontology").json()
    assert body["ok"] is False
    assert body["hint"]


# ---------------------------------------------------------------------- trust


def test_trust_summary_forwards_the_claim_verdict(client, monkeypatch):
    _stub_cortex(
        monkeypatch,
        {
            "claim": {
                "statement": "0 confidently wrong",
                "supported": False,
                "blockers": ["corpus is N=47, claim target is N=310"],
                "corpus_n": 47,
                "corpus_target": 310,
                "confidently_wrong": 0,
            },
            "runs": {},
            "thresholds": {"confidently_wrong": 0},
        },
    )
    claim = client.get("/v1/trust/summary").json()["claim"]
    assert claim["supported"] is False
    assert claim["corpus_n"] == 47


def test_trust_refuses_the_claim_when_evidence_is_unavailable(client, monkeypatch):
    _stub_cortex(monkeypatch, None, ok=False)
    body = client.get("/v1/trust/summary").json()
    assert body["ok"] is False
    assert body["claim"]["supported"] is False
    assert body["claim"]["blockers"]


def test_trust_ask_path_does_not_flip_cortex_claim(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    (tmp_path / "score_hostile.json").write_text(
        '{"pack":"hostile_score","precision_on_answered":100,"coverage_pct":71.43,'
        '"correct":10,"answered":10,"wrong":0,"total":14,"passed":true}',
        encoding="utf-8",
    )
    _stub_cortex(monkeypatch, None, ok=False)
    body = client.get("/v1/trust/summary").json()
    assert body["claim"]["supported"] is False
    assert body["ask_path"][0]["pack"] == "hostile_score"
    assert body["ask_path"][0]["wrong"] == 0


def test_trust_run_detail_degrades_to_an_empty_list(client, monkeypatch):
    _stub_cortex(monkeypatch, None, ok=False)
    body = client.get("/v1/trust/runs/corpus").json()
    assert body["items"] == []
    assert body["ok"] is False


# ------------------------------------------------------------- runs and admin


def test_runs_says_not_configured_instead_of_inventing_history(client):
    body = client.get("/v1/runs").json()
    assert body["configured"] is False
    assert body["runs"] == []
    assert "DATABASE_URL" in body["hint"]


def test_admin_says_not_configured_instead_of_inventing_users(client):
    body = client.get("/v1/admin/overview").json()
    assert body["configured"] is False
    assert body["users"] == []
    assert body["tenant_id"]


# --------------------------------------------------------------------- spaces


def test_create_space_is_marked_unpersisted_without_postgres(client, gate_allows):
    res = client.post("/v1/spaces", json={"name": "Pilot cutover"})
    assert res.status_code == 201
    body = res.json()
    assert body["space"]["name"] == "Pilot cutover"
    assert body["persisted"] is False
    assert "DATABASE_URL" in body["hint"]
    assert body["storage"]["backend"] == "memory"
    assert any(s["name"] == "Pilot cutover" for s in client.get("/v1/spaces").json()["spaces"])


def test_create_space_refuses_when_the_gate_cannot_decide(client):
    """No Cortex means no compliance decision, and a write must not land anyway.

    This is the regression guard for the fail-open allowance the write routes
    used to carry: ``gate_unavailable`` was in an exception set, so with Cortex
    down every mutation in the product proceeded ungated and unlogged.
    """
    res = client.post("/v1/spaces", json={"name": "Ungated write"})
    assert res.status_code == 403
    assert res.json()["detail"] == "gate_unavailable"
    assert all(s["name"] != "Ungated write" for s in client.get("/v1/spaces").json()["spaces"])


def test_duplicate_space_name_is_a_conflict(client, gate_allows):
    client.post("/v1/spaces", json={"name": "Duplicate check"})
    assert client.post("/v1/spaces", json={"name": "duplicate check"}).status_code == 409


def test_list_spaces_memory_honesty(client):
    body = client.get("/v1/spaces").json()
    assert body["persisted"] is False
    assert body["storage"]["backend"] == "memory"
    assert "DATABASE_URL" in (body.get("hint") or "")


def test_space_sources_scope_to_that_space(client):
    spaces = client.get("/v1/spaces").json()["spaces"]
    q3 = next(s for s in spaces if s["name"] == "Finance")
    body = client.get(f"/v1/spaces/{q3['id']}/sources").json()
    assert body["count"] == len(body["sources"])
    assert all(s["space_id"] == q3["id"] for s in body["sources"])


def test_unknown_space_sources_is_a_404(client):
    assert client.get("/v1/spaces/sp_nope/sources").status_code == 404
