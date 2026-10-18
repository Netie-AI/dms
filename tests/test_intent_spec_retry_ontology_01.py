"""INTENT-SPEC-01 / A2-02: retried writer SQL gets the declared-ontology check.

With ``DMS_INTENT_SPEC=1`` a checker mismatch retries the writer. The first
writer SQL was checked against the lake's declared ontology (A2-02); the
retried SQL was only grant-checked, so a retry that joined through a failed
declared link was served as L2_VALIDATED with an orphan-skewed number. The
same SQL as a first reply abstains. Both now abstain.

Every assertion is on the customer envelope: rendered text, rows, badge, and
for the route case the JSON body of POST /v1/chat/ask. Synthetic data only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dms_executor.pipeline_failure as tickets
import pytest
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology
from fastapi.testclient import TestClient
from test_a2_02_declared_ontology_refuses_sql import (
    GRANTS,
    _assert_names_orphan_link,
    _ontology,
    _seed,
)
from test_intent_spec_01 import _cited, _Harness, _tickets

_QUESTION = "lowest 3 regions by revenue"
# Customers only: never touches the failed order_customer link. DESC, so the
# spec (asc) rejects it and the writer is asked again.
_WRITER_SQL = (
    "SELECT region, COUNT(*) AS n FROM customers GROUP BY region ORDER BY n DESC LIMIT 3"
)
# Matches the spec (ASC, 3) but joins through the failed link.
_RETRY_JOIN_SQL = (
    "SELECT c.region, SUM(o.amount_myr) AS revenue FROM orders o "
    "JOIN customers c ON o.customer_id = c.customer_id "
    "GROUP BY c.region ORDER BY revenue ASC LIMIT 3"
)


class _OntoHarness(_Harness):
    def __init__(self, lake: Path, onto: Ontology | None) -> None:
        super().__init__(lake)
        self.onto = onto
        self.spec = _cited(direction="asc", direction_span="lowest", n=3, n_span="3")
        self.writer_sql = _WRITER_SQL
        self.retry_sql = _RETRY_JOIN_SQL

    def ask(self, question: str) -> dict[str, Any] | None:
        return maybe_generative_ask(
            question,
            warehouse=self.lake,
            grantable=set(GRANTS),
            compute=self.compute,
            submit=self.submit,
            ledger_append=lambda _payload: SimpleNamespace(entry_id="led_spec", hash="hash_spec"),
            ontology=self.onto,
            bind_on_miss=False,
        )


def _make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, orphan: bool,
          declared: bool) -> _OntoHarness:
    lake = tmp_path / "lake.duckdb"
    _seed(lake, orphan=orphan)
    monkeypatch.setenv("DMS_INTENT_SPEC", "1")
    return _OntoHarness(lake, _ontology() if declared else None)


def _regions(env: dict[str, Any]) -> dict[str, float]:
    return {r["region"]: float(r["revenue"]) for r in env["rows"]}


def test_retry_sql_over_failed_declared_link_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    tickets._reset_pipeline_failures()
    h = _make(tmp_path, monkeypatch, orphan=True, declared=True)
    with caplog.at_level(logging.WARNING, logger="dms_executor.pipeline_failure"):
        env = h.ask(_QUESTION)
    assert env is not None
    assert_envelope_valid(env)
    _assert_names_orphan_link(env)
    assert env["values"] == []
    assert "revenue" not in json.dumps(env["rows"])
    assert h.submits == [], "the retried join must not execute"
    assert h.calls.count("sql_writer") >= 2, "the writer was retried"
    rows = _tickets(caplog)
    assert len(rows) == 1
    assert rows[0]["reason"] == "ontology_unverified"
    assert rows[0]["stage"] == "intent_spec"
    assert rows[0]["retries"] >= 1


def test_control_same_join_as_first_reply_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _make(tmp_path, monkeypatch, orphan=True, declared=True)
    h.writer_sql = _RETRY_JOIN_SQL
    env = h.ask(_QUESTION)
    assert env is not None
    assert_envelope_valid(env)
    _assert_names_orphan_link(env)
    assert h.submits == []


def test_retry_still_serves_on_a_clean_declared_ontology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _make(tmp_path, monkeypatch, orphan=False, declared=True)
    env = h.ask(_QUESTION)
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] != "ABSTAIN"
    assert env["text"]
    assert _regions(env) == {"North": 400.0, "South": 600.0}
    assert [r["region"] for r in env["rows"]] == ["North", "South"]
    assert h.submits == [_RETRY_JOIN_SQL]


def test_retry_still_serves_when_no_ontology_was_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BIRD shape: same orphan data, nothing declared, so nothing to refuse."""
    h = _make(tmp_path, monkeypatch, orphan=True, declared=False)
    env = h.ask(_QUESTION)
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert _regions(env) == {"North": 400.0, "South": 600.0}
    assert h.submits == [_RETRY_JOIN_SQL]


def test_retry_over_failed_link_abstains_on_the_chat_ask_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The JSON body of POST /v1/chat/ask, not only the executor dict."""
    h = _make(tmp_path, monkeypatch, orphan=True, declared=True)

    class _Ask:
        def live_ask(self, question: str, **_k: Any) -> dict[str, Any]:
            env = h.ask(question)
            assert env is not None
            return env

    app = create_app()
    app.state.ask_service = _Ask()
    app.state.cortex = object()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    r = TestClient(app).post(
        "/v1/chat/ask", json={"question": _QUESTION, "session_id": "ses_spec_retry_onto"}
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    _assert_names_orphan_link(env)
    assert env["values"] == []
    assert h.submits == []
