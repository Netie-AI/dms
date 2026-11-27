"""DEMO-SCOPE-01: demo lanes answer only in the demo Space.

A customer Space must not receive a demo-warehouse figure, a curated-pack
answer, or a demo source card stamped with its own id. The must-fail case is
the served WRONG from issue #417: ``total line_total by product category, top 3``
coming back as SKU-BETA at RM 8,312.50 with an L2 badge.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import duckdb
import pytest
from dms_executor.bronze import bronze_table_for_sheet
from dms_executor.bronze_sheet_ask import maybe_bronze_sheet_ask
from dms_executor.cca.cascade import run_cascade
from dms_executor.demo_ask import answer_demo_question
from dms_executor.demo_grants import is_demo_space
from dms_executor.demo_pack import (
    HOW_FULL_TRAP_Q,
    SPEND_BY_COUNTRY_Q,
    maybe_pack_ask,
    maybe_uncertified_refuse_ask,
)
from dms_executor.demo_warehouse import (
    DEMO_WAREHOUSE_ORIGIN,
    ensure_demo_warehouse,
)
from dms_executor.envelope import (
    assert_envelope_valid,
    build_answer_envelope,
    normalize_contributing_sources,
)
from dms_executor.generative_ask import load_verified_ontology
from dms_executor.lake_schema import ensure_lake_schemas
from dms_executor.verified_queries import lookup_verified_query, register_verified_query

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
CUSTOMER = "11111111-1111-4111-8111-111111111111"
LEAK_Q = "total line_total by product category, top 3"
_DEMO_FIGURE = "8,312.50"
_SHEET_Q = (
    "In cf98e431_p50_01_sales_messy.xlsx sheet Sales, what are the top 3 "
    "categories by sales_value_myr?"
)
_CASCADE_Q = "rental across SEA, commercial only"


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "dms_demo.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    return path


def _demo_txn_source() -> dict[str, object]:
    return {
        "ref_id": "ref_txn",
        "container": "transactions",
        "kind": "sql",
        "row_count": 1,
        "contribution": 1.0,
        "origin_uri": f"{DEMO_WAREHOUSE_ORIGIN}transactions",
    }


def _assert_not_demo_figure(env: dict[str, object]) -> None:
    blob = json.dumps(env)
    assert env["badge"] != "L2_VALIDATED"
    assert env["badge"] not in {"L0_CERTIFIED", "L1_GOVERNED_METRIC"}
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert env["contributing_sources"] == []
    assert "SKU-BETA" not in blob
    assert _DEMO_FIGURE not in blob
    assert "8312.5" not in blob


def test_ids() -> None:
    assert is_demo_space(FINANCE)
    assert is_demo_space(None)
    assert not is_demo_space(CUSTOMER)


def test_customer_space_does_not_get_the_demo_top_sku(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #417 case. The demo router must not read the demo warehouse.

    None means the lane was skipped. The caller continues on the Space's
    own grants. A bare demo_scope refuse is not this function's job.
    """

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("demo warehouse was read for a customer Space")

    monkeypatch.setattr("dms_executor.demo_ask.execute_sql", _boom)
    monkeypatch.setattr("dms_executor.demo_ask.total_outbound_revenue", _boom)
    assert answer_demo_question(LEAK_Q, space_id=CUSTOMER) is None


def test_demo_space_still_answers_the_same_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)
    env = answer_demo_question(LEAK_Q, space_id=FINANCE)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert "SKU-BETA" in env["text"]
    assert _DEMO_FIGURE in env["text"]
    sources = env["contributing_sources"]
    assert sources
    assert sources[0]["space_id"] == FINANCE
    assert str(sources[0]["origin_uri"]).startswith(DEMO_WAREHOUSE_ORIGIN)
    assert_envelope_valid(env)


def test_customer_space_does_not_get_a_curated_pack_answer() -> None:
    submitted: list[str] = []

    def _submit(sql: str) -> object:
        submitted.append(sql)
        return type("R", (), {"ok": True, "output": {"rows": []}, "run_id": "r"})()

    def _ledger(_payload: dict[str, object]) -> object:
        return type("L", (), {"entry_id": "e1", "hash": "h1"})()

    missed = maybe_pack_ask(
        SPEND_BY_COUNTRY_Q,
        space_id=CUSTOMER,
        grantable={"inventory", "suppliers"},
        submit=_submit,
        ledger_append=_ledger,
        dialect="duckdb",
    )
    assert missed is None
    assert submitted == []
    served = maybe_pack_ask(
        SPEND_BY_COUNTRY_Q,
        space_id=FINANCE,
        grantable={"inventory", "suppliers"},
        submit=_submit,
        ledger_append=_ledger,
        dialect="duckdb",
    )
    assert served is not None
    assert submitted


def test_customer_space_does_not_get_the_planted_pack_refuse() -> None:
    assert maybe_uncertified_refuse_ask(HOW_FULL_TRAP_Q, space_id=CUSTOMER) is None
    demo = maybe_uncertified_refuse_ask(HOW_FULL_TRAP_Q, space_id=FINANCE)
    assert demo is not None
    assert demo["abstained"] is True


def test_customer_space_verified_query_does_not_replay_the_demo_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse = _seed(tmp_path, monkeypatch)
    register_verified_query(
        space_id=FINANCE,
        question=LEAK_Q,
        sql="SELECT sku FROM transactions",
        path=warehouse,
        dialect="duckdb",
    )
    leaked = lookup_verified_query(
        LEAK_Q,
        space_id=CUSTOMER,
        warehouse=warehouse,
        grantable={"transactions"},
        dialect="duckdb",
    )
    assert leaked is None
    own = lookup_verified_query(
        LEAK_Q,
        space_id=FINANCE,
        warehouse=warehouse,
        grantable={"transactions"},
        dialect="duckdb",
    )
    assert own is not None
    assert "transactions" in own["sql"]


def test_customer_space_does_not_load_demo_ontology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse = _seed(tmp_path, monkeypatch)

    def _boom(_path: Path) -> None:
        raise AssertionError("demo_ontology was loaded for a customer Space")

    monkeypatch.setattr("dms_executor.generative_ask.demo_ontology", _boom)
    assert load_verified_ontology(warehouse, space_id=CUSTOMER) is None


def test_demo_space_still_loads_demo_ontology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse = _seed(tmp_path, monkeypatch)
    onto = load_verified_ontology(warehouse, space_id=FINANCE)
    assert onto is not None
    assert onto.verified
    omitted = load_verified_ontology(warehouse)
    assert omitted is not None
    assert omitted.verified


def test_cascade_does_not_engage_for_a_customer_space(tmp_path: Path) -> None:
    db = tmp_path / "deals.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE deals (deal_id VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO deals VALUES ('D1', 100.0)")
    con.close()
    demo = run_cascade(_CASCADE_Q, warehouse=db, tables=["deals"], space_id=FINANCE)
    plain = run_cascade(_CASCADE_Q, warehouse=db, tables=["deals"])
    customer = run_cascade(_CASCADE_Q, warehouse=db, tables=["deals"], space_id=CUSTOMER)
    assert demo.engaged is True
    assert plain.engaged is True
    assert customer.engaged is False
    assert customer.trace == ()


def test_bronze_sheet_lane_does_not_answer_a_customer_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", "1")
    db = tmp_path / "wh.duckdb"
    ident = bronze_table_for_sheet("cf98e431_p50_01_sales_messy.xlsx", "Sales").split(".", 1)[-1]
    con = duckdb.connect(str(db))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute(f'CREATE TABLE bronze."{ident}" (category VARCHAR, sales_value_myr DOUBLE)')
        con.execute(
            f"""
            INSERT INTO bronze."{ident}" VALUES
              ('Electronics', 10.0),
              ('Home', 20.0),
              ('Sports', 30.0)
            """
        )
    finally:
        con.close()
    customer = maybe_bronze_sheet_ask(_SHEET_Q, warehouse=db, space_id=CUSTOMER)
    demo = maybe_bronze_sheet_ask(_SHEET_Q, warehouse=db, space_id=FINANCE)
    assert customer is None
    assert demo is not None
    assert demo["abstained"] is False
    assert demo["rows"]


def test_demo_origin_is_not_stamped_with_a_customer_space() -> None:
    customer = normalize_contributing_sources([_demo_txn_source()], space_id=CUSTOMER)
    assert "space_id" not in customer[0]
    finance = normalize_contributing_sources([_demo_txn_source()], space_id=FINANCE)
    assert finance[0]["space_id"] == FINANCE
    own = normalize_contributing_sources(
        [
            {
                "ref_id": "orders",
                "container": "bronze.orders",
                "kind": "sql",
                "origin_uri": "duckdb://space/orders",
                "row_count": 1,
                "contribution": 1.0,
            }
        ],
        space_id=CUSTOMER,
    )
    assert own[0]["space_id"] == CUSTOMER


def test_l2_over_demo_sources_abstains_for_a_customer_space() -> None:
    leaked = build_answer_envelope(
        answer_id="ans_leak",
        text="SKU-BETA at RM 8,312.50.",
        badge="L2_VALIDATED",
        sql_used="SELECT sku, revenue_myr FROM transactions",
        values=[{"id": "v_top", "value": 8312.50, "unit": "MYR", "label": "revenue"}],
        rows=[{"sku": "SKU-BETA", "revenue_myr": 8312.50}],
        contributing_sources=[_demo_txn_source()],
        space_id=CUSTOMER,
        ask_mode="demo",
    )
    _assert_not_demo_figure(leaked)
    assert leaked["abstain_reason"] == "demo_scope"
    ask_back = leaked["reconfirm"]
    assert ask_back["reason_code"] == "demo_scope"
    assert "granted source" in ask_back["missing"]
    assert "demo warehouse" in leaked["text"]
    assert_envelope_valid(leaked)
    kept = build_answer_envelope(
        answer_id="ans_demo",
        text="SKU-BETA at RM 8,312.50.",
        badge="L2_VALIDATED",
        sql_used="SELECT sku, revenue_myr FROM transactions",
        values=[{"id": "v_top", "value": 8312.50, "unit": "MYR", "label": "revenue"}],
        rows=[{"sku": "SKU-BETA", "revenue_myr": 8312.50}],
        contributing_sources=[_demo_txn_source()],
        space_id=FINANCE,
        ask_mode="demo",
    )
    assert kept["badge"] == "L2_VALIDATED"
    assert kept["contributing_sources"][0]["space_id"] == FINANCE
    assert_envelope_valid(kept)


_SALES_SQL = (
    "SELECT category, SUM(line_total) AS line_total "
    "FROM sales GROUP BY category ORDER BY line_total DESC LIMIT 3"
)


_SALES_SOURCE = UUID("00000000-0000-4000-8000-000000000001")


class _SalesGrant:
    def is_space_member(self, space_id: str, user_id: str) -> bool:
        return space_id == CUSTOMER

    def list_space_source_ids(self, space_id: str) -> list[object]:
        return [_SALES_SOURCE] if space_id == CUSTOMER else []

    def list_user_source_grants(self, tenant_id: str, user_id: str) -> list[object]:
        from dms_executor.acl import SourceGrant

        if tenant_id != "tenant_demo":
            return []
        return [SourceGrant(source_id=_SALES_SOURCE, kind="sql", table_name="sales")]

    def default_pool_id(self, tenant_id: str) -> str:
        return "default"


class _NoGrant:
    def is_space_member(self, space_id: str, user_id: str) -> bool:
        return space_id == CUSTOMER

    def list_space_source_ids(self, space_id: str) -> list[int]:
        return []

    def list_user_source_grants(self, tenant_id: str, user_id: str) -> list[object]:
        return []

    def default_pool_id(self, tenant_id: str) -> str:
        return "default"


def _customer_sales(tmp_path: Path) -> Path:
    db = tmp_path / "customer.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE sales (category VARCHAR, line_total DOUBLE)")
    con.execute(
        """
        INSERT INTO sales VALUES
          ('Widgets', 100.0),
          ('Widgets', 50.0),
          ('Gadgets', 40.0),
          ('Sprockets', 10.0)
        """
    )
    con.close()
    return db


def _minting(monkeypatch: pytest.MonkeyPatch) -> object:
    from cortex_contract.execution import Manifest
    from dms_executor.manifest import ManifestMinter, SessionAcl

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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


def test_customer_space_with_no_grant_asks_back_for_the_missing_table() -> None:
    """No granted source: ask back by name. Do not bare-refuse, do not use demo rows."""
    from dms_executor import Executor

    exe = Executor(session_store=_NoGrant())
    env = exe.demo_ask(LEAK_Q, space_id=CUSTOMER)
    assert env is not None
    _assert_not_demo_figure(env)
    assert env["abstain_reason"] == "demo_scope"
    assert env["reconfirm"]["missing"] == "it has no granted table"
    assert "it has no granted table" in env["text"]
    assert env["reconfirm"]["suggested_question"] == LEAK_Q
    assert_envelope_valid(env)


def test_customer_space_answers_the_417_question_from_its_own_sales(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model SQL runs on the Space's sales table. The demo warehouse does not."""
    from types import SimpleNamespace

    from cortex_contract.execution import QueryResult
    from dms_executor import Executor

    db = _customer_sales(tmp_path)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))

    def _no_demo(*_a: object, **_k: object) -> None:
        raise AssertionError("demo warehouse was opened for a customer Space")

    monkeypatch.setattr("dms_executor.ensure_demo_warehouse", _no_demo)
    monkeypatch.setattr("dms_executor.demo_warehouse.ensure_demo_warehouse", _no_demo)
    monkeypatch.setattr(
        "dms_executor.space_ontology.load_space_ontology",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "dms_executor._insights_compute_seam",
        lambda *_a, **_k: {"query_sql": _SALES_SQL},
    )

    class _Cortex:
        def __init__(self) -> None:
            self.sqls: list[str] = []

        def submit(self, req: object) -> QueryResult:
            plan = getattr(req, "plan", None) or {}
            if plan.get("kind") == "session_bind":
                return QueryResult(ok=True, status="bound", run_id="bind")
            sql = str((getattr(req, "body", None) or {}).get("sql") or "")
            self.sqls.append(sql)
            con = duckdb.connect(str(db), read_only=True)
            try:
                cur = con.execute(sql)
                cols = [str(item[0]) for item in (cur.description or [])]
                rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
            finally:
                con.close()
            return QueryResult(ok=True, status="ok", run_id="run_sales", output={"rows": rows})

        def ledger_append(self, _req: object) -> object:
            return SimpleNamespace(entry_id="led_sales", hash="hash_sales", seq=1)

        def ask(self, _req: object) -> None:
            raise AssertionError("contract ask ran after the Space's own SQL")

        def compute_insights(self, *_a: object, **_k: object) -> dict[str, str]:
            return {"query_sql": _SALES_SQL}

    cortex = _Cortex()
    exe = Executor(
        cortex=cortex,
        minter=_minting(monkeypatch),
        warehouse_path=db,
        session_store=_SalesGrant(),
    )
    env = exe.demo_ask(LEAK_Q, space_id=CUSTOMER)
    assert env is not None
    blob = json.dumps(env)
    assert "SKU-BETA" not in blob
    assert _DEMO_FIGURE not in blob
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert "sales" in str(env.get("sql_used") or "")
    assert "transactions" not in str(env.get("sql_used") or "").lower()
    totals = {row["category"]: row["line_total"] for row in env["rows"]}
    assert totals == {"Widgets": 150.0, "Gadgets": 40.0, "Sprockets": 10.0}
    assert any("sales" in sql.lower() for sql in cortex.sqls)
    assert_envelope_valid(env)
