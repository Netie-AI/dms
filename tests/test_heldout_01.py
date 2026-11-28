"""DMS-ACC-HELDOUT-01. Stub tally on the BIRD runner. Not a live score.

Rule-served matches stay out of held-out correct. They are not WRONG when
the rows match. Live mode is the OpenVault route and does not send a
provider. Missing keys are CONFIG.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from bench.bird import baseline, gold, heldout, run  # noqa: E402
from bench.bird import memory as heldout_memory  # noqa: E402
from bench.bird.__main__ import main  # noqa: E402


def test_check_counts_rule_match_as_failure_and_keeps_wrong(tmp_path: Path) -> None:
    summary = heldout.run_check(tmp_path)
    assert summary["wrong"] == 2
    assert summary["official_wrong"] == 2
    assert summary["heldout_correct"] == 2
    assert summary["rule_fail"] == 1
    assert summary["official_correct"] == 3
    assert summary["abstain"] == 3
    assert summary["databases"] == 4
    assert summary["stages"]["schema_retrieval"] == 1
    assert summary["stages"]["ontology_mapping"] == 1
    assert summary["stages"]["sql_generation"] == 1
    assert summary["stages"]["self_correction"] == 1
    assert summary["stages"]["rule_served"] == 1
    assert summary["pass"] is False
    text = heldout.format_heldout(summary)
    assert text.startswith("WRONG=2")
    assert "PASS" not in text


def test_flag_off_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(run.FLAG, raising=False)
    out = tmp_path / "held.jsonl"
    code = main(
        [
            "heldout",
            "--dataset",
            "bird",
            "--provider",
            "stub",
            "--questions",
            str(tmp_path / "missing.json"),
            "--db-root",
            str(tmp_path),
            "--out",
            str(out),
        ]
    )
    assert code == run.EXIT_CONFIG
    assert "No ask was sent" in capsys.readouterr().out
    assert not out.exists()
    assert run.surface() == {"prompt": None, "envelope": None, "route": None}


def test_live_without_key_is_config_not_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(run.FLAG, "1")
    monkeypatch.setenv("OPENVAULT_URL", "http://ov.example")
    monkeypatch.delenv("OPENVAULT_API_KEY", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    out = tmp_path / "live.jsonl"
    code = main(
        [
            "heldout",
            "--dataset",
            "bird",
            "--provider",
            "live",
            "--questions",
            str(tmp_path / "q.json"),
            "--db-root",
            str(tmp_path),
            "--out",
            str(out),
            "--limit",
            "1",
        ]
    )
    captured = capsys.readouterr().out
    assert code == run.EXIT_CONFIG
    assert "ov_ key" in captured
    assert "Not a live pass" in captured
    assert "PASS" not in captured
    assert not out.exists()
    assert heldout.live_block({"OPENVAULT_URL": "", "OPENVAULT_API_KEY": ""}) == "openvault_url"


def test_compact_schema_omits_cell_values(tmp_path: Path) -> None:
    path = tmp_path / "shop.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE items (n INTEGER, name TEXT)")
    con.execute("INSERT INTO items VALUES (1, 'RowTokenZZ')")
    con.commit()
    con.close()
    block = gold.schema_block(path, sample_rows=0)
    assert "RowTokenZZ" not in block
    assert "sample " not in block
    assert "CREATE TABLE" in block
    body = baseline.baseline_body("count rows", block)
    assert body["role"] == "sql"
    assert "provider" not in body
    assert "model" not in body
    with pytest.raises(baseline.ModelKeyRefused):
        baseline.reject_model_keys({"role": "sql", "provider": "claude"})


def test_spider_sqlite_slice_skips_cloud_and_missing_gold(tmp_path: Path) -> None:
    root = tmp_path / "spider2-lite"
    shop = root / "resource" / "databases" / "sqlite" / "shop"
    shop.mkdir(parents=True)
    con = sqlite3.connect(shop / "shop.sqlite")
    con.execute("CREATE TABLE items (n INTEGER)")
    con.execute("INSERT INTO items VALUES (1)")
    con.commit()
    con.close()
    (root / "resource" / "databases" / "sqlite" / "yard").mkdir(parents=True)
    gold_dir = root / "evaluation_suite" / "gold" / "sql"
    gold_dir.mkdir(parents=True)
    (gold_dir / "local001.sql").write_text("SELECT COUNT(*) FROM items\n", encoding="utf-8")
    rows = [
        {"instance_id": "local001", "db": "shop", "question": "how many items"},
        {"instance_id": "local002", "db": "yard", "question": "list plots"},
        {"instance_id": "bq001", "db": "ga4", "question": "traffic last month"},
        {"instance_id": "../x", "db": "shop", "question": "bad id"},
    ]
    (root / "spider2-lite.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    loaded = heldout.load_spider(root)
    assert loaded.sqlite_questions == 2
    assert loaded.excluded_cloud == 2
    assert loaded.gold_absent == 1
    assert loaded.databases == 2
    assert heldout.SPIDER_LICENCE == "MIT"
    out = tmp_path / "spider.jsonl"
    produced = heldout.run_heldout(
        loaded.questions,
        dataset="spider2",
        provider="stub",
        db_root=loaded.db_root,
        out=out,
    )
    summary = heldout.summarize(produced)
    assert summary["wrong"] == 0
    assert summary["heldout_correct"] == 0
    assert summary["abstain"] == 1
    assert summary["unscored"] == 1
    assert summary["pass"] is False
    blob = out.read_text(encoding="utf-8")
    assert "how many items" not in blob
    assert "SELECT" not in blob
    assert "traffic" not in blob


def test_memory_is_context_and_budget_is_the_daily_cap(tmp_path: Path) -> None:
    summary = heldout_memory.run_compare_check(tmp_path)
    assert summary["equal_wrong"] == 0
    assert summary["replay_treatment"] == "abstain"
    assert summary["clarify_treatment"] == "abstain"
    assert summary["control_wrong_on_clarify_case"] == 1
    assert summary["budget_default"] == 500
    assert summary["calls_on_cap_2"] == 2
    assert heldout_memory.memory_on({"DMS_HELDOUT_MEMORY": "1"})
    assert not heldout_memory.memory_on({"DMS_HELDOUT_MEMORY": "true"})
    assert not heldout_memory.memory_on({})


def test_compare_without_key_is_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(run.FLAG, "1")
    monkeypatch.setenv(heldout_memory.FLAG, "1")
    monkeypatch.setenv("OPENVAULT_URL", "http://ov.example")
    monkeypatch.delenv("OPENVAULT_API_KEY", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    sessions = tmp_path / "sessions.jsonl"
    sessions.write_text(
        '{"kind":"confirmed","db_id":"archive","question":"count items","sql":"SELECT 1"}\n',
        encoding="utf-8",
    )
    code = main(
        [
            "compare",
            "--dataset",
            "bird",
            "--sessions",
            str(sessions),
            "--questions",
            str(tmp_path / "q.json"),
            "--db-root",
            str(tmp_path),
            "--out",
            str(tmp_path / "out.jsonl"),
            "--limit",
            "1",
        ]
    )
    captured = capsys.readouterr().out
    assert code == run.EXIT_CONFIG
    assert "ov_ key" in captured
    assert "PASS" not in captured
    assert "Not a live pass" in captured


def test_same_database_sessions_are_not_retrieved() -> None:
    memory = {
        "examples": [
            {"question": "count items", "sql": "SELECT 1", "db_id": "shop", "note": ""},
        ],
        "notes": [],
    }
    pack = heldout_memory.retrieve(memory, "count items", exclude_db="shop")
    assert pack["examples"] == []


def test_stub_abstain_ignores_question_text(tmp_path: Path) -> None:
    db = tmp_path / "db"
    path = db / "shop" / "shop.sqlite"
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE items (n INTEGER)")
    con.execute("INSERT INTO items VALUES (1)")
    con.commit()
    con.close()
    seen: list[str] = []

    def post(url: str, body: dict[str, object], headers: dict[str, str]) -> tuple[int, dict]:
        del url, headers
        seen.append(str(body.get("question")))
        return heldout.abstain_post("http://stub", {}, {})

    questions = [
        {"question_id": "a", "db_id": "shop", "question": "alpha wording", "SQL": "SELECT 1"},
        {"question_id": "b", "db_id": "shop", "question": "beta wording", "SQL": "SELECT 1"},
    ]
    out = tmp_path / "stub.jsonl"
    rows = heldout.run_heldout(
        questions,
        dataset="bird",
        provider="stub",
        db_root=db,
        out=out,
        post=post,
    )
    assert seen == ["alpha wording", "beta wording"]
    assert {row["heldout"] for row in rows} == {"abstain"}
    assert heldout.summarize(rows)["wrong"] == 0
