"""BENCH-MATRIX-01: synthetic matrix runner. Not a dataset score. Not PASS.

The five-question fixture is built in a temp dir. No BIRD, Spider, or
AdventureWorks rows are committed or read.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bench_matrix  # noqa: E402
from score_bound import zero_wrong_summary_bounded  # noqa: E402

_EMPTY_TRIPLE = {"prompt": None, "envelope": None, "route": None}


def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench_matrix, "pgrep_armed", lambda: False)
    monkeypatch.delenv("OPENVAULT_URL", raising=False)
    monkeypatch.delenv("DMS_BENCH_MATRIX", raising=False)


def _bird_items(n: int, dbs: int) -> list[dict[str, object]]:
    return [
        {
            "question_id": i,
            "db_id": f"db{i % dbs}",
            "question": f"synthetic count {i}",
            "SQL": "SELECT 1 AS n",
        }
        for i in range(n)
    ]


def test_self_check_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert bench_matrix.main(["--self-check"]) == bench_matrix.EXIT_PASS
    out = capsys.readouterr().out
    assert "Not PASS" in out
    assert "served_correct=1" in out
    assert "WRONG=1" in out
    for marker in ("SYN_RED", "SYN_BLUE", "SYN_LEAK"):
        assert marker not in out


def test_artifact_schema_rejects_a_pass_claim() -> None:
    doc = {
        "kind": bench_matrix.KIND,
        "ticket": bench_matrix.TICKET,
        "dataset": "synthetic_sqlite",
        "git_sha": "abc",
        "run_label": "lab",
        "n": 1,
        "served_correct": 0,
        "wrong": 0,
        "reconfirm": 0,
        "abstain": 1,
        "gold_error": 0,
        "error": 0,
        "wrong_served_upper_95": None,
        "seconds_p50": 0.1,
        "seconds_p95": 0.2,
        "ov_usage_usd": None,
        "ov_usage_read": "skipped",
        "ov_usage_reason": "no_openvault_url",
        "header_lang": None,
        "baseline": False,
        "target": None,
        "pass": False,
        "cases": [{"id": "q1", "verdict": "abstain", "seconds": 0.1, "reason_code": "ungranted"}],
    }
    assert bench_matrix.artifact_problems(doc) == []
    doc["pass"] = True
    assert "pass" in bench_matrix.artifact_problems(doc)
    doc["pass"] = False
    doc["cases"][0]["rows"] = [{"name": "SYN_RED"}]
    problems = bench_matrix.artifact_problems(doc)
    assert "case_keys" in problems or "leak_key" in problems


def test_flag_off_sends_no_prompt_envelope_or_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _quiet(monkeypatch)
    seen: list[object] = []

    def post(*args: object, **kwargs: object) -> dict[str, object]:
        seen.append((args, kwargs))
        return {}

    monkeypatch.setattr(bench_matrix, "post_ask", post)
    argv = [
        "--dataset",
        "bird_minidev_sqlite",
        "--source",
        "unused.json",
        "--space",
        "space-1",
        "--out",
        str(tmp_path),
    ]
    first = bench_matrix.main(argv)
    first_out = capsys.readouterr().out
    monkeypatch.setenv("DMS_BENCH_MATRIX", "0")
    second = bench_matrix.main(argv)
    second_out = capsys.readouterr().out
    assert first == second == bench_matrix.EXIT_CONFIG
    assert first_out == second_out
    assert "No ask was sent" in first_out
    assert seen == []
    assert list(tmp_path.glob("*.json")) == []
    assert {"prompt": None, "envelope": None, "route": None} == _EMPTY_TRIPLE


def test_flag_on_posts_the_question_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _quiet(monkeypatch)
    monkeypatch.setenv("DMS_BENCH_MATRIX", "1")
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    con = sqlite3.connect(db_dir / "toy.sqlite")
    con.execute("CREATE TABLE widgets (n INTEGER)")
    con.execute("INSERT INTO widgets VALUES (1)")
    con.commit()
    con.close()
    question = "How many widgets are in the tray?"
    source = tmp_path / "questions.json"
    source.write_text(
        json.dumps(
            [
                {
                    "question_id": 0,
                    "db_id": "toy",
                    "question": question,
                    "SQL": "SELECT COUNT(*) AS n FROM widgets",
                }
            ]
        ),
        encoding="utf-8",
    )
    seen: list[tuple[str, str]] = []

    def post(base: str, asked: str, space: str, timeout: float) -> dict[str, object]:
        body, route = bench_matrix.ask_request(asked, space)
        seen.append((route, json.dumps(body, sort_keys=True)))
        assert base == "http://127.0.0.1:9"
        assert timeout == 60.0
        return {"badge": "ABSTAIN", "abstained": True, "reason_code": "ungranted"}

    monkeypatch.setattr(bench_matrix, "post_ask", post)
    out = tmp_path / "out"
    code = bench_matrix.main(
        [
            "--dataset",
            "bird_minidev_sqlite",
            "--source",
            str(source),
            "--db-dir",
            str(db_dir),
            "--space",
            "space-1",
            "--limit",
            "1",
            "--url",
            "http://127.0.0.1:9",
            "--out",
            str(out),
            "--run-label",
            "unit-label",
        ]
    )
    text = capsys.readouterr().out
    assert code == bench_matrix.EXIT_PASS
    body, route = bench_matrix.ask_request(question, "space-1")
    assert seen == [(route, json.dumps(body, sort_keys=True))]
    assert route == "/v1/chat/ask"
    artifact = json.loads((out / "bird_minidev_sqlite.json").read_text(encoding="utf-8"))
    assert artifact["pass"] is False
    assert artifact["target"] is None
    assert artifact["abstain"] == 1
    assert artifact["wrong"] == 0
    assert bench_matrix.artifact_problems(artifact) == []
    assert "question" not in artifact["cases"][0]
    assert zero_wrong_summary_bounded(text.splitlines())
    for marker in ("SYN_RED", "SYN_BLUE", "SYN_LEAK"):
        assert marker not in text
        assert marker not in json.dumps(artifact)


def test_refuses_when_pgrep_finds_an_armed_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []

    def run(argv: list[str], **_kwargs: object) -> object:
        seen.extend(argv)

        class Proc:
            returncode = 0

        return Proc()

    assert bench_matrix.pgrep_armed(run=run) is True
    assert seen == bench_matrix.ARMED_ARGV
    monkeypatch.setattr(bench_matrix, "pgrep_armed", lambda: True)
    monkeypatch.setenv("DMS_BENCH_MATRIX", "1")
    called: list[int] = []
    monkeypatch.setattr(bench_matrix, "post_ask", lambda *_a, **_k: called.append(1) or {})
    code = bench_matrix.main(["--dataset", "spider2_lite_sqlite", "--source", "x", "--space", "s"])
    assert code == bench_matrix.EXIT_CONFIG
    assert called == []
    assert "armed_run_live" in capsys.readouterr().out
    assert bench_matrix.main(["--self-check"]) == bench_matrix.EXIT_CONFIG


def test_exception_text_does_not_keep_row_values(tmp_path: Path) -> None:
    db = tmp_path / "toy.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE widgets (n INTEGER)")
    con.execute("INSERT INTO widgets VALUES (1)")
    con.commit()
    con.close()
    case = bench_matrix.Case(
        id="q1",
        question="How many widgets are in the tray?",
        gold_sql="SELECT COUNT(*) AS n FROM widgets",
        db_id="toy",
    )

    def ask(_question: str) -> dict[str, object]:
        raise RuntimeError("cell SYN_RED")

    rows, unasked, blocked = bench_matrix.score_cases(
        [case], bench_matrix._sqlite_gold_fn(lambda _case: db), ask
    )
    blob = json.dumps(rows)
    assert unasked == 0 and blocked is False
    assert rows[0]["verdict"] == "error"
    assert rows[0]["reason_code"] == "RuntimeError"
    assert "SYN_RED" not in blob
    assert bench_matrix.safe_reason("the cell is SYN_RED") == "unstructured"


def test_header_lang_keeps_question_text_and_values(tmp_path: Path) -> None:
    _db, csv_dir, map_path, cases, _plan = bench_matrix._synthetic(tmp_path)
    tables, columns = bench_matrix.reingest_csv(csv_dir, map_path, "zh", tmp_path / "zh.sqlite")
    rewritten = bench_matrix.apply_header_lang(cases, tables, columns)
    assert [case.question for case in rewritten] == [case.question for case in cases]
    original = bench_matrix._sqlite_gold_fn(lambda _case: _db)
    translated = bench_matrix._sqlite_gold_fn(lambda _case: tmp_path / "zh.sqlite")
    import bird_minidev

    for before, after in zip(cases, rewritten, strict=True):
        left, left_err = original(before)
        if left_err:
            continue
        right, right_err = translated(after)
        assert right_err is None
        assert bird_minidev.rows_equal(left or [], right or [])
    quoted = tmp_path / "quote.csv"
    quoted.write_text("name\nSYN_RED\n", encoding="utf-8")
    quote_map = tmp_path / "quote_map.json"
    quote_map.write_text(
        json.dumps({"columns": {"quote.name": {"zh": 'a"b'}}}),
        encoding="utf-8",
    )
    quote_dir = tmp_path / "quote_csv"
    quote_dir.mkdir()
    (quote_dir / "quote.csv").write_text("name\nSYN_RED\n", encoding="utf-8")
    bench_matrix.reingest_csv(quote_dir, quote_map, "zh", tmp_path / "quote.sqlite")
    case = bench_matrix.Case(id="n", question="List names.", gold_sql="SELECT name FROM quote")
    renamed = bench_matrix.apply_header_lang([case], {}, {("quote", "name"): 'a"b'})
    rows, err = bench_matrix.run_sqlite_gold(renamed[0].gold_sql, tmp_path / "quote.sqlite")
    assert err is None
    assert rows == [{'a"b': "SYN_RED"}]


def test_bird_ids_must_be_0_through_499(tmp_path: Path) -> None:
    full = _bird_items(500, 11)
    assert bench_matrix.aligned_ids(full) is True
    shifted = [dict(item) for item in full]
    shifted[0]["question_id"] = 1
    assert bench_matrix.aligned_ids(shifted) is False
    good = tmp_path / "full.json"
    good.write_text(json.dumps(full), encoding="utf-8")
    cases, digest = bench_matrix.load_bird(
        bench_matrix.Slice(dataset="bird_minidev_sqlite", source=str(good), space="s", out=tmp_path)
    )
    assert len(cases) == 500
    assert digest
    bad = tmp_path / "small.json"
    bad.write_text(json.dumps(_bird_items(2, 1)), encoding="utf-8")
    with pytest.raises(bench_matrix.ConfigError) as small:
        bench_matrix.load_bird(
            bench_matrix.Slice(
                dataset="bird_minidev_sqlite", source=str(bad), space="s", out=tmp_path
            )
        )
    assert small.value.code == "minidev_shape"
    gap = tmp_path / "gap.json"
    gap.write_text(json.dumps(shifted), encoding="utf-8")
    with pytest.raises(bench_matrix.ConfigError) as missing:
        bench_matrix.load_bird(
            bench_matrix.Slice(
                dataset="bird_minidev_sqlite", source=str(gap), space="s", out=tmp_path
            )
        )
    assert missing.value.code == "aligned_ids"


def test_spider_compares_each_result_csv(tmp_path: Path) -> None:
    root = tmp_path / "spider"
    gold = root / "gold"
    gold.mkdir(parents=True)
    (gold / "i1.csv").write_text("name\nSYN_RED\n", encoding="utf-8")
    (root / "questions.json").write_text(
        json.dumps(
            [
                {
                    "instance_id": "i1",
                    "db_id": "toy",
                    "question": "List the color.",
                    "gold_csv": "gold/i1.csv",
                }
            ]
        ),
        encoding="utf-8",
    )

    def ask(_question: str) -> dict[str, object]:
        return {"badge": "L0_CERTIFIED", "abstained": False, "rows": [{"name": "SYN_LEAK"}]}

    code, doc = bench_matrix.run_slice(
        bench_matrix.Slice(
            dataset="spider2_lite_sqlite",
            source=str(root),
            space="space-1",
            out=tmp_path / "out",
            run_label="spider-unit",
        ),
        ask_fn=ask,
        usage_fn=lambda _label: {
            "usd": None,
            "read": "skipped",
            "reason": "no_openvault_url",
        },
    )
    assert code == bench_matrix.EXIT_FAIL
    assert doc["wrong"] == 1
    assert doc["cases"][0]["reason_code"] == "rows_mismatch"
    blob = json.dumps(doc)
    assert "SYN_RED" not in blob
    assert "SYN_LEAK" not in blob
    assert "List the color." not in blob


def test_adventureworks_without_a_dsn_does_not_ask(tmp_path: Path) -> None:
    source = tmp_path / "aw.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "a1",
                    "question": "How many products?",
                    "gold_sql": "SELECT 1 AS n",
                }
            ]
        ),
        encoding="utf-8",
    )
    called: list[int] = []

    def ask(_question: str) -> dict[str, object]:
        called.append(1)
        return {}

    with pytest.raises(bench_matrix.ConfigError) as exc:
        bench_matrix.run_slice(
            bench_matrix.Slice(
                dataset="adventureworks_pg",
                source=str(source),
                space="space-1",
                out=tmp_path / "out",
            ),
            ask_fn=ask,
        )
    assert exc.value.code == "pg_dsn_unset"
    assert called == []
    assert "password" not in exc.value.code


def test_ov_usage_sums_only_the_run_label() -> None:
    got = bench_matrix.read_ov_usage(
        "http://fixture.invalid/api/usage", "bench-self-check", bench_matrix._fixture_usage
    )
    blob = json.dumps(got)
    assert got["usd"] == 0.3
    assert got["read"] == "ok"
    assert "sk-SECRET-VALUE" not in blob
    assert "api_key" not in got
    other = bench_matrix.read_ov_usage(
        "http://fixture.invalid/api/usage",
        "missing",
        lambda _url, _params: {"events": [{"run_label": "other", "cost_usd": 9}]},
    )
    assert other["usd"] is None
    assert other["reason"] == "no_run_label_match"


def test_gold_sql_cannot_escape_or_mutate(tmp_path: Path) -> None:
    db = tmp_path / "toy.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE widgets (n INTEGER)")
    con.execute("INSERT INTO widgets VALUES (1)")
    con.commit()
    con.close()
    rows, err = bench_matrix.run_sqlite_gold("DROP TABLE widgets", db)
    assert rows is None and err == "gold_not_select"
    rows, err = bench_matrix.run_sqlite_gold("SELECT 1 AS n; DROP TABLE widgets", db)
    assert rows is None and err == "gold_not_select"
    con = sqlite3.connect(db)
    assert con.execute("SELECT n FROM widgets").fetchone()[0] == 1
    con.close()
    root = tmp_path / "pack"
    root.mkdir()
    secret = tmp_path / "secret.csv"
    secret.write_text("name\nSYN_RED\n", encoding="utf-8")
    (root / "questions.json").write_text(
        json.dumps(
            [
                {
                    "instance_id": "i1",
                    "question": "List the color.",
                    "gold_csv": "../secret.csv",
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(bench_matrix.ConfigError) as exc:
        bench_matrix.load_spider(
            bench_matrix.Slice(
                dataset="spider2_lite_sqlite",
                source=str(root),
                space="space-1",
                out=tmp_path / "out",
            )
        )
    assert exc.value.code == "gold_csv_outside"


def test_zero_wrong_summary_carries_the_bound(capsys: pytest.CaptureFixture[str]) -> None:
    doc = bench_matrix.build_artifact(
        dataset="synthetic_sqlite",
        git_sha="abc",
        run_label="lab",
        cases=[{"id": "q1", "verdict": "abstain", "seconds": 0.2, "reason_code": "ungranted"}],
        usage={"usd": None, "read": "skipped", "reason": "no_openvault_url"},
        header_lang=None,
        baseline=False,
        smoke=False,
        blocked=False,
        unasked=0,
        source_sha256=None,
        gold_db=None,
    )
    assert doc["wrong_served_upper_95"] is None
    bench_matrix.print_report(doc)
    assert zero_wrong_summary_bounded(capsys.readouterr().out.splitlines())
