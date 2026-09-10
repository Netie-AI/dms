"""Phase 0 — customer envelope invariants E1–E9 + single-constructor AST gate.

INVARIANT-CHANGE: Phase 0 envelope mapping — abstain must not stamp L2_VALIDATED.
INVARIANT-CHANGE: E9 (F26) — a path that executed no query may quote a figure a
cited snippet contains, but may never render one it computed.
INVARIANT-CHANGE: hard rule 12 — executed SQL with zero rows demotes to ABSTAIN
(empty-filter green is a P0; COUNT returning a zero cell still has a row).
INVARIANT-CHANGE: E9-02 (F32) — ambiguous multi-sheet / multi-file category
ranking must not stay confident green when competing Sales vs Wide_Fill (or
cross-file sales) scopes disagree; uniquely scoped executed ranks may certify.
INVARIANT-CHANGE: E9-02 verify — SQL-cited stem_Sales/stem_Wide_Fill infers the
sibling pair so an ungrounded DEMO_TABLES ask cannot keep Wide_Fill green.
INVARIANT-CHANGE: E12 (ANS-02) — a one-number ask must not keep a confident
badge when executed SQL is GROUP BY and 2+ rows come back.
INVARIANT-CHANGE: E11 (FF-02) — a negated ask must not keep L1_GOVERNED_METRIC
when the matched metric's filter asserts the positive of that negation.
INVARIANT-CHANGE: E12 (ANS-02) — a one-number ask must not keep a confident
badge when the matched query returns a grouped ranking.
INVARIANT-CHANGE: E9-03 — a confident badge with no executed rows and no SQL
must demote to ABSTAIN (governed_metric empty-result shrug).
INVARIANT-CHANGE: xlsx-named ask answered from demo warehouse SQL demotes.
INVARIANT-CHANGE: E9-02/F32 derived path skips SQL that only cites DEMO_TABLES
(lake metric, not a workbook ranking). Sheet-sibling fallback is bronze ingest
labels; Cortex column-card members are not sheets. Wide_Fill SQL still demotes.
INVARIANT-CHANGE: E9-02/F32 quoted warehouse.inventory SQL stays a lake join;
warehouse_* grants are not workbook sheets. spend_by_country must not ABSTAIN.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cortex_client.models import AskResponse
from dms_executor import map_ask_response_to_envelope
from dms_executor.demo_warehouse import DEMO_TABLES
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

    Planted Wide_Fill-class ranking with executed SQL on one non-disambiguated
    sheet. Competing labels come from the SQL identifier + its workbook sibling,
    not a test-only ``competing_scopes`` plant. Removing the E9-02 guard makes
    this fixture fail red.
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
        grounded_tables=list(DEMO_TABLES),
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
    """live_ask path: map_ask_response_to_envelope with DEMO_TABLES, no plant.

    ``Executor.live_ask`` passes grounded_tables from the minted ACL and never
    sets competing_scopes. An ungrounded ask is DEMO_TABLES. Wide_Fill in SQL
    must still demote.
    """
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
        grounded_tables=list(DEMO_TABLES),
        question="show top 3 categoty sales",
    )
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["values"] == []
    assert env["rows"] == []
    assert "383,803.56" not in env["text"]
    assert "scope conflict" in env["text"].lower()
    assert env["audit_id"] == "aud_f32"
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


def test_e9_03_governed_metric_with_no_rows_demotes():
    """Live hostile pack: L1_GOVERNED_METRIC, empty rows, SQL stub, v_count=0.

    BETA / KL / Malay paraphrase shipped a green badge the score_answers judge
    reads as WRONG (confident + no ranking). Abstain costs coverage, not precision.
    """
    env = build_answer_envelope(
        answer_id="a_e903",
        text="I can't map that filter onto a governed metric.",
        badge="L1_GOVERNED_METRIC",
        values=[{"id": "v_count", "value": 0.0, "label": "row_count"}],
        sql_used="-- live ask (SQL not returned)",
        rows=[],
        route="governed_metric",
        ask_mode="live",
        question=(
            "In encoding_value_norm.xlsx sheet Sales, total sales_value_myr for sku BETA?"
        ),
    )
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert not env["values"]
    assert any("E9-03" in a for a in env["assumptions"])
    assert_envelope_valid(env)


def test_xlsx_named_ask_answered_from_demo_warehouse_demotes():
    """Hostile pack: encoding_value_norm.xlsx sku BETA -> outbound revenue."""
    env = build_answer_envelope(
        answer_id="a_xlsx_demo",
        text="Result: revenue_myr = 80787598.3",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 80787598.3, "label": "revenue_myr"}],
        sql_used=(
            "SELECT ROUND(COALESCE(SUM(quantity_kg * unit_cost_myr), 0), 2) "
            "AS revenue_myr FROM transactions WHERE txn_type = 'OUT' LIMIT 1000"
        ),
        rows=[{"revenue_myr": 80787598.3}],
        question=(
            "In encoding_value_norm.xlsx sheet Sales, what is total "
            "sales_value_myr for sku BETA?"
        ),
        ask_mode="live",
    )
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert not env["values"]
    assert "80787598" not in env["text"]
    assert any("demo warehouse" in a for a in env["assumptions"])
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


def test_e12_scalar_ask_answered_by_ranking_demotes():
    """ANS-02 on the customer envelope: one-number ask, grouped ranking back.

    Live 2026-08-27: "What is total inventory quantity?" returned 10 category
    value rows under L2_VALIDATED from a stored query skill.
    """
    env = build_answer_envelope(
        answer_id="ans_e12",
        text="Found 10 row(s).",
        badge="L2_VALIDATED",
        abstained=False,
        values=[{"id": "v0", "value": 67710506.66, "label": "total_value_myr"}],
        sql_used=(
            "SELECT category, SUM(quantity_kg * unit_cost_myr) AS total_value_myr "
            "FROM inventory GROUP BY category LIMIT 1000"
        ),
        rows=[
            {"category": "FOOD_COLD", "total_value_myr": 67710506.66},
            {"category": "CHEMICALS", "total_value_myr": 61894503.52},
        ],
        question="What is total inventory quantity?",
    )

    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert not env["values"]
    assert "different question" in env["text"].lower()
    assert_envelope_valid(env)


def test_e12_plain_scalar_ask_still_certifies():
    """R-0005: a true one-row total must stay certified."""
    env = build_answer_envelope(
        answer_id="ans_e12_ok",
        text="Result: qty = 91",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 91, "label": "qty"}],
        sql_used="SELECT SUM(quantity_kg) AS qty FROM inventory",
        rows=[{"qty": 91}],
        question="What is total inventory quantity?",
    )

    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert env["values"][0]["value"] == 91
    assert_envelope_valid(env)


def test_e12_total_by_category_keeps_ranking():
    """A total that names the group is a breakdown ask, not ANS-02."""
    env = build_answer_envelope(
        answer_id="ans_e12_by",
        text="FOOD_COLD 67,710,506.66; CHEMICALS 61,894,503.52",
        badge="L2_VALIDATED",
        abstained=False,
        values=[
            {"id": "v0", "value": 67710506.66, "label": "FOOD_COLD"},
            {"id": "v1", "value": 61894503.52, "label": "CHEMICALS"},
        ],
        sql_used=(
            "SELECT category, SUM(quantity_kg * unit_cost_myr) AS total_value_myr "
            "FROM inventory GROUP BY category LIMIT 1000"
        ),
        rows=[
            {"category": "FOOD_COLD", "total_value_myr": 67710506.66},
            {"category": "CHEMICALS", "total_value_myr": 61894503.52},
        ],
        question="What is total stock value by category?",
    )
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["rows"]
    assert_envelope_valid(env)


def test_e12_ask_path_map_demotes_ranking_for_a_total():
    """Same constructor path as POST /v1/chat/ask (map_ask_response_to_envelope)."""
    resp = AskResponse.model_validate(
        {
            "answer": "FOOD_COLD 67,710,506.66; CHEMICALS 61,894,503.52",
            "audit_id": "aud_e12",
            "route": "query_skill",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "sql_used": (
                "SELECT category, SUM(quantity_kg * unit_cost_myr) AS total_value_myr "
                "FROM inventory GROUP BY category LIMIT 1000"
            ),
            "rows": [
                {"category": "FOOD_COLD", "total_value_myr": 67710506.66},
                {"category": "CHEMICALS", "total_value_myr": 61894503.52},
            ],
        }
    )
    env = map_ask_response_to_envelope(
        resp,
        space_id="sp_ops",
        session_id="ses_e12",
        question="What is total inventory quantity?",
    )
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert not env["values"]
    assert not env["rows"]
    assert "67,710,506.66" not in env["text"]
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
# E11 / FF-02 — a negated ask is not settled by a metric whose filter is the
# positive of that negation.
# ---------------------------------------------------------------------------

_COLD_COUNT_SQL = "SELECT COUNT(*) AS cold_count FROM locations WHERE is_cold_storage = TRUE"
_COLD_COUNT_ROWS = [{"cold_count": 4}]
_NEGATED_COLD_ASK = (
    "How many kilograms of FOOD_COLD goods have been delivered to warehouses "
    "that are not cold storage?"
)
_POSITIVE_COLD_ASK = "How many cold storage locations do we have?"


def test_e11_negated_ask_answered_by_positive_filter_demotes():
    """The FF-02 shape, caught live by scripts/verify_freeform_demo.py.

    Asked for kilograms of FOOD_COLD delivered to warehouses that are *not*
    cold storage. Answered with cold_count=4 from
    ``WHERE is_cold_storage = TRUE`` under L1_GOVERNED_METRIC. Ask and answer
    are both scalar (E10 does not fire). The match inverted polarity on the
    token "cold storage" and still stamped governed.
    """
    env = build_answer_envelope(
        answer_id="ans_ff02",
        text="Result: cold_count = 4",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 4, "label": "cold_count"}],
        sql_used=_COLD_COUNT_SQL,
        rows=list(_COLD_COUNT_ROWS),
        question=_NEGATED_COLD_ASK,
    )

    assert env["badge"] == "ABSTAIN", (
        f"a NOT-cold-storage ask settled with is_cold_storage=TRUE certified as {env['badge']!r}"
    )
    assert env["abstained"] is True
    assert not env["values"], "an abstention must not ship the inverted figure"
    assert not env["rows"]
    assert "4" not in env["text"], "the substituted cold_count must not render"
    assert "inverse" in env["text"].lower() or "exclude" in env["text"].lower()
    assert_envelope_valid(env)


@pytest.mark.parametrize(
    "question",
    [
        _NEGATED_COLD_ASK,
        "kilograms delivered to warehouses excluding cold storage",
        "FOOD_COLD goods delivered to sites other than cold storage",
        "FOOD_COLD delivered to non-cold-storage warehouses",
    ],
)
def test_e11_closed_list_negation_markers_demote(question: str):
    """The minimum subset: not / excluding / other than / non- all flip polarity."""
    env = build_answer_envelope(
        answer_id="ans_ff02_mark",
        text="Result: cold_count = 4",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 4, "label": "cold_count"}],
        sql_used=_COLD_COUNT_SQL,
        rows=list(_COLD_COUNT_ROWS),
        question=question,
    )
    assert env["badge"] == "ABSTAIN", question
    assert env["abstained"] is True
    assert not env["values"]
    assert_envelope_valid(env)


def test_e11_positive_cold_storage_ask_still_certifies():
    """R-0005 — the guard must narrow matching, not disable the metric.

    'How many cold storage locations do we have?' is the metric's own
    question. Same SQL, same 4. If E11 were implemented as 'is_cold_storage
    in the SQL is suspicious', this would go red.
    """
    env = build_answer_envelope(
        answer_id="ans_cold_ok",
        text="Result: cold_count = 4",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 4, "label": "cold_count"}],
        sql_used=_COLD_COUNT_SQL,
        rows=list(_COLD_COUNT_ROWS),
        question=_POSITIVE_COLD_ASK,
    )

    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert env["values"][0]["value"] == 4
    assert env["rows"][0]["cold_count"] == 4
    assert "4" in env["text"]
    assert_envelope_valid(env)


def test_e11_ask_path_map_demotes_polarity_mismatch():
    """Same constructor path as POST /v1/chat/ask (map_ask_response_to_envelope)."""
    resp = AskResponse.model_validate(
        {
            "answer": "Result: cold_count = 4",
            "audit_id": "aud_ff02",
            "route": "governed_metric",
            "provenance": {"badge": "governed_metric", "layer": "L1"},
            "sql_used": _COLD_COUNT_SQL,
            "rows": list(_COLD_COUNT_ROWS),
        }
    )
    env = map_ask_response_to_envelope(
        resp,
        space_id="sp_finance",
        session_id="ses_ff02",
        question=_NEGATED_COLD_ASK,
    )
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert not env["values"]
    assert not env["rows"]
    assert "4" not in env["text"]
    assert_envelope_valid(env)


def test_e11_sql_that_already_negates_the_filter_still_certifies():
    """Polarity agreement is not a mismatch. L2 answering the negated ask
    with ``NOT l.is_cold_storage`` must keep its figure (R-0005).
    """
    env = build_answer_envelope(
        answer_id="ans_breach_ok",
        text="Result: ambient_breach = 102,986.0",
        badge="L2_VALIDATED",
        abstained=False,
        values=[{"id": "v0", "value": 102986.0, "label": "breach_kg"}],
        sql_used=(
            "SELECT ROUND(SUM(s.quantity_kg), 1) AS breach_kg "
            "FROM shipments s "
            "JOIN sku_category c ON c.sku = s.sku "
            "JOIN locations l ON l.location_id = s.destination_location_id "
            "WHERE c.category = 'FOOD_COLD' AND s.status = 'DELIVERED' "
            "AND NOT l.is_cold_storage"
        ),
        rows=[{"breach_kg": 102986.0}],
        question=_NEGATED_COLD_ASK,
    )

    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["values"][0]["value"] == 102986.0
    assert_envelope_valid(env)


def test_e11_subject_noun_does_not_shield_a_wrong_answer():
    """A trailing noun in the negated phrase must not veto the real match.

    "non-hazardous **inventory**" negates {hazardous, inventory}. The filter
    that matters is a single column, ``is_hazardous``, which can never carry
    the subject noun too - a SQL identifier is not a sentence. Requiring both
    negated words to appear together on one atom meant the distinguishing word
    ("hazardous") matching cleanly was overridden by the *other* word never
    being able to match anything, and the answer certified.
    """
    env = build_answer_envelope(
        answer_id="ans_nonhaz_wrong",
        text="Result: value = 999999.99",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 999999.99, "label": "value"}],
        sql_used=(
            "SELECT SUM(quantity_kg * unit_cost_myr) AS value "
            "FROM inventory WHERE is_hazardous = TRUE"
        ),
        rows=[{"value": 999999.99}],
        question="total value of non-hazardous inventory",
    )

    assert env["badge"] == "ABSTAIN", (
        f"non-hazardous answered from is_hazardous = TRUE certified as {env['badge']!r}"
    )
    assert env["abstained"] is True


def test_e11_excluded_literal_does_not_hide_behind_its_own_table_name():
    """Same shape as above, on a literal instead of a boolean column.

    "excluding cancelled **shipments**" negates {cancelled, shipments}. The
    filter that matters is the literal ``'CANCELLED'`` on ``status`` - the
    literal can carry "cancelled" but never "shipments", which is the name of
    the table the filter lives in, not a value any column holds.
    """
    env = build_answer_envelope(
        answer_id="ans_excl_wrong",
        text="Result: total = 500000.00",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 500000.00, "label": "total"}],
        sql_used="SELECT SUM(cost_myr) AS total FROM shipments WHERE status = 'CANCELLED'",
        rows=[{"total": 500000.00}],
        question="total shipping cost excluding cancelled shipments",
    )

    assert env["badge"] == "ABSTAIN", (
        f"'excluding cancelled shipments' answered from status = 'CANCELLED' "
        f"certified as {env['badge']!r}"
    )
    assert env["abstained"] is True


def test_e11_single_shared_word_does_not_launder_an_unrelated_column():
    """R-0005 guard on the fix above: the relaxed rule must stay narrow.

    A column that happens to share a word with the negated phrase's subject
    noun, but is otherwise unrelated to the actual filter, must not itself
    trigger a demote - only the column carrying the real distinguishing word
    does, and it correctly answers in the negative here.
    """
    env = build_answer_envelope(
        answer_id="ans_coincidental_ok",
        text="Result: value = 100.00",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v0", "value": 100.00, "label": "value"}],
        sql_used=(
            "SELECT SUM(x) AS value FROM inventory "
            "WHERE is_hazardous = FALSE AND inventory_active = TRUE"
        ),
        rows=[{"value": 100.00}],
        question="total value of non-hazardous inventory",
    )

    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False


#: What a real customer upload looks like: one workbook, two sheets, neither
#: named the way the original F32 fixture family was.
_F32_REAL_WORKBOOK = [
    "bronze.q3_regional_report_Summary",
    "bronze.q3_regional_report_Detail",
]


def test_f32_demotes_on_a_customer_workbook_whose_sheets_are_not_named_sales():
    """F32 must fire on sheet shape, not on the fixture's sheet names.

    The named-class path only recognised ``Sales`` and ``Wide_Fill``. Production
    never plants ``competing_scopes`` — ``Executor.live_ask`` passes
    ``sorted(acl.row_predicates)`` as ``grounded_tables`` — so a workbook with
    sheets ``Summary`` and ``Detail`` produced no conflict, and an ask that did
    not pin the sheet shipped one sheet's ranking under a green badge while the
    other sheet held different numbers. Nothing downstream could catch it.

    Note this fixture passes NO ``competing_scopes``: it exercises the derivation
    path that every other F32 test plants around.
    """
    home, sports, misc = _WIDE_FILL_CLASS
    env = build_answer_envelope(
        answer_id="a_f32_real",
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
            "FROM bronze.q3_regional_report_Detail "
            "GROUP BY category ORDER BY 2 DESC LIMIT 3"
        ),
        rows=[
            {"category": "Home", "sales_value_myr": home},
            {"category": "Sports", "sales_value_myr": sports},
            {"category": "Misc", "sales_value_myr": misc},
        ],
        question="show top 3 category sales",
        grounded_tables=_F32_REAL_WORKBOOK,
        ask_mode="live",
    )
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "scope conflict" in env["text"].lower()
    for n in ("383,803.56", "242,755.97", "228,548.84"):
        assert n not in env["text"]
    assert_envelope_valid(env)


def test_f32_does_not_demote_a_legitimate_multi_table_grant():
    """The widened shape rule must not turn ordinary Space grants into abstains.

    A demo Space grants six unrelated single-token tables. They share no stem, so
    they are not alternate scopes for the same thing and a ranking over them must
    stay certified. This is the false-positive guard on the change above.
    """
    home, sports, misc = _WIDE_FILL_CLASS
    env = build_answer_envelope(
        answer_id="a_f32_ok",
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
            "FROM transactions GROUP BY category ORDER BY 2 DESC LIMIT 3"
        ),
        rows=[
            {"category": "Home", "sales_value_myr": home},
            {"category": "Sports", "sales_value_myr": sports},
            {"category": "Misc", "sales_value_myr": misc},
        ],
        question="show top 3 category sales",
        grounded_tables=[
            "alerts",
            "inventory",
            "locations",
            "shipments",
            "suppliers",
            "transactions",
        ],
        ask_mode="live",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert "383,803.56" in env["text"]
    assert_envelope_valid(env)


_SPEND_BY_COUNTRY_SQL = (
    "SELECT s.country, ROUND(SUM(i.quantity_kg * i.unit_cost_myr), 2) "
    "AS total_spend_myr FROM inventory AS i JOIN suppliers AS s "
    "ON i.supplier_id = s.supplier_id GROUP BY s.country "
    "ORDER BY total_spend_myr DESC, s.country ASC"
)
_STOCK_BY_CATEGORY_SQL = (
    "SELECT category, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS stock_value_myr "
    "FROM inventory GROUP BY category "
    "ORDER BY stock_value_myr DESC, category ASC"
)
_SPEND_ROWS = [
    {"country": "MY", "total_spend_myr": 20516.00},
    {"country": "SG", "total_spend_myr": 7524.00},
    {"country": "TH", "total_spend_myr": 1800.00},
]
_STOCK_ROWS = [
    {"category": "PACKAGING", "stock_value_myr": 15015.00},
    {"category": "PARTS", "stock_value_myr": 7210.00},
    {"category": "RAW", "stock_value_myr": 5816.00},
]
_F32_SOUP_GRANT = list(DEMO_TABLES) + _F32_REAL_WORKBOOK
_COLUMN_CARDS = [
    {
        "ref_id": "src_inv",
        "container": "inventory",
        "member": "category",
        "kind": "sql",
        "row_count": 3,
        "contribution": 0.5,
    },
    {
        "ref_id": "src_sup",
        "container": "suppliers",
        "member": "country",
        "kind": "sql",
        "row_count": 3,
        "contribution": 0.5,
    },
]


def test_f32_does_not_demote_spend_by_country_lake_sql_with_sheet_grant_soup():
    """Cortex matched cq_spend_by_country; leftover ABSTAIN was E9-02/F32.

    Live path: Finance grant includes DEMO_TABLES plus a customer workbook's
    sheets, and Cortex column cards become container_member labels. Those look
    like _sheet_siblings. The executed SQL is inventory JOIN suppliers — a lake
    metric, not Sales vs Wide_Fill.
    """
    env = build_answer_envelope(
        answer_id="a_spend_country",
        text="Spend by country: MY 20,516.00, SG 7,524.00, TH 1,800.00.",
        badge="L0_CERTIFIED",
        values=[
            {"id": "v0", "value": 20516.00, "label": "total_spend_myr"},
            {"id": "v1", "value": 7524.00, "label": "total_spend_myr"},
            {"id": "v2", "value": 1800.00, "label": "total_spend_myr"},
        ],
        sql_used=_SPEND_BY_COUNTRY_SQL,
        rows=_SPEND_ROWS,
        question="What is our total spend by supplier country?",
        grounded_tables=_F32_SOUP_GRANT,
        contributing_sources=_COLUMN_CARDS,
        drillthrough_token="dt_spend",
        ask_mode="live",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert "20,516.00" in env["text"]
    assert "scope conflict" not in env["text"].lower()
    assert not any("E9-02" in a or "F32" in a for a in env["assumptions"])
    assert_envelope_valid(env)


def test_f32_does_not_demote_stock_by_category_lake_sql_with_sheet_grant_soup():
    env = build_answer_envelope(
        answer_id="a_stock_cat",
        text="Stock: PACKAGING 15,015.00, PARTS 7,210.00, RAW 5,816.00.",
        badge="L1_GOVERNED_METRIC",
        values=[
            {"id": "v0", "value": 15015.00, "label": "stock_value_myr"},
            {"id": "v1", "value": 7210.00, "label": "stock_value_myr"},
            {"id": "v2", "value": 5816.00, "label": "stock_value_myr"},
        ],
        sql_used=_STOCK_BY_CATEGORY_SQL,
        rows=_STOCK_ROWS,
        question="What is total stock value by category?",
        grounded_tables=_F32_SOUP_GRANT,
        contributing_sources=_COLUMN_CARDS,
        drillthrough_token="dt_stock",
        ask_mode="live",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert "15,015.00" in env["text"]
    assert "scope conflict" not in env["text"].lower()
    assert_envelope_valid(env)


def test_f32_ask_path_map_keeps_spend_by_country_with_grant_soup():
    """live_ask map: DEMO_TABLES + bronze siblings + column cards, lake SQL."""
    resp = AskResponse.model_validate(
        {
            "answer": "MY 20,516.00; SG 7,524.00; TH 1,800.00.",
            "audit_id": "aud_cq_spend",
            "route": "certified_metric",
            "provenance": {"badge": "certified", "layer": "L0"},
            "sql_used": _SPEND_BY_COUNTRY_SQL,
            "rows": _SPEND_ROWS,
            "contributing_sources": _COLUMN_CARDS,
            "drillthrough_token": "dt_cq_spend",
            "abstained": False,
        }
    )
    env = map_ask_response_to_envelope(
        resp,
        space_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        session_id="ses_cq_spend",
        grounded_tables=_F32_SOUP_GRANT,
        question="What is our total spend by supplier country?",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"]
    assert "20,516.00" in env["text"] or "20516" in env["text"]
    assert "scope conflict" not in env["text"].lower()
    assert_envelope_valid(env)


_WAREHOUSE_SCOPE_GRANT = [
    "warehouse_inventory",
    "warehouse_locations",
    "warehouse_suppliers",
    "warehouse_transactions",
]
_QUOTED_WAREHOUSE_SPEND_SQL = (
    'SELECT s.country, ROUND(SUM(i.quantity_kg * i.unit_cost_myr), 2) '
    'AS total_spend_myr FROM "warehouse"."inventory" AS i '
    'JOIN "warehouse"."suppliers" AS s ON i.supplier_id = s.supplier_id '
    "GROUP BY s.country ORDER BY total_spend_myr DESC"
)
_WAREHOUSE_PREFIX_SPEND_SQL = (
    "SELECT s.country, ROUND(SUM(i.quantity_kg * i.unit_cost_myr), 2) "
    "AS total_spend_myr FROM warehouse_inventory AS i "
    "JOIN warehouse_suppliers AS s ON i.supplier_id = s.supplier_id "
    "GROUP BY s.country ORDER BY total_spend_myr DESC"
)
_WAREHOUSE_SCOPE_SOURCES = [
    {
        "ref_id": "src_inv",
        "container": "warehouse_inventory",
        "kind": "sql",
        "row_count": 3,
        "contribution": 0.5,
    },
    {
        "ref_id": "src_sup",
        "container": "warehouse_suppliers",
        "kind": "sql",
        "row_count": 3,
        "contribution": 0.5,
    },
]


def test_f32_does_not_demote_warehouse_prefixed_lake_tables():
    """Live leftover text: scope conflict across warehouse_inventory / _suppliers.

    Cortex SQL already joined country. F32 treated warehouse_* as workbook
    sheets because they share the token prefix ``warehouse``.
    """
    env = build_answer_envelope(
        answer_id="a_wh_prefix",
        text="Spend by country: MY 20,516.00, SG 7,524.00, TH 1,800.00.",
        badge="L0_CERTIFIED",
        values=[
            {"id": "v0", "value": 20516.00, "label": "total_spend_myr"},
            {"id": "v1", "value": 7524.00, "label": "total_spend_myr"},
            {"id": "v2", "value": 1800.00, "label": "total_spend_myr"},
        ],
        sql_used=_WAREHOUSE_PREFIX_SPEND_SQL,
        rows=_SPEND_ROWS,
        question="What is our total spend by supplier country?",
        grounded_tables=_WAREHOUSE_SCOPE_GRANT,
        contributing_sources=_WAREHOUSE_SCOPE_SOURCES,
        drillthrough_token="dt_warehouse_spend",
        ask_mode="live",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"]
    assert "20,516.00" in env["text"]
    assert "scope conflict" not in env["text"].lower()
    assert_envelope_valid(env)


def test_f32_does_not_demote_quoted_warehouse_schema_spend_with_sheet_soup():
    """Live Cortex SQL quotes schema.table; grant also holds a workbook.

    ``FROM "warehouse"."inventory"`` used to cite only ``warehouse``, so the
    lake-SQL skip missed and F32 demoted spend_by_country as a sheet conflict.
    """
    env = build_answer_envelope(
        answer_id="a_wh_quoted",
        text="Spend by country: MY 20,516.00, SG 7,524.00, TH 1,800.00.",
        badge="L0_CERTIFIED",
        values=[
            {"id": "v0", "value": 20516.00, "label": "total_spend_myr"},
            {"id": "v1", "value": 7524.00, "label": "total_spend_myr"},
            {"id": "v2", "value": 1800.00, "label": "total_spend_myr"},
        ],
        sql_used=_QUOTED_WAREHOUSE_SPEND_SQL,
        rows=_SPEND_ROWS,
        question="What is our total spend by supplier country?",
        grounded_tables=_WAREHOUSE_SCOPE_GRANT + _F32_REAL_WORKBOOK,
        contributing_sources=_WAREHOUSE_SCOPE_SOURCES,
        drillthrough_token="dt_warehouse_quoted",
        ask_mode="live",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"]
    assert "20,516.00" in env["text"]
    assert "scope conflict" not in env["text"].lower()
    assert_envelope_valid(env)


def test_f32_ask_path_map_keeps_quoted_warehouse_spend():
    resp = AskResponse.model_validate(
        {
            "answer": "MY 20,516.00; SG 7,524.00; TH 1,800.00.",
            "audit_id": "aud_wh_quoted",
            "route": "certified_metric",
            "provenance": {"badge": "certified", "layer": "L0"},
            "sql_used": _QUOTED_WAREHOUSE_SPEND_SQL,
            "rows": _SPEND_ROWS,
            "contributing_sources": _WAREHOUSE_SCOPE_SOURCES,
            "drillthrough_token": "dt_wh_quoted_map",
            "abstained": False,
        }
    )
    env = map_ask_response_to_envelope(
        resp,
        space_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        session_id="ses_wh_quoted",
        grounded_tables=_WAREHOUSE_SCOPE_GRANT + _F32_REAL_WORKBOOK,
        question="What is our total spend by supplier country?",
    )
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"]
    assert "20,516.00" in env["text"] or "20516" in env["text"]
    assert "scope conflict" not in env["text"].lower()
    assert_envelope_valid(env)


def test_f32_wide_fill_still_demotes_with_column_cards_and_demo_grant():
    """Lake-SQL skip must not swallow a real Wide_Fill ranking."""
    home, sports, misc = _WIDE_FILL_CLASS
    env = build_answer_envelope(
        answer_id="a_f32_cards",
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
        grounded_tables=_F32_SOUP_GRANT,
        contributing_sources=_COLUMN_CARDS,
        drillthrough_token="dt_wf",
        ask_mode="live",
    )
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "scope conflict" in env["text"].lower()
    for n in ("383,803.56", "242,755.97", "228,548.84"):
        assert n not in env["text"]
    assert_envelope_valid(env)
