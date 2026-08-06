"""RAG-04 — DMS envelope contributing_sources mapping for doc-RAG hits."""

from __future__ import annotations

from cortex_client.models import AskResponse
from dms_executor import map_ask_response_to_envelope
from dms_executor.envelope import assert_envelope_valid, normalize_contributing_sources


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
    resp = AskResponse.model_validate(
        {
            "answer": "Bay-3 leakage was flagged in the walkthrough notes.",
            "audit_id": "aud_doc_rag",
            "route": "doc_rag",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "rows": [{"excerpt": "Bay-3 leakage"}],
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
    assert env["contributing_sources"][0]["container"] == "notes_a.csv"
    assert env["contributing_sources"][0]["snippet"]
    assert env["sql_used"] == "-- document retrieval (no SQL)"
    assert env["drillthrough_token"] == "dt_doc_rag_token"


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
    assert env["abstained"] is True
    assert env["contributing_sources"] == []
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
    assert env["contributing_sources"] == []
    assert_envelope_valid(env)
