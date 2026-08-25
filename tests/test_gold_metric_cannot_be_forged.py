"""#76 / PRD-001 F70: a caller cannot assert that a metric is certified.

``run_promote`` gates a gold pipeline on ``GoldMetricDef.is_signed``, which was
``bool(signature and steward_id and signed_at)`` - three plain fields that
``wiring.pipeline_run`` copied straight out of the caller's ``gold_metric`` dict. So a
request could assert its own certification and the gate believed it, with nothing
appended to the ledger anywhere.

Distinct from A-0005, which is closed: that wrote a *false name* into the chain. This
bypassed the chain entirely, which is worse - a forged actor still leaves a record that
something happened.

R-0001: the first two assert on the HTTP response a client receives from
``POST /v1/pipelines/run``, because that is the surface the defect was reachable on.
Asserting only on ``sign_gold_metric`` would be necessary and insufficient: that
function was never the hole.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from dms_core.pipelines import GoldMetricDef
from fastapi.testclient import TestClient

FORGED = {
    "metric_id": "m_revenue",
    "name": "Revenue",
    "sql": "SELECT SUM(amount) AS total FROM silver.sales",
    "steward_id": "cfo@victim.example",
    "signed_at": "2026-01-01T00:00:00Z",
    "signature": "not-a-real-signature",
    "ledger_entry_id": "led_i_made_this_up",
}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "wh.duckdb"))

    import dms_api.routes.pipelines as pipeline_routes
    from cortex_client.gate import ComplianceDecision

    # Promote is a mutation, so with no Cortex the gate correctly fails closed. Stub it
    # allowed so the test reaches the code under test rather than stopping at the gate.
    monkeypatch.setattr(
        pipeline_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )
    from dms_api.app import create_app

    return TestClient(create_app())


def test_a_forged_signature_is_refused_not_honoured(client: TestClient) -> None:
    """The regression. Pre-fix this ran the gold promote on an invented signature."""
    resp = client.post(
        "/v1/pipelines/run",
        json={"pipeline": "gold_sales_total", "gold_metric": FORGED},
    )

    assert resp.status_code == 400, (
        f"a self-asserted signature was accepted: {resp.status_code} {resp.text[:300]}"
    )
    detail = str(resp.json().get("detail", ""))
    assert "signature" in detail
    assert "server-side" in detail, "the refusal must say where a signature comes from"


def test_the_refusal_names_every_attestation_field_offered(client: TestClient) -> None:
    """Refuse loudly, do not silently drop.

    Quietly ignoring the fields would leave the caller believing it signed something.
    That is the same lie in the other direction (R-0011).
    """
    resp = client.post(
        "/v1/pipelines/run",
        json={"pipeline": "gold_sales_total", "gold_metric": FORGED},
    )
    detail = str(resp.json().get("detail", ""))
    for field in ("signature", "signed_at", "ledger_entry_id", "steward_id"):
        assert field in detail, f"{field} was accepted or dropped silently"


def test_a_definition_without_attestation_is_still_allowed_through(
    client: TestClient,
) -> None:
    """R-0005 - the control must refuse the forged attestation, not gold promotes.

    With no Cortex the sign step cannot complete, so this is refused for a *different*
    and honest reason: nothing can be signed onto a ledger that is not there. What must
    NOT happen is the attestation-forgery refusal, because this caller offered none.
    """
    resp = client.post(
        "/v1/pipelines/run",
        json={
            "pipeline": "gold_sales_total",
            "gold_metric": {
                "metric_id": "m_revenue",
                "name": "Revenue",
                "sql": "SELECT SUM(amount) AS total FROM silver.sales",
            },
        },
    )
    detail = str(resp.json().get("detail", ""))
    assert "may not assert" not in detail, (
        "a caller offering only a definition was accused of forging an attestation"
    )


def test_is_signed_requires_a_ledger_entry() -> None:
    """The second lock. Three strings are not an attestation without a chain entry."""
    no_entry = GoldMetricDef(
        metric_id="m",
        name="n",
        sql="SELECT 1 AS total",
        steward_id="someone",
        signed_at="2026-01-01T00:00:00Z",
        signature="looks-real",
    )
    assert no_entry.is_signed is False

    with_entry = GoldMetricDef(
        metric_id="m",
        name="n",
        sql="SELECT 1 AS total",
        steward_id="someone",
        signed_at="2026-01-01T00:00:00Z",
        signature="looks-real",
        ledger_entry_id="led_1",
    )
    assert with_entry.is_signed is True


def test_the_signature_is_the_chain_hash_not_the_entry_id() -> None:
    """F52(b): ``entry_hash`` does not exist on LedgerAppendResponse; the field is ``hash``.

    getattr returned None every time, so the signature silently degraded to the entry
    id - an identifier, which verifies nothing. A signature equal to the id it is
    supposed to authenticate is not a signature.
    """
    from dms_executor.promote import sign_gold_metric

    class _Resp:
        entry_id = "led_42"
        hash = "sha256:deadbeef"

    signed = sign_gold_metric(
        GoldMetricDef(metric_id="m", name="n", sql="SELECT 1 AS total", steward_id="s"),
        cortex_append=lambda **kw: _Resp(),
        actor="svc_steward",
    )

    assert signed.signature == "sha256:deadbeef", (
        f"signature is {signed.signature!r} - it degraded to the entry id again"
    )
    assert signed.ledger_entry_id == "led_42"
    assert signed.signature != signed.ledger_entry_id


def test_the_ledger_actor_is_still_the_resolved_one(client: TestClient) -> None:
    """A-0005 stays closed - the two defects share a surface and must not trade places."""
    seen: dict[str, Any] = {}

    class _Resp:
        entry_id = "led_1"
        hash = "sha256:abc"

    from dms_executor.promote import sign_gold_metric

    def _capture(**kw):
        seen.update(kw)
        return _Resp()

    sign_gold_metric(
        GoldMetricDef(
            metric_id="m", name="n", sql="SELECT 1 AS total", steward_id="cfo@victim.example"
        ),
        cortex_append=_capture,
        actor="svc_steward",
    )
    assert seen["actor"] == "svc_steward"
