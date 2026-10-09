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
        assert blocked[qid] == readable[qid], qid
    for row in rows:
        assert blocked[row["id"]] == readable[row["id"]], row["id"]


def _path_opens_tests(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        val = node.value
        if val == "tests" or val.startswith("tests/") or "/tests/" in val:
            return True
    return False


def test_runtime_modules_do_not_reference_scoring_files() -> None:
    hits: list[str] = []
    for root in _RUNTIME:
        for path in root.rglob("*.py"):
            if any(part in _SKIP for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT).as_posix()
            if "oracles.yaml" in text or "tests/fixtures" in text:
                hits.append(rel)
            tree = ast.parse(text)
            if _path_opens_tests(tree):
                hits.append(rel)
    assert hits == []
