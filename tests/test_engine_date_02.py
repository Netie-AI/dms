"""ENGINE-DATE-02 / dms#308 follow-up.

Live rounds name the clock source, close on a second /health read, and
refuse any-case $AS_OF. Harness-only. No network, no keys.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from score_curated import (  # noqa: E402
    DEFAULT_PACK,
    grid_score_hook,
    live,
    load_pack,
    merge_pack_questions,
)

_START = "2024-01-01"
_END = "2024-01-02"
_CASE_DAY = "2024-06-15"
_TZ = "UTC"
_CLOCK_KEYS = (
    "engine_as_of",
    "engine_as_of_after",
    "engine_timezone",
    "engine_timezone_after",
)


def _pack_n() -> int:
    pack = load_pack(DEFAULT_PACK)
    return len(merge_pack_questions(list(pack["questions"])))


def _envelope(*, case_clock: bool) -> dict[str, Any]:
    env: dict[str, Any] = {
        "badge": "ABSTAIN",
        "abstained": True,
        "rows": [],
        "values": [],
        "text": "abstain",
    }
    if case_clock:
        env.update(
            {
                "engine_as_of": _CASE_DAY,
                "engine_as_of_after": _CASE_DAY,
                "engine_timezone": _TZ,
                "engine_timezone_after": _TZ,
            }
        )
    return env


class _Resp:
    def __init__(self, body: dict[str, Any], status: int = 200) -> None:
        self.status_code = status
        self._body = body
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


def _oracle_db(tmp_path: Path) -> Path:
    db = tmp_path / "oracle.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('schema_version', 'engine-date-02')")
    finally:
        con.close()
    return db


def _health(day: str, *, after: str | None = None) -> dict[str, Any]:
    return {
        "status": "ok",
        "product": "dms",
        "ask_mode": "live",
        "demo_fallback": False,
        "engine_as_of": day,
        "engine_as_of_after": day if after is None else after,
        "engine_timezone": _TZ,
        "engine_timezone_after": _TZ,
    }


def _publish_generated_clock(env: dict[str, Any]) -> None:
    """Connection read for a case_clock_at clock. Constants, not the envelope.

    Only the generated 2024-06-15/UTC pair. A mutate or a planted date
    does not match, so it records nothing.
    """
    generated = (
        env.get("engine_as_of"),
        env.get("engine_as_of_after"),
        env.get("engine_timezone"),
        env.get("engine_timezone_after"),
    )
    if generated != (_CASE_DAY, _CASE_DAY, _TZ, _TZ):
        return
    from dms_executor.demo_warehouse import _publish_engine_clock, clear_engine_clock

    _publish_engine_clock(_CASE_DAY, _TZ, _CASE_DAY, _TZ)
    clear_engine_clock()


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    health_bodies: list[dict[str, Any] | None],
    health_error_at: int | None = None,
    case_clock_at: set[int] | None = None,
    mutate: Any = None,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Stub score_http. health_bodies[0] is the open read. Later bodies are later GETs."""
    from dms_executor.demo_warehouse import clear_engine_clock

    clear_engine_clock()
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    asks: list[dict[str, Any]] = []
    health_gets: list[int] = []
    cursor = {"i": 0}
    case_clock_at = case_clock_at if case_clock_at is not None else set()

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> _Resp:
        if method == "GET" and url.rstrip("/").endswith("/health"):
            n = len(health_gets) + 1
            health_gets.append(n)
            if health_error_at == n:
                raise OSError("end health unread")
            body = health_bodies[n - 1] if n - 1 < len(health_bodies) else health_bodies[-1]
            if body is None:
                return _Resp({"status": "ok", "product": "dms"})
            return _Resp(body)
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            body = dict(json_body or {})
            asks.append(body)
            idx = cursor["i"]
            cursor["i"] += 1
            env = _envelope(case_clock=idx in case_clock_at)
            if mutate is not None:
                mutate(idx, env)
            end_missing = health_error_at is not None or any(
                body is None for body in health_bodies
            )
            if idx in case_clock_at and not end_missing:
                _publish_generated_clock(env)
            return _Resp(env)
        raise RuntimeError(f"unexpected {method} {url}")

    monkeypatch.setattr("score_curated.score_http", score_http)
    return asks, health_gets


def _report(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))


def _lines(report: dict[str, Any]) -> list[dict[str, Any]]:
    path = Path(str(report["case_record"]))
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _outside(tmp_path: Path) -> Path:
    path = tmp_path / "out_records"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_live_case_record_names_clock_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case vs round_health on each record line. The grid has its own count."""
    db = _oracle_db(tmp_path)
    _install(
        monkeypatch,
        tmp_path,
        health_bodies=[_health(_START), _health(_START)],
        case_clock_at={0},
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    lines = _lines(report)
    asks, _gets = _install(
        monkeypatch,
        tmp_path,
        health_bodies=[_health(_START), _health(_START)],
        case_clock_at={0},
    )
    grid = grid_score_hook("http://score.test", 1.0, db)
    assert asks  # the grid round asked
    assert (
        lines[0].get("clock_source"),
        lines[1].get("clock_source"),
        sum(1 for row in lines if row.get("clock_source") == "round_health"),
        report.get("round_health"),
        grid.get("round_health"),
        report.get("n"),
        "UNCONFIRMED" in {row.get("outcome") for row in lines},
    ) == ("case", "round_health", _pack_n() - 1, _pack_n() - 1, _pack_n() - 1, _pack_n(), False)


def test_live_second_health_read_spans_round_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end date is the second GET. The first body's after field stays the start day."""
    db = _oracle_db(tmp_path)
    _asks, gets = _install(
        monkeypatch,
        tmp_path,
        health_bodies=[
            _health(_START),
            _health(_END, after=_START),
        ],
        case_clock_at={0},
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    cases = report["cases"]
    rest = [row for row in cases if row is not cases[0]]
    case_spanned = (
        cases[0].get("verdict") == "INVALID"
        and cases[0].get("reason") == "round_spans_midnight"
    )
    assert (
        len(gets),
        report.get("n"),
        report.get("invalid"),
        case_spanned,
        rest[0].get("verdict"),
        rest[0].get("reason"),
        "UNCONFIRMED" in {row.get("verdict") for row in cases},
    ) == (2, _pack_n(), _pack_n() - 1, False, "INVALID", "round_spans_midnight", False)


def test_live_first_health_after_is_not_the_end_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different engine_as_of_after on the open read is not a midnight cross."""
    db = _oracle_db(tmp_path)
    _asks, gets = _install(
        monkeypatch,
        tmp_path,
        health_bodies=[
            _health(_START, after=_END),
            _health(_START),
        ],
        case_clock_at=set(),
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    reasons = {row.get("reason") for row in report["cases"]}
    assert (
        len(gets),
        report.get("n"),
        report.get("invalid"),
        "round_spans_midnight" in reasons,
        "engine_date_mismatch" in reasons,
    ) == (2, _pack_n(), 0, False, False)


def test_live_end_health_failure_is_round_end_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(_outside(tmp_path)))
    db = _oracle_db(tmp_path)
    _install(
        monkeypatch,
        tmp_path,
        health_bodies=[_health(_START)],
        health_error_at=2,
        case_clock_at=set(range(_pack_n())),
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert (
        report.get("reason"),
        report.get("round_label"),
        report.get("baseline_eligible"),
        report.get("baseline_ineligible_reasons"),
        report.get("n"),
        report.get("reason") == "UNCONFIRMED",
    ) == (
        "round_end_unread",
        "INVALID",
        False,
        ["round_end_unread", "engine_clock_masked"],
        _pack_n(),
        False,
    )


def test_live_end_health_empty_is_round_end_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(_outside(tmp_path)))
    db = _oracle_db(tmp_path)
    _install(
        monkeypatch,
        tmp_path,
        health_bodies=[_health(_START), None],
        case_clock_at=set(range(_pack_n())),
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert (
        report.get("reason"),
        report.get("round_label"),
        report.get("baseline_ineligible_reasons"),
        report.get("n"),
    ) == ("round_end_unread", "INVALID", ["round_end_unread", "engine_clock_masked"], _pack_n())


def test_live_one_timezone_reading_is_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _oracle_db(tmp_path)

    def mutate(idx: int, env: dict[str, Any]) -> None:
        if idx == 3:
            env.pop("engine_timezone_after", None)

    _install(
        monkeypatch,
        tmp_path,
        health_bodies=[_health(_START), _health(_START)],
        case_clock_at=set(range(_pack_n())),
        mutate=mutate,
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    hit = report["cases"][3]
    assert (
        report.get("n"),
        report.get("invalid"),
        hit.get("verdict"),
        hit.get("reason"),
        "UNCONFIRMED",
    ) == (_pack_n(), 1, "INVALID", "engine_timezone_unread", "UNCONFIRMED")


def test_live_nosql_after_sql_carries_no_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SQL answer's clock does not ride onto the next no-SQL answer or round."""
    from dms_executor import Executor
    from dms_executor.demo_warehouse import current_engine_clock

    exe = Executor(warehouse_path=tmp_path / "sql.duckdb")
    sql_env = exe.answer_user_sql("SELECT 1 AS n", session_id="ses_sql")
    sql_day = sql_env.get("engine_as_of")
    assert sql_day
    nosql = exe.answer_user_sql("SELECT $as_of AS day", session_id="ses_nosql")
    asks, _gets = _install(
        monkeypatch,
        tmp_path,
        health_bodies=[{
            "status": "ok",
            "product": "dms",
            "ask_mode": "live",
            "demo_fallback": False,
        }],
    )
    live("http://score.test", 1.0, db := _oracle_db(tmp_path))
    report = _report(tmp_path)
    assert db.is_file()
    assert (
        current_engine_clock() is None,
        nosql.get("engine_as_of"),
        report.get("oracle_as_of") == sql_day,
        len(asks),
    ) == (True, None, False, 0)


def test_live_user_sql_upper_as_of_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import Executor
    from dms_executor.envelope import RESERVED_PARAM_AS_OF, assert_envelope_valid

    db = _oracle_db(tmp_path)
    _install(
        monkeypatch,
        tmp_path,
        health_bodies=[_health(_START), _health(_START)],
        case_clock_at=set(range(_pack_n())),
    )
    live("http://score.test", 1.0, db)
    assert _report(tmp_path)["n"] == _pack_n()
    exe = Executor(warehouse_path=tmp_path / "user.duckdb")
    try:
        env = exe.answer_user_sql("SELECT $AS_OF AS day", session_id="ses_upper")
    except Exception as exc:  # noqa: BLE001 - parent lets DuckDB see $AS_OF
        env = {
            "abstain_reason": type(exc).__name__,
            "rows": [1],
            "badge": "ERR",
            "text": str(exc),
        }
    if env.get("badge") == "ABSTAIN" and env.get("abstain_reason") == RESERVED_PARAM_AS_OF:
        assert_envelope_valid(env)
    assert (
        env.get("abstain_reason"),
        env.get("rows"),
        env.get("badge"),
    ) == (RESERVED_PARAM_AS_OF, [], "ABSTAIN")


def test_live_generated_sql_upper_as_of_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cortex_contract.execution import Manifest as M
    from cortex_contract.execution import QueryResult
    from dms_executor import Executor
    from dms_executor.envelope import RESERVED_PARAM_AS_OF, assert_envelope_valid
    from dms_executor.manifest import ManifestMinter, SessionAcl

    db = _oracle_db(tmp_path)
    _install(
        monkeypatch,
        tmp_path,
        health_bodies=[_health(_START), _health(_START)],
        case_clock_at=set(range(_pack_n())),
    )
    live("http://score.test", 1.0, db)
    assert _report(tmp_path)["n"] == _pack_n()

    class _Insights:
        def __init__(self) -> None:
            self.submits: list[Any] = []

        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            return {
                "query_sql": "SELECT $AS_OF AS day",
                "served_provider": "stub",
                "served_model": "stub",
            }

        def submit(self, req: Any) -> QueryResult:
            self.submits.append(req)
            return QueryResult(ok=True, status="ok", run_id="run", output={"rows": []})

        def ledger_append(self, _req: Any) -> Any:
            return None

        def ask(self, _req: Any) -> Any:
            raise AssertionError("contract ask ran")

    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> M:
        return M(
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
    fake = _Insights()
    exe = Executor(
        cortex=fake,  # type: ignore[arg-type]
        minter=minter,
        warehouse_path=tmp_path / "gen.duckdb",
    )
    try:
        env = exe.live_ask(
            "What is total stock value by category?",
            session_id="ses_engine_date_02",
            ask_path="generative",
        )
    except Exception as exc:  # noqa: BLE001 - parent does not abstain on $AS_OF
        env = {"abstain_reason": type(exc).__name__, "rows": [1], "badge": "ERR", "text": str(exc)}
    if env.get("badge") == "ABSTAIN" and env.get("abstain_reason") == RESERVED_PARAM_AS_OF:
        assert_envelope_valid(env)
    assert (env.get("abstain_reason"), env.get("rows"), env.get("badge"), fake.submits) == (
        RESERVED_PARAM_AS_OF,
        [],
        "ABSTAIN",
        [],
    )


def test_live_followup_sql_upper_as_of_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.envelope import RESERVED_PARAM_AS_OF, assert_envelope_valid
    from dms_executor.session_followup import run_followup_sql

    db = _oracle_db(tmp_path)
    _install(
        monkeypatch,
        tmp_path,
        health_bodies=[_health(_START), _health(_START)],
        case_clock_at=set(range(_pack_n())),
    )
    live("http://score.test", 1.0, db)
    assert _report(tmp_path)["n"] == _pack_n()
    warehouse = ensure_demo_warehouse(tmp_path / "follow.duckdb")
    try:
        env = run_followup_sql(
            "SELECT $AS_OF AS day",
            warehouse=warehouse,
            space_id=None,
            session_id="ses_follow",
            question="average of them",
            why="follow-up sql",
            text="should not run",
        )
    except Exception as exc:  # noqa: BLE001 - parent lets DuckDB see $AS_OF
        env = {"abstain_reason": type(exc).__name__, "rows": [1], "badge": "ERR", "route": "ERR"}
    if env.get("abstain_reason") == RESERVED_PARAM_AS_OF:
        assert_envelope_valid(env)
    assert (env.get("abstain_reason"), env.get("rows"), env.get("route")) == (
        RESERVED_PARAM_AS_OF,
        [],
        "followup",
    )


_SQL_A = "2024-05-01"
_SQL_B = "2024-05-02"


def test_live_sql_nosql_sql_records_keep_own_clocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One live() round. SQL, then no-SQL, then SQL. No clock reset between them."""
    import dms_executor.demo_warehouse as warehouse
    from dms_executor import Executor

    reads = {"n": 0}

    def read_clock(_con: Any) -> tuple[str, str]:
        reads["n"] += 1
        day = _SQL_A if reads["n"] <= 2 else _SQL_B
        return day, _TZ

    monkeypatch.setattr(warehouse, "_read_con_clock", read_clock)
    exe = Executor(warehouse_path=tmp_path / "seq.duckdb")
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    asks: list[int] = []

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> _Resp:
        if method == "GET" and url.rstrip("/").endswith("/health"):
            return _Resp(_health(_START))
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            idx = len(asks)
            asks.append(idx)
            if idx == 0:
                env = exe.answer_user_sql("SELECT 1 AS n", session_id="ses_a")
            elif idx == 1:
                env = exe.answer_user_sql("SELECT $as_of AS day", session_id="ses_b")
            elif idx == 2:
                env = exe.answer_user_sql("SELECT 2 AS n", session_id="ses_c")
            else:
                env = _envelope(case_clock=False)
            return _Resp(env)
        raise RuntimeError(f"unexpected {method} {url}")

    monkeypatch.setattr("score_curated.score_http", score_http)
    live("http://score.test", 1.0, _oracle_db(tmp_path))
    report = _report(tmp_path)
    lines = _lines(report)

    def carried(row: dict[str, Any]) -> tuple[Any, Any, Any]:
        env = row.get("envelope") or {}
        return (
            row.get("engine_date"),
            env.get("engine_as_of"),
            env.get("engine_as_of_after"),
        )

    assert (
        carried(lines[0]),
        carried(lines[1]),
        carried(lines[2]),
        report.get("n"),
    ) == (
        (_SQL_A, _SQL_A, _SQL_A),
        (None, None, None),
        (_SQL_B, _SQL_B, _SQL_B),
        _pack_n(),
    )
    assert _SQL_A not in json.dumps(lines[1])
    assert _SQL_A not in json.dumps(lines[2])
    assert _SQL_B not in json.dumps(lines[0])
    assert _SQL_B not in json.dumps(lines[1])
