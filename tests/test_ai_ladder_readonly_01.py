"""Read-only extract: DuckDB refuses writes, one SELECT, lock wait abstains.

The regex guard is not on this path. ``run_readonly`` is called directly.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client.compute import begin_answer_model_calls
from dms_executor.cca.binder import scan_landed_columns
from dms_executor.cca.cascade import cascade_enabled, run_cascade
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.schema_context import build_space_index
from dms_executor.sql_loop import run_readonly

_INSERT = (
    "INSERT INTO locations BY NAME SELECT * REPLACE "
    "('ZZ-PROBE' AS location_id) FROM locations LIMIT 1"
)
_CREATE = "CREATE TABLE zz_probe AS SELECT 1"
_DELETE = "DELETE FROM locations WHERE location_id = 'WH-A'"
_ALTER = "SELECT 1; ALTER TABLE locations RENAME TO locations_gone"
_PARSE = "not sql at all $$$"
_WITH = "WITH c AS (SELECT COUNT(*) AS n FROM locations) SELECT n FROM c"
_COUNT = "SELECT COUNT(*) AS n FROM locations"
_GRANTS = {"locations", "inventory", "suppliers", "transactions", "shipments", "alerts"}


def _rows(db: Path, sql: str) -> list[tuple[Any, ...]]:
    con = connect_file(db)
    try:
        return list(con.execute(sql).fetchall())
    finally:
        con.close()


def _refused(err: str | None) -> None:
    assert err is not None
    assert "InvalidInputException" in err
    assert "read-only" in err


def test_insert_is_refused_by_the_read_only_engine(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "ins.duckdb")
    rows, err = run_readonly(_INSERT, db)
    assert rows is None
    _refused(err)
    assert _rows(db, "SELECT COUNT(*) FROM locations") == [(5,)]
    assert _rows(db, "SELECT COUNT(*) FROM locations WHERE location_id = 'ZZ-PROBE'") == [(0,)]


def test_create_is_refused_by_the_read_only_engine(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "crt.duckdb")
    rows, err = run_readonly(_CREATE, db)
    assert rows is None
    _refused(err)
    names = {str(r[0]) for r in _rows(
        db, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    )}
    assert "zz_probe" not in names


def test_delete_is_refused_by_the_read_only_engine(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "del.duckdb")
    rows, err = run_readonly(_DELETE, db)
    assert rows is None
    _refused(err)
    assert _rows(db, "SELECT COUNT(*) FROM locations") == [(5,)]


def test_two_statements_are_refused_before_execute(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "alt.duckdb")
    rows, err = run_readonly(_ALTER, db)
    assert rows is None
    assert err == "multi_statement"
    assert _rows(db, "SELECT COUNT(*) FROM locations") == [(5,)]


def test_parse_failure_is_refused(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "parse.duckdb")
    rows, err = run_readonly(_PARSE, db)
    assert rows is None
    assert err is not None
    assert err.startswith("ParserException:")


def test_with_select_runs(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "with.duckdb")
    rows, err = run_readonly(_WITH, db)
    assert err is None
    assert rows == [{"n": 5}]


def test_eight_reads_and_two_index_builds(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "burst.duckdb")
    got: list[tuple[Any, Any]] = []
    stamps: list[str] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def _read() -> None:
        try:
            result = run_readonly(_COUNT, db)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            return
        with guard:
            got.append(result)

    def _index(space: str) -> None:
        try:
            stamp = build_space_index(db, space, _GRANTS, "duckdb")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            return
        with guard:
            stamps.append(stamp)

    threads = [threading.Thread(target=_read) for _ in range(8)]
    threads.extend(
        threading.Thread(target=_index, args=(f"sp-burst-{i}",)) for i in range(2)
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert len(got) == 8
    assert all(err is None and rows == [{"n": 5}] for rows, err in got)
    assert stamps == ["", ""]


def test_cascade_scan_waits_on_the_same_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_CCA_CASCADE", "1")
    assert cascade_enabled()
    db = ensure_demo_warehouse(tmp_path / "cca.duckdb")
    started = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        con = connect_file(db)
        started.set()
        assert release.wait(5)
        con.close()

    holder = threading.Thread(target=_hold)
    holder.start()
    assert started.wait(5)
    box: dict[str, Any] = {}

    def _scan() -> None:
        box["scan"] = scan_landed_columns(
            db, tables=["suppliers"], column_names=("country",)
        )

    def _ask() -> None:
        box["rows"], box["err"] = run_readonly(_COUNT, db)
        box["cascade"] = run_cascade(
            "spend across SEA", warehouse=db, tables=["suppliers", "locations"]
        )

    scan = threading.Thread(target=_scan)
    ask = threading.Thread(target=_ask)
    scan.start()
    ask.start()
    scan.join(0.3)
    ask.join(0.3)
    assert scan.is_alive()
    assert ask.is_alive()
    release.set()
    scan.join(10)
    ask.join(10)
    holder.join(5)
    assert box["err"] is None
    assert box["rows"] == [{"n": 5}]
    assert isinstance(box["scan"], list)
    assert box["cascade"].engaged is True


def _names(sql: str) -> dict[str, Any]:
    return {
        "phase": "generate",
        "query_sql": sql,
        "served_model": "fake-model",
        "served_provider": "fake-provider",
        "ov_key_id": "ovk-fake",
    }


def _submit(db: Path):
    def submit(sql: str) -> Any:
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_ro", output={"rows": rows})

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_ro", hash="hash_ro")


def _ask(db: Path, onto: Any) -> dict[str, Any] | None:
    begin_answer_model_calls()
    return maybe_generative_ask(
        "Count the location rows",
        warehouse=db,
        grantable=set(_GRANTS),
        compute=lambda _ctx: _names(_COUNT),
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
    )


def _grade(env: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(env, dict):
        return "abstain", "no_envelope"
    if env.get("abstained"):
        reason = "unspecified"
        for item in env.get("assumptions") or []:
            text = str(item)
            if text.startswith("GEN-01: "):
                reason = text[len("GEN-01: ") :].split(":", 1)[0].strip() or reason
                break
        return "abstain", reason
    if env.get("rows") == [{"n": 5}]:
        return "correct", ""
    return "wrong", ""


def _tally(grades: list[tuple[str, str]]) -> tuple[int, int, int, dict[str, int]]:
    correct = sum(1 for kind, _reason in grades if kind == "correct")
    wrong = sum(1 for kind, _reason in grades if kind == "wrong")
    abstain = sum(1 for kind, _reason in grades if kind == "abstain")
    reasons: dict[str, int] = {}
    for kind, reason in grades:
        if kind == "abstain" and reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    return correct, wrong, abstain, reasons


def test_lock_wait_ticket_is_serving_lock_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    monkeypatch.setattr(
        "cortex_client.compute.insights_timeout_s", lambda env=None: 0.25
    )
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    db = ensure_demo_warehouse(tmp_path / "wait.duckdb")
    onto = load_verified_ontology(db, demo_ontology(db))
    release = threading.Event()
    ready = threading.Event()
    fired = {"on": False}

    class _Conn:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return self._inner.execute(*args, **kwargs)

        def close(self) -> None:
            self._inner.close()
            if fired["on"]:
                return
            fired["on"] = True

            def _run() -> None:
                con = connect_file(db)
                ready.set()
                assert release.wait(5)
                con.close()

            threading.Thread(target=_run, daemon=True).start()
            assert ready.wait(5)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    def _connect(path: Path, **kwargs: Any) -> _Conn:
        from dms_executor.demo_warehouse import connect_locked_readonly as real

        return _Conn(real(path, **kwargs))

    monkeypatch.setattr("dms_executor.generative_ask.connect_locked_readonly", _connect)
    try:
        env = _ask(db, onto)
    finally:
        release.set()
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "dms_executor.pipeline_failure"
        and rec.getMessage().startswith("pipeline_failure ")
    ]
    assert len(lines) == 1
    payload = json.loads(lines[0].split(" ", 1)[1])
    assert payload["reason"] == "serving_lock_wait"
    assert payload["ticket_id"] == env["ticket_id"]
    assert isinstance(payload["ticket_id"], str) and payload["ticket_id"]
    blob = lines[0]
    assert "Count the location rows" not in blob
    assert "SELECT" not in blob
    assert "TimeoutError" not in blob
    assert "serving lock" not in blob


def test_checker_lock_wait_is_serving_lock_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A writer held before the ask must abstain, not hang in the checker."""
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    monkeypatch.setattr(
        "cortex_client.compute.insights_timeout_s", lambda env=None: 0.25
    )
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    db = ensure_demo_warehouse(tmp_path / "checker-wait.duckdb")
    onto = load_verified_ontology(db, demo_ontology(db))
    release = threading.Event()
    ready = threading.Event()

    def _hold() -> None:
        con = connect_file(db)
        ready.set()
        assert release.wait(5)
        con.close()

    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    assert ready.wait(5)
    box: dict[str, Any] = {}

    def _run() -> None:
        box["env"] = _ask(db, onto)

    ask = threading.Thread(target=_run, daemon=True)
    ask.start()
    ask.join(3)
    release.set()
    holder.join(5)
    assert not ask.is_alive()
    env = box["env"]
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "dms_executor.pipeline_failure"
        and rec.getMessage().startswith("pipeline_failure ")
    ]
    assert len(lines) == 1
    payload = json.loads(lines[0].split(" ", 1)[1])
    assert payload["reason"] == "serving_lock_wait"
    assert payload["ticket_id"] == env["ticket_id"]
    blob = lines[0]
    assert "Count the location rows" not in blob
    assert "SELECT" not in blob
    assert "TimeoutError" not in blob
    assert "serving lock" not in blob


def test_twenty_asks_p95_stays_under_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    monkeypatch.setenv("DMS_LANE_ONTOLOGY_RANKED", "0")
    db = ensure_demo_warehouse(tmp_path / "p95.duckdb")
    onto = load_verified_ontology(db, demo_ontology(db))
    serial = _grade(_ask(db, onto))
    n = 20
    samples: list[float] = []
    grades: list[tuple[str, str]] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def _one() -> None:
        started = time.perf_counter()
        try:
            env = _ask(db, onto)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            return
        elapsed = time.perf_counter() - started
        with guard:
            samples.append(elapsed)
            grades.append(_grade(env))

    threads = [threading.Thread(target=_one) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    assert len(grades) == n
    serial_correct = 1 if serial[0] == "correct" else 0
    correct, wrong, abstain, reasons = _tally(grades)
    ordered = sorted(samples)
    rank = max(1, math.ceil(0.95 * n))
    p95 = ordered[rank - 1]
    slowest = ordered[-1]
    reason_text = ",".join(f"{code}:{count}" for code, count in sorted(reasons.items())) or "-"
    print(
        f"serial n=1 correct={serial_correct} wrong={int(serial[0] == 'wrong')} "
        f"abstain={int(serial[0] == 'abstain')} reasons={serial[1] or '-'}"
    )
    print(
        f"load n={n} correct={correct} wrong={wrong} abstain={abstain} "
        f"reasons={reason_text} p95={p95:.3f}s max={slowest:.3f}s"
    )
    assert correct >= serial_correct
    assert p95 <= 7.5, f"n={n} p95={p95:.3f}s max={slowest:.3f}s"
