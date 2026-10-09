"""GEN01-WRONG-TO-LADDER-01.

A generate HTTP 4xx/5xx is an AI-call error. With DMS_GEN01_RULES unset,
that error must not compile the ontology rule builder, and a wrong result
must not carry L2_VALIDATED.

On 57d85c52 the same fake returns a wrong L2_VALIDATED envelope for the
three curated asks below. Oracle as_of is pinned to 2026-10-09 so the
audit 90-day window matches the Cortex Check artifact (oracle_as_of
2026-10-09). Product SQL rejects a $as_of placeholder, so a correct fake
model inlines DATE '2026-10-09'.
"""

from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from oracle_row_match import run_oracle_select  # noqa: E402
from cortex_client.client import CortexClient
from cortex_client.compute import gen01_rules_enabled, ranked_measure_tokens
from cortex_client.models import (
    AskRequest,
    AskResponse,
    LedgerAppendRequest,
    LedgerAppendResponse,
)
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.semantic_retrieve import load_measure_aliases

_FIXTURE = Path(__file__).resolve().parents[0] / "fixtures" / "curated_ceo"
_ORACLE_AS_OF = "2026-10-09"
_NAMED = ("cq_chemicals_list", "cq_audit_overdue", "ops_chemicals_list")
_KEY = "ov_test_gen01_ladder"


def _pack() -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    questions = yaml.safe_load((_FIXTURE / "questions.yaml").read_text(encoding="utf-8"))
    oracles = yaml.safe_load((_FIXTURE / "oracles.yaml").read_text(encoding="utf-8"))
    spaces = {str(k): str(v) for k, v in (questions.get("spaces") or {}).items()}
    rows = [row for row in questions["questions"] if isinstance(row, dict)]
    specs = oracles if isinstance(oracles, dict) else {}
    return spaces, rows, specs


def _case(case_id: str) -> dict[str, Any]:
    spaces, rows, specs = _pack()
    row = next(item for item in rows if str(item.get("id")) == case_id)
    spec = specs.get(case_id) if isinstance(specs.get(case_id), dict) else {}
    return {
        "id": case_id,
        "question": str(row["question"]),
        "space_id": spaces[str(row["space"])],
        "sql": str(spec.get("sql") or ""),
    }


def _model_sql(sql: str) -> str:
    """Inline the pinned date. A real $as_of placeholder is not executed."""
    return sql.replace("$as_of", f"DATE '{_ORACLE_AS_OF}'")


def _cell(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        num = Decimal(str(value))
    else:
        text = "" if value is None else str(value).strip()
        if not text:
            return ""
        try:
            num = Decimal(text)
        except (InvalidOperation, ValueError):
            return text.casefold()
    if num == num.to_integral_value():
        return str(int(num))
    return format(num.normalize(), "f")


def _flat(rows: Any) -> list[str]:
    cells: list[str] = []
    if not isinstance(rows, list):
        return cells
    for row in rows:
        if isinstance(row, dict):
            vals: list[Any] = list(row.values())
        elif isinstance(row, (list, tuple)):
            vals = list(row)
        else:
            vals = [row]
        cells.extend(_cell(v) for v in vals)
    cells.sort()
    return cells


def _gold(db: Path, sql: str) -> list[dict[str, Any]]:
    rows, err = run_oracle_select(db, sql, params={"as_of": _ORACLE_AS_OF})
    assert err is None, err
    assert rows is not None
    return rows


def _wrong(env: dict[str, Any], gold: list[dict[str, Any]] | None) -> bool:
    """Served rows that are not the gold values. An abstain is not a wrong."""
    if env.get("abstained") is True:
        return False
    if gold is None:
        return True
    return _flat(env.get("rows")) != _flat(gold)


def _rank(question: str) -> list[dict[str, Any]]:
    """Catalog order by token overlap with the ask. Not a phrase list."""
    qtoks = ranked_measure_tokens(question)
    ids = list(load_measure_aliases())

    def _key(metric_id: str) -> tuple[int, int]:
        overlap = len(ranked_measure_tokens(metric_id) & qtoks)
        return (-overlap, ids.index(metric_id))

    ordered = sorted(ids, key=_key)
    return [{"id": metric_id, "importance": {"rank": i + 1}} for i, metric_id in enumerate(ordered)]


class _Box:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.mode = "error"
        self.status = 422
        self.sql_for: dict[str, str] = {}


class _Resp:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {}
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def _client_cls(box: _Box) -> type:
    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def post(
            self, url: str, json: dict[str, Any], headers: dict[str, str] | None = None
        ) -> _Resp:
            box.posts.append({"url": url, "json": json, "headers": headers or {}})
            if box.mode == "timeout":
                raise httpx.TimeoutException("generate timed out")
            generate_posts = [row for row in box.posts if str(row["url"]).endswith("/v1/insights")]
            if box.mode == "error" or (box.mode == "retry" and len(generate_posts) == 1):
                return _Resp(
                    box.status,
                    {"detail": [{"type": "value_error", "msg": "validation"}]},
                )
            asked = str(json.get("question") or "").split("\n", 1)[0].strip()
            sql = box.sql_for[asked]
            return _Resp(
                200,
                {
                    "ok": True,
                    "phase": "generate",
                    "query_sql": sql,
                    "values": [],
                    "generative": {"ok": True, "sql": sql, "stamp": {"impl": "fake"}},
                },
            )

        def get(
            self,
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> _Resp:
            del url, headers
            question = str((params or {}).get("q") or "")
            return _Resp(
                200,
                {
                    "ok": True,
                    "phase": "ontology",
                    "ontology": {"ok": True, "metrics": _rank(question)},
                },
            )

    return _Client


class _Cortex:
    def __init__(self, db: Path, inner: CortexClient) -> None:
        self._db = db
        self._inner = inner

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        return self._inner.compute_insights(question, **kwargs)

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_gen01_bind")
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        con = connect_file(self._db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_gen01", output={"rows": rows})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_gen01", hash="hash_gen01")

    def ask(self, req: AskRequest) -> AskResponse:
        del req
        return AskResponse(
            answer="I cannot certify an answer for that question.",
            badge="ABSTAIN",
            abstained=True,
            rows=[],
            sql_used=None,
            audit_id="aud_gen01_miss",
            route="abstain",
        )


def _executor(db: Path, *, api_key: str | None) -> Executor:
    inner = CortexClient("http://127.0.0.1:8010", api_key=api_key, timeout=8.0)
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
    return Executor(cortex=_Cortex(db, inner), minter=minter, warehouse_path=db)  # type: ignore[arg-type]


def _flags_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_GEN01_RULES",
        "DMS_LANE_BRONZE_SHEET",
        "DMS_SCHEMA_CONTEXT",
        "DMS_CCA_CASCADE",
        "DMS_HARNESS_ASK_PATHS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")


def _ask(exe: Executor, row: dict[str, Any]) -> dict[str, Any]:
    env = exe.live_ask(
        str(row["question"]),
        space_id=str(row["space_id"]),
        session_id=f"ses_{row['id']}",
    )
    assert isinstance(env, dict)
    return env


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "gen01.duckdb"
    ensure_demo_warehouse(path)
    return path


def test_gen01_rules_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_GEN01_RULES", raising=False)
    assert gen01_rules_enabled() is False


def test_audit_gold_uses_pinned_as_of(db: Path) -> None:
    """90 days before 2026-10-09 keeps SUP-01 and SUP-03, not the August audit."""
    case = _case("cq_audit_overdue")
    gold = _flat(_gold(db, case["sql"]))
    assert "sup-01" in gold
    assert "sup-03" in gold
    assert "sup-02" not in gold
    assert "sup-04" not in gold


@pytest.mark.parametrize("status", [422, 500])
def test_ai_error_does_not_stamp_l2_on_a_wrong(
    db: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Key present. Generate 4xx/5xx plus a ranking GET. No wrong L2."""
    _flags_off(monkeypatch)
    box = _Box()
    box.mode = "error"
    box.status = status
    monkeypatch.setattr("cortex_client.compute.httpx.Client", _client_cls(box))
    exe = _executor(db, api_key=_KEY)
    for case_id in _NAMED:
        row = _case(case_id)
        before = len(box.posts)
        env = _ask(exe, row)
        posts = box.posts[before:]
        assert posts, case_id
        assert posts[0]["headers"]["Authorization"] == f"Bearer {_KEY}"
        assert str(posts[0]["url"]).endswith("/v1/insights")
        assert any("/ontology" in str(item["url"]) for item in posts[1:]) or any(
            "/ontology" in str(item["url"]) for item in posts
        )
        gold = _gold(db, row["sql"])
        assert _wrong(env, gold) is False, case_id
        if _flat(env.get("rows")) != _flat(gold):
            assert env.get("badge") != "L2_VALIDATED", case_id
        assert env.get("badge") != "L2_VALIDATED", case_id
        assert env.get("abstained") is True, case_id


def test_empty_key_does_not_call_generate(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags_off(monkeypatch)
    box = _Box()
    monkeypatch.setattr("cortex_client.compute.httpx.Client", _client_cls(box))
    exe = _executor(db, api_key="")
    env = _ask(exe, _case("cq_chemicals_list"))
    assert box.posts == []
    assert env.get("abstained") is True
    assert env.get("badge") != "L2_VALIDATED"


def test_timeout_does_not_stamp_l2(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flags_off(monkeypatch)
    box = _Box()
    box.mode = "timeout"
    monkeypatch.setattr("cortex_client.compute.httpx.Client", _client_cls(box))
    exe = _executor(db, api_key=_KEY)
    env = _ask(exe, _case("cq_audit_overdue"))
    assert env.get("abstained") is True
    assert env.get("badge") != "L2_VALIDATED"


def test_flag_on_keeps_ranking_for_the_retirement_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DMS_GEN01_RULES=1 leaves the ranking on the payload. Default is off."""
    from cortex_client.compute import compute_insights

    _flags_off(monkeypatch)
    monkeypatch.setenv("DMS_GEN01_RULES", "1")
    assert gen01_rules_enabled() is True
    box = _Box()
    box.mode = "error"
    box.status = 422
    monkeypatch.setattr("cortex_client.compute.httpx.Client", _client_cls(box))
    question = _case("cq_chemicals_list")["question"]
    out = compute_insights(
        "http://127.0.0.1:8010",
        question=question,
        api_key=_KEY,
    )
    assert out is not None
    assert out.get("insights_fail") == "insights_ai_error"
    metrics = (out.get("ontology") or {}).get("metrics") or []
    assert metrics


def test_correct_model_sql_serves_via_the_ladder(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags_off(monkeypatch)
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    box = _Box()
    box.mode = "sql"
    box.sql_for = {row["question"]: _model_sql(row["sql"]) for row in (_case(i) for i in _NAMED)}
    monkeypatch.setattr("cortex_client.compute.httpx.Client", _client_cls(box))
    exe = _executor(db, api_key=_KEY)
    for case_id in _NAMED:
        row = _case(case_id)
        before = len(box.posts)
        env = _ask(exe, row)
        body = box.posts[before]["json"]
        onto = body.get("ontology")
        assert isinstance(onto, dict) and onto, case_id
        gold = _gold(db, row["sql"])
        assert _flat(env.get("rows")) == _flat(gold), case_id
        assert env.get("badge") == "L2_VALIDATED", case_id
        assert env.get("plan_origin") == "generate_sql", case_id
        loop = env.get("loop") or []
        assert any(isinstance(step, dict) and step.get("outcome") == "served" for step in loop)


def test_ai_error_retries_once_then_serves_correct_sql(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flags_off(monkeypatch)
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    box = _Box()
    box.mode = "retry"
    box.status = 422
    box.sql_for = {row["question"]: _model_sql(row["sql"]) for row in (_case(i) for i in _NAMED)}
    monkeypatch.setattr("cortex_client.compute.httpx.Client", _client_cls(box))
    exe = _executor(db, api_key=_KEY)
    row = _case("cq_chemicals_list")
    env = _ask(exe, row)
    generate = [item for item in box.posts if str(item["url"]).endswith("/v1/insights")]
    assert len(generate) == 2
    feedback = generate[1]["json"].get("sql_feedback") or {}
    assert feedback.get("reason") == "insights_ai_error"
    assert isinstance(generate[1]["json"].get("ontology"), dict)
    gold = _gold(db, row["sql"])
    assert _flat(env.get("rows")) == _flat(gold)
    assert env.get("badge") == "L2_VALIDATED"
    assert env.get("plan_origin") == "generate_sql"


def test_curated_52_ai_error_has_no_wrong_l2(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flags off, generate 422. No served wrong, and no wrong carries L2."""
    _flags_off(monkeypatch)
    box = _Box()
    box.mode = "error"
    box.status = 422
    monkeypatch.setattr("cortex_client.compute.httpx.Client", _client_cls(box))
    exe = _executor(db, api_key=_KEY)
    spaces, rows, specs = _pack()
    counts = {"correct": 0, "wrong": 0, "abstain": 0}
    named_bad: list[str] = []
    for row in rows:
        case_id = str(row["id"])
        spec = specs.get(case_id) if isinstance(specs.get(case_id), dict) else {}
        asked = {
            "id": case_id,
            "question": str(row["question"]),
            "space_id": spaces[str(row["space"])],
        }
        env = _ask(exe, asked)
        sql = str(spec.get("sql") or "")
        gold: list[dict[str, Any]] | None
        if sql and str(spec.get("expect") or "") != "refuse":
            got, err = run_oracle_select(db, sql, params={"as_of": _ORACLE_AS_OF})
            gold = got if err is None else None
        else:
            gold = None
        if env.get("abstained") is True:
            counts["abstain"] += 1
        elif gold is not None and _flat(env.get("rows")) == _flat(gold):
            counts["correct"] += 1
        else:
            counts["wrong"] += 1
            if env.get("badge") == "L2_VALIDATED":
                named_bad.append(case_id)
        if case_id in _NAMED:
            assert env.get("badge") != "L2_VALIDATED", case_id
            assert env.get("abstained") is True, case_id
    assert counts["wrong"] == 0, counts
    assert named_bad == []
    assert counts == {"correct": 23, "wrong": 0, "abstain": 29}
