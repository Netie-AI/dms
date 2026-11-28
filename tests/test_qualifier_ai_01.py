"""BIRD-QUALIFIER-AI-01. The model checks the SQL. Python does not read the question.

Flag default off. Off, the qualifier reason, the envelope, and the served
route stay the bytes they were, and no verifier call is sent. On, a fake
verifier that says the MAX SQL is honored serves it. A SQL that drops a
stated filter is repaired or reconfirmed, and is not served.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import _submit_validated, validated_sql_reason
from dms_executor.ontology import NO_SILENT_PAD, Coverage
from dms_executor.qualifier_ai import (
    bind_qualifier_transport,
    qualifier_ai_enabled,
    request_body,
    reset_qualifier_transport,
    review_candidate,
)

_ASKED = "What are the maximum points of British constructors?"
_HINT = "maximum points refers to MAX(points)"
_MAX_SQL = "SELECT MAX(points) AS maximum_points FROM constructors WHERE country = 'British'"
_OPEN_SQL = "SELECT SUM(points) AS total FROM constructors"
_FIXED_SQL = "SELECT SUM(points) AS total FROM constructors WHERE country = 'British'"
_TOTAL_Q = "What is the total points of British constructors?"
_BANNED = (
    "model",
    "model_id",
    "model_name",
    "served_model",
    "served_provider",
    "provider",
    "strict",
)
_KEYS = ("OPENVAULT_API_KEY", "CORTEX_API_KEY", "OPENVAULT_URL")


def _lake(path: Path) -> Path:
    db = path / "qual.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE constructors (name VARCHAR, country VARCHAR, points INTEGER)")
        con.execute("CREATE TABLE races (race_id INTEGER, year INTEGER, circuit VARCHAR)")
        con.execute("CREATE TABLE results (constructor VARCHAR, race_id INTEGER, points INTEGER)")
        con.execute(
            "INSERT INTO constructors VALUES ('Alpine', 'British', 30), ('Ferrari', 'Italian', 40)"
        )
        con.execute("INSERT INTO races VALUES (1, 2019, 'Silverstone'), (2, 2020, 'Monza')")
        con.execute("INSERT INTO results VALUES ('Alpine', 1, 30), ('Ferrari', 2, 40)")
    finally:
        con.close()
    return db


def _grants() -> set[str]:
    return {"constructors", "races", "results"}


def _honored(fragment: str, literal: str | None = "British") -> dict[str, Any]:
    return {
        "requirements": [
            {
                "requirement": "maximum points of British constructors",
                "verdict": "honored",
                "sql_fragment": fragment,
                "column": "constructors.country",
                "literal": literal,
            }
        ],
        "suggested_question": _ASKED,
    }


def _not_honored() -> dict[str, Any]:
    return {
        "requirements": [
            {
                "requirement": "limited to British constructors",
                "verdict": "not_honored",
                "sql_fragment": None,
                "column": "constructors.country",
                "literal": "British",
            }
        ],
        "suggested_question": "What is the total points of British constructors?",
    }


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_QUALIFIER_AI", raising=False)
    for name in _KEYS:
        monkeypatch.delenv(name, raising=False)


def _coverage() -> Coverage:
    return Coverage(include=("n",), exclude=(NO_SILENT_PAD,), unsure=("none",))


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="ent_qual", hash="hash_qual")


def _rows(sql: str) -> Any:
    return SimpleNamespace(
        ok=True,
        status="ok",
        run_id="run_qual",
        output={"rows": [{"n": 1}], "sql": sql},
    )


def _stable(env: dict[str, Any]) -> dict[str, Any]:
    out = dict(env)
    for key in list(out):
        if key in {"as_of"} or key.startswith("engine_"):
            out.pop(key)
    return out


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    assert qualifier_ai_enabled() is False
    monkeypatch.setenv("DMS_QUALIFIER_AI", "0")
    assert qualifier_ai_enabled() is False


def test_flag_off_does_not_call_and_keeps_the_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear(monkeypatch)
    db = _lake(tmp_path)

    def transport(_body: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("verifier call")

    out = review_candidate(
        _ASKED,
        _MAX_SQL,
        hint=_HINT,
        warehouse=db,
        grantable=_grants(),
        transport=transport,
    )
    assert out["action"] == "serve"
    assert out["sql"] == _MAX_SQL
    assert out["envelope"] is None
    monkeypatch.setenv("DMS_QUALIFIER_AI", "0")
    again = review_candidate(
        _ASKED, _MAX_SQL, hint=_HINT, warehouse=db, grantable=_grants(), transport=transport
    )
    assert again == out


def test_flag_off_reason_and_envelope_are_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    sql = "SELECT 1 AS n"
    question = "how many"
    unset_reason = validated_sql_reason(sql, question=question, warehouse=None, grantable=set())
    monkeypatch.setenv("DMS_QUALIFIER_AI", "0")
    off_reason = validated_sql_reason(sql, question=question, warehouse=None, grantable=set())
    assert unset_reason == off_reason
    assert unset_reason is None

    def submit(statement: str) -> Any:
        return _rows(statement)

    monkeypatch.delenv("DMS_QUALIFIER_AI", raising=False)
    first = _submit_validated(
        sql,
        question=question,
        space_id="spc_qual",
        session_id="ses_qual",
        submit=submit,
        ledger_append=_ledger,
        notes=(),
        plan_source="ontology_plan",
        coverage=_coverage(),
        served_route="model",
    )
    monkeypatch.setenv("DMS_QUALIFIER_AI", "0")
    second = _submit_validated(
        sql,
        question=question,
        space_id="spc_qual",
        session_id="ses_qual",
        submit=submit,
        ledger_append=_ledger,
        notes=(),
        plan_source="ontology_plan",
        coverage=_coverage(),
        served_route="model",
    )
    assert_envelope_valid(first)
    assert first["abstained"] is False
    assert first["sql_used"] == sql
    assert first.get("served_route") == "model"
    assert json.dumps(_stable(first), sort_keys=True, default=str) == json.dumps(
        _stable(second), sort_keys=True, default=str
    )


def test_request_sends_role_hint_and_schema_and_no_model_id(tmp_path: Path) -> None:
    db = _lake(tmp_path)
    body = request_body(
        asked=_ASKED,
        hint=_HINT,
        sql=_MAX_SQL,
        schema=[
            {
                "table": "constructors",
                "columns": [
                    {"name": "name", "type": "VARCHAR"},
                    {"name": "country", "type": "VARCHAR"},
                    {"name": "points", "type": "INTEGER"},
                ],
            }
        ],
        explain="physical_plan",
        sample_rows=[{"maximum_points": 30}],
        notes=["unhonored_qualifier:named_filter=Max"],
    )
    assert body["role"] == "sql_verify"
    blob = json.dumps(body)
    for banned in _BANNED:
        assert f'"{banned}"' not in blob
    user = json.loads(body["messages"][1]["content"])
    assert user["question"] == _ASKED
    assert user["hint"] == _HINT
    assert "MAX(points)" in user["sql"]
    assert user["schema"][0]["table"] == "constructors"
    assert user["notes"] == ["unhonored_qualifier:named_filter=Max"]
    _ = db


def test_maximum_hint_serves_when_the_verifier_says_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DMS_QUALIFIER_AI", "1")
    db = _lake(tmp_path)
    seen: list[dict[str, Any]] = []

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        seen.append(body)
        return _honored("MAX(points)")

    out = review_candidate(
        _ASKED,
        _MAX_SQL,
        hint=_HINT,
        warehouse=db,
        grantable=_grants(),
        served_route="model",
        transport=transport,
    )
    assert out["action"] == "serve"
    assert out["sql"] == _MAX_SQL
    assert "MAX(points)" in out["sql"]
    assert seen[0]["role"] == "sql_verify"
    user = json.loads(seen[0]["messages"][1]["content"])
    assert user["hint"] == _HINT
    assert "constructors" in json.dumps(user["schema"])
    assert user["explain"]
    assert isinstance(user["sample_rows"], list)


def test_dropped_filter_is_repaired_or_reconfirmed_never_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DMS_QUALIFIER_AI", "1")
    db = _lake(tmp_path)
    calls: list[str] = []

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        user = json.loads(body["messages"][1]["content"])
        calls.append(user["sql"])
        if "country" in user["sql"]:
            return _honored("country = 'British'")
        return _not_honored()

    def repair(previous: str, feedback: str) -> str:
        assert previous == _OPEN_SQL
        assert "British" in feedback
        return _FIXED_SQL

    fixed = review_candidate(
        _TOTAL_Q,
        _OPEN_SQL,
        hint=_HINT,
        warehouse=db,
        grantable=_grants(),
        served_route="model",
        transport=transport,
        repair=repair,
    )
    assert fixed["action"] == "serve"
    assert fixed["sql"] == _FIXED_SQL
    assert fixed["repaired"] is True
    assert _OPEN_SQL not in (fixed["sql"],)
    assert calls[0] == _OPEN_SQL
    assert "country" in calls[1]

    def keep(_previous: str, _feedback: str) -> str:
        return _OPEN_SQL

    held = review_candidate(
        _TOTAL_Q,
        _OPEN_SQL,
        warehouse=db,
        grantable=_grants(),
        served_route="model",
        transport=transport,
        repair=keep,
    )
    assert held["action"] == "reconfirm"
    env = held["envelope"]
    assert isinstance(env, dict)
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["sql_used"] is None
    assert env["values"] == []
    block = env["reconfirm"]
    assert block["suggested_question"]
    assert block["requirement"] == "limited to British constructors"


def test_hallucinated_literal_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DMS_QUALIFIER_AI", "1")
    db = _lake(tmp_path)

    def transport(_body: dict[str, Any]) -> dict[str, Any]:
        return _honored("country = 'Narnia'", literal="Narnia")

    out = review_candidate(
        _TOTAL_Q,
        "SELECT SUM(points) AS total FROM constructors WHERE country = 'Narnia'",
        warehouse=db,
        grantable=_grants(),
        served_route="model",
        transport=transport,
    )
    assert out["action"] == "reconfirm"
    assert out["envelope"]["sql_used"] is None
    assert "Narnia" not in str(out["envelope"].get("text") or "")


def test_unsure_reconfirms_with_a_suggested_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DMS_QUALIFIER_AI", "1")
    db = _lake(tmp_path)

    def transport(_body: dict[str, Any]) -> dict[str, Any]:
        return {
            "requirements": [
                {
                    "requirement": "which season the points belong to",
                    "verdict": "unsure",
                    "sql_fragment": None,
                    "column": None,
                    "literal": None,
                }
            ],
            "suggested_question": "What are the maximum points of British constructors in 2019?",
        }

    out = review_candidate(
        _ASKED,
        _MAX_SQL,
        warehouse=db,
        grantable=_grants(),
        transport=transport,
    )
    assert out["action"] == "reconfirm"
    block = out["envelope"]["reconfirm"]
    assert block["requirement"] == "which season the points belong to"
    assert block["suggested_question"].endswith("2019?")


def test_group_by_still_decides_when_the_flag_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear(monkeypatch)
    db = _lake(tmp_path)
    sql = "SELECT SUM(points) AS total FROM constructors"
    question = "points by supplier"
    monkeypatch.delenv("DMS_QUALIFIER_AI", raising=False)
    off = validated_sql_reason(sql, question=question, warehouse=db, grantable=_grants())
    monkeypatch.setenv("DMS_QUALIFIER_AI", "1")
    on = validated_sql_reason(sql, question=question, warehouse=db, grantable=_grants())
    assert off == "unhonored_qualifier:group_by=supplier"
    assert on == off


def test_submit_serves_the_honored_max_sql(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DMS_QUALIFIER_AI", "1")
    db = _lake(tmp_path)
    token = bind_qualifier_transport(lambda _body: _honored("MAX(points)"))
    submitted: list[str] = []

    def submit(statement: str) -> Any:
        submitted.append(statement)
        return _rows(statement)

    try:
        env = _submit_validated(
            _MAX_SQL,
            question=_ASKED,
            space_id="spc_qual",
            session_id="ses_qual",
            submit=submit,
            ledger_append=_ledger,
            notes=(),
            plan_source="ontology_plan",
            coverage=_coverage(),
            warehouse=db,
            grantable=_grants(),
            served_route="model",
            hint=_HINT,
        )
    finally:
        reset_qualifier_transport(token)
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["sql_used"] == _MAX_SQL
    assert "MAX(points)" in str(env["sql_used"])
    assert env.get("served_route") == "model"
    assert submitted == [_MAX_SQL]
