"""UX-FEEDBACK-01: answer feedback, behind DMS_FEEDBACK_LOOP.

Flag off, the routes are absent, the ask body is unchanged, and a feedback
row is not an answer. Flag on, Yes on a governed answer files a retrieved
example. No, Fix it, and a queued review do not. Steward approval re-gates
the SQL, then files. Thumbs-down and a failed ask show on /v1/runs.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_core.control_plane.ask_audit import AskAuditRecord
from dms_executor.feedback_loop import (
    FeedbackDisabled,
    attach_runs,
    audit_rows,
    enabled,
    failure_runs,
    note_ask_outcome,
    record_failure,
)
from dms_executor.generative_ask import validate_compiled_sql
from dms_executor.verified_queries import (
    _save_example,
    lookup_verified_query,
    retrieve_verified_examples,
)
from fastapi.testclient import TestClient

FLAG = "DMS_FEEDBACK_LOOP"
SPACE = "space-feedback-01"
QUESTION = "how many widgets"
OK_SQL = "SELECT qty FROM widgets"
DROP_SQL = "DROP TABLE widgets"
ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _clear_settings() -> None:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv(FLAG, "1")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv("DMS_VQ_RAG", raising=False)
    monkeypatch.delenv("DMS_STUDIO_ADJUST", raising=False)
    monkeypatch.delenv("DMS_INSIGHT_PROOF", raising=False)
    monkeypatch.delenv("DMS_ASK_TIMINGS", raising=False)
    _clear_settings()


def _lake(tmp_path: Path) -> Path:
    path = tmp_path / "feedback.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE widgets (sku VARCHAR, qty INTEGER)")
    con.execute("INSERT INTO widgets VALUES ('sku-1', 2), ('sku-2', 5)")
    con.close()
    return path


def _grant(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor.verified_queries as vq

    monkeypatch.setattr(vq, "_grantable", lambda _space, _warehouse: {"widgets"})


def _allow(monkeypatch: pytest.MonkeyPatch, *modules: str) -> None:
    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    for module in modules:
        monkeypatch.setattr(f"{module}.compliance_gate", allow)


@pytest.fixture(autouse=True)
def _reset_feedback() -> None:
    from dms_executor.feedback_loop import _reset
    from dms_executor.pipeline_failure import _reset_pipeline_failures

    _reset()
    _reset_pipeline_failures()


def _examples(path: Path) -> list[dict[str, Any]]:
    return retrieve_verified_examples(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        grantable={"widgets"},
        dialect="duckdb",
    )


def _audit(**over: object) -> AskAuditRecord:
    base: dict[str, object] = {
        "ask_id": "ask-feedback-01",
        "asked_at": datetime(2026, 11, 28, 3, 0, tzinfo=UTC),
        "actor": ACTOR,
        "actor_kind": "deployment",
        "space_id": SPACE,
        "question": QUESTION,
        "executed_sql": OK_SQL,
        "tables_read": ("widgets",),
        "badge": "validated",
        "badge_level": "L1_GOVERNED_METRIC",
        "abstain_reason": "",
        "row_count": 2,
        "cortex_entry_id": "",
        "ask_mode": "live",
    }
    base.update(over)
    return AskAuditRecord(**base)  # type: ignore[arg-type]


def test_flag_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    assert enabled() is False


def test_flag_off_does_not_store_or_mutate(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, False)
    env = {"abstained": True, "badge": "ABSTAIN", "text": "no"}
    before = copy.deepcopy(env)
    note_ask_outcome(question=QUESTION, space_id=SPACE, envelope=env, error=None)
    assert env == before
    assert failure_runs() == []
    assert audit_rows() == []
    with pytest.raises(FeedbackDisabled):
        record_failure(
            question=QUESTION,
            space_id=SPACE,
            stage="ask",
            reason="abstain",
            action="ask_failed",
        )
    body = {"configured": False, "runs": []}
    assert attach_runs(body) is body


def test_flag_off_routes_and_openapi(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, False)
    client = TestClient(create_app())
    for path, method in (
        ("/v1/feedback/status", "get"),
        ("/v1/feedback/audit", "get"),
        ("/v1/feedback/reviews", "get"),
        ("/v1/feedback/thumb", "post"),
        ("/v1/feedback/fix", "post"),
        ("/v1/feedback/review", "post"),
        ("/v1/feedback/report", "post"),
    ):
        res = client.get(path) if method == "get" else client.post(path, json={})
        assert res.status_code == 404
        assert res.json() == {"detail": "Not Found"}
    approve = client.post("/v1/feedback/reviews/rev_x/approve")
    assert approve.status_code == 404

    _flag(monkeypatch, True)
    on = create_app().openapi()
    _flag(monkeypatch, False)
    off = create_app().openapi()
    new_paths = {p for p in on["paths"] if p.startswith("/v1/feedback")}
    assert new_paths == {
        "/v1/feedback/status",
        "/v1/feedback/audit",
        "/v1/feedback/reviews",
        "/v1/feedback/thumb",
        "/v1/feedback/fix",
        "/v1/feedback/review",
        "/v1/feedback/reviews/{review_id}/approve",
        "/v1/feedback/report",
    }
    stripped = copy.deepcopy(on)
    for path in new_paths:
        del stripped["paths"][path]
    for name in ("ThumbIn", "FixIn", "ReviewIn", "ReportIn"):
        del stripped["components"]["schemas"][name]
    assert json.dumps(stripped, sort_keys=True) == json.dumps(off, sort_keys=True)


def test_feedback_row_is_retrieved_and_never_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, False)
    _grant(monkeypatch)
    path = _lake(tmp_path)
    saved = _save_example(
        question=QUESTION,
        sql=OK_SQL,
        space_id=SPACE,
        warehouse=path,
        grantable={"widgets"},
        dialect="duckdb",
        provenance="feedback_yes",
        actor=ACTOR,
    )
    assert saved is not None
    assert lookup_verified_query(
        QUESTION, space_id=SPACE, warehouse=path, grantable={"widgets"}, dialect="duckdb"
    ) is None
    assert _examples(path) == []
    _flag(monkeypatch, True)
    assert lookup_verified_query(
        QUESTION, space_id=SPACE, warehouse=path, grantable={"widgets"}, dialect="duckdb"
    ) is None
    found = _examples(path)
    assert [row["sql"] for row in found] == [OK_SQL]
    assert found[0]["provenance"] == "feedback_yes"


def test_failed_ask_is_on_runs_only_when_the_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow(monkeypatch, "dms_api.routes.chat", "dms_api.routes.runs")

    class _Ask:
        def live_ask(self, question: str, **_kwargs: object) -> dict[str, Any]:
            return {
                "abstained": True,
                "badge": "ABSTAIN",
                "text": "declined",
                "question": question,
                "sql_used": None,
                "rows": [],
                "values": [],
                "sources": [],
                "assumptions": [],
            }

    def ask(on: bool) -> dict[str, Any]:
        _flag(monkeypatch, on)
        app = create_app()
        with TestClient(app) as client:
            app.state.ask_service = _Ask()
            res = client.post("/v1/chat/ask", json={"question": QUESTION})
            assert res.status_code == 200
            body = res.json()
            runs = client.get("/v1/runs").json()
        return {"ask": body, "runs": runs}

    off = ask(False)
    on = ask(True)
    assert off["ask"] == on["ask"]
    assert "feedback" not in off["ask"]
    assert all(row.get("kind") != "pipeline_failure" for row in off["runs"]["runs"])
    failed = [row for row in on["runs"]["runs"] if row.get("kind") == "pipeline_failure"]
    assert len(failed) == 1
    assert failed[0]["question"] == QUESTION
    assert failed[0]["stage"] == "ask"
    assert failed[0]["reason"] == "abstain"


def _client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, steward: bool = True
) -> tuple[TestClient, Path, Any]:
    _flag(monkeypatch, True)
    _grant(monkeypatch)
    path = _lake(tmp_path)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    if steward:
        monkeypatch.setenv("DMS_ACTOR_ROLE", "steward")
    else:
        monkeypatch.setenv("DMS_ACTOR_ROLE", "viewer")
    _clear_settings()
    _allow(monkeypatch, "dms_api.routes.feedback", "dms_api.routes.runs")
    app = create_app()
    client = TestClient(app)
    return client, path, app


def test_thumb_down_audits_and_does_not_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, path, _app = _client(monkeypatch, tmp_path)
    res = client.post(
        "/v1/feedback/thumb",
        json={
            "space_id": SPACE,
            "question": QUESTION,
            "sql": OK_SQL,
            "vote": "down",
            "reason": "wrong_number",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["filed"] is False
    assert body["asset_id"] is None
    assert body["audit"]["action"] == "thumb_down"
    assert _examples(path) == []
    runs = client.get("/v1/runs").json()["runs"]
    row = next(item for item in runs if item["kind"] == "pipeline_failure")
    assert row["question"] == QUESTION
    assert row["stage"] == "feedback"
    assert row["reason"] == "wrong_number"
    missing = client.post(
        "/v1/feedback/thumb",
        json={
            "space_id": SPACE,
            "question": QUESTION,
            "sql": OK_SQL,
            "vote": "down",
            "reason": "other",
        },
    )
    assert missing.status_code == 400
    assert missing.json()["detail"] == "note_required"


def test_thumb_up_files_only_a_governed_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, path, app = _client(monkeypatch, tmp_path)
    with client:
        plain = client.post(
            "/v1/feedback/thumb",
            json={"space_id": SPACE, "question": QUESTION, "sql": OK_SQL, "vote": "up"},
        )
        assert plain.status_code == 200
        assert plain.json()["filed"] is False
        assert _examples(path) == []
        app.state.ask_audit_store.record(_audit())
        governed = client.post(
            "/v1/feedback/thumb",
            json={"space_id": SPACE, "question": QUESTION, "sql": OK_SQL, "vote": "up"},
        )
        assert governed.status_code == 200
        assert governed.json()["filed"] is True
        assert governed.json()["asset_id"]
        found = _examples(path)
        assert [row["sql"] for row in found] == [OK_SQL]
        assert found[0]["provenance"] == "feedback_yes"
        assert lookup_verified_query(
            QUESTION, space_id=SPACE, warehouse=path, grantable={"widgets"}, dialect="duckdb"
        ) is None
        audits = client.get("/v1/feedback/audit").json()["rows"]
    actions = [row["action"] for row in audits]
    assert actions.count("thumb_up") == 2
    filed = [row for row in audits if row["action"] == "thumb_up" and row["asset_id"]]
    assert len(filed) == 1


def test_fix_regates_and_does_not_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, path, _app = _client(monkeypatch, tmp_path)
    ran: list[str] = []
    import dms_executor.sql_loop as sql_loop

    real = sql_loop.run_readonly

    def _spy(sql: str, warehouse: Path) -> tuple[Any, str | None]:
        ran.append(sql)
        return real(sql, warehouse)

    monkeypatch.setattr(sql_loop, "run_readonly", _spy)
    why = validate_compiled_sql(
        DROP_SQL, grantable={"widgets"}, warehouse=path, dialect="duckdb"
    )
    assert why
    refused = client.post(
        "/v1/feedback/fix",
        json={
            "space_id": SPACE,
            "question": QUESTION,
            "sql": DROP_SQL,
            "interpretation": "drop the table",
        },
    )
    assert refused.status_code == 400
    assert refused.json()["detail"] == why
    assert ran == []
    assert _examples(path) == []
    ran.clear()
    ok = client.post(
        "/v1/feedback/fix",
        json={
            "space_id": SPACE,
            "question": QUESTION,
            "sql": OK_SQL,
            "interpretation": "DROP TABLE widgets",
        },
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["filed"] is False
    assert body["row_count"] == 2
    assert ran == [OK_SQL]
    assert _examples(path) == []
    audits = client.get("/v1/feedback/audit").json()["rows"]
    assert any(row["action"] == "fix_refused" for row in audits)
    assert any(row["action"] == "fix_run" and not row["asset_id"] for row in audits)


def test_review_files_only_when_a_steward_approves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, path, _app = _client(monkeypatch, tmp_path)
    queued = client.post(
        "/v1/feedback/review",
        json={"space_id": SPACE, "question": QUESTION, "sql": OK_SQL},
    )
    assert queued.status_code == 200
    assert queued.json()["filed"] is False
    review_id = queued.json()["id"]
    assert _examples(path) == []
    monkeypatch.setenv("DMS_ACTOR_ROLE", "viewer")
    _clear_settings()
    denied = client.post(f"/v1/feedback/reviews/{review_id}/approve")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "steward_grant_required"
    assert _examples(path) == []
    monkeypatch.setenv("DMS_ACTOR_ROLE", "steward")
    _clear_settings()
    approved = client.post(f"/v1/feedback/reviews/{review_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["filed"] is True
    found = _examples(path)
    assert [row["provenance"] for row in found] == ["feedback_approved"]
    assert lookup_verified_query(
        QUESTION, space_id=SPACE, warehouse=path, grantable={"widgets"}, dialect="duckdb"
    ) is None
    again = client.post(f"/v1/feedback/reviews/{review_id}/approve")
    assert again.status_code == 200
    assert again.json()["filed"] is False
    assert len(_examples(path)) == 1
    audits = client.get("/v1/feedback/audit").json()["rows"]
    assert any(row["action"] == "review_queued" and not row["asset_id"] for row in audits)
    assert any(row["action"] == "review_approved" and row["asset_id"] for row in audits)


def test_report_problem_is_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, path, _app = _client(monkeypatch, tmp_path)
    res = client.post(
        "/v1/feedback/report",
        json={"note": "the chart is blank", "question": QUESTION, "space_id": SPACE},
    )
    assert res.status_code == 200
    assert res.json()["filed"] is False
    assert res.json()["stage"] == "report"
    assert res.json()["reason"] == "report_problem"
    runs = client.get("/v1/runs").json()["runs"]
    row = next(item for item in runs if item["kind"] == "pipeline_failure")
    assert row["question"] == QUESTION
    assert row["stage"] == "report"
    assert row["reason"] == "report_problem"
    assert _examples(path) == []
