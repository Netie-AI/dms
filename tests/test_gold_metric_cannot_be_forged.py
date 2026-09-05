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
        cortex_verify=lambda: type("V", (), {"ok": True})(),
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
        cortex_verify=lambda: type("V", (), {"ok": True})(),
        actor="svc_steward",
    )
    assert seen["actor"] == "svc_steward"


def test_append_ok_but_verify_not_ok_refuses_and_does_not_present_as_signed() -> None:
    """F70 readback: an append response is not attestation until the chain verifies."""
    from dms_executor.promote import sign_gold_metric

    class _Resp:
        entry_id = "led_fresh"
        hash = "sha256:looks_real"

    with pytest.raises(ValueError, match="ledger verify failed"):
        sign_gold_metric(
            GoldMetricDef(metric_id="m", name="n", sql="SELECT 1 AS total", steward_id="s"),
            cortex_append=lambda **kw: _Resp(),
            cortex_verify=lambda: type("V", (), {"ok": False, "first_break": "led_1"})(),
            actor="svc_steward",
        )


def test_hash_equal_to_entry_id_is_refused() -> None:
    """F52(b) closed as refuse, not as silent degradation."""
    from dms_executor.promote import sign_gold_metric

    class _Resp:
        entry_id = "led_same"
        hash = "led_same"

    with pytest.raises(ValueError, match="hash equal to entry_id"):
        sign_gold_metric(
            GoldMetricDef(metric_id="m", name="n", sql="SELECT 1 AS total", steward_id="s"),
            cortex_append=lambda **kw: _Resp(),
            cortex_verify=lambda: type("V", (), {"ok": True})(),
            actor="svc_steward",
        )


def test_verify_fail_on_run_is_http_4xx_and_promote_does_not_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Customer surface: append returns entry_id+hash, verify says broken → 400, no promote."""
    import dms_executor
    from cortex_client.models import LedgerAppendResponse, LedgerVerifyResponse
    from dms_api import deps

    class _FakeCortex:
        def ledger_append(self, req):  # noqa: ANN001
            return LedgerAppendResponse(entry_id="led_1", hash="sha256:abc")

        def verify_ledger(self):
            return LedgerVerifyResponse(ok=False, first_break="led_1", checked=1)

    promoted: list[Any] = []

    def _capture_promote(*args, **kwargs):
        promoted.append((args, kwargs))
        raise AssertionError("run_promote must not run when verify fails")

    monkeypatch.setattr(dms_executor, "run_promote", _capture_promote)

    app = client.app
    app.dependency_overrides[deps.get_cortex_client] = lambda: _FakeCortex()

    try:
        resp = client.post(
            "/v1/pipelines/run",
            json={
                "yaml_text": (
                    "target: gold.sales_total\n"
                    "sources: [silver.sales]\n"
                    "lineage: aggregate\n"
                    "lineage_reason: metric aggregate for test\n"
                ),
                "gold_metric": {
                    "metric_id": "m_revenue",
                    "name": "Revenue",
                    "sql": "SELECT 1 AS total",
                },
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 400, f"expected 4xx, got {resp.status_code} {resp.text[:300]}"
    detail = str(resp.json().get("detail", "")).lower()
    assert "verify" in detail or "ledger" in detail
    assert promoted == [], "gold promote ran despite a broken ledger verify"


def test_append_and_verify_ok_still_signs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path stub: append + verify ok → signed metric returned from /gold/sign."""
    from cortex_client.models import LedgerAppendResponse, LedgerVerifyResponse
    from dms_api import deps

    class _FakeCortex:
        def ledger_append(self, req):  # noqa: ANN001
            return LedgerAppendResponse(entry_id="led_ok", hash="sha256:okhash")

        def verify_ledger(self):
            return LedgerVerifyResponse(ok=True, checked=2)

    app = client.app
    app.dependency_overrides[deps.get_cortex_client] = lambda: _FakeCortex()
    try:
        resp = client.post(
            "/v1/pipelines/gold/sign",
            json={
                "metric_id": "m_revenue",
                "name": "Revenue",
                "sql": "SELECT 1 AS total",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body["is_signed"] is True
    assert body["ledger_entry_id"] == "led_ok"
    assert body["signature"] == "sha256:okhash"
    assert body["signature"] != body["ledger_entry_id"]


GOLD_YAML = (
    "target: gold.sales_total\n"
    "sources: [silver.sales]\n"
    "lineage: aggregate\n"
    "lineage_reason: metric aggregate for test\n"
)


def _looks_signed() -> GoldMetricDef:
    return GoldMetricDef(
        metric_id="m",
        name="n",
        sql="SELECT 1 AS total",
        steward_id="s",
        signed_at="2026-01-01T00:00:00Z",
        ledger_entry_id="led_1",
        signature="sha256:abc",
    )


def test_gold_promote_gate_refuses_without_chain_readback(tmp_path: Path) -> None:
    """EPIC-025: is_signed is a claim. The gate must verify, not trust the fields."""
    from dms_executor.pipeline_loader import PipelineLoadError, load_pipeline_yaml
    from dms_executor.promote import run_promote

    signed = _looks_signed()
    assert signed.is_signed is True
    with pytest.raises(PipelineLoadError, match="verify is required"):
        run_promote(load_pipeline_yaml(GOLD_YAML), path=tmp_path / "wh.duckdb", gold_metric=signed)


def test_gold_promote_gate_refuses_when_verify_not_ok(tmp_path: Path) -> None:
    from dms_executor.pipeline_loader import PipelineLoadError, load_pipeline_yaml
    from dms_executor.promote import run_promote

    with pytest.raises(PipelineLoadError, match="verify failed"):
        run_promote(
            load_pipeline_yaml(GOLD_YAML),
            path=tmp_path / "wh.duckdb",
            gold_metric=_looks_signed(),
            cortex_verify=lambda: type("V", (), {"ok": False})(),
        )


def test_gold_promote_gate_refuses_when_cortex_unreachable(tmp_path: Path) -> None:
    from dms_executor.pipeline_loader import PipelineLoadError, load_pipeline_yaml
    from dms_executor.promote import run_promote

    def _boom() -> None:
        raise RuntimeError("connection refused")

    with pytest.raises(PipelineLoadError, match="unreachable"):
        run_promote(
            load_pipeline_yaml(GOLD_YAML),
            path=tmp_path / "wh.duckdb",
            gold_metric=_looks_signed(),
            cortex_verify=_boom,
        )


def test_verify_fail_at_promote_gate_is_http_4xx_after_sign_succeeded(
    client: TestClient,
) -> None:
    """Construction verify succeeding is not enough: the gate must read the chain back."""
    from cortex_client.models import LedgerAppendResponse, LedgerVerifyResponse
    from dms_api import deps

    class _FakeCortex:
        def __init__(self) -> None:
            self.n = 0

        def ledger_append(self, req):  # noqa: ANN001
            return LedgerAppendResponse(entry_id="led_1", hash="sha256:abc")

        def verify_ledger(self):
            self.n += 1
            if self.n == 1:
                return LedgerVerifyResponse(ok=True, checked=1)
            return LedgerVerifyResponse(ok=False, first_break="led_1", checked=1)

    fake = _FakeCortex()
    app = client.app
    app.dependency_overrides[deps.get_cortex_client] = lambda: fake
    try:
        resp = client.post(
            "/v1/pipelines/run",
            json={
                "yaml_text": GOLD_YAML,
                "gold_metric": {
                    "metric_id": "m_revenue",
                    "name": "Revenue",
                    "sql": "SELECT 1 AS total",
                },
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 400, f"expected 4xx, got {resp.status_code} {resp.text[:300]}"
    detail = str(resp.json().get("detail", "")).lower()
    assert "verify" in detail or "ledger" in detail
    assert "unsigned" in detail or "refused" in detail


def test_unreachable_cortex_at_promote_gate_is_http_4xx(
    client: TestClient,
) -> None:
    """Unreachable Cortex at the gate must refuse gold, not proceed."""
    from cortex_client.models import LedgerAppendResponse, LedgerVerifyResponse
    from dms_api import deps

    class _FakeCortex:
        def __init__(self) -> None:
            self.n = 0

        def ledger_append(self, req):  # noqa: ANN001
            return LedgerAppendResponse(entry_id="led_1", hash="sha256:abc")

        def verify_ledger(self):
            self.n += 1
            if self.n == 1:
                return LedgerVerifyResponse(ok=True, checked=1)
            raise RuntimeError("connection refused")

    fake = _FakeCortex()
    app = client.app
    app.dependency_overrides[deps.get_cortex_client] = lambda: fake
    try:
        resp = client.post(
            "/v1/pipelines/run",
            json={
                "yaml_text": GOLD_YAML,
                "gold_metric": {
                    "metric_id": "m_revenue",
                    "name": "Revenue",
                    "sql": "SELECT 1 AS total",
                },
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 400, f"expected 4xx, got {resp.status_code} {resp.text[:300]}"
    detail = str(resp.json().get("detail", "")).lower()
    assert "unreachable" in detail
