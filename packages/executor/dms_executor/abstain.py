"""The only abstain constructor in the served package.

Every abstain envelope is built here. The same function writes the
pipeline-failure ticket, and only after that write succeeds does it set
``ticket_id``. ``DMS_CLOOP_B`` off writes nothing and adds no key.

Import ``build_abstain`` from this module. #419, #420, and #421 use it
instead of building an abstain envelope themselves.
"""

from __future__ import annotations

import logging
import re
from typing import Any

_LOG = logging.getLogger(__name__)
# The bronze grant path still echoes its reason in ``text``. That echo is
# NAME-ECHO-01. This builder does not rewrite ``text``.
_RAW_TEXT = "text"


def _code_and_names(reason: str) -> tuple[str, list[str]]:
    """Known-code prefix, then the name tail.

    The tail is not a word list. A closed code drops whatever follows it,
    same as the ticket. Any other reason is left whole, including a known
    code whose tail is still part of the code (``unhonored_qualifier:...``).
    """
    from dms_executor.pipeline_failure import _CLOSED, _TOKEN, _known_codes

    known = _known_codes()
    codes: list[str] = []
    names: list[str] = []
    closed = False
    for part in str(reason or "").split(":"):
        token = part.strip()
        if not token:
            continue
        folded = token.casefold()
        if closed:
            names.append(token)
            continue
        if not (_TOKEN.fullmatch(folded) and folded in known):
            return "", []
        codes.append(folded)
        closed = folded in _CLOSED
    return ":".join(codes), names


def _names_of(*reasons: str | None) -> tuple[str, list[str]]:
    visible = ""
    names: list[str] = []
    for reason in reasons:
        if not isinstance(reason, str):
            continue
        code, tail = _code_and_names(reason)
        if code and not visible:
            visible = code
        elif code and tail:
            visible = code
        for part in tail:
            if part not in names:
                names.append(part)
    return visible, names


def _scrub_text(value: str, names: list[str]) -> str:
    """Drop each name in any case. Quotes and a schema prefix still contain it."""
    out = value
    for name in names:
        if len(name) < 2:
            continue
        out = re.sub(rf"(?i){re.escape(name)}", "", out)
    return out


def _scrub_value(value: Any, names: list[str]) -> Any:
    if isinstance(value, str):
        return _scrub_text(value, names)
    if isinstance(value, list):
        return [_scrub_value(item, names) for item in value]
    if isinstance(value, dict):
        return {
            key: item if key == _RAW_TEXT else _scrub_value(item, names)
            for key, item in value.items()
        }
    return value


def _bare_assumption(item: str, visible: str, names: list[str]) -> str:
    scrubbed = _scrub_text(item, names).strip()
    if visible and scrubbed.casefold().rstrip(":").strip() == visible:
        return visible
    return scrubbed


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
    **envelope: Any,
) -> dict[str, Any]:
    """Build one ABSTAIN envelope and, when the flag is on, its ticket.

    ``demote`` stamps a ticket on an envelope that was already built. A
    cascade trace failure also applies its abstain marks here, so no other
    module assigns an abstain badge.
    """
    visible, names = _names_of(reason, abstain_reason)
    if names:
        envelope = {
            key: value if key == _RAW_TEXT else _scrub_value(value, names)
            for key, value in envelope.items()
        }
        assumptions = envelope.get("assumptions")
        if isinstance(assumptions, list):
            envelope["assumptions"] = [
                _bare_assumption(str(item), visible, names)
                if visible
                else _scrub_text(str(item), names)
                for item in assumptions
            ]
        answer_id = envelope.get("answer_id")
        if isinstance(answer_id, str):
            envelope["answer_id"] = answer_id.strip().rstrip(":").strip() or answer_id
    if demote is not None:
        if names:
            for key in list(demote):
                if key == _RAW_TEXT:
                    continue
                demote[key] = _scrub_value(demote[key], names)
            if visible and isinstance(demote.get("abstain_reason"), str):
                demote["abstain_reason"] = visible
            held = demote.get("assumptions")
            if visible and isinstance(held, list):
                demote["assumptions"] = [
                    _bare_assumption(str(item), visible, names) for item in held
                ]
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

    shown_question = _scrub_text(question, names) if names else question
    if shown_question and "question" not in envelope:
        envelope["question"] = shown_question
    envelope["badge"] = "ABSTAIN"
    envelope["abstained"] = True
    env = build_answer_envelope(_from_builder=True, **envelope)
    if abstain_reason is not None:
        env["abstain_reason"] = visible or abstain_reason
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
