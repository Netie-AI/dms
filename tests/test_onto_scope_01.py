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
import test_onto_derive_01 as od
from dms_core.control_plane.onto_data import (
    ontology_data_store,
    reset_ontology_data_store,
    store_proposal,
)
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import load_space_ontology, set_ontology_store
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
    od._assert_abstain(env, f"ontology_unverified: fk_intact on {od.BROKEN_LINK}")


# --- clause 5: a measure or a grain of Space A is not offered in Space B ----------

ORDERS_PER_STATUS = {"open": 2, "paid": 4}
MEASURE_Q = "How many orders per status?"


def measure_proposal(orders: str, *, measure: bool = True, grain: bool = True) -> dict[str, Any]:
    """The orders key, with a declared measure and a grain alias on it."""
    body: dict[str, Any] = {
        "status": "proposed",
        "terms": [{"name": orders, "table": orders, "key": ["order_id"]}],
    }
    if measure:
        body["measures"] = [{"name": "order_count", "grain": orders, "expression": "COUNT(*)"}]
    if grain:
        body["grains"] = [{"name": "order", "object": orders}]
    return body


def plan(group_object: str) -> dict[str, Any]:
    return {
        "query_plan": {"measure": "order_count", "group_by": [[group_object, "status"]]},
        "plan_source": "ontology_plan",
    }


def assert_measure_served(env: dict[str, Any], orders: str) -> None:
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert all(set(r) == {f"{orders}_status", "order_count"} for r in env["rows"]), env["rows"]
    got = {r[f"{orders}_status"]: r["order_count"] for r in env["rows"]}
    assert got == ORDERS_PER_STATUS, env["rows"]
    assert "Found 2 row(s)." in str(env.get("text") or ""), env.get("text")


def customer_view(env: dict[str, Any]) -> dict[str, Any]:
    return {f: env.get(f) for f in ("badge", "abstained", "text", "rows", "values", "assumptions")}


def test_a_measure_approved_for_space_a_is_not_offered_in_space_b(rig: Rig) -> None:
    a, ta, b, tb = two_spaces(rig)
    orders_b = tb["b_orders.csv"]
    bare = rig.ask(b, MEASURE_Q, plan(orders_b))
    assert_abstains(bare, "no_declared_measure", rig, 0)

    approve_for(a, measure_proposal(orders_b))  # filed under A, about B's table
    env = rig.ask(b, MEASURE_Q, plan(orders_b))
    assert_abstains(env, "no_declared_measure", rig, 0)
    assert "no_declared_measure: order_count" in reasons(env)
    assert customer_view(env) == customer_view(bare)
    # The same measure answers the Space it was approved for.
    approve_for(a, measure_proposal(ta["a_orders.csv"]))
    assert_measure_served(rig.ask(a, MEASURE_Q, plan(ta["a_orders.csv"])), ta["a_orders.csv"])


def test_a_grain_approved_for_space_a_is_not_offered_in_space_b(rig: Rig) -> None:
    a, _ta, b, tb = two_spaces(rig)
    orders_b = tb["b_orders.csv"]
    approve_for(b, measure_proposal(orders_b, grain=False))  # B's own key and measure
    assert_measure_served(rig.ask(b, MEASURE_Q, plan(orders_b)), orders_b)
    bare = rig.ask(b, MEASURE_Q, plan("order"))  # B has no grain named "order"
    assert bare["badge"] == "ABSTAIN" and bare["rows"] == [] and bare["values"] == []

    approve_for(a, measure_proposal(orders_b, measure=False))  # A's grain alias onto B's table
    env = rig.ask(b, MEASURE_Q, plan("order"))
    assert customer_view(env) == customer_view(bare)
    # Control: the alias approved for B itself does resolve.
    approve_for(b, measure_proposal(orders_b, measure=False))
    assert_measure_served(rig.ask(b, MEASURE_Q, plan("order")), orders_b)


# --- clause 6: a pii protection is install-wide ------------------------------------

PII_Q = "List the customer of every order"


def declared_orders(orders: str) -> dict[str, Any]:
    """The orders key with ``customer_id`` and ``status`` declared as attributes."""
    return {
        "status": "proposed",
        "terms": [
            {
                "name": orders,
                "table": orders,
                "key": ["order_id"],
                "attributes": [{"source_column": "customer_id"}, {"source_column": "status"}],
            }
        ],
    }


@pytest.mark.parametrize("named", ["another_space", "no_space", "this_space"])
def test_a_pii_item_protects_its_table_whatever_space_its_version_names(
    named: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    a, _ta, b, tb = two_spaces(rig)
    orders_b = tb["b_orders.csv"]
    sql = f"SELECT customer_id FROM {orders_b} ORDER BY order_id"
    approve_for(b, declared_orders(orders_b))
    # Without the protection the column is served: the test is not vacuous.
    assert_serves(rig.ask(b, PII_Q, gen(sql)), [{"customer_id": r[1]} for r in ORDER_ROWS])

    pii = {
        "status": "proposed",
        "pii": [{"name": "customer_ref", "relation": orders_b, "column": "customer_id"}],
    }
    approve_for({"another_space": a, "no_space": None, "this_space": b}[named], pii)
    env = rig.ask(b, PII_Q, gen(sql))
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert "pii" in str(env.get("text") or "") + reasons(env), (env.get("text"), reasons(env))
    assert "C1" not in str(env.get("text") or "")
    assert len(rig.cortex.executed) == 1  # only the control above ran
    # A column the pii item does not name still answers.
    ok = rig.ask(b, "List order status", gen(f"SELECT status FROM {orders_b} ORDER BY order_id"))
    assert_serves(ok, [{"status": r[2]} for r in ORDER_ROWS])


# --- clause 7: every reader on the ask path builds the ontology for the asking Space


def test_def_validate_reads_the_asking_space_items_only(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reader ``def_validate.validate_generated_sql``. Fails on the parent by serving."""
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    a, _ta, b, tb = two_spaces(rig)
    orders_b = tb["b_orders.csv"]
    sql = f"SELECT COUNT(*) AS n FROM {orders_b}"
    bare = rig.ask(b, "How many orders?", gen(sql))
    assert bare["badge"] == "ABSTAIN" and bare["rows"] == [] and bare["values"] == []
    assert "not_in_ontology" in str(bare.get("text") or "")

    approve_for(a, declared_orders(orders_b))  # filed under A, about B's table
    env = rig.ask(b, "How many orders?", gen(sql))
    assert customer_view(env) == customer_view(bare)
    assert rig.cortex.executed == []
    # Control: the same item approved for B lets the count through.
    approve_for(b, declared_orders(orders_b))
    assert_serves(rig.ask(b, "How many orders?", gen(sql)), [{"n": 6}])


def test_grain_ai_offers_the_asking_space_grains_only(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reader ``grain_ai.approved_grain_names`` through ``map_question_to_grains``."""
    import json

    import dms_executor.grain_ai as grain_ai

    monkeypatch.setenv("DMS_GRAIN_AI", "1")
    a, ta, b, tb = two_spaces(rig)
    approve_for(a, measure_proposal(tb["b_orders.csv"]))  # grain "order", filed under A
    sent: list[list[str]] = []

    def _transport(body: dict[str, Any]) -> dict[str, Any]:
        user = json.loads(body["messages"][1]["content"])
        sent.append(list(user["candidates"]))
        return {"grains": ["order"], "evidence": [], "confidence": 0.99, "reading": "orders"}

    monkeypatch.setattr(grain_ai, "default_transport", _transport)
    env = rig.ask(b, MEASURE_Q, plan(tb["b_orders.csv"]))
    assert sent, "the grain reading was not reached from POST /v1/chat/ask"
    assert all("order" not in names and tb["b_orders.csv"] not in names for names in sent), sent
    assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == []
    # Control: Space A is offered the grain its own version holds.
    sent.clear()
    rig.ask(a, MEASURE_Q, plan(ta["a_orders.csv"]))
    assert sent and all("order" in names for names in sent), sent


def test_load_verified_ontology_builds_for_the_asking_space(rig: Rig) -> None:
    """Reader ``generative_ask.load_verified_ontology`` (the flag branch).

    ``POST /v1/chat/ask`` cannot reach this branch under the flag: the executor
    always hands ``maybe_generative_ask`` an ontology, so ``onto is None`` is
    false there. It is asserted on the function itself.
    """
    import dms_executor.generative_ask as gen_mod

    a, _ta, b, tb = two_spaces(rig)
    approve_for(a, link_proposal(tb["b_shipments.csv"], tb["b_orders.csv"]))
    for_b = gen_mod.load_verified_ontology(rig.lake, space_id=b)
    assert for_b is not None and for_b.objects == {} and for_b.links == {}
    for_a = gen_mod.load_verified_ontology(rig.lake, space_id=a)
    assert for_a is not None and set(for_a.objects) == set(tb.values())


def _contract_join(rig: Rig, monkeypatch: pytest.MonkeyPatch, sql: str) -> None:
    from cortex_client.models import AskResponse

    def _ask(req: Any) -> AskResponse:
        rig.cortex.asks.append(req)
        return AskResponse(
            answer="There are 6 paid shipments and 2 open ones.",
            abstained=False,
            badge="generated",
            route="generated",
            sql_used=sql,
            rows=list(JOIN_TRUTH),
        )

    monkeypatch.setattr(rig.cortex, "ask", _ask)


@pytest.mark.parametrize("named", ["another_space", "this_space", "no_space"])
def test_contract_ask_join_stays_fail_closed_under_the_flag(
    named: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fifth reader (F-TBD-38). Unchanged by this ticket, and recorded.

    Under the flag the contract-ask guard holds no ontology (dms#520, pinned by
    ``test_connect_ask_03``), so a join in ``sql_used`` abstains whatever items
    exist. No item of any Space can make this door serve a join.
    """
    a, _ta, b, tb = two_spaces(rig)
    sql = join_sql(tb["b_shipments.csv"], tb["b_orders.csv"])
    approve_for(
        {"another_space": a, "this_space": b, "no_space": None}[named],
        link_proposal(tb["b_shipments.csv"], tb["b_orders.csv"]),
    )
    _contract_join(rig, monkeypatch, sql)
    env = rig.ask(b, JOIN_Q, {})
    assert rig.cortex.asks, "the contract ask was not reached"
    text = assert_abstains(env, "unverified_join", rig, 0)
    assert "unverified_join:no_ontology" in reasons(env) + text
    assert "paid" not in text


# --- clause 8: AWAITS THE FOUNDER -------------------------------------------------


def test_founder_question_what_does_an_approved_item_naming_no_space_govern(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dms#523 clause 8, not answered. Today: every Space, as on the parent commit.

    Option 1: demo Spaces only. Option 2: nothing until assigned. Option 3:
    every Space granted the tables (today). The answer lives in one function,
    ``onto_scope.unassigned_items_govern``. Whoever answers it changes that
    function and this test, nothing else.
    """
    import dms_executor.onto_scope as onto_scope

    _a, _ta, b, tb = two_spaces(rig)
    sql = join_sql(tb["b_shipments.csv"], tb["b_orders.csv"])
    approve_for(None, link_proposal(tb["b_shipments.csv"], tb["b_orders.csv"]))
    assert onto_scope.unassigned_items_govern(b) is True
    assert_serves(rig.ask(b, JOIN_Q, gen(sql)), JOIN_TRUTH)
    # The one function decides: answer it "no" and the same ask abstains.
    monkeypatch.setattr(onto_scope, "unassigned_items_govern", lambda _space: False)
    assert_abstains(rig.ask(b, JOIN_Q, gen(sql)), "unverified_join", rig, 1)


# --- clause 9: a demo Space is unchanged -------------------------------------------

DEMO_Q = "How many suppliers per country?"
DEMO_PLAN = {
    "query_plan": {"measure": "supplier_total", "group_by": [["supplier", "country"]]},
    "plan_source": "ontology_plan",
}
DEMO_BODY = {
    "status": "proposed",
    "terms": [{"name": "supplier", "table": "suppliers", "key": ["supplier_id"]}],
    "measures": [{"name": "supplier_total", "grain": "supplier", "expression": "COUNT(*)"}],
}
DEMO_TRUTH = {"MY": 2, "SG": 1, "TH": 1}


def ask_demo(rig: Rig) -> dict[str, Any]:
    from dms_executor.envelope import assert_envelope_valid

    rig.cortex.payload = dict(DEMO_PLAN)
    r = rig.client.post("/v1/chat/ask", json={"question": DEMO_Q, "session_id": "ses_demo"})
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    return env


@pytest.mark.parametrize("named", ["no_space", "a_customer_space"])
def test_a_demo_space_reads_approved_items_as_on_the_parent(named: str, rig: Rig) -> None:
    """Clause 9. Measured on the parent: a demo ask reads every approved item."""
    counted = oracle(rig.lake, "SELECT country, COUNT(*) AS n FROM suppliers GROUP BY country")
    assert {r["country"]: r["n"] for r in counted} == DEMO_TRUTH
    bare = ask_demo(rig)
    assert bare["badge"] == "ABSTAIN" and bare["rows"] == []
    approve_for(None if named == "no_space" else rig.new_space("Customer"), DEMO_BODY)
    env = ask_demo(rig)
    assert env["badge"] == "L2_VALIDATED" and env["abstained"] is False
    assert {r["supplier_country"]: r["supplier_total"] for r in env["rows"]} == DEMO_TRUTH
    assert "Found 3 row(s)." in str(env.get("text") or "")


# --- clause 10: flag unset ----------------------------------------------------------


def flag_unset(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the flag. The generate leg returns the payload whatever catalog it is sent
    (the recording fake parses a Space catalog, and a flag-off file Space sends none)."""
    monkeypatch.delenv("DMS_ONTO_FROM_DATA", raising=False)
    monkeypatch.setattr(
        rig.cortex, "compute_insights", lambda *_a, **_k: dict(rig.cortex.payload), raising=True
    )


@pytest.mark.parametrize("named", ["another_space", "this_space", "no_space"])
def test_flag_unset_no_item_is_read_and_the_join_abstains_no_ontology(
    named: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    flag_unset(rig, monkeypatch)
    a, _ta, b, tb = two_spaces(rig)
    sql = join_sql(tb["b_shipments.csv"], tb["b_orders.csv"])
    bare = rig.ask(b, JOIN_Q, gen(sql))
    assert_abstains(bare, "unverified_join", rig, 0)
    assert "unverified_join:no_ontology" in reasons(bare)
    approve_for(
        {"another_space": a, "this_space": b, "no_space": None}[named],
        link_proposal(tb["b_shipments.csv"], tb["b_orders.csv"]),
    )
    env = rig.ask(b, JOIN_Q, gen(sql))
    assert customer_view(env) == customer_view(bare)


def test_flag_unset_def_validate_reads_as_on_the_parent(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 10, recorded and not liked: with ``DMS_ONTO_FROM_DATA`` unset,
    ``DMS_DEF_VALIDATE=1`` still reads every approved item, as on the parent.

    Scoping it there is a behaviour change with the flag unset, which clause 10
    forbids. It is listed as open in the PR.
    """
    flag_unset(rig, monkeypatch)
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    a, _ta, b, tb = two_spaces(rig)
    approve_for(a, declared_orders(tb["b_orders.csv"]))
    env = rig.ask(b, "How many orders?", gen(f"SELECT COUNT(*) AS n FROM {tb['b_orders.csv']}"))
    assert_serves(env, [{"n": 6}])


# --- clause 11: a profile item is still not read here ------------------------------


def test_an_approved_profile_item_is_not_read_by_the_scoped_ontology(rig: Rig) -> None:
    """dms#515 owns that read. The Space's own approved profile link changes nothing."""
    a, ta, _b, _tb = two_spaces(rig)
    sql = join_sql(ta["a_shipments.csv"], ta["a_orders.csv"])
    bare = rig.ask(a, JOIN_Q, gen(sql))
    assert_abstains(bare, "unverified_join", rig, 0)
    body = rig.profile(a)
    approved = [
        i["id"]
        for i in body["items"]
        if rig.client.post(f"/v1/ontology/data/items/{i['id']}/approve").status_code == 200
    ]
    assert approved and any(i["kind"] == "link" for i in body["items"])
    store = ontology_data_store()
    assert store.space_profile_items(a, "approved")
    assert store.approved_items_of_space(a) == []
    assert customer_view(rig.ask(a, JOIN_Q, gen(sql))) == customer_view(bare)


# --- clause 12: an item store that cannot be read ------------------------------------


def _boom(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("item store down: host db-7 password hunter2")


@pytest.mark.parametrize("read", ["approved_items_of_space", "approved_items"])
def test_an_unreadable_item_store_abstains_naming_it(
    read: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, ta, _b, _tb = two_spaces(rig)
    approve_for(a, link_proposal(ta["a_shipments.csv"], ta["a_orders.csv"]))
    sql = join_sql(ta["a_shipments.csv"], ta["a_orders.csv"])
    assert_serves(rig.ask(a, JOIN_Q, gen(sql)), JOIN_TRUTH)
    monkeypatch.setattr(ontology_data_store(), read, _boom)
    for space_id, question, payload in ((a, JOIN_Q, gen(sql)), (None, DEMO_Q, dict(DEMO_PLAN))):
        if space_id is None and read != "approved_items":
            continue
        if space_id is None:
            env = ask_demo(rig)
        else:
            env = rig.ask(space_id, question, payload)
        text = assert_abstains(env, "ontology_store_unavailable", rig, 1)
        assert "hunter2" not in text and "db-7" not in text
        assert "hunter2" not in reasons(env)


def test_an_unreadable_derived_store_abstains_under_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    space = sql_space(tmp_path, monkeypatch, minter)
    monkeypatch.setattr(store, "load_ontology", _boom, raising=False)
    monkeypatch.setattr("dms_executor.onto_scope.load_space_ontology", _boom)
    env = space.ask(od.VERIFIED_Q, gen(od.VERIFIED_SQL))
    od._assert_abstain(env, "ontology_store_unavailable")
    assert space.cortex.executed == []


# --- the store read (memory). Postgres: tests/control_plane/test_onto_scope_01_pg.py -


def test_store_reads_approved_items_by_the_space_their_version_names() -> None:
    store = ontology_data_store()
    body = link_proposal("bronze.s", "bronze.o")
    for_a = {i["id"] for i in approve_for("space-a", body)}
    for_b = {i["id"] for i in approve_for("space-b", body)}
    for_none = {i["id"] for i in approve_for(None, body)}
    proposed = store_proposal(body, created_by="steward", space_id="space-a")["items"]
    revoked = approve_for("space-a", body)
    for item in revoked:
        store.revoke(item["id"], actor="steward")

    def ids(space_id: str | None) -> set[str]:
        return {str(i["id"]) for i in store.approved_items_of_space(space_id)}

    assert ids("space-a") == for_a
    assert ids("space-b") == for_b
    assert ids(None) == for_none
    assert ids("space-c") == set() and ids("") == set() and ids("  ") == set()
    assert not ids("space-a") & {i["id"] for i in proposed}
    assert {i["status"] for i in store.approved_items_of_space("space-a")} == {"approved"}
    assert len(for_a) == 3 and not (for_a & for_b) and not (for_a & for_none)


# --- adversary round (three attackers, none of them the author) --------------------


def _catalogs(rig: Rig) -> str:
    import json

    return json.dumps([c.get("ontology") for c in rig.cortex.insights], sort_keys=True, default=str)


@pytest.mark.parametrize("door", ["first_generate", "extract_loop"])
@pytest.mark.parametrize("flag", ["", "DMS_DEF_VALIDATE", "DMS_GRAIN_AI", "DMS_SCHEMA_CONTEXT"])
def test_adversary_every_kind_of_item_filed_under_a_leaves_b_unchanged(
    door: str, flag: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attacker 1. Link, measure, grain, declared columns and a fanning-out link,
    all filed under A and all about B's tables: B's envelope and the catalog
    sent to Cortex for B are what they are with no item at all."""
    if door == "extract_loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    if flag:
        monkeypatch.setenv(flag, "1")
    a, _ta, b, tb = two_spaces(rig)
    orders_b, ships_b = tb["b_orders.csv"], tb["b_shipments.csv"]
    sql = join_sql(ships_b, orders_b)
    # Warm-up ask: since CATALOG-SCHEMA-01 (#518) B's bronze columns are listed,
    # and a column line gains ``distinct=`` once the column index is ready,
    # which happens between B's first and second ask with no item filed at
    # all. The baseline is B's second ask, so only A's items can differ.
    rig.ask(b, JOIN_Q, gen(sql))
    rig.cortex.insights.clear()
    bare = rig.ask(b, JOIN_Q, gen(sql))
    assert bare["badge"] == "ABSTAIN" and bare["rows"] == [] and bare["values"] == []
    bare_catalogs = _catalogs(rig)

    approve_for(a, link_proposal(ships_b, orders_b))
    approve_for(a, measure_proposal(orders_b))
    approve_for(a, declared_orders(orders_b))
    approve_for(a, link_proposal(ships_b, orders_b, order_key=["customer_id"]))
    rig.cortex.insights.clear()
    env = rig.ask(b, JOIN_Q, gen(sql))
    assert customer_view(env) == customer_view(bare)
    assert _catalogs(rig) == bare_catalogs
    assert rig.cortex.executed == []


def test_adversary_a_same_named_object_of_a_does_not_rekey_b_own_link(rig: Rig) -> None:
    """Attacker 1. B serves over its own link. A then files an object with the
    same name on the same table, keyed on a column that repeats."""
    a, _ta, b, tb = two_spaces(rig)
    orders_b, ships_b = tb["b_orders.csv"], tb["b_shipments.csv"]
    sql = join_sql(ships_b, orders_b)
    approve_for(b, link_proposal(ships_b, orders_b))
    before = rig.ask(b, JOIN_Q, gen(sql))
    assert_serves(before, JOIN_TRUTH)
    approve_for(a, link_proposal(ships_b, orders_b, order_key=["customer_id"]))
    after = rig.ask(b, JOIN_Q, gen(sql))
    assert customer_view(after) == customer_view(before)


def test_adversary_a_good_link_of_a_does_not_repair_b_fanning_out_link(rig: Rig) -> None:
    """Attacker 1. B's own approved key is not a key. A's correct link is not B's."""
    a = rig.new_space("Space A")
    b = rig.new_space("Space B")
    tb = rig.ingest(b, space_files("b", DUP_ORDER_ROWS))
    orders_b, ships_b = tb["b_orders.csv"], tb["b_shipments.csv"]
    sql = join_sql(ships_b, orders_b)
    approve_for(b, link_proposal(ships_b, orders_b))
    bare = rig.ask(b, JOIN_Q, gen(sql))
    assert bare["badge"] == "ABSTAIN" and bare["rows"] == [] and bare["values"] == []
    approve_for(a, link_proposal(ships_b, orders_b, order_key=["order_id", "customer_id"]))
    assert customer_view(rig.ask(b, JOIN_Q, gen(sql))) == customer_view(bare)
    assert rig.cortex.executed == []


@pytest.mark.parametrize("read", ["approved_items", "approved_items_of_space"])
def test_adversary_unreadable_item_store_in_def_validate_abstains(
    read: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """Attacker 2. A SQL-source Space never reads the item store in the executor,
    so ``def_validate`` was the first to meet the failure and it surfaced as a
    503 carrying the exception text. Now a named ABSTAIN, and nothing runs."""
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    space = sql_space(tmp_path, monkeypatch, minter)
    monkeypatch.setattr(ontology_data_store(), read, _boom)
    env = space.ask(od.VERIFIED_Q, gen(od.VERIFIED_SQL))
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert "ontology_store_unavailable" in reasons(env)
    assert "hunter2" not in str(env) and "db-7" not in str(env)
    assert space.cortex.executed == []


@pytest.mark.parametrize("named", ["another_space", "no_space", "this_space"])
def test_adversary_pii_protects_a_sql_source_space_too(
    named: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """Attacker 2. The derived ontology answers alone, and a pii item still applies."""
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    space = sql_space(tmp_path, monkeypatch, minter)
    version_space = {
        "another_space": "5c0be000-0000-4000-8000-000000000523",
        "no_space": None,
        "this_space": space.space_id,
    }[named]
    approve_for(
        version_space,
        {
            "status": "proposed",
            "pii": [{"name": "school_name", "relation": od.SCHOOLS, "column": "name"}],
        },
    )
    env = space.ask("List school names", gen(f"SELECT name FROM {od.SCHOOLS} ORDER BY name"))
    assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == []
    assert "pii" in reasons(env) + str(env.get("text") or "")
    assert "Alder" not in str(env.get("text") or "")
    assert space.cortex.executed == []


def test_adversary_grain_reading_abstains_when_the_item_store_is_down(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attacker 2's class, the second reader: ``map_question_to_grains``."""
    import dms_executor.grain_ai as grain_ai

    monkeypatch.setattr(ontology_data_store(), "approved_items", _boom)
    monkeypatch.setattr(ontology_data_store(), "approved_items_of_space", _boom)
    out = grain_ai.map_question_to_grains(MEASURE_Q, space_id=rig.new_space("S"), session_id="s")
    assert out["abstained"] is True and out["rows"] == [] and out["reconfirm"] is None
    assert "ontology_store_unavailable" in str(out.get("assumptions"))
    assert "hunter2" not in str(out)


# --- adversary round 2 (Sonnet, not the author): cases that held --------------------


def _secret_grain_proposal(table: str) -> dict[str, Any]:
    return {
        "status": "proposed",
        "terms": [{"name": table, "table": table, "key": ["order_id"]}],
        "grains": [{"name": "zebrasecret", "object": table}],
        "measures": [{"name": "zz_measure", "grain": table, "expression": "COUNT(*)"}],
    }


@pytest.mark.parametrize("flag", ["DMS_GRAIN_AI", "DMS_SCHEMA_CONTEXT", "DMS_DEF_VALIDATE"])
def test_adversary2_a_grain_and_measure_name_never_reach_b_catalog_or_text(
    flag: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attacker 3. A's distinctive grain and measure names are in neither the
    insights payload sent for B nor B's envelope, and asking B for A's grain
    by name does not serve."""
    import json

    monkeypatch.setenv(flag, "1")
    a, ta, b, tb = two_spaces(rig)
    approve_for(a, _secret_grain_proposal(ta["a_orders.csv"]))
    rig.cortex.insights.clear()
    env = rig.ask(b, MEASURE_Q, plan(tb["b_orders.csv"]))
    blob = json.dumps([rig.cortex.insights, env], default=str)
    assert "zebrasecret" not in blob and "zz_measure" not in blob
    asked = rig.ask(b, MEASURE_Q, plan("zebrasecret"))
    assert asked["badge"] == "ABSTAIN" and asked["rows"] == [] and asked["values"] == []


def test_adversary2_same_file_names_in_two_spaces_do_not_share_an_item(rig: Rig) -> None:
    """Attacker 3. Two Spaces ingest identically named files; A's link on that
    table is not read when B asks."""
    a = rig.new_space("A same names")
    ta = rig.ingest(a, space_files("x"))
    b = rig.new_space("B same names")
    tb = rig.ingest(b, space_files("x"))
    approve_for(a, link_proposal(ta["x_shipments.csv"], ta["x_orders.csv"]))
    env = rig.ask(b, JOIN_Q, gen(join_sql(tb["x_shipments.csv"], tb["x_orders.csv"])))
    assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == []
