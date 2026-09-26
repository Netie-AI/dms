"""RAG-05 provenance: contributing_sources are exactly what Cortex cited, in this Space.

Root cause of the ~1-in-6 flake in
``tests/control_plane/test_rag_space_boundary.py::test_ask_envelope_doc_rag_scoped_per_space``:
the PII masker ran over ``contributing_sources`` and its account-number pattern
(``\\d{3,4}-\\d{3,4}-\\d{4,8}``) matched the digit groups inside a random UUID
``source_id``. ``91cc8921-b320-4502-9218-4ad5f2cd8f21`` came back as
``91cc8921-bDMSMASK_account_01-4ad5f2cd8f21``: a ref_id no source has, so the
Source panel attributed the answer to nothing Cortex cited. Whether it happened
depended only on which uuid4 the ingest drew.

These tests pin digit-heavy UUIDs so the misattribution reproduces every run,
and assert the customer envelope (``assert_envelope_valid``, text, rows,
sources) on ``Executor.live_ask`` and ``POST /v1/chat/ask``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_core.pii import mask_payload
from dms_executor import Executor
from dms_executor.envelope import (
    assert_envelope_valid,
    build_answer_envelope,
    foreign_space_sources,
    normalize_contributing_sources,
)
from dms_executor.manifest import ManifestMinter, SessionAcl

SPACE_A = "3f2b8a10-1c4d-4e5f-8a9b-0c1d2e3f4a5b"
SPACE_B = "7d6c5b4a-3e2f-4a1b-9c8d-7e6f5a4b3c2d"
# Each holds an account-shaped run (``320-4502-9218``, ``601-2345-6789``) that
# the old masker rewrote. The second also holds a Malaysian-phone-shaped run.
SRC_A = "91cc8921-b320-4502-9218-4ad5f2cd8f21"
SRC_B = "0a1b2c3d-e601-2345-6789-0123456789ab"
SPACE_A_MARK = "Bay-3 leakage for Space A only"
SPACE_B_MARK = "Forklift battery schedule for Space B only"


@dataclass
class _DocRagCortex:
    chunk_index: dict[str, list[dict[str, Any]]]
    asks: list[AskRequest] = field(default_factory=list)
    binds: list[Any] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        self.binds.append(req)
        return QueryResult(ok=True, status="bound", run_id="run-doc-rag")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        sid = req.space_id or ""
        hits = self.chunk_index.get(sid, [])
        top = hits[0]
        return AskResponse(
            answer=f"From the notes: {top['content']}",
            audit_id=f"aud_{sid[:8]}",
            route="doc_rag",
            provenance={"badge": "query_skill", "layer": "L2"},
            rows=[{"excerpt": top["content"]}],
            drillthrough_token=f"dt_{sid[:8]}_doc_rag",
            contributing_sources=[
                {
                    "ref_id": h["source_id"],
                    "filename": h["filename"],
                    "contribution_pct": 100 // len(hits),
                    "content": h["content"],
                    "chunk_index": 0,
                    **({"space_id": h["space_id"]} if "space_id" in h else {}),
                }
                for h in hits
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


def _index() -> dict[str, list[dict[str, Any]]]:
    return {
        SPACE_A: [{"source_id": SRC_A, "filename": "notes_a.csv", "content": SPACE_A_MARK}],
        SPACE_B: [{"source_id": SRC_B, "filename": "notes_b.csv", "content": SPACE_B_MARK}],
    }


# --- the root cause, at the masker ----------------------------------------


@pytest.mark.parametrize("uuid", [SRC_A, SRC_B, SPACE_A, SPACE_B])
def test_masker_leaves_uuid_identifiers_intact(uuid: str) -> None:
    got = mask_payload(
        text=f"see source {uuid} for detail",
        sources=[{"ref_id": uuid, "container": "notes.csv"}],
    )
    assert got["sources"][0]["ref_id"] == uuid
    assert got["text"] == f"see source {uuid} for detail"


def test_masker_still_masks_the_same_digits_outside_a_uuid() -> None:
    got = mask_payload(
        text="Pay account 320-4502-9218 or call 012-345 6789.",
        sources=[{"ref_id": SRC_A, "snippet": "acct 320-4502-9218"}],
    )
    assert "320-4502-9218" not in got["text"]
    assert "DMSMASK_account_" in got["text"]
    assert "320-4502-9218" not in got["sources"][0]["snippet"]
    assert got["sources"][0]["ref_id"] == SRC_A


@pytest.mark.parametrize(
    "addr",
    [
        f"{SRC_A}@corp.com",
        f"jane.{SRC_A}@corp.com",
        f"{SRC_A}_ops@corp.com",
        f"ops+{SRC_B}@corp.com",
    ],
)
def test_masker_still_masks_an_email_with_a_uuid_local_part(addr: str) -> None:
    """The UUID carve-out must not lift a UUID out of the email it belongs to."""
    got = mask_payload(
        text=f"mail {addr} now",
        sources=[{"ref_id": SRC_A, "snippet": f"Contact {addr} for the loan"}],
        rows=[{"contact": addr}],
    )
    assert addr not in got["text"]
    assert "@corp.com" not in got["text"]
    assert "DMSMASK_email_" in got["text"]
    assert "@corp.com" not in got["sources"][0]["snippet"]
    assert "DMSMASK_email_" in got["sources"][0]["snippet"]
    assert got["rows"][0]["contact"] != addr
    assert got["sources"][0]["ref_id"] == SRC_A


def test_masker_does_not_skip_an_all_digit_uuid_shape() -> None:
    """An 8-4-4-4-12 run of digits only is not a minted id; it stays scanned."""
    raw = "12345678-1234-1234-1234-123456789012"
    got = mask_payload(text=f"ref {raw} end", sources=[{"ref_id": SRC_A, "snippet": raw}])
    assert raw not in got["text"]
    assert raw not in got["sources"][0]["snippet"]


def test_envelope_keeps_cited_ref_id_verbatim() -> None:
    env = build_answer_envelope(
        answer_id="ans_x",
        text=f"From the notes: {SPACE_A_MARK}",
        badge="L2_VALIDATED",
        abstained=False,
        rows=[{"excerpt": SPACE_A_MARK}],
        sql_used="-- document retrieval (no SQL)",
        contributing_sources=[{"ref_id": SRC_A, "filename": "notes_a.csv"}],
        drillthrough_token="dt_ans_x_doc_rag",
        space_id=SPACE_A,
        route="doc_rag",
    )
    assert_envelope_valid(env)
    assert [s["ref_id"] for s in env["contributing_sources"]] == [SRC_A]


# --- live_ask + POST /v1/chat/ask -----------------------------------------


def test_live_ask_sources_are_exactly_the_cited_ones_per_space() -> None:
    exe = Executor(cortex=_DocRagCortex(chunk_index=_index()), minter=_minter())  # type: ignore[arg-type]

    env_a = exe.live_ask("What about Bay-3 leakage?", space_id=SPACE_A, session_id="ses_a")
    env_b = exe.live_ask("forklift battery schedule", space_id=SPACE_B, session_id="ses_b")

    for env, mark, other, src in (
        (env_a, SPACE_A_MARK, SPACE_B_MARK, SRC_A),
        (env_b, SPACE_B_MARK, SPACE_A_MARK, SRC_B),
    ):
        assert_envelope_valid(env)
        assert env["abstained"] is False
        assert mark in env["text"] and other not in env["text"]
        assert env["rows"] == [{"excerpt": mark}]
        assert [s["ref_id"] for s in env["contributing_sources"]] == [src]
        assert all(s["space_id"] == env["space_id"] for s in env["contributing_sources"])


def test_chat_ask_post_sources_are_exactly_the_cited_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from dms_api.store.memory import DemoSpaceStore
    from dms_core.control_plane.spaces import SpaceRecord
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    settings_mod.get_settings.cache_clear()
    try:
        cortex = _DocRagCortex(chunk_index=_index())
        app = create_app()
        app.state.space_store = DemoSpaceStore(
            _spaces=[
                SpaceRecord(id=SPACE_A, name="rag-a", source_count=1, member_count=1),
                SpaceRecord(id=SPACE_B, name="rag-b", source_count=1, member_count=1),
            ]
        )
        app.state.ask_service = Executor(cortex=cortex, minter=_minter())  # type: ignore[arg-type]
        app.state.cortex = cortex
        client = TestClient(app)
        body_a = client.post(
            "/v1/chat/ask", json={"question": "Bay-3 leakage?", "space_id": SPACE_A}
        ).json()
        body_b = client.post(
            "/v1/chat/ask", json={"question": "forklift battery?", "space_id": SPACE_B}
        ).json()
    finally:
        settings_mod.get_settings.cache_clear()

    assert_envelope_valid(body_a)
    assert_envelope_valid(body_b)
    assert SPACE_A_MARK in body_a["text"] and SPACE_B_MARK not in body_a["text"]
    assert SPACE_B_MARK in body_b["text"] and SPACE_A_MARK not in body_b["text"]
    assert [s["ref_id"] for s in body_a["contributing_sources"]] == [SRC_A]
    assert [s["ref_id"] for s in body_b["contributing_sources"]] == [SRC_B]


# --- a source Cortex attributes to another Space ---------------------------


def test_cited_source_from_another_space_abstains_named() -> None:
    index = _index()
    # Cortex answers Space B's ask from Space A's chunk and says so.
    index[SPACE_B] = [{**index[SPACE_A][0], "space_id": SPACE_A}]
    exe = Executor(cortex=_DocRagCortex(chunk_index=index), minter=_minter())  # type: ignore[arg-type]

    env_b = exe.live_ask("forklift battery schedule", space_id=SPACE_B, session_id="ses_b")

    assert_envelope_valid(env_b)
    assert env_b["badge"] == "ABSTAIN" and env_b["abstained"] is True
    assert SPACE_A_MARK not in env_b["text"]
    assert "gap: cross_space_source" in env_b["text"]
    assert env_b["rows"] == [] and env_b["values"] == []
    assert env_b["contributing_sources"] == []
    assert env_b["audit_receipt"]["unsure"]["why"] == "ABSTAIN reason: cross_space_source"


def test_normalize_keeps_cortex_order_and_drops_foreign_space() -> None:
    raw = [
        {"ref_id": "s3", "filename": "c.pdf", "space_id": SPACE_B},
        {"ref_id": "s1", "filename": "a.pdf"},
        {"ref_id": "sx", "filename": "x.pdf", "space_id": SPACE_A},
        {"ref_id": "s2", "filename": "b.pdf", "space_id": SPACE_B},
    ]
    out = normalize_contributing_sources(raw, space_id=SPACE_B)
    assert [s["ref_id"] for s in out] == ["s3", "s1", "s2"]
    assert all(s["space_id"] == SPACE_B for s in out)
    assert foreign_space_sources(raw, space_id=SPACE_B) == ["sx"]
    # Same input, same output: no set or hash ordering anywhere.
    assert normalize_contributing_sources(raw, space_id=SPACE_B) == out
