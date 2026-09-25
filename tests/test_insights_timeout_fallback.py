"""Insights ask timeout: configurable bound, and ranking still runs on timeout.

Live 52-question prove: a Cortex FreeRoute generate takes 13-53s, the ask
lane waited 8s, and 13/52 asks returned ``insights_timeout`` before the
no-model ontology ranking (``GET /v1/insights/ontology``) was even asked.
Here the bound is Settings/env-configurable (default 60s), and a generate
timeout falls through to the ranking lookup. ``insights_timeout`` stays the
named abstain reason only when generate timed out AND ranking is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cortex_client import CortexClient
from cortex_client.compute import (
    COMPUTE_PATH,
    GENERATE_TIMED_OUT,
    INSIGHTS_ASK_TIMEOUT_ENV,
    INSIGHTS_ASK_TIMEOUT_SECONDS,
    INSIGHTS_FAIL_TIMEOUT,
    INSIGHTS_PATH,
    compute_insights,
    insights_ask_timeout_seconds,
)
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology

_BASE = "http://127.0.0.1:8010"
_KEY = "steward-key-for-tests"


class _Http:
    """Fake httpx.Client: generate POST may time out; ontology GET may rank."""

    def __init__(
        self,
        *,
        post_timeouts: int = 0,
        get_timeout: bool = False,
        ranking: list[str] | None = None,
        generate: dict[str, Any] | None = None,
    ) -> None:
        self.post_timeouts = post_timeouts
        self.get_timeout = get_timeout
        self.ranking = ranking
        self.generate = generate if generate is not None else {"phase": "generate"}
        self.calls: list[str] = []
        self.client_timeouts: list[Any] = []

    def __call__(self, *a: Any, timeout: Any = None, **k: Any) -> _Http:
        self.client_timeouts.append(timeout)
        return self

    def __enter__(self) -> _Http:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    @staticmethod
    def _resp(body: dict[str, Any]) -> Any:
        return SimpleNamespace(status_code=200, json=lambda: body)

    def post(self, url: str, json: Any = None, headers: Any = None) -> Any:
        self.calls.append("POST " + url)
        if str(url).endswith(COMPUTE_PATH):
            raise AssertionError("ask lane must never POST /dms/query")
        if self.post_timeouts > 0:
            self.post_timeouts -= 1
            raise httpx.ReadTimeout("generate still running in Cortex")
        return self._resp(self.generate)

    def get(self, url: str, params: Any = None, headers: Any = None) -> Any:
        self.calls.append("GET " + url)
        if self.get_timeout:
            raise httpx.ReadTimeout("ontology ranking unreachable")
        if self.ranking is None:
            return self._resp({"phase": "ontology", "ontology": {"metrics": []}})
        return self._resp(
            {"phase": "ontology", "ontology": {"metrics": [{"id": m} for m in self.ranking]}}
        )


def test_default_bound_clears_a_measured_generate() -> None:
    assert INSIGHTS_ASK_TIMEOUT_SECONDS == 60.0
    assert INSIGHTS_ASK_TIMEOUT_SECONDS > 53.0  # slowest generate on the prove


def test_bound_is_env_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INSIGHTS_ASK_TIMEOUT_ENV, "90")
    assert insights_ask_timeout_seconds() == 90.0
    for bad in ("", "abc", "0", "-5", "inf"):
        monkeypatch.setenv(INSIGHTS_ASK_TIMEOUT_ENV, bad)
        assert insights_ask_timeout_seconds() == INSIGHTS_ASK_TIMEOUT_SECONDS, bad
    monkeypatch.delenv(INSIGHTS_ASK_TIMEOUT_ENV)
    assert insights_ask_timeout_seconds() == INSIGHTS_ASK_TIMEOUT_SECONDS
    assert insights_ask_timeout_seconds(12.5) == 12.5


def test_compute_insights_uses_env_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INSIGHTS_ASK_TIMEOUT_ENV, "45")
    fake = _Http(ranking=["cq_cold_storage"])
    with patch("cortex_client.compute.httpx.Client", fake):
        compute_insights(_BASE, question="Which locations are cold storage?", api_key=_KEY)
    assert fake.client_timeouts == [45.0]


def test_settings_bound_reaches_cortex_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_api.settings import Settings

    monkeypatch.setenv("DMS_INSIGHTS_ASK_TIMEOUT_SECONDS", "75")
    assert Settings().dms_insights_ask_timeout_seconds == 75.0
    monkeypatch.delenv("DMS_INSIGHTS_ASK_TIMEOUT_SECONDS")
    assert Settings().dms_insights_ask_timeout_seconds == 60.0
    fake = _Http(ranking=["cq_cold_storage"])
    with patch("cortex_client.compute.httpx.Client", fake):
        client = CortexClient(_BASE, timeout=120.0, api_key=_KEY, insights_timeout=75.0)
        client.compute_insights("Which locations are cold storage?")
        capped = CortexClient(_BASE, timeout=20.0, api_key=_KEY, insights_timeout=75.0)
        capped.compute_insights("Which locations are cold storage?")
    # Capped by the contract timeout; never unbounded.
    assert fake.client_timeouts == [75.0, 20.0]


def test_generate_timeout_still_runs_ontology_ranking() -> None:
    fake = _Http(post_timeouts=1, ranking=["cq_cold_storage"])
    with patch("cortex_client.compute.httpx.Client", fake):
        out = compute_insights(_BASE, question="Which locations are cold storage?", api_key=_KEY)
    assert out is not None
    assert out.get("insights_fail") is None
    assert out.get(GENERATE_TIMED_OUT) is True
    assert out["ontology"]["metrics"][0]["id"] == "cq_cold_storage"
    assert fake.calls == [
        f"POST {_BASE}{INSIGHTS_PATH}",
        f"GET {_BASE}{INSIGHTS_PATH}/ontology",
    ]
    assert out["generate_legs"]["legs"] == [{"returned": "timeout"}]


def test_generate_and_ranking_both_fail_stays_named_timeout() -> None:
    # Guard, passes on origin/main by design: the new timeout fallthrough must
    # not turn a timeout with no usable ranking into a miss or a green badge.
    fake = _Http(post_timeouts=1, get_timeout=True)
    with patch("cortex_client.compute.httpx.Client", fake):
        out = compute_insights(_BASE, question="Which locations are cold storage?", api_key=_KEY)
    assert out is not None
    assert out.get("insights_fail") == INSIGHTS_FAIL_TIMEOUT
    fake2 = _Http(post_timeouts=1, ranking=None)
    with patch("cortex_client.compute.httpx.Client", fake2):
        out2 = compute_insights(_BASE, question="Which locations are cold storage?", api_key=_KEY)
    assert out2 is not None
    assert out2.get("insights_fail") == INSIGHTS_FAIL_TIMEOUT


def test_retry_timeout_keeps_first_leg_ranking() -> None:
    # First generate answers with no SQL; the ranked retry times out.
    fake = _Http(ranking=["cq_cold_storage"], generate={"phase": "generate", "generative": {}})
    real_post = fake.post
    count = {"n": 0}

    def post(url: str, json: Any = None, headers: Any = None) -> Any:
        count["n"] += 1
        if count["n"] == 2:
            fake.calls.append("POST(retry) " + url)
            raise httpx.ReadTimeout("retry generate still running")
        return real_post(url, json=json, headers=headers)

    fake.post = post  # type: ignore[method-assign]
    with patch("cortex_client.compute.httpx.Client", fake):
        out = compute_insights(_BASE, question="Which locations are cold storage?", api_key=_KEY)
    assert out is not None
    assert out.get("insights_fail") is None
    assert out["ontology"]["metrics"][0]["id"] == "cq_cold_storage"
    assert any(c.startswith("POST(retry)") for c in fake.calls)


def _submit(db: Path) -> Any:
    def submit(sql: str) -> Any:
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_to", output={"rows": rows})

    return submit


def _ledger(_p: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_to", hash="hash_to_not_entry")


def _envelope(tmp_path: Path, question: str, fake: _Http) -> dict[str, Any] | None:
    db = tmp_path / "to.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))

    def compute(ctx: dict[str, Any]) -> dict[str, Any] | None:
        with patch("cortex_client.compute.httpx.Client", fake):
            return compute_insights(_BASE, question=question, ontology=ctx, api_key=_KEY)

    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers", "shipments"},
        compute=compute,
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
    )


def test_generate_timeout_answers_from_ranking_on_envelope(tmp_path: Path) -> None:
    fake = _Http(post_timeouts=1, ranking=["cq_cold_storage"])
    env = _envelope(tmp_path, "Which locations are cold storage?", fake)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["plan_origin"] == "ontology_ranking"
    assert env["rows"] == [{"location_location_code": "WH-C"}]
    assert "WH-C" in env["text"]
    assert env["generate_legs"]["legs"] == [{"returned": "timeout"}]
    assert env.get("audit_id")


def test_generate_timeout_no_ranking_is_named_abstain_on_envelope(tmp_path: Path) -> None:
    # Guard, passes on origin/main by design: the new timeout fallthrough must
    # not turn a timeout with no usable ranking into a miss or a green badge.
    fake = _Http(post_timeouts=1, get_timeout=True)
    env = _envelope(tmp_path, "Which locations are cold storage?", fake)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert INSIGHTS_FAIL_TIMEOUT in " ".join(env["assumptions"])


def test_generate_timeout_unusable_ranking_is_named_abstain(tmp_path: Path) -> None:
    # Guard, passes on origin/main by design: the new timeout fallthrough must
    # not turn a timeout with no usable ranking into a miss or a green badge.
    # Ranking answered, but only with an id DMS cannot compile for this ask.
    fake = _Http(post_timeouts=1, ranking=["active_alerts_by_severity"])
    env = _envelope(tmp_path, "How many florbs did wibble sell?", fake)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["assumptions"] == [f"GEN-01: {INSIGHTS_FAIL_TIMEOUT}"]
