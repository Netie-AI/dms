"""Streamable HTTP MCP at /mcp. Auth is DMS_API_KEYS, not the SDK OAuth verifier.

The session manager is started from the FastAPI lifespan, once. The child
Starlette lifespan is idle so it cannot start that manager a second time.
Elicitation needs a stateful SSE session, so json_response and stateless stay off.
DNS rebinding stays off: the test client host is not localhost, and a deploy
puts its own proxy in front.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from starlette.datastructures import Headers
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from dms_api.mcp_server.auth import (
    allow_actor,
    bearer_from_headers,
    bind_mcp_actor,
    lookup_bearer,
    reset_mcp_actor,
)

_MAX_BODY = 4 * 1024 * 1024


class _TooBig(Exception):
    pass


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body") or b""
        size += len(chunk)
        if size > _MAX_BODY:
            raise _TooBig
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _rpc_id(body: bytes) -> Any:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or "id" not in data:
        return None
    ident = data["id"]
    if ident is None or (isinstance(ident, (str, int)) and not isinstance(ident, bool)):
        return ident
    return None


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _send_json(send: Send, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw, "more_body": False})


class _AuthGate:
    def __init__(self, inner: Any, app: Any) -> None:
        self._inner = inner
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._inner(scope, receive, send)
            return
        try:
            body = await _read_body(receive)
        except _TooBig:
            await _send_json(send, 413, _error(None, -32001, "request_too_large"))
            return
        token = bearer_from_headers(Headers(scope=scope))
        caller, reason = lookup_bearer(token)
        del token
        if caller is None:
            await _send_json(send, 401, _error(_rpc_id(body), -32001, reason))
            return
        if not allow_actor(caller.actor):
            await _send_json(send, 429, _error(_rpc_id(body), -32029, "rate_limited"))
            return
        from dms_api.mcp_server.tools import bind_app, reset_app

        actor_token = bind_mcp_actor(caller.actor)
        app_token = bind_app(self._app)
        sent = False

        async def replay() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        try:
            await self._inner(scope, replay, send)
        finally:
            reset_mcp_actor(actor_token)
            reset_app(app_token)


def _missing_app() -> Callable[..., Any]:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        body = await _read_body(receive)
        await _send_json(send, 503, _error(_rpc_id(body), -32000, "mcp_extra_missing"))

    return app


@asynccontextmanager
async def _idle(_app: Any) -> AsyncIterator[None]:
    yield


def _build() -> tuple[Any, Any]:
    from mcp.server.mcpserver.server import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    from dms_api.mcp_server.tools import register

    server = MCPServer("dms")
    register(server)
    inner = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=False,
        stateless_http=False,
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    inner.router.lifespan_context = _idle
    manager = server._lowlevel_server._session_manager
    return inner, manager


@asynccontextmanager
async def _manager_lifespan(manager: Any) -> AsyncIterator[None]:
    if manager is None:
        yield
        return
    async with manager.run():
        yield


def mount_mcp(app: Any) -> Any:
    """Mount /mcp and return the lifespan context manager. Flag-on only."""
    try:
        inner, manager = _build()
    except ImportError:
        inner, manager = _missing_app(), None
    app.router.routes.append(
        Route(
            "/mcp",
            endpoint=_AuthGate(inner, app),
            methods=["GET", "POST", "DELETE"],
            name="mcp_v2",
            include_in_schema=False,
        )
    )
    return _manager_lifespan(manager)
