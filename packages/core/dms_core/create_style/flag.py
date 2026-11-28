"""``DMS_CREATE_STYLE`` is off unless the value is the digit 1."""

from __future__ import annotations

import os

FLAG = "DMS_CREATE_STYLE"


def enabled() -> bool:
    """True only when ``DMS_CREATE_STYLE`` is ``1`` after stripping. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"
