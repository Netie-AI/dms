"""GRADE-AO-01. Compile, one steward approval, armed precheck.

In-memory cases always run. The Postgres case skips only when DATABASE_URL
is unset. A set URL that cannot be reached fails the test.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import duckdb
import psycopg
import pytest
from dms_core.control_plane.onto_data import (
    OntologyDataStore,
    PostgresOntologyDataStore,
    reset_ontology_data_store,
)
from test_ao_vendor_01 import _recorded

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import grade52  # noqa: E402
import onto_compile  # noqa: E402
import onto_steward  # noqa: E402

FOUNDER_REF = "steward=founder Jian Hong, 2026-10-28 17:16 SGT"
_DEFAULT_ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _body() -> dict[str, Any]:
    return {
        "status": "proposed",
        "served": False,
        "vendor_sha": "grade-ao-01",
        "terms": [
            {
                "name": "Customer",
                "table": "customers",
                "description": "buyer",
                "attributes": [],
                "key": ["id"],
            }
        ],
        "measures": [{"name": "orders", "expression": "count(*)"}],
    }


def _prove_warehouse(path: Path) -> None:
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR)")
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, sku VARCHAR)")
    conn.execute(
        "CREATE TABLE orders ("
        "id INTEGER PRIMARY KEY, "
        "customer_id INTEGER, "
        "product_id INTEGER, "
        "qty INTEGER, "
        "FOREIGN KEY (product_id) REFERENCES products(id))"
    )
    conn.execute("INSERT INTO customers VALUES (1, 'ada'), (2, 'bea')")
    conn.execute("INSERT INTO products VALUES (10, 'sku-a')")
    conn.execute("INSERT INTO orders VALUES (100, 1, 10, 3), (101, 1, 10, 1)")
    conn.close()


def _transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    assert url.endswith("/v1/chat/completions")
    assert headers.get("Authorization", "").startswith("Bearer ")
    assert body.get("role") == "ontology"
    assert "model" not in body
    return _recorded(body)


def _arm_compile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_AO_COMPILE", "1")
    monkeypatch.setenv("OPENVAULT_API_KEY", "ov_gradeao")
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")


def test_approve_version_records_actor_ref_and_approves_every_item(
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = OntologyDataStore()
    stored = store.store_proposal(_body(), created_by=str(uuid.uuid4()))
    assert len(stored["items"]) == 2
    actor = str(uuid.uuid4())
    onto_steward.approve_version_cmd(
        version_id=stored["version_id"],
        actor=actor,
        approval_ref=FOUNDER_REF,
        store=store,
    )
    approved = store.approved_items()
    assert {row["id"] for row in approved} == {row["id"] for row in stored["items"]}
    assert {row["status"] for row in approved} == {"approved"}
    assert {row["approved_by"] for row in approved} == {actor}
    assert {row["approval_ref"] for row in approved} == {FOUNDER_REF}
    assert {row["provenance"]["approval_ref"] for row in approved} == {FOUNDER_REF}
    assert {row["provenance"]["approved_by"] for row in approved} == {actor}
    out = capsys.readouterr().out
    assert "steward=founder" in out
    assert FOUNDER_REF in out
    assert "provenance=stored" in out
    assert "Customer" in out


def test_onto_compile_fake_transport_in_memory_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _arm_compile(monkeypatch)
    warehouse = tmp_path / "prove.duckdb"
    _prove_warehouse(warehouse)
    store = OntologyDataStore()
    dest = tmp_path / "onto_proposal.json"
    result = onto_compile.compile_warehouse(
        warehouse,
        str(uuid.uuid4()),
        transport=_transport,
        store=store,
        dest=dest,
    )
    counts = result["counts"]
    assert counts["object"] == 3
    assert counts["link"] >= 1
    assert counts["unkeyed"] == 0
    assert result["status"] == "proposed"
    assert result["served"] is False
    assert store.approved_items() == []
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written["version_id"] == result["version_id"]
    assert written["counts"] == counts
    assert written["served"] is False
    assert written["proposal"]["status"] == "proposed"


def test_compile_cli_refuses_without_database_and_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DMS_AO_COMPILE", raising=False)
    monkeypatch.delenv("DMS_ONTO_PG", raising=False)
    with pytest.raises(SystemExit, match="REFUSED"):
        onto_compile.main(["--warehouse", "missing.duckdb", "--tenant", str(uuid.uuid4())])


def test_onto_steward_refuses_empty_or_default_actor() -> None:
    store = OntologyDataStore()
    stored = store.store_proposal(_body(), created_by=str(uuid.uuid4()))
    version_id = stored["version_id"]
    for actor in ("", "   ", _DEFAULT_ACTOR, "not-a-uuid"):
        with pytest.raises(SystemExit, match="REFUSED"):
            onto_steward.approve_version_cmd(
                version_id=version_id,
                actor=actor,
                approval_ref=FOUNDER_REF,
                store=store,
            )
    assert store.approved_items() == []
    held = store.get_item(stored["items"][0]["id"])
    assert held is not None and held.status == "proposed"
    with pytest.raises(SystemExit, match="approval_ref_empty"):
        onto_steward.approve_version_cmd(
            version_id=version_id,
            actor=str(uuid.uuid4()),
            approval_ref="  ",
            store=store,
        )
    assert store.approved_items() == []


def test_armed_precheck_exits_when_the_store_has_no_approved_items() -> None:
    class _Empty:
        def approved_items(self) -> list[dict[str, Any]]:
            return []

    class _NoObjects:
        def approved_items(self) -> list[dict[str, Any]]:
            return [
                {
                    "kind": "link",
                    "status": "approved",
                    "version_id": "v",
                    "approved_by": "a",
                }
            ]

    for store in (_Empty(), _NoObjects()):
        with pytest.raises(SystemExit, match="no approved ontology version"):
            grade52.approved_ontology_precheck(store=store)


def test_ov_calls_split_cortex_from_dms_roles() -> None:
    events = [
        {"caller_role": "cortex", "model_requested": "pin-a", "model_served": "pin-b"},
        {"caller_role": "grain", "model_requested": "pin-a", "model_served": "pin-a"},
        {"role": "ontology", "model_requested": "pin-a", "model_served": "pin-c"},
        {"model_requested": "auto", "model_served": "pin-x"},
        {"model_requested": "pin-a", "model_served": "pin-a"},
    ]
    got = grade52.ov_calls_by_caller_role(events)
    assert got["cortex"] == {"calls": 2, "model_mismatch": 1}
    assert got["grain"] == {"calls": 1, "model_mismatch": 0}
    assert got["ontology"] == {"calls": 1, "model_mismatch": 1}
    assert got["unattributed"] == {"calls": 1, "model_mismatch": 1}


def test_grade52_never_approves() -> None:
    text = (ROOT / "scripts" / "verify" / "grade52.py").read_text(encoding="utf-8")
    assert "approve_version" not in text
    assert "onto_steward" not in text


def test_armed_all_flags_on_includes_reconfirm_run_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The armed all-flags-on config turns the held-SQL Yes flag on. Default stays off."""
    assert grade52.ARMED_ALL_FLAGS_ON["DMS_RECONFIRM_RUN_HELD"] == "1"
    assert grade52.ARMED_ALL_FLAGS_ON["DMS_DEMO_FALLBACK"] == "0"
    held = "DMS_RECONFIRM_RUN_HELD"
    monkeypatch.delenv(held, raising=False)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    try:
        grade52._prepare_armed_flags(all_flags=False)
        assert os.environ.get(held) is None
        assert os.environ["DMS_DEMO_FALLBACK"] == "0"
        grade52._prepare_armed_flags(all_flags=True)
        assert os.environ[held] == "1"
        assert os.environ["DMS_AI_FIRST"] == "1"
        assert os.environ["DMS_ONTO_FROM_DATA"] == "1"
    finally:
        for name in grade52.ARMED_ALL_FLAGS_ON:
            if name != "DMS_DEMO_FALLBACK":
                os.environ.pop(name, None)
    with pytest.raises(SystemExit, match="--all-flags is for --armed"):
        grade52.main(["--all-flags"])


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


@pytest.fixture
def pg_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL is unset; Postgres grade-ao test skipped")
    try:
        conn = psycopg.connect(url, connect_timeout=3)
    except Exception as exc:
        pytest.fail(f"DATABASE_URL is set but Postgres is unreachable ({type(exc).__name__})")
    else:
        conn.close()
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    return url


def _drop_tenant(url: str, tenant: str) -> None:
    conn = psycopg.connect(url, autocommit=True)
    try:
        conn.execute("DELETE FROM dms.tenants WHERE id = %s", (tenant,))
    finally:
        conn.close()


def test_postgres_compile_approve_and_precheck_share_one_store(
    pg_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tenant = str(uuid.uuid4())
    conn = psycopg.connect(pg_url, autocommit=True)
    try:
        missing = conn.execute("SELECT 1 FROM dms.tenants WHERE id = %s", (tenant,)).fetchone()
    finally:
        conn.close()
    assert missing is None
    _arm_compile(monkeypatch)
    monkeypatch.setenv("DMS_ONTO_PG", "1")
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("DMS_TENANT_ID", tenant)
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    reset_ontology_data_store()
    warehouse = tmp_path / "prove.duckdb"
    _prove_warehouse(warehouse)
    try:
        compiled = onto_compile.compile_warehouse(
            warehouse, tenant, transport=_transport, dest=tmp_path / "onto_proposal.json"
        )
        assert compiled["counts"]["object"] == 3
        conn = psycopg.connect(pg_url, autocommit=True)
        try:
            present = conn.execute("SELECT 1 FROM dms.tenants WHERE id = %s", (tenant,)).fetchone()
        finally:
            conn.close()
        assert present is not None
        actor = str(uuid.uuid4())
        onto_steward.approve_version_cmd(
            version_id=compiled["version_id"],
            actor=actor,
            approval_ref=FOUNDER_REF,
        )
        reader = PostgresOntologyDataStore(pg_url, tenant_id=tenant)
        approved = reader.approved_items()
        assert approved
        assert {row["status"] for row in approved} == {"approved"}
        assert {row["approved_by"] for row in approved} == {actor}
        assert {row["approval_ref"] for row in approved} == {FOUNDER_REF}
        assert {row["provenance"]["approval_ref"] for row in approved} == {FOUNDER_REF}
        objects = [row for row in approved if row["kind"] == "object"]
        assert len(objects) == compiled["counts"]["object"]
        snap = grade52.approved_ontology_precheck()
        assert snap["ontology_version_id"] == compiled["version_id"]
        assert snap["approver_actor_id"] == actor
        assert snap["approved_counts"]["object"] == len(objects)
        empty = str(uuid.uuid4())
        monkeypatch.setenv("DMS_TENANT_ID", empty)
        reset_ontology_data_store()
        with pytest.raises(SystemExit, match="no approved ontology version"):
            grade52.approved_ontology_precheck()
    finally:
        reset_ontology_data_store()
        _drop_tenant(pg_url, tenant)
