"""One pipeline-failure ticket list for DMS.

``log_pipeline_failure_ticket`` is the shared writer. #409 (INTENT-SPEC-01)
calls this and does not keep a second logger. The extract loop calls it
when ``DMS_CLOOP_B`` is on and an abstain leaves the loop.

Group key is ``(reason, sha256 of the normalised masked question)``. The
raw question is never part of the key and is never logged. Normalisation
is the masked text, casefolded, with whitespace collapsed.

The live groups sit in an in-process map guarded by a lock. The map holds
at most ``PIPELINE_FAILURE_CAP`` groups. A group not touched for
``PIPELINE_FAILURE_TTL_S`` seconds is dropped on the next call (sliding
window). A new group past the cap evicts the least-recently-used group.
An evicted or expired group that shows up again is a new first occurrence.

Emission. The first time a group is seen, one line is logged immediately
with ``count`` 1. Each repeat increments that count and logs one snapshot
with the same ``group`` id and the new count. The ticket list is not the
raw log: keep the snapshot with the greatest ``count`` for each ``group``.
That is one ticket per reason and question. A retry storm updates that
ticket instead of opening another one.

Each line is ``pipeline_failure`` plus one JSON object, on the logger
``dms_executor.pipeline_failure``. Question text and SQL go through
``fail_closed_mask_payload``, then bearer and secret redaction. If that
fails, the line is still written and those two fields are left out.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

_LOG = logging.getLogger(__name__)
PIPELINE_FAILURE_CAP = 256
PIPELINE_FAILURE_TTL_S = 600.0
_LOCK = threading.Lock()
# group id -> (count, last_seen monotonic)
_GROUPS: OrderedDict[str, tuple[int, float]] = OrderedDict()


def _reset_pipeline_failures() -> None:
    with _LOCK:
        _GROUPS.clear()


def _normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def _safe_text(value: Any) -> str | None:
    """Masked text, or None when there is nothing safe to store."""
    if not isinstance(value, str) or not value:
        return None
    try:
        from dms_core.pii import fail_closed_mask_payload

        from dms_executor.sql_loop import _scrub_string

        got = fail_closed_mask_payload(text=value)
        if not isinstance(got, dict):
            return None
        masked = got.get("text")
        if not isinstance(masked, str):
            return None
        return _scrub_string(masked)
    except Exception:
        return None


def _bump(group: str) -> int:
    now = time.monotonic()
    ttl = PIPELINE_FAILURE_TTL_S
    cap = PIPELINE_FAILURE_CAP if PIPELINE_FAILURE_CAP >= 1 else 1
    with _LOCK:
        expired = [
            key
            for key, (_count, seen) in _GROUPS.items()
            if now - seen > ttl
        ]
        for key in expired:
            _GROUPS.pop(key, None)
        if group in _GROUPS:
            count, _seen = _GROUPS.pop(group)
            count += 1
            _GROUPS[group] = (count, now)
            return count
        while len(_GROUPS) >= cap:
            _GROUPS.popitem(last=False)
        _GROUPS[group] = (1, now)
        return 1


def log_pipeline_failure_ticket(
    reason: str,
    question: str,
    sql: str | None,
    retries: int,
    stage: str,
) -> None:
    """Log one snapshot of the ticket group for this reason and question.

    See the module docstring for grouping, the cap, and the TTL.
    """
    reason_s = str(reason or "").strip() or "unspecified"
    stage_s = str(stage or "").strip() or "unspecified"
    try:
        retries_n = int(retries)
    except (TypeError, ValueError):
        retries_n = 0
    if retries_n < 0:
        retries_n = 0
    question_text = _safe_text(question)
    sql_text = _safe_text(sql)
    question_hash = hashlib.sha256(
        _normalise(question_text or "").encode()
    ).hexdigest()
    group = hashlib.sha256(f"{reason_s}\n{question_hash}".encode()).hexdigest()
    count = _bump(group)
    payload: dict[str, Any] = {
        "reason": reason_s,
        "question_hash": question_hash,
        "retries": retries_n,
        "stage": stage_s,
        "count": count,
        "group": group,
    }
    if question_text:
        payload["question"] = question_text
    if sql_text:
        payload["sql"] = sql_text
    _LOG.info(
        "pipeline_failure %s",
        json.dumps(payload, sort_keys=True, default=str),
    )
