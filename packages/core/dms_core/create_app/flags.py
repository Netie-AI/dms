"""Flags for runnable Create apps. Only the digit 1 turns a flag on."""

from __future__ import annotations

import os

FLAG = "DMS_CREATE_APP"
SALESFORCE_FLAG = "DMS_CREATE_APP_SALESFORCE"


def _digit(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def enabled() -> bool:
    """``DMS_CREATE_APP=1`` exactly. Unset, ``true``, and ``yes`` stay off."""
    return _digit(FLAG)


def salesforce_enabled() -> bool:
    """``DMS_CREATE_APP_SALESFORCE=1`` exactly. Unset stays off."""
    return _digit(SALESFORCE_FLAG)
