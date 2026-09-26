"""SQL-source ingest defects found by the ONTO-DERIVE-01 loopback and adversary rounds (dms#277).

F-b  bronze never reached Cortex's serving warehouse after a SQL-source ingest.
F-c  a digit-first bronze name (``bronze.2024_sales``) failed the manifest for the
     whole Space, so every ask abstained ``submit_failed``.
F-d  a second Space ingesting the same source overwrote the first Space's table.
F-e  every column landed VARCHAR, so a generated ``SUM`` over a numeric column
     could not run: the largest known BIRD cost.

Every answer-path case posts ``POST /v1/chat/ask`` and asserts the customer
envelope (``assert_envelope_valid``, badge, rendered text, rows). The source
catalog is replayed through the connector's fake DB-API connection, including
``INFORMATION_SCHEMA.COLUMNS`` as PostgreSQL answers it.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor import Executor
from dms_executor import demo_warehouse as dw
from dms_executor.bronze import record_source_pull
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.manifest import ManifestMinter
from dms_executor.space_ontology import set_ontology_store
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _install
from test_onto_derive_01 import _gate_allows, _Space, make_minter
from test_space_gen_01 import _multiset, _RecordingCortex

TABLES = [("public", "districts"), ("public", "schools")]
PKS = [("public", "districts", "district_id", 1), ("public", "schools", "school_id", 1)]
FKS = [
    (
        "schools_district_id_fkey",
        "public",
        "schools",
        "district_id",
        "public",
        "districts",
        "district_id",
        1,
    )
]
# As psycopg returns them: ints, Decimals, dates. The connector stringifies.
DATA = {
    '"public"."districts"': (["district_id", "name"], [[1, "North"], [2, "South"]]),
    '"public"."schools"': (
        ["school_id", "district_id", "name", "budget", "opened"],
        [
            [1, 1, "Alder", Decimal("100.50"), "2001-09-01"],
            [2, 1, "Birch", Decimal("200.25"), "1999-01-15"],
            [3, 2, "Cedar", Decimal("50.00"), "2010-03-02"],
            [4, 1, "Dogwood", Decimal("10.00"), None],
        ],
    ),
}
# INFORMATION_SCHEMA.COLUMNS: (name, data_type, numeric_precision, numeric_scale)
TYPES = {
    "public.districts": [("district_id", "integer", 32, 0), ("name", "text", None, None)],
    "public.schools": [
        ("school_id", "integer", 32, 0),
        ("district_id", "integer", 32, 0),
        ("name", "character varying", None, None),
        ("budget", "numeric", 10, 2),
        ("opened", "date", None, None),
    ],
}

SUM_Q = "What is the total budget of schools in the North district?"
SUM_SQL = (
    "SELECT SUM(s.budget) AS total_budget FROM bronze.public_schools s "
    "JOIN bronze.public_districts d ON s.district_id = d.district_id WHERE d.name = 'North'"
)


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


class _Rig:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter):
        self.lake = tmp_path / "ingest_fixes.duckdb"
        monkeypatch.setenv("DMS_WAREHOUSE_DB", str(self.lake))
        dw._SEEDED.clear()
        ensure_demo_warehouse(self.lake)
        get_settings.cache_clear()
        _gate_allows(monkeypatch)
        self.monkeypatch = monkeypatch
        self.cortex = _RecordingCortex(warehouse=self.lake, payload={})
        self.app = create_app()
        exe = Executor(cortex=self.cortex, minter=minter, warehouse_path=self.lake)  # type: ignore[arg-type]
        self.app.state.ask_service = exe
        self.app.state.cortex = self.cortex
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            dms_ask_mode="live",
            dms_demo_fallback=False,
            dms_harness_ask_paths=True,
        )
        self.app.dependency_overrides[get_settings] = lambda: settings
        self.client = TestClient(self.app)

    def space(self, name: str) -> str:
        return str(self.app.state.space_store.create(name).id)

    def ingest(
        self,
        space_id: str,
        *,
        tables: list[tuple[str, str]] | None = None,
        data: dict[str, Any] | None = None,
        types: dict[str, list[tuple[Any, ...]]] | None = None,
    ) -> dict[str, Any]:
        _install(
            self.monkeypatch,
            _FakeConnection(
                tables or TABLES,
                data or DATA,
                pks=PKS,
                fks=FKS,
                column_types=TYPES if types is None else types,
            ),
        )
        r = self.client.post(
            "/v1/studio/sources/sql",
            json={
                "kind": "postgresql",
                "host": "db.example.net",
                "database": "schools",
                "user": "reader",
                "password": "pw-never-echoed",
                "space_id": space_id,
            },
        )
        assert r.status_code == 200, r.text
        assert "pw-never-echoed" not in r.text
        return dict(r.json())

    def ask(self, space_id: str, question: str, sql: str) -> dict[str, Any]:
        return _Space(self.client, self.cortex, space_id, self.lake).ask(
            question, {"query_sql": sql, "plan_source": "ontology_plan"}
        )


def _oracle(lake: Path, sql: str) -> list[dict[str, Any]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [str(c[0]) for c in (cur.description or [])]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()


def _types(lake: Path, table: str) -> dict[str, str]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        return {str(r[0]): str(r[1]) for r in con.execute(f"DESCRIBE {table}").fetchall()}
    finally:
        con.close()


# --- F-e: declared types land; a SUM over a numeric column answers -------------------


def test_declared_types_land_and_a_numeric_sum_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Typed schools")
    receipt = rig.ingest(space)

    landed = _types(rig.lake, "bronze.public_schools")
    assert landed["school_id"] == "BIGINT"
    assert landed["budget"] == "DECIMAL(10,2)"
    assert landed["opened"] == "DATE"
    assert landed["name"] == "VARCHAR"
    schools = next(t for t in receipt["tables"] if t["bronze_table"] == "bronze.public_schools")
    assert schools["column_types"]["budget"] == "DECIMAL(10,2)"
    assert schools["untyped_columns"] == {}

    env = rig.ask(space, SUM_Q, SUM_SQL)
    oracle = _oracle(rig.lake, SUM_SQL)
    assert oracle == [{"total_budget": Decimal("310.75")}]
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert _multiset(env["rows"]) == _multiset([{"total_budget": 310.75}])
    assert "310.75" in str(env.get("text") or ""), env.get("text")


def test_a_value_that_does_not_fit_keeps_the_column_varchar_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Dirty budget")
    rows = [list(r) for r in DATA['"public"."schools"'][1]]
    rows[2][3] = "n/a"  # a legacy text value in a column declared numeric
    data = dict(DATA)
    data['"public"."schools"'] = (DATA['"public"."schools"'][0], rows)
    receipt = rig.ingest(space, data=data)

    schools = next(t for t in receipt["tables"] if t["bronze_table"] == "bronze.public_schools")
    assert schools["column_types"]["budget"] == "VARCHAR"
    assert "1 value(s) do not fit declared DECIMAL(10,2)" in schools["untyped_columns"]["budget"]
    # The other columns still typed; nothing half-converted into NULLs.
    assert schools["column_types"]["school_id"] == "BIGINT"
    assert _oracle(
        rig.lake, "SELECT COUNT(*) AS n FROM bronze.public_schools WHERE budget = 'n/a'"
    ) == [{"n": 1}]


def test_an_undeclared_catalog_lands_varchar_and_names_every_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Undeclared")
    receipt = rig.ingest(space, types={})
    schools = next(t for t in receipt["tables"] if t["bronze_table"] == "bronze.public_schools")
    assert set(schools["untyped_columns"]) == {
        "school_id",
        "district_id",
        "name",
        "budget",
        "opened",
    }
    assert all(
        v == "source did not declare column types" for v in schools["untyped_columns"].values()
    )


# --- F-c: a digit-first name never lands, and a legacy one cannot break the Space -----


def test_digit_first_source_lands_under_a_valid_name_and_the_space_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Digit schema")
    tables = [*TABLES, ("2024", "sales")]
    data = dict(DATA)
    data['"2024"."sales"'] = (["id", "amount"], [[1, 5]])
    types = dict(TYPES)
    types["2024.sales"] = [("id", "integer", 32, 0), ("amount", "integer", 32, 0)]
    receipt = rig.ingest(space, tables=tables, data=data, types=types)
    assert "bronze.t_2024_sales" in {t["bronze_table"] for t in receipt["tables"]}

    env = rig.ask(space, SUM_Q, SUM_SQL)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert _multiset(env["rows"]) == _multiset([{"total_budget": 310.75}])


def test_a_legacy_digit_first_table_is_not_granted_and_does_not_break_the_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Legacy name")
    rig.ingest(space)
    # A table landed before the rename rule existed.
    con = duckdb.connect(str(rig.lake))
    try:
        con.execute('CREATE TABLE bronze."2024_legacy" (id INTEGER)')
    finally:
        con.close()
    record_source_pull(
        table_name="2024_legacy",
        source="postgresql://db.example.net:5432/schools#2024.legacy",
        ingest_id="ing_legacy",
        row_count=0,
        truncated=False,
        space_id=space,
        path=rig.lake,
    )
    grantable = rig.app.state.ask_service.grantable_tables(space_id=space)
    assert "bronze.2024_legacy" not in grantable
    assert "bronze.public_schools" in grantable

    env = rig.ask(space, SUM_Q, SUM_SQL)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))


# --- F-d: a second Space's pull of the same source never overwrites the first ---------


def test_second_space_same_source_gets_its_own_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space_a = rig.space("Space A")
    space_b = rig.space("Space B")
    rig.ingest(space_a)
    # The source changed before B pulled: B's rows must not replace A's.
    rows_b = [list(r) for r in DATA['"public"."schools"'][1]]
    rows_b[0][3] = Decimal("9999.00")
    data_b = dict(DATA)
    data_b['"public"."schools"'] = (DATA['"public"."schools"'][0], rows_b)
    receipt_b = rig.ingest(space_b, data=data_b)

    b_tables = {t["bronze_table"] for t in receipt_b["tables"]}
    assert "bronze.public_schools" not in b_tables, b_tables
    assert all(t["note"] and "another Space" in t["note"] for t in receipt_b["tables"])

    env_a = rig.ask(space_a, SUM_Q, SUM_SQL)
    assert env_a["badge"] == "L2_VALIDATED", (env_a["badge"], env_a.get("text"))
    assert _multiset(env_a["rows"]) == _multiset([{"total_budget": 310.75}])

    b_schools = next(t for t in b_tables if "schools" in t)
    b_districts = next(t for t in b_tables if "districts" in t)
    sql_b = SUM_SQL.replace("bronze.public_schools", b_schools).replace(
        "bronze.public_districts", b_districts
    )
    env_b = rig.ask(space_b, SUM_Q, sql_b)
    assert env_b["badge"] == "L2_VALIDATED", (env_b["badge"], env_b.get("text"))
    assert _multiset(env_b["rows"]) == _multiset([{"total_budget": 10209.25}])
    # And A cannot read B's table.
    env_cross = rig.ask(space_a, SUM_Q, sql_b)
    assert env_cross["abstained"] is True and env_cross["rows"] == []


def test_re_ingest_into_the_same_space_keeps_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Re-pull")
    first = {t["bronze_table"] for t in rig.ingest(space)["tables"]}
    second = rig.ingest(space)
    assert {t["bronze_table"] for t in second["tables"]} == first
    assert all(t["note"] is None for t in second["tables"])


# --- F-b: SQL-source bronze reaches the serving warehouse -------------------------------


def test_sql_ingest_syncs_bronze_to_the_serving_warehouse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    serving = tmp_path / "serving.duckdb"
    duckdb.connect(str(serving)).close()
    monkeypatch.setenv("CORTEX_WAREHOUSE_DB", str(serving))
    # The sync stands down under pytest so a laptop path cannot receive test
    # bronze; this test names its own serving file, so it may run.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    space = rig.space("Synced")
    receipt = rig.ingest(space)
    assert receipt["serving_sync"] == {"state": "ok", "detail": "copied"}, receipt["serving_sync"]
    con = duckdb.connect(str(serving), read_only=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM bronze.public_schools").fetchone()
        typed = {
            str(r[0]): str(r[1]) for r in con.execute("DESCRIBE bronze.public_schools").fetchall()
        }
    finally:
        con.close()
    assert n == (4,)
    assert typed["budget"] == "DECIMAL(10,2)"


def test_sql_ingest_says_when_it_did_not_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    monkeypatch.setenv("CORTEX_WAREHOUSE_DB", str(tmp_path / "missing.duckdb"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    space = rig.space("Unsynced")
    receipt = rig.ingest(space)
    assert receipt["serving_sync"]["state"] == "failed"
    assert "serving warehouse missing" in receipt["serving_sync"]["detail"]


# --- adversary round 4 ---------------------------------------------------------------

PAY_T = [("public", "payments")]
PAY_PKS = [("public", "payments", "id", 1)]


def _pay(rig: _Rig, space: str, amounts: list[Any], dtype: str, prec: Any = None) -> dict[str, Any]:
    rig.monkeypatch.setattr("test_ingest_fixes_277.PKS", PAY_PKS, raising=True)
    rig.monkeypatch.setattr("test_ingest_fixes_277.FKS", [], raising=True)
    return rig.ingest(
        space,
        tables=PAY_T,
        data={'"public"."payments"': (["id", "amount"], [[i, a] for i, a in enumerate(amounts)])},
        types={"public.payments": [("id", "integer", 32, 0), ("amount", dtype, prec, None)]},
    )


MAX_SQL = "SELECT MAX(amount) AS biggest FROM bronze.public_payments"


def test_bare_numeric_lands_exact_decimal_and_max_is_numeric(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Payments")
    receipt = _pay(rig, space, [Decimal("9.50"), Decimal("100.25"), Decimal("20")], "numeric")
    assert receipt["tables"][0]["column_types"]["amount"] == "DECIMAL(38,2)"
    env = rig.ask(space, "What is the largest payment amount?", MAX_SQL)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert _multiset(env["rows"]) == _multiset([{"biggest": 100.25}])


def test_money_with_symbols_stays_text_and_any_read_of_it_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Money")
    receipt = _pay(rig, space, ["$9.50", "$100.25", "$20.00"], "money")
    assert "amount" in receipt["tables"][0]["untyped_columns"]
    # Text MAX is '$9.50': the confident wrong figure this refuses.
    assert _oracle(rig.lake, MAX_SQL) == [{"biggest": "$9.50"}]
    env = rig.ask(space, "What is the largest payment amount?", MAX_SQL)
    assert env["badge"] == "ABSTAIN" and env["rows"] == []
    assert "gap: untyped_numeric:bronze.public_payments.amount" in str(env.get("text")), env.get(
        "text"
    )


def test_typing_never_rounds_a_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Rounding")
    receipt = _pay(rig, space, ["1.5", "2"], "integer", 32)
    assert receipt["tables"][0]["column_types"]["amount"] == "VARCHAR"
    assert "do not fit declared BIGINT exactly" in receipt["tables"][0]["untyped_columns"]["amount"]
    assert _oracle(rig.lake, "SELECT amount FROM bronze.public_payments ORDER BY id") == [
        {"amount": "1.5"},
        {"amount": "2"},
    ]


def test_zoned_timestamps_keep_the_source_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Orders KL")
    receipt = _pay(
        rig, space, ["2024-01-01 05:00:00+08:00", "2024-01-01 23:00:00+08:00"], "timestamptz"
    )
    assert receipt["tables"][0]["column_types"]["amount"] == "VARCHAR"
    assert "zoned timestamp" in receipt["tables"][0]["untyped_columns"]["amount"]
    sql = (
        "SELECT COUNT(*) AS n FROM bronze.public_payments "
        "WHERE CAST(amount AS DATE) = DATE '2024-01-01'"
    )
    assert _oracle(rig.lake, sql) == [{"n": 2}]


def test_case_colliding_source_columns_land_renamed_not_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Case")
    monkeypatch.setattr("test_ingest_fixes_277.PKS", PAY_PKS)
    monkeypatch.setattr("test_ingest_fixes_277.FKS", [])
    receipt = rig.ingest(
        space,
        tables=PAY_T,
        data={'"public"."payments"': (["id", "Name", "name"], [[1, "A", "a"]])},
        types={
            "public.payments": [
                ("id", "integer", 32, 0),
                ("Name", "text", None, None),
                ("name", "text", None, None),
            ]
        },
    )
    assert "column 'name' landed as 'name_2'" in receipt["tables"][0]["note"]
    assert _oracle(rig.lake, "SELECT name_2 FROM bronze.public_payments") == [{"name_2": "a"}]
