"""EPIC-014 MCP-01: three tools over existing HTTP handlers.

Swap: IDE/Cursor MCP client. Off unless ``DMS_MCP=1``. No new serving engine,
no CortexOS import, no second ask path. Mutations still hit the inner routes'
``compliance_gate`` (ask) / ``enforce`` (preview).
"""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from dms_api.deps import AskServiceDep, CortexDep, SettingsDep, SpaceStoreDep
from dms_api.gatekeeping import enforce
from dms_api.routes.chat import AskBody, chat_ask
from dms_api.routes.library import preview_wh_table
from dms_api.routes.ontology import ontology_section

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])

TOOL_ASK = "ask"
TOOL_PREVIEW = "preview"
TOOL_LIST_METRICS = "list_metrics"

TOOLS: list[dict[str, Any]] = [
    {
        "name": TOOL_ASK,
        "description": "Governed ask. Same handler as POST /v1/chat/ask.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "space_id": {"type": "string"},
                "session_id": {"type": "string"},
                "grounded_tables": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question"],
        },
        "http": "POST /v1/chat/ask",
    },
    {
        "name": TOOL_PREVIEW,
        "description": "Warehouse preview. Same handler as GET .../warehouse/{table}/preview.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "space_id": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["table"],
        },
        "http": "GET /v1/library/warehouse/{table}/preview",
    },
    {
        "name": TOOL_LIST_METRICS,
        "description": "Ontology metrics. Same handler as GET /v1/ontology/metrics.",
        "inputSchema": {
            "type": "object",
            "properties": {"pack": {"type": "string"}},
        },
        "http": "GET /v1/ontology/metrics",
    },
]


class McpCallIn(BaseModel):
    """Matches cortex-contract McpCallIn (name + arguments). No new wire type."""

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _int_arg(args: dict[str, Any], key: str, default: int, *, lo: int, hi: int) -> int:
    raw = args.get(key, default)
    try:
        n = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{key} must be an integer") from exc
    if n < lo or n > hi:
        raise HTTPException(status_code=400, detail=f"{key} out of range")
    return n


@router.get("/tools")
def mcp_tools() -> dict[str, Any]:
    return {
        "ok": True,
        "protocol": "dms-mcp-http/0.1",
        "flag": "DMS_MCP",
        "honesty": "Thin wrap of existing DMS HTTP. Not a second serving engine.",
        "tools": TOOLS,
    }


@router.post("/call")
def mcp_call(
    body: McpCallIn,
    settings: SettingsDep,
    store: SpaceStoreDep,
    cortex: CortexDep,
    ask: AskServiceDep,
) -> dict[str, Any]:
    decision = compliance_gate(
        action="mcp.call",
        metadata={"task_id": "mcp.call", "tool": body.name[:80]},
        client=cortex,
    )
    # Reads only (ask / preview / list_metrics). POST is the MCP transport.
    enforce(decision, mutation=False)

    name = body.name.strip()
    args = body.arguments or {}
    if name == TOOL_ASK:
        question = str(args.get("question") or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="question required")
        grounded = args.get("grounded_tables")
        try:
            ask_body = AskBody(
                question=question,
                space_id=args.get("space_id"),
                session_id=args.get("session_id"),
                grounded_tables=grounded,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        result = chat_ask(ask_body, settings, store, cortex, ask)
        return {"ok": True, "name": name, "result": result}

    if name == TOOL_PREVIEW:
        table = str(args.get("table") or "").strip()
        if not table or "/" in table or ".." in table:
            raise HTTPException(status_code=400, detail="table required")
        limit = _int_arg(args, "limit", 100, lo=1, hi=500)
        offset = _int_arg(args, "offset", 0, lo=0, hi=1_000_000)
        space_id = args.get("space_id")
        space = str(space_id).strip() if space_id else None
        result = preview_wh_table(
            table, cortex, limit=limit, offset=offset, space_id=space
        )
        return {"ok": True, "name": name, "result": result}

    if name == TOOL_LIST_METRICS:
        pack = args.get("pack")
        pack_s = str(pack).strip() if pack else None
        result = ontology_section("metrics", settings, pack=pack_s)
        return {"ok": True, "name": name, "result": result}

    raise HTTPException(
        status_code=404,
        detail={"ok": False, "error": "unknown_tool", "name": name},
    )
