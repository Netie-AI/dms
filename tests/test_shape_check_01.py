"""R1-SHAPE-CHECK-01. The model judges result shape. Python does not.

Flag default off. Off, the extract loop does not call OpenVault and the
served SQL, prompt, and route stay the bytes they were. On, a misfit gets
one repair through the schema-retry feedback slot. A second misfit, or a
reply that is not JSON, reconfirms ``shape_misfit`` and is not served.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from dms_executor.demo_warehouse import SERVING_DIALECT, ensure_demo_warehouse
from dms_executor.shape_check import (
    bind_shape_transport,
    check_answer_shape,
    reset_shape_transport,
    shape_check_enabled,
    sql_shape_facts,
)
from dms_executor.sql_loop import AskCallBudget, run_model_loop

_SOURCE = Path(__file__).resolve().parents[1] / "packages/executor/dms_executor/shape_check.py"
_TOP3 = "Which three locations have the most volume?"
_PER_LOCATION = "Show quantity per location"
_WIDE = "SELECT location_code FROM locations LIMIT 1000"
_RANKED = "SELECT location_code FROM locations ORDER BY location_code LIMIT 3"
_TOTAL = "SELECT COUNT(*) AS total FROM locations"
_BANNED = (
    "model",
    "model_id",
    "model_name",
    "served_model",
    "served_provider",
    "provider",
    "strict",
)


def _model_sql(body: dict[str, Any] | None) -> str | None:
    if not isinstance(body, dict):
        return None
    sql = body.get("query_sql")
    return sql if isinstance(sql, str) and sql.strip() else None


def _submit(box: list[str]):
    def submit(sql: str, _attempts: list[dict[str, Any]]) -> dict[str, Any]:
        box.append(sql)
        return {
            "badge": "L2_VALIDATED",
            "abstained": False,
            "rows": [{"location_code": "kept"}],
            "values": [],
            "sql_used": sql,
            "served_route": "model",
            "route": "generated",
            "text": "served",
        }

    return submit


def _abstain(
    reason: str,
    _attempts: list[dict[str, Any]],
    *,
    sql: str | None = None,
    retries: int = 0,
) -> dict[str, Any]:
    return {
        "badge": "ABSTAIN",
        "abstained": True,
        "rows": [],
        "values": [],
        "sql_used": None,
        "served_route": "abstain",
        "route": "generated",
        "text": reason,
        "assumptions": [f"GEN-01: {reason}"],
        "retries": retries,
        "held_sql": sql,
    }


def _loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    flag: str | None,
    question: str,
    payload: dict[str, Any],
    compute,
    transport,
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    if flag is None:
        monkeypatch.delenv("DMS_SHAPE_CHECK", raising=False)
    else:
        monkeypatch.setenv("DMS_SHAPE_CHECK", flag)
    monkeypatch.setenv("DMS_INSIGHTS_CALL_CAP", "4")
    db = tmp_path / "shape.duckdb"
    ensure_demo_warehouse(db)
    submitted: list[str] = []
    attempts: list[dict[str, Any]] = []
    token = bind_shape_transport(transport)
    try:
        env = run_model_loop(
            question=question,
            ctx={
                "shape_ontology": {
                    "grain_keys": [{"object": "location", "keys": ["location_code"]}],
                    "measures": [],
                }
            },
            payload=payload,
            compute=compute,
            warehouse=db,
            dialect=SERVING_DIALECT,
            model_sql=_model_sql,
            check=lambda _sql: None,
            submit_sql=_submit(submitted),
            abstain=_abstain,
            empty_answer=lambda sql, _attempts: _submit(submitted)(sql, _attempts),
            attempts=attempts,
            budget=AskCallBudget(),
        )
    finally:
        reset_shape_transport(token)
    return env, submitted, attempts


def _stable(env: dict[str, Any]) -> str:
    return json.dumps(env, sort_keys=True, default=str)


def test_only_digit_one_enables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SHAPE_CHECK", raising=False)
    assert shape_check_enabled() is False
    for raw in ("0", "true", "yes", "on", "2", ""):
        monkeypatch.setenv("DMS_SHAPE_CHECK", raw)
        assert shape_check_enabled() is False
    monkeypatch.setenv("DMS_SHAPE_CHECK", "1")
    assert shape_check_enabled() is True


def test_facts_come_from_the_sql_tree_and_ignore_the_question() -> None:
    assert "question" not in inspect.signature(sql_shape_facts).parameters
    wide = sql_shape_facts(_WIDE, "duckdb")
    assert wide["parsed"] is True
    assert wide["has_order_by"] is False
    assert wide["limit"] == 1000
    assert wide["aggregate"] is False
    ranked = sql_shape_facts(_RANKED, "duckdb")
    assert ranked["has_order_by"] is True
    assert ranked["limit"] == 3
    total = sql_shape_facts(_TOTAL, "duckdb")
    assert total["aggregate"] is True
    assert total["group_by"] == []
    grouped = sql_shape_facts(
        "SELECT location_code, SUM(quantity_kg) AS qty FROM inventory GROUP BY location_code",
        "duckdb",
    )
    assert grouped["aggregate"] is True
    assert grouped["group_by"]
    assert "location_code" in grouped["group_by"][0]
    assert sql_shape_facts("not sql at all (((", "duckdb")["parsed"] is False


def test_request_uses_role_reason_and_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SHAPE_CHECK", "1")
    seen: list[dict[str, Any]] = []

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        seen.append(body)
        return {"verdict": "fit", "reasons": [], "fix_hint": None}

    out = check_answer_shape(
        _TOP3,
        _WIDE,
        rows=[{"location_code": "WH-A", "email": "person@example.com"}],
        ontology={
            "grain_keys": [{"object": "location", "keys": ["location_code"]}],
            "measures": [{"name": "qty", "grain": "location", "expression": "SUM(quantity_kg)"}],
        },
        dialect="duckdb",
        transport=transport,
    )
    assert out["verdict"] == "fit"
    assert len(seen) == 1
    body = seen[0]
    assert body["role"] == "reason"
    assert body["schema"] == "AnswerShapeReply"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "AnswerShapeReply"
    assert "strict" not in body["response_format"]["json_schema"]
    blob = json.dumps(body)
    for banned in _BANNED:
        assert f'"{banned}"' not in blob
    user = json.loads(body["messages"][1]["content"])
    assert user["facts"]["has_order_by"] is False
    assert user["facts"]["limit"] == 1000
    assert user["ontology"]["grain_keys"][0]["keys"] == ["location_code"]
    assert "person@example.com" not in blob


def test_flag_off_check_does_not_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SHAPE_CHECK", raising=False)

    def transport(_body: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("shape call")

    out = check_answer_shape(_TOP3, _WIDE, rows=[{"location_code": "WH-A"}], transport=transport)
    assert out == {"verdict": "skipped", "reasons": [], "fix_hint": None}


def test_unparseable_reply_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SHAPE_CHECK", "1")
    out = check_answer_shape(
        _TOP3,
        _WIDE,
        rows=[{"n": 1}],
        transport=lambda _body: "this is not json",
    )
    assert out["verdict"] == "unknown"
    assert out["fix_hint"] is None
    fenced = check_answer_shape(
        _TOP3,
        _RANKED,
        rows=[{"location_code": "WH-A"}],
        transport=lambda _body: (
            "```json\n" + json.dumps({"verdict": "fit", "reasons": [], "fix_hint": None}) + "\n```"
        ),
    )
    assert fenced["verdict"] == "fit"


def test_flag_off_is_byte_identical_and_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def transport(_body: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        raise AssertionError("shape call")

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("repair call")

    payload = {"query_sql": _WIDE}
    first, submitted, attempts = _loop(
        tmp_path,
        monkeypatch,
        flag=None,
        question=_TOP3,
        payload=payload,
        compute=compute,
        transport=transport,
    )
    second, submitted_b, attempts_b = _loop(
        tmp_path,
        monkeypatch,
        flag="0",
        question=_TOP3,
        payload=payload,
        compute=compute,
        transport=transport,
    )
    assert calls == []
    assert submitted == [_WIDE]
    assert submitted_b == [_WIDE]
    assert _stable(first) == _stable(second)
    assert first["served_route"] == "model"
    assert first["sql_used"] == _WIDE
    assert attempts[-1]["prompt"] == _TOP3
    assert attempts_b[-1]["prompt"] == _TOP3
    assert attempts[-1]["outcome"] == "served"
    assert json.dumps(attempts, sort_keys=True, default=str) == json.dumps(
        attempts_b, sort_keys=True, default=str
    )


def test_top3_misfit_repairs_once_and_serves_the_ranked_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shape_calls: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []

    def transport(body: dict[str, Any]) -> dict[str, Any]:
        shape_calls.append(body)
        user = json.loads(body["messages"][1]["content"])
        facts = user["facts"]
        if facts["has_order_by"] is True and facts["limit"] == 3:
            return {"verdict": "fit", "reasons": [], "fix_hint": None}
        return {
            "verdict": "misfit",
            "reasons": ["ranked result needs an order and a limit of 3"],
            "fix_hint": "Add ORDER BY and LIMIT 3.",
        }

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        repairs.append(ctx.get("sql_loop_feedback") or {})
        return {"query_sql": _RANKED}

    env, submitted, attempts = _loop(
        tmp_path,
        monkeypatch,
        flag="1",
        question=_TOP3,
        payload={"query_sql": _WIDE},
        compute=compute,
        transport=transport,
    )
    assert len(shape_calls) == 2
    assert shape_calls[0]["role"] == "reason"
    assert submitted == [_RANKED]
    assert "ORDER BY" in env["sql_used"]
    assert "LIMIT 3" in env["sql_used"]
    assert env["served_route"] == "model"
    assert env["abstained"] is False
    assert repairs[0]["repair_hint"]["kind"] == "shape_misfit"
    assert "LIMIT 3" in repairs[0]["repair_hint"]["fix_hint"]
    assert attempts[0]["outcome"] == "shape_misfit"
    assert attempts[-1]["outcome"] == "served"
    assert _WIDE not in submitted


def test_per_location_single_total_misfits_twice_and_reconfirms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def transport(body: dict[str, Any]) -> dict[str, Any]:
        user = json.loads(body["messages"][1]["content"])
        if user["facts"]["aggregate"] is True and user["row_count"] == 1:
            return {
                "verdict": "misfit",
                "reasons": ["one total row is not one row per location"],
                "fix_hint": "Group by the location grain.",
            }
        return {"verdict": "fit", "reasons": [], "fix_hint": None}

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        assert ctx["sql_loop_feedback"]["repair_hint"]["kind"] == "shape_misfit"
        return {"query_sql": _TOTAL}

    env, submitted, _attempts = _loop(
        tmp_path,
        monkeypatch,
        flag="1",
        question=_PER_LOCATION,
        payload={"query_sql": _TOTAL},
        compute=compute,
        transport=transport,
    )
    assert submitted == []
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["sql_used"] is None
    assert env["badge"] == "ABSTAIN"
    block = env["reconfirm"]
    assert block["reason_code"] == "shape_misfit"
    assert block["verdict"] == "misfit"
    assert block["reasons"]


def test_unparseable_model_reconfirms_and_does_not_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    computes: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        computes.append(ctx)
        return {"query_sql": _RANKED}

    env, submitted, attempts = _loop(
        tmp_path,
        monkeypatch,
        flag="1",
        question=_TOP3,
        payload={"query_sql": _WIDE},
        compute=compute,
        transport=lambda _body: {"choices": [{"message": {"content": "nope"}}]},
    )
    assert computes == []
    assert submitted == []
    assert env["rows"] == []
    assert env["sql_used"] is None
    assert env["reconfirm"]["reason_code"] == "shape_misfit"
    assert env["reconfirm"]["verdict"] == "unknown"
    assert "unparseable" in env["reconfirm"]["reasons"]
    assert attempts[0]["outcome"] == "shape_unknown"


def test_shape_check_source_has_no_regex_or_question_membership() -> None:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"re", "regex"}
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"re", "regex"}
        if isinstance(node, ast.Attribute) and node.attr in {
            "search",
            "match",
            "findall",
            "fullmatch",
            "sub",
            "compile",
        }:
            base = node.value
            assert not (isinstance(base, ast.Name) and base.id in {"re", "regex"})
        membership = isinstance(node, ast.Compare) and any(
            isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
        )
        if membership:
            for child in ast.walk(node):
                assert not (isinstance(child, ast.Name) and child.id == "question")
