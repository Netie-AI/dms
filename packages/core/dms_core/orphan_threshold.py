"""Steward orphan ratio for one Space. Default 0.

The API records the ratio. The executor reads it while verifying a link.
Flag off always reports 0, so a stored ratio does not change the refusal.
"""

from __future__ import annotations

import os
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass

FK_ORPHAN_DISCLOSE_ENV = "DMS_FK_ORPHAN_DISCLOSE"

_SPACE: ContextVar[str | None] = ContextVar("dms_fk_orphan_space", default=None)
_LOCK = threading.Lock()


@dataclass(frozen=True)
class OrphanThreshold:
    """A steward-set orphan ratio for one Space. ``set_by`` is the server actor."""

    space_id: str
    ratio: float
    set_by: str


_THRESHOLDS: dict[str, OrphanThreshold] = {}


def fk_orphan_disclose_enabled() -> bool:
    """True only when ``DMS_FK_ORPHAN_DISCLOSE`` is the digit 1. Unset is off."""
    return os.environ.get(FK_ORPHAN_DISCLOSE_ENV, "0").strip() == "1"


def bind_orphan_space(space_id: str | None) -> Token[str | None]:
    return _SPACE.set(str(space_id) if space_id else None)


def reset_orphan_space(token: Token[str | None]) -> None:
    _SPACE.reset(token)


def current_orphan_space() -> str | None:
    return _SPACE.get()


def orphan_threshold_ratio(space_id: str | None = None) -> float:
    """The steward ratio for this Space. 0 until one is set.

    Flag off always returns 0, even when a steward has recorded a ratio.
    """
    if not fk_orphan_disclose_enabled():
        return 0.0
    key = str(space_id or current_orphan_space() or "")
    if not key:
        return 0.0
    with _LOCK:
        row = _THRESHOLDS.get(key)
    return 0.0 if row is None else float(row.ratio)


def orphan_threshold_record(space_id: str) -> OrphanThreshold | None:
    with _LOCK:
        return _THRESHOLDS.get(str(space_id))


def set_orphan_threshold(space_id: str, ratio: float, *, actor: str) -> OrphanThreshold:
    """Record a steward ratio. ``actor`` is the server identity, never a request field."""
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise ValueError("ratio_invalid")
    value = float(ratio)
    if value < 0.0 or value > 1.0:
        raise ValueError("ratio_invalid")
    who = str(actor or "").strip()
    if not who:
        raise ValueError("actor_required")
    row = OrphanThreshold(space_id=str(space_id), ratio=value, set_by=who)
    with _LOCK:
        _THRESHOLDS[str(space_id)] = row
    return row


def clear_orphan_thresholds() -> None:
    """Tests reset the process table. Production does not call this."""
    with _LOCK:
        _THRESHOLDS.clear()
