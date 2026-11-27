"""ENVELOPE-GATE-01 (dms#590): the rule 10a gate must be able to fail.

Each gap is a hand-built envelope that ``assert_envelope_valid`` must refuse,
next to the control it must still pass. The envelopes start from the product's
own builders (``build_answer_envelope``, ``build_abstain``) so every other
clause (E1-E14) holds; only the property under test is changed.

Abstention is detected structurally, never by wording (rule 15): the abstain
reason code, the abstain route stamp, the builder's own withhold stamp and
template.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from dms_executor.abstain import build_abstain
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.row_listing import found_rows_text

ROWS = [{"location_code": f"WH-{i:02d}", "total_cost_myr": 1000.5 + i} for i in range(3)]
SQL = (
    "SELECT location_code, SUM(cost_myr) AS total_cost_myr "
    "FROM shipments GROUP BY 1"
)


def _served(text: str | None = None, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = copy.deepcopy(rows if rows is not None else ROWS)
    env = build_answer_envelope(
        answer_id="ans_gate",
        text=text if text is not None else found_rows_text(rows),
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        rows=rows,
        sql_used=SQL,
        question="shipment cost by destination",
    )
    assert env["abstained"] is False, env["text"]
    return env


def _abstain(question: str = "shipment cost by destination") -> dict[str, Any]:
    return build_abstain(
        reason="gate_test",
        question=question,
        answer_id="ans_gate_abstain",
        text="I can't answer that from this data.",
        rows=[],
        sql_used=None,
        log_ticket=False,
        stage="gate_test",
    )


def test_controls_pass_the_gate() -> None:
    assert_envelope_valid(_served())
    assert_envelope_valid(_abstain())


# --- gap 1: a green badge on abstention prose --------------------------------------


def test_gap1_a_served_answer_carrying_an_abstain_reason_fails() -> None:
    env = _served()
    env["abstain_reason"] = "ungranted"
    env["text"] = "I can't answer that from this data."
    with pytest.raises(AssertionError, match="E15"):
        assert_envelope_valid(env)


def test_gap1_a_served_answer_stamped_with_an_abstain_route_fails() -> None:
    env = _served()
    env["served_route"] = "abstain:generate"
    with pytest.raises(AssertionError, match="E15"):
        assert_envelope_valid(env)


def test_gap1_a_served_answer_with_the_builders_withhold_stamp_fails() -> None:
    """The demotion wrote its text and stamp, and the badge was put back green."""
    withheld = build_answer_envelope(
        answer_id="ans_gate_e14",
        text="Found 1 row(s).\n  - location_code=WH-00, total_cost_myr=9999.5",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        rows=copy.deepcopy(ROWS[:1]),
        sql_used=SQL,
    )
    assert withheld["abstained"] is True
    env = _served()
    env["text"] = withheld["text"]
    env["assumptions"] = list(env.get("assumptions") or []) + list(withheld["assumptions"])
    with pytest.raises(AssertionError, match="E15"):
        assert_envelope_valid(env)


def test_gap1_the_builders_withhold_text_alone_on_a_green_badge_fails() -> None:
    withheld = build_answer_envelope(
        answer_id="ans_gate_e14b",
        text="Found 1 row(s).\n  - location_code=WH-00, total_cost_myr=9999.5",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        rows=copy.deepcopy(ROWS[:1]),
        sql_used=SQL,
    )
    env = _served()
    env["text"] = withheld["text"]
    with pytest.raises(AssertionError, match="E15"):
        assert_envelope_valid(env)


# --- gap 2: figures in abstained text ----------------------------------------------


def test_gap2_an_abstention_that_states_a_figure_fails() -> None:
    env = _abstain()
    env["text"] = "I can't answer that from this data, but the total was 1,610,511.05."
    with pytest.raises(AssertionError, match="E16"):
        assert_envelope_valid(env)


def test_gap2_an_abstention_that_states_an_integer_fails() -> None:
    env = _abstain()
    env["text"] = "I can't answer that. There were 42 shipments."
    with pytest.raises(AssertionError, match="E16"):
        assert_envelope_valid(env)


def test_gap2_an_abstention_may_quote_the_reason_code_it_declares() -> None:
    """Digits inside the declared ``GEN-01: <code>`` are part of the code.

    The question is not read (rule 15), so a figure echoed from it, outside
    the code, still fails.
    """
    env = _abstain()
    env["assumptions"] = ["GEN-01: unhonored_qualifier:rank_window=4-8"]
    env["text"] = (
        "I cannot certify an ontology-grounded query for that question "
        "(gap: unhonored_qualifier:rank_window=4-8), so I am not executing one."
    )
    assert_envelope_valid(env)
    env["text"] += " You asked for ranks 4 to 8."
    with pytest.raises(AssertionError, match="E16"):
        assert_envelope_valid(env)


def test_gap2_a_numbered_list_ordinal_is_not_a_figure() -> None:
    env = _abstain()
    env["text"] = "Which reading should I answer?\n1. List sku values\n2. List location ids"
    assert_envelope_valid(env)
    env["text"] += "\n3. List the 12 largest"
    with pytest.raises(AssertionError, match="E16"):
        assert_envelope_valid(env)


def test_gap2_the_builder_withholds_a_figure_stating_abstention() -> None:
    """At runtime the builder demotes the text, so the gate never raises on the ask path."""
    env = build_answer_envelope(
        answer_id="ans_gate_e16",
        text="Maybe 999.00.",
        badge="ABSTAIN",
        abstained=True,
        rows=[],
        sql_used=None,
    )
    assert env["abstained"] is True
    assert "999" not in env["text"]
    assert any("withheld (E16)" in a for a in env["assumptions"])
    assert_envelope_valid(env)


# --- gap 3: an uncited figure in an SQL answer -------------------------------------


def test_gap3_an_integer_not_in_values_or_rows_fails() -> None:
    env = _served()
    env["text"] = found_rows_text(ROWS) + "\nThat is 42 more than last month."
    with pytest.raises(AssertionError, match="E17"):
        assert_envelope_valid(env)


def test_gap3_figures_the_envelope_declares_pass() -> None:
    """Row cells, values, the row count and digits inside a row label are declared."""
    text = found_rows_text(ROWS) + "\n3 destinations; WH-02 is the highest at 1002.5."
    assert_envelope_valid(_served(text=text))


def test_gap3_the_builder_withholds_an_uncited_figure() -> None:
    env = build_answer_envelope(
        answer_id="ans_gate_e17",
        text="RM 1,000.50 across 412 outbound movements.",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        rows=copy.deepcopy(ROWS[:1]),
        sql_used=SQL,
    )
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "412" not in env["text"]
    assert env["values"] == [] and env["rows"] == []
    assert any("withheld (E17)" in a for a in env["assumptions"])
    assert_envelope_valid(env)


@pytest.mark.parametrize("label", ["row_count", "operand"])
def test_gap3_a_declared_count_or_operand_passes(label: str) -> None:
    rows = copy.deepcopy(ROWS)
    env = build_answer_envelope(
        answer_id="ans_gate_declared",
        text="2 of 3 location_code values (WH-02, WH-01) carry 1002.5 and 1001.5.",
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        values=[{"id": "v_k", "value": 2, "label": label}],
        rows=rows,
        sql_used=SQL,
    )
    assert env["abstained"] is False, env["text"]
    assert_envelope_valid(env)
    env["values"] = [v for v in env["values"] if v.get("id") != "v_k"]
    with pytest.raises(AssertionError, match="E17"):
        assert_envelope_valid(env)


def test_gap3_tokens_are_read_whole() -> None:
    """1e-07 is one figure; a label 1 does not eat the 1 of 9.1; a date is a date."""
    rows = [{"zone": "1", "share": 9.1}, {"zone": "2", "share": 1e-07}]
    env = _served(text="Zone 1 holds 9.1 pct; zone 2 holds 1e-07 as of 2026-11-20.", rows=rows)
    assert_envelope_valid(env)
    env["text"] = "Zone 1 holds 9.1 pct across 7 shipments."
    with pytest.raises(AssertionError, match="E17"):
        assert_envelope_valid(env)
