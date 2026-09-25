"""A2-05 / dms#261: named currency vs data, via sqlglot (not a regex allowlist).

Each adversarial SQL shape either ships MYR as USD on b10a5d22, or would be
certified by a name-matching regex. This gate abstains and names the mismatch.
Questions that name no currency are untouched. No FX conversion.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology
from dms_executor.sql_currency import asked_currencies, asked_currency, currency_mismatch_reason

USD_Q = "What is total revenue in USD?"
NO_CCY_Q = "What is revenue by region?"
GRANTS = {"customers", "orders", "order_lines", "usd_book", "usd.book", "book", "usd"}


def _naive_name_match_certifies(sql: str) -> bool:
    """Regex/name gate: USD-looking identifiers or a SUM(revenue_usd) certify.

    The class the founder rejected. These tests stay red if that design returns.
    """
    stripped = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    stripped = re.sub(r"--[^\n]*", " ", stripped)
    low = stripped.lower()
    if "usd_book" in low or "revenue_usd" in low or '"usd.' in low:
        return True
    if re.search(r"\bSUM\s*\([^)]*usd", stripped, re.I):
        return True
    # Quoted-dot read as schema.table (usd.book).
    if re.search(r'"usd"\s*\.\s*"book"|schema\s+usd', stripped, re.I):
        return True
    return False


def _ontology(amount: str = "amount_myr") -> Ontology:
    o = Ontology()
    o.add_object("customer", "customers", ["customer_id"], business_key=["customer_code"])
    o.add_object("order", "orders", ["order_id"])
    o.add_object("line", "order_lines", ["line_id"])
    o.add_link("order_customer", "order", ["customer_id"], "customer", ["customer_id"])
    o.add_link("line_order", "line", ["order_id"], "order", ["order_id"])
    o.add_measure("revenue", "order", f"SUM(f.{amount})")
    o.add_measure("units", "line", "SUM(f.qty)")
    o.add_measure("customer_count", "customer", "COUNT(*)")
    return o


def _seed_myr(path: Path, extra: tuple[str, ...] = ()) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE customers (customer_id INTEGER, customer_code VARCHAR, region VARCHAR)"
        )
        con.execute(
            "INSERT INTO customers VALUES "
            "(1,'C1','North'),(2,'C2','South'),(3,'C3','North')"
        )
        con.execute(
            "CREATE TABLE orders ("
            "order_id INTEGER, customer_id INTEGER, amount_myr DOUBLE)"
        )
        con.execute("INSERT INTO orders VALUES (10,1,100),(11,2,200),(12,3,300),(13,2,400)")
        con.execute("CREATE TABLE order_lines (line_id INTEGER, order_id INTEGER, qty DOUBLE)")
        con.execute("INSERT INTO order_lines VALUES (10,11,1),(11,11,2),(12,10,5),(13,12,7)")
        con.execute("CREATE TABLE usd_book (amount DOUBLE)")
        con.execute("INSERT INTO usd_book VALUES (999)")
        con.execute('CREATE TABLE "usd.book" (amount DOUBLE)')
        con.execute('INSERT INTO "usd.book" VALUES (999)')
        con.execute("CREATE MACRO kahan_sum(x) AS (x)")
        for stmt in extra:
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
        except Exception:  # noqa: BLE001
            return SimpleNamespace(ok=False, status="err", run_id="run_a205", output=None)
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_a205", output={"rows": rows})

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_a205", hash="hash_a205_not_entry")


def _ask(
    lake: Path,
    question: str,
    payload: dict[str, Any],
    *,
    amount: str = "amount_myr",
) -> dict[str, Any]:
    env = maybe_generative_ask(
        question,
        warehouse=lake,
        grantable=set(GRANTS),
        compute=lambda _ctx: payload,
        submit=_submitter(lake),
        ledger_append=_ledger,
        ontology=_ontology(amount),
    )
    assert env is not None, "no envelope"
    assert_envelope_valid(env)
    return env


def _assert_currency_abstain(env: dict[str, Any], sql: str | None = None) -> None:
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    said = " ".join([str(env.get("text") or ""), *map(str, env.get("assumptions") or [])])
    low = said.lower()
    assert "usd" in low or "currency" in low, said
    assert "myr" in low or "unknown" in low or "traced" in low or "parsed" in low, said
    text = str(env.get("text") or "")
    assert "usd" in text.lower() or "currency" in text.lower(), text
    if sql is not None:
        assert _naive_name_match_certifies(sql) or "sum(" in sql.lower()


# --- recognition -------------------------------------------------------------


def test_asked_currency_iso_words_symbols_fullwidth() -> None:
    assert asked_currency("What is total revenue in USD?") == "USD"
    assert asked_currency("total revenue in dollars") == "USD"
    assert asked_currency("revenue in euro") == "EUR"
    assert asked_currency("revenue in pounds") == "GBP"
    assert asked_currency("revenue in yuan") == "CNY"
    assert asked_currency("revenue in ringgit") == "MYR"
    wide = "What is total revenue in " + unicodedata.normalize("NFKC", "ＵＳＤ") + "?"
    # Fullwidth tokens NFKC to ASCII USD.
    assert asked_currency("What is total revenue in ＵＳＤ?") == "USD"
    assert asked_currency(wide) == "USD"
    assert asked_currency("What is revenue by region?") is None
    assert asked_currency("What is units by region?") is None
    assert asked_currencies("USD and MYR") == frozenset({"USD", "MYR"})


def test_question_naming_no_currency_is_untouched(tmp_path: Path) -> None:
    lake = tmp_path / "ctrl.duckdb"
    _seed_myr(lake)
    sql = (
        "SELECT c.region, SUM(o.amount_myr) AS revenue FROM orders o "
        "JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.region"
    )
    env = _ask(lake, NO_CCY_Q, {"query_sql": sql})
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"], env
    nums = [
        float(v)
        for row in env["rows"]
        for v in row.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    assert sorted(nums) == [400.0, 600.0]


def test_matching_usd_column_and_currency_answers(tmp_path: Path) -> None:
    lake = tmp_path / "usd_ok.duckdb"
    con = duckdb.connect(str(lake))
    try:
        con.execute("CREATE TABLE orders (order_id INTEGER, amount_usd DOUBLE, currency VARCHAR)")
        con.execute("INSERT INTO orders VALUES (1, 50, 'USD'), (2, 70, 'USD')")
    finally:
        con.close()
    o = Ontology()
    o.add_object("order", "orders", ["order_id"])
    o.add_measure("revenue", "order", "SUM(f.amount_usd)")
    env = maybe_generative_ask(
        USD_Q,
        warehouse=lake,
        grantable={"orders"},
        compute=lambda _c: {"query_sql": "SELECT SUM(amount_usd) AS revenue FROM orders"},
        submit=_submitter(lake),
        ledger_append=_ledger,
        ontology=o,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is False, env.get("text")
    assert env["rows"]
    vals = [
        float(v)
        for row in env["rows"]
        for v in row.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    assert vals == [120.0]


# --- round 1 -----------------------------------------------------------------


_ROUND1 = {
    "cte_named_after_usd_table": (
        "WITH usd_book AS (SELECT amount_myr AS amt FROM orders) "
        "SELECT SUM(amt) AS revenue_usd FROM usd_book"
    ),
    "alias_collides_with_table": (
        "SELECT SUM(usd_book.amount_myr) AS revenue_usd FROM orders AS usd_book"
    ),
    "untraced_agg_beside_decoy_sum": (
        "SELECT kahan_sum(amount_myr) AS revenue_usd FROM orders "
        "WHERE EXISTS (SELECT SUM(amount) FROM usd_book)"
    ),
}


# --- round 2 -----------------------------------------------------------------


_ROUND2 = {
    "derived_rename_exists_in_decoy": (
        "SELECT SUM(t.amt) AS revenue_usd FROM ("
        "SELECT amount_myr AS amt FROM orders"
        ") t WHERE EXISTS (SELECT 1 FROM usd_book) "
        "AND t.amt IN (SELECT amount FROM usd_book UNION SELECT amount_myr FROM orders)"
    ),
    "comment_hides_from": "SELECT SUM(amount_myr) AS revenue_usd FROM /* usd_book */ orders",
    "comment_after_from": "SELECT SUM(amount_myr) AS revenue_usd FROM orders -- FROM usd_book",
    "scalar_subquery_headline": (
        "SELECT (SELECT SUM(amount_myr) FROM orders) AS revenue_usd"
    ),
    "sum_plus_literal": "SELECT SUM(amount_myr) + 1 AS revenue_usd FROM orders",
}


# --- round 3 -----------------------------------------------------------------


_ROUND3 = {
    "plain_numeric_column": "SELECT amount_myr AS revenue_usd FROM orders",
    "quoted_dot_identifier": 'SELECT SUM(amount) AS revenue_usd FROM "usd.book"',
    "any_value_plain_agg": (
        "SELECT ANY_VALUE(amount_myr) AS revenue_usd FROM orders "
        "WHERE EXISTS (SELECT SUM(amount) FROM usd_book)"
    ),
}


def _sql_path(sql: str) -> dict[str, Any]:
    return {"query_sql": sql}


def test_round1_cte_alias_untraced_agg_abstain(tmp_path: Path) -> None:
    lake = tmp_path / "r1.duckdb"
    _seed_myr(lake)
    for name, sql in _ROUND1.items():
        env = _ask(lake, USD_Q, _sql_path(sql))
        _assert_currency_abstain(env, sql)
        if name != "untraced_agg_beside_decoy_sum":
            assert _naive_name_match_certifies(sql), name


def test_round2_derived_comments_scalar_literal_abstain(tmp_path: Path) -> None:
    lake = tmp_path / "r2.duckdb"
    _seed_myr(lake)
    for name, sql in _ROUND2.items():
        env = _ask(lake, USD_Q, _sql_path(sql))
        _assert_currency_abstain(env, sql)
        if name in {"derived_rename_exists_in_decoy", "comment_hides_from", "comment_after_from"}:
            assert _naive_name_match_certifies(sql) or "usd_book" in sql.lower()


def test_round3_plain_column_quoted_dot_words_and_ccy_cols(tmp_path: Path) -> None:
    lake = tmp_path / "r3.duckdb"
    _seed_myr(lake)
    for _name, sql in _ROUND3.items():
        env = _ask(lake, USD_Q, _sql_path(sql))
        _assert_currency_abstain(env, sql)
    myr_sum = _sql_path("SELECT SUM(amount_myr) FROM orders")
    env = _ask(lake, "What is total revenue in dollars?", myr_sum)
    _assert_currency_abstain(env)
    env = _ask(lake, "What is total revenue in euro?", myr_sum)
    assert env["abstained"] is True
    env = _ask(lake, "What is total revenue in pounds?", myr_sum)
    assert env["abstained"] is True
    env = _ask(lake, "What is total revenue in yuan?", myr_sum)
    assert env["abstained"] is True
    env = _ask(lake, "What is total revenue in ＵＳＤ?", myr_sum)
    _assert_currency_abstain(env)


def test_round3_currency_iso_and_currencycode_conflict(tmp_path: Path) -> None:
    lake = tmp_path / "lying_iso.duckdb"
    con = duckdb.connect(str(lake))
    try:
        con.execute(
            "CREATE TABLE orders (order_id INTEGER, revenue_usd DOUBLE, "
            "currency_iso VARCHAR, currencycode VARCHAR, ccy VARCHAR)"
        )
        con.execute(
            "INSERT INTO orders VALUES (1, 100, 'MYR', 'MYR', 'MYR'), "
            "(2, 200, 'MYR', 'MYR', 'MYR')"
        )
    finally:
        con.close()
    sql = "SELECT SUM(revenue_usd) AS revenue_usd FROM orders"
    o = Ontology()
    o.add_object("order", "orders", ["order_id"])
    o.add_measure("revenue", "order", "SUM(f.revenue_usd)")
    for question in (USD_Q, "What is total revenue in MYR?"):
        env = maybe_generative_ask(
            question,
            warehouse=lake,
            grantable={"orders"},
            compute=lambda _c, s=sql: {"query_sql": s},
            submit=_submitter(lake),
            ledger_append=_ledger,
            ontology=o,
        )
        assert env is not None
        assert_envelope_valid(env)
        assert env["abstained"] is True, env.get("text")
        said = str(env.get("text") or "").lower()
        assert "unverified" in said or "myr" in said


def test_typed_plan_usd_on_myr_abstains(tmp_path: Path) -> None:
    lake = tmp_path / "plan.duckdb"
    _seed_myr(lake)
    env = _ask(lake, USD_Q, {"query_plan": {"measure": "revenue", "group_by": []}})
    _assert_currency_abstain(env)


def test_unparseable_sql_abstains_when_currency_named(tmp_path: Path) -> None:
    lake = tmp_path / "bad.duckdb"
    _seed_myr(lake)
    reason = currency_mismatch_reason(USD_Q, "SELECT SUM(amount_myr FROM orders", warehouse=lake)
    assert reason is not None
    assert "parsed" in reason.lower()
    # EXPLAIN will also fail this shape; the gate reason is what we pin.
    assert "USD" in reason


def test_no_currency_named_skips_gate_even_with_usd_cte_name(tmp_path: Path) -> None:
    lake = tmp_path / "skip.duckdb"
    _seed_myr(lake)
    sql = (
        "WITH usd_book AS (SELECT amount_myr AS amt FROM orders) "
        "SELECT SUM(amt) AS revenue FROM usd_book"
    )
    assert currency_mismatch_reason(NO_CCY_Q, sql, warehouse=lake) is None
    env = _ask(lake, "What is total revenue?", _sql_path(sql))
    assert env["abstained"] is False, env.get("text")
    vals = [
        float(v)
        for row in env["rows"]
        for v in row.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    assert vals == [1000.0]
