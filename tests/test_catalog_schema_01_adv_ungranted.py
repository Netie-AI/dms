"""Adversary round for CATALOG-SCHEMA-01: a table in a granted schema that is NOT granted.

Every test asserts the SECURE behaviour; a failing test is a finding.

Adversary round for CATALOG-SCHEMA-01 (dms#518), written by an agent that did
not write the fix. Kept here: the cases that hold. The cases that failed on a
defect older than this change are listed by name in the pull request, not
skipped here.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from dms_executor import schema_context as sc
from dms_executor.ask_payload import build_dms_payload
from dms_executor.manifest import ManifestMinter
from dms_executor.schema_context import (
    SCHEMA_CONTEXT_FIELD,
    _forget_index,
    _serving_file,
    build_space_index,
    cached_value_index,
    note_serving_source,
    prepare_generate_context,
    read_catalog,
)
from test_catalog_schema_01 import (
    ONE_TABLE_Q,
    ONE_TABLE_SQL,
    _assert_nothing_outside,
    _file_space,
    _two_schema_lake,
)
from test_onto_derive_01 import make_minter
from test_schema_context_no_empty_01 import _schema_prompts, _tables_in


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture(autouse=True)
def _flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")


def _join_workers() -> None:
    with sc._WORKER_LOCK:
        workers = list(sc._WORKERS)
    for w in workers:
        w.join(30)


def _names(grant: set[str] | None, lake: Path) -> set[str]:
    _fp, body = read_catalog(lake, grant, "duckdb")
    return {d["name"] for d in body["datasets"]}


def _extended_lake(tmp_path: Path) -> Path:
    lake = _two_schema_lake(tmp_path)
    con = duckdb.connect(str(lake))
    try:
        con.execute("CREATE SCHEMA bronze2")
        con.execute("CREATE TABLE bronze2.orders (amount BIGINT, status VARCHAR)")
        con.execute("INSERT INTO bronze2.orders VALUES (9, 'BRONZE2CANARY')")
        con.execute("CREATE TABLE bronze._ingest_registry (table_name VARCHAR, src VARCHAR)")
        con.execute("INSERT INTO bronze._ingest_registry VALUES ('REGCANARY', 'x')")
        con.execute("CREATE TABLE bronze._land_row_log (k VARCHAR)")
        con.execute("INSERT INTO bronze._land_row_log VALUES ('LANDLOGCANARY')")
        con.execute("CREATE VIEW bronze.orders_v AS SELECT * FROM main.orders")
    finally:
        con.close()
    return lake


# ---- grant-name tricks, function level ------------------------------------------


@pytest.mark.parametrize(
    "grant",
    [
        {".orders"},
        {"bronze."},
        {"orders."},
        {"a.b.c"},
        {"lake.bronze.orders"},
        {'"bronze"."orders"'},
        {"bronze..orders"},
        {" "},
        {""},
        {"."},
        {".."},
        {"bronze.*"},
        {"bronze.%"},
        {"bronze.orders;"},
        {"bronze.orders.status"},
        {"main."},
        {"bronze2.inventory"},
        {"bronze.inventory"},
        {"landing_zone_7.inventory"},
    ],
)
def test_malformed_or_mismatched_grant_grants_nothing(tmp_path: Path, grant: set[str]) -> None:
    lake = _extended_lake(tmp_path)
    assert _names(grant, lake) == set()


@pytest.mark.parametrize(
    ("grant", "expected"),
    [
        ({"Bronze.Orders"}, {"bronze.orders"}),
        ({"BRONZE.ORDERS"}, {"bronze.orders"}),
        ({" bronze.orders "}, {"bronze.orders"}),
        ({"bronze2.orders"}, {"bronze2.orders"}),
        ({"main.orders"}, {"orders"}),
        ({"MAIN.ORDERS"}, {"orders"}),
    ],
)
def test_case_and_neighbour_schema_grants(
    tmp_path: Path, grant: set[str], expected: set[str]
) -> None:
    lake = _extended_lake(tmp_path)
    got = {n.lower() for n in _names(grant, lake)}
    assert got == expected


def test_registry_tables_and_views_never_listed_for_sibling_grant(tmp_path: Path) -> None:
    lake = _extended_lake(tmp_path)
    for grant in ({"bronze.orders"}, {"bronze.orders", "orders"}, {"bronze.unticked"}):
        _fp, body = read_catalog(lake, grant, "duckdb")
        text = json.dumps(body)
        for bad in ("_ingest_registry", "_land_row_log", "orders_v", "REGCANARY", "bronze2"):
            assert bad not in text, (grant, bad)


def test_index_never_reads_registry_or_neighbour_values(tmp_path: Path) -> None:
    lake = _extended_lake(tmp_path)
    space = "space_adv_registry"
    _forget_index(space)
    try:
        build_space_index(lake, space, {"bronze.orders"}, "duckdb")
        blob = json.dumps(cached_value_index(space)) + json.dumps(sc._space_samples(space))
        blob += _serving_file(lake, space).read_text()
        for bad in (
            "REGCANARY",
            "LANDLOGCANARY",
            "BRONZE2CANARY",
            "MAINONLYCANARYA",
            "THIRDCANARY",
            "UNTICKEDCANARY",
            "_ingest_registry",
            "hidden_col",
        ):
            assert bad not in blob, bad
    finally:
        _forget_index(space)


# ---- stale index under a narrower grant -----------------------------------------

WIDE = {"bronze.orders", "orders", "landing_zone_7.orders", "bronze.unticked"}
CANARIES = (
    "MAINONLYCANARYA",
    "THIRDCANARY",
    "UNTICKEDCANARY",
    "hidden_col",
    "third_marker",
    "main_only_marker",
)


def test_stale_wide_index_does_not_reach_a_narrow_prompt(tmp_path: Path) -> None:
    lake = _two_schema_lake(tmp_path)
    space = "space_adv_stale"
    _forget_index(space)
    try:
        assert build_space_index(lake, space, WIDE, "duckdb") == ""
        assert any("MAINONLYCANARYA" in json.dumps(v) for v in cached_value_index(space).values())
        ctx = prepare_generate_context(
            {},
            question="status WEB MAINONLYCANARYA THIRDCANARY",
            serving=lake,
            grantable={"bronze.orders"},
            dialect="duckdb",
            space_id=space,
        )
        prompt = str(ctx[SCHEMA_CONTEXT_FIELD]) + str(ctx.get(sc.SCHEMA_CONTEXT_ENVELOPE_KEY))
        for c in CANARIES:
            assert c not in prompt, c
        assert _tables_in(str(ctx[SCHEMA_CONTEXT_FIELD])) == ["bronze.orders"]
        # after the background rebuild the memory index must be the narrow one
        _join_workers()
        blob = json.dumps(cached_value_index(space)) + json.dumps(sc._space_samples(space))
        blob += _serving_file(lake, space).read_text()
        for c in CANARIES:
            assert c not in blob, c
    finally:
        _join_workers()
        _forget_index(space)


def test_wide_then_narrow_disk_record(tmp_path: Path) -> None:
    lake = _two_schema_lake(tmp_path)
    space = "space_adv_disk"
    _forget_index(space)
    try:
        build_space_index(lake, space, WIDE, "duckdb")
        build_space_index(lake, space, {"bronze.orders"}, "duckdb")
        text = _serving_file(lake, space).read_text()
        for c in (*CANARIES, "landing_zone_7", "unticked"):
            assert c not in text, c
    finally:
        _forget_index(space)


# ---- note_serving_source with grantable=None (what ingest does) -----------------


def test_file_space_ask_after_ingest_note_wire_and_envelope_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig = _file_space(tmp_path, monkeypatch, minter)
    note_serving_source(rig.lake, rig.space_id)
    _join_workers()
    env = rig.ask(ONE_TABLE_Q, ONE_TABLE_SQL)
    for body in rig.posts:
        for prompt in _schema_prompts(body):
            _assert_nothing_outside(prompt)
    _assert_nothing_outside(json.dumps(env))


# ---- chat ask narrowed to chosen tables ------------------------------------------


def test_ask_grounded_to_one_table_sends_only_that_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig = _file_space(tmp_path, monkeypatch, minter)
    build_space_index(rig.lake, rig.space_id, rig.grants(), "duckdb")
    # The HTTP path with grounded_tables takes the live-Cortex branch in this rig
    # (no Insights POST, an abstain envelope). Assert that envelope leaks nothing...
    r = rig.client.post(
        "/v1/chat/ask",
        json={
            "question": ONE_TABLE_Q,
            "session_id": "ses_adv_narrow",
            "space_id": rig.space_id,
            "grounded_tables": ["bronze.orders"],
        },
    )
    assert r.status_code == 200, r.text
    text = json.dumps(r.json())
    for bad in ("zq.canary", "Zanthippe", "North", "bronze.customers"):
        assert bad not in text, bad
    _assert_nothing_outside(text)
    # ...and drive the narrowed grant through the context builder against the wide index.
    ctx = prepare_generate_context(
        {},
        question="orders by region North",
        serving=rig.lake,
        grantable={"bronze.orders"},
        dialect="duckdb",
        space_id=rig.space_id,
    )
    p = str(ctx[SCHEMA_CONTEXT_FIELD]) + str(ctx.get(sc.SCHEMA_CONTEXT_ENVELOPE_KEY))
    assert _tables_in(str(ctx[SCHEMA_CONTEXT_FIELD])) == ["bronze.orders"], p
    for col in ("region", "email", "name"):
        assert f"- {col} " not in p
    assert "North" not in p and "South" not in p
    _join_workers()


def test_personal_context_catalog_excludes_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    rig = _file_space(tmp_path, monkeypatch, minter)
    grant = set(rig.exe.grantable_tables(space_id=None))
    _fp, body = read_catalog(rig.lake, grant, "duckdb")
    names = {d["name"] for d in body["datasets"]}
    assert not any("_ingest_registry" in n or "_land_row_log" in n for n in names), names
    assert "main_only_marker" not in json.dumps(body)  # main.orders is not a demo table
    for d in body["datasets"]:
        for c in d["columns"]:
            if "." in d["name"]:
                assert c["name"] not in ("_src", "_ingest_id")


# ---- dms_payload / schema hash with qualified grants ------------------------------


def test_dms_payload_does_not_read_a_main_table_with_a_dotted_name(tmp_path: Path) -> None:
    """A main table literally named "bronze.orders" must never be read for grant bronze.orders."""
    lake = _two_schema_lake(tmp_path)
    con = duckdb.connect(str(lake))
    try:
        con.execute('CREATE TABLE main."bronze.orders" (channel VARCHAR)')
        con.execute("INSERT INTO main.\"bronze.orders\" VALUES ('DOTTEDCANARY')")
    finally:
        con.close()
    out = build_dms_payload("orders by channel", serving=lake, readable={"bronze.orders"})
    assert "DOTTEDCANARY" not in json.dumps(out)
    assert "DOTTEDCANARY" not in json.dumps(read_catalog(lake, {"bronze.orders"}, "duckdb")[1])


def test_schema_hash_differs_between_qualified_and_bare_grants(tmp_path: Path) -> None:
    from dms_executor.verified_queries import _schema_hash

    lake = _two_schema_lake(tmp_path)
    hashes = {
        _schema_hash(lake, g, "duckdb")
        for g in ({"orders"}, {"bronze.orders"}, {"bronze.orders", "orders"})
    }
    assert len(hashes) == 3 and "" not in hashes
