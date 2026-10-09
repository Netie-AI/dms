"""One pipeline-failure ticket list for DMS.

``log_pipeline_failure_ticket`` is the shared writer. #409 and #419 call
this function and do not keep a second logger. The parameter names are
``reason``, ``question``, ``sql``, ``retries``, ``stage``, and ``ask_id``.
There is no alias: ``rejected_sql`` and ``retry_count`` are not parameters.

``build_abstain`` calls it when ``DMS_CLOOP_B`` is on. Each line is logged
at WARNING as ``pipeline_failure`` plus one JSON object, on the logger
``dms_executor.pipeline_failure``. WARNING is what a process with no log
handler still writes to stderr. The return value is the ticket id from
``mint_id`` (letters only). The same group keeps the same id. The envelope
stamps that id only after this function returns.

The line never contains the question text or the SQL text. It carries
``question_hash``, ``stage``, ``retries``, and ``ask_id``. ``ask_id`` is
the envelope ``audit_id`` (or ``answer_id`` when that is the record id) so
a fixer can pull the ask through its existing access-controlled record.
``reason`` is a named reason code. A database or engine error message is
not a code and is dropped. Unknown prose becomes ``unspecified``.

Group key is ``(reason code, sha256 of the normalised masked question)``.
The raw question is never the key. Normalisation is the masked text,
casefolded, with whitespace collapsed. Masking is ``fail_closed_mask_payload``,
then bearer and secret redaction, and it is used only to build the hash.

When masking fails, the group key is a new per-occurrence id. It is never
the hash of an empty string, the text fields stay absent, and the line
sets ``mask_failed`` true. Those occurrences are not merged with each
other or with any successful group.

The live groups sit in an in-process map guarded by a lock. The map holds
at most ``PIPELINE_FAILURE_CAP`` groups. A group not touched for
``PIPELINE_FAILURE_TTL_S`` seconds is dropped on the next call (sliding
window). A new group past the cap evicts the least-recently-used group.
An evicted or expired group that shows up again is a new first occurrence.

Emission. The first time a group is seen, one line is logged immediately
with ``count`` 1. Each repeat increments that count and logs one snapshot
with the same ``group`` id and the new count. The ticket list is not the
raw log: keep the snapshot with the greatest ``count`` for each ``group``.
That is one ticket per reason code and question. A retry storm updates
that ticket instead of opening another one. A mask failure is its own
group, so its count stays 1.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any

_LOG = logging.getLogger(__name__)
PIPELINE_FAILURE_CAP = 256
PIPELINE_FAILURE_TTL_S = 600.0
_LOCK = threading.Lock()
# group id -> (count, last_seen monotonic, ticket id)
_GROUPS: OrderedDict[str, tuple[int, float, str]] = OrderedDict()
_TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")
# Closed set. A token that is not here is data or an error message.
_CODES = frozenset(
    {
        "abstain",
        "as_of",
        "checker",
        "clarify",
        "clarify_binding_ungranted",
        "clarify_column_unreadable",
        "clarify_expired",
        "clarify_option_unknown",
        "clarify_unknown",
        "db_error",
        "empty_result_unverified",
        "envelope_demoted",
        "explain",
        "filter_dropped",
        "filter_parse_failed",
        "generate",
        "hostile_sql",
        "insights_call_cap",
        "insights_timeout",
        "intent_spec_mismatch",
        "intent_spec_unverified",
        "loop_exhausted",
        "multi_statement",
        "no_sql",
        "none_fits",
        "ontology",
        "openvault_mint",
        "path_not_allowed",
        "reserved_param",
        "retry",
        "sql_not_analyzable",
        "statement_not_allowed",
        "submit_failed",
        "ticket_missing",
        "ungranted",
        "value_exists_pending",
        "warehouse_missing",
        "writer_unavailable",
    }
)
# The tail after these codes is an error message or a list of names.
_CLOSED = frozenset(
    {
        "db_error",
        "explain",
        "insights_call_cap",
        "intent_spec_mismatch",
        "intent_spec_unverified",
        "ungranted",
    }
)


def _reset_pipeline_failures() -> None:
    with _LOCK:
        _GROUPS.clear()


def _normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def _known_codes() -> frozenset[str]:
    """Allowlist plus the named gap reasons. Prose still fails the token check."""
    from dms_executor.gen_path_refuse import GAP_REASONS

    return _CODES | GAP_REASONS


def _reason_code(raw: str) -> str:
    """Named code only. Error prose and unknown tokens are dropped."""
    known = _known_codes()
    codes: list[str] = []
    for part in str(raw or "").casefold().split(":"):
        token = part.strip()
        if not _TOKEN.fullmatch(token) or token not in known:
            break
        codes.append(token)
        if token in _CLOSED:
            break
    return ":".join(codes) if codes else "unspecified"


def _masked(value: Any) -> tuple[str | None, bool]:
    """``(text, failed)``. Empty input is not a failure."""
    if value is None or value == "":
        return None, False
    if not isinstance(value, str):
        return None, True
    try:
        from dms_core.pii import fail_closed_mask_payload

        from dms_executor.sql_loop import _scrub_string

        got = fail_closed_mask_payload(text=value)
        if not isinstance(got, dict):
            return None, True
        masked = got.get("text")
        if not isinstance(masked, str):
            return None, True
        return _scrub_string(masked), False
    except Exception:
        return None, True


def _bump(group: str) -> tuple[int, str]:
    from dms_core.ids import mint_id

    now = time.monotonic()
    ttl = PIPELINE_FAILURE_TTL_S
    cap = PIPELINE_FAILURE_CAP if PIPELINE_FAILURE_CAP >= 1 else 1
    with _LOCK:
        expired = [
            key for key, (_count, seen, _ticket) in _GROUPS.items() if now - seen > ttl
        ]
        for key in expired:
            _GROUPS.pop(key, None)
        if group in _GROUPS:
            count, _seen, ticket_id = _GROUPS.pop(group)
            count += 1
            _GROUPS[group] = (count, now, ticket_id)
            return count, ticket_id
        while len(_GROUPS) >= cap:
            _GROUPS.popitem(last=False)
        ticket_id = mint_id("tkt")
        _GROUPS[group] = (1, now, ticket_id)
        return 1, ticket_id


def log_pipeline_failure_ticket(
    reason: str,
    question: str,
    sql: str | None,
    retries: int,
    stage: str,
    ask_id: str,
) -> str:
    """Log one WARNING snapshot for this reason code and question.

    Returns the letters-only ticket id for this group. See the module
    docstring for the parameter names, grouping, the cap, and the TTL.
    Question text and SQL text are not written.
    """
    reason_s = _reason_code(reason)
    stage_s = str(stage or "").strip() or "unspecified"
    ask_s = str(ask_id or "").strip() or "unspecified"
    try:
        retries_n = int(retries)
    except (TypeError, ValueError):
        retries_n = 0
    if retries_n < 0:
        retries_n = 0
    question_text, question_failed = _masked(question)
    _sql_text, sql_failed = _masked(sql)
    payload: dict[str, Any] = {
        "ask_id": ask_s,
        "reason": reason_s,
        "retries": retries_n,
        "stage": stage_s,
    }
    if question_failed or sql_failed:
        group = uuid.uuid4().hex
        count, ticket_id = _bump(group)
        payload["mask_failed"] = True
        payload["group"] = group
        payload["count"] = count
        payload["ticket_id"] = ticket_id
    else:
        question_hash = hashlib.sha256(_normalise(question_text or "").encode()).hexdigest()
        group = hashlib.sha256(f"{reason_s}\n{question_hash}".encode()).hexdigest()
        count, ticket_id = _bump(group)
        payload["question_hash"] = question_hash
        payload["group"] = group
        payload["count"] = count
        payload["ticket_id"] = ticket_id
    _LOG.warning(
        "pipeline_failure %s",
        json.dumps(payload, sort_keys=True, default=str),
    )
    return ticket_id
