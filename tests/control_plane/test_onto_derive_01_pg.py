"""ONTO-DERIVE-01 against a real PostgreSQL source and the Postgres ontology store.

The CI-runnable twin (``tests/test_onto_derive_01.py``) replays the source
catalog through a fake DB-API connection. This one connects the real
``psycopg`` connector to a real source database: three tables, two declared
foreign keys, one added ``NOT VALID`` over orphan rows (how a legacy database
carries a broken FK). The ontology is stored through ``PostgresOntologyStore``
under RLS. Runs wherever the control-plane suite runs (R-0002).
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest
from dms_core.control_plane.onto_store import PostgresOntologyStore
from dms_executor.space_ontology import set_ontology_store

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_onto_derive_01 import (  # noqa: E402
    BROKEN_LINK,
    BROKEN_Q,
    BROKEN_SQL,
    GOOD_LINK,
    VERIFIED_Q,
    VERIFIED_SQL,
    WRONG_JOIN_SQL,
    _assert_abstain,
    _gate_allows,
    _oracle,
    _Space,
    make_minter,
)
from test_space_gen_01 import _multiset, _RecordingCortex  # noqa: E402


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> Any:
    return make_minter(monkeypatch)


SOURCE_DB = f"onto_src_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def source_db(migrated_db: str) -> Iterator[dict[str, Any]]:
    url = urlparse(migrated_db)
    with psycopg.connect(migrated_db, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{SOURCE_DB}"')
    src = migrated_db.rsplit("/", 1)[0] + f"/{SOURCE_DB}"
    with psycopg.connect(src) as conn:
        conn.execute(
            """
            CREATE TABLE districts (district_id INT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE schools (
              school_id INT PRIMARY KEY,
              district_id INT NOT NULL REFERENCES districts(district_id),
              name TEXT NOT NULL
            );
            CREATE TABLE enrollments (
              enrollment_id INT PRIMARY KEY, school_id INT NOT NULL, students INT NOT NULL
            );
            INSERT INTO districts VALUES (1, 'North'), (2, 'South');
            INSERT INTO schools VALUES (1, 1, 'Alder'), (2, 1, 'Birch'), (3, 2, 'Cedar'),
              (4, 1, 'Dogwood');
            INSERT INTO enrollments VALUES (100, 1, 30), (101, 2, 25), (102, 7, 40),
              (103, 9, 12);
            ALTER TABLE enrollments ADD CONSTRAINT enrollments_school_id_fkey
              FOREIGN KEY (school_id) REFERENCES schools(school_id) NOT VALID;
            """
        )
        conn.commit()
    try:
        yield {
            "host": url.hostname or "127.0.0.1",
            "port": url.port or 5432,
            "user": url.username or "dms",
            "password": url.password or "",
            "database": SOURCE_DB,
        }
    finally:
        with psycopg.connect(migrated_db, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{SOURCE_DB}" WITH (FORCE)')


def _pg_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: Any,
    conn: psycopg.Connection,
    tenants: dict[str, Any],
    source: dict[str, Any],
    migrated_db: str,
) -> _Space:
    from dms_api.app import create_app
    from dms_api.settings import Settings, get_settings
    from dms_core.control_plane.session import set_tenant_context
    from dms_executor import Executor
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from fastapi.testclient import TestClient

    lake = tmp_path / "onto_derive_pg.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    get_settings.cache_clear()
    _gate_allows(monkeypatch)
    cortex = _RecordingCortex(warehouse=lake, payload={})
    app = create_app()
    space = app.state.space_store.create(f"pg-schools-{uuid.uuid4().hex[:6]}")
    # The same Space in the control plane, so the ontology rows have a parent.
    set_tenant_context(conn, tenants["alpha"], role="admin")
    conn.execute(
        "INSERT INTO dms.spaces (id, tenant_id, name, created_by) VALUES (%s, %s, %s, %s)",
        (space.id, tenants["alpha"], space.name, tenants["user"]),
    )
    conn.commit()
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    rig = _Space(client, cortex, space.id, lake)
    r = client.post(
        "/v1/studio/sources/sql",
        json={"kind": "postgresql", "space_id": space.id, **source},
    )
    assert r.status_code == 200, r.text
    assert source["password"] not in r.text or not source["password"]
    rig.receipt = r.json()
    return rig


def test_real_postgres_source_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: Any,
    conn: psycopg.Connection,
    two_tenants: dict[str, Any],
    source_db: dict[str, Any],
    migrated_db: str,
) -> None:
    store = PostgresOntologyStore(migrated_db, tenant_id=two_tenants["alpha"])
    prev = set_ontology_store(store)
    try:
        rig = _pg_space(tmp_path, monkeypatch, minter, conn, two_tenants, source_db, migrated_db)
        assert rig.receipt["declared_foreign_keys"] == 2
        assert rig.receipt["ontology"]["derived"] is True, rig.receipt["ontology"]

        view = rig.client.get(f"/v1/spaces/{rig.space_id}/ontology").json()
        (only,) = view["ontologies"]
        links = {link["name"]: link for link in only["links"]}
        assert links[GOOD_LINK]["status"] == "verified"
        assert links[BROKEN_LINK]["status"] == "unverified"
        assert links[BROKEN_LINK]["violations"][0]["check"] == "fk_intact"
        assert {o["name"] for o in only["objects"]} == {
            "bronze.public_districts",
            "bronze.public_schools",
            "bronze.public_enrollments",
        }
        # Landed types come from the real source, not a guess.
        schools = next(o for o in only["objects"] if o["name"] == "bronze.public_schools")
        assert schools["key"] == ["school_id"]

        env = rig.ask(VERIFIED_Q, {"query_sql": VERIFIED_SQL, "plan_source": "ontology_plan"})
        oracle = _oracle(rig.lake, VERIFIED_SQL)
        assert oracle == [{"school_count": 3}]
        assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"))
        assert _multiset(env["rows"]) == _multiset(oracle)
        assert "school_count=3" in str(env.get("text") or "")

        env = rig.ask(BROKEN_Q, {"query_sql": BROKEN_SQL, "plan_source": "ontology_plan"})
        _assert_abstain(env, f"ontology_unverified: fk_intact on {BROKEN_LINK}")

        env = rig.ask(VERIFIED_Q, {"query_sql": WRONG_JOIN_SQL, "plan_source": "ontology_plan"})
        _assert_abstain(env, "unverified_join")
        assert rig.cortex.executed == [VERIFIED_SQL]

        # Durable: rows in dms.onto_snapshot under tenant alpha, invisible to beta.
        beta = PostgresOntologyStore(migrated_db, tenant_id=two_tenants["beta"])
        assert beta.active_for_space(uuid.UUID(rig.space_id)) == []
        assert store.active_for_space(uuid.UUID(rig.space_id)) != []

        # Re-derive with the source connection reads keys only and appends a snapshot.
        r = rig.client.post(
            f"/v1/spaces/{rig.space_id}/ontology/derive",
            json={"source": {"kind": "postgresql", **source_db}},
        )
        assert r.status_code == 200, r.text
        assert source_db["password"] not in r.text or not source_db["password"]
        (out,) = r.json()["ontologies"]
        assert {link["name"]: link["status"] for link in out["links"]} == {
            GOOD_LINK: "verified",
            BROKEN_LINK: "unverified",
        }
    finally:
        set_ontology_store(prev)
