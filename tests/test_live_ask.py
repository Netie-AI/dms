"""Live ask path — fake Cortex client (bind once, refusals)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from cortex_client.models import AskResponse
from cortex_contract.execution import QueryResult
from dms_api.app import create_app
from dms_core.ask import AskServiceError
from dms_executor import Executor
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient


@dataclass
class FakeCortex:
    submits: list[Any]
    asks: list[Any]
    submit_result: QueryResult | None = None
    submit_error: Exception | None = None
    ask_response: AskResponse | None = None
    ask_error: Exception | None = None

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        if self.submit_error is not None:
            raise self.submit_error
        return self.submit_result or QueryResult(ok=True, status="bound", run_id="run-1")

    def ask(self, req: Any) -> AskResponse:
        self.asks.append(req)
        if self.ask_error is not None:
            raise self.ask_error
        # A top-5 question answered with one row and ``SELECT 1`` describes an
        # answer that cannot exist, and E10 (FF-01) is right to refuse it. The
        # fixture now returns a shape its own question could actually produce:
        # a grouped, ranked, multi-row result. Tests that care about session
        # binding are unaffected; tests that care about answer shape now have a
        # fixture that is not quietly lying to them.
        return self.ask_response or AskResponse(
            answer="Top 5 SKUs by revenue, highest first.",
            badge="certified",
            sql_used=(
                "SELECT sku, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS sales_value_myr "
                "FROM transactions WHERE txn_type = 'OUT' "
                "GROUP BY sku ORDER BY sales_value_myr DESC LIMIT 5"
            ),
            rows=[
                {"sku": "SKU-00397", "sales_value_myr": 726158.36},
                {"sku": "SKU-00183", "sales_value_myr": 581836.43},
                {"sku": "SKU-00171", "sales_value_myr": 538201.10},
                {"sku": "SKU-00042", "sales_value_myr": 401002.75},
                {"sku": "SKU-00311", "sales_value_myr": 388940.12},
            ],
            assumptions="fixture",
            audit_id="aud-1",
            route="sql",
        )


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    m = ManifestMinter()
    # Avoid OpenVault — inject a stub key via private API if present
    key = MagicMock()
    key.kid = "test-kid"
    key.sign.return_value = "dGVzdA"  # base64url-ish stub; mint may still fail verify upstream
    # Prefer patching mint_manifest to return a prebuilt object when signing is heavy
    from cortex_contract.execution import Manifest

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def test_live_ask_binds_once_per_session(minter: ManifestMinter) -> None:
    fake = FakeCortex(submits=[], asks=[])
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env1 = exe.live_ask("Top 5 selling SKUs by revenue", session_id="ses_fixed")
    env2 = exe.live_ask("Divide the revenue by 5", session_id="ses_fixed")
    assert len(fake.submits) == 1
    assert len(fake.asks) == 2
    assert env1["ask_mode"] == "live"
    assert env2["session_id"] == "ses_fixed"
    assert env1["badge"] == "L0_CERTIFIED"


def test_bind_pool_mismatch(minter: ManifestMinter) -> None:
    fake = FakeCortex(
        submits=[],
        asks=[],
        submit_result=QueryResult(ok=False, status="pool_mismatch", error="pool_mismatch"),
    )
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    with pytest.raises(AskServiceError) as caught:
        exe.bind_session(exe.demo_acl(session_id="ses_x"))
    assert caught.value.code == "pool_mismatch"


def test_live_ask_rebinds_on_session_expired(minter: ManifestMinter) -> None:
    class Transient:
        def __init__(self) -> None:
            self.submits: list[Any] = []
            self.asks: list[Any] = []
            self._ask_n = 0

        def submit(self, req: Any) -> QueryResult:
            self.submits.append(req)
            return QueryResult(ok=True, status="bound", run_id="run-x")

        def ask(self, req: Any) -> AskResponse:
            self.asks.append(req)
            self._ask_n += 1
            if self._ask_n == 1:
                raise RuntimeError("session_expired: binding expired")
            # A non-abstaining answer must carry sql_used and a value (E3), the
            # same as the sibling fake above. The bare `answer="ok"` stub built
            # an envelope that assert_envelope_valid rightly refuses, so the
            # rebind path failed on envelope shape instead of on rebinding.
            return AskResponse(
                answer="Total revenue was RM 10.00.",
                badge="certified",
                sql_used="SELECT 1",
                rows=[{"revenue_myr": 10.0}],
                assumptions="fixture",
                route="sql",
                audit_id="a2",
            )

    fake = Transient()
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask("What was total revenue?", session_id="ses_rebind")
    assert env["ask_mode"] == "live"
    assert len(fake.submits) == 2  # initial bind + rebind after expiry
    assert len(fake.asks) == 2


def test_chat_live_mode_pool_mismatch_http(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeCortex(
        submits=[],
        asks=[],
        submit_result=QueryResult(ok=False, status="pool_mismatch", error="nope"),
    )
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    settings_mod.get_settings.cache_clear()

    app = create_app()
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = fake
    client = TestClient(app)
    r = client.post(
        "/v1/chat/ask",
        json={"question": "Top 5", "space_id": "sp_q3_audit", "session_id": "ses_http"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "pool_mismatch"
    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    settings_mod.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# #43 — a slow engine is not a forbidden one.
#
# The status used to be `409 if code in {session_unbound, session_expired}
# else 403`, so 403 was the default and every unclassified engine failure was
# reported as a permission refusal. A cold engine's first submit times out,
# classifies as `submit_failed`, and therefore told the user their own upload
# was forbidden. That is the demo's first question, on a cold laptop.
# ---------------------------------------------------------------------------


def _live_client(minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, fake: FakeCortex):
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    app = create_app()
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = fake
    return TestClient(app)


def _ask(client) -> Any:
    return client.post(
        "/v1/chat/ask",
        json={"question": "Top 5", "space_id": "sp_q3_audit", "session_id": "ses_t"},
    )


def test_e12_http_ask_demotes_ranking_for_a_total(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/chat/ask: one-number ask, grouped ranking back -> ABSTAIN (E12)."""
    fake = FakeCortex(
        submits=[],
        asks=[],
        ask_response=AskResponse(
            answer="FOOD_COLD 67,710,506.66; CHEMICALS 61,894,503.52",
            badge="query_skill",
            sql_used=(
                "SELECT category, SUM(quantity_kg * unit_cost_myr) AS total_value_myr "
                "FROM inventory GROUP BY category LIMIT 1000"
            ),
            rows=[
                {"category": "FOOD_COLD", "total_value_myr": 67710506.66},
                {"category": "CHEMICALS", "total_value_myr": 61894503.52},
            ],
            audit_id="aud_e12_http",
            route="query_skill",
        ),
    )
    client = _live_client(minter, monkeypatch, fake)
    r = client.post(
        "/v1/chat/ask",
        json={
            "question": "What is total inventory quantity?",
            "space_id": "sp_q3_audit",
            "session_id": "ses_e12",
        },
    )
    assert r.status_code == 200
    env = r.json()
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert not env["values"]
    assert not env["rows"]
    assert "67,710,506.66" not in (env.get("text") or "")


def test_a_submit_timeout_is_not_reported_as_forbidden(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #43 shape: cold engine, first submit times out.

    403 and 504 demand opposite responses from a user. One is permanent and
    about who they are; the other clears on retry. Reporting the second as the
    first is a degradation wearing a policy decision's clothes.
    """
    fake = FakeCortex(
        submits=[],
        asks=[],
        submit_result=QueryResult(ok=False, status="submit_failed", error="timed out"),
    )
    r = _ask(_live_client(minter, monkeypatch, fake))

    assert r.status_code != 403, (
        "a timeout was reported as Forbidden - the user is told they lack "
        "permission when the engine was merely slow"
    )
    assert r.status_code == 504
    assert r.json()["detail"].get("retryable") is not False


def test_an_unclassified_engine_failure_is_upstream_not_forbidden(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """403 must be earned by a code that means refusal, not fallen back to."""
    fake = FakeCortex(
        submits=[],
        asks=[],
        submit_result=QueryResult(ok=False, status="submit_failed", error="kaboom"),
    )
    r = _ask(_live_client(minter, monkeypatch, fake))

    assert r.status_code == 502, (
        f"unclassified engine failure returned {r.status_code}; an upstream "
        "fault is not a statement about the caller's rights"
    )


def test_a_real_policy_refusal_still_returns_403(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-0005 — narrowing 403 must not stop the boundary from refusing."""
    fake = FakeCortex(
        submits=[],
        asks=[],
        submit_result=QueryResult(
            ok=False, status="statement_not_allowed", error="statement_not_allowed"
        ),
    )
    r = _ask(_live_client(minter, monkeypatch, fake))

    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "statement_not_allowed"
