"""RAG-01 — Space-scoped document chunks do not cross Spaces at write time."""

from __future__ import annotations

from pathlib import Path

import pytest
from dms_core.control_plane.document_chunks import list_chunks, search_chunks
from dms_executor.batch_ingest import ingest_batch

pytestmark = pytest.mark.usefixtures("_require_postgres")

NOTES_A = b"Finance walkthrough notes.\nBay-3 leakage for Space A only.\n"
NOTES_B = b"Warehouse ops notes.\nForklift battery schedule for Space B only.\n"


def test_two_spaces_do_not_share_chunk_rows_at_write_time(
    migrated_db: str, two_spaces: dict[str, str], tmp_path: Path
) -> None:
    """WHEN unstructured docs land in Space A and Space B THE SYSTEM SHALL
    store chunk rows bound only to that space_id (no cross-space bleed)."""
    wh = tmp_path / "rag.duckdb"
    tid = two_spaces["tenant_id"]
    space_a = two_spaces["space_a"]
    space_b = two_spaces["space_b"]

    receipt_a = ingest_batch(
        [("notes_a.csv", NOTES_A)],
        path=wh,
        space_id=space_a,
        database_url=migrated_db,
        tenant_id=tid,
    )
    receipt_b = ingest_batch(
        [("notes_b.csv", NOTES_B)],
        path=wh,
        space_id=space_b,
        database_url=migrated_db,
        tenant_id=tid,
    )

    fa = next(f for f in receipt_a.files if f.file == "notes_a.csv")
    fb = next(f for f in receipt_b.files if f.file == "notes_b.csv")
    assert fa.document_index == "indexed"
    assert fb.document_index == "indexed"
    assert fa.chunk_count and fa.chunk_count > 0
    assert fb.chunk_count and fb.chunk_count > 0
    assert fa.source_id and fb.source_id and fa.source_id != fb.source_id

    chunks_a = list_chunks(migrated_db, tenant_id=tid, space_id=space_a)
    chunks_b = list_chunks(migrated_db, tenant_id=tid, space_id=space_b)
    assert chunks_a
    assert chunks_b
    assert {c["space_id"] for c in chunks_a} == {space_a}
    assert {c["space_id"] for c in chunks_b} == {space_b}
    assert {c["source_id"] for c in chunks_a} == {fa.source_id}
    assert {c["source_id"] for c in chunks_b} == {fb.source_id}
    assert not any(c["source_id"] == fa.source_id for c in chunks_b)
    assert not any("Space A only" in c["content"] for c in chunks_b)
    assert not any("Space B only" in c["content"] for c in chunks_a)


def test_unstructured_without_space_stays_pending(tmp_path: Path) -> None:
    receipt = ingest_batch(
        [("notes.csv", NOTES_A)],
        path=tmp_path / "pending.duckdb",
        space_id=None,
    )
    entry = receipt.files[0]
    assert entry.document_index == "pending"
    assert entry.chunk_count is None
    assert entry.ingested is False
    assert entry.table is None


def test_studio_ingest_receipt_and_chunks_api(
    migrated_db: str,
    two_spaces: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Studio receipt + GET /v1/studio/chunks are steward-visible (not table-only)."""
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DMS_TENANT_ID", two_spaces["tenant_id"])
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "studio_rag.duckdb"))
    get_settings.cache_clear()

    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        studio_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )

    client = TestClient(create_app())
    space_a = two_spaces["space_a"]
    r = client.post(
        "/v1/studio/ingest",
        data={"space_id": space_a},
        files={"file": ("notes_a.csv", NOTES_A, "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    files = body.get("files") or []
    assert files
    entry = files[0]
    assert entry["document_index"] == "indexed"
    assert entry["chunk_count"] and entry["chunk_count"] > 0
    assert entry["source_id"]

    listed = client.get(f"/v1/studio/chunks?space_id={space_a}")
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert rows
    assert all(row["space_id"] == space_a for row in rows)
    assert all(row["source_id"] == entry["source_id"] for row in rows)

    other = client.get(f"/v1/studio/chunks?space_id={two_spaces['space_b']}")
    assert other.status_code == 200
    assert other.json() == []


def test_search_chunks_scoped_to_space(
    migrated_db: str, two_spaces: dict[str, str], tmp_path: Path
) -> None:
    """WHEN retrieve is called with space_id THE SYSTEM SHALL return only that space's chunks."""
    wh = tmp_path / "search.duckdb"
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
        migrated_db,
        tenant_id=tid,
        space_id=space_a,
        q="Bay-3 leakage",
        top_k=5,
    )
    assert hits_a
    assert all(h["space_id"] == space_a for h in hits_a)
    assert any("Bay-3" in h["content"] for h in hits_a)
    assert not any("Forklift" in h["content"] for h in hits_a)

    hits_b = search_chunks(
        migrated_db,
        tenant_id=tid,
        space_id=space_b,
        q="forklift battery",
        top_k=5,
    )
    assert hits_b
    assert all(h["space_id"] == space_b for h in hits_b)
    assert not any(h["space_id"] == space_a for h in hits_b)

    assert search_chunks(migrated_db, tenant_id=tid, space_id=space_a, q="forklift") == []
    assert search_chunks(migrated_db, tenant_id=tid, space_id="", q="Bay") == []


def test_library_chunks_search_api(
    migrated_db: str,
    two_spaces: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DMS_TENANT_ID", two_spaces["tenant_id"])
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "search_api.duckdb"))
    get_settings.cache_clear()

    ingest_batch(
        [("notes_a.csv", NOTES_A)],
        path=tmp_path / "search_api.duckdb",
        space_id=two_spaces["space_a"],
        database_url=migrated_db,
        tenant_id=two_spaces["tenant_id"],
    )

    client = TestClient(create_app())
    space_a = two_spaces["space_a"]
    space_b = two_spaces["space_b"]

    ok = client.get(
        "/v1/library/chunks/search",
        params={"space_id": space_a, "q": "Bay-3"},
    )
    assert ok.status_code == 200, ok.text
    rows = ok.json()
    assert rows
    assert all(r["space_id"] == space_a for r in rows)

    empty = client.get(
        "/v1/library/chunks/search",
        params={"space_id": space_b, "q": "Bay-3"},
    )
    assert empty.status_code == 200
    assert empty.json() == []
