"""Bearer to a DMS actor. DMS_API_KEYS only. No demo-key fallback, no token echo.

The env shape matches Cortex packs/dms/security/api_auth.py: ``role:secret``
pairs separated by ``;``, roles ``viewer``, ``steward``, ``admin``. An actor
is ``api_<role>``. A secret listed under two roles is dropped. Empty
``DMS_API_KEYS`` means no keys: every call is unauthorized.

ponytail: the rate window is 20 requests / 60s per actor, the same cap
grade52 already uses for the OpenVault free tier. One process, one dict.
When OpenVault exposes a per-actor budget, call that instead of this window.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass

_ROLES = frozenset({"viewer", "steward", "admin"})
_WINDOW_S = 60.0
_LIMIT = 20

_actor: ContextVar[str | None] = ContextVar("dms_mcp_actor", default=None)
_hits: dict[str, deque[float]] = {}


@dataclass(frozen=True)
class Caller:
    role: str
    actor: str


def parse_api_keys(raw: str) -> dict[str, str]:
    """Secret to role. Ambiguous secrets are omitted. The secret is not logged."""
    found: dict[str, str] = {}
    bad: set[str] = set()
    for part in raw.split(";"):
        item = part.strip()
        if ":" not in item:
            continue
        role, secret = item.split(":", 1)
        role = role.strip().lower()
        secret = secret.strip()
        if role not in _ROLES or not secret:
            continue
        prev = found.get(secret)
        if prev is not None and prev != role:
            bad.add(secret)
        else:
            found[secret] = role
    for secret in bad:
        found.pop(secret, None)
    return found


def lookup_bearer(token: str | None) -> tuple[Caller | None, str]:
    """Map a bearer to a caller. ``token`` is never stored on the caller."""
    if not token:
        return None, "missing_bearer"
    from dms_core.harness_sec import demo_key_denied

    if demo_key_denied(token):
        return None, "demo_key_denied"
    role = parse_api_keys(os.environ.get("DMS_API_KEYS", "")).get(token)
    if role is None:
        return None, "invalid_bearer"
    return Caller(role=role, actor=f"api_{role}"), ""


def bearer_from_headers(headers: Mapping[str, str]) -> str | None:
    raw = headers.get("authorization") or headers.get("Authorization")
    if not raw:
        return None
    kind, _, rest = raw.partition(" ")
    if kind.lower() != "bearer" or not rest.strip():
        return None
    return rest.strip()


def current_mcp_actor() -> str | None:
    return _actor.get()


def bind_mcp_actor(actor: str) -> Token[str | None]:
    return _actor.set(actor)


def reset_mcp_actor(token: Token[str | None]) -> None:
    _actor.reset(token)


def allow_actor(actor: str) -> bool:
    now = time.monotonic()
    hits = _hits.setdefault(actor, deque())
    while hits and now - hits[0] > _WINDOW_S:
        hits.popleft()
    if len(hits) >= _LIMIT:
        return False
    hits.append(now)
    return True


def reset_rate_limits() -> None:
    _hits.clear()
