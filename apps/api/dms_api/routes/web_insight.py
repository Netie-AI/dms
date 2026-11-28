"""Google AI Overview tool. Mounted only when ``DMS_WEB_INSIGHT`` is the digit 1.

The body is the question. Rows are rejected. The fetcher is pluggable so CI
replays a fixture and never starts a browser.
"""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from dms_core.skills.errors import SkillError
from dms_core.skills.web_insight import run, status, tool_spec
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/studio/web-insight", tags=["skills"])


class OverviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)


def _actor(settings: Any) -> str:
    role = str(settings.dms_actor_role or "").strip().lower()
    actor = str(settings.dms_actor_user_id or "").strip()
    if role not in {"steward", "admin"} or not actor:
        raise HTTPException(status_code=403, detail="steward_grant_required")
    return actor


@router.get("/status")
def web_status() -> dict[str, Any]:
    return status()


@router.get("/tool")
def web_tool() -> dict[str, Any]:
    return {"tool": tool_spec()}


@router.post("/overview")
def web_overview(
    body: OverviewIn,
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="skills.web_insight",
            actor=str(settings.dms_actor_user_id or ""),
            resource="web_insight",
            metadata={"task_id": "skills.web_insight"},
            client=cortex,
        )
    )
    _actor(settings)
    transport = getattr(request.app.state, "web_insight_transport", None)
    try:
        return run(
            body.question,
            transport=transport if callable(transport) else None,
            tenant=str(settings.dms_tenant_id),
        )
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc
