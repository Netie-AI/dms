"""SCHEMA-RETRY-01: repair a database error, then schema, then the escalate role.

Synthetic schemas only. The flag defaults off. Off, the retry context and the
envelope match the typed-code retry.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client.compute import (
    FREEROUTE_PREFERENCE,
    _insights_body,
    begin_answer_model_calls,
)
from dms_executor.demo_warehouse import (
    RESERVED_PARAM_AS_OF,
    SERVING_DIALECT,
    connect_file,
    ensure_demo_warehouse,
)
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.schema_retry import (
    SchemaLadder,
    build_repair_hint,
    identifier_in_sql,
    parse_engine_error,
    select_candidates,
)
from dms_executor.sql_loop import AskCallBudget, run_model_loop

_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_MODEL = "openai/gpt-oss-120b"
_PROVIDER = "groq"
_KEY = "ovk-schema-retry"
_QUESTION = "Which inventory skus are listed?"
_BAD = "SELECT skuu FROM inventory"
_GOOD = "SELECT sku FROM inventory"
_SENTINEL = "ZXQROW8675309"
_MESSAGE_BITS = ("Binder Error", "Catalog Error", "Candidate bindings", "Did you mean")


def _loop(monkeypatch: pytest.MonkeyPatch, *, flag: str | None, cap: str) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    monkeypatch.setenv("DMS_INSIGHTS_CALL_CAP", cap)
    monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)
    if flag is None:
        monkeypatch.delenv("DMS_SCHEMA_RETRY", raising=False)
    else:
        monkeypatch.setenv("DMS_SCHEMA_RETRY", flag)


def _names(**extra: Any) -> dict[str, Any]:
    base = {
        "served_model": _MODEL,
        "served_provider": _PROVIDER,
        "ov_key_id": _KEY,
        "phase": "generate",
    }
    base.update(extra)
    return base


def _prepare(db: Path) -> None:
    ensure_demo_warehouse(db)
    con = connect_file(db)
    try:
        con.execute("ALTER TABLE inventory ADD COLUMN sku_hidden VARCHAR")
        con.execute("UPDATE inventory SET sku_hidden = ?", [_SENTINEL])
        con.execute("CREATE TABLE vault (sku_hidden VARCHAR)")
        con.execute("INSERT INTO vault VALUES (?)", [_SENTINEL])
    finally:
        con.close()


def _grants() -> set[str]:
    """``inventory.sku`` limits that table. ``sku_hidden`` and ``vault`` stay out."""
    return set(_GRANTS) | {"inventory.sku"}


def _submit(db: Path):
    def submit(sql: str) -> Any:
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_schema", output={"rows": rows})

    return submit


def _ledger(_p: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_schema", hash="hash_schema_not_entry")


def _ask(db: Path, compute) -> dict[str, Any] | None:
    begin_answer_model_calls()
    onto = load_verified_ontology(db, demo_ontology(db))
    return maybe_generative_ask(
        _QUESTION,
        warehouse=db,
        grantable=_grants(),
        compute=compute,
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
        dialect=SERVING_DIALECT,
    )


def _blob(*parts: Any) -> str:
    return json.dumps(parts, sort_keys=True, default=str)


def _assert_clean(text: str) -> None:
    assert _SENTINEL not in text
    for bit in _MESSAGE_BITS:
        assert bit not in text


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable(item)
            for key, item in value.items()
            if key not in {"as_of", "ticket_id", "audit_id"}
        }
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


class _Pg(Exception):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.sqlstate = state


class _My(Exception):
    def __init__(self, errno: int, message: str) -> None:
        super().__init__(errno, message)
        self.errno = errno


class _Ss(Exception):
    def __init__(self, number: int, message: str) -> None:
        super().__init__(number, message)


def test_missing_column_repairs_on_rung1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch, flag="1", cap="4")
    db = tmp_path / "loop.duckdb"
    _prepare(db)
    seen: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(ctx))
        if len(seen) == 1:
            return _names(query_sql=_BAD)
        return _names(query_sql=_GOOD)

    env = _ask(db, compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    feedback = seen[1].get("sql_loop_feedback") or {}
    hint = feedback.get("repair_hint") or {}
    assert hint.get("kind") == "missing_column"
    assert hint.get("identifier") == "skuu"
    names = [item.get("name") for item in hint.get("candidates") or []]
    assert "inventory.sku" in names
    assert all("hidden" not in str(name) and not str(name).startswith("vault") for name in names)
    assert all(item.get("route_hint") is None for item in seen)
    served = [item for item in env["loop"] if item.get("repair") == "repair_ok@rung1"]
    assert served
    assert served[0]["rung"] == "rung1"
    assert served[0]["served_model"] == _MODEL
    assert "skuu" not in str(env.get("sql_used") or "")
    _assert_clean(_blob(seen, env))


def test_ungranted_column_is_not_a_candidate(tmp_path: Path) -> None:
    db = tmp_path / "cols.duckdb"
    _prepare(db)
    hint = build_repair_hint(
        _BAD,
        warehouse=db,
        grantable=_grants(),
        dialect=SERVING_DIALECT,
    )
    names = [item["name"] for item in hint["candidates"]]
    assert hint["identifier"] == "skuu"
    assert "inventory.sku" in names
    assert "inventory.sku_hidden" not in names
    assert all(not name.startswith("vault") for name in names)
    _assert_clean(json.dumps(hint))


def test_candidates_from_a_grant_fixture_skip_the_hidden_name() -> None:
    names = [
        item["name"]
        for item in select_candidates(
            "missing_column",
            "revenu",
            sql="SELECT revenu FROM sales",
            warehouse=None,
            grantable={"sales", "sales.revenue"},
            dialect="duckdb",
            columns=[
                ("sales", "revenue", "INTEGER"),
                ("sales", "revenue_hidden", "INTEGER"),
                ("vault", "revenue_hidden", "INTEGER"),
            ],
        )
    ]
    assert names == ["sales.revenue"]


def test_duckdb_missing_table_and_ambiguous_column(tmp_path: Path) -> None:
    db = tmp_path / "kinds.duckdb"
    con = connect_file(db)
    con.execute("CREATE TABLE inventory (sku VARCHAR)")
    con.execute("CREATE TABLE shipments (sku VARCHAR)")
    con.close()
    missing = build_repair_hint(
        "SELECT sku FROM inventry",
        warehouse=db,
        grantable={"inventry", "inventory"},
        dialect="duckdb",
    )
    assert missing["kind"] == "missing_table"
    assert missing["identifier"] == "inventry"
    assert any(item["name"] == "inventory" for item in missing["candidates"])
    ambiguous = build_repair_hint(
        "SELECT sku FROM inventory JOIN shipments ON inventory.sku = shipments.sku",
        warehouse=db,
        grantable={"inventory", "shipments"},
        dialect="duckdb",
    )
    assert ambiguous["kind"] == "ambiguous_column"
    assert ambiguous["identifier"] == "sku"
    _assert_clean(json.dumps([missing, ambiguous]))


def test_conversion_message_does_not_become_the_hint(tmp_path: Path) -> None:
    db = tmp_path / "cast.duckdb"
    con = connect_file(db)
    con.execute("CREATE TABLE sales (sku VARCHAR)")
    con.execute("INSERT INTO sales VALUES (?)", [_SENTINEL])
    con.close()
    hint = build_repair_hint(
        "SELECT CAST(sku AS INTEGER) FROM sales",
        warehouse=db,
        grantable={"sales"},
        dialect="duckdb",
    )
    assert hint["kind"] == "type_mismatch"
    assert hint["identifier"] is None
    _assert_clean(json.dumps(hint))


@pytest.mark.parametrize(
    ("state", "kind"),
    [("42703", "missing_column"), ("42P01", "missing_table"), ("42702", "ambiguous_column")],
)
def test_postgres_sqlstate(state: str, kind: str) -> None:
    parsed, _token = parse_engine_error(_Pg(state, 'column "revenu" does not exist'))
    assert parsed == kind


@pytest.mark.parametrize(
    ("errno", "kind"),
    [(1054, "missing_column"), (1146, "missing_table"), (1052, "ambiguous_column")],
)
def test_mysql_errno(errno: int, kind: str) -> None:
    parsed, _token = parse_engine_error(_My(errno, "Unknown column 'revenu' in 'field list'"))
    assert parsed == kind


def test_sqlserver_number() -> None:
    parsed, _token = parse_engine_error(_Ss(207, "Invalid column name 'revenu'."))
    assert parsed == "missing_column"


def test_sqlite_prefixes() -> None:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE sales (revenue INTEGER)")
    with pytest.raises(sqlite3.OperationalError) as missing:
        con.execute("SELECT revenu FROM sales")
    kind, token = parse_engine_error(missing.value)
    assert (kind, token) == ("missing_column", "revenu")
    with pytest.raises(sqlite3.OperationalError) as table:
        con.execute("SELECT revenue FROM inventry")
    assert parse_engine_error(table.value)[0] == "missing_table"
    con.execute("CREATE TABLE buyers (revenue INTEGER)")
    with pytest.raises(sqlite3.OperationalError) as ambiguous:
        con.execute("SELECT revenue FROM sales JOIN buyers")
    assert parse_engine_error(ambiguous.value)[0] == "ambiguous_column"


def test_unknown_dialect_reconfirms_without_a_duckdb_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_SCHEMA_RETRY", "1")
    calls: list[Any] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(ctx)
        return _names(query_sql=_GOOD)

    budget = AskCallBudget()
    budget.take()
    env = run_model_loop(
        question=_QUESTION,
        ctx={},
        payload=_names(query_sql=_BAD),
        compute=compute,
        warehouse=None,
        dialect="not-a-dialect",
        model_sql=lambda payload: (payload or {}).get("query_sql"),
        check=lambda _sql: "explain:BinderException",
        submit_sql=lambda _sql, _attempts: {"abstained": False, "rows": [{"sku": "a"}]},
        abstain=lambda why, attempts, **_kw: {"abstained": True, "reason": why, "loop": attempts},
        empty_answer=lambda _sql, _attempts: {"abstained": True},
        attempts=[],
        budget=budget,
        schema_ladder=SchemaLadder(
            grantable={"inventory"}, warehouse=None, dialect="not-a-dialect"
        ),
    )
    assert env["reason"] == "sql_dialect_unknown"
    assert calls == []
    assert identifier_in_sql("SELECT revenu FROM sales", "revenu", "") is None
    assert identifier_in_sql("SELECT revenu FROM sales", "revenu", "not-a-dialect") is None
    assert (
        select_candidates(
            "missing_column",
            "revenu",
            sql="SELECT revenu FROM sales",
            warehouse=None,
            grantable={"sales", "sales.revenue"},
            dialect="not-a-dialect",
            columns=[("sales", "revenue", "INTEGER")],
        )
        == []
    )


def test_message_token_is_dropped_when_the_sql_did_not_write_it() -> None:
    hint = build_repair_hint(
        "SELECT revenu FROM sales",
        warehouse=None,
        grantable={"sales"},
        dialect="duckdb",
        exc=_Pg("42703", f'column "{_SENTINEL}" does not exist'),
    )
    assert hint["identifier"] is None
    assert hint["candidates"] == []
    _assert_clean(json.dumps(hint))
    assert "does not exist" not in json.dumps(hint)


def test_cap_two_stops_after_rung1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch, flag="1", cap="2")
    db = tmp_path / "cap.duckdb"
    _prepare(db)
    seen: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(ctx))
        return _names(query_sql=_BAD)

    env = _ask(db, compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert len(seen) == 2
    feedback = seen[1].get("sql_loop_feedback") or {}
    assert (feedback.get("repair_hint") or {}).get("identifier") == "skuu"
    assert "schema_slice" not in feedback
    assert seen[1].get("route_hint") is None
    assert env["reconfirm"]["reason_code"].startswith("schema_repair_exhausted:")
    assert any(item.get("rung") == "rung1" for item in env["loop"])
    _assert_clean(_blob(seen, env))


def test_cap_one_does_not_call_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch, flag="1", cap="1")
    db = tmp_path / "cap1.duckdb"
    _prepare(db)
    seen: list[int] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(1)
        return _names(query_sql=_BAD)

    env = _ask(db, compute)
    assert env is not None
    assert len(seen) == 1
    assert env["reconfirm"]["reason_code"].startswith("schema_repair_exhausted:")
    assert all(item.get("rung") is None for item in env["loop"])


def test_rung3_is_the_only_escalate_and_a_422_reconfirms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch, flag="1", cap="4")
    db = tmp_path / "esc.duckdb"
    _prepare(db)
    seen: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(ctx))
        if len(seen) < 4:
            return _names(query_sql=_BAD)
        return _names(_insights_http_status=422, ai_call_status=422, route_error="unknown_role")

    env = _ask(db, compute)
    assert env is not None
    assert len(seen) == 4
    assert seen[1].get("route_hint") is None
    assert seen[2].get("route_hint") is None
    assert "schema_slice" in (seen[2].get("sql_loop_feedback") or {})
    assert seen[3].get("route_hint") == "escalate"
    assert env["reconfirm"]["reason_code"].startswith("schema_repair_exhausted:")
    assert any(item.get("rung") == "rung3" for item in env["loop"])
    _assert_clean(_blob(seen, env))
    assert "opus" not in _blob(seen).lower()
    assert "anthropic" not in _blob(seen).lower()


def test_flag_off_matches_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def once(flag: str | None, db: Path) -> tuple[Any, Any]:
        _loop(monkeypatch, flag=flag, cap="4")
        _prepare(db)
        seen: list[Any] = []

        def compute(ctx: dict[str, Any]) -> dict[str, Any]:
            seen.append(_stable(ctx))
            return _names(query_sql=_BAD)

        env = _ask(db, compute)
        assert env is not None
        return seen, _stable(env)

    unset_seen, unset_env = once(None, tmp_path / "off.duckdb")
    zero_seen, zero_env = once("0", tmp_path / "zero.duckdb")
    assert unset_seen == zero_seen
    assert unset_env == zero_env
    feedback = unset_seen[1].get("sql_loop_feedback") or {}
    assert set(feedback) == {"previous_sql", "reason", "prompt"}
    assert "repair_hint" not in _blob(unset_seen, unset_env)
    assert "route_hint" not in _blob(unset_seen, unset_env)


def test_seam_forwards_escalate_as_a_role_hint() -> None:
    from dms_executor import _insights_compute_seam

    captured: dict[str, Any] = {}

    class _Cortex:
        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
            captured["question"] = question
            captured["kwargs"] = kwargs
            return {"query_sql": "SELECT 1", "served_model": _MODEL}

    feedback = {
        "previous_sql": _BAD,
        "reason": "checker:explain:BinderException",
        "prompt": "stored",
        "repair_hint": {
            "kind": "missing_column",
            "identifier": "skuu",
            "candidates": [{"name": "inventory.sku", "type": "VARCHAR"}],
        },
    }
    _insights_compute_seam(
        _Cortex(),
        _QUESTION,
        session_id="s",
        space_id="p",
        ontology={"route_hint": "escalate", "sql_loop_feedback": feedback},
    )
    assert captured["kwargs"]["route_hint"] == "escalate"
    assert "route_hint" not in (captured["kwargs"].get("ontology") or {})
    assert captured["kwargs"]["sql_feedback"]["repair_hint"]["identifier"] == "skuu"
    captured.clear()
    _insights_compute_seam(
        _Cortex(),
        _QUESTION,
        session_id="s",
        space_id="p",
        ontology={"sql_loop_feedback": feedback},
    )
    assert "route_hint" not in captured["kwargs"]


def test_prompt_bytes_change_only_when_a_hint_is_present() -> None:
    feedback = {
        "previous_sql": _BAD,
        "reason": "checker:explain:BinderException",
        "prompt": "stored",
    }
    plain = _insights_body(
        _QUESTION, session_id="s", space_id="p", ontology=None, sql_feedback=feedback
    )
    assert plain["question"] == (
        f"{_QUESTION}\n\nprevious_sql:\n{_BAD}\n\nfeedback:\nchecker:explain:BinderException"
    )
    assert plain["model_preference"] == FREEROUTE_PREFERENCE
    assert set(plain["sql_feedback"]) == {"previous_sql", "reason"}
    hinted = _insights_body(
        _QUESTION,
        session_id="s",
        space_id="p",
        ontology=None,
        sql_feedback={
            **feedback,
            "repair_hint": {
                "kind": "missing_column",
                "identifier": "skuu",
                "candidates": [{"name": "inventory.sku", "type": "VARCHAR"}],
                "message": f"Binder Error {_SENTINEL}",
            },
            "schema_slice": {
                "tables": [
                    {
                        "name": "inventory",
                        "columns": [
                            {
                                "name": "sku",
                                "type": "VARCHAR",
                                "nullable": True,
                                "samples": [_SENTINEL],
                            }
                        ],
                    }
                ],
                "joins": [],
            },
        },
        route_hint="escalate",
    )
    assert "Fix only the identifier and kind named in repair_hint." in hinted["question"]
    assert hinted["model_preference"] == "escalate"
    assert set(hinted["sql_feedback"]) == {"previous_sql", "reason"}
    _assert_clean(hinted["question"])
    assert "opus" not in hinted["model_preference"]


@pytest.mark.parametrize(
    "reason",
    ["definition_mismatch", "pii", "def_validate", RESERVED_PARAM_AS_OF],
)
def test_no_retry_reasons_stay_closed(reason: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCHEMA_RETRY", "1")
    calls: list[Any] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(ctx)
        return {}

    budget = AskCallBudget()
    budget.take()
    env = run_model_loop(
        question=_QUESTION,
        ctx={},
        payload=_names(query_sql=_BAD),
        compute=compute,
        warehouse=None,
        dialect="duckdb",
        model_sql=lambda payload: (payload or {}).get("query_sql"),
        check=lambda _sql: reason,
        submit_sql=lambda _sql, _attempts: {"abstained": False, "rows": [{"sku": "a"}]},
        abstain=lambda why, attempts, **_kw: {"abstained": True, "reason": why, "loop": attempts},
        empty_answer=lambda _sql, _attempts: {"abstained": True},
        attempts=[],
        no_retry_reasons=frozenset(
            {RESERVED_PARAM_AS_OF, "pii", "def_validate", "definition_mismatch"}
        ),
        budget=budget,
        schema_ladder=SchemaLadder(grantable={"inventory"}, warehouse=None, dialect="duckdb"),
    )
    assert env["reason"] == reason
    assert calls == []


def test_repair_ok_counts_read_attempt_records() -> None:
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[1] / "scripts" / "verify" / "failure_cases.py"
    spec = importlib.util.spec_from_file_location("failure_cases_schema", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["failure_cases_schema"] = mod
    spec.loader.exec_module(mod)
    counts = mod.repair_ok_counts(
        {
            "q": {
                "env": {
                    "loop": [
                        {"repair": "repair_ok@rung1"},
                        {"repair": "repair_ok@rung1"},
                        {"outcome": "served"},
                    ]
                }
            }
        }
    )
    assert counts == [("repair_ok@rung1", 2)]
