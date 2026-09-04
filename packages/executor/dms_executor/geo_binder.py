"""CCA-04 — geo region binder. Certify SEA only against landed membership.

Does not invent country lists. Default SEA pack is empty until extract-only SoT
lands dim values. Tests inject members + landed dims. Cortex pack rewrite HELD.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# Empty on purpose. ASEAN-10 here would be invented membership.
LANDED_REGION_MEMBERS: dict[str, tuple[str, ...]] = {
    "SEA": (),
}

_EVIDENCE = "dms_executor.geo_binder.LANDED_REGION_MEMBERS"

# Longest first. Whole-word "sea" so "search" does not match.
_REGION_PHRASES: dict[str, tuple[str, ...]] = {
    "SEA": ("southeast asia", "south east asia", "across sea", "sea"),
}


def _norm(question: str) -> str:
    return " ".join((question or "").lower().replace("-", " ").split())


def _hit(hay: str, needle: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", hay) is not None


def bind_geo_region(
    question: str,
    *,
    region_members: Mapping[str, Sequence[str]] | None = None,
    landed_dim_values: Sequence[str] | None = None,
    proposed_members: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a CCA-01 geo constraint. Never invents country membership."""
    q = _norm(question)
    region = next(
        (name for name, phrases in _REGION_PHRASES.items() if any(_hit(q, p) for p in phrases)),
        None,
    )
    if region is None:
        return _abstain(q, "no SEA region in the question; missing geo binding")
    pack_src = region_members if region_members is not None else LANDED_REGION_MEMBERS
    pack = tuple(str(x) for x in (pack_src.get(region) or ()))
    if not pack:
        return _abstain(
            q,
            "missing SEA membership pack; will not invent countries",
        )
    if proposed_members is not None:
        proposed = tuple(str(x) for x in proposed_members)
        extra = [m for m in proposed if m not in pack]
        if extra:
            return _abstain(
                q,
                "proposed SEA members are not on the landed pack; will not invent countries",
            )
        pack = proposed
    landed = {str(x) for x in (landed_dim_values or ())}
    bound = [m for m in pack if m in landed]
    missing = [m for m in pack if m not in landed]
    if missing or not bound:
        return _abstain(
            q,
            "SEA membership is not on the landed dim; will not invent countries",
        )
    bound_s = ",".join(bound)
    return {
        "constraint_id": "geo_01",
        "type": "geo",
        "candidate": region,
        "binding": bound_s,
        "evidence": [_EVIDENCE, region, *bound],
        "status": "CERTIFIED",
        "reasons": [],
    }


def _abstain(candidate: str, reason: str) -> dict[str, Any]:
    return {
        "constraint_id": "geo_01",
        "type": "geo",
        "candidate": candidate,
        "binding": None,
        "evidence": [_EVIDENCE],
        "status": "ABSTAIN",
        "reasons": [reason],
    }
