"""GRANT-UNICODE-01: a Unicode table name is a grant refusal, not an EXPLAIN.

The three asks are red on main (the regex misses the identifier, EXPLAIN
runs, and DMS_CLOOP_B=1 puts the Catalog Error in the reply). On this head
each is a direct abstain from validate_compiled_sql through build_abstain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import get_settings
from dms_executor import Executor
from dms_executor.demo_grants import DemoSessionStore, ingested_bronze_tables
from dms_executor.demo_warehouse import (
    DEMO_TABLES,
    connect_file,
    connect_serving,
    ensure_demo_warehouse,
)
from dms_executor.envelope import assert_envelope_valid
from dms_executor.gen_path_refuse import customer_abstain_text
from dms_executor.generative_ask import validate_compiled_sql
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

_SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_QUESTION = "Count the open items for grant-unicode-nonce"
_GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}
_GRANTED_SQL = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_CASES = (
    ("unquoted", "SELECT 1 AS n FROM 人员", "人员"),
    ("quoted", 'SELECT 1 AS n FROM "客户表"', "客户表"),
    ("mixed", "SELECT i.sku FROM inventory AS i JOIN 人员 AS u ON 1 = 1", "人员"),
)


def _minter() -> ManifestMinter:
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-09T00:00:00+00:00",
            expires_at="2026-10-09T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return minter


class _Cortex:
    def __init__(self, db: Path, sql: str) -> None:
        self._db = db
        self._sql = sql
        self.calls = 0
        self.sql_submits = 0

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        del question
        self.calls += 1
        return {"phase": "generate", "query_sql": self._sql}

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_unicode_bind")
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        self.sql_submits += 1
        con = connect_file(self._db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_unicode", output={"rows": rows})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_unicode", hash="hash_unicode")

    def ask(self, req: AskRequest) -> AskResponse:
        del req
        return AskResponse(
            answer="There are 5 locations.",
            badge="certified",
            sql_used="SELECT COUNT(*) AS location_count FROM locations",
            rows=[{"location_count": 5}],
            audit_id="aud_unicode",
            route="sql",
        )


# A row only this table holds. Main serves it. Head must not.
_PLANTED = 8675309
_EXISTING_SQL = "SELECT n FROM 人员"


def _plant_ungranted_unicode(db: Path) -> None:
    """Create 人员 in the warehouse. It is not added to the grant set."""
    con = connect_file(db)
    try:
        con.execute('CREATE TABLE "人员" (n INTEGER)')
        con.execute("INSERT INTO 人员 VALUES (?)", [_PLANTED])
    finally:
        con.close()


def _space_store(db: Path, *tables: str) -> DemoSessionStore:
    """Put these names on the Space grant intersection the ask reads."""
    return DemoSessionStore(
        extra_grants=tables,
        uploads=lambda: ingested_bronze_tables(db),
        warehouse=db,
    )


def _ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sql: str,
    *,
    cloop: bool,
    prepare: Any = None,
    session_store: Any = None,
    db_name: str = "unicode.duckdb",
) -> tuple[dict[str, Any], _Cortex]:
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.delenv("DMS_LANE_ONTOLOGY_RANKED", raising=False)
    if cloop:
        monkeypatch.setenv("DMS_CLOOP_B", "1")
    else:
        monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    get_settings.cache_clear()
    db = tmp_path / db_name
    ensure_demo_warehouse(db)
    if prepare is not None:
        prepare(db)
    cortex = _Cortex(db, sql)
    app = create_app()
    try:
        with TestClient(app) as client:
            app.state.cortex = cortex
            app.state.ask_service = Executor(
                cortex=cortex,  # type: ignore[arg-type]
                minter=_minter(),
                warehouse_path=db,
                session_store=session_store,
            )
            res = client.post(
                "/v1/chat/ask",
                json={
                    "question": _QUESTION,
                    "space_id": _SPACE,
                    "session_id": "ses_unicode",
                },
            )
    finally:
        get_settings.cache_clear()
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, dict)
    return body, cortex


def _assert_direct_refusal(body: dict[str, Any], cortex: _Cortex, table: str) -> None:
    assert_envelope_valid(body)
    assert body["badge"] == "ABSTAIN"
    assert body["abstained"] is True
    assert body["rows"] == []
    assert body["values"] == []
    assert body["sql_used"] is None
    assert body["answer_id"] == "ans_gen01_abstain"
    assert body["text"] == customer_abstain_text("ungranted")
    assert table not in body["text"]
    blob = json.dumps(body)
    assert "Catalog Error" not in blob
    assert "CatalogException" not in blob
    assert "loop_exhausted" not in blob
    assert "ungranted" in blob
    assert cortex.calls == 1
    assert cortex.sql_submits == 0


@pytest.mark.parametrize("cloop", [False, True], ids=["flag-off", "cloop-b"])
@pytest.mark.parametrize(("name", "sql", "table"), _CASES, ids=[c[0] for c in _CASES])
def test_unicode_table_is_a_direct_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cloop: bool,
    name: str,
    sql: str,
    table: str,
) -> None:
    del name
    body, cortex = _ask(tmp_path, monkeypatch, sql, cloop=cloop)
    _assert_direct_refusal(body, cortex, table)


@pytest.mark.parametrize("cloop", [False, True], ids=["flag-off", "cloop-b"])
def test_existing_ungranted_unicode_table_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cloop: bool,
) -> None:
    """The table exists. It is ungranted. Main serves the row. Head refuses."""
    body, cortex = _ask(
        tmp_path,
        monkeypatch,
        _EXISTING_SQL,
        cloop=cloop,
        prepare=_plant_ungranted_unicode,
    )
    _assert_direct_refusal(body, cortex, "人员")
    assert str(_PLANTED) not in json.dumps(body)


_SERVED_ROWS = [{"n": _PLANTED}]


@pytest.mark.parametrize("cloop", [False, True], ids=["flag-off", "cloop-b"])
def test_granted_unicode_table_serves_same_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cloop: bool,
) -> None:
    """人员 granted in the Space is served. The same table ungranted is refused.

    A parse that refuses every non-ASCII name fails the granted half.
    """
    refused, cortex_no = _ask(
        tmp_path,
        monkeypatch,
        _EXISTING_SQL,
        cloop=cloop,
        prepare=_plant_ungranted_unicode,
        db_name="ungranted.duckdb",
    )
    _assert_direct_refusal(refused, cortex_no, "人员")
    assert str(_PLANTED) not in json.dumps(refused)

    # Default readable is the Space grant intersected with the demo allowlist.
    # This ask grants 人员 on the Space and allowlists it, so the checker sees it.
    monkeypatch.setattr("dms_executor.DEMO_TABLES", (*DEMO_TABLES, "人员"))
    granted_db = tmp_path / "granted.duckdb"
    store = _space_store(granted_db, "人员")
    probe = Executor(
        cortex=_Cortex(granted_db, _EXISTING_SQL),  # type: ignore[arg-type]
        minter=_minter(),
        warehouse_path=granted_db,
        session_store=store,
    )
    assert "人员" in probe.grantable_tables(space_id=_SPACE)

    body, cortex = _ask(
        tmp_path,
        monkeypatch,
        _EXISTING_SQL,
        cloop=cloop,
        prepare=_plant_ungranted_unicode,
        session_store=store,
        db_name="granted.duckdb",
    )
    assert_envelope_valid(body)
    assert body["badge"] == "L2_VALIDATED"
    assert body["abstained"] is False
    assert body["rows"] == _SERVED_ROWS
    assert body["text"] == f"Found 1 row(s).\n  - n={_PLANTED}"
    assert "Catalog Error" not in json.dumps(body)
    assert cortex.calls == 1
    assert cortex.sql_submits == 1


@pytest.mark.parametrize("cloop", [False, True], ids=["flag-off", "cloop-b"])
def test_granted_ascii_query_stays_served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cloop: bool,
) -> None:
    body, cortex = _ask(tmp_path, monkeypatch, _GRANTED_SQL, cloop=cloop)
    assert_envelope_valid(body)
    assert body["badge"] == "L2_VALIDATED"
    assert body["abstained"] is False
    assert body["rows"] == [{"location_code": "WH-C"}]
    assert body["text"] == "Found 1 row(s).\n  - location_code=WH-C"
    assert "Catalog Error" not in json.dumps(body)
    assert cortex.calls == 1
    assert cortex.sql_submits == 1


def test_schema_qualified_unicode_and_cte_refs(tmp_path: Path) -> None:
    db = tmp_path / "names.duckdb"
    ensure_demo_warehouse(db)
    granted = validate_compiled_sql(
        "SELECT location_code FROM main.locations",
        grantable=set(_GRANTS),
        warehouse=db,
    )
    assert granted is None
    quoted = validate_compiled_sql(
        'SELECT 1 AS n FROM "模式"."客户表"',
        grantable=set(_GRANTS),
        warehouse=db,
    )
    assert quoted is not None and quoted.startswith("ungranted:")
    assert "客户表" in quoted
    cte = validate_compiled_sql(
        "WITH c AS (SELECT sku FROM inventory) SELECT sku FROM c",
        grantable=set(_GRANTS),
        warehouse=db,
    )
    assert cte is None
    hidden = validate_compiled_sql(
        "WITH c AS (SELECT 1 AS n FROM 人员) SELECT n FROM c",
        grantable=set(_GRANTS),
        warehouse=db,
    )
    assert hidden == "ungranted:人员"


# Ungranted tables planted in the lake: (id, name, the one row it holds).
_SECRET = 4242
_PLANTS = (
    ("ascii", "payroll_secret", _SECRET),
    ("unicode", "人员", _PLANTED),
)

# Each shape reads the planted table {t} in DuckDB. Main refused the first
# five as ungranted. The previous head served them: it skipped a FROM item
# that is a function call, kept one source per alias, and took a recursive
# CTE's anchor for the CTE. Main and the previous head both served the
# LATERAL function and the SUMMARIZE subquery.
_BYPASS_SHAPES = (
    ("query_table", "SELECT n FROM query_table('{t}')"),
    ("query", "SELECT n FROM query('SELECT n FROM {t}')"),
    ("duckdb_tables", "SELECT table_name AS n FROM duckdb_tables() WHERE table_name = '{t}'"),
    ("alias_shadow", "SELECT n FROM {t}, (SELECT 1 AS z) AS {t}"),
    (
        "recursive_cte",
        "WITH RECURSIVE {t} AS (SELECT n FROM {t} UNION ALL SELECT 1 WHERE false) "
        "SELECT n FROM {t}",
    ),
    ("lateral_function", "SELECT n FROM (SELECT 1 AS k) AS d, LATERAL query_table('{t}')"),
    ("summarize", 'SELECT "max" AS n FROM (SUMMARIZE {t})'),
)


def _plant(table: str, value: int) -> Any:
    def _prepare(db: Path) -> None:
        con = connect_file(db)
        try:
            con.execute(f'CREATE TABLE "{table}" (n INTEGER)')
            con.execute(f'INSERT INTO "{table}" VALUES (?)', [value])
        finally:
            con.close()

    return _prepare


def _serving_rows(db: Path, sql: str) -> list[tuple[Any, ...]]:
    con = connect_serving(db)
    try:
        return list(con.execute(sql).fetchall())
    finally:
        con.close()


@pytest.mark.parametrize("cloop", [False, True], ids=["flag-off", "cloop-b"])
@pytest.mark.parametrize(("plant", "table", "value"), _PLANTS, ids=[p[0] for p in _PLANTS])
@pytest.mark.parametrize(("shape", "template"), _BYPASS_SHAPES, ids=[s[0] for s in _BYPASS_SHAPES])
def test_unprovable_read_of_ungranted_table_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cloop: bool,
    plant: str,
    table: str,
    value: int,
    shape: str,
    template: str,
) -> None:
    del plant
    sql = template.format(t=table)
    body, cortex = _ask(tmp_path, monkeypatch, sql, cloop=cloop, prepare=_plant(table, value))
    _assert_direct_refusal(body, cortex, table)
    assert str(value) not in json.dumps(body)

    # The refusal matters: the serving connection does read the planted table.
    leaked = _serving_rows(tmp_path / "unicode.duckdb", sql)
    expected = table if shape == "duckdb_tables" else str(value)
    assert [str(row[0]) for row in leaked] == [expected]


# DuckDB runs ``(TABLE t)`` as ``SELECT * FROM t``. sqlglot reads it as a
# column named TABLE, and ``FROM (TABLE t)`` as a table named "table" that the
# CTE called "table" covers. So the sqlglot walk saw no read of {t}, and the
# previous head served every shape below. Main served the three without a CTE
# of their own; it refused the CTE shapes only because it refused every CTE.
_TABLE_FORM_SHAPES = (
    ("cte_body", "WITH c AS (TABLE {t}) SELECT n FROM c"),
    ("cte_scalar", "WITH c AS (SELECT (TABLE {t}) AS n) SELECT n FROM c"),
    ("cte_values", "WITH c AS (VALUES ((TABLE {t}))) SELECT * FROM c"),
    ("scalar", "SELECT (TABLE {t}) AS n"),
    ("granted_rows", "SELECT location_code, (TABLE {t}) AS n FROM locations"),
    ("cte_named_table", 'WITH "table" AS (SELECT 1 AS n) SELECT n FROM (TABLE {t}) AS x'),
)


@pytest.mark.parametrize("cloop", [False, True], ids=["flag-off", "cloop-b"])
@pytest.mark.parametrize(("plant", "table", "value"), _PLANTS, ids=[p[0] for p in _PLANTS])
@pytest.mark.parametrize(
    ("shape", "template"), _TABLE_FORM_SHAPES, ids=[s[0] for s in _TABLE_FORM_SHAPES]
)
def test_table_query_form_of_ungranted_table_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cloop: bool,
    plant: str,
    table: str,
    value: int,
    shape: str,
    template: str,
) -> None:
    del plant, shape
    sql = template.format(t=table)
    body, cortex = _ask(tmp_path, monkeypatch, sql, cloop=cloop, prepare=_plant(table, value))
    _assert_direct_refusal(body, cortex, table)
    assert str(value) not in json.dumps(body)

    # The refusal matters: the serving connection does read the planted table.
    leaked = _serving_rows(tmp_path / "unicode.duckdb", sql)
    assert str(value) in {str(v) for row in leaked for v in row}


# The same form in every other place an expression goes. As a predicate it
# leaks the value through which granted rows come back.
_TABLE_FORM_EXPRESSIONS = (
    "SELECT CASE WHEN (TABLE {t}) > 0 THEN (TABLE {t}) END AS n",
    "SELECT coalesce((TABLE {t}), 0) AS n",
    "SELECT (TABLE {t}) + 0 AS n",
    "SELECT list_value((TABLE {t})) AS n",
    "SELECT unnest([(TABLE {t})]) AS n",
    "SELECT n FROM (SELECT 1 AS k) AS d, LATERAL (SELECT (TABLE {t}) AS n)",
    "SELECT location_code FROM locations WHERE (TABLE {t}) = {v}",
    "SELECT location_code FROM locations GROUP BY location_code HAVING (TABLE {t}) = {v}",
    "SELECT location_code FROM locations ORDER BY (TABLE {t})",
    "WITH c AS MATERIALIZED (TABLE {t}) SELECT n FROM c",
    # A later CTE of the same name does not cover the earlier body.
    "WITH c AS (TABLE {t}), {t} AS (SELECT 1 AS n) SELECT c.n FROM c, {t}",
)


@pytest.mark.parametrize(("plant", "table", "value"), _PLANTS, ids=[p[0] for p in _PLANTS])
def test_table_query_form_in_any_expression_is_refused(
    tmp_path: Path,
    plant: str,
    table: str,
    value: int,
) -> None:
    del plant
    db = tmp_path / "names.duckdb"
    ensure_demo_warehouse(db)
    _plant(table, value)(db)
    for template in _TABLE_FORM_EXPRESSIONS:
        sql = template.format(t=table, v=value)
        assert validate_compiled_sql(sql, grantable=set(_GRANTS), warehouse=db) == (
            f"ungranted:{table}"
        ), sql
        assert _serving_rows(db, sql), sql


@pytest.mark.parametrize("cloop", [False, True], ids=["flag-off", "cloop-b"])
def test_table_query_form_of_granted_table_stays_served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cloop: bool,
) -> None:
    """The refusal is the grant, not the form: TABLE over a granted table serves."""
    sql = "WITH c AS (TABLE locations) SELECT location_code FROM c WHERE is_cold_storage"
    body, cortex = _ask(
        tmp_path, monkeypatch, sql, cloop=cloop, prepare=_plant("payroll_secret", _SECRET)
    )
    assert_envelope_valid(body)
    assert body["badge"] == "L2_VALIDATED"
    assert body["abstained"] is False
    assert body["rows"] == [{"location_code": "WH-C"}]
    assert body["text"] == "Found 1 row(s).\n  - location_code=WH-C"
    assert cortex.sql_submits == 1


@pytest.mark.parametrize("cloop", [False, True], ids=["flag-off", "cloop-b"])
@pytest.mark.parametrize(("plant", "table", "value"), _PLANTS, ids=[p[0] for p in _PLANTS])
def test_cte_named_after_ungranted_table_serves_the_cte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cloop: bool,
    plant: str,
    table: str,
    value: int,
) -> None:
    """The query body binds the name to the CTE, so this reads locations only."""
    del plant
    sql = f"WITH {table} AS (SELECT location_code AS n FROM locations) SELECT n FROM {table}"
    body, cortex = _ask(tmp_path, monkeypatch, sql, cloop=cloop, prepare=_plant(table, value))
    assert_envelope_valid(body)
    assert body["badge"] == "L2_VALIDATED"
    assert body["abstained"] is False
    codes = ["WH-A", "WH-B", "WH-C", "WH-D", "WH-E"]
    assert body["rows"] == [{"n": c} for c in codes]
    assert body["text"] == "Found 5 row(s).\n" + "\n".join(f"  - n={c}" for c in codes)
    assert str(value) not in json.dumps(body)
    assert cortex.sql_submits == 1


def test_names_the_check_cannot_prove_are_refused(tmp_path: Path) -> None:
    db = tmp_path / "names.duckdb"
    ensure_demo_warehouse(db)
    _plant("payroll_secret", _SECRET)(db)
    _plant("ä", 1)(db)
    _plant("Ä", 777)(db)
    grants = {*_GRANTS, "ä"}
    refused = {
        # CALL parses as an opaque Command, so its table is not visible.
        "SELECT 1 AS n; CALL query_table('payroll_secret')": "ungranted",
        # DuckDB folds ASCII letters only: "Ä" is a table apart from "ä".
        'SELECT n FROM "Ä"': "ungranted:Ä",
        'WITH "ä" AS (SELECT location_code AS n FROM locations) SELECT n FROM "Ä"': (
            "ungranted:Ä"
        ),
        # A CTE body that names a later CTE reads the base table.
        "WITH a AS (SELECT n FROM payroll_secret), payroll_secret AS (SELECT 1 AS n) "
        "SELECT n FROM a": "ungranted:payroll_secret",
        # A CTE inside a subquery does not cover the same name outside it.
        "SELECT payroll_secret.n FROM (WITH payroll_secret AS (SELECT 1 AS n) "
        "SELECT n FROM payroll_secret) AS q, payroll_secret": "ungranted:payroll_secret",
    }
    for sql, reason in refused.items():
        assert validate_compiled_sql(sql, grantable=grants, warehouse=db) == reason, sql
        rows = [str(v) for row in _serving_rows(db, sql) for v in row]
        assert {"4242", "777"} & set(rows), sql

    served = (
        'SELECT n FROM "ä"',
        "WITH locations AS (SELECT * FROM locations WHERE is_cold_storage) "
        "SELECT location_code FROM locations",
        "WITH a AS (SELECT location_code FROM locations), b AS (SELECT * FROM a) SELECT * FROM b",
        "WITH x AS (SELECT location_code FROM locations) "
        "SELECT * FROM x UNION ALL SELECT location_code FROM x",
        "WITH X AS (SELECT location_code FROM locations) SELECT * FROM x",
    )
    for sql in served:
        assert validate_compiled_sql(sql, grantable=grants, warehouse=db) is None, sql
