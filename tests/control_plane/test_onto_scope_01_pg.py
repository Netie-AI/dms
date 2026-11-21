"""ONTO-SCOPE-01 (dms#523) against the Postgres item store.

The CI-runnable twin (``tests/test_onto_scope_01.py``) runs every clause on the
in-memory store. This one turns ``DMS_ONTO_PG`` on over a migrated database and
asserts the Space-scoped read, then clauses 2, 3, 6 and 8 on the same
``POST /v1/chat/ask`` envelopes. Runs wherever the control-plane suite runs
(R-0002).
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dms_core.control_plane.onto_data import (
    PostgresOntologyDataStore,
    ontology_data_store,
    reset_ontology_data_store,
)
from dms_core.control_plane.pg_spaces import PostgresSpaceStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_onto_derive_01 import make_minter  # noqa: E402
from test_onto_profile_01 import Rig, make_rig  # noqa: E402
from test_onto_scope_01 import (  # noqa: E402
    JOIN_Q,
    JOIN_TRUTH,
    ORDER_ROWS,
    PII_Q,
    approve_for,
    assert_abstains,
    assert_serves,
    declared_orders,
    gen,
    join_sql,
    link_proposal,
    reasons,
    space_files,
)


@pytest.fixture()
def pg_rig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    migrated_db: str,
    two_tenants: dict[str, Any],
) -> Iterator[tuple[Rig, str]]:
    tenant = str(two_tenants["alpha"])
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DMS_TENANT_ID", tenant)
    monkeypatch.setenv("DMS_ONTO_PG", "1")
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    reset_ontology_data_store()
    rig = make_rig(tmp_path, monkeypatch, make_minter(monkeypatch))
    assert isinstance(ontology_data_store(), PostgresOntologyDataStore)
    # The version row references ``dms.spaces``, so the Space has to exist there.
    rig.app.state.space_store = PostgresSpaceStore(migrated_db, tenant_id=tenant, role="steward")
    try:
        yield rig, tenant
    finally:
        reset_ontology_data_store()


def _two_spaces(rig: Rig) -> tuple[str, dict[str, str], str, dict[str, str]]:
    tag = uuid.uuid4().hex[:8]
    a = rig.new_space(f"Space A {tag}")
    ta = rig.ingest(a, space_files(f"a{tag}"))
    b = rig.new_space(f"Space B {tag}")
    tb = rig.ingest(b, space_files(f"b{tag}"))
    return (
        a,
        {k.split("_", 1)[1]: v for k, v in ta.items()},
        b,
        {k.split("_", 1)[1]: v for k, v in tb.items()},
    )


def test_pg_reads_approved_items_by_the_space_their_version_names(
    pg_rig: tuple[Rig, str], migrated_db: str
) -> None:
    rig, tenant = pg_rig
    a, _ta, b, tb = _two_spaces(rig)
    body = link_proposal(tb["shipments.csv"], tb["orders.csv"])
    reader = PostgresOntologyDataStore(migrated_db, tenant_id=tenant)
    before_none = {i["id"] for i in reader.approved_items_of_space(None)}

    for_a = {i["id"] for i in approve_for(a, body)}
    for_b = {i["id"] for i in approve_for(b, body)}
    for_none = {i["id"] for i in approve_for(None, body)}
    proposed = ontology_data_store().store_proposal(body, created_by="", space_id=a)["items"]
    revoked = approve_for(a, body)
    for item in revoked:
        assert ontology_data_store().revoke(item["id"], actor="steward") is not None

    def ids(space_id: str | None) -> set[str]:
        return {str(i["id"]) for i in reader.approved_items_of_space(space_id)}

    assert len(for_a) == 3
    assert ids(a) == for_a
    assert ids(b) == for_b
    assert ids(None) - before_none == for_none
    assert not ids(a) & {i["id"] for i in proposed}
    assert not ids(a) & {i["id"] for i in revoked}
    # A Space with no version, and an id that is not a UUID, read nothing.
    # Neither reads the items that name no Space.
    assert ids(str(uuid.uuid4())) == set()
    assert ids("not-a-uuid") == set() and ids("") == set()
    assert {i["status"] for i in reader.approved_items_of_space(a)} == {"approved"}

    # A profile item is never part of this read (dms#515 owns it).
    profiled = rig.profile(a)
    approved_profile = [
        i["id"]
        for i in profiled["items"]
        if rig.client.post(f"/v1/ontology/data/items/{i['id']}/approve").status_code == 200
    ]
    assert approved_profile
    assert ids(a) == for_a


def test_pg_an_item_governs_the_space_its_version_names(pg_rig: tuple[Rig, str]) -> None:
    """Clauses 2 and 3 on the Postgres store."""
    rig, _tenant = pg_rig
    a, ta, b, tb = _two_spaces(rig)
    sql_b = join_sql(tb["shipments.csv"], tb["orders.csv"])
    sql_a = join_sql(ta["shipments.csv"], ta["orders.csv"])
    bare = rig.ask(b, JOIN_Q, gen(sql_b))
    bare_text = assert_abstains(bare, "unverified_join", rig, 0, figures=True)

    approve_for(a, link_proposal(tb["shipments.csv"], tb["orders.csv"]))  # the label lies
    env = rig.ask(b, JOIN_Q, gen(sql_b))
    assert assert_abstains(env, "unverified_join", rig, 0, figures=True) == bare_text
    assert reasons(env) == reasons(bare)

    approve_for(a, link_proposal(ta["shipments.csv"], ta["orders.csv"]))
    assert_serves(rig.ask(a, JOIN_Q, gen(sql_a)), JOIN_TRUTH)
    assert rig.cortex.executed == [sql_a]


def test_pg_pii_stays_install_wide(
    pg_rig: tuple[Rig, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 6 on the Postgres store: a pii item filed under A protects B's table."""
    rig, _tenant = pg_rig
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    a, _ta, b, tb = _two_spaces(rig)
    orders_b = tb["orders.csv"]
    sql = f"SELECT customer_id FROM {orders_b} ORDER BY order_id"
    approve_for(b, declared_orders(orders_b))
    assert_serves(rig.ask(b, PII_Q, gen(sql)), [{"customer_id": r[1]} for r in ORDER_ROWS])
    approve_for(
        a,
        {
            "status": "proposed",
            "pii": [{"name": "customer_ref", "relation": orders_b, "column": "customer_id"}],
        },
    )
    env = rig.ask(b, PII_Q, gen(sql))
    assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == []
    assert "pii" in str(env.get("text") or "") + reasons(env)
    assert len(rig.cortex.executed) == 1


def test_pg_founder_question_an_item_naming_no_space_governs_every_space_today(
    pg_rig: tuple[Rig, str],
) -> None:
    """Clause 8 on the Postgres store. Awaits the founder (dms#523)."""
    rig, _tenant = pg_rig
    _a, _ta, b, tb = _two_spaces(rig)
    approve_for(None, link_proposal(tb["shipments.csv"], tb["orders.csv"]))
    env = rig.ask(b, JOIN_Q, gen(join_sql(tb["shipments.csv"], tb["orders.csv"])))
    assert_serves(env, JOIN_TRUTH)
