"""AO-JSON-01: JSON schema request body behind DMS_AO_RESPONSE_FORMAT.

Synthetic schemas only. Flag off keeps today's request bytes and today's
json.loads refusal. Flag on adds response_format and one fenced-JSON retry.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from ao_vendor.llm.messages import HumanMessage
from ao_vendor.llm.ov_client import (
    REFUSE_OUTPUT_INVALID,
    RESPONSE_FORMAT_FLAG,
    AoLlmError,
    OpenVaultOntologyClient,
    build_request_body,
    default_transport,
)
from pydantic import BaseModel, ConfigDict, ValidationError

_MODEL_KEYS = frozenset(
    {
        "model",
        "model_id",
        "model_name",
        "served_model",
        "served_provider",
        "provider",
        "strict",
    }
)
_TODAY = (
    '{"role": "ontology", "messages": [{"role": "user", "content": "ping"}], '
    '"schema": "SyntheticNote"}'
)
_BARE = {"label": "alpha", "count": 2}


class SyntheticNote(BaseModel):
    """Synthetic structured output. Not a catalog table."""

    model_config = ConfigDict(extra="forbid")

    label: str
    count: int


class SyntheticSneak(BaseModel):
    """A field whose JSON name is a forbidden body key."""

    model_config = ConfigDict(extra="forbid")

    model: str


def _fenced(payload: dict[str, Any], *, tag: str = "json") -> str:
    return f"```{tag}\n{json.dumps(payload)}\n```\n"


def _client(monkeypatch: pytest.MonkeyPatch, transport: Any) -> OpenVaultOntologyClient:
    monkeypatch.setenv("OPENVAULT_API_KEY", "ov_synthetickey")
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    return OpenVaultOntologyClient(transport=transport)


def _walk_keys(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.append(str(key).casefold())
            found.extend(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_keys(item))
    return found


@pytest.mark.parametrize("value", [None, "0", "", "true", "yes"])
def test_flag_off_body_equals_todays_bytes(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(RESPONSE_FORMAT_FLAG, raising=False)
    else:
        monkeypatch.setenv(RESPONSE_FORMAT_FLAG, value)
    body = build_request_body([HumanMessage(content="ping")], SyntheticNote)
    assert json.dumps(body) == _TODAY
    assert list(body) == ["role", "messages", "schema"]
    assert "response_format" not in body


def test_flag_on_body_has_response_format_and_no_model_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RESPONSE_FORMAT_FLAG, "1")
    body = build_request_body([HumanMessage(content="ping")], SyntheticNote)
    assert list(body) == ["role", "messages", "schema", "response_format"]
    assert body["role"] == "ontology"
    assert body["schema"] == "SyntheticNote"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "SyntheticNote",
            "schema": SyntheticNote.model_json_schema(),
        },
    }
    assert "strict" not in body["response_format"]["json_schema"]
    keys = set(_walk_keys(body))
    assert not keys & _MODEL_KEYS
    encoded = json.dumps(body).casefold()
    for needle in ("gpt-", "claude", "llama", "gemini"):
        assert needle not in encoded


def test_flag_on_refuses_a_schema_key_that_names_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RESPONSE_FORMAT_FLAG, "1")
    with pytest.raises(AoLlmError) as info:
        build_request_body([HumanMessage(content="ping")], SyntheticSneak)
    assert info.value.code == "model_key_refused"


def test_flag_on_bare_and_fenced_strings_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RESPONSE_FORMAT_FLAG, "1")
    bare = json.dumps(_BARE)
    samples = (bare, _fenced(_BARE), _fenced(_BARE, tag=""), " \n" + _fenced(_BARE))
    for raw in samples:
        seen: list[dict[str, Any]] = []

        def transport(
            url: str, headers: dict[str, str], body: dict[str, Any], *, raw: str = raw
        ) -> str:
            assert url.endswith("/v1/chat/completions")
            assert headers["Authorization"] == "Bearer ov_synthetickey"
            assert "ov_synthetickey" not in json.dumps(body)
            seen.append(body)
            return raw

        client = _client(monkeypatch, transport)
        parsed = client.complete_structured([HumanMessage(content="ping")], SyntheticNote)
        assert parsed.label == "alpha"
        assert parsed.count == 2
        assert client.calls_ok == 1
        assert client.failures == []
        assert seen[0]["response_format"]["type"] == "json_schema"


def test_flag_on_does_not_hunt_for_a_fence_in_the_middle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fence that is not the leading line is one failed parse, then the same refusal."""
    monkeypatch.setenv(RESPONSE_FORMAT_FLAG, "1")

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        return "note\n" + _fenced(_BARE)

    client = _client(monkeypatch, transport)
    with pytest.raises(json.JSONDecodeError):
        client.complete_structured([HumanMessage(content="ping")], SyntheticNote)
    assert client.failures == [REFUSE_OUTPUT_INVALID]
    assert client.calls_ok == 0


def test_flag_off_fenced_valid_json_still_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RESPONSE_FORMAT_FLAG, raising=False)

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        assert "response_format" not in body
        return _fenced(_BARE)

    client = _client(monkeypatch, transport)
    with pytest.raises(json.JSONDecodeError):
        client.complete_structured([HumanMessage(content="ping")], SyntheticNote)
    assert client.failures == [REFUSE_OUTPUT_INVALID]
    assert client.calls_ok == 0


@pytest.mark.parametrize("flag", ["0", "1"])
@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "```json\nnot-json\n```",
        '{"nope": 1}',
        '```json\n{"nope": 1}\n```',
    ],
)
def test_garbage_yields_ao_model_output_invalid(
    monkeypatch: pytest.MonkeyPatch, flag: str, raw: str
) -> None:
    monkeypatch.setenv(RESPONSE_FORMAT_FLAG, flag)

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        return raw

    client = _client(monkeypatch, transport)
    with pytest.raises((json.JSONDecodeError, ValidationError)):
        client.complete_structured([HumanMessage(content="ping")], SyntheticNote)
    assert client.failures == [REFUSE_OUTPUT_INVALID]
    assert client.calls_ok == 0


class _Chat:
    """Loopback chat completion. ``content`` is the message string."""

    def __init__(self) -> None:
        self.content = ""
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                payload = {"choices": [{"message": {"content": outer.content}}]}
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address[:2]
        self.url = f"http://{host}:{port}/v1/chat/completions"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    server = _Chat()
    try:
        yield server
    finally:
        server.close()


def test_default_transport_flag_off_parses_bare_and_rejects_fence(
    monkeypatch: pytest.MonkeyPatch, chat: _Chat
) -> None:
    monkeypatch.delenv(RESPONSE_FORMAT_FLAG, raising=False)
    chat.content = json.dumps(_BARE)
    assert default_transport(chat.url, {}, {"role": "ontology"}) == _BARE
    chat.content = _fenced(_BARE)
    with pytest.raises(json.JSONDecodeError):
        default_transport(chat.url, {}, {"role": "ontology"})


def test_default_transport_flag_on_parses_bare_and_fenced(
    monkeypatch: pytest.MonkeyPatch, chat: _Chat
) -> None:
    monkeypatch.setenv(RESPONSE_FORMAT_FLAG, "1")
    chat.content = json.dumps(_BARE)
    assert default_transport(chat.url, {}, {"role": "ontology"}) == _BARE
    chat.content = _fenced(_BARE)
    assert default_transport(chat.url, {}, {"role": "ontology"}) == _BARE
    chat.content = "```json\nnot-json\n```"
    with pytest.raises(json.JSONDecodeError):
        default_transport(chat.url, {}, {"role": "ontology"})
