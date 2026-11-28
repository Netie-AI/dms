"""Steward semantic-model import (FEAT-SEMANTIC-IMPORT-01).

``DMS_SEMANTIC_IMPORT`` defaults to off. While it is not the digit 1,
``app.py`` does not mount this router, so both paths are a 404 and the
OpenAPI document is main's. Nothing here reads question text.
"""

from __future__ import annotations

import os

from fastapi import APIRouter

FLAG = "DMS_SEMANTIC_IMPORT"

router = APIRouter(prefix="/v1/spaces", tags=["semantic-import"])


def enabled() -> bool:
    """``DMS_SEMANTIC_IMPORT=1`` exactly. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"
