"""UNICODE-IDENT-01: stable ASCII identifiers for non-ASCII headers.

``DMS_UNICODE_IDENT`` defaults off. Off, the fold and the schema prompt stay
the ones this file's legacy replica produces. On, a name that still has a
non-ASCII character lands as ``<ascii stem or t>_<8 hex of sha256(full name)>``
and the original is ``source_name``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import duckdb
import pytest
import sqlglot
from dms_executor import db_connector as dbc
from dms_executor.bronze import (
    ident_safe,
    ingest_csv_bytes,
    relabel_row_headers,
)
from dms_executor.db_connector import SourceTable, _bronze_name
from dms_executor.envelope import build_answer_envelope
from dms_executor.schema_context import build_schema_context, schema_from_landed
from dms_executor.space_ontology import _wire_id
from sqlglot import exp

_HEADERS = ("销售额", "数量", "库存表", "仓库", "jumlah_jualan")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _legacy_ident(stem: str) -> str:
    """The flag-off body of ``ident_safe``. A drift here is a byte change."""
    out = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in stem)
    if not out or not (out[0].isalpha() or out[0] == "_"):
        out = f"t_{out}"
    return out


def _legacy_wire(name: str) -> str | None:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned or None


def _csv() -> bytes:
    return (",".join(_HEADERS) + "\n1,2,3,4,5\n").encode()


def _columns(path: Path, qualified: str) -> list[str]:
    schema, _, table = qualified.partition(".")
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            ORDER BY ordinal_position
            """,
            [schema, table],
        ).fetchall()
    finally:
        con.close()
    return [str(row[0]) for row in rows if str(row[0]) not in {"_src", "_ingest_id"}]


def _duckdb_headers(data: bytes, tmp: Path) -> list[str]:
    """Same ``read_csv`` options ``ingest_csv_bytes`` uses, on a scratch file."""
    path = tmp / "headers.csv"
    path.write_bytes(data)
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            CREATE TABLE t AS SELECT * FROM read_csv(
              '{path.as_posix()}',
              header := true,
              auto_detect := true,
              delim := ',',
              quote := '"',
              sample_size := -1
            )
            """
        )
        rows = con.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 't' ORDER BY ordinal_position
            """
        ).fetchall()
    finally:
        con.close()
    return [str(row[0]) for row in rows]


@pytest.fixture
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "unicode.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.delenv("CORTEX_WAREHOUSE_DB", raising=False)
    return path


def test_flag_off_matches_the_legacy_fold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_UNICODE_IDENT", raising=False)
    samples = ("销售额", "库存表", "数量", "仓库", "jumlah_jualan", "2024_sales", "dbo.sales")
    for name in samples:
        assert ident_safe(name) == _legacy_ident(name)
        assert _wire_id(name) == _legacy_wire(name)
    assert ident_safe("销售额") == ident_safe("库存表") == "___"
    assert ident_safe("数量") == ident_safe("仓库") == "__"
    monkeypatch.setenv("DMS_UNICODE_IDENT", "0")
    for name in samples:
        assert ident_safe(name) == _legacy_ident(name)
    assert _bronze_name(SourceTable("dbo", "销售额")) == _bronze_name(SourceTable("dbo", "库存表"))


def test_flag_off_prompt_is_unchanged_for_an_ascii_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = {
        "dialect": "duckdb",
        "datasets": [
            {
                "name": "sales",
                "columns": [{"name": "jumlah_jualan", "type": "DECIMAL"}],
            }
        ],
    }
    monkeypatch.delenv("DMS_UNICODE_IDENT", raising=False)
    off = build_schema_context("total", schema).prompt
    monkeypatch.setenv("DMS_UNICODE_IDENT", "0")
    zero = build_schema_context("total", schema).prompt
    monkeypatch.setenv("DMS_UNICODE_IDENT", "1")
    armed = build_schema_context("total", schema).prompt
    assert off == zero == armed
    assert "source_name:" not in off


def test_flag_on_hash_is_the_full_source_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_UNICODE_IDENT", "1")
    for name, stem in (("销售额", "t"), ("数量", "t"), ("qty销售额", "qty")):
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        assert ident_safe(name) == f"{stem}_{digest}"
        assert _IDENT.fullmatch(ident_safe(name))
    assert ident_safe("销售额") != ident_safe("库存表")
    assert ident_safe("数量") != ident_safe("仓库")
    assert ident_safe("jumlah_jualan") == "jumlah_jualan"
    assert _wire_id("销售额") != _wire_id("库存表")
    assert _wire_id("销售额") == ident_safe("销售额")
    left = _bronze_name(SourceTable("dbo", "销售额"))
    right = _bronze_name(SourceTable("dbo", "库存表"))
    assert left != right
    assert _IDENT.fullmatch(left) and _IDENT.fullmatch(right)
    assert left == ident_safe("dbo.销售额")


def test_csv_lands_five_distinct_identifiers(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_UNICODE_IDENT", raising=False)
    off = ingest_csv_bytes(filename="stock.csv", data=_csv(), path=warehouse)
    assert off.column_sources is None
    assert off.source_name is None
    off_cols = _columns(warehouse, off.table or "")
    assert off_cols == _duckdb_headers(_csv(), warehouse.parent)

    monkeypatch.setenv("DMS_UNICODE_IDENT", "1")
    on = ingest_csv_bytes(filename="stock.csv", data=_csv(), path=warehouse)
    assert on.ingested == 1
    assert on.source_name == "stock"
    assert on.column_sources is not None
    landed = _columns(warehouse, on.table or "")
    assert landed == list(on.column_sources)
    assert len(set(landed)) == 5
    assert all(_IDENT.fullmatch(name) for name in landed)
    assert set(on.column_sources.values()) == set(_HEADERS)
    assert on.column_sources["jumlah_jualan"] == "jumlah_jualan"
    assert on.column_sources[ident_safe("销售额")] == "销售额"
    assert ident_safe("销售额") != ident_safe("库存表")

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        row = con.execute(
            """
            SELECT source_name, column_sources FROM bronze._ingest_registry
            WHERE table_name = ?
            """,
            [(on.table or "").split(".", 1)[-1]],
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row[0] == "stock"
    stored = json.loads(row[1])
    assert stored == on.column_sources

    built = build_schema_context("rows", schema_from_landed(warehouse, on.table or ""))
    for source in _HEADERS:
        assert f"source_name: {source}" in built.prompt
    for ident, source in on.column_sources.items():
        assert f"column: {ident}  source_name: {source}  type:" in built.prompt

    relabeled = relabel_row_headers([{ident: 1 for ident in landed}], path=warehouse)
    assert list(relabeled[0]) == list(_HEADERS)
    env = build_answer_envelope(
        answer_id="a-unicode",
        text="See the table.",
        badge="L2_VALIDATED",
        abstained=False,
        sql_used="SELECT " + ", ".join(landed) + f" FROM {on.table}",
        rows=[{ident: 1 for ident in landed}],
        ask_mode="demo",
    )
    assert env["abstained"] is False
    assert list(env["rows"][0]) == list(_HEADERS)


def test_flag_on_sql_pull_keeps_the_bronze_table_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The column rewrite must not overwrite the table name it just claimed."""
    monkeypatch.setenv("DMS_UNICODE_IDENT", "1")
    warehouse = tmp_path / "pull.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    target = SourceTable(schema="dbo", name="库存表")
    monkeypatch.setattr(
        dbc,
        "_fetch",
        lambda *_a, **_k: (["销售额", "jumlah_jualan"], [["1", "2"]], False),
    )
    monkeypatch.setattr(dbc, "list_source_column_types", lambda *_a, **_k: None)
    cfg = dbc.SourceConfig(kind="postgresql", host="h", database="d", user="u")
    pull = dbc._pull_one(cfg, object(), target, max_rows=10, path=warehouse, space_id=None)
    bare = pull.bronze_table.split(".", 1)[-1]
    assert bare == _bronze_name(target)
    assert bare != "jumlah_jualan"
    cols = _columns(warehouse, pull.bronze_table)
    assert ident_safe("销售额") in cols
    assert "jumlah_jualan" in cols
    assert pull.source_name == "dbo.库存表"
    assert pull.column_sources[ident_safe("销售额")] == "销售额"
    assert pull.column_sources["jumlah_jualan"] == "jumlah_jualan"


def test_caller_sql_grammar_accepts_each_identifier(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identifier shape ``validate_caller_sql`` resolves: one unquoted ASCII part.

    Cortex folds ASCII case only. A declared catalog keyed by that fold accepts
    every landed name, and DuckDB runs the same SELECT.
    """
    monkeypatch.setenv("DMS_UNICODE_IDENT", "1")
    receipt = ingest_csv_bytes(filename="stock.csv", data=_csv(), path=warehouse)
    assert receipt.column_sources is not None
    table = (receipt.table or "").split(".", 1)[-1]
    idents = list(receipt.column_sources)
    declared = {_fold(f"bronze.{table}"): [_fold(name) for name in idents]}
    sql = "SELECT " + ", ".join(idents) + f" FROM bronze.{table}"
    tree = sqlglot.parse_one(sql, read="duckdb")
    assert isinstance(tree, exp.Select)
    seen = [col.name for col in tree.find_all(exp.Column)]
    assert seen == idents
    tables = [node.name for node in tree.find_all(exp.Table)]
    assert tables == [table]
    folded_cols = declared[_fold(f"bronze.{table}")]
    assert [_fold(name) for name in seen] == folded_cols
    for name in (*idents, table):
        assert _IDENT.fullmatch(name)
        assert name.translate(_ASCII_FOLD) == _fold(name)

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    assert row == (1, 2, 3, 4, 5)


def _fold(name: str) -> str:
    return name.translate(_ASCII_FOLD)
