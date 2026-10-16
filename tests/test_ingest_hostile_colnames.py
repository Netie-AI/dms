"""A source column name is one quoted identifier, never SQL (integrity lens, pre-existing).

``write_bronze_rows`` interpolated each source column name into DDL as ``"{c}" VARCHAR``
with no escaping of the double quote, and DuckDB's ``execute`` runs a multi-statement
string. Whoever controls a column name in a connected source could run a second statement
on the lake (``CREATE TABLE``, ``COPY ... TO``, ``ATTACH``, ``read_csv`` of a local file),
and a name holding a single ``"`` was a raw ``ParserException`` (a 500).

The same name then lands in the lake, and every reader that quotes it the same way is a
second sink. ``contract_infer`` is one: it reads the landed column names and interpolates
them into ``SELECT COUNT("{col}")``. Fixing the writer alone moves the statement to the
reader, so both are pinned here, each reached through the API a customer calls
(``POST /v1/studio/sources/sql``, then ``POST /v1/pipelines/infer-contract`` and
``POST /v1/chat/ask``) and each asserting the landed rows, not the SQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor.bronze import write_bronze_rows
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import set_ontology_store
from test_ingest_fixes_277 import _oracle, _Rig, _types
from test_onto_derive_01 import make_minter


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture()
def store() -> Iterator[OntologyStore]:
    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


EVIL_DDL = 'x" VARCHAR); CREATE TABLE main.pwned_by_colname AS SELECT 42 AS v; --'
LONE_QUOTE = 'Size 5" tube'


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _ingest_one_extra_column(
    rig: _Rig, space: str, column: str, values: tuple[str, str] = ("a", "b")
) -> dict[str, Any]:
    """Land ``public.sales(id, <column>)`` through ``POST /v1/studio/sources/sql``."""
    return rig.ingest(
        space,
        tables=[("public", "sales")],
        data={'"public"."sales"': (["id", column], [[1, values[0]], [2, values[1]]])},
        types={"public.sales": [("id", "integer", 32, 0), (column, "text", None, None)]},
    )


def _lake_tables(lake: Path) -> set[str]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        return {
            str(r[0])
            for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
    finally:
        con.close()


def test_a_source_column_name_cannot_run_a_second_statement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Inj")
    receipt = _ingest_one_extra_column(rig, space, EVIL_DDL)

    assert "pwned_by_colname" not in _lake_tables(rig.lake), (
        "a source column name executed a second DuckDB statement"
    )
    # The name is one identifier: the table landed under its true column names, and the
    # rows are what the source held, readable through that exact quoted name.
    landed = _types(rig.lake, "bronze.public_sales")
    assert list(landed) == ["id", EVIL_DDL, "_src", "_ingest_id"]
    assert _oracle(
        rig.lake, f"SELECT id, {_q(EVIL_DDL)} AS v FROM bronze.public_sales ORDER BY id"
    ) == [
        {"id": 1, "v": "a"},
        {"id": 2, "v": "b"},
    ]
    table = receipt["tables"][0]
    assert table["bronze_table"] == "bronze.public_sales"
    assert table["row_count"] == 2
    assert table["note"] is None, "a legal name must not be renamed"


def test_a_lone_double_quote_in_a_column_name_lands_as_that_name_not_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Quote")
    receipt = _ingest_one_extra_column(rig, space, LONE_QUOTE, values=("S", "M"))

    assert list(_types(rig.lake, "bronze.public_sales")) == ["id", LONE_QUOTE, "_src", "_ingest_id"]
    assert _oracle(
        rig.lake, f"SELECT {_q(LONE_QUOTE)} AS size FROM bronze.public_sales ORDER BY id"
    ) == [{"size": "S"}, {"size": "M"}]
    table = receipt["tables"][0]
    assert table["row_count"] == 2
    assert table["column_types"][LONE_QUOTE] == "VARCHAR"
    assert table["note"] is None


def test_a_landed_hostile_name_does_not_run_when_the_contract_is_inferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    """The writer is fixed, the name lands, and a reader that re-quotes it must not run it.

    ``infer_contract`` runs ``SELECT COUNT("{col}") FROM "bronze"."public_sales"`` over the
    landed column names. This name closes that statement, runs a ``COPY ... TO`` (which a
    read-only connection still allows) and reopens a statement that parses.
    """
    import dms_api.routes.pipelines as pipeline_routes
    from cortex_client.gate import ComplianceDecision

    exfil = tmp_path / "exfil_from_infer.csv"
    evil = (
        'x") FROM "bronze"."public_sales"; '
        f"COPY (SELECT 42 AS v) TO '{exfil.as_posix()}' (FORMAT CSV); "
        'SELECT COUNT("x'
    )
    rig = _Rig(tmp_path, monkeypatch, minter)
    monkeypatch.setattr(
        pipeline_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(allowed=True, reason="test_allow", action=action),
    )
    space = rig.space("Infer")
    _ingest_one_extra_column(rig, space, evil)
    assert not exfil.exists(), "the ingest ran the name as a statement"

    r = rig.client.post("/v1/pipelines/infer-contract", json={"source": "bronze.public_sales"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not exfil.exists(), "infer_contract ran a landed column name as a second statement"
    assert body["row_count"] == 2
    assert set(body["columns"]) == {"id", evil}
    assert body["columns"][evil]["null_rate"] == 0.0
    assert body["null_rates"][evil] == 0.0


def test_a_control_character_or_empty_name_lands_renamed_and_the_receipt_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    """NUL and the empty string cannot be a DuckDB identifier: they land under a legal
    name and the receipt names the change, the way a case-duplicate column already does."""
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Control")
    names = ["id", "a\x00b", "", "line1\nline2"]
    receipt = rig.ingest(
        space,
        tables=[("public", "sales")],
        data={'"public"."sales"': (names, [[1, "n", "e", "l"], [2, "N", "E", "L"]])},
        types={
            "public.sales": [("id", "integer", 32, 0)]
            + [(n, "text", None, None) for n in names[1:]]
        },
    )

    landed = [c for c in _types(rig.lake, "bronze.public_sales") if c not in {"_src", "_ingest_id"}]
    assert landed == ["id", "a_b", "column_3", "line1_line2"]
    assert _oracle(
        rig.lake,
        "SELECT id, a_b, column_3, line1_line2 FROM bronze.public_sales ORDER BY id",
    ) == [
        {"id": 1, "a_b": "n", "column_3": "e", "line1_line2": "l"},
        {"id": 2, "a_b": "N", "column_3": "E", "line1_line2": "L"},
    ]
    table = receipt["tables"][0]
    assert table["row_count"] == 2
    for original, now in (("a\x00b", "a_b"), ("", "column_3"), ("line1\nline2", "line1_line2")):
        assert f"column {original!r} landed as {now!r}" in (table["note"] or ""), table["note"]
    # The declared type followed the column to its landed name.
    assert table["column_types"]["a_b"] == "VARCHAR"
    assert table["untyped_columns"] == {}


def test_two_names_that_clean_to_the_same_name_both_land(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Clash")
    names = ["id", "a\x00b", "a\x01b"]
    receipt = rig.ingest(
        space,
        tables=[("public", "sales")],
        data={'"public"."sales"': (names, [[1, "first", "second"]])},
        types={
            "public.sales": [("id", "integer", 32, 0)]
            + [(n, "text", None, None) for n in names[1:]]
        },
    )
    assert _oracle(rig.lake, "SELECT id, a_b, a_b_2 FROM bronze.public_sales") == [
        {"id": 1, "a_b": "first", "a_b_2": "second"}
    ]
    note = receipt["tables"][0]["note"] or ""
    assert "column 'a\\x00b' landed as 'a_b'" in note
    assert "column 'a\\x01b' landed as 'a_b_2'" in note


def test_a_cleaned_name_never_takes_a_legal_column_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    """Adversary round 0 (integrity, P0). ``budget\\x01`` cleans to ``budget_``, which is the
    legal column's own name. Landed in source order the cleaned name took it: the legal column
    landed as ``budget__2`` and ``SUM(budget_)`` summed the hostile column (3, not 30) under a
    confirmed measure. A legal name keeps its name; only the cleaned one is renamed, and the
    receipt says so."""
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Twin")
    names = ["id", "budget\x01", "budget_"]
    receipt = rig.ingest(
        space,
        tables=[("public", "sales")],
        data={'"public"."sales"': (names, [[1, 1, 10], [2, 2, 20]])},
        types={"public.sales": [(n, "integer", 32, 0) for n in ["id", *names[1:]]]},
    )
    assert _oracle(rig.lake, "SELECT SUM(budget_) AS v FROM bronze.public_sales") == [{"v": 30}]
    assert _oracle(rig.lake, "SELECT SUM(budget__2) AS v FROM bronze.public_sales") == [{"v": 3}]
    note = receipt["tables"][0]["note"] or ""
    assert "column 'budget\\x01' landed as 'budget__2'" in note, note
    assert "budget_'" not in note, note  # the legal column is not renamed
    env = rig.ask(
        space,
        "What is the total budget of all sales?",
        "SELECT SUM(budget_) AS v FROM bronze.public_sales",
    )
    if env["abstained"]:
        assert env["badge"] != "L2_VALIDATED", (env["badge"], env.get("text"))
    else:
        assert env["rows"] == [{"v": 30}], env["rows"]


def test_a_hostile_named_column_answers_at_the_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    """The hostile name lands and a question over it is answered from the landed rows."""
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Ask")
    names = ["id", LONE_QUOTE]
    rig.ingest(
        space,
        tables=[("public", "sales")],
        data={'"public"."sales"': (names, [[1, "S"], [2, "M"], [3, "S"]])},
        types={"public.sales": [("id", "integer", 32, 0), (LONE_QUOTE, "text", None, None)]},
    )
    sql = f"SELECT COUNT(*) AS n FROM bronze.public_sales WHERE {_q(LONE_QUOTE)} = 'S'"
    assert _oracle(rig.lake, sql) == [{"n": 2}]
    env = rig.ask(space, "How many sales are size S?", sql)
    # Whatever the lane decides, the envelope must not state a figure the lake does not
    # hold: either it answers 2 from the executed rows, or it abstains in words.
    if env["abstained"]:
        assert env["badge"] != "L2_VALIDATED", (env["badge"], env.get("text"))
    else:
        assert env["rows"] == [{"n": 2}], env["rows"]
        assert "2" in str(env.get("text") or ""), env.get("text")


# --- the sink itself, in addition to the API-level cases above -------------------------


def test_write_bronze_rows_quotes_every_column_name(tmp_path: Path) -> None:
    lake = tmp_path / "unit.duckdb"
    columns = ["plain", 'q"uote', EVIL_DDL, "semi;colon", "it's", "--c", "/*c*/"]
    row = [str(i) for i in range(len(columns))]
    landed = write_bronze_rows(table="hostile", columns=columns, rows=[row], path=lake)
    assert landed == "bronze.hostile"
    assert "pwned_by_colname" not in _lake_tables(lake)
    got = _oracle(lake, "SELECT * EXCLUDE (_src, _ingest_id) FROM bronze.hostile")
    assert got == [dict(zip(columns, row, strict=True))]


@pytest.mark.parametrize("bad", ["a\x00b", ""])
def test_write_bronze_rows_refuses_a_name_that_cannot_be_an_identifier(
    tmp_path: Path, bad: str
) -> None:
    lake = tmp_path / "unit.duckdb"
    write_bronze_rows(table="kept", columns=["ok", "also"], rows=[["old", "row"]], path=lake)
    with pytest.raises(ValueError, match="identifier"):
        write_bronze_rows(table="kept", columns=["ok", bad], rows=[["new", "row"]], path=lake)
    # A refused write is refused before the lake is touched: the table it would have
    # replaced still holds the rows it held.
    assert _oracle(lake, "SELECT ok, also FROM bronze.kept") == [{"ok": "old", "also": "row"}]


def test_quote_ident_escapes_the_quote_and_refuses_nul_and_empty() -> None:
    from dms_executor.bronze import quote_ident

    assert quote_ident("a") == '"a"'
    assert quote_ident('a"b') == '"a""b"'
    assert quote_ident('"') == '""""'
    for bad in ("", "a\x00b", "\x00"):
        with pytest.raises(ValueError, match="identifier"):
            quote_ident(bad)
