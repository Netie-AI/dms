"""INSIGHTS-BUDGET-01: DMS_INSIGHTS_TIMEOUT_S, named timeout leg, DMS->Cortex call cap.

Unset env is byte-identical to 6f7139a3: 8s, bare ``insights_timeout``, no new
stamp. Set env names the leg (``insights_timeout:generate|ontology|retry``).
A timeout or cap stop is a GEN-01 ABSTAIN with no rows, never L2_VALIDATED.
The cap counts DMS->Cortex generate POSTs only, not Cortex-internal model calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import cortex_client.compute as compute
import httpx
import pytest
from cortex_client import CortexClient
from cortex_client.compute import compute_insights, compute_query, insights_fail_payload
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import Ontology
from test_gen_restore_01 import _grantable, _ledger_ok, _ontology, _seed

_TIMEOUT_ENV = "DMS_INSIGHTS_TIMEOUT_S"
_CAP_ENV = "DMS_INSIGHTS_CALL_CAP"
_GOLDEN = Path(__file__).parent / "fixtures/insights_budget_01/timeout_unset_6f7139a3.json"
_ROOT = "http://127.0.0.1:8010"
_Q = "How many SKUs do we have in inventory?"
_EMPTY = {
    "status": "ABSTAIN",
    "phase": "generate",
    "generative": {"ok": False, "sql": None, "climb": {"final": "EMPTY"}},
}
_SQL = {
    "status": "ABSTAIN",
    "phase": "generate",
    "generative": {"ok": True, "sql": "SELECT COUNT(*) AS n FROM lots"},
}
_RANK = {
    "ok": True,
    "phase": "ontology",
    "ontology": {"ok": True, "metrics": [{"id": "sku_count"}]},
}


class _Resp:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _Scripted:
    """generate -> EMPTY, ontology GET -> sku_count ranking, retry -> SQL."""

    def __init__(self, *, time_out: str | None = None) -> None:
        self.time_out = time_out
        self.calls: list[str] = []
        self.timeouts: list[Any] = []

    def __call__(self, *_a: Any, timeout: Any = None, **_k: Any) -> _Scripted:
        self.timeouts.append(timeout)
        return self

    def __enter__(self) -> _Scripted:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def _leg(self, name: str, body: dict[str, Any]) -> _Resp:
        self.calls.append(name)
        if name == self.time_out:
            raise httpx.ReadTimeout(f"{name} timed out")
        return _Resp(body)

    def post(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
        if json and json.get("generate_retry"):
            return self._leg("retry", _SQL)
        return self._leg("generate", _EMPTY)

    def get(self, url: str, params: Any = None, headers: Any = None) -> _Resp:
        return self._leg("ontology", _RANK)


def _compute(fake: _Scripted) -> dict[str, Any] | None:
    with patch("cortex_client.compute.httpx.Client", fake):
        return compute_insights(
            _ROOT, question=_Q, ontology={"intent_slots": {"measure": "sku_count"}}
        )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(_CAP_ENV, raising=False)


@pytest.fixture()
def wh(tmp_path: Path) -> Path:
    path = tmp_path / "budget.duckdb"
    _seed(path)
    return path


@pytest.fixture()
def onto(wh: Path) -> Ontology:
    loaded = load_verified_ontology(wh, _ontology())
    assert loaded is not None
    return loaded


def _ask(payload: dict[str, Any] | None, onto: Ontology, wh: Path) -> dict[str, Any]:
    def _submit(sql: str) -> Any:
        raise AssertionError(f"budget stop executed SQL: {sql}")

    env = maybe_generative_ask(
        _Q,
        warehouse=wh,
        grantable=_grantable(),
        compute=lambda _c: payload,
        submit=_submit,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    return env


def _assert_budget_abstain(env: dict[str, Any], reason: str) -> None:
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert env["sql_used"] is None
    assert env["insights_fail"] == reason
    assert f"GEN-01: {reason}" in env["assumptions"]
    assert env["text"] == (
        "I cannot certify an ontology-grounded query for that question "
        f"(gap: {reason}), so I am not executing one."
    )


# --- unset env: byte-identical to 6f7139a3 ---


def test_unset_timeout_abstain_is_byte_identical_to_6f7139a3(
    onto: Ontology, wh: Path
) -> None:
    assert compute.insights_timeout_env_set() is False
    assert compute.insights_timeout_s() == 8.0
    got: dict[str, Any] = {}
    for leg in ("generate", "ontology", "retry"):
        payload = _compute(_Scripted(time_out=leg))
        env = dict(_ask(payload, onto, wh))
        env["as_of"] = "<as_of>"
        got[leg] = {"payload": payload, "envelope": env}
        assert payload is not None and payload["insights_fail"] == "insights_timeout"
        assert "insights_fail" not in env
    assert json.dumps(got, sort_keys=True, indent=1) + "\n" == _GOLDEN.read_text()


def test_unset_env_is_eight_seconds_and_unchanged_legs() -> None:
    assert (compute.insights_timeout_s(), compute.insights_call_cap()) == (8.0, 2)
    fake = _Scripted()
    out = _compute(fake)
    assert fake.timeouts == [8.0]
    assert fake.calls == ["generate", "ontology", "retry"]
    assert out is not None and str(out.get("query_sql") or "").startswith("SELECT")
    assert "insights_fail" not in out


# --- set env ---


def test_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TIMEOUT_ENV, "45")
    fake = _Scripted()
    _compute(fake)
    assert fake.timeouts == [45.0]


@pytest.mark.parametrize("raw", ["60.5", "180"])
def test_timeout_env_clamps_to_60_and_logs(
    raw: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(_TIMEOUT_ENV, raw)
    fake = _Scripted()
    client = CortexClient(_ROOT, timeout=120.0)
    with caplog.at_level(logging.WARNING, logger="cortex_client.compute"):
        with patch("cortex_client.compute.httpx.Client", fake):
            client.compute_insights(_Q, ontology={"intent_slots": {"measure": "sku_count"}})
        _compute(fake)
    assert fake.timeouts == [60.0, 60.0]
    assert "DMS_INSIGHTS_TIMEOUT_S" in caplog.text
    assert f"requested {raw}" in caplog.text and "effective 60" in caplog.text


@pytest.mark.parametrize("raw", ["nan", "inf", "-5", "0", "", "abc"])
def test_invalid_timeout_env_falls_back_to_eight_and_logs(
    raw: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(_TIMEOUT_ENV, raw)
    fake = _Scripted()
    with caplog.at_level(logging.WARNING, logger="cortex_client.compute"):
        assert compute.insights_timeout_s() == 8.0
        _compute(fake)
    assert fake.timeouts == [8.0]
    assert "DMS_INSIGHTS_TIMEOUT_S" in caplog.text and "effective 8.0" in caplog.text


def test_both_use_sites_read_one_resolved_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """compute_insights' default (~:1136) and the httpx.Client (~:1044) agree."""
    monkeypatch.setenv(_TIMEOUT_ENV, "75")
    resolved = compute.insights_timeout_s()
    assert resolved == 60.0
    fake = _Scripted()
    with patch("cortex_client.compute.httpx.Client", fake):
        compute_insights(_ROOT, question=_Q)
        compute_query(_ROOT, question=_Q, timeout=75.0, dms_query=False)
        CortexClient(_ROOT, timeout=120.0).compute_insights(_Q)
    assert fake.timeouts == [resolved, resolved, resolved]


@pytest.mark.parametrize(("raw", "effective"), [("30", 30.0), ("120", 60.0)])
def test_live_client_path_honours_timeout_env(
    raw: str, effective: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CortexClient.compute_insights (client.py ~:210) is the live product path."""
    monkeypatch.setenv(_TIMEOUT_ENV, raw)
    fake = _Scripted()
    with patch("cortex_client.compute.httpx.Client", fake):
        CortexClient(_ROOT, timeout=120.0).compute_insights(_Q)
    assert fake.timeouts == [effective]
    assert compute.insights_timeout_s() == effective


def test_only_insights_legs_read_env_law_keys_identity_stay_8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ontology (client.py ~:240) reads the env; law/keys/identity stay min(8.0, ...)."""
    monkeypatch.setenv(_TIMEOUT_ENV, "60")
    seen: dict[str, float] = {}

    def _get(_base: str, tail: str = "", **kw: Any) -> dict[str, Any]:
        seen[tail or "/"] = kw["timeout"]
        return {}

    monkeypatch.setattr("cortex_client.client.insights_get", _get)
    client = CortexClient(_ROOT, timeout=120.0)
    client.insights_law()
    client.insights_keys()
    client.insights_identity()
    client.insights_ontology("how many skus")
    assert seen == {"/": 8.0, "/keys": 8.0, "/identity": 8.0, "/ontology": 60.0}


@pytest.mark.parametrize("leg", ["generate", "ontology", "retry"])
def test_timeout_env_set_names_the_leg(
    leg: str, onto: Ontology, wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_TIMEOUT_ENV, "30")
    fake = _Scripted(time_out=leg)
    out = _compute(fake)
    reason = f"insights_timeout:{leg}"
    assert fake.timeouts == [30.0]
    assert fake.calls[-1] == leg
    assert out is not None and out["insights_fail"] == reason
    for key in ("query_sql", "query_plan", "ontology", "generative"):
        assert key not in out, key
    env = _ask(out, onto, wh)
    _assert_budget_abstain(env, reason)
    if leg != "ontology":
        assert env["generate_legs"]["legs"][-1]["returned"] == "timeout"


def test_call_cap_abstains_before_retry(
    onto: Ontology, wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_CAP_ENV, "1")
    fake = _Scripted()
    out = _compute(fake)
    assert fake.calls == ["generate", "ontology"]
    assert out is not None and out["insights_fail"] == "insights_call_cap:1"
    for key in ("query_sql", "query_plan", "ontology"):
        assert key not in out, key
    _assert_budget_abstain(_ask(out, onto, wh), "insights_call_cap:1")


@pytest.mark.parametrize(
    ("raw", "cap"),
    [("2", 2), ("9", 4), ("0", 2), ("1.5", 2), ("off", 2), ("nan", 2)],
)
def test_call_cap_env_has_no_off_value(
    raw: str, cap: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above the ceiling clamps to 4; invalid falls back to 2. Never uncapped."""
    monkeypatch.setenv(_CAP_ENV, raw)
    monkeypatch.setenv(_TIMEOUT_ENV, "60")
    assert compute.insights_call_cap() == cap
    fake = _Scripted()
    out = _compute(fake)
    assert fake.calls.count("generate") + fake.calls.count("retry") == 2
    assert out is not None and "insights_fail" not in out


@pytest.mark.parametrize("reason", ["insights_timeout:retry", "insights_call_cap:2"])
def test_budget_stop_never_widens_partial_payload(
    reason: str, onto: Ontology, wh: Path
) -> None:
    """A stop carrying leftover SQL + ranking still abstains with no rows."""
    partial = {**_SQL, "query_sql": _SQL["generative"]["sql"], "ontology": _RANK["ontology"]}
    _assert_budget_abstain(_ask(insights_fail_payload(reason, partial), onto, wh), reason)
