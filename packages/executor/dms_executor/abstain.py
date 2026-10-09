"""The only abstain constructor in the served package.

Every abstain envelope is built here. The same function writes the
pipeline-failure ticket, and only after that write succeeds does it set
``ticket_id``. ``DMS_CLOOP_B`` off writes nothing and adds no key.
An intent-spec abstain (``stage="intent_spec"``) always tickets, because
that exit is armed by ``DMS_INTENT_SPEC``, not by the loop flag.

Import ``build_abstain`` from this module. #419, #420, and #421 use it
instead of building an abstain envelope themselves.
"""

from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)


def _ask_id(env: dict[str, Any], ask_id: str | None) -> str:
    raw = ask_id
    if not isinstance(raw, str) or not raw.strip():
        raw = env.get("audit_id")
    if not isinstance(raw, str) or not raw.strip():
        raw = env.get("answer_id")
    return raw.strip() if isinstance(raw, str) else ""


def _stamp_ticket(
    env: dict[str, Any],
    *,
    reason: str,
    question: str,
    sql: str | None,
    retries: int,
    stage: str,
    ask_id: str | None,
) -> None:
    """One ticket when the loop flag is on, or this is an intent-spec exit.

    A raise leaves ``env`` untouched.
    """
    from cortex_client.compute import cloop_b_enabled

    if not cloop_b_enabled() and stage != "intent_spec":
        return
    try:
        from dms_executor.pipeline_failure import log_pipeline_failure_ticket

        ticket_id = log_pipeline_failure_ticket(
            reason=reason,
            question=question,
            sql=sql,
            retries=retries,
            stage=stage,
            ask_id=_ask_id(env, ask_id),
        )
    except Exception:
        _LOG.warning("pipeline_failure ticket was not written")
        return
    if isinstance(ticket_id, str) and ticket_id:
        env["ticket_id"] = ticket_id


def build_abstain(
    *,
    reason: str,
    question: str = "",
    sql: str | None = None,
    retries: int = 0,
    stage: str = "unspecified",
    ask_id: str | None = None,
    abstain_reason: str | None = None,
    demote: dict[str, Any] | None = None,
    **envelope: Any,
) -> dict[str, Any]:
    """Build one ABSTAIN envelope and, when the flag is on, its ticket.

    ``demote`` stamps a ticket on an envelope that was already built. A
    cascade trace failure also applies its abstain marks here, so no other
    module assigns an abstain badge.
    """
    if demote is not None:
        if "text" in envelope or "demote_note" in envelope:
            demote["constraint_trace"] = []
            demote["badge"] = "ABSTAIN"
            demote["abstained"] = True
            demote["values"] = []
            demote["rows"] = []
            demote["sql_used"] = None
            if "text" in envelope:
                demote["text"] = envelope["text"]
            note = envelope.get("demote_note")
            if isinstance(note, str):
                assumptions = demote.get("assumptions")
                if not isinstance(assumptions, list):
                    assumptions = []
                    demote["assumptions"] = assumptions
                assumptions.append(note)
        _stamp_ticket(
            demote,
            reason=reason,
            question=question,
            sql=sql,
            retries=retries,
            stage=stage,
            ask_id=ask_id,
        )
        return demote

    from dms_executor.envelope import build_answer_envelope

    if question and "question" not in envelope:
        envelope["question"] = question
    envelope["badge"] = "ABSTAIN"
    envelope["abstained"] = True
    env = build_answer_envelope(_from_builder=True, **envelope)
    if abstain_reason is not None:
        env["abstain_reason"] = abstain_reason
    _stamp_ticket(
        env,
        reason=reason,
        question=question,
        sql=sql if isinstance(sql, str) else None,
        retries=retries,
        stage=stage,
        ask_id=ask_id,
    )
    return env


def _envelope_reason(env: dict[str, Any]) -> str:
    raw = env.get("abstain_reason")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    prefix = "GEN-01: "
    for item in env.get("assumptions") or []:
        text = str(item)
        if text.startswith(prefix):
            reason = text[len(prefix) :].strip()
            if reason:
                return reason
    return "unspecified"


def backstop_missing_ticket(env: dict[str, Any], question: str) -> None:
    """Ticket an abstain that reached the boundary with no ticket id.

    Off entirely when ``DMS_CLOOP_B`` is off. Does not change ``env``.
    A raise does not change ``env``.
    """
    from cortex_client.compute import cloop_b_enabled

    if not cloop_b_enabled():
        return
    if not env.get("abstained"):
        return
    ticket_id = env.get("ticket_id")
    if isinstance(ticket_id, str) and ticket_id.strip():
        return
    route = env.get("route")
    stage = route.strip() if isinstance(route, str) and route.strip() else "unspecified"
    sql_used = env.get("sql_used")
    try:
        from dms_executor.pipeline_failure import log_pipeline_failure_ticket

        log_pipeline_failure_ticket(
            reason=f"ticket_missing:{_envelope_reason(env)}",
            question=question,
            sql=sql_used if isinstance(sql_used, str) else None,
            retries=0,
            stage=stage,
            ask_id=_ask_id(env, None),
        )
    except Exception:
        _LOG.warning("pipeline_failure ticket was not written")
