"""NL19-SCHEMA-ONTO-GAP. Flag default off.

A question whose measure is not on the ontology reaches the AI SQL leg when
``DMS_MEASURE_FROM_SCHEMA`` is 1. Off, the prompt, the envelope, and the
served route stay the same. No question-word measure table.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.measure_from_schema import ENV, enabled
from dms_executor.ontology import Ontology

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "dispatch volume total"
JOIN_Q = "order count"
MONTH_Q = "What is revenue per month?"
EXAMPLE_SQL = "SELECT 9 AS parked FROM orders"
MODEL_SQL = "SELECT COUNT(*) AS n FROM orders"
JOIN_SQL = (
    "SELECT customers.name, COUNT(*) AS n FROM orders "
    "JOIN customers ON orders.customer_id = customers.customer_id "
    "GROUP BY customers.name"
)
REPAIR_SQL = "SELECT date_trunc('month', ordered_at) AS month, COUNT(*) AS n FROM orders GROUP BY 1"


def _ontology() -> Ontology:
    onto = Ontology()
    onto.add_object("order", "orders", ["order_id"])
    onto.add_object("customer", "customers", ["customer_id"])
    onto.add_object("product", "products", ["product_id"])
    onto.add_measure("order_count", "order", "COUNT(*)")
    onto.verified = True
    return onto


def _seed(path: Path) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE orders ("
            "order_id VARCHAR, customer_id VARCHAR, product_id VARCHAR, "
            "ordered_at DATE, qty INTEGER)"
        )
        con.execute("CREATE TABLE customers (customer_id VARCHAR, name VARCHAR)")
        con.execute("CREATE TABLE products (product_id VARCHAR, name VARCHAR)")
        con.execute(
            "INSERT INTO orders VALUES "
            "('o1','c1','p1','2024-01-15',2),('o2','c1','p2','2024-02-15',1)"
        )
        con.execute("INSERT INTO customers VALUES ('c1','C-1')")
        con.execute("INSERT INTO products VALUES ('p1','item-a'),('p2','item-b')")
    finally:
        con.close()


def _plan(measure: str, group_by: list[list[str]] | None = None) -> dict[str, Any]:
    return {
        "query_plan": {
            "measure": measure,
            "group_by": group_by or [],
            "filters": [],
        }
    }


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_schema_gap", hash="hash_schema_gap_not_entry")


def _submit_factory(seen: list[str]):
    def submit(sql: str) -> Any:
        seen.append(sql)
        return SimpleNamespace(
            ok=True,
            status="ok",
            run_id="run_schema_gap",
            output={"rows": [{"n": 2}]},
        )

    return submit


def _examples(*_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
    return [{"question": "parked sample", "sql": EXAMPLE_SQL}]


def _ask(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    flag: str | None,
    question: str = QUESTION,
    plan: dict[str, Any] | None = None,
    model_sql: str = MODEL_SQL,
    repair_sql: str = REPAIR_SQL,
    calls: list[dict[str, Any]] | None = None,
    submitted: list[str] | None = None,
) -> dict[str, Any] | None:
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)
    monkeypatch.delenv("DMS_VQ_RAG", raising=False)
    if flag is not None:
        monkeypatch.setenv(ENV, flag)
    root = Path(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    warehouse = root / "gap.duckdb"
    _seed(warehouse)
    box: list[dict[str, Any]] = calls if calls is not None else []
    sent: list[str] = submitted if submitted is not None else []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        box.append(json.loads(json.dumps(ctx, sort_keys=True, default=str)))
        kind = ctx.get("sql_vote_kind")
        if kind == "qualifier_check":
            return {"query_sql": repair_sql}
        if kind == "schema_sql":
            return {"query_sql": model_sql}
        return plan or _plan("parcel_count")

    with patch(
        "dms_executor.verified_queries.retrieve_verified_examples",
        _examples,
    ):
        return maybe_generative_ask(
            question,
            warehouse=warehouse,
            grantable={"orders", "customers", "products"},
            compute=compute,
            submit=_submit_factory(sent),
            ledger_append=_ledger,
            ontology=_ontology(),
            bind_on_miss=False,
            dialect="duckdb",
        )


def _stable(env: dict[str, Any] | None) -> str:
    body = dict(env or {})
    body.pop("as_of", None)
    return json.dumps(body, sort_keys=True, default=str)


def test_flag_defaults_off_and_only_the_digit_turns_it_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV, raising=False)
    assert enabled() is False
    monkeypatch.setenv(ENV, "true")
    assert enabled() is False
    monkeypatch.setenv(ENV, "1")
    assert enabled() is True


def test_flag_off_is_byte_identical(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    off_calls: list[dict[str, Any]] = []
    zero_calls: list[dict[str, Any]] = []
    off = _ask(monkeypatch, tmp_path / "off", flag=None, calls=off_calls)
    zero = _ask(monkeypatch, tmp_path / "zero", flag="0", calls=zero_calls)
    assert off is not None and zero is not None
    assert json.dumps(off_calls, sort_keys=True) == json.dumps(zero_calls, sort_keys=True)
    assert _stable(off) == _stable(zero)
    assert off.get("served_route") == zero.get("served_route")
    assert off["abstained"] is True
    blob = " ".join(str(a) for a in (off.get("assumptions") or []))
    assert "unknown_measure" in blob
    assert all(call.get("sql_vote_kind") != "schema_sql" for call in off_calls)


def test_unknown_measure_reaches_the_ai_sql_leg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []
    submitted: list[str] = []
    env = _ask(
        monkeypatch,
        tmp_path,
        flag="1",
        calls=calls,
        submitted=submitted,
    )
    assert env is not None
    assert env["abstained"] is False
    kinds = [call.get("sql_vote_kind") for call in calls]
    assert "schema_sql" in kinds
    writer = next(call for call in calls if call.get("sql_vote_kind") == "schema_sql")
    schema = str(writer.get("schema_context") or "")
    assert "orders" in schema and "customers" in schema and "products" in schema
    assert EXAMPLE_SQL in str(writer.get("vq_examples") or "")
    assert submitted == [MODEL_SQL]
    assert EXAMPLE_SQL not in submitted
    assert env.get("served_route") not in {"ontology_compile", "l1", "rule"}


def test_missing_join_proposes_the_confirm_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    submitted: list[str] = []
    env = _ask(
        monkeypatch,
        tmp_path,
        flag="1",
        question=JOIN_Q,
        plan=_plan("order_count", [["customer", "name"]]),
        model_sql=JOIN_SQL,
        submitted=submitted,
    )
    assert env is not None
    assert env["abstained"] is True
    assert env.get("sql_used") is None
    assert submitted == []
    card = env["reconfirm"]
    assert card["reason_code"] == "join_unconfirmed"
    assert card["suggested_question"] == JOIN_Q
    assert "JOIN" in str(card.get("definition") or env.get("text") or "")


def test_unhonored_qualifier_is_repaired_by_the_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []
    submitted: list[str] = []
    env = _ask(
        monkeypatch,
        tmp_path,
        flag="1",
        question=MONTH_Q,
        plan=_plan("order_count"),
        model_sql=MODEL_SQL,
        calls=calls,
        submitted=submitted,
    )
    assert env is not None
    kinds = [call.get("sql_vote_kind") for call in calls]
    assert "schema_sql" in kinds
    assert "qualifier_check" in kinds
    repair = next(call for call in calls if call.get("sql_vote_kind") == "qualifier_check")
    assert repair.get("route_hint") == "reason"
    assert "model" not in repair and "provider" not in repair
    assert submitted == [REPAIR_SQL]
    assert env["abstained"] is False


def test_ungranted_sql_stays_an_abstain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    submitted: list[str] = []
    env = _ask(
        monkeypatch,
        tmp_path,
        flag="1",
        model_sql="SELECT COUNT(*) AS n FROM payroll",
        submitted=submitted,
    )
    assert env is not None
    assert env["abstained"] is True
    assert submitted == []
    blob = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "ungranted" in blob or "sql_relation_not_granted" in blob


def test_new_module_adds_no_question_word_rule() -> None:
    path = str(ROOT / "scripts" / "verify")
    if path not in sys.path:
        sys.path.insert(0, path)
    inventory = importlib.import_module("rule_inventory")
    module = ROOT / "packages" / "executor" / "dms_executor" / "measure_from_schema.py"
    sites = inventory.scan_file(module, rel="packages/executor/dms_executor/measure_from_schema.py")
    assert sites == []
