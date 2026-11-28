"""BIRD-HARNESS-CI-01. Synthetic two-database fixture. Not the BIRD corpus.

Official EX is a set of row tuples. Column order matters. EX is correct
divided by every attempted id. An unresolved 402 or 429 stays in that
denominator and marks the run INCOMPLETE. The runner flag defaults off and
does not change the product prompt, envelope, or served route.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from bench.bird import baseline, corpus, ex, gold, ingest, run, score  # noqa: E402
from bench.bird.__main__ import main  # noqa: E402

_EMPTY = {"prompt": None, "envelope": None, "route": None}
_AI = {
    "plan_origin": "generate_sql",
    "ladder_rung": "plan",
    "served_model": "copied-from-openvault",
    "abstained": False,
}
_RULE = {
    "badge": "L1_GOVERNED_METRIC",
    "plan_origin": "l1_governed_metric",
    "ladder_rung": "l1",
    "abstained": False,
}


def _write_db(path: Path, statements: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(statements)
    con.commit()
    con.close()


def _two_dbs(root: Path) -> None:
    _write_db(
        root / "shop.sqlite",
        """
        CREATE TABLE items (n INTEGER, name TEXT);
        INSERT INTO items VALUES (1, 'Alpha');
        INSERT INTO items VALUES (1, 'Alpha');
        INSERT INTO items VALUES (2, 'Beta');
        INSERT INTO items VALUES (3, 'Gamma');
        INSERT INTO items VALUES (4, 'Delta');
        """,
    )
    _write_db(
        root / "yard.sqlite",
        """
        CREATE TABLE plots (code TEXT, city TEXT);
        INSERT INTO plots VALUES ('A', 'North');
        INSERT INTO plots VALUES ('B', 'South');
        """,
    )


def _questions() -> list[dict[str, object]]:
    return [
        {
            "question_id": "s1",
            "db_id": "shop",
            "question": "How many distinct item ids are there?",
            "evidence": "use the items table",
            "SQL": "SELECT COUNT(DISTINCT n) FROM items",
        },
        {
            "question_id": "s2",
            "db_id": "yard",
            "question": "List plot codes",
            "SQL": "SELECT code FROM plots",
        },
    ]


def test_official_ex_column_order_duplicates_and_floats() -> None:
    assert ex.calculate_ex([(1, "a")], [("a", 1)]) == 0
    assert ex.calculate_ex([(1,), (1,)], [(1,)]) == 1
    assert ex.calculate_ex([(1.0,)], [(1.1,)]) == 0
    assert ex.calculate_ex([], []) == 1
    assert ex.calculate_ex([("Alpha",)], [("alpha",)]) == 0
    assert ex.calculate_ex_normalised([("Alpha",)], [("alpha",)]) == 1
    assert ex.calculate_ex_normalised([(1.0,)], [("1.0000004",)]) == 1
    assert ex.calculate_ex([(1.0,)], [("1.0000004",)]) == 0
    assert ex.calculate_ex_normalised([(1, "a")], [("a", 1)]) == 0
    assert ex.nearest_rank([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert ex.nearest_rank([1.0, 2.0, 3.0, 4.0], 95) == 4.0


def test_sqlite_duplicates_collapse_and_writes_do_not_land(tmp_path: Path) -> None:
    _two_dbs(tmp_path)
    rows, err = gold.execute_readonly(tmp_path / "shop.sqlite", "SELECT n, name FROM items")
    assert err is None and rows is not None
    assert ex.calculate_ex(rows, [(1, "Alpha"), (2, "Beta"), (3, "Gamma"), (4, "Delta")]) == 1
    _denied, write_err = gold.execute_readonly(
        tmp_path / "shop.sqlite", "INSERT INTO items VALUES (9, 'nope')"
    )
    assert write_err == "destructive_sql"
    counted, count_err = gold.execute_readonly(
        tmp_path / "shop.sqlite", "SELECT COUNT(*) FROM items"
    )
    assert count_err is None and counted == [(5,)]


def test_contamination_is_not_scored_and_is_retried() -> None:
    assert score.contaminated(429, None)
    assert score.contaminated(402, None)
    assert score.contaminated(200, "HTTP 429")
    assert score.contaminated(200, "payment 402")
    assert not score.contaminated(200, "code 4020")
    assert not score.contaminated(200, "id 1402")
    graded = score.classify(
        http_status=429,
        error=None,
        envelope=_AI,
        predicted=[(1,)],
        gold_rows=[(1,)],
        pred_error=None,
        gold_error=None,
    )
    assert graded["contaminated"] is True
    assert graded["outcome"] is None
    summary = score.report(
        [
            {
                "id": "s1",
                "outcome": "CORRECT",
                "ex": 1,
                "ex_norm": 1,
                "contaminated": False,
                "seconds": 1.0,
                "path": "ai",
            },
            {"id": "s2", "contaminated": True, "http_status": 402, "seconds": 9.0},
        ]
    )
    assert summary["n_total"] == 2
    assert summary["n_scored"] == 1
    assert summary["infra_unscored"] == 1
    assert summary["n_contaminated"] == 1
    assert summary["retry_ids"] == ["s2"]
    assert summary["ex"] == 0.5
    assert summary["status"] == "INCOMPLETE"
    assert summary["headline"] == "INCOMPLETE"
    assert summary["ex_scored_only"] == 1.0
    assert summary["p50"] == 1.0
    assert summary["pass"] is False


def test_one_correct_and_one_unresolved_429_is_incomplete() -> None:
    summary = score.report(
        [
            {
                "id": "s1",
                "outcome": "CORRECT",
                "ex": 1,
                "ex_norm": 1,
                "contaminated": False,
                "seconds": 1.0,
                "path": "ai",
            },
            {"id": "s2", "contaminated": True, "http_status": 429, "seconds": 9.0},
        ]
    )
    assert summary["ex"] == 0.5
    assert summary["status"] == "INCOMPLETE"
    assert summary["headline"] == "INCOMPLETE"
    assert summary["infra_unscored"] == 1
    assert summary["n_total"] == 2
    text = score.format_report(summary)
    assert text.startswith("INCOMPLETE")
    assert "ex_scored_only=1.0000 secondary" in text
    assert text.splitlines()[0].startswith("INCOMPLETE")


def test_attribution_uses_grade52_path_group() -> None:
    ai = score.classify(
        http_status=200,
        error=None,
        envelope=_AI,
        predicted=[(1,)],
        gold_rows=[(1,)],
        pred_error=None,
        gold_error=None,
    )
    rule = score.classify(
        http_status=200,
        error=None,
        envelope=_RULE,
        predicted=[(2,)],
        gold_rows=[(1,)],
        pred_error=None,
        gold_error=None,
    )
    assert ai["outcome"] == "CORRECT" and ai["path"] == "ai"
    assert rule["outcome"] == "WRONG" and rule["path"] == "rule"
    summary = score.report(
        [
            {"id": "a", **ai, "seconds": 1.0, "contaminated": False},
            {"id": "b", **rule, "seconds": 2.0, "contaminated": False},
        ]
    )
    assert summary["paths"] == {"ai": 1, "rule": 1, "unattributed": 0}
    assert summary["outcomes"]["CORRECT"] == 1
    assert summary["outcomes"]["WRONG"] == 1


def test_askback_abstain_and_empty_match() -> None:
    askback = {
        "abstained": True,
        "badge": "ABSTAIN",
        "reconfirm": {"suggested_question": "How many items?", "token": "tok-1"},
    }
    graded = score.classify(
        http_status=200,
        error=None,
        envelope=askback,
        predicted=[],
        gold_rows=[],
        pred_error=None,
        gold_error=None,
    )
    assert graded["outcome"] == "ASKBACK"
    bare = {"abstained": True, "badge": "ABSTAIN", "reconfirm": {"suggested_question": ""}}
    abstained = score.classify(
        http_status=200,
        error=None,
        envelope=bare,
        predicted=[],
        gold_rows=[(1,)],
        pred_error=None,
        gold_error=None,
    )
    assert abstained["outcome"] == "ABSTAIN"
    empty = score.classify(
        http_status=200,
        error=None,
        envelope=_AI,
        predicted=[],
        gold_rows=[],
        pred_error=None,
        gold_error=None,
    )
    assert empty["outcome"] == "CORRECT"
    destructive = score.classify(
        http_status=200,
        error=None,
        envelope=None,
        predicted=None,
        gold_rows=None,
        pred_error="destructive_sql",
        gold_error=None,
    )
    assert destructive["outcome"] == "ABSTAIN"


def test_flag_off_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(run.FLAG, raising=False)
    frozen = dict(_EMPTY)
    out = tmp_path / "score.jsonl"
    code = main(
        [
            "run",
            "--arm",
            "dms",
            "--questions",
            str(tmp_path / "missing.json"),
            "--db-root",
            str(tmp_path),
            "--out",
            str(out),
        ]
    )
    captured = capsys.readouterr().out
    assert code == run.EXIT_CONFIG
    assert "No ask was sent" in captured
    assert not out.exists()
    assert run.surface() == frozen == _EMPTY


def test_ingest_one_space_per_database(tmp_path: Path) -> None:
    _two_dbs(tmp_path)
    seen: list[tuple[str, dict[str, object]]] = []

    def post(path: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
        seen.append((path, body))
        if path == "/v1/spaces":
            return 201, {"space": {"id": "space-" + str(body["name"])}}
        return 200, {"ok": True}

    spaces = ingest.ingest_root(post, tmp_path)
    assert set(spaces) == {"shop", "yard"}
    assert spaces["shop"]["space_id"] == "space-shop"
    assert spaces["yard"]["database"] == "yard.sqlite"
    sources = [body for path, body in seen if path == "/v1/studio/sources/sql"]
    assert len(sources) == 2
    assert {body["kind"] for body in sources} == {"sqlite"}
    assert {body["database"] for body in sources} == {"shop.sqlite", "yard.sqlite"}

    def boom(path: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
        return 500, {"detail": "tenant"}

    with pytest.raises(ingest.IngestError) as caught:
        ingest.ingest_one(boom, name="shop", database="shop.sqlite")
    assert caught.value.code == "space_create_failed"


def test_baseline_prompt_is_one_role_and_three_rows(tmp_path: Path) -> None:
    _two_dbs(tmp_path)
    block = gold.schema_block(tmp_path / "shop.sqlite")
    body = baseline.baseline_body("How many distinct item ids are there?", block)
    assert body["role"] == "sql"
    with pytest.raises(baseline.ModelKeyRefused):
        baseline.reject_model_keys({"role": "sql", "model": "hidden"})
    escalated = baseline.escalate_body(body)
    assert escalated["role"] == "escalate"
    assert escalated["messages"] == body["messages"]
    assert "model" not in escalated
    with pytest.raises(baseline.ModelKeyRefused):
        baseline.escalate_body({"role": "sql", "model": "hidden"})
    sample_line = next(line for line in block.splitlines() if line.startswith("sample "))
    sample = json.loads(sample_line[len("sample ") :])
    assert len(sample["rows"]) == 3
    reply = {"choices": [{"message": {"content": "```sql\nSELECT 1\n```"}}]}
    assert baseline.sql_from_reply(reply) == "SELECT 1"


def test_simulated_yes_and_resume(tmp_path: Path) -> None:
    root = tmp_path / "db"
    _two_dbs(root)
    calls: list[str] = []

    def post(url: str, body: dict[str, object], headers: dict[str, str]) -> tuple[int, dict]:
        calls.append(url)
        if url.endswith("/confirm"):
            assert body == {"token": "tok-1", "yes": True}
            return 200, {"rows": [{"n": 4}], "abstained": False, **_AI}
        question = str(body.get("question") or "")
        if question.startswith("List plot"):
            return 429, {"detail": {"code": "429"}}
        return 200, {
            "abstained": True,
            "badge": "ABSTAIN",
            "reconfirm": {"suggested_question": "How many items?", "token": "tok-1"},
        }

    out = tmp_path / "score.jsonl"
    questions = _questions()
    run.run_questions(
        questions,
        arm="dms",
        db_root=root,
        spaces={"shop": "space-shop", "yard": "space-yard"},
        evidence=False,
        simulate_yes=True,
        concurrency=2,
        out=out,
        post=post,
        api_base="http://api.example",
    )
    lines = run.load_jsonl(out)
    by_id = {row["id"]: row for row in lines}
    assert by_id["s1"]["outcome"] == "CORRECT"
    assert by_id["s1"]["yes"] is True
    assert by_id["s1"]["path"] == "ai"
    assert by_id["s2"]["contaminated"] is True
    assert "rows" not in by_id["s1"]
    assert "sql_used" not in by_id["s1"]
    blob = out.read_text(encoding="utf-8")
    assert "Alpha" not in blob
    assert "SELECT" not in blob

    def post_again(url: str, body: dict[str, object], headers: dict[str, str]) -> tuple[int, dict]:
        question = str(body.get("question") or "")
        assert not question.startswith("How many")
        return 200, {"rows": [{"code": "A"}, {"code": "B"}], "abstained": False, **_RULE}

    run.run_questions(
        questions,
        arm="dms",
        db_root=root,
        spaces={"shop": "space-shop", "yard": "space-yard"},
        evidence=False,
        simulate_yes=True,
        concurrency=1,
        out=out,
        post=post_again,
        api_base="http://api.example",
        prior=run.load_jsonl(out),
    )
    summary = score.report(run.load_jsonl(out))
    assert summary["n_contaminated"] == 0
    assert summary["infra_unscored"] == 0
    assert summary["status"] == "COMPLETE"
    assert summary["ex"] == 1.0
    assert summary["outcomes"]["CORRECT"] == 2
    assert summary["paths"]["ai"] == 1
    assert summary["paths"]["rule"] == 1
    assert any(url.endswith("/v1/chat/ask") for url in calls)
    asked = corpus.asked_text(questions[0], evidence=False)
    assert asked == questions[0]["question"]
    with_evidence = corpus.asked_text(questions[0], evidence=True)
    assert with_evidence.startswith(str(questions[0]["question"]))
    assert "\nEvidence: " in with_evidence


def test_baseline_executes_the_model_sql(tmp_path: Path) -> None:
    root = tmp_path / "db"
    _two_dbs(root)
    seen: list[dict[str, object]] = []

    def post(url: str, body: dict[str, object], headers: dict[str, str]) -> tuple[int, dict]:
        seen.append(body)
        return 200, {
            "choices": [{"message": {"content": "SELECT COUNT(DISTINCT n) FROM items"}}]
        }

    out = tmp_path / "base.jsonl"
    run.run_questions(
        [_questions()[0]],
        arm="baseline",
        db_root=root,
        spaces={},
        evidence=False,
        simulate_yes=False,
        concurrency=1,
        out=out,
        post=post,
        ov_url="http://ov.example",
    )
    row = run.load_jsonl(out)[0]
    assert row["outcome"] == "CORRECT"
    assert row["path"] == "unattributed"
    assert seen[0]["role"] == "sql"
    assert "model" not in seen[0]
    assert "Alpha" not in out.read_text(encoding="utf-8")


def test_release_layout_and_full_file_guard(tmp_path: Path) -> None:
    packed = tmp_path / "minidev" / "MINIDEV"
    db_dir = packed / "dev_databases"
    _two_dbs(db_dir)
    questions = packed / "mini_dev_sqlite.json"
    questions.write_text(json.dumps(_questions()), encoding="utf-8")
    found_q, found_db = corpus.locate_release(tmp_path)
    assert found_q == questions
    assert found_db == db_dir
    assert corpus.validate_full(_questions(), limit=None) is not None
    assert corpus.validate_full(_questions(), limit=1) is None
    zip_path = tmp_path / "mini.zip"
    with zipfile.ZipFile(zip_path, "w") as handle:
        for path in packed.rglob("*"):
            if path.is_file():
                handle.write(path, path.relative_to(tmp_path).as_posix())
    unpacked = tmp_path / "out"
    got_q, got_db = corpus.unpack_release(zip_path, unpacked)
    assert got_q.is_file()
    assert (got_db / "shop.sqlite").is_file()


def test_402_sends_escalate_role_before_one_same_post_retry(tmp_path: Path) -> None:
    root = tmp_path / "db"
    _two_dbs(root)
    seen: list[tuple[str, object, dict[str, str]]] = []

    def post(url: str, body: dict[str, object], headers: dict[str, str]) -> tuple[int, dict]:
        seen.append((url, body.get("role"), headers))
        blob = json.dumps(body).lower()
        assert "claude" not in blob
        assert "model" not in body
        if body.get("role") == "escalate":
            return 200, {"choices": [{"message": {"content": "SELECT code FROM plots"}}]}
        return 402, {"detail": {"code": "402"}}

    out = tmp_path / "dms.jsonl"
    run.run_questions(
        [_questions()[1]],
        arm="dms",
        db_root=root,
        spaces={"yard": "space-yard"},
        evidence=False,
        simulate_yes=True,
        concurrency=1,
        out=out,
        post=post,
        api_base="http://api.example",
        ov_url="http://ov.example",
        ov_headers={"Authorization": "Bearer ov_test"},
    )
    assert [item[0] for item in seen] == [
        "http://api.example/v1/chat/ask",
        "http://ov.example/v1/chat/completions",
    ]
    assert seen[0][2] == {}
    assert seen[1][1] == "escalate"
    assert "Authorization" in seen[1][2]
    row = run.load_jsonl(out)[0]
    assert row["outcome"] == "CORRECT"
    assert row["rung"] == "escalate"
    assert row["contaminated"] is False
    blob = out.read_text(encoding="utf-8")
    assert "ov_test" not in blob
    assert "Alpha" not in blob
    assert "SELECT" not in blob

    seen.clear()

    def post_again(
        url: str, body: dict[str, object], headers: dict[str, str]
    ) -> tuple[int, dict]:
        seen.append((url, body.get("role"), headers))
        return 429, {"detail": {"code": "429"}}

    base_out = tmp_path / "base.jsonl"
    run.run_questions(
        [_questions()[0]],
        arm="baseline",
        db_root=root,
        spaces={},
        evidence=False,
        simulate_yes=False,
        concurrency=1,
        out=base_out,
        post=post_again,
        ov_url="http://ov.example",
    )
    assert [item[1] for item in seen] == ["sql", "escalate", "sql"]
    unresolved = run.load_jsonl(base_out)[0]
    assert unresolved["contaminated"] is True
    assert unresolved["rung"] == "unresolved"
    summary = score.report(run.load_jsonl(base_out))
    assert summary["ex"] == 0.0
    assert summary["infra_unscored"] == 1
    assert summary["status"] == "INCOMPLETE"


def test_nightly_workflow_is_off_until_the_variable() -> None:
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "bird-nightly.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert "vars.DMS_BIRD_HARNESS == '1'" in text
    assert "mini_dev_sqlite.json" not in text
