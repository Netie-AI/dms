"""HELDOUT-PACK-D hook: fail-closed preflight and row-judged scoring of a frozen pack.

Synthetic pack built in tmp_path. No real pack, no network, no keys, no Cortex.
CI fixtures, not live. No scored round of the held-out pack is run here.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import duckdb
import pytest
from dms_executor.envelope import build_answer_envelope

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_heldout import (  # noqa: E402
    EXIT_CONFIG,
    EXIT_PASS,
    PackError,
    Scored,
    db_fingerprint,
    main,
    manifest_root,
    score,
    summary_lines,
    verify_pack,
    wrong_upper_pct,
)

SUM_SQL = "SELECT region, ROUND(SUM(amount), 2) AS total FROM sales GROUP BY region"
TOP_SQL = "SELECT region FROM sales GROUP BY region ORDER BY SUM(amount) DESC LIMIT 1"


def _write(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _seal(pack: Path) -> str:
    files = {}
    for p in sorted(pack.rglob("*")):
        rel = p.relative_to(pack).as_posix()
        if p.is_file() and rel != "MANIFEST.json":
            files[rel] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    root = manifest_root(files)
    _write(pack / "MANIFEST.json", {"files": files, "root_sha256": root})
    return root


def _make_pack(
    tmp_path: Path, *, scan: str = "PASS", fingerprint: bool = True
) -> tuple[Path, Path, str]:
    db = tmp_path / "oracle.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE sales(region VARCHAR, amount DECIMAL(18,2))")
    con.execute("INSERT INTO sales VALUES ('North', 10.10), ('North', 5.00), ('South', 7.25)")
    # No gold query reads this table: only the fingerprint can see an edit to it.
    con.execute("CREATE TABLE notes(id INTEGER, body VARCHAR, noted DATE)")
    con.execute("INSERT INTO notes VALUES (1, 'a', DATE '2025-01-02'), (2, NULL, NULL)")
    con.close()
    pack = tmp_path / "pack_repo"
    questions = [
        {
            "id": "HO-0001",
            "question": "Total sales by region?",
            "expect": "l0",
            "difficulty": "easy",
            "gold_sql": SUM_SQL,
        },
        {
            "id": "HO-0002",
            "question": "Top region by sales?",
            "expect": "l0",
            "difficulty": "medium",
            "gold_sql": TOP_SQL,
        },
        {
            "id": "HO-0003",
            "question": "What will sales be next year?",
            "expect": "refuse",
            "refuse_reason": "missing_data",
        },
    ]
    gold = {
        "HO-0001": {
            "columns": ["region", "total"],
            "rows": [["North", "15.10"], ["South", "7.25"]],
        },
        "HO-0002": {"columns": ["region"], "rows": [["North"]]},
    }
    _write(pack / "pack" / "questions.json", {"questions": questions})
    _write(pack / "pack" / "gold_results.json", gold)
    _write(pack / "scan" / "result.json", {"verdict": scan})
    if fingerprint:
        _write(pack / "db" / "fingerprint.json", {"tables": db_fingerprint(db)})
    return pack, db, _seal(pack)


def _args(pack: Path, db: Path, root: str) -> list[str]:
    return ["--self-check", "--pack", str(pack), "--oracle-db", str(db), "--expect-root", root]


def test_cli_self_check_runs_without_pythonpath(tmp_path: Path) -> None:
    """The real entry point, in a clean env: oracle imports must resolve on their own."""
    pack, db, root = _make_pack(tmp_path)
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATHEXT", "HOME", "TEMP", "TMP")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    script = Path(__file__).resolve().parents[1] / "scripts" / "score_heldout.py"
    proc = subprocess.run(
        [sys.executable, str(script), *_args(pack, db, root)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == EXIT_PASS, proc.stdout + proc.stderr
    assert "VERDICT: PACK OK" in proc.stdout


def test_self_check_passes_on_a_sealed_pack(tmp_path: Path, capsys) -> None:
    pack, db, root = _make_pack(tmp_path)
    assert main(_args(pack, db, root)) == EXIT_PASS
    out = capsys.readouterr().out
    assert "cases=3 answerable=2 refuse=1" in out
    assert "NOT pack D" in out  # explicit --expect-root is always labelled
    assert "Not a score" in out


def test_tampered_question_is_refused(tmp_path: Path, capsys) -> None:
    pack, db, root = _make_pack(tmp_path)
    q = pack / "pack" / "questions.json"
    q.write_text(q.read_text(encoding="utf-8").replace("next year", "this year"), encoding="utf-8")
    assert main(_args(pack, db, root)) == EXIT_CONFIG
    assert "manifest mismatch" in capsys.readouterr().out


def test_unpinned_root_is_refused_without_expect_root(tmp_path: Path) -> None:
    pack, _db, _root = _make_pack(tmp_path)
    with pytest.raises(PackError, match="not the pinned root"):
        verify_pack(pack)


def test_failed_scan_blocks_the_pack(tmp_path: Path, capsys) -> None:
    pack, db, root = _make_pack(tmp_path, scan="FAIL")
    assert main(_args(pack, db, root)) == EXIT_CONFIG
    assert "scan verdict" in capsys.readouterr().out


def test_drifted_oracle_is_config_not_a_score(tmp_path: Path, capsys) -> None:
    pack, db, root = _make_pack(tmp_path)
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO sales VALUES ('South', 100.00)")
    con.close()
    assert main(_args(pack, db, root)) == EXIT_CONFIG
    assert "oracle drift" in capsys.readouterr().out


def test_edit_no_gold_query_reads_is_caught_by_the_fingerprint(tmp_path: Path, capsys) -> None:
    pack, db, root = _make_pack(tmp_path)
    con = duckdb.connect(str(db))
    con.execute("UPDATE notes SET body = 'tuned' WHERE id = 1")
    con.close()
    assert main(_args(pack, db, root)) == EXIT_CONFIG
    out = capsys.readouterr().out
    assert "oracle fingerprint differs on 1 table(s): notes" in out
    assert "oracle drift" not in out  # every gold query still reproduces


def test_an_extra_table_in_the_oracle_is_caught(tmp_path: Path, capsys) -> None:
    pack, db, root = _make_pack(tmp_path)
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE answers(id INTEGER)")
    con.close()
    assert main(_args(pack, db, root)) == EXIT_CONFIG
    assert "extra:answers" in capsys.readouterr().out


def test_a_pack_without_a_fingerprint_is_refused(tmp_path: Path) -> None:
    pack, _db, root = _make_pack(tmp_path, fingerprint=False)
    with pytest.raises(PackError, match=r"does not cover db/fingerprint\.json"):
        verify_pack(pack, expect_root=root)


def test_pack_inside_the_dms_tree_is_refused(tmp_path: Path) -> None:
    inside = Path(__file__).resolve().parents[1] / "tests"
    with pytest.raises(PackError, match="inside the dms tree"):
        verify_pack(inside, expect_root="x")


def _cases(pack: Path) -> list[dict]:
    return json.loads((pack / "pack" / "questions.json").read_text(encoding="utf-8"))["questions"]


def _answer(badge: str, rows: list[dict]) -> dict:
    """A valid served envelope, built by DMS's own constructor."""
    return build_answer_envelope(
        answer_id="ans_t",
        text="The rows are listed below.",
        badge=badge,
        values=[{"label": "rows", "value": "listed"}],
        sql_used="SELECT region FROM sales",
        rows=rows,
        as_of="2026-10-01T00:00:00Z",
        audit_id="aud_t",
    )


def _abstain() -> dict:
    return build_answer_envelope(
        answer_id="ans_a",
        text="I cannot answer that from this Space.",
        badge="ABSTAIN",
        rows=[],
        as_of="2026-10-01T00:00:00Z",
    )


def test_score_judges_rows_not_badges(tmp_path: Path) -> None:
    pack, db, _root = _make_pack(tmp_path)
    answers = {
        # right rows, column order swapped: rows match, non-L0 badge -> LAYER
        "Total sales by region?": _answer(
            "L2_VALIDATED", [{"total": 7.25, "region": "South"}, {"total": 15.1, "region": "North"}]
        ),
        # green badge, wrong rows: WRONG
        "Top region by sales?": _answer("L0_CERTIFIED", [{"region": "South"}]),
        # confident answer to a refusal question: WRONG
        "What will sales be next year?": _answer("L0_CERTIFIED", [{"region": "North"}]),
    }
    got = score(_cases(pack), ask=answers.__getitem__, oracle_db=db)
    assert got.status == "ok"
    assert got.tallies == Counter({"LAYER": 1, "WRONG": 2})
    verdicts = {r["id"]: r["verdict"] for r in got.cases}
    assert verdicts == {"HO-0001": "LAYER", "HO-0002": "WRONG", "HO-0003": "WRONG"}
    assert got.groups["refuse:missing_data"]["WRONG"] == 1
    assert "refuse" not in got.groups  # never pooled across reasons
    assert got.badges == Counter({"L0_CERTIFIED": 2, "L2_VALIDATED": 1})
    assert not got.envelope


def test_doubled_row_under_a_green_badge_is_wrong(tmp_path: Path) -> None:
    pack, db, _root = _make_pack(tmp_path)
    doubled = _answer("L0_CERTIFIED", [{"region": "North"}, {"region": "North"}])
    got = score(_cases(pack)[1:2], ask=lambda _q: doubled, oracle_db=db)
    assert got.tallies == Counter({"WRONG": 1})


def test_valid_abstention_is_abstain_never_ok(tmp_path: Path) -> None:
    pack, db, _root = _make_pack(tmp_path)
    got = score(_cases(pack), ask=lambda _q: _abstain(), oracle_db=db)
    assert got.tallies == Counter({"ABSTAIN": 3})
    assert not got.envelope


def test_green_badge_on_an_abstention_is_invalid_not_abstain(tmp_path: Path) -> None:
    """Rule 10a P0: abstention prose under a confident badge must surface, not hide."""
    pack, db, _root = _make_pack(tmp_path)
    mislabelled = {**_abstain(), "badge": "L2_VALIDATED"}
    got = score(_cases(pack), ask=lambda _q: mislabelled, oracle_db=db)
    assert got.tallies == Counter({"INVALID": 3})
    assert got.envelope == Counter({"E1": 3})
    assert all(c["reason"].startswith("envelope:E1:") for c in got.cases)


def test_right_rows_with_a_broken_envelope_are_invalid(tmp_path: Path) -> None:
    pack, db, _root = _make_pack(tmp_path)
    no_sql = {**_answer("L0_CERTIFIED", [{"region": "North"}]), "sql_used": None}
    got = score(_cases(pack)[1:2], ask=lambda _q: no_sql, oracle_db=db)
    assert got.tallies == Counter({"INVALID": 1})
    assert "E3" in got.envelope or "E13" in got.envelope


def test_a_wrong_answer_is_never_downgraded_by_an_envelope_break(tmp_path: Path) -> None:
    pack, db, _root = _make_pack(tmp_path)
    wrong_and_broken = {**_answer("L0_CERTIFIED", [{"region": "South"}]), "sql_used": None}
    got = score(_cases(pack)[1:2], ask=lambda _q: wrong_and_broken, oracle_db=db)
    assert got.tallies == Counter({"WRONG": 1})
    assert sum(got.envelope.values()) == 1


def test_summary_prints_n_counts_bound_badges_and_envelope() -> None:
    scored = Scored(
        tallies=Counter({"OK": 40, "LAYER": 10, "ABSTAIN": 5}),
        badges=Counter({"L0_CERTIFIED": 40, "L2_VALIDATED": 10, "ABSTAIN": 5}),
    )
    lines = summary_lines(scored, 55)
    assert lines[0].startswith("n=55 OK=40 LAYER=10 ABSTAIN=5 WRONG=0")
    assert "WRONG=0 of answered=50" in lines[1]
    assert "bound about 6.00 pct" in lines[1]
    assert "badges served: ABSTAIN=5 L0_CERTIFIED=40 L2_VALIDATED=10" in lines[2]
    assert lines[3].endswith("envelope violations (assert_envelope_valid): none")


def test_pack_d_root_is_pinned_not_a_placeholder() -> None:
    from score_heldout import PACK_D_ROOT_SHA256

    assert len(PACK_D_ROOT_SHA256) == 64
    int(PACK_D_ROOT_SHA256, 16)  # raises on anything but hex


def test_wrong_upper_bound() -> None:
    assert wrong_upper_pct(0, 0) is None
    assert abs(wrong_upper_pct(0, 300) - 0.994) < 0.01  # rule of three, about 1 pct
    assert wrong_upper_pct(3, 100) > 3.0
