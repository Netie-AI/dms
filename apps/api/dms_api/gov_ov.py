"""OpenVault usage readback. No provider HTTP and no cap-file edit.

``GET /api/usage`` is per identity (tokens, model, and ``est_cost_usd`` when
the payload has it). ``GET /api/keys/quota`` is the per-key daily token cap.
``PUT /api/ship/budget`` is one install figure, not a person, so it is not
called. A missing price stays missing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from dms_core.governance.enforce import KeyCap, UsageEvent, UsageRead

_LIMIT = 500
_reader: Any = None


def set_reader(reader: Any) -> None:
    """Tests install a fake. ``None`` restores the HTTP reader."""
    global _reader
    _reader = reader


def get_reader(base_url: str) -> Any:
    if _reader is not None:
        return _reader
    return _http_read(base_url.rstrip("/"))


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _event(raw: object, identity: str) -> UsageEvent | None:
    if not isinstance(raw, dict):
        return None
    event_id = str(raw.get("event_id") or "").strip()
    if not event_id or len(event_id) > 200:
        return None
    owner = raw.get("identity")
    if isinstance(owner, str) and owner and owner != identity:
        return None
    created = raw.get("created_at")
    if isinstance(created, bool) or not isinstance(created, int | float):
        return None
    tokens = raw.get("total_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int | float):
        tokens = 0
    model = raw.get("model_served") or raw.get("model_requested") or ""
    if not isinstance(model, str):
        model = ""
    model = model.replace("\n", " ").replace("\r", "")[:120]
    return UsageEvent(
        event_id=event_id,
        at=datetime.fromtimestamp(float(created), UTC),
        tokens=max(0, int(tokens)),
        cost_usd=_num(raw.get("est_cost_usd")),
        model=model,
    )


def _keys(payload: object) -> tuple[KeyCap, ...]:
    if not isinstance(payload, dict):
        return ()
    rows = payload.get("keys")
    if not isinstance(rows, list):
        return ()
    out: list[KeyCap] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        used = row.get("tokens_used")
        limit = row.get("daily_limit")
        if isinstance(used, bool) or not isinstance(used, int | float):
            used = 0
        cap: int | None
        if limit is None:
            cap = None
        elif isinstance(limit, bool) or not isinstance(limit, int | float):
            cap = None
        else:
            cap = int(limit)
        out.append(KeyCap(tokens_used=max(0, int(used)), daily_limit=cap))
    return tuple(out)


def _http_read(base: str) -> Any:
    def read(identity: str, since: float) -> UsageRead:
        if not base:
            return UsageRead(ok=False)
        try:
            with httpx.Client(timeout=2.5) as http:
                usage = http.get(
                    f"{base}/api/usage",
                    params={"identity": identity, "since": since, "limit": _LIMIT},
                )
                quota = http.get(f"{base}/api/keys/quota")
        except httpx.HTTPError:
            return UsageRead(ok=False)
        if usage.status_code != 200:
            return UsageRead(ok=False)
        try:
            body = usage.json()
        except ValueError:
            return UsageRead(ok=False)
        raw_events = body.get("events") if isinstance(body, dict) else None
        if not isinstance(raw_events, list):
            return UsageRead(ok=False)
        events = tuple(event for event in (_event(item, identity) for item in raw_events) if event)
        keys: tuple[KeyCap, ...] = ()
        if quota.status_code == 200:
            try:
                keys = _keys(quota.json())
            except ValueError:
                keys = ()
        count = body.get("count") if isinstance(body, dict) else None
        truncated = len(raw_events) >= _LIMIT or (
            isinstance(count, int) and not isinstance(count, bool) and count >= _LIMIT
        )
        return UsageRead(ok=True, events=events, keys=keys, truncated=truncated)

    return read
