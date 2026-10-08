"""C-LOOP-B: model SQL runs read-only, retries on errors, credits only its SQL.

Lake table and column names belong in this file. Product code stays source-agnostic.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client.compute import begin_answer_model_calls
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.sql_currency import dropped_conjuncts
from dms_executor.sql_loop import dialect_for_connector

_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_MODEL = "openai/gpt-oss-120b"
_PROVIDER = "groq"
_KEY = "ovk-loop-b"
_COLD_SQL = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_EMPTY_SQL = (
    "SELECT sku FROM inventory WHERE category = 'CHEMICALS' "
    "AND expiry_date >= DATE '2026-10-01' "
    "AND expiry_date < DATE '2026-11-01'"
)


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")


def _names(**extra: Any) -> dict[str, Any]:
    base = {
        "served_model": _MODEL,
        "served_provider": _PROVIDER,
        "ov_key_id": _KEY,
        "phase": "generate",
    }
    base.update(extra)
    return base


def _submit(db: Path):
    def submit(sql: str) -> Any:
        from dms_executor.demo_warehouse import connect_file

        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_loop", output={"rows": rows})

    return submit


def _ledger(_p: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_loop", hash="hash_loop_not_entry")


def _ask(
    tmp_path: Path,
    question: str,
    compute,
    *,
    grantable: set[str] | None = None,
) -> dict[str, Any] | None:
    begin_answer_model_calls()
    db = tmp_path / "loop.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=set(_GRANTS if grantable is None else grantable),
        compute=compute,
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
    )


def _assert_abstain(env: dict[str, Any] | None) -> dict[str, Any]:
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    return env


def test_dialect_for_connector_is_not_pinned_to_one_warehouse() -> None:
    assert dialect_for_connector("postgresql") == "postgres"
    assert dialect_for_connector("mysql") == "mysql"
    assert dialect_for_connector("sqlserver") == "tsql"
    assert dialect_for_connector("file") == "duckdb"


def test_dropped_conjunct_uses_the_connector_dialect() -> None:
    previous = "SELECT a FROM t WHERE a = 1 AND b = 2"
    nxt = "SELECT a FROM t WHERE a = 1"
    for dialect in ("postgres", "mysql", "tsql", "duckdb"):
        dropped = dropped_conjuncts(previous, nxt, dialect)
        assert dropped
        assert len(dropped) == 1
    # OR stays one conjunct.
    whole = dropped_conjuncts(
        "SELECT a FROM t WHERE a = 1 OR b = 2",
        "SELECT a FROM t WHERE a = 1",
        "postgres",
    )
    assert whole is not None
    assert len(whole) == 1


def test_db_error_retry_gets_the_error_text_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _off(monkeypatch)
    seen: list[Any] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(ctx.get("sql_loop_feedback"))
        if len(seen) == 1:
            return _names(query_sql="SELECT error('boom')")
        return _names(query_sql=_COLD_SQL)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"]
    assert len(seen) == 2
    reason = str((seen[1] or {}).get("reason") or "")
    assert reason.startswith("db_error:")
    assert "boom" in reason
    assert env["loop"][0]["outcome"].startswith("db_error:")
    assert env["loop"][1]["outcome"] == "served"
    assert env["served_attribution"] == "reported"
    assert env["served_model"] == _MODEL
    assert env["ov_key_id"] == _KEY


def test_checker_flag_retry_gets_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _off(monkeypatch)
    seen: list[Any] = []
    bad = "SELECT nope FROM not_granted"

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(ctx.get("sql_loop_feedback"))
        if len(seen) == 1:
            return _names(query_sql=bad)
        return _names(query_sql=_COLD_SQL)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert len(seen) == 2
    reason = str((seen[1] or {}).get("reason") or "")
    assert reason.startswith("checker:")
    assert "not_granted" in reason
    assert env["loop"][0]["outcome"] == reason
    assert env["loop"][1]["outcome"] == "served"


def test_loop_exhaustion_is_a_named_abstain_with_zero_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _off(monkeypatch)
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        return _names(query_sql="SELECT nope FROM not_granted")

    env = _assert_abstain(_ask(tmp_path, "Which locations are cold storage?", compute))
    assert calls["n"] == 2
    assert env["rows"] == []
    assert "loop_exhausted:" in env["text"]
    assert env["sql_used"] is None
    assert env["served_attribution"] == "none"
    assert all(str(item.get("outcome") or "").strip() for item in env["loop"])


def test_confirmed_empty_serves_the_note_and_does_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _off(monkeypatch)
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        return _names(query_sql=_EMPTY_SQL, empty_confirmed=True)

    env = _assert_abstain(
        _ask(tmp_path, "Which chemicals are expiring this month?", compute)
    )
    assert calls["n"] == 1
    assert "no rows match" in env["text"]
    assert "no rows match" in env["assumptions"]
    assert env["rows"] == []
    assert env["loop"][0]["outcome"] == "served"
    assert env["served_attribution"] == "reported"
    assert env["served_model"] == _MODEL
    assert env["ov_key_id"] == _KEY


def test_unconfirmed_empty_does_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _off(monkeypatch)
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        return _names(query_sql=_EMPTY_SQL)

    env = _ask(tmp_path, "Which chemicals are expiring this month?", compute)
    assert calls["n"] == 1
    assert env is not None
    assert env["loop"][0]["outcome"] == "empty"


def test_retry_that_drops_a_filter_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _off(monkeypatch)
    calls = {"n": 0}
    first = "SELECT a FROM t WHERE a = 1 AND b = 2"
    second = "SELECT a FROM t WHERE a = 1"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        sql = first if calls["n"] == 1 else second
        return _names(query_sql=sql)

    env = _assert_abstain(_ask(tmp_path, "Count rows where a and b match", compute))
    assert calls["n"] == 2
    assert env["loop"][1]["outcome"] == "filter_dropped"
    assert "loop_exhausted:filter_dropped" in env["text"]
    assert env["rows"] == []
    assert all(str(item.get("outcome") or "").strip() for item in env["loop"])


def test_credit_is_none_when_a_non_model_path_serves(
    tmp_path: Path,
) -> None:
    rejected = "SELECT secret_col FROM not_granted"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(
            query_sql=rejected,
            ontology={"metrics": [{"id": "cq_cold_storage"}]},
        )

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["plan_origin"] == "ontology_ranking"
    assert env["badge"] == "L2_VALIDATED"
    assert env["sql_used"] != rejected
    assert env["served_attribution"] == "none"
    assert env["loop"][0]["outcome"].startswith("checker:")
    assert env["loop"][0]["sql"] == rejected
    assert env["loop"][0]["outcome"]


def test_credit_names_model_and_key_when_model_sql_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _off(monkeypatch)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_COLD_SQL)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["served_attribution"] == "reported"
    assert env["served_model"] == _MODEL
    assert env["served_provider"] == _PROVIDER
    assert env["ov_key_id"] == _KEY
    assert env["loop"][0]["outcome"] == "served"


def test_lowest_categories_do_not_fall_back_to_ontology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _off(monkeypatch)
    calls = {"n": 0}
    question = "lowest 3 categories by stock value"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        return _names(
            ontology={"metrics": [{"id": "stock_value_by_category"}]},
        )

    env = _assert_abstain(_ask(tmp_path, question, compute))
    assert calls["n"] == 2
    assert "loop_exhausted:no_sql" in env["text"]
    assert env.get("plan_origin") != "ontology_ranking"
    assert env["sql_used"] is None
    assert env["rows"] == []
    assert env["served_attribution"] == "none"
    assert all(item.get("outcome") == "no_sql" for item in env["loop"])


def test_chemicals_list_shapes_are_not_served_by_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _off(monkeypatch)
    question = "List chemicals in inventory"
    shapes = (
        "SELECT sku FROM not_granted WHERE category = 'CHEMICALS'",
        "SELECT not_a_column FROM inventory",
    )
    for sql in shapes:
        calls = {"n": 0}

        def compute(_ctx: dict[str, Any], sql: str = sql) -> dict[str, Any]:
            calls["n"] += 1
            return _names(
                query_sql=sql,
                ontology={"metrics": [{"id": "stock_value_by_category"}]},
            )

        env = _assert_abstain(_ask(tmp_path, question, compute))
        assert calls["n"] == 2
        assert env.get("plan_origin") != "ontology_ranking"
        assert env["rows"] == []
        assert env["served_attribution"] == "none"
        assert "loop_exhausted:" in env["text"]
        assert all(str(item.get("outcome") or "").strip() for item in env["loop"])
