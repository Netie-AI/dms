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
    if gap.startswith("source_truncated:"):
        tables = gap.split(":", 1)[1].strip() or "a source"
        return (
            f"I cannot certify that answer: {tables} was only partly loaded (the "
            "ingest row cap cut it short), so a figure over it would describe part "
            f"of the data as all of it (gap: {gap}). I am not executing it."
        )
    if not gap:
        # SPACE-GEN-01: an ABSTAIN always names why. An empty reason is itself
        # the defect, so say so rather than render the unnamed sentence.
        gap = "abstain_reason_missing"
    head = gap.split(":", 1)[0].strip()
    if head == REASON_SPACE_ID_EMPTY:
        return (
            "I can't answer that: the request named no Space (an empty Space id), "
            "so no tables are granted to it and nothing was read "
            f"(gap: {REASON_SPACE_ID_EMPTY})."
        )
    if head == REASON_SPACE_NOT_FOUND:
        return (
            "I can't answer that: that Space does not exist, so no tables are "
            f"granted to it and nothing was read (gap: {REASON_SPACE_NOT_FOUND})."
        )
    if head.startswith("insights_"):
        return (
            "I cannot certify an ontology-grounded query for that question: the "
            "engine's query generator returned nothing usable "
            f"(gap: {customer_gap_label(gap)}), so I am not executing one."
        )
    # Named gaps and every other refusal reason alike appear in the sentence.
    # The unnamed "cannot certify" text hid 110 of 500 BIRD refusals' causes.
    return (
        "I cannot certify an ontology-grounded query for that question "
        f"(gap: {customer_gap_label(gap)}), so I am not executing one."
    )


#: SPACE-GEN-01 round 2: a request whose ``space_id`` is empty names no Space.
REASON_SPACE_ID_EMPTY = "space_id_empty"
#: A ``space_id`` the Space store does not hold.
REASON_SPACE_NOT_FOUND = "space_not_found"

#: Reasons DMS writes itself, verbatim, with nothing a caller or a model chose
#: inside them. Safe to show the customer as written.
_SAFE_FIXED_REASONS = frozenset(
    {
        "abstain_reason_missing",
        "compile_failed",
        "compute abstained (unsure)",
        "existential many-to-many filter: ask path will not choose a reading",
        "exact-match miss: not a certified VQ/pack hit",
        "insights generate did not return a typed plan or SQL",
        "keep_gt_empty",
        "ledger_append_failed",
        "ledger_entry_missing",
        "ledger_hash_missing",
        "predictive: history is not a forecast",
        "query_plan was not typed",
        "query_sql was empty",
        "question is too vague or time-unbounded to ground",
        "retrieve bind abstained (unsure)",
        "sql_unanalysable",
        "submit_failed",
        "submit_had_no_rows",
        "uncertified paraphrase: not a generative certify boundary",
        "warehouse_missing",
        "year 2099 is not a certified period; all-time history is not that year",
        REASON_SPACE_ID_EMPTY,
        REASON_SPACE_NOT_FOUND,
    }
)

#: Heads of DMS-written reasons whose tail carries runtime detail (the retrieve
#: method list, the lane's own explanation). The head is shown; the tail stays
#: in ``assumptions``.
_SAFE_HEADS = {
    "query_plan was not typed after retrieve": "query_plan was not typed",
    "generative miss": "generative miss",
    "ungranted": "ungranted_table",
    "ambiguous_table": "ambiguous_table",
    "relation_name_invalid": "relation_name_invalid",
}

#: What the customer reads for a reason DMS did not write itself.
GAP_UNNAMED_REFUSAL = "generation_refused"


def customer_gap_label(reason: str) -> str:
    """The reason as a customer reads it: named, without guard internals.

    The full reason stays in the envelope ``assumptions`` (``GEN-01: ...``) for
    the audit trail. The rendered text must not tell a caller which security
    guard tripped (``hostile_sql:path_not_allowed``) or which engine exception
    class a probe produced (``explain:BinderException``).

    SPACE-GEN-01 round 2: nor may it echo a relation a generated query invented
    (``ungranted:secret_salary`` renders ``ungranted_table``). A reason that is
    neither a named gap nor a string DMS writes verbatim renders the named
    ``generation_refused``; it is never passed through.
    """
    gap = str(reason or "").strip()
    head, _, rest = gap.partition(":")
    validate = head.strip() == "validate"
    prefix = "validate:" if validate else ""
    inner = rest.strip() if validate else gap
    if inner.startswith("hostile_sql"):
        return f"{prefix}unsafe_sql"
    if inner.startswith("explain:"):
        return f"{prefix}sql_does_not_run"
    inner_head = inner.split(":", 1)[0].split("(", 1)[0].strip()
    if inner_head in _SAFE_HEADS:
        return prefix + _SAFE_HEADS[inner_head]
    if inner in _SAFE_FIXED_REASONS:
        return prefix + inner
    if gap_reason_name(inner) is not None or inner_head in GRAIN_REASONS:
        # Named compile / ranking / Insights gaps render as they always have.
        return prefix + inner
    if inner_head == "source_truncated":
        return prefix + inner
    return prefix + GAP_UNNAMED_REFUSAL


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
    "GAP_UNNAMED_REFUSAL",
    "REASON_SPACE_ID_EMPTY",
    "REASON_SPACE_NOT_FOUND",
    "customer_abstain_text",
    "customer_gap_label",
    "gap_reason_name",
    "ranking_missing_metric_gap",
]
