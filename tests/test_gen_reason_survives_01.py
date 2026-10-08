"""GEN-REASON-STAMP-01: a guard-rejected generate retry keeps its named reason.

Executor.live_ask, real Insights client, Cortex generate bodies mocked.
Question is curated l0 ``ops_shipment_cost_syn`` (exact pack text). On
38afd6bd the live_ask relabel rewrites these abstains to exact-match miss /
pack-metric miss. #390 deletes that rewrite.

``tests/test_insights_budget_01.py::test_timeout_env_set_names_the_leg``
covers a budget stop inside ``maybe_generative_ask`` only. It does not call
``Executor.live_ask``. The control here is that path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import yaml
from cortex_client.compute import compute_insights
from cortex_client.models import AskResponse, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import RESERVED_PARAM_AS_OF, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.gen_path_refuse import customer_abstain_text
from dms_executor.manifest import ManifestMinter, SessionAcl

# Warehouse Ops. shipments is granted; Finance is not.
_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_RANKING: dict[str, Any] = {
    "ok": True,
    "phase": "ontology",
    "metrics": [
        {
            "id": "cost_by_destination",
            "kind": "metric",
            "where": {"tables": ["shipments", "locations"]},
        },
        {
            "id": "count_by_destination",
            "kind": "metric",
            "where": {"tables": ["shipments", "locations"]},
        },
        {"id": "delayed_count", "kind": "metric", "where": {"tables": ["shipments"]}},
    ],
}
_GROUP_BY_1 = (
    "SELECT l.location_code, SUM(s.cost_myr) AS total_cost_myr "
    "FROM shipments s JOIN locations l ON s.destination_location_id = l.location_id "
    "GROUP BY 1 ORDER BY 2 DESC"
)
_HOSTILE = (
    "SELECT destination_location_id, SUM(cost_myr) FROM shipments "
    "GROUP BY destination_location_id; DROP TABLE shipments"
)
_AS_OF = (
    "SELECT destination_location_id, SUM(cost_myr) AS total_cost_myr FROM shipments "
    "WHERE expected_arrival <= $as_of GROUP BY destination_location_id"
)


def _pack_question(qid: str) -> str:
    path = Path(__file__).parent / "fixtures" / "curated_ceo" / "questions.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for row in data.get("questions") or []:
        if isinstance(row, dict) and row.get("id") == qid:
            question = str(row.get("question") or "").strip()
            if question:
                return question
    raise AssertionError(f"{qid} missing from the curated pack")


Q = _pack_question("ops_shipment_cost_syn")


def _nothing() -> dict[str, Any]:
    """Leg 1: generate ran, no SQL, ranking attached (retry is eligible)."""
    return {
        "ok": False,
        "status": "REFUSE",
        "phase": "ontology",
        "intent": Q,
        "values": [],
        "ontology": _RANKING,
        "generative": {
            "ok": False,
            "sql": None,
            "valid": False,
            "climb": {"final": "EMPTY"},
            "refuse_reason": "FreeRoute generative-ask refused",
        },
        "served_provider": None,
        "served_model": None,
    }


def _sql(sql: str) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "ABSTAIN",
        "phase": "generate",
        "intent": Q,
        "values": [],
        "sql_used": sql,
        "ontology": _RANKING,
        "generative": {"ok": True, "sql": sql, "valid": True, "climb": {}},
        "served_provider": "groq",
        "served_model": "openai/gpt-oss-120b",
    }


class _Resp:
    headers: dict[str, str] = {}

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._body


class _Http:
    """Scripted httpx.Client. 'timeout' raises ReadTimeout on that POST."""

    def __init__(self, posts: list[Any]) -> None:
        self.posts = list(posts)
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, *_a: Any, timeout: Any = None, **_k: Any) -> _Http:
        return self

    def __enter__(self) -> _Http:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def post(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
        retry = bool(isinstance(json, dict) and json.get("generate_retry"))
        self.calls.append(("POST", retry))
        nxt = self.posts.pop(0) if self.posts else "timeout"
        if nxt == "timeout":
            raise httpx.ReadTimeout("scripted timeout")
        return _Resp(nxt)

    def get(self, url: str, params: Any = None, headers: Any = None) -> _Resp:
        self.calls.append(("GET", False))
        return _Resp({"ok": True, "phase": "ontology", "ontology": _RANKING})


class _Cortex:
    """Same seam as live_ask: compute_insights is the real client."""

    def __init__(self, posts: list[Any]) -> None:
        self.http = _Http(posts)
        self.submits: list[Any] = []

    def compute_insights(self, question: str, **kw: Any) -> dict[str, Any] | None:
        with patch("cortex_client.compute.httpx.Client", self.http):
            return compute_insights(
                "http://127.0.0.1:8010",
                question=question,
                session_id=kw.get("session_id"),
                space_id=kw.get("space_id"),
                ontology=kw.get("ontology"),
            )

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        return QueryResult(ok=True, status="bound", run_id="run_bind")

    def ledger_append(self, req: Any) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_ok", hash="hash_ok")

    def ask(self, req: Any) -> AskResponse:
        return AskResponse(
            answer="contract ask",
            badge="abstain",
            sql_used=None,
            rows=[],
            audit_id="aud_ask",
            route="abstain",
        )


def _minter() -> ManifestMinter:
    """Mint stub. Same shape as tests/test_live_ask.py::minter."""
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-08T00:00:00+00:00",
            expires_at="2026-10-08T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return minter


def _live(tmp_path: Path, posts: list[Any]) -> tuple[dict[str, Any], _Cortex]:
    cortex = _Cortex(posts)
    db = tmp_path / "w.duckdb"
    ensure_demo_warehouse(db)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=db)  # type: ignore[arg-type]
    try:
        env = exe.live_ask(Q, space_id=_OPS, session_id="ses_gen_reason")
    finally:
        exe.close()
    return env, cortex


def _named_reason(env: dict[str, Any]) -> str:
    """abstain_reason field, else the GEN-01 assumption. Empty if neither."""
    raw = env.get("abstain_reason")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    for note in env.get("assumptions") or []:
        text = str(note).strip()
        if text.startswith("GEN-01: "):
            return text[len("GEN-01: ") :]
    return ""


def _assert_kept(env: dict[str, Any], cortex: _Cortex, reason: str) -> None:
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["badge"] == "ABSTAIN"
    got = _named_reason(env)
    assert got == reason
    assert got != "curated_pack_metric_miss"
    assert "exact-match miss" not in got
    rendered = str(env.get("text") or "")
    assert "exact-match miss" not in rendered
    assert "pack-metric miss" not in rendered
    assert cortex.submits == []
    assert any(kind == "POST" and retry for kind, retry in cortex.http.calls)
    if reason == RESERVED_PARAM_AS_OF:
        assert env["text"] == reason
        assert env["abstain_reason"] == reason
    else:
        assert env["text"] == customer_abstain_text(reason)
        assert f"GEN-01: {reason}" in env["assumptions"]


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        (_GROUP_BY_1, "unhonored_qualifier:group_by=destination"),
        (_HOSTILE, "validate:hostile_sql:statement_not_allowed"),
        (_AS_OF, RESERVED_PARAM_AS_OF),
    ],
    ids=["group_by_1", "hostile_sql", "reserved_as_of"],
)
def test_guard_rejected_retry_keeps_named_reason(
    sql: str, reason: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_INSIGHTS_TIMEOUT_S", raising=False)
    monkeypatch.delenv("DMS_INSIGHTS_CALL_CAP", raising=False)
    env, cortex = _live(tmp_path, [_nothing(), _sql(sql)])
    _assert_kept(env, cortex, reason)


def test_budget_stop_keeps_named_reason_through_live_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """insights_timeout:retry through live_ask. Not the maybe_generative_ask case.

    Regression guard, not a must-fail: passes on 38afd6bd and 72df50d8 because
    #393's exemption already let budget stops through.
    """
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", "60")
    env, cortex = _live(tmp_path, [_nothing(), "timeout"])
    _assert_kept(env, cortex, "insights_timeout:retry")
    assert env["insights_fail"] == "insights_timeout:retry"


def test_call_cap_keeps_named_reason_through_live_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """insights_call_cap through live_ask. Its own stub, not the golden unsure.

    Regression guard, not a must-fail: the cap stop already survives live_ask
    on 71b38947. The retry POST must not run, and it is not called guided.
    """
    monkeypatch.setenv("DMS_INSIGHTS_CALL_CAP", "1")
    monkeypatch.delenv("DMS_INSIGHTS_TIMEOUT_S", raising=False)
    env, cortex = _live(tmp_path, [_nothing()])
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["badge"] == "ABSTAIN"
    assert _named_reason(env) == "insights_call_cap:1"
    assert "GEN-01: insights_call_cap:1" in env["assumptions"]
    assert env["insights_fail"] == "insights_call_cap:1"
    assert env["text"] == customer_abstain_text("insights_call_cap:1")
    assert cortex.submits == []
    assert not any(retry for _kind, retry in cortex.http.calls)
