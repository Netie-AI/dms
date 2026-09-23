"""dms#260 A2-04: a declared business key is a claim verify() executes.

A unique surrogate (customer_id) over a duplicated natural key (customer_code
C1 on ids 1 and 4) counts one customer as two. The key is declared, never
profiled: only an object that says ``business_key=[...]`` is checked.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology, from_manifest

_CUSTOMERS = (
    "CREATE TABLE customers (customer_id INTEGER, customer_code VARCHAR, region VARCHAR)",
    "INSERT INTO customers VALUES (1,'C1','North'),(2,'C2','South'),(3,'C3','North')",
)
_DUP = "INSERT INTO customers VALUES (4,'C1','North')"
_NULL = "INSERT INTO customers VALUES (5,NULL,'South')"


def _con(*extra: str) -> Any:
    con = duckdb.connect()
    for stmt in (*_CUSTOMERS, *extra):
        con.execute(stmt)
    return con


def _onto(business_key: list[str] | None = None) -> Ontology:
    o = Ontology()
    o.add_object("customer", "customers", ["customer_id"], business_key=business_key)
    o.add_measure("customer_count", "customer", "COUNT(*)")
    return o


def test_duplicate_business_key_is_a_named_violation() -> None:
    onto = _onto(["customer_code"])
    violations = onto.verify(_con(_DUP))
    assert [v.check for v in violations] == ["business_key_unique"]
    detail = violations[0].detail
    assert "customer_code" in detail and "2 rows" in detail and "duplicate" in detail
    assert onto.verified is False


def test_null_business_key_is_a_violation() -> None:
    onto = _onto(["customer_code"])
    violations = onto.verify(_con(_NULL))
    assert [v.check for v in violations] == ["business_key_unique"]
    assert "NULL" in violations[0].detail and "customer_code" in violations[0].detail


def test_clean_business_key_verifies() -> None:
    onto = _onto(["customer_code"])
    assert onto.verify(_con()) == []
    assert onto.verified is True


def test_undeclared_business_key_is_not_profiled() -> None:
    """No declaration, no check: the surrogate is unique, so verify passes."""
    onto = _onto()
    assert onto.verify(_con(_DUP)) == []


def test_from_manifest_carries_declared_business_keys() -> None:
    entry = {
        "tables": [{"schema": "main", "table": "customers", "path": "customers"}],
        "primary_keys": {"main.customers": ["customer_id"]},
        "business_keys": {"main.customers": ["customer_code"]},
    }
    onto = from_manifest(entry, relation_for=lambda _s, t: t)
    assert onto.objects["main.customers"].business_key == ("customer_code",)
    assert [v.check for v in onto.verify(_con(_DUP))] == ["business_key_unique"]
    bare = from_manifest({**entry, "business_keys": {}}, relation_for=lambda _s, t: t)
    assert bare.objects["main.customers"].business_key == ()


def _ask(tmp_path: Path, *extra: str) -> dict[str, Any]:
    lake = tmp_path / "bk.duckdb"
    con = duckdb.connect(str(lake))
    for stmt in (*_CUSTOMERS, *extra):
        con.execute(stmt)
    con.close()

    def submit(sql: str) -> Any:
        c = duckdb.connect(str(lake))
        try:
            cur = c.execute(sql)
            cols = [str(d[0]) for d in cur.description or []]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            c.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_bk", output={"rows": rows})

    env = maybe_generative_ask(
        "How many customers do we have?",
        warehouse=lake,
        grantable={"customers"},
        compute=lambda _ctx: {"query_plan": {"measure": "customer_count", "group_by": []}},
        submit=submit,
        ledger_append=lambda _p: SimpleNamespace(entry_id="led_bk", hash="h"),
        ontology=_onto(["customer_code"]),
    )
    assert env is not None
    assert_envelope_valid(env)
    return env


def test_count_over_duplicated_business_key_abstains_naming_it(tmp_path: Path) -> None:
    env = _ask(tmp_path, _DUP)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    text = env["text"]
    assert "business_key_unique" in text and "customer_code" in text
    assert "2 rows share 1 duplicate" in text


def test_clean_business_key_still_answers_three(tmp_path: Path) -> None:
    env = _ask(tmp_path)
    assert env["abstained"] is False
    assert "3" in env["text"]
    assert [float(v) for r in env["rows"] for v in r.values()] == [3.0]
