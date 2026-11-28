"""Always-on read of optional feature flags.

The UI fetches this once and skips routes that stay unmounted while a flag
is off. Only the digit 1 turns a flag on. This path does not mount those
routes, and it is not a mutation, so it does not call the compliance gate.
"""

from __future__ import annotations

import os

from dms_core.control_plane.ready_flow import guide_enabled
from dms_core.signing.flag import enabled as sign_enabled
from fastapi import APIRouter

router = APIRouter(prefix="/v1/features", tags=["features"])

# Same digit check as insight_proof.enabled. That module stays off the
# importer list (tests/test_insight_proof_01.py).
_INSIGHT_PROOF = "DMS_INSIGHT_PROOF"


def _digit(name: str) -> bool:
    return os.environ.get(name, "0").strip() == "1"


@router.get("")
def read_features() -> dict[str, bool]:
    return {
        "ready_guide": guide_enabled(),
        "sign": sign_enabled(),
        "insight_proof": _digit(_INSIGHT_PROOF),
    }
