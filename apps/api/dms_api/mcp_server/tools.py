"""MCP tools. Each one calls an existing route handler. No second ask path.

Descriptions are the constants below. They carry no rows, no secrets, and no
question text. Clarify is detected from the envelope's clarify block, never
from the wording of the question.
"""

from __future__ import annotations

import enum
import json
import secrets
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any, Literal

import anyio
from cortex_client import compliance_gate
from dms_core.ask_stream import stream_enabled, wait_events
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from mcp.server.elicitation import AcceptedElicitation
from mcp.server.mcpserver.context import Context
from pydantic import BaseModel, ValidationError, create_model

from dms_api.gatekeeping import enforce
from dms_api.mcp_server.auth import current_mcp_actor
from dms_api.routes.chat import AskBody, ClarifyPickBody, chat_ask, chat_clarify_pick
from dms_api.routes.ontology import ontology_section
from dms_api.routes.spaces import get_space_ontology, list_spaces
from dms_api.settings import get_settings

ASK_DESCRIPTION = "Governed ask. Same handler as POST /v1/chat/ask. Read only."
ANSWER_CLARIFY_DESCRIPTION = (
    "Pick one stored clarify reading by id. Same handler as POST /v1/chat/clarify/pick. Read only."
)
LIST_SPACES_DESCRIPTION = "List Spaces for the caller. Names and counts only. Read only."
LIST_METRICS_DESCRIPTION = "Ontology metrics. Same handler as GET /v1/ontology/metrics. Read only."
DESCRIBE_SPACE_DESCRIPTION = (
    "Ontology summary for one Space: objects, links, and status. No cell values. Read only."
)
GET_PROOF_DESCRIPTION = (
    "SQL, sources, and route stamp for a proof id this caller already received. "
    "No cell values. Read only."
)

_ROW_KEYS = frozenset({"rows", "cells", "sample_rows", "preview_rows", "row_values", "records"})
_PROOF_CAP = 256
_proofs: dict[str, dict[str, Any]] = {}
_proof_order: deque[str] = deque()
_app: ContextVar[Any] = ContextVar("dms_mcp_app", default=None)


def bind_app(app: Any) -> Token[Any]:
    return _app.set(app)


def reset_app(token: Token[Any]) -> None:
    _app.reset(token)


def reset_proofs() -> None:
    _proofs.clear()
    _proof_order.clear()


def _fastapi() -> Any:
    app = _app.get()
    if app is None:
        raise RuntimeError("mcp app unset")
    return app


def _dump(payload: Any) -> str:
    return json.dumps(jsonable_encoder(payload), separators=(",", ":"), sort_keys=True)


def _handles() -> tuple[Any, Any, Any, Any, Any, Any]:
    app = _fastapi()
    settings = get_settings()
    store = app.state.space_store
    cortex = getattr(app.state, "cortex", None)
    ask = getattr(app.state, "ask_service", None)
    if ask is None:
        from dms_api.wiring import build_ask_service

        ask = build_ask_service(cortex)
        app.state.ask_service = ask
    audit = getattr(app.state, "ask_audit_store", None)
    if audit is None:
        from dms_core.control_plane.ask_audit import InMemoryAskAuditStore

        audit = InMemoryAskAuditStore()
        app.state.ask_audit_store = audit
    binding = getattr(app.state, "space_store_binding", None)
    if binding is None:
        from dms_api.store.binding import StoreBinding

        binding = StoreBinding.memory(configured=bool(settings.database_url))
    return settings, store, cortex, ask, audit, binding


def _http_body(exc: HTTPException) -> str:
    return _dump({"status": exc.status_code, "detail": exc.detail})


def _call_handler(fn: Callable[[], Any]) -> str:
    try:
        result = fn()
    except HTTPException as exc:
        return _http_body(exc)
    return _dump(result)


def _stub_request(app: Any, ask_id: str) -> Any:
    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/ask",
        "raw_path": b"/v1/chat/ask",
        "query_string": b"",
        "headers": [(b"x-dms-ask-id", ask_id.encode("ascii"))],
        "client": ("127.0.0.1", 0),
        "server": ("testserver", 80),
        "root_path": "",
        "app": app,
    }

    async def receive() -> dict[str, Any]:
        await anyio.Event().wait()
        return {"type": "http.disconnect"}

    return Request(scope, receive)


def _clarify_block(env: dict[str, Any]) -> dict[str, Any] | None:
    block = env.get("clarify")
    if not isinstance(block, dict):
        return None
    readings = block.get("readings")
    if not isinstance(readings, list) or not readings:
        return None
    return block


def _can_elicit(ctx: Context) -> bool:
    caps = ctx.client_capabilities
    elicitation = getattr(caps, "elicitation", None) if caps is not None else None
    if elicitation is None:
        return False
    form = getattr(elicitation, "form", None)
    url = getattr(elicitation, "url", None)
    return form is not None or url is None


def _reading_schema(ids: list[str]) -> type[BaseModel]:
    choice = Literal[tuple(ids)]  # type: ignore[valid-type]
    return create_model("ReadingChoice", reading_id=(choice, ...))


def _reading_value(data: Any) -> str | None:
    raw = getattr(data, "reading_id", None)
    if raw is None and isinstance(data, dict):
        raw = data.get("reading_id")
    if isinstance(raw, enum.Enum):
        raw = raw.value
    if isinstance(raw, str) and raw:
        return raw
    return None


def _sources(env: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("contributing_sources", "sources"):
        raw = env.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str) and item not in found:
                found.append(item)
    return found


def _remember(env: dict[str, Any], actor: str) -> None:
    if env.get("status") is not None and "detail" in env and "badge" not in env:
        return
    sql = env.get("sql_used")
    route = env.get("served_route")
    if not isinstance(route, str) or not route:
        route = env.get("route")
    record = {
        "sql": sql if isinstance(sql, str) else "",
        "sources": _sources(env),
        "route": route if isinstance(route, str) else "",
        "actor": actor,
    }
    for key in (env.get("answer_id"), env.get("audit_id")):
        if not isinstance(key, str) or not key:
            continue
        if key not in _proofs:
            _proof_order.append(key)
        _proofs[key] = record
        while len(_proof_order) > _PROOF_CAP:
            _proofs.pop(_proof_order.popleft(), None)


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items() if key not in _ROW_KEYS}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


async def _pump(ctx: Context, ask_id: str, stop: anyio.Event) -> None:
    if not stream_enabled():
        return
    index = 0
    seen = 0
    while not stop.is_set():
        batch, done = await anyio.to_thread.run_sync(lambda i=index: wait_events(ask_id, i, 0.05))
        for event in batch:
            seen += 1
            stage = str(event.get("stage") or "")
            status = str(event.get("status") or "")
            await ctx.report_progress(seen, message=f"{stage}:{status}")
        index += len(batch)
        if done:
            return


async def _maybe_elicit(ctx: Context, env: dict[str, Any]) -> str | None:
    block = _clarify_block(env)
    if block is None or not _can_elicit(ctx):
        return None
    readings = [item for item in block["readings"] if isinstance(item, dict)]
    ids = [item["id"] for item in readings if isinstance(item.get("id"), str) and item["id"]]
    if len(ids) < 1:
        return None
    lines = ["Pick one reading by id."]
    for item in readings:
        rid = item.get("id")
        text = item.get("text")
        if isinstance(rid, str) and isinstance(text, str):
            lines.append(f"{rid}: {text}")
    try:
        outcome = await ctx.elicit("\n".join(lines), _reading_schema(ids))
    except Exception:
        return None
    if not isinstance(outcome, AcceptedElicitation):
        return None
    chosen = _reading_value(outcome.data)
    if chosen not in ids:
        return None
    clarify_id = block.get("clarify_id")
    if not isinstance(clarify_id, str) or not clarify_id:
        return None
    _settings, _store, cortex, ask, _audit, _binding = _handles()
    body = ClarifyPickBody(clarify_id=clarify_id, reading_id=chosen)
    return await anyio.to_thread.run_sync(
        lambda: _call_handler(lambda: chat_clarify_pick(body, cortex, ask))
    )


def register(server: Any) -> None:
    @server.tool(name="ask", description=ASK_DESCRIPTION, structured_output=False)
    async def ask(
        question: str,
        ctx: Context,
        space_id: str | None = None,
        session_id: str | None = None,
        grounded_tables: list[str] | None = None,
    ) -> str:
        settings, store, cortex, ask_svc, audit, _binding = _handles()
        decision = compliance_gate(
            action="mcp.call",
            actor=current_mcp_actor(),
            metadata={"task_id": "mcp.call", "tool": "ask"},
            client=cortex,
        )
        enforce(decision, mutation=False)
        if not question.strip():
            return _dump({"error": "question_required"})
        bad_tables = grounded_tables is not None and (
            not isinstance(grounded_tables, list)
            or any(not isinstance(item, str) for item in grounded_tables)
        )
        if bad_tables:
            return _dump({"error": "invalid_grounded_tables"})
        try:
            body = AskBody(
                question=question.strip(),
                space_id=space_id,
                session_id=session_id,
                grounded_tables=grounded_tables,
            )
        except ValidationError as exc:
            return _dump({"error": "invalid_ask", "detail": exc.errors()})
        app = _fastapi()
        ask_id = secrets.token_hex(8)
        request = _stub_request(app, ask_id)
        stop = anyio.Event()

        def _run() -> str:
            return _call_handler(
                lambda: chat_ask(body, settings, store, cortex, ask_svc, audit, request)
            )

        # The pump must drain stage events after the ask returns. Cancelling it
        # with the ask drops the last notifications, including reconfirm.
        async with anyio.create_task_group() as tg:
            tg.start_soon(_pump, ctx, ask_id, stop)
            encoded = await anyio.to_thread.run_sync(_run)
            stop.set()
        try:
            env = json.loads(encoded)
        except json.JSONDecodeError:
            return encoded
        if isinstance(env, dict):
            actor = current_mcp_actor()
            if actor:
                _remember(env, actor)
            picked = await _maybe_elicit(ctx, env)
            if picked is not None:
                return picked
        return encoded

    @server.tool(
        name="answer_clarify",
        description=ANSWER_CLARIFY_DESCRIPTION,
        structured_output=False,
    )
    async def answer_clarify(clarify_id: str, reading_id: str) -> str:
        _settings, _store, cortex, ask_svc, _audit, _binding = _handles()
        decision = compliance_gate(
            action="mcp.call",
            actor=current_mcp_actor(),
            metadata={"task_id": "mcp.call", "tool": "answer_clarify"},
            client=cortex,
        )
        enforce(decision, mutation=False)
        try:
            body = ClarifyPickBody(clarify_id=clarify_id, reading_id=reading_id)
        except ValidationError as exc:
            return _dump({"error": "invalid_clarify", "detail": exc.errors()})
        return await anyio.to_thread.run_sync(
            lambda: _call_handler(lambda: chat_clarify_pick(body, cortex, ask_svc))
        )

    @server.tool(name="list_spaces", description=LIST_SPACES_DESCRIPTION, structured_output=False)
    async def list_spaces_tool() -> str:
        _settings, store, cortex, _ask, _audit, binding = _handles()
        decision = compliance_gate(
            action="mcp.call",
            actor=current_mcp_actor(),
            metadata={"task_id": "mcp.call", "tool": "list_spaces"},
            client=cortex,
        )
        enforce(decision, mutation=False)
        return await anyio.to_thread.run_sync(
            lambda: _call_handler(lambda: list_spaces(store, binding))
        )

    @server.tool(name="list_metrics", description=LIST_METRICS_DESCRIPTION, structured_output=False)
    async def list_metrics(space_id: str | None = None) -> str:
        settings, _store, cortex, _ask, _audit, _binding = _handles()
        decision = compliance_gate(
            action="mcp.call",
            actor=current_mcp_actor(),
            metadata={"task_id": "mcp.call", "tool": "list_metrics"},
            client=cortex,
        )
        enforce(decision, mutation=False)
        return await anyio.to_thread.run_sync(
            lambda: _call_handler(
                lambda: ontology_section("metrics", settings, pack=None, space_id=space_id)
            )
        )

    @server.tool(
        name="describe_space",
        description=DESCRIBE_SPACE_DESCRIPTION,
        structured_output=False,
    )
    async def describe_space(space_id: str) -> str:
        _settings, store, cortex, _ask, _audit, _binding = _handles()
        decision = compliance_gate(
            action="mcp.call",
            actor=current_mcp_actor(),
            metadata={"task_id": "mcp.call", "tool": "describe_space"},
            client=cortex,
        )
        enforce(decision, mutation=False)
        if not space_id.strip():
            return _dump({"error": "space_id_required"})

        def _run() -> str:
            try:
                view = get_space_ontology(space_id.strip(), store)
            except HTTPException as exc:
                return _http_body(exc)
            return _dump(_scrub(view))

        return await anyio.to_thread.run_sync(_run)

    @server.tool(name="get_proof", description=GET_PROOF_DESCRIPTION, structured_output=False)
    async def get_proof(proof_id: str) -> str:
        _settings, _store, cortex, _ask, _audit, _binding = _handles()
        actor = current_mcp_actor()
        decision = compliance_gate(
            action="mcp.call",
            actor=actor,
            metadata={"task_id": "mcp.call", "tool": "get_proof"},
            client=cortex,
        )
        enforce(decision, mutation=False)
        record = _proofs.get(proof_id)
        if record is None or record.get("actor") != actor:
            return _dump({"error": "proof_not_found"})
        return _dump({"sql": record["sql"], "sources": record["sources"], "route": record["route"]})
