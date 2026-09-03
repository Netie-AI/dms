"""Follow a Cortex manifest refusal to the customer envelope.

What this reproduces
--------------------
``CortexOS.dms.answer_engine._abstain_refused`` emits::

    {"route": "refused", "layer": "refused", "badge": "refused", ...}

or, after Cortex laundering, ``badge: session`` with ``route: refused``.

LINK 1 (Cortex#11, engine half): does ``_is_abstain_signal`` treat ``refused``
as an abstain? Diagnostic only. Skip if Cortex is not importable.

LINK 2 (dms#66, DMS half): ``map_ask_response_to_envelope`` is the customer
path. A refusal with ``route=refused`` and a confident badge must leave as
``badge=ABSTAIN``, ``abstained=True``. Calling ``build_answer_envelope`` with
no route is not the product path and must not be used as the P0 signal.

    python scripts/repro_refused_badge.py

Exit 0 = customer envelope still confident (P0). Exit 1 = DMS half closed
(customer sees ABSTAIN). Cortex LINK 1 is printed either way.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "executor"))
sys.path.insert(0, str(ROOT / "packages" / "cortex_client"))

CORTEX = Path(os.environ.get("CORTEX_HOME", r"E:\Cortex"))
if not CORTEX.exists():
    CORTEX = Path(r"D:\Cortex")

REFUSAL = {
    "route": "refused",
    "layer": "refused",
    "badge": "refused",
    "assumptions": "PathNotAllowed: transactions is not in this session's grant",
}


def link_one() -> str:
    """Cortex: does the contract layer recognise 'refused' as a refusal?"""
    src = CORTEX / "CortexOS" / "api" / "contract_routes.py"
    if src.is_file():
        text = src.read_text(encoding="utf-8")
        tokens_ok = (
            '"refused"' in text
            and "_FLAT_BADGE.get(badge_raw, Badge.ABSTAIN)" in text
        )
        print(
            "  LINK 1  engine tree "
            + ("HAS F40 tokens (refused -> ABSTAIN)" if tokens_ok else "MISSING F40 tokens")
        )
    else:
        print(f"  LINK 1  skipped - no {src}")

    if str(CORTEX) not in sys.path:
        sys.path.insert(0, str(CORTEX))
    try:
        from CortexOS.api.contract_routes import (  # noqa: PLC0415
            _is_abstain_signal,
            _provenance_from_flat,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"          import skipped ({type(exc).__name__})")
        return "session"

    recognised = _is_abstain_signal(REFUSAL)
    prov = _provenance_from_flat(REFUSAL)
    badge = str(getattr(prov.badge, "value", prov.badge))
    print(f"  LINK 1  _is_abstain_signal(refusal) = {recognised}")
    print(f"          provenance.layer           = {prov.layer}")
    print(f"          provenance.badge           = {prov.badge}")
    if recognised:
        print("          the refusal IS recognised - the first link is repaired")
    else:
        print("          Cortex half still open (Cortex#11)")
    return badge


def link_two() -> dict[str, object]:
    """DMS customer path: map a refused AskResponse the way POST /v1/chat/ask does."""
    from cortex_client.models import AskResponse  # noqa: PLC0415
    from dms_executor import map_ask_response_to_envelope  # noqa: PLC0415
    from dms_executor.envelope import assert_envelope_valid  # noqa: PLC0415

    resp = AskResponse.model_validate(
        {
            "answer": (
                "That question can't be answered inside this session's data grant "
                "(PathNotAllowed). Nothing was read."
            ),
            "audit_id": "aud_refused",
            "route": "refused",
            "provenance": {"badge": "session", "layer": "refused"},
            "sql_used": None,
            "rows": [],
            "abstained": False,
        }
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_r")
    print(f"  LINK 2  badge     = {env['badge']}")
    print(f"          abstained = {env['abstained']}")
    print(f"          text      = {str(env['text'])[:78]}")
    assert_envelope_valid(env)
    print("          assert_envelope_valid: PASSED")
    return env


def main() -> int:
    print("=== a manifest refusal, followed to the customer envelope ===")
    link_one()
    print()
    env = link_two()
    print()
    customer_ok = env["badge"] == "ABSTAIN" and env["abstained"] is True
    if not customer_ok:
        print("P0 REPRODUCED")
        print(f"  the engine read nothing and refused; the customer sees {env['badge']}")
        print(f"  with abstained={env['abstained']}, and every envelope invariant passes.")
        return 0
    print("NOT REPRODUCED on the customer path - DMS half closed (dms#66).")
    print("  Cortex LINK 1 may still be open; that is Cortex#11, not this script's P0.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
