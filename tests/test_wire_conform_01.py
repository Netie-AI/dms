"""WIRE-CONFORM-01 (#276, proposed F119). Insights POST bodies stay inside contract 1.9.0.

Cortex rejects an undeclared body field with 422 ``unknown_request_fields``.
DMS sent ``max_tokens`` on the reconfirm suggestion POST, so every suggestion
request was refused and the customer saw an empty ``suggested_question``.
Same class as #442 (``model`` / ``strict``).

The field sets are read from the vendored ``contract/openapi-1.9.0.json``, not
typed here, so a new body field fails this file until the contract declares it.

The fake Cortex is a real HTTP server on 127.0.0.1 with an ephemeral port. It
answers like Cortex @bcdbb45 ``insights/routes.py``: an undeclared field is a 422.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from cortex_client.client import CortexClient
from cortex_client.compute import begin_answer_model_calls
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contract" / "openapi-1.9.0.json"
_KEY = "ov_test_wire_conform"
_SUGGESTION = "How many SKUs are in inventory?"
_QUESTION = "how many skus"
_OPS_SPACE = "dddddddd-dddd-dddd-dddd-dddddddddddd"


def _insights_op() -> tuple[dict[str, Any], dict[str, Any]]:
    doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return doc, doc["paths"]["/v1/insights"]["post"]


def _ask_in_fields() -> frozenset[str]:
    doc, _op = _insights_op()
    return frozenset(doc["components"]["schemas"]["InsightsAskIn"]["properties"])


def _declared_fields() -> frozenset[str]:
    """The request body schema: InsightsAskIn plus the schema_context model."""
    doc, op = _insights_op()
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    out: set[str] = set()
    for part in schema["allOf"]:
        name = str(part["$ref"]).rsplit("/", 1)[-1]
        out |= set(doc["components"]["schemas"][name]["properties"])
    return frozenset(out)


def _wire_fields() -> frozenset[str]:
    """What Cortex @bcdbb45 accepts: the body schema plus InsightsWireIn's extension."""
    _doc, op = _insights_op()
    return _declared_fields() | frozenset(op["x-cortex-request-extension"]["fields"])


def test_contract_field_sets_are_the_ones_this_file_reads() -> None:
    """Pins the reading. A contract bump that moves these fields shows up here first."""
    assert _ask_in_fields() == {
        "ask",
        "consumer",
        "generate",
        "intent",
        "question",
        "session_id",
        "space_id",
    }
    assert _declared_fields() == _ask_in_fields() | {"schema_context"}
    assert "max_tokens" not in _wire_fields()
    _doc, op = _insights_op()
    assert "unknown_request_fields" in op["x-cortex-request-extension"]["unknown_fields"]


class _FakeCortex:
    """Loopback Cortex. POST /v1/insights 422s any field outside ``allowed``."""

    def __init__(self, allowed: frozenset[str]) -> None:
        self.allowed = allowed
        self.posts: list[dict[str, Any]] = []
        self.statuses: list[int] = []
        self.details: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def __enter__(self) -> _FakeCortex:
        self._thread.start()
        return self

    def __exit__(self, *_a: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _insights(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.posts.append(body)
        extras = sorted(set(body) - self.allowed)
        if extras:
            detail = "not modelled by POST /v1/insights: " + ", ".join(extras)
            self.statuses.append(422)
            self.details.append(detail)
            return 422, {
                "ok": False,
                "status": "ABSTAIN",
                "phase": "request",
                "badge": "abstain",
                "refuse_reason": "unknown_request_fields",
                "detail": detail,
                "answer": f"Abstained (unknown_request_fields): {detail}.",
                "values": [],
            }
        self.statuses.append(200)
        return 200, {
            "ok": True,
            "status": "ABSTAIN",
            "phase": "generate",
            "answer": _SUGGESTION,
            "values": [],
            "model_called": True,
        }

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                return None

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:  # noqa: N802 -- http.server API
                size = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(size) if size else b"{}"
                body = json.loads(raw.decode("utf-8") or "{}")
                path = self.path.split("?", 1)[0].rstrip("/")
                if path == "/v1/insights":
                    self._send(*fake._insights(body if isinstance(body, dict) else {}))
                elif path == "/dms/tasks/gate/check":
                    self._send(200, {"status": "ok", "executable": True})
                else:
                    self._send(404, {"detail": "not found"})

            def do_GET(self) -> None:  # noqa: N802 -- http.server API
                path = self.path.split("?", 1)[0].rstrip("/")
                if path == "/v1/insights/ontology":
                    self._send(200, {"ok": True, "phase": "ontology", "ontology": {}})
                else:
                    self._send(404, {"detail": "not found"})

        return _Handler


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No live stack: no OpenVault probe, no database, no strict pin env."""
    monkeypatch.setattr("dms_executor.probe_openvault", lambda **_k: (None, "off"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in ("DMS_SCHEMA_CONTEXT", "DMS_STRICT_MODEL", "DMS_STRICT_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    begin_answer_model_calls()
    yield
    begin_answer_model_calls()


# --- acceptance 1: POST /v1/chat/ask -----------------------------------------


def _reconfirm_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: _FakeCortex
) -> dict[str, Any]:
    """A validate failure that is not ungranted, so a reconfirm is offered.

    The answer ladder is planted (no Insights call). The suggestion is the real
    ``CortexClient.complete`` against the loopback fake.
    """
    from cortex_contract.execution import Manifest
    from dms_api.app import create_app
    from dms_api.settings import Settings, get_settings
    from dms_executor import Executor
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.manifest import ManifestMinter, SessionAcl
    from dms_executor.reconfirm import _reset_reconfirm

    _reset_reconfirm()
    monkeypatch.setenv("DMS_PIPELINE_FAILURE_JSONL", str(tmp_path / "failures.jsonl"))

    class _Product(CortexClient):
        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any] | None:
            del question
            return {"query_sql": "SELECT no_such_column FROM inventory"}

        def ask(self, _req: Any) -> Any:
            raise AssertionError("contract ask")

        def submit(self, _req: Any) -> Any:
            raise AssertionError("a SQL that failed validate was submitted")

        def ledger_append(self, _req: Any) -> Any:
            raise AssertionError("an abstain appended a ledger row")

    db = ensure_demo_warehouse(tmp_path / "warehouse.duckdb")
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
            issued_at="2026-10-31T00:00:00+00:00",
            expires_at="2026-10-31T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    product = _Product(fake.url, api_key=_KEY, timeout=5.0)
    route_cortex = CortexClient(fake.url, api_key=_KEY, timeout=5.0)
    try:
        app = create_app()
        app.state.ask_service = Executor(cortex=product, minter=minter, warehouse_path=db)
        app.state.cortex = route_cortex
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,  # type: ignore[call-arg]
            dms_ask_mode="live",
            dms_demo_fallback=False,
        )
        response = TestClient(app).post(
            "/v1/chat/ask",
            json={
                "question": "what is the mauve widget total",
                "session_id": "ses_wire_conform",
                "space_id": _OPS_SPACE,
            },
        )
    finally:
        product.close()
        route_cortex.close()
    assert response.status_code == 200, response.text
    body = response.json()
    assert_envelope_valid(body)
    return dict(body)


def test_reconfirm_suggestion_reaches_the_customer_without_a_422(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Red before the fix: the suggestion POST carried max_tokens, Cortex 422'd it,
    and the offer went out with ``suggested_question == ""``."""
    with _FakeCortex(_declared_fields()) as fake:
        body = _reconfirm_ask(monkeypatch, tmp_path, fake)

    assert 422 not in fake.statuses, fake.details
    assert fake.statuses == [200]
    assert len(fake.posts) == 1
    sent = fake.posts[0]
    assert set(sent) <= _declared_fields(), sorted(set(sent) - _declared_fields())
    assert "max_tokens" not in sent
    assert sent["generate"] is True and sent["ask"] is False

    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["rows"] == []
    assert body["values"] == []
    assert "GEN-01: validate:explain:BinderException" in body["assumptions"]
    offer = body["reconfirm"]
    assert offer["reason_code"] == "validate:explain:BinderException"
    assert offer["token"]
    assert offer["suggested_question"] == _SUGGESTION


# --- acceptance 2: every Insights POST body DMS builds ------------------------


def _send_ask(client: CortexClient) -> None:
    client.insights_ask(intent=_QUESTION, question=_QUESTION, ask=True)


def _send_generate(client: CortexClient) -> None:
    client.insights_ask(intent=_QUESTION, question=_QUESTION, ask=False, generate=True)


def _send_generate_schema_context(client: CortexClient) -> None:
    client.insights_ask(
        intent=_QUESTION,
        question=_QUESTION,
        ask=False,
        generate=True,
        schema_context="tables: inventory(sku)",
    )


def _send_suggestion(client: CortexClient) -> None:
    assert client.complete("closest answerable question") == _SUGGESTION


def _send_suggestion_via_reconfirm(client: CortexClient) -> None:
    """The route's call: reconfirm.complete_with_model, not complete() directly."""
    from dms_executor.reconfirm import complete_with_model

    assert complete_with_model(client, "closest answerable question") == _SUGGESTION


_INSIGHTS_POST_PATHS: dict[str, Callable[[CortexClient], None]] = {
    "ask": _send_ask,
    "generate": _send_generate,
    "generate_schema_context": _send_generate_schema_context,
    "suggestion_complete": _send_suggestion,
    "suggestion_via_reconfirm": _send_suggestion_via_reconfirm,
}


@pytest.mark.parametrize("path", sorted(_INSIGHTS_POST_PATHS))
def test_insights_post_bodies_are_insights_ask_in_plus_schema_context(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``insights_post`` bodies: InsightsAskIn plus schema_context, nothing else."""
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    with _FakeCortex(_wire_fields()) as fake:
        client = CortexClient(fake.url, api_key=_KEY, timeout=5.0)
        try:
            _INSIGHTS_POST_PATHS[path](client)
        finally:
            client.close()
    assert fake.posts, f"{path} sent no Insights POST"
    for sent in fake.posts:
        extra = sorted(set(sent) - _declared_fields())
        assert extra == [], f"{path} sent fields contract 1.9.0 does not declare: {extra}"
    assert fake.statuses == [200] * len(fake.posts)
    if path == "generate_schema_context":
        assert fake.posts[0]["schema_context"] == "tables: inventory(sku)"


def _compute_lane(client: CortexClient, *, sql_feedback: dict[str, Any] | None = None) -> None:
    client.compute_insights(
        _QUESTION,
        session_id="ses_wire_conform",
        space_id="sp",
        ontology={"intent_slots": {"measure": "sku_count"}, "tables": ["inventory"]},
        schema_context="tables: inventory(sku)",
        sql_feedback=sql_feedback,
    )


def test_compute_insights_bodies_stay_inside_the_declared_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ask-lane generate body may also use the contract's request extension."""
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    with _FakeCortex(_wire_fields()) as fake:
        client = CortexClient(fake.url, api_key=_KEY, timeout=5.0)
        try:
            _compute_lane(client)
        finally:
            client.close()
    assert fake.posts, "compute_insights sent no Insights POST"
    for sent in fake.posts:
        assert sorted(set(sent) - _wire_fields()) == []
    assert 422 not in fake.statuses, fake.details


@pytest.mark.xfail(
    strict=True,
    reason=(
        "sql_feedback is not in contract 1.9.0 (InsightsAskIn or "
        "x-cortex-request-extension); Cortex @bcdbb45 would 422 the feedback "
        "retry. Found by this test, not routed yet, not fixed here."
    ),
)
def test_compute_insights_feedback_retry_stays_inside_the_declared_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    with _FakeCortex(_wire_fields()) as fake:
        client = CortexClient(fake.url, api_key=_KEY, timeout=5.0)
        try:
            _compute_lane(client, sql_feedback={"previous_sql": "SELECT 1", "reason": "empty"})
        finally:
            client.close()
    assert fake.posts
    for sent in fake.posts:
        assert sorted(set(sent) - _wire_fields()) == []
