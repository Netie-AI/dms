"""ENGINE-DATE-01 / dms#308 follow-up.

Live rounds take the engine date from the ask path. $as_of binds only on
oracle and scorer calls. Harness-only. No network, no keys.
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
    judge_envelope_detailed,
    live,
    load_oracles,
    load_pack,
    merge_pack_questions,
)

_ENGINE_DAY = "2024-06-15"
_ROUND_DAY = "2024-01-01"
_TZ = "UTC"


def _questions() -> list[dict[str, Any]]:
    pack = load_pack(DEFAULT_PACK)
    return merge_pack_questions(list(pack["questions"]))


def _envelope(
    *,
    before: str = _ENGINE_DAY,
    after: str = _ENGINE_DAY,
    tz: str = _TZ,
    tz_after: str | None = None,
) -> dict[str, Any]:
    return {
        "badge": "ABSTAIN",
        "abstained": True,
        "rows": [],
        "values": [],
        "text": "abstain",
        "engine_as_of": before,
        "engine_as_of_after": after,
        "engine_timezone": tz,
        "engine_timezone_after": tz_after if tz_after is not None else tz,
    }


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
        con.execute(
            "INSERT INTO meta VALUES ('schema_version', 'engine-date-01')"
        )
    finally:
        con.close()
    return db


def _install_ask(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    health: dict[str, Any],
    mutate: Any,
) -> list[dict[str, Any]]:
    from dms_executor.demo_warehouse import clear_engine_clock

    clear_engine_clock()
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    questions = _questions()
    cursor = {"i": 0}
    asks: list[dict[str, Any]] = []

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> _Resp:
        if method == "GET" and url.rstrip("/").endswith("/health"):
            return _Resp(health)
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            body = dict(json_body or {})
            asks.append(body)
            idx = cursor["i"]
            cursor["i"] += 1
            case = questions[idx] if idx < len(questions) else {}
            env = _envelope()
            mutate(idx, case, env)
            return _Resp(env)
        raise RuntimeError(f"unexpected {method} {url}")

    monkeypatch.setattr("score_curated.score_http", score_http)
    return asks


def _health_open() -> dict[str, Any]:
    return {
        "status": "ok",
        "product": "dms",
        "ask_mode": "live",
        "demo_fallback": False,
        "engine_as_of": _ROUND_DAY,
        "engine_as_of_after": _ROUND_DAY,
        "engine_timezone": _TZ,
        "engine_timezone_after": _TZ,
    }


def _health_unread() -> dict[str, Any]:
    return {
        "status": "ok",
        "product": "dms",
        "ask_mode": "live",
        "demo_fallback": False,
    }


def _report(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))


def test_live_unread_engine_date_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round unread: n=0, no asks, named reason. Not the oracle file clock."""
    db = _oracle_db(tmp_path)
    asks = _install_ask(
        monkeypatch, tmp_path, health=_health_unread(), mutate=lambda *_a: None
    )
    code = live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert asks == []
    assert code != 0
    assert report["reason"] == "engine_date_unread"
    assert report["round_label"] == "INVALID"
    assert report["passed"] is False
    assert report["n"] == 0
    assert report["total"] == 0
    assert report["cases"] == []
    assert report["oracle_as_of"] is None


def test_live_one_midnight_case_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _oracle_db(tmp_path)

    def mutate(idx: int, _case: dict[str, Any], env: dict[str, Any]) -> None:
        if idx == 0:
            env["engine_as_of_after"] = "2024-06-16"

    asks = _install_ask(
        monkeypatch, tmp_path, health=_health_open(), mutate=mutate
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert len(asks) == 52
    assert report["n"] == 52
    assert report["invalid"] == 1
    assert report["n_without_invalid"] == 51
    assert report["passed"] is False
    _assert_one_invalid_rest_judged(report["cases"], db)


def test_live_timezone_mismatch_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _oracle_db(tmp_path)

    def mutate(idx: int, _case: dict[str, Any], env: dict[str, Any]) -> None:
        if idx == 3:
            env["engine_timezone_after"] = "Asia/Kuala_Lumpur"

    _install_ask(monkeypatch, tmp_path, health=_health_open(), mutate=mutate)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    invalid = [row for row in report["cases"] if row["verdict"] == "INVALID"]
    assert report["n"] == 52
    assert report["invalid"] == 1
    assert len(invalid) == 1
    assert invalid[0]["reason"] == "engine_timezone_mismatch"
    assert report["n_without_invalid"] == 51
    assert report["passed"] is False


def test_live_valid_engine_date_scores_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must stay unchanged: a valid engine date scores through live()."""
    db = _oracle_db(tmp_path)
    asks = _install_ask(
        monkeypatch, tmp_path, health=_health_open(), mutate=lambda *_a: None
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    questions = _questions()
    oracles = load_oracles()
    assert len(asks) == 52
    assert report["n"] == 52
    assert report["n_without_invalid"] == 52
    assert report["invalid"] == 0
    assert report["round_label"] is None
    assert len(report["cases"]) == 52
    for case, row in zip(questions, report["cases"], strict=True):
        env = _envelope()
        expected = judge_envelope_detailed(
            case, env, oracle_db=db, oracles=oracles, as_of=_ENGINE_DAY
        )
        assert row["verdict"] == expected.verdict
        assert row["verdict"] != "INVALID"
        assert row["oracle_as_of"] == _ENGINE_DAY


def test_live_oracle_and_scorer_still_bind_as_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must stay unchanged: oracle/scorer bind $as_of to the engine date."""
    import score_curated

    db = _oracle_db(tmp_path)
    seen: list[tuple[str, Any]] = []
    real = score_curated.run_oracle_select

    def spy(db_path: Any, sql: str, params: Any = None) -> Any:
        seen.append((sql, params))
        return real(db_path, sql, params)

    monkeypatch.setattr(score_curated, "run_oracle_select", spy)
    _install_ask(
        monkeypatch, tmp_path, health=_health_open(), mutate=lambda *_a: None
    )
    live("http://score.test", 1.0, db)
    bound = [params for sql, params in seen if "$as_of" in sql]
    assert bound, "scorer did not run an oracle that binds $as_of"
    assert all(
        isinstance(params, dict) and str(params.get("as_of")) == _ENGINE_DAY
        for params in bound
    )


def test_ab_offline_counts_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must stay unchanged: 52-pack exact and generative quints, INVALID 0."""
    from score_curated import ab_offline

    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    ab_offline()
    report = json.loads((tmp_path / "ab_gen01.json").read_text(encoding="utf-8"))
    exact = report["exact_match"]
    gen = report["generative"]

    def quint(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
        # ok / layer / abstain / wrong / oracle_error
        return (
            int(row["ok"]),
            int(row["layer"]),
            int(row["abstain"]),
            int(row["wrong"]),
            int(row["oracle_error"]),
        )

    assert quint(exact) == (0, 16, 36, 0, 0)
    assert quint(gen) == (0, 26, 11, 15, 0)
    assert exact["invalid"] == 0
    assert gen["invalid"] == 0
    assert exact["n"] == 52
    assert gen["n"] == 52
    assert exact["n_without_invalid"] == exact["n"]
    assert gen["n_without_invalid"] == gen["n"]


def _assert_one_invalid_rest_judged(
    cases: list[dict[str, Any]], db: Path
) -> None:
    """One engine-date mismatch is INVALID. Every other case is the judge's verdict."""
    questions = {str(q["id"]): q for q in _questions()}
    oracles = load_oracles()
    invalid = [row for row in cases if row["verdict"] == "INVALID"]
    assert len(invalid) == 1
    assert invalid[0]["reason"] == "engine_date_mismatch"
    assert invalid[0]["verdict"] != "WRONG"
    rest = [row for row in cases if row["verdict"] != "INVALID"]
    assert len(rest) == len(cases) - 1
    for row in rest:
        expected = judge_envelope_detailed(
            questions[str(row["id"])],
            _envelope(),
            oracle_db=db,
            oracles=oracles,
            as_of=_ENGINE_DAY,
        )
        assert row["verdict"] == expected.verdict


def _patch_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "score_curated.probe_climb_host", lambda *_a, **_k: ("ok", "up")
    )
    monkeypatch.setattr(
        "score_curated.probe_climb_health",
        lambda *_a, **_k: (
            "ok",
            "up",
            {"product": "dms", "ask_mode": "live", "demo_fallback": False},
        ),
    )


def test_climb_passes_envelope_engine_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from score_curated import climb

    db = _oracle_db(tmp_path)
    asks = _install_ask(
        monkeypatch, tmp_path, health=_health_open(), mutate=lambda *_a: None
    )
    _patch_probe(monkeypatch)
    climb("http://score.test", 1.0, db)
    body = json.loads((tmp_path / "score_climb_cases.json").read_text(encoding="utf-8"))
    assert len(asks) == 52
    assert body
    assert all(row["oracle_as_of"] == _ENGINE_DAY for row in body)
    report = json.loads((tmp_path / "score_climb.json").read_text(encoding="utf-8"))
    assert report["oracle_as_of"] == _ROUND_DAY
    assert report.get("reason") is None
    assert report["invalid"] == 0
    assert report["n_without_invalid"] == report["measured"]["n"]


def test_climb_one_midnight_case_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from score_curated import climb

    db = _oracle_db(tmp_path)

    def mutate(idx: int, _case: dict[str, Any], env: dict[str, Any]) -> None:
        if idx == 0:
            env["engine_as_of_after"] = "2024-06-16"

    _install_ask(monkeypatch, tmp_path, health=_health_open(), mutate=mutate)
    _patch_probe(monkeypatch)
    climb("http://score.test", 1.0, db)
    cases = json.loads((tmp_path / "score_climb_cases.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "score_climb.json").read_text(encoding="utf-8"))
    assert report["invalid"] == 1
    assert report["n_without_invalid"] == report["measured"]["n"] - 1
    _assert_one_invalid_rest_judged(cases, db)


def test_climb_ab_live_passes_envelope_engine_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from score_curated import climb_ab_live

    db = _oracle_db(tmp_path)
    asks = _install_ask(
        monkeypatch, tmp_path, health=_health_open(), mutate=lambda *_a: None
    )
    _patch_probe(monkeypatch)
    climb_ab_live("http://score.test", 1.0, db)
    blob = json.loads(
        (tmp_path / "score_climb_ab_cases.json").read_text(encoding="utf-8")
    )
    assert len(asks) == 104
    for lane in ("exact", "generative"):
        assert blob[lane]
        assert all(row["oracle_as_of"] == _ENGINE_DAY for row in blob[lane])
    report = json.loads((tmp_path / "score_climb_ab.json").read_text(encoding="utf-8"))
    assert report["exact_match"]["invalid"] == 0
    assert report["generative"]["invalid"] == 0
    assert report["exact_match"]["n_without_invalid"] == report["exact_match"]["n"]
    assert report["reason"] is None


def test_climb_ab_live_one_midnight_case_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact lane. climb_ab_live calls score_live_entry for exact first."""
    from score_curated import climb_ab_live

    db = _oracle_db(tmp_path)

    def mutate(idx: int, _case: dict[str, Any], env: dict[str, Any]) -> None:
        if idx == 0:
            env["engine_as_of_after"] = "2024-06-16"

    _install_ask(monkeypatch, tmp_path, health=_health_open(), mutate=mutate)
    _patch_probe(monkeypatch)
    climb_ab_live("http://score.test", 1.0, db)
    blob = json.loads(
        (tmp_path / "score_climb_ab_cases.json").read_text(encoding="utf-8")
    )
    report = json.loads((tmp_path / "score_climb_ab.json").read_text(encoding="utf-8"))
    assert report["invalid"] == 1
    assert report["exact_match"]["invalid"] == 1
    assert report["exact_match"]["n"] == 52
    assert report["exact_match"]["n_without_invalid"] == 51
    assert report["generative"]["invalid"] == 0
    assert report["generative"]["n_without_invalid"] == report["generative"]["n"]
    _assert_one_invalid_rest_judged(blob["exact"], db)


def test_climb_ab_live_generative_lane_one_midnight_case_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generative lane. Second score_live_entry call inside climb_ab_live."""
    from score_curated import climb_ab_live

    db = _oracle_db(tmp_path)
    n_pack = len(_questions())

    def mutate(idx: int, _case: dict[str, Any], env: dict[str, Any]) -> None:
        if idx == n_pack:
            env["engine_as_of_after"] = "2024-06-16"

    _install_ask(monkeypatch, tmp_path, health=_health_open(), mutate=mutate)
    _patch_probe(monkeypatch)
    climb_ab_live("http://score.test", 1.0, db)
    blob = json.loads(
        (tmp_path / "score_climb_ab_cases.json").read_text(encoding="utf-8")
    )
    report = json.loads((tmp_path / "score_climb_ab.json").read_text(encoding="utf-8"))
    assert report["invalid"] == 1
    assert report["generative"]["invalid"] == 1
    assert report["generative"]["n"] == 52
    assert report["generative"]["n_without_invalid"] == 51
    assert report["exact_match"]["invalid"] == 0
    assert report["exact_match"]["n_without_invalid"] == report["exact_match"]["n"]
    _assert_one_invalid_rest_judged(blob["generative"], db)


def test_prove_path_live_passes_envelope_engine_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from score_curated import prove_path_live

    db = _oracle_db(tmp_path)
    asks = _install_ask(
        monkeypatch, tmp_path, health=_health_open(), mutate=lambda *_a: None
    )
    _patch_probe(monkeypatch)
    prove_path_live("http://score.test", 1.0, db)
    cases = json.loads(
        (tmp_path / "score_gen_path_prove_cases.json").read_text(encoding="utf-8")
    )
    assert len(asks) == 104
    assert cases
    assert all(row["oracle_as_of"] == _ENGINE_DAY for row in cases)
    report = json.loads(
        (tmp_path / "score_gen_path_prove.json").read_text(encoding="utf-8")
    )
    assert report["oracle_as_of"] == _ROUND_DAY
    assert report["invalid"] == 0
    assert report["n_without_invalid"] == report["n"]


def test_prove_path_live_one_midnight_case_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generative lane. prove_path_live calls score_live_entry for generative first."""
    from score_curated import prove_path_live

    db = _oracle_db(tmp_path)

    def mutate(idx: int, _case: dict[str, Any], env: dict[str, Any]) -> None:
        if idx == 0:
            env["engine_as_of_after"] = "2024-06-16"

    _install_ask(monkeypatch, tmp_path, health=_health_open(), mutate=mutate)
    _patch_probe(monkeypatch)
    prove_path_live("http://score.test", 1.0, db)
    cases = json.loads(
        (tmp_path / "score_gen_path_prove_cases.json").read_text(encoding="utf-8")
    )
    report = json.loads(
        (tmp_path / "score_gen_path_prove.json").read_text(encoding="utf-8")
    )
    assert report["invalid"] == 1
    assert report["n"] == 52
    assert report["n_without_invalid"] == 51
    _assert_one_invalid_rest_judged(cases, db)


def test_prove_path_live_exact_lane_one_midnight_case_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact lane. Second score_live_entry call inside prove_path_live."""
    from score_curated import prove_path_live

    db = _oracle_db(tmp_path)
    n_pack = len(_questions())

    def mutate(idx: int, _case: dict[str, Any], env: dict[str, Any]) -> None:
        if idx == n_pack:
            env["engine_as_of_after"] = "2024-06-16"

    _install_ask(monkeypatch, tmp_path, health=_health_open(), mutate=mutate)
    _patch_probe(monkeypatch)
    prove_path_live("http://score.test", 1.0, db)
    cases = json.loads(
        (tmp_path / "score_gen_path_prove_exact_cases.json").read_text(encoding="utf-8")
    )
    gen_cases = json.loads(
        (tmp_path / "score_gen_path_prove_cases.json").read_text(encoding="utf-8")
    )
    report = json.loads(
        (tmp_path / "score_gen_path_prove.json").read_text(encoding="utf-8")
    )
    assert report["invalid"] == 0
    assert report["exact_invalid"] == 1
    assert report["exact_n"] == 52
    assert report["exact_n_without_invalid"] == 51
    assert all(row["verdict"] != "INVALID" for row in gen_cases)
    _assert_one_invalid_rest_judged(cases, db)


def test_grid_hook_counts_one_invalid_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from score_curated import grid_score_hook

    db = _oracle_db(tmp_path)

    def mutate(idx: int, _case: dict[str, Any], env: dict[str, Any]) -> None:
        if idx == 1:
            env["engine_as_of_after"] = "2024-06-16"

    asks = _install_ask(
        monkeypatch, tmp_path, health=_health_open(), mutate=mutate
    )
    row = grid_score_hook("http://score.test", 1.0, db)
    assert len(asks) == 52
    assert row["issue"] == 299
    assert row["n"] == 52
    assert row["invalid"] == 1
    assert row["n_without_invalid"] == 51
    assert row["passed"] is False
    _assert_one_invalid_rest_judged(row["cases"], db)


def test_grid_hook_unread_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from score_curated import grid_score_hook

    db = _oracle_db(tmp_path)
    asks = _install_ask(
        monkeypatch, tmp_path, health=_health_unread(), mutate=lambda *_a: None
    )
    row = grid_score_hook("http://score.test", 1.0, db)
    assert asks == []
    assert row["n"] == 0
    assert row["invalid"] == 0
    assert row["n_without_invalid"] == 0
    assert row["reason"] == "engine_date_unread"
    assert row["round_label"] == "INVALID"
    assert row["passed"] is False
    assert row["cases"] == []


def test_answer_connection_stamps_before_and_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.demo_ask import answer_demo_question
    from dms_executor.demo_warehouse import clear_engine_clock

    clear_engine_clock()
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "stamp.duckdb"))
    calls: list[tuple[int, str]] = []
    real = duckdb.DuckDBPyConnection.execute

    def wrapped(self: Any, sql: str, *args: Any, **kwargs: Any) -> Any:
        calls.append((id(self), " ".join(str(sql).split())))
        return real(self, sql, *args, **kwargs)

    monkeypatch.setattr(duckdb.DuckDBPyConnection, "execute", wrapped)
    env = answer_demo_question("What was total revenue?")
    assert env["engine_as_of"] == env["engine_as_of_after"]
    assert env["engine_timezone"]
    assert env["engine_timezone_after"] == env["engine_timezone"]
    rev = [i for i, (_cid, sql) in enumerate(calls) if "revenue_myr" in sql]
    assert rev
    i = rev[-1]
    cid = calls[i][0]
    assert calls[i - 1][0] == cid
    assert "CURRENT_DATE" in calls[i - 1][1]
    assert calls[i + 1][0] == cid
    assert "CURRENT_DATE" in calls[i + 1][1]


def test_generated_sql_reserved_as_of_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cortex_contract.execution import Manifest as M
    from cortex_contract.execution import QueryResult
    from dms_executor import Executor
    from dms_executor.envelope import RESERVED_PARAM_AS_OF, assert_envelope_valid
    from dms_executor.manifest import ManifestMinter, SessionAcl

    class _Insights:
        def __init__(self) -> None:
            self.submits: list[Any] = []
            self.computes: list[str] = []

        def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
            self.computes.append(question)
            return {
                "query_sql": "SELECT $as_of AS day",
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
    env = exe.live_ask(
        "What is total stock value by category?",
        session_id="ses_engine_date",
        ask_path="generative",
    )
    assert_envelope_valid(env)
    assert env["abstain_reason"] == RESERVED_PARAM_AS_OF
    assert env["text"] == RESERVED_PARAM_AS_OF
    assert RESERVED_PARAM_AS_OF in (env.get("assumptions") or [])
    assert env["rows"] == []
    assert env["values"] == []
    assert env["badge"] == "ABSTAIN"
    assert fake.computes
    assert fake.submits == []


def test_user_sql_reserved_as_of_abstains(tmp_path: Path) -> None:
    from dms_executor import Executor, ReservedParamError
    from dms_executor.envelope import RESERVED_PARAM_AS_OF, assert_envelope_valid

    exe = Executor(warehouse_path=tmp_path / "user.duckdb")
    env = exe.answer_user_sql("SELECT $as_of AS day", session_id="ses_user")
    assert_envelope_valid(env)
    assert env["abstain_reason"] == RESERVED_PARAM_AS_OF
    assert env["text"] == RESERVED_PARAM_AS_OF
    assert env["rows"] == []
    assert env["badge"] == "ABSTAIN"
    with pytest.raises(ReservedParamError):
        exe.execute("SELECT $as_of AS day")


def test_followup_sql_reserved_as_of_abstains(tmp_path: Path) -> None:
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.envelope import RESERVED_PARAM_AS_OF, assert_envelope_valid
    from dms_executor.session_followup import run_followup_sql

    db = ensure_demo_warehouse(tmp_path / "follow.duckdb")
    env = run_followup_sql(
        "SELECT $as_of AS day",
        warehouse=db,
        space_id=None,
        session_id="ses_follow",
        question="average of them",
        why="follow-up sql",
        text="should not run",
    )
    assert_envelope_valid(env)
    assert env["abstain_reason"] == RESERVED_PARAM_AS_OF
    assert env["rows"] == []
    assert env["route"] == "followup"


def _bound_as_of(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """SQL texts that were executed with an as_of bind."""
    seen: list[str] = []
    real = duckdb.DuckDBPyConnection.execute

    def wrapped(self: Any, sql: str, *args: Any, **kwargs: Any) -> Any:
        params = args[0] if args else kwargs.get("parameters")
        if isinstance(params, dict) and "as_of" in params:
            seen.append(str(sql))
        return real(self, sql, *args, **kwargs)

    monkeypatch.setattr(duckdb.DuckDBPyConnection, "execute", wrapped)
    return seen


def test_executor_literal_and_comment_as_of_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor import Executor

    bound = _bound_as_of(monkeypatch)
    exe = Executor(warehouse_path=tmp_path / "lit.duckdb")
    literal = exe.answer_user_sql("SELECT '$as_of' AS x", session_id="ses_lit")
    assert literal["rows"] == [{"x": "$as_of"}]
    assert literal.get("abstain_reason") != "reserved_param:as_of"
    assert "$as_of" in str(literal.get("sql_used"))
    commented = exe.answer_user_sql("SELECT 1 AS n -- $as_of", session_id="ses_c")
    assert commented["rows"] == [{"n": 1}]
    assert commented.get("abstain_reason") != "reserved_param:as_of"
    assert bound == []


def test_followup_literal_and_comment_as_of_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.session_followup import run_followup_sql

    bound = _bound_as_of(monkeypatch)
    db = ensure_demo_warehouse(tmp_path / "follow_lit.duckdb")
    literal = run_followup_sql(
        "SELECT '$as_of' AS x",
        warehouse=db,
        space_id=None,
        session_id="ses_lit",
        question="average of them",
        why="literal",
        text="literal stays",
    )
    assert literal["rows"] == [{"x": "$as_of"}]
    assert literal.get("abstain_reason") != "reserved_param:as_of"
    commented = run_followup_sql(
        "SELECT 1 AS n -- $as_of",
        warehouse=db,
        space_id=None,
        session_id="ses_c",
        question="average of them",
        why="comment",
        text="comment stays",
    )
    assert commented["rows"] == [{"n": 1}]
    assert commented.get("abstain_reason") != "reserved_param:as_of"
    assert bound == []


def test_dollar_string_beside_real_as_of_placeholder(
    tmp_path: Path,
) -> None:
    """A `$` string next to a real $as_of is not the placeholder. The placeholder is."""
    from dms_executor import Executor
    from dms_executor.demo_warehouse import execute_sql
    from dms_executor.envelope import RESERVED_PARAM_AS_OF, assert_envelope_valid

    sql = "SELECT '$' AS buck, CAST($as_of AS VARCHAR) AS day"
    exe = Executor(warehouse_path=tmp_path / "mix.duckdb")
    refused = exe.answer_user_sql(sql, session_id="ses_mix")
    assert_envelope_valid(refused)
    assert refused["abstain_reason"] == RESERVED_PARAM_AS_OF
    assert refused["rows"] == []
    rows = execute_sql(sql, path=tmp_path / "serve.duckdb")
    assert rows[0]["buck"] == "$"
    assert rows[0]["day"] not in {"$", "$as_of"}
    assert str(rows[0]["day"])[:4].isdigit()


def test_serving_execute_sql_ignores_as_of_plain_text(tmp_path: Path) -> None:
    from dms_executor.demo_warehouse import execute_sql

    db = tmp_path / "plain.duckdb"
    assert execute_sql("SELECT '$as_of' AS x", path=db) == [{"x": "$as_of"}]
    assert execute_sql("SELECT 1 AS n -- $as_of", path=db) == [{"n": 1}]
