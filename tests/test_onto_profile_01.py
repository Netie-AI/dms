"""ONTO-PROFILE-01 (dms#514): measure a file Space, propose keys and links.

Before: a Space fed by uploaded files answered ``derived: false`` from
``GET /v1/spaces/{id}/ontology`` and nothing was ever proposed, because a CSV
declares no key and the only proposer was a model call.

Here five seeded CSV files are ingested through the real route
(``POST /v1/studio/ingest-batch``) into a non-demo Space. Every clause is
asserted on the HTTP response a steward receives. The "never read" clauses
post ``POST /v1/chat/ask`` and compare the customer envelope after
``assert_envelope_valid`` (rule 10a).

No skip, no xfail. ``tests/control_plane/test_onto_profile_01_pg.py`` runs the
store clauses against Postgres.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_core.control_plane.onto_data import ontology_data_store, reset_ontology_data_store
from dms_executor import Executor
from dms_executor import demo_warehouse as dw
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter
from dms_executor.ontology import Ontology
from fastapi.testclient import TestClient
from test_onto_derive_01 import _RecordingCortex, make_minter

SEED = 514
ORPHAN_CUSTOMERS = ["C901", "C902", "C903", "C904", "C905"]


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    return ("\n".join(",".join(r) for r in [header, *rows]) + "\n").encode()


def retail_files() -> dict[str, bytes]:
    """Five CSV files with the counts the ticket's acceptance clauses name.

    customers 60 (``customer_id`` and ``name`` unique), products 25
    (``product_id``, ``product_name``, ``unit_price`` unique), orders 300
    (``order_id`` unique, 5 rows naming 5 customers that do not exist),
    order_items 800 (no unique column, no unique pair, 796 distinct rows),
    shipments 120 (``shipment_id`` unique, every ``order_id`` exists).
    """
    rng = random.Random(SEED)
    cities = ["Ipoh", "Kuching", "Melaka", "Penang", "Johor", "Klang"]
    customers = [
        [f"C{i:03d}", f"Customer {i:03d}", cities[i % len(cities)], ["retail", "trade"][i % 2]]
        for i in range(1, 61)
    ]
    categories = ["tools", "paint", "garden", "lighting"]
    products = [
        [f"P{i:02d}", f"Product {i:02d}", f"{10.5 + i * 1.25:.2f}", categories[i % 4]]
        for i in range(1, 26)
    ]
    orders = []
    for i in range(1, 301):
        customer = f"C{rng.randint(1, 60):03d}"
        orders.append(
            [
                f"O{i:04d}",
                customer,
                f"2026-{1 + i % 9:02d}-{1 + i % 28:02d}",
                ["open", "paid"][i % 2],
            ]
        )
    for slot, orphan in zip((17, 81, 144, 203, 266), ORPHAN_CUSTOMERS, strict=True):
        orders[slot][1] = orphan
    items: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    while len(items) < 796:
        row = [
            f"O{rng.randint(1, 300):04d}",
            f"P{rng.randint(1, 25):02d}",
            str(rng.randint(1, 5)),
            ["0.00", "0.05", "0.10"][rng.randint(0, 2)],
        ]
        if tuple(row) not in seen:
            seen.add(tuple(row))
            items.append(row)
    items.extend(list(items[k]) for k in (3, 90, 400, 777))  # four whole-row duplicates
    carriers = ["north", "south", "east", "west"]
    shipments = [
        [f"S{i:04d}", f"O{rng.randint(1, 100):04d}", carriers[i % 4]] for i in range(1, 121)
    ]
    return {
        "customers.csv": _csv(["customer_id", "name", "city", "segment"], customers),
        "products.csv": _csv(["product_id", "product_name", "unit_price", "category"], products),
        "orders.csv": _csv(["order_id", "customer_id", "order_date", "status"], orders),
        "order_items.csv": _csv(["order_id", "product_id", "quantity", "discount"], items),
        "shipments.csv": _csv(["shipment_id", "order_id", "carrier"], shipments),
    }


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    reset_ontology_data_store()
    yield
    reset_ontology_data_store()


def gate_allows(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Allow every gated route in this file and record the actions asked."""
    import dms_api.routes.ontology as onto_routes
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    asked: list[str] = []

    def _allow(*, action: str, **_: Any) -> ComplianceDecision:
        asked.append(action)
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr(studio_routes, "compliance_gate", _allow)
    monkeypatch.setattr(onto_routes, "compliance_gate", _allow)
    return asked


@dataclass
class Rig:
    client: TestClient
    cortex: _RecordingCortex
    lake: Path
    app: Any
    gate_actions: list[str] = field(default_factory=list)
    tables: dict[str, dict[str, str]] = field(default_factory=dict)

    def new_space(self, name: str) -> str:
        return str(self.app.state.space_store.create(name).id)

    def ingest(self, space_id: str, files: dict[str, bytes]) -> dict[str, str]:
        """Upload through the real route. Returns file name -> landed table label."""
        r = self.client.post(
            "/v1/studio/ingest-batch",
            data={"space_id": space_id},
            files=[("files", (name, raw, "text/csv")) for name, raw in files.items()],
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ingested"] == len(files), body
        landed = {str(f["file"]): str(f["table"]) for f in body["files"]}
        assert set(landed) == set(files), landed
        self.tables[space_id] = landed
        return landed

    def profile(self, space_id: str) -> dict[str, Any]:
        r = self.client.post(f"/v1/ontology/data/profile?space_id={space_id}")
        assert r.status_code == 200, r.text
        return r.json()

    def ask(self, space_id: str, question: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.cortex.payload = payload
        r = self.client.post(
            "/v1/chat/ask",
            json={"question": question, "session_id": "ses_onto_profile", "space_id": space_id},
        )
        assert r.status_code == 200, r.text
        env = r.json()
        assert_envelope_valid(env)
        return env


def make_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> Rig:
    lake = tmp_path / "onto_profile.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    get_settings.cache_clear()
    actions = gate_allows(monkeypatch)
    cortex = _RecordingCortex(warehouse=lake, payload={})
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return Rig(TestClient(app), cortex, lake, app, actions)


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> Rig:
    return make_rig(tmp_path, monkeypatch, minter)


@pytest.fixture()
def retail(rig: Rig) -> tuple[Rig, str, dict[str, str]]:
    space_id = rig.new_space("Retail files")
    return rig, space_id, rig.ingest(space_id, retail_files())


# --- helpers -----------------------------------------------------------------------

EVIDENCE_KEYS = {
    "object": {
        "row_count",
        "distinct_count",
        "null_count",
        "column_types",
        "referenced_by",
        "truncated",
    },
    "link": {
        "child_rows",
        "child_null_rows",
        "child_distinct",
        "parent_distinct",
        "matched_parent_keys",
        "orphan_rows",
        "orphan_keys",
        "max_fanout",
        "child_is_own_key",
        "child_has_key",
        "column_types",
        "truncated",
    },
}
ENVELOPE_FIELDS = ("badge", "abstained", "text", "rows", "values", "sources", "assumptions")


def _objects(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in body["items"] if i["kind"] == "object"]


def _links(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in body["items"] if i["kind"] == "link"]


def _keys(body: dict[str, Any], table: str) -> list[list[str]]:
    return sorted(i["payload"]["key"] for i in _objects(body) if i["payload"]["relation"] == table)


def _item(body: dict[str, Any], table: str, key: list[str]) -> dict[str, Any]:
    (found,) = [
        i
        for i in _objects(body)
        if i["payload"]["relation"] == table and i["payload"]["key"] == key
    ]
    return found


def _evidence(item: dict[str, Any]) -> dict[str, Any]:
    return item["provenance"]["evidence"]


def _approve(rig: Rig, item_id: str) -> Any:
    return rig.client.post(f"/v1/ontology/data/items/{item_id}/approve")


def _revoke(rig: Rig, item_id: str) -> Any:
    return rig.client.post(f"/v1/ontology/data/items/{item_id}/revoke")


def _space_items(rig: Rig, space_id: str, status: str | None = None) -> list[dict[str, Any]]:
    suffix = f"?status={status}" if status else ""
    r = rig.client.get(f"/v1/ontology/data/spaces/{space_id}/items{suffix}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["served"] is False
    return body["items"]


def _oracle(lake: Path, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _stored_text() -> str:
    """Every stored version and item of the in-memory store, as one string."""
    store = ontology_data_store()
    rows = [vars(v) for v in store._versions.values()] + [vars(i) for i in store._items.values()]  # type: ignore[union-attr]
    return json.dumps(rows, default=str, sort_keys=True)


# --- step 1: the gap this ticket closes --------------------------------------------


def test_file_space_ontology_is_not_derived_and_a_proposal_does_not_change_that(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_id, _ = retail
    before = rig.client.get(f"/v1/spaces/{space_id}/ontology").json()
    assert before["derived"] is False and before["ontologies"] == []
    body = rig.profile(space_id)
    for item in body["items"]:
        _approve(rig, item["id"])
    # A proposal, even approved, is not a derived ontology the ask path reads.
    after = rig.client.get(f"/v1/spaces/{space_id}/ontology").json()
    assert after == before


# --- clause 1: one object item per candidate key -------------------------------------


def test_profile_proposes_one_object_per_candidate_key(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_id, t = retail
    body = rig.profile(space_id)
    assert body["status"] == "proposed"
    assert body["served"] is False
    assert body["space_id"] == space_id
    assert body["version_id"]
    assert body["persisted"] is False and "memory" in body["hint"]

    assert _keys(body, t["customers.csv"]) == [["customer_id"], ["name"]]
    assert _keys(body, t["products.csv"]) == [["product_id"], ["product_name"], ["unit_price"]]
    assert _keys(body, t["orders.csv"]) == [["order_id"]]
    assert _keys(body, t["shipments.csv"]) == [["shipment_id"]]
    assert _keys(body, t["order_items.csv"]) == []
    assert len(_objects(body)) == 7

    for item in _objects(body):
        assert item["status"] == "proposed" and item["source"] == "profile"
        assert item["version_id"] == body["version_id"]
        assert item["approved_by"] is None and item["approval_ref"] is None
        payload = item["payload"]
        assert set(payload) == {"name", "relation", "key"}
        assert payload["name"] == payload["relation"]
        ev = _evidence(item)
        assert set(ev) == EVIDENCE_KEYS["object"]
        # The measured definition: no NULL, as many distinct values as rows.
        assert ev["null_count"] == 0 and ev["distinct_count"] == ev["row_count"] > 0
        assert set(ev["column_types"]) == set(payload["key"])
        (rows,) = _oracle(rig.lake, f"SELECT COUNT(*) FROM {payload['relation']}")
        assert ev["row_count"] == rows[0]
        # The shape Ontology reads (ontology.py _apply_stored_item).
        onto = Ontology.from_store([{**item, "status": "approved"}])
        assert onto.objects[payload["name"]].key == tuple(payload["key"])

    cust = _evidence(_item(body, t["customers.csv"], ["customer_id"]))
    assert cust["row_count"] == 60 and cust["column_types"] == {"customer_id": "VARCHAR"}
    # The evidence that lets a steward tell the id from the coincidence.
    assert [r["table"] for r in cust["referenced_by"]] == [t["orders.csv"]]
    assert _evidence(_item(body, t["customers.csv"], ["name"]))["referenced_by"] == []
    price = _evidence(_item(body, t["products.csv"], ["unit_price"]))
    assert price["column_types"] == {"unit_price": "DOUBLE"} and price["referenced_by"] == []
    order_refs = _evidence(_item(body, t["orders.csv"], ["order_id"]))["referenced_by"]
    assert {(r["table"], r["result"]) for r in order_refs} == {
        (t["order_items.csv"], "child_has_no_key"),
        (t["shipments.csv"], "link"),
    }


def test_a_pair_is_proposed_only_when_no_single_column_is_a_key(rig: Rig) -> None:
    space_id = rig.new_space("Stock")
    rows = [
        [site, sku, str(1 + (i % 2))]
        for i, (site, sku) in enumerate(
            (s, k) for s in ("north", "south", "east") for k in ("a1", "b2", "c3")
        )
    ]
    t = rig.ingest(space_id, {"stock.csv": _csv(["site", "sku", "qty"], rows)})
    body = rig.profile(space_id)
    (only,) = _objects(body)
    assert only["payload"] == {
        "name": t["stock.csv"],
        "relation": t["stock.csv"],
        "key": ["site", "sku"],
    }
    ev = _evidence(only)
    assert ev["row_count"] == ev["distinct_count"] == 9 and ev["null_count"] == 0
    assert set(ev["column_types"]) == {"site", "sku"}
    assert body["bounds"]["pair_search"]["tables"][t["stock.csv"]]["pairs_searched"] == 3
    assert body["unkeyed_tables"] == []


def test_a_column_with_nulls_is_never_part_of_a_proposed_pair_key(rig: Rig) -> None:
    """``g`` and ``h`` each hold a NULL and repeat; the pair is distinct on every row.

    A NULL key is not a key (``Ontology.verify`` key_not_null), so nothing is
    proposed and the response says which columns were left out of the pair search.
    """
    space_id = rig.new_space("Null pair")
    rows = [["a", "x", "k"], ["a", "y", "k"], ["b", "x", "k"], ["b", "y", "k"]]
    rows += [["", "x", "k"], ["a", "", "k"]]
    t = rig.ingest(space_id, {"gh.csv": _csv(["g", "h", "v"], rows)})
    table = t["gh.csv"]
    # The trap is real on the lake: both columns hold a NULL, the pair is unique.
    ((total, g_nulls, h_nulls, pairs),) = _oracle(
        rig.lake,
        f"SELECT COUNT(*), COUNT(*) - COUNT(g), COUNT(*) - COUNT(h), "
        f"(SELECT COUNT(*) FROM (SELECT DISTINCT g, h FROM {table})) FROM {table}",
    )
    assert (total, g_nulls, h_nulls, pairs) == (6, 1, 1, 6)

    body = rig.profile(space_id)
    assert body["items"] == [] and body["version_id"] is None
    (unkeyed,) = body["unkeyed_tables"]
    assert unkeyed["table"] == table
    assert unkeyed["reason"] == "no_unique_column_or_pair"
    assert unkeyed["searched"] == {"single_columns": 3, "pairs": 0}
    assert body["bounds"]["pair_search"]["tables"][table] == {
        "pairs_searched": 0,
        "columns_with_nulls_not_paired": ["g", "h"],
        "columns_over_pair_cap_not_paired": [],
    }
    # And nothing was stored for the steward to approve.
    assert _space_items(rig, space_id) == []
    assert ontology_data_store()._items == {}  # type: ignore[union-attr]


# --- clause 2: links ----------------------------------------------------------------


def test_profile_proposes_the_link_whose_every_value_exists(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_id, t = retail
    body = rig.profile(space_id)
    (link,) = _links(body)
    assert link["status"] == "proposed" and link["source"] == "profile"
    payload = link["payload"]
    assert payload["from_object"] == t["shipments.csv"] and payload["from_columns"] == ["order_id"]
    assert payload["to_object"] == t["orders.csv"] and payload["to_columns"] == ["order_id"]
    assert payload["one_to_one"] is False and payload["name"]
    ev = _evidence(link)
    assert set(ev) == EVIDENCE_KEYS["link"]
    ship = t["shipments.csv"]
    ((rows, distinct, fanout),) = _oracle(
        rig.lake,
        f"SELECT (SELECT COUNT(*) FROM {ship}), (SELECT COUNT(DISTINCT order_id) FROM {ship}), "
        f"(SELECT MAX(n) FROM (SELECT COUNT(*) n FROM {ship} GROUP BY order_id))",
    )
    assert (ev["child_rows"], ev["child_distinct"], ev["max_fanout"]) == (rows, distinct, fanout)
    assert rows == 120 and fanout > 1
    assert ev["child_null_rows"] == 0
    assert ev["parent_distinct"] == 300
    assert ev["matched_parent_keys"] == distinct
    assert ev["orphan_rows"] == 0 and ev["orphan_keys"] == 0
    assert ev["child_is_own_key"] is False
    assert ev["column_types"] == {"child": "VARCHAR", "parent": "VARCHAR"}


def test_null_is_not_a_key_and_a_null_reference_is_not_an_orphan(rig: Rig) -> None:
    space_id = rig.new_space("Nulls")
    parents = [[f"A{i}", "" if i == 3 else f"code{i}", "x"] for i in range(1, 7)]
    kids = [[f"K{i}", "" if i in (2, 5) else f"A{1 + i % 6}", "y"] for i in range(1, 9)]
    # Same parent ids in another case: a match only after a case fold is no match.
    folded = [[f"F{i}", f"a{1 + i % 6}", "z"] for i in range(1, 9)]
    profiles = [[f"A{i}", "w"] for i in range(1, 7)]
    t = rig.ingest(
        space_id,
        {
            "parents.csv": _csv(["pid", "code", "kind"], parents),
            "kids.csv": _csv(["kid", "pid", "kind"], kids),
            "folded.csv": _csv(["fid", "pid", "kind"], folded),
            "profiles.csv": _csv(["pid", "kind"], profiles),
        },
    )
    body = rig.profile(space_id)
    # ``code`` is distinct on every non-NULL row and has one NULL: not a key.
    assert _keys(body, t["parents.csv"]) == [["pid"]]
    by_child = {(i["payload"]["from_object"], i["payload"]["to_object"]): i for i in _links(body)}
    kid_link = _evidence(by_child[(t["kids.csv"], t["parents.csv"])])
    assert kid_link["child_null_rows"] == 2
    assert kid_link["orphan_rows"] == 0 and kid_link["orphan_keys"] == 0
    assert kid_link["child_rows"] == 8
    # Compared as landed (rule 12): the lower-case twin is not proposed.
    assert not [k for k in by_child if k[0] == t["folded.csv"]]
    assert not [o for o in body["observations"] if o["child_table"] == t["folded.csv"]]
    assert body["bounds"]["link_search"]["pairs_below_orphan_threshold"] >= 1
    # A child column that is the child's own key is shown as such and is never
    # proposed one-to-one, so ``Ontology.verify`` keeps it unverified.
    twin = by_child[(t["profiles.csv"], t["parents.csv"])]
    assert _evidence(twin)["child_is_own_key"] is True
    assert twin["payload"]["one_to_one"] is False


# --- clause 3: observations ----------------------------------------------------------


def test_orphans_and_keyless_children_are_observed_and_not_stored(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_id, t = retail
    body = rig.profile(space_id)
    seen = {
        (
            o["child_table"],
            tuple(o["child_columns"]),
            o["parent_table"],
            tuple(o["parent_columns"]),
        ): o
        for o in body["observations"]
    }
    orphan_key = (t["orders.csv"], ("customer_id",), t["customers.csv"], ("customer_id",))
    items_order = (t["order_items.csv"], ("order_id",), t["orders.csv"], ("order_id",))
    items_product = (t["order_items.csv"], ("product_id",), t["products.csv"], ("product_id",))
    assert set(seen) == {orphan_key, items_order, items_product}

    orphans = seen[orphan_key]
    assert orphans["reason"] == "orphans"
    ev = orphans["evidence"]
    assert set(ev) == EVIDENCE_KEYS["link"]
    assert (ev["orphan_rows"], ev["orphan_keys"]) == (5, 5)
    assert ev["child_rows"] == 300 and ev["parent_distinct"] == 60
    assert ev["matched_parent_keys"] == ev["child_distinct"] - 5

    for key in (items_order, items_product):
        obs = seen[key]
        assert obs["reason"] == "child_has_no_key"
        assert obs["evidence"]["orphan_rows"] == 0 and obs["evidence"]["orphan_keys"] == 0
        assert obs["evidence"]["child_rows"] == 800 and obs["evidence"]["child_has_key"] is False

    (unkeyed,) = body["unkeyed_tables"]
    assert unkeyed["table"] == t["order_items.csv"] and unkeyed["row_count"] == 800
    assert unkeyed["reason"] == "no_unique_column_or_pair"
    assert unkeyed["searched"] == {"single_columns": 4, "pairs": 6}

    # Nothing is stored for an observation: no item names these child columns.
    stored = _space_items(rig, space_id)
    assert len(stored) == 8
    for item in stored:
        if item["kind"] == "link":
            assert item["payload"]["from_object"] not in {t["order_items.csv"], t["orders.csv"]}


# --- clause 4: no cell value anywhere -------------------------------------------------


#: Shapes a leaked canary cell would have, with or without its decimals. A
#: bare run of digits is not one: an item id or a ``measured_at`` timestamp
#: can hold the same digits by chance, so a number must stand on its own.
CANARY_PATTERNS = (
    r"CNRY",
    r"(?<![\w.])919[23]\d(?:\.\d+)?(?!\w)",
    r"(?<![\w.])828[23]\d(?:\.\d+)?(?!\w)",
    r"2031-07",
)


def _leaks(text: str) -> list[str]:
    """Every canary pattern found in ``text``. Empty means no cell value."""
    return [pattern for pattern in CANARY_PATTERNS if re.search(pattern, text)]


def canary_files() -> dict[str, bytes]:
    accounts = [
        [f"CNRYacct{i}q", f"CNRYowner{i % 3}q", f"{91919.25 + i:.2f}", f"2031-07-{i:02d}"]
        for i in range(1, 13)
    ]
    moves = [
        [f"CNRYmove{i}q", f"CNRYacct{1 + i % 12}q", f"{82828.5 + i % 4:.2f}"] for i in range(1, 31)
    ]
    moves[7][1] = "CNRYghostq"  # one orphan, so an observation is serialised too
    return {
        "accounts.csv": _csv(["acct", "owner", "balance", "opened"], accounts),
        "moves.csv": _csv(["move", "acct", "amount"], moves),
    }


def test_the_canary_patterns_catch_every_fixture_cell_in_every_form() -> None:
    """The fixture and the patterns cannot drift apart: each cell is checked here."""
    cells = 0
    for raw in canary_files().values():
        for line in raw.decode().splitlines()[1:]:
            for cell in line.split(","):
                cells += 1
                forms = [cell]
                try:
                    number = float(cell)
                except ValueError:
                    pass
                else:
                    # As landed (DOUBLE), as JSON prints it, and without decimals.
                    forms += [str(number), json.dumps(number), str(int(number))]
                for form in forms:
                    assert _leaks(form), (cell, form)
                    assert _leaks(json.dumps({"min": form, "note": f"saw {form} here"})), form
    assert cells == 12 * 4 + 30 * 3
    # And they do not fire on what a clean response does contain.
    for clean in (
        "2026-11-13T01:30:04.919205+00:00",
        "2026-11-13T01:30:04.828291+00:00",
        "a9192031-8282-4919-9828-919208282912",
        '{"row_count": 12, "distinct_count": 12, "child_rows": 30, "max_fanout": 3}',
    ):
        assert _leaks(clean) == [], clean


def test_no_cell_value_reaches_the_response_the_store_or_the_log(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    space_id = rig.new_space("Canaries")
    t = rig.ingest(space_id, canary_files())
    with caplog.at_level("DEBUG"):
        body = rig.profile(space_id)
        again = rig.profile(space_id)
        listed = _space_items(rig, space_id)
    assert _keys(body, t["accounts.csv"]) and body["observations"]
    text = "\n".join(
        [
            json.dumps(body, sort_keys=True),
            json.dumps(again, sort_keys=True),
            json.dumps(listed, sort_keys=True),
            _stored_text(),
            caplog.text,
        ]
    )
    assert _leaks(text) == []


@pytest.mark.parametrize(
    "aggregate",
    [
        "MAX(balance)",
        "CAST(FLOOR(MIN(balance)) AS BIGINT)",
        "MAX(amount)",
        "CAST(FLOOR(MIN(amount)) AS BIGINT)",
        "MAX(opened)",
    ],
)
def test_the_canary_check_fails_when_a_column_extreme_leaks(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, aggregate: str
) -> None:
    """The gate above can fail: a leaked maximum, or a minimum without decimals."""
    import dms_executor

    space_id = rig.new_space("Canaries")
    t = rig.ingest(space_id, canary_files())
    table = t["moves.csv"] if "amount" in aggregate else t["accounts.csv"]
    ((value,),) = _oracle(rig.lake, f"SELECT {aggregate} FROM {table}")
    real = dms_executor.profile_space

    def _leaky(space: str, **kw: Any) -> dict[str, Any]:
        profile = real(space, **kw)
        profile["bounds"]["widest"] = {"aggregate": value if isinstance(value, int) else str(value)}
        profile["keys"][0]["evidence"]["extreme"] = (
            value if isinstance(value, float) else str(value)
        )
        return profile

    assert _leaks(json.dumps(rig.profile(space_id))) == []
    monkeypatch.setattr(dms_executor, "profile_space", _leaky)
    leaked = rig.profile(space_id)
    assert _leaks(json.dumps(leaked["bounds"])), (aggregate, value)
    assert _leaks(json.dumps(leaked["items"])), (aggregate, value)
    assert _leaks(_stored_text()), (aggregate, value)


def test_a_failed_measurement_is_named_by_class_only(
    retail: tuple[Rig, str, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, space_id, _ = retail
    import dms_executor.onto_profile as prof

    def _boom(*_a: Any, **_k: Any) -> tuple[int, int]:
        raise duckdb.ConversionException("Could not convert string 'CNRYsecretq' to INT32")

    monkeypatch.setattr(prof, "_containment", _boom)
    body = rig.profile(space_id)
    text = json.dumps(body)
    assert "CNRYsecretq" not in text and "Could not convert" not in text
    unmeasured = body["bounds"]["unmeasured"]
    assert unmeasured and {u["error"] for u in unmeasured} == {"ConversionException"}
    assert len(unmeasured) == body["bounds"]["link_search"]["pairs_measured"]
    assert _links(body) == [] and body["observations"] == []


# --- clause 5: no measure, grain, pii or aggregate ------------------------------------


def _numbers(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _numbers(v, f"{path}.{k}")
    elif isinstance(node, list):
        for v in node:
            yield from _numbers(v, f"{path}[]")
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield path, node


def test_profile_never_proposes_a_measure_grain_pii_or_aggregate(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_id, t = retail
    body = rig.profile(space_id)
    stored = _space_items(rig, space_id)
    assert {i["kind"] for i in body["items"]} == {"object", "link"}
    assert {i["kind"] for i in stored} == {"object", "link"}
    for item in (*body["items"], *stored):
        assert set(_evidence(item)) == EVIDENCE_KEYS[item["kind"]]
        assert set(item["provenance"]) == {"profiler", "measured_at", "evidence"}
    # Every number in the response is a whole count, except the stated threshold.
    for path, value in _numbers(body):
        if path == ".bounds.orphan_observation_min_match_ratio":
            continue
        assert isinstance(value, int), (path, value)
    # And none of them is an aggregate of a numeric column.
    items, products = t["order_items.csv"], t["products.csv"]
    ((qty_sum,),) = _oracle(rig.lake, f"SELECT SUM(quantity) FROM {items}")
    ((price_sum, price_min, price_max),) = _oracle(
        rig.lake, f"SELECT SUM(unit_price), MIN(unit_price), MAX(unit_price) FROM {products}"
    )
    values = {v for _, v in _numbers(body)}
    assert qty_sum not in values
    text = json.dumps(body)
    for agg in (price_sum, price_min, price_max):
        assert f"{agg:.2f}" not in text and str(agg) not in text


# --- clause 6: an approved profile item belongs to its Space ---------------------------


def test_an_approved_profile_item_is_read_only_through_its_space(
    retail: tuple[Rig, str, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, space_a, t = retail
    space_b = rig.new_space("Other files")
    rig.ingest(space_b, {"tags.csv": _csv(["tag", "colour"], [["t1", "red"], ["t2", "red"]])})
    body = rig.profile(space_a)
    key = _item(body, t["orders.csv"], ["order_id"])
    (link,) = _links(body)
    assert _approve(rig, key["id"]).status_code == 200
    assert _approve(rig, link["id"]).status_code == 200

    approved_a = _space_items(rig, space_a, "approved")
    assert {i["id"] for i in approved_a} == {key["id"], link["id"]}
    assert all(i["status"] == "approved" and i["approved_by"] for i in approved_a)
    assert _space_items(rig, space_b) == []
    assert _space_items(rig, space_b, "approved") == []

    # The tenant-wide read, and so the data ontology, never contains it.
    assert ontology_data_store().approved_items() == []
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    onto = Ontology.from_store()
    assert onto.objects == {} and onto.links == {}
    assert onto.__dict__["_served_ids"] == ()


def test_profiling_one_space_measures_and_stores_nothing_of_another(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_a, t = retail
    space_b = rig.new_space("Depot files")
    depots = [[f"D{i:02d}", f"zone{i % 3}"] for i in range(1, 10)]
    crates = [[f"K{i:03d}", f"D{1 + i % 9:02d}", str(i % 4)] for i in range(1, 41)]
    b_tables = rig.ingest(
        space_b,
        {
            "depots.csv": _csv(["depot_id", "zone"], depots),
            "crates.csv": _csv(["crate_id", "depot_id", "weight"], crates),
        },
    )
    assert not set(b_tables.values()) & set(t.values())

    body = rig.profile(space_a)
    # Space A's response is exactly Space A: tables, items, observations, counts.
    assert {tb["table"] for tb in body["tables"]} == set(t.values())
    assert len(_objects(body)) == 7 and len(_links(body)) == 1
    assert len(body["observations"]) == 3 and len(body["unkeyed_tables"]) == 1
    text = json.dumps(body)
    for foreign in (*b_tables.values(), "depot_id", "crate_id", "zone", "weight"):
        assert foreign not in text, foreign
    links = body["bounds"]["link_search"]
    columns = {tb["table"]: len(tb["columns"]) for tb in body["tables"]}
    considered = sum(
        sum(n for table, n in columns.items() if table != item["payload"]["relation"])
        for item in _objects(body)
    )
    assert considered == links["pairs_measured"] + links["pairs_type_incompatible"]

    # Stored for A: only A's tables. Stored for B: nothing at all.
    stored_a = _space_items(rig, space_a)
    assert len(stored_a) == 8
    assert not any(table in json.dumps(stored_a) for table in b_tables.values())
    assert _space_items(rig, space_b) == []
    store = ontology_data_store()
    assert {v.space_id for v in store._versions.values()} == {space_a}  # type: ignore[union-attr]
    assert not any(table in _stored_text() for table in b_tables.values())

    # Space B, profiled on its own, has its own proposals: the fixture can link.
    own = rig.profile(space_b)
    assert {tb["table"] for tb in own["tables"]} == set(b_tables.values())
    assert len(_links(own)) == 1 and len(_objects(own)) == 2
    assert not any(table in json.dumps(own) for table in t.values())
    assert len(_space_items(rig, space_a)) == 8


# --- clause 7: a proposal never reaches an answer --------------------------------------


def _asks(rig: Rig, space_id: str, t: dict[str, str]) -> dict[str, dict[str, Any]]:
    orders, ships, cust = t["orders.csv"], t["shipments.csv"], t["customers.csv"]
    questions = {
        "count": ("How many orders are there?", f"SELECT COUNT(*) AS n FROM {orders}"),
        "proposed_link_join": (
            "How many shipments per order status?",
            f"SELECT o.status, COUNT(*) AS n FROM {ships} s JOIN {orders} o "
            "ON s.order_id = o.order_id GROUP BY o.status",
        ),
        "orphan_join": (
            "How many orders per city?",
            f"SELECT c.city, COUNT(*) AS n FROM {orders} o JOIN {cust} c "
            "ON o.customer_id = c.customer_id GROUP BY c.city",
        ),
    }
    out: dict[str, dict[str, Any]] = {}
    for name, (question, sql) in questions.items():
        sent = len(rig.cortex.insights)
        env = rig.ask(space_id, question, {"query_sql": sql, "plan_source": "ontology_plan"})
        out[name] = {
            **{f: env.get(f) for f in ENVELOPE_FIELDS},
            "catalog_sent": [i["parsed"] for i in rig.cortex.insights[sent:]],
        }
    return out


@pytest.mark.parametrize("flag", [None, "1"])
def test_profile_items_in_any_status_leave_the_ask_envelope_unchanged(
    retail: tuple[Rig, str, dict[str, str]], monkeypatch: pytest.MonkeyPatch, flag: str | None
) -> None:
    rig, space_id, t = retail
    if flag is None:
        monkeypatch.delenv("DMS_ONTO_FROM_DATA", raising=False)
    else:
        monkeypatch.setenv("DMS_ONTO_FROM_DATA", flag)
    before = _asks(rig, space_id, t)
    assert _asks(rig, space_id, t) == before  # the comparison is stable on its own
    for name in ("proposed_link_join", "orphan_join"):
        assert before[name]["badge"] == "ABSTAIN" and before[name]["abstained"] is True
        assert before[name]["rows"] == [] and before[name]["values"] == []
    if flag == "1":
        # Not a vacuous comparison: one ask answers with rows, the joins name the gap.
        assert before["count"]["badge"] == "L2_VALIDATED" and before["count"]["rows"] == [
            {"n": 300}
        ]
        assert "unverified_join" in before["proposed_link_join"]["text"]

    body = rig.profile(space_id)
    assert _asks(rig, space_id, t) == before, "proposed items changed an answer"

    approved = [i["id"] for i in body["items"] if _approve(rig, i["id"]).status_code == 200]
    assert len(approved) == 5  # four keys (one per keyed table) and the link
    assert len(_space_items(rig, space_id, "approved")) == 5
    assert _asks(rig, space_id, t) == before, "approved items changed an answer"

    for item_id in approved:
        assert _revoke(rig, item_id).status_code == 200
    assert len(_space_items(rig, space_id, "revoked")) == 5
    assert _asks(rig, space_id, t) == before, "revoked items changed an answer"


def test_control_the_same_items_tenant_wide_do_change_the_answer(
    retail: tuple[Rig, str, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate above can fail: these payloads outside ``profile`` reach the ask."""
    from dms_core.control_plane.onto_data import store_proposal

    rig, space_id, t = retail
    monkeypatch.setenv("DMS_ONTO_FROM_DATA", "1")
    before = _asks(rig, space_id, t)
    orders, ships = t["orders.csv"], t["shipments.csv"]
    stored = store_proposal(
        {
            "status": "proposed",
            "terms": [
                {"name": orders, "table": orders, "key": ["order_id"]},
                {"name": ships, "table": ships, "key": ["shipment_id"]},
            ],
            "declared_fks": [
                {
                    "source_table": ships,
                    "source_column": "order_id",
                    "target_table": orders,
                    "target_column": "order_id",
                }
            ],
        },
        created_by="control",
    )
    for item in stored["items"]:
        ontology_data_store().approve(item["id"], actor="control")
    after = _asks(rig, space_id, t)
    assert after["proposed_link_join"] != before["proposed_link_join"]
    assert after["proposed_link_join"]["abstained"] is False


# --- clause 8: profiling twice stores nothing twice ------------------------------------


def test_profiling_twice_keeps_ids_and_statuses_and_refreshes_counts(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_id, t = retail
    first = rig.profile(space_id)
    second = rig.profile(space_id)
    ids = {i["id"]: i for i in first["items"]}
    assert {i["id"] for i in second["items"]} == set(ids)
    assert second["version_id"] == first["version_id"]
    assert second["not_remeasured"] == []
    for item in second["items"]:
        assert item["status"] == "proposed"
        assert item["payload"] == ids[item["id"]]["payload"]
        assert _evidence(item) == _evidence(ids[item["id"]])
    assert len(_space_items(rig, space_id)) == 8
    store = ontology_data_store()
    assert len(store._items) == 8 and len(store._versions) == 1  # type: ignore[union-attr]

    key = _item(first, t["orders.csv"], ["order_id"])
    gone = _item(first, t["customers.csv"], ["name"])
    assert _approve(rig, key["id"]).status_code == 200
    assert _revoke(rig, gone["id"]).status_code == 200
    # New data in the Space: one more file that references orders.
    returns = [[f"R{i:03d}", f"O{1 + i % 40:04d}"] for i in range(1, 51)]
    landed = rig.ingest(space_id, {"returns.csv": _csv(["return_id", "order_id"], returns)})
    third = rig.profile(space_id)
    by_id = {i["id"]: i for i in third["items"]}
    assert set(ids) <= set(by_id)
    assert by_id[key["id"]]["status"] == "approved"
    assert by_id[key["id"]]["approved_by"] and by_id[key["id"]]["approval_ref"]
    # Revoked is reported as revoked, and is not proposed again under a new id.
    assert by_id[gone["id"]]["status"] == "revoked"
    assert _keys(third, t["customers.csv"]) == [["customer_id"], ["name"]]
    assert by_id[gone["id"]]["provenance"]["revoked_by"]
    # Fresh counts on the same item: the orders key is now referenced by returns.
    refs = {r["table"] for r in _evidence(by_id[key["id"]])["referenced_by"]}
    assert landed["returns.csv"] in refs
    fresh = [i for i in third["items"] if i["id"] not in ids]
    assert {i["status"] for i in fresh} == {"proposed"}
    assert {i["version_id"] for i in fresh} == {third["version_id"]}
    assert third["version_id"] != first["version_id"]
    assert len(store._items) == 8 + len(fresh) == len(third["items"])  # type: ignore[union-attr]


def test_an_item_the_run_did_not_measure_again_is_named() -> None:
    store = ontology_data_store()
    key_a = {"table": "bronze.a", "columns": ["id"], "evidence": {"row_count": 3}}
    key_b = {"table": "bronze.b", "columns": ["id"], "evidence": {"row_count": 4}}
    first = store.store_profile({"keys": [key_a, key_b]}, created_by="s", space_id="sp-1")  # type: ignore[union-attr]
    second = store.store_profile({"keys": [key_a]}, created_by="s", space_id="sp-1")  # type: ignore[union-attr]
    (b_item,) = [i for i in first["items"] if i["payload"]["relation"] == "bronze.b"]
    assert second["not_remeasured"] == [b_item["id"]]
    assert [i["payload"]["relation"] for i in second["items"]] == ["bronze.a"]
    with pytest.raises(ValueError, match="belongs to a Space"):
        store.store_profile({"keys": [key_a]}, created_by="s", space_id="")  # type: ignore[union-attr]


# --- clause 9: one approved key per table ----------------------------------------------


def test_a_second_key_for_one_table_is_refused_and_changes_nothing(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    from dms_core.control_plane.onto_data import ProfileKeyConflict

    rig, space_id, t = retail
    body = rig.profile(space_id)
    first = _item(body, t["customers.csv"], ["customer_id"])
    second = _item(body, t["customers.csv"], ["name"])

    # Approving the whole version would approve two keys for customers: refused whole.
    with pytest.raises(ProfileKeyConflict):
        ontology_data_store().approve_version(body["version_id"], "steward")
    assert {i["status"] for i in _space_items(rig, space_id)} == {"proposed"}

    ok = _approve(rig, first["id"])
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    stamped = ok.json()
    refused = _approve(rig, second["id"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == {
        "code": "key_already_approved",
        "relation": t["customers.csv"],
        "approved_item_id": first["id"],
    }
    after = {i["id"]: i for i in _space_items(rig, space_id)}
    assert after[second["id"]]["status"] == "proposed"
    assert after[second["id"]]["approved_by"] is None
    assert after[first["id"]]["approval_ref"] == stamped["approval_ref"]
    assert after[first["id"]]["ts"] == stamped["ts"]
    assert len(_space_items(rig, space_id, "approved")) == 1
    # A key of another table is not blocked by it.
    other = _item(body, t["products.csv"], ["product_id"])
    assert _approve(rig, other["id"]).status_code == 200
    # Once the first is revoked the steward can choose the other key.
    assert _revoke(rig, first["id"]).status_code == 200
    assert _approve(rig, second["id"]).status_code == 200
    assert _approve(rig, first["id"]).status_code == 409


# --- clause 10: steward grant, then the gate -------------------------------------------


def test_no_steward_grant_is_403_before_the_gate(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_id, _ = retail
    ontology_data_store().deny_steward(Settings(_env_file=None).dms_actor_user_id)  # type: ignore[call-arg]
    rig.gate_actions.clear()
    for call in (
        lambda: rig.client.post(f"/v1/ontology/data/profile?space_id={space_id}"),
        lambda: rig.client.get(f"/v1/ontology/data/spaces/{space_id}/items"),
    ):
        res = call()
        assert res.status_code == 403, res.text
        assert res.json()["detail"] == "steward_grant_required"
    assert rig.gate_actions == []
    assert ontology_data_store()._items == {}  # type: ignore[union-attr]


def test_a_refused_gate_measures_and_stores_nothing(
    retail: tuple[Rig, str, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_api.routes.ontology as onto_routes
    import dms_executor
    from cortex_client.gate import ComplianceDecision

    rig, space_id, _ = retail
    measured: list[str] = []
    real = dms_executor.profile_space

    def _spy(space: str, **kw: Any) -> dict[str, Any]:
        measured.append(space)
        return real(space, **kw)

    monkeypatch.setattr(dms_executor, "profile_space", _spy)
    allow = onto_routes.compliance_gate
    monkeypatch.setattr(
        onto_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=False, reason="policy_denied", action=action
        ),
    )
    res = rig.client.post(f"/v1/ontology/data/profile?space_id={space_id}")
    assert res.status_code == 403 and res.json()["detail"] == "policy_denied"
    assert rig.client.get(f"/v1/ontology/data/spaces/{space_id}/items").status_code == 403
    assert measured == []
    store = ontology_data_store()
    assert store._items == {} and store._versions == {}  # type: ignore[union-attr]
    # The same call with the gate open measures: the spy and the refusal are both live.
    monkeypatch.setattr(onto_routes, "compliance_gate", allow)
    rig.profile(space_id)
    assert measured == [space_id]
    assert "ontology.profile" in rig.gate_actions


# --- clause 11: unknown Space, no file tables, SQL tables skipped ----------------------


def test_unknown_space_is_404_and_a_space_without_files_is_409(rig: Rig) -> None:
    missing = "99999999-9999-4999-8999-999999999999"
    res = rig.client.post(f"/v1/ontology/data/profile?space_id={missing}")
    assert res.status_code == 404 and res.json()["detail"] == "space_not_found"
    res = rig.client.get(f"/v1/ontology/data/spaces/{missing}/items")
    assert res.status_code == 404 and res.json()["detail"] == "space_not_found"
    assert rig.client.post("/v1/ontology/data/profile").status_code == 422

    empty = rig.new_space("Empty")
    res = rig.client.post(f"/v1/ontology/data/profile?space_id={empty}")
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == {"code": "no_file_tables", "skipped": []}
    assert ontology_data_store()._versions == {}  # type: ignore[union-attr]


def test_sql_source_tables_are_skipped_and_named(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.spaces as space_routes
    import dms_api.routes.studio as studio_routes
    from dms_core.control_plane.onto_store import OntologyStore
    from dms_executor.space_ontology import set_ontology_store
    from test_db_connector import _FakeConnection, _install
    from test_onto_derive_01 import PG_FKS, PG_PKS, PG_TABLES, SPACE_TABLES, TABLE_DATA

    monkeypatch.setattr(space_routes, "compliance_gate", studio_routes.compliance_gate)
    prev = set_ontology_store(OntologyStore())
    try:
        sql_only = rig.new_space("SQL only")
        mixed = rig.new_space("SQL and files")
        for space_id, database in ((sql_only, "schools"), (mixed, "schools2")):
            _install(
                monkeypatch, _FakeConnection(PG_TABLES, dict(TABLE_DATA), pks=PG_PKS, fks=PG_FKS)
            )
            r = rig.client.post(
                "/v1/studio/sources/sql",
                json={
                    "kind": "postgresql",
                    "host": "db.example.net",
                    "database": database,
                    "user": "reader",
                    "password": "pw",
                    "space_id": space_id,
                },
            )
            assert r.status_code == 200, r.text
        sql_tables = {
            row["table"] for row in rig.client.get(f"/v1/studio/bronze?space_id={mixed}").json()
        }
        assert len(sql_tables) == len(SPACE_TABLES) == 3

        res = rig.client.post(f"/v1/ontology/data/profile?space_id={sql_only}")
        assert res.status_code == 409, res.text
        detail = res.json()["detail"]
        assert detail["code"] == "no_file_tables"
        assert len(detail["skipped"]) == 3
        assert {s["reason"] for s in detail["skipped"]} == {"sql_source"}

        landed = rig.ingest(
            mixed, {"tags.csv": _csv(["tag", "colour"], [["t1", "red"], ["t2", "red"]])}
        )
        body = rig.profile(mixed)
        assert {s["table"] for s in body["skipped"]} == sql_tables
        assert {s["reason"] for s in body["skipped"]} == {"sql_source"}
        assert [tb["table"] for tb in body["tables"]] == [landed["tags.csv"]]
        named = json.dumps([body["items"], body["observations"], body["unkeyed_tables"]])
        assert not any(table in named for table in sql_tables)
        assert _keys(body, landed["tags.csv"]) == [["tag"]]
    finally:
        set_ontology_store(prev)


# --- clause 12: every bound is stated --------------------------------------------------


def test_every_bound_is_stated_and_the_link_search_adds_up(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_id, t = retail
    body = rig.profile(space_id)
    bounds = body["bounds"]
    assert bounds["max_columns_per_table"] == 60
    assert bounds["columns_not_measured"] == {}
    assert bounds["orphan_observation_min_match_ratio"] == 0.5
    assert bounds["truncated_tables"] == [] and bounds["unmeasured"] == []
    pairs = bounds["pair_search"]
    assert pairs["max_columns"] == 20 and pairs["wider_than_pairs"] == "not searched"
    assert pairs["tables"] == {
        t["order_items.csv"]: {
            "pairs_searched": 6,
            "columns_with_nulls_not_paired": [],
            "columns_over_pair_cap_not_paired": [],
        }
    }
    links = bounds["link_search"]
    assert links["self_references"] == "not searched" and links["max_pairs"] == 2000
    assert "no cast" in links["type_rule"]
    # Every pair considered is accounted for: nothing is dropped silently.
    assert links["pairs_measured"] == (
        len(_links(body)) + len(body["observations"]) + links["pairs_below_orphan_threshold"]
    )
    assert links["pairs_below_orphan_threshold"] > 0
    assert links["pairs_type_incompatible"] > 0  # BIGINT quantity against VARCHAR keys
    assert links["pairs_over_cap_not_measured"] == 0
    assert links["pairs_child_all_null"] == 0
    keys = sum(len(_keys(body, tb["table"])) for tb in body["tables"])
    columns = {tb["table"]: len(tb["columns"]) for tb in body["tables"]}
    considered = sum(
        sum(n for table, n in columns.items() if table != item["payload"]["relation"])
        for item in _objects(body)
    )
    assert keys == 7
    assert considered == links["pairs_measured"] + links["pairs_type_incompatible"]


def test_columns_over_the_cap_are_named_not_dropped(rig: Rig) -> None:
    space_id = rig.new_space("Wide")
    header = [f"c{i:02d}" for i in range(62)]
    rows = [[f"r{r}" if i == 61 else "same" for i in range(62)] for r in range(4)]
    t = rig.ingest(space_id, {"wide.csv": _csv(header, rows)})
    body = rig.profile(space_id)
    assert body["bounds"]["columns_not_measured"] == {t["wide.csv"]: ["c60", "c61"]}
    # The only unique column is past the cap: not proposed, and the response says why.
    assert _objects(body) == []
    (unkeyed,) = body["unkeyed_tables"]
    assert unkeyed["searched"]["single_columns"] == 60
    pair = body["bounds"]["pair_search"]["tables"][t["wide.csv"]]
    assert pair["pairs_searched"] == 190 and len(pair["columns_over_pair_cap_not_paired"]) == 40


def test_a_truncated_table_is_stated_on_the_bound_and_the_evidence(
    retail: tuple[Rig, str, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, space_id, t = retail
    import dms_executor.onto_profile as prof

    real = prof.list_bronze_tables

    def _truncated(**kw: Any) -> list[dict[str, Any]]:
        rows = real(**kw)
        for row in rows:
            if row["table"] == t["orders.csv"]:
                row["truncated"] = True
        return rows

    monkeypatch.setattr(prof, "list_bronze_tables", _truncated)
    body = rig.profile(space_id)
    assert body["bounds"]["truncated_tables"] == [t["orders.csv"]]
    assert _evidence(_item(body, t["orders.csv"], ["order_id"]))["truncated"] is True
    (link,) = _links(body)
    assert _evidence(link)["truncated"] is True
    assert _evidence(_item(body, t["customers.csv"], ["customer_id"]))["truncated"] is False


def test_a_source_column_name_is_one_quoted_identifier(rig: Rig) -> None:
    space_id = rig.new_space("Names")
    raw = b'"order id (raw)","size""in","select"\nA1,1x,p\nA2,2x,p\nA3,3x,q\n'
    t = rig.ingest(space_id, {"names.csv": raw})
    schema, name = t["names.csv"].split(".")
    landed = [
        r[0]
        for r in _oracle(
            rig.lake,
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = '{schema}' AND table_name = '{name}' "
            "AND column_name NOT IN ('_src', '_ingest_id') ORDER BY ordinal_position",
        )
    ]
    body = rig.profile(space_id)
    (table,) = body["tables"]
    assert [c["name"] for c in table["columns"]] == landed
    assert _keys(body, t["names.csv"]) == sorted([[landed[0]], [landed[1]]])
    assert body["bounds"]["unmeasured"] == []


# --- clause 13: the steward's Space read ------------------------------------------------


def test_space_items_route_returns_status_and_stored_evidence(
    retail: tuple[Rig, str, dict[str, str]],
) -> None:
    rig, space_id, t = retail
    assert _space_items(rig, space_id) == []
    body = rig.profile(space_id)
    listed = _space_items(rig, space_id)
    assert {i["id"] for i in listed} == {i["id"] for i in body["items"]}
    by_id = {i["id"]: i for i in body["items"]}
    for item in listed:
        assert item["status"] == "proposed" and item["source"] == "profile"
        assert item["payload"] == by_id[item["id"]]["payload"]
        assert _evidence(item) == _evidence(by_id[item["id"]])
        assert item["provenance"]["measured_at"]

    key = _item(body, t["orders.csv"], ["order_id"])
    drop = _item(body, t["products.csv"], ["unit_price"])
    assert _approve(rig, key["id"]).status_code == 200
    assert _revoke(rig, drop["id"]).status_code == 200
    assert [i["id"] for i in _space_items(rig, space_id, "approved")] == [key["id"]]
    assert [i["id"] for i in _space_items(rig, space_id, "revoked")] == [drop["id"]]
    assert len(_space_items(rig, space_id, "proposed")) == 6
    assert len(_space_items(rig, space_id)) == 8
    res = rig.client.get(f"/v1/ontology/data/spaces/{space_id}/items?status=served")
    assert res.status_code == 422
    assert "ontology.space_items" in rig.gate_actions
    # The catch-all section route still answers its own paths.
    assert rig.client.get("/v1/ontology/nope").status_code == 404
