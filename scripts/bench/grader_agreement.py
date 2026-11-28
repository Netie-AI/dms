"""Grader agreement on the 52-question pack. Offline replay, diagnostic, a local claim.

Three row-match labels for every recorded served case that has a certified oracle:
  A  grade52.compare_rows (scripts/verify/grade52.py:567): numbers within 0.005 after
     ROUND_HALF_UP to gold's shorter decimal count (cells_equal :350); columns map by
     name then value signature, extra served columns projected away, ORDER BY honoured.
  B  score_curated.judge_detailed (scripts/score_curated.py:781): rows compare in
     oracle_row_match.rows_mismatch_reason (:145), ROUND_HALF_UP at the oracle SQL's
     ROUND scale (:68) else exact Decimal, column names ignored, order only with LIMIT;
     then min_rows and the badge. judge_envelope_detailed (:870) is recorded as B_env.
  C  the dms#299 spec, implemented here only: sha256 over the multiset of rows, each row
     the multiset of its cells, numbers quantized to 2 dp ROUND_HALF_EVEN. Column names,
     column order and row order are ignored; extra served columns are not projected away.
Gold is grade52's own: fresh ensure_demo_warehouse file per run, certified oracle executed
read-only, $as_of bound (_open_warehouse :968, _certified_sql :947, _execute :959). B then
re-executes that oracle itself through run_oracle_select on the same file, as it always
does. Everything runs twice, fresh warehouse each time; a label that moves is reported.

Usage (cwd = repo root; no network, no model, no live service):
    python scripts/bench/grader_agreement.py --out DIR [--fixture PATH ...]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for _rel in ("scripts", "scripts/verify"):
    _entry = str(ROOT / _rel)
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import grade52  # noqa: E402
import oracle_row_match as orm  # noqa: E402
import score_curated  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "ask_guide"
FIXTURE_NAMES = (
    "copy_only_57d85c52.jsonl", "flag_off_52_exec_main.json", "flag_off_52_f9ffc3e1.json",
    "cortex_responses_52.json",
)
FIXTURES = tuple(FIXTURE_DIR / name for name in FIXTURE_NAMES)
TWO_DP = Decimal("0.01")
B_MATCH = frozenset({"OK", "LAYER"})
LABEL_KEYS = ("A", "B", "B_env", "C")
LIST_KEYS = ("order_keys", "gold_cols", "served_cols", "flipped_cells", "gold_rows", "served_rows")
CSV_COLUMNS = (
    "fixture", "id", "served_n", "gold_n", "A", "A_reason", "B", "B_reason", "B_env", "C",
    "A_match", "B_match", "C_match", "round_scale", *LIST_KEYS, "note", "run2_same",
)
PAIRS = {
    "A!=B": ("A_match", "B_match"),
    "A!=C": ("A_match", "C_match"),
    "B!=C": ("B_match", "C_match"),
}
Rows = list[dict[str, Any]]
Case = dict[str, Any]


@dataclass(frozen=True)
class Gold:
    as_of: str
    db_path: Path
    sql: dict[str, str]
    rows: dict[str, Rows]
    questions: dict[str, dict[str, Any]]


def build_gold(folder: Path) -> Gold:
    """grade52's gold builder, reused: fresh warehouse, read-only oracle, $as_of bound."""
    grade52._bootstrap()
    pack = grade52._load_yaml(grade52.PACK_PATH)
    oracle_doc = grade52._load_yaml(grade52.ORACLE_PATH).get("oracles") or {}
    con, as_of, db_path = grade52._open_warehouse(folder)
    sql_by_id: dict[str, str] = {}
    rows_by_id: dict[str, Rows] = {}
    questions: dict[str, dict[str, Any]] = {}
    try:
        for question in pack["questions"]:
            qid = str(question["id"])
            questions[qid] = question
            oracle = oracle_doc.get(qid)
            node = oracle if isinstance(oracle, Mapping) else None
            if grade52._is_trap(question, node):
                continue
            sql = grade52._certified_sql(node)
            if sql is None:
                continue
            sql_by_id[qid] = sql
            rows_by_id[qid] = grade52._execute(con, sql, as_of)
    finally:
        con.close()
    return Gold(as_of, db_path, sql_by_id, rows_by_id, questions)


def c_cell(value: Any) -> str:
    """dms#299 normalisation: numbers to 2 dp ROUND_HALF_EVEN, anything else verbatim."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, (int, float, Decimal)):
        num = Decimal(str(value))
        if num.is_finite():
            num = num.quantize(TWO_DP, rounding=ROUND_HALF_EVEN)
        return f"num:{num}"
    return f"str:{value}"


def c_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    """Multiset of rows; each row the multiset of its cells. Names and order ignored."""
    canon = sorted(sorted(c_cell(value) for value in row.values()) for row in rows)
    return hashlib.sha256(json.dumps(canon).encode("utf-8")).hexdigest()


def _numeric(values: Iterable[Any]) -> list[Any]:
    keyed = [(num, v) for v in values if (num := grade52._as_float(v)) is not None]
    return [value for _num, value in sorted(keyed, key=lambda pair: pair[0])]


def flipped_cells(gold_rows: Rows, served: Rows, sql: str) -> list[str]:
    """Numeric cells, paired per gold column after sorting, where the three cell rules split."""
    scale = orm.numeric_scale_from_sql(sql)
    served_cols = {col.casefold(): col for row in served for col in row}
    out: list[str] = []
    for col in gold_rows[0] if gold_rows else {}:
        s_col = served_cols.get(col.casefold())
        if s_col is None:
            continue
        gold_vals = _numeric(row.get(col) for row in gold_rows)
        served_vals = _numeric(row.get(s_col) for row in served)
        for g, s in zip(gold_vals, served_vals, strict=False):
            a, b = grade52.cells_equal(g, s), orm.cells_equal(g, s, scale)
            c = c_cell(g) == c_cell(s)
            if not a == b == c:
                out.append(f"{col}: gold={g!r} served={s!r} A={a} B={b} C={c}")
    return out


def grade_one(fixture: str, qid: str, env: Case, served: Rows, gold: Gold, oracles: Any) -> Case:
    sql, gold_rows, question = gold.sql[qid], gold.rows[qid], gold.questions[qid]
    keys = grade52.order_keys(sql)
    a_bucket, a_reason = grade52.compare_rows(
        gold_rows, served, ordered=keys is not None, order_keys=keys
    )
    kw: dict[str, Any] = {"oracle_db": gold.db_path, "oracles": oracles, "as_of": gold.as_of}
    b = score_curated.judge_detailed(question, env, **kw)
    b_env = score_curated.judge_envelope_detailed(question, env, **kw)
    c_label = "MATCH" if c_hash(gold_rows) == c_hash(served) else "DIFF"
    gold_cols = list(gold_rows[0]) if gold_rows else []
    served_cols = list(served[0]) if served else []
    gold_fold = {col.casefold() for col in gold_cols}
    served_fold = {col.casefold() for col in served_cols}
    extra = [col for col in served_cols if col.casefold() not in gold_fold]
    missing = [col for col in gold_cols if col.casefold() not in served_fold]
    note = [f"A: {a_reason}", f"B: {b.reason}" if b.reason else ""]
    note.append(f"extra served cols={extra}" if extra else "")
    note.append(f"gold cols absent={missing}" if missing else "")
    return {
        "fixture": fixture, "id": qid, "served_n": len(served), "gold_n": len(gold_rows),
        "A": a_bucket, "A_reason": a_reason, "B": b.verdict, "B_reason": b.reason,
        "B_env": b_env.verdict, "C": c_label,
        "A_match": a_bucket == grade52.BUCKET_CORRECT, "B_match": b.verdict in B_MATCH,
        "C_match": c_label == "MATCH", "round_scale": orm.numeric_scale_from_sql(sql),
        "order_keys": keys, "gold_cols": gold_cols, "served_cols": served_cols,
        "flipped_cells": flipped_cells(gold_rows, served, sql),
        "gold_rows": gold_rows, "served_rows": served,
        "note": "; ".join(part for part in note if part),
    }


def _cortex_shape(path: Path) -> str | None:
    """Why a Cortex-response capture cannot be graded, or None when it is not that shape."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    by_id = data.get("by_id") if isinstance(data, dict) else None
    if not isinstance(by_id, dict):
        return None
    stub = sum(
        1 for item in by_id.values()
        if grade52._is_stub_row((item.get("submit") or {}).get("rows") or [])
    )
    return (
        "Cortex response shape (by_id -> ask/submit/ledger/insights), not a DMS envelope; "
        f"grade52 has no loader for it; {stub}/{len(by_id)} submit rows are the one-cell n=1 "
        "stub, so nothing served is gradable"
    )


def load_fixture(path: Path) -> tuple[list[Case] | None, str]:
    """grade52's own loader. (records, note) or (None, why this file cannot be graded)."""
    why = _cortex_shape(path)
    try:
        records, meta = grade52.load_envelopes_meta(path)
    except SystemExit as exc:
        refusal = f"grade52.load_envelopes_meta refuses it: {exc}"
        return None, f"{why}; {refusal}" if why else refusal
    if why:
        return None, why
    sha = meta.get("dms_sha") or "none declared"
    return records, f"dms_sha={sha}; submit-stub={grade52._is_submit_stub(records)}"


def run_once(fixtures: Sequence[Path], folder: Path) -> tuple[list[Case], list[str], Gold]:
    gold = build_gold(folder)
    oracles = score_curated.load_oracles()
    cases: list[Case] = []
    notes: list[str] = []
    for path in fixtures:
        records, note = load_fixture(path)
        if records is None:
            notes.append(f"{path.name}: NOT GRADED. {note}")
            continue
        counts = {"graded": 0, "abstained_or_no_rows": 0, "served_without_certified_oracle": 0}
        for record in records:
            qid, env = str(record["id"]), record["env"]
            served = grade52._rows_of(env.get("rows"))
            if grade52._is_abstain(env) or not served:
                counts["abstained_or_no_rows"] += 1
            elif qid not in gold.sql:
                counts["served_without_certified_oracle"] += 1
            else:
                cases.append(grade_one(path.name, qid, env, served, gold, oracles))
                counts["graded"] += 1
        tail = ", ".join(f"{key}={value}" for key, value in counts.items())
        notes.append(f"{path.name}: {note}; {tail}")
    return cases, notes, gold


def labels(case: Case) -> tuple[str, ...]:
    return tuple(str(case[key]) for key in LABEL_KEYS)


def summarise(cases: list[Case], rerun: list[Case]) -> Case:
    disagree = {name: sum(c[x] != c[y] for c in cases) for name, (x, y) in PAIRS.items()}
    flipped = [c for c in cases if c["A_match"] != c["B_match"] or c["A_match"] != c["C_match"]]
    changed = []
    for first, second in zip(cases, rerun, strict=True):
        first["run2_same"] = labels(first) == labels(second)
        if not first["run2_same"]:
            where = f"{first['fixture']} / {first['id']}"
            changed.append(f"{where}: {labels(first)} -> {labels(second)}")
    split = sum(1 for c in cases if c["flipped_cells"])
    return {"n": len(cases), "disagree": disagree, "flipped": flipped, "changed": changed,
            "split": split}


def write_outputs(out: Path, cases: list[Case], summary: Case, notes: list[str], sha: str,
                  as_of: str) -> tuple[Path, Path]:
    csv_path, md_path = out / "grader_agreement.csv", out / "grader_agreement.md"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for case in cases:
            writer.writerow({**case, **{key: json.dumps(case[key]) for key in LIST_KEYS}})
    claim = f"local claim at {sha}"
    lines = [
        f"# Grader agreement, {claim}", "",
        f"Offline replay of recorded envelopes at `{ROOT}`; warehouse CURRENT_DATE = {as_of}.",
        f"No model, no network, no live service. Every number here is a {claim}, not a score.", "",
        "A = grade52.compare_rows (scripts/verify/grade52.py:567). "
        "B = score_curated.judge_detailed (scripts/score_curated.py:781; row kernel "
        "scripts/oracle_row_match.py:145). B_env = judge_envelope_detailed (:870). "
        "C = dms#299 2 dp ROUND_HALF_EVEN multiset row-hash, implemented only in this script.",
        "", "## Fixtures", "", *[f"- {note}" for note in notes], "", "## Cases", "",
        "| fixture | id | served/gold rows | A | B | B_env | C | flipped numeric cells; note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        detail = "; ".join(case["flipped_cells"])
        if case in summary["flipped"]:
            detail = "; ".join(part for part in (detail, case["note"]) if part)
        lines.append(
            f"| {case['fixture']} | {case['id']} | {case['served_n']}/{case['gold_n']} | "
            f"{case['A']} | {case['B']} | {case['B_env']} | {case['C']} | {detail} |"
        )
    lines += [
        "", f"## Summary ({claim})", "",
        f"- cases graded (served rows and a certified oracle): {summary['n']}",
        *[f"- {name}: {count}" for name, count in summary["disagree"].items()],
        f"- cases where some numeric cell pair splits the three cell rules: {summary['split']}",
        f"- determinism (run 1 vs run 2, fresh warehouse each): labels changed on "
        f"{len(summary['changed'])} of {summary['n']} cases",
        "", "### Flipped cases (label disagreement), exact values", "",
    ]
    for case in summary["flipped"]:
        cells = "; ".join(case["flipped_cells"]) or f"no numeric cell split; {case['note']}"
        lines.append(f"- {case['fixture']} / {case['id']}: A={case['A']} B={case['B']} "
                     f"C={case['C']}: {cells}")
        lines.append(f"  - gold rows: `{json.dumps(case['gold_rows'], default=str)}`")
        lines.append(f"  - served rows: `{json.dumps(case['served_rows'], default=str)}`")
    lines += ["- none"] if not summary["flipped"] else []
    lines += ["", "### Determinism changes", "", *[f"- {item}" for item in summary["changed"]]]
    lines += ["- none"] if not summary["changed"] else []
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, required=True, help="output directory (created)")
    parser.add_argument("--fixture", type=Path, action="append", help="envelope file (repeat)")
    args = parser.parse_args(argv)
    fixtures = list(args.fixture) if args.fixture else list(FIXTURES)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    sha = grade52.product_sha()
    cases, notes, gold = run_once(fixtures, out / "warehouse_run1")
    rerun, _notes2, gold2 = run_once(fixtures, out / "warehouse_run2")
    if gold2.as_of != gold.as_of:
        notes.append(f"run 2 as_of {gold2.as_of} differs from run 1 {gold.as_of}")
    summary = summarise(cases, rerun)
    csv_path, md_path = write_outputs(out, cases, summary, notes, sha, gold.as_of)
    print(f"grader_agreement: local claim at {sha}, as_of {gold.as_of}")
    for note in notes:
        print("  " + note)
    print(f"  cases={summary['n']} " + " ".join(f"{k}={v}" for k, v in summary["disagree"].items()))
    print(f"  determinism: {len(summary['changed'])} label changes between run 1 and run 2")
    print(f"  csv={csv_path}\n  md={md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
