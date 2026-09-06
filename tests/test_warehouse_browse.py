"""Warehouse browse + library preview API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "browse.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    return path


def test_list_and_preview(warehouse: Path) -> None:
    from dms_executor.warehouse_browse import list_warehouse_tables, preview_warehouse_table

    tables = list_warehouse_tables(path=warehouse)
    names = {t["table"] for t in tables}
    assert "transactions" in names
    assert all(t["row_count"] >= 0 for t in tables)

    prev = preview_warehouse_table("transactions", limit=5, path=warehouse)
    assert prev["table"] == "transactions"
    assert len(prev["rows"]) <= 5
    assert "sku" in prev["columns"]


def test_preview_rejects_unknown(warehouse: Path) -> None:
    from dms_executor.warehouse_browse import preview_warehouse_table

    with pytest.raises(ValueError):
        preview_warehouse_table("drop_me;--", path=warehouse)


def test_library_preview_route(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    from dms_api.app import create_app

    client = TestClient(create_app())
    r = client.get("/v1/library/warehouse/transactions/preview?limit=3")
    assert r.status_code == 200
    body = r.json()
    assert body["table"] == "transactions"
    assert len(body["rows"]) <= 3
    assert body["row_count"] > len(body["rows"])

    # A table that does not exist is refused for scope (403), not reported missing
    # (404). It used to 404 here, which made the pair of status codes an enumeration
    # oracle: 404 meant "no such table", 403 meant "real table you may not read", so a
    # caller could map the warehouse by reading status codes alone. Both answers are
    # now "not in scope", because a name you have no grant for is not a name you are
    # entitled to learn the existence of.
    bad = client.get("/v1/library/warehouse/nope/preview")
    assert bad.status_code == 403
    assert bad.json()["detail"]["code"] == "warehouse_not_in_space"
    assert bad.json()["detail"]["scope"] == "company-default"


def test_every_tree_leaf_previews(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PREVIEW-01: no bronze/warehouse leaf from the tree may 404."""
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    from dms_api.app import create_app
    from dms_executor.bronze import ingest_csv_bytes
    from dms_executor.warehouse_browse import list_bronze_tables, list_warehouse_tables

    ingest_csv_bytes(filename="tree_leaf.csv", data=b"sku,qty\nA,1\nB,2\n", path=warehouse)

    client = TestClient(create_app())
    tree = client.get("/v1/library/tree").json()

    def walk(nodes: list[dict]) -> list[dict]:
        out: list[dict] = []
        for n in nodes:
            if n.get("kind") == "leaf" and n.get("id", "").startswith(("bronze:", "warehouse:")):
                out.append(n)
            out.extend(walk(n.get("children") or []))
        return out

    for leaf in walk(tree["nodes"]):
        node_id = leaf["id"]
        kind, table = node_id.split(":", 1)
        path = (
            f"/v1/library/warehouse/{table}/preview?limit=200"
            if kind == "warehouse"
            else f"/v1/library/bronze/{table}/preview?limit=200"
        )
        r = client.get(path)
        assert r.status_code == 200, f"{node_id} -> {r.status_code} {r.text}"
        body = r.json()
        assert "rows" in body and "columns" in body
        assert body["row_count"] >= len(body["rows"])

    for row in list_bronze_tables(path=warehouse):
        r = client.get(f"/v1/library/bronze/{row['table']}/preview?limit=200")
        assert r.status_code == 200
        assert r.json()["row_count"] == row["row_count"]

    for row in list_warehouse_tables(path=warehouse):
        r = client.get(f"/v1/library/warehouse/{row['table']}/preview?limit=200")
        assert r.status_code == 200
        assert r.json()["row_count"] == row["row_count"]


def test_preview_allows_digit_leading_bronze_names(warehouse: Path) -> None:
    """Uploaded xlsx tables inherit the filename stem (often digit-leading)."""
    from dms_executor.bronze import ingest_csv_bytes
    from dms_executor.warehouse_browse import preview_bronze_table

    ingest_csv_bytes(
        filename="15_digit_lead.csv",
        data=b"sku,qty\nA,1\n",
        path=warehouse,
        table_name="15_digit_lead",
    )
    prev = preview_bronze_table("bronze.15_digit_lead", limit=200, path=warehouse)
    assert prev["row_count"] == 1
    assert prev["rows"][0]["sku"] == "A"


def test_preview_pagination_total_is_table_size(warehouse: Path) -> None:
    from dms_executor.warehouse_browse import preview_warehouse_table

    prev = preview_warehouse_table("transactions", limit=5, offset=0, path=warehouse)
    assert len(prev["rows"]) == 5
    assert prev["row_count"] == 15
    assert prev["row_count"] != len(prev["rows"])

    page2 = preview_warehouse_table("transactions", limit=5, offset=5, path=warehouse)
    assert len(page2["rows"]) == 5
    assert page2["row_count"] == 15


def test_data_map_notes_missing_database(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from dms_api.app import create_app
    from dms_api.settings import get_settings

    get_settings.cache_clear()
    client = TestClient(create_app())
    r = client.get("/v1/library/data-map")
    assert r.status_code == 200
    body = r.json()
    assert body["database_configured"] is False
    assert "DATABASE_URL" in body["note"]
    assert isinstance(body["warehouse_tables"], list)


def test_ensure_lake_schemas_concurrent_first_create(tmp_path: Path) -> None:
    """Two Library requests may create the lake schemas at the same instant.

    ``CREATE SCHEMA IF NOT EXISTS`` is not concurrency-safe in DuckDB. ``IF NOT
    EXISTS`` only skips a schema that is already *committed*, so two connections
    on one file that start the same CREATE together both write the catalog entry
    and DuckDB aborts the loser::

        _duckdb.TransactionException: TransactionContext Error:
          Catalog write-write conflict on create with "bronze"

    Library fires ``/tree`` twice, so the first load against a warehouse whose
    lake schemas do not exist yet raced itself and one request 500ed. Eight
    call sites depend on ``ensure_lake_schemas`` (bronze ingest, promote,
    warehouse browse, warehouse identity), so the guard belongs here, on the
    shared function, not on any one of them.

    This is the deterministic half of the pair: the barrier puts every thread on
    the catalog write at the same instant, so it fails on every iteration before
    the fix and passes on every one after. See
    ``docs/subagents_findings/2026-09-05_lake-schema-catalog-race.md``.

    Repro::

        python scripts/repro_lake_schema_race.py schemas 40 12
    """
    import threading

    import duckdb
    from dms_executor.lake_schema import ensure_lake_schemas

    # Spelled out, not imported from the module under test: a test that reads its
    # expectation out of the code it is checking agrees with that code by
    # construction. It also keeps this test runnable against the pre-fix module,
    # which is how the guard was shown to fail before it was trusted green.
    expected = {"bronze", "silver", "gold", "quarantine", "dim"}

    db = tmp_path / "race.duckdb"
    duckdb.connect(str(db)).close()  # file exists, no schemas in it

    threads = 12
    gate = threading.Barrier(threads)
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        # connect() is inside the try: if it raises, an exception escaping the
        # thread body would be printed and otherwise lost, leaving `errors` empty
        # and this test green on a run where nothing was actually checked. The
        # thread that then never reaches the barrier breaks it for the others, so
        # the run fails loudly instead of hanging.
        con = None
        try:
            con = duckdb.connect(str(db))
            # A timeout, not a bare wait: a regression that wedges a worker must
            # fail the run loudly rather than hang CI until the job is killed.
            gate.wait(timeout=60)
            ensure_lake_schemas(con)
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            with lock:
                errors.append(exc)
        finally:
            if con is not None:
                con.close()

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()

    assert not errors, f"concurrent ensure_lake_schemas raised: {errors!r}"

    # The postcondition, not merely the absence of an exception. Swallowing the
    # conflict without creating the schema would be the worse bug: every caller
    # here goes on to write into bronze/silver on the assumption it exists.
    con = duckdb.connect(str(db))
    try:
        present = {
            row[0]
            for row in con.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
        }
    finally:
        con.close()
    assert expected <= present, f"missing after ensure: {expected - present}"


def test_parallel_library_lists_same_file(warehouse: Path) -> None:
    """Library fires /tree twice against one DuckDB file. Both must be served.

    Two failure classes reach this path:

    1. Mixing ``read_only=True`` with a RW connection on one file 500s DuckDB
       (``docs/subagents_findings/2026-09-03_library-tree-duckdb-config.md``).
    2. Concurrent lake-schema creation hits a catalog write-write conflict
       (``docs/subagents_findings/2026-09-05_lake-schema-catalog-race.md``).

    What this test does NOT guarantee
    ---------------------------------
    It does not deterministically reach class 2, and a barrier here would not
    make it. ``ensure_demo_warehouse`` takes a module-global lock and holds it
    across a fresh connection plus a schema probe, so threads released together
    are re-serialised before any of them reaches ``ensure_lake_schemas``:
    measured, 8 threads entered within 0.7 ms of each other and left over a
    22.7 s spread. Class 2 therefore landed here about once in sixty runs, which
    read as flaky infrastructure rather than as the defect it was reporting.

    ``test_ensure_lake_schemas_concurrent_first_create`` above is the
    deterministic guard for class 2. This test's job is the end-to-end one: the
    three real Library calls, concurrently, on one file, asserting the rows a
    customer would receive.

    Repro the class-2 failure through this path (probabilistic, ~1 in 60)::

        python scripts/repro_lake_schema_race.py lists 60 8 16
    """
    from concurrent.futures import ThreadPoolExecutor

    from dms_executor.bronze import list_bronze_tables
    from dms_executor.warehouse_browse import list_promote_targets, list_warehouse_tables

    def one() -> tuple[list[dict], list[dict], list[dict]]:
        return (
            list_bronze_tables(path=warehouse),
            list_warehouse_tables(path=warehouse),
            list_promote_targets(path=warehouse),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(one) for _ in range(16)]
        results = [fut.result() for fut in futs]

    # Not just "nothing raised". list_warehouse_tables swallows a per-table count
    # failure as row_count 0, so a path that degraded under contention would have
    # satisfied a bare fut.result() by handing back zeros. Assert the rows the
    # customer receives (CLAUDE.md rule 10).
    for bronze, warehouse_tables, promote in results:
        counts = {t["table"]: t["row_count"] for t in warehouse_tables}
        assert "transactions" in counts
        assert counts["transactions"] == 15
        assert bronze == []
        assert promote == []

    # Concurrency must not change *what* is served, only when.
    assert all(r == results[0] for r in results)
