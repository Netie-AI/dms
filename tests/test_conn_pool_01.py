"""CONN-POOL-01: concurrent asks must not 503 on a shared lake file.

The lake is a temp DuckDB file this test creates. Tables are invented here.
Nothing is read from tests/ traps or held-out fixtures.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
import uvicorn
from cortex_client.compute import INSIGHTS_ASK_TIMEOUT_SECONDS
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor.cca.binder import scan_landed_columns
from dms_executor.demo_warehouse import (
    connect_file,
    connect_serving,
    ensure_demo_warehouse,
    execute_sql,
)
from dms_executor.envelope import assert_envelope_valid
from dms_executor.lake_registry import (
    DEFAULT_INGEST_WAIT_S,
    DEFAULT_SERVING_LEASE_QUEUE_MAX,
    SERVING_LEASE_CAP_REASON,
    SERVING_LEASE_QUEUE_FULL_REASON,
    IngestWaitTimeout,
    ServingLeaseCap,
    ServingLeaseQueueFull,
    ServingWaitCancelled,
    cancel_serving_waiters,
    lease_refs,
    reader_pause_s,
    serving_lease_queue_max,
    serving_lease_wait_s,
    serving_max_readers,
    serving_max_waiters,
    serving_waiter_count,
)
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.semantic_retrieve import retrieve_schema_sql, retrieve_value_encodings

_N = 40
_GROUNDED = "transactions"


@dataclass
class _Cortex:
    asks: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        return {"unsure": True}

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_pool_sql",
                output={"rows": [{"n": 1}]},
            )
        return QueryResult(ok=True, status="bound", run_id="run_pool_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_pool", hash="hash_pool_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req.question)
        return AskResponse(
            answer="No governed figure for that question.",
            badge="abstain",
            abstained=True,
            assumptions="fixture abstain",
            audit_id="aud_pool",
            route="abstain",
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
            issued_at="2026-10-08T00:00:00+00:00",
            expires_at="2026-10-08T01:00:00+00:00",
            signature="dGVzdA",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return minter


def _lake(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE dock_berths (
              berth_id VARCHAR,
              port_name VARCHAR,
              draft_m DOUBLE
            )
            """
        )
        con.execute(
            """
            CREATE TABLE tide_reads (
              read_id VARCHAR,
              berth_id VARCHAR,
              height_m DOUBLE
            )
            """
        )
        con.execute(
            """
            INSERT INTO dock_berths VALUES
              ('B-01', 'North Yard', 9.5),
              ('B-02', 'South Yard', 7.25),
              ('B-03', 'North Yard', 11.0)
            """
        )
        con.execute(
            """
            INSERT INTO tide_reads VALUES
              ('R-1', 'B-01', 1.2),
              ('R-2', 'B-02', 0.4),
              ('R-3', 'B-01', 1.8)
            """
        )
    finally:
        con.close()


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _boot(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[uvicorn.Server, threading.Thread, int, Executor]:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(lake))
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.delenv("DMS_CCA_CASCADE", raising=False)
    monkeypatch.delenv("DMS_HARNESS_ASK_PATHS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    import dms_executor

    previous = dms_executor.probe_openvault
    dms_executor.probe_openvault = lambda **_k: (None, "off")  # type: ignore[method-assign]
    exe = Executor(cortex=_Cortex(), minter=_minter(), warehouse_path=lake)  # type: ignore[arg-type]

    def _build(*_a: Any, **_k: Any) -> Executor:
        return exe

    monkeypatch.setattr("dms_api.app.build_ask_service", _build)
    monkeypatch.setattr("dms_api.wiring.build_ask_service", _build)
    try:
        app = create_app()
    finally:
        dms_executor.probe_openvault = previous
    app.state.ask_service = exe
    app.state.cortex = exe._cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=False,
        dms_mcp=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, name="conn-pool-api", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    last = ""
    while time.monotonic() < deadline:
        try:
            got = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
            if got.status_code == 200:
                return server, thread, port, exe
            last = f"status {got.status_code}"
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(0.05)
    server.should_exit = True
    thread.join(timeout=5)
    raise RuntimeError(f"API did not become healthy: {last}")


def _stop(server: uvicorn.Server, thread: threading.Thread, exe: Executor) -> None:
    server.should_exit = True
    thread.join(timeout=10)
    exe.close()


def _assert_envelope(response: httpx.Response) -> None:
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert isinstance(body, dict)
    assert_envelope_valid(body)
    text = response.text.lower()
    assert "api_key" not in text
    assert "secret" not in text
    assert "sk-" not in text
    if body.get("abstained"):
        named = " ".join(str(item) for item in (body.get("assumptions") or []))
        assert (named.strip() or str(body.get("text") or "").strip()), body


def _ask(port: int, index: int, *, grounded: bool) -> httpx.Response:
    body: dict[str, Any] = {
        "question": f"how many dock berths sit at each port ({index})",
        "session_id": f"ses_pool_{index}",
    }
    if grounded:
        body["grounded_tables"] = [_GROUNDED]
    return httpx.post(f"http://127.0.0.1:{port}/v1/chat/ask", json=body, timeout=60.0)


def _burst(port: int) -> list[httpx.Response]:
    barrier = threading.Barrier(_N)
    out: list[httpx.Response | None] = [None] * _N
    errors: list[BaseException] = []

    def _one(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            out[index] = _ask(port, index, grounded=index % 2 == 0)
        except BaseException as exc:  # noqa: BLE001 - captured for the assert
            errors.append(exc)

    threads = [threading.Thread(target=_one, args=(i,)) for i in range(_N)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors[0]
    return [item for item in out if item is not None]


def test_concurrent_asks_do_not_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = tmp_path / "yard.duckdb"
    _lake(lake)
    server, thread, port, exe = _boot(lake, monkeypatch)
    try:
        responses = _burst(port)
        assert len(responses) == _N
        assert not any(item.status_code == 503 for item in responses)
        for item in responses:
            _assert_envelope(item)
    finally:
        _stop(server, thread, exe)


def test_concurrent_asks_with_schema_value_and_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema and value reads run on the same lake while serving SQL does."""
    lake = tmp_path / "yard.duckdb"
    _lake(lake)
    server, thread, port, exe = _boot(lake, monkeypatch)
    stop = threading.Event()
    side_errors: list[BaseException] = []

    def _schema_and_values() -> None:
        try:
            while not stop.is_set():
                retrieve_schema_sql(
                    lake,
                    {"dock_berths", "tide_reads"},
                    {"port", "berth", "height"},
                )
                retrieve_value_encodings(
                    lake,
                    [{"table": "dock_berths", "columns": ["port_name", "berth_id"]}],
                    {"port", "berth"},
                )
                scan_landed_columns(
                    lake,
                    tables=["dock_berths", "tide_reads"],
                    column_names=["port_name", "berth_id", "height_m"],
                )
        except BaseException as exc:  # noqa: BLE001
            side_errors.append(exc)

    def _serving() -> None:
        try:
            while not stop.is_set():
                rows = execute_sql(
                    "SELECT COUNT(*) AS n FROM dock_berths",
                    path=lake,
                    product=True,
                )
                assert rows and rows[0]["n"] == 3
        except BaseException as exc:  # noqa: BLE001
            side_errors.append(exc)

    workers = [
        threading.Thread(target=_schema_and_values, name="schema-values"),
        threading.Thread(target=_schema_and_values, name="schema-values-2"),
        threading.Thread(target=_serving, name="serving"),
        threading.Thread(target=_serving, name="serving-2"),
    ]
    for worker in workers:
        worker.start()
    try:
        responses = _burst(port)
        assert len(responses) == _N
        assert not any(item.status_code == 503 for item in responses)
        for item in responses:
            _assert_envelope(item)
        assert not side_errors, side_errors[0]
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=30)
        _stop(server, thread, exe)


def test_serving_connection_rejects_writes(tmp_path: Path) -> None:
    """DuckDB rejects writes on the serving cursor. The SQL filter is not in this path."""
    lake = tmp_path / "yard.duckdb"
    other = tmp_path / "other.duckdb"
    duckdb.connect(str(other)).close()
    writer = connect_file(lake, write=True)
    try:
        writer.execute("CREATE TABLE yard_mark (n INTEGER)")
        writer.execute("INSERT INTO yard_mark VALUES (1)")
    finally:
        writer.close()
    before = lake.read_bytes()
    mtime = lake.stat().st_mtime_ns
    copy_to = tmp_path / "yard_copy.csv"
    statements = (
        "INSERT INTO yard_mark VALUES (9)",
        "UPDATE yard_mark SET n = 9",
        "DELETE FROM yard_mark",
        "CREATE TABLE yard_other (n INTEGER)",
        "DROP TABLE yard_mark",
        "ALTER TABLE yard_mark ADD COLUMN extra INTEGER",
        f"COPY yard_mark TO '{copy_to.as_posix()}'",
        f"ATTACH '{other.as_posix()}' AS extra_db",
        "INSTALL httpfs",
        "LOAD httpfs",
        "PRAGMA enable_external_access=true",
    )
    serving = connect_serving(lake)
    try:
        mode = serving.execute("SELECT current_setting('access_mode')").fetchone()
        external = serving.execute("SELECT current_setting('enable_external_access')").fetchone()
        assert mode == ("read_only",)
        assert external == (False,)
        for sql in statements:
            with pytest.raises(duckdb.Error):
                serving.execute(sql)
    finally:
        serving.close()
    assert lake.read_bytes() == before
    assert lake.stat().st_mtime_ns == mtime
    assert not copy_to.exists()
    check = connect_serving(lake)
    try:
        assert check.execute("SELECT n FROM yard_mark").fetchall() == [(1,)]
    finally:
        check.close()


def test_ingest_write_and_serving_reads_stay_consistent(tmp_path: Path) -> None:
    """Ingest uses the write connection. A serving read in flight still finishes.

    A read that arrives during the write waits, then sees the committed value.
    It does not 503 and it does not keep a cursor on a closed parent.
    """
    lake = tmp_path / "yard.duckdb"
    writer = connect_file(lake, write=True)
    try:
        writer.execute("CREATE TABLE yard_mark (n INTEGER)")
        writer.execute("INSERT INTO yard_mark VALUES (1)")
    finally:
        writer.close()

    errors: list[BaseException] = []
    started = threading.Event()

    def _holder() -> None:
        con = connect_serving(lake)
        try:
            started.set()
            time.sleep(0.3)
            got = con.execute("SELECT n FROM yard_mark").fetchone()
            if got != (1,):
                errors.append(RuntimeError(f"in-flight read saw {got}"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            con.close()

    def _writer() -> None:
        if not started.wait(5):
            errors.append(RuntimeError("holder did not start"))
            return
        con = connect_file(lake, write=True)
        try:
            con.execute("BEGIN")
            con.execute("UPDATE yard_mark SET n = 2")
            con.execute("COMMIT")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            con.close()

    def _reader() -> None:
        for _ in range(30):
            con = connect_serving(lake)
            try:
                got = con.execute("SELECT n FROM yard_mark").fetchall()
                if got not in ([(1,)], [(2,)]):
                    errors.append(RuntimeError(f"torn read {got}"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                con.close()

    threads = [threading.Thread(target=_holder, name="holder")]
    threads.append(threading.Thread(target=_writer, name="ingest"))
    threads.extend(threading.Thread(target=_reader, name=f"read-{i}") for i in range(4))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        if thread.is_alive():
            errors.append(RuntimeError(f"{thread.name} still running"))
    assert not errors, errors[0]
    after = connect_serving(lake)
    try:
        assert after.execute("SELECT n FROM yard_mark").fetchone() == (2,)
    finally:
        after.close()


def test_serving_refuses_file_reads_and_config_unlock(tmp_path: Path) -> None:
    """Grant walk is not involved. DuckDB refuses the file read on the serving cursor."""
    lake = tmp_path / "yard.duckdb"
    writer = connect_file(lake, write=True)
    try:
        writer.execute("CREATE TABLE yard_mark (n INTEGER)")
        writer.execute("INSERT INTO yard_mark VALUES (1)")
    finally:
        writer.close()
    serving = connect_serving(lake)
    try:
        flags = serving.execute(
            "SELECT current_setting('enable_external_access'), "
            "current_setting('lock_configuration'), current_setting('access_mode')"
        ).fetchone()
        assert flags == (False, True, "read_only")
        for sql in (
            "SELECT * FROM '/etc/passwd'",
            "SELECT * FROM read_csv_auto('/etc/passwd')",
        ):
            with pytest.raises(duckdb.Error):
                serving.execute(sql)
        with pytest.raises(duckdb.Error):
            serving.execute("SET enable_external_access=true")
        with pytest.raises(duckdb.Error):
            serving.execute("SELECT * FROM read_csv_auto('/etc/passwd')")
        assert serving.execute("SELECT current_setting('enable_external_access')").fetchone() == (
            False,
        )
    finally:
        serving.close()


def test_ingest_csv_then_serving_reads_same_process(tmp_path: Path) -> None:
    """A locked serving connection does not stop a later ingest of a new CSV."""
    from dms_executor.bronze import ingest_csv_bytes

    lake = tmp_path / "yard.duckdb"
    writer = connect_file(lake, write=True)
    try:
        writer.execute("CREATE TABLE yard_mark (n INTEGER)")
        writer.execute("INSERT INTO yard_mark VALUES (1)")
    finally:
        writer.close()
    warm = connect_serving(lake)
    try:
        assert warm.execute("SELECT n FROM yard_mark").fetchall() == [(1,)]
    finally:
        warm.close()

    receipt = ingest_csv_bytes(
        filename="berth_loads.csv",
        data=b"berth_id,loads\nB1,4\nB2,5\n",
        path=lake,
        table_name="berth_loads",
    )
    assert receipt.ingested == 2
    serving = connect_serving(lake)
    try:
        rows = serving.execute(
            'SELECT berth_id, loads FROM bronze.berth_loads ORDER BY berth_id'
        ).fetchall()
        assert [(str(a), str(b)) for a, b in rows] == [("B1", "4"), ("B2", "5")]
        with pytest.raises(duckdb.Error):
            serving.execute("SELECT * FROM read_csv_auto('/etc/passwd')")
    finally:
        serving.close()

    errors: list[BaseException] = []

    def _ingest() -> None:
        try:
            ingest_csv_bytes(
                filename="berth_loads.csv",
                data=b"berth_id,loads\nB1,9\nB2,8\n",
                path=lake,
                table_name="berth_loads",
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def _read() -> None:
        for _ in range(20):
            con = connect_serving(lake)
            try:
                got = con.execute("SELECT COUNT(*) FROM bronze.berth_loads").fetchall()
                if got != [(2,)]:
                    errors.append(RuntimeError(f"torn ingest read {got}"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                con.close()

    threads = [threading.Thread(target=_ingest, name="csv-ingest")]
    threads.extend(threading.Thread(target=_read, name=f"serve-{i}") for i in range(4))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        if thread.is_alive():
            errors.append(RuntimeError(f"{thread.name} still running"))
    assert not errors, errors[0]


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def test_ingest_wait_is_bounded_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held serving read must not pin ingest past the named bound.

    On a reader-preferring lock this thread is still inside connect_file when
    the bound elapses.
    """
    monkeypatch.setenv("DMS_LAKE_INGEST_WAIT_S", "0.2")
    lake = tmp_path / "bound.duckdb"
    _lake(lake)
    held = connect_serving(lake)
    box: dict[str, BaseException] = {}

    def _write() -> None:
        lease = None
        try:
            lease = connect_file(lake, write=True)
        except BaseException as exc:  # noqa: BLE001 - the assertion names it
            box["exc"] = exc
        finally:
            if lease is not None:
                lease.close()

    thread = threading.Thread(target=_write, name="bounded-ingest")
    thread.start()
    thread.join(1.0)
    try:
        assert not thread.is_alive()
        got = box.get("exc")
        assert isinstance(got, IngestWaitTimeout)
        assert got.code == "ingest_wait_timeout"
    finally:
        held.close()
        thread.join(2.0)


def test_serving_lease_cap_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over the cap, the ask is a named abstain. It is not a 503.

    Two leases already out and a cap of 2: a third lease raises, and the ask
    comes back through the abstain exit with ``serving_lease_cap``.
    """
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_CAP", "2")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "0.2")
    lake = tmp_path / "cap.duckdb"
    _lake(lake)
    server, thread, port, exe = _boot(lake, monkeypatch)
    ensure_demo_warehouse(lake)
    held = [connect_serving(lake), connect_serving(lake)]
    try:
        assert lease_refs(lake) == 2
        with pytest.raises(ServingLeaseCap) as caught:
            connect_serving(lake)
        assert caught.value.code == SERVING_LEASE_CAP_REASON
        assert lease_refs(lake) == 2
        response = _ask(port, 0, grounded=False)
        assert response.status_code == 200, response.text[:500]
        body = response.json()
        assert_envelope_valid(body)
        assert body.get("abstained") is True
        assert SERVING_LEASE_CAP_REASON in list(body.get("assumptions") or [])
        assert response.status_code < 500
        assert lease_refs(lake) == 2
    finally:
        for con in held:
            con.close()
        _stop(server, thread, exe)


def test_writer_prefers_over_continuous_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """8 threads x 200 asks while ingest keeps writing.

    Ingest acquires inside the default bound. Every ask is 200. None is a
    503 or any other 5xx. Serving then reads the rows ingest wrote.
    """
    lake = tmp_path / "starve.duckdb"
    _lake(lake)
    server, thread, port, exe = _boot(lake, monkeypatch)
    askers_n = 8
    each = 200
    started = threading.Event()
    first = threading.Event()
    askers_done = threading.Event()
    latencies: list[float] = []
    statuses: list[int] = []
    assumptions: list[list[str]] = []
    errors: list[BaseException] = []
    ingest_times: list[float] = []
    lock = threading.Lock()

    def _one(slot: int) -> None:
        for i in range(each):
            started.set()
            t0 = time.monotonic()
            try:
                response = _ask(port, slot * each + i, grounded=i % 2 == 0)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                return
            elapsed = time.monotonic() - t0
            body = response.json() if response.status_code == 200 else {}
            named = [str(item) for item in (body.get("assumptions") or [])]
            with lock:
                latencies.append(elapsed)
                statuses.append(response.status_code)
                assumptions.append(named)

    def _ingest() -> None:
        try:
            if not started.wait(30):
                errors.append(RuntimeError("asks did not start"))
                return
            n = 0
            while not askers_done.is_set():
                t0 = time.monotonic()
                with connect_file(lake, write=True) as con:
                    con.execute(
                        "INSERT INTO tide_reads VALUES (?, 'B-09', 3.5)",
                        [f"I-{n}"],
                    )
                ingest_times.append(time.monotonic() - t0)
                n += 1
                if n == 1:
                    first.set()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            first.set()

    askers = [
        threading.Thread(target=_one, args=(slot,), name=f"ask-{slot}")
        for slot in range(askers_n)
    ]
    ingest = threading.Thread(target=_ingest, name="continuous-ingest")
    for worker in askers:
        worker.start()
    ingest.start()
    acquired = first.wait(DEFAULT_INGEST_WAIT_S + 1.0)
    try:
        for worker in askers:
            worker.join()
    finally:
        askers_done.set()
        ingest.join(DEFAULT_INGEST_WAIT_S + 5.0)
        _stop(server, thread, exe)
    assert acquired, "ingest did not acquire while asks were running"
    assert not ingest.is_alive()
    assert not errors, errors[0]
    assert len(statuses) == askers_n * each
    n503 = sum(1 for code in statuses if code == 503)
    n5xx = sum(1 for code in statuses if code >= 500)
    ncap = sum(1 for row in assumptions if SERVING_LEASE_CAP_REASON in row)
    timeouts = sum(1 for waited in latencies if waited >= INSIGHTS_ASK_TIMEOUT_SECONDS)
    pause = reader_pause_s(lake)
    ingest_max = max(ingest_times) if ingest_times else -1.0
    report = (
        f"p50={_percentile(latencies, 0.50):.3f} "
        f"p95={_percentile(latencies, 0.95):.3f} "
        f"pause={pause:.3f} timeouts={timeouts} s503={n503} s5xx={n5xx} "
        f"lease_cap_abstains={ncap} ingest_max={ingest_max:.3f} "
        f"ingest_n={len(ingest_times)} asks={len(statuses)}"
    )
    print(report)
    assert n503 == 0, report
    assert n5xx == 0, report
    assert ncap == 0, report
    assert timeouts == 0, report
    assert pause <= 2.0, report
    assert ingest_times, report
    assert ingest_max <= DEFAULT_INGEST_WAIT_S, report
    seen = connect_serving(lake)
    try:
        got = seen.execute(
            "SELECT COUNT(*) FROM tide_reads WHERE berth_id = 'B-09'"
        ).fetchone()
        assert got is not None and int(got[0]) >= 1
    finally:
        seen.close()


def _reasons(body: dict[str, Any]) -> list[str]:
    return [str(item) for item in (body.get("assumptions") or [])]


def test_queue_default_holds_the_short_burst(monkeypatch: pytest.MonkeyPatch) -> None:
    """32 covers cap plus 16 asks, twice. The bound is config, not the thread count."""
    monkeypatch.delenv("DMS_LAKE_SERVING_LEASE_QUEUE_MAX", raising=False)
    assert serving_lease_queue_max() == DEFAULT_SERVING_LEASE_QUEUE_MAX
    assert serving_lease_queue_max() >= 16


def test_serving_lease_wait_is_clamped_under_the_ask_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "30")
    assert serving_lease_wait_s() == INSIGHTS_ASK_TIMEOUT_SECONDS - 1
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "2")
    assert serving_lease_wait_s() == 2.0


def test_waiter_queue_is_bounded_and_does_not_hold_the_read_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cap=1 plus 48 arrivals: readers stay at the cap, waiters stay at the bound.

    Arrivals past the bound abstain immediately with ``serving_lease_queue_full``.
    """
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_CAP", "1")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_QUEUE_MAX", "8")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "2")
    lake = tmp_path / "queue.duckdb"
    _lake(lake)
    holder = connect_serving(lake)
    results: list[tuple[Any, ...]] = []
    lock = threading.Lock()

    def _attempt() -> None:
        started = time.monotonic()
        try:
            con = connect_serving(lake)
        except ServingLeaseQueueFull as exc:
            with lock:
                results.append(("full", time.monotonic() - started, exc.code))
            return
        except ServingLeaseCap as exc:
            with lock:
                results.append(("cap", time.monotonic() - started, exc.code))
            return
        try:
            with lock:
                results.append(("ok", time.monotonic() - started, ""))
        finally:
            con.close()

    threads = [threading.Thread(target=_attempt) for _ in range(48)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with lock:
            fulls = [row for row in results if row[0] == "full"]
        if len(fulls) >= 40 and serving_waiter_count(lake) <= 8:
            break
        time.sleep(0.01)
    try:
        assert serving_max_readers(lake) <= 1
        assert serving_max_waiters(lake) <= 8
        assert serving_waiter_count(lake) <= 8
        with lock:
            fulls = [row for row in results if row[0] == "full"]
        assert len(fulls) == 40
        assert max(row[1] for row in fulls) < 0.3
        assert {row[2] for row in fulls} == {SERVING_LEASE_QUEUE_FULL_REASON}
    finally:
        holder.close()
        for thread in threads:
            thread.join(3.0)
    assert serving_waiter_count(lake) == 0
    assert all(not thread.is_alive() for thread in threads)


def test_serving_lease_queue_is_fifo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The oldest waiter takes the next slot. 30/30."""
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_CAP", "1")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_QUEUE_MAX", "8")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "2")
    lake = tmp_path / "fifo.duckdb"
    _lake(lake)
    for _trial in range(30):
        holder = connect_serving(lake)
        order: list[str] = []

        def _wait(name: str) -> None:
            con = connect_serving(lake)
            order.append(name)
            con.close()

        oldest = threading.Thread(target=_wait, args=("old",))
        newest = threading.Thread(target=_wait, args=("new",))
        oldest.start()
        deadline = time.monotonic() + 1.0
        while serving_waiter_count(lake) < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        newest.start()
        deadline = time.monotonic() + 1.0
        while serving_waiter_count(lake) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        holder.close()
        oldest.join(2.0)
        newest.join(2.0)
        assert order[0] == "old", order
        assert serving_waiter_count(lake) == 0


def test_timed_out_waiters_leave_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_CAP", "1")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_QUEUE_MAX", "4")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "0.25")
    lake = tmp_path / "timeout-queue.duckdb"
    _lake(lake)
    holder = connect_serving(lake)
    errors: list[BaseException] = []

    def _wait() -> None:
        try:
            connect_serving(lake)
        except BaseException as exc:  # noqa: BLE001 - the exit under test
            errors.append(exc)

    threads = [threading.Thread(target=_wait) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2.0)
    try:
        assert serving_waiter_count(lake) == 0
        assert len(errors) == 4
        assert all(isinstance(exc, ServingLeaseCap) for exc in errors)
        assert {exc.code for exc in errors if isinstance(exc, ServingLeaseCap)} == {
            SERVING_LEASE_CAP_REASON
        }
    finally:
        holder.close()
    started = time.monotonic()
    again = connect_serving(lake)
    elapsed = time.monotonic() - started
    again.close()
    assert elapsed < 0.15
    assert serving_waiter_count(lake) == 0


def test_cancelled_waiters_leave_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_CAP", "1")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_QUEUE_MAX", "4")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "5")
    lake = tmp_path / "cancel-queue.duckdb"
    _lake(lake)
    holder = connect_serving(lake)
    errors: list[BaseException] = []

    def _wait() -> None:
        try:
            connect_serving(lake)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_wait) for _ in range(4)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 1.0
    while serving_waiter_count(lake) < 4 and time.monotonic() < deadline:
        time.sleep(0.005)
    cancel_serving_waiters(lake)
    for thread in threads:
        thread.join(2.0)
    try:
        assert serving_waiter_count(lake) == 0
        assert len(errors) == 4
        assert all(isinstance(exc, ServingWaitCancelled) for exc in errors)
    finally:
        holder.close()
    started = time.monotonic()
    again = connect_serving(lake)
    elapsed = time.monotonic() - started
    again.close()
    assert elapsed < 0.15
    assert serving_waiter_count(lake) == 0


def test_waiter_exception_leaves_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disconnect that raises inside the wait still gives the slot back."""
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_CAP", "1")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_QUEUE_MAX", "4")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "5")
    lake = tmp_path / "exc-queue.duckdb"
    _lake(lake)
    holder = connect_serving(lake)
    from dms_executor import lake_registry as registry

    lake_obj = registry._lake_for(registry._key(lake))
    original = lake_obj.mu.wait

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("client disconnect")

    lake_obj.mu.wait = _boom  # type: ignore[method-assign]
    box: dict[str, BaseException] = {}

    def _wait() -> None:
        try:
            connect_serving(lake)
        except BaseException as exc:  # noqa: BLE001
            box["exc"] = exc

    thread = threading.Thread(target=_wait)
    thread.start()
    thread.join(2.0)
    lake_obj.mu.wait = original  # type: ignore[method-assign]
    try:
        assert not thread.is_alive()
        assert isinstance(box.get("exc"), RuntimeError)
        assert serving_waiter_count(lake) == 0
    finally:
        holder.close()
    again = connect_serving(lake)
    again.close()
    assert serving_waiter_count(lake) == 0


def test_cap_and_queue_full_stamp_their_own_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_CAP", "1")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_QUEUE_MAX", "1")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "0.3")
    lake = tmp_path / "reasons.duckdb"
    _lake(lake)
    server, thread, port, exe = _boot(lake, monkeypatch)
    ensure_demo_warehouse(lake)
    holder = connect_serving(lake)
    queued: dict[str, BaseException] = {}

    def _fill() -> None:
        try:
            connect_serving(lake).close()
        except BaseException as exc:  # noqa: BLE001
            queued["exc"] = exc

    filler = threading.Thread(target=_fill)
    filler.start()
    deadline = time.monotonic() + 1.0
    while serving_waiter_count(lake) < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    try:
        started = time.monotonic()
        full = _ask(port, 1, grounded=False)
        full_s = time.monotonic() - started
        assert full.status_code == 200, full.text[:500]
        full_body = full.json()
        assert_envelope_valid(full_body)
        assert full_body.get("abstained") is True
        assert SERVING_LEASE_QUEUE_FULL_REASON in _reasons(full_body)
        assert SERVING_LEASE_CAP_REASON not in _reasons(full_body)
        assert full_s < 0.5
        cancel_serving_waiters(lake)
        filler.join(2.0)
        started = time.monotonic()
        capped = _ask(port, 2, grounded=False)
        cap_s = time.monotonic() - started
        assert capped.status_code == 200, capped.text[:500]
        cap_body = capped.json()
        assert_envelope_valid(cap_body)
        assert SERVING_LEASE_CAP_REASON in _reasons(cap_body)
        assert SERVING_LEASE_QUEUE_FULL_REASON not in _reasons(cap_body)
        assert cap_s < INSIGHTS_ASK_TIMEOUT_SECONDS
        assert cap_s < 1.0
    finally:
        cancel_serving_waiters(lake)
        holder.close()
        filler.join(2.0)
        _stop(server, thread, exe)


def test_short_burst_over_cap_is_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lead case 1. Cap plus 16 asks, short holds, all answered, no cap abstain."""
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_CAP", "4")
    monkeypatch.delenv("DMS_LAKE_SERVING_LEASE_QUEUE_MAX", raising=False)
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "2")
    lake = tmp_path / "burst.duckdb"
    _lake(lake)
    server, thread, port, exe = _boot(lake, monkeypatch)
    ensure_demo_warehouse(lake)
    held = [connect_serving(lake) for _ in range(4)]

    def _release() -> None:
        time.sleep(0.3)
        for con in held:
            con.close()

    releaser = threading.Thread(target=_release)
    releaser.start()
    responses: list[httpx.Response | None] = [None] * 16
    errors: list[BaseException] = []

    def _one(index: int) -> None:
        try:
            responses[index] = _ask(port, index, grounded=False)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    askers = [threading.Thread(target=_one, args=(i,)) for i in range(16)]
    for asker in askers:
        asker.start()
    for asker in askers:
        asker.join(8.0)
    releaser.join(2.0)
    try:
        assert not errors, errors
        assert all(response is not None and response.status_code == 200 for response in responses)
        for response in responses:
            assert response is not None
            body = response.json()
            assert SERVING_LEASE_CAP_REASON not in _reasons(body)
            assert SERVING_LEASE_QUEUE_FULL_REASON not in _reasons(body)
            assert response.status_code < 500
    finally:
        for con in held:
            con.close()
        _stop(server, thread, exe)


def test_abstain_including_watermark_stays_under_ask_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One wait, including the watermark stamp. Default 2s and a raised 30s."""
    lake = tmp_path / "abstain-time.duckdb"
    _lake(lake)
    server, thread, port, exe = _boot(lake, monkeypatch)
    ensure_demo_warehouse(lake)
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_CAP", "1")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_QUEUE_MAX", "8")
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "2")
    holder = connect_serving(lake)

    def _hold(seconds: float) -> None:
        time.sleep(seconds)
        holder.close()

    keeper = threading.Thread(target=_hold, args=(5.0,))
    keeper.start()
    try:
        started = time.monotonic()
        first = _ask(port, 1, grounded=False)
        default_s = time.monotonic() - started
        assert first.status_code == 200, first.text[:500]
        body = first.json()
        assert SERVING_LEASE_CAP_REASON in _reasons(body)
        assert default_s < INSIGHTS_ASK_TIMEOUT_SECONDS
        assert default_s < 3.5
    finally:
        holder.close()
        keeper.join(6.0)
    monkeypatch.setenv("DMS_LAKE_SERVING_LEASE_WAIT_S", "30")
    holder = connect_serving(lake)
    keeper = threading.Thread(target=_hold, args=(9.0,))
    keeper.start()
    try:
        started = time.monotonic()
        raised = _ask(port, 2, grounded=False)
        raised_s = time.monotonic() - started
        assert raised.status_code == 200, raised.text[:500]
        body = raised.json()
        assert SERVING_LEASE_CAP_REASON in _reasons(body)
        assert raised_s < INSIGHTS_ASK_TIMEOUT_SECONDS
        assert raised_s >= 5.0
    finally:
        holder.close()
        keeper.join(10.0)
        _stop(server, thread, exe)
