"""KEY-01 (dms#273): no default Cortex key, no fallback, named refusals.

The published demo viewer key used to be the DMS default (`settings.cortex_api_key`)
and the silent fallback in `cortex_read`, so a DMS API with no key configured still
reached Cortex under a key that is published in the Cortex repo. These tests pin the
opposite: no key means no outbound call and a NAMED refusal, on every keyed surface.

Seeded fake token only. Nothing here reads an env file, OpenVault or Secret Manager.
Does not stamp COMPLETE and does not clear the pilot security bar: Platform must show
live that an unkeyed DMS API refuses.

`DEMO_VIEWER_KEY` below is the retired default. It stays in this file ONLY so the
tests can assert that it is refused and absent. It is not a credential.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client import CortexClient
from cortex_client.compute import (
    COMPUTE_PATH,
    INSIGHTS_FAIL_BEARER_MISSING,
    INSIGHTS_PATH,
    compute_insights,
    compute_query,
    insights_fail_reason,
)
from dms_api.app import create_app, lifespan
from dms_api.cortex_read import cortex_get
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
_FAKE = "seeded-test-token-key01"
DEMO_VIEWER_KEY = "dms-demo-viewer-key"
_CORTEX = "http://127.0.0.1:8010"
_MISSING = (None, "", "   ")


# --------------------------------------------------------------------------- helpers


class _Resp:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True, "values": []}
        self.text = json.dumps(self._body)
        self.content = self.text.encode()

    def json(self) -> dict[str, Any]:
        return dict(self._body)


class _Wire:
    """Stands in for httpx.Client everywhere. Records every request, so `calls == []`
    proves nothing left the process, whichever module made the request."""

    def __init__(
        self,
        calls: list[dict[str, Any]],
        *,
        status_code: int = 200,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.calls = calls
        self.status_code = status_code
        self.body = body

    def __call__(self, *_a: Any, **_k: Any) -> _Wire:
        return self

    def __enter__(self) -> _Wire:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def close(self) -> None:
        return None

    def _record(self, method: str, url: Any, headers: Any, body: Any) -> _Resp:
        self.calls.append(
            {
                "method": method,
                "url": str(url),
                "headers": dict(headers or {}),
                "json": body,
            }
        )
        return _Resp(self.status_code, self.body)

    def request(
        self,
        method: str,
        url: str,
        headers: Any = None,
        json: Any = None,
        params: Any = None,
    ) -> _Resp:
        return self._record(method, url, headers, json if json is not None else params)

    def post(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
        return self._record("POST", url, headers, json)

    def get(self, url: str, params: Any = None, headers: Any = None) -> _Resp:
        return self._record("GET", url, headers, params)


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_code: int = 200,
    body: dict[str, Any] | None = None,
) -> _CortexOnly:
    """Record every request any module makes through httpx.Client. The return value is
    a view of only the requests addressed to Cortex.

    httpx.Client is one attribute on one module. cortex_read, insights, compute, gate
    and the generated contract client all resolve it there, so one patch covers them.
    App startup also probes OpenVault; that is not a Cortex call, so it is filtered out
    of the list this returns, which is what every `calls == []` below asserts on.
    """
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr("httpx.Client", _Wire(seen, status_code=status_code, body=body))
    return _CortexOnly(seen)


class _CortexOnly:
    """A live view of every recorded request, restricted to those addressed to Cortex."""

    def __init__(self, seen: list[dict[str, Any]]) -> None:
        self._seen = seen

    def _rows(self) -> list[dict[str, Any]]:
        return [c for c in self._seen if c["url"].startswith(_CORTEX)]

    def __iter__(self) -> Any:
        return iter(self._rows())

    def __eq__(self, other: object) -> bool:
        return self._rows() == other

    def __repr__(self) -> str:
        return repr(self._rows())

    @property
    def every_request(self) -> list[dict[str, Any]]:
        """All requests, whoever they were addressed to."""
        return list(self._seen)

    def clear(self) -> None:
        self._seen.clear()


def _generate_posts(calls: _CortexOnly) -> list[dict[str, Any]]:
    return [
        c
        for c in calls
        if c["url"].rstrip("/").endswith(INSIGHTS_PATH)
        and c["method"] == "POST"
        and (c["json"] or {}).get("generate") is True
    ]


@pytest.fixture
def no_key_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A process with no Cortex key configured, and no .env that could supply one."""
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


# ----------------------------------------------------------------- settings: no default


def test_settings_have_no_default_key_and_blank_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    assert Settings(_env_file=None).cortex_api_key is None
    for blank in ("", "   ", "\t"):
        monkeypatch.setenv("CORTEX_API_KEY", blank)
        assert Settings(_env_file=None).cortex_api_key is None, repr(blank)
    monkeypatch.setenv("CORTEX_API_KEY", f"  {_FAKE}  ")
    assert Settings(_env_file=None).cortex_api_key == _FAKE


def test_live_start_with_no_key_warns_by_name_and_hands_the_client_no_key(
    no_key_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    no_key_env.setattr(
        "dms_api.app.build_ask_service", lambda _c: SimpleNamespace(close=lambda: None)
    )
    no_key_env.setenv("DMS_ASK_MODE", "live")
    get_settings.cache_clear()
    caplog.set_level(logging.WARNING)

    async def _boot() -> str | None:
        app = create_app()
        async with lifespan(app):
            return app.state.cortex.api_key

    assert asyncio.run(_boot()) is None
    assert "CORTEX_API_KEY is not set" in caplog.text
    assert "cortex_key_missing" in caplog.text
    assert DEMO_VIEWER_KEY not in caplog.text

    caplog.clear()
    no_key_env.setenv("CORTEX_API_KEY", _FAKE)
    get_settings.cache_clear()
    assert asyncio.run(_boot()) == _FAKE
    assert "CORTEX_API_KEY is not set" not in caplog.text
    assert _FAKE not in caplog.text


# ------------------------------------------------------------------- cortex_read refuses


@pytest.mark.parametrize("key", _MISSING)
def test_cortex_get_without_a_key_makes_no_call_and_names_the_refusal(
    key: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _wire(monkeypatch)
    out = cortex_get(_CORTEX, "/dms/ontology", api_key=key)
    assert out["ok"] is False
    assert out["error"] == "cortex_key_missing"
    assert "CORTEX_API_KEY" in out["hint"]
    assert out["data"] is None
    assert calls == [], f"keyless read leaked a request: {calls}"
    assert DEMO_VIEWER_KEY not in json.dumps(out)

    # Control, inside the same test: a configured key still reads, and it is the
    # configured key that travels, nothing else.
    ok = cortex_get(_CORTEX, "/dms/ontology", api_key=_FAKE)
    assert ok["ok"] is True
    assert [c["headers"].get("X-API-Key") for c in calls] == [_FAKE]


def test_the_default_viewer_key_symbol_is_gone() -> None:
    import dms_api.cortex_read as cortex_read

    assert not hasattr(cortex_read, "DEFAULT_VIEWER_KEY")
    assert "DEFAULT_VIEWER_KEY" not in cortex_read.__all__
    import cortex_client.insights as insights

    assert not hasattr(insights, "DEMO_VIEWER_KEY")


# ------------------------------------------------- routes, real settings, no key set


def test_ontology_and_trust_routes_refuse_by_name_with_zero_outbound_calls(
    no_key_env: pytest.MonkeyPatch,
) -> None:
    calls = _wire(no_key_env)
    client = TestClient(create_app())
    for path in (
        "/v1/ontology",
        "/v1/ontology/metrics",
        "/v1/trust/summary",
        "/v1/trust/runs/corpus",
    ):
        body = client.get(path).json()
        assert body["ok"] is False, path
        assert body["error"] == "cortex_key_missing", (path, body)
        assert "CORTEX_API_KEY" in body["hint"], path
        assert DEMO_VIEWER_KEY not in json.dumps(body), path
    summary = client.get("/v1/trust/summary").json()
    assert summary["claim"]["supported"] is False
    assert calls == [], f"unkeyed ontology/trust routes leaked: {calls}"

    # Control: with a key configured the same route does call Cortex with that key.
    no_key_env.setenv("CORTEX_API_KEY", _FAKE)
    get_settings.cache_clear()
    assert TestClient(create_app()).get("/v1/ontology").json()["ok"] is True
    assert [c["headers"].get("X-API-Key") for c in calls] == [_FAKE]


def test_insights_routes_refuse_by_name_before_the_gate_or_any_call(
    no_key_env: pytest.MonkeyPatch,
) -> None:
    calls = _wire(no_key_env)

    def _gate_must_not_run(**_k: Any) -> Any:
        raise AssertionError("F5 gate called for an unkeyed insights request")

    no_key_env.setattr("dms_api.routes.insights.compliance_gate", _gate_must_not_run)
    app = create_app()
    app.state.cortex = CortexClient(_CORTEX, api_key=None)
    client = TestClient(app)
    responses = [
        client.get("/v1/insights"),
        client.get("/v1/insights/keys"),
        client.get("/v1/insights/identity"),
        client.get("/v1/insights/ontology", params={"q": "how many skus"}),
        client.post("/v1/insights", json={"intent": "how many skus"}),
        client.post("/v1/insights", json={"intent": "how many skus", "generate": True}),
    ]
    for res in responses:
        assert res.status_code == 503, (res.request.url, res.text)
        body = res.json()
        assert body["status"] == "REFUSE"
        assert body["code"] == "cortex_key_missing"
        assert body["values"] == []
        assert body["live_5000_ci"] is False
        assert DEMO_VIEWER_KEY not in res.text
    assert calls == [], f"unkeyed insights routes leaked: {calls}"

    # Control: a keyed client still gets through to Cortex on the same route.
    app.state.cortex = CortexClient(_CORTEX, api_key=_FAKE)
    no_key_env.setattr(
        "dms_api.routes.insights.compliance_gate",
        lambda **k: SimpleNamespace(allowed=True, reason="ok", action=k.get("action")),
    )
    assert client.get("/v1/insights").status_code == 200
    assert [c["headers"].get("X-API-Key") for c in calls] == [_FAKE]


# ----------------------------- ask lane: None key abstains by name, zero calls (both calls)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda key: compute_insights(_CORTEX, question="how many skus?", api_key=key),
            id="compute_insights",
        ),
        pytest.param(
            lambda key: compute_query(
                _CORTEX, question="how many skus?", api_key=key, dms_query=False
            ),
            id="compute_query_ask_lane",
        ),
        pytest.param(
            lambda key: CortexClient(_CORTEX, api_key=key).compute_insights("how many skus?"),
            id="CortexClient.compute_insights",
        ),
        pytest.param(
            lambda key: CortexClient(_CORTEX, api_key=key).compute_query(
                "how many skus?", dms_query=False
            ),
            id="CortexClient.compute_query_ask_lane",
        ),
    ],
)
def test_ask_lane_with_no_key_abstains_by_name_and_makes_no_http_call(
    call: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _wire(monkeypatch)
    for key in _MISSING:
        out = call(key)
        assert out is not None, repr(key)
        assert insights_fail_reason(out) == INSIGHTS_FAIL_BEARER_MISSING, repr(key)
        assert out["values"] == []
        assert calls == [], f"key={key!r} sent {calls}"

    # Control, same test: the same call with a seeded fake token does go out, once as
    # generate, and carries that token.
    out = call(_FAKE)
    assert out is not None
    gens = _generate_posts(calls)
    assert gens, calls
    assert gens[0]["headers"]["Authorization"] == f"Bearer {_FAKE}"


def test_product_ask_with_no_key_is_a_named_abstain_envelope_and_no_http(
    no_key_env: pytest.MonkeyPatch,
) -> None:
    """Rule 10a: assert the customer envelope, not the intermediate client payload."""
    calls = _wire(no_key_env)
    minter = ManifestMinter()
    from cortex_contract.execution import Manifest

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-09-25T00:00:00+00:00",
            expires_at="2026-09-25T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    no_key_env.setattr(minter, "mint_manifest", _mint)
    no_key_env.setattr(minter, "fetch_intermediate", lambda: None)
    no_key_env.setattr(minter, "close", lambda: None)
    no_key_env.setattr(minter, "invalidate", lambda *_a, **_k: None)

    exe = Executor(cortex=CortexClient(_CORTEX, api_key=None), minter=minter)
    env = exe.live_ask(
        "How many florbs did wibble sell last week?",
        session_id="ses_key01",
        ask_path="generative",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["values"] == []
    assert env["rows"] == []
    text = str(env.get("text") or "")
    assert INSIGHTS_FAIL_BEARER_MISSING in text
    assert "cannot certify" in text.lower()
    assert DEMO_VIEWER_KEY not in json.dumps(env, default=str)
    assert calls == [], f"unkeyed product ask leaked: {calls}"


def test_post_chat_ask_default_lane_with_no_key_is_a_named_abstain_envelope(
    no_key_env: pytest.MonkeyPatch,
) -> None:
    """Rule 10a: the envelope from POST /v1/chat/ask, the product route and the DEFAULT
    ask_path (no harness lane), live mode, no Cortex key.

    Offline: OpenVault is never probed. The ask service is built without startup() and
    with a stub minter, the F5 gate is allowed by a stub, and httpx.Client is replaced
    so any request any module makes is recorded rather than sent.
    """
    from cortex_contract.execution import Manifest

    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-09-25T00:00:00+00:00",
            expires_at="2026-09-25T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    no_key_env.setattr(minter, "mint_manifest", _mint)
    no_key_env.setattr(minter, "fetch_intermediate", lambda: None)
    no_key_env.setattr(minter, "close", lambda: None)
    no_key_env.setattr(minter, "invalidate", lambda *_a, **_k: None)
    no_key_env.setattr(
        "dms_api.app.build_ask_service", lambda _c, **_k: SimpleNamespace(close=lambda: None)
    )
    no_key_env.setenv("DMS_ASK_MODE", "live")
    no_key_env.setenv("DMS_DEMO_FALLBACK", "0")
    get_settings.cache_clear()
    no_key_env.setattr(
        "dms_api.routes.chat.compliance_gate",
        lambda **k: SimpleNamespace(allowed=True, reason="test_allow", action=k.get("action")),
    )

    app = create_app()
    cortex = CortexClient(_CORTEX, api_key=None)
    app.state.cortex = cortex
    app.state.ask_service = Executor(cortex=cortex, minter=minter)
    calls = _wire(no_key_env)

    res = TestClient(app).post(
        "/v1/chat/ask", json={"question": "How many florbs did wibble sell last week?"}
    )
    assert res.status_code == 200, res.text
    env = res.json()
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["values"] == []
    assert env["rows"] == []
    text = str(env.get("text") or env.get("answer") or "")
    assert INSIGHTS_FAIL_BEARER_MISSING in text, text
    assert DEMO_VIEWER_KEY not in res.text
    # Nothing reached /v1/insights, nothing reached Cortex at all, and no request of any
    # kind carried an auth header.
    assert [c for c in calls.every_request if INSIGHTS_PATH in c["url"]] == []
    assert calls == []
    assert calls.every_request == [], f"unkeyed POST /v1/chat/ask sent {calls.every_request}"
    for c in calls.every_request:
        assert not {h.lower() for h in c["headers"]} & {"authorization", "x-api-key"}


# ------------------------------ the dms_query=True path: locked, has no product caller

_TARGETS = frozenset({"compute_query", "post_compute_query"})

#: Every reference to compute_query that apps/ and packages/ may hold, as
#: (file, scope, kind, name, dms_query as written). A reference is a call, a bare name
#: (partial, callback, alias), an attribute, an import or a name string. Anything else
#: is a way to reach the dms_query=True path and fails the lock.
_ALLOWED_COMPUTE_QUERY_REFS = {
    # compute_insights is the ask lane. It pins dms_query=False.
    (
        "packages/cortex_client/cortex_client/compute.py",
        "compute_insights",
        "name",
        "compute_query",
        "False",
    ),
    # The public method default is True. Nothing in apps/ or packages/ calls it.
    (
        "packages/cortex_client/cortex_client/client.py",
        "<module>",
        "import",
        "compute_query",
        "<not a call>",
    ),
    (
        "packages/cortex_client/cortex_client/client.py",
        "CortexClient.compute_query",
        "name",
        "compute_query",
        "dms_query",
    ),
}


def _source_files(*roots: str) -> list[Path]:
    skip = {"node_modules", "dist", "__pycache__", ".venv", "generated"}
    found: list[Path] = []
    for root in roots:
        for base, dirs, names in os.walk(_ROOT / root):
            dirs[:] = [d for d in dirs if d not in skip]
            found.extend(Path(base) / n for n in names if n.endswith(".py"))
    return sorted(found)


def _compute_query_refs(source: str, rel: str) -> set[tuple[str, str, str, str, str]]:
    """Every reference to compute_query in one module, from its source text.

    Walks every node, so it sees sync and async functions, methods, class bodies and
    module scope, and a reference that is not a call (functools.partial, a callback, a
    stored bound method). An `import ... as alias` is resolved, so the alias is reported
    under the real name.
    """
    tree = ast.parse(source, filename=rel)
    alias_of: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                leaf = a.name.rsplit(".", 1)[-1]
                if a.asname and leaf in _TARGETS:
                    alias_of[a.asname] = leaf
    exported = {
        id(elt)
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets)
        for elt in ast.walk(n.value)
    }
    called_with: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            kw = {k.arg: ast.unparse(k.value) for k in node.keywords if k.arg}
            called_with[id(node.func)] = kw.get("dms_query", "<default True>")

    found: set[tuple[str, str, str, str, str]] = set()

    def walk(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for a in child.names:
                    leaf = a.name.rsplit(".", 1)[-1]
                    if leaf in _TARGETS:
                        found.add((rel, scope, "import", leaf, "<not a call>"))
            kind = name = ""
            if isinstance(child, ast.Name) and (child.id in _TARGETS or child.id in alias_of):
                kind, name = "name", alias_of.get(child.id, child.id)
            elif isinstance(child, ast.Attribute) and child.attr in _TARGETS:
                kind, name = "attr", child.attr
            elif (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and child.value in _TARGETS
                and id(child) not in exported
            ):
                kind, name = "string", child.value
            if kind:
                found.add((rel, scope, kind, name, called_with.get(id(child), "<not a call>")))
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child.name if scope == "<module>" else f"{scope}.{child.name}"
            walk(child, inner)

    walk(tree, "<module>")
    return found


def _compute_query_refs_in_tree() -> set[tuple[str, str, str, str, str]]:
    found: set[tuple[str, str, str, str, str]] = set()
    for path in _source_files("apps", "packages"):
        rel = path.relative_to(_ROOT).as_posix()
        found |= _compute_query_refs(path.read_text(encoding="utf-8"), rel)
    return found


def _first_walker_hits(source: str) -> set[str]:
    """The walker this one replaces, kept only as the baseline the shapes below beat.

    It looked at Call nodes inside plain `def` bodies, by the called name. Of the
    shapes in `_PROBES` it caught only the sync def.
    """
    hits: set[str] = set()
    for fn in [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if called in _TARGETS:
                    hits.add(fn.name)
    return hits


_PROBE_REL = "apps/api/dms_api/_probe_tmp.py"

#: (source, did the first walker miss it, a kind the new walker must report)
_PROBES = [
    pytest.param(
        'def h(cortex):\n    return cortex.compute_query("q")\n',
        False,
        "attr",
        id="sync_def_attribute_call",
    ),
    pytest.param(
        'async def h(cortex):\n    return cortex.compute_query("q")\n',
        True,
        "attr",
        id="async_def_caller",
    ),
    pytest.param(
        "from cortex_client.compute import compute_query as cq\n\n\n"
        'def h():\n    return cq("http://x", question="q")\n',
        True,
        "name",
        id="aliased_import_call",
    ),
    pytest.param(
        "import functools\n"
        "from cortex_client.compute import compute_query\n\n"
        '_ASK = functools.partial(compute_query, "http://x", question="q")\n',
        True,
        "name",
        id="module_level_functools_partial",
    ),
    pytest.param(
        'from cortex_client import CortexClient\n\n_ASK = CortexClient("http://x").compute_query\n',
        True,
        "attr",
        id="module_level_bound_method_alias",
    ),
    pytest.param(
        'class H:\n    async def go(self, cortex):\n        return cortex.compute_query("q")\n',
        True,
        "attr",
        id="async_method_in_class",
    ),
    pytest.param(
        'def h(cortex):\n    return getattr(cortex, "compute_query")("q")\n',
        True,
        "string",
        id="name_string_lookup",
    ),
]


@pytest.mark.parametrize(("source", "first_walker_missed", "kind"), _PROBES)
def test_the_lock_flags_every_way_to_reach_compute_query(
    source: str, first_walker_missed: bool, kind: str
) -> None:
    refs = _compute_query_refs(source, _PROBE_REL)
    assert refs, "the lock missed this shape"
    assert refs.isdisjoint(_ALLOWED_COMPUTE_QUERY_REFS)
    assert kind in {r[2] for r in refs}, refs
    # The shape really was a miss for the walker this one replaces (the sync def was not).
    assert (_first_walker_hits(source) == set()) is first_walker_missed


def test_the_lock_does_not_flag_what_is_not_a_reference() -> None:
    quiet = (
        '"""Docs may say compute_query and post_compute_query."""\n'
        "from cortex_client.compute import compute_insights\n\n"
        '__all__ = ["compute_insights", "compute_query"]\n\n\n'
        'async def h(cortex):\n    return cortex.compute_insights("q")\n'
    )
    assert _compute_query_refs(quiet, _PROBE_REL) == set()


def test_dms_query_true_path_has_no_product_caller() -> None:
    """`compute_query` keeps its dms_query=True default for CONTRACT-FAKE-01, and on that
    path the bearer refusals do not apply. This pins that nothing in the product can
    reach it: the ask lane pins dms_query=False, and no other call, bare reference,
    attribute, alias import or string-named lookup of compute_query exists in apps/ or
    packages/, in sync or async code, in methods, class bodies or module scope. A new
    caller must either refuse there or change this test in review."""
    assert _compute_query_refs_in_tree() == _ALLOWED_COMPUTE_QUERY_REFS
    # The Executor reaches Cortex's planner only through compute_insights.
    exe_src = (_ROOT / "packages/executor/dms_executor/__init__.py").read_text(encoding="utf-8")
    assert 'getattr(cortex, "compute_insights", None)' in exe_src
    # And the leftover /dms/query URL is built in exactly one module.
    owners = [
        p.relative_to(_ROOT).as_posix()
        for p in _source_files("apps", "packages")
        if "COMPUTE_PATH}" in p.read_text(encoding="utf-8")
    ]
    assert owners == ["packages/cortex_client/cortex_client/compute.py"]


def test_product_ask_never_posts_dms_query_with_a_key_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _wire(
        monkeypatch,
        status_code=401,
        body={"ok": False, "status": "REFUSE", "refused": "needs its own OpenVault ov_ key"},
    )
    out = CortexClient(_CORTEX, api_key=_FAKE).compute_insights("how many skus?")
    assert out is not None
    assert _generate_posts(calls), calls
    assert all(not c["url"].endswith(COMPUTE_PATH) for c in calls), calls
    # And with no key the lane does not even attempt generate.
    calls.clear()
    CortexClient(_CORTEX, api_key=None).compute_insights("how many skus?")
    assert calls == []


# ---------------------------------------------- the retired key: recogniser, and absence


def test_demo_key_is_recognised_for_refusal_only_by_digest() -> None:
    from cortex_client import insights, strict_pin
    from cortex_client.insights import is_published_demo_key

    assert hashlib.sha256(DEMO_VIEWER_KEY.encode()).hexdigest() == insights._DEMO_VIEWER_KEY_SHA256
    assert is_published_demo_key(DEMO_VIEWER_KEY)
    assert is_published_demo_key(f"  {DEMO_VIEWER_KEY}\n")
    assert not is_published_demo_key(DEMO_VIEWER_KEY.upper())
    assert not is_published_demo_key(DEMO_VIEWER_KEY + "x")
    assert not is_published_demo_key(_FAKE)
    assert not is_published_demo_key("")
    assert not is_published_demo_key(None)
    assert insights.generate_bearer_refuse(DEMO_VIEWER_KEY, _CORTEX) == INSIGHTS_FAIL_BEARER_MISSING

    # strict_pin relays a configured bearer and never the demo key (same recogniser).
    saved = {k: os.environ.pop(k, None) for k in ("OPENVAULT_API_KEY", "CORTEX_API_KEY")}
    try:
        os.environ["CORTEX_API_KEY"] = DEMO_VIEWER_KEY
        assert strict_pin._forward_bearer() is None
        os.environ["OPENVAULT_API_KEY"] = _FAKE
        assert strict_pin._forward_bearer() == _FAKE
    finally:
        for k in ("OPENVAULT_API_KEY", "CORTEX_API_KEY"):
            os.environ.pop(k, None)
            if saved[k] is not None:
                os.environ[k] = saved[k]  # type: ignore[assignment]


def test_retired_demo_key_text_is_absent_from_apps_and_packages() -> None:
    """The acceptance grep, as a test: `git grep dms-demo-viewer-key -- apps packages`
    returns nothing. Behaviour is pinned by the tests above; this pins the text."""
    skip = {"node_modules", "dist", "__pycache__", ".venv"}
    binary = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pyc", ".zip"}
    hits: list[str] = []
    for root in ("apps", "packages"):
        for base, dirs, names in os.walk(_ROOT / root):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in names:
                path = Path(base) / name
                if path.suffix.lower() in binary:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if DEMO_VIEWER_KEY in text:
                    hits.append(path.relative_to(_ROOT).as_posix())
    assert hits == [], f"retired demo key text found: {hits}"
