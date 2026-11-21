"""ONTO-PROFILE-02 (dms#515): a file Space's approved keys and links reach the
rule 16 join check, measured again at every ask.

Built like ``tests/test_onto_derive_01.py`` and ``tests/test_onto_profile_01.py``:
a non-demo Space fed by CSV files through ``POST /v1/studio/ingest-batch``,
profiled through ``POST /v1/ontology/data/profile``, items approved through
``POST /v1/ontology/data/items/{id}/approve``. Cortex is the recording fake and
the generate leg returns fixed SQL. Every clause is asserted on the
``POST /v1/chat/ask`` envelope after ``assert_envelope_valid`` (rule 10a), with
the rendered text and the rows against a truth written by hand and re-measured
against the lake (rule 10).

No skip, no xfail.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
import test_onto_derive_01 as od
from cortex_client.models import AskResponse
from dms_core.control_plane.onto_data import (
    ontology_data_store,
    reset_ontology_data_store,
    store_proposal,
)
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import load_space_ontology, set_ontology_store
from test_onto_derive_01 import make_minter
from test_onto_profile_01 import (
    ENVELOPE_FIELDS,
    Rig,
    _approve,
    _csv,
    _revoke,
    gate_allows,
    make_rig,
)

DOORS = ["first_generate", "extract_loop", "contract_ask"]
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
    """A fresh derived-ontology store (ONTO-DERIVE-01)."""
    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> Rig:
    monkeypatch.delenv("DMS_ONTO_FROM_DATA", raising=False)
    built = make_rig(tmp_path, monkeypatch, minter)
    cortex = built.cortex

    def _insights(question: str, **kw: Any) -> dict[str, Any]:
        # ``_RecordingCortex`` asserts every Insights body names its source. A
        # file Space with no ontology is asked with the demo retrieve context,
        # which names none, and the assertion would be swallowed as a compute
        # miss. Record the body as sent and answer; the envelope is what counts.
        cortex.insights.append({"question": question, **kw})
        return dict(cortex.payload)

    monkeypatch.setattr(cortex, "compute_insights", _insights)
    return built


# --- the fixture: a keyed child, a keyed parent, one clean reference, a keyless table ---

# Six orders (``order_id`` is the only unique column).
ORDER_ROWS = [
    ["O1", "C1", "paid"],
    ["O2", "C1", "open"],
    ["O3", "C2", "paid"],
    ["O4", "C2", "paid"],
    ["O5", "C3", "open"],
    ["O6", "C3", "paid"],
]
# Eight shipments (``shipment_id`` unique). Every ``order_id`` exists in orders.
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
# Seven order lines. The first row is stored twice, so no column, no pair and
# not even the whole row identifies a line: the table is keyless.
LINE_ROWS = [
    ["O1", "bolt", "x2"],
    ["O1", "bolt", "x2"],
    ["O2", "nut", "x1"],
    ["O3", "bolt", "x5"],
    ["O4", "nut", "x1"],
    ["O5", "gear", "x3"],
    ["O6", "gear", "x3"],
]
# Three customers. Nothing between orders and customers is ever approved here.
CUSTOMER_ROWS = [["C1", "Ipoh"], ["C2", "Melaka"], ["C3", "Ipoh"]]

#: Shipments per order status over the approved link. Written by hand.
JOIN_TRUTH = [{"status": "open", "n": 2}, {"status": "paid", "n": 6}]
LINES_Q = "How many order lines are there?"
LINES_TRUTH = [{"n": 7}]


def space_files(tag: str) -> dict[str, bytes]:
    """The four files. ``tag`` keeps two Spaces' landed table names apart."""
    return {
        f"{tag}_orders.csv": _csv(["order_id", "customer_id", "status"], ORDER_ROWS),
        f"{tag}_shipments.csv": _csv(["shipment_id", "order_id", "carrier"], SHIP_ROWS),
        f"{tag}_lines.csv": _csv(["order_id", "sku", "pack"], LINE_ROWS),
        f"{tag}_customers.csv": _csv(["customer_id", "city"], CUSTOMER_ROWS),
    }


class Space:
    """One profiled file Space and the three profile items the ticket is about."""

    def __init__(self, rig: Rig, tag: str = "pf", space_id: str | None = None) -> None:
        self.rig = rig
        self.tag = tag
        self.id = space_id or rig.new_space(f"Files {tag}")
        t = rig.ingest(self.id, space_files(tag))
        self.orders = t[f"{tag}_orders.csv"]
        self.ships = t[f"{tag}_shipments.csv"]
        self.lines = t[f"{tag}_lines.csv"]
        self.customers = t[f"{tag}_customers.csv"]
        self.items = rig.profile(self.id)["items"]
        self.order_key = self.key_item(self.orders, ["order_id"])
        self.ship_key = self.key_item(self.ships, ["shipment_id"])
        self.customer_key = self.key_item(self.customers, ["customer_id"])
        self.link = self.link_item(self.ships, "order_id", self.orders, "order_id")

    def key_item(self, table: str, key: list[str]) -> str:
        (found,) = [
            i["id"]
            for i in self.items
            if i["kind"] == "object"
            and i["payload"]["relation"] == table
            and i["payload"]["key"] == key
        ]
        return str(found)

    def link_item(self, child: str, child_col: str, parent: str, parent_col: str) -> str:
        (found,) = [
            i["id"]
            for i in self.items
            if i["kind"] == "link"
            and (i["payload"]["from_object"], i["payload"]["from_columns"]) == (child, [child_col])
            and (i["payload"]["to_object"], i["payload"]["to_columns"]) == (parent, [parent_col])
        ]
        return str(found)

    def approve(self, *item_ids: str) -> None:
        for item_id in item_ids:
            r = _approve(self.rig, item_id)
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "approved"

    def revoke(self, *item_ids: str) -> None:
        for item_id in item_ids:
            r = _revoke(self.rig, item_id)
            assert r.status_code == 200, r.text

    def approve_the_link(self) -> None:
        """The child key, the parent key and the link between them (clause 1)."""
        self.approve(self.ship_key, self.order_key, self.link)

    @property
    def join_sql(self) -> str:
        return join_sql(self.ships, self.orders)

    @property
    def lines_sql(self) -> str:
        return f"SELECT COUNT(*) AS n FROM {self.lines}"

    def run(self, sql: str) -> None:
        """Change the landed rows after approval, as a later upload would."""
        con = duckdb.connect(str(self.rig.lake))
        try:
            con.execute(sql)
        finally:
            con.close()


def join_sql(ships: str, orders: str, on: str = "s.order_id = o.order_id") -> str:
    return (
        f"SELECT o.status, COUNT(*) AS n FROM {ships} s JOIN {orders} o "
        f"ON {on} GROUP BY o.status ORDER BY o.status"
    )


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


def set_door(door: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if door == "extract_loop":
        monkeypatch.setenv("DMS_CLOOP_B", "1")
        monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")


def ask(
    rig: Rig,
    space_id: str,
    question: str,
    sql: str,
    door: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    reaches_cortex: bool = True,
) -> dict[str, Any]:
    """Post the question with ``sql`` arriving through ``door``.

    ``first_generate`` and ``extract_loop``: the generate leg returns ``sql``.
    ``contract_ask``: generation returns nothing and the contract ask answers
    with ``sql`` as its ``sql_used`` and the rows that SQL really returns.
    """
    if door != "contract_ask":
        return rig.ask(space_id, question, {"query_sql": sql, "plan_source": "ontology_plan"})
    try:
        rows = oracle(rig.lake, sql)
    except duckdb.Error:
        rows = [{"n": 1}]
    reached = len(rig.cortex.asks)

    def _ask(req: Any) -> AskResponse:
        rig.cortex.asks.append(req)
        return AskResponse(
            answer="Cortex answered from its own SQL.",
            abstained=False,
            badge="generated",
            route="generated",
            sql_used=sql,
            rows=rows,
        )

    monkeypatch.setattr(rig.cortex, "ask", _ask)
    env = rig.ask(space_id, question, {})
    assert len(rig.cortex.asks) == reached + int(reaches_cortex), "contract ask reached or not"
    return env


def view(env: dict[str, Any]) -> dict[str, Any]:
    """The fields a customer reads. Two envelopes are 'the same' when these are."""
    return {f: env.get(f) for f in ENVELOPE_FIELDS}


def _no_names(text: str) -> str:
    """``text`` without Space ids and without this file's table names."""
    return re.sub(r"[0-9a-f-]{36}|bronze\.\w+", "", text)


def assert_abstains(env: dict[str, Any], gap: str, *, figures: bool = False) -> str:
    """ABSTAIN naming ``gap``: no row, no value, and (E9) no figure in the text."""
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert not env.get("drillthrough_token")
    text = str(env.get("text") or "")
    assert gap in text, text
    if not figures:
        assert not re.search(r"\d", _no_names(text)), text
    return text


def assert_serves(env: dict[str, Any], truth: list[dict[str, Any]], door: str) -> None:
    assert env["abstained"] is False, (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["badge"] != "ABSTAIN"
    assert env["rows"] == truth, env["rows"]
    if door != "contract_ask":
        # DMS executed the SQL and rendered the rows itself.
        assert env["badge"] == "L2_VALIDATED", env["badge"]
        text = str(env.get("text") or "")
        for row in truth:
            assert ", ".join(f"{k}={v}" for k, v in row.items()) in text, (row, text)


# --- step 1 / clause 13: the gap this ticket closes -----------------------------------


@pytest.mark.parametrize("door", DOORS)
def test_an_approved_link_that_holds_answers_the_join(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 1. Fails on the parent commit: an approved link was read by nothing."""
    set_door(door, monkeypatch)
    space = Space(rig)
    assert oracle(rig.lake, space.join_sql) == JOIN_TRUTH
    bare = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    assert_abstains(bare, "unverified_join:no_ontology")
    assert rig.cortex.executed == []

    space.approve_the_link()
    env = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    assert_serves(env, JOIN_TRUTH, door)
    if door != "contract_ask":
        assert rig.cortex.executed == [space.join_sql]


# --- shared pieces for the clauses below ------------------------------------------------

GAP = "unverified_join"
#: The first-generate door names a failed measurement ``ontology_unverified`` and
#: puts the counts in the text; the other two doors say ``unverified_join``. The
#: first is the wording #523 pinned, because generative_ask.py is not edited here.
BROKEN_GAP = {
    "first_generate": "ontology_unverified",
    "extract_loop": GAP,
    "contract_ask": GAP,
}

#: change after approval -> the statement that undoes it
BREAKS = {
    "orphan_row": (
        "INSERT INTO {ships} (shipment_id, order_id, carrier) VALUES ('S9', 'O99', 'north')",
        "DELETE FROM {ships} WHERE shipment_id = 'S9'",
    ),
    "duplicate_parent_key": (
        "INSERT INTO {orders} (order_id, customer_id, status) VALUES ('O1', 'C9', 'open')",
        "DELETE FROM {orders} WHERE customer_id = 'C9'",
    ),
    "null_parent_key": (
        "INSERT INTO {orders} (order_id, customer_id, status) VALUES (NULL, 'C9', 'open')",
        "DELETE FROM {orders} WHERE customer_id = 'C9'",
    ),
    "duplicate_child_key": (
        "INSERT INTO {ships} (shipment_id, order_id, carrier) VALUES ('S1', 'O2', 'west')",
        "DELETE FROM {ships} WHERE shipment_id = 'S1' AND order_id = 'O2'",
    ),
    "parent_row_removed": (
        "DELETE FROM {orders} WHERE order_id = 'O6'",
        "INSERT INTO {orders} (order_id, customer_id, status) VALUES ('O6', 'C3', 'paid')",
    ),
}


def change(space: Space, statement: str) -> None:
    space.run(statement.format(ships=space.ships, orders=space.orders))


# --- clause 2: a proposed, revoked or half-approved set is not read -----------------------


def _only_parent_and_link(s: Space) -> None:
    s.approve(s.order_key, s.link)


def _only_child_and_link(s: Space) -> None:
    s.approve(s.ship_key, s.link)


def _keys_without_link(s: Space) -> None:
    s.approve(s.ship_key, s.order_key)


def _link_revoked(s: Space) -> None:
    s.approve_the_link()
    s.revoke(s.link)


def _child_key_revoked(s: Space) -> None:
    s.approve_the_link()
    s.revoke(s.ship_key)


def _everything_revoked(s: Space) -> None:
    s.approve_the_link()
    s.revoke(s.ship_key, s.order_key, s.link)


NOT_READ = {
    "nothing_approved": lambda s: None,
    "link_proposed_keys_approved": _keys_without_link,
    "link_revoked": _link_revoked,
    "child_key_not_approved": _only_parent_and_link,
    "parent_key_not_approved": _only_child_and_link,
    "child_key_revoked": _child_key_revoked,
    "all_revoked": _everything_revoked,
}


@pytest.mark.parametrize("state", sorted(NOT_READ))
@pytest.mark.parametrize("door", DOORS)
def test_a_proposed_revoked_or_half_approved_set_is_not_read(
    door: str, state: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 2: the envelope equals that of the same Space with no profile item."""
    set_door(door, monkeypatch)
    space = Space(rig)
    bare = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    assert_abstains(bare, "unverified_join:no_ontology")
    NOT_READ[state](space)
    env = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    assert view(env) == view(bare), (state, env["text"])
    assert_abstains(env, "unverified_join:no_ontology")
    assert rig.cortex.executed == []


# --- clause 3: an approved item that no longer holds is refused at the next ask ----------


@pytest.mark.parametrize("what", sorted(BREAKS))
@pytest.mark.parametrize("door", DOORS)
def test_an_approved_item_that_stops_holding_is_refused_at_the_next_ask(
    door: str, what: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 3: measured at every ask. Break it, refused; repair it, answered again."""
    set_door(door, monkeypatch)
    space = Space(rig)
    space.approve_the_link()
    assert_serves(ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch), JOIN_TRUTH, door)
    broken, repair = BREAKS[what]
    change(space, broken)
    executed = len(rig.cortex.executed)
    env = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    text = assert_abstains(env, BROKEN_GAP[door], figures=True)
    assert "status=" not in text, text
    assert len(rig.cortex.executed) == executed, "a refused join was executed"
    change(space, repair)
    assert oracle(rig.lake, space.join_sql) == JOIN_TRUTH
    assert_serves(ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch), JOIN_TRUTH, door)


def test_first_generate_names_a_broken_item_ontology_unverified_with_the_counts(
    rig: Rig,
) -> None:
    """DEVIATION from clause 3 wording, pinned and listed (generative_ask.py is not
    edited here): the first-generate door says ``ontology_unverified: <check> on
    <subject>`` with the measured counts, not ``unverified_join:<why>``. It is still
    ABSTAIN, no row, no value, and it states no answer figure. #523 pinned the same."""
    space = Space(rig)
    space.approve_the_link()
    change(space, BREAKS["duplicate_parent_key"][0])
    env = rig.ask(space.id, JOIN_Q, {"query_sql": space.join_sql, "plan_source": "ontology_plan"})
    text = assert_abstains(env, "ontology_unverified: key_unique on", figures=True)
    assert "7 rows, 6 distinct keys" in text, text
    assert "unverified_join" not in text


# --- clause 4: nothing but an approved link that verified joins two tables ---------------

SHAPES = {
    "keyless_child_to_approved_parent": (
        "How many order lines per order status?",
        "SELECT o.status, COUNT(*) AS n FROM {lines} l JOIN {orders} o "
        "ON l.order_id = o.order_id GROUP BY o.status",
    ),
    "link_proposed_both_keys_approved": (
        "How many orders per city?",
        "SELECT c.city, COUNT(*) AS n FROM {orders} o JOIN {customers} c "
        "ON o.customer_id = c.customer_id GROUP BY c.city",
    ),
    "column_pair_never_proposed": (
        JOIN_Q,
        "SELECT o.status, COUNT(*) AS n FROM {ships} s JOIN {orders} o "
        "ON s.order_id = o.customer_id GROUP BY o.status",
    ),
    "approved_link_wrong_direction_columns": (
        JOIN_Q,
        "SELECT o.status, COUNT(*) AS n FROM {ships} s JOIN {orders} o "
        "ON s.shipment_id = o.order_id GROUP BY o.status",
    ),
    "approved_link_plus_unlinked_third_table": (
        "How many shipments per city?",
        "SELECT c.city, COUNT(*) AS n FROM {ships} s JOIN {orders} o ON s.order_id = o.order_id "
        "JOIN {customers} c ON o.customer_id = c.customer_id GROUP BY c.city",
    ),
}


@pytest.mark.parametrize("door", DOORS)
def test_no_join_but_an_approved_verified_link_answers(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 4 and the hostile shapes of the ticket, through each door."""
    set_door(door, monkeypatch)
    space = Space(rig)
    # The parent key of orders -> customers is approved too, so the only thing
    # missing for that join is an approved link.
    space.approve_the_link()
    space.approve(space.customer_key)
    assert_serves(ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch), JOIN_TRUTH, door)
    executed = len(rig.cortex.executed)
    for name, (question, template) in SHAPES.items():
        sql = template.format(
            ships=space.ships, orders=space.orders, lines=space.lines, customers=space.customers
        )
        env = ask(rig, space.id, question, sql, door, monkeypatch)
        assert_abstains(env, GAP)
        assert len(rig.cortex.executed) == executed, f"{name} executed"


# --- clause 5: two approved keys for one table: neither is used -------------------------


def add_second_key(space: Space, key_item_id: str, columns: list[str]) -> str:
    """The route refuses a second key (409), so write the approved row as a store
    that was written to directly would hold it."""
    from dataclasses import replace
    from uuid import uuid4

    store_ = ontology_data_store()
    row = store_._items[key_item_id]  # type: ignore[attr-defined]
    payload = {**row.payload, "key": columns}
    twin = replace(row, id=str(uuid4()), payload=payload, status="approved", approved_by="steward")
    store_._items[twin.id] = twin  # type: ignore[attr-defined]
    return twin.id


@pytest.mark.parametrize("door", DOORS)
def test_two_approved_keys_for_a_table_leave_it_keyless(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_door(door, monkeypatch)
    space = Space(rig)
    bare = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    space.approve_the_link()
    # A second key on a table the join does not touch changes nothing.
    space.approve(space.customer_key)
    add_second_key(space, space.customer_key, ["customer_id", "city"])
    assert_serves(ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch), JOIN_TRUTH, door)
    # A second key on the parent: the table is keyless, so the link has no end.
    add_second_key(space, space.order_key, ["order_id", "customer_id"])
    executed = len(rig.cortex.executed)
    env = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    assert view(env) == view(bare), env["text"]
    assert_abstains(env, "unverified_join:no_ontology")
    assert len(rig.cortex.executed) == executed
    # Counting the table still works: it is asked about, not joined.
    count = ask(
        rig,
        space.id,
        "How many orders are there?",
        f"SELECT COUNT(*) AS n FROM {space.orders}",
        door,
        monkeypatch,
    )
    assert_serves(count, [{"n": 6}], door)


# --- clause 6: no measure, grain or pii item, and no other source, enters ----------------


def inject(space: Space, like_item_id: str, **fields: Any) -> str:
    """An approved row in this Space's version, as another writer could have stored it."""
    from dataclasses import replace
    from uuid import uuid4

    store_ = ontology_data_store()
    row = store_._items[like_item_id]  # type: ignore[attr-defined]
    twin = replace(row, id=str(uuid4()), status="approved", approved_by="steward", **fields)
    store_._items[twin.id] = twin  # type: ignore[attr-defined]
    return twin.id


def test_a_measure_is_never_declared_by_a_profile_item(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 6: a question needing a measure abstains ``no_declared_measure``, also
    when a measure, a grain and a pii item of source ``profile`` are approved."""
    space = Space(rig)
    space.approve_the_link()
    plan = {
        "query_plan": {"measure": "order_count", "group_by": [[space.orders, "status"]]},
        "plan_source": "ontology_plan",
    }
    env = rig.ask(space.id, "What is the order count by status?", plan)
    text = assert_abstains(env, "no_declared_measure", figures=True)
    assert "order_count" not in text, "a model-chosen measure name reached the customer"
    inject(
        space,
        space.order_key,
        kind="measure",
        payload={"name": "order_count", "grain": space.orders, "expression": "COUNT(*)"},
    )
    inject(space, space.order_key, kind="grain", payload={"name": space.orders})
    inject(
        space,
        space.order_key,
        kind="pii",
        payload={"table": space.orders, "column": "customer_id"},
    )
    again = rig.ask(space.id, "What is the order count by status?", plan)
    assert view(again) == view(env)
    assert rig.cortex.executed == []


@pytest.mark.parametrize("door", DOORS)
def test_an_approved_item_of_another_source_never_licenses_a_join(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 6: the link orders -> customers is approved under source ``ao-vendor`` in
    this Space's own version, and both keys are approved profile keys. Only the
    profile loader runs here, and it takes source ``profile`` alone."""
    set_door(door, monkeypatch)
    space = Space(rig)
    space.approve_the_link()
    space.approve(space.customer_key)
    inject(space, space.link, source="ao-vendor")  # a second origin of the same link
    other = space.link_item(space.orders, "customer_id", space.customers, "customer_id")
    inject(space, other, source="ao-vendor")
    sql = (
        f"SELECT c.city, COUNT(*) AS n FROM {space.orders} o JOIN {space.customers} c "
        "ON o.customer_id = c.customer_id GROUP BY c.city"
    )
    env = ask(rig, space.id, "How many orders per city?", sql, door, monkeypatch)
    assert_abstains(env, GAP)
    assert_serves(ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch), JOIN_TRUTH, door)


# --- clause 7: another Space's approvals change nothing here -----------------------------


@pytest.mark.parametrize("door", DOORS)
def test_approving_in_space_a_leaves_space_b_envelopes_unchanged(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_door(door, monkeypatch)
    a, b = Space(rig, "pa"), Space(rig, "pb")
    before = ask(rig, b.id, JOIN_Q, b.join_sql, door, monkeypatch)
    assert_abstains(before, "unverified_join:no_ontology")
    count_before = ask(rig, b.id, LINES_Q, b.lines_sql, door, monkeypatch)
    a.approve_the_link()
    assert_serves(ask(rig, a.id, JOIN_Q, a.join_sql, door, monkeypatch), JOIN_TRUTH, door)
    assert view(ask(rig, b.id, JOIN_Q, b.join_sql, door, monkeypatch)) == view(before)
    assert view(ask(rig, b.id, LINES_Q, b.lines_sql, door, monkeypatch)) == view(count_before)
    # B's own steward then approves: B answers, and A is as it was.
    b.approve_the_link()
    assert_serves(ask(rig, b.id, JOIN_Q, b.join_sql, door, monkeypatch), JOIN_TRUTH, door)
    change(a, BREAKS["orphan_row"][0])
    assert_serves(ask(rig, b.id, JOIN_Q, b.join_sql, door, monkeypatch), JOIN_TRUTH, door)
    assert_abstains(
        ask(rig, a.id, JOIN_Q, a.join_sql, door, monkeypatch), BROKEN_GAP[door], figures=True
    )


# --- clause 8: a table with no approved key answers as before ---------------------------


@pytest.mark.parametrize("door", DOORS)
def test_a_one_table_question_about_a_keyless_table_is_unchanged_by_approval(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_door(door, monkeypatch)
    space = Space(rig)
    asks = {
        "keyless": (LINES_Q, space.lines_sql, LINES_TRUTH),
        "unapproved_key": (
            "How many customers are there?",
            f"SELECT COUNT(*) AS n FROM {space.customers}",
            [{"n": 3}],
        ),
    }
    before = {k: ask(rig, space.id, q, sql, door, monkeypatch) for k, (q, sql, _) in asks.items()}
    for key, (_, _, truth) in asks.items():
        assert_serves(before[key], truth, door)
    space.approve_the_link()
    for key, (q, sql, truth) in asks.items():
        env = ask(rig, space.id, q, sql, door, monkeypatch)
        assert view(env) == view(before[key]), key
        assert_serves(env, truth, door)
    # and after the data moved, the answer follows the rows
    space.run(f"INSERT INTO {space.lines} (order_id, sku, pack) VALUES ('O6', 'nut', 'x9')")
    env = ask(rig, space.id, LINES_Q, space.lines_sql, door, monkeypatch)
    assert_serves(env, [{"n": 8}], door)


# --- clause 9: an unreadable item store is a named abstain ------------------------------


@pytest.mark.parametrize("door", DOORS)
def test_an_unreadable_item_store_abstains_ontology_store_unavailable(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_door(door, monkeypatch)
    space = Space(rig)
    space.approve_the_link()
    assert_serves(ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch), JOIN_TRUTH, door)

    def _boom(*_: Any, **__: Any) -> list[dict[str, Any]]:
        raise RuntimeError("item store down")

    monkeypatch.setattr(ontology_data_store(), "space_profile_items", _boom)
    executed = len(rig.cortex.executed)
    # the store is read before the contract ask: the abstain comes first
    env = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch, reaches_cortex=False)
    assert_abstains(env, "ontology_store_unavailable")
    assert len(rig.cortex.executed) == executed
    # a one-table question is not answered without the rule either
    env = ask(rig, space.id, LINES_Q, space.lines_sql, door, monkeypatch, reaches_cortex=False)
    assert_abstains(env, "ontology_store_unavailable")


# --- clause 10: a derived ontology answers alone -----------------------------------------


@pytest.mark.parametrize("door", ["first_generate", "contract_ask"])
def test_a_space_with_a_derived_ontology_keeps_it_alone(
    door: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter: ManifestMinter,
    store: OntologyStore,
) -> None:
    """Clause 10: a Space with a SQL source and uploaded files. Its steward approves
    the files' keys and link. The derived ontology still governs alone: the files'
    join is not licensed, the SQL source's verified join still answers."""
    monkeypatch.delenv("DMS_ONTO_FROM_DATA", raising=False)
    sql_space = od._space(tmp_path, monkeypatch, minter)
    gate_allows(monkeypatch)
    rig = Rig(
        client=sql_space.client,
        cortex=sql_space.cortex,
        lake=sql_space.lake,
        app=sql_space.client.app,
    )
    assert load_space_ontology(sql_space.space_id) is not None
    space = Space(rig, space_id=sql_space.space_id)
    space.approve_the_link()
    assert load_space_ontology(space.id) is not None
    env = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    assert_abstains(env, GAP)
    assert rig.cortex.executed == []
    # the derived ontology's own verified join still answers, with its true rows
    if door == "first_generate":
        env = rig.ask(
            space.id, od.VERIFIED_Q, {"query_sql": od.VERIFIED_SQL, "plan_source": "ontology_plan"}
        )
        assert env["badge"] == "L2_VALIDATED", (env["badge"], env["text"])
        assert env["rows"] == oracle(rig.lake, od.VERIFIED_SQL)
        assert "school_count=3" in env["text"]


# --- clause 11: DMS_ONTO_FROM_DATA=1 behaves as on the parent ----------------------------


@pytest.mark.parametrize("door", DOORS)
def test_under_the_data_flag_profile_items_are_not_read_and_the_item_ontology_governs(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clause 11. The ontology that governs under the flag is the Space's item
    ontology (#523). Profile items are not read there, approved or not."""
    from test_onto_scope_01 import link_proposal

    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    set_door(door, monkeypatch)
    space = Space(rig)
    bare = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    assert_abstains(bare, GAP)
    space.approve_the_link()
    env = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    assert view(env) == view(bare), "a profile item was read under the flag"
    assert rig.cortex.executed == []
    # control: the same link as an approved item of this Space is what governs
    stored = store_proposal(
        link_proposal(space.ships, space.orders), created_by="steward", space_id=space.id
    )
    for item in stored["items"]:
        assert ontology_data_store().approve(item["id"], actor="steward") is not None
    env = ask(rig, space.id, JOIN_Q, space.join_sql, door, monkeypatch)
    if door == "contract_ask":
        # #523 pinned: under the flag the contract-ask guard holds no ontology.
        assert_abstains(env, GAP)
    else:
        assert_serves(env, JOIN_TRUTH, door)


# --- clause 12 (replay): the hostile join shapes of test_onto_derive_01 -------------------


def _file_shape(sql: str, s: Space) -> str:
    """A ``JOIN_ATTACKS`` statement moved onto this Space: schools -> shipments,
    districts -> orders, with the approved link as the verified one."""
    for old, new in (
        ("BRONZE.PUBLIC_SCHOOLS", s.ships.upper()),
        ("BRONZE.PUBLIC_DISTRICTS", s.orders.upper()),
        (od.SCHOOLS, s.ships),
        (od.DISTRICTS, s.orders),
        ("school_id", "shipment_id"),
        ("district_id", "order_id"),
        ("name", "status"),
        ("'North'", "'open'"),
    ):
        sql = sql.replace(old, new)
    return sql


@pytest.mark.parametrize("door", DOORS)
def test_no_hostile_join_shape_answers_over_the_approved_link(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_door(door, monkeypatch)
    space = Space(rig)
    space.approve_the_link()
    truth = [{"school_count": 2}]
    base = _file_shape(od.BASE, space) + " WHERE d.status = 'open'"
    assert oracle(rig.lake, base) == truth
    served = []
    for name, template in sorted(od.JOIN_ATTACKS.items()):
        sql = _file_shape(template, space)
        try:
            oracle(rig.lake, sql)
        except duckdb.Error:
            pass  # a statement the lake cannot run is still posted: it must not answer
        env = ask(rig, space.id, "How many open shipments have an order?", sql, door, monkeypatch)
        if env["abstained"] is False:
            served.append(name)
            # Answering is allowed only with the true figure (test_onto_derive_01).
            assert env["rows"] == truth, (name, env["rows"], env["text"])
        else:
            assert env["badge"] == "ABSTAIN" and env["rows"] == [] and env["values"] == []
    assert len(served) < len(od.JOIN_ATTACKS), "every shape answered: the rule is not applied"


# --- item 3 of the hand-off, measured and recorded, not changed --------------------------


@pytest.mark.parametrize("door", DOORS)
def test_a_one_table_read_of_a_table_whose_approved_key_broke(
    door: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RECORDED, NOT LIKED, needs a decision (no clause asks for it). When the
    approved key of ``orders`` stops holding, a ONE-table count of ``orders``
    abstains on both generate doors, because the whole ontology is verified before
    the ask, and is served on the contract door, whose join rule looks only at
    joins. Neither door states a wrong figure."""
    set_door(door, monkeypatch)
    space = Space(rig)
    space.approve_the_link()
    change(space, BREAKS["duplicate_parent_key"][0])
    env = ask(
        rig,
        space.id,
        "How many orders are there?",
        f"SELECT COUNT(*) AS n FROM {space.orders}",
        door,
        monkeypatch,
    )
    if door == "contract_ask":
        assert_serves(env, [{"n": 7}], door)
    else:
        assert_abstains(env, "ontology_unverified", figures=True)


# --- the loader, without the ask path (step 2) -------------------------------------------


def _ids(items: list[dict[str, Any]]) -> set[str]:
    return {str(i["id"]) for i in items}


def _measure(lake: Path, onto: Any) -> list[Any]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        return list(onto.verify(con))
    finally:
        con.close()


def test_loader_keeps_only_approved_profile_keys_and_links_of_the_space(rig: Rig) -> None:
    from dms_executor.onto_profile import approved_profile_items, approved_profile_ontology

    space = Space(rig)
    assert approved_profile_items(space.id) == []
    assert approved_profile_ontology(space.id) is None
    space.approve(space.ship_key, space.order_key, space.customer_key, space.link)
    kept = approved_profile_items(space.id)
    assert _ids(kept) == {space.ship_key, space.order_key, space.customer_key, space.link}
    onto = approved_profile_ontology(space.id)
    assert onto is not None
    assert sorted(onto.objects) == sorted([space.ships, space.orders, space.customers])
    assert len(onto.links) == 1
    # a revoked item leaves; the link then loses an end and leaves with it
    space.revoke(space.order_key)
    assert _ids(approved_profile_items(space.id)) == {space.ship_key, space.customer_key}
    assert approved_profile_ontology(space.id) is None
    # a Space that is not this one, a demo Space, and no Space read nothing
    other = Space(rig, "pz")
    assert approved_profile_items(other.id) == []
    for nobody in (None, "", "   ", "demo"):
        assert approved_profile_items(nobody) == []


def test_loader_never_returns_a_measure_grain_pii_or_another_source(rig: Rig) -> None:
    from dms_executor.onto_profile import approved_profile_items

    space = Space(rig)
    space.approve_the_link()
    inject(space, space.order_key, kind="measure", payload={"name": "m", "grain": space.orders})
    inject(space, space.order_key, kind="grain", payload={"name": space.orders})
    inject(space, space.order_key, kind="pii", payload={"table": space.orders})
    inject(space, space.order_key, source="ao-vendor")
    inject(space, space.link, source="model")
    held = approved_profile_items(space.id)
    assert _ids(held) == {space.ship_key, space.order_key, space.link}
    assert {(i["kind"], i["source"]) for i in held} == {("object", "profile"), ("link", "profile")}


def test_a_table_with_two_approved_keys_keeps_none_and_takes_its_links(rig: Rig) -> None:
    from dms_executor.onto_profile import approved_profile_items, approved_profile_ontology

    space = Space(rig)
    space.approve_the_link()
    space.approve(space.customer_key)
    twin = add_second_key(space, space.customer_key, ["customer_id", "city"])
    kept = _ids(approved_profile_items(space.id))
    assert space.customer_key not in kept and twin not in kept
    assert kept == {space.ship_key, space.order_key, space.link}
    # the other tables are not touched by it
    onto = approved_profile_ontology(space.id)
    assert onto is not None and space.customers not in onto.objects
    # two keys on the parent: both go, and the link that needs it goes too
    add_second_key(space, space.order_key, ["order_id", "customer_id"])
    assert _ids(approved_profile_items(space.id)) == {space.ship_key}
    assert approved_profile_ontology(space.id) is None


def test_a_link_is_kept_only_when_both_ends_are_kept(rig: Rig) -> None:
    from dms_executor.onto_profile import approved_profile_items, approved_profile_ontology

    space = Space(rig)
    space.approve(space.link)
    assert approved_profile_items(space.id) == []
    space.approve(space.ship_key)  # child end only
    assert _ids(approved_profile_items(space.id)) == {space.ship_key}
    assert approved_profile_ontology(space.id) is None
    space.approve(space.order_key)  # parent end too
    assert _ids(approved_profile_items(space.id)) == {space.ship_key, space.order_key, space.link}
    assert approved_profile_ontology(space.id) is not None
    # the parent end alone does not keep the link either
    space.revoke(space.ship_key)
    assert _ids(approved_profile_items(space.id)) == {space.order_key}
    assert approved_profile_ontology(space.id) is None


def test_the_loaded_ontology_is_unverified_and_measured_again_at_every_call(rig: Rig) -> None:
    from dms_executor.onto_profile import approved_profile_ontology

    space = Space(rig)
    space.approve_the_link()
    first = approved_profile_ontology(space.id)
    assert first is not None and first.verified is False
    assert _measure(rig.lake, first) == []
    assert first.verified is True  # measured now, against the rows as they are
    # nothing is carried over: the next load starts unverified and is a new object
    second = approved_profile_ontology(space.id)
    assert second is not None and second is not first and second.verified is False
    # a link that breaks after approval is refused at the next measurement
    change(space, BREAKS["orphan_row"][0])
    third = approved_profile_ontology(space.id)
    assert third is not None and third.verified is False
    violations = _measure(rig.lake, third)
    assert violations and third.verified is False
    assert any(v.check == "fk_intact" for v in violations), [v.check for v in violations]
    # and repaired, the next load measures clean again
    change(space, BREAKS["orphan_row"][1])
    fourth = approved_profile_ontology(space.id)
    assert fourth is not None and _measure(rig.lake, fourth) == [] and fourth.verified is True


def test_the_loader_raises_when_the_item_store_cannot_be_read(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.onto_profile import approved_profile_ontology

    space = Space(rig)
    space.approve_the_link()

    def _boom(*_: Any, **__: Any) -> list[dict[str, Any]]:
        raise RuntimeError("item store down")

    monkeypatch.setattr(ontology_data_store(), "space_profile_items", _boom)
    with pytest.raises(RuntimeError):
        approved_profile_ontology(space.id)
