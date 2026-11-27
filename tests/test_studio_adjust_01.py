"""ADJUST-SQL-VQ-01: steward Adjust SQL, behind DMS_STUDIO_ADJUST.

Flag off, the route is absent and nothing is stored. Flag on, hostile SQL,
an ungranted table, and a multi-statement are the same codes as
``validate_compiled_sql``. A non-steward is 403. A saved row keeps the actor,
a schema hash, and provenance ``steward_adjusted``, and ``DMS_VQ_RAG`` can
retrieve it as a few-shot example.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor.generative_ask import validate_compiled_sql
from dms_executor.verified_queries import (
    AdjustDisabled,
    _rows_for_space,
    examples_text,
    preview_adjusted_sql,
    retrieve_verified_examples,
    save_steward_adjusted,
    studio_adjust_enabled,
)
from fastapi.testclient import TestClient

FLAG = "DMS_STUDIO_ADJUST"
SPACE = "space-adjust-01"
ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
QUESTION = "how many widgets"
OK_SQL = "SELECT qty FROM widgets"
PII_SQL = "SELECT email FROM widgets"
PII_VALUE = "adjust-pii-token@example.test"
REFUSALS = (
    "SELECT qty FROM read_csv_auto('x.csv')",
    "SELECT qty FROM not_granted",
    "SELECT qty FROM widgets; SELECT qty FROM widgets",
    "DROP TABLE widgets",
    "INSERT INTO widgets VALUES ('z', 1, 'nope@example.test')",
)


def _clear_settings() -> None:
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()


def _lake(tmp_path: Path) -> Path:
    path = tmp_path / "adjust.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE widgets (sku VARCHAR, qty INTEGER, email VARCHAR)")
    con.execute(
        "INSERT INTO widgets VALUES ('sku-1', 2, ?), ('sku-2', 5, 'other@example.test')",
        [PII_VALUE],
    )
    con.close()
    return path


def _grant(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor.verified_queries as vq

    monkeypatch.setattr(vq, "_grantable", lambda _space, _warehouse: {"widgets"})


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv(FLAG, "1")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    _clear_settings()


def _allow(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        seen.setdefault("actions", []).append(action)
        seen["actor"] = actor
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.studio_adjust.compliance_gate", allow)
    return seen


def test_flag_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    assert studio_adjust_enabled() is False


def test_flag_off_does_not_run_or_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, False)
    _grant(monkeypatch)
    path = _lake(tmp_path)
    ran: list[str] = []

    def _boom(sql: str, warehouse: Path) -> tuple[None, str]:
        ran.append(sql)
        raise AssertionError(warehouse)

    monkeypatch.setattr("dms_executor.sql_loop.run_readonly", _boom)
    with pytest.raises(AdjustDisabled):
        preview_adjusted_sql(sql=OK_SQL, space_id=SPACE, path=path, dialect="duckdb")
    with pytest.raises(AdjustDisabled):
        save_steward_adjusted(
            question=QUESTION,
            sql=OK_SQL,
            space_id=SPACE,
            actor=ACTOR,
            path=path,
            dialect="duckdb",
        )
    assert ran == []
    assert _rows_for_space(path, SPACE) == []


def test_refusals_match_model_sql_and_do_not_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, True)
    _grant(monkeypatch)
    path = _lake(tmp_path)
    ran: list[str] = []
    real = __import__("dms_executor.sql_loop", fromlist=["run_readonly"]).run_readonly

    def _spy(sql: str, warehouse: Path) -> Any:
        ran.append(sql)
        return real(sql, warehouse)

    monkeypatch.setattr("dms_executor.sql_loop.run_readonly", _spy)
    for sql in REFUSALS:
        why = validate_compiled_sql(sql, grantable={"widgets"}, warehouse=path, dialect="duckdb")
        assert why
        with pytest.raises(ValueError) as caught:
            preview_adjusted_sql(sql=sql, space_id=SPACE, path=path, dialect="duckdb")
        assert str(caught.value) == why
    assert ran == []
    con = duckdb.connect(str(path), read_only=True)
    try:
        count = con.execute("SELECT count(*) FROM widgets").fetchone()
    finally:
        con.close()
    assert count is not None
    assert count[0] == 2


def test_run_masks_pii_and_save_keeps_actor_and_schema_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, True)
    _grant(monkeypatch)
    path = _lake(tmp_path)
    preview = preview_adjusted_sql(sql=PII_SQL, space_id=SPACE, path=path, dialect="duckdb")
    blob = json.dumps(preview)
    assert PII_VALUE not in blob
    assert "DMSMASK_email_" in blob
    saved = save_steward_adjusted(
        question=QUESTION,
        sql=OK_SQL,
        space_id=SPACE,
        actor=ACTOR,
        path=path,
        dialect="duckdb",
    )
    assert saved["actor"] == ACTOR
    assert saved["schema_hash"]
    assert saved["provenance"] == "steward_adjusted"
    stored = _rows_for_space(path, saved["space_id"])
    assert len(stored) == 1
    assert stored[0]["actor"] == ACTOR
    assert stored[0]["schema_hash"] == saved["schema_hash"]
    assert stored[0]["provenance"] == "steward_adjusted"
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    examples = retrieve_verified_examples(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        grantable={"widgets"},
        dialect="duckdb",
    )
    assert examples_text(examples) == f"Question: {QUESTION}\nSQL: {OK_SQL}"
    monkeypatch.delenv("DMS_VQ_RAG")
    assert retrieve_verified_examples(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        grantable={"widgets"},
        dialect="duckdb",
    ) == []


def test_flag_off_examples_stay_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, False)
    _grant(monkeypatch)
    path = _lake(tmp_path)
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    before = retrieve_verified_examples(
        QUESTION, space_id=SPACE, warehouse=path, grantable={"widgets"}, dialect="duckdb"
    )
    with pytest.raises(AdjustDisabled):
        save_steward_adjusted(
            question=QUESTION, sql=OK_SQL, space_id=SPACE, actor=ACTOR, path=path, dialect="duckdb"
        )
    after = retrieve_verified_examples(
        QUESTION, space_id=SPACE, warehouse=path, grantable={"widgets"}, dialect="duckdb"
    )
    assert before == after == []
    assert examples_text(before) == examples_text(after) == ""


def test_flag_off_route_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, False)
    monkeypatch.delenv("DMS_INSIGHT_PROOF", raising=False)
    client = TestClient(create_app())
    for path in (
        "/v1/studio/adjust/status",
        "/v1/studio/adjust/run",
        "/v1/studio/adjust/save",
    ):
        res = client.post(path, json={}) if path.endswith(("run", "save")) else client.get(path)
        assert res.status_code == 404
        assert res.json() == {"detail": "Not Found"}


def test_flag_off_openapi_is_flag_on_minus_the_adjust_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_INSIGHT_PROOF", raising=False)
    _flag(monkeypatch, True)
    on = create_app().openapi()
    _flag(monkeypatch, False)
    off = create_app().openapi()
    new_paths = {p for p in on["paths"] if p.startswith("/v1/studio/adjust")}
    assert new_paths == {
        "/v1/studio/adjust/status",
        "/v1/studio/adjust/run",
        "/v1/studio/adjust/save",
    }
    stripped = copy.deepcopy(on)
    for path in new_paths:
        del stripped["paths"][path]
    for name in ("AdjustRunIn", "AdjustSaveIn"):
        del stripped["components"]["schemas"][name]
    assert json.dumps(stripped, sort_keys=True) == json.dumps(off, sort_keys=True)


def test_non_steward_is_403(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, True)
    _grant(monkeypatch)
    path = _lake(tmp_path)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.setenv("DMS_ACTOR_ROLE", "viewer")
    _clear_settings()
    _allow(monkeypatch)
    client = TestClient(create_app())
    run = client.post(
        "/v1/studio/adjust/run",
        json={"space_id": SPACE, "sql": OK_SQL},
    )
    save = client.post(
        "/v1/studio/adjust/save",
        json={"space_id": SPACE, "question": QUESTION, "sql": OK_SQL},
    )
    assert run.status_code == 403
    assert run.json()["detail"] == "steward_grant_required"
    assert save.status_code == 403
    assert save.json()["detail"] == "steward_grant_required"
    assert _rows_for_space(path, SPACE) == []


def test_steward_route_runs_and_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag(monkeypatch, True)
    _grant(monkeypatch)
    path = _lake(tmp_path)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.setenv("DMS_ACTOR_ROLE", "steward")
    _clear_settings()
    seen = _allow(monkeypatch)
    client = TestClient(create_app())
    status = client.get("/v1/studio/adjust/status")
    assert status.status_code == 200
    assert status.json() == {"enabled": True}
    run = client.post(
        "/v1/studio/adjust/run",
        json={"space_id": SPACE, "sql": OK_SQL},
    )
    assert run.status_code == 200, run.text
    assert {row["qty"] for row in run.json()["rows"]} == {2, 5}
    bad = client.post(
        "/v1/studio/adjust/run",
        json={"space_id": SPACE, "sql": "SELECT qty FROM widgets; SELECT 1"},
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == validate_compiled_sql(
        "SELECT qty FROM widgets; SELECT 1",
        grantable={"widgets"},
        warehouse=path,
        dialect="duckdb",
    )
    save = client.post(
        "/v1/studio/adjust/save",
        json={"space_id": SPACE, "question": QUESTION, "sql": OK_SQL},
    )
    assert save.status_code == 200, save.text
    body = save.json()
    assert body["actor"] == get_settings().dms_actor_user_id
    assert body["schema_hash"]
    assert body["provenance"] == "steward_adjusted"
    assert "studio.adjust_run" in seen["actions"]
    assert "studio.adjust_save" in seen["actions"]
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    examples = retrieve_verified_examples(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        grantable={"widgets"},
        dialect="duckdb",
    )
    assert [row["sql"] for row in examples] == [OK_SQL]
