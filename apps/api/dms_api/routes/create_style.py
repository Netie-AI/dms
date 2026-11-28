"""Create exports. Mounted only when ``DMS_CREATE_STYLE`` is the digit 1.

``POST /v1/create/render`` turns blocks, or facts the model turns into blocks,
into docx, pptx, xlsx, or HTML. The theme file supplies colours, fonts, logo,
and spacing. This route does not write the sentences.
"""

from __future__ import annotations

from typing import Any, Literal

from cortex_client.gate import compliance_gate
from dms_core.create_style.errors import CreateError
from dms_core.create_style.flag import enabled
from dms_core.create_style.model import propose
from dms_core.create_style.render import render_document
from dms_core.create_style.spec import catalog
from dms_core.create_style.theme import load_theme, theme_fields
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/create", tags=["create"])


class RenderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["docx", "pptx", "xlsx", "html"]
    title: str = Field(default="", max_length=20_000)
    theme: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] | None = None
    facts: str | None = None


@router.get("/layouts")
def list_layouts(request: Request, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    del request
    enforce(
        compliance_gate(
            action="create.layouts",
            actor=str(settings.dms_actor_user_id or ""),
            client=cortex,
        ),
        mutation=False,
    )
    if not enabled():
        raise HTTPException(status_code=404, detail="create_style_disabled")
    return {"blocks": catalog(), "theme": theme_fields()}


@router.post("/render")
def render_export(
    body: RenderIn, request: Request, settings: SettingsDep, cortex: CortexDep
) -> Response:
    del request
    enforce(
        compliance_gate(
            action="create.render",
            actor=str(settings.dms_actor_user_id or ""),
            resource=body.format,
            client=cortex,
        )
    )
    if not enabled():
        raise HTTPException(status_code=404, detail="create_style_disabled")
    try:
        document = _document(body)
        theme = load_theme(body.theme)
        data, media, filename = render_document(document, theme, body.format)
    except CreateError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from None
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _document(body: RenderIn) -> dict[str, Any]:
    has_blocks = body.blocks is not None
    has_facts = body.facts is not None
    if has_blocks == has_facts:
        raise CreateError("blocks_or_facts")
    if has_facts:
        document = propose(body.facts or "")
        if body.title:
            document = {"title": body.title, "blocks": document["blocks"]}
        return document
    return {"title": body.title, "blocks": body.blocks or []}
