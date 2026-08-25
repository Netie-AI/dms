"""ENV-E4 / dms#28 — reorder/low-stock listings must not 500 on orphan prose figures.

E4 requires every decimal/comma number in ``text`` to appear in ``values[]``.
``_ensure_values`` harvests row cells; live Cortex (and some demo prose) also
renders shortfalls / currency that are not cells. Before the fix that raised
inside ``assert_envelope_valid`` → customer 500. Constructor must demote or
ground — never crash, never invent.
"""

from __future__ import annotations

from cortex_client.models import AskResponse
from dms_executor import map_ask_response_to_envelope
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

_REORDER_ROWS = [
    {"sku": "RS622XKR", "quantity_kg": 80.0, "reorder_level_kg": 200.0},
    {"sku": "SKU-GAMMA", "quantity_kg": 150.0, "reorder_level_kg": 300.0},
    {"sku": "SKU-DELTA", "quantity_kg": 60.0, "reorder_level_kg": 250.0},
]
_REORDER_SQL = (
    "SELECT sku, quantity_kg, reorder_level_kg FROM inventory "
    "WHERE quantity_kg < reorder_level_kg ORDER BY sku"
)


def test_reorder_shortfall_in_prose_does_not_500():
    """Live-shaped listing: shortfall in prose, only qty/reorder in rows.

    Must not raise. Grounded shortfall (reorder − qty) stays green; an
    ungrounded figure must abstain — never invent into values[].
    """
    text = (
        "SKUs below reorder: RS622XKR at 80.0 kg (reorder 200.0, shortfall 120.0); "
        "SKU-GAMMA at 150.0 kg (reorder 300.0, shortfall 150.0); "
        "SKU-DELTA at 60.0 kg (reorder 250.0, shortfall 190.0)."
    )
    env = build_answer_envelope(
        answer_id="ans_e4_shortfall",
        text=text,
        badge="L2_VALIDATED",
        abstained=False,
        sql_used=_REORDER_SQL,
        rows=_REORDER_ROWS,
        values=[],
        ask_mode="live",
        audit_id="aud_e4_shortfall",
        question="Which SKUs are below reorder level?",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert "RS622XKR" in env["text"]
    assert "120.0" in env["text"] or "120" in env["text"]
    assert len(env["rows"]) == 3
    value_nums = [float(v["value"]) for v in env["values"]]
    assert any(abs(v - 120.0) < 0.011 for v in value_nums)
    assert any(abs(v - 80.0) < 0.011 for v in value_nums)


def test_reorder_listing_row_figures_only_stays_l2():
    """R-0005 canary: legitimate listing with matching values must not abstain."""
    text = (
        "SKUs below reorder level: RS622XKR qty 80.0 reorder 200.0; "
        "SKU-GAMMA qty 150.0 reorder 300.0; SKU-DELTA qty 60.0 reorder 250.0."
    )
    env = build_answer_envelope(
        answer_id="ans_e4_listing_ok",
        text=text,
        badge="L2_VALIDATED",
        abstained=False,
        sql_used=_REORDER_SQL,
        rows=_REORDER_ROWS,
        values=[],
        ask_mode="live",
        audit_id="aud_e4_ok",
        question="Which SKUs are below reorder level?",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert "RS622XKR" in env["text"]
    assert "SKU-DELTA" in env["text"]
    assert len(env["rows"]) == 3
    assert {r["sku"] for r in env["rows"]} == {"RS622XKR", "SKU-GAMMA", "SKU-DELTA"}


def test_ungrounded_currency_in_listing_abstains_not_500():
    """Invented / unciteable money in prose → ABSTAIN, never AssertionError."""
    text = (
        "Low stock on RS622XKR, SKU-GAMMA, SKU-DELTA. "
        "Total shortfall value is RM 1,234.56."
    )
    env = build_answer_envelope(
        answer_id="ans_e4_invent",
        text=text,
        badge="L2_VALIDATED",
        abstained=False,
        sql_used=_REORDER_SQL,
        rows=_REORDER_ROWS,
        values=[],
        ask_mode="live",
        audit_id="aud_e4_invent",
        question="low stock",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["values"] == []
    assert env["rows"] == []
    assert any("E4" in a for a in env["assumptions"])
    # Must not launder the invented figure into values under a green badge.
    assert "1,234.56" not in env["text"] and "1234.56" not in env["text"]


def test_map_ask_reorder_shortfall_does_not_500():
    """Customer path: map_ask_response_to_envelope + assert must not raise."""
    resp = AskResponse.model_validate(
        {
            "answer": (
                "Below reorder: RS622XKR qty 80.0 (reorder 200.0, short 120.0), "
                "SKU-GAMMA qty 150.0 (reorder 300.0, short 150.0)."
            ),
            "audit_id": "aud_e4_map",
            "route": "query_skill",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "sql_used": _REORDER_SQL,
            "rows": _REORDER_ROWS[:2],
            "values": [],
        }
    )
    env = map_ask_response_to_envelope(
        resp,
        space_id="sp_demo",
        session_id="ses_e4",
        question="Which SKUs are below reorder level?",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert "RS622XKR" in env["text"]
    assert len(env["rows"]) == 2


def test_map_ask_ungrounded_figure_abstains():
    resp = AskResponse.model_validate(
        {
            "answer": "Low stock SKUs total exposure RM 9,999.99.",
            "audit_id": "aud_e4_map_bad",
            "route": "query_skill",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "sql_used": _REORDER_SQL,
            "rows": _REORDER_ROWS,
        }
    )
    env = map_ask_response_to_envelope(resp, question="low stock")
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
