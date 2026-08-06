"""One place that decides what a compliance decision means for a mutation.

Every write route called ``compliance_gate`` and then hand-rolled the same
allowance::

    if not decision.allowed and decision.reason not in {
        "gate_unavailable",
        "gate_task_unknown",
    }:
        raise HTTPException(status_code=403, detail=decision.reason)

Read it out loud: refuse when refused, *except* when the gate could not be
reached or did not recognise the task — which are exactly the two cases where no
compliance decision was made at all. With Cortex down, every mutation in the
product proceeded ungated, and the ledger append failed in the same outage, so
nothing recorded that it had happened.

``cortex_client.gate`` already knew better. Its 404 branch carries the comment
"Task catalog miss - fail closed for mutations", and the skeleton ping route does
fail closed. The write routes were overriding their own client, nine times, by
copy-paste. So the posture lives here now and the routes ask rather than decide:
the next write route inherits fail-closed instead of inheriting the exception.

**This refuses work that used to succeed.** A mutation attempted while Cortex is
unreachable now returns 403 rather than quietly applying. That is the intended
trade: an ungated, unaudited write is not "legitimate work" being refused
(R-0005), it is the failure the gate exists to prevent. There is deliberately no
bypass flag — a documented hole is still a hole, and the fix for a local write is
to start Cortex, not to skip compliance.

Reads are a different question and keep their own posture: ``chat.ask`` may soft
-fallback when the gate is unreachable, because refusing to answer a question is
not the same risk as applying an unrecorded change. Pass ``mutation=False`` to
say so explicitly at the call site.
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import HTTPException

#: Reasons that mean "no compliance decision was reached", as opposed to "the
#: decision was no". Allowing these through is what made the gate fail open.
NO_DECISION_REASONS = frozenset({"gate_unavailable", "gate_task_unknown"})


class _Decision(Protocol):
    allowed: bool
    reason: str | None


def enforce(decision: _Decision | Any, *, mutation: bool = True) -> None:
    """Raise unless ``decision`` actually permits the call.

    403 for every refusal, including an unreachable gate. A 503 would arguably
    describe "gate down" better, but the repo's existing fail-closed precedent
    (``/v1/skeleton/ping``, asserted in tests/test_smoke.py) answers 403 with the
    reason in ``detail``, and one status code for one meaning beats a more
    precise code that only some routes use.
    """
    if getattr(decision, "allowed", False):
        return

    reason = getattr(decision, "reason", None) or "gate_denied"
    if reason in NO_DECISION_REASONS and not mutation:
        # Read path: no decision available, and the risk of answering is not the
        # risk of writing. The caller has opted into this explicitly.
        return
    raise HTTPException(status_code=403, detail=reason)


__all__ = ["NO_DECISION_REASONS", "enforce"]
