"""GOLD-LEAK-01: answer serving does not read scoring files.

Planted scoring SQL must not change the seven served envelopes. A static
scan forbids a runtime reference. With the scoring tree unreadable, the
same seven envelopes come back, and each id is reported.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import QueryResult
from dms_executor.demo_warehouse import ensure_demo_warehouse, execute_sql
from dms_executor.envelope import assert_envelope_valid
from test_boot_crash_386 import _cases, _client, _flags_off, _mask

ROOT = Path(__file__).resolve().parents[1]
_PACK = ROOT / "tests" / "fixtures" / "curated_ceo"
_ORACLES = _PACK / "oracles.yaml"
_LEAK_IDS = (
    "cq_sku_count",
    "cq_sales_top3_volume",
    "cq_sku_count_by_category",
    "cq_supplier_ranking",
    "trap_categoty",
    "ops_sku_count",
    "ops_sku_count_by_category",
)
_RUNTIME = (ROOT / "apps", ROOT / "packages")
_SKIP = {".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", ".git", "archive"}


class _Exec:
    """Submit runs the SQL on the seeded demo warehouse. Ask does not invent rows."""

    def __init__(self, db: Path) -> None:
        self.db = db

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        return {"unsure": True}

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind != "sql":
            return QueryResult(ok=True, status="bound", run_id="run_bind")
        body = getattr(req, "body", None)
        sql = body.get("sql") if isinstance(body, dict) else None
        if not isinstance(sql, str) or not sql.strip():
            return QueryResult(ok=False, status="err", run_id="run_err")
        try:
            rows = execute_sql(sql, path=self.db, product=True)
        except Exception:  # noqa: BLE001 - a bad submit is not an answer
            return QueryResult(ok=False, status="err", run_id="run_err")
        return QueryResult(ok=True, status="ok", run_id="run_sql", output={"rows": rows})

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        _ = req
        return LedgerAppendResponse(entry_id="led_gold", hash="hash_gold_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        _ = req
        return AskResponse(
            answer="No certified rows.",
            badge="abstain",
            sql_used=None,
            rows=[],
            assumptions="no scoring file",
            audit_id="aud_gold",
            route="abstain",
        )


def _questions() -> list[dict[str, str]]:
    rows = _cases()
    assert len(rows) == 52
    return rows


def _serve(
    db: Path,
    rows: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    _flags_off(monkeypatch)
    ensure_demo_warehouse(db)
    client, exe = _client(_Exec(db), db)
    out: dict[str, Any] = {}
    try:
        for row in rows:
            res = client.post(
                "/v1/chat/ask",
                json={
                    "question": row["question"],
                    "space_id": row["space_id"],
                    "session_id": f"ses_{row['id']}",
                },
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert isinstance(body, dict)
            assert_envelope_valid(body)
            out[row["id"]] = _mask(body)
    finally:
        exe.close()
    return out


def _plant(text: str) -> str:
    doc = yaml.safe_load(text) or {}
    oracles = doc.get("oracles") or {}
    for qid in _LEAK_IDS:
        assert qid in oracles, qid
        oracles[qid]["sql"] = "SELECT 0 AS planted_gold"
    return yaml.safe_dump(doc)


def _rows_key(rows: list[dict[str, Any]]) -> list[tuple[tuple[str, str], ...]]:
    keyed = [tuple(sorted((str(k), str(v)) for k, v in row.items())) for row in rows]
    return sorted(keyed)


def _report(envelopes: dict[str, Any], db: Path) -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(_ORACLES.read_text(encoding="utf-8")) or {}
    oracles = doc.get("oracles") or {}
    out: dict[str, dict[str, Any]] = {}
    for qid in _LEAK_IDS:
        env = envelopes[qid]
        sql = str((oracles.get(qid) or {}).get("sql") or "")
        oracle_rows = execute_sql(" ".join(sql.split()), path=db)
        served = list(env.get("rows") or [])
        correct = (not env.get("abstained")) and _rows_key(served) == _rows_key(oracle_rows)
        out[qid] = {
            "badge": env.get("badge"),
            "abstained": bool(env.get("abstained")),
            "served_rows": len(served),
            "oracle_rows": len(oracle_rows),
            "correct": correct,
        }
    return out


def test_planted_gold_does_not_change_the_seven_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [row for row in _questions() if row["id"] in _LEAK_IDS]
    assert len(rows) == 7
    before = _serve(tmp_path / "before.duckdb", rows, monkeypatch)
    planted = tmp_path / "oracles.yaml"
    planted.write_text(_plant(_ORACLES.read_text(encoding="utf-8")), encoding="utf-8")
    original = _ORACLES.read_text(encoding="utf-8")
    import dms_executor.demo_pack as demo_pack

    if hasattr(demo_pack, "_score_fixture_dir"):
        monkeypatch.setattr(demo_pack, "_score_fixture_dir", lambda: planted.parent)
    try:
        _ORACLES.write_text(planted.read_text(encoding="utf-8"), encoding="utf-8")
        demo_pack.score_pack_exact_metrics.cache_clear()
        demo_pack.curated_l0_question_norms.cache_clear()
        after = _serve(tmp_path / "after.duckdb", rows, monkeypatch)
    finally:
        _ORACLES.write_text(original, encoding="utf-8")
        demo_pack.score_pack_exact_metrics.cache_clear()
        demo_pack.curated_l0_question_norms.cache_clear()
    for qid in _LEAK_IDS:
        assert after[qid] == before[qid], qid
        assert "planted_gold" not in str(after[qid])


def _under_tests(path: Path) -> bool:
    return "tests" in path.parts


def test_seven_ids_when_scoring_tree_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _questions()
    readable = _serve(tmp_path / "readable.duckdb", rows, monkeypatch)
    report = _report(readable, tmp_path / "oracle.duckdb")
    print(json.dumps(report, sort_keys=True))
    assert list(report) == list(_LEAK_IDS)
    for qid, item in report.items():
        assert set(item) == {"badge", "abstained", "served_rows", "oracle_rows", "correct"}, qid
    real_read = Path.read_text
    real_is_file = Path.is_file
    real_stat = Path.stat

    def _read(self: Path, *args: Any, **kwargs: Any) -> str:
        if _under_tests(self):
            raise PermissionError("unreadable")
        return real_read(self, *args, **kwargs)

    def _is_file(self: Path, *args: Any, **kwargs: Any) -> bool:
        if _under_tests(self):
            raise PermissionError("unreadable")
        return real_is_file(self, *args, **kwargs)

    def _stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if _under_tests(self):
            raise PermissionError("unreadable")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read)
    monkeypatch.setattr(Path, "is_file", _is_file)
    monkeypatch.setattr(Path, "stat", _stat)
    blocked = _serve(tmp_path / "blocked.duckdb", rows, monkeypatch)
    for qid in _LEAK_IDS:
        for side in (readable, blocked):
            assert side[qid]["badge"] != "L1_GOVERNED_METRIC", qid
            assert not side[qid].get("rows"), qid
            assert "oracles.yaml" not in str(side[qid])


def _path_opens_tests(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        val = node.value
        if val == "tests" or val.startswith("tests/") or "/tests/" in val:
            return True
    return False


_LADDER_SQL = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


class _Ladder:
    """The first SQL submit is the governed metric and is empty.

    Later submits are the ladder. ``fill`` gives those submits rows.
    """

    def __init__(self, db: Path, *, fill: bool) -> None:
        self.db = db
        self.fill = fill
        self.insights: list[str] = []
        self.sql_submits = 0

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.insights.append(question)
        return {
            "phase": "generate",
            "query_sql": _LADDER_SQL,
            "generative": {"sql": _LADDER_SQL, "ok": True, "stamp": {"impl": "stub"}},
        }

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind != "sql":
            return QueryResult(ok=True, status="bound", run_id="run_bind")
        self.sql_submits += 1
        if self.sql_submits == 1 or not self.fill:
            return QueryResult(ok=True, status="ok", run_id="run_empty", output={"rows": []})
        body = getattr(req, "body", None)
        sql = body.get("sql") if isinstance(body, dict) else ""
        if isinstance(sql, str) and sql.strip():
            try:
                rows = execute_sql(sql, path=self.db, product=True)
            except Exception:  # noqa: BLE001 - a bad ladder SQL is not an answer
                rows = []
            if rows:
                return QueryResult(
                    ok=True, status="ok", run_id="run_ladder", output={"rows": rows}
                )
        return QueryResult(
            ok=True,
            status="ok",
            run_id="run_ladder",
            output={"rows": [{"location_code": "WH-A"}]},
        )

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        _ = req
        return LedgerAppendResponse(entry_id="led_ladder", hash="hash_ladder_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        _ = req
        return AskResponse(
            answer="No certified rows.",
            badge="governed_metric",
            sql_used="SELECT 0 AS empty_l1",
            rows=[],
            audit_id="aud_empty_l1",
            route="governed_metric",
        )


def _ladder_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fill: bool) -> dict[str, Any]:
    from dms_executor.demo_pack import COLD_STORAGE_Q

    _flags_off(monkeypatch)
    db = tmp_path / "ladder.duckdb"
    cortex = _Ladder(db, fill=fill)
    client, exe = _client(cortex, db)
    try:
        res = client.post(
            "/v1/chat/ask",
            json={
                "question": COLD_STORAGE_Q,
                "space_id": _FINANCE,
                "session_id": "ses_ladder",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert isinstance(body, dict)
        assert_envelope_valid(body)
        body["_insights"] = len(cortex.insights)
        return body
    finally:
        exe.close()


def test_zero_row_l1_ladder_serves_the_rows_it_finds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    earned = _ladder_ask(tmp_path, monkeypatch, fill=True)
    assert earned["_insights"] >= 1
    assert earned["badge"] == "L2_VALIDATED"
    assert earned["abstained"] is False
    served = list(earned.get("rows") or [])
    assert served
    real = execute_sql(_LADDER_SQL, path=tmp_path / "ladder.duckdb", product=True)
    assert real
    assert _rows_key(served) == _rows_key(real)


_OVERDUE_SQL = (
    "SELECT supplier_id FROM suppliers "
    "WHERE CAST(last_audit_date AS DATE) < CURRENT_DATE - INTERVAL 90 DAY"
)


class _RealSQL:
    """Every SQL submit runs on the warehouse. Nothing is canned empty."""

    def __init__(self, db: Path, ladder_sql: str) -> None:
        self.db = db
        self.ladder_sql = ladder_sql
        self.insights: list[str] = []
        self.sqls: list[str] = []

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.insights.append(question)
        return {
            "phase": "generate",
            "query_sql": self.ladder_sql,
            "generative": {"sql": self.ladder_sql, "ok": True, "stamp": {"impl": "stub"}},
        }

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind != "sql":
            return QueryResult(ok=True, status="bound", run_id="run_bind")
        body = getattr(req, "body", None)
        sql = body.get("sql") if isinstance(body, dict) else ""
        self.sqls.append(sql if isinstance(sql, str) else "")
        rows = execute_sql(sql, path=self.db, product=True) if isinstance(sql, str) else []
        return QueryResult(ok=True, status="ok", run_id="run_real", output={"rows": rows})

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        _ = req
        return LedgerAppendResponse(entry_id="led_real", hash="hash_real_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        raise AssertionError(f"ladder should have served: {req}")


def test_true_empty_nothing_overdue_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both steps execute and match nothing. The empty answer is served."""
    import duckdb
    from dms_executor.demo_pack import COLD_STORAGE_Q, COLD_STORAGE_SQL
    from dms_executor.envelope import PROVENANCE_L1_EMPTY, PROVENANCE_LADDER_EMPTY

    _flags_off(monkeypatch)
    db = tmp_path / "overdue.duckdb"
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        con.execute("UPDATE locations SET is_cold_storage = FALSE")
        con.execute("UPDATE suppliers SET last_audit_date = CURRENT_DATE")
    finally:
        con.close()
    assert execute_sql(COLD_STORAGE_SQL, path=db, product=True) == []
    assert execute_sql(_OVERDUE_SQL, path=db, product=True) == []
    cortex = _RealSQL(db, _OVERDUE_SQL)
    client, exe = _client(cortex, db)
    try:
        res = client.post(
            "/v1/chat/ask",
            json={
                "question": COLD_STORAGE_Q,
                "space_id": _FINANCE,
                "session_id": "ses_overdue_empty",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert isinstance(body, dict)
        assert_envelope_valid(body)
    finally:
        exe.close()
    assert cortex.insights
    assert body["abstained"] is False
    assert body["badge"] != "ABSTAIN"
    assert body["badge"] == "L2_VALIDATED"
    assert body["rows"] == []
    assumptions = list(body.get("assumptions") or [])
    assert PROVENANCE_L1_EMPTY in assumptions
    assert PROVENANCE_LADDER_EMPTY in assumptions
    assert " ".join(str(body.get("sql_used") or "").split()) == " ".join(_OVERDUE_SQL.split())
    assert any(_OVERDUE_SQL in sql for sql in cortex.sqls)


def test_runtime_modules_do_not_reference_scoring_files() -> None:
    hits: list[str] = []
    for root in _RUNTIME:
        for path in root.rglob("*.py"):
            if any(part in _SKIP for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT).as_posix()
            if "oracles.yaml" in text:
                hits.append(rel)
    assert hits == []


def test_load_score_pack_metrics_is_not_on_live_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dms_executor.demo_pack as demo_pack

    def boom(*_a: Any, **_k: Any) -> tuple:
        raise AssertionError("load_score_pack_metrics")

    monkeypatch.setattr(demo_pack, "load_score_pack_metrics", boom)
    rows = _questions()
    served = _serve(tmp_path / "reach.duckdb", rows, monkeypatch)
    assert set(served) == {row["id"] for row in rows}
