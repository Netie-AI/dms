"""ACT-02. No skip, no xfail. CI runs this file inside pytest tests/.

Flag off is main. With ``DMS_ACTIONS`` unset, nothing under ``/v1/amend/actions``
exists: every route, every method and every body (malformed JSON included) gets
the bytes an unknown path gets, never a 422 or a 403, and the OpenAPI schema is
the same document whether the flag is on or off. No Postgres needed.

R-0007: a 404 test passes vacuously if the routes were never mounted, so the
last test turns the flag on and proves the same path answers.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator

import pytest
from cortex_client.gate import ComplianceDecision
from fastapi.testclient import TestClient

U = str(uuid.uuid4())
S = str(uuid.uuid4())
BAD_JSON = b'{"space_id": "x", "action_type": '

#: The nine action routes, each with its own method and a body that main would
#: have to parse to refuse. Every one must be indistinguishable from no route.
ACTION_ROUTES: list[tuple[str, str, bytes | None]] = [
    ("GET", "/v1/amend/actions/types", None),
    ("GET", f"/v1/amend/actions/proposals?space_id={S}", None),
    ("POST", "/v1/amend/actions/proposals", BAD_JSON),
    ("POST", f"/v1/amend/actions/proposals/{U}/apply", b'{"idempotency_token": 7}'),
    ("POST", f"/v1/amend/actions/proposals/{U}/decline", b"{}"),
    ("GET", f"/v1/amend/actions/objects?object_type=inventory_item&space_id={S}", None),
    ("GET", f"/v1/amend/actions/decisions?space_id={S}", None),
    ("GET", f"/v1/amend/actions/decisions/{U}?space_id={S}", None),
    ("POST", f"/v1/amend/actions/decisions/{U}/revert", b"[not json"),
]


def _client() -> TestClient:
    from dms_api.app import create_app
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("DMS_ACTIONS", raising=False)
    yield _client()


def _send(client: TestClient, method: str, path: str, body: bytes | None):  # type: ignore[no-untyped-def]
    headers = {"content-type": "application/json"} if body is not None else {}
    return client.request(method, path, content=body, headers=headers)


def test_flag_off_404_byte_identical(flag_off: TestClient) -> None:
    baseline = _send(flag_off, "POST", "/v1/amend/no-such-route", BAD_JSON)
    assert baseline.status_code == 404
    assert baseline.content == b'{"detail":"Not Found"}'

    assert len(ACTION_ROUTES) == 9
    for method, path, body in ACTION_ROUTES:
        resp = _send(flag_off, method, path, body)
        assert resp.status_code == 404, f"{method} {path} answered {resp.status_code}"
        assert resp.content == baseline.content, f"{method} {path}: {resp.content!r}"
        assert resp.headers.get("content-type") == baseline.headers.get("content-type")


def test_flag_off_other_methods_are_not_405(flag_off: TestClient) -> None:
    """A path that exists only behind the flag must not leak as 405 Method Not Allowed."""
    baseline = _send(flag_off, "DELETE", "/v1/amend/no-such-route", None)
    for method, path, _body in ACTION_ROUTES:
        other = "DELETE" if method != "DELETE" else "PUT"
        resp = _send(flag_off, other, path, None)
        assert (resp.status_code, resp.content) == (baseline.status_code, baseline.content)


@pytest.mark.parametrize("value", ["true", "yes", " 0", "0", ""])
def test_only_the_digit_one_turns_it_on(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DMS_ACTIONS", value)
    resp = _send(_client(), "GET", "/v1/amend/actions/types", None)
    assert (resp.status_code, resp.content) == (404, b'{"detail":"Not Found"}')


def _openapi_sha(monkeypatch: pytest.MonkeyPatch, flag: str | None) -> tuple[str, list[str]]:
    if flag is None:
        monkeypatch.delenv("DMS_ACTIONS", raising=False)
    else:
        monkeypatch.setenv("DMS_ACTIONS", flag)
    from dms_api.app import create_app
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    app = create_app()
    doc = app.openapi()
    served = TestClient(app).get("/openapi.json").json()
    assert served == doc
    text = json.dumps(doc, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest(), list(doc["paths"])


def test_openapi_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    off_sha, off_paths = _openapi_sha(monkeypatch, None)
    on_sha, on_paths = _openapi_sha(monkeypatch, "1")
    assert not [p for p in off_paths if "/actions" in p]
    assert not [p for p in on_paths if "/actions" in p]
    assert on_sha == off_sha


def test_mcp_tools_hold_no_action_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in (None, "1"):
        if flag is None:
            monkeypatch.delenv("DMS_ACTIONS", raising=False)
        else:
            monkeypatch.setenv("DMS_ACTIONS", flag)
        from dms_api.routes.mcp import TOOLS

        names = [str(t.get("name", "")) for t in TOOLS]
        assert names and not [n for n in names if "action" in n.lower()], names


def test_flag_on_the_same_path_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard the guard: the 404s above mean 'off', not 'never mounted'."""
    calls: list[str] = []

    def allow(*, action: str, **_kw: object) -> ComplianceDecision:
        calls.append(action)
        return ComplianceDecision(allowed=True, reason="ok", action=action)

    monkeypatch.setenv("DMS_ACTIONS", "1")
    monkeypatch.setattr("dms_api.routes.actions.compliance_gate", allow)
    client = _client()
    resp = client.get("/v1/amend/actions/types")
    assert resp.status_code == 200, resp.text
    types = resp.json()
    assert [t["name"] for t in types] == ["inventory.set_reorder_level"]
    assert types[0]["field"] == "reorder_level_kg"
    assert types[0]["key_fields"] == ["sku", "location_id"]
    assert calls == ["action.list"]

    malformed = _send(client, "POST", "/v1/amend/actions/proposals", BAD_JSON)
    assert malformed.status_code == 422, "flag on, a malformed body is a validation error"

    wrong_key = client.post(
        "/v1/amend/actions/proposals",
        json={
            "space_id": S,
            "action_type": "inventory.set_reorder_level",
            "object_key": {"sku": "SKU-BETA", "location_id": "WH-B"},
            "value": "450",
            "actor": "someone-else",
        },
    )
    assert wrong_key.status_code == 422, "extra='forbid': a body cannot name an actor"
