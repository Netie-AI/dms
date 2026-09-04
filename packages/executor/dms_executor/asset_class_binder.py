"""CCA-03 — asset-class binder. Certify only against landed encodings.

Does not invent commercial/residential membership. Default pack is empty until
extract-only SoT lands dim values. Tests inject encodings + landed dims.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# Empty on purpose. Filling this with guessed labels is inventing encodings.
LANDED_CLASS_ENCODINGS: dict[str, tuple[str, ...]] = {
    "commercial": (),
    "residential": (),
}

_EVIDENCE = "dms_executor.asset_class_binder.LANDED_CLASS_ENCODINGS"

_CLASS_PHRASES: dict[str, tuple[str, ...]] = {
    "commercial": ("commercial only", "commercial-only", "commercial"),
    "residential": ("residential",),
}
_EXCLUDE_RESIDENTIAL = (
    "ignore residential",
    "excluding residential",
    "exclude residential",
)


def _norm(question: str) -> str:
    return " ".join((question or "").lower().replace("-", " ").split())


def _hit(hay: str, needle: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", hay) is not None


def bind_asset_class(
    question: str,
    *,
    encodings: Mapping[str, Sequence[str]] | None = None,
    landed_dim_values: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a CCA-01 asset_class constraint. Never invents membership."""
    q = _norm(question)
    asked: list[str] = []
    exclude_res = any(_hit(q, p) for p in _EXCLUDE_RESIDENTIAL)
    for cls, phrases in _CLASS_PHRASES.items():
        if any(_hit(q, p) for p in phrases):
            asked.append(cls)
    if exclude_res:
        asked = [c for c in asked if c != "residential"]
        if "commercial" not in asked:
            asked.append("commercial")
    asked = list(dict.fromkeys(asked))
    if not asked:
        return _abstain(
            q,
            "no commercial/residential class in the question; missing class binding",
        )
    if len(asked) > 1:
        return _abstain(
            q,
            "asset class is ambiguous between commercial, residential; "
            "will not certify a class filter",
        )
    cls = asked[0]
    pack = encodings if encodings is not None else LANDED_CLASS_ENCODINGS
    members = tuple(str(x) for x in (pack.get(cls) or ()))
    if not members:
        return _abstain(
            q,
            f"missing encoding for {cls}; will not invent a class filter",
        )
    landed = {str(x) for x in (landed_dim_values or ())}
    bound = [m for m in members if m in landed]
    missing = [m for m in members if m not in landed]
    if missing or not bound:
        return _abstain(
            q,
            f"{cls} encoding is not on the landed dim; will not invent membership",
        )
    return {
        "constraint_id": "asset_class_01",
        "type": "asset_class",
        "candidate": cls,
        "binding": ",".join(bound),
        "evidence": [_EVIDENCE, cls, *bound],
        "status": "CERTIFIED",
        "reasons": [],
    }


def _abstain(candidate: str, reason: str) -> dict[str, Any]:
    return {
        "constraint_id": "asset_class_01",
        "type": "asset_class",
        "candidate": candidate,
        "binding": None,
        "evidence": [_EVIDENCE],
        "status": "ABSTAIN",
        "reasons": [reason],
    }
