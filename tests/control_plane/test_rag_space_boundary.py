"""RAG-05 — adversarial: Space A ask must not surface Space B doc chunks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_core.control_plane.document_chunks import search_chunks
from dms_executor import Executor
from dms_executor.batch_ingest import ingest_batch
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

NOTES_A = b"Finance walkthrough notes.\nBay-3 leakage for Space A only.\n"
NOTES_B = b"Warehouse ops notes.\nForklift battery schedule for Space B only.\n"

SPACE_A_MARK = "Bay-3 leakage for Space A only"
SPACE_B_MARK = "Forklift battery schedule for Space B only"


@dataclass
class DocRagCortex:
    """Returns doc-RAG envelopes scoped to the ask space_id."""

    binds: list[Any] = field(default_factory=list)
    asks: list[AskRequest] = field(default_factory=list)
    chunk_index: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def submit(self, req: Any) -> QueryResult:
        self.binds.append(req)
        return QueryResult(ok=True, status="bound", run_id="run-doc-rag")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        sid = req.space_id or ""
        hits = self.chunk_index.get(sid, [])
        if not hits:
            return AskResponse(
                answer="Abstained — no matching documents in this Space.",
                abstained=True,
                audit_id=f"aud_{sid[:8]}",
                route="abstain",
                provenance={"badge": "abstain"},
            )
        top = hits[0]
        return AskResponse(
            answer=f"From the notes: {top['content']}",
            audit_id=f"aud_{sid[:8]}",
            route="doc_rag",
            provenance={"badge": "query_skill", "layer": "L2"},
            rows=[{"excerpt": top["content"]}],
            sql_used=None,
            drillthrough_token=f"dt_{sid[:8]}_doc_rag",
            contributing_sources=[
                {
                    "ref_id": top["source_id"],
                    "filename": top.get("filename", "notes.csv"),
                    "contribution_pct": 100,
                    "content": top["content"],
                    "chunk_index": top.get("chunk_index", 0),
                }
            ],
        )


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
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

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def test_chunk_search_never_crosses_spaces(
    migrated_db: str, two_spaces: dict[str, str], tmp_path: Path
) -> None:
    wh = tmp_path / "rag_boundary.duckdb"
    tid = two_spaces["tenant_id"]
    space_a = two_spaces["space_a"]
    space_b = two_spaces["space_b"]

    ingest_batch(
        [("notes_a.csv", NOTES_A)],
        path=wh,
        space_id=space_a,
        database_url=migrated_db,
        tenant_id=tid,
    )
    ingest_batch(
        [("notes_b.csv", NOTES_B)],
        path=wh,
        space_id=space_b,
        database_url=migrated_db,
        tenant_id=tid,
    )

    hits_a = search_chunks(
        migrated_db, tenant_id=tid, space_id=space_a, q="Bay-3 leakage", top_k=5
    )
    assert hits_a
    assert all(h["space_id"] == space_a for h in hits_a)
    assert any(SPACE_A_MARK in h["content"] for h in hits_a)
    assert not any(SPACE_B_MARK in h["content"] for h in hits_a)

    hits_b = search_chunks(
        migrated_db, tenant_id=tid, space_id=space_b, q="forklift battery", top_k=5
    )
    assert hits_b
    assert all(h["space_id"] == space_b for h in hits_b)
    assert not any(SPACE_A_MARK in h["content"] for h in hits_b)


def test_ask_envelope_doc_rag_scoped_per_space(
    migrated_db: str,
    two_spaces: dict[str, str],
    tmp_path: Path,
    minter: ManifestMinter,
) -> None:
    wh = tmp_path / "rag_ask.duckdb"
    tid = two_spaces["tenant_id"]
    space_a = two_spaces["space_a"]
    space_b = two_spaces["space_b"]

    ra = ingest_batch(
        [("notes_a.csv", NOTES_A)],
        path=wh,
        space_id=space_a,
        database_url=migrated_db,
        tenant_id=tid,
    )
    rb = ingest_batch(
        [("notes_b.csv", NOTES_B)],
        path=wh,
        space_id=space_b,
        database_url=migrated_db,
        tenant_id=tid,
    )
    fa = ra.files[0]
    fb = rb.files[0]

    cortex = DocRagCortex(
        chunk_index={
            space_a: [
                {
                    "source_id": fa.source_id,
                    "filename": "notes_a.csv",
                    "content": SPACE_A_MARK,
                    "chunk_index": 0,
                }
            ],
            space_b: [
                {
                    "source_id": fb.source_id,
                    "filename": "notes_b.csv",
                    "content": SPACE_B_MARK,
                    "chunk_index": 0,
                }
            ],
        }
    )
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=wh)  # type: ignore[arg-type]

    env_a = exe.live_ask("What about Bay-3 leakage?", space_id=space_a, session_id="ses_a")
    env_b = exe.live_ask("forklift battery schedule", space_id=space_b, session_id="ses_b")

    assert_envelope_valid(env_a)
    assert_envelope_valid(env_b)
    assert SPACE_A_MARK in env_a["text"]
    assert SPACE_B_MARK not in env_a["text"]
    assert SPACE_B_MARK in env_b["text"]
    assert SPACE_A_MARK not in env_b["text"]
    assert env_a["contributing_sources"][0]["ref_id"] == fa.source_id
    assert env_b["contributing_sources"][0]["ref_id"] == fb.source_id
    assert env_a["contributing_sources"][0]["ref_id"] != env_b["contributing_sources"][0]["ref_id"]


def test_chat_ask_post_envelope_no_cross_space_doc_leak(
    migrated_db: str,
    two_spaces: dict[str, str],
    tmp_path: Path,
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v1/chat/ask — assert envelope text + contributing_sources stay in Space."""
    from dms_api import settings as settings_mod
    from dms_api.store.memory import DemoSpaceStore
    from dms_core.control_plane.spaces import SpaceRecord

    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()

    wh = tmp_path / "rag_api.duckdb"
    tid = two_spaces["tenant_id"]
    space_a = two_spaces["space_a"]
    space_b = two_spaces["space_b"]

    ra = ingest_batch(
        [("notes_a.csv", NOTES_A)],
        path=wh,
        space_id=space_a,
        database_url=migrated_db,
        tenant_id=tid,
    )
    rb = ingest_batch(
        [("notes_b.csv", NOTES_B)],
        path=wh,
        space_id=space_b,
        database_url=migrated_db,
        tenant_id=tid,
    )
    fa = ra.files[0]
    fb = rb.files[0]

    cortex = DocRagCortex(
        chunk_index={
            space_a: [
                {
                    "source_id": fa.source_id,
                    "filename": "notes_a.csv",
                    "content": SPACE_A_MARK,
                }
            ],
            space_b: [
                {
                    "source_id": fb.source_id,
                    "filename": "notes_b.csv",
                    "content": SPACE_B_MARK,
                }
            ],
        }
    )

    # TestClient without `with` skips lifespan, so create_app keeps DemoSpaceStore
    # (cccc/dddd). Bind the Postgres-seeded space ids the ask route looks up.
    app = create_app()
    app.state.space_store = DemoSpaceStore(
        _spaces=[
            SpaceRecord(id=space_a, name="rag-a", source_count=1, member_count=1),
            SpaceRecord(id=space_b, name="rag-b", source_count=1, member_count=1),
        ]
    )
    app.state.ask_service = Executor(cortex=cortex, minter=minter, warehouse_path=wh)  # type: ignore[arg-type]
    app.state.cortex = cortex
    client = TestClient(app)

    body_a = client.post(
        "/v1/chat/ask",
        json={"question": "Bay-3 leakage?", "space_id": space_a},
    ).json()
    body_b = client.post(
        "/v1/chat/ask",
        json={"question": "forklift battery?", "space_id": space_b},
    ).json()

    assert_envelope_valid(body_a)
    assert_envelope_valid(body_b)
    assert SPACE_B_MARK not in body_a["text"]
    assert SPACE_A_MARK not in body_b["text"]
    assert all(s.get("ref_id") != fb.source_id for s in body_a["contributing_sources"])
    assert all(s.get("ref_id") != fa.source_id for s in body_b["contributing_sources"])

    settings_mod.get_settings.cache_clear()
    monkeypatch.delenv("DMS_ASK_MODE", raising=False)
    monkeypatch.delenv("DMS_DEMO_FALLBACK", raising=False)
    settings_mod.get_settings.cache_clear()
