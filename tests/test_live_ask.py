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
        return self.ask_response or AskResponse(
            answer="Top SKU revenue was RM 10.00.",
            badge="certified",
            sql_used="SELECT 1",
            rows=[{"sku": "SKU-BETA", "sales_value_myr": 10.0}],
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


def test_chat_live_mode_pool_mismatch_http(minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch) -> None:
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
