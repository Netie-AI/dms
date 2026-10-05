"""Badge-label audit: is the badge MORE confident than the envelope's own evidence?

Derived from (read, not assumed):
  - envelope.py ``ALLOWED_BADGES`` / ``_BADGE_MAP`` / ``assert_envelope_valid`` (E1-E13)
  - executor/__init__.py ``_BADGE_MAP`` and ``_REFUSAL_ROUTES``
  - the lanes that stamp a badge by construction:
      verified_queries.py   route verified_query   -> L0_CERTIFIED
      demo_pack.py          route governed_metric  -> L1_GOVERNED_METRIC
      generative_ask.py     route generated        -> L2_VALIDATED (never L0/L1)
      session_followup.py   route followup         -> L2_VALIDATED
      bronze_sheet_ask.py   route bronze_sheet     -> L0_CERTIFIED (by construction)
      Executor.answer_user_sql route user_sql      -> L2_VALIDATED
      Cortex contract ask   route sql/query_skill/session/catalog/...: engine-assigned
  - demo_ask.py          ask_mode demo             -> L2_VALIDATED, but a demo answer is
                                                     never evidence for a confident badge

``badge_label_audit`` returns ``{"ok": bool, "reasons": [...], "notes": [...]}``.
``reasons`` are violations (the badge is more confident than the evidence). ``notes``
are informational (a by-construction badge the harness cannot verify).
"""

from __future__ import annotations

import re
from typing import Any

ALLOWED_BADGES = frozenset(
    {"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED", "L2_ANOMALOUS", "ABSTAIN"}
)
CONFIDENT_BADGES = frozenset({"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED", "L2_ANOMALOUS"})
CERTIFIED_BADGES = frozenset({"L0_CERTIFIED", "L1_GOVERNED_METRIC"})
REFUSAL_ROUTES = frozenset({"abstain", "blocked", "needs_clarification", "refused"})

#: route -> the confident badges that lane may legally stamp. A route absent from
#: this table is engine-assigned (Cortex contract ask) and only gets a note.
ROUTE_BADGES: dict[str, frozenset[str]] = {
    "generated": frozenset({"L2_VALIDATED"}),
    "user_sql": frozenset({"L2_VALIDATED"}),
    "followup": frozenset({"L2_VALIDATED"}),
    "verified_query": frozenset({"L0_CERTIFIED"}),
    "governed_metric": frozenset({"L1_GOVERNED_METRIC"}),
    "bronze_sheet": frozenset({"L0_CERTIFIED"}),
}
#: Routes whose badge is assigned by construction, not by evidence the harness can see.
BY_CONSTRUCTION_ROUTES = frozenset({"verified_query", "governed_metric", "bronze_sheet"})
MODEL_PLAN_ORIGINS = frozenset({"generate_sql", "ontology_ranking"})
MODEL_PLAN_SOURCES = frozenset({"ontology_plan", "bind_plan"})
_PLACEHOLDER_SQL = ("-- live ask (SQL not returned)",)
_DEMOTION_MARKERS = (
    "withheld",
    "shape mismatch",
    "scope conflict",
    "scope mismatch",
    "polarity mismatch",
    "all-time pad",
    "history pad",
)
#: A decimal or thousands-separated figure. Bare integers (years, counts) are not "figures".
_FIGURE = re.compile(
    r"(?<![A-Za-z0-9_])-?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+)(?![A-Za-z0-9_])"
)


def _empty(v: Any) -> bool:
    return v is None or v == [] or v == "" or v == {}


def badge_label_audit(envelope: dict[str, Any]) -> dict[str, Any]:
    """Flag an envelope whose badge outruns its evidence. Never raises."""
    reasons: list[str] = []
    notes: list[str] = []
    if not isinstance(envelope, dict):
        return {"ok": False, "reasons": ["envelope_not_a_mapping"], "notes": []}

    badge = envelope.get("badge")
    abstained = bool(envelope.get("abstained"))
    route = str(envelope.get("route") or "").strip().lower()
    rows = envelope.get("rows")
    values = envelope.get("values")
    sql = envelope.get("sql_used")
    text = str(envelope.get("text") or "")
    assumptions = [str(a) for a in (envelope.get("assumptions") or [])]
    ask_mode = str(envelope.get("ask_mode") or "").strip().lower()
    confident = badge in CONFIDENT_BADGES and not abstained

    if badge not in ALLOWED_BADGES:
        reasons.append(f"badge_not_allowed:{badge!r}")
    if abstained != (badge == "ABSTAIN"):
        reasons.append(f"abstained_badge_mismatch:abstained={abstained},badge={badge!r}")

    # A refusal route outranks any badge (executor/__init__.py _REFUSAL_ROUTES).
    if route in REFUSAL_ROUTES and badge != "ABSTAIN":
        reasons.append(f"refusal_route_with_confident_badge:route={route},badge={badge}")

    if badge == "ABSTAIN" or abstained:
        carried = [
            name
            for name, v in (
                ("values", values),
                ("rows", rows),
                ("contributing_sources", envelope.get("contributing_sources")),
                ("drillthrough_token", envelope.get("drillthrough_token")),
            )
            if not _empty(v)
        ]
        if carried:
            reasons.append(f"abstain_carries_data:{','.join(carried)}")
        if not _empty(sql):
            # build_answer_envelope keeps the executed SQL on a demoted answer (E9/E10/E12 ...).
            # It is provenance, not a figure: a note, not a violation.
            notes.append("abstain_keeps_sql_used:demoted_after_execution")
        if _FIGURE.search(text):
            reasons.append(f"abstain_text_states_a_figure:{_FIGURE.search(text).group(0)}")

    if confident:
        if _empty(rows) and _empty(values):
            reasons.append("confident_without_rows_or_values")
        elif _empty(rows):
            reasons.append("confident_without_rows")
        if not (isinstance(sql, str) and sql.strip()):
            reasons.append("confident_without_sql")
        elif sql.strip() in _PLACEHOLDER_SQL:
            reasons.append("confident_with_sql_not_returned")
        if ask_mode == "demo" or envelope.get("demo_fallback_used") is True:
            reasons.append(f"demo_answer_with_confident_badge:ask_mode={ask_mode or 'unset'}")
        if (
            envelope.get("demo_fallback_used") is True
            and envelope.get("demo_fallback_banner") is not True
        ):
            reasons.append("demo_fallback_without_banner")
        hit = next((m for m in _DEMOTION_MARKERS if any(m in a.lower() for a in assumptions)), None)
        if hit:
            reasons.append(f"confident_badge_over_a_demotion_note:{hit}")

        allowed_for_route = ROUTE_BADGES.get(route)
        if allowed_for_route is not None:
            if badge not in allowed_for_route:
                reasons.append(
                    f"badge_exceeds_route:route={route},badge={badge},"
                    f"legal={sorted(allowed_for_route)}"
                )
            if route in BY_CONSTRUCTION_ROUTES:
                notes.append(f"badge_by_construction:route={route}")
        elif route:
            notes.append(f"engine_assigned_badge:route={route}")
        else:
            notes.append("route_missing")

        plan_origin = str(envelope.get("plan_origin") or "").strip().lower()
        plan_source = str(envelope.get("plan_source") or "").strip().lower()
        model_planned = plan_origin in MODEL_PLAN_ORIGINS or plan_source in MODEL_PLAN_SOURCES
        if badge in CERTIFIED_BADGES and model_planned:
            reasons.append(
                f"certified_badge_on_model_plan:badge={badge},plan_origin={plan_origin or '-'},"
                f"plan_source={plan_source or '-'}"
            )

        if badge == "L2_VALIDATED" and route == "generated":
            # The generative lane stamps L2_VALIDATED only after validate + Cortex submit
            # + a ledger entry. Missing proof of any of those means "validated" is unearned.
            if not any(
                "executed via cortex submit after validate" in a.lower() for a in assumptions
            ):
                reasons.append("l2_validated_without_execution_note")
            audit_id = str(envelope.get("audit_id") or "")
            if not audit_id or audit_id == str(envelope.get("answer_id") or ""):
                reasons.append("l2_validated_without_ledger_audit_id")
        if badge == "L2_ANOMALOUS":
            notes.append("L2_ANOMALOUS_is_never_emitted_by_a_lane_on_main")
        if (
            badge == "L2_VALIDATED"
            and route in ("", "sql", "query_skill", "session")
            and _empty(sql)
        ):
            reasons.append("l2_validated_without_sql")

    return {"ok": not reasons, "reasons": reasons, "notes": notes}
