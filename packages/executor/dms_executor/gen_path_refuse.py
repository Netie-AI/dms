"""GEN-PATH-REFUSE-01 — fail-closed named gap when the ontology cannot answer.

Compile already refuses unknown_measure / no_path. This module names that gap
on the customer envelope and treats a Cortex-ranked intended metric that does
not resolve onto the verified DMS ontology as the same refusal — never a
keyword bind or a confident badge.

Not GEN-03 ask_path 400 containment. Not COMPLETE.
"""

from __future__ import annotations

from typing import Any

from cortex_client.compute import (
    query_plan_from_insights_ranking,
    ranking_is_noise,
    resolve_ranked_measure,
)

from dms_executor.ontology import Ontology
from dms_executor.semantic_retrieve import intent_slots, load_measure_aliases
from dms_executor.sql_grain import GRAIN_REASONS, grain_abstain_text

GAP_REASONS = frozenset(
    {
        "unknown_measure",
        "no_path",
        "unknown_object",
        "unknown_column",
        "ontology_unverified",
        "missing_metric",
        "missing_ontology",
        "missing_join",
        "fanout_refused",
        "ambiguous_path",
        "unknown_link",
        "coverage_invalid",
        "insights_unarmed",
        "insights_refused",
        "insights_unauthorized",
        "insights_timeout",
        "insights_no_sql_no_ranking",
        "insights_bearer_missing",
        "insights_bearer_insecure_transport",
        "unhonored_qualifier",
    }
)


def gap_reason_name(reason: str) -> str | None:
    """Head token of a compile/ranking refusal, or None if not a named gap."""
    head = str(reason or "").strip().split(":", 1)[0].strip()
    return head if head in GAP_REASONS else None


def customer_abstain_text(reason: str) -> str:
    """Rendered ABSTAIN text. Named gaps appear in the sentence (hard rule 10)."""
    gap = str(reason or "").strip()
    if gap.startswith("currency_mismatch:"):
        body = gap.split(":", 1)[1].strip()
        if body:
            return body
    if gap.split(":", 1)[0].strip() in GRAIN_REASONS:
        return grain_abstain_text(gap)
    if not gap:
        # SPACE-GEN-01: an ABSTAIN always names why. An empty reason is itself
        # the defect, so say so rather than render the unnamed sentence.
        gap = "abstain_reason_missing"
    # Named gaps and every other refusal reason alike appear in the sentence.
    # The unnamed "cannot certify" text hid 110 of 500 BIRD refusals' causes.
    return (
        "I cannot certify an ontology-grounded query for that question "
        f"(gap: {gap}), so I am not executing one."
    )


def ranking_missing_metric_gap(
    question: str,
    payload: dict[str, Any] | None,
    *,
    onto: Ontology | None,
) -> str | None:
    """Named unknown_measure when Cortex ranked an intended metric missing on DMS.

    Overlay recovery (ranked slots that compile) is not a gap. Transport miss
    (no Insights ontology ranking) is not a gap — product Cortex.ask still runs.
    """
    if not isinstance(payload, dict) or onto is None or not onto.measures:
        return None
    prefer = intent_slots(question, onto).get("measure")
    prefer_s = str(prefer).strip() if prefer else ""
    aliases = load_measure_aliases()
    specs = {name: (m.description or "") for name, m in onto.measures.items()}
    allowed = set(onto.measures)
    ranked = query_plan_from_insights_ranking(
        payload,
        allowed,
        aliases=aliases,
        specs=specs,
        prefer=prefer_s or None,
        question=question,
    )
    if isinstance(ranked, dict):
        raw = ranked.get("query_plan")
        if isinstance(raw, dict):
            measure = str(raw.get("measure") or "").strip()
            if measure and measure not in onto.measures:
                return f"unknown_measure: no measure named {measure!r}"
        return None
    onto_block = payload.get("ontology")
    if not isinstance(onto_block, dict):
        return None
    lock = prefer_s or None
    for row in onto_block.get("metrics") or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or "").strip()
        if not mid:
            continue
        resolved = resolve_ranked_measure(
            mid,
            allowed,
            aliases=aliases,
            specs=specs,
            prefer=lock,
        )
        if resolved is not None:
            continue
        if ranking_is_noise(mid, question=question, prefer=lock):
            continue
        return f"unknown_measure: no measure named {mid!r}"
    return None


__all__ = [
    "GAP_REASONS",
    "customer_abstain_text",
    "gap_reason_name",
    "ranking_missing_metric_gap",
]
