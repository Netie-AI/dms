"""RAG-04 — DMS envelope contributing_sources mapping for doc-RAG hits.

Hard rule 10/10a: answer-path tests assert rendered text, rows, and customer
envelope properties (badge, abstained, values, sources, drillthrough_token,
audit_id) via assert_envelope_valid — including POST /v1/chat/ask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_executor import Executor, map_ask_response_to_envelope
from dms_executor.envelope import assert_envelope_valid, normalize_contributing_sources
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient


def test_normalize_flat_filename_strings_to_source_cards() -> None:
    sources = normalize_contributing_sources(
        ["Finance/notes/q3_walkthrough.pdf", "Warehouse/ops/forklift.txt"],
        space_id="sp_fin",
    )
    assert len(sources) == 2
    assert sources[0]["container"] == "q3_walkthrough.pdf"
    assert sources[0]["kind"] == "pdf"
    assert sources[0]["space_id"] == "sp_fin"
    assert sources[1]["kind"] == "csv"


def test_normalize_contribution_pct_and_snippet() -> None:
    sources = normalize_contributing_sources(
        [
            {
                "ref_id": "chunk_1",
                "filename": "bay3_leak.pdf",
                "contribution_pct": 60,
                "content": "Bay-3 leakage noted in inspection.",
                "chunk_index": 2,
            }
        ],
        space_id="sp_a",
    )
    assert sources[0]["contribution"] == 0.6
    assert sources[0]["snippet"] == "Bay-3 leakage noted in inspection."
    assert sources[0]["chunk_index"] == 2


def test_doc_rag_live_envelope_requires_drillthrough_and_sql_stub() -> None:
    answer = "Bay-3 leakage was flagged in the walkthrough notes."
    excerpt = "Bay-3 leakage"
    resp = AskResponse.model_validate(
        {
            "answer": answer,
            "audit_id": "aud_doc_rag",
            "route": "doc_rag",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "rows": [{"excerpt": excerpt}],
            "drillthrough_token": "dt_doc_rag_token",
            "contributing_sources": [
                {
                    "ref_id": "src_notes",
                    "filename": "notes_a.csv",
                    "contribution_pct": 100,
                    "content": "Bay-3 leakage for Space A only.",
                    "chunk_index": 0,
                }
            ],
        }
    )
    env = map_ask_response_to_envelope(resp, space_id="space_a", session_id="ses_rag")
    assert_envelope_valid(env)
    # Hard rule 10 — rendered answer + rows, not SQL alone.
    assert answer in env["text"]
    assert env["rows"] == [{"excerpt": excerpt}]
    # Hard rule 10a — customer envelope properties.
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["values"]
    assert env["contributing_sources"][0]["container"] == "notes_a.csv"
    assert env["contributing_sources"][0]["snippet"]
    assert env["contributing_sources"][0]["space_id"] == "space_a"
    assert env["drillthrough_token"] == "dt_doc_rag_token"
    assert env["audit_id"] == "aud_doc_rag"
    assert env["sql_used"] == "-- document retrieval (no SQL)"


def test_abstain_clears_contributing_sources() -> None:
    resp = AskResponse.model_validate(
        {
            "answer": "Cannot answer.",
            "audit_id": "aud_abstain",
            "route": "abstain",
            "provenance": {"badge": "abstain"},
            "contributing_sources": [{"ref_id": "should_drop"}],
            "drillthrough_token": "dt_should_drop",
        }
    )
    env = map_ask_response_to_envelope(resp)
    assert "Cannot answer." in env["text"]
    assert env["rows"] == []
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["values"] == []
    assert env["contributing_sources"] == []
    assert env["drillthrough_token"] in (None, "")
    assert env["audit_id"] == "aud_abstain"
    assert_envelope_valid(env)


def test_sources_stripped_without_drillthrough_token() -> None:
    resp = AskResponse.model_validate(
        {
            "answer": "From the notes.",
            "audit_id": "aud_no_dt",
            "route": "doc_rag",
            "provenance": {"badge": "query_skill"},
            "sql_used": "SELECT 1",
            "rows": [{"n": 1}],
            "contributing_sources": [
                {"ref_id": "doc_0", "filename": "notes.pdf", "contribution_pct": 100}
            ],
        }
    )
    env = map_ask_response_to_envelope(resp)
    assert "From the notes." in env["text"]
    assert env["rows"] == [{"n": 1}]
    assert env["contributing_sources"] == []
    assert_envelope_valid(env)


@dataclass
class _DocRagCortex:
    asks: list[AskRequest] = field(default_factory=list)
    answer: str = "Bay-3 leakage was flagged in the walkthrough notes."
    excerpt: str = "Bay-3 leakage"
    snippet: str = "Bay-3 leakage for Space A only."

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run-doc-rag")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer=self.answer,
            audit_id="aud_doc_rag_http",
            route="doc_rag",
            provenance={"badge": "query_skill", "layer": "L2"},
            rows=[{"excerpt": self.excerpt}],
            drillthrough_token="dt_doc_rag_http",
            contributing_sources=[
                {
                    "ref_id": "src_notes",
                    "filename": "notes_a.csv",
                    "contribution_pct": 100,
                    "content": self.snippet,
                    "chunk_index": 0,
                }
            ],
        )


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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    # Avoid OpenVault noise when Executor constructs without a prebuilt minter path.
    key = MagicMock()
    key.kid = "test-kid"
    key.sign.return_value = "dGVzdA"
    return m


def test_chat_ask_post_doc_rag_envelope_sources_and_rows(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/chat/ask — RAG-04 customer envelope: text, rows, sources, E1–E9."""
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()

    cortex = _DocRagCortex()
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    app.state.cortex = cortex
    client = TestClient(app)

    body = client.post(
        "/v1/chat/ask",
        json={
            "question": "Bay-3 leakage?",
            "space_id": "sp_q3_audit",
            "session_id": "ses_rag04",
        },
    ).json()

    assert_envelope_valid(body)
    assert cortex.answer in body["text"]
    assert body["rows"] == [{"excerpt": cortex.excerpt}]
    assert body["badge"] == "L2_VALIDATED"
    assert body["abstained"] is False
    assert body["values"]
    assert body["contributing_sources"][0]["ref_id"] == "src_notes"
    assert body["contributing_sources"][0]["container"] == "notes_a.csv"
    assert body["contributing_sources"][0]["snippet"] == cortex.snippet
    assert body["contributing_sources"][0]["space_id"] == "sp_q3_audit"
    assert body["drillthrough_token"] == "dt_doc_rag_http"
    assert body["audit_id"] == "aud_doc_rag_http"
    assert body["sql_used"] == "-- document retrieval (no SQL)"

    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    settings_mod.get_settings.cache_clear()
