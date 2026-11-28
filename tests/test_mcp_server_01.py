"""FEAT-MCP-SERVER-01: /mcp JSON-RPC. Flag off is 404 and the HTTP ask is unchanged."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.settings import get_settings
from fastapi.testclient import TestClient
from starlette.types import ASGIApp

PROTOCOL = "2025-11-25"
ACCEPT = "application/json, text/event-stream"
TOKEN = "test-mcp-key-01"
OTHER = "test-mcp-key-02"
BAD = "bad-bearer-token-zz"
KEYS = f"steward:{TOKEN};admin:{OTHER}"
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
STABLE = (
    "badge",
    "abstained",
    "values",
    "text",
    "sql_used",
    "rows",
    "route",
    "served_route",
)
_ACTORS: list[tuple[str, str | None]] = []


def _allow(*, action: str, actor: str | None = None, **_: object) -> ComplianceDecision:
    _ACTORS.append((action, actor))
    return ComplianceDecision(allowed=True, reason="test_allow", action=action)


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.mcp_server.tools as tools
    import dms_api.routes.chat as chat_routes

    monkeypatch.setattr(tools, "compliance_gate", _allow)
    monkeypatch.setattr(chat_routes, "compliance_gate", _allow)


def _stable(env: dict[str, Any]) -> bytes:
    picked = {key: env.get(key) for key in STABLE}
    return json.dumps(picked, sort_keys=True, default=str).encode()


def _parse_body(content_type: str, text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    if "text/event-stream" in content_type or text.lstrip().startswith("data:"):
        found: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw:
                found.append(json.loads(raw))
        return found
    body = json.loads(text)
    if isinstance(body, list):
        return body
    return [body]


def _headers(token: str | None, session: str | None) -> dict[str, str]:
    headers = {
        "Accept": ACCEPT,
        "Content-Type": "application/json",
        "mcp-protocol-version": PROTOCOL,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if session:
        headers["mcp-session-id"] = session
    return headers


def _rpc(
    client: TestClient,
    *,
    method: str,
    params: dict[str, Any] | None = None,
    req_id: int = 1,
    token: str | None = TOKEN,
    session: str | None = None,
    progress: str | None = None,
) -> tuple[httpx.Response, list[dict[str, Any]]]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    if progress is not None:
        payload.setdefault("params", {}).setdefault("_meta", {})["progressToken"] = progress
    resp = client.post(
        "/mcp",
        content=json.dumps(payload),
        headers=_headers(token, session),
    )
    return resp, _parse_body(resp.headers.get("content-type", ""), resp.text)


def _result(messages: list[dict[str, Any]], req_id: int) -> dict[str, Any]:
    for message in messages:
        if message.get("id") == req_id and "result" in message:
            return message["result"]
    raise AssertionError("no result")


def _tool_json(messages: list[dict[str, Any]], req_id: int) -> Any:
    result = _result(messages, req_id)
    content = result["content"]
    return json.loads(content[0]["text"])


def _handshake(
    client: TestClient,
    *,
    token: str | None = TOKEN,
    elicit: bool = False,
) -> str:
    caps: dict[str, Any] = {"elicitation": {}} if elicit else {}
    resp, messages = _rpc(
        client,
        method="initialize",
        req_id=1,
        token=token,
        params={
            "protocolVersion": PROTOCOL,
            "capabilities": caps,
            "clientInfo": {"name": "dms-test", "version": "0"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert any(item.get("result") for item in messages)
    session = resp.headers.get("mcp-session-id")
    assert session
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    noted = client.post("/mcp", content=json.dumps(note), headers=_headers(token, session))
    assert noted.status_code in (200, 202), noted.text
    return session


def _ask_payload() -> dict[str, Any]:
    return {"question": "What was total revenue?", "space_id": FINANCE}


@pytest.fixture(autouse=True)
def _reset_mcp_state() -> Iterator[None]:
    from dms_api.mcp_server.auth import reset_rate_limits
    from dms_api.mcp_server.tools import reset_proofs
    from dms_core.ask_stream import clear_asks

    _ACTORS.clear()
    clear_asks()
    reset_rate_limits()
    reset_proofs()
    yield
    _ACTORS.clear()
    clear_asks()
    reset_rate_limits()
    reset_proofs()
    get_settings.cache_clear()


@pytest.fixture()
def mcp_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("DMS_MCP_V2", "1")
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.setenv("DMS_API_KEYS", KEYS)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DMS_MCP", raising=False)
    path = tmp_path / "wh.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    _gate_allows(monkeypatch)
    from dms_api.app import create_app

    return create_app()


def _http_ask(client: TestClient) -> dict[str, Any]:
    resp = client.post("/v1/chat/ask", json=_ask_payload())
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_flag_off_mcp_is_404_and_http_ask_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_MCP_V2", raising=False)
    monkeypatch.delenv("DMS_MCP", raising=False)
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "wh.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    import dms_api.routes.chat as chat_routes

    monkeypatch.setattr(chat_routes, "compliance_gate", _allow)
    from dms_api.app import create_app

    off = create_app()
    paths = [getattr(route, "path", None) for route in off.routes]
    assert "/mcp" not in paths
    with TestClient(off) as client:
        missing = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert missing.status_code == 404
        assert client.get("/v1/mcp/tools").status_code == 404
        first = _stable(_http_ask(client))
        second = _stable(_http_ask(client))
    assert first == second
    monkeypatch.setenv("DMS_MCP_V2", "1")
    monkeypatch.setenv("DMS_API_KEYS", KEYS)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        third = _stable(_http_ask(client))
    assert first == third


def test_flag_on_leaves_rest_mcp_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_MCP", "1")
    monkeypatch.setenv("DMS_MCP_V2", "1")
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.setenv("DMS_API_KEYS", KEYS)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "wh.duckdb"))
    get_settings.cache_clear()
    from dms_api.app import create_app

    with TestClient(create_app()) as client:
        listed = client.get("/v1/mcp/tools")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["tools"]] == ["ask", "preview", "list_metrics"]


def test_initialize_and_ask_matches_http(mcp_app: Any) -> None:
    with TestClient(mcp_app) as client:
        http_env = _http_ask(client)
        session = _handshake(client)
        resp, messages = _rpc(
            client,
            method="tools/list",
            req_id=2,
            session=session,
        )
        assert resp.status_code == 200, resp.text
        names = [item["name"] for item in _result(messages, 2)["tools"]]
        assert names == [
            "ask",
            "answer_clarify",
            "list_spaces",
            "list_metrics",
            "describe_space",
            "get_proof",
        ]
        listed = json.dumps(_result(messages, 2))
        assert TOKEN not in listed
        resp, messages = _rpc(
            client,
            method="tools/call",
            req_id=3,
            session=session,
            params={"name": "ask", "arguments": _ask_payload()},
        )
        assert resp.status_code == 200, resp.text
        got = _tool_json(messages, 3)
        assert _stable(got) == _stable(http_env)
        assert ("mcp.call", "api_steward") in _ACTORS


def test_ungranted_table_matches_http(mcp_app: Any) -> None:
    """A Space with no grant. Demo mode askbacks instead of serving a figure."""
    with TestClient(mcp_app) as client:
        created = client.app.state.space_store.create("Ungranted")
        body = {"question": "Count the rows", "space_id": created.id}
        http = client.post("/v1/chat/ask", json=body)
        session = _handshake(client)
        resp, messages = _rpc(
            client,
            method="tools/call",
            req_id=4,
            session=session,
            params={"name": "ask", "arguments": body},
        )
        assert resp.status_code == 200
        got = _tool_json(messages, 4)
    assert http.status_code == 200
    env = http.json()
    assert env.get("abstained") is True
    assert env.get("values") == []
    assert env.get("abstain_reason") == "demo_scope"
    assert _stable(got) == _stable(env)


def test_missing_and_bad_bearer_have_no_result(mcp_app: Any) -> None:
    with TestClient(mcp_app) as client:
        missing, missing_msgs = _rpc(client, method="initialize", token=None, params={})
        bad, bad_msgs = _rpc(
            client,
            method="initialize",
            token=BAD,
            params={
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "x", "version": "0"},
            },
        )
    assert missing.status_code == 401
    assert bad.status_code == 401
    assert missing_msgs[0]["error"]["message"] == "missing_bearer"
    assert bad_msgs[0]["error"]["message"] == "invalid_bearer"
    assert "result" not in missing_msgs[0]
    assert "result" not in bad_msgs[0]
    assert BAD not in bad.text
    assert TOKEN not in bad.text


def test_describe_space_drops_rows(mcp_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.mcp_server.tools as tools

    def _view(space_id: str, store: object) -> dict[str, Any]:
        return {"space_id": space_id, "objects": [{"name": "orders"}], "rows": [{"n": 1}]}

    monkeypatch.setattr(tools, "get_space_ontology", _view)
    with TestClient(mcp_app) as client:
        session = _handshake(client)
        _resp, messages = _rpc(
            client,
            method="tools/call",
            req_id=5,
            session=session,
            params={"name": "describe_space", "arguments": {"space_id": FINANCE}},
        )
        got = _tool_json(messages, 5)
    assert got["objects"] == [{"name": "orders"}]
    assert "rows" not in got


def test_get_proof_is_caller_scoped(mcp_app: Any) -> None:
    with TestClient(mcp_app) as client:
        session = _handshake(client)
        _resp, messages = _rpc(
            client,
            method="tools/call",
            req_id=6,
            session=session,
            params={"name": "ask", "arguments": _ask_payload()},
        )
        env = _tool_json(messages, 6)
        proof_id = env.get("answer_id") or env.get("audit_id")
        assert isinstance(proof_id, str) and proof_id
        _resp, messages = _rpc(
            client,
            method="tools/call",
            req_id=7,
            session=session,
            params={"name": "get_proof", "arguments": {"proof_id": proof_id}},
        )
        proof = _tool_json(messages, 7)
        other = _handshake(client, token=OTHER)
        _resp, messages = _rpc(
            client,
            method="tools/call",
            req_id=8,
            session=other,
            token=OTHER,
            params={"name": "get_proof", "arguments": {"proof_id": proof_id}},
        )
        hidden = _tool_json(messages, 8)
    assert proof["sql"] == (env.get("sql_used") or "")
    assert "rows" not in proof
    assert "values" not in proof
    assert "text" not in proof
    assert hidden == {"error": "proof_not_found"}


def test_rate_limit_returns_no_data(mcp_app: Any) -> None:
    from dms_api.mcp_server.auth import reset_rate_limits

    with TestClient(mcp_app) as client:
        session = _handshake(client)
        reset_rate_limits()
        last = None
        for num in range(1, 22):
            last, messages = _rpc(client, method="tools/list", req_id=num, session=session)
            if num <= 20:
                assert last.status_code == 200, last.text
        assert last is not None
        assert last.status_code == 429
        assert messages[0]["error"]["message"] == "rate_limited"
        assert "result" not in messages[0]
        assert TOKEN not in last.text


def test_progress_uses_stream_stages(mcp_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ASK_STREAM", "1")
    with TestClient(mcp_app) as client:
        session = _handshake(client)
        resp, messages = _rpc(
            client,
            method="tools/call",
            req_id=9,
            session=session,
            progress="p1",
            params={"name": "ask", "arguments": _ask_payload()},
        )
    assert resp.status_code == 200, resp.text
    notes = [
        item.get("params", {}).get("message", "")
        for item in messages
        if item.get("method") == "notifications/progress"
    ]
    assert any(isinstance(note, str) and note.startswith("reconfirm:") for note in notes)
    assert TOKEN not in resp.text


def _clarify_env() -> dict[str, Any]:
    return {
        "badge": "clarify",
        "abstained": True,
        "values": [],
        "text": "Which reading?",
        "clarify": {
            "clarify_id": "clr_test_01",
            "readings": [
                {"id": "r1", "text": "Revenue last month"},
                {"id": "r2", "text": "Revenue last year"},
            ],
        },
    }


def _picked_env() -> dict[str, Any]:
    return {
        "badge": "green",
        "abstained": False,
        "values": [1],
        "text": "picked",
        "sql_used": "select 1",
        "rows": [],
    }


@asynccontextmanager
async def _lifespan(app: ASGIApp) -> Any:
    started = anyio.Event()
    stopped = anyio.Event()
    failed: list[str] = []

    async def receive() -> dict[str, Any]:
        if not started.is_set():
            return {"type": "lifespan.startup"}
        await stopped.wait()
        return {"type": "lifespan.shutdown"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "lifespan.startup.complete":
            started.set()
        elif message["type"] == "lifespan.startup.failed":
            failed.append(str(message.get("message") or "startup failed"))
            started.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(app, {"type": "lifespan", "state": {}}, receive, send)
        await started.wait()
        if failed:
            raise RuntimeError(failed[0])
        try:
            yield
        finally:
            stopped.set()


class _Live:
    """In-process ASGI call that yields SSE before the response finishes.

    httpx's ASGI transport buffers until the app returns, which deadlocks
    elicitation: the server will not finish the POST until the client answers.
    """

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.started = anyio.Event()
        self._send, self._recv = anyio.create_memory_object_stream[bytes](32)

    async def lines(self) -> Any:
        buf = ""
        async for chunk in self._recv:
            buf += chunk.decode()
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                yield line.rstrip("\r")
        if buf:
            yield buf.rstrip("\r")


def _begin(
    tg: anyio.abc.TaskGroup,
    app: ASGIApp,
    payload: dict[str, Any] | None,
    *,
    token: str | None,
    session: str | None,
) -> _Live:
    live = _Live()
    body = b"" if payload is None else json.dumps(payload).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in _headers(token, session).items()
        ],
        "client": ("127.0.0.1", 123),
        "server": ("testserver", 80),
        "root_path": "",
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await anyio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            live.status = int(message["status"])
            live.headers = {
                key.decode().lower(): value.decode() for key, value in message.get("headers", [])
            }
            live.started.set()
        elif message["type"] == "http.response.body":
            chunk = message.get("body") or b""
            if chunk:
                await live._send.send(chunk)
            if not message.get("more_body", False):
                await live._send.aclose()

    async def run() -> None:
        try:
            await app(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await live._send.aclose()
            live.started.set()

    tg.start_soon(run)
    return live


async def _finish(live: _Live) -> tuple[int, dict[str, str], list[dict[str, Any]]]:
    with anyio.fail_after(10):
        await live.started.wait()
    text = "\n".join([line async for line in live.lines()])
    assert live.status is not None
    return live.status, live.headers, _parse_body(live.headers.get("content-type", ""), text)


async def _open_live(tg: anyio.abc.TaskGroup, app: ASGIApp, *, elicit: bool) -> str:
    caps: dict[str, Any] = {"elicitation": {}} if elicit else {}
    status, headers, messages = await _finish(
        _begin(
            tg,
            app,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": caps,
                    "clientInfo": {"name": "dms-test", "version": "0"},
                },
            },
            token=TOKEN,
            session=None,
        )
    )
    assert status == 200
    assert any(item.get("result") for item in messages)
    session = headers.get("mcp-session-id")
    assert session
    noted, _note_headers, _note_messages = await _finish(
        _begin(
            tg,
            app,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            token=TOKEN,
            session=session,
        )
    )
    assert noted in (200, 202)
    return session


def test_clarify_without_elicitation_returns_the_envelope(
    mcp_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_api.mcp_server.tools as tools

    picks: list[str] = []

    def _ask(*_a: object, **_k: object) -> dict[str, Any]:
        return _clarify_env()

    def _pick(body: object, *_a: object, **_k: object) -> dict[str, Any]:
        picks.append(getattr(body, "reading_id", ""))
        return _picked_env()

    monkeypatch.setattr(tools, "chat_ask", _ask)
    monkeypatch.setattr(tools, "chat_clarify_pick", _pick)
    with TestClient(mcp_app) as client:
        session = _handshake(client, elicit=False)
        _resp, messages = _rpc(
            client,
            method="tools/call",
            req_id=10,
            session=session,
            params={"name": "ask", "arguments": {"question": "Revenue?"}},
        )
        got = _tool_json(messages, 10)
    assert got["clarify"]["clarify_id"] == "clr_test_01"
    assert picks == []


def test_clarify_elicitation_pick_returns_the_answer(
    mcp_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_api.mcp_server.tools as tools

    picks: list[str] = []

    def _ask(*_a: object, **_k: object) -> dict[str, Any]:
        return _clarify_env()

    def _pick(body: object, *_a: object, **_k: object) -> dict[str, Any]:
        picks.append(str(getattr(body, "reading_id", "")))
        return _picked_env()

    monkeypatch.setattr(tools, "chat_ask", _ask)
    monkeypatch.setattr(tools, "chat_clarify_pick", _pick)

    async def _run() -> None:
        with anyio.fail_after(15):
            async with _lifespan(mcp_app):
                async with anyio.create_task_group() as tg:
                    session = await _open_live(tg, mcp_app, elicit=True)
                    live = _begin(
                        tg,
                        mcp_app,
                        {
                            "jsonrpc": "2.0",
                            "id": 11,
                            "method": "tools/call",
                            "params": {"name": "ask", "arguments": {"question": "Revenue?"}},
                        },
                        token=TOKEN,
                        session=session,
                    )
                    await live.started.wait()
                    assert live.status == 200
                    replied = False
                    collected: list[dict[str, Any]] = []

                    async def _reply(message: dict[str, Any]) -> None:
                        params = message.get("params") or {}
                        schema = json.dumps(params.get("requestedSchema") or params)
                        assert "r1" in schema and "r2" in schema
                        answer = {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {"action": "accept", "content": {"reading_id": "r1"}},
                        }
                        status, _headers_out, _messages = await _finish(
                            _begin(tg, mcp_app, answer, token=TOKEN, session=session)
                        )
                        assert status in (200, 202)

                    async for line in live.lines():
                        if not line.startswith("data:"):
                            continue
                        message = json.loads(line[5:].strip())
                        collected.append(message)
                        if replied or message.get("method") != "elicitation/create":
                            continue
                        replied = True
                        tg.start_soon(_reply, message)
                    assert replied
                    got = _tool_json(collected, 11)
                    assert got["text"] == "picked"
                    assert picks == ["r1"]

    anyio.run(_run)


def test_clarify_decline_does_not_guess(mcp_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.mcp_server.tools as tools

    picks: list[str] = []

    def _ask(*_a: object, **_k: object) -> dict[str, Any]:
        return _clarify_env()

    def _pick(body: object, *_a: object, **_k: object) -> dict[str, Any]:
        picks.append("called")
        return _picked_env()

    monkeypatch.setattr(tools, "chat_ask", _ask)
    monkeypatch.setattr(tools, "chat_clarify_pick", _pick)

    async def _run() -> None:
        with anyio.fail_after(15):
            async with _lifespan(mcp_app):
                async with anyio.create_task_group() as tg:
                    session = await _open_live(tg, mcp_app, elicit=True)
                    live = _begin(
                        tg,
                        mcp_app,
                        {
                            "jsonrpc": "2.0",
                            "id": 12,
                            "method": "tools/call",
                            "params": {"name": "ask", "arguments": {"question": "Revenue?"}},
                        },
                        token=TOKEN,
                        session=session,
                    )
                    await live.started.wait()
                    replied = False
                    collected: list[dict[str, Any]] = []

                    async def _reply(message: dict[str, Any]) -> None:
                        answer = {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {"action": "decline"},
                        }
                        status, _headers_out, _messages = await _finish(
                            _begin(tg, mcp_app, answer, token=TOKEN, session=session)
                        )
                        assert status in (200, 202)

                    async for line in live.lines():
                        if not line.startswith("data:"):
                            continue
                        message = json.loads(line[5:].strip())
                        collected.append(message)
                        if replied or message.get("method") != "elicitation/create":
                            continue
                        replied = True
                        tg.start_soon(_reply, message)
                    assert replied
                    got = _tool_json(collected, 12)
                    assert got["clarify"]["clarify_id"] == "clr_test_01"
                    assert picks == []

    anyio.run(_run)
