"""ONTO-STORE-01 / dms#279: durable versioned ontology store.

No skip, no xfail, no importorskip. Figures here are CI fixtures, not live.

Floor 1 (migration): GitHub CI has no Postgres (`DMS_SKIP_CONTROL_PLANE_TESTS=1`
in lint-type-test). Live `alembic upgrade head` / downgrade is
`tests/control_plane/conftest.py:migrated_db` when Postgres is reachable
(`docker compose ... up -d postgres`). This file always runs 0004
`upgrade()` then `downgrade()` against a recording Alembic `op` (the same
callables Alembic invokes) and asserts tables, CHECKs, and drops.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import fields
from pathlib import Path
from types import ModuleType
from uuid import uuid4

from dms_core.control_plane.onto_store import (
    FORBIDDEN_IDENTITY_KEYS,
    IDENTITY_FIELDS,
    OntologyStore,
    SchemaColumn,
    SchemaForeignKey,
    SchemaTable,
    SourceIdentity,
    decide_reconnect,
    schema_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
REV_PATH = ROOT / "alembic" / "versions" / "0004_ontology_store.py"
STORE_PATH = ROOT / "packages" / "core" / "dms_core" / "control_plane" / "onto_store.py"

REQUIRED_TABLES = (
    "ontology_version",
    "onto_object_type",
    "onto_property",
    "onto_link",
    "onto_measure",
    "onto_object",
    "onto_link_instance",
    "onto_audit",
)
REQUIRED_VERSION_COLS = {
    "space_id",
    "source_kind",
    "source_host",
    "source_database",
    "source_schema",
    "schema_fingerprint",
    "status",
    "created_by",
    "created_at",
}
CREDENTIAL_COLUMNS = FORBIDDEN_IDENTITY_KEYS | {
    "connectionstring",
    "passwd_hash",
    "password_hash",
}
_SKIP_COL = frozenset(
    {"unique", "check", "primary", "constraint", "foreign", "create", "references"}
)


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(str(sql))


def _load_revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rev0004_ontology_store", REV_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _create_blocks(sql: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2)
        for m in re.finditer(
            r"CREATE TABLE dms\.([a-z_]+)\s*\((.*?)\)\s*;", sql, flags=re.S | re.I
        )
    }


def _columns(block: str) -> list[str]:
    names: list[str] = []
    for raw in block.splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        first = line.split()[0].strip('",').lower()
        if first in _SKIP_COL:
            continue
        names.append(first)
    return names


def _orders() -> tuple[SchemaTable, SchemaTable]:
    id_col = SchemaColumn("id", "INTEGER")
    amt = SchemaColumn("amount", "NUMERIC(12,2)")
    a = SchemaTable("orders", columns=(id_col, amt), primary_key=("id",))
    b = SchemaTable("orders", columns=(amt, id_col), primary_key=("id",))
    return a, b


def _identity() -> SourceIdentity:
    return SourceIdentity(
        kind="postgresql",
        host="db.example.test",
        database="sales",
        schema="public",
    )


def test_migration_0004_upgrades_and_downgrades_cleanly() -> None:
    assert REV_PATH.is_file()
    chain = (ROOT / "alembic" / "versions" / "0003_document_chunks.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0003_document_chunks"' in chain
    mod = _load_revision()
    assert mod.revision == "0004_ontology_store"
    assert mod.down_revision == "0003_document_chunks"
    rec = _Recorder()
    mod.op = rec
    mod.upgrade()
    upgrade_sql = "\n".join(rec.statements)
    rec.statements.clear()
    mod.downgrade()
    downgrade_sql = "\n".join(rec.statements)

    blocks = _create_blocks(upgrade_sql)
    assert set(blocks) == set(REQUIRED_TABLES)
    version_cols = _columns(blocks["ontology_version"])
    assert REQUIRED_VERSION_COLS <= set(version_cols)
    assert "CHECK (status IN ('proposed', 'active', 'superseded'))" in upgrade_sql
    prop_cols = _columns(blocks["onto_property"])
    assert "is_pk" in prop_cols
    link_sql = blocks["onto_link"]
    assert "one-to-one" in link_sql and "one-to-many" in link_sql
    assert "measured_cardinality" in _columns(link_sql)
    measure_cols = _columns(blocks["onto_measure"])
    assert {"column_name", "aggregate", "grain"} <= set(measure_cols)
    for table in REQUIRED_TABLES:
        if table == "ontology_version":
            continue
        assert "state" in _columns(blocks[table]) or table == "onto_audit"
    audit_cols = _columns(blocks["onto_audit"])
    assert {"actor_user_id", "action_type", "inputs", "result", "created_at"} <= set(
        audit_cols
    )
    for table in REQUIRED_TABLES:
        assert "state TEXT NOT NULL DEFAULT 'proposed'" in blocks[table] or table in {
            "ontology_version",
            "onto_audit",
        }
        assert (
            f"DROP TABLE IF EXISTS dms.{table} CASCADE" in downgrade_sql
        ), f"downgrade missing {table}"
    assert upgrade_sql.lower().count("create table dms.") == len(REQUIRED_TABLES)
    # Second upgrade after downgrade still emits the same CREATE TABLE set.
    rec.statements.clear()
    mod.upgrade()
    again = _create_blocks("\n".join(rec.statements))
    assert set(again) == set(REQUIRED_TABLES)


def test_same_fingerprint_reconnect_returns_same_version_id() -> None:
    store = OntologyStore()
    space = uuid4()
    ident = _identity()
    fp = schema_fingerprint(_orders()[:1])
    first = store.reconnect(
        space_id=space, identity=ident, fingerprint=fp, created_by=uuid4()
    )
    second = store.reconnect(
        space_id=space, identity=ident, fingerprint=fp, created_by=uuid4()
    )
    assert first.id == second.id
    assert first.status == "active"
    assert store.load_active(space, ident) is not None
    assert store.load_active(space, ident).id == first.id  # type: ignore[union-attr]
    assert store.load_by_version(first.id) is not None
    assert [v.id for v in store.list_versions(space, ident)] == [first.id]
    assert len(store._audit) == 1


def test_changed_fingerprint_makes_proposed_old_stays_active() -> None:
    store = OntologyStore()
    space = uuid4()
    ident = _identity()
    orders, _ = _orders()
    extra = SchemaTable(
        "lines",
        columns=(
            SchemaColumn("id", "INTEGER"),
            SchemaColumn("order_id", "INTEGER"),
        ),
        primary_key=("id",),
    )
    fp1 = schema_fingerprint([orders])
    fp2 = schema_fingerprint(
        [orders, extra],
        (
            SchemaForeignKey(
                name="lines_order",
                from_table="lines",
                from_columns=("order_id",),
                to_table="orders",
                to_columns=("id",),
            ),
        ),
    )
    assert fp1 != fp2
    old = store.reconnect(space_id=space, identity=ident, fingerprint=fp1)
    new = store.reconnect(space_id=space, identity=ident, fingerprint=fp2)
    assert new.id != old.id
    assert new.status == "proposed"
    assert old.status == "active"
    active = store.load_active(space, ident)
    assert active is not None
    assert active.id == old.id
    assert {v.id for v in store.list_versions(space, ident)} == {old.id, new.id}
    audits = [a for a in store._audit if a.version_id == new.id]
    assert len(audits) == 1
    assert audits[0].action_type == "reconnect"
    assert set(audits[0].inputs) <= {
        "space_id",
        "kind",
        "host",
        "database",
        "schema",
        "schema_fingerprint",
    }
    assert not (FORBIDDEN_IDENTITY_KEYS & set(audits[0].inputs))
    # Reconnect of the new fingerprint is idempotent: no second proposed row.
    again = store.reconnect(space_id=space, identity=ident, fingerprint=fp2)
    assert again.id == new.id
    assert store.load_active(space, ident).id == old.id  # type: ignore[union-attr]
    assert len(store._audit) == 2
    assert not hasattr(OntologyStore, "confirm")
    assert not hasattr(OntologyStore, "reject")
    src = STORE_PATH.read_text(encoding="utf-8")
    assert "UPDATE dms.ontology_version" not in src


def test_no_table_stores_credentials() -> None:
    ident_names = {f.name for f in fields(SourceIdentity)}
    assert ident_names == set(IDENTITY_FIELDS)
    assert not (ident_names & FORBIDDEN_IDENTITY_KEYS)
    raised = False
    try:
        SourceIdentity.from_mapping(
            {
                "kind": "postgresql",
                "host": "db.example.test",
                "database": "sales",
                "schema": "public",
                "password": "hunter2",
            }
        )
    except ValueError:
        raised = True
    assert raised
    raised_user = False
    try:
        SourceIdentity.from_mapping(
            {
                "kind": "postgresql",
                "host": "db.example.test",
                "database": "sales",
                "schema": "public",
                "user": "sa",
            }
        )
    except ValueError:
        raised_user = True
    assert raised_user
    raised_conn = False
    try:
        SourceIdentity(
            kind="postgresql",
            host="postgresql://u:p@db.example.test/sales",
            database="sales",
            schema="public",
        )
    except ValueError:
        raised_conn = True
    assert raised_conn

    mod = _load_revision()
    rec = _Recorder()
    mod.op = rec
    mod.upgrade()
    sql = "\n".join(rec.statements)
    all_cols: list[str] = []
    for block in _create_blocks(sql).values():
        all_cols.extend(_columns(block))
    for name in all_cols:
        assert name not in CREDENTIAL_COLUMNS, name
        assert "password" not in name
        assert "secret" not in name
        assert "credential" not in name
        assert "conn_str" not in name
        assert "connection_string" not in name
        assert name not in {"user", "username", "dsn", "token", "api_key"}
    assert "source_kind" in all_cols
    assert "source_host" in all_cols
    assert "source_database" in all_cols
    assert "source_schema" in all_cols
    assert "password" not in sql.lower().split()


def test_fingerprint_stable_when_column_order_changes() -> None:
    a, b = _orders()
    assert schema_fingerprint([a]) == schema_fingerprint([b])
    customer = SchemaTable(
        "customers",
        columns=(SchemaColumn("id", "INT"), SchemaColumn("name", "TEXT")),
        primary_key=("id",),
    )
    assert schema_fingerprint([a, customer]) == schema_fingerprint([customer, a])
    typed = SchemaTable(
        "orders",
        columns=(
            SchemaColumn("id", "integer"),
            SchemaColumn("amount", "numeric(12,2)"),
        ),
        primary_key=("id",),
    )
    assert schema_fingerprint([a]) == schema_fingerprint([typed])
    changed = SchemaTable(
        "orders",
        columns=(
            SchemaColumn("id", "INTEGER"),
            SchemaColumn("amount", "TEXT"),
        ),
        primary_key=("id",),
    )
    assert schema_fingerprint([a]) != schema_fingerprint([changed])
    fk_a = SchemaForeignKey(
        "fk", "lines", ("order_id",), "orders", ("id",)
    )
    fk_b = SchemaForeignKey(
        "fk", "lines", ("order_id",), "orders", ("id",)
    )
    lines = SchemaTable(
        "lines",
        columns=(SchemaColumn("order_id", "INT"), SchemaColumn("id", "INT")),
        primary_key=("id",),
    )
    assert schema_fingerprint([a, lines], (fk_a,)) == schema_fingerprint(
        [lines, a], (fk_b,)
    )
    empty = decide_reconnect([], schema_fingerprint([a]))
    assert empty.action == "create" and empty.status == "active"
