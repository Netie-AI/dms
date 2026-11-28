"""NL19-NO-SQL-GENERATE. Flag default off.

Off, the generate prompt, the envelope, and the served route stay as they
are. On, an empty generate is retried once with the granted schema and the
refusal reason. SQL from that retry is served. Two empties ask back with a
non-empty suggestion. No question-text rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from cortex_client.compute import begin_answer_model_calls
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generate_retry import (
    append_granted_schema,
    enabled,
    granted_schema_block,
)
from dms_executor.generative_ask import maybe_generative_ask

_ASK = "Count the orders."
_SQL = "SELECT COUNT(*) AS n FROM orders"
_SPACE = "space-nl19"
_CLOCK = ("as_of",)


def _stable(env: dict[str, Any] | None) -> dict[str, Any] | None:
    if env is None:
        return None
    out = dict(env)
    for key in list(out):
        if key in _CLOCK or key.startswith("engine_"):
            out.pop(key)
    return out


def _lake(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TYPE ship_status AS ENUM ('open', 'held')")
        con.execute("CREATE TABLE orders (id INTEGER, status ship_status)")
        con.execute("INSERT INTO orders VALUES (1, 'open')")
    finally:
        con.close()
    return path


def _submit(db: Path):
    def submit(sql: str) -> Any:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(item[0]) for item in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_nl19", output={"rows": rows})

    return submit


def _ask(db: Path, compute) -> dict[str, Any] | None:
    begin_answer_model_calls()
    return maybe_generative_ask(
        _ASK,
        warehouse=db,
        grantable={"orders"},
        compute=compute,
        submit=_submit(db),
        ledger_append=lambda _payload: SimpleNamespace(entry_id="led_nl19", hash="hash_nl19"),
        dialect="duckdb",
        space_id=_SPACE,
        demo_ontology_allowed=False,
    )


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DMS_GENERATE_RETRY_ASKBACK",
        "DMS_SCALE_MISS",
        "DMS_ASK_LADDER",
        "DMS_CLOOP_B",
        "DMS_SCHEMA_RETRY",
        "DMS_SCHEMA_CONTEXT",
        "DMS_AI_FIRST",
        "DMS_RULES_OFF",
    ):
        monkeypatch.delenv(name, raising=False)


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    assert enabled() is False
    monkeypatch.setenv("DMS_GENERATE_RETRY_ASKBACK", "true")
    assert enabled() is False
    monkeypatch.setenv("DMS_GENERATE_RETRY_ASKBACK", "0")
    assert enabled() is False
    monkeypatch.setenv("DMS_GENERATE_RETRY_ASKBACK", "1")
    assert enabled() is True


def test_schema_block_lists_physical_columns_and_enum_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear(monkeypatch)
    db = _lake(tmp_path / "orders.duckdb")
    block = granted_schema_block(db, {"orders"}, "duckdb")
    assert "orders" in block
    assert "id" in block
    assert "status" in block
    assert "open" in block
    assert "held" in block
    bare = "head\nQuestion:\nCount"
    assert append_granted_schema(bare, db, {"orders"}, "duckdb") == bare


def test_flag_off_prompt_envelope_and_route_are_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear(monkeypatch)
    db = _lake(tmp_path / "orders.duckdb")
    seen: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(json.dumps(ctx, sort_keys=True, default=str))
        return {
            "query_sql": _SQL,
            "served_model": "fake",
            "served_provider": "groq",
        }

    unset = _stable(_ask(db, compute))
    assert unset is not None
    assert unset.get("abstained") is not True
    assert unset.get("rows")
    route = unset.get("served_route")
    prompt = seen[0]
    assert "generate_retry" not in prompt
    assert "GRANTED SCHEMA" not in prompt
    monkeypatch.setenv("DMS_GENERATE_RETRY_ASKBACK", "0")
    seen.clear()
    zero = _stable(_ask(db, compute))
    assert zero == unset
    assert seen[0] == prompt
    assert zero.get("served_route") == route
    assert json.dumps(unset, sort_keys=True, default=str) == json.dumps(
        zero, sort_keys=True, default=str
    )


def test_flag_off_empty_generate_does_not_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear(monkeypatch)
    db = _lake(tmp_path / "orders.duckdb")
    seen: list[str] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(json.dumps(ctx, sort_keys=True, default=str))
        return {}

    unset = _ask(db, compute)
    first = list(seen)
    monkeypatch.setenv("DMS_GENERATE_RETRY_ASKBACK", "0")

    def compute_zero(ctx: dict[str, Any]) -> dict[str, Any]:
        zero_seen.append(json.dumps(ctx, sort_keys=True, default=str))
        return {}

    zero_seen: list[str] = []
    zero = _ask(db, compute_zero)
    assert unset == zero
    assert len(first) == 1
    assert first == zero_seen
    assert "GRANTED SCHEMA" not in first[0]
    assert "generate_retry" not in first[0]


def test_none_then_sql_is_served_after_one_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DMS_GENERATE_RETRY_ASKBACK", "1")
    db = _lake(tmp_path / "orders.duckdb")
    seen: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(ctx))
        if len(seen) == 1:
            return {"status": "REFUSE", "ok": False}
        return {
            "query_sql": _SQL,
            "served_model": "fake",
            "served_provider": "groq",
        }

    env = _ask(db, compute)
    assert isinstance(env, dict)
    assert_envelope_valid(env)
    assert env.get("abstained") is not True
    assert env.get("rows")
    assert len(seen) == 2
    prompt = str(seen[1].get("sql_vote_prompt") or "")
    assert "GRANTED SCHEMA" in prompt
    assert "orders" in prompt
    assert "status" in prompt
    assert "open" in prompt
    assert "held" in prompt
    assert "insights_refused" in prompt
    assert seen[1].get("sql_vote_kind") == "generate_retry"
    audit = env["generate_audit"]
    assert audit["legs"] == 2
    assert audit["rung"] == "generate_retry"
    assert audit["refusal_class"] == "insights_refused"
    assert "cannot certify" not in str(env.get("text") or "").lower()


def test_none_twice_asks_back_with_a_suggestion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DMS_GENERATE_RETRY_ASKBACK", "1")
    db = _lake(tmp_path / "orders.duckdb")
    seen: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(ctx))
        return {}

    env = _ask(db, compute)
    assert isinstance(env, dict)
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert len(seen) == 2
    offer = env["reconfirm"]
    assert isinstance(offer, dict)
    suggestion = str(offer.get("suggested_question") or "")
    assert suggestion
    assert "cannot certify" not in str(env.get("text") or "").lower()
    audit = env["generate_audit"]
    assert audit["legs"] == 2
    assert audit["rung"] == "askback"
    assert audit["refusal_class"] == "no_sql"
    block = str(seen[1].get("schema_context") or "")
    assert "status" in block
    assert "open" in block
