"""NL19-EXPLAIN-REPAIR. Synthetic schema. The flag defaults off.

Off, the prompt bytes, the envelope, and the served route match a second
off run. On, one repair serves. The same SQL stops and asks back.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client.compute import begin_answer_model_calls
from dms_executor.demo_warehouse import SERVING_DIALECT, connect_file
from dms_executor.envelope import assert_envelope_valid
from dms_executor.explain_repair import audit_records, clear_audits
from dms_executor.generative_ask import maybe_generative_ask

_ROOT = Path(__file__).resolve().parents[1]
_QUESTION = "Count shipment rows for one status code."
_BAD = "SELECT COUNT(*) AS n FROM shipments WHERE shipment_status = 'delayed'"
_GOOD = "SELECT COUNT(*) AS n FROM shipments WHERE status = 'DELAYED'"
_SPACE = "space-explain-repair"
_SENTINEL = "ZXQROW8675309"
_CLOCK = {"as_of", "engine_as_of", "engine_as_of_after"}


def _env(monkeypatch: pytest.MonkeyPatch, flag: str | None) -> None:
    monkeypatch.setenv("DMS_INSIGHTS_CALL_CAP", "4")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.delenv("DMS_ACTOR_ROLE", raising=False)
    for name in (
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_ASK_LADDER",
        "DMS_SCHEMA_RETRY",
        "DMS_SCALE_MISS",
        "DMS_AI_FIRST",
        "DMS_RULES_OFF",
        "DMS_AI_CLARIFY",
        "DMS_SCHEMA_CONTEXT",
    ):
        monkeypatch.delenv(name, raising=False)
    if flag is None:
        monkeypatch.delenv("DMS_EXPLAIN_REPAIR_TRACE", raising=False)
    else:
        monkeypatch.setenv("DMS_EXPLAIN_REPAIR_TRACE", flag)


def _lake(tmp_path: Path) -> Path:
    db = tmp_path / "shipments.duckdb"
    con = connect_file(db)
    try:
        con.execute("CREATE TABLE shipments (shipment_id VARCHAR, status VARCHAR, note VARCHAR)")
        rows = [("ROW-9001", "DELAYED", f"{_SENTINEL} note")]
        rows.extend((f"ROW-{index}", "ON_TIME", f"{_SENTINEL} note") for index in range(10))
        rows.append(("ROW-9012", "DELAYED", f"{_SENTINEL} note"))
        con.executemany("INSERT INTO shipments VALUES (?, ?, ?)", rows)
    finally:
        con.close()
    return db


def _submit(db: Path):
    def submit(sql: str) -> Any:
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            fetched = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_explain", output={"rows": fetched})

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_explain", hash="hash_explain_not_entry")


def _ask(db: Path, compute) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    seen: list[dict[str, Any]] = []

    def _wrapped(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(json.loads(json.dumps(ctx, default=str)))
        return compute(ctx, len(seen))

    begin_answer_model_calls()
    clear_audits()
    env = maybe_generative_ask(
        _QUESTION,
        warehouse=db,
        grantable={"shipments"},
        compute=_wrapped,
        submit=_submit(db),
        ledger_append=_ledger,
        dialect=SERVING_DIALECT,
        space_id=_SPACE,
        demo_ontology_allowed=False,
    )
    return seen, env


def _names(**extra: Any) -> dict[str, Any]:
    body = {"phase": "generate", "served_model": "role/repair", "served_provider": "openvault"}
    body.update(extra)
    return body


def _freeze(value: Any) -> Any:
    """Clock fields drop. String lists sort so DuckDB value order does not flake."""
    if isinstance(value, dict):
        return {key: _freeze(item) for key, item in sorted(value.items()) if key not in _CLOCK}
    if isinstance(value, list):
        items = [_freeze(item) for item in value]
        if items and all(isinstance(item, str) for item in items):
            return sorted(items)
        return items
    return value


def _repair_prompt(seen: list[dict[str, Any]]) -> str:
    for ctx in seen:
        blob = json.dumps(ctx, sort_keys=True)
        if "enum_values:" in blob:
            return str(ctx.get("sql_vote_prompt") or blob)
    return ""


def test_flag_off_is_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _lake(tmp_path)

    def compute(_ctx: dict[str, Any], _nth: int) -> dict[str, Any]:
        return _names(query_sql=_BAD)

    _env(monkeypatch, None)
    seen_off, env_off = _ask(db, compute)
    _env(monkeypatch, "0")
    seen_zero, env_zero = _ask(db, compute)
    assert env_off is not None and env_zero is not None
    assert_envelope_valid(env_off)
    assert _freeze(seen_off) == _freeze(seen_zero)
    assert _freeze(env_off) == _freeze(env_zero)
    assert env_off.get("served_route") == env_zero.get("served_route")
    assert "debug" not in env_off
    assert len(seen_off) == 1
    assert "enum_values:" not in json.dumps(seen_off)
    assert "validate:explain:" in " ".join(str(item) for item in env_off["assumptions"])


def test_one_repair_serves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, "1")
    db = _lake(tmp_path)

    def compute(ctx: dict[str, Any], nth: int) -> dict[str, Any]:
        if "enum_values:" in json.dumps(ctx):
            return _names(query_sql=_GOOD)
        assert nth == 1
        return _names(query_sql=_BAD)

    seen, env = _ask(db, compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert "status" in str(env.get("sql_used") or "")
    assert "DELAYED" in str(env.get("sql_used") or "")
    assert len(seen) == 2
    prompt = _repair_prompt(seen)
    assert "enum_values:" in prompt
    assert "shipments.status: DELAYED | ON_TIME" in prompt
    assert "granted_schema:" in prompt
    assert "shipments.status VARCHAR" in prompt
    assert "ask_ladder_rung:" in prompt
    assert "repair" in prompt
    assert "BinderException" in prompt
    assert _SENTINEL not in prompt
    assert "ROW-9001" not in prompt
    debug = env["debug"]["explain_repair"]
    assert debug["error_class"] == "BinderException"
    assert debug["repair_attempts"] == 1
    assert debug["sql_hash"] == hashlib.sha256(_BAD.encode()).hexdigest()
    assert _SENTINEL not in debug["error_message"]
    assert _BAD not in debug["error_message"]
    assert "sql_text" not in debug
    assert env["audit_receipt"]["explain_repair"] == debug
    assert _BAD not in json.dumps(env["debug"])
    recorded = audit_records()
    assert recorded
    assert "sql_text" not in recorded[-1]


def test_repeated_sql_asks_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, "1")
    db = _lake(tmp_path)

    def compute(_ctx: dict[str, Any], _nth: int) -> dict[str, Any]:
        return _names(query_sql=_BAD)

    seen, env = _ask(db, compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert len(seen) == 2
    offer = env["reconfirm"]
    assert str(offer.get("suggested_question") or "").strip()
    assert str(offer.get("missing_fact") or "").strip()
    assert "shipments.status" in offer["suggested_question"]
    debug = env["debug"]["explain_repair"]
    assert debug["error_class"] == "BinderException"
    assert debug["repair_attempts"] == 1
    assert "sql_text" not in debug
    assert _BAD not in json.dumps(debug)
    assert _SENTINEL not in json.dumps(env["debug"])


def test_steward_audit_keeps_sql_off_the_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, "1")
    monkeypatch.setenv("DMS_ACTOR_ROLE", "steward")
    db = _lake(tmp_path)

    def compute(ctx: dict[str, Any], _nth: int) -> dict[str, Any]:
        if "enum_values:" in json.dumps(ctx):
            return _names(query_sql=_GOOD)
        return _names(query_sql=_BAD)

    _seen, env = _ask(db, compute)
    assert env is not None
    assert "sql_text" not in env["debug"]["explain_repair"]
    assert _BAD not in json.dumps(env["debug"])
    recorded = audit_records()
    assert recorded[-1].get("sql_text") == _BAD


def test_no_word_rules() -> None:
    path = str(_ROOT / "scripts" / "verify")
    if path not in sys.path:
        sys.path.insert(0, path)
    inv = importlib.import_module("rule_inventory")
    sites = inv.scan_file(_ROOT / "packages" / "executor" / "dms_executor" / "explain_repair.py")
    assert sites == []
