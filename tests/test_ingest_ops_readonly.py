"""INGEST-OPS round 2: Space read routes never take the warehouse write lock.

Round-1 verifier (blocking): ``GET /v1/spaces``, ``/v1/spaces/{id}`` and
``/v1/spaces/{id}/sources`` opened the DuckDB warehouse in write mode on every
call, and a registry read error became a 500. A SQL-source ingest in another
process holds that file for minutes. These hold a real writer on the file and
assert on what the API caller receives: 200, the Space, and a named degraded
state - never a 5xx, never a silent "0 sources".

Also here: the unscoped library listing no longer hands out every Space's SQL
connection, the row-cap line appears only on answers that cite a capped table,
and the chat route names a provider failure so a harness can tell it from a DMS
crash.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dms_api.app import create_app
from dms_api.deps import get_space_store
from dms_api.settings import get_settings
from dms_api.store.memory import DemoSpaceStore
from fastapi.testclient import TestClient
from test_ingest_ops_sources import _plant_old_registry


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "ingest_ro.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _client(space_name: str = "BIRD") -> tuple[TestClient, str]:
    store = DemoSpaceStore.seeded()
    space = store.create(space_name)
    app = create_app()
    app.dependency_overrides[get_space_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False), space.id


class _ExternalWriter:
    """Another process holding the warehouse open read-write (an ingest)."""

    def __init__(self, path: Path) -> None:
        code = textwrap.dedent(
            f"""
            import sys, duckdb
            con = duckdb.connect({str(path)!r})
            con.execute("SELECT 1").fetchall()
            print("held", flush=True)
            sys.stdin.readline()
            con.close()
            """
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline().strip()
        assert line == "held", line

    def release(self) -> None:
        if self.proc.poll() is None:
            assert self.proc.stdin is not None
            self.proc.stdin.write("\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=30)


@pytest.fixture()
def held(warehouse: Path) -> Iterator[Path]:
    _plant_old_registry(warehouse, "sp-placeholder")
    writer = _ExternalWriter(warehouse)
    try:
        yield warehouse
    finally:
        writer.release()


def _plant_for(warehouse: Path, space_id: str) -> None:
    from dms_executor.demo_grants import canonical_space_id

    _plant_old_registry(warehouse, canonical_space_id(space_id))


# --- read routes: read-only, degraded not 5xx ---------------------------------


def test_space_routes_read_the_registry_read_only(warehouse: Path) -> None:
    """Unheld: the planted pull counts, and the file was never opened RW by a GET."""
    client, space_id = _client()
    _plant_for(warehouse, space_id)
    mtime = warehouse.stat().st_mtime_ns

    one = client.get(f"/v1/spaces/{space_id}")
    assert one.status_code == 200, one.text
    assert one.json()["source_count"] == 1
    assert "degraded" not in one.json()
    listed = client.get("/v1/spaces")
    assert listed.status_code == 200
    assert "degraded" not in listed.json()
    src = client.get(f"/v1/spaces/{space_id}/sources")
    assert src.status_code == 200, src.text
    assert [s["bronze_table"] for s in src.json()["sources"]] == ["bronze.public_trans"]
    assert warehouse.stat().st_mtime_ns == mtime


def test_space_routes_answer_200_while_another_process_holds_the_writer(
    warehouse: Path,
) -> None:
    client, space_id = _client()
    _plant_for(warehouse, space_id)
    writer = _ExternalWriter(warehouse)
    try:
        one = client.get(f"/v1/spaces/{space_id}")
        assert one.status_code == 200, one.text
        body = one.json()
        assert body["id"] == space_id
        assert body["degraded"]["code"] == "warehouse_unavailable"
        assert "ingest" in body["degraded"]["message"]

        listed = client.get("/v1/spaces")
        assert listed.status_code == 200, listed.text
        assert listed.json()["degraded"]["code"] == "warehouse_unavailable"
        assert space_id in {s["id"] for s in listed.json()["spaces"]}

        src = client.get(f"/v1/spaces/{space_id}/sources")
        assert src.status_code == 200, src.text
        assert src.json()["degraded"]["code"] == "warehouse_unavailable"
        assert src.json()["sources"] == []

        lib = client.get(f"/v1/library/sources?space_id={space_id}")
        assert lib.status_code == 200, lib.text
        assert lib.headers.get("X-DMS-Degraded") == "warehouse_unavailable"
    finally:
        writer.release()

    # Writer gone: the same routes see the pull again, and are not degraded.
    after = client.get(f"/v1/spaces/{space_id}")
    assert after.status_code == 200
    assert after.json()["source_count"] == 1
    assert "degraded" not in after.json()


def test_space_routes_answer_200_while_this_process_holds_the_writer(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An in-process writer on another thread: bounded wait, then degraded."""
    from dms_executor import demo_warehouse as dw

    monkeypatch.setattr(dw, "READ_LOCK_TIMEOUT_S", 0.2)
    client, space_id = _client()
    _plant_for(warehouse, space_id)
    holding, done = threading.Event(), threading.Event()

    def hold() -> None:
        con = dw.connect_file(warehouse)
        try:
            holding.set()
            done.wait(30)
        finally:
            con.close()

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert holding.wait(10)
        r = client.get(f"/v1/spaces/{space_id}/sources")
        assert r.status_code == 200, r.text
        assert r.json()["degraded"]["code"] == "warehouse_unavailable"
    finally:
        done.set()
        t.join(30)
    assert client.get(f"/v1/spaces/{space_id}/sources").json()["count"] == 1


def test_unscoped_library_listing_does_not_leak_space_sql_connections(
    warehouse: Path,
) -> None:
    client, space_id = _client()
    _plant_for(warehouse, space_id)

    everything = client.get("/v1/library/sources")
    assert everything.status_code == 200
    blob = repr(everything.json())
    assert "db.example.net" not in blob
    assert "public_trans" not in blob

    scoped = client.get(f"/v1/library/sources?space_id={space_id}").json()
    pull = next(s for s in scoped if s["id"] == "bronze:public_trans")
    assert pull["ref"] == "postgresql://db.example.net:5432/sales#public.trans"


def test_unscoped_listing_keeps_company_pulls_without_their_connection(
    warehouse: Path,
) -> None:
    import duckdb

    _plant_old_registry(warehouse, "sp-other")
    con = duckdb.connect(str(warehouse))
    try:
        con.execute("UPDATE bronze._ingest_registry SET space_id = NULL")
    finally:
        con.close()
    client, _space = _client()
    rows = client.get("/v1/library/sources").json()
    pull = next(s for s in rows if s["id"] == "bronze:public_trans")
    assert pull["ref"] == "bronze.public_trans"
    assert pull["connection_redacted"] is True
    assert "db.example.net" not in repr(rows)


# --- row-cap line: only when a capped table is cited --------------------------


def test_rowcap_line_only_when_a_capped_table_is_cited(warehouse: Path) -> None:
    from dms_executor.bronze import truncation_notes

    _plant_old_registry(warehouse, "sp-bird")
    cited = truncation_notes(
        tables=["bronze.public_trans"], sql="SELECT SUM(amount) FROM bronze.public_trans",
        path=warehouse,
    )
    assert cited == [
        "partial table: bronze.public_trans holds 3 of an unknown number of source rows "
        "(ingest row cap); this answer covers the loaded rows only"
    ]
    assert truncation_notes(
        tables=["inventory"], sql="SELECT COUNT(*) FROM inventory", path=warehouse
    ) == []


def test_rowcap_on_a_held_warehouse_adds_no_noise_to_uncited_answers(
    held: Path,
) -> None:
    """Round-1 minor: on a lock every answer got 'row-cap check unavailable'."""
    from dms_executor.bronze import truncation_notes

    # Demo tables, an uncapped bronze table: nothing, busy or not.
    assert truncation_notes(
        tables=["inventory"], sql="SELECT COUNT(*) FROM inventory", path=held
    ) == []
    assert truncation_notes(tables=[], sql=None, path=held) == []


def test_rowcap_on_a_held_warehouse_still_names_a_cited_capped_table(
    warehouse: Path,
) -> None:
    from dms_executor.bronze import truncation_notes

    _plant_old_registry(warehouse, "sp-bird")
    sql = "SELECT SUM(amount) FROM bronze.public_trans"
    # One good read, then an ingest takes the file.
    assert len(truncation_notes(tables=[], sql=sql, path=warehouse)) == 1
    writer = _ExternalWriter(warehouse)
    try:
        notes = truncation_notes(tables=[], sql=sql, path=warehouse)
        assert len(notes) == 1 and "bronze.public_trans holds 3" in notes[0], notes
        assert truncation_notes(
            tables=["inventory"], sql="SELECT 1 FROM inventory", path=warehouse
        ) == []
    finally:
        writer.release()


def test_rowcap_never_read_and_held_names_only_cited_bronze(held: Path) -> None:
    """Nothing known yet: a cited bronze table gets the 'unavailable' line (it may
    be partial); a demo-table answer gets nothing."""
    from dms_executor.bronze import ROWCAP_UNAVAILABLE_NOTE, truncation_notes

    assert truncation_notes(
        tables=[], sql="SELECT SUM(amount) FROM bronze.public_trans", path=held
    ) == [ROWCAP_UNAVAILABLE_NOTE]
    assert truncation_notes(
        tables=["transactions"], sql="SELECT 1 FROM transactions", path=held
    ) == []


# --- chat route: a provider failure is named, a DMS crash is not --------------


class _RaisingAsk:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def live_ask(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        raise self.exc

    def demo_ask(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("demo fallback must not run")


def _chat_client(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> TestClient:
    import dms_api.routes.chat as chat_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(
        chat_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )
    app = create_app()
    app.state.ask_service = _RaisingAsk(exc)
    app.state.cortex = object()
    return TestClient(app, raise_server_exceptions=False)


def test_provider_budget_failure_is_named_on_the_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _chat_client(
        monkeypatch, RuntimeError("FreeRoute token budget exceeded (HTTP 429)")
    )
    r = client.post("/v1/chat/ask", json={"question": "how many schools"})
    assert r.status_code == 429, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "provider_rate_limited"
    assert detail["upstream"] == "provider"
    get_settings.cache_clear()


def test_dms_internal_crash_is_not_named_a_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _chat_client(monkeypatch, KeyError("rows"))
    r = client.post("/v1/chat/ask", json={"question": "how many schools"})
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "live_ask_failed"
    assert "upstream" not in detail
    get_settings.cache_clear()


def test_harness_grades_the_real_route_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end over the actual chat-route bodies: provider excluded, crash WRONG."""
    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from bird_minidev import provider_error_kind

    def kind_for(exc: Exception) -> str | None:
        client = _chat_client(monkeypatch, exc)
        r = client.post("/v1/chat/ask", json={"question": "q"})
        req = httpx.Request("POST", "http://dms.test/v1/chat/ask")
        resp = httpx.Response(r.status_code, request=req, content=r.content,
                              headers={"content-type": "application/json"})
        return provider_error_kind(httpx.HTTPStatusError("x", request=req, response=resp))

    assert kind_for(RuntimeError("FreeRoute has no candidate hop (HTTP 503)")) == (
        "provider:provider_unavailable"
    )
    assert kind_for(KeyError("rows")) == "http_503"
    get_settings.cache_clear()
