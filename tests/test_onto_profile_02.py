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
from test_onto_profile_01 import ENVELOPE_FIELDS, Rig, _approve, _csv, _revoke, make_rig

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

    def __init__(self, rig: Rig, tag: str = "pf") -> None:
        self.rig = rig
        self.tag = tag
        self.id = rig.new_space(f"Files {tag}")
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
    assert len(rig.cortex.asks) == reached + 1, "the contract ask was not reached"
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
