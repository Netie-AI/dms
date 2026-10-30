"""Pipeline-failure tickets from the extract loop.

One group per reason code and masked question. The line is a WARNING.
It carries the question hash, the stage, the retry count, and the ask id.
It does not carry the question text or the SQL text. The ask envelope
does not change.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import dms_executor.pipeline_failure as tickets
import pytest
from dms_executor.pipeline_failure import log_pipeline_failure_ticket
from test_c_loop_b import (
    _COLD_SQL,
    _EMPTY_SQL,
    _ask,
    _assert_abstain,
    _loop,
    _names,
)

_LITERAL = "Bearer seeded-literal-9f3a"
_CLOCK = {
    "as_of",
    "engine_as_of",
    "engine_as_of_after",
    "engine_timezone",
    "engine_timezone_after",
}
_EMPTY_HASH = hashlib.sha256(b"").hexdigest()
_SIGNATURE = ["reason", "question", "sql", "retries", "stage", "ask_id"]
_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_groups() -> None:
    tickets._reset_pipeline_failures()


def _records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in caplog.records:
        if rec.name != "dms_executor.pipeline_failure":
            continue
        text = rec.getMessage()
        if not text.startswith("pipeline_failure "):
            continue
        assert rec.levelno == logging.WARNING, rec.levelname
        got = json.loads(text[len("pipeline_failure ") :])
        assert isinstance(got, dict)
        out.append(got)
    return out


def _groups(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = str(row["group"])
        prev = best.get(group)
        if prev is None or int(row["count"]) >= int(prev["count"]):
            best[group] = row
    return best


def _freeze(env: dict[str, Any]) -> dict[str, Any]:
    def norm(obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for key, val in obj.items():
                name = str(key)
                if name in _CLOCK and isinstance(val, str):
                    out[name] = "CLOCK"
                else:
                    out[name] = norm(val)
            return out
        if isinstance(obj, list):
            return [norm(item) for item in obj]
        if isinstance(obj, float):
            return round(obj, 6)
        return obj

    frozen = json.loads(json.dumps(norm(env), sort_keys=True, default=str))
    assert isinstance(frozen, dict)
    return frozen


def _watch(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)


def _no_prose(row: dict[str, Any], *needles: str) -> None:
    assert "question" not in row
    assert "sql" not in row
    blob = json.dumps(row)
    for needle in needles:
        assert needle not in blob


def test_signature_names_are_the_only_parameters() -> None:
    params = list(inspect.signature(log_pipeline_failure_ticket).parameters)
    assert params == _SIGNATURE
    assert "rejected_sql" not in params
    assert "retry_count" not in params


def test_flag_on_abstain_kinds_each_write_one_ticket(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)
    cases = (
        (
            "empty_result_unverified:value_exists_pending",
            _EMPTY_SQL,
            "expiring chemicals",
            0,
        ),
        (
            "loop_exhausted:checker:explain",
            "SELECT nope FROM inventory",
            "cold storage locations",
            1,
        ),
        (
            "checker:hostile_sql:path_not_allowed",
            "SELECT * FROM read_csv('notes.csv')",
            "sheet rows",
            0,
        ),
    )
    for reason, sql, question, retries in cases:
        caplog.clear()
        tickets._reset_pipeline_failures()

        def compute(_ctx: dict[str, Any], sql: str = sql) -> dict[str, Any]:
            return _names(query_sql=sql)

        env = _assert_abstain(_ask(tmp_path, question, compute))
        rows = _records(caplog)
        grouped = _groups(rows)
        assert len(grouped) == 1, question
        ticket = next(iter(grouped.values()))
        assert ticket["reason"] == reason, ticket
        assert ticket["stage"] == "extract_loop"
        assert ticket["retries"] == retries
        assert ticket["ask_id"] == env["audit_id"]
        assert ticket["ask_id"] == env["answer_id"]
        assert env["ticket_id"] == ticket["ticket_id"]
        assert str(env["ticket_id"]).isalpha() and str(env["ticket_id"]).islower()
        _no_prose(ticket, question, "notes.csv", "nope")
        assert question not in caplog.text
        assert "pipeline_failure" not in json.dumps(env)


def test_db_error_text_stays_out_of_the_ticket(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)
    marker = "boom-ticket-7c2e"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(
            query_sql=f"SELECT error('{marker}') FROM locations"
        )

    env = _assert_abstain(_ask(tmp_path, "Which locations are cold storage?", compute))
    grouped = _groups(_records(caplog))
    assert len(grouped) == 1
    ticket = next(iter(grouped.values()))
    assert ticket["reason"] == "loop_exhausted:db_error"
    assert ticket["retries"] == 1
    assert ticket["ask_id"] == env["audit_id"]
    _no_prose(ticket, marker)
    assert marker not in caplog.text
    assert marker in json.dumps(env.get("loop"))


def test_cast_marker_stays_out_of_the_ticket(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """CAST is an extra db-error. ``error()`` stays the original case."""
    _loop(monkeypatch)
    _watch(caplog)
    marker = "boom-cast-7c2e"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=f"SELECT CAST('{marker}' AS INTEGER) FROM locations")

    env = _assert_abstain(_ask(tmp_path, "Which locations are cold storage?", compute))
    ticket = next(iter(_groups(_records(caplog)).values()))
    assert ticket["reason"] == "loop_exhausted:db_error"
    _no_prose(ticket, marker)
    assert marker not in caplog.text
    assert marker in json.dumps(env.get("loop"))


def test_flag_off_writes_no_ticket(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.delenv("DMS_LANE_ONTOLOGY_RANKED", raising=False)
    _watch(caplog)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql="SELECT nope FROM inventory")

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert _records(caplog) == []
    assert "ticket_id" not in env
    assert "pipeline_failure" not in json.dumps(env)


def test_served_answer_keeps_attribution_and_writes_no_ticket(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_COLD_SQL)

    env = _ask(tmp_path, "Which locations are cold storage?", compute)
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["served_attribution"] == "reported"
    assert _records(caplog) == []


def test_identical_abstains_are_one_group_and_envelopes_match(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)
    question = "Which chemicals are expiring this month?"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_EMPTY_SQL)

    bodies = []
    for _ in range(3):
        bodies.append(_freeze(_assert_abstain(_ask(tmp_path, question, compute))))
    assert bodies[0] == bodies[1] == bodies[2]
    grouped = _groups(_records(caplog))
    assert len(grouped) == 1
    ticket = next(iter(grouped.values()))
    assert ticket["count"] == 3
    assert ticket["reason"] == "empty_result_unverified:value_exists_pending"
    assert ticket["ask_id"] == bodies[0]["audit_id"]
    _no_prose(ticket, question)
    assert question not in caplog.text


def test_writer_failure_leaves_the_envelope_unchanged(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)
    question = "Which chemicals are expiring this month?"

    def compute(_ctx: dict[str, Any]) -> dict[str, Any]:
        return _names(query_sql=_EMPTY_SQL)

    before = _freeze(_assert_abstain(_ask(tmp_path, question, compute)))
    assert "ticket_id" in before

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("ticket down")

    monkeypatch.setattr(tickets, "log_pipeline_failure_ticket", _boom)
    caplog.clear()
    after = _freeze(_assert_abstain(_ask(tmp_path, question, compute)))
    assert "ticket_id" not in after
    before.pop("ticket_id")
    assert after == before
    assert any(
        rec.getMessage() == "pipeline_failure ticket was not written" for rec in caplog.records
    )


def test_seeded_literal_is_absent_and_mask_failure_is_its_own_group(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch(caplog)
    question = f"Which locations are cold storage? {_LITERAL}"
    sql = f"SELECT sku FROM inventory -- {_LITERAL}"
    log_pipeline_failure_ticket(
        "checker:hostile_sql:path_not_allowed", question, sql, 0, "extract_loop", "ask-seed"
    )
    log_pipeline_failure_ticket(
        "checker:hostile_sql:path_not_allowed", question, sql, 0, "extract_loop", "ask-seed"
    )
    blob = caplog.text
    assert _LITERAL not in blob
    assert "seeded-literal-9f3a" not in blob
    assert "cold storage" not in blob
    grouped = _groups(_records(caplog))
    assert len(grouped) == 1
    ticket = next(iter(grouped.values()))
    assert ticket["count"] == 2
    assert "question_hash" in ticket
    assert ticket["question_hash"] != _EMPTY_HASH
    caplog.clear()
    tickets._reset_pipeline_failures()

    def _fail(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("mask down")

    monkeypatch.setattr("dms_core.pii.fail_closed_mask_payload", _fail)
    log_pipeline_failure_ticket(
        "checker:hostile_sql:path_not_allowed", question, sql, 0, "extract_loop", "ask-a"
    )
    log_pipeline_failure_ticket(
        "checker:hostile_sql:path_not_allowed", question, sql, 0, "extract_loop", "ask-b"
    )
    blob = caplog.text
    assert _LITERAL not in blob
    assert "seeded-literal-9f3a" not in blob
    grouped = _groups(_records(caplog))
    assert len(grouped) == 2
    assert {row["count"] for row in grouped.values()} == {1}
    for row in grouped.values():
        assert row["mask_failed"] is True
        assert "question" not in row
        assert "sql" not in row
        assert "question_hash" not in row
        assert row["group"] != _EMPTY_HASH
        assert row["ask_id"] in {"ask-a", "ask-b"}


def test_reason_and_question_split_groups(caplog: pytest.LogCaptureFixture) -> None:
    _watch(caplog)
    log_pipeline_failure_ticket(
        "empty_result_unverified:value_exists_pending", "question one", "SELECT 1", 0, "s", "a1"
    )
    log_pipeline_failure_ticket(
        "loop_exhausted:checker:explain", "question one", "SELECT 1", 1, "s", "a2"
    )
    log_pipeline_failure_ticket(
        "empty_result_unverified:value_exists_pending", "question two", "SELECT 2", 0, "s", "a3"
    )
    grouped = _groups(_records(caplog))
    assert len(grouped) == 3
    assert {row["count"] for row in grouped.values()} == {1}
    blob = caplog.text
    assert "question one" not in blob
    assert "question two" not in blob
    assert "SELECT 1" not in blob


def test_same_db_error_code_is_one_group(caplog: pytest.LogCaptureFixture) -> None:
    _watch(caplog)
    log_pipeline_failure_ticket(
        "db_error:Catalog Error: boom one", "same question", "SELECT 1", 1, "s", "ask"
    )
    log_pipeline_failure_ticket(
        "db_error:BinderException: boom two", "same question", "SELECT 2", 1, "s", "ask"
    )
    grouped = _groups(_records(caplog))
    assert len(grouped) == 1
    ticket = next(iter(grouped.values()))
    assert ticket["count"] == 2
    assert ticket["reason"] == "db_error"
    assert "boom one" not in caplog.text
    assert "boom two" not in caplog.text
    assert "Catalog" not in caplog.text
    assert "BinderException" not in caplog.text


def test_cap_evicts_and_stays_bounded(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch(caplog)
    monkeypatch.setattr(tickets, "PIPELINE_FAILURE_CAP", 2)
    for index in range(3):
        log_pipeline_failure_ticket("same", f"question {index}", "SELECT 1", 0, "s", "ask")
    assert len(tickets._GROUPS) <= 2
    caplog.clear()
    log_pipeline_failure_ticket("same", "question 0", "SELECT 1", 0, "s", "ask")
    assert len(tickets._GROUPS) <= 2
    revived = next(iter(_groups(_records(caplog)).values()))
    assert revived["count"] == 1


def test_threads_share_one_group_count() -> None:
    import threading

    question = "threaded question"
    errors: list[BaseException] = []

    def _one() -> None:
        try:
            log_pipeline_failure_ticket("threaded", question, "SELECT 1", 0, "s", "ask")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_one) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(tickets._GROUPS) == 1
    count, _seen, _ticket = next(iter(tickets._GROUPS.values()))
    assert count == 8


def _image_layout(dst: Path) -> None:
    """What apps/api/Dockerfile COPYs. No tests/."""
    skip = shutil.ignore_patterns("__pycache__", "*.egg-info", "node_modules", ".pytest_cache")
    for rel in ("packages", "apps/api", "contract", "alembic"):
        shutil.copytree(_ROOT / rel, dst / rel, ignore=skip)
    for rel in ("pyproject.toml", "README.md", "alembic.ini"):
        shutil.copy2(_ROOT / rel, dst / rel)


_BOOT = r"""
import json
import logging
from pathlib import Path
from typing import Any

from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

class Cortex:
    def compute_insights(self, question: str, **_k: Any) -> dict[str, Any]:
        return {"query_sql": "SELECT 1 WHERE 0 = 1"}

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "sql":
            return QueryResult(
                ok=True, status="ok", run_id="run_boot_sql", output={"rows": [{"n": 7}]}
            )
        return QueryResult(ok=True, status="bound", run_id="run_boot_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_boot", hash="hash_boot_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        return AskResponse(
            answer="There are 5 locations.",
            badge="certified",
            sql_used="SELECT 1",
            rows=[{"n": 1}],
            assumptions="fixture",
            audit_id="aud_boot",
            route="sql",
        )

def minter() -> ManifestMinter:
    m = ManifestMinter()

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

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return m

app = create_app()
logging.getLogger().handlers.clear()
wh = Path("wh.duckdb")
ensure_demo_warehouse(wh)
cortex = Cortex()
exe = Executor(cortex=cortex, minter=minter(), warehouse_path=wh)
app.state.ask_service = exe
app.state.cortex = cortex
client = TestClient(app)
health = client.get("/health")
ask = client.post(
    "/v1/chat/ask",
    json={
        "question": "how many florbs did wibble sell last quarter florbs-ticket-nonce-7c2e",
        "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "session_id": "ses_boot_ticket",
    },
)
body = ask.json()
if ask.status_code == 200 and isinstance(body, dict) and "badge" in body:
    assert_envelope_valid(body)
print(json.dumps({
    "health": health.status_code,
    "ask": ask.status_code,
    "badge": body.get("badge") if isinstance(body, dict) else None,
    "abstained": body.get("abstained") if isinstance(body, dict) else None,
    "ask_id": body.get("audit_id") if isinstance(body, dict) else None,
    "ticket_id": body.get("ticket_id") if isinstance(body, dict) else None,
}))
exe.close()
"""


def _boot_env(app_root: Path, *, cloop: bool) -> dict[str, str]:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            str(app_root / name)
            for name in (
                "apps/api",
                "packages/core",
                "packages/cortex_client",
                "packages/executor",
                "packages/ledger",
            )
        ),
        "DMS_DEMO_FALLBACK": "0",
        "DMS_SKIP_CONTROL_PLANE_TESTS": "1",
    }
    for name in (
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_LANE_BRONZE_SHEET",
        "DMS_SERVED_ATTR_DIAG",
        "DMS_CCA_CASCADE",
        "DMS_HARNESS_ASK_PATHS",
        "DATABASE_URL",
    ):
        env.pop(name, None)
    if cloop:
        env["DMS_CLOOP_B"] = "1"
    return env


def _run_boot(tmp_path: Path, *, cloop: bool) -> subprocess.CompletedProcess[str]:
    app_root = tmp_path / "app"
    _image_layout(app_root)
    assert not (app_root / "tests").exists()
    return subprocess.run(
        [sys.executable, "-c", _BOOT],
        cwd=app_root,
        env=_boot_env(app_root, cloop=cloop),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_copy_only_image_logs_a_warning_ticket(tmp_path: Path) -> None:
    """Dockerfile COPY set, no tests/. Flag on. The real stderr has a ticket."""
    proc = _run_boot(tmp_path, cloop=True)
    assert proc.returncode == 0, proc.stderr[-4000:]
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["health"] == 200, got
    assert got["ask"] == 200, got
    assert got["badge"] == "ABSTAIN", got
    assert got["abstained"] is True, got
    # Default root level is WARNING, and this process adds no handler.
    # An INFO ticket never reaches stderr. A WARNING ticket does.
    lines = [line for line in proc.stderr.splitlines() if "pipeline_failure " in line]
    assert lines, proc.stderr[-4000:]
    payload = json.loads(lines[0].split("pipeline_failure ", 1)[1])
    assert payload["stage"] == "extract_loop"
    assert payload["ask_id"] == got["ask_id"]
    assert "question" not in payload
    assert "sql" not in payload
    assert "florbs-ticket-nonce-7c2e" not in lines[0]
    assert "SELECT 1 WHERE 0 = 1" not in lines[0]
    assert isinstance(got["ticket_id"], str) and got["ticket_id"].isalpha()


_UVICORN = r"""
import os
import uvicorn
from dms_api.app import create_app

uvicorn.run(
    create_app(),
    host="127.0.0.1",
    port=int(os.environ["PORT"]),
    log_level="warning",
)
"""


def _http(method: str, url: str, body: bytes | None = None) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
            return resp.status, payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload if isinstance(payload, dict) else {}


def _uvicorn_boot(tmp_path: Path, *, cloop: bool) -> tuple[dict[str, Any], str, int]:
    app_root = tmp_path / "app"
    _image_layout(app_root)
    assert not (app_root / "tests").exists()
    port = 20000 + (os.getpid() % 20000)
    env = _boot_env(app_root, cloop=cloop)
    env["PORT"] = str(port)
    env["CORTEX_TIMEOUT_SECONDS"] = "2"
    proc = subprocess.Popen(
        [sys.executable, "-c", _UVICORN],
        cwd=app_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        health = 0
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                err = proc.stderr.read() if proc.stderr else ""
                raise AssertionError(f"uvicorn exited {proc.returncode}\n{err[-2000:]}")
            try:
                health, _body = _http("GET", f"http://127.0.0.1:{port}/health")
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.2)
                continue
            if health == 200:
                break
            time.sleep(0.2)
        assert health == 200
        status, ask = _http(
            "POST",
            f"http://127.0.0.1:{port}/v1/chat/ask",
            json.dumps(
                {
                    "question": "how many florbs did wibble sell",
                    "space_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    "session_id": "ses_uv_ticket",
                }
            ).encode(),
        )
        assert status == 200, ask
        proc.send_signal(signal.SIGTERM)
        try:
            _out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            _out, err = proc.communicate()
            raise AssertionError("uvicorn did not exit on SIGTERM")
        return ask, err, proc.returncode
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def test_copy_only_uvicorn_sigterm_flag_on(tmp_path: Path) -> None:
    """Real uvicorn, flag on. Health, one abstain ticket, SIGTERM."""
    ask, err, code = _uvicorn_boot(tmp_path, cloop=True)
    assert ask.get("badge") == "ABSTAIN"
    assert ask.get("abstained") is True
    assert isinstance(ask.get("ticket_id"), str) and str(ask["ticket_id"]).isalpha()
    lines = [line for line in err.splitlines() if "pipeline_failure " in line]
    assert len(lines) == 1, err[-2000:]
    payload = json.loads(lines[0].split("pipeline_failure ", 1)[1])
    assert payload["ticket_id"] == ask["ticket_id"]
    assert payload["reason"] != "unspecified"
    assert "Traceback" not in err
    assert code in {0, -signal.SIGTERM, 128 + signal.SIGTERM}


def test_copy_only_uvicorn_sigterm_flag_off(tmp_path: Path) -> None:
    """Real uvicorn, flag off. Normal envelope, no ticket, SIGTERM."""
    ask, err, code = _uvicorn_boot(tmp_path, cloop=False)
    assert ask.get("badge")
    assert ask.get("ticket_id") is None
    assert "pipeline_failure " not in err
    assert "Traceback" not in err
    assert code in {0, -signal.SIGTERM, 128 + signal.SIGTERM}


def test_copy_only_image_flag_off_has_no_ticket(tmp_path: Path) -> None:
    """Flag off: a normal envelope, no ticket line, no ticket id."""
    proc = _run_boot(tmp_path, cloop=False)
    assert proc.returncode == 0, proc.stderr[-4000:]
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["health"] == 200, got
    assert got["ask"] == 200, got
    assert got["badge"]
    assert got["ticket_id"] is None
    assert "pipeline_failure " not in proc.stderr
