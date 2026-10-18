"""Model SQL runs on the one pooled serving configuration.

Flags on, ask_path generative, one fake ``compute_insights``. The checker
EXPLAIN closes its serving cursor, then this test opens the file for write
(an ingest). The extract must wait on the lake registry and return the
warehouse rows. A bare ``duckdb.connect`` for the extract (the main-branch
shape) raises ConnectionException beside that writer, and the ask abstains.
Every other open of the file takes the same path: one parent per lake, read
only, external access off, config locked.

``DMS_INSIGHTS_CALL_CAP=1`` is required for that split. The default cap
retries after the hold drops, and the retry's connect then succeeds.
"""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import (
    connect_file,
    connect_serving,
    ensure_demo_warehouse,
)
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
            con = connect_file(self.db, write=True)
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
        return _Conn(connect_serving(path, **kwargs), hold)

    monkeypatch.setattr("dms_executor.generative_ask.connect_serving", _connect)
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
    con = connect_serving(db)
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


def _non_select(case: str, dest: Path) -> str:
    if case == "copy":
        return f"COPY (SELECT 1 AS n) TO '{dest.as_posix()}'"
    if case == "set":
        return "SET threads = 1"
    if case == "alter":
        return "ALTER TABLE locations RENAME TO locations_gone"
    if case == "rename":
        return "ALTER TABLE locations RENAME location_code TO loc_gone"
    raise AssertionError(case)


class _LeakModel:
    """Runs submitted SQL. A gate miss shows up as a file or a schema change."""

    def __init__(self, db: Path, sql: str) -> None:
        self._db = db
        self._sql = sql
        self.sql: list[str] = []
        self.asks: list[Any] = []
        self.ontologies: list[Any] = []

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        del question
        self.ontologies.append(kwargs.get("ontology"))
        return {
            "phase": "generate",
            "query_sql": self._sql,
            "served_model": "fake-model",
            "served_provider": "fake-provider",
            "ov_key_id": "ovk-fake",
        }

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_ns_bind")
        body = getattr(req, "body", None)
        text = str(body.get("sql") or "") if isinstance(body, dict) else ""
        self.sql.append(text)
        if text:
            con = connect_file(self._db, write=True)
            try:
                con.execute(text)
            finally:
                con.close()
        return QueryResult(ok=True, status="ok", run_id="run_ns", output={"rows": []})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_ns", hash="hash_ns")

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


@pytest.mark.parametrize("path_name", ["ask", "sql"])
@pytest.mark.parametrize("case", ["copy", "set", "alter", "rename"])
def test_single_non_select_through_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_name: str, case: str
) -> None:
    """One non-SELECT is refused from its parsed type, before EXPLAIN.

    The first-token check already names these heads. This turns that check
    off so the parsed-type gate is the one under test. ``ask`` is the extract
    loop. ``sql`` is ``kind == sql`` with the loop off.
    """
    monkeypatch.setattr("dms_executor.manifest.reject_hostile_chat_sql", lambda _sql: None)
    # GRANT-STRUCT-01: grant_struct.serve_gap is the one chat-path caller of
    # the scanner (generative_ask and the executor reach it through there).
    monkeypatch.setattr("dms_executor.grant_struct.reject_hostile_chat_sql", lambda _sql: None)
    db = ensure_demo_warehouse(tmp_path / f"{path_name}-{case}.duckdb")
    _flags(monkeypatch, db)
    # The schema index opens the file on another thread. This case does not
    # need it, and a second attach with a different mode trips DuckDB.
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "0")
    if path_name == "sql":
        monkeypatch.setenv("DMS_CLOOP_B", "0")
    dest = tmp_path / f"{path_name}-{case}.out"
    before = _shape(db)
    sql = _non_select(case, dest)
    model = _LeakModel(db, sql)
    try:
        client = _client(db, model, monkeypatch)  # type: ignore[arg-type]
        resp = client.post(
            "/v1/chat/ask",
            json={
                "question": "Count the location rows",
                "space_id": _SPACE,
                "session_id": f"ses_ns_{path_name}_{case}",
                "ask_path": "generative",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_envelope_valid(body)
        assert not dest.exists()
        assert _shape(db) == before
        assert body["abstained"] is True
        assert body["badge"] == "ABSTAIN"
        assert body["rows"] == []
        assert model.sql == []
        blob = " ".join(str(item) for item in body["assumptions"])
        assert "hostile_sql" not in blob
        assert "statement_not_allowed" in blob
    finally:
        stop_index_builds(5)
        from dms_api import settings as settings_mod

        settings_mod.get_settings.cache_clear()


def test_serving_connect_disables_external_access(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "sandbox.duckdb")
    dest = tmp_path / "sandbox.csv"
    con = connect_serving(db)
    try:
        flag = con.execute("SELECT current_setting('enable_external_access')").fetchone()
        assert flag is not None
        assert str(flag[0]).lower() in {"false", "0"}
        with pytest.raises(duckdb.Error):
            con.execute(f"COPY (SELECT 1 AS n) TO '{dest.as_posix()}'")
    finally:
        con.close()
    assert not dest.exists()


# --- one configuration for every open of a lake file -----------------------

_COUNT = "SELECT COUNT(*) AS n FROM locations"


def _locations(db: Path) -> int:
    con = connect_serving(db)
    try:
        return int(con.execute(_COUNT).fetchone()[0])
    finally:
        con.close()


def test_extract_shares_the_config_of_a_held_serving_cursor(tmp_path: Path) -> None:
    """The extract open must not clash with the pool's parent."""
    from dms_executor.sql_loop import run_readonly

    db = ensure_demo_warehouse(tmp_path / "share.duckdb")
    held = connect_serving(db)
    try:
        rows, err = run_readonly(_COUNT, db)
    finally:
        held.close()
    assert err is None
    assert rows == [{"n": 5}]


def test_extract_waits_for_a_writer_then_returns_rows(tmp_path: Path) -> None:
    from dms_executor.sql_loop import run_readonly

    db = ensure_demo_warehouse(tmp_path / "wait.duckdb")
    ready = threading.Event()

    def _writer() -> None:
        con = connect_file(db, write=True)
        ready.set()
        time.sleep(_HOLD_S)
        con.close()

    thread = threading.Thread(target=_writer, daemon=True)
    thread.start()
    assert ready.wait(10)
    try:
        rows, err = run_readonly(_COUNT, db)
    finally:
        thread.join(5)
    assert err is None
    assert rows == [{"n": 5}]


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("INSERT INTO locations SELECT * FROM locations", "statement_not_allowed"),
        ("DELETE FROM locations", "statement_not_allowed"),
        ("CREATE TABLE gone AS SELECT 1 AS n", "statement_not_allowed"),
        ("ALTER TABLE locations RENAME TO locations_gone", "statement_not_allowed"),
        ("SELECT 1 AS n; DELETE FROM locations", "multi_statement"),
        # A syntax error runs nothing but stays retryable, as on main.
        ("SELEC nope FROM", "explain:ParserException"),
    ],
)
def test_extract_refuses_what_is_not_one_select(tmp_path: Path, sql: str, code: str) -> None:
    from dms_executor.sql_loop import run_readonly

    db = ensure_demo_warehouse(tmp_path / "refuse.duckdb")
    before = _shape(db)
    rows, err = run_readonly(sql, db)
    assert rows is None
    assert err == code
    assert _shape(db) == before


def test_with_select_runs(tmp_path: Path) -> None:
    from dms_executor.sql_loop import run_readonly

    db = ensure_demo_warehouse(tmp_path / "with.duckdb")
    rows, err = run_readonly(f"WITH c AS ({_COUNT}) SELECT n FROM c", db)
    assert err is None
    assert rows == [{"n": 5}]


def test_extract_raises_the_named_error_not_a_db_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lease miss is a serving abstain at the ask boundary, never a retry."""
    from dms_executor.lake_registry import ServingLeaseCap
    from dms_executor.sql_loop import run_readonly

    db = ensure_demo_warehouse(tmp_path / "cap.duckdb")

    def _miss(_path: Path) -> Any:
        raise ServingLeaseCap(1, 0.0)

    monkeypatch.setattr("dms_executor.demo_warehouse.connect_serving", _miss)
    with pytest.raises(ServingLeaseCap):
        run_readonly(_COUNT, db)


def test_quarantine_probe_shares_the_config_of_a_held_serving_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The result-hash probe used a config of its own beside the pool's parent.

    That raised ConnectionException, and the row was dropped as uncomputable.
    A row whose result is not on the list must be kept.
    """
    from dms_executor.skills_quarantine import filter_retrieved_rows, item_result_hash

    for name in (
        "DMS_SCORED_PACK_HASHES",
        "DMS_SCORED_ITEM_HASHES",
        "DMS_SCORED_ITEM_HASHES_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_SCORED_RESULT_HASHES", item_result_hash([{"n": 99}]))
    db = ensure_demo_warehouse(tmp_path / "probe.duckdb")
    held = connect_serving(db)
    try:
        kept = filter_retrieved_rows(
            [{"question": "count the location rows", "sql": _COUNT, "asset_id": "vq_probe"}],
            warehouse=db,
        )
    finally:
        held.close()
    assert [row["question"] for row in kept] == ["count the location rows"]


def test_no_bare_file_open_outside_the_registry() -> None:
    """Every open of a lake file goes through ``lake_registry``.

    A ``duckdb.connect`` of a path with its own config beside the pool's
    parent is the "same database file with a different configuration" clash.
    ``:memory:`` is not a lake file. The CCA binder keeps one seam that tests
    replace; production there goes through the registry.
    """
    import ast

    root = Path(__file__).resolve().parents[1] / "packages" / "executor" / "dms_executor"
    allowed = {"lake_registry.py", "binder.py"}
    bare: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "connect"
                and isinstance(func.value, ast.Name)
                and func.value.id == "duckdb"
            ):
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and first.value == ":memory:":
                continue
            bare.append(f"{path.relative_to(root)}:{node.lineno}")
    assert bare == []
