"""Flags for declared-FK joins and disclosed FK orphans. Default off.

``DMS_JOIN_DECLARED_FK`` keeps a declared foreign key whose child table has
no primary key. The parent side is measured on the landed rows.

``DMS_FK_ORPHAN_DISCLOSE`` can treat a few orphan rows as a disclosed
exclusion. The steward ratio lives in ``dms_core`` so the API can record it
without importing the executor. It defaults to 0, so the flag alone does not
bless a dirty link.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any

from dms_core.orphan_threshold import (  # noqa: F401  re-export for the executor
    FK_ORPHAN_DISCLOSE_ENV,
    OrphanThreshold,
    bind_orphan_space,
    clear_orphan_thresholds,
    current_orphan_space,
    fk_orphan_disclose_enabled,
    orphan_threshold_ratio,
    orphan_threshold_record,
    reset_orphan_space,
    set_orphan_threshold,
)

JOIN_DECLARED_FK_ENV = "DMS_JOIN_DECLARED_FK"

_SERVE_ONTO: ContextVar[Any] = ContextVar("dms_serve_ontology", default=None)


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


def bind_serve_ontology(onto: Any) -> None:
    """The ontology a successful serve may read for orphan exclusion lines."""
    _SERVE_ONTO.set(onto)


def current_serve_ontology() -> Any:
    return _SERVE_ONTO.get()
