"""FEAT-BENCH-SPACE-01. Benchmarks live beside a Space, never in the prompt.

Flag ``DMS_SPACE_BENCH`` defaults off. Off, the routes are 404 and a chat
ask body matches the flag-on body. On, ground truth is gated, a run tallies
through grade52, and the verified-query retriever does not read the book.
Synthetic questions only. No held-out pack.
"""

from __future__ import annotations

import ast
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_api.routes.space_bench import enabled, wait_bench_run
from dms_api.settings import get_settings
from dms_core.control_plane.bench_store import reset_memory_bench
from dms_executor.envelope import build_answer_envelope
from dms_executor.skills_quarantine import WRITE_BLOCKED, item_content_hash
from dms_executor.space_bench import grade_answer, run_items, tally
from dms_executor.verified_queries import retrieve_verified_examples
from fastapi.testclient import TestClient

FLAG = "DMS_SPACE_BENCH"
SPACE = "space-bench-01"
GOLD = "SELECT qty FROM bench_sku WHERE sku = 'a'"
ROOT = Path(__file__).resolve().parents[1]
_RETRIEVERS = (
    "packages/executor/dms_executor/verified_queries.py",
    "packages/executor/dms_executor/semantic_retrieve.py",
    "packages/executor/dms_executor/generative_ask.py",
    "apps/api/dms_api/routes/chat.py",
)
_BENCH_TABLES = ("space_benchmarks", "space_bench_runs", "space_bench_results", "bench_store")
_ASK_FILES = (
    "apps/api/dms_api/routes/chat.py",
    "packages/executor/dms_executor/generative_ask.py",
    "packages/executor/dms_executor/verified_queries.py",
    "packages/executor/dms_executor/sql_vote.py",
)


def _clear_settings() -> None:
    get_settings.cache_clear()


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv(FLAG, "1")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _clear_settings()
    reset_memory_bench()


def _lake(tmp_path: Path) -> Path:
    path = tmp_path / "bench.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE bench_sku (sku VARCHAR, qty INTEGER)")
    con.execute("INSERT INTO bench_sku VALUES ('a', 1), ('b', 2), ('c', 3)")
    con.close()
    return path


def _grant(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.wiring as wiring

    monkeypatch.setattr(wiring, "bench_grantable", lambda _space: {"bench_sku"})


def _allow(monkeypatch: pytest.MonkeyPatch) -> None:
    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        del actor
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.space_bench.compliance_gate", allow)


def _client(monkeypatch: pytest.MonkeyPatch, *, on: bool) -> TestClient:
    _flag(monkeypatch, on)
    return TestClient(create_app())


def _env(kind: str) -> dict[str, Any]:
    if kind == "correct":
        return build_answer_envelope(
            answer_id="bench-correct",
            text="Quantity listed.",
            badge="L2_VALIDATED",
            values=[{"id": "v0", "value": 1, "label": "qty"}],
            sql_used=GOLD,
            rows=[{"qty": 1}],
            ask_mode="live",
        )
    if kind == "wrong":
        return {
            "badge": "L2_VALIDATED",
            "abstained": False,
            "rows": [{"qty": 9}],
            "sql_used": "SELECT 9",
            "text": "mismatch",
        }
    if kind == "clarify":
        return {
            "badge": "ABSTAIN",
            "abstained": True,
            "rows": [],
            "route": "needs_clarification",
            "clarify": {"blocks": True},
            "text": "",
        }
    if kind == "reconfirm":
        return {
            "badge": "ABSTAIN",
            "abstained": True,
            "rows": [],
            "reconfirm": {"suggested_question": "bench synth reading"},
            "text": "",
        }
    return {"badge": "ABSTAIN", "abstained": True, "rows": [], "route": "abstain", "text": ""}


def _grade52() -> Any:
    import sys

    entry = str(ROOT / "scripts" / "verify")
    if entry not in sys.path:
        sys.path.insert(0, entry)
    import grade52

    return grade52


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    assert enabled() is False
    monkeypatch.setenv(FLAG, "0")
    assert enabled() is False
    monkeypatch.setenv(FLAG, "true")
    assert enabled() is False
    monkeypatch.setenv(FLAG, "1")
    assert enabled() is True


def test_flag_off_routes_are_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, on=False)
    base = f"/v1/spaces/{SPACE}/benchmarks"
    assert client.get(base).status_code == 404
    assert client.post(base, json={"question": "bench synth", "gold_sql": GOLD}).status_code == 404
    assert client.post(f"{base}/runs").status_code == 404
    assert "DMS_SPACE_BENCH" not in client.get(base).text


def test_flag_off_openapi_is_flag_on_minus_bench_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_INSIGHT_PROOF", raising=False)
    _flag(monkeypatch, True)
    on = create_app().openapi()
    _flag(monkeypatch, False)
    off = create_app().openapi()
    new_paths = {path for path in on["paths"] if "/benchmarks" in path}
    assert new_paths
    stripped = copy.deepcopy(on)
    for path in new_paths:
        del stripped["paths"][path]
    schemas = stripped.get("components", {}).get("schemas", {})
    for name in list(schemas):
        if name.startswith("Bench"):
            del schemas[name]
    assert json.dumps(stripped, sort_keys=True) == json.dumps(off, sort_keys=True)


def test_flag_off_ask_bytes_match_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = {
        "answer_id": "ans_bench_flag",
        "text": "no number",
        "badge": "ABSTAIN",
        "abstained": True,
        "assumptions": [],
        "values": [],
        "rows": [],
        "route": "abstain",
    }
    seen: list[bytes] = []

    def client_for(on: bool) -> TestClient:
        _flag(monkeypatch, on)
        monkeypatch.setattr(
            "dms_api.routes.chat.compliance_gate",
            lambda **_k: ComplianceDecision(allowed=True, reason="test_allow", action="chat.ask"),
        )
        app = create_app()
        app.state.cortex = SimpleNamespace(base_url="")
        ask = app.state.ask_service

        def live_ask(question: str, **_k: Any) -> dict[str, Any]:
            seen.append(question.encode("utf-8"))
            return json.loads(json.dumps(envelope))

        ask.live_ask = live_ask  # type: ignore[method-assign]
        ask.offer_reconfirm = lambda env, **_k: env  # type: ignore[method-assign]
        return TestClient(app)

    off_body = client_for(False).post("/v1/chat/ask", json={"question": "how many bench rows"})
    on_body = client_for(True).post("/v1/chat/ask", json={"question": "how many bench rows"})
    assert off_body.status_code == 200
    assert on_body.status_code == 200
    assert off_body.content == on_body.content
    assert seen[0] == seen[1]
    assert b"how many bench rows" == seen[0]


def test_ask_sources_do_not_read_the_flag() -> None:
    for rel in _ASK_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert FLAG not in text
        assert "space_benchmarks" not in text


def test_prompt_bytes_ignore_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_executor.sql_vote import plan_prompt, reading_prompt

    question = "how many bench rows"
    ontology = '{"tables":["bench_sku"],"columns":["qty"]}'
    plan = {"tables": ["bench_sku"], "joins": [], "measure": "", "filters": []}

    def prompt() -> bytes:
        body = plan_prompt(question, ontology)
        body += reading_prompt(question, reading=1)
        body += reading_prompt(question, reading=2, plan=plan, ontology=ontology)
        return body.encode("utf-8")

    monkeypatch.delenv(FLAG, raising=False)
    unset = prompt()
    monkeypatch.setenv(FLAG, "0")
    off = prompt()
    monkeypatch.setenv(FLAG, "1")
    on = prompt()
    assert unset == off == on
    assert len(on) > 0


def test_update_ground_truth_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _grant(monkeypatch)
    _allow(monkeypatch)
    client = _client(monkeypatch, on=True)
    base = f"/v1/spaces/{SPACE}/benchmarks"
    res = client.post(
        base,
        json={"question": "bench synth write", "gold_sql": "UPDATE bench_sku SET qty = 1"},
    )
    assert res.status_code == 400
    assert "statement_not_allowed" in res.json()["detail"]
    assert client.get(base).json()["items"] == []


def test_grant_miss_is_saved_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _grant(monkeypatch)
    _allow(monkeypatch)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(_lake(tmp_path)))
    client = _client(monkeypatch, on=True)
    res = client.post(
        f"/v1/spaces/{SPACE}/benchmarks",
        json={"question": "bench synth missing", "gold_sql": "SELECT qty FROM missing_bench"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "invalid"
    assert body["invalid_reason"]
    assert "1" not in body["invalid_reason"]


def test_scored_sql_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _grant(monkeypatch)
    _allow(monkeypatch)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(_lake(tmp_path)))
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", item_content_hash(GOLD))
    client = _client(monkeypatch, on=True)
    res = client.post(
        f"/v1/spaces/{SPACE}/benchmarks",
        json={"question": "bench synth listed", "gold_sql": GOLD},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == WRITE_BLOCKED
    assert client.get(f"/v1/spaces/{SPACE}/benchmarks").json()["items"] == []


def test_ten_rows_match_the_expected_tally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _grant(monkeypatch)
    _allow(monkeypatch)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(_lake(tmp_path)))
    plan = ["correct"] * 4 + ["wrong"] * 2 + ["clarify", "reconfirm"] + ["abstain"] * 2

    def fake(_ask: Any, question: str, _space: str) -> dict[str, Any]:
        return _env(question.split()[0])

    monkeypatch.setattr("dms_api.routes.space_bench.product_ask", fake)
    client = _client(monkeypatch, on=True)
    base = f"/v1/spaces/{SPACE}/benchmarks"
    for index, kind in enumerate(plan):
        saved = client.post(
            base,
            json={"question": f"{kind} bench synth {index}", "gold_sql": GOLD},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "valid"
    started = client.post(f"{base}/runs")
    assert started.status_code == 200
    run_id = started.json()["run_id"]
    assert wait_bench_run(run_id, timeout=10)
    body = client.get(f"{base}/runs/{run_id}").json()
    assert body["status"] == "done"
    assert body["progress_done"] == 10
    assert body["progress_total"] == 10
    counts = body["tally"]
    assert counts["correct"] == 4
    assert counts["WRONG"] == 2
    assert counts["clarify"] == 1
    assert counts["reconfirm"] == 1
    assert counts["abstain"] == 2
    assert counts["refusal_wrong"] == 0
    assert counts["coverage"] == 0.4
    assert counts["n"] == 10


def test_cancel_stops_the_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _grant(monkeypatch)
    _allow(monkeypatch)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(_lake(tmp_path)))
    started_ask = threading.Event()
    release = threading.Event()

    def fake(_ask: Any, _question: str, _space: str) -> dict[str, Any]:
        if not started_ask.is_set():
            started_ask.set()
            release.wait(3)
        return _env("abstain")

    monkeypatch.setattr("dms_api.routes.space_bench.product_ask", fake)
    client = _client(monkeypatch, on=True)
    base = f"/v1/spaces/{SPACE}/benchmarks"
    for index in range(2):
        assert (
            client.post(
                base,
                json={"question": f"abstain bench synth {index}", "gold_sql": GOLD},
            ).status_code
            == 200
        )
    run_id = client.post(f"{base}/runs").json()["run_id"]
    assert started_ask.wait(5)
    cancelled = client.post(f"{base}/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    release.set()
    assert wait_bench_run(run_id, timeout=5)
    body = client.get(f"{base}/runs/{run_id}").json()
    assert body["status"] == "cancelled"
    assert len(body["results"]) == 1


def test_bad_review_needs_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _grant(monkeypatch)
    _allow(monkeypatch)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(_lake(tmp_path)))
    monkeypatch.setattr(
        "dms_api.routes.space_bench.product_ask",
        lambda _ask, _question, _space: _env("wrong"),
    )
    client = _client(monkeypatch, on=True)
    base = f"/v1/spaces/{SPACE}/benchmarks"
    assert (
        client.post(base, json={"question": "wrong bench synth", "gold_sql": GOLD}).status_code
        == 200
    )
    run_id = client.post(f"{base}/runs").json()["run_id"]
    assert wait_bench_run(run_id)
    result_id = client.get(f"{base}/runs/{run_id}").json()["results"][0]["id"]
    empty = client.post(
        f"{base}/runs/{run_id}/results/{result_id}/review",
        json={"review": "bad", "note": ""},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"] == "review_note_required"
    marked = client.post(
        f"{base}/runs/{run_id}/results/{result_id}/review",
        json={"review": "bad", "note": "rows do not match"},
    )
    assert marked.status_code == 200
    assert marked.json()["review"] == "bad"
    assert marked.json()["outcome"] == "WRONG"


def _listed(rows: list[dict[str, Any]], sql: str) -> dict[str, Any]:
    values = []
    for index, row in enumerate(rows):
        for key, value in row.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            values.append({"id": f"v{index}", "value": value, "label": key})
            break
    return build_answer_envelope(
        answer_id="bench-listed",
        text="Listed.",
        badge="L2_VALIDATED",
        values=values,
        sql_used=sql,
        rows=rows,
        ask_mode="live",
    )


def test_order_and_tolerance_follow_grade52() -> None:
    unordered = "SELECT sku, qty FROM bench_sku"
    ordered = "SELECT sku, qty FROM bench_sku ORDER BY qty"
    gold = [{"sku": "a", "qty": 1}, {"sku": "b", "qty": 2}]
    flipped = [{"sku": "b", "qty": 2}, {"sku": "a", "qty": 1}]
    env = _listed(flipped, unordered)
    assert grade_answer(env, gold, unordered)["outcome"] == "correct"
    assert grade_answer(env, gold, ordered)["outcome"] == "WRONG"
    near_sql = "SELECT n FROM bench_num"
    near = _listed([{"n": 1.203}], near_sql)
    far = {"rows": [{"n": 1.22}], "sql_used": near_sql, "badge": "L2_VALIDATED", "abstained": False}
    assert grade_answer(near, [{"n": 1.2}], near_sql)["outcome"] == "correct"
    assert grade_answer(far, [{"n": 1.2}], near_sql)["outcome"] == "WRONG"
    source = (ROOT / "packages/executor/dms_executor/space_bench.py").read_text(encoding="utf-8")
    assert "ABS_TOL =" not in source
    assert "grade_case" in source


def test_synthetic_20_matches_grade_case() -> None:
    """In-product tally equals grade52.grade_case, which eval_all imports."""
    plan = ["correct"] * 8 + ["wrong"] * 3 + ["clarify"] * 3 + ["reconfirm"] * 3 + ["abstain"] * 3
    gold_sql = "SELECT qty FROM bench_sku"
    gold_rows = [{"qty": 1}]
    items = [
        {
            "id": f"b{index}",
            "question": f"bench-synth-{index}",
            "gold_sql": gold_sql,
            "status": "valid",
            "invalid_reason": "",
        }
        for index in range(len(plan))
    ]
    envs = {item["question"]: _env(plan[index]) for index, item in enumerate(items)}

    def ask(question: str) -> dict[str, Any]:
        return envs[question]

    def execute(_sql: str) -> tuple[list[dict[str, Any]], None]:
        return gold_rows, None

    status, results = run_items(items, ask, execute, lambda: False)
    grader = _grade52()
    direct = [
        grader.grade_case(
            gold=gold_rows,
            gold_sql=gold_sql,
            env=envs[item["question"]],
            served_rows=list(envs[item["question"]].get("rows") or []),
            as_of=str(envs[item["question"]].get("as_of") or ""),
        )
        for item in items
    ]
    assert status == "done"
    assert [row["bucket"] for row in results] == [row["bucket"] for row in direct]
    counted = tally(results)
    wrong_buckets = {grader.BUCKET_WRONG, grader.BUCKET_REFUSAL_WRONG}
    wrong = sum(1 for row in direct if row["bucket"] in wrong_buckets)
    assert counted == {
        "n": 20,
        "graded": 20,
        "coverage": 8 / 20,
        "correct": 8,
        "WRONG": wrong,
        "refusal_wrong": 0,
        "reconfirm": 3,
        "clarify": 3,
        "abstain": 3,
        "invalid_gold": 0,
    }
    assert counted["WRONG"] == 3


def test_retriever_never_returns_a_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _lake(tmp_path)
    reset_memory_bench()
    from dms_core.control_plane.bench_store import memory_bench

    memory_bench().add_item(
        space_id=SPACE,
        question="bench synth marker question",
        gold_sql="SELECT qty FROM bench_sku WHERE sku = 'b'",
        status="valid",
        invalid_reason="",
        source="steward",
    )
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    rows = retrieve_verified_examples(
        "bench synth marker question",
        space_id=SPACE,
        warehouse=path,
        grantable={"bench_sku"},
        dialect="duckdb",
        force=True,
    )
    assert rows == []
    blob = json.dumps(rows)
    assert "sku = 'b'" not in blob


def test_retriever_ast_does_not_name_the_bench_tables() -> None:
    for rel in _RETRIEVERS:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for name in _BENCH_TABLES:
                    assert name not in node.value, f"{rel} names {name}"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "bench_store" not in node.module
                assert "space_bench" not in node.module


def test_from_examples_copies_verified_sql_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _grant(monkeypatch)
    _allow(monkeypatch)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(_lake(tmp_path)))

    def rows(_space: str) -> list[dict[str, str]]:
        return [{"question": "bench synth example count", "sql": GOLD}]

    monkeypatch.setattr("dms_api.routes.space_bench.bench_example_rows", rows)
    client = _client(monkeypatch, on=True)
    base = f"/v1/spaces/{SPACE}/benchmarks"
    copied = client.post(f"{base}/from-examples")
    assert copied.status_code == 200
    assert copied.json()["added"] == 1
    items = client.get(base).json()["items"]
    assert len(items) == 1
    assert items[0]["source"] == "verified_example"
    assert items[0]["status"] == "valid"


def test_non_steward_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch)
    _flag(monkeypatch, True)
    monkeypatch.setenv("DMS_ACTOR_ROLE", "viewer")
    _clear_settings()
    client = TestClient(create_app())
    res = client.get(f"/v1/spaces/{SPACE}/benchmarks")
    assert res.status_code == 403
    assert res.json()["detail"] == "steward_grant_required"
