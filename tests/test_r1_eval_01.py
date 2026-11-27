"""R1-EVAL-01. CLARIFY is a bucket. The ambiguity pack is data. BIRD stays outside.

No question-text rule. A clarify is the route or the clarify block. The founder
file, not Python, says which ids may clarify or answer.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))
sys.path.insert(0, str(ROOT / "scripts"))

import ambig_pack  # noqa: E402
import eval_all  # noqa: E402
import grade52  # noqa: E402

FOUNDER = ROOT / "tests" / "fixtures" / "curated_ceo" / "founder_f2.yaml"


def test_founder_file_is_data_and_only_the_one_expect() -> None:
    rulings = grade52.load_founder_rulings(FOUNDER)
    assert rulings == {}
    pack = grade52._load_yaml(grade52.PACK_PATH)
    ids = {str(row["id"]) for row in pack["questions"]}
    assert set(rulings) <= ids


def test_clarify_is_a_bucket_and_only_the_ruling_is_a_pass() -> None:
    ruled = grade52.grade_case(
        gold=[{"n": 1}],
        gold_sql=None,
        env={"badge": "ABSTAIN", "route": "needs_clarification", "abstained": True},
        served_rows=[],
        as_of="2026-10-09",
        expect=grade52.EXPECT_CLARIFY_OR_ANSWER,
    )
    plain = grade52.grade_case(
        gold=[{"n": 1}],
        gold_sql=None,
        env={"badge": "ABSTAIN", "route": "needs_clarification", "abstained": True},
        served_rows=[],
        as_of="2026-10-09",
    )
    assert ruled["bucket"] == "CLARIFY" and ruled["expect_met"] is True
    assert plain["bucket"] == "CLARIFY" and "expect_met" not in plain
    counts = grade52.tally([ruled, plain])
    assert counts["clarify"] == 2
    assert counts["abstain"] == 0


def test_question_text_does_not_make_a_clarify() -> None:
    case = grade52.grade_case(
        gold=[{"n": 1}],
        gold_sql=None,
        env={
            "badge": "ABSTAIN",
            "route": "abstain",
            "abstained": True,
            "text": "please clarify spend by country",
            "question": "What is our total spend by supplier country?",
        },
        served_rows=[],
        as_of="2026-10-09",
        expect=grade52.EXPECT_CLARIFY_OR_ANSWER,
    )
    assert case["bucket"] == "ABSTAIN"
    assert case["expect_met"] is False


def test_a_gold_match_meets_clarify_or_answer() -> None:
    from dms_executor.envelope import build_audit_receipt

    rows = [{"n": 1}]
    sql = "SELECT 1 AS n"
    badge = "L1_GOVERNED_METRIC"
    env = {
        "badge": badge,
        "route": "governed_metric",
        "abstained": False,
        "rows": rows,
        "values": [{"label": "row_count", "value": 1}],
        "sql_used": sql,
        "audit_id": "synthetic",
        "as_of": "2026-01-01T00:00:00Z",
        "audit_receipt": build_audit_receipt(
            abstained=False, badge=badge, rows=rows, sql_used=sql, assumptions=[]
        ),
    }
    case = grade52.grade_case(
        gold=rows,
        gold_sql=None,
        env=env,
        served_rows=rows,
        as_of="2026-10-09",
        expect=grade52.EXPECT_CLARIFY_OR_ANSWER,
    )
    assert case["bucket"] == "CORRECT"
    assert case["expect_met"] is True


def test_hop_stamp_is_the_header_not_served_model() -> None:
    stamped = grade52.hop_model_from(
        {"headers": {"x-openvault-served-model": "openai/gpt-oss-120b"}}
    )
    bare = grade52.hop_model_from({"served_model": "openai/gpt-oss-120b"})
    assert stamped == "openai/gpt-oss-120b"
    assert bare is None
    split = grade52.pin_split(
        [
            {
                "answered": True,
                "hop_model": stamped,
                "model_requested": stamped,
            },
            {"answered": True, "served_model": stamped, "hop_model": ""},
        ]
    )
    assert split == {"all_served": 2, "scored_on_pin": 1, "not_scored_on_pin": 1}


def test_ambig_pack_has_the_three_kinds() -> None:
    errors = ambig_pack.validate_pack()
    assert errors == []
    cases = ambig_pack.load_cases()
    assert len(cases) >= 40
    counts = ambig_pack.counts_by_kind(cases)
    assert set(counts) == {"ambiguous", "answerable", "unanswerable"}
    assert all(count > 0 for count in counts.values())


def test_answerable_gold_sql_runs_on_the_demo_warehouse(tmp_path: Path) -> None:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    database = tmp_path / "warehouse.duckdb"
    ensure_demo_warehouse(database)
    import duckdb

    connection = duckdb.connect(str(database))
    try:
        for case in ambig_pack.load_cases():
            if case["kind"] != "answerable":
                continue
            cursor = connection.execute(str(case["gold_sql"]))
            got = cursor.fetchall()
            assert len(got) >= 1, case["id"]
    finally:
        connection.close()


def test_clarify_precision_and_recall() -> None:
    scores = ambig_pack.clarify_scores(
        [
            {"expect": "clarify", "predicted": "clarify"},
            {"expect": "clarify", "predicted": "abstain"},
            {"expect": "answer", "predicted": "clarify"},
            {"expect": "abstain", "predicted": "abstain"},
        ]
    )
    assert scores["precision"] == 0.5
    assert scores["recall"] == 0.5


def test_bird_checksum_refuses_a_file_inside_the_repo(tmp_path: Path) -> None:
    inside = ROOT / "tests" / "fixtures" / "ambig_pack" / "not_a_corpus.json"
    inside.write_text("{}\n", encoding="utf-8")
    try:
        with pytest.raises(SystemExit, match="outside the repo"):
            eval_all.verify_corpus(inside)
    finally:
        inside.unlink()
    corpus = tmp_path / "mini_dev.json"
    corpus.write_bytes(b"[]")
    digest = eval_all.sha256_file(corpus)
    pin = tmp_path / "pin.sha256"
    pin.write_text("UNPINNED\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="UNPINNED"):
        eval_all.verify_corpus(corpus, pin)
    pin.write_text(digest + "\n", encoding="utf-8")
    assert eval_all.verify_corpus(corpus, pin) == digest
    pin.write_text("0" * 64 + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="mismatch"):
        eval_all.verify_corpus(corpus, pin)


def test_sqlite_ontology_has_columns_and_no_row_values(tmp_path: Path) -> None:
    path = tmp_path / "one.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE widgets (id INTEGER, name TEXT)")
    connection.execute("INSERT INTO widgets VALUES (1, 'secret-row')")
    connection.commit()
    connection.close()
    ontology = eval_all.ontology_from_sqlite(path)
    blob = json.dumps(ontology)
    assert ontology["tables"] == [{"name": "widgets", "columns": ["id", "name"]}]
    assert "secret-row" not in blob


def test_bird_outcome_splits_clarify_reconfirm_and_ex() -> None:
    clarify = eval_all.bird_outcome(
        {"badge": "ABSTAIN", "route": "needs_clarification", "abstained": True, "rows": []},
        [{"n": 1}],
    )
    reconfirm = eval_all.bird_outcome(
        {
            "badge": "ABSTAIN",
            "abstained": True,
            "rows": [],
            "reconfirm": {"suggested_question": "use stock value?"},
        },
        [{"n": 1}],
    )
    matched = eval_all.bird_outcome(
        {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"n": 1}]},
        [{"n": 1}],
    )
    wrong = eval_all.bird_outcome(
        {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"n": 2}]},
        [{"n": 1}],
    )
    assert (clarify, reconfirm, matched, wrong) == ("clarify", "reconfirm", "EX", "WRONG")
    tally = eval_all.bird_tally(
        [
            {"dialect": "postgresql", "outcome": matched},
            {"dialect": "sqlite", "outcome": wrong},
        ]
    )
    assert tally["overall"]["EX"] == 1
    assert tally["overall"]["WRONG"] == 1
    assert tally["by_dialect"]["postgresql"]["ex_rate"] == 1.0
    assert tally["by_dialect"]["sqlite"]["WRONG"] == 1


def test_assemble_marks_pack_h_and_bird_unmeasured(tmp_path: Path) -> None:
    case = {
        "id": "q",
        "bucket": "CORRECT",
        "served_route": "rule",
        "answered": True,
        "reason": "match",
    }
    report = {
        "mode": "stub-exec",
        "dms_sha": "a" * 40,
        "n": 1,
        "correct": 1,
        "wrong": 0,
        "refusal_wrong": 0,
        "reconfirm": 0,
        "clarify": 0,
        "abstain": 0,
        "refusal_ok": 0,
        "gold_broken": 0,
        "empty_gold": 0,
        "cases": [case],
    }
    body = eval_all.assemble(report, report, elapsed_s=1.0)
    assert body["serving_flag"] is None
    assert body["pack_h"]["status"] == "unmeasured (stub)"
    assert body["bird"]["ex_overall"] == "unmeasured (stub)"
    assert "unmeasured (stub)" in body["public_target"]
    assert body["pack_52"]["flag_off"]["pin_status"] == "unmeasured (stub)"
    md_path, json_path = eval_all.write_outputs(body, tmp_path)
    assert md_path.is_file() and json_path.is_file()
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["ambig"]["n"] >= 40
    assert saved["ambig"]["schema"] == "ok"


def test_there_is_no_serving_flag() -> None:
    assert eval_all.SERVING_FLAG is None
