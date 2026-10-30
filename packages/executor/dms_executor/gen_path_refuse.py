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
        "insights_call_cap",
        "insights_no_sql_no_ranking",
        "insights_bearer_missing",
        "insights_bearer_insecure_transport",
        "unhonored_qualifier",
        "ungrounded_measure",
        # ONTO-DERIVE-01 (dms#277): a SQL-source Space's own ontology.
        "unverified_join",
        "no_declared_measure",
        # dms#284: the second named abstain, and the two ask-time rule 12 / empty heads.
        "no_declared_link",
        "unmatched_filter_value",
        "ambiguous_filter_value",
        "empty_aggregate",
        "too_many_groups",
        "masked_figure",
        "ontology_store_unavailable",
        "untyped_numeric",
        "ambiguous_measure",
        "rank_window_unhandled_terms",
        "ungrounded_qualifier",
        "rank_window_entity_mismatch",
        "rank_window_open_ended",
        "unrequested_offset",
        "loop_exhausted",
        "empty_result_unverified",
        "no_sql",
        "multi_statement",
        "warehouse_missing",
    }
)

_GENERIC_ABSTAIN = (
    "I cannot certify an ontology-grounded query for that question, "
    "so I am not executing one."
)

#: The customer sentence shows the head only. The measure name, the relation names and the
#: filter value (all of them chosen by the model or typed by the asker) stay in assumptions.
_HEAD_ONLY_GAPS = frozenset(
    {
        "no_declared_measure",
        "no_declared_link",
        "unmatched_filter_value",
        "ambiguous_filter_value",
        "empty_aggregate",
        "masked_figure",
    }
)

#: One plain sentence per head, said first, in the words a customer uses. It names nothing
#: the model chose. The ``(gap: <head>)`` clause after it is unchanged, so every existing
#: substring assertion on the head keeps holding.
_PLAIN_SENTENCE = {
    "no_declared_measure": "No steward has confirmed a measure that answers this question.",
    "no_declared_link": (
        "The data does not declare a link between these tables, so I will not join them."
    ),
    "unmatched_filter_value": "That value does not appear in the data exactly as written.",
    "ambiguous_filter_value": (
        "That value appears in the data under more than one spelling, so I will not pick one."
    ),
    "empty_aggregate": "The measure has no values for this question.",
    "too_many_groups": "That breakdown has more groups than I can state completely.",
    "masked_figure": "That figure would be hidden by the privacy layer, so I cannot state it.",
}

#: Abstains that offer the Space's usable confirmed measures as what to ask instead.
SUGGESTING_GAPS = frozenset({"no_declared_measure", "no_declared_link"})


def gap_reason_name(reason: str) -> str | None:
    """Head token of a compile/ranking refusal, or None if not a named gap."""
    head = str(reason or "").strip().split(":", 1)[0].strip()
    return head if head in GAP_REASONS else None


def customer_abstain_text(reason: str) -> str:
    """Rendered ABSTAIN text. Named gaps appear in the sentence (hard rule 10)."""
    gap = str(reason or "").strip()
    if gap.startswith("currency_mismatch:"):
        body = gap.split(":", 1)[1].strip()
        return body if body else _GENERIC_ABSTAIN
    head = gap_reason_name(gap)
    if head is None:
        return _GENERIC_ABSTAIN
    shown = head if head in _HEAD_ONLY_GAPS else gap
    plain = _PLAIN_SENTENCE.get(head)
    return (
        (f"{plain} " if plain else "")
        + "I cannot certify an ontology-grounded query for that question "
        f"(gap: {shown}), so I am not executing one."
    )


def ranking_missing_metric_gap(
    question: str,
    payload: dict[str, Any] | None,
    *,
    onto: Ontology | None,
    demo: bool = True,
) -> str | None:
    """Named unknown_measure when Cortex ranked an intended metric missing on DMS.

    Overlay recovery (ranked slots that compile) is not a gap. Transport miss
    (no Insights ontology ranking) is not a gap — product Cortex.ask still runs.
    ``demo=False`` (a Space with its own ontology) reads no demo-pack aliases, the same
    rule ``ontology_plan_from_ranking`` follows.
    """
    if not isinstance(payload, dict) or onto is None or not onto.measures:
        return None
    prefer = intent_slots(question, onto).get("measure")
    prefer_s = str(prefer).strip() if prefer else ""
    aliases = load_measure_aliases() if demo else {}
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
    "SUGGESTING_GAPS",
    "customer_abstain_text",
    "gap_reason_name",
    "ranking_missing_metric_gap",
]
