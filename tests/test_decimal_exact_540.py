"""DECIMAL-EXACT-01 (dms#540): a certified figure is exact decimal, never a DOUBLE sum.

Rules 10 and 10a: the served clauses are asserted on the envelope from
``POST /v1/chat/ask`` (rendered text, rows, values, assumptions) through
``assert_envelope_valid``. SQL and landed types are asserted in addition.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.bronze import (
    bronze_table_for_sheet,
    ingest_csv_bytes,
)
from dms_executor.bronze_sheet_ask import maybe_bronze_sheet_ask
from dms_executor.demo_warehouse import connect_serving, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

_SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_NOISE = "0.30000000000000004"
_NOTE = "precision: a binary-float figure is shown at 15 significant digits"


def _describe(db: Path, table: str) -> dict[str, str]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(f'DESCRIBE SELECT * FROM bronze."{table}"').fetchall()
    finally:
        con.close()
    return {str(r[0]): str(r[1]) for r in rows}


def _values(db: Path, table: str, col: str) -> list[Any]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return [
            r[0]
            for r in con.execute(f'SELECT "{col}" FROM bronze."{table}" ORDER BY rowid').fetchall()
        ]
    finally:
        con.close()


# ---------------------------------------------------------------- acceptance 1


def test_plain_decimal_column_lands_as_decimal_at_max_scale(tmp_path: Path) -> None:
    db = tmp_path / "wh.duckdb"
    receipt = ingest_csv_bytes(
        filename="amt.csv",
        data=b"id,amount\n1,0.1\n2,0.25\n3,-12.5\n4,\n",
        path=db,
        table_name="amt_plain",
    )
    assert receipt.ingested == 4
    assert not [r for r in receipt.reasons if "kept DOUBLE" in r["reason"]]
    assert _describe(db, "amt_plain")["amount"] == "DECIMAL(38,2)"
    vals = _values(db, "amt_plain", "amount")
    assert vals == [Decimal("0.10"), Decimal("0.25"), Decimal("-12.50"), None]
    assert all(isinstance(v, Decimal) for v in vals if v is not None)


def test_scale_18_is_typed_decimal_and_keeps_every_digit(tmp_path: Path) -> None:
    db = tmp_path / "wh.duckdb"
    ingest_csv_bytes(
        filename="s18.csv",
        data=b"id,x\n1,0.123456789012345678\n2,1.5\n",
        path=db,
        table_name="s18",
    )
    assert _describe(db, "s18")["x"] == "DECIMAL(38,18)"
    assert _values(db, "s18", "x") == [Decimal("0.123456789012345678"), Decimal("1.5")]


@pytest.mark.parametrize(
    ("cell", "why"),
    [
        ("1e5", "not a plain decimal"),
        ("nan", "not a plain decimal"),
        ("0.1234567890123456789", "scale 19 is over 18"),
    ],
)
def test_unrepresentable_value_keeps_double_and_receipt_says_why(
    tmp_path: Path, cell: str, why: str
) -> None:
    db = tmp_path / "wh.duckdb"
    receipt = ingest_csv_bytes(
        filename="mix.csv",
        data=f"id,x\n1,0.5\n2,{cell}\n".encode(),
        path=db,
        table_name="mix",
    )
    assert receipt.ingested == 2
    assert _describe(db, "mix")["x"] == "DOUBLE"
    reasons = [r["reason"] for r in receipt.reasons]
    kept = [r for r in reasons if r.startswith("column x kept DOUBLE:")]
    assert len(kept) == 1, reasons
    if "scale" in why:
        assert "scale 19 is over 18" in kept[0]
    else:
        assert "not a plain decimal" in kept[0]
    vals = _values(db, "mix", "x")
    assert vals[0] == 0.5
    assert isinstance(vals[0], float)


def test_integer_and_text_columns_are_untouched(tmp_path: Path) -> None:
    db = tmp_path / "wh.duckdb"
    receipt = ingest_csv_bytes(
        filename="t.csv", data=b"id,name\n1,a\n2,b\n", path=db, table_name="t_plain"
    )
    types = _describe(db, "t_plain")
    assert types["id"] == "BIGINT"
    assert types["name"] == "VARCHAR"
    assert receipt.reasons == []


# ---------------------------------------------------------------- acceptance 2


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


class _Model:
    """compute_insights names the SQL; submit runs it on the warehouse file."""

    def __init__(self, db: Path, sql: str) -> None:
        self._db = db
        self._sql = sql
        self.sql: list[str] = []

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        del question, kwargs
        return {
            "phase": "generate",
            "query_sql": self._sql,
            "served_model": "fake-model",
            "served_provider": "groq",
            "ov_key_id": "ovk-fake",
        }

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_exec_bind")
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        self.sql.append(sql)
        con = connect_serving(self._db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_exec", output={"rows": rows})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_exec", hash="hash_exec")

    def ask(self, req: AskRequest) -> AskResponse:
        return AskResponse(
            answer="unused",
            badge="certified",
            sql_used="SELECT 1",
            rows=[{"n": 1}],
            audit_id="aud_unused",
            route="sql",
        )


def _flags(monkeypatch: pytest.MonkeyPatch, db: Path, *, sheet: bool) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.setenv("DMS_HARNESS_ASK_PATHS", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", "1" if sheet else "0")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()


def _grant(monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]) -> None:
    import dms_executor as pkg
    from dms_executor.demo_warehouse import DEMO_TABLES

    monkeypatch.setattr(pkg, "DEMO_TABLES", (*DEMO_TABLES, *names))
    real = pkg.Executor.grantable_tables

    def _grants(self: Executor, *, space_id: str | None) -> list[str]:
        base = list(real(self, space_id=space_id))
        for name in names:
            if name not in base:
                base.append(name)
        return base

    monkeypatch.setattr(pkg.Executor, "grantable_tables", _grants)


def _client(db: Path, model: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import dms_api.app as app_mod

    @asynccontextmanager
    async def _quiet(_app: Any):
        yield

    monkeypatch.setattr(app_mod, "lifespan", _quiet)
    app = app_mod.create_app()
    app.state.cortex = model
    app.state.ask_service = Executor(cortex=model, minter=_minter(), warehouse_path=db)  # type: ignore[arg-type]
    return TestClient(app)


def _ask(
    client: TestClient, question: str, sid: str, ask_path: str = "generative"
) -> dict[str, Any]:
    resp = client.post(
        "/v1/chat/ask",
        json={
            "question": question,
            "space_id": _SPACE,
            "session_id": sid,
            "ask_path": ask_path,
        },
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    assert_envelope_valid(body)
    return body


_CSV_01_02 = b"category,amount\nA,0.1\nA,0.2\n"


def _assert_exact_0_3(body: dict[str, Any]) -> None:
    assert body["abstained"] is False, body["text"]
    assert body["badge"] != "ABSTAIN"
    assert _NOISE not in json.dumps(body)
    assert "0.3" in body["text"]
    assert len(body["rows"]) == 1
    cell = body["rows"][0]["amount"]
    assert Decimal(str(cell)) == Decimal("0.3")
    assert str(cell) in ("0.3", "0.30")
    # The generated path fills ``values`` from the returned rows when it
    # states a figure; when it does, 0.3 is the number it holds.
    for item in body["values"]:
        assert _NOISE not in json.dumps(item)


def test_generated_sum_of_point1_and_point2_is_point3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = ensure_demo_warehouse(tmp_path / "wh.duckdb")
    table = "dec540_gen"
    receipt = ingest_csv_bytes(
        filename="dec540_gen.csv", data=_CSV_01_02, path=db, table_name=table, space_id=_SPACE
    )
    assert receipt.ingested == 2
    assert _describe(db, table)["amount"] == "DECIMAL(38,1)"
    _flags(monkeypatch, db, sheet=False)
    _grant(monkeypatch, (f"bronze.{table}",))
    sql = f'SELECT category, SUM(amount) AS amount FROM bronze."{table}" GROUP BY 1'
    model = _Model(db, sql)
    try:
        body = _ask(
            _client(db, model, monkeypatch),
            "Sum the amount for each category",
            "ses_dec540_gen",
        )
    finally:
        from dms_api import settings as settings_mod

        settings_mod.get_settings.cache_clear()
    assert model.sql, "the generated SQL was never executed"
    _assert_exact_0_3(body)
    assert body["rows"][0]["category"] == "A"


_SHEET_Q = (
    "In dec540_sheet.xlsx sheet Sales, what are the top 3 categories by sales_value_myr?"
)


def _land_sheet(db: Path, *, with_space: bool = True) -> str:
    ident = bronze_table_for_sheet("dec540_sheet.xlsx", "Sales").split(".", 1)[-1]
    receipt = ingest_csv_bytes(
        filename="dec540_sheet.csv",
        data=b"category,sales_value_myr\nA,0.1\nA,0.2\nB,0.05\n",
        path=db,
        table_name=ident,
        space_id=_SPACE if with_space else None,
    )
    assert receipt.ingested == 3
    assert _describe(db, ident)["sales_value_myr"] == "DECIMAL(38,2)"
    return ident


def test_sheet_lane_function_sums_in_decimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", "1")
    db = ensure_demo_warehouse(tmp_path / "wh.duckdb")
    _land_sheet(db)
    env = maybe_bronze_sheet_ask(_SHEET_Q, warehouse=db, space_id=_SPACE)
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED"
    assert env["abstained"] is False
    assert [r["category"] for r in env["rows"]] == ["A", "B"]
    assert Decimal(str(env["rows"][0]["sales_value_myr"])) == Decimal("0.3")
    assert _NOISE not in json.dumps(env)
    assert "0.3" in env["text"]
    assert "AS DOUBLE" not in (env["sql_used"] or "")


def test_sheet_lane_through_the_route_sums_in_decimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = ensure_demo_warehouse(tmp_path / "wh.duckdb")
    ident = _land_sheet(db)
    _flags(monkeypatch, db, sheet=True)
    _grant(monkeypatch, (f"bronze.{ident}",))
    model = _Model(db, "SELECT 1")
    try:
        client = _client(db, model, monkeypatch)
        body = _ask(client, _SHEET_Q, "ses_dec540_sheet", ask_path="product")
    finally:
        from dms_api import settings as settings_mod

        settings_mod.get_settings.cache_clear()
    assert body.get("lane") == "bronze_sheet" or body["badge"] == "L0_CERTIFIED", body["text"]
    assert model.sql == [], "the route did not serve from the sheet lane"
    assert body["badge"] == "L0_CERTIFIED"
    assert body["abstained"] is False
    assert Decimal(str(body["rows"][0]["sales_value_myr"])) == Decimal("0.3")
    assert _NOISE not in json.dumps(body)
    assert "0.3" in body["text"]


# ---------------------------------------------------------------- acceptance 3


def test_true_float_source_renders_at_15_digits_with_precision_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = ensure_demo_warehouse(tmp_path / "wh.duckdb")
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute('CREATE TABLE bronze."dec540_float" (k VARCHAR, x DOUBLE)')
        con.execute(
            "INSERT INTO bronze.\"dec540_float\" VALUES "
            "('big', 12345678901234.56), ('noise', 0.30000000000000004)"
        )
    finally:
        con.close()
    _flags(monkeypatch, db, sheet=False)
    _grant(monkeypatch, ("bronze.dec540_float",))
    sql = 'SELECT k, x FROM bronze."dec540_float" ORDER BY k'
    model = _Model(db, sql)
    try:
        body = _ask(
            _client(db, model, monkeypatch),
            "List each key and its x value",
            "ses_dec540_float",
        )
    finally:
        from dms_api import settings as settings_mod

        settings_mod.get_settings.cache_clear()
    assert model.sql
    assert body["abstained"] is False, body["text"]
    text = body["text"]
    assert "12345678901234.6" in text
    assert "12345678901234.56" not in text
    assert "0.3" in text
    assert _NOISE not in text
    assert _NOTE in body["assumptions"]


# ------------------------------------------- a legitimate float source still answers


def _land_declared_float(db: Path, table: str) -> None:
    from dms_executor.bronze import type_bronze_columns, write_bronze_rows

    write_bronze_rows(
        table=f"bronze.{table}",
        columns=["k", "x"],
        rows=[["big", "12345678901234.56"], ["noise", "0.30000000000000004"]],
        path=db,
    )
    typed = type_bronze_columns(
        table=f"bronze.{table}",
        declared={"x": ("double precision", None, None)},
        path=db,
    )
    assert typed is not None
    assert _describe(db, table)["x"] == "DOUBLE"


def _ask_generated(
    monkeypatch: pytest.MonkeyPatch, db: Path, table: str, sql: str, question: str, sid: str
) -> tuple[dict[str, Any], _Model]:
    _flags(monkeypatch, db, sheet=False)
    _grant(monkeypatch, (f"bronze.{table}",))
    model = _Model(db, sql)
    try:
        body = _ask(_client(db, model, monkeypatch), question, sid)
    finally:
        from dms_api import settings as settings_mod

        settings_mod.get_settings.cache_clear()
    return body, model


def test_sql_source_declared_float_select_is_served_at_15_digits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = ensure_demo_warehouse(tmp_path / "wh.duckdb")
    _land_declared_float(db, "dec540_decl")
    body, model = _ask_generated(
        monkeypatch,
        db,
        "dec540_decl",
        'SELECT k, x FROM bronze."dec540_decl" ORDER BY k',
        "List each key and its x value",
        "ses_dec540_decl",
    )
    assert model.sql
    assert body["abstained"] is False, body["text"]
    assert body["badge"] != "ABSTAIN"
    assert len(body["rows"]) == 2
    text = body["text"]
    assert "12345678901234.6" in text
    assert "12345678901234.56" not in text
    assert _NOISE not in text
    assert _NOTE in body["assumptions"]


def test_sql_source_declared_float_sum_is_served_at_15_digits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = ensure_demo_warehouse(tmp_path / "wh.duckdb")
    _land_declared_float(db, "dec540_decl_sum")
    body, model = _ask_generated(
        monkeypatch,
        db,
        "dec540_decl_sum",
        'SELECT SUM(x) AS x FROM bronze."dec540_decl_sum"',
        "Sum the x value",
        "ses_dec540_decl_sum",
    )
    assert model.sql
    assert body["abstained"] is False, body["text"]
    assert len(body["rows"]) == 1
    assert isinstance(body["rows"][0]["x"], float)
    # 12345678901234.56 + 0.3 is 12345678901234.86 as a binary float; the text
    # never states more than 15 significant digits of it.
    import re

    figures = re.findall(r"\d[\d,]*\.?\d*", body["text"])
    sig = [f.replace(",", "").replace(".", "").lstrip("0") for f in figures]
    assert any(s.startswith("123456789012") for s in sig), body["text"]
    assert all(len(s) <= 15 for s in sig), body["text"]
    assert _NOTE in body["assumptions"]


def test_sheet_lane_on_a_double_measure_still_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", "1")
    db = ensure_demo_warehouse(tmp_path / "wh.duckdb")
    ident = bronze_table_for_sheet("cf98e431_p50_01_sales_messy.xlsx", "Sales").split(".", 1)[-1]
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute(f'CREATE TABLE bronze."{ident}" (category VARCHAR, sales_value_myr DOUBLE)')
        con.execute(
            f"""INSERT INTO bronze."{ident}" VALUES
              ('Electronics', 1545366.40), ('Home', 1199018.49),
              ('Sports', 400000.00), ('Misc', 100.00)"""
        )
    finally:
        con.close()
    env = maybe_bronze_sheet_ask(
        "In cf98e431_p50_01_sales_messy.xlsx sheet Sales, what are the top 3 "
        "categories by sales_value_myr?",
        warehouse=db,
        space_id=_SPACE,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert [r["category"] for r in env["rows"]] == ["Electronics", "Home", "Sports"]
    assert Decimal(str(env["rows"][0]["sales_value_myr"])) == Decimal("1545366.40")
    assert "1545366.4" in env["text"]
