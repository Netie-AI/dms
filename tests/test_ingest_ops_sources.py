"""INGEST-OPS: a SQL-source ingest is visible on its Space, truncation included.

Measured on the BIRD Mini-Dev local run (dms PR #312): ``POST /v1/studio/sources/sql``
landed 75 tables into a Space, then ``GET /v1/spaces/{id}`` said ``source_count: 0``
and ``GET /v1/spaces/{id}/sources`` answered 500. ``trans`` landed 500,000 of
1,056,320 rows and only the one ingest receipt said so.

Every assertion here is on what an API caller receives: the Space record, the
sources listing, and the ``POST /v1/chat/ask`` envelope (hard rules 10/10a).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import QueryResult
from dms_api.app import create_app
from dms_api.deps import get_space_store
from dms_api.settings import get_settings
from dms_api.store.memory import DemoSpaceStore
from dms_executor import Executor
from dms_executor.envelope import assert_envelope_valid
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _FakeCursor, _install
from test_e9_02_ask_envelope import minter  # noqa: F401 - pytest fixture
from test_sql_source_route import _body, _gate_allows


class _CountingCursor(_FakeCursor):
    """The shared fake plus ``SELECT COUNT(*)``, which a capped pull now asks."""

    def execute(self, sql: str, params: Any = None) -> None:
        if sql.startswith("SELECT COUNT(*) FROM"):
            self._owner.executed.append(sql)
            data = self._owner.table_data
            if isinstance(data, dict):
                _cols, rows = next(p for ident, p in data.items() if ident in sql)
            else:
                _cols, rows = data
            self.description = [("count",)]
            self._result = [(len(rows),)]
            return
        super().execute(sql, params)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._result[0] if self._result else None


class _CountingConnection(_FakeConnection):
    def cursor(self) -> _CountingCursor:  # type: ignore[override]
        return _CountingCursor(self)


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor import demo_warehouse as dw
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "ingest_ops.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _client_with_space(name: str = "BIRD") -> tuple[TestClient, str]:
    store = DemoSpaceStore.seeded()
    space = store.create(name)
    app = create_app()
    app.dependency_overrides[get_space_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False), space.id


def _two_table_source() -> _CountingConnection:
    return _CountingConnection(
        [("public", "account"), ("public", "trans")],
        {
            '"public"."account"': (["account_id", "district"], [["1", "A"], ["2", "B"]]),
            '"public"."trans"': (
                ["trans_id", "amount"],
                [[str(i), str(10 * i)] for i in range(1, 6)],
            ),
        },
    )


def _ingest(client: TestClient, space_id: str, **over: object) -> dict[str, Any]:
    body = _body(kind="postgresql", space_id=space_id, **over)
    r = client.post("/v1/studio/sources/sql", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_sql_ingest_counts_on_the_space_and_lists_its_sources(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, _two_table_source())
    _gate_allows(monkeypatch)
    client, space_id = _client_with_space()

    receipt = _ingest(client, space_id)
    landed = sorted(t["bronze_table"] for t in receipt["tables"])
    assert landed == ["bronze.public_account", "bronze.public_trans"]

    one = client.get(f"/v1/spaces/{space_id}")
    assert one.status_code == 200, one.text
    assert one.json()["source_count"] == 2

    listed = {s["id"]: s for s in client.get("/v1/spaces").json()["spaces"]}
    assert listed[space_id]["source_count"] == 2

    res = client.get(f"/v1/spaces/{space_id}/sources")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 2
    assert sorted(s["bronze_table"] for s in body["sources"]) == landed
    assert all(s["space_id"] == space_id for s in body["sources"])
    assert all(s["kind"] == "sql" for s in body["sources"])
    assert {s["ref"] for s in body["sources"]} == {
        "postgresql://db.example.net:5432/sales#public.account",
        "postgresql://db.example.net:5432/sales#public.trans",
    }

    # Another Space does not see them: registration is per Space, not global.
    other = DemoSpaceStore.seeded().list_spaces()[0].id
    assert client.get(f"/v1/spaces/{other}").json()["source_count"] == 3


def test_space_sources_with_database_url_is_200_not_500(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 500: the route called the endpoint function, so ``space_id`` was the
    truthy ``Query(None)`` FieldInfo, and ``UUID(<FieldInfo>)`` raised."""
    import dms_api.routes.library as library_routes

    seen: list[tuple[Any, ...]] = []

    class _Conn:
        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Conn:
            seen.append(tuple(params))
            return self

        def fetchall(self) -> list[tuple[Any, ...]]:
            return []

        def commit(self) -> None:
            return None

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@127.0.0.1:1/fake")
    get_settings.cache_clear()
    monkeypatch.setattr(library_routes.psycopg, "connect", lambda *_a, **_k: _Conn())
    monkeypatch.setattr(library_routes, "set_tenant_context", lambda *_a, **_k: None)
    _install(monkeypatch, _two_table_source())
    _gate_allows(monkeypatch)
    client, space_id = _client_with_space("PG backed")
    _ingest(client, space_id, tables=["account"])

    res = client.get(f"/v1/spaces/{space_id}/sources")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 1
    assert body["sources"][0]["bronze_table"] == "bronze.public_account"
    # The data_sources query was scoped to this Space's uuid, not a FieldInfo.
    assert any(UUID(space_id) in p for p in seen), seen

    # A non-uuid Space id cannot own a data_sources row: empty, not a 500.
    alias = client.get("/v1/spaces/sp_q3_audit/sources")
    assert alias.status_code == 200, alias.text


def test_truncated_pull_is_visible_on_receipt_and_sources_api(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, _two_table_source())
    _gate_allows(monkeypatch)
    client, space_id = _client_with_space()

    receipt = _ingest(client, space_id, max_rows=3)
    trans = next(t for t in receipt["tables"] if t["bronze_table"] == "bronze.public_trans")
    assert trans["truncated"] is True
    assert trans["row_count"] == 3
    assert trans["source_row_count"] == 5
    assert trans["partial"].startswith("partial: 3 of 5 source rows")
    assert receipt["truncated_tables"] == ["bronze.public_trans"]

    body = client.get(f"/v1/spaces/{space_id}/sources").json()
    by_table = {s["bronze_table"]: s for s in body["sources"]}
    capped = by_table["bronze.public_trans"]
    assert capped["truncated"] is True
    assert capped["loaded_rows"] == 3
    assert capped["source_row_count"] == 5
    assert capped["partial"] == "partial: 3 of 5 source rows (ingest row cap)"
    whole = by_table["bronze.public_account"]
    assert whole["truncated"] is False
    assert whole["loaded_rows"] == 2
    assert whole["partial"] is None
    assert body["truncated_count"] == 1


@dataclass
class _TransTotalCortex:
    """Engine answers a total over the capped table under a confident badge."""

    asks: list[AskRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        return QueryResult(ok=True, status="bound", run_id="run-rowcap")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Total transaction amount is 60.",
            audit_id="aud_rowcap",
            route="query_skill",
            provenance={"badge": "query_skill", "layer": "L2"},
            sql_used="SELECT SUM(CAST(amount AS DOUBLE)) AS total FROM bronze.public_trans",
            rows=[{"total": 60.0}],
            drillthrough_token="dt_rowcap",
            contributing_sources=[
                {
                    "ref_id": "s1",
                    "container": "public_trans",
                    "kind": "sql",
                    "row_count": 3,
                    "contribution": 1,
                }
            ],
        )


def test_answer_over_truncated_table_says_so_on_the_chat_envelope(
    warehouse: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter,  # noqa: F811 - pytest fixture
) -> None:
    _install(monkeypatch, _two_table_source())
    _gate_allows(monkeypatch)
    ingest_client, space_id = _client_with_space()
    _ingest(ingest_client, space_id, tables=["trans"], max_rows=3)

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    get_settings.cache_clear()
    cortex = _TransTotalCortex()
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    app.state.cortex = cortex
    body = TestClient(app).post(
        "/v1/chat/ask",
        json={"question": "what is the total transaction amount", "session_id": "ses_rc"},
    ).json()

    assert_envelope_valid(body)
    assert len(cortex.asks) == 1
    assert body["abstained"] is False, body
    assert body["rows"] == [{"total": 60.0}]
    assert "60" in body["text"]
    notes = [a for a in body["assumptions"] if "partial table" in a]
    assert notes == [
        "partial table: bronze.public_trans holds 3 of 5 source rows (ingest row cap); "
        "this answer covers the loaded rows only"
    ], body["assumptions"]


def test_answer_over_whole_table_carries_no_partial_line(
    warehouse: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter,  # noqa: F811 - pytest fixture
) -> None:
    _install(monkeypatch, _two_table_source())
    _gate_allows(monkeypatch)
    ingest_client, space_id = _client_with_space()
    _ingest(ingest_client, space_id, tables=["trans"])  # 5 rows, under the cap

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    get_settings.cache_clear()
    cortex = _TransTotalCortex()
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    app.state.cortex = cortex
    body = TestClient(app).post(
        "/v1/chat/ask",
        json={"question": "what is the total transaction amount", "session_id": "ses_rc2"},
    ).json()
    assert_envelope_valid(body)
    assert body["abstained"] is False, body
    assert body["rows"] == [{"total": 60.0}]
    assert not [a for a in body["assumptions"] if "partial table" in a]


def _plant_old_registry(
    path: Path, space_id: str, *, with_source_kind: bool = True
) -> None:
    """A warehouse written before ``source_row_count`` existed, holding one capped
    SQL pull - the state of the BIRD warehouse this lane was measured on."""
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute("DROP TABLE IF EXISTS bronze._ingest_registry")
        kind_col = ", source_kind VARCHAR" if with_source_kind else ""
        con.execute(
            "CREATE TABLE bronze._ingest_registry (table_name VARCHAR PRIMARY KEY, "
            "filename VARCHAR, sha256 VARCHAR, ingest_id VARCHAR, created_at TIMESTAMPTZ, "
            "space_id VARCHAR, row_count INTEGER, truncated BOOLEAN, extracted_at VARCHAR"
            f"{kind_col})"
        )
        con.execute(
            "CREATE OR REPLACE TABLE bronze.public_trans AS "
            "SELECT * FROM (VALUES ('1', '10'), ('2', '20'), ('3', '30')) t(trans_id, amount)"
        )
        vals = [
            "public_trans",
            "postgresql://db.example.net:5432/sales#public.trans",
            "sha",
            "ing-old",
            space_id,
            3,
            True,
            "2026-09-01T00:00:00.000000Z",
        ]
        if with_source_kind:
            vals.append("sql")
        marks = ", ".join("?" for _ in vals)
        cols = (
            "table_name, filename, sha256, ingest_id, space_id, row_count, truncated, "
            "extracted_at" + (", source_kind" if with_source_kind else "")
        )
        con.execute(f"INSERT INTO bronze._ingest_registry ({cols}) VALUES ({marks})", vals)
    finally:
        con.close()


def _registry_cols(path: Path) -> set[str]:
    import duckdb

    con = duckdb.connect(str(path))
    try:
        return {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'bronze' AND table_name = '_ingest_registry'"
            ).fetchall()
        }
    finally:
        con.close()


def test_pre_existing_registry_without_source_row_count_is_read_not_emptied(
    warehouse: Path,
    monkeypatch: pytest.MonkeyPatch,
    minter,  # noqa: F811 - pytest fixture
) -> None:
    """Verifier P3b: an old registry made every read fail, the error was swallowed
    into [], and the Space said 0 sources until an unrelated route widened it.
    No /v1/library/tree call here: the result must not depend on request order."""
    client, space_id = _client_with_space()
    _plant_old_registry(warehouse, space_id)

    one = client.get(f"/v1/spaces/{space_id}")
    assert one.status_code == 200, one.text
    assert one.json()["source_count"] == 1

    res = client.get(f"/v1/spaces/{space_id}/sources")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 1
    assert body["truncated_count"] == 1
    src = body["sources"][0]
    assert src["bronze_table"] == "bronze.public_trans"
    assert src["truncated"] is True
    assert src["loaded_rows"] == 3
    assert src["source_row_count"] is None
    assert src["partial"] == "partial: 3 of an unknown number of source rows (ingest row cap)"
    # Tolerated on read, not fixed by a side effect of some other route.
    assert "source_row_count" not in _registry_cols(warehouse)

    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    get_settings.cache_clear()
    cortex = _TransTotalCortex()
    app = create_app()
    app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    app.state.cortex = cortex
    env = TestClient(app).post(
        "/v1/chat/ask",
        json={"question": "what is the total transaction amount", "session_id": "ses_old"},
    ).json()
    assert_envelope_valid(env)
    assert env["abstained"] is False, env
    assert env["rows"] == [{"total": 60.0}]
    assert "60" in env["text"]
    assert [a for a in env["assumptions"] if "partial table" in a] == [
        "partial table: bronze.public_trans holds 3 of an unknown number of source rows "
        "(ingest row cap); this answer covers the loaded rows only"
    ], env["assumptions"]


def test_registry_older_than_source_kind_classifies_sql_pulls_by_filename(
    warehouse: Path,
) -> None:
    from dms_executor.bronze import list_source_pulls

    _plant_old_registry(warehouse, "sp-bird", with_source_kind=False)
    pulls = list_source_pulls(space_id="sp-bird", path=warehouse)
    assert [p["bronze_table"] for p in pulls] == ["bronze.public_trans"]
    assert pulls[0]["truncated"] is True


def test_partial_line_only_for_tables_the_sql_reads(warehouse: Path) -> None:
    """Verifier P8: a column sharing the capped table's name is not a read of it."""
    from dms_executor.bronze import truncation_notes

    _plant_old_registry(warehouse, "sp-bird")
    assert truncation_notes(
        tables=["orders"], sql="SELECT public_trans FROM orders", path=warehouse
    ) == []
    for sql in (
        'SELECT SUM(amount) FROM "bronze"."public_trans"',
        "SELECT o.id FROM orders o JOIN bronze.PUBLIC_TRANS t ON t.trans_id = o.id",
        "WITH x AS (SELECT * FROM public_trans) SELECT COUNT(*) FROM x",
    ):
        notes = truncation_notes(tables=[], sql=sql, path=warehouse)
        assert len(notes) == 1 and "bronze.public_trans" in notes[0], sql
    # Unparseable SQL falls back to the wide match: noise over a silent miss.
    assert len(truncation_notes(tables=[], sql="SELEC FROM (( public_trans", path=warehouse)) == 1
