"""UX-FEEDBACK-01. Answer feedback, behind ``DMS_FEEDBACK_LOOP``.

The flag defaults to off. Off, nothing is stored and nothing is retrieved.
On, thumbs-up of a governed answer, or steward approval of a fix, stores the
question and the SQL as a verified example. Lookup never replays that row.
Thumbs-down, a failed ask, and Report a problem file a pipeline-failure
record the runs page can show. Fix-it runs the model SQL gates before any
read. Question text is not scanned for phrases.

# ponytail: in-process cap 256, dropped on restart. Upgrade path is a
# dms.feedback_event table beside ask_audit. That table is still not a ledger.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FLAG = "DMS_FEEDBACK_LOOP"
PROVENANCE_YES = "feedback_yes"
PROVENANCE_APPROVED = "feedback_approved"
DOWN_REASONS = frozenset(
    {"wrong_number", "wrong_grain", "missing_data", "too_slow", "other"}
)
GOVERNED_BADGES = frozenset(
    {"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED"}
)
_CAP = 256
_LOCK = threading.Lock()
_FAILURES: list[dict[str, Any]] = []
_AUDITS: list[dict[str, Any]] = []
_REVIEWS: dict[str, dict[str, Any]] = {}
# (space_id, question, sql) this process actually served.
_GOVERNED: list[tuple[str, str, str]] = []


class FeedbackDisabled(RuntimeError):
    """The feedback flag is off. Nothing ran and nothing was stored."""


def enabled() -> bool:
    """``DMS_FEEDBACK_LOOP=1`` exactly. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


def _reset() -> None:
    with _LOCK:
        _FAILURES.clear()
        _AUDITS.clear()
        _REVIEWS.clear()
        _GOVERNED.clear()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _clip(text: str, cap: int = 500) -> str:
    return " ".join((text or "").split())[:cap]


def _masked(text: str, cap: int = 500) -> str:
    raw = _clip(text, cap)
    if not raw:
        return ""
    try:
        from dms_core.pii import fail_closed_mask_payload

        got = fail_closed_mask_payload(text=raw)
    except Exception:
        return ""
    if not isinstance(got, dict):
        return ""
    masked = got.get("text")
    if not isinstance(masked, str):
        return ""
    return _clip(masked, cap)


def _push(bucket: list[dict[str, Any]], row: dict[str, Any]) -> None:
    bucket.append(row)
    extra = len(bucket) - _CAP
    if extra > 0:
        del bucket[:extra]


def _audit(
    *,
    action: str,
    space_id: str,
    question: str,
    sql: str,
    reason: str,
    asset_id: str | None,
    actor: str = "",
) -> dict[str, Any]:
    row = {
        "id": _new_id("fba"),
        "action": action,
        "space_id": space_id,
        "question": _masked(question, 2000),
        "sql": _clip(sql, 2000),
        "reason": _clip(reason, 120),
        "asset_id": asset_id,
        "actor": _clip(actor, 128),
        "created_at": _now(),
        "ledger_entry_id": None,
    }
    with _LOCK:
        _push(_AUDITS, row)
    return row


def _require() -> None:
    if not enabled():
        raise FeedbackDisabled()


def remember_governed(
    *, space_id: str, question: str, sql: str, badge: str
) -> None:
    """Remember one served governed answer. Flag off remembers nothing."""
    if not enabled():
        return
    if badge not in GOVERNED_BADGES:
        return
    q = _clip(question, 2000)
    s = (sql or "").strip()
    if not q or not s:
        return
    with _LOCK:
        _GOVERNED.append((space_id or "", q, s))
        extra = len(_GOVERNED) - _CAP
        if extra > 0:
            del _GOVERNED[:extra]


def governed_match(
    *,
    space_id: str,
    question: str,
    sql: str,
    audit_rows: list[Any] | None = None,
) -> bool:
    """True when this process served the SQL, or an audit row names it.

    The caller does not get to assert governed. A badge on the request is ignored.
    """
    q = _clip(question, 2000)
    s = (sql or "").strip()
    if not q or not s:
        return False
    with _LOCK:
        seen = list(_GOVERNED)
    for sid, seen_q, seen_s in seen:
        if seen_q == q and seen_s == s and (not space_id or not sid or sid == space_id):
            return True
    for rec in audit_rows or []:
        if getattr(rec, "badge", "") != "validated":
            continue
        if getattr(rec, "badge_level", "") not in GOVERNED_BADGES:
            continue
        if getattr(rec, "abstain_reason", ""):
            continue
        if _clip(str(getattr(rec, "question", "") or ""), 2000) != q:
            continue
        if str(getattr(rec, "executed_sql", "") or "").strip() != s:
            continue
        rec_space = str(getattr(rec, "space_id", "") or "")
        if space_id and rec_space and rec_space != space_id:
            continue
        return True
    return False


def _error_reason(exc: BaseException) -> str:
    detail = getattr(exc, "detail", None)
    token = ""
    if isinstance(detail, str):
        token = detail.strip()
    elif isinstance(detail, dict):
        code = detail.get("code")
        token = code.strip() if isinstance(code, str) else ""
    if token and all(ch.isalnum() or ch == "_" for ch in token):
        return token[:80]
    return "ask_failed"


def _abstain_reason(env: dict[str, Any]) -> str:
    for key in ("abstain_code", "reason_code"):
        raw = env.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:80]
    block = env.get("reconfirm")
    if isinstance(block, dict):
        code = block.get("reason_code")
        if isinstance(code, str) and code.strip():
            return code.strip()[:80]
    return "abstain"


def record_failure(
    *,
    question: str,
    space_id: str,
    stage: str,
    reason: str,
    action: str,
    note: str = "",
    actor: str = "",
) -> dict[str, Any]:
    """One pipeline-failure row plus an audit row. Flag off stores nothing."""
    _require()
    shown_q = _masked(question, 2000)
    stage_s = _clip(stage, 80) or "unspecified"
    reason_s = _clip(reason, 80) or "unspecified"
    note_s = _masked(note, 500)
    shown_reason = f"{reason_s}: {note_s}" if note_s else reason_s
    row = {
        "id": _new_id("pfl"),
        "kind": "pipeline_failure",
        "status": "failed",
        "created_at": _now(),
        "space_name": space_id or None,
        "detail": shown_q,
        "question": shown_q,
        "stage": stage_s,
        "reason": reason_s,
        "note": note_s,
        "reasons": [{"file": stage_s, "reason": shown_reason, "fix": ""}],
        "ledger_seq": None,
    }
    with _LOCK:
        _push(_FAILURES, row)
    audit = _audit(
        action=action,
        space_id=space_id,
        question=question,
        sql="",
        reason=reason_s,
        asset_id=None,
        actor=actor,
    )
    try:
        from dms_executor.pipeline_failure import log_pipeline_failure_ticket

        log_pipeline_failure_ticket(
            reason=reason_s,
            question=question or "",
            sql=None,
            retries=0,
            stage=stage_s,
            ask_id=str(row["id"]),
        )
    except Exception:
        pass
    return {"failure": row, "audit": audit}


def failure_runs() -> list[dict[str, Any]]:
    """Newest first. Empty when the flag is off, even if a prior on-state wrote."""
    if not enabled():
        return []
    with _LOCK:
        return list(reversed(_FAILURES))


def audit_rows() -> list[dict[str, Any]]:
    if not enabled():
        return []
    with _LOCK:
        return list(reversed(_AUDITS))


def review_rows() -> list[dict[str, Any]]:
    if not enabled():
        return []
    with _LOCK:
        rows = [dict(row) for row in _REVIEWS.values()]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows


def attach_runs(body: dict[str, Any]) -> dict[str, Any]:
    """Same object when the flag is off or there is nothing to add."""
    extra = failure_runs()
    if not extra:
        return body
    merged = dict(body)
    runs = list(extra)
    runs.extend(body.get("runs") or [])
    merged["runs"] = runs
    counts = dict(body.get("counts") or {})
    for row in extra:
        status = str(row.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
    if counts:
        merged["counts"] = counts
    return merged


def note_ask_outcome(
    *,
    question: str,
    space_id: str | None,
    envelope: dict[str, Any] | None,
    error: BaseException | None,
) -> None:
    """Side record only. Does not write the envelope.

    Flag off returns before any store. A failed or abstained ask files a
    pipeline-failure row. A governed answer is remembered so a later Yes can
    file the example. The envelope object is not modified.
    """
    if not enabled():
        return
    sid = space_id or ""
    if error is not None:
        record_failure(
            question=question,
            space_id=sid,
            stage="ask",
            reason=_error_reason(error),
            action="ask_failed",
        )
        return
    if not isinstance(envelope, dict):
        return
    badge = str(envelope.get("badge") or "").upper()
    abstained = bool(envelope.get("abstained")) or badge == "ABSTAIN"
    if abstained:
        record_failure(
            question=question,
            space_id=sid,
            stage="ask",
            reason=_abstain_reason(envelope),
            action="ask_failed",
        )
        return
    sql = envelope.get("sql_used")
    if isinstance(sql, str):
        remember_governed(
            space_id=sid,
            question=question,
            sql=sql,
            badge=str(envelope.get("badge") or ""),
        )


def _file_example(
    *,
    question: str,
    sql: str,
    space_id: str,
    actor: str,
    provenance: str,
    path: Path | None,
    dialect: str | None,
) -> dict[str, Any]:
    """Validate, then store. Never a replayable row."""
    _require()
    if provenance not in {PROVENANCE_YES, PROVENANCE_APPROVED}:
        raise ValueError("provenance_not_allowed")
    from dms_executor.generative_ask import validate_compiled_sql
    from dms_executor.verified_queries import (
        _adjust_dialect,
        _grantable,
        _save_example,
        warehouse_path,
    )

    db = path or warehouse_path()
    allowed = _grantable(space_id, db)
    read = _adjust_dialect(dialect)
    why = validate_compiled_sql((sql or "").strip(), grantable=allowed, warehouse=db, dialect=read)
    if why:
        raise ValueError(why)
    saved = _save_example(
        question=(question or "").strip(),
        sql=(sql or "").strip(),
        space_id=space_id,
        warehouse=db,
        grantable=allowed,
        dialect=read,
        provenance=provenance,
        actor=(actor or "").strip() or None,
    )
    if not saved or saved.get("provenance") != provenance:
        raise ValueError("not_stored")
    return saved


def record_thumb(
    *,
    space_id: str,
    question: str,
    sql: str,
    vote: str,
    reason: str,
    note: str,
    governed: bool,
    actor: str,
    path: Path | None = None,
    dialect: str | None = None,
) -> dict[str, Any]:
    """Yes files only when ``governed`` is already true. No never files."""
    _require()
    if vote not in {"up", "down"}:
        raise ValueError("vote_required")
    if vote == "down":
        if reason not in DOWN_REASONS:
            raise ValueError("reason_required")
        if reason == "other" and not (note or "").strip():
            raise ValueError("note_required")
        filed = record_failure(
            question=question,
            space_id=space_id,
            stage="feedback",
            reason=reason,
            action="thumb_down",
            note=note if reason == "other" else "",
            actor=actor,
        )
        return {
            "vote": "down",
            "filed": False,
            "asset_id": None,
            "audit": filed["audit"],
        }
    asset_id = None
    saved: dict[str, Any] | None = None
    if governed:
        saved = _file_example(
            question=question,
            sql=sql,
            space_id=space_id,
            actor=actor,
            provenance=PROVENANCE_YES,
            path=path,
            dialect=dialect,
        )
        asset_id = str(saved.get("asset_id") or "") or None
    audit = _audit(
        action="thumb_up",
        space_id=space_id,
        question=question,
        sql=sql,
        reason="",
        asset_id=asset_id,
        actor=actor,
    )
    return {
        "vote": "up",
        "filed": asset_id is not None,
        "asset_id": asset_id,
        "audit": audit,
        "example": saved,
    }


def run_fix(
    *,
    space_id: str,
    question: str,
    sql: str,
    interpretation: str = "",
    actor: str = "",
    path: Path | None = None,
    dialect: str | None = None,
) -> dict[str, Any]:
    """Re-gate the edited SQL, then read. Does not file an example.

    ``interpretation`` is stored on the audit row. It is not parsed and it
    is not turned into SQL.
    """
    _require()
    from dms_executor.verified_queries import run_gated_sql

    try:
        preview = run_gated_sql(sql=sql, space_id=space_id, path=path, dialect=dialect)
    except ValueError as exc:
        code = exc.args[0] if exc.args and isinstance(exc.args[0], str) else "refused"
        _audit(
            action="fix_refused",
            space_id=space_id,
            question=question,
            sql=sql,
            reason=str(code)[:120],
            asset_id=None,
            actor=actor,
        )
        raise
    audit = _audit(
        action="fix_run",
        space_id=space_id,
        question=interpretation or question,
        sql=sql,
        reason="",
        asset_id=None,
        actor=actor,
    )
    out = dict(preview)
    out["filed"] = False
    out["asset_id"] = None
    out["interpretation"] = _masked(interpretation, 2000)
    out["audit"] = audit
    return out


def queue_review(
    *,
    space_id: str,
    question: str,
    sql: str,
    interpretation: str = "",
    note: str = "",
    actor: str = "",
) -> dict[str, Any]:
    """Queue one fix for a steward. Does not file an example."""
    _require()
    q = (question or "").strip()
    s = (sql or "").strip()
    if not q:
        raise ValueError("question_required")
    if not s:
        raise ValueError("sql_required")
    review_id = _new_id("rev")
    row: dict[str, Any] = {
        "id": review_id,
        "space_id": space_id,
        "question": q,
        "sql": s,
        "interpretation": (interpretation or "").strip(),
        "note": _masked(note, 500),
        "status": "queued",
        "asset_id": None,
        "actor": "",
        "created_at": _now(),
    }
    with _LOCK:
        _REVIEWS[review_id] = row
        extra = len(_REVIEWS) - _CAP
        if extra > 0:
            for key in list(_REVIEWS)[:extra]:
                _REVIEWS.pop(key, None)
    audit = _audit(
        action="review_queued",
        space_id=space_id,
        question=q,
        sql=s,
        reason="",
        asset_id=None,
        actor=actor,
    )
    shown = dict(row)
    shown["audit"] = audit
    return shown


def approve_review(
    *,
    review_id: str,
    actor: str,
    path: Path | None = None,
    dialect: str | None = None,
) -> dict[str, Any]:
    """Re-gate the queued SQL, then file it. A second approve does not file again."""
    _require()
    actor_id = (actor or "").strip()
    if not actor_id:
        raise ValueError("actor_required")
    token = (review_id or "").strip()
    with _LOCK:
        row = _REVIEWS.get(token)
        if row is None:
            raise KeyError(token)
        if row.get("status") == "approved":
            return {
                "review": dict(row),
                "filed": False,
                "asset_id": row.get("asset_id"),
                "audit": None,
            }
        question = str(row.get("question") or "")
        interpretation = str(row.get("interpretation") or "")
        sql = str(row.get("sql") or "")
        space_id = str(row.get("space_id") or "")
    from dms_executor.verified_queries import run_gated_sql

    run_gated_sql(sql=sql, space_id=space_id, path=path, dialect=dialect)
    filed_question = interpretation.strip() or question
    saved = _file_example(
        question=filed_question,
        sql=sql,
        space_id=space_id,
        actor=actor_id,
        provenance=PROVENANCE_APPROVED,
        path=path,
        dialect=dialect,
    )
    asset_id = str(saved.get("asset_id") or "")
    with _LOCK:
        current = _REVIEWS.get(token)
        if current is None:
            raise KeyError(token)
        if current.get("status") == "approved":
            return {
                "review": dict(current),
                "filed": False,
                "asset_id": current.get("asset_id"),
                "audit": None,
            }
        current["status"] = "approved"
        current["asset_id"] = asset_id
        current["actor"] = actor_id
        shown = dict(current)
    audit = _audit(
        action="review_approved",
        space_id=space_id,
        question=filed_question,
        sql=sql,
        reason="",
        asset_id=asset_id,
        actor=actor_id,
    )
    return {"review": shown, "filed": True, "asset_id": asset_id, "audit": audit, "example": saved}


def report_problem(
    *,
    note: str,
    space_id: str = "",
    question: str = "",
    actor: str = "",
) -> dict[str, Any]:
    """Floating Report a problem. A failure row, not an example."""
    text = (note or "").strip()
    if not text:
        raise ValueError("note_required")
    shown = (question or "").strip() or text
    return record_failure(
        question=shown,
        space_id=space_id,
        stage="report",
        reason="report_problem",
        action="report_problem",
        note=text,
        actor=actor,
    )
