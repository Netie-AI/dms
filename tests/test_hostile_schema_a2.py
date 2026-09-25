"""EPIC-A2 #256 / A2-01: when the customer's schema lies, measure the envelope.

Four defects that cannot occur in the engine bench (every declared key there
is correct by construction) are planted one at a time into an otherwise clean
warehouse:

  orphan            an order references a customer that does not exist
  fk_wrong_col      order_lines -> orders is declared on line_id, whose values
                    happen to equal real order ids, instead of order_id
  dup_business_key  customer_code C1 appears twice behind a unique surrogate
  lying_column      the revenue column is named revenue_usd, every row says
                    currency = 'MYR'

Each question runs twice, once as a typed ontology plan and once as generated
SQL, through ``maybe_generative_ask`` with a submit that really executes on the
same duckdb. Verdicts come from the envelope's rows compared against the
oracle, not from row counts:

  OK               confident and the rows equal the oracle
  WRONG            confident and the rows differ, or no confident answer exists
  ABSTAIN          abstained and the text names the defect
  ABSTAIN_UNNAMED  abstained without naming why (a coverage cost, not WRONG)

``MEASURED`` pins what main does today, gaps included. It is not a target and
not a skip: a fix or a regression both fail this test until the pin is edited
in the same commit, so neither can land silently. Every WRONG row cites the
follow-up ticket that owns it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology

GRANTS = {"customers", "orders", "order_lines"}

_BASE = (
    "CREATE TABLE customers (customer_id INTEGER, customer_code VARCHAR, region VARCHAR)",
    "INSERT INTO customers VALUES (1,'C1','North'),(2,'C2','South'),(3,'C3','North')",
    "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount_myr DOUBLE)",
    "INSERT INTO orders VALUES (10,1,100),(11,2,200),(12,3,300),(13,2,400)",
    "CREATE TABLE order_lines (line_id INTEGER, order_id INTEGER, qty DOUBLE)",
    # line_id values equal real order ids on purpose (fk_wrong_col).
    "INSERT INTO order_lines VALUES (10,11,1),(11,11,2),(12,10,5),(13,12,7)",
)


@dataclass(frozen=True)
class Case:
    extra_sql: tuple[str, ...] = ()
    line_fk: str = "order_id"
    amount: str = "amount_myr"


CASES: dict[str, Case] = {
    "control": Case(),
    "orphan": Case(extra_sql=("INSERT INTO orders VALUES (14,99,1000)",)),
    "fk_wrong_col": Case(line_fk="line_id"),
    "dup_business_key": Case(extra_sql=("INSERT INTO customers VALUES (4,'C1','North')",)),
    "lying_column": Case(
        extra_sql=(
            "ALTER TABLE orders RENAME COLUMN amount_myr TO revenue_usd",
            "ALTER TABLE orders ADD COLUMN currency VARCHAR DEFAULT 'MYR'",
        ),
        amount="revenue_usd",
    ),
}

#: qid -> (question, typed plan, generated SQL template)
QUESTIONS: dict[str, tuple[str, dict[str, Any], str]] = {
    "revenue_by_region": (
        "What is revenue by region?",
        {"query_plan": {"measure": "revenue", "group_by": [["customer", "region"]]}},
        "SELECT c.region, SUM(o.{amount}) AS revenue FROM orders o "
        "JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.region",
    ),
    "units_by_region": (
        "What is units by region?",
        {"query_plan": {"measure": "units", "group_by": [["customer", "region"]]}},
        "SELECT c.region, SUM(l.qty) AS units FROM order_lines l "
        "JOIN orders o ON l.{line_fk} = o.order_id "
        "JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.region",
    ),
    "customer_count": (
        "How many customers do we have?",
        {"query_plan": {"measure": "customer_count", "group_by": []}},
        "SELECT COUNT(*) AS customer_count FROM customers",
    ),
    "revenue_usd": (
        "What is total revenue in USD?",
        {"query_plan": {"measure": "revenue", "group_by": []}},
        "SELECT SUM({amount}) AS revenue_usd FROM orders",
    ),
}

_REGIONS_REV = {("North", 400.0), ("South", 600.0)}
_REGIONS_UNITS = {("North", 12.0), ("South", 3.0)}

#: Ground truth as a set of row tuples. None = no confident answer is correct
#: (the data cannot support one), so only an abstention passes.
ORACLE: dict[str, dict[str, set[tuple[Any, ...]] | None]] = {
    "control": {
        "revenue_by_region": _REGIONS_REV,
        "units_by_region": _REGIONS_UNITS,
        "customer_count": {(3.0,)},
        # MYR is not USD; nothing in the data converts it.
        "revenue_usd": None,
    },
    "orphan": {
        # Order 14 belongs to no customer: any region split misattributes or
        # drops 1000 MYR.
        "revenue_by_region": None,
        "units_by_region": _REGIONS_UNITS,
        "customer_count": {(3.0,)},
        "revenue_usd": None,
    },
    "fk_wrong_col": {
        "revenue_by_region": _REGIONS_REV,
        "units_by_region": _REGIONS_UNITS,
        "customer_count": {(3.0,)},
        "revenue_usd": None,
    },
    "dup_business_key": {
        "revenue_by_region": _REGIONS_REV,
        "units_by_region": _REGIONS_UNITS,
        # C1 is one customer under two surrogate ids.
        "customer_count": {(3.0,)},
        "revenue_usd": None,
    },
    "lying_column": {
        "revenue_by_region": _REGIONS_REV,
        "units_by_region": _REGIONS_UNITS,
        "customer_count": {(3.0,)},
        "revenue_usd": None,
    },
}

#: Words an abstention must contain to count as naming the defect.
DEFECT_WORDS: dict[str, tuple[str, ...]] = {
    "control": ("usd", "currency"),
    "orphan": ("orphan", "fk_intact", "does not exist", "order_customer"),
    "fk_wrong_col": ("line_order", "line_id", "foreign key"),
    "dup_business_key": ("customer_code", "duplicate", "business key"),
    "lying_column": ("usd", "myr", "currency"),
}

# Measured on main @ b10a5d22 then A2-05. (case, qid, mode) -> verdict.
# WRONG rows name the ticket that owns the fix. Improving a row is editing it.
# dms#258 A2-02 closed: a declared ontology that failed verify now refuses SQL
# joining the broken link and names it (check, link, orphan count) on both paths.
_FIXED_ORPHAN = "ABSTAIN"
# dms#259 A2-03: verify() refuses a link declared on the child's own key
# (fk_is_child_key). With A2-02, both paths abstain and name it. Coverage cost:
# fk_wrong_col questions that do not use line_order abstain too (whole ontology drops).
_FIXED_WRONG_FK = "ABSTAIN"
# dms#260 A2-04: the declared business key (customer_code) is verified, so the
# plan path abstains naming it; with A2-02 merged the SQL path refuses too.
# Cost: every dup_business_key plan row abstains, including revenue/units by
# region whose oracle is unaffected.
_GAP_DUP_KEY = "ABSTAIN"
_GAP_CURRENCY = "ABSTAIN"  # dms#261 A2-05: named currency must match the measure unit
MEASURED: dict[tuple[str, str, str], str] = {}
for _case in CASES:
    for _qid in QUESTIONS:
        for _mode in ("plan", "sql"):
            MEASURED[(_case, _qid, _mode)] = "OK"
for _case in CASES:
    for _mode in ("plan", "sql"):
        MEASURED[(_case, "revenue_usd", _mode)] = _GAP_CURRENCY
for _qid in QUESTIONS:
    MEASURED[("orphan", _qid, "plan")] = _FIXED_ORPHAN
    MEASURED[("dup_business_key", _qid, "plan")] = _GAP_DUP_KEY
    MEASURED[("fk_wrong_col", _qid, "plan")] = _FIXED_WRONG_FK
MEASURED[("orphan", "revenue_by_region", "sql")] = _FIXED_ORPHAN
# Coverage cost: the units SQL joins through order_customer too. Its rows would
# be right (order 14 has no lines) but the join is over a failed link.
MEASURED[("orphan", "units_by_region", "sql")] = _FIXED_ORPHAN
MEASURED[("fk_wrong_col", "units_by_region", "sql")] = _FIXED_WRONG_FK
# With A2-02 merged, generated SQL over the failed business key refuses too.
MEASURED[("dup_business_key", "customer_count", "sql")] = _FIXED_ORPHAN
# Coverage cost (A2-02 x A2-04): any SQL reading customers now refuses while
# customer_code is duplicated, even where the region rows would be right.
MEASURED[("dup_business_key", "revenue_by_region", "sql")] = _FIXED_ORPHAN
MEASURED[("dup_business_key", "units_by_region", "sql")] = _FIXED_ORPHAN
_A2_06_OK: tuple[tuple[str, str, str], ...] = (
    ("orphan", "customer_count", "plan"),
    ("fk_wrong_col", "revenue_by_region", "plan"),
    ("fk_wrong_col", "customer_count", "plan"),
)
for _key in _A2_06_OK:
    MEASURED[_key] = "OK"


def _ontology(case: Case) -> Ontology:
    o = Ontology()
    o.add_object("customer", "customers", ["customer_id"], business_key=["customer_code"])
    o.add_object("order", "orders", ["order_id"])
    o.add_object("line", "order_lines", ["line_id"])
    o.add_link("order_customer", "order", ["customer_id"], "customer", ["customer_id"])
    o.add_link("line_order", "line", [case.line_fk], "order", ["order_id"])
    o.add_measure("revenue", "order", f"SUM(f.{case.amount})")
    o.add_measure("units", "line", "SUM(f.qty)")
    o.add_measure("customer_count", "customer", "COUNT(*)")
    return o


def _seed(path: Path, case: Case) -> None:
    con = duckdb.connect(str(path))
    try:
        for stmt in (*_BASE, *case.extra_sql):
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
        except Exception:  # noqa: BLE001 -- execute failure is an abstain, not a pass
            return SimpleNamespace(ok=False, status="err", run_id="run_a2", output=None)
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_a2", output={"rows": rows})

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_a2", hash="hash_a2_not_entry")


def _row_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    """Column names differ by path (region vs customer_region); values do not."""
    text = tuple(sorted(str(v) for v in row.values() if isinstance(v, str)))
    nums = tuple(
        sorted(
            round(float(v), 6)
            for v in row.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        )
    )
    return (*text, *nums)


def _defect_words(case_id: str, qid: str) -> tuple[str, ...]:
    """Case defect, plus currency words on revenue_usd (unanswerable on every lake)."""
    base = DEFECT_WORDS[case_id]
    if qid != "revenue_usd":
        return base
    extra = DEFECT_WORDS["lying_column"] if case_id == "lying_column" else DEFECT_WORDS["control"]
    return tuple(dict.fromkeys((*base, *extra)))


def grade(env: dict[str, Any], oracle: set[tuple[Any, ...]] | None, words: tuple[str, ...]) -> str:
    if env.get("abstained") is True or env.get("badge") == "ABSTAIN":
        said = " ".join([str(env.get("text") or ""), *map(str, env.get("assumptions") or [])])
        return "ABSTAIN" if any(w in said.lower() for w in words) else "ABSTAIN_UNNAMED"
    if oracle is None:
        return "WRONG"
    got = {_row_tuple(r) for r in env.get("rows") or [] if isinstance(r, dict)}
    return "OK" if got == oracle else "WRONG"


def _ask(tmp_path: Path, case_id: str, qid: str, mode: str) -> dict[str, Any]:
    case = CASES[case_id]
    lake = tmp_path / f"{case_id}.duckdb"
    if not lake.exists():
        _seed(lake, case)
    question, plan, sql = QUESTIONS[qid]
    payload = (
        plan
        if mode == "plan"
        else {"query_sql": sql.format(amount=case.amount, line_fk=case.line_fk)}
    )
    env = maybe_generative_ask(
        question,
        warehouse=lake,
        grantable=set(GRANTS),
        compute=lambda _ctx: payload,
        submit=_submitter(lake),
        ledger_append=_ledger,
        ontology=_ontology(case),
    )
    assert env is not None, f"{case_id}/{qid}/{mode}: no envelope"
    assert_envelope_valid(env)
    return env


@pytest.mark.parametrize("mode", ["plan", "sql"])
@pytest.mark.parametrize("qid", list(QUESTIONS))
@pytest.mark.parametrize("case_id", list(CASES))
def test_envelope_verdict_matches_measured(
    tmp_path: Path, case_id: str, qid: str, mode: str
) -> None:
    env = _ask(tmp_path, case_id, qid, mode)
    verdict = grade(env, ORACLE[case_id][qid], _defect_words(case_id, qid))
    rendered = (env.get("text") or "")[:160]
    assert verdict == MEASURED[(case_id, qid, mode)], (
        f"{case_id}/{qid}/{mode}: envelope says {verdict}, pin says "
        f"{MEASURED[(case_id, qid, mode)]}. badge={env.get('badge')} "
        f"rows={env.get('rows')} text={rendered!r}. A fix edits the pin in the same commit."
    )


def test_control_answers_so_abstaining_cannot_pass_for_free(tmp_path: Path) -> None:
    """Clean schema: every question with an oracle is answered with its values."""
    for qid, oracle in ORACLE["control"].items():
        if oracle is None:
            continue
        for mode in ("plan", "sql"):
            env = _ask(tmp_path, "control", qid, mode)
            assert env["abstained"] is False, f"control/{qid}/{mode} abstained"
            assert env["text"], f"control/{qid}/{mode} has no rendered text"
            assert grade(env, oracle, ()) == "OK", f"control/{qid}/{mode}: {env['rows']}"


def test_grade_can_fail() -> None:
    """R-0007: the value check rejects a confident answer with the wrong number."""
    confident = {"abstained": False, "badge": "L2_VALIDATED", "rows": [{"n": 4}]}
    assert grade(confident, {(3.0,)}, ()) == "WRONG"
    assert grade(confident, None, ()) == "WRONG"
    assert grade({**confident, "rows": [{"n": 3}]}, {(3.0,)}, ()) == "OK"
    abstain = {"abstained": True, "badge": "ABSTAIN", "text": "orphan rows on order_customer"}
    assert grade(abstain, None, ("orphan",)) == "ABSTAIN"
    assert grade(abstain, None, ("duplicate",)) == "ABSTAIN_UNNAMED"


def test_measured_wrong_count_is_printed() -> None:
    """The scoreboard a buyer would see, with n. Not a target."""
    wrong = sorted(k for k, v in MEASURED.items() if v == "WRONG")
    n = len(MEASURED)
    print(f"A2 hostile schema: n={n} confident_wrong={len(wrong)}")
    for key in wrong:
        print("  WRONG", "/".join(key))
    assert n == len(CASES) * len(QUESTIONS) * 2
