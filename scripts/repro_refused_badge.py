"""A Cortex manifest refusal reaches the customer as a confident green badge.

What this reproduces
--------------------
``CortexOS.dms.answer_engine._abstain_refused`` is what runs when the manifest
enforcer refuses - PathNotAllowed, StatementNotAllowed, SqlNotAnalyzable. It is
the strongest "no" the engine has: no rows, no SQL, nothing read. It emits::

    {"route": "refused", "layer": "refused", "badge": "refused", ...}

``CortexOS.api.contract_routes._is_abstain_signal`` recognises only::

    route in {needs_clarification, abstain, blocked}
    badge in {abstain, blocked}
    layer in {abstain, blocked}

``refused`` is in none of the three. So the signal is missed, and
``_provenance_from_flat`` falls through to ``_FLAT_BADGE.get(badge_raw,
Badge.SESSION)`` - and ``refused`` is not a key in ``_FLAT_BADGE`` either. The
refusal leaves Cortex labelled ``SESSION``.

DMS then maps ``session -> L2_VALIDATED`` (``_BADGE_MAP`` in
``dms_executor.envelope``) and derives ``abstained`` from the *normalised
badge*, so ``abstained`` is False. The customer sees "This SQL was generated,
then checked." over prose saying nothing was read.

Why no existing control catches it
----------------------------------
* DMS's unknown-badge default is ABSTAIN, which is right - but the laundering
  happens upstream in Cortex, so by the time DMS sees it the badge is a known,
  legitimate one.
* E9 demotes a no-query answer only when the text carries unbacked *numbers*.
  The refusal text has no figures, so E9 does not fire.
* The hard-rule-12 empty-result demote requires ``_executed_query(sql_used)``.
  The refusal sets ``sql_used`` to None, so it does not fire.
* ``assert_envelope_valid`` (E1-E9) passes: the envelope is internally
  consistent. It is consistently wrong, which is the failure mode CLAUDE.md 10a
  names - a green badge on abstention prose is a P0.

This script is a reproduction, not a test. It is deliberately not in ``tests/``:
the defect is unfixed, and the fix is a routing decision for the PRD agent, not
something to smuggle in beside a red test.

    python scripts/repro_refused_badge.py

Exit 0 means the defect reproduced. Exit 1 means it no longer does.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "executor"))

CORTEX = Path(os.environ.get("CORTEX_HOME", r"D:\Cortex"))

REFUSAL = {
    "route": "refused",
    "layer": "refused",
    "badge": "refused",
    "assumptions": "PathNotAllowed: transactions is not in this session's grant",
}


def link_one() -> str:
    """Cortex: does the contract layer recognise 'refused' as a refusal?"""
    if str(CORTEX) not in sys.path:
        sys.path.insert(0, str(CORTEX))
    try:
        from CortexOS.api.contract_routes import (  # noqa: PLC0415
            _is_abstain_signal,
            _provenance_from_flat,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  LINK 1  skipped - Cortex not importable ({type(exc).__name__})")
        print("          assuming badge 'session', which is what _FLAT_BADGE yields")
        return "session"

    recognised = _is_abstain_signal(REFUSAL)
    prov = _provenance_from_flat(REFUSAL)
    badge = str(getattr(prov.badge, "value", prov.badge))
    print(f"  LINK 1  _is_abstain_signal(refusal) = {recognised}")
    print(f"          provenance.layer           = {prov.layer}")
    print(f"          provenance.badge           = {prov.badge}")
    if recognised:
        print("          the refusal IS recognised - the first link is repaired")
    return badge


def link_two(cortex_badge: str) -> dict[str, object]:
    """DMS: what does the customer envelope say?"""
    from dms_executor.envelope import (  # noqa: PLC0415
        assert_envelope_valid,
        build_answer_envelope,
    )

    env = build_answer_envelope(
        text=(
            "That question can't be answered inside this session's data grant "
            "(PathNotAllowed). Nothing was read."
        ),
        badge=cortex_badge,
        rows=[],
        sql_used=None,
        values=[],
        contributing_sources=[],
        assumptions=REFUSAL["assumptions"],
        answer_id="ans-repro",
        audit_id="audit-repro",
        space_id="space-repro",
        question="what is the total stock value of hazardous inventory?",
    )
    print(f"  LINK 2  badge     = {env['badge']}")
    print(f"          abstained = {env['abstained']}")
    print(f"          text      = {str(env['text'])[:78]}")
    assert_envelope_valid(env)
    print("          assert_envelope_valid (E1-E9): PASSED")
    return env


def main() -> int:
    print("=== a manifest refusal, followed to the customer envelope ===")
    badge = link_one()
    print()
    env = link_two(badge)
    print()
    if env["badge"] != "ABSTAIN" or env["abstained"]:
        print("P0 REPRODUCED")
        print(f"  the engine read nothing and refused; the customer sees {env['badge']}")
        print(f"  with abstained={env['abstained']}, and every envelope invariant passes.")
        return 0
    print("NOT REPRODUCED - the refusal now arrives as ABSTAIN. This script can retire.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
