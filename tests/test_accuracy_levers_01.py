"""Accuracy levers on the AI floor. Each flag is off unless it is the digit 1.

Self-consistency samples three SQLs only after a sanity miss or an ambiguous
flag, and serves a majority. Value link profiles a database and injects
``<values>``. Learned memory stores a Yes only for the asker's own question
and intent, then re-executes that SQL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_executor.ai_floor import run_floor
from dms_executor.demo_warehouse import SERVING_DIALECT, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.learned_memory import (
    MemoryDisabled,
    MemoryRefused,
    apply_fix,
    confirm,
    examples_context,
    intent_for,
    note_served,
    saved_sql,
)
from dms_executor.learned_memory import (
    enabled as memory_on,
)
from dms_executor.learned_memory import (
    reset as reset_memory,
)
from dms_executor.self_consistency import (
    candidate_prompt,
)
from dms_executor.self_consistency import (
    enabled as vote_on,
)
from dms_executor.sql_vote import prompts_differ_in_substance
from dms_executor.value_link import (
    cache_misses,
    values_context,
)
from dms_executor.value_link import (
    enabled as link_on,
)
from dms_executor.value_link import (
    reset as reset_link,
)

GOOD = "SELECT COUNT(*) AS n FROM inventory"
OTHER = "SELECT COUNT(*) AS n FROM suppliers"
THIRD = "SELECT COUNT(*) AS n FROM shipments"
BAD = "SELECT nope FROM inventory"
LITERAL = "SELECT 3 AS n"
ASKER = "ada"
SPACE = "spc_levers"
QUESTION = "How many SKUs do we have in inventory?"


class _Rows:
    def __init__(self, rows: list[dict[str, Any]], run_id: str = "run_lever") -> None:
        self.ok = True
        self.run_id = run_id
        self.output = {"rows": rows}


class _Ledger:
    entry_id = "led_lever"
    hash = "hash_lever_not_the_entry"


def _body(sql: str, *, ambiguous: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query_sql": sql,
        "served_model": "ov-routed",
        "served_provider": "openvault",
    }
    if ambiguous:
        body["ambiguous"] = True
    return body


def _floor(
    tmp_path: Path,
    compute: Any,
    submit: Any | None = None,
    *,
    question: str = QUESTION,
    asker: str = ASKER,
) -> dict[str, Any]:
    warehouse = tmp_path / "lever.duckdb"
    ensure_demo_warehouse(warehouse)

    def _submit(sql: str) -> _Rows:
        if submit is not None:
            return submit(sql)
        return _Rows([{"n": 4}])

    return run_floor(
        question,
        compute=compute,
        submit=_submit,
        ledger_append=lambda _payload: _Ledger(),
        warehouse=warehouse,
        grantable={"inventory", "suppliers", "shipments"},
        dialect=SERVING_DIALECT,
        space_id=SPACE,
        session_id="ses_lever",
        asker=asker,
    )


def test_flags_accept_only_the_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, fn in (
        ("DMS_SELF_CONSISTENCY", vote_on),
        ("DMS_VALUE_LINK", link_on),
        ("DMS_LEARNED_MEMORY", memory_on),
    ):
        monkeypatch.delenv(name, raising=False)
        assert fn() is False
        for token in ("", "0", "true", "yes", "on", "2"):
            monkeypatch.setenv(name, token)
            assert fn() is False
        monkeypatch.setenv(name, "1")
        assert fn() is True


def test_prompts_differ_and_share_the_question() -> None:
    prompts = [candidate_prompt("same question", "schema", index) for index in range(3)]
    assert len(set(prompts)) == 3
    for left, right in ((0, 1), (0, 2), (1, 2)):
        assert prompts_differ_in_substance(prompts[left], prompts[right])


def test_sane_answer_stays_one_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_AI_FLOOR", "1")
    monkeypatch.setenv("DMS_SELF_CONSISTENCY", "1")
    seen: list[dict[str, Any]] = []

    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        seen.append(catalog)
        return _body(GOOD)

    env = _floor(tmp_path, compute)
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["sql_used"] == GOOD
    assert len(seen) == 1
    assert "sample_temperature" not in seen[0]
    assert "self_consistency" not in env


def test_sanity_miss_keeps_the_majority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SELF_CONSISTENCY", "1")
    seen: list[float | None] = []

    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        temp = catalog.get("sample_temperature")
        seen.append(temp if isinstance(temp, float) else None)
        if temp is None:
            return _body(BAD)
        if temp == 0.8:
            return _body(OTHER)
        return _body(GOOD)

    def submit(sql: str) -> _Rows:
        return _Rows([{"n": 4 if "inventory" in sql else 1}])

    env = _floor(tmp_path, compute, submit)
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["sql_used"] == GOOD
    assert env["rows"] == [{"n": 4}]
    assert env["self_consistency"]["agreed"] is True
    assert seen[0] is None
    assert seen[1:] == [0.0, 0.4, 0.8]


def test_disagreement_asks_instead_of_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SELF_CONSISTENCY", "1")
    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        temp = catalog.get("sample_temperature")
        if not isinstance(temp, float):
            return _body(BAD)
        return _body({0.0: GOOD, 0.4: OTHER, 0.8: THIRD}[temp])

    def submit(sql: str) -> _Rows:
        if "inventory" in sql:
            return _Rows([{"n": 4}])
        if "suppliers" in sql:
            return _Rows([{"n": 2}])
        return _Rows([{"n": 9}])

    env = _floor(tmp_path, compute, submit)
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["sql_used"] is None
    assert env["values"] == []
    assert env["route"] == "needs_clarification"
    assert len(env["clarify"]["readings"]) >= 2


def test_ambiguous_flag_votes_even_when_the_first_sql_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SELF_CONSISTENCY", "1")

    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        temp = catalog.get("sample_temperature")
        if not isinstance(temp, float):
            return _body(OTHER, ambiguous=True)
        if temp == 0.8:
            return _body(OTHER)
        return _body(GOOD)

    def submit(sql: str) -> _Rows:
        return _Rows([{"n": 1 if "suppliers" in sql else 4}])

    env = _floor(tmp_path, compute, submit)
    assert_envelope_valid(env)
    assert env["sql_used"] == GOOD
    assert env["rows"] == [{"n": 4}]


def test_literal_majority_does_not_serve_a_figure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_SELF_CONSISTENCY", "1")

    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        if catalog.get("sample_temperature") is None:
            return _body(BAD)
        return _body(LITERAL)

    env = _floor(tmp_path, compute, lambda _sql: _Rows([{"n": 3}]))
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["sql_used"] is None


def test_flag_off_still_repairs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_SELF_CONSISTENCY", raising=False)
    seen: list[str] = []

    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        seen.append(str(catalog.get("sql_vote_kind")))
        if len(seen) == 1:
            return _body(BAD)
        return _body(GOOD)

    env = _floor(tmp_path, compute)
    assert_envelope_valid(env)
    assert env["sql_used"] == GOOD
    assert env["ai_floor"]["stage"] == "repair"
    assert len(seen) == 2
    assert "self_consistency" not in env


def test_value_link_injects_real_values_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_link()
    db = tmp_path / "values.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE places (region VARCHAR, customer_name VARCHAR)")
    con.execute("INSERT INTO places VALUES ('Florida', 'Ada'), ('Georgia', 'Ben')")
    con.execute("CREATE TABLE amounts (amount INTEGER)")
    con.execute("INSERT INTO amounts VALUES (3), (9)")
    con.close()
    monkeypatch.delenv("DMS_VALUE_LINK", raising=False)
    empty = values_context(
        "Flordia amount",
        warehouse=db,
        grantable={"places", "amounts"},
        dialect="duckdb",
    )
    assert empty == ""
    monkeypatch.setenv("DMS_VALUE_LINK", "1")
    block = values_context(
        "Flordia amount",
        warehouse=db,
        grantable={"places", "amounts"},
        dialect="duckdb",
    )
    assert block.startswith("<values>")
    assert "Florida" in block
    assert "Ada" not in block
    assert "Ben" not in block
    assert "amounts.amount=" in block
    again = values_context(
        "Flordia amount",
        warehouse=db,
        grantable={"places", "amounts"},
        dialect="duckdb",
    )
    assert again == block
    assert cache_misses() == 1


def test_memory_binds_to_the_asker_question_and_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_memory()
    monkeypatch.setenv("DMS_LEARNED_MEMORY", "1")
    monkeypatch.setenv("DMS_SELF_CONSISTENCY", "0")
    calls: list[str] = []

    def compute(catalog: dict[str, Any]) -> dict[str, Any]:
        calls.append("compute")
        return _body(GOOD)

    def submit(sql: str) -> _Rows:
        calls.append(sql)
        return _Rows([{"n": 4}])

    env = _floor(tmp_path, compute, submit)
    assert_envelope_valid(env)
    block = env["learned_memory"]
    with pytest.raises(MemoryRefused, match="generic_reading"):
        confirm(
            answer_id=block["answer_id"],
            question="a generic reading of that",
            intent=block["intent"],
            confirmer=ASKER,
        )
    with pytest.raises(MemoryRefused, match="not_own_confirm"):
        confirm(
            answer_id=block["answer_id"],
            question=QUESTION,
            intent=block["intent"],
            confirmer="someone_else",
        )
    with pytest.raises(MemoryRefused, match="intent_mismatch"):
        confirm(
            answer_id=block["answer_id"],
            question=QUESTION,
            intent="reading:generic",
            confirmer=ASKER,
        )
    assert saved_sql(QUESTION, space_id=SPACE, asker=ASKER) == []
    saved = confirm(
        answer_id=block["answer_id"],
        question=QUESTION,
        intent=block["intent"],
        confirmer=ASKER,
    )
    assert saved["sql"] == GOOD
    assert "<examples>" in examples_context(QUESTION, space_id=SPACE, asker=ASKER)
    assert examples_context(QUESTION, space_id=SPACE, asker="someone_else") == ""

    def live(sql: str) -> _Rows:
        calls.append("live:" + sql)
        return _Rows([{"n": 9}])

    rerun = _floor(tmp_path, compute, live)
    assert_envelope_valid(rerun)
    assert rerun["sql_used"] == GOOD
    assert rerun["rows"] == [{"n": 9}]
    assert rerun["learned_memory_rerun"] is True
    assert calls.count("compute") == 1


def test_two_intents_clarify_and_fix_reruns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_memory()
    monkeypatch.setenv("DMS_LEARNED_MEMORY", "1")
    warehouse = tmp_path / "fix.duckdb"
    con = duckdb.connect(str(warehouse))
    con.execute("CREATE TABLE inventory (sku VARCHAR)")
    con.execute("INSERT INTO inventory VALUES ('a'), ('b')")
    con.close()
    first = {"answer_id": "ans_a", "audit_id": "ans_a"}
    second = {"answer_id": "ans_b", "audit_id": "ans_b"}
    note_served(first, question=QUESTION, sql=GOOD, asker=ASKER, space_id=SPACE, dialect="duckdb")
    note_served(
        second,
        question=QUESTION,
        sql=OTHER,
        asker=ASKER,
        space_id=SPACE,
        dialect="duckdb",
    )
    confirm(
        answer_id="ans_a",
        question=QUESTION,
        intent=first["learned_memory"]["intent"],
        confirmer=ASKER,
    )
    confirm(
        answer_id="ans_b",
        question=QUESTION,
        intent=second["learned_memory"]["intent"],
        confirmer=ASKER,
    )

    def boom(_catalog: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("two intents must not generate")

    env = run_floor(
        QUESTION,
        compute=boom,
        submit=lambda _sql: _Rows([{"n": 1}]),
        ledger_append=lambda _payload: _Ledger(),
        warehouse=ensure_demo_warehouse(tmp_path / "floor.duckdb"),
        grantable={"inventory", "suppliers", "shipments"},
        dialect=SERVING_DIALECT,
        space_id=SPACE,
        session_id="ses_lever",
        asker=ASKER,
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["sql_used"] is None

    fixed = "SELECT COUNT(*) AS n FROM inventory"
    out = apply_fix(
        answer_id="ans_a",
        question=QUESTION,
        intent=intent_for(GOOD, "duckdb"),
        confirmer=ASKER,
        sql=fixed,
        warehouse=warehouse,
        grantable={"inventory"},
        dialect="duckdb",
        interpretation="drop the table",
    )
    assert out["rows"] == [{"n": 2}]
    assert out["sql"] == fixed
    with pytest.raises(MemoryRefused):
        apply_fix(
            answer_id="ans_a",
            question=QUESTION,
            intent=intent_for(GOOD, "duckdb"),
            confirmer=ASKER,
            sql="DROP TABLE inventory",
            warehouse=warehouse,
            grantable={"inventory"},
            dialect="duckdb",
        )


def test_memory_off_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_memory()
    monkeypatch.delenv("DMS_LEARNED_MEMORY", raising=False)
    env: dict[str, Any] = {"answer_id": "ans_off", "audit_id": "ans_off"}
    note_served(env, question=QUESTION, sql=GOOD, asker=ASKER, space_id=SPACE, dialect="duckdb")
    assert "learned_memory" not in env
    with pytest.raises(MemoryDisabled):
        confirm(answer_id="ans_off", question=QUESTION, intent="answer:x", confirmer=ASKER)
    assert examples_context(QUESTION, space_id=SPACE, asker=ASKER) == ""


def _route_paths(app: Any) -> set[str]:
    found: set[str] = set()
    for route in app.routes:
        original = getattr(route, "original_router", None)
        routes = original.routes if original is not None else [route]
        for inner in routes:
            path = getattr(inner, "path", "")
            if isinstance(path, str) and path:
                found.add(path)
    return found


def test_router_is_absent_until_the_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_LEARNED_MEMORY", raising=False)
    from dms_api.app import create_app

    app = create_app()
    paths = _route_paths(app)
    assert "/v1/chat/ask" in paths
    assert "/v1/chat/learned/confirm" not in paths
    assert "/v1/chat/learned/fix" not in paths
    monkeypatch.setenv("DMS_LEARNED_MEMORY", "1")
    paths_on = _route_paths(create_app())
    assert "/v1/chat/learned/confirm" in paths_on
    assert "/v1/chat/learned/fix" in paths_on


def test_seam_strips_sample_temperature() -> None:
    from dms_executor import _insights_compute_seam

    seen: list[dict[str, Any]] = []

    class _Cortex:
        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
            seen.append({"question": question, **kwargs})
            return {"query_sql": GOOD}

    catalog = {
        "sql_vote_prompt": "Write one SELECT.\nQuestion:\nHow many",
        "sql_vote_kind": "direct",
        "sample_temperature": 0.4,
    }
    out = _insights_compute_seam(
        _Cortex(),
        "How many",
        session_id="s",
        space_id="p",
        ontology=catalog,
    )
    assert out is not None
    assert seen[0]["question"].startswith("Write one SELECT")
    assert "sample_temperature" not in seen[0]
    onto = seen[0].get("ontology") or {}
    assert "sample_temperature" not in onto
    assert "model" not in seen[0]
