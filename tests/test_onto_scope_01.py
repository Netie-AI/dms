"""ONTO-SCOPE-01 (dms#523): with ``DMS_ONTO_FROM_DATA=1`` a Space's joins are
governed only by ontology items approved for that Space.

Built like ``tests/test_onto_derive_01.py`` and ``tests/test_onto_profile_01.py``:
non-demo Spaces fed by CSV files through ``POST /v1/studio/ingest-batch``,
proposals stored with ``store_proposal`` and approved, Cortex is the recording
fake, the generate leg returns fixed SQL. Every clause is asserted on the
``POST /v1/chat/ask`` envelope after ``assert_envelope_valid`` (rule 10a), with
the rendered text and the rows (rule 10).

No skip, no xfail. Truths are written by hand and re-measured against the lake.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_core.control_plane.onto_store import OntologyStore
from dms_core.control_plane.onto_data import (
    ontology_data_store,
    reset_ontology_data_store,
    store_proposal,
)
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import load_space_ontology, set_ontology_store

import test_onto_derive_01 as od
from test_onto_derive_01 import make_minter
from test_onto_profile_01 import Rig, _csv, make_rig

JOIN_Q = "How many shipments per order status?"


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    reset_ontology_data_store()
    yield
    reset_ontology_data_store()


@pytest.fixture()
def store() -> Iterator[OntologyStore]:
    """A fresh derived-ontology store (ONTO-DERIVE-01), as in ``test_onto_derive_01``."""
    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> Rig:
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    return make_rig(tmp_path, monkeypatch, minter)


# --- fixtures: two Spaces, hand-written rows --------------------------------------

# Six orders, eight shipments. Shipments per status: paid 6, open 2.
ORDER_ROWS = [
    ["O1", "C1", "paid"],
    ["O2", "C1", "open"],
    ["O3", "C2", "paid"],
    ["O4", "C2", "paid"],
    ["O5", "C3", "open"],
    ["O6", "C3", "paid"],
]
SHIP_ROWS = [
    ["S1", "O1", "north"],
    ["S2", "O1", "south"],
    ["S3", "O2", "north"],
    ["S4", "O3", "east"],
    ["S5", "O4", "east"],
    ["S6", "O4", "west"],
    ["S7", "O5", "north"],
    ["S8", "O6", "south"],
]
JOIN_TRUTH = [{"status": "open", "n": 2}, {"status": "paid", "n": 6}]


def space_files(tag: str, orders: list[list[str]] | None = None) -> dict[str, bytes]:
    """One Space's two files. ``tag`` keeps the landed table names apart."""
    return {
        f"{tag}_orders.csv": _csv(["order_id", "customer_id", "status"], orders or ORDER_ROWS),
        f"{tag}_shipments.csv": _csv(["shipment_id", "order_id", "carrier"], SHIP_ROWS),
    }


def join_sql(ships: str, orders: str, on: str = "s.order_id = o.order_id") -> str:
    return (
        f"SELECT o.status, COUNT(*) AS n FROM {ships} s JOIN {orders} o "
        f"ON {on} GROUP BY o.status ORDER BY o.status"
    )


def gen(sql: str) -> dict[str, Any]:
    return {"query_sql": sql, "plan_source": "ontology_plan"}


def link_proposal(
    ships: str,
    orders: str,
    *,
    order_key: list[str] | None = None,
    target_column: str = "order_id",
) -> dict[str, Any]:
    """Two keys and the shipments -> orders link, as an ordinary proposal."""
    return {
        "status": "proposed",
        "terms": [
            {"name": orders, "table": orders, "key": order_key or ["order_id"]},
            {"name": ships, "table": ships, "key": ["shipment_id"]},
        ],
        "declared_fks": [
            {
                "source_table": ships,
                "source_column": "order_id",
                "target_table": orders,
                "target_column": target_column,
            }
        ],
    }


def approve_for(space_id: str | None, body: dict[str, Any]) -> list[dict[str, Any]]:
    """Store ``body`` under a version naming ``space_id`` and approve every item."""
    stored = store_proposal(body, created_by="steward", space_id=space_id)
    assert stored["status"] == "proposed" and stored["items"], stored
    for item in stored["items"]:
        assert ontology_data_store().approve(item["id"], actor="steward") is not None
    return list(stored["items"])


def oracle(lake: Path, sql: str) -> list[dict[str, Any]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [str(c[0]) for c in (cur.description or [])]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()


def reasons(env: dict[str, Any]) -> str:
    return " ".join(str(a) for a in env.get("assumptions") or [])


def assert_abstains(
    env: dict[str, Any], gap: str, rig: Rig, executed_before: int, *, figures: bool = False
) -> str:
    """ABSTAIN naming ``gap``: no row, no value, no figure, nothing executed."""
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert not env.get("drillthrough_token")
    text = str(env.get("text") or "")
    assert gap in text, text
    # E9: nothing ran, so the text states no figure at all.
    if not figures:
        assert not re.search(r"\d", _no_names(text)), text
    assert len(rig.cortex.executed) == executed_before, rig.cortex.executed
    return text


def _no_names(text: str) -> str:
    """``text`` without Space ids and without this file's table names."""
    return re.sub(r"[0-9a-f-]{36}|bronze\.\w+", "", text)


def assert_serves(env: dict[str, Any], truth: list[dict[str, Any]]) -> None:
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert env["rows"] == truth, env["rows"]
    text = str(env.get("text") or "")
    for row in truth:
        assert ", ".join(f"{k}={v}" for k, v in row.items()) in text, (row, text)


# --- clause 1 (step 1, decides the severity): an approved link that fans out -------

# Case a: the approved link points at a parent column that repeats. Each order
# has two lines, so every shipment matches two parent rows.
LINE_ROWS = [
    [f"L{n}", order, status]
    for n, (order, status) in enumerate(
        [(o[0], o[2]) for o in ORDER_ROWS for _ in range(2)], start=1
    )
]
# Case b: the approved parent key has a duplicate row. O1 is stored twice.
DUP_ORDER_ROWS = [*ORDER_ROWS, ["O1", "C9", "open"]]


def _fanout_space(rig: Rig, case: str) -> tuple[str, str, list[dict[str, Any]]]:
    """A Space whose approved link fans out. Returns (space, sql, the wrong rows)."""
    space_id = rig.new_space(f"Fan-out {case}")
    if case == "parent_column_repeats":
        t = rig.ingest(
            space_id,
            {
                "fa_lines.csv": _csv(["line_id", "order_id", "status"], LINE_ROWS),
                "fa_shipments.csv": _csv(["shipment_id", "order_id", "carrier"], SHIP_ROWS),
            },
        )
        parent, ships = t["fa_lines.csv"], t["fa_shipments.csv"]
        body = link_proposal(ships, parent, order_key=["line_id"])
        wrong = [{"status": "open", "n": 4}, {"status": "paid", "n": 12}]
    else:
        t = rig.ingest(space_id, space_files("fb", DUP_ORDER_ROWS))
        parent, ships = t["fb_orders.csv"], t["fb_shipments.csv"]
        body = link_proposal(ships, parent)
        wrong = [{"status": "open", "n": 4}, {"status": "paid", "n": 6}]
    approve_for(space_id, body)
    sql = join_sql(ships, parent)
    # The join does fan out: it runs, and it counts more than the 8 shipments.
    assert oracle(rig.lake, sql) == wrong
    assert sum(r["n"] for r in wrong) > len(SHIP_ROWS)
    return space_id, sql, wrong


@pytest.mark.parametrize("case", ["parent_column_repeats", "parent_key_duplicated"])
@pytest.mark.parametrize("door", ["first_generate", "extract_loop"])
def test_an_approved_link_that_fans_out_abstains(
    case: str, door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    if door == "extract_loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    space_id, sql, wrong = _fanout_space(rig, case)
    env = rig.ask(space_id, JOIN_Q, gen(sql))
    if (case, door) == ("parent_key_duplicated", "first_generate"):
        # Measured on the parent commit (dms#523 step 1): this door names the
        # broken key before the join rule runs. Still no row and no value. The
        # only figures are the two counts that prove the key is not one.
        text = assert_abstains(env, "ontology_unverified: key_unique on ", rig, 0, figures=True)
        assert re.findall(r"\d+", re.sub(r"fb_orders", "", text)) == ["7", "6"], text
    else:
        text = assert_abstains(env, "unverified_join", rig, 0)
    for row in wrong:
        assert f"n={row['n']}" not in text
    assert "paid" not in text and "open" not in text


# --- clauses 2 and 3: an item governs the Space its version names, no other --------


def two_spaces(rig: Rig) -> tuple[str, dict[str, str], str, dict[str, str]]:
    """Space A and Space B, each with its own orders and shipments files."""
    a = rig.new_space("Space A")
    ta = rig.ingest(a, space_files("a"))
    b = rig.new_space("Space B")
    tb = rig.ingest(b, space_files("b"))
    return a, ta, b, tb


@pytest.mark.parametrize("door", ["first_generate", "extract_loop"])
def test_an_item_approved_for_space_a_does_not_join_space_b_tables(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 2. The label lies: the items are filed under A and name B's tables."""
    if door == "extract_loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    a, _ta, b, tb = two_spaces(rig)
    sql = join_sql(tb["b_shipments.csv"], tb["b_orders.csv"])
    assert oracle(rig.lake, sql) == JOIN_TRUTH  # the join is right; it is not B's to use

    bare = rig.ask(b, JOIN_Q, gen(sql))
    bare_text = assert_abstains(bare, "unverified_join", rig, 0)

    approve_for(a, link_proposal(tb["b_shipments.csv"], tb["b_orders.csv"]))
    env = rig.ask(b, JOIN_Q, gen(sql))
    text = assert_abstains(env, "unverified_join", rig, 0)
    # As with no approved item at all: the same refusal, word for word.
    assert text == bare_text
    assert reasons(env) == reasons(bare)


def test_the_same_items_answer_the_space_they_were_approved_for(rig: Rig) -> None:
    """Clause 3. A's items name A's tables and hold in the data: A's join answers."""
    a, ta, b, tb = two_spaces(rig)
    approve_for(a, link_proposal(ta["a_shipments.csv"], ta["a_orders.csv"]))
    sql = join_sql(ta["a_shipments.csv"], ta["a_orders.csv"])
    assert oracle(rig.lake, sql) == JOIN_TRUTH
    env = rig.ask(a, JOIN_Q, gen(sql))
    assert_serves(env, JOIN_TRUTH)
    assert rig.cortex.executed == [sql]
    # B asks the same shape over its own tables and has no item of its own.
    env_b = rig.ask(b, JOIN_Q, gen(join_sql(tb["b_shipments.csv"], tb["b_orders.csv"])))
    assert_abstains(env_b, "unverified_join", rig, 1)


# --- clause 4: a SQL-source Space answers through its derived ontology alone -------

# Every enrollment names school 1 or 2, and 1 and 2 are also district ids. So
# enrollments.school_id -> districts.district_id measures many-to-one with no
# orphan. The source never declared it, and it is meaningless: school 1 is not
# district 1.
CLEAN_ENROLL_ROWS = [["100", "1", "30"], ["101", "2", "25"], ["102", "1", "40"], ["103", "2", "12"]]
UNDECLARED_Q = "How many enrollments per district?"
UNDECLARED_SQL = (
    f"SELECT d.name, COUNT(*) AS n FROM {od.ENROLL} e JOIN {od.DISTRICTS} d "
    "ON e.school_id = d.district_id GROUP BY d.name ORDER BY d.name"
)
UNDECLARED_ROWS = [{"name": "North", "n": 2}, {"name": "South", "n": 2}]
UNDECLARED_LINK = {
    "status": "proposed",
    "terms": [
        {"name": od.ENROLL, "table": od.ENROLL, "key": ["enrollment_id"]},
        {"name": od.DISTRICTS, "table": od.DISTRICTS, "key": ["district_id"]},
    ],
    "declared_fks": [
        {
            "source_table": od.ENROLL,
            "source_column": "school_id",
            "target_table": od.DISTRICTS,
            "target_column": "district_id",
        }
    ],
}


def sql_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    enroll_rows: list[list[str]] | None = None,
) -> od._Space:
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    space = od._space(tmp_path, monkeypatch, minter, enroll_rows=enroll_rows)
    assert load_space_ontology(space.space_id) is not None
    return space


@pytest.mark.parametrize("named", ["this_space", "no_space", "another_space"])
def test_a_sql_source_space_refuses_a_join_only_an_approved_item_offers(
    named: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """Clause 4. The derived ontology wins alone; an item is never merged into it."""
    space = sql_space(tmp_path, monkeypatch, minter, CLEAN_ENROLL_ROWS)
    assert oracle(space.lake, UNDECLARED_SQL) == UNDECLARED_ROWS
    version_space = {
        "this_space": space.space_id,
        "no_space": None,
        "another_space": "5c0be000-0000-4000-8000-000000000523",
    }[named]
    approve_for(version_space, UNDECLARED_LINK)
    env = space.ask(UNDECLARED_Q, gen(UNDECLARED_SQL))
    text = od._assert_abstain(env, "unverified_join")
    assert "North" not in text and "South" not in text
    assert space.cortex.executed == []


def test_a_sql_source_space_still_answers_its_declared_join_under_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    """Clause 4, the other half: the flag no longer replaces the declared ontology.

    Same envelope as with the flag unset
    (``test_onto_derive_01::test_join_over_the_verified_link_answers_with_oracle_rows``).
    """
    space = sql_space(tmp_path, monkeypatch, minter)
    assert oracle(space.lake, od.VERIFIED_SQL) == [{"school_count": 3}]
    env = space.ask(od.VERIFIED_Q, gen(od.VERIFIED_SQL))
    assert_serves(env, [{"school_count": 3}])
    assert space.cortex.executed == [od.VERIFIED_SQL]
    assert {c["ontology"]["source"] for c in space.cortex.insights} == {"space"}
    # The declared link that is broken in the data still abstains, naming it.
    env = space.ask(od.BROKEN_Q, gen(od.BROKEN_SQL))
    od._assert_abstain(env, f"ontology_unverified: fk_orphans on {od.BROKEN_LINK}")
