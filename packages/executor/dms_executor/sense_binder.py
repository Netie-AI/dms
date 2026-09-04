"""CCA-02 — sense binder from a curated synonym asset, not intent regex.

Senses: lease | buy | housing-rent. LLM may propose a candidate; this module
only CERTIFIES a binding that is on the pack, or ABSTAINS. It does not invent
a sense. Multi-word phrases beat leftover tokens so "housing rent" is not also
lease.
"""

from __future__ import annotations

import re
from typing import Any

# Curated pack. Closed list. Not a product-intent cascade.
SENSE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "housing-rent": (
        "housing rent",
        "housing-rent",
        "residential rent",
        "residential rental",
    ),
    "lease": ("lease", "leased", "leasing", "rent", "rental", "letting"),
    "buy": ("buy", "buying", "purchase", "purchased", "acquire"),
}

_EVIDENCE = "dms_executor.sense_binder.SENSE_SYNONYMS"


def _norm(question: str) -> str:
    return " ".join((question or "").lower().replace("-", " ").split())


def _consume_phrase(hay: str, needle: str) -> str | None:
    """Return hay with the first whole-phrase hit removed, or None."""
    pat = rf"(?<![a-z]){re.escape(needle)}(?![a-z])"
    if re.search(pat, hay) is None:
        return None
    return re.sub(pat, " ", hay, count=1)


def bind_sense(question: str) -> dict[str, Any]:
    """Return a CCA-01 sense-stage constraint. Never invents a binding."""
    q = _norm(question)
    remaining = q
    hits: dict[str, str] = {}
    # Longest phrases first so housing-rent consumes "rent" inside the phrase.
    phrases: list[tuple[str, str]] = []
    tokens: list[tuple[str, str]] = []
    for sense, syns in SENSE_SYNONYMS.items():
        for syn in syns:
            key = syn.replace("-", " ")
            if " " in key:
                phrases.append((sense, key))
            else:
                tokens.append((sense, key))
    phrases.sort(key=lambda x: len(x[1]), reverse=True)
    for sense, key in phrases:
        if sense in hits:
            continue
        nxt = _consume_phrase(remaining, key)
        if nxt is not None:
            hits[sense] = key
            remaining = nxt
    leftover = set(re.findall(r"[a-z]+", remaining))
    for sense, key in tokens:
        if sense in hits:
            continue
        if key in leftover:
            hits[sense] = key
    if len(hits) == 1:
        sense, syn = next(iter(hits.items()))
        return {
            "constraint_id": "sense_01",
            "type": "sense",
            "candidate": syn,
            "binding": sense,
            "evidence": [_EVIDENCE, syn],
            "status": "CERTIFIED",
            "reasons": [],
        }
    if len(hits) > 1:
        named = ", ".join(sorted(hits))
        return {
            "constraint_id": "sense_01",
            "type": "sense",
            "candidate": _norm(question),
            "binding": None,
            "evidence": [_EVIDENCE],
            "status": "ABSTAIN",
            "reasons": [
                f"sense is ambiguous between {named}; will not certify a number"
            ],
        }
    return {
        "constraint_id": "sense_01",
        "type": "sense",
        "candidate": _norm(question),
        "binding": None,
        "evidence": [_EVIDENCE],
        "status": "ABSTAIN",
        "reasons": [
            "no lease/buy/housing-rent synonym on the sense pack; missing vocabulary binding"
        ],
    }


def sense_certified(trace: list[dict[str, Any]]) -> bool:
    for item in trace:
        if item.get("type") == "sense":
            return item.get("status") == "CERTIFIED"
    return False
