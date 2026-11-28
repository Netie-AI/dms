"""SCALE-01: one ask budget, one parse, one read connection, bounded scans.

Schemas and questions here are synthetic. No held-out pack.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.ask_clock import (
    ensure_clock,
    note_deadline,
    release_clock,
    scale_budget_enabled,
    scale_budget_left_s,
)
from cortex_client.client import CortexClient
from cortex_client.compute import compute_insights

SQL = "SELECT region, sum(amount) AS total FROM facts GROUP BY region"


class _HoldHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        path = self.path.split("?", 1)[0].rstrip("/")
        self.server.posts += 1  # type: ignore[attr-defined]
        if path.endswith("/cancel"):
            self.server.cancels += 1  # type: ignore[attr-defined]
            body = b"{}"
            self._send(200, body)
            return
        self.server.generates += 1  # type: ignore[attr-defined]
        if self.server.hold:  # type: ignore[attr-defined]
            self.server.open_count += 1  # type: ignore[attr-defined]
            try:
                self.connection.settimeout(0.05)
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    try:
                        chunk = self.connection.recv(1)
                    except TimeoutError:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
            finally:
                self.server.open_count -= 1  # type: ignore[attr-defined]
            return
        body = b'{"suggested_question":"how many regions"}'
        self._send(200, body)

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        del fmt, args


class _HoldServer(ThreadingHTTPServer):
    def __init__(self, *, hold: bool) -> None:
        super().__init__(("127.0.0.1", 0), _HoldHandler)
        self.hold = hold
        self.posts = 0
        self.cancels = 0
        self.generates = 0
        self.open_count = 0
        self.daemon_threads = True


def _serve(*, hold: bool) -> tuple[_HoldServer, threading.Thread]:
    server = _HoldServer(hold=hold)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server: _HoldServer, thread: threading.Thread) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and server.open_count:
        time.sleep(0.02)
    server.shutdown()
    thread.join(1.0)


def test_only_the_digit_1_enables_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SCALE_BUDGET", raising=False)
    assert scale_budget_enabled() is False
    assert scale_budget_left_s() is None
    monkeypatch.setenv("DMS_SCALE_BUDGET", "true")
    assert scale_budget_enabled() is False
    monkeypatch.setenv("DMS_SCALE_BUDGET", "1")
    token = ensure_clock()
    try:
        assert scale_budget_left_s() is None
        note_deadline(time.monotonic() + 5)
        left = scale_budget_left_s()
        assert left is not None and 4.0 < left <= 5.0
    finally:
        release_clock(token)


def test_leg_stops_at_the_budget_and_the_suggestion_does_not_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 30s leg is cut at the ask deadline. The 2s suggestion does not run."""
    server, thread = _serve(hold=True)
    host, port = server.server_address
    monkeypatch.setenv("DMS_SCALE_BUDGET", "1")
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", "30")
    monkeypatch.delenv("DMS_ASK_LEAN", raising=False)
    monkeypatch.delenv("DMS_ASK_LEAN_CANCEL", raising=False)
    monkeypatch.delenv("DMS_DEADLINE_ASKBACK", raising=False)
    token = ensure_clock()
    note_deadline(time.monotonic() + 0.3)
    started = time.perf_counter()
    try:
        payload = compute_insights(
            f"http://{host}:{port}",
            question="how many regions",
            api_key="scale-probe-key",
            session_id="s",
            space_id="sp",
        )
        elapsed = time.perf_counter() - started
        assert elapsed < 2.5
        assert isinstance(payload, dict)
        assert str(payload.get("insights_fail") or "").startswith("insights_timeout")
        assert server.cancels >= 1
        generates = server.generates
        note_deadline(time.monotonic() - 1)
        client = CortexClient(
            f"http://{host}:{port}", api_key="scale-probe-key", timeout=30
        )
        assert client.complete("closest question") == ""
        assert server.generates == generates
    finally:
        release_clock(token)
        _stop(server, thread)


def test_flag_off_still_waits_out_the_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    server, thread = _serve(hold=True)
    host, port = server.server_address
    monkeypatch.delenv("DMS_SCALE_BUDGET", raising=False)
    monkeypatch.setenv("DMS_INSIGHTS_TIMEOUT_S", "0.4")
    monkeypatch.delenv("DMS_ASK_LEAN_CANCEL", raising=False)
    token = ensure_clock()
    note_deadline(time.monotonic() + 0.05)
    started = time.perf_counter()
    try:
        payload = compute_insights(
            f"http://{host}:{port}",
            question="how many regions",
            api_key="scale-probe-key",
            session_id="s",
            space_id="sp",
        )
    finally:
        elapsed = time.perf_counter() - started
        release_clock(token)
        _stop(server, thread)
    assert elapsed >= 0.3
    assert isinstance(payload, dict)
    assert server.cancels == 0


def test_parse_cache_hits_and_the_ask_keeps_one_tree() -> None:
    from dms_executor.sql_parse import (
        bind_ask_parse,
        cache_info,
        clear_parse_cache,
        parse_cached,
        reset_ask_parse,
    )

    clear_parse_cache()
    token = bind_ask_parse()
    try:
        first = parse_cached(SQL, "duckdb")
        misses = cache_info()[0].misses
        for node in first[0].walk():
            if type(node).__name__ == "Table":
                node.set("this", "mutated")
                break
        # The ask slot copies the first tree. It does not re-enter sqlglot.
        second = parse_cached(SQL, "duckdb")
        rendered = second[0].sql(dialect="duckdb").lower()
        assert cache_info()[0].misses == misses
        assert "facts" in rendered
        assert "mutated" not in rendered
    finally:
        reset_ask_parse(token)
    # After the ask, the same text is an LRU hit.
    parse_cached(SQL, "duckdb")
    info = cache_info()[0]
    assert info.misses == misses
    assert info.hits >= 1
    clear_parse_cache()


def test_parse_cache_is_safe_across_threads() -> None:
    from dms_executor.sql_parse import clear_parse_cache, parse_one_cached

    clear_parse_cache()
    found: list[str] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            tree = parse_one_cached(SQL, "duckdb")
            found.append(tree.sql(dialect="duckdb"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not errors
    assert len(found) == 12
    assert len(set(found)) == 1


def _facts(path: Path, rows: int = 4) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE facts (region VARCHAR, amount INTEGER)")
        con.execute(
            "INSERT INTO facts SELECT 'r' || (i % 4)::VARCHAR, 1 FROM range(?) t(i)",
            [rows],
        )
    finally:
        con.close()


def test_serving_cursors_share_one_parent(tmp_path: Path) -> None:
    from dms_executor.demo_warehouse import connect_serving
    from dms_executor.lake_registry import close_lakes, parent_open_count

    path = tmp_path / "pool.duckdb"
    _facts(path)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(5):
                cur = connect_serving(path)
                try:
                    got = cur.execute("SELECT count(*) FROM facts").fetchone()
                    assert got == (4,)
                finally:
                    cur.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert not errors
    assert parent_open_count(path) == 1
    close_lakes()


def test_serving_cursor_sets_memory_and_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.demo_warehouse import connect_serving
    from dms_executor.lake_registry import lake_memory_limit, lake_threads

    monkeypatch.setenv("DMS_LAKE_MEMORY_LIMIT", "nope;drop")
    monkeypatch.setenv("DMS_LAKE_THREADS", "0")
    assert lake_memory_limit() == "1GB"
    assert lake_threads() == 4
    monkeypatch.setenv("DMS_LAKE_MEMORY_LIMIT", "256MiB")
    monkeypatch.setenv("DMS_LAKE_THREADS", "2")
    path = tmp_path / "limits.duckdb"
    _facts(path)
    cur = connect_serving(path)
    try:
        row = cur.execute(
            "SELECT current_setting('memory_limit'), current_setting('threads'), "
            "current_setting('enable_external_access'), "
            "current_setting('lock_configuration')"
        ).fetchone()
    finally:
        cur.close()
    assert row is not None
    assert "256" in str(row[0]).upper()
    assert int(float(row[1])) == 2
    assert row[2] is False
    assert row[3] is True


def _index(con: duckdb.DuckDBPyConnection, n: int) -> tuple[dict[str, Any], list[str]]:
    import dms_executor.schema_context as sc

    con.execute("CREATE TABLE labels (region VARCHAR)")
    con.execute(
        "INSERT INTO labels SELECT 'r' || (i % ?)::VARCHAR FROM range(?) t(i)",
        [n, n * 3],
    )
    sqls: list[str] = []
    inner = con

    class _Proxy:
        def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
            sqls.append(str(sql))
            return inner.execute(sql, *args, **kwargs)

        def interrupt(self) -> None:
            inner.interrupt()

    col: dict[str, Any] = {"name": "region", "type": "VARCHAR"}
    sc._index_column(_Proxy(), "labels", col, 10000, time.monotonic() + 30)
    return col, sqls


def test_approx_count_keeps_the_cap_decision(tmp_path: Path) -> None:
    """Under the cap the stored set is exact. Over the cap nothing is stored."""
    con = duckdb.connect(str(tmp_path / "approx.duckdb"))
    try:
        small, small_sql = _index(con, 4)
        assert set(small["values"]) == {"r0", "r1", "r2", "r3"}
        assert small.get("capped") is not True
        con.execute("DROP TABLE labels")
        capped, capped_sql = _index(con, 10000)
    finally:
        con.close()
    assert capped.get("values") == []
    assert capped.get("capped") is True
    assert capped.get("distinct") == ">=500"
    blob = " ".join(small_sql + capped_sql)
    assert "approx_count_distinct" in blob
    assert "COUNT(DISTINCT" not in blob.upper()
    assert "SELECT DISTINCT" not in " ".join(capped_sql).upper()


def test_scale_smoke_self_check() -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "verify" / "scale_smoke.py"
    spec = importlib.util.spec_from_file_location("scale_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--self-check"]) == 0
