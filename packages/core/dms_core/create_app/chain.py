"""Chained Create. Context moves by reference. Grants cannot widen.

A doc or slides handoff is started by the executor, which calls the Create
surface already on main. A research handoff is a prompt for a Cursor v1
agent: research, then create. The prompt carries ``context_ref`` and the
grant names. It does not carry table rows.
"""

from __future__ import annotations

import json
from typing import Any

_KINDS = frozenset({"doc", "slides", "research"})


def plan_handoff(
    *,
    source: str,
    source_ref: str,
    kind: str,
    goal: str,
    source_grants: set[str],
    requested_grants: list[str] | None = None,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a handoff. ``record`` is not copied. Rows on it stay put."""
    del record
    if kind not in _KINDS:
        return {"ok": False, "code": "kind_unknown"}
    if source not in ("app", "answer"):
        return {"ok": False, "code": "source_unknown"}
    if not source_ref or not goal.strip():
        return {"ok": False, "code": "handoff_incomplete"}
    if requested_grants is not None and not set(requested_grants) <= set(source_grants):
        return {"ok": False, "code": "grant_widened"}
    grants = sorted(requested_grants) if requested_grants is not None else sorted(source_grants)
    return {
        "ok": True,
        "source": source,
        "context_ref": f"{source}:{source_ref}",
        "kind": kind,
        "goal": goal.strip(),
        "grants": grants,
    }


def research_prompt(handoff: dict[str, Any]) -> str:
    """Cursor prompt. Reference and grants only."""
    payload = {
        "task": "Research the governed context, then create the requested artifact.",
        "context_ref": handoff.get("context_ref"),
        "grants": handoff.get("grants"),
        "goal": handoff.get("goal"),
        "note": "Do not expect raw table rows. Aggregates come from the DMS data client.",
    }
    return json.dumps(payload)


def research_body(handoff: dict[str, Any], *, agent_id: str | None = None) -> dict[str, Any]:
    import os

    text = research_prompt(handoff)
    if agent_id:
        return {"prompt": {"text": text}, "agent_id": agent_id}
    body: dict[str, Any] = {"prompt": {"text": text}, "autoCreatePR": False}
    repo = os.environ.get("DMS_CREATE_CURSOR_REPO", "").strip()
    ref = os.environ.get("DMS_CREATE_CURSOR_REF", "main").strip() or "main"
    if repo:
        body["repos"] = [{"url": repo, "startingRef": ref}]
    return body


def research_turn(
    handoff: dict[str, Any],
    *,
    transport: Any = None,
) -> dict[str, Any]:
    """Send the research prompt. A missing Cursor key does not invent a run."""
    from dms_core.create_generator import GeneratorError, _cursor_key, default_cursor

    body = research_body(handoff)
    if transport is None and "repos" not in body and "agent_id" not in body:
        return {**handoff, "started": False, "code": "cursor_repo_missing"}
    try:
        if transport is None:
            raw = default_cursor(body, _cursor_key())
        else:
            raw = transport(body, "stub")
    except GeneratorError as exc:
        return {**handoff, "started": False, "code": exc.code}
    return {**handoff, "started": True, "code": "research_started", "cursor": _public_cursor(raw)}


def _public_cursor(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"status": "unreadable"}
    status = raw.get("status") if isinstance(raw.get("status"), str) else ""
    ident = raw.get("id") if isinstance(raw.get("id"), str) else ""
    return {"id": ident, "status": status}
