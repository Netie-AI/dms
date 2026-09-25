"""A2-06 / dms#262: one failed claim must not unverify the whole ontology.

Failed subjects stay marked. Compile and generated SQL that do not use the
failed subject as their grain answer with oracle-equal rows. Defect-touching
rows keep a named ABSTAIN. Lakes with no declared ontology keep today's
demo/live behaviour.

Assertions are on the customer envelope (``assert_envelope_valid``) plus
``load_verified_ontology``'s return. CI fixtures, not live.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask

from tests.test_hostile_schema_a2 import (
    _A2_06_OK,
    CASES,
    ORACLE,
    _ask,
    _defect_words,
    _ontology,
    _seed,
    grade,
)

GRANTS = {"customers", "orders", "order_lines"}
_REGION_SQL = (
    "SELECT c.region, SUM(o.amount_myr) AS revenue FROM orders o "
    "JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.region"
)


def _seed_orphan(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE customers (customer_id INTEGER, region VARCHAR)")
        con.execute("INSERT INTO customers VALUES (1,'North'),(2,'South'),(3,'North')")
        con.execute(
            "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount_myr DOUBLE)"
        )
        con.execute("INSERT INTO orders VALUES (10,1,100),(11,2,200),(12,3,300),(13,2,400)")
        con.execute("INSERT INTO orders VALUES (14,99,1000)")
    finally:
        con.close()


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_a206", hash="hash_a206_not_entry")


def test_declared_failed_ontology_stays_loaded(tmp_path: Path) -> None:
    lake = tmp_path / "orphan.duckdb"
    case = CASES["orphan"]
    _seed(lake, case)
    loaded = load_verified_ontology(lake, _ontology(case))
    assert loaded is not None
    assert loaded.verified is False
    checks = [v.check for v in loaded.__dict__.get("_violations") or []]
    assert "fk_intact" in checks


def test_undeclared_demo_ontology_still_none_on_foreign_lake(tmp_path: Path) -> None:
    """BIRD / live: a lake the demo ontology was never declared for."""
    lake = tmp_path / "foreign.duckdb"
    con = duckdb.connect(str(lake))
    try:
        con.execute("CREATE TABLE t (id INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
    finally:
        con.close()
    assert load_verified_ontology(lake) is None


def test_nine_unaffected_oracles_answer_ok(tmp_path: Path) -> None:
    """The 9 MEASURED pins A2-06 flips: rows match the oracle, not just a badge."""
    for case_id, qid, mode in _A2_06_OK:
        env = _ask(tmp_path, case_id, qid, mode)
        oracle = ORACLE[case_id][qid]
        assert oracle is not None, f"{case_id}/{qid}/{mode} has no oracle"
        verdict = grade(env, oracle, _defect_words(case_id, qid))
        assert verdict == "OK", (
            f"{case_id}/{qid}/{mode}: {verdict} badge={env.get('badge')} "
            f"rows={env.get('rows')} text={(env.get('text') or '')[:160]!r}"
        )
        assert env["abstained"] is False
        assert env["text"]


def test_defect_touching_rows_still_name_the_subject(tmp_path: Path) -> None:
    stay = (
        ("orphan", "revenue_by_region", "plan"),
        ("orphan", "revenue_by_region", "sql"),
        ("fk_wrong_col", "units_by_region", "plan"),
        ("fk_wrong_col", "units_by_region", "sql"),
        ("dup_business_key", "customer_count", "plan"),
        ("dup_business_key", "customer_count", "sql"),
    )
    for case_id, qid, mode in stay:
        env = _ask(tmp_path, case_id, qid, mode)
        verdict = grade(env, ORACLE[case_id][qid], _defect_words(case_id, qid))
        assert verdict == "ABSTAIN", (
            f"{case_id}/{qid}/{mode}: {verdict} text={(env.get('text') or '')[:200]!r}"
        )
        assert env["rows"] == []


def test_no_declared_ontology_keeps_answering_sql(tmp_path: Path) -> None:
    lake = tmp_path / "bird_like.duckdb"
    _seed_orphan(lake)
    calls: list[str] = []

    def submit(sql: str) -> Any:
        calls.append(sql)
        con = duckdb.connect(str(lake))
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_a206", output={"rows": rows})

    env = maybe_generative_ask(
        "What is revenue by region?",
        warehouse=lake,
        grantable=set(GRANTS),
        compute=lambda _ctx: {"query_sql": _REGION_SQL},
        submit=submit,
        ledger_append=_ledger,
        ontology=None,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["text"]
    got = {(r["region"], float(r["revenue"])) for r in env["rows"]}
    assert got == {("North", 400.0), ("South", 600.0)}
    assert len(calls) == 1
