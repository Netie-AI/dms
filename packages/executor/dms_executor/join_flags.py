"""Flags for declared-FK joins and disclosed FK orphans. Default off.

``DMS_JOIN_DECLARED_FK`` keeps a declared foreign key whose child table has
no primary key. The parent side is measured on the landed rows.

``DMS_FK_ORPHAN_DISCLOSE`` can treat a few orphan rows as a disclosed
exclusion. The steward ratio defaults to 0, so the flag alone does not
bless a dirty link.
"""

from __future__ import annotations

import os
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

JOIN_DECLARED_FK_ENV = "DMS_JOIN_DECLARED_FK"
FK_ORPHAN_DISCLOSE_ENV = "DMS_FK_ORPHAN_DISCLOSE"

_SPACE: ContextVar[str | None] = ContextVar("dms_fk_orphan_space", default=None)
_SERVE_ONTO: ContextVar[Any] = ContextVar("dms_serve_ontology", default=None)
_LOCK = threading.Lock()


@dataclass(frozen=True)
class OrphanThreshold:
    """A steward-set orphan ratio for one Space. ``set_by`` is the server actor."""

    space_id: str
    ratio: float
    set_by: str


_THRESHOLDS: dict[str, OrphanThreshold] = {}


def _digit_one(name: str) -> bool:
    return os.environ.get(name, "0").strip() == "1"


#: Join-rule refusals one SQL repair may try. Names are gate codes, not question text.
REPAIRABLE_JOIN_TAILS = (
    "parent_rows_repeat",
    "fan_trap",
    "join_kind",
    "subquery_shape",
)


def repairable_join_reason(reason: str) -> bool:
    """True when ``reason`` is one of the four fixable join-rule refusals."""
    marker = "unverified_join:"
    idx = str(reason or "").find(marker)
    if idx < 0:
        return False
    tail = str(reason)[idx + len(marker) :].lstrip()
    return any(tail.startswith(name) for name in REPAIRABLE_JOIN_TAILS)


def join_repair_stop(reason: str) -> bool:
    """True when the extract loop should stop and repair once. Flag off is False."""
    if not join_declared_fk_enabled():
        return False
    return repairable_join_reason(reason)


def join_declared_fk_enabled() -> bool:
    """True only when ``DMS_JOIN_DECLARED_FK`` is the digit 1. Unset is off."""
    return _digit_one(JOIN_DECLARED_FK_ENV)


def fk_orphan_disclose_enabled() -> bool:
    """True only when ``DMS_FK_ORPHAN_DISCLOSE`` is the digit 1. Unset is off."""
    return _digit_one(FK_ORPHAN_DISCLOSE_ENV)


def bind_orphan_space(space_id: str | None) -> Token[str | None]:
    return _SPACE.set(str(space_id) if space_id else None)


def reset_orphan_space(token: Token[str | None]) -> None:
    _SPACE.reset(token)


def current_orphan_space() -> str | None:
    return _SPACE.get()


def bind_serve_ontology(onto: Any) -> None:
    """The ontology a successful serve may read for orphan exclusion lines."""
    _SERVE_ONTO.set(onto)


def current_serve_ontology() -> Any:
    return _SERVE_ONTO.get()


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
