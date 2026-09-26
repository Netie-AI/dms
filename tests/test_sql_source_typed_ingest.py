"""TYPED-INGEST (dms#277): a SQL source lands in bronze with its own column types.

Batch 1's bulk load quoted every value, so every SQL-sourced bronze column was
VARCHAR and a generated ``SUM(amount)`` failed to bind - the ask abstained on a
question the data answers (the largest known BIRD accuracy cost). Parent: every
column below lands VARCHAR, and the envelope ask is an ABSTAIN.

Three sources, one set of assertions:

* a fake DB-API connection shaped like psycopg (type OIDs, native values) - runs
  everywhere;
* a throwaway local PostgreSQL 16 cluster (``initdb`` under /tmp) - the real driver,
  the real ``cursor.description``;
* the SPACE-GEN-01 rig for the customer envelope: ``POST /v1/chat/ask`` on a table
  landed by ``ingest_source_database`` answers SUM / AVG at L2 with the oracle value.

"Exact or VARCHAR": a column whose values cannot all be kept (a Postgres numeric
``NaN``) lands VARCHAR for that column only, and the note is visible on the receipt,
the Library preview and the Space sources list.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_executor import db_connector as dbc
from dms_executor.bronze import list_source_pulls
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.manifest import ManifestMinter
from dms_executor.source_types import ColumnType, map_description, prepare_columns
from test_space_gen_01 import _ask, _rig, minter  # noqa: F401

PG_BIN = Path(os.environ.get("DMS_PG_BIN", "/usr/lib/postgresql/16/bin"))
BIG = 2**53 + 1  # not representable as a double: a float round-trip would change it

#: The source table. Column order matches EXPECTED_TYPES.
PG_DDL = """
CREATE TABLE public.payments (
  id        integer,
  big_id    bigint,
  amount    numeric(12,4),
  score     double precision,
  paid      boolean,
  due       date,
  booked_at timestamp,
  zip       text,
  ratio     numeric
)
"""
PG_ROWS: list[tuple[Any, ...]] = [
    (1, BIG, Decimal("10.5000"), 0.1, True, dt.date(2024, 2, 29),
     dt.datetime(2024, 1, 1, 10, 0, 0, 123456), "01234", Decimal("1.5")),
    (2, -(2**62), Decimal("20.2500"), -1.5e300, False, dt.date(1999, 12, 31),
     dt.datetime(1999, 12, 31, 23, 59, 59), "00501", Decimal("NaN")),
    (3, None, None, None, None, None, None, None, None),
    (4, 7, Decimal("1234.5678"), 3.0, True, dt.date(2000, 1, 1),
     dt.datetime(2000, 1, 1, 0, 0, 0), "", Decimal("2")),
]
EXPECTED_TYPES = {
    "id": "INTEGER",
    "big_id": "BIGINT",
    "amount": "DECIMAL(12,4)",
    "score": "DOUBLE",
    "paid": "BOOLEAN",
    "due": "DATE",
    "booked_at": "TIMESTAMP",
    "zip": "VARCHAR",
    # Postgres numeric NaN has no DECIMAL form: this column falls back, with a note.
    "ratio": "VARCHAR",
}
SUM_AMOUNT = Decimal("1265.3178")
AVG_AMOUNT = SUM_AMOUNT / 3

#: psycopg's cursor.description for PG_DDL: (name, type_oid, display, internal, p, s, null_ok)
PSYCOPG_DESCRIPTION = [
    ("id", 23, None, 4, None, None, None),
    ("big_id", 20, None, 8, None, None, None),
    ("amount", 1700, None, None, 12, 4, None),
    ("score", 701, None, 8, None, None, None),
    ("paid", 16, None, 1, None, None, None),
    ("due", 1082, None, 4, None, None, None),
    ("booked_at", 1114, None, 8, None, None, None),
    ("zip", 25, None, None, None, None, None),
    ("ratio", 1700, None, None, None, None, None),
]


# --- sources -----------------------------------------------------------------------


class _Cursor:
    def __init__(self, owner: _PsycopgShaped) -> None:
        self._owner = owner
        self._rows: list[tuple[Any, ...]] = []
        self.description: list[tuple[Any, ...]] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        if "INFORMATION_SCHEMA.TABLES" in sql:
            self._rows = [("public", "payments")]
        elif "INFORMATION_SCHEMA" in sql:
            self._rows = []
        else:
            self.description = list(PSYCOPG_DESCRIPTION)
            self._rows = list(PG_ROWS)

    def fetchall(self) -> list[tuple[Any, ...]]:
        out, self._rows = self._rows, []
        return out

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        out, self._rows = self._rows[:size], self._rows[size:]
        return out

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def close(self) -> None:
        return None


class _PsycopgShaped:
    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _as_pg_user(cmd: list[str]) -> list[str]:
    # initdb refuses to run as root; the cluster is owned by the postgres account.
    if os.geteuid() == 0:
        return ["runuser", "-u", "postgres", "--", *cmd]
    return cmd


@pytest.fixture(scope="module")
def live_pg() -> Iterator[dbc.SourceConfig]:
    """A throwaway PostgreSQL 16 cluster holding ``public.payments``.

    R-0002: a skipped test is a failing test. No Postgres binaries is a hard failure
    unless the run said so out loud with ``DMS_SKIP_CONTROL_PLANE_TESTS=1`` (the same
    "this machine has no Postgres" decision the control-plane suite honours).
    """
    initdb = PG_BIN / "initdb"
    if not initdb.is_file():
        if os.environ.get("DMS_SKIP_CONTROL_PLANE_TESTS", "").lower() in {"1", "true", "yes"}:
            pytest.skip(f"DMS_SKIP_CONTROL_PLANE_TESTS set; no PostgreSQL at {PG_BIN}")
        raise AssertionError(
            f"typed-ingest live test needs PostgreSQL binaries at {PG_BIN} "
            "(set DMS_PG_BIN), or DMS_SKIP_CONTROL_PLANE_TESTS=1 to skip deliberately"
        )
    import psycopg

    root = Path(tempfile.mkdtemp(prefix="dms_typed_pg_", dir="/tmp"))
    root.chmod(0o777)
    if os.geteuid() == 0:
        shutil.chown(root, user="postgres")
    data = root / "data"
    port = _free_port()
    subprocess.run(
        _as_pg_user([str(initdb), "-D", str(data), "-A", "trust", "-U", "postgres",
                     "-E", "UTF8", "--locale=C.UTF-8"]),
        check=True, capture_output=True,
    )
    pg_ctl = str(PG_BIN / "pg_ctl")
    subprocess.run(
        _as_pg_user([pg_ctl, "-D", str(data), "-l", str(root / "log"), "-w", "-o",
                     f"-p {port} -k {root} -c listen_addresses=127.0.0.1", "start"]),
        check=True, capture_output=True,
    )
    try:
        with psycopg.connect(
            host="127.0.0.1", port=port, user="postgres", dbname="postgres", autocommit=True
        ) as con:
            con.execute(PG_DDL)
            with con.cursor() as cur:
                cur.executemany(
                    "INSERT INTO public.payments VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", PG_ROWS
                )
        yield dbc.SourceConfig(
            kind="postgresql", host="127.0.0.1", port=port, database="postgres",
            user="postgres", password="",
        )
    finally:
        subprocess.run(
            _as_pg_user([pg_ctl, "-D", str(data), "-m", "immediate", "stop"]),
            capture_output=True,
        )
        shutil.rmtree(root, ignore_errors=True)


@contextmanager
def _fake_source(monkeypatch: pytest.MonkeyPatch) -> Iterator[dbc.SourceConfig]:
    @contextmanager
    def _connect(cfg: dbc.SourceConfig) -> Iterator[Any]:
        yield _PsycopgShaped()

    monkeypatch.setattr(dbc, "connect", _connect)
    yield dbc.SourceConfig(kind="postgresql", host="pg.example.net", database="bird", user="r")


@pytest.fixture(params=["psycopg_shaped", "live_pg16"])
def source(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    if request.param == "live_pg16":
        yield request.getfixturevalue("live_pg")
        return
    with _fake_source(monkeypatch) as cfg:
        yield cfg


@pytest.fixture()
def wh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "lake.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    ensure_demo_warehouse(db)
    return db


def _bronze(wh: Path, table: str) -> tuple[dict[str, str], list[tuple[Any, ...]]]:
    con = duckdb.connect(str(wh), read_only=True)
    try:
        types = {
            str(r[0]): str(r[1])
            for r in con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'bronze' AND table_name = ? ORDER BY ordinal_position",
                [table],
            ).fetchall()
            if not str(r[0]).startswith("_")
        }
        cols = ", ".join(f'"{c}"' for c in types)
        rows = [
            tuple(r)
            for r in con.execute(f"SELECT {cols} FROM bronze.{table} ORDER BY id").fetchall()
        ]
    finally:
        con.close()
    return types, rows


# --- landing ------------------------------------------------------------------------


def test_source_types_land_and_every_value_round_trips(source: Any, wh: Path) -> None:
    extract = dbc.ingest_source_database(source, tables=["payments"], path=wh)
    [pull] = extract.pulls
    assert pull.bronze_table == "bronze.public_payments"
    types, rows = _bronze(wh, "public_payments")
    assert types == EXPECTED_TYPES
    assert pull.column_types == EXPECTED_TYPES

    for got, want in zip(rows, PG_ROWS, strict=True):
        exp = list(want)
        # The one fallback column keeps the connector's str() text, NULL stays NULL.
        exp[8] = None if want[8] is None else str(want[8])
        assert list(got) == exp, (got, want)
    # Exactness, not just equality under coercion.
    assert rows[0][1] == BIG and isinstance(rows[0][1], int)
    assert rows[0][2] == Decimal("10.5000") and rows[0][2].as_tuple().exponent == -4
    assert rows[0][7] == "01234" and rows[3][7] == ""  # text that looks numeric stays text
    assert all(v is None for v in rows[2][1:])  # NULLs stay NULL, not '' or 'None'


def test_sum_and_avg_bind_over_the_numeric_column(source: Any, wh: Path) -> None:
    dbc.ingest_source_database(source, tables=["payments"], path=wh)
    con = duckdb.connect(str(wh), read_only=True)
    try:
        total, avg, n_paid = con.execute(
            "SELECT SUM(amount), AVG(amount), COUNT(*) FILTER (WHERE paid) "
            "FROM bronze.public_payments"
        ).fetchone()
    finally:
        con.close()
    assert total == SUM_AMOUNT
    assert avg == pytest.approx(float(AVG_AMOUNT))
    assert n_paid == 2


def test_a_column_that_cannot_be_kept_lands_varchar_with_a_visible_note(
    source: Any, wh: Path
) -> None:
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    extract = dbc.ingest_source_database(source, tables=["payments"], path=wh, space_id="sp1")
    [pull] = extract.pulls
    assert len(pull.type_notes) == 1, pull.type_notes
    note = pull.type_notes[0]
    assert note.startswith("ratio:") and "numeric" in note and "VARCHAR" in note, note

    # On the source, not just the in-process receipt: Space sources and the preview.
    [listed] = [s for s in list_source_pulls(path=wh) if s["bronze_table"] == pull.bronze_table]
    assert listed["type_notes"] == [note]
    body = TestClient(create_app()).get(
        "/v1/library/bronze/public_payments/preview", params={"limit": 10}
    ).json()
    assert body["type_notes"] == [note], body


def test_sql_source_route_receipt_names_types_and_notes(
    wh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The receipt the steward reads after POST /v1/studio/sources/sql."""
    from dms_api.wiring import sql_source_ingest

    with _fake_source(monkeypatch):
        receipt = sql_source_ingest(
            kind="postgresql", host="pg.example.net", database="bird", user="r", password="x",
        )
    [table] = receipt["tables"]
    assert table["column_types"] == EXPECTED_TYPES
    assert len(table["type_notes"]) == 1 and table["type_notes"][0].startswith("ratio:")


# --- mapping unit cases -------------------------------------------------------------


def test_duckdb_cast_failure_falls_back_for_that_column_only(wh: Path) -> None:
    """A value that passed the Python check but DuckDB will not cast still falls back."""
    from dms_executor.bronze import write_typed_bronze_rows

    cols = [ColumnType("k", "int4", "INTEGER"), ColumnType("t", "time", "TIME")]
    rows: list[list[Any]] = [[1, dt.time(1, 2, 3)], [2, None]]
    landed = write_typed_bronze_rows(table="t_ok", column_types=cols, rows=rows, path=wh)
    assert landed.column_types == {"k": "INTEGER", "t": "TIME"} and landed.type_notes == []

    # A MySQL zero date comes back from the driver as text, not a date.
    cols = [ColumnType("k", "int4", "INTEGER"), ColumnType("d", "date", "DATE")]
    rows = [[1, dt.date(2020, 1, 1)], [2, "0000-00-00"]]
    landed = write_typed_bronze_rows(table="t_bad", column_types=cols, rows=rows, path=wh)
    assert landed.column_types == {"k": "INTEGER", "d": "VARCHAR"}
    assert len(landed.type_notes) == 1 and landed.type_notes[0].startswith("d:")
    con = duckdb.connect(str(wh), read_only=True)
    try:
        got = con.execute("SELECT k, d FROM bronze.t_bad ORDER BY k").fetchall()
    finally:
        con.close()
    assert got == [(1, "2020-01-01"), (2, "0000-00-00")]


def test_mapping_across_drivers() -> None:
    import decimal

    pg = map_description("postgresql", [("x", 2950, None, 16, None, None, None)])
    assert pg[0].duck_type == "VARCHAR" and pg[0].note  # uuid: unknown, recorded
    bare = map_description("postgresql", [("x",)])
    assert bare[0].duck_type == "VARCHAR" and bare[0].note
    my = map_description("mysql", [("a", 246, None, 14, 14, 4, True), ("b", 8, None, 20, 20, 0, 1)])
    assert [c.duck_type for c in my] == ["DECIMAL(?,4)", "BIGINT"]
    ms = map_description(
        "sqlserver",
        [("a", decimal.Decimal, None, 12, 12, 4, True), ("b", bool, None, 1, 1, 0, True)],
    )
    assert [c.duck_type for c in ms] == ["DECIMAL(12,4)", "BOOLEAN"]

    # MySQL UNSIGNED BIGINT beyond int64 widens exactly; DECIMAL(?,s) resolves from values.
    prepared = prepare_columns(
        my, [[Decimal("12345678.1234"), 2**64 - 1], [None, 1]]
    )
    assert [p.duck_type for p in prepared] == ["DECIMAL(12,4)", "HUGEINT"]
    assert prepared[1].values == [str(2**64 - 1), "1"]
    assert prepared[1].note and "HUGEINT" in prepared[1].note


# --- the customer envelope ------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "sql", "column", "oracle"),
    [
        (
            "What is the total amount of payments?",
            "SELECT SUM(amount) AS total_amount FROM bronze.public_payments",
            "total_amount",
            SUM_AMOUNT,
        ),
        (
            "What is the average payment amount?",
            "SELECT ROUND(AVG(amount), 4) AS avg_amount FROM bronze.public_payments",
            "avg_amount",
            Decimal("421.7726"),
        ),
    ],
)
def test_envelope_ask_sums_the_source_numeric_column_at_l2(
    tmp_path: Path,
    minter: ManifestMinter,  # noqa: F811
    source: Any,
    question: str,
    sql: str,
    column: str,
    oracle: Decimal,
) -> None:
    """Parent: bronze.public_payments.amount is VARCHAR, SUM does not bind, ABSTAIN."""
    rig = _rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    dbc.ingest_source_database(source, tables=["payments"], path=rig.lake, space_id=rig.space_id)

    env = _ask(rig, question)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False

    con = duckdb.connect(str(rig.lake), read_only=True)
    try:
        truth = con.execute(sql).fetchone()[0]
    finally:
        con.close()
    assert Decimal(str(truth)) == oracle
    assert len(env["rows"]) == 1
    assert float(env["rows"][0][column]) == pytest.approx(float(oracle))
    text = str(env.get("text") or "")
    assert f"{column}={oracle}" in text, text
    assert rig.cortex.executed == [sql]
