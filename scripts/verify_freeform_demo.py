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

Two kinds of case
-----------------
``expect: "answer"``  a defensible confident answer exists, and the oracle says
                      what it is. Abstaining costs coverage, never precision.
``expect: "abstain"`` there is no defensible confident answer, so any confident
                      answer is WRONG no matter what number it carries.

The second kind is the half that a precision-only gate cannot see. A product
that answers everything scores 100 pct precision on the questions it happens to
get right while inventing an answer to every question it should have refused,
and nothing in a coverage number distinguishes that from competence.

An abstain case never asserts "this is unanswerable" on the author's say-so.
Each one carries a ``unanswerable`` proof that is executed at run time before
the case is allowed to judge anybody:

  ambiguous  two defensible readings of the same question, which must return
             DIFFERENT results. If a data change ever makes them agree, the
             question has stopped being ambiguous and the case is retired
             rather than left demanding a refusal it can no longer justify.
  empty      a filter that matches nothing, proven by a count that must be 0.
             A confident 0.00 here is the most dangerous answer in the product.
  absent     the warehouse carries no column that could answer it, proven by a
             count over information_schema that must be 0.

Metrics
-------
  precision-on-answered  share of confident answers matching the oracle on
                         magnitude AND rank. Target 100.00 pct.
  coverage               share answered rather than abstained.
  refusal-precision      share of unanswerable questions correctly refused.

Coverage never buys back a wrong answer. Abstention is free on ``expect:
answer`` cases on purpose: refusing to answer is the product working, and the
gate must not create pressure to guess. It is not free on ``expect: abstain``
cases - there, refusing IS the right answer.

  python scripts/verify_freeform_demo.py --space <space_id>
  python scripts/verify_freeform_demo.py --space <id> --oracle-only
  python scripts/verify_freeform_demo.py --self-check   # can each case pass AND fail?
  python scripts/verify_freeform_demo.py --list

Run --self-check before trusting a green live run. It feeds each case its own
oracle (which must be accepted) and a perturbed answer (which must be rejected),
so a case that cannot fail, or one that rejects its own truth, is caught without
asking the product anything.

Exit 0 only when nothing is confidently wrong.
"""

from __future__ import annotations

import argparse
import os
import re
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
        # The scope is stated in the question on purpose. The original phrasing -
        # "rank carriers by on-time percentage for hazmat only" - does not say
        # whether a shipment that has not arrived counts against the carrier, and
        # 6,026 of 12,000 shipments have never arrived. Both readings are
        # defensible and they disagree on the winner (all-shipments puts J&T
        # Express first at 25.12; delivered-only puts GDEX first at 50.00), so
        # the unscoped phrasing cannot be graded against any single oracle. It is
        # not discarded - it is now ff_ontime_ambiguous_denominator, where the
        # correct behaviour is to refuse. Grading it as an answerable question
        # would have marked the conventional reading wrong, which is R-0005: a
        # control that refuses legitimate work is a failure, not a win.
        "question": (
            "Of hazmat shipments that have been delivered, rank carriers by the "
            "percentage that arrived on or before the expected date. Top 3."
        ),
        # `inventory` is one row per stock LOT, not per SKU - 7,388 rows for 509
        # SKUs. Joining shipments to it directly counts every shipment once per
        # lot and reweights the ratio by how many lots each SKU happens to have.
        # It changes the ranking, not just the magnitudes.
        "oracle_sql": """
            SELECT s.carrier,
                   ROUND(100.0 * SUM(CASE WHEN s.actual_arrival <= s.expected_arrival
                                          THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(*), 0), 2) AS on_time_percentage
            FROM shipments s
            JOIN (SELECT DISTINCT sku FROM inventory WHERE is_hazardous) h
              ON s.sku = h.sku
            WHERE s.status = 'DELIVERED'
            GROUP BY s.carrier
            ORDER BY on_time_percentage DESC, s.carrier ASC
        """,
        "top_n": 3,
    },
    {
        "id": "ff_category_sales",
        "why": "F32 shape done honestly - names the scope, so a right answer is possible",
        "question": (
            "Valued at the unit cost recorded on each transaction, what is the "
            "total outbound value in MYR for each inventory category, counting "
            "only outbound transactions? Give me the top 3."
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
            "why": "category totals must sum to overall outbound value at cost",
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
        # From the adversarial workflow. The strongest case in the set: it needs a
        # SAFE dimension join (locations, 20 unique rows, for the cold-storage
        # filter) and a HAZARDOUS one (inventory, for category) in the same query.
        # Joining inventory raw does not merely inflate - it inverts ranks 2 and 3,
        # because lots-per-sku differs. MACHINERY_PARTS 688 -> 10,816 kg, and
        # PPE/MEDICAL swap. Denominated in kilograms on purpose: transactions and
        # inventory disagree on unit_cost_myr for the same sku, so a money version
        # would have two defensible oracles and could not be graded.
        "id": "ff_writeoff_kg_coldstore_by_category",
        "why": "two joins at once - one safe, one fan-out - where the trap "
               "inverts rank, not just magnitude",
        "question": (
            "At our cold-storage warehouses only, how many kilograms of stock have "
            "been written off, broken down by product category? Top 3."
        ),
        "oracle_sql": """
            SELECT ic.category, SUM(t.quantity_kg) AS written_off_kg
            FROM transactions t
            JOIN locations l ON l.location_id = t.location_id
            JOIN (SELECT DISTINCT sku, category FROM inventory) ic ON ic.sku = t.sku
            WHERE t.txn_type = 'WRITE_OFF' AND l.is_cold_storage
            GROUP BY ic.category
            ORDER BY written_off_kg DESC, ic.category ASC
        """,
        "top_n": 3,
        "conservation": {
            "sql": (
                "SELECT SUM(t.quantity_kg) FROM transactions t "
                "JOIN locations l ON l.location_id = t.location_id "
                "WHERE t.txn_type = 'WRITE_OFF' AND l.is_cold_storage"
            ),
            "why": "category write-offs must sum to the cold-store write-off total, "
                   "computed without touching inventory at all",
        },
    },
    {
        "id": "ff_chemicals_freight_by_country",
        "why": "three tables, one of them the fan-out hazard, resolving a dimension "
               "the fact table does not carry",
        "question": (
            "For shipments of CHEMICALS that have already been delivered, what is the "
            "total shipping cost broken down by the supplier's country?"
        ),
        "oracle_sql": """
            WITH sku_category AS (SELECT DISTINCT sku, category FROM inventory)
            SELECT sup.country AS supplier_country,
                   ROUND(SUM(s.cost_myr), 2) AS total_shipping_cost_myr
            FROM shipments s
            JOIN sku_category sc ON sc.sku = s.sku
            JOIN suppliers sup ON sup.supplier_id = s.supplier_id
            WHERE sc.category = 'CHEMICALS' AND s.status = 'DELIVERED'
            GROUP BY sup.country
            ORDER BY total_shipping_cost_myr DESC, sup.country ASC
        """,
        # Only four supplier countries exist and the question says "broken down
        # by", so all four are graded. At top_n 3 an answer that silently dropped
        # Singapore (144,952.93, 9.6 pct of the total) scored correct.
        "top_n": 4,
        "exact_rows": True,
        "conservation": {
            "sql": (
                "WITH sku_category AS (SELECT DISTINCT sku, category FROM inventory) "
                "SELECT ROUND(SUM(s.cost_myr), 2) FROM shipments s "
                "JOIN sku_category sc ON sc.sku = s.sku "
                "JOIN suppliers sup ON sup.supplier_id = s.supplier_id "
                "WHERE sc.category = 'CHEMICALS' AND s.status = 'DELIVERED'"
            ),
            "why": "country freight must sum to total delivered CHEMICALS freight",
        },
    },
    {
        # Reshaped from the workflow's version, which returned (kg, shipment_count).
        # This gate grades column 1 as the value, so a two-metric scalar would have
        # been graded on the shipment count while the question asks about kilograms.
        "id": "ff_coldchain_breach_kg",
        "why": "a scalar behind a NOT filter - two different slips produce two "
               "different plausible kilogram figures",
        # Two distinct wrong answers, both plausible. Inverting the negation
        # (cold-storage destinations) gives 33,008 kg - SMALLER, not larger, and
        # it is the complement rather than the population. Dropping the
        # destination join entirely gives 135,994 kg. An earlier comment claimed
        # inverting the negation "yields the whole population", which is wrong in
        # both direction and magnitude; the case discriminates either way, but a
        # rationale that misdescribes its own trap misleads the next author.
        "question": (
            "How many kilograms of FOOD_COLD goods have been delivered to warehouses "
            "that are not cold storage?"
        ),
        "oracle_sql": """
            WITH sku_category AS (SELECT DISTINCT sku, category FROM inventory)
            SELECT 'ambient_breach' AS scope,
                   ROUND(SUM(s.quantity_kg), 1) AS breach_kg
            FROM shipments s
            JOIN sku_category c ON c.sku = s.sku
            JOIN locations l ON l.location_id = s.destination_location_id
            WHERE c.category = 'FOOD_COLD' AND s.status = 'DELIVERED'
              AND NOT l.is_cold_storage
        """,
        "top_n": 1,
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
        # "of each severity" is four, not three - LOW (113) was outside the
        # graded window, so dropping it entirely would have scored correct.
        "top_n": 4,
        "exact": True,
        "exact_rows": True,
        "conservation": {
            "sql": "SELECT COUNT(*) FROM alerts WHERE NOT resolved",
            "why": "severity counts must sum to the unresolved total",
        },
    },
    # ----------------------------------------------------------------------
    # Shapes the set above never reaches. Each one was chosen because a naive
    # generated query produces a *plausible* number, not an error.
    # ----------------------------------------------------------------------
    {
        "id": "ff_net_stock_movement",
        "why": "a signed measure, with the sign convention stated - txn_type carries "
               "the sign and quantity_kg is positive for all four types",
        # The scope is spelled out because the unscoped version of this question
        # was not gradeable, in two separate ways found by adversarial review:
        #
        #   1. "which warehouses GAINED the most" - exactly ONE of 20 gained.
        #      Ranks 2 and 3 are -69 and -110 kg: both LOST stock. A product
        #      answering "only Miri East Store gained" returns one row and is the
        #      most accurate answer available, and the gate called it WRONG.
        #   2. whether a WRITE_OFF is stock that "left" is a real choice. It is a
        #      book event, not a movement out of the door, and excluding it is
        #      defensible. That reading changes rank 3 and multiplies rank 1 by
        #      four. It is now ff_net_movement_writeoff_ambiguous.
        #
        # ADJUST is excluded and said so out loud. It is 746 rows and 74,812 kg -
        # 27 pct of all transaction mass - and it is unsigned, so treating it as
        # inbound is a guess rather than a reading. Excluding it is the only
        # non-guessing choice, but it means this measure is a thin residual: 41 kg
        # separates ranks 2 and 3 against ~25,000 kg of gross throughput each.
        # Graded on warehouse NAME, which is the word the question uses and what
        # ff_over_capacity_locations grades on.
        "question": (
            "Counting stock receipts as increases and both issues and write-offs "
            "as decreases, and ignoring inventory adjustments, rank our warehouses "
            "by net kilogram movement, highest first. Top 3."
        ),
        # The naive unsigned SUM(quantity_kg) ranks Sibu River Hub first at
        # 27,829 kg: fifty times larger, about a different quantity, and it reads
        # perfectly well as an answer.
        "oracle_sql": """
            SELECT l.location_name,
                   ROUND(SUM(CASE WHEN t.txn_type = 'IN' THEN t.quantity_kg
                                  WHEN t.txn_type IN ('OUT', 'WRITE_OFF') THEN -t.quantity_kg
                                  ELSE 0 END), 2) AS net_movement_kg
            FROM transactions t
            JOIN locations l ON l.location_id = t.location_id
            GROUP BY l.location_name
            ORDER BY net_movement_kg DESC, l.location_name ASC
        """,
        "top_n": 3,
        # Three separate scalar queries rather than the same CASE expression with
        # the GROUP BY removed. An identity that is the oracle restated can only
        # catch a grouping slip; this one is computed by a different route and so
        # can also catch a mistake inside the CASE.
        "conservation": {
            "sql": (
                "SELECT ROUND((SELECT SUM(quantity_kg) FROM transactions WHERE txn_type = 'IN') "
                "- (SELECT SUM(quantity_kg) FROM transactions WHERE txn_type = 'OUT') "
                "- (SELECT SUM(quantity_kg) FROM transactions WHERE txn_type = 'WRITE_OFF'), 2)"
            ),
            "why": "per-warehouse net movement must sum to receipts minus issues "
                   "minus write-offs, each totalled independently",
        },
    },
    {
        "id": "ff_net_movement_writeoff_ambiguous",
        "why": "the unscoped twin of ff_net_stock_movement - whether a write-off is "
               "stock that 'left' is a definitional choice, not a fact",
        "question": (
            "Which 3 warehouses gained the most stock overall, in kilograms, "
            "counting everything that came in minus everything that left?"
        ),
        "expect": "abstain",
        # Counting write-offs as stock that left gives Miri East Store +554,
        # Taiping Industrial -69, Kemaman Chemical Park -110. Excluding them gives
        # Miri East Store +2,212, Taiping Industrial +1,168, Sibu River Hub +1,052
        # - a different warehouse at rank 3, four times the magnitude at rank 1,
        # and nine warehouses gaining rather than one. Both are honest readings of
        # "everything that left".
        "unanswerable": {
            "kind": "ambiguous",
            "why": "'everything that left' does not settle whether a write-off is a "
                   "stock movement or a book adjustment",
            "reading_a": """
                SELECT l.location_name,
                       ROUND(SUM(CASE WHEN t.txn_type = 'IN' THEN t.quantity_kg
                                      WHEN t.txn_type IN ('OUT', 'WRITE_OFF') THEN -t.quantity_kg
                                      ELSE 0 END), 2) AS net_movement_kg
                FROM transactions t JOIN locations l ON l.location_id = t.location_id
                GROUP BY l.location_name
                ORDER BY net_movement_kg DESC, l.location_name ASC LIMIT 3
            """,
            "reading_b": """
                SELECT l.location_name,
                       ROUND(SUM(CASE WHEN t.txn_type = 'IN' THEN t.quantity_kg
                                      WHEN t.txn_type = 'OUT' THEN -t.quantity_kg
                                      ELSE 0 END), 2) AS net_movement_kg
                FROM transactions t JOIN locations l ON l.location_id = t.location_id
                GROUP BY l.location_name
                ORDER BY net_movement_kg DESC, l.location_name ASC LIMIT 3
            """,
        },
    },
    {
        "id": "ff_network_utilization",
        "why": "ratio of sums vs average of ratios - the classic weighting error, "
               "and both readings look like 'average utilization'",
        "question": "How full is our warehouse network overall, as a percentage of capacity?",
        # A network-level percentage is total load over total capacity: 68.26 pct.
        # AVG(load/capacity) weights every site equally regardless of size and
        # returns 69.69 pct. Capacities run 9,051 to 24,696 kg across 20 sites -
        # a 2.7x spread, which is enough to separate the two readings by 1.43
        # points: far too small to look wrong, far too large to be rounding.
        # There is no additive conservation identity for a ratio, so this case
        # declares none rather than inventing one.
        #
        # The default 0.5 pct tolerance is too loose HERE. The window [67.92,
        # 68.60] admits the recomputed figure for 8 of the 20 possible
        # single-warehouse omissions, so an engine that silently dropped a site
        # from a network total would have been certified green. A network figure
        # missing a site is a P0, so this case tightens the tolerance until the
        # nearest such value (68.21) falls outside it.
        "oracle_sql": """
            SELECT 'network' AS label,
                   ROUND(100.0 * SUM(current_load_kg) / SUM(capacity_kg), 2) AS utilization_pct
            FROM locations
        """,
        "top_n": 1,
        "rel_tol": 0.0005,
    },
    {
        "id": "ff_writeoff_value_by_category",
        "why": "a write-off is a loss, not a sale - same fan-out hazard as the sales "
               "case but over a measure a reader is far less likely to sanity-check",
        # transactions.unit_cost_myr and inventory.unit_cost_myr disagree for all
        # 201 written-off SKUs, and valuing at carrying cost gives a different
        # winner (MACHINERY_PARTS, not MEDICAL) and a different order. The
        # question therefore names which cost it means; without that it belongs
        # with the abstain cases, which is why the sibling cold-store case is
        # denominated in kilograms rather than money.
        "question": (
            "Valued at the unit cost recorded on each write-off transaction, "
            "what is the value in MYR of stock we have written off, by category? "
            "Give me the top 3."
        ),
        # Naive join to inventory inflates ~14.9x: MEDICAL 1,385,709.41 becomes
        # 20,687,536.56. The rank happens to survive here, which is worse, not
        # better - it means the only visible symptom is the magnitude.
        "oracle_sql": """
            SELECT c.category, ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS writeoff_myr
            FROM transactions t
            JOIN (SELECT DISTINCT sku, category FROM inventory) c ON t.sku = c.sku
            WHERE t.txn_type = 'WRITE_OFF'
            GROUP BY c.category
            ORDER BY writeoff_myr DESC, c.category ASC
        """,
        "top_n": 3,
        "conservation": {
            "sql": (
                "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) "
                "FROM transactions WHERE txn_type = 'WRITE_OFF'"
            ),
            "why": "category write-offs must sum to the total written off",
        },
    },
    {
        "id": "ff_expired_stock_value",
        "why": "NULL is not a date, and 'already' is not CURRENT_DATE - two ways to "
               "be wrong that both return a confident number",
        "question": "What is the total value in MYR of inventory that has already expired?",
        # 5,304 of 7,388 lots have no expiry_date at all. Treating NULL as expired
        # returns 498,802,341.72 against a truth of 36,081,286.59 - 13.8x, and it
        # is the natural thing to write if you reach for
        # `WHERE expiry_date IS NULL OR expiry_date < ...`.
        #
        # The cutoff is anchored to MAX(transactions.timestamp), never
        # CURRENT_DATE. Stated honestly, that axis is currently untested: the
        # last expiry before the anchor is 2026-07-30 and the next is 2026-08-30,
        # so a CURRENT_DATE engine returns the identical figure today and this
        # case cannot tell the two apart. It is anchored anyway, because the
        # alternative rots: from 2026-08-31 the two diverge by ~1 pct, and an
        # oracle written against the wall clock would then be asserting a number
        # that changes overnight with no change to the stack. When that date
        # passes, this case starts failing a CURRENT_DATE engine that is
        # arguably right about "already expired" - which is the point at which
        # the question needs a stated as-of date rather than a better oracle.
        "oracle_sql": """
            SELECT 'expired' AS label,
                   ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS expired_value_myr
            FROM inventory
            WHERE expiry_date IS NOT NULL
              AND expiry_date < (SELECT MAX(timestamp)::DATE FROM transactions)
        """,
        "top_n": 1,
    },
    {
        "id": "ff_cheapest_carriers",
        "why": "bottom-N - every other case asks for a top, so nothing yet proves "
               "the stack reads 'least' rather than pattern-matching 'rank by cost'",
        "question": "Which 3 carriers cost us the least in shipping, in MYR?",
        "oracle_sql": """
            SELECT carrier, ROUND(SUM(cost_myr), 2) AS shipping_cost_myr
            FROM shipments GROUP BY carrier
            ORDER BY shipping_cost_myr ASC, carrier ASC
        """,
        "top_n": 3,
        "conservation": {
            "sql": "SELECT ROUND(SUM(cost_myr), 2) FROM shipments",
            "why": "carrier costs must sum to total shipping cost regardless of order",
        },
    },
    {
        "id": "ff_distinct_skus_by_carrier",
        "why": "COUNT DISTINCT rather than COUNT - 'how many different' is the one "
               "phrase where counting rows instead of values is off by 4x",
        # The scope is spelled out because "has shipped" does not settle whether
        # a PENDING or CANCELLED consignment counts. Excluding those two - 2,143
        # of 12,000 rows - inverts the winner to GDEX 489 over City-Link 480, so
        # the unscoped phrasing has two defensible answers and could not be
        # graded against one oracle.
        "question": (
            "Counting every shipment record we hold, whatever its status, how "
            "many different SKUs has each carrier shipped? Top 3."
        ),
        # City-Link 497 distinct SKUs across 2,044 shipments. COUNT(*) returns
        # 2,044 - a plausible-looking number about a different thing. It also
        # reorders the result (City-Link, DHL MY, GDEX rather than City-Link,
        # GDEX, Pos Laju), so the ranking shifts without obviously breaking.
        "oracle_sql": """
            SELECT carrier, COUNT(DISTINCT sku) AS distinct_skus
            FROM shipments GROUP BY carrier
            ORDER BY distinct_skus DESC, carrier ASC
        """,
        "top_n": 3,
        # 497 vs 496 is 0.2 pct apart, inside the default relative tolerance,
        # so without this the gate would accept an off-by-one on a count.
        "exact": True,
    },
    {
        "id": "ff_spend_share_by_status",
        "why": "percent-of-total - the denominator is the whole table, not the group, "
               "and the shares are checkable against 100",
        "question": "What percentage of our shipping spend sits in each shipment status?",
        # Shares by COST are DELIVERED 49.89 / IN_TRANSIT 23.59 / PENDING 15.12.
        # Shares by ROW COUNT are 49.78 / 23.81 / 14.68 - close enough to pass a
        # glance and wrong enough to misstate exposure. Note the first position
        # is INSIDE tolerance (49.78 vs 49.89), so this trap is only caught from
        # position 2 onward, which is why all five statuses are graded.
        #
        # The identity that shares total 100 is deliberately NOT declared as a
        # conservation check: the row-count measure sums to 100 as well, so it
        # would pass on exactly the wrong answer it is supposed to catch. A
        # tautology that cannot fail is worse than no check, because it reads
        # like one.
        "oracle_sql": """
            SELECT status,
                   ROUND(100.0 * SUM(cost_myr) / (SELECT SUM(cost_myr) FROM shipments), 2)
                       AS pct_of_spend
            FROM shipments GROUP BY status
            ORDER BY pct_of_spend DESC, status ASC
        """,
        # "each shipment status" is all five, not the top three. With top_n=3 an
        # engine that silently dropped CANCELLED would have scored correct.
        "top_n": 5,
        "exact_rows": True,
        # "in each status" names no order. Grading position-wise marked a fully
        # correct alphabetical answer WRONG, and named the wrong status in the
        # failure line while doing it.
        "ordered": False,
    },
    {
        "id": "ff_over_capacity_locations",
        "why": "a threshold filter where the answer is the membership list, so "
               "returning too many rows is as wrong as returning a wrong number",
        "question": "Which warehouses are running above 90 percent of their capacity?",
        "oracle_sql": """
            SELECT location_name, ROUND(100.0 * current_load_kg / capacity_kg, 2) AS utilization_pct
            FROM locations
            WHERE current_load_kg > 0.9 * capacity_kg
            ORDER BY utilization_pct DESC, location_name ASC
        """,
        "top_n": 4,
        # The answer is the set. Four warehouses qualify; returning eight is a
        # wrong answer even if the first four are right, and truncating to the
        # oracle's length before comparing would have hidden that. "Which
        # warehouses" asks for membership, not a ranking, so order is not graded.
        "exact_rows": True,
        "ordered": False,
    },
    {
        "id": "ff_alerts_by_severity_and_type",
        "why": "two grouping keys - top-N over a compound grain, where collapsing to "
               "one key still produces a well-formed ranked answer",
        "question": (
            "Break our unresolved alerts down by severity and alert type together. "
            "Which 3 combinations are the most common?"
        ),
        "oracle_sql": """
            SELECT severity || ' / ' || alert_type AS severity_and_type,
                   COUNT(*) AS alert_count
            FROM alerts WHERE NOT resolved
            GROUP BY severity_and_type
            ORDER BY alert_count DESC, severity_and_type ASC
        """,
        "top_n": 3,
        "exact": True,
        "conservation": {
            "sql": "SELECT COUNT(*) FROM alerts WHERE NOT resolved",
            "why": "severity-by-type counts must still sum to the unresolved total",
        },
    },
    # ----------------------------------------------------------------------
    # expect: abstain. There is no defensible confident answer to any of these,
    # so a number is wrong however it was arrived at. Each carries a proof that
    # runs before the case is allowed to judge.
    # ----------------------------------------------------------------------
    {
        "id": "ff_ontime_ambiguous_denominator",
        "why": "the original phrasing of ff_carrier_ontime, kept as the abstain case "
               "it always was - 'on-time percentage' does not say what the denominator is",
        "question": "rank carriers by on-time percentage for hazmat only",
        "expect": "abstain",
        # 6,026 of 12,000 shipments have never arrived; actual_arrival is NULL for
        # every status except DELIVERED. Counting a PENDING shipment as "not on
        # time" is a choice, and it is not the conventional one. Both readings are
        # defensible and they disagree on the winner, so no oracle can grade this
        # phrasing - which is exactly why it must be refused rather than answered.
        "unanswerable": {
            "kind": "ambiguous",
            "why": "the denominator is undetermined: all shipments vs delivered only",
            "reading_a": """
                SELECT s.carrier, ROUND(100.0 * SUM(CASE WHEN s.status = 'DELIVERED'
                       AND s.actual_arrival <= s.expected_arrival THEN 1 ELSE 0 END)
                       / NULLIF(COUNT(*), 0), 2) AS pct
                FROM shipments s
                JOIN (SELECT DISTINCT sku FROM inventory WHERE is_hazardous) h ON s.sku = h.sku
                GROUP BY s.carrier ORDER BY pct DESC, s.carrier ASC LIMIT 3
            """,
            "reading_b": """
                SELECT s.carrier, ROUND(100.0 * SUM(CASE WHEN s.actual_arrival <= s.expected_arrival
                       THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS pct
                FROM shipments s
                JOIN (SELECT DISTINCT sku FROM inventory WHERE is_hazardous) h ON s.sku = h.sku
                WHERE s.status = 'DELIVERED'
                GROUP BY s.carrier ORDER BY pct DESC, s.carrier ASC LIMIT 3
            """,
        },
    },
    {
        "id": "ff_outbound_by_supplier_country",
        "why": "the dimension does not exist at the grain the question assumes - a SKU "
               "has many suppliers, so 'sales for Malaysian suppliers' has no referent",
        "question": (
            "What was our total outbound sales value in MYR for goods from "
            "Malaysian suppliers?"
        ),
        "expect": "abstain",
        # 500 of 509 SKUs are stocked from suppliers in more than one country, and
        # transactions carries no supplier_id at all. So attributing an outbound
        # transaction to a supplier country requires a rule the warehouse does not
        # contain, and the rule chosen decides the answer:
        #
        #   SKUs sourced ONLY from Malaysia (8 SKUs)      ->        11,468.14
        #   SKUs with ANY Malaysian supplier (508 SKUs)   ->    80,375,993.99
        #
        # Seven thousand times apart, both arrived at honestly. Note the second
        # equals the unfiltered outbound total exactly, because all but one SKU
        # has some Malaysian supplier - so that reading's filter removes nothing,
        # which is itself the point: neither reading is informative and the
        # question needs a definition, not a better query.
        #
        # Both readings carry the SAME label on purpose. An earlier version gave
        # them different labels, which made the retirement guard in
        # prove_unanswerable unreachable: it compares result tuples, so rows that
        # can never be equal can never be shown to have converged.
        "unanswerable": {
            "kind": "ambiguous",
            "why": "sku-to-supplier is many-to-many, so supplier country is not a "
                   "property of an outbound transaction",
            "reading_a": """
                SELECT 'malaysian_supplier_outbound' AS label,
                       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS v
                FROM transactions t
                WHERE t.txn_type = 'OUT' AND t.sku IN (
                    SELECT i.sku FROM inventory i
                    JOIN suppliers s ON i.supplier_id = s.supplier_id
                    GROUP BY i.sku
                    HAVING COUNT(DISTINCT s.country) = 1 AND MAX(s.country) = 'Malaysia')
            """,
            "reading_b": """
                SELECT 'malaysian_supplier_outbound' AS label,
                       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS v
                FROM transactions t
                WHERE t.txn_type = 'OUT' AND t.sku IN (
                    SELECT DISTINCT i.sku FROM inventory i
                    JOIN suppliers s ON i.supplier_id = s.supplier_id
                    WHERE s.country = 'Malaysia')
            """,
        },
    },
    {
        "id": "ff_hazardous_medical_value",
        "why": "the filter parses, validates, executes and matches nothing - hard rule "
               "12's headline shape, where the wrong answer is a confident 0.00",
        "question": "What is the total stock value in MYR of hazardous medical supplies?",
        "expect": "abstain",
        # is_hazardous is true for CHEMICALS lots and nothing else: 299 of 725
        # CHEMICALS lots, and 0 lots in each of the other nine categories.
        #
        # "No such stock exists" is a good answer and this case accepts it - as an
        # abstention, which is how the envelope carries "I looked and there is
        # nothing to state". What it rejects is the same fact rendered as a
        # confident figure: "MYR 0.00" under a green badge is the same characters
        # a reader would see if the business held stock worth nothing, and they
        # cannot tell the two apart. The naive aggregate returns NULL rather than
        # 0 here, so this is a claim about what the product renders, not about
        # what SQL returns.
        "unanswerable": {
            "kind": "empty",
            "why": "no inventory lot is both hazardous and MEDICAL",
            "sql": "SELECT COUNT(*) FROM inventory WHERE is_hazardous AND category = 'MEDICAL'",
            "expect_value": 0,
        },
    },
    {
        "id": "ff_profit_margin_by_category",
        "why": "a false premise - the warehouse holds costs, never a selling price, "
               "so margin is not a number that is being hidden, it does not exist",
        "question": "What is our profit margin by product category? Show me the top 3.",
        "expect": "abstain",
        # unit_cost_myr is a cost on both transactions and inventory. Nothing in
        # any table records what anything sold for. A stack that answers this has
        # necessarily redefined "margin" into something else and not said so.
        "unanswerable": {
            "kind": "absent",
            "why": "no column in any table records price, revenue, margin or profit",
            # Stated honestly, this is a spelling test: it proves no column is
            # NAMED like a selling price, not that none exists under another
            # name. Broadened past the obvious four because a warehouse growing
            # a `unit_sale_myr` column would silently keep this case demanding a
            # refusal the product could by then answer correctly (R-0005). The
            # structural claim it stands for - that every money column here is a
            # cost - cannot be expressed in information_schema, so this is the
            # closest executable proxy and is labelled as one.
            "sql": (
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE lower(column_name) LIKE '%profit%' "
                "OR lower(column_name) LIKE '%margin%' "
                "OR lower(column_name) LIKE '%price%' "
                "OR lower(column_name) LIKE '%revenue%' "
                "OR lower(column_name) LIKE '%sale%' "
                "OR lower(column_name) LIKE '%sell%' "
                "OR lower(column_name) LIKE '%invoice%'"
            ),
            "expect_value": 0,
        },
    },
    {
        "id": "ff_month_over_month_outbound",
        "why": "the comparison the question asks for needs two periods and the "
               "warehouse holds one - an answer here is arithmetic on a single point",
        "question": "How did our outbound sales value change month over month?",
        "expect": "abstain",
        # transactions runs 2026-07-01 to 2026-07-31 inclusive: exactly one
        # calendar month. Any month-over-month figure is either a comparison
        # against an empty period or a silent redefinition of "month".
        "unanswerable": {
            "kind": "absent",
            "why": "transactions covers exactly one calendar month, so there is no "
                   "prior month to compare against",
            "sql": (
                "SELECT COUNT(DISTINCT date_trunc('month', timestamp)) - 1 "
                "FROM transactions"
            ),
            "expect_value": 0,
        },
    },
]


class OracleBroken(Exception):
    """The oracle failed its own consistency check, so it cannot judge anything."""


def expects_abstention(case: dict[str, Any]) -> bool:
    return str(case.get("expect", "answer")) == "abstain"


def prove_unanswerable(case: dict[str, Any], warehouse: Path) -> str:
    """Prove, against the warehouse, that the question really has no right answer.

    An abstain case demands a refusal. That is a strong demand, and it must not
    rest on the author having been sure. Data changes: a filter that matches
    nothing today can match something next quarter, two readings that disagree
    today can converge, and a column that does not exist can be added. In every
    one of those cases the question stops being unanswerable, and a gate that
    kept demanding a refusal would be punishing the stack for getting better -
    R-0005, in the direction that is hardest to notice.

    So the proof runs first, and a proof that stops holding fails the run loudly
    rather than quietly grading anybody.
    """
    import duckdb

    spec = case.get("unanswerable")
    if not isinstance(spec, dict):
        raise OracleBroken(f"{case['id']}: expect=abstain with no unanswerable proof")

    kind = str(spec.get("kind"))
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        if kind == "ambiguous":
            a = con.execute(" ".join(str(spec["reading_a"]).split())).fetchall()
            b = con.execute(" ".join(str(spec["reading_b"]).split())).fetchall()
            if not a or not b:
                raise OracleBroken(
                    f"{case['id']}: a reading returned nothing, so the two cannot be "
                    "shown to disagree. Fix the readings before demanding a refusal."
                )
            if a == b:
                raise OracleBroken(
                    f"{case['id']}: the two readings now AGREE ({a[:2]}). The question "
                    "is no longer ambiguous, so refusing it is no longer correct. "
                    "Retire this case or re-derive the ambiguity."
                )
            return f"readings disagree: {a[0]} vs {b[0]}"

        if kind in {"empty", "absent"}:
            row = con.execute(" ".join(str(spec["sql"]).split())).fetchone()
            if row is None:
                raise OracleBroken(
                    f"{case['id']}: the unanswerability proof returned no row at all, "
                    "so it proves nothing. Fix the proof before demanding a refusal."
                )
            got = row[0]
            want = spec.get("expect_value", 0)
            if got != want:
                raise OracleBroken(
                    f"{case['id']}: proof returned {got!r}, expected {want!r}. "
                    f"{spec.get('why')} is no longer true of this warehouse, so the "
                    "question may now be answerable. Retire or re-derive this case."
                )
            return f"{kind} proven: {spec.get('why')}"

        raise OracleBroken(f"{case['id']}: unknown unanswerable kind {kind!r}")
    finally:
        con.close()


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

    What it cannot see, stated so nobody over-trusts it: an additive identity
    constrains the TOTAL, not the grouping. Re-grouping an oracle by the wrong
    dimension - by operator_id instead of category, by carrier instead of
    country - still sums to the same total and still passes. It catches
    fan-out, dropped filters and arithmetic slips. It does not catch answering
    a different question with the same rows.
    """
    import duckdb

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        rows = con.execute(" ".join(str(case["oracle_sql"]).split())).fetchall()
        # The oracle names its own measure. Recording it here means the gate
        # can find that column in an engine's answer instead of grading
        # whichever numeric column happens to come first.
        if con.description and len(con.description) > 1:
            case.setdefault("measure", str(con.description[1][0]))
        cons = case.get("conservation")
        if cons:
            row = con.execute(" ".join(str(cons["sql"]).split())).fetchone()
            expected = row[0] if row else None
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


def _close(
    a: float, b: float, *, exact: bool = False, rel_tol: float | None = None
) -> bool:
    """Tolerant by default, exact for counts.

    The relative tolerance exists because money is computed in a different
    order by two engines and lands a fraction of a cent apart. Applied to a
    count it is a hole: 0.5 pct of any count above 200 is more than one, so
    ``_close(496, 497)`` is True and the gate accepts an off-by-one. A count is
    not approximate - there were 497 SKUs or there were not - so a case whose
    measure is a count declares ``exact`` and is compared without tolerance.
    """
    if exact:
        return abs(a - b) < 1e-9
    rel = REL_TOL if rel_tol is None else rel_tol
    return abs(a - b) <= ABS_TOL or abs(a - b) / max(abs(b), 1e-9) <= rel


def _norm(value: Any) -> str:
    """Fold a label to a comparable token: case, spacing and punctuation are noise."""
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _numeric_columns(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for k, v in row.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in seen:
                seen.append(k)
    return seen


def _string_columns(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for k, v in row.items():
            if isinstance(v, str) and v.strip() and k not in seen:
                seen.append(k)
    return seen


def _split_tokens(value: Any) -> set[str]:
    """Fold one label cell into its parts.

    Both sides of a label comparison go through here, which is the whole point.
    A composite grain is written as a concatenation because this gate reads one
    label column, and an engine may return it either way: as two columns
    ("MEDIUM", "LOW_STOCK") or as one already-joined string ("MEDIUM /
    LOW_STOCK"). Splitting only the oracle's side made the second form fail -
    caught by --self-check, not by review.
    """
    return {_norm(part) for part in str(value).split("/") if _norm(part)}


def _row_tokens(row: dict[str, Any], keys: list[str]) -> frozenset[str]:
    """Every string cell in the row, folded. Order and separator are not the answer."""
    out: set[str] = set()
    for k in keys:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            out |= _split_tokens(v)
    return frozenset(out)


def _oracle_tokens(label: str) -> frozenset[str]:
    """The oracle's label, folded exactly as a claimed label is."""
    return frozenset(_split_tokens(label))


def _column_values(rows: list[dict[str, Any]], col: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r.get(col)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    return out


def resolve_measure_column(
    rows: list[dict[str, Any]],
    expected: list[tuple[str, float]],
    *,
    measure: str | None = None,
    exact: bool = False,
    rel_tol: float | None = None,
) -> tuple[str | None, str]:
    """Decide which numeric column carries the claim, and say how it was decided.

    Grading the *first* numeric column is what the earlier version did, and it
    rejects correct answers routinely: an engine that returns
    ``{status, total_cost_myr, pct_of_spend}`` is graded on the cost while the
    oracle holds the percentage. Returning supporting columns beside the measure
    is ordinary SQL, not a defect, and a gate that cannot read past column one
    fails the product for the instrument's convenience (R-0005).

    Resolution order, most trustworthy first. The last rule never launders a
    wrong answer: it can only select a column that already equals the oracle.
    """
    nums = _numeric_columns(rows)
    if not nums:
        return None, "no numeric column"
    if measure:
        want = _norm(measure)
        for col in nums:
            if _norm(col) == want:
                return col, "named by the oracle"
    if len(nums) == 1:
        return nums[0], "the only numeric column"
    matching = [
        col
        for col in nums
        if len(_column_values(rows, col)) >= len(expected)
        and all(
            _close(g, w, exact=exact, rel_tol=rel_tol)
            for g, (_, w) in zip(_column_values(rows, col), expected)
        )
    ]
    if len(matching) == 1:
        return matching[0], f"matched the oracle ({matching[0]!r})"
    return nums[0], "first numeric column (nothing matched)"


def claimed_ranking(
    env: dict[str, Any],
    expected: list[tuple[str, float]] | None = None,
    *,
    measure: str | None = None,
    exact: bool = False,
    rel_tol: float | None = None,
) -> tuple[list[tuple[frozenset[str], str, float]] | None, str]:
    """Read the claim the envelope actually asserts, from executed rows.

    Rows, not prose: E9 guarantees a non-abstained numeric answer carries
    executed rows, so rows are the honest place to read a claim from. Row order
    is the ranking - a top-N whose ORDER BY is wrong is wrong even when every
    figure is individually right.

    Returns ``(claim, how)`` where each claim entry is
    ``(label tokens, label shown to a human, value)``.
    """
    rows = [r for r in (env.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        return None, "no executed rows"

    col, how = resolve_measure_column(
        rows, expected or [], measure=measure, exact=exact, rel_tol=rel_tol
    )
    if col is None:
        return None, "rows carried no numeric column to read a figure from"

    dims = _string_columns(rows)
    out: list[tuple[frozenset[str], str, float]] = []
    for r in rows:
        val = r.get(col)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        tokens = _row_tokens(r, dims)
        shown = " / ".join(str(r[k]) for k in dims if isinstance(r.get(k), str)) or "value"
        out.append((tokens, shown, float(val)))
    return (out or None), how


def judge(
    env: dict[str, Any],
    expected: list[tuple[str, float]],
    *,
    expect_abstain: bool = False,
    exact: bool = False,
    exact_rows: bool = False,
    ordered: bool = True,
    measure: str | None = None,
    rel_tol: float | None = None,
) -> tuple[str, str]:
    """abstained | correct | WRONG. Only the last two touch precision.

    ``expect_abstain`` inverts what abstention means. On an ordinary case a
    refusal is free - it costs coverage and nothing else. On a case whose proof
    shows the question has no right answer, the refusal *is* the right answer,
    and a confident reply is wrong however plausible its number.

    ``ordered`` is False for a question that names no ranking - "what percentage
    sits in each status", "which warehouses are above 90 pct". The answer is a
    set, and marking a correct set wrong because it came back alphabetically is
    the gate inventing a requirement the question never had.
    """
    abstained = bool(env.get("abstained"))
    badge = env.get("badge")

    # E1, asserted here and not only in the envelope builder: abstained and
    # badge must agree. A refusal under a confident badge is the P0 shape
    # CLAUDE.md 10a names, and scoring it "correct" because the flag was set
    # would let the customer-visible half of the defect through untouched.
    if abstained and str(badge).upper() not in {"ABSTAIN", "NONE", ""}:
        return "WRONG", f"abstained=true under a confident badge [{badge}] - E1 violation"

    if abstained:
        return ("correct" if expect_abstain else "abstained"), str(env.get("text") or "")[:110]

    claimed, how = claimed_ranking(
        env, expected, measure=measure, exact=exact, rel_tol=rel_tol
    )

    if expect_abstain:
        shown = (
            ", ".join(f"{lbl}={v:,.2f}" for _, lbl, v in claimed[:3])
            if claimed
            else str(env.get("text") or "")[:90]
        )
        # Two very different products both fail here, and the operator needs to
        # tell them apart at a glance: one guessed, and one picked a reading and
        # said so. Naming an assumption is not currently enough to pass - whether
        # it should be is a product decision, not this gate's to make - but a run
        # that cannot show which happened is not worth much in a review.
        stated = [str(a) for a in (env.get("assumptions") or []) if a]
        note = (
            f" - assumption stated: {stated[0][:70]!r}"
            if stated
            else " - no assumption stated, so this is a guess"
        )
        return "WRONG", f"answered an unanswerable question under [{badge}]: {shown}{note}"

    if claimed is None:
        return "WRONG", f"confident badge but {how}"

    if exact_rows and len(claimed) != len(expected):
        return "WRONG", (
            f"returned {len(claimed)} rows, the complete answer has {len(expected)}"
        )

    if not ordered:
        return _judge_as_set(claimed, expected, exact=exact, rel_tol=rel_tol, how=how)

    claimed = claimed[: len(expected)]
    if len(claimed) < len(expected):
        return "WRONG", f"returned {len(claimed)} rows, oracle has {len(expected)}"

    # Magnitudes first: a right ranking over wrong numbers is the F26 shape.
    for (_, lbl, got), (key, want) in zip(claimed, expected):
        if not _close(got, want, exact=exact, rel_tol=rel_tol):
            return "WRONG", (
                f"{key}: answered {got:,.2f}, oracle {want:,.2f} (read {lbl!r}, {how})"
            )

    # Then order, but only when the oracle actually has a dimension to rank by.
    if len(expected) > 1:
        exp_order = [_oracle_tokens(k) for k, _ in expected]
        got_order = [t for t, _, _ in claimed]
        if any(e for e in exp_order) and exp_order != got_order:
            shown_e = [" ".join(sorted(e)) for e in exp_order]
            shown_g = [" ".join(sorted(g)) for g in got_order]
            return "WRONG", f"rank order {shown_g} != oracle {shown_e}"

    return "correct", ", ".join(f"{lbl}={v:,.2f}" for _, lbl, v in claimed)


def _judge_as_set(
    claimed: list[tuple[frozenset[str], str, float]],
    expected: list[tuple[str, float]],
    *,
    exact: bool,
    rel_tol: float | None,
    how: str,
) -> tuple[str, str]:
    """Grade an unordered answer by matching labels, never by position.

    Zipping positionally against an unordered answer produces a false
    diagnostic as well as a false verdict: it reports "DELIVERED: answered
    3.06" when 3.06 was CANCELLED's correct figure and DELIVERED was right.
    """
    by_token = {t: (lbl, v) for t, lbl, v in claimed}
    for key, want in expected:
        tokens = _oracle_tokens(key)
        hit = by_token.get(tokens)
        if hit is None:
            hit = next(((lbl, v) for t, (lbl, v) in by_token.items() if t & tokens), None)
        if hit is None:
            return "WRONG", f"{key}: missing from the answer ({how})"
        lbl, got = hit
        if not _close(got, want, exact=exact, rel_tol=rel_tol):
            return "WRONG", f"{key}: answered {got:,.2f}, oracle {want:,.2f} (read {lbl!r})"
    return "correct", ", ".join(f"{k}={v:,.2f}" for k, v in expected)


def _envelope_from(
    truth: list[tuple[str, float]], case: dict[str, Any], *, badge: str = "L2_VALIDATED"
) -> dict[str, Any]:
    """The answer a perfect engine would return for this case, shaped like a real one."""
    measure = str(case.get("measure") or "value")
    rows = [{"label": k, measure: v} for k, v in truth]
    return {"badge": badge, "abstained": False, "rows": rows, "text": "answer", "values": []}


def _perturb(value: float, case: dict[str, Any]) -> float:
    """Move a figure just far enough that a correct gate must reject it."""
    if case.get("exact"):
        return value + 1
    rel = float(case.get("rel_tol") or REL_TOL)
    return value + max(ABS_TOL * 10.0, abs(value) * rel * 4.0, 1e-6)


def self_check(cases: list[tuple[dict[str, Any], list[tuple[str, float]]]]) -> int:
    """Prove every shipped case can both pass and fail. R-0007, applied to the set.

    A gate is only worth its exit code if each case accepts the right answer and
    rejects a wrong one. Both halves have failed here before, and neither was
    visible by reading the file:

      * cases that could not fail - a conservation identity of ``SELECT 100.0``
        is satisfied by the wrong measure as readily as the right one;
      * cases that rejected their own truth - grading the first numeric column,
        demanding the oracle's private label separator, or enforcing an order the
        question never asked for.

    This runs the real oracles through the real ``judge``, so it needs the
    warehouse but not a live stack. It never asks the product anything.
    """
    print("\n=== SELF-CHECK (can each case pass, and can each case fail?) ===")
    broken: list[str] = []

    for case, truth in cases:
        cid = str(case["id"])
        exact = bool(case.get("exact"))
        exact_rows = bool(case.get("exact_rows"))
        ordered = bool(case.get("ordered", True))
        measure = case.get("measure")
        rel_tol = case.get("rel_tol")
        probes: list[tuple[str, dict[str, Any], str]] = []

        if expects_abstention(case):
            refused = {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "refused"}
            guessed = {
                "badge": "L2_VALIDATED",
                "abstained": False,
                "rows": [{"label": "x", "value": 1.0}],
                "text": "answer",
            }
            green_refusal = {
                "badge": "L2_VALIDATED",
                "abstained": True,
                "rows": [],
                "text": "refused",
            }
            probes = [
                ("refusal accepted", refused, "correct"),
                ("guess rejected", guessed, "WRONG"),
                ("refusal under a green badge rejected", green_refusal, "WRONG"),
            ]
            for name, env, want in probes:
                got, detail = judge(
                    env,
                    truth,
                    expect_abstain=True,
                    exact=exact,
                    exact_rows=exact_rows,
                    ordered=ordered,
                    measure=measure,
                    rel_tol=rel_tol,
                )
                if got != want:
                    broken.append(f"{cid}: {name} - judge said {got} ({detail})")
            print(f"  {cid:<34} abstain probes: {len(probes)}")
            continue

        probes.append(("oracle answer accepted", _envelope_from(truth, case), "correct"))

        wrong = list(truth)
        wrong[0] = (wrong[0][0], _perturb(wrong[0][1], case))
        probes.append(("perturbed figure rejected", _envelope_from(wrong, case), "WRONG"))

        if len(truth) > 1 and ordered:
            swapped = [truth[1], truth[0], *truth[2:]]
            probes.append(("swapped ranking rejected", _envelope_from(swapped, case), "WRONG"))

        if exact_rows and len(truth) > 1:
            probes.append(
                ("short answer rejected", _envelope_from(truth[:-1], case), "WRONG")
            )
            padded = [*truth, ("extra", truth[-1][1] / 2.0)]
            probes.append(("padded answer rejected", _envelope_from(padded, case), "WRONG"))

        # A real engine returns supporting columns beside the measure. It must
        # still pass: rejecting it is R-0005, and it did until this round.
        noisy = _envelope_from(truth, case)
        for row in noisy["rows"]:
            row["row_count_seen"] = 12345
        probes.append(("supporting column tolerated", noisy, "correct"))

        for name, env, want in probes:
            got, detail = judge(
                env,
                truth,
                exact=exact,
                exact_rows=exact_rows,
                ordered=ordered,
                measure=measure,
                rel_tol=rel_tol,
            )
            if got != want:
                broken.append(f"{cid}: {name} - judge said {got} ({detail})")
        print(f"  {cid:<34} probes: {len(probes)}")

    if broken:
        print(f"\nFAIL self-check: {len(broken)} case probes behaved wrongly.")
        for b in broken:
            print(f"  - {b}")
        print("\n  A case that cannot fail certifies nothing. A case that rejects its")
        print("  own oracle will reject a correct product (R-0005). Fix before running live.")
        return 1

    print("\nPASS every case accepts its oracle and rejects a wrong answer.")
    return 0


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
    ap.add_argument("--self-check", action="store_true",
                    help="prove every case can pass AND fail, then stop (no stack needed)")
    ap.add_argument("--list", action="store_true", help="print the demo set and stop")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args(argv)

    if args.list:
        for c in DEMO_SET:
            tag = "MUST ABSTAIN" if expects_abstention(c) else "answerable"
            print(f"  {c['id']:<34} [{tag}] {c['why']}")
            print(f"  {'':<34} {c['question']}")
        return 0
    if not args.oracle_only and not args.self_check and not args.space:
        ap.error("--space is required unless --oracle-only or --self-check is given")
    if not args.warehouse.is_file():
        print(f"FAIL warehouse not found: {args.warehouse}")
        print("     start the stack once so the demo warehouse is built")
        return 2

    print("=== ORACLE (recomputed now, direct DuckDB, independent of the answer path) ===")
    cases: list[tuple[dict[str, Any], list[tuple[str, float]]]] = []
    for case in DEMO_SET:
        cid = str(case["id"])
        if expects_abstention(case):
            try:
                proof = prove_unanswerable(case, args.warehouse)
            except Exception as exc:  # noqa: BLE001
                print(f"  {cid:<34} PROOF ERROR {type(exc).__name__}: {exc}")
                return 2
            cases.append((case, []))
            print(f"  {cid:<34} MUST ABSTAIN - {proof}")
            print(f"  {'':<34} why: {case['why']}")
            continue
        try:
            truth = oracle(case, args.warehouse)
        except Exception as exc:  # noqa: BLE001
            print(f"  {cid:<34} ORACLE ERROR {type(exc).__name__}: {exc}")
            return 2
        if not truth:
            print(f"  {cid:<34} ORACLE EMPTY - the case cannot judge anything")
            return 2
        cases.append((case, truth))
        print(f"  {cid:<34} {'  '.join(f'{k}={v:,.2f}' for k, v in truth)}")
        print(f"  {'':<34} why: {case['why']}")

    if args.self_check:
        return self_check(cases)

    if args.oracle_only:
        return 0

    print(f"\n=== ANSWERS (space={args.space}) ===")
    answered = correct = wrong = 0
    refusals_due = refusals_kept = 0
    failures: list[str] = []
    abstentions: list[str] = []

    for case, truth in cases:
        cid = str(case["id"])
        want_abstain = expects_abstention(case)
        if want_abstain:
            refusals_due += 1
        try:
            env = ask_live(str(case["question"]), str(args.space), args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"  {cid:<34} ERROR  {type(exc).__name__}: {str(exc)[:80]}")
            failures.append(f"{cid}: ask failed ({type(exc).__name__})")
            continue

        outcome, detail = judge(
            env,
            truth,
            expect_abstain=want_abstain,
            exact=bool(case.get("exact")),
            exact_rows=bool(case.get("exact_rows")),
            ordered=bool(case.get("ordered", True)),
            measure=case.get("measure"),
            rel_tol=case.get("rel_tol"),
        )
        badge = env.get("badge")
        print(f"  {cid:<34} {outcome:<10} [{badge}] {detail}")
        if outcome == "abstained":
            abstentions.append(cid)
            continue
        if want_abstain:
            # A correctly refused question is not "answered" - it must never
            # flatter precision-on-answered, which is a claim about figures.
            if outcome == "correct":
                refusals_kept += 1
            else:
                wrong += 1
                failures.append(f"{cid}: {detail}")
            continue
        answered += 1
        if outcome == "correct":
            correct += 1
        else:
            wrong += 1
            failures.append(f"{cid}: {detail}")

    total = len(cases)
    answerable = total - refusals_due
    precision = (correct / answered * 100.0) if answered else 100.0
    coverage = answered / answerable * 100.0 if answerable else 0.0
    refusal_precision = (
        refusals_kept / refusals_due * 100.0 if refusals_due else 100.0
    )

    print("\n=== RESULT ===")
    print(f"  precision-on-answered  {precision:6.2f} pct   ({correct}/{answered})")
    print(f"  coverage               {coverage:6.2f} pct   ({answered}/{answerable})")
    print(f"  refusal-precision      {refusal_precision:6.2f} pct   "
          f"({refusals_kept}/{refusals_due} unanswerable questions refused)")
    print(f"  demo set               {total} curated questions "
          f"({answerable} answerable, {refusals_due} must-abstain)"
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
