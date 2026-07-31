"""Phase 0 — customer envelope invariants E1–E8 + single-constructor AST gate.

INVARIANT-CHANGE: Phase 0 envelope mapping — abstain must not stamp L2_VALIDATED.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cortex_client.models import AskResponse
from dms_executor import map_ask_response_to_envelope
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_CONSTRUCTOR = ROOT / "packages" / "executor" / "dms_executor" / "envelope.py"


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
        ask_mode="demo",
        demo_fallback_used=True,
    )
    assert env.get("demo_fallback_banner") is True
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
