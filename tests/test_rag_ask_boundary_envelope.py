"""RAG-05 — adversarial envelope checks (no Postgres required)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

SPACE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SPACE_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SPACE_A_MARK = "Bay-3 leakage for Space A only"
SPACE_B_MARK = "Forklift battery schedule for Space B only"


@dataclass
class DocRagCortex:
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
                answer="Abstained.",
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
            provenance={"badge": "query_skill"},
            rows=[{"excerpt": top["content"]}],
            drillthrough_token=f"dt_{sid[:8]}_doc_rag",
            contributing_sources=[
                {
                    "ref_id": top["source_id"],
                    "filename": top["filename"],
                    "contribution_pct": 100,
                    "content": top["content"],
                }
            ],
        )


def _minter() -> ManifestMinter:
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
    return m


def test_live_ask_doc_rag_envelope_never_crosses_spaces() -> None:
    cortex = DocRagCortex(
        chunk_index={
            SPACE_A: [
                {
                    "source_id": "src_a",
                    "filename": "notes_a.csv",
                    "content": SPACE_A_MARK,
                }
            ],
            SPACE_B: [
                {
                    "source_id": "src_b",
                    "filename": "notes_b.csv",
                    "content": SPACE_B_MARK,
                }
            ],
        }
    )
    exe = Executor(cortex=cortex, minter=_minter())  # type: ignore[arg-type]

    env_a = exe.live_ask("Bay-3 leakage?", space_id=SPACE_A, session_id="ses_a")
    env_b = exe.live_ask("forklift battery?", space_id=SPACE_B, session_id="ses_b")

    assert_envelope_valid(env_a)
    assert_envelope_valid(env_b)
    assert SPACE_A_MARK in env_a["text"]
    assert SPACE_B_MARK not in env_a["text"]
    assert SPACE_B_MARK in env_b["text"]
    assert SPACE_A_MARK not in env_b["text"]
    assert env_a["contributing_sources"][0]["ref_id"] == "src_a"
    assert env_b["contributing_sources"][0]["ref_id"] == "src_b"
