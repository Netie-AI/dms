"""SCALE-FREE-AI-01 — FreeRoute provider plan for prove/ask consumption.

GET only. Labels/reasons, never tokens. Fail honest when OpenVault is down.
"""

from __future__ import annotations

from typing import Any

from dms_core.freeroute import FREEROUTE_PREFERENCE, render_harness_md
from fastapi import APIRouter

from dms_api.deps import SettingsDep
from dms_api.freeroute_client import consume_freeroute_plan

router = APIRouter(prefix="/v1/freeroute", tags=["freeroute"])


@router.get("/providers")
def freeroute_providers(settings: SettingsDep) -> dict[str, Any]:
    """OpenVault-resolved free+normal attempt vs skip. Not a second vault."""
    plan = consume_freeroute_plan(settings.openvault_url)
    plan["preference"] = FREEROUTE_PREFERENCE
    plan["harness_md"] = render_harness_md(plan)
    return plan
