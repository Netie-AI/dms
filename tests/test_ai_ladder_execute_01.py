"""Model SQL must run on the serving DuckDB file.

Flags on, ask_path generative, one fake ``compute_insights``. The checker
EXPLAIN closes the serving attach, then this test holds that file open.
A second ``read_only`` connect raises ConnectionException on DuckDB 1.5, so
the extract abstains and the rows never come back. The shared attach waits
on the same lock and returns the warehouse rows.

``DMS_INSIGHTS_CALL_CAP=1`` is required for that split. The default cap
retries after the hold drops, and the retry's read-only connect then succeeds.
"""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.schema_context import stop_index_builds
from fastapi.testclient import TestClient

_SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_QUESTION = "How many raw inventory lots are on hand"
_SQL = "SELECT COUNT(*) AS n FROM inventory WHERE category = 'RAW'"
_HOLD_S = 0.5


def _flags(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.setenv("DMS_HARNESS_ASK_PATHS", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    monkeypatch.setenv("DMS_INSIGHTS_CALL_CAP", "1")
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from dms_api import settings as settings_mod

    settings_mod.get_settings.cache_clear()


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


class _Hold:
    """After the checker closes, keep the serving file open on another thread."""

    def __init__(self, db: Path) -> None:
        self.db = db
        self.armed = False
        self.fired = False
        self.ready = threading.Event()
        self.thread: threading.Thread | None = None

    def arm(self) -> None:
        self.armed = True

    def close_hook(self) -> None:
        if not self.armed or self.fired:
            return
        self.fired = True

        def _run() -> None:
            con = connect_file(self.db)
            self.ready.set()
            time.sleep(_HOLD_S)
            con.close()

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()
        assert self.ready.wait(10), "serving hold did not acquire the file"

    def join(self) -> None:
        if self.thread is not None:
            self.thread.join(5)


class _Conn:
    def __init__(self, inner: Any, hold: _Hold) -> None:
        self._inner = inner
        self._hold = hold

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.execute(*args, **kwargs)

    def close(self) -> None:
        self._inner.close()
        self._hold.close_hook()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Model:
    """Same seam as ``_insights_compute_seam``: ``compute_insights`` only."""

    def __init__(self, db: Path, hold: _Hold) -> None:
        self._db = db
        self._hold = hold
        self.ontologies: list[Any] = []
        self.sql: list[str] = []
        self.asks: list[Any] = []

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        del question
        self.ontologies.append(kwargs.get("ontology"))
        self._hold.arm()
        return {
            "phase": "generate",
            "query_sql": _SQL,
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
        con = connect_file(self._db)
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
        self.asks.append(req)
        return AskResponse(
            answer="unused",
            badge="certified",
            sql_used="SELECT 1",
            rows=[{"n": 1}],
            audit_id="aud_unused",
            route="sql",
        )


def _client(db: Path, model: _Model, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import dms_api.app as app_mod

    @asynccontextmanager
    async def _quiet(_app: Any):
        yield

    monkeypatch.setattr(app_mod, "lifespan", _quiet)
    app = app_mod.create_app()
    app.state.cortex = model
    app.state.ask_service = Executor(
        cortex=model, minter=_minter(), warehouse_path=db
    )  # type: ignore[arg-type]
    return TestClient(app)


def test_model_sql_executes_on_the_serving_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = ensure_demo_warehouse(tmp_path / "serving.duckdb")
    _flags(monkeypatch, db)
    hold = _Hold(db)
    model = _Model(db, hold)

    def _connect(path: Path, **kwargs: Any) -> _Conn:
        from dms_executor.demo_warehouse import connect_locked_readonly as real

        return _Conn(real(path, **kwargs), hold)

    monkeypatch.setattr("dms_executor.generative_ask.connect_locked_readonly", _connect)
    try:
        client = _client(db, model, monkeypatch)
        resp = client.post(
            "/v1/chat/ask",
            json={
                "question": _QUESTION,
                "space_id": _SPACE,
                "session_id": "ses_ai_ladder_exec",
                "ask_path": "generative",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_envelope_valid(body)
        assert body["abstained"] is False
        assert body["badge"] == "L2_VALIDATED"
        assert body["sql_used"] == _SQL
        assert body["rows"] == [{"n": 2}]
        assert body["loop"][-1]["outcome"] == "served"
        assert model.sql == [_SQL]
        assert model.asks == []
        onto = model.ontologies[0]
        assert isinstance(onto, dict)
        assert isinstance(onto.get("schema_context"), str)
        assert onto["schema_context"]
    finally:
        hold.join()
        stop_index_builds(5)
        from dms_api import settings as settings_mod

        settings_mod.get_settings.cache_clear()


_ALTER = "SELECT 1 AS n; ALTER TABLE locations RENAME TO locations_gone"
_RENAME = "SELECT 1 AS n; ALTER TABLE locations RENAME location_code TO loc_gone"


def _shape(db: Path) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    con = connect_file(db)
    try:
        tables = tuple(
            str(row[0])
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        )
        cols = tuple(
            str(row[0])
            for row in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'locations' ORDER BY column_name"
            ).fetchall()
        )
        count = int(con.execute("SELECT COUNT(*) FROM locations").fetchone()[0])
    finally:
        con.close()
    return tables, cols, count


def test_alter_and_rename_through_ask_leave_the_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-statement ALTER reaches EXPLAIN only if the engine gate is skipped.

    sqlglot already counts two statements. This patches that count off so the
    request hits the EXPLAIN attach. The engine must refuse before EXPLAIN.
    """
    monkeypatch.setattr(
        "dms_executor.generative_ask.is_multi_statement", lambda _sql, _dialect: False
    )
    for index, sql in enumerate((_ALTER, _RENAME)):
        db = ensure_demo_warehouse(tmp_path / f"alter-{index}.duckdb")
        _flags(monkeypatch, db)
        before = _shape(db)

        class _Model:
            def __init__(self) -> None:
                self.sql: list[str] = []
                self.asks: list[Any] = []
                self.ontologies: list[Any] = []

            def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
                del question
                self.ontologies.append(kwargs.get("ontology"))
                return {
                    "phase": "generate",
                    "query_sql": sql,
                    "served_model": "fake-model",
                    "served_provider": "fake-provider",
                    "ov_key_id": "ovk-fake",
                }

            def submit(self, req: Any) -> QueryResult:
                plan = getattr(req, "plan", None)
                kind = plan.get("kind") if isinstance(plan, dict) else None
                if kind == "session_bind":
                    return QueryResult(ok=True, status="bound", run_id="run_alter_bind")
                body = getattr(req, "body", None)
                text = str(body.get("sql") or "") if isinstance(body, dict) else ""
                self.sql.append(text)
                return QueryResult(ok=True, status="ok", run_id="run_alter", output={"rows": []})

            def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
                return LedgerAppendResponse(entry_id="led_alter", hash="hash_alter")

            def ask(self, req: AskRequest) -> AskResponse:
                self.asks.append(req)
                return AskResponse(
                    answer="unused",
                    badge="certified",
                    sql_used="SELECT 1",
                    rows=[{"n": 1}],
                    audit_id="aud_unused",
                    route="sql",
                )

        model = _Model()
        try:
            client = _client(db, model, monkeypatch)  # type: ignore[arg-type]
            resp = client.post(
                "/v1/chat/ask",
                json={
                    "question": "Count the location rows",
                    "space_id": _SPACE,
                    "session_id": "ses_ai_ladder_alter",
                    "ask_path": "generative",
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert_envelope_valid(body)
            assert _shape(db) == before
            assert body["abstained"] is True
            assert body["badge"] == "ABSTAIN"
            assert body["rows"] == []
            assert model.sql == []
            assert "GEN-01: checker:multi_statement" in body["assumptions"]
        finally:
            stop_index_builds(5)
            from dms_api import settings as settings_mod

            settings_mod.get_settings.cache_clear()
