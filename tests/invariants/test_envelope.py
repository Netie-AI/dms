"""Phase 0 — customer envelope invariants E1–E9 + single-constructor AST gate.

INVARIANT-CHANGE: Phase 0 envelope mapping — abstain must not stamp L2_VALIDATED.
INVARIANT-CHANGE: E9 (F26) — a path that executed no query may quote a figure a
cited snippet contains, but may never render one it computed.
INVARIANT-CHANGE: hard rule 12 — executed SQL with zero rows demotes to ABSTAIN
(empty-filter green is a P0; COUNT returning a zero cell still has a row).
INVARIANT-CHANGE: E9-02 (F32) — ambiguous multi-sheet / multi-file category
ranking must not stay confident green when competing Sales vs Wide_Fill (or
cross-file sales) scopes disagree; uniquely scoped executed ranks may certify.
INVARIANT-CHANGE: E11 (FF-02) — a negated ask answered by the inverse
predicate (NOT cold storage -> is_cold_storage = TRUE) must not stay
L1_GOVERNED_METRIC; refuse or answer the complement, never the inverse
under a success badge.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cortex_client.models import AskResponse
from dms_executor import map_ask_response_to_envelope
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_CONSTRUCTOR = ROOT / "packages" / "executor" / "dms_executor" / "envelope.py"

_F32_COMPETING = [
    "bronze.aa64458a_p50_03_inventory_messy_Sales",
    "bronze.aa64458a_p50_03_inventory_messy_Wide_Fill",
]
_WIDE_FILL_CLASS = (383803.56, 242755.97, 228548.84)
_SALES_CLASS = (1545366.40, 1199018.49, 380948.33)


def test_e1_abstain_lockstep():
    env = build_answer_envelope(
        answer_id="a1",
        text="Cannot answer.",
        badge="ABSTAIN",
        abstained=True,
        ask_mode="demo",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["values"] == []
    assert env["contributing_sources"] == []
    assert env["drillthrough_token"] is None


def test_e1_rejects_mismatched_badge():
    env = build_answer_envelope(
        answer_id="a1",
        text="Cannot answer.",
        badge="L2_VALIDATED",
        abstained=True,
        ask_mode="demo",
    )
    assert env["badge"] == "ABSTAIN"
    assert_envelope_valid(env)


def test_needs_clarification_maps_to_abstain_envelope():
    """Regression for the Phase 0 defect: Cortex abstain stamped L2_VALIDATED."""
    raw = {
        "answer": "I can't answer that from the DMS semantic layer with confidence.",
        "audit_id": "aud_nc",
        "route": "needs_clarification",
        "provenance": {"badge": "abstain", "layer": "abstain"},
        "sql_used": None,
        "rows": [],
    }
    resp = AskResponse.model_validate(raw)
    assert resp.abstained is True
    env = map_ask_response_to_envelope(resp, session_id="ses_x")
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert_envelope_valid(env)


def test_session_badge_without_abstain_still_l2_when_answered():
    raw = {
        "answer": "Top SKU is A at 12.5.",
        "audit_id": "aud_ok",
        "route": "sql",
        "provenance": {"badge": "session", "layer": "session"},
        "sql_used": "SELECT 1",
        "rows": [{"sku": "A", "revenue_myr": 12.5}],
    }
    resp = AskResponse.model_validate(raw)
    assert resp.abstained is False
    env = map_ask_response_to_envelope(resp, session_id="ses_x")
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert_envelope_valid(env)


def test_e6_demo_fallback_requires_banner():
    env = build_answer_envelope(
        answer_id="a1",
        text="Total was 10.00.",
        badge="L2_VALIDATED",
        values=[{"id": "v0", "value": 10.0, "label": "total"}],
        sql_used="SELECT 10",
        rows=[{"total": 10}],
        ask_mode="demo",
        demo_fallback_used=True,
    )
    assert env.get("demo_fallback_banner") is True
    assert env["abstained"] is False
    assert_envelope_valid(env)


def _doc_rag_response(answer: str, snippet: str, **extra):
    """The AirGPT failure shape: retrieval route, no query, prose figures."""
    return AskResponse.model_validate(
        {
            "answer": answer,
            "audit_id": "aud_f26",
            "route": "doc_rag",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "drillthrough_token": "dt_f26_token",
            "contributing_sources": [
                {
                    "ref_id": "src_wide_fill",
                    "filename": "aa64458a_p50_03_inventory_messy.xlsx",
                    "contribution_pct": 100,
                    "content": snippet,
                    "chunk_index": 0,
                }
            ],
            **extra,
        }
    )


def test_e9_computed_totals_from_prose_never_certify():
    """F26 regression, with the figures actually shown to the client.

    AirGPT reported Home 383,803.56 / Sports 242,755.97 / Misc 228,548.84 citing
    Wide_Fill. Measured truth for that sheet is 2,223,118.16 / 2,358,800.10 /
    2,691,552.10 — wrong magnitudes and wrong ranking. No contiguous row window
    of any sheet in any of the eight workbooks sums to the reported values: the
    model added up a retrieved sample. Nothing here executed a query, so nothing
    here may state a total.
    """
    resp = _doc_rag_response(
        "Top 3 category sales are Home 383,803.56 MYR, Sports 242,755.97 MYR "
        "and Misc 228,548.84 MYR.",
        snippet="Wide_Fill rows for SKU-00260, SKU-00159 and SKU-00185.",
        rows=[{"excerpt": "Wide_Fill sheet rows"}],
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_demo", session_id="ses_f26")

    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    for fabricated in ("383,803.56", "242,755.97", "228,548.84"):
        assert fabricated not in env["text"], (
            f"E9: refusal still renders the uncertified figure {fabricated}"
        )
    assert_envelope_valid(env)


def test_e9_allows_a_figure_the_cited_snippet_actually_contains():
    """R-0005 — a control that refuses legitimate work is a failure.

    Quoting a number a document states is extractive and traceable. Only the
    computed number is barred.
    """
    resp = _doc_rag_response(
        "The contract sets the late-delivery penalty at 5,000.00 MYR.",
        snippet="Clause 7.2: a penalty of 5,000.00 MYR applies per late delivery.",
        rows=[{"excerpt": "Clause 7.2"}],
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_legal", session_id="ses_ok")

    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert "5,000.00" in env["text"]
    assert_envelope_valid(env)


def test_e9_leaves_executed_sql_answers_alone():
    """An executed query is exactly the authority E9 asks for."""
    env = build_answer_envelope(
        answer_id="a_sql",
        text="Home revenue is 1,199,018.49 MYR.",
        badge="L2_VALIDATED",
        values=[{"id": "v0", "value": 1199018.49, "label": "sales_value_myr"}],
        sql_used="SELECT category, SUM(sales_value_myr) FROM sales GROUP BY category",
        rows=[{"category": "Home", "sales_value_myr": 1199018.49}],
        ask_mode="demo",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert_envelope_valid(env)


def test_f32_ambiguous_ranking_demotes_wide_fill_class_totals():
    """F32: ambiguous ask + competing Sales/Wide_Fill must not stay green.

    Planted Wide_Fill-class ranking under a confident badge with executed SQL
    on one non-disambiguated sheet — the demote must fire. Removing the E9-02
    guard makes this fixture fail red.
    """
    home, sports, misc = _WIDE_FILL_CLASS
    env = build_answer_envelope(
        answer_id="a_f32",
        text=(
            f"Top 3 category sales are Home {home:,.2f} MYR, "
            f"Sports {sports:,.2f} MYR and Misc {misc:,.2f} MYR."
        ),
        badge="L2_VALIDATED",
        values=[
            {"id": "v0", "value": home, "label": "sales_value_myr"},
            {"id": "v1", "value": sports, "label": "sales_value_myr"},
            {"id": "v2", "value": misc, "label": "sales_value_myr"},
        ],
        sql_used=(
            "SELECT category, SUM(sales_value_myr) AS sales_value_myr "
            "FROM bronze.aa64458a_p50_03_inventory_messy_Wide_Fill "
            "GROUP BY category ORDER BY 2 DESC LIMIT 3"
        ),
        rows=[
            {"category": "Home", "sales_value_myr": home},
            {"category": "Sports", "sales_value_myr": sports},
            {"category": "Misc", "sales_value_myr": misc},
        ],
        question="show top 3 categoty sales",
        competing_scopes=_F32_COMPETING,
        grounded_tables=_F32_COMPETING,
        ask_mode="live",
    )
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "scope conflict" in env["text"].lower()
    for n in ("383,803.56", "242,755.97", "228,548.84"):
        assert n not in env["text"]
    assert any("E9-02" in a or "F32" in a for a in env["assumptions"])
    assert_envelope_valid(env)


def test_f32_unique_sales_scope_may_certify_executed_totals():
    """Uniquely scoped Sales ask with executed rows may stay confident."""
    elec, home, misc = _SALES_CLASS
    env = build_answer_envelope(
        answer_id="a_f32_ok",
        text=(
            f"Top 3 on Sales: Electronics {elec:,.2f}, "
            f"Home {home:,.2f}, Misc {misc:,.2f}."
        ),
        badge="L2_VALIDATED",
        values=[
            {"id": "v0", "value": elec, "label": "sales_value_myr"},
            {"id": "v1", "value": home, "label": "sales_value_myr"},
            {"id": "v2", "value": misc, "label": "sales_value_myr"},
        ],
        sql_used=(
            "SELECT category, SUM(sales_value_myr) AS sales_value_myr "
            "FROM bronze.aa64458a_p50_03_inventory_messy_Sales "
            "GROUP BY category ORDER BY 2 DESC LIMIT 3"
        ),
        rows=[
            {"category": "Electronics", "sales_value_myr": elec},
            {"category": "Home", "sales_value_myr": home},
            {"category": "Misc", "sales_value_myr": misc},
        ],
        question=(
            "In aa64458a_p50_03_inventory_messy.xlsx sheet Sales, what are the "
            "top 3 categories by sales_value_myr?"
        ),
        competing_scopes=_F32_COMPETING,
        grounded_tables=_F32_COMPETING,
        ask_mode="live",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert "1,545,366.40" in env["text"]
    assert "1,199,018.49" in env["text"]
    assert_envelope_valid(env)


def test_f32_grounded_single_table_may_certify():
    """Grounding the ask in one file is unique scope even without sheet words."""
    elec, home, misc = _SALES_CLASS
    only = ["bronze.aa64458a_p50_03_inventory_messy_Sales"]
    env = build_answer_envelope(
        answer_id="a_f32_ground",
        text=(
            f"Electronics {elec:,.2f}, Home {home:,.2f}, Misc {misc:,.2f}."
        ),
        badge="L2_VALIDATED",
        values=[
            {"id": "v0", "value": elec, "label": "sales_value_myr"},
            {"id": "v1", "value": home, "label": "sales_value_myr"},
            {"id": "v2", "value": misc, "label": "sales_value_myr"},
        ],
        sql_used="SELECT category, SUM(sales_value_myr) FROM bronze.x GROUP BY 1",
        rows=[
            {"category": "Electronics", "sales_value_myr": elec},
            {"category": "Home", "sales_value_myr": home},
            {"category": "Misc", "sales_value_myr": misc},
        ],
        question="show top 3 categoty sales",
        competing_scopes=_F32_COMPETING,
        grounded_tables=only,
        ask_mode="live",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert_envelope_valid(env)


def test_f32_ask_path_map_demotes_ambiguous_ranking():
    """Same constructor path as POST /v1/chat/ask (map_ask_response_to_envelope)."""
    home, sports, misc = _WIDE_FILL_CLASS
    resp = AskResponse.model_validate(
        {
            "answer": (
                f"Top 3: Home {home:,.2f}, Sports {sports:,.2f}, Misc {misc:,.2f}."
            ),
            "audit_id": "aud_f32",
            "route": "query_skill",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "sql_used": (
                "SELECT category, SUM(sales_value_myr) "
                "FROM bronze.aa64458a_p50_03_inventory_messy_Wide_Fill "
                "GROUP BY category LIMIT 3"
            ),
            "rows": [
                {"category": "Home", "sales_value_myr": home},
                {"category": "Sports", "sales_value_myr": sports},
                {"category": "Misc", "sales_value_myr": misc},
            ],
        }
    )
    env = map_ask_response_to_envelope(
        resp,
        space_id="sp_hostile",
        session_id="ses_f32",
        grounded_tables=_F32_COMPETING,
        question="show top 3 categoty sales",
        competing_scopes=_F32_COMPETING,
    )
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "383,803.56" not in env["text"]
    assert_envelope_valid(env)


def test_empty_executed_result_demotes_hard_rule_12():
    """BETA-filter miss: SQL ran, matched nothing, must not stay green."""
    env = build_answer_envelope(
        answer_id="a_empty",
        text="Total for BETA is 0.00.",
        badge="L2_VALIDATED",
        values=[{"id": "v0", "value": 0.0, "label": "sales_value_myr"}],
        sql_used="SELECT SUM(sales_value_myr) FROM sales WHERE sku = 'BETA'",
        rows=[],
        ask_mode="live",
    )
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "0.00" not in env["text"] or "No matching rows" in env["text"]
    assert any("hard rule 12" in a for a in env["assumptions"])
    assert_envelope_valid(env)


def test_count_zero_row_still_certifies():
    """A COUNT that returns one cell of 0 is a real answer, not an empty miss."""
    env = build_answer_envelope(
        answer_id="a_count0",
        text="There are 0 matching alerts.",
        badge="L2_VALIDATED",
        values=[{"id": "v0", "value": 0.0, "label": "n"}],
        sql_used="SELECT COUNT(*) AS n FROM alerts WHERE severity = 'critical'",
        rows=[{"n": 0}],
        ask_mode="live",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert_envelope_valid(env)


def test_e9_gate_can_fail():
    """R-0007 — prove the assertion fires before trusting it green.

    Hand-built envelope, bypassing the constructor's demotion, so the validator
    is the only thing standing between a prose total and a green badge.
    """
    env = {
        "answer_id": "a_bypass",
        "text": "Home total is 383,803.56.",
        "values": [{"id": "v0", "value": 383803.56, "label": "sales_value_myr"}],
        "badge": "L2_VALIDATED",
        "abstained": False,
        "sql_used": "-- document retrieval (no SQL)",
        "assumptions": [],
        "as_of": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "contributing_sources": [],
        "drillthrough_token": None,
        "audit_id": "a_bypass",
    }
    with pytest.raises(AssertionError, match="E9"):
        assert_envelope_valid(env)


def test_envelope_dict_literals_only_in_constructor():
    """AST gate: answer_id+badge dict literals only allowed in envelope.py."""
    roots = [ROOT / "packages" / "executor", ROOT / "apps" / "api"]
    offenders: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if path.resolve() == ALLOWED_CONSTRUCTOR.resolve():
                continue
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                keys: set[str] = set()
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
                if "answer_id" in keys and "badge" in keys:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, "envelope dict literals outside envelope.py: " + ", ".join(offenders)


# ---------------------------------------------------------------------------
# E10 / FF-01 — a grouped or ranked ask is not settled by an ungrouped scalar.
# ---------------------------------------------------------------------------


def test_e10_grouped_ask_answered_by_scalar_demotes():
    """The FF-01 shape, caught live by scripts/verify_freeform_demo.py.

    Asked for total sales *per category*, top 3. Answered with one row - total
    outbound revenue - under L1_GOVERNED_METRIC. The figure was real, the SQL
    was real, and together they answered a different question. A reader cannot
    detect that from the envelope, which is what makes it worse than an obvious
    error: real number, real query, green badge.
    """
    env = build_answer_envelope(
        answer_id="ans_ff01",
        text="Result: revenue_myr = 80375993.99",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 80375993.99, "label": "revenue_myr"}],
        sql_used=(
            "SELECT ROUND(COALESCE(SUM(quantity_kg * unit_cost_myr), 0), 2) AS revenue_myr "
            "FROM transactions WHERE txn_type = 'OUT' LIMIT 1000"
        ),
        rows=[{"revenue_myr": 80375993.99}],
        question=(
            "What is the total sales value in MYR for each inventory category, "
            "counting only outbound transactions? Give me the top 3."
        ),
    )

    assert env["badge"] == "ABSTAIN", (
        f"a per-category top-3 ask settled with one ungrouped total certified as "
        f"{env['badge']!r}"
    )
    assert env["abstained"] is True
    assert not env["values"], "an abstention must not ship the substituted figure"
    assert "breakdown" in env["text"].lower()
    assert_envelope_valid(env)


def test_e10_plain_scalar_ask_still_certifies():
    """R-0005 — the guard must narrow matching, not disable the metric.

    'What was our total revenue?' asks for exactly one number, so nothing about
    a single ungrouped row is a mismatch. This is the case that would break if
    E10 were implemented as "scalar answers are suspicious".
    """
    env = build_answer_envelope(
        answer_id="ans_scalar_ok",
        text="Result: revenue_myr = 80375993.99",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 80375993.99, "label": "revenue_myr"}],
        sql_used=(
            "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS revenue_myr "
            "FROM transactions WHERE txn_type = 'OUT'"
        ),
        rows=[{"revenue_myr": 80375993.99}],
        question="What was our total revenue?",
    )

    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert env["values"][0]["value"] == 80375993.99
    assert_envelope_valid(env)


def test_e10_grouped_query_returning_one_group_still_certifies():
    """A real GROUP BY that happens to find one group is not a shape mismatch.

    This is the case that separates a cardinality contract from a heuristic: the
    query did the grouping it was asked to do, and the data had one group. The
    GROUP BY is the evidence, so E10 must read the SQL and not merely count rows.
    """
    env = build_answer_envelope(
        answer_id="ans_one_group",
        text="CHEMICALS: 130,523,362.43",
        badge="L2_VALIDATED",
        abstained=False,
        values=[{"id": "v0", "value": 130523362.43, "label": "sales_value_myr"}],
        sql_used=(
            "SELECT i.category, SUM(t.quantity_kg * t.unit_cost_myr) AS sales_value_myr "
            "FROM transactions t JOIN inventory i ON t.sku = i.sku "
            "WHERE i.is_hazardous GROUP BY i.category ORDER BY sales_value_myr DESC"
        ),
        rows=[{"category": "CHEMICALS", "sales_value_myr": 130523362.43}],
        question="sales for each hazardous category",
    )

    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert_envelope_valid(env)


def test_e10_grouped_ask_with_a_real_breakdown_certifies():
    """The happy path the demo needs: asked for three, given three."""
    env = build_answer_envelope(
        answer_id="ans_real_breakdown",
        text="ELECTRONICS 133,931,869.04; FOOD_DRY 130,689,827.09; CHEMICALS 130,523,362.43",
        badge="L2_VALIDATED",
        abstained=False,
        values=[],
        sql_used=(
            "SELECT i.category, SUM(t.quantity_kg * t.unit_cost_myr) AS sales_value_myr "
            "FROM transactions t JOIN inventory i ON t.sku = i.sku "
            "WHERE t.txn_type = 'OUT' GROUP BY i.category "
            "ORDER BY sales_value_myr DESC LIMIT 3"
        ),
        rows=[
            {"category": "ELECTRONICS", "sales_value_myr": 133931869.04},
            {"category": "FOOD_DRY", "sales_value_myr": 130689827.09},
            {"category": "CHEMICALS", "sales_value_myr": 130523362.43},
        ],
        question="total sales for each inventory category, top 3",
    )

    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert len(env["rows"]) == 3
    assert_envelope_valid(env)


# ---------------------------------------------------------------------------
# E11 / FF-02 — a negated ask is not settled by the inverse predicate.
# ---------------------------------------------------------------------------

_FF02_QUESTION = (
    "How many kilograms of FOOD_COLD goods have been delivered to warehouses "
    "that are not cold storage?"
)
_FF02_INVERSE_SQL = (
    "SELECT ROUND(SUM(s.quantity_kg), 1) AS kg FROM shipments s "
    "JOIN locations l ON l.location_id = s.destination_location_id "
    "WHERE l.is_cold_storage = TRUE"
)
_FF02_CORRECT_SQL = (
    "SELECT ROUND(SUM(s.quantity_kg), 1) AS kg FROM shipments s "
    "JOIN locations l ON l.location_id = s.destination_location_id "
    "WHERE c.category = 'FOOD_COLD' AND s.status = 'DELIVERED' "
    "AND NOT l.is_cold_storage"
)


def test_e11_negated_ask_answered_by_inverse_demotes():
    """The FF-02 shape, the only confidently-wrong gate answer.

    Asked for warehouses that are *not* cold storage. Matched the governed
    metric that filters ``is_cold_storage = TRUE``. Returned 4 (cold-store
    count) instead of 102,986, under L1_GOVERNED_METRIC. Real figure, real
    SQL, inverse question. This test goes red if that polarity is dropped.
    """
    env = build_answer_envelope(
        answer_id="ans_ff02",
        text="Result: kg = 4",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 4, "label": "kg"}],
        sql_used=_FF02_INVERSE_SQL,
        rows=[{"kg": 4}],
        question=_FF02_QUESTION,
    )

    assert env["badge"] == "ABSTAIN", (
        f"a NOT-cold-storage ask settled with is_cold_storage=TRUE certified as "
        f"{env['badge']!r}"
    )
    assert env["abstained"] is True
    assert not env["values"], "an abstention must not ship the inverted figure"
    assert "4" not in env["text"]
    assert "inverse" in env["text"].lower()
    assert_envelope_valid(env)


def test_e11_negated_ask_with_negated_sql_still_certifies():
    """R-0005 — the guard must not refuse a correctly polarised answer."""
    env = build_answer_envelope(
        answer_id="ans_ff02_ok",
        text="Result: kg = 102,986",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 102986, "label": "kg"}],
        sql_used=_FF02_CORRECT_SQL,
        rows=[{"kg": 102986}],
        question=_FF02_QUESTION,
    )

    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert env["values"][0]["value"] == 102986
    assert_envelope_valid(env)


def test_e11_positive_cold_storage_ask_still_certifies():
    """R-0005 — 'at our cold-storage warehouses' is not a negation."""
    env = build_answer_envelope(
        answer_id="ans_ff02_pos",
        text="Result: kg = 4",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 4, "label": "kg"}],
        sql_used=_FF02_INVERSE_SQL,
        rows=[{"kg": 4}],
        question=(
            "At our cold-storage warehouses only, how many kilograms of stock "
            "have been written off?"
        ),
    )

    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert env["values"][0]["value"] == 4
    assert_envelope_valid(env)


def test_e11_bare_boolean_predicate_is_still_the_inverse():
    """The live metric may say ``AND l.is_cold_storage``, not ``= TRUE``."""
    env = build_answer_envelope(
        answer_id="ans_ff02_bare",
        text="Result: n = 4",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 4, "label": "n"}],
        sql_used=(
            "SELECT COUNT(*) AS n FROM locations l "
            "WHERE l.capacity_kg > 0 AND l.is_cold_storage"
        ),
        rows=[{"n": 4}],
        question="how many warehouses are not cold storage?",
    )

    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert not env["values"]
    assert_envelope_valid(env)


def test_e11_live_map_path_demotes_inverse_under_l1():
    """Same constructor path as POST /v1/chat/ask (map_ask_response_to_envelope)."""
    resp = AskResponse.model_validate(
        {
            "answer": "Result: kg = 4",
            "audit_id": "aud_ff02",
            "route": "governed_metric",
            "provenance": {"badge": "governed_metric", "layer": "L1"},
            "sql_used": _FF02_INVERSE_SQL,
            "rows": [{"kg": 4}],
        }
    )
    env = map_ask_response_to_envelope(
        resp,
        space_id="sp_ops",
        session_id="ses_ff02",
        question=_FF02_QUESTION,
    )

    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert not env["values"]
    assert_envelope_valid(env)
