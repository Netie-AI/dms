"""Free-form demo gate: precision and coverage, against an oracle the answer path never sees.

Why this exists
---------------
``verify_l2_vs_l1.py`` asserts a badge and a non-abstention. It never compares a
returned figure to a recomputed truth. So its ``ok: true`` means "an answer was
produced", not "an answer was right", and free-form precision is currently
measured on **zero** questions. By the rule of three (R-0010) that is n=0: there
is no bound on the free-form error rate at all.

That is the wrong state to demo free-form prompting from. The single
unrecoverable event in a buyer meeting is a confident badge over a number they
can check and find wrong. This gate is the thing that makes that event
detectable before the room does.

The oracle
----------
Truth is recomputed at run time by **direct DuckDB SQL, hand-written per case**,
executed against the same warehouse the stack serves. That path shares nothing
with the answer path (DMS -> Cortex -> FreeRoute -> generated SQL), which is what
makes it an oracle rather than a second opinion. No gold file is ever
hand-authored: a stored number cannot notice that the warehouse changed
underneath it.

Metrics
-------
  precision-on-answered  share of confident answers matching the oracle on
                         magnitude AND rank. Target 100.00 pct.
  coverage               share answered rather than abstained.

Coverage never buys back a wrong answer. Abstention is free here on purpose:
refusing to answer is the product working, and the gate must not create
pressure to guess.

  python scripts/verify_freeform_demo.py --space <space_id>
  python scripts/verify_freeform_demo.py --space <id> --oracle-only
  python scripts/verify_freeform_demo.py --list

Exit 0 only when nothing is confidently wrong.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("DMS_URL", "http://127.0.0.1:8090").rstrip("/")


def _default_warehouse() -> Path:
    """The warehouse that actually answers, which is not always DMS's own.

    A free-form ask is executed by Cortex against *its* warehouse. DMS keeps a
    local demo duckdb too, and the two can carry different schemas - DMS's
    ``inventory`` has no ``category`` column while Cortex's does. Pointing the
    oracle at the wrong file produces either a binder error or, far worse, a
    confident comparison against data the stack never read.
    """
    env = os.environ.get("DMS_ORACLE_WAREHOUSE")
    if env:
        return Path(env)
    cortex = Path(os.environ.get("CORTEX_HOME", r"D:\Cortex")) / "data" / "dms_demo.duckdb"
    if cortex.is_file():
        return cortex
    return ROOT / "data" / "dms_demo.duckdb"

REL_TOL = 0.005
ABS_TOL = 0.01


# --------------------------------------------------------------------------
# The demo set.
#
# Curated, and labelled as such. Genie reports 100% on 13 curated questions;
# curation is not the dishonest part, pretending the set is representative
# would be. Each case names why it is in the demo, so a question cannot quietly
# become "one we happen to pass".
#
# `oracle_sql` must be readable by a human as obviously-correct, because it is
# the thing standing in judgement over the model.
# --------------------------------------------------------------------------
DEMO_SET: list[dict[str, Any]] = [
    {
        "id": "ff_carrier_ontime",
        "why": "the one free-form question with prior evidence; joins + filter + ratio",
        "question": "rank carriers by on-time percentage for hazmat only",
        # `inventory` is one row per stock LOT, not per SKU - 7,388 rows for 509
        # SKUs. Joining shipments to it directly counts every shipment once per
        # lot, turning 1,214 hazardous shipments into 7,425 and reweighting the
        # ratio by how many lots each SKU happens to have. It changes the
        # ranking, not just the magnitudes: the naive join puts DHL MY first,
        # the correct one puts J&T Express first.
        "oracle_sql": """
            SELECT s.carrier,
                   ROUND(100.0 * SUM(CASE WHEN s.status = 'DELIVERED'
                                           AND s.actual_arrival <= s.expected_arrival
                                          THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(*), 0), 2) AS on_time_percentage
            FROM shipments s
            JOIN (SELECT DISTINCT sku FROM inventory WHERE is_hazardous) h
              ON s.sku = h.sku
            GROUP BY s.carrier
            ORDER BY on_time_percentage DESC, s.carrier ASC
        """,
        "top_n": 3,
    },
    {
        "id": "ff_category_sales",
        "why": "F32 shape done honestly - names the scope, so a right answer is possible",
        "question": (
            "What is the total sales value in MYR for each inventory category, "
            "counting only outbound transactions? Give me the top 3."
        ),
        # Same lot/SKU trap. The naive join fans 2,018 outbound transactions into
        # 29,891 rows and inflates every category total by ~14.8x - and it moves
        # the ranking: FOOD_DRY appears second under the naive join and is not in
        # the top 3 at all once deduplicated.
        #
        # Proof the deduplicated form is right: its category totals sum to
        # exactly 80,375,993.99, which is the overall outbound revenue the
        # governed metric returns independently. The naive form sums to
        # 1,192,883,779.21, which is not any revenue this business ever had.
        # That conservation identity is asserted at run time below.
        "oracle_sql": """
            SELECT c.category,
                   ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS sales_value_myr
            FROM transactions t
            JOIN (SELECT DISTINCT sku, category FROM inventory) c ON t.sku = c.sku
            WHERE t.txn_type = 'OUT'
            GROUP BY c.category
            ORDER BY sales_value_myr DESC, c.category ASC
        """,
        "top_n": 3,
        # An oracle that can be checked against a number derived another way is
        # worth far more than one that cannot. If a schema change ever breaks
        # the grouping, this fires before the gate can judge anybody.
        "conservation": {
            "sql": (
                "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) "
                "FROM transactions WHERE txn_type = 'OUT'"
            ),
            "why": "category totals must sum to overall outbound revenue",
        },
    },
    {
        "id": "ff_leadtime_by_country",
        "why": "a real GROUP BY - suppliers is one row per supplier, so grouping by "
               "supplier_id would be a sort wearing an aggregate's clothes",
        "question": "which 3 countries have the longest average supplier lead time?",
        "oracle_sql": """
            SELECT country, ROUND(AVG(lead_time_days), 2) AS avg_lead_time_days
            FROM suppliers
            GROUP BY country
            ORDER BY avg_lead_time_days DESC, country ASC
        """,
        "top_n": 3,
    },
    {
        "id": "ff_hazardous_value",
        "why": "boolean filter + single scalar; a wrong filter shows up as a wrong magnitude",
        # No join, so no fan-out: summing lots IS the stock value.
        "question": "what is the total stock value in MYR of hazardous inventory?",
        "oracle_sql": """
            SELECT 'hazardous' AS scope,
                   ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS total_value_myr
            FROM inventory WHERE is_hazardous
        """,
        "top_n": 1,
    },
    {
        "id": "ff_shipment_cost_by_carrier",
        "why": "money grouped over a table with a clean grain - shipments is unique per id",
        "question": "which 3 carriers cost us the most in shipping, in MYR?",
        "oracle_sql": """
            SELECT carrier, ROUND(SUM(cost_myr), 2) AS shipping_cost_myr
            FROM shipments GROUP BY carrier
            ORDER BY shipping_cost_myr DESC, carrier ASC
        """,
        "top_n": 3,
        "conservation": {
            "sql": "SELECT ROUND(SUM(cost_myr), 2) FROM shipments",
            "why": "carrier costs must sum to total shipping cost",
        },
    },
    {
        "id": "ff_stock_value_by_category",
        "why": "the honest sibling of ff_category_sales - stock value, no join, so lots "
               "are the unit of truth rather than a fan-out hazard",
        "question": "what is our total stock value in MYR by category? top 3",
        "oracle_sql": """
            SELECT category, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS stock_value_myr
            FROM inventory GROUP BY category
            ORDER BY stock_value_myr DESC, category ASC
        """,
        "top_n": 3,
        "conservation": {
            "sql": "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) FROM inventory",
            "why": "category stock values must sum to total inventory value",
        },
    },
    {
        "id": "ff_unresolved_alerts_by_severity",
        "why": "a count rather than money, behind a boolean filter - a dropped WHERE "
               "shows up as an inflated count instead of a plausible sum",
        "question": "how many unresolved alerts do we have of each severity?",
        "oracle_sql": """
            SELECT severity, COUNT(*) AS alert_count
            FROM alerts WHERE NOT resolved
            GROUP BY severity ORDER BY alert_count DESC, severity ASC
        """,
        "top_n": 3,
        "conservation": {
            "sql": "SELECT COUNT(*) FROM alerts WHERE NOT resolved",
            "why": "severity counts must sum to the unresolved total",
        },
    },
]


class OracleBroken(Exception):
    """The oracle failed its own consistency check, so it cannot judge anything."""


def oracle(case: dict[str, Any], warehouse: Path) -> list[tuple[str, float]]:
    """Recompute truth with hand-written SQL. Never the answer path, never a gold file.

    Where a case declares a ``conservation`` identity, it is enforced here on the
    *full* result before any truncation to top-N. This exists because the first
    version of this file got two oracles wrong the same way: ``inventory`` holds
    one row per stock lot rather than per SKU, so joining to it fanned the fact
    tables out and inflated every total by ~15x. Both wrong oracles looked
    entirely plausible, and one of them even agreed with what the live stack
    returned - because the stack makes the same mistake. Two wrongs agreeing is
    not verification, and that near-miss is what this check is for.
    """
    import duckdb

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        rows = con.execute(" ".join(str(case["oracle_sql"]).split())).fetchall()
        cons = case.get("conservation")
        if cons:
            expected = con.execute(" ".join(str(cons["sql"]).split())).fetchone()[0]
            got = sum(float(r[1]) for r in rows if r[1] is not None)
            if expected is None or abs(got - float(expected)) > 0.05:
                raise OracleBroken(
                    f"{case['id']}: {cons['why']} - oracle sums to {got:,.2f}, "
                    f"independent total is {float(expected or 0):,.2f}. "
                    "The oracle is wrong; fix it before trusting any verdict."
                )
    finally:
        con.close()
    out: list[tuple[str, float]] = []
    for r in rows[: int(case["top_n"])]:
        label = str(r[0])
        value = r[1]
        if value is None:
            continue
        out.append((label, float(value)))
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= ABS_TOL or abs(a - b) / max(abs(b), 1e-9) <= REL_TOL


def claimed_ranking(env: dict[str, Any]) -> list[tuple[str, float]] | None:
    """Read the ranking the envelope actually asserts, from executed rows.

    Rows, not prose: E9 guarantees a non-abstained numeric answer carries
    executed rows, so rows are the honest place to read a claim from. Row order
    is the ranking - a top-N whose ORDER BY is wrong is wrong even when every
    figure is individually right.
    """
    rows = env.get("rows") or []
    if not rows:
        return None
    first = rows[0]
    dim = next((k for k, v in first.items() if isinstance(v, str)), None)
    num = next(
        (k for k, v in first.items() if isinstance(v, (int, float)) and not isinstance(v, bool)),
        None,
    )
    if num is None:
        return None
    out: list[tuple[str, float]] = []
    for r in rows:
        val = r.get(num)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        label = str(r.get(dim)).strip() if dim else "value"
        out.append((label, float(val)))
    return out or None


def judge(env: dict[str, Any], expected: list[tuple[str, float]]) -> tuple[str, str]:
    """abstained | correct | WRONG. Only the last two touch precision."""
    if env.get("abstained"):
        return "abstained", str(env.get("text") or "")[:110]

    claimed = claimed_ranking(env)
    if claimed is None:
        return "WRONG", "confident badge with no executed rows to read a claim from"

    claimed = claimed[: len(expected)]
    if len(claimed) < len(expected):
        return "WRONG", f"returned {len(claimed)} rows, oracle has {len(expected)}"

    # Magnitudes first: a right ranking over wrong numbers is the F26 shape.
    for (_, got), (key, want) in zip(claimed, expected):
        if not _close(got, want):
            return "WRONG", f"{key}: answered {got:,.2f}, oracle {want:,.2f}"

    # Then order, but only when the oracle actually has a dimension to rank by.
    if len(expected) > 1:
        exp_keys = [k.lower() for k, _ in expected]
        got_keys = [k.lower() for k, _ in claimed]
        if exp_keys != got_keys and all(k != "value" for k in got_keys):
            return "WRONG", f"rank order {got_keys} != oracle {exp_keys}"

    return "correct", ", ".join(f"{k}={v:,.2f}" for k, v in claimed)


def ask_live(question: str, space_id: str, timeout: float) -> dict[str, Any]:
    import httpx

    r = httpx.post(
        f"{API}/v1/chat/ask",
        json={"question": question, "space_id": space_id},
        timeout=timeout,
    )
    r.raise_for_status()
    return dict(r.json())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--space", help="Space id to ask against")
    ap.add_argument("--warehouse", type=Path, default=_default_warehouse(),
                help="oracle warehouse; defaults to the one Cortex serves")
    ap.add_argument("--oracle-only", action="store_true", help="print truth and stop")
    ap.add_argument("--list", action="store_true", help="print the demo set and stop")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args(argv)

    if args.list:
        for c in DEMO_SET:
            print(f"  {c['id']:<22} {c['why']}")
            print(f"  {'':<22} {c['question']}")
        return 0
    if not args.oracle_only and not args.space:
        ap.error("--space is required unless --oracle-only is given")
    if not args.warehouse.is_file():
        print(f"FAIL warehouse not found: {args.warehouse}")
        print("     start the stack once so the demo warehouse is built")
        return 2

    print("=== ORACLE (recomputed now, direct DuckDB, independent of the answer path) ===")
    cases: list[tuple[dict[str, Any], list[tuple[str, float]]]] = []
    for case in DEMO_SET:
        try:
            truth = oracle(case, args.warehouse)
        except Exception as exc:  # noqa: BLE001
            print(f"  {case['id']:<22} ORACLE ERROR {type(exc).__name__}: {exc}")
            return 2
        if not truth:
            print(f"  {case['id']:<22} ORACLE EMPTY - the case cannot judge anything")
            return 2
        cases.append((case, truth))
        print(f"  {case['id']:<22} {'  '.join(f'{k}={v:,.2f}' for k, v in truth)}")
        print(f"  {'':<22} why: {case['why']}")

    if args.oracle_only:
        return 0

    print(f"\n=== ANSWERS (space={args.space}) ===")
    answered = correct = wrong = 0
    failures: list[str] = []
    abstentions: list[str] = []

    for case, truth in cases:
        try:
            env = ask_live(str(case["question"]), str(args.space), args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"  {case['id']:<22} ERROR  {type(exc).__name__}: {str(exc)[:80]}")
            failures.append(f"{case['id']}: ask failed ({type(exc).__name__})")
            continue

        outcome, detail = judge(env, truth)
        badge = env.get("badge")
        print(f"  {case['id']:<22} {outcome:<10} [{badge}] {detail}")
        if outcome == "abstained":
            abstentions.append(case["id"])
            continue
        answered += 1
        if outcome == "correct":
            correct += 1
        else:
            wrong += 1
            failures.append(f"{case['id']}: {detail}")

    total = len(cases)
    precision = (correct / answered * 100.0) if answered else 100.0
    coverage = answered / total * 100.0 if total else 0.0

    print("\n=== RESULT ===")
    print(f"  precision-on-answered  {precision:6.2f} pct   ({correct}/{answered})")
    print(f"  coverage               {coverage:6.2f} pct   ({answered}/{total})")
    print(f"  demo set               {total} curated questions"
          f" - not a representative sample, and not claimed as one")
    # The rule of three (R-0010): zero errors in n trials bounds the true rate
    # at 3/n with 95pct confidence. That only says something once n is large
    # enough for 3/n to be below 1 - at n=1 it "bounds" the rate at 300 pct,
    # which is not a bound, it is arithmetic with no content. Printing it would
    # be exactly the kind of authoritative-looking nonsense this gate exists to
    # catch, so below n=4 it says what is actually true: nothing yet.
    if not wrong and answered:
        bound = 3.0 / answered * 100.0
        if bound < 100.0:
            print(f"  error bound (R-0010)   0 wrong in {answered} answered bounds the true"
                  f" error rate at ~{bound:.1f} pct - not at 0")
        else:
            print(f"  error bound (R-0010)   {answered} answered is too few to bound the"
                  f" error rate at all; n>=300 before claiming <1 pct")

    if wrong:
        print(f"\nFAIL {wrong} confidently wrong. Coverage does not buy this back.")
        for f in failures:
            print(f"  - {f}")
        print("\n  Do not demo free-form until this is 0.")
        return 1
    if failures:
        print("\nFAIL run incomplete:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASS 0 confidently wrong.")
    if abstentions:
        print(f"     abstained: {', '.join(abstentions)}")
        print("     Abstention is the product working. Raise coverage by curating"
              " assets, never by loosening this gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
