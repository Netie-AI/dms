"""Always-on read of optional feature flags.

The UI fetches this once and skips routes that stay unmounted while a flag
is off. Only the digit 1 turns a flag on. This path does not mount those
routes, and it is not a mutation, so it does not call the compliance gate.
"""

from __future__ import annotations

from dms_core.control_plane.ready_flow import guide_enabled
from dms_core.signing.flag import enabled as sign_enabled
from fastapi import APIRouter

from dms_api.routes.insight_proof import enabled as insight_proof_enabled

router = APIRouter(prefix="/v1/features", tags=["features"])


@router.get("")
def read_features() -> dict[str, bool]:
    return {
        "ready_guide": guide_enabled(),
        "sign": sign_enabled(),
        "insight_proof": insight_proof_enabled(),
    }
