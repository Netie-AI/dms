"""EPIC-A2 #256 / A2-02 (dms#258): a declared ontology that failed verify.

Generated SQL used to ship L2_VALIDATED over a lake whose declared ontology
failed ``Ontology.verify`` (an orphan FK on ``order_customer``). Now the SQL
path abstains when it joins through the failed link, and both paths name the
violation: check, link, orphan row count. A lake with no declared ontology
keeps answering (BIRD Space #173 recorded WRONG=0 there).

Every assertion is on the customer envelope: rendered text and rows.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology

GRANTS = {"customers", "orders"}
_REGION_SQL = (
    "SELECT c.region, SUM(o.amount_myr) AS revenue FROM orders o "
    "JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.region"
)
_COUNT_SQL = "SELECT COUNT(*) AS customer_count FROM customers"


def _seed(path: Path, *, orphan: bool) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE customers (customer_id INTEGER, region VARCHAR)")
        con.execute("INSERT INTO customers VALUES (1,'North'),(2,'South'),(3,'North')")
        con.execute(
            "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount_myr DOUBLE)"
        )
        con.execute("INSERT INTO orders VALUES (10,1,100),(11,2,200),(12,3,300),(13,2,400)")
        if orphan:
            con.execute("INSERT INTO orders VALUES (14,99,1000),(15,98,5)")
    finally:
        con.close()


def _ontology() -> Ontology:
    o = Ontology()
    o.add_object("customer", "customers", ["customer_id"])
    o.add_object("order", "orders", ["order_id"])
    o.add_link("order_customer", "order", ["customer_id"], "customer", ["customer_id"])
    o.add_measure("revenue", "order", "SUM(f.amount_myr)")
    o.add_measure("customer_count", "customer", "COUNT(*)")
    return o


def _submitter(path: Path, calls: list[str]) -> Any:
    def submit(sql: str) -> Any:
        calls.append(sql)
        con = duckdb.connect(str(path))
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_a202", output={"rows": rows})

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_a202", hash="hash_a202_not_entry")


def _ask(
    lake: Path, question: str, payload: dict[str, Any], calls: list[str], onto: Ontology | None
) -> dict[str, Any]:
    env = maybe_generative_ask(
        question,
        warehouse=lake,
        grantable=set(GRANTS),
        compute=lambda _ctx: payload,
        submit=_submitter(lake, calls),
        ledger_append=_ledger,
        ontology=onto,
    )
    assert env is not None
    assert_envelope_valid(env)
    return env


def _assert_names_orphan_link(env: dict[str, Any]) -> None:
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    text = env["text"]
    # kind + link name + orphan row count, in the sentence the customer reads
    assert "fk_intact" in text, text
    assert "order_customer" in text, text
    assert "2 rows" in text, text
    assert "does not exist" in text, text


def test_sql_over_failed_declared_link_abstains_named(tmp_path: Path) -> None:
    lake = tmp_path / "orphan.duckdb"
    _seed(lake, orphan=True)
    calls: list[str] = []
    env = _ask(lake, "What is revenue by region?", {"query_sql": _REGION_SQL}, calls, _ontology())
    _assert_names_orphan_link(env)
    assert calls == [], "a join over a failed link must not execute"


def test_plan_over_failed_declared_ontology_names_the_link(tmp_path: Path) -> None:
    lake = tmp_path / "orphan.duckdb"
    _seed(lake, orphan=True)
    calls: list[str] = []
    plan = {"query_plan": {"measure": "revenue", "group_by": [["customer", "region"]]}}
    env = _ask(lake, "What is revenue by region?", plan, calls, _ontology())
    _assert_names_orphan_link(env)
    assert "ontology_unverified" in env["text"]
    assert calls == []


def test_sql_that_never_touches_the_failed_link_still_answers(tmp_path: Path) -> None:
    lake = tmp_path / "orphan.duckdb"
    _seed(lake, orphan=True)
    calls: list[str] = []
    env = _ask(
        lake, "How many customers do we have?", {"query_sql": _COUNT_SQL}, calls, _ontology()
    )
    assert env["abstained"] is False
    assert env["text"]
    assert [float(r["customer_count"]) for r in env["rows"]] == [3.0]


def test_no_declared_ontology_keeps_answering_sql(tmp_path: Path) -> None:
    """BIRD shape: a lake no ontology was declared for. Same orphan data."""
    lake = tmp_path / "bird_like.duckdb"
    _seed(lake, orphan=True)
    calls: list[str] = []
    env = _ask(lake, "What is revenue by region?", {"query_sql": _REGION_SQL}, calls, None)
    assert env["abstained"] is False
    assert env["badge"] != "ABSTAIN"
    assert env["text"]
    got = {(r["region"], float(r["revenue"])) for r in env["rows"]}
    assert got == {("North", 400.0), ("South", 600.0)}
    assert len(calls) == 1


def test_clean_declared_ontology_answers_sql(tmp_path: Path) -> None:
    lake = tmp_path / "clean.duckdb"
    _seed(lake, orphan=False)
    calls: list[str] = []
    env = _ask(lake, "What is revenue by region?", {"query_sql": _REGION_SQL}, calls, _ontology())
    assert env["abstained"] is False
    assert env["text"]
    got = {(r["region"], float(r["revenue"])) for r in env["rows"]}
    assert got == {("North", 400.0), ("South", 600.0)}
