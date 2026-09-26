"""KEY-01 (dms#273): the DMS API fails closed on a missing Cortex key.

``settings.cortex_api_key`` used to default to Cortex's published demo key,
and ``cortex_read`` fell back to it, so a DMS API with no key configured still
reached Cortex. Now there is no default and no fallback:

* live mode without the bannered demo fallback refuses to start, naming
  ``cortex_key_missing``;
* demo mode and the bannered fallback start with no Cortex client at all;
* the read surfaces refuse before any request;
* ``compute_query`` / ``compute_insights`` with no key make zero HTTP calls on
  both lanes (the ask lane abstains ``insights_bearer_missing``).

Cortex's published demo key counts as missing everywhere. Tests use a seeded
fake token, never a real credential.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cortex_client.compute import compute_insights, compute_query
from cortex_client.insights import (
    DEMO_VIEWER_KEY,
    INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT,
    INSIGHTS_FAIL_BEARER_MISSING,
)
from dms_executor.envelope import assert_envelope_valid
from fastapi.testclient import TestClient

_FAKE = "fake-key01-test-token"
#: The named refusal (cortex_client.insights.CORTEX_KEY_MISSING), spelled out so
#: each test runs, and fails on its own assertion, against the parent commit.
CORTEX_KEY_MISSING = "cortex_key_missing"
_LOOPBACK = "http://127.0.0.1:8010"
_REPO = Path(__file__).resolve().parents[1]
_MISSING = (None, "", "   ", DEMO_VIEWER_KEY)
_CORTEX_HOSTS = ("127.0.0.1:8010", "cortex.example.com")


class _NoCall:
    """httpx.Client stand-in that records any attempt to reach Cortex.

    ``patch("<module>.httpx.Client")`` replaces ``httpx.Client`` process-wide,
    so non-Cortex hosts (the OpenVault probe on :5000) see an unreachable
    network instead of a recorded Cortex call.
    """

    calls: list[str] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        return None

    def __enter__(self) -> _NoCall:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def close(self) -> None:
        return None

    def _hit(self, method: str, url: str) -> Any:
        if not any(host in str(url) for host in _CORTEX_HOSTS):
            raise httpx.ConnectError("offline in test")
        _NoCall.calls.append(f"{method} {url}")
        raise AssertionError(f"KEY-01: unkeyed DMS reached Cortex: {method} {url}")

    def get(self, url: str, *a: Any, **k: Any) -> Any:
        return self._hit("GET", url)

    def post(self, url: str, *a: Any, **k: Any) -> Any:
        return self._hit("POST", url)

    def request(self, method: str, url: str, *a: Any, **k: Any) -> Any:
        return self._hit(method, url)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    from dms_api import settings as settings_mod

    for name in ("CORTEX_API_KEY", "DMS_ASK_MODE", "DMS_DEMO_FALLBACK", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    _NoCall.calls = []
    settings_mod.get_settings.cache_clear()
    yield
    settings_mod.get_settings.cache_clear()


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    key: str | None,
    mode: str = "live",
    fallback: bool = False,
) -> None:
    from dms_api import settings as settings_mod

    if key is not None:
        monkeypatch.setenv("CORTEX_API_KEY", key)
    monkeypatch.setenv("DMS_ASK_MODE", mode)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "1" if fallback else "0")
    settings_mod.get_settings.cache_clear()


# -- settings: no default -----------------------------------------------------


def test_settings_have_no_default_cortex_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_api.settings import Settings

    monkeypatch.chdir(_REPO / "tests")  # no .env here
    assert Settings().cortex_api_key is None
    monkeypatch.setenv("CORTEX_API_KEY", _FAKE)
    assert Settings().cortex_api_key == _FAKE


# -- startup: live refuses, demo / fallback start without Cortex ---------------


@pytest.mark.parametrize("key", [None, "", DEMO_VIEWER_KEY])
def test_live_mode_without_key_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    from dms_api.app import create_app

    _configure(monkeypatch, key=key)
    app = create_app()
    with (
        patch("cortex_client.gate.httpx.Client", _NoCall),
        pytest.raises(RuntimeError, match=CORTEX_KEY_MISSING),
        TestClient(app),
    ):
        pass
    assert app.state.cortex is None  # no client was ever built to call Cortex
    assert _NoCall.calls == []


def test_live_mode_with_key_starts_and_forwards_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # Guard, passes on the parent by design. Positive control: the refusal
    # must not block a configured deployment.
    from dms_api.app import create_app

    _configure(monkeypatch, key=_FAKE)
    app = create_app()
    with TestClient(app):
        cortex = app.state.cortex
        assert cortex is not None
        assert cortex.api_key == _FAKE


def test_demo_mode_without_key_answers_with_no_cortex_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_api.app import create_app

    _configure(monkeypatch, key=None, mode="demo")
    app = create_app()
    with patch("cortex_client.gate.httpx.Client", _NoCall), TestClient(app) as client:
        assert app.state.cortex is None
        res = client.post("/v1/chat/ask", json={"question": "How many SKUs do we have?"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ask_mode"] == "demo"
    assert body["text"]
    assert _NoCall.calls == []


def test_live_fallback_without_key_serves_bannered_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_api.app import create_app

    _configure(monkeypatch, key=None, fallback=True)
    app = create_app()
    with patch("cortex_client.gate.httpx.Client", _NoCall), TestClient(app) as client:
        assert app.state.cortex is None
        res = client.post("/v1/chat/ask", json={"question": "How many SKUs do we have?"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert_envelope_valid(body)  # E6: demo fallback carries the banner
    assert body["demo_fallback_used"] is True
    assert "Cortex client missing" in " ".join(body["assumptions"])
    assert _NoCall.calls == []


# -- read surfaces refuse before any request ------------------------------------


@pytest.mark.parametrize("key", _MISSING)
def test_cortex_read_refuses_without_key(key: str | None) -> None:
    from dms_api.cortex_read import cortex_get

    with patch("dms_api.cortex_read.httpx.Client", _NoCall):
        out = cortex_get(_LOOPBACK, "/dms/ontology", api_key=key)
    assert out["ok"] is False
    assert out["error"] == CORTEX_KEY_MISSING
    assert "CORTEX_API_KEY" in out["hint"]
    assert DEMO_VIEWER_KEY not in str(out)
    assert _NoCall.calls == []


@pytest.mark.parametrize("path", ["/v1/ontology", "/v1/trust/summary"])
def test_read_routes_without_key_name_the_refusal(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from dms_api.app import create_app

    _configure(monkeypatch, key=None, mode="demo")
    with patch("dms_api.cortex_read.httpx.Client", _NoCall):
        res = TestClient(create_app()).get(path)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False
    assert body["error"] == CORTEX_KEY_MISSING
    assert _NoCall.calls == []


def test_cortex_read_sends_only_the_configured_key() -> None:
    # Guard, passes on the parent by design: a configured key is sent as is.
    from dms_api.cortex_read import cortex_get

    seen: list[dict[str, str]] = []

    class _Ok:
        def __init__(self, *a: Any, **k: Any) -> None:
            return None

        def __enter__(self) -> _Ok:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def get(self, url: str, headers: dict[str, str], params: Any = None) -> Any:
            seen.append(headers)

            class _R:
                status_code = 200

                @staticmethod
                def json() -> dict[str, Any]:
                    return {"objects": []}

            return _R()

    with patch("dms_api.cortex_read.httpx.Client", _Ok):
        out = cortex_get(_LOOPBACK, "/dms/ontology", api_key=_FAKE)
    assert out["ok"] is True
    assert seen == [{"X-API-Key": _FAKE}]


# -- ask lane: no key, no HTTP call, named abstain -------------------------------


@pytest.mark.parametrize("key", _MISSING)
def test_compute_insights_without_key_makes_no_call(key: str | None) -> None:
    # None fails on the parent (the missing_none exception). The empty, blank
    # and demo cases are dms#289 guards and pass there by design.
    with patch("cortex_client.compute.httpx.Client", _NoCall):
        out = compute_insights(_LOOPBACK, question="how many skus?", api_key=key)
    assert out is not None
    assert out["insights_fail"] == INSIGHTS_FAIL_BEARER_MISSING
    assert out["values"] == []
    assert _NoCall.calls == []


@pytest.mark.parametrize(
    ("key", "base"),
    [
        (None, _LOOPBACK),
        ("", _LOOPBACK),
        (DEMO_VIEWER_KEY, _LOOPBACK),
        (_FAKE, "http://cortex.example.com:8010"),
    ],
)
def test_compute_query_dms_query_lane_applies_the_same_refusals(
    key: str | None, base: str
) -> None:
    # Epic 17:33 item 2: the leftover dms_query=True lane used to skip the
    # refusal entirely. It now posts nothing (no generate, no /dms/query).
    with patch("cortex_client.compute.httpx.Client", _NoCall):
        out = compute_query(base, question="how many skus?", api_key=key, dms_query=True)
    assert out is None
    assert _NoCall.calls == []


def test_compute_query_insecure_transport_on_ask_lane_is_named() -> None:
    # Guard, passes on the parent by design (dms#289 refusal kept untouched).
    with patch("cortex_client.compute.httpx.Client", _NoCall):
        out = compute_query(
            "http://cortex.example.com:8010",
            question="how many skus?",
            api_key=_FAKE,
            dms_query=False,
        )
    assert out is not None
    assert out["insights_fail"] == INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT
    assert _NoCall.calls == []


def test_unkeyed_ask_envelope_abstains_named(tmp_path: Path) -> None:
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
    from dms_executor.ontology import demo_ontology

    db = tmp_path / "key01.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    submits: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any] | None:
        with patch("cortex_client.compute.httpx.Client", _NoCall):
            return compute_insights(_LOOPBACK, question=q, ontology=ctx, api_key=None)

    q = "Which locations are cold storage?"
    env = maybe_generative_ask(
        q,
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers", "shipments"},
        compute=compute,
        submit=lambda sql: submits.append(sql),
        ledger_append=lambda _p: None,
        ontology=onto,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert env.get("chart") is None
    assert "WH-C" not in env["text"]
    assert INSIGHTS_FAIL_BEARER_MISSING in " ".join(env["assumptions"])
    assert submits == []
    assert _NoCall.calls == []


# -- the published key is not a default anywhere --------------------------------


def test_demo_key_is_only_a_denylist_entry_in_product_code() -> None:
    hits: list[str] = []
    for root in ("apps", "packages"):
        for path in (_REPO / root).rglob("*.py"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if DEMO_VIEWER_KEY in line:
                    hits.append(f"{path.relative_to(_REPO)}:{n}: {line.strip()}")
    # The only occurrence is the constant generate_bearer_refuse and
    # cortex_key_missing refuse against. No default, no fallback.
    assert hits == [
        'packages/cortex_client/cortex_client/insights.py:21: '
        'DEMO_VIEWER_KEY = "dms-demo-viewer-key"'
    ], hits
