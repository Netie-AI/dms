"""``DMS_CREATE_DOCS`` is off unless the value is the digit 1."""

from __future__ import annotations

import os

FLAG = "DMS_CREATE_DOCS"


def enabled() -> bool:
    """True only for ``1``. Unset, ``0``, ``true``, and ``yes`` stay off."""
    return os.environ.get(FLAG, "0").strip() == "1"
