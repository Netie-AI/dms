"""``DMS_SEMANTIC_IMPORT`` defaults off."""

from __future__ import annotations

import os

FLAG = "DMS_SEMANTIC_IMPORT"


def enabled() -> bool:
    """On only when the variable is the digit 1. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


class SemanticImportDisabled(RuntimeError):
    """The flag is off. Nothing was read and nothing was stored."""
