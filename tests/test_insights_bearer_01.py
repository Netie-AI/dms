"""BEARER-01 (dms#289): generate=true bearer + transport guards.

Seeded fake token only. Never reads a real secret or Secret Manager.
Does not stamp COMPLETE. Ranked (generate=false) lanes stay as they are.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from cortex_client import CortexClient
from cortex_client.compute import (
    INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT,
    INSIGHTS_FAIL_BEARER_MISSING,
    INSIGHTS_PATH,
    compute_insights,
    insights_fail_reason,
)
from cortex_client.insights import (
    DEMO_VIEWER_KEY,
    generate_bearer_refuse,
    insights_get,
    insights_post,
    redact_secrets,
)
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_curated import judge  # noqa: E402

# Seeded fake. Not a real OpenVault token. Never log or echo this in answers.
_FAKE = "seeded-test-token-bearer01"
_LOOPBACK = "http://127.0.0.1:8010"
_REMOTE_HTTP = "http://203.0.113.10:8010"
_REMOTE_HTTPS = "https://cortex.example.test"
_CERTIFIED = {
    "ok": True,
    "status": "CERTIFIED",
    "values": [{"sku_count": 3}],
    "answer": "There are 3 skus.",
    "query_sql": "SELECT COUNT(*) AS sku_count FROM lots",
    "live_5000_ci": False,
}


class _Resp:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return dict(self._body)


class _Capture:
    """Records every HTTP call. One map for insights.py request() and compute.py post/get."""

    def __init__(self, calls: list[dict[str, Any]], body: dict[str, Any] | None = None) -> None:
        self.calls = calls
        self.body = dict(body or _CERTIFIED)

    def __call__(self, *a: Any, timeout: Any = None, **k: Any) -> _Capture:
        return self

    def __enter__(self) -> _Capture:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _Resp:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "json": json,
                "params": params,
            }
        )
        return _Resp(200, self.body)

    def post(
        self,
        url: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Resp:
        return self.request("POST", url, headers=headers, json=json)

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Resp:
        return self.request("GET", url, headers=headers, params=params)


def _generate_posts(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in calls:
        url = str(row.get("url") or "").rstrip("/")
        if not url.endswith(INSIGHTS_PATH):
            continue
        if str(row.get("method") or "POST").upper() != "POST":
            continue
        body = row.get("json") or {}
        if body.get("generate") is True:
            out.append(row)
    return out


def _blob(*parts: Any) -> str:
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (dict, list)):
            chunks.append(json.dumps(part, default=str))
        else:
            chunks.append(str(part))
    return "\n".join(chunks)


def _tiny_ontology() -> Ontology:
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    o.add_object("lot", "lots", ["lot_id"])
    o.add_measure("revenue", "sale", "SUM(f.amount)")
    return o


def _submit_trap(sql: str) -> Any:
    raise AssertionError(f"generate refuse executed SQL: {sql[:80]}")


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_bearer01", hash="hash_bearer01")


def _ask_envelope(compute: Any) -> dict[str, Any] | None:
    return maybe_generative_ask(
        "What is revenue by product category?",
        grantable={"sales", "lots"},
        compute=compute,
        submit=_submit_trap,
        ledger_append=_ledger_ok,
        ontology=_tiny_ontology(),
        warehouse=None,
        bind_on_miss=True,
    )


def _assert_named_abstain(env: dict[str, Any] | None, reason: str, *secrets: str) -> None:
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["values"] == []
    assert env["rows"] == []
    text = str(env.get("text") or "")
    assert reason in text
    assert "cannot certify" in text.lower()
    notes = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert reason in notes or reason in text
    assert judge({"expect": "l0"}, env) == "ABSTAIN"
    assert judge({"expect": "refuse"}, env) == "ABSTAIN"
    assert judge({"expect": "refuse"}, env) != "WRONG"
    leaked = _blob(env)
    for secret in secrets:
        assert secret not in leaked
        assert secret not in text


def test_guard_names_and_transport_units() -> None:
    assert generate_bearer_refuse(_FAKE, _LOOPBACK) is None
    assert generate_bearer_refuse(_FAKE, "http://localhost:8010") is None
    assert generate_bearer_refuse(_FAKE, "http://[::1]:8010") is None
    assert generate_bearer_refuse(_FAKE, _REMOTE_HTTPS) is None
    assert generate_bearer_refuse("", _LOOPBACK) == INSIGHTS_FAIL_BEARER_MISSING
    assert generate_bearer_refuse("  ", _LOOPBACK) == INSIGHTS_FAIL_BEARER_MISSING
    assert generate_bearer_refuse(None, _LOOPBACK) == INSIGHTS_FAIL_BEARER_MISSING
    assert generate_bearer_refuse(DEMO_VIEWER_KEY, _LOOPBACK) == INSIGHTS_FAIL_BEARER_MISSING
    assert (
        generate_bearer_refuse(_FAKE, _REMOTE_HTTP) == INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT
    )
    assert generate_bearer_refuse(_FAKE, _LOOPBACK, missing_none=False) is None
    assert generate_bearer_refuse(None, _LOOPBACK, missing_none=False) is None


def test_loopback_generate_sends_bearer_header() -> None:
    calls: list[dict[str, Any]] = []
    cap = _Capture(calls)
    with (
        patch("cortex_client.compute.httpx.Client", cap),
        patch("cortex_client.insights.httpx.Client", cap),
    ):
        compute_out = compute_insights(_LOOPBACK, question="how many skus?", api_key=_FAKE)
        post_out = insights_post(
            _LOOPBACK, intent="how many skus", generate=True, api_key=_FAKE
        )
        client = CortexClient(_LOOPBACK, api_key=_FAKE)
        client.compute_insights("how many skus?")
    gens = _generate_posts(calls)
    assert gens, calls
    for row in gens:
        assert row["headers"]["Authorization"] == f"Bearer {_FAKE}"
        assert row["headers"]["X-API-Key"] == _FAKE
        assert "api_key" not in (row.get("json") or {})
    assert compute_out is not None
    assert post_out["status"] == "CERTIFIED"
    assert _FAKE not in _blob(compute_out, post_out)


def test_https_non_loopback_generate_sends_bearer_header() -> None:
    calls: list[dict[str, Any]] = []
    cap = _Capture(calls)
    with (
        patch("cortex_client.compute.httpx.Client", cap),
        patch("cortex_client.insights.httpx.Client", cap),
    ):
        compute_insights(_REMOTE_HTTPS, question="how many skus?", api_key=_FAKE)
        insights_post(_REMOTE_HTTPS, intent="how many skus", generate=True, api_key=_FAKE)
    gens = _generate_posts(calls)
    assert gens, calls
    for row in gens:
        assert row["headers"]["Authorization"] == f"Bearer {_FAKE}"
        assert str(row["url"]).startswith("https://")


def test_fake_token_absent_from_logs_error_answer_and_envelope(caplog: Any) -> None:
    caplog.set_level(logging.DEBUG)
    calls: list[dict[str, Any]] = []
    cap = _Capture(calls)
    with (
        patch("cortex_client.compute.httpx.Client", cap),
        patch("cortex_client.insights.httpx.Client", cap),
    ):
        out = compute_insights(_LOOPBACK, question="how many skus?", api_key=_FAKE)
        hosted = insights_post(
            _LOOPBACK, intent="how many skus", generate=True, api_key=_FAKE
        )
    env = _ask_envelope(
        lambda _c: compute_insights(_LOOPBACK, question="how many skus?", api_key="")
    )
    err = redact_secrets(f"Authorization: Bearer {_FAKE} boom", extra=_FAKE)
    leaked = _blob(caplog.text, out, hosted, env, err)
    assert _FAKE not in leaked
    assert _FAKE not in err
    assert "[redacted]" in err
    assert "LIVE_KEY" not in leaked
    gens = _generate_posts(calls)
    assert gens
    assert gens[0]["headers"]["Authorization"] == f"Bearer {_FAKE}"
    _assert_named_abstain(env, INSIGHTS_FAIL_BEARER_MISSING, _FAKE, DEMO_VIEWER_KEY)


def test_ranked_generate_false_still_posts_with_demo_key() -> None:
    """Ranked / consume-without-generate keeps today's demo-key behaviour (dms#273)."""
    calls: list[dict[str, Any]] = []
    cap = _Capture(calls)
    with patch("cortex_client.insights.httpx.Client", cap):
        insights_post(_LOOPBACK, intent="how many skus", generate=False, api_key=DEMO_VIEWER_KEY)
        insights_get(_LOOPBACK, api_key=DEMO_VIEWER_KEY)
    assert calls
    assert _generate_posts(calls) == []
    headers = calls[0]["headers"]
    assert headers["Authorization"] == f"Bearer {DEMO_VIEWER_KEY}"


def _refuse_cases() -> list[tuple[str, str | None, str, str]]:
    return [
        ("empty", "", _LOOPBACK, INSIGHTS_FAIL_BEARER_MISSING),
        ("demo", DEMO_VIEWER_KEY, _LOOPBACK, INSIGHTS_FAIL_BEARER_MISSING),
        ("http_remote", _FAKE, _REMOTE_HTTP, INSIGHTS_FAIL_BEARER_INSECURE_TRANSPORT),
    ]


def test_missing_demo_and_insecure_generate_abstain_with_zero_calls(caplog: Any) -> None:
    caplog.set_level(logging.DEBUG)
    for label, key, url, reason in _refuse_cases():
        calls: list[dict[str, Any]] = []
        cap = _Capture(calls)
        with (
            patch("cortex_client.compute.httpx.Client", cap),
            patch("cortex_client.insights.httpx.Client", cap),
        ):
            compute_out = compute_insights(url, question="how many skus?", api_key=key)
            hosted = insights_post(url, intent="how many skus", generate=True, api_key=key)
            client_out = CortexClient(url, api_key=key).compute_insights("how many skus?")
            env = _ask_envelope(lambda _c, payload=compute_out: payload)
        assert calls == [], f"{label} leaked outbound {calls}"
        assert _generate_posts(calls) == []
        assert compute_out is not None
        assert insights_fail_reason(compute_out) == reason
        assert client_out is not None
        assert insights_fail_reason(client_out) == reason
        assert hosted["status"] == "ABSTAIN"
        assert hosted.get("insights_fail") == reason
        assert hosted.get("values") == []
        _assert_named_abstain(env, reason, _FAKE, DEMO_VIEWER_KEY)
        leaked = _blob(caplog.text, compute_out, hosted, client_out, env)
        assert _FAKE not in leaked, label
        assert "ov_" not in leaked.lower()


def test_insights_post_none_key_generate_abstains_without_http() -> None:
    calls: list[dict[str, Any]] = []
    cap = _Capture(calls)
    with patch("cortex_client.insights.httpx.Client", cap):
        body = insights_post(_LOOPBACK, intent="how many skus", generate=True, api_key=None)
    assert calls == []
    assert body["status"] == "ABSTAIN"
    assert body.get("insights_fail") == INSIGHTS_FAIL_BEARER_MISSING
    assert _FAKE not in _blob(body)
