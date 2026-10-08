"""C-LOOP-B: model SQL runs read-only, retries on errors, credits only its SQL.

Lake table and column names belong in this file. Product code stays source-agnostic.
"""

from __future__ import annotations

import json
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
from dms_executor.sql_loop import (
    EXTRACT_DIALECT,
    apply_sql_credit,
    dialect_for_connector,
    extract_dialect,
    loop_entry,
)

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


def _loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Product defaults are off. These tests opt into the extract loop."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
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
    _loop(monkeypatch)
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
    _loop(monkeypatch)
    seen: list[Any] = []
    bad = "SELECT nope FROM inventory"

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
    assert "nope" in reason
    assert env["loop"][0]["outcome"] == reason
    assert env["loop"][1]["outcome"] == "served"


def test_loop_exhaustion_is_a_named_abstain_with_zero_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        return _names(query_sql="SELECT nope FROM inventory")

    env = _assert_abstain(_ask(tmp_path, "Which locations are cold storage?", compute))
    assert calls["n"] == 2
    assert env["rows"] == []
    assert "loop_exhausted:" in env["text"]
    assert env["sql_used"] is None
    assert env["served_attribution"] == "missing"
    assert all(str(item.get("outcome") or "").strip() for item in env["loop"])
    assert all(item.get("outcome") != "served" for item in env["loop"])


def test_empty_result_abstains_unverified_and_does_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        return _names(query_sql=_EMPTY_SQL)

    env = _assert_abstain(
        _ask(tmp_path, "Which chemicals are expiring this month?", compute)
    )
    assert calls["n"] == 1
    assert "empty_result_unverified:value_exists_pending" in env["text"]
    assert env["rows"] == []
    assert env["sql_used"] is None
    assert env["loop"][0]["sql"] == _EMPTY_SQL
    assert env["loop"][0]["outcome"] == "empty_result_unverified:value_exists_pending"
    assert env["loop"][0]["outcome"] != "served"
    assert env["served_attribution"] == "missing"


def test_submit_abstain_is_not_logged_as_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        return _names(query_sql=_COLD_SQL)

    def submit(_sql: str) -> Any:
        raise RuntimeError("submit down")

    begin_answer_model_calls()
    db = tmp_path / "loop.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = _assert_abstain(
        maybe_generative_ask(
            "Which locations are cold storage?",
            warehouse=db,
            grantable=set(_GRANTS),
            compute=compute,
            submit=submit,
            ledger_append=_ledger,
            ontology=onto,
        )
    )
    assert calls["n"] == 1
    assert env["loop"][0]["sql"] == _COLD_SQL
    assert env["loop"][0]["outcome"] == "submit_failed"
    assert env["loop"][0]["outcome"] != "served"


_DESC_STOCK = (
    "SELECT category, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS stock_value_myr "
    "FROM inventory GROUP BY category ORDER BY stock_value_myr DESC LIMIT 3"
)


def test_lowest_ask_still_serves_desc_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Known gap, INTENT-SPEC-01. Direction is not checked, so DESC is served."""
    _loop(monkeypatch)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_DESC_STOCK)

    env = _ask(tmp_path, "what's the bottom three categories by stock value", compute)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["rows"]
    assert isinstance(env["sql_used"], str) and "DESC" in env["sql_used"]
    assert env["served_attribution"] == "reported"
    assert env["loop"][0]["outcome"] == "served"


def test_retry_that_drops_a_filter_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    calls = {"n": 0}
    first = "SELECT nope FROM inventory WHERE a = 1 AND b = 2"
    second = "SELECT nope FROM inventory WHERE a = 1"

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
    assert all(item.get("outcome") != "served" for item in env["loop"])


def test_credit_is_missing_when_ranking_serves_after_a_model_call(
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
    assert env["served_attribution"] == "missing"
    assert "loop" not in env


def test_credit_names_model_and_key_when_model_sql_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)

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
    _loop(monkeypatch)
    calls = {"n": 0}
    question = "lowest 3 categories by stock value"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        return _names(
            ontology={"metrics": [{"id": "stock_value_by_category"}]},
        )

    env = _assert_abstain(_ask(tmp_path, question, compute))
    assert calls["n"] == 1
    assert "no_sql" in env["text"]
    assert env.get("plan_origin") != "ontology_ranking"
    assert env["sql_used"] is None
    assert env["rows"] == []
    assert env["served_attribution"] == "missing"
    assert env["loop"][0]["outcome"] == "no_sql"
    assert env["loop"][0]["outcome"] != "served"


def test_chemicals_list_shapes_are_not_served_by_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
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
        assert env.get("plan_origin") != "ontology_ranking"
        assert env["rows"] == []
        assert env["served_attribution"] == "missing"
        assert env["loop"]
        assert all(item.get("outcome") != "served" for item in env["loop"])
        if "not_granted" in sql:
            assert calls["n"] == 1
            assert env["loop"][0]["outcome"].startswith("checker:ungranted:")
        else:
            assert calls["n"] == 2
            assert "loop_exhausted:" in env["text"]


def test_cte_drop_inner_and_subquery_drop_inner() -> None:
    cte_prev = "WITH c AS (SELECT * FROM t WHERE secret = 1 AND keep = 1) SELECT * FROM c"
    cte_next = "WITH c AS (SELECT * FROM t WHERE keep = 1) SELECT * FROM c"
    cte_dropped = dropped_conjuncts(cte_prev, cte_next, EXTRACT_DIALECT)
    assert cte_dropped
    sub_prev = "SELECT * FROM (SELECT * FROM t WHERE secret = 1) s"
    sub_next = "SELECT * FROM (SELECT * FROM t) s"
    sub_dropped = dropped_conjuncts(sub_prev, sub_next, EXTRACT_DIALECT)
    assert sub_dropped


def test_duckdb_only_syntax_drop_is_checked_and_parse_failure_is_closed() -> None:
    previous = "SELECT #1 FROM t WHERE a = 1 AND b = 2"
    nxt = "SELECT #1 FROM t WHERE a = 1"
    assert extract_dialect(None) == "duckdb"
    dropped = dropped_conjuncts(previous, nxt, "duckdb")
    assert dropped
    assert len(dropped) == 1
    assert dropped_conjuncts(previous, nxt, "tsql") is None


def test_parse_failure_on_retry_does_not_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return _names(query_sql="SELECT nope FROM inventory WHERE a = 1")
        return _names(query_sql="SELECT !!!")

    env = _assert_abstain(_ask(tmp_path, "Which locations are cold storage?", compute))
    assert calls["n"] == 2
    assert "filter_parse_failed" in env["loop"][1]["outcome"]
    assert env["rows"] == []
    assert env["served_attribution"] != "reported"


def test_hostile_file_read_and_ungranted_do_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    cases = (
        "SELECT * FROM read_csv('notes.csv')",
        "SELECT 1; SELECT 2",
        "SELECT sku FROM not_granted",
    )
    for sql in cases:
        seen: list[Any] = []

        def compute(ctx: dict[str, Any], sql: str = sql) -> dict[str, Any]:
            seen.append(ctx.get("sql_loop_feedback"))
            return _names(query_sql=sql)

        env = _assert_abstain(_ask(tmp_path, "Show the sheet", compute))
        assert len(seen) == 1
        assert seen[0] is None
        assert env["loop"][0]["outcome"].startswith("checker:")
        assert env["loop"][0]["outcome"] != "served"
        assert "previous_sql" not in json.dumps(seen)


def test_missing_extract_does_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    calls = {"n": 0}

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        return _names(query_sql="SELECT 1 AS n")

    env = _assert_abstain(
        maybe_generative_ask(
            "How many rows?",
            warehouse=tmp_path / "missing.duckdb",
            grantable=set(),
            compute=compute,
            submit=_submit(tmp_path / "missing.duckdb"),
            ledger_append=_ledger,
            ontology=None,
        )
    )
    assert calls["n"] == 1
    assert "warehouse_missing" in env["text"] or any(
        "warehouse_missing" in str(item.get("outcome")) for item in env["loop"]
    )


def test_db_error_text_is_masked_before_prompt_and_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    seen: list[Any] = []
    secret_row = "passport A12345678 born 1990-01-02 jane.doe@example.com"

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.append(ctx.get("sql_loop_feedback"))
        if len(seen) == 1:
            return _names(query_sql=f"SELECT error('{secret_row}')")
        return _names(query_sql=_COLD_SQL)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    blob = json.dumps({"loop": env["loop"], "seen": seen, "text": env["text"]})
    assert "A12345678" not in blob
    assert "1990-01-02" not in blob
    assert "jane.doe@example.com" not in blob
    assert "DMSMASK_" in blob
    assert env["badge"] == "L2_VALIDATED"


def test_from_payload_sets_attribution_and_does_not_keep_stale_reported() -> None:
    ranking = "SELECT location_code FROM locations"
    env = {
        "sql_used": ranking,
        "served_attribution": "reported",
        "served_model": "stale-model",
        "served_provider": "stale-provider",
        "ov_key_id": "stale-key",
    }
    loop = [
        {
            "model_wrote": True,
            "sql": "SELECT sku FROM inventory",
            "model": "real-model",
            "provider": "groq",
            "key": "ovk-real",
            "dialect": "duckdb",
        }
    ]
    payload = {
        "query_sql": ranking,
        "served_model": "payload-model",
        "served_provider": "payload-provider",
        "ov_key_id": "ovk-payload",
    }
    apply_sql_credit(env, payload, loop, dialect="duckdb")
    assert env["served_attribution"] == "missing"
    assert env["served_attribution"] != "reported"


def test_none_clears_credit_fields() -> None:
    env = {
        "sql_used": None,
        "served_attribution": "reported",
        "served_model": "stale-model",
        "served_provider": "stale-provider",
        "ov_key_id": "stale-key",
    }
    apply_sql_credit(env, None, None, dialect="duckdb")
    assert env["served_attribution"] == "none"
    assert env.get("served_model") is None
    assert env.get("served_provider") is None
    assert env.get("ov_key_id") is None


def test_stored_attempt_strips_secret_keys_values_and_pii() -> None:
    long_key = "AbCdEfGhIjKlMnOpQrStUvWx123456"
    payload = {
        "query_sql": "SELECT 1",
        "served_model": _MODEL,
        "x-api-key": "header-secret",
        "api-key": "hyphen-secret",
        "apiKey": "camel-secret",
        "bearer": "bearer-secret",
        "token": "token-secret",
        "ov_token": "ov-secret",
        "cookie": "cookie-secret",
        "nested": {"Authorization": "nested-secret", "x-api-key": "nested-header"},
        "note": f"Bearer {long_key}",
        "assigned": "key=sk-live-12345678",
        "name": "Jane Doe",
        "dob": "1990-01-02",
        "passport": "passport A12345678",
        "contact": "jane.doe@example.com",
        "route_store_id": "learn-store-99",
        "prompt_tokens": 11,
        "completion_tokens": 4,
    }
    entry = loop_entry(
        prompt="question mentions passport A12345678",
        payload=payload,
        sql="SELECT 1",
        outcome="served",
        dialect="duckdb",
    )
    blob = json.dumps(entry)
    for secret in (
        "header-secret",
        "hyphen-secret",
        "camel-secret",
        "bearer-secret",
        "token-secret",
        "ov-secret",
        "cookie-secret",
        "nested-secret",
        "nested-header",
        long_key,
        "sk-live-12345678",
        "Jane Doe",
        "1990-01-02",
        "A12345678",
        "jane.doe@example.com",
    ):
        assert secret not in blob, secret
    assert entry["ov_route"] is None
    assert entry["ov_route_reason"] == "unattributed"
    assert entry["route_store_id"] == "learn-store-99"
    assert entry["tokens"]["prompt_tokens"] == 11
    assert entry["tokens"]["completion_tokens"] == 4
    assert "learn-store-99" not in str(entry["ov_route"])


def test_eight_false_credit_envelopes_record_loop_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loop(monkeypatch)
    questions = (
        "List chemicals in inventory",
        "What chemicals do we have in stock?",
        "top 5 SKUs by sales",
        "show the five best-selling SKUs",
        "top 3 categories by value",
        "show top 3 categoty sales",
        "which supplier audits are overdue",
        "what's the freight cost by destination",
    )
    sql = "SELECT sku FROM not_granted"
    for question in questions:
        calls = {"n": 0}

        def compute(_ctx: dict[str, Any], sql: str = sql) -> dict[str, Any]:
            calls["n"] += 1
            return _names(query_sql=sql)

        env = _ask(tmp_path, question, compute)
        assert env is not None, question
        assert env.get("loop"), question
        assert calls["n"] == 1, question
        assert env["loop"][0]["sql"] == sql
        assert str(env["loop"][0]["outcome"]).startswith("checker:")
        assert env["served_attribution"] != "reported"
