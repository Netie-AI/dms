"""The only abstain constructor in the served package.

Every abstain envelope is built here. The same function writes the
pipeline-failure ticket, and only after that write succeeds does it set
``ticket_id``. ``DMS_CLOOP_B`` off writes nothing and adds no key.

Import ``build_abstain`` from this module. #419, #420, and #421 use it
instead of building an abstain envelope themselves.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Any

from dms_executor.name_echo import hide_echo

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
    """One ticket when the flag is on. A raise leaves ``env`` untouched."""
    from cortex_client.compute import cloop_b_enabled

    if not cloop_b_enabled():
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
    ticket_reason: str | None = None,
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
            reason=ticket_reason or reason,
            question=question,
            sql=sql,
            retries=retries,
            stage=stage,
            ask_id=ask_id,
        )
        return hide_echo(demote, ticket_reason or reason)

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
        reason=ticket_reason or reason,
        question=question,
        sql=sql if isinstance(sql, str) else None,
        retries=retries,
        stage=stage,
        ask_id=ask_id,
    )
    return hide_echo(env, ticket_reason or reason)


_SERVED_UNGRANTED_TEXT = (
    "I can't answer this: the query behind this answer read a table this "
    "Space does not grant. No figure was returned."
)


def _quoted(parts: tuple[str, ...]) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in parts)


def refuse_ungranted_answer(
    env: dict[str, Any],
    *,
    question: str = "",
    grants: Collection[str] | None = None,
) -> dict[str, Any]:
    """Abstain when an answer's ``sql_used`` reads a relation the turn does not grant.

    A hit is a grant breach or a misread grant set. Either way the figure
    is not shipped and the source is not hidden under the badge. The
    ticket carries the names; the envelope does not. ``env`` is returned
    unchanged when nothing is ungranted.
    """
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.name_echo import served_ungranted

    names = served_ungranted(env, grants)
    if not names:
        return env
    sql = env.get("sql_used")
    reason = "ungranted:" + ",".join(_quoted(parts) for parts in names)
    # The served sentence hides the name. A qualified miss also keeps the
    # structural code, so a caller that reads the code still sees it.
    codes = ["ungranted"]
    if isinstance(sql, str) and sql.strip():
        from dms_executor.grant_struct import customer_grant_reason, serve_gap
        from dms_executor.name_echo import _dialect, _granted

        gap = serve_gap(
            sql,
            grantable=set(_granted(grants)),
            dialect=_dialect(),
            cite_missing=False,
        )
        shown = customer_grant_reason(gap) if isinstance(gap, str) else ""
        if shown and shown not in codes:
            codes.append(shown)
    out = build_abstain(
        reason=reason,
        question=question,
        sql=sql if isinstance(sql, str) else None,
        stage="served_sql",
        answer_id=str(env.get("answer_id") or "ans_served_ungranted"),
        text=_SERVED_UNGRANTED_TEXT,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[
            "served SQL read a relation outside this Space's grant",
            *codes,
        ],
        abstain_reason="ungranted",
        audit_id=env.get("audit_id"),
        as_of=env.get("as_of"),
        space_id=env.get("space_id"),
        session_id=env.get("session_id"),
        ask_mode=str(env.get("ask_mode") or "live"),
        route=env.get("route"),
    )
    loop = env.get("loop")
    if isinstance(loop, list) and loop:
        out["loop"] = loop
        hide_echo(out, reason, grants=grants)
    assert_envelope_valid(out)
    return out


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
