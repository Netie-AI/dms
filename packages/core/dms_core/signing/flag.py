"""``DMS_SIGN`` is off unless the value is the digit 1."""

from __future__ import annotations

import os

FLAG = "DMS_SIGN"


def enabled() -> bool:
    """True only when ``DMS_SIGN`` is ``1`` after stripping. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"
