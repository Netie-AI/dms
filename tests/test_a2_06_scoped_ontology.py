"""A2-06 / dms#262: one failed claim must not unverify the whole ontology.

Failed subjects stay marked. A hop through a failed ``fk_intact`` link is
a use of that subject. Compile and generated SQL that do not touch a
failed subject answer with oracle-equal rows. Defect-touching rows keep a
named ABSTAIN. Lakes with no declared ontology keep today's demo/live
behaviour. No hop-blessing of failed-link cardinality.

Assertions are on the customer envelope (``assert_envelope_valid``) plus
``load_verified_ontology``'s return. CI fixtures, not live.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import Ontology

from tests.test_hostile_schema_a2 import (
    _A2_06_OK,
    CASES,
    ORACLE,
    QUESTIONS,
    _ask,
    _defect_words,
    _ontology,
    _row_tuple,
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
    assert loaded.links["order_customer"].cardinality == "unverified"


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


def _rows_multiset(rows: Any) -> Counter[tuple[Any, ...]]:
    """Order-insensitive value tuples; duplicate rows count. Badge is not used."""
    if not isinstance(rows, list):
        return Counter()
    return Counter(_row_tuple(r) for r in rows if isinstance(r, dict))


def test_multiset_rejects_duplicate_rows_a_set_would_accept() -> None:
    """R-0007: a badge/set match is not enough; a doubled row fails the pin."""
    want = {("North", 12.0), ("South", 3.0)}
    north = {"region": "North", "units": 12.0}
    doubled = [north, north, {"region": "South", "units": 3.0}]
    assert {_row_tuple(r) for r in doubled} == want
    assert _rows_multiset(doubled) != Counter(want)


@pytest.mark.parametrize("case_id,qid,mode", list(_A2_06_OK))
def test_flipped_pin_rows_equal_oracle_multiset(
    tmp_path: Path, case_id: str, qid: str, mode: str
) -> None:
    """Each ABSTAIN->OK pin: envelope rows == oracle as a multiset.

    Column names differ by path (region vs customer_region); values do not.
    ``_row_tuple`` drops names. ``Counter`` counts duplicate tuples. A
    confident badge, OK status, or matching row count alone cannot pass.
    """
    oracle = ORACLE[case_id][qid]
    assert oracle is not None, (
        f"{case_id}/{qid}/{mode} has no oracle; pin must stay ABSTAIN"
    )
    env = _ask(tmp_path, case_id, qid, mode)
    assert env["abstained"] is False, f"{case_id}/{qid}/{mode} abstained: {env.get('text')!r}"
    assert env["text"], f"{case_id}/{qid}/{mode} has no rendered text"
    got = _rows_multiset(env.get("rows"))
    want = Counter(oracle)
    assert got == want, (
        f"{case_id}/{qid}/{mode} {QUESTIONS[qid][0]!r}: "
        f"row multiset {dict(got)} != oracle {dict(want)}. "
        f"badge={env.get('badge')} n={len(env.get('rows') or [])} "
        f"rows={env.get('rows')}"
    )


def test_defect_touching_rows_still_name_the_subject(tmp_path: Path) -> None:
    stay = (
        ("orphan", "revenue_by_region", "plan"),
        ("orphan", "revenue_by_region", "sql"),
        ("orphan", "units_by_region", "plan"),
        ("orphan", "units_by_region", "sql"),
        ("fk_wrong_col", "units_by_region", "plan"),
        ("fk_wrong_col", "units_by_region", "sql"),
        ("dup_business_key", "customer_count", "plan"),
        ("dup_business_key", "customer_count", "sql"),
        ("dup_business_key", "revenue_by_region", "plan"),
        ("dup_business_key", "revenue_by_region", "sql"),
        ("dup_business_key", "units_by_region", "plan"),
        ("dup_business_key", "units_by_region", "sql"),
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


def _hostile_onto() -> Ontology:
    o = Ontology()
    o.add_object("customer", "customers", ["customer_id"], business_key=["customer_code"])
    o.add_object("order", "orders", ["order_id"])
    o.add_object("line", "order_lines", ["line_id"])
    o.add_link("order_customer", "order", ["customer_id"], "customer", ["customer_id"])
    o.add_link("line_order", "line", ["order_id"], "order", ["order_id"])
    o.add_measure("revenue", "order", "SUM(f.amount_myr)")
    o.add_measure("units", "line", "SUM(f.qty)")
    o.add_measure("customer_count", "customer", "COUNT(*)")
    return o


def _seed_stmts(path: Path, stmts: tuple[str, ...]) -> None:
    con = duckdb.connect(str(path))
    try:
        for stmt in stmts:
            con.execute(stmt)
    finally:
        con.close()


def _submitter(path: Path) -> Any:
    def submit(sql: str) -> Any:
        con = duckdb.connect(str(path))
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001 -- execute failure is an abstain
            return SimpleNamespace(ok=False, status="err", run_id="run_a206", output=None)
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_a206", output={"rows": rows})

    return submit


def _ask_fixture(
    lake: Path, question: str, payload: dict[str, Any], onto: Ontology
) -> dict[str, Any]:
    env = maybe_generative_ask(
        question,
        warehouse=lake,
        grantable=set(GRANTS),
        compute=lambda _ctx: payload,
        submit=_submitter(lake),
        ledger_append=_ledger,
        ontology=onto,
    )
    assert env is not None
    assert_envelope_valid(env)
    return env


_CLEAN = (
    "CREATE TABLE customers (customer_id INTEGER, customer_code VARCHAR, region VARCHAR)",
    "INSERT INTO customers VALUES (1,'C1','North'),(2,'C2','South'),(3,'C3','North')",
    "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount_myr DOUBLE)",
    "INSERT INTO orders VALUES (10,1,100),(11,2,200),(12,3,300),(13,2,400)",
    "CREATE TABLE order_lines (line_id INTEGER, order_id INTEGER, qty DOUBLE)",
    "INSERT INTO order_lines VALUES (10,11,1),(11,11,2),(12,10,5),(13,12,7)",
)

#: Orphan order 14 has lines. LEFT JOIN misattributes; INNER JOIN drops qty.
_ORPHAN_WITH_LINES = (
    *_CLEAN,
    "INSERT INTO orders VALUES (14,99,1000)",
    "INSERT INTO order_lines VALUES (14,14,4)",
)

#: Two customer rows share customer_id=1. A join to that dimension fans out.
_DUP_DIM_KEY = (
    "CREATE TABLE customers (customer_id INTEGER, customer_code VARCHAR, region VARCHAR)",
    "INSERT INTO customers VALUES (1,'C1','North'),(1,'C1x','East'),"
    "(2,'C2','South'),(3,'C3','North')",
    "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount_myr DOUBLE)",
    "INSERT INTO orders VALUES (10,1,100),(11,2,200),(12,3,300),(13,2,400)",
    "CREATE TABLE order_lines (line_id INTEGER, order_id INTEGER, qty DOUBLE)",
    "INSERT INTO order_lines VALUES (10,11,1),(11,11,2),(12,10,5),(13,12,7)",
)

_TOUCHING = (
    (
        "What is revenue by region?",
        {"query_plan": {"measure": "revenue", "group_by": [["customer", "region"]]}},
    ),
    (
        "What is revenue by region?",
        {
            "query_sql": (
                "SELECT c.region, SUM(o.amount_myr) AS revenue FROM orders o "
                "JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.region"
            )
        },
    ),
    (
        "What is units by region?",
        {"query_plan": {"measure": "units", "group_by": [["customer", "region"]]}},
    ),
    (
        "What is units by region?",
        {
            "query_sql": (
                "SELECT c.region, SUM(l.qty) AS units FROM order_lines l "
                "JOIN orders o ON l.order_id = o.order_id "
                "JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.region"
            )
        },
    ),
)


def _assert_named_unverified(env: dict[str, Any], *words: str) -> None:
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    text = str(env.get("text") or "")
    assert "ontology_unverified" in text, text
    said = text.lower()
    for word in words:
        assert word.lower() in said, text


@pytest.mark.parametrize("question,payload", list(_TOUCHING))
def test_orphan_with_lines_touches_failed_link(
    tmp_path: Path, question: str, payload: dict[str, Any]
) -> None:
    """Orphan order that has lines: hop through order_customer must abstain."""
    lake = tmp_path / "orphan_lines.duckdb"
    _seed_stmts(lake, _ORPHAN_WITH_LINES)
    env = _ask_fixture(lake, question, payload, _hostile_onto())
    _assert_named_unverified(env, "fk_intact", "order_customer")


@pytest.mark.parametrize("question,payload", list(_TOUCHING))
def test_dup_dimension_key_touches_failed_object(
    tmp_path: Path, question: str, payload: dict[str, Any]
) -> None:
    """Join to a dimension whose key failed unique: named ABSTAIN, no rows."""
    lake = tmp_path / "dup_dim.duckdb"
    _seed_stmts(lake, _DUP_DIM_KEY)
    env = _ask_fixture(lake, question, payload, _hostile_onto())
    _assert_named_unverified(env, "key_unique", "customer")


def test_orphan_with_lines_count_still_answers(tmp_path: Path) -> None:
    """Count of customers does not use order_customer."""
    lake = tmp_path / "orphan_lines_count.duckdb"
    _seed_stmts(lake, _ORPHAN_WITH_LINES)
    env = _ask_fixture(
        lake,
        "How many customers do we have?",
        {"query_plan": {"measure": "customer_count", "group_by": []}},
        _hostile_onto(),
    )
    assert env["abstained"] is False
    assert _rows_multiset(env.get("rows")) == Counter({(3.0,)})


def test_dup_dimension_key_orders_only_sql_still_answers(tmp_path: Path) -> None:
    """SQL that never reads customers is not made wrong by a customer key fail."""
    lake = tmp_path / "dup_dim_orders.duckdb"
    _seed_stmts(lake, _DUP_DIM_KEY)
    env = _ask_fixture(
        lake,
        "What is total revenue?",
        {"query_sql": "SELECT SUM(amount_myr) AS revenue FROM orders"},
        _hostile_onto(),
    )
    assert env["abstained"] is False
    assert _rows_multiset(env.get("rows")) == Counter({(1000.0,)})
