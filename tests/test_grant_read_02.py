"""GRANT-READ-02 / dms#307: refuse by name before any Cortex or model call.

(a) An unread or empty grant is a named ABSTAIN (grant_unreadable) with zero
Insights posts, zero Cortex asks and zero submits.
(b) A ticked table outside the grant is refused by name (R-0005), never
dropped while another lane answers.

CI fixtures, not live. Fake httpx transport only. No network.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from cortex_contract.execution import Manifest
from dms_core.ask import GroundingRefused
from dms_executor import Executor
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_grant_read_01 import (  # noqa: E402
    FINANCE,
    GEN_Q,
    SECRET_TABLE,
    _AskCortex,
    _CaptureHttp,
    _insights_bodies,
    _seed_secret,
    _store,
)

# Granted by no Space (STATUS A-0007). Never in FINANCE's grant.
UNGRANTED = "alerts"


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-01T00:00:00+00:00",
            expires_at="2026-10-01T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def _rig(
    tmp_path: Path, minter: ManifestMinter, name: str
) -> tuple[Executor, _AskCortex, list[dict[str, Any]]]:
    wh = _seed_secret(tmp_path / f"{name}.duckdb")
    posts: list[dict[str, Any]] = []
    cortex = _AskCortex(posts=posts)
    exe = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=minter,
        warehouse_path=wh,
        session_store=_store(wh),
    )
    return exe, cortex, posts


def _boom(self: Executor, *, space_id: str | None = None) -> list[str]:
    raise RuntimeError("grant unread")


def _empty(self: Executor, *, space_id: str | None = None) -> list[str]:
    return []


def _assert_no_calls(cortex: _AskCortex, posts: list[dict[str, Any]]) -> None:
    assert _insights_bodies(posts) == [], posts
    assert cortex.asks == [], cortex.asks
    assert cortex.submits == [], cortex.submits


def _assert_grant_abstain(env: dict[str, Any]) -> None:
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert "grant_unreadable" in env["text"]
    assert "nothing was sent to a model" in env["text"]


@pytest.mark.parametrize("ladder", ["product", "generative", "exact"])
def test_unread_grant_abstains_named_with_zero_calls(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, ladder: str
) -> None:
    exe, cortex, posts = _rig(tmp_path, minter, f"boom_{ladder}")
    monkeypatch.setattr(Executor, "grantable_tables", _boom)
    with patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)):
        env = exe.live_ask(
            GEN_Q, session_id=f"ses_boom_{ladder}", space_id=FINANCE, ask_path=ladder
        )
    _assert_grant_abstain(env)
    _assert_no_calls(cortex, posts)


@pytest.mark.parametrize("ladder", ["generative", "exact"])
def test_empty_grant_abstains_named_with_zero_calls(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, ladder: str
) -> None:
    exe, cortex, posts = _rig(tmp_path, minter, f"empty_{ladder}")
    monkeypatch.setattr(Executor, "grantable_tables", _empty)
    with patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)):
        env = exe.live_ask(
            GEN_Q, session_id=f"ses_empty_{ladder}", space_id=FINANCE, ask_path=ladder
        )
    _assert_grant_abstain(env)
    _assert_no_calls(cortex, posts)


def test_empty_grant_on_product_lane_sends_no_empty_schema_generate(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readable, empty grant is a document-only Space on the product lane.

    No Insights generate call with an empty schema. The manifest-bound Cortex
    ask still runs, so doc RAG keeps answering (tests/test_rag_ask_boundary_
    envelope.py, unedited).
    """
    exe, cortex, posts = _rig(tmp_path, minter, "empty_product")
    monkeypatch.setattr(Executor, "grantable_tables", _empty)
    with patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)):
        env = exe.live_ask(GEN_Q, session_id="ses_empty_product", space_id=FINANCE)
    assert_envelope_valid(env)
    assert _insights_bodies(posts) == [], posts
    assert len(cortex.asks) == 1, cortex.asks


def test_ungranted_tick_is_refused_by_name_with_zero_calls(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    """At 87a9497 the generative lane dropped the tick and answered ABSTAIN.

    The product lane already refused it late, in demo_acl, so it is not pinned
    here (it passes on the parent and would not discriminate).
    """
    ladder = "generative"
    exe, cortex, posts = _rig(tmp_path, minter, f"tick_{ladder}")
    assert UNGRANTED not in exe.grantable_tables(space_id=FINANCE)
    with (
        patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)),
        pytest.raises(GroundingRefused) as caught,
    ):
        exe.live_ask(
            GEN_Q,
            session_id=f"ses_tick_{ladder}",
            space_id=FINANCE,
            tables=[UNGRANTED],
            ask_path=ladder,
        )
    assert caught.value.ungrantable == [UNGRANTED]
    assert UNGRANTED in caught.value.message
    assert "Pick a different file" in caught.value.message
    _assert_no_calls(cortex, posts)


def test_mixed_tick_refuses_the_ungranted_table_not_drops_it(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    exe, cortex, posts = _rig(tmp_path, minter, "mixed")
    assert SECRET_TABLE in exe.grantable_tables(space_id=FINANCE)
    with (
        patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)),
        pytest.raises(GroundingRefused) as caught,
    ):
        exe.live_ask(
            GEN_Q,
            session_id="ses_mixed",
            space_id=FINANCE,
            tables=[SECRET_TABLE, UNGRANTED],
            ask_path="generative",
        )
    assert caught.value.ungrantable == [UNGRANTED]
    _assert_no_calls(cortex, posts)

    # Control: the gate can pass. A granted tick is not refused, and with
    # nothing ticked a readable grant still reaches Insights. (A ticked table
    # skips Insights on the generative lane at 87a9497 too; not this ticket.)
    with patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)):
        ticked = exe.live_ask(
            GEN_Q,
            session_id="ses_mixed_ok",
            space_id=FINANCE,
            tables=[SECRET_TABLE],
            ask_path="generative",
        )
        open_ask = exe.live_ask(
            GEN_Q, session_id="ses_mixed_open", space_id=FINANCE, ask_path="generative"
        )
    for env in (ticked, open_ask):
        assert_envelope_valid(env)
        assert "grant_unreadable" not in env["text"]
    assert len(_insights_bodies(posts)) == 1, posts


# --- the customer envelope: POST /v1/chat/ask (CLAUDE.md 10a) ---------------


@pytest.fixture()
def live_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_HARNESS_ASK_PATHS", "1")
    settings_mod.get_settings.cache_clear()
    yield
    settings_mod.get_settings.cache_clear()


def _client(exe: Executor, cortex: _AskCortex) -> Any:
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    app.state.ask_service = exe
    app.state.cortex = cortex
    return TestClient(app)


def test_http_unread_grant_is_a_named_abstain_envelope(
    tmp_path: Path, minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch, live_settings: Any
) -> None:
    exe, cortex, posts = _rig(tmp_path, minter, "http_boom")
    monkeypatch.setattr(Executor, "grantable_tables", _boom)
    client = _client(exe, cortex)  # before the httpx patch: TestClient is an httpx.Client
    with patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)):
        r = client.post(
            "/v1/chat/ask",
            json={"question": GEN_Q, "space_id": FINANCE, "session_id": "ses_http_boom"},
        )
    assert r.status_code == 200, r.text
    env = r.json()
    _assert_grant_abstain(env)
    assert env["demo_fallback_used"] is False
    _assert_no_calls(cortex, posts)


def test_http_ungranted_tick_is_refused_by_name_before_insights(
    tmp_path: Path, minter: ManifestMinter, live_settings: Any
) -> None:
    """Generative harness lane. At 87a9497 this was a 200 ABSTAIN, not a refusal."""
    exe, cortex, posts = _rig(tmp_path, minter, "http_tick")
    client = _client(exe, cortex)  # before the httpx patch: TestClient is an httpx.Client
    with patch("cortex_client.compute.httpx.Client", _CaptureHttp(posts)):
        r = client.post(
            "/v1/chat/ask",
            json={
                "question": GEN_Q,
                "space_id": FINANCE,
                "session_id": "ses_http_tick",
                "grounded_tables": [UNGRANTED],
                "ask_path": "generative",
            },
        )
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "grounding_not_grantable"
    assert detail["ungrantable_tables"] == [UNGRANTED]
    assert UNGRANTED in detail["message"]
    assert "Pick a different file" in detail["message"]
    _assert_no_calls(cortex, posts)
