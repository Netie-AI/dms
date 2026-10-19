"""ASK reconfirm. Direct abstain stays for ungranted, PII, and destructive SQL.

The suggested question is the model text. Yes still runs validate_compiled_sql.
No appends a pipeline-failure JSONL record and increments the counter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskResponse
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def _abstain(reason: str) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain

    return build_abstain(
        reason=reason,
        question="q",
        stage="test",
        answer_id="ans_test",
        text=reason,
        abstain_reason=reason,
        assumptions=[f"GEN-01: {reason}"],
        route="abstain",
        ask_mode="live",
    )


def test_ungranted_pii_and_destructive_stay_direct_abstain() -> None:
    from dms_executor.reconfirm import apply_reconfirm

    def boom(_prompt: str) -> str:
        raise AssertionError("the model must not write a question for a direct abstain")

    cases = [
        ("List active alerts across the warehouse network", _abstain("ungranted:alerts")),
        ("show the customer email", _abstain("unknown_measure")),
        ("DROP TABLE inventory", _abstain("unknown_measure")),
        ("Please DROP TABLE inventory", _abstain("unknown_measure")),
        ("delete the rows", _abstain("hostile_sql:statement_not_allowed")),
    ]
    for question, env in cases:
        out = apply_reconfirm(
            env,
            question=question,
            space_id="sp",
            session_id="ses",
            tables=None,
            ask_path=None,
            ontology="measure sku_count",
            schema="inventory.quantity",
            complete=boom,
        )
        assert "reconfirm" not in out
        assert out["abstained"] is True
        assert out["badge"] == "ABSTAIN"


def test_pack_grant_miss_stays_a_direct_abstain() -> None:
    """The live pack miss is a grant code, not the planted ``ungranted:alerts`` string."""
    from dms_executor.demo_pack import SPEND_BY_COUNTRY_Q, maybe_pack_ask
    from dms_executor.pipeline_failure import reconfirm_disposition
    from dms_executor.reconfirm import apply_reconfirm

    def boom(_prompt: str) -> str:
        raise AssertionError("a grant miss must not ask the model for another question")

    env = maybe_pack_ask(
        SPEND_BY_COUNTRY_Q,
        space_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        grantable={"locations", "inventory", "shipments"},
        dialect="duckdb",
    )
    assert env is not None
    assert env["abstained"] is True
    code = str(env.get("abstain_reason") or "")
    assumptions = " ".join(str(item) for item in env.get("assumptions") or [])
    assert reconfirm_disposition(code) == "direct" or "validate:" in assumptions
    out = apply_reconfirm(
        env,
        question=SPEND_BY_COUNTRY_Q,
        space_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        session_id="ses",
        tables=None,
        ask_path=None,
        ontology="measure sku_count",
        schema="inventory",
        complete=boom,
    )
    assert "reconfirm" not in out
    assert out["abstained"] is True
    assert out["badge"] == "ABSTAIN"


def test_suggestion_is_the_model_text() -> None:
    from dms_executor.reconfirm import apply_reconfirm, write_suggested_question

    seen: list[str] = []

    def complete(prompt: str) -> str:
        seen.append(prompt)
        return "zq sentinel question"

    got = write_suggested_question(
        ontology="measure sku_count",
        schema="inventory.quantity",
        reason="unknown_measure",
        complete=complete,
    )
    assert got == "zq sentinel question"
    assert "sku_count" in seen[0]
    assert "inventory.quantity" in seen[0]
    assert "unknown_measure" in seen[0]

    out = apply_reconfirm(
        _abstain("unknown_measure"),
        question="what is the mauve widget total",
        space_id="sp",
        session_id="ses",
        tables=None,
        ask_path=None,
        ontology="measure sku_count",
        schema="inventory.quantity",
        complete=complete,
    )
    offer = out["reconfirm"]
    assert offer["suggested_question"] == "zq sentinel question"
    assert offer["reason_code"] == "unknown_measure"
    assert offer["reason_text"]
    assert offer["token"]


def test_suggester_source_has_no_template_or_word_list() -> None:
    src = (ROOT / "packages/executor/dms_executor/reconfirm.py").read_text(encoding="utf-8")
    assert "re.compile" not in src
    assert "How many" not in src
    assert "word_list" not in src


def test_no_appends_a_pipeline_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.reconfirm import (
        _reset_reconfirm,
        apply_reconfirm,
        decline_count,
        decline_envelope,
        pipeline_failure_jsonl_path,
        take_pending,
    )

    monkeypatch.setenv("DMS_PIPELINE_FAILURE_JSONL", str(tmp_path / "failures.jsonl"))
    _reset_reconfirm()
    out = apply_reconfirm(
        _abstain("unknown_measure"),
        question="what is the mauve widget total",
        space_id="sp",
        session_id="ses",
        tables=None,
        ask_path=None,
        ontology="measure sku_count",
        schema="inventory.quantity",
        complete=lambda _prompt: "closest stock question",
    )
    pending = take_pending(out["reconfirm"]["token"])
    assert pending is not None
    before = decline_count()
    declined = decline_envelope(pending)
    assert declined["text"] == "not found in the database"
    assert declined["abstained"] is True
    assert "reconfirm" not in declined
    assert decline_count() == before + 1
    lines = pipeline_failure_jsonl_path().read_text(encoding="utf-8").splitlines()
    assert lines
    record = json.loads(lines[-1])
    assert record["stage"] == "reconfirm_no"
    assert record["count"] == decline_count()
    assert record["ticket_id"]
    assert "question" not in record
    assert "sql" not in record


def test_yes_runs_validate_compiled_sql(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cortex_contract.execution import Manifest
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from dms_executor import Executor
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.manifest import ManifestMinter, SessionAcl
    from dms_executor.reconfirm import _reset_reconfirm

    _reset_reconfirm()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_PIPELINE_FAILURE_JSONL", str(tmp_path / "failures.jsonl"))
    get_settings.cache_clear()
    calls: list[str] = []

    def spy(sql: str, **_kwargs: Any) -> str:
        calls.append(sql)
        return "ungranted:inventory"

    monkeypatch.setattr("dms_executor.generative_ask.validate_compiled_sql", spy)
    suggested = "total stock of mauve widgets"
    sql = "SELECT count(*) AS n FROM inventory"

    class _Cortex:
        def complete(self, prompt: str) -> str:
            assert "Ontology:" in prompt
            return suggested

        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            if question == suggested:
                return {"query_sql": sql}
            return {"abstain": True}

        def ask(self, _req: Any) -> AskResponse:
            return AskResponse(
                answer="no",
                badge="abstain",
                sql_used=None,
                rows=[],
                assumptions=["fixture"],
                audit_id="aud-reconfirm",
                route="abstain",
            )

        def submit(self, _req: Any) -> Any:
            raise AssertionError("yes executed SQL without validate")

        def ledger_append(self, _req: Any) -> Any:
            raise AssertionError("yes appended a ledger row")

    db = tmp_path / "warehouse.duckdb"
    ensure_demo_warehouse(db)
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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    app = create_app()
    app.state.ask_service = Executor(cortex=_Cortex(), minter=minter, warehouse_path=db)  # type: ignore[arg-type]
    app.state.cortex = app.state.ask_service._cortex
    client = TestClient(app)
    asked = client.post(
        "/v1/chat/ask",
        json={"question": "what is the mauve widget total", "session_id": "ses_rc"},
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["abstained"] is True
    assert "reconfirm" in body
    token = body["reconfirm"]["token"]
    assert body["reconfirm"]["suggested_question"] == suggested
    calls.clear()
    yes = client.post("/v1/chat/ask/confirm", json={"token": token, "yes": "yes"})
    assert yes.status_code == 200, yes.text
    assert calls, "yes did not run validate_compiled_sql"
    assert sql in calls[0]
    get_settings.cache_clear()


def _confirm_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    compute: Any,
) -> tuple[Any, TestClient]:
    from cortex_contract.execution import Manifest
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from dms_executor import Executor
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.manifest import ManifestMinter, SessionAcl
    from dms_executor.reconfirm import _reset_reconfirm

    _reset_reconfirm()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    get_settings.cache_clear()

    class _Cortex:
        def complete(self, _prompt: str) -> str:
            return "closest stock question"

        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            return compute(question)

        def ask(self, _req: Any) -> AskResponse:
            return AskResponse(
                answer="no",
                badge="abstain",
                sql_used=None,
                rows=[],
                assumptions=["fixture"],
                audit_id="aud-reconfirm",
                route="abstain",
            )

        def submit(self, _req: Any) -> Any:
            raise AssertionError("confirm executed SQL")

        def ledger_append(self, _req: Any) -> Any:
            raise AssertionError("confirm appended a ledger row")

    db = tmp_path / "warehouse.duckdb"
    ensure_demo_warehouse(db)
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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    app = create_app()
    app.state.ask_service = Executor(cortex=_Cortex(), minter=minter, warehouse_path=db)  # type: ignore[arg-type]
    app.state.cortex = app.state.ask_service._cortex
    return app, TestClient(app)


def _token(client: TestClient) -> str:
    asked = client.post(
        "/v1/chat/ask",
        json={"question": "what is the mauve widget total", "session_id": "ses_rc"},
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert "reconfirm" in body
    return str(body["reconfirm"]["token"])


def test_confirm_maps_grounding_and_ask_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_api.settings import get_settings
    from dms_core.ask import AskServiceError, GroundingRefused

    app, client = _confirm_client(monkeypatch, tmp_path, compute=lambda _q: {"abstain": True})
    service = app.state.ask_service
    token_ground = _token(client)
    token_path = _token(client)

    def refuse(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise GroundingRefused(ungrantable=["alerts"], grantable=["inventory"])

    service.live_ask = refuse  # type: ignore[method-assign]
    grounded = client.post(
        "/v1/chat/ask/confirm", json={"token": token_ground, "yes": "yes"}
    )
    assert grounded.status_code == 403, grounded.text
    detail = grounded.json()["detail"]
    assert detail["code"] == "grounding_not_grantable"
    assert detail["ungrantable_tables"] == ["alerts"]

    def path_blocked(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AskServiceError("path_not_allowed", "table 'alerts' is not named by this manifest")

    service.live_ask = path_blocked  # type: ignore[method-assign]
    blocked = client.post(
        "/v1/chat/ask/confirm", json={"token": token_path, "yes": "yes"}
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["detail"]["code"] == "path_not_allowed"
    get_settings.cache_clear()


def test_confirm_yes_honors_the_insights_call_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_api.settings import get_settings

    def compute(question: str) -> dict[str, Any]:
        if question == "closest stock question":
            return {"insights_fail": "insights_call_cap:2"}
        return {"abstain": True}

    _app, client = _confirm_client(monkeypatch, tmp_path, compute=compute)
    yes = client.post(
        "/v1/chat/ask/confirm", json={"token": _token(client), "yes": "yes"}
    )
    assert yes.status_code == 200, yes.text
    body = yes.json()
    assert body["abstained"] is True
    blob = json.dumps(body)
    assert "insights_call_cap" in blob
    get_settings.cache_clear()
