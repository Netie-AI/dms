"""WIRE-19: DMS consumes the Cortex contract 1.9.0 badges ``document`` and ``reconfirm``.

Cortex 1.7.0 added ``Badge.document``: an answer cited from the Space's documents.
A client generated from 1.5.0 raises ValueError on it, so the customer got a 502
(or demo numbers with DMS_DEMO_FALLBACK on). Cortex also returns ``reconfirm``
with ``{why, closest_question}``; DMS dropped the object and showed an abstain
with no Yes. On Yes DMS re-asks with ``plan_sql_confirm=yes``; on No it sends
``plan_sql_confirm=no`` and the customer reads "not found in the database".

Every case goes through the real generated client (httpx.MockTransport behind
CortexClient) and asserts the customer envelope from POST /v1/chat/ask
(hard rule 10a).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_executor import Executor
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "contract" / "openapi-1.9.0.json"

_DOC_TEXT = "Damaged goods may be returned with the original receipt [returns_policy.pdf]."
_WHY = "No granted column records a return reason."
_CLOSEST = "How many returns were logged per location?"
_QUESTION = "why do customers return the blue widget"
_CONFIDENT = {"L0_CERTIFIED", "L1_GOVERNED_METRIC"}


def _document_answer(text: str = _DOC_TEXT) -> dict[str, Any]:
    """What Cortex main serves for a document answer (contract_routes._enrich_answer)."""
    return {
        "answer": text,
        "sql_used": None,
        "audit_id": "aud_doc_19",
        "answer_id": "aud_doc_19",
        "route": "rag",
        "rows": [],
        "provenance": {"layer": "rag", "badge": "document"},
        "contributing_sources": [
            {
                "ref_id": "returns_policy.pdf",
                "container": "documents",
                "member": "returns_policy.pdf",
                "kind": "document",
            }
        ],
        "drillthrough_token": None,
        "assumptions": [],
    }


def _reconfirm_answer() -> dict[str, Any]:
    return {
        "answer": f"{_WHY} Closest answerable question: {_CLOSEST} "
        "Yes runs that question. No answers 'not found in the database'.",
        "sql_used": None,
        "audit_id": "aud_rcf_19",
        "route": "reconfirm",
        "rows": [],
        "provenance": {"layer": "reconfirm", "badge": "reconfirm", "assumptions": _WHY},
        "contributing_sources": [],
        "drillthrough_token": None,
        "suggestions": [_CLOSEST],
        "reconfirm": {"why": _WHY, "closest_question": _CLOSEST},
    }


def _not_found_answer() -> dict[str, Any]:
    """Cortex confirm-no: an abstain, never the old session badge."""
    return {
        "answer": "not found in the database",
        "sql_used": None,
        "audit_id": "aud_no_19",
        "route": "abstain",
        "rows": [],
        "provenance": {"layer": "abstain", "badge": "abstain"},
        "contributing_sources": [],
        "drillthrough_token": None,
    }


class _WireCortex:
    """A Cortex that answers over HTTP, parsed by the real generated client."""

    def __init__(self, reply: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        from cortex_client.client import CortexClient

        self.bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.bodies.append(body)
            return httpx.Response(200, json=reply(body))

        self._real = CortexClient("http://cortex.test")
        self._real._client.set_httpx_client(
            httpx.Client(base_url="http://cortex.test", transport=httpx.MockTransport(handler))
        )

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run-wire19")

    def ask(self, req: AskRequest) -> AskResponse:
        return self._real.ask(req)


@pytest.fixture()
def minter() -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-30T00:00:00+00:00",
            expires_at="2026-10-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    key = MagicMock()
    key.kid = "test-kid"
    key.sign.return_value = "dGVzdA"
    return m


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from dms_api import settings as settings_mod
    from dms_executor.reconfirm import _reset_reconfirm

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    _reset_reconfirm()
    yield
    _reset_reconfirm()
    settings_mod.get_settings.cache_clear()


def _client(cortex: _WireCortex, minter: ManifestMinter) -> TestClient:
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    app.state.cortex = cortex
    return TestClient(app)


# ── contract pin and generated client ────────────────────────────────────────


def test_the_vendored_190_spec_is_pinned_and_declares_the_new_fields() -> None:
    digest = SPEC.with_name(SPEC.name + ".sha256")
    assert SPEC.is_file() and digest.is_file()
    assert hashlib.sha256(SPEC.read_bytes()).hexdigest() == digest.read_text().split()[0]
    schemas = json.loads(SPEC.read_text(encoding="utf-8"))["components"]["schemas"]
    assert "document" in schemas["Badge"]["enum"]
    assert "schema_context" in schemas["AskRequest"]["properties"]
    assert "masked" in schemas["AskPayloadColumn"]["properties"]


def test_the_generated_client_parses_the_document_badge() -> None:
    from cortex_client.generated.models import AskPayloadColumn, Badge, Provenance
    from cortex_client.generated.models import AskRequest as GenAskRequest

    assert Provenance.from_dict({"layer": "rag", "badge": "document"}).badge is Badge.DOCUMENT
    body = GenAskRequest.from_dict({"question": "q?", "schema_context": "TABLE t (a INT)"})
    assert body.to_dict()["schema_context"] == "TABLE t (a INT)"
    assert AskPayloadColumn.from_dict({"name": "c", "masked": False}).to_dict()["masked"] is False


# ── document ─────────────────────────────────────────────────────────────────


def test_a_document_answer_on_chat_ask_keeps_its_cards_and_is_never_green(
    minter: ManifestMinter,
) -> None:
    cortex = _WireCortex(lambda _b: _document_answer())
    resp = _client(cortex, minter).post(
        "/v1/chat/ask", json={"question": _QUESTION, "session_id": "ses_doc19"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_envelope_valid(body)
    assert len(cortex.bodies) == 1
    assert body["text"] == _DOC_TEXT
    assert body["rows"] == []
    assert body["abstained"] is False
    assert body["badge"] == "L2_VALIDATED"
    assert body["badge"] not in _CONFIDENT
    assert body["route"] == "document"
    assert body["sql_used"] == "-- document retrieval (no SQL)"
    cards = body["contributing_sources"]
    assert [c["container"] for c in cards] == ["returns_policy.pdf"]
    assert cards[0]["kind"] == "pdf"
    # No SQL ran, so nothing can be drilled into; DMS does not invent a token.
    assert body["drillthrough_token"] is None
    assert body["audit_id"] == "aud_doc_19"
    assert body.get("reconfirm") is None


def test_a_document_answer_with_an_uncited_figure_abstains(minter: ManifestMinter) -> None:
    text = "Refunds this year came to RM 48,210.55 according to the policy."
    cortex = _WireCortex(lambda _b: _document_answer(text))
    resp = _client(cortex, minter).post(
        "/v1/chat/ask", json={"question": _QUESTION, "session_id": "ses_doc19b"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert_envelope_valid(body)
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["values"] == []
    assert body["contributing_sources"] == []
    assert body["drillthrough_token"] in (None, "")
    assert "48,210.55" not in body["text"]
    assert body["audit_id"]


def test_e7_still_needs_a_token_for_a_table_card_on_a_document_route() -> None:
    from dms_executor.envelope import build_answer_envelope

    env = build_answer_envelope(
        answer_id="ans_e7",
        text="From the notes.",
        badge="L2_VALIDATED",
        abstained=False,
        route="document",
        contributing_sources=[
            {"ref_id": "inventory", "container": "warehouse", "kind": "sql"},
        ],
        ask_mode="live",
    )
    with pytest.raises(AssertionError, match="E7"):
        assert_envelope_valid(env)


def test_e7_still_needs_a_token_for_a_document_card_off_the_document_route() -> None:
    from dms_executor.envelope import build_answer_envelope

    env = build_answer_envelope(
        answer_id="ans_e7b",
        text="From the notes.",
        badge="L2_VALIDATED",
        abstained=False,
        route="doc_rag",
        contributing_sources=[{"ref_id": "a.pdf", "container": "a.pdf", "kind": "pdf"}],
        ask_mode="live",
    )
    with pytest.raises(AssertionError, match="E7"):
        assert_envelope_valid(env)


# ── reconfirm ────────────────────────────────────────────────────────────────


def _reconfirm_flow(body: dict[str, Any]) -> dict[str, Any]:
    confirm = body.get("plan_sql_confirm")
    if confirm == "no":
        return _not_found_answer()
    if confirm == "yes":
        return _document_answer()
    return _reconfirm_answer()


def _offer(cortex: _WireCortex, minter: ManifestMinter) -> tuple[TestClient, dict[str, Any]]:
    client = _client(cortex, minter)
    resp = client.post("/v1/chat/ask", json={"question": _QUESTION, "session_id": "ses_rcf19"})
    assert resp.status_code == 200, resp.text
    return client, resp.json()


def test_a_cortex_reconfirm_shows_why_and_the_closest_question(minter: ManifestMinter) -> None:
    cortex = _WireCortex(_reconfirm_flow)
    _, body = _offer(cortex, minter)
    assert_envelope_valid(body)
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["values"] == []
    assert body["rows"] == []
    assert body["contributing_sources"] == []
    assert body["drillthrough_token"] in (None, "")
    assert body["audit_id"]
    assert _WHY in body["text"] and _CLOSEST in body["text"]
    offer = body["reconfirm"]
    assert offer["suggested_question"] == _CLOSEST
    assert offer["reason_text"] == _WHY
    assert offer["token"]


def test_yes_on_a_cortex_reconfirm_reasks_with_plan_sql_confirm_yes(
    minter: ManifestMinter,
) -> None:
    cortex = _WireCortex(_reconfirm_flow)
    client, offered = _offer(cortex, minter)
    resp = client.post(
        "/v1/chat/ask/confirm", json={"token": offered["reconfirm"]["token"], "yes": True}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    yes = [b for b in cortex.bodies if b.get("plan_sql_confirm") == "yes"]
    assert len(yes) == 1 and yes[0]["question"] == _CLOSEST
    assert_envelope_valid(body)
    assert body["text"] == _DOC_TEXT
    assert body["abstained"] is False
    assert body["badge"] not in _CONFIDENT
    assert body["audit_id"] == "aud_doc_19"


def test_no_on_a_cortex_reconfirm_sends_no_and_shows_not_found(
    minter: ManifestMinter,
) -> None:
    cortex = _WireCortex(_reconfirm_flow)
    client, offered = _offer(cortex, minter)
    resp = client.post(
        "/v1/chat/ask/confirm", json={"token": offered["reconfirm"]["token"], "yes": False}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    no = [b for b in cortex.bodies if b.get("plan_sql_confirm") == "no"]
    assert len(no) == 1 and no[0]["question"] == _QUESTION
    assert_envelope_valid(body)
    assert body["text"] == "not found in the database"
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["values"] == []
    assert body["rows"] == []
    assert body["contributing_sources"] == []
    assert body["drillthrough_token"] in (None, "")
    assert body["audit_id"] == "aud_no_19"
    assert body.get("reconfirm") is None


def test_no_never_serves_even_if_the_engine_answers(minter: ManifestMinter) -> None:
    def reply(body: dict[str, Any]) -> dict[str, Any]:
        if body.get("plan_sql_confirm") == "no":
            return _document_answer()
        return _reconfirm_answer()

    cortex = _WireCortex(reply)
    client, offered = _offer(cortex, minter)
    body = client.post(
        "/v1/chat/ask/confirm", json={"token": offered["reconfirm"]["token"], "yes": False}
    ).json()
    assert_envelope_valid(body)
    assert body["text"] == "not found in the database"
    assert body["abstained"] is True
    assert body["badge"] == "ABSTAIN"
    assert body["contributing_sources"] == []
