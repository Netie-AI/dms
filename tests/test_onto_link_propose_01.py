"""FDE-ONTO-LINKS-01. Flag default off. Synthetic schema only.

Two declared foreign keys. Four undeclared keys whose names do not match the
parent key. One small integer code that is contained in a parent and sits
under the coverage gate. One column whose name matches a parent key and whose
values do not. The model names every pair it is shown, including the weak
code and a lookalike the data does not contain. Only the four eligible pairs
are stored, as proposed items with payload status unverified. A join on one
of them is refused until that item is approved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb
import pytest
from dms_core.control_plane.onto_data import (
    ontology_data_store,
    reset_ontology_data_store,
)
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.link_propose import bind_link_transport, reset_link_transport
from dms_executor.space_ontology import (
    check_sql_against_space,
    derive_and_store,
    derive_and_verify,
    load_space_ontology,
    set_ontology_store,
    source_identity,
)

SPACE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
ACTOR = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
_NAMES = {
    ("main.pos_txn", "member_ref", "main.members", "member_id"): "n_member_ref",
    ("main.pos_txn_line", "item_code", "main.products", "product_id"): "n_item_code",
    ("main.rx_scripts", "patient_no", "main.members", "member_id"): "n_patient_no",
    ("main.rx_scripts", "branch_id", "main.outlets", "outlet_id"): "n_branch_id",
    ("main.pos_txn_line", "line_no", "main.outlets", "outlet_id"): "n_line_no",
}
_ELIGIBLE = {key for key in _NAMES if key[1] != "line_no"}
MEMBER_SQL = (
    "SELECT t.txn_id FROM main.pos_txn t "
    "JOIN main.members m ON t.member_ref = m.member_id"
)
BRANCH_SQL = (
    "SELECT r.script_id FROM main.rx_scripts r "
    "JOIN main.outlets o ON r.branch_id = o.outlet_id"
)
DECLARED_SQL = (
    "SELECT t.txn_id FROM main.pos_txn t "
    "JOIN main.outlets o ON t.outlet_id = o.outlet_id"
)


@pytest.fixture
def _store() -> Any:
    reset_ontology_data_store()
    previous = set_ontology_store(OntologyStore())
    try:
        yield
    finally:
        set_ontology_store(previous)
        reset_ontology_data_store()


def _catalog() -> dict[str, Any]:
    names = {
        "outlets": ["outlet_id"],
        "products": ["product_id"],
        "members": ["member_id"],
        "pos_txn": ["txn_id"],
        "pos_txn_line": ["line_id"],
        "rx_scripts": ["script_id"],
    }
    tables = [
        {
            "schema": "main",
            "table": name,
            "path": f"main.{name}",
            "source_table": f"public.{name}",
        }
        for name in names
    ]
    return {
        "source": "synthetic",
        "tables": tables,
        "primary_keys": {f"main.{name}": cols for name, cols in names.items()},
        "foreign_keys": [
            {
                "name": "fk_txn_outlet",
                "from_table": "main.pos_txn",
                "from_column": "outlet_id",
                "to_table": "main.outlets",
                "to_column": "outlet_id",
            },
            {
                "name": "fk_line_txn",
                "from_table": "main.pos_txn_line",
                "from_column": "txn_id",
                "to_table": "main.pos_txn",
                "to_column": "txn_id",
            },
        ],
        "unlanded_links": [],
    }


def _warehouse(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE outlets (outlet_id INTEGER PRIMARY KEY, name VARCHAR)")
        con.execute("CREATE TABLE products (product_id INTEGER PRIMARY KEY, sku VARCHAR)")
        con.execute("CREATE TABLE members (member_id INTEGER PRIMARY KEY, label VARCHAR)")
        con.execute(
            "CREATE TABLE pos_txn ("
            "txn_id INTEGER PRIMARY KEY, outlet_id INTEGER, member_ref INTEGER)"
        )
        con.execute(
            "CREATE TABLE pos_txn_line ("
            "line_id INTEGER PRIMARY KEY, txn_id INTEGER, item_code INTEGER, line_no INTEGER)"
        )
        con.execute(
            "CREATE TABLE rx_scripts ("
            "script_id INTEGER PRIMARY KEY, patient_no INTEGER, "
            "branch_id INTEGER, outlet_id INTEGER)"
        )
        con.execute(
            "INSERT INTO outlets SELECT i, 'o' || i::VARCHAR FROM range(1, 31) t(i)"
        )
        con.execute(
            "INSERT INTO products SELECT 2000 + i, 's' || i::VARCHAR FROM range(0, 30) t(i)"
        )
        con.execute(
            "INSERT INTO members SELECT 3000 + i, 'm' || i::VARCHAR FROM range(0, 30) t(i)"
        )
        con.execute(
            "INSERT INTO pos_txn "
            "SELECT 4000 + i, 1 + (i % 30), 3000 + (i % 30) FROM range(0, 40) t(i)"
        )
        con.execute(
            "INSERT INTO pos_txn_line "
            "SELECT i, 4000 + (i % 40), 2000 + (i % 30), 1 + (i % 3) FROM range(0, 80) t(i)"
        )
        con.execute(
            "INSERT INTO rx_scripts "
            "SELECT 5000 + i, 3000 + (i % 30), 1 + (i % 30), 9000 + (i % 3) "
            "FROM range(0, 40) t(i)"
        )
    finally:
        con.close()


def _model(seen: list[dict[str, Any]]) -> Any:
    def _transport(body: dict[str, Any]) -> dict[str, Any]:
        seen.append(body)
        user = json.loads(body["messages"][1]["content"])
        ranked = []
        for index, cand in enumerate(user["candidates"], start=1):
            key = (cand["child"], cand["column"], cand["parent"], cand["parent_column"])
            ranked.append(
                {
                    "child": key[0],
                    "column": key[1],
                    "parent": key[2],
                    "parent_column": key[3],
                    "name": _NAMES.get(key, "n_unnamed"),
                    "rank": index,
                }
            )
        ranked.append(
            {
                "child": "main.rx_scripts",
                "column": "outlet_id",
                "parent": "main.outlets",
                "parent_column": "outlet_id",
                "name": "n_outlet_lookalike",
                "rank": 0,
            }
        )
        return {"ranked": ranked}

    return _transport


def _derive(path: Path, catalog: dict[str, Any]) -> dict[str, Any]:
    return derive_and_store(
        catalog,
        space_id=SPACE,
        identity=source_identity("postgresql", "synthetic.invalid", "pharmacy", catalog),
        warehouse=path,
        actor=ACTOR,
    )


def _pair(link: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(link["from"]),
        str(link["from_columns"][0]),
        str(link["to"]),
        str(link["to_columns"][0]),
    )


def test_flag_off_is_byte_identical_and_does_not_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _store: Any
) -> None:
    monkeypatch.delenv("DMS_ONTO_LINK_PROPOSE", raising=False)
    path = tmp_path / "off.duckdb"
    _warehouse(path)
    catalog = _catalog()
    body, _violations = derive_and_verify(catalog, warehouse=path)
    seen: list[dict[str, Any]] = []

    def _boom(_body: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("model called with the flag off")

    token = bind_link_transport(_boom)
    try:
        receipt = _derive(path, catalog)
    finally:
        reset_link_transport(token)
    snap = ontology_store_body(SPACE)
    assert snap == body
    assert "proposed_links" not in snap
    assert "link_propose_error" not in snap
    assert seen == []
    assert ontology_data_store().approved_items() == []
    names = {link["name"] for link in receipt["links"]}
    assert names == {"fk_txn_outlet", "fk_line_txn"}


def ontology_store_body(space_id: str) -> dict[str, Any]:
    from dms_executor.space_ontology import ontology_store

    sid = UUID(space_id)
    version = ontology_store().active_for_space(sid)[0]
    snap = ontology_store().latest_snapshot(version.id)
    assert snap is not None
    return dict(snap.body)


def test_four_synonym_keys_are_proposed_and_unused_until_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _store: Any
) -> None:
    monkeypatch.setenv("DMS_ONTO_LINK_PROPOSE", "1")
    path = tmp_path / "on.duckdb"
    _warehouse(path)
    catalog = _catalog()
    seen: list[dict[str, Any]] = []
    token = bind_link_transport(_model(seen))
    try:
        receipt = _derive(path, catalog)
    finally:
        reset_link_transport(token)

    assert len(seen) == 1
    request = seen[0]
    assert request["role"] == "ontology"
    for banned in ("model", "model_id", "provider", "strict"):
        assert banned not in request
    user = json.loads(request["messages"][1]["content"])
    blob = json.dumps(user)
    assert "coverage" not in blob
    assert "fanout" not in blob
    sent = {
        (row["child"], row["column"], row["parent"], row["parent_column"])
        for row in user["candidates"]
    }
    assert _ELIGIBLE <= sent
    assert ("main.pos_txn_line", "line_no", "main.outlets", "outlet_id") in sent
    assert ("main.rx_scripts", "outlet_id", "main.outlets", "outlet_id") not in sent
    assert all(row["column_type"] and row["child_samples"] for row in user["candidates"])

    proposed = [link for link in receipt["links"] if link.get("item_id")]
    got = {_pair(link) for link in proposed}
    assert got == _ELIGIBLE
    assert all(link["status"] == "unverified" for link in proposed)
    assert {link["name"] for link in proposed} == {
        "n_member_ref",
        "n_item_code",
        "n_patient_no",
        "n_branch_id",
    }
    assert "n_line_no" not in {link["name"] for link in receipt["links"]}
    assert "n_outlet_lookalike" not in {link["name"] for link in receipt["links"]}

    items = ontology_data_store().diff()["proposed_only"]
    assert len(items) == 4
    assert {row["status"] for row in items} == {"proposed"}
    assert {row["kind"] for row in items} == {"link"}
    assert {row["payload"]["status"] for row in items} == {"unverified"}
    assert ontology_data_store().approved_items() == []

    onto = load_space_ontology(SPACE)
    assert onto is not None
    assert check_sql_against_space(DECLARED_SQL, onto, path) is None
    refused = check_sql_against_space(MEMBER_SQL, onto, path)
    assert refused is not None and refused.startswith("unverified_join")
    assert check_sql_against_space(BRANCH_SQL, onto, path) is not None

    member = next(link for link in proposed if link["name"] == "n_member_ref")
    approved = ontology_data_store().approve(str(member["item_id"]), actor=str(ACTOR))
    assert approved is not None and approved.status == "approved"
    onto = load_space_ontology(SPACE)
    assert onto is not None
    assert check_sql_against_space(MEMBER_SQL, onto, path) is None
    still = check_sql_against_space(BRANCH_SQL, onto, path)
    assert still is not None and still.startswith("unverified_join")
