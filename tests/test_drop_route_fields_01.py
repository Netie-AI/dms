"""DROP-ROUTE-FIELDS-01. Cortex requests do not carry model or strict.

Cortex has rejected unknown Insights fields since #277. The detail is
``not modelled by POST /v1/insights: model, strict``. Pin 279cbd85 still
ignores unknown fields. Routing stays in OpenVault; DMS only prompts.

Red on main (57d85c52): the generate body still has those keys, and the
current-schema stub answers 422. Green on head.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cortex_client.client import CortexClient
from cortex_client.compute import (
    _insights_body,
    _insights_generate_post,
    compute_insights,
    compute_query,
)
from cortex_client.insights import insights_post
from cortex_client.models import LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

# Cortex main InsightsWireIn (InsightsAskIn plus the extension). Unknown keys
# are 422. Pin 279cbd85 is InsightsAskIn only and drops unknown keys.
_PIN_FIELDS = frozenset(
    {
        "intent",
        "question",
        "ask",
        "generate",
        "session_id",
        "space_id",
        "consumer",
    }
)
_CURRENT_FIELDS = _PIN_FIELDS | {
    "mode",
    "model_preference",
    "ontology",
    "intent_slots",
    "query_plan",
    "ranked_metric",
    "generate_retry",
}
_SQL = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_QUESTION = "List cold storage location codes"
_LOOPBACK = "http://127.0.0.1:9"
_KEY = "ov_test_drop_route"


def _route_keys(body: dict[str, Any]) -> dict[str, Any]:
    return {key: body[key] for key in ("model", "strict") if key in body}


class _Resp:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {}
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body


class _Capture:
    def __init__(self) -> None:
        self.insights: list[dict[str, Any]] = []

    def client(self) -> type:
        cap = self

        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def post(
                self,
                url: str,
                json: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None,
            ) -> _Resp:
                if str(url).rstrip("/").endswith("/v1/insights"):
                    cap.insights.append(dict(json or {}))
                return _Resp(200, {"ok": True, "status": "ABSTAIN", "values": []})

            def request(
                self,
                method: str,
                url: str,
                headers: dict[str, str] | None = None,
                json: dict[str, Any] | None = None,
                params: dict[str, Any] | None = None,
            ) -> _Resp:
                if method.upper() == "POST" and str(url).rstrip("/").endswith("/v1/insights"):
                    cap.insights.append(dict(json or {}))
                return _Resp(200, {"ok": True, "status": "ABSTAIN", "values": []})

            def get(self, url: str, params: dict[str, Any] | None = None, **k: Any) -> _Resp:
                return _Resp(200, {"ok": True, "phase": "ontology", "ontology": {}})

        return _Client


def _patch_capture(monkeypatch: pytest.MonkeyPatch) -> _Capture:
    cap = _Capture()
    client = cap.client()
    monkeypatch.setattr("cortex_client.compute.httpx.Client", client)
    monkeypatch.setattr("cortex_client.insights.httpx.Client", client)
    return cap


def _assert_clean(bodies: list[tuple[str, dict[str, Any]]]) -> None:
    bad = [(name, _route_keys(body)) for name, body in bodies if _route_keys(body)]
    assert bad == []


def test_insights_bodies_omit_model_and_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every /v1/insights body DMS builds. Both keys absent on every path."""
    monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)
    monkeypatch.delenv("DMS_STRICT_MODEL", raising=False)
    monkeypatch.delenv("DMS_STRICT_PROVIDER", raising=False)
    cap = _patch_capture(monkeypatch)
    built = _insights_body(
        "how many skus",
        session_id="s",
        space_id="sp",
        ontology={"intent_slots": {"measure": "sku_count"}, "tables": ["inventory"]},
        sql_feedback={"previous_sql": "SELECT 1", "reason": "empty"},
    )
    insights_post(_LOOPBACK, intent="how many skus", generate=False, api_key=_KEY)
    insights_post(_LOOPBACK, intent="how many skus", generate=True, api_key=_KEY)
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    schema_built = _insights_body(
        "how many skus",
        session_id="s",
        space_id="sp",
        ontology={"schema_context": "tables: inventory(sku)"},
    )
    insights_post(
        _LOOPBACK,
        intent="how many skus",
        generate=True,
        api_key=_KEY,
        schema_context="tables: inventory(sku)",
    )
    monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)
    client = CortexClient(_LOOPBACK, api_key=_KEY, timeout=5.0)
    client.insights_ask(intent="how many skus", generate=True)
    compute_insights(_LOOPBACK, question="how many skus", api_key=_KEY, timeout=5.0)
    compute_query(_LOOPBACK, question="how many skus", api_key=_KEY, timeout=5.0)
    _insights_generate_post(
        cap.client()(),
        _LOOPBACK,
        {
            "intent": "how many skus",
            "question": "how many skus",
            "generate": True,
            "query_plan": {"measure": "sku_count"},
            "ranked_metric": "sku_count",
            "generate_retry": "ranked_slots",
            "model": "drop-me",
            "strict": True,
        },
        None,
    )
    named = [("built", built), ("schema_built", schema_built)]
    named.extend((f"sent_{i}", body) for i, body in enumerate(cap.insights))
    assert len(cap.insights) >= 7
    _assert_clean(named)


def _model_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "status": "ABSTAIN",
        "phase": "generate",
        "values": [],
        "sql": _SQL,
        "model_called": True,
        "generative": {
            "ok": True,
            "sql": _SQL,
            "stamp": {"task": "freeroute", "call_id": "ov-route"},
        },
    }


def _not_modelled(body: dict[str, Any], allowed: frozenset[str]) -> str | None:
    extras = sorted(set(body) - allowed)
    if not extras:
        return None
    return "not modelled by POST /v1/insights: " + ", ".join(extras)


class _Schema:
    """One Insights stub. ``mode`` is pin (ignore unknown) or current (422)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.posts: list[dict[str, Any]] = []
        self.statuses: list[int] = []
        self.details: list[str] = []
        self.reached_ov = False

    def client(self) -> type:
        stub = self

        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def post(
                self,
                url: str,
                json: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None,
            ) -> _Resp:
                body = dict(json or {})
                if not str(url).rstrip("/").endswith("/v1/insights"):
                    return _Resp(200, {"ok": True, "status": "ABSTAIN", "values": []})
                stub.posts.append(body)
                allowed = _PIN_FIELDS if stub.mode == "pin" else _CURRENT_FIELDS
                if stub.mode == "pin":
                    detail = None
                else:
                    detail = _not_modelled(body, allowed)
                if detail:
                    stub.statuses.append(422)
                    stub.details.append(detail)
                    return _Resp(
                        422,
                        {
                            "ok": False,
                            "status": "ABSTAIN",
                            "phase": "request",
                            "badge": "abstain",
                            "refuse_reason": "unknown_request_fields",
                            "detail": detail,
                            "answer": f"Abstained (unknown_request_fields): {detail}.",
                            "values": [],
                        },
                    )
                stub.statuses.append(200)
                stub.reached_ov = True
                return _Resp(200, _model_payload())

            def get(self, url: str, params: dict[str, Any] | None = None, **k: Any) -> _Resp:
                return _Resp(200, {"ok": True, "phase": "ontology", "ontology": {}})

        return _Client


class _Executing:
    def __init__(self, db: Path, inner: CortexClient) -> None:
        self._db = db
        self._inner = inner

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        return self._inner.compute_insights(question, **kwargs)

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_drop_bind")
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        con = connect_file(self._db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_drop_sql", output={"rows": rows})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_drop", hash="hash_drop")


def _minter() -> ManifestMinter:
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
            signature="dGVzdA",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return minter


def _gold(db: Path) -> list[str]:
    con = connect_file(db)
    try:
        cur = con.execute(_SQL)
        return sorted(str(row[0]) for row in cur.fetchall())
    finally:
        con.close()


def _ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> tuple[dict[str, Any], _Schema]:
    for name in (
        "DMS_SCHEMA_CONTEXT",
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_CCA_CASCADE",
        "DMS_HARNESS_ASK_PATHS",
        "DMS_STRICT_MODEL",
        "DMS_STRICT_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    db = tmp_path / "drop.duckdb"
    ensure_demo_warehouse(db)
    stub = _Schema(mode)
    monkeypatch.setattr("cortex_client.compute.httpx.Client", stub.client())
    inner = CortexClient(_LOOPBACK, api_key=_KEY, timeout=5.0)
    exe = Executor(
        cortex=_Executing(db, inner),  # type: ignore[arg-type]
        minter=_minter(),
        warehouse_path=db,
    )
    env = exe.live_ask(_QUESTION, session_id="ses_drop_route", ask_path="generative")
    return env, stub


def _served_codes(env: dict[str, Any]) -> list[str]:
    rows = env.get("rows") or []
    if not isinstance(rows, list):
        return []
    return sorted(str(row.get("location_code")) for row in rows if isinstance(row, dict))


def test_pinned_279cbd85_still_serves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin contract ignores unknown fields and still serves the model SQL.

    The body itself has no model or strict key.
    """
    env, stub = _ask(tmp_path, monkeypatch, "pin")
    assert_envelope_valid(env)
    assert stub.reached_ov is True
    assert stub.statuses == [200]
    assert env.get("abstained") is not True
    assert _served_codes(env) == _gold(tmp_path / "drop.duckdb")
    assert stub.posts
    _assert_clean([("pin", stub.posts[0])])


def test_current_cortex_schema_reaches_ov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Current Cortex rejects unknown fields. The AI lane still reaches OV.

    On main this is 422 ``not modelled by POST /v1/insights: model, strict``.
    """
    env, stub = _ask(tmp_path, monkeypatch, "current")
    assert stub.statuses == [200], stub.details
    assert stub.reached_ov is True
    assert_envelope_valid(env)
    assert env.get("abstained") is not True
    assert _served_codes(env) == _gold(tmp_path / "drop.duckdb")
    assert stub.posts
    _assert_clean([("current", stub.posts[0])])


def _values(db: Path, sql: str) -> list[str]:
    con = connect_file(db)
    try:
        cur = con.execute(sql)
        return sorted(str(cell) for row in cur.fetchall() for cell in row)
    finally:
        con.close()


class _Ranked:
    """Insights stub whose generate reply carries model SQL and a ranked metric.

    The SQL is what the model returned. The ranked metric is Cortex's
    retrieval opinion. DMS must serve the SQL when it validates and runs.
    """

    def __init__(self, sql: str, metric_id: str) -> None:
        self.payload = {
            "ok": True,
            "status": "ABSTAIN",
            "phase": "generate",
            "values": [],
            "query_sql": sql,
            "sql": sql,
            "plan_origin": "generate_sql",
            "ontology": {"metrics": [{"id": metric_id}]},
            "model_called": True,
            "generative": {"ok": True, "sql": sql, "stamp": {"task": "freeroute"}},
        }

    def client(self) -> type:
        stub = self

        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def post(
                self,
                url: str,
                json: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None,
            ) -> _Resp:
                if str(url).rstrip("/").endswith("/v1/insights"):
                    return _Resp(200, stub.payload)
                return _Resp(200, {"ok": True, "status": "ABSTAIN", "values": []})

            def get(self, url: str, params: dict[str, Any] | None = None, **k: Any) -> _Resp:
                return _Resp(200, {"ok": True, "phase": "ontology", "ontology": {}})

            def close(self) -> None:
                return None

        return _Client


def _http_ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    metric_id: str,
    sql: str = _SQL,
) -> tuple[dict[str, Any], Path]:
    """POST /v1/chat/ask on the generative lane. Model SQL plus a ranked metric."""
    from dms_api.app import create_app
    from dms_api.settings import Settings, get_settings
    from fastapi.testclient import TestClient

    for name in (
        "DMS_SCHEMA_CONTEXT",
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_CCA_CASCADE",
        "DMS_STRICT_MODEL",
        "DMS_STRICT_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    db = tmp_path / "shape.duckdb"
    ensure_demo_warehouse(db)
    monkeypatch.setattr("cortex_client.compute.httpx.Client", _Ranked(sql, metric_id).client())
    inner = CortexClient(_LOOPBACK, api_key=_KEY, timeout=5.0)
    cortex = _Executing(db, inner)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=db)  # type: ignore[arg-type]
    app = create_app()
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    res = TestClient(app).post(
        "/v1/chat/ask",
        json={"question": question, "session_id": "ses_drop_shape", "ask_path": "generative"},
    )
    assert res.status_code == 200, res.text
    env = res.json()
    assert_envelope_valid(env)
    return env, db


def _row_codes(env: dict[str, Any]) -> list[str]:
    rows = env.get("rows") or []
    assert isinstance(rows, list)
    return sorted(str(cell) for row in rows if isinstance(row, dict) for cell in row.values())


def _assert_model_sql_served(env: dict[str, Any], db: Path, sql: str = _SQL) -> None:
    """The model SQL ran. Its own rows are the answer, in rows and in text."""
    gold = _values(db, sql)
    assert gold
    assert env.get("abstained") is not True, env.get("text")
    assert env.get("plan_origin") == "generate_sql"
    assert _row_codes(env) == gold
    text = str(env.get("text") or "")
    assert all(cell in text for cell in gold), text
    assert "served_model" not in env


def test_question_words_do_not_discard_validated_model_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model SQL validated. "by code" in the question is context, not a rule.

    Red on c5df0e5e: _should_demote_shape_mismatch matched "by code" before
    submit and the rule ranked compile replaced the model SQL, serving an
    unasked load figure with plan_origin ontology_ranking.

    Main's post-run E10 check (envelope.py, unchanged here) still judges the
    executed one-row result. So the envelope is either the model's own rows or
    that abstain. It is never the rule compile's rows.
    """
    env, db = _http_ask(
        tmp_path,
        monkeypatch,
        "Which location is cold storage? Show it by code",
        "cq_sku_count",
    )
    text = str(env.get("text") or "")
    assert env.get("plan_origin") != "ontology_ranking"
    assert "ontology_ranking" not in json.dumps(env, default=str)
    assert "96.7" not in text
    if env.get("abstained"):
        assert env.get("rows") == []
        assert env.get("values") == []
        assert "single total" in text
    else:
        _assert_model_sql_served(env, db)


@pytest.mark.parametrize(
    ("question", "metric_id"),
    [
        ("How many SKUs in inventory?", "cq_sku_count"),
        ("List chemicals in inventory", "cq_chemicals_list"),
        ("Which suppliers have an audit overdue?", "cq_audit_overdue"),
    ],
)
def test_ranker_disagreement_does_not_replace_validated_model_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, question: str, metric_id: str
) -> None:
    """Model SQL that validated and never ran is not discarded for the rule compile.

    The ranker's metric reads other columns. That is a disagreement between a
    rule path and the AI path, and the rule path does not win it. Red on
    c5df0e5e: shape:columns climbed to the ranked compile.
    """
    env, db = _http_ask(tmp_path, monkeypatch, question, metric_id)
    _assert_model_sql_served(env, db)
    assert "ontology_ranking" not in json.dumps(env, default=str)


def test_uncompilable_ranked_metric_does_not_abstain_valid_model_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct abstain is for ungranted, PII and destructive SQL, not ranker gaps.

    Red on c5df0e5e: the ranker named a metric DMS cannot compile and the
    valid, granted model SQL was abstained as unknown_measure.
    """
    env, db = _http_ask(
        tmp_path,
        monkeypatch,
        "List active alerts across the warehouse network",
        "active_alerts",
    )
    _assert_model_sql_served(env, db)
    assumptions = " ".join(str(item) for item in (env.get("assumptions") or []))
    assert "unknown_measure" not in assumptions


def test_grouped_ask_one_row_select_is_not_swapped_for_the_rule_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-row ungrouped SELECT for a by-destination ask is not rule-replaced.

    E10 still judges the executed result on the envelope (main behaviour). The
    ranked compile never serves in its place, so the envelope carries no
    ontology_ranking origin and no rows from a rule path.
    """
    env, _db = _http_ask(
        tmp_path, monkeypatch, "shipment cost by destination", "cq_cost_by_destination"
    )
    assert env.get("plan_origin") != "ontology_ranking"
    assert "ontology_ranking" not in json.dumps(env, default=str)
    assert env.get("abstained") is True
    assert env.get("rows") == []
    assert str(env.get("text") or "").strip()
