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
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
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


def _ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sql: str,
    *,
    cloop: bool,
    prepare: Any = None,
) -> tuple[dict[str, Any], _Cortex]:
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.delenv("DMS_LANE_ONTOLOGY_RANKED", raising=False)
    if cloop:
        monkeypatch.setenv("DMS_CLOOP_B", "1")
    else:
        monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    get_settings.cache_clear()
    db = tmp_path / "unicode.duckdb"
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
    assert body["rows"]
    assert any("location_code" in row for row in body["rows"])
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
