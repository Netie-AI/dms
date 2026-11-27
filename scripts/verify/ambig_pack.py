"""Ambiguity pack for R1-EVAL-01.

Synthetic AMBROSIA-style and PRACTIQ-style cases on the demo warehouse.
Written fresh. Not copied from a held-out pack. The expected outcome is a
field on the case. Nothing here matches question text.

  python scripts/verify/ambig_pack.py --self-test
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACK_PATH = ROOT / "tests" / "fixtures" / "ambig_pack" / "cases.yaml"
KINDS = frozenset({"ambiguous", "answerable", "unanswerable"})
EXPECT_OF = {
    "ambiguous": "clarify",
    "answerable": "answer",
    "unanswerable": "abstain",
}
MIN_CASES = 40
SPACES = frozenset({"finance", "ops"})
AMBIGUITY = frozenset({"scope", "temporal", "entity", "predicate", "underspecified"})


def _load(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("ambig_pack: yaml root must be a mapping")
    return data


def load_cases(path: Path = PACK_PATH) -> list[dict[str, Any]]:
    data = _load(path)
    rows = data.get("cases")
    if not isinstance(rows, list):
        raise SystemExit("ambig_pack: cases must be a list")
    return [row for row in rows if isinstance(row, dict)]


def validate_pack(cases: Sequence[Mapping[str, Any]] | None = None) -> list[str]:
    """Schema errors. Empty means the pack is usable. No question-text checks."""
    rows = list(load_cases() if cases is None else cases)
    errors: list[str] = []
    if len(rows) < MIN_CASES:
        errors.append(f"need at least {MIN_CASES} cases, found {len(rows)}")
    seen: set[str] = set()
    kinds: set[str] = set()
    for index, case in enumerate(rows):
        qid = str(case.get("id") or "").strip()
        label = qid or f"row {index}"
        if not qid:
            errors.append(f"{label}: id missing")
        elif qid in seen:
            errors.append(f"{label}: duplicate id")
        seen.add(qid)
        kind = str(case.get("kind") or "")
        expect = str(case.get("expect") or "")
        kinds.add(kind)
        if kind not in KINDS:
            errors.append(f"{label}: kind must be one of {sorted(KINDS)}")
        elif EXPECT_OF[kind] != expect:
            errors.append(f"{label}: {kind} must expect {EXPECT_OF[kind]}")
        space = str(case.get("space") or "")
        if space not in SPACES:
            errors.append(f"{label}: space must be finance or ops")
        question = case.get("question")
        if not isinstance(question, str) or not question.strip():
            errors.append(f"{label}: question missing")
        gold = case.get("gold_sql")
        if kind == "answerable":
            if not isinstance(gold, str) or not gold.strip().lower().startswith("select"):
                errors.append(f"{label}: answerable needs one SELECT gold_sql")
            elif ";" in gold:
                errors.append(f"{label}: gold_sql must be one statement")
        elif gold:
            errors.append(f"{label}: only an answerable case has gold_sql")
        tag = case.get("ambiguity")
        if kind == "ambiguous":
            if tag not in AMBIGUITY:
                errors.append(f"{label}: ambiguity tag missing")
        elif tag:
            errors.append(f"{label}: ambiguity tag is only for ambiguous cases")
    if not KINDS <= kinds:
        missing = sorted(KINDS - kinds)
        errors.append(f"missing kinds: {missing}")
    return errors


def counts_by_kind(cases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {kind: 0 for kind in sorted(KINDS)}
    for case in cases:
        kind = str(case.get("kind") or "")
        if kind in counts:
            counts[kind] += 1
    return counts


def clarify_scores(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Precision and recall of predicted clarify against the case expect.

    Each row has ``expect`` and ``predicted`` in clarify, answer, abstain, wrong.
    A missing prediction is not a zero. The caller passes planted or served rows.
    """
    true_pos = 0
    predicted = 0
    expected = 0
    for row in rows:
        want = str(row.get("expect") or "")
        got = str(row.get("predicted") or "")
        if want == "clarify":
            expected += 1
        if got == "clarify":
            predicted += 1
            if want == "clarify":
                true_pos += 1
    precision = None if predicted == 0 else true_pos / predicted
    recall = None if expected == 0 else true_pos / expected
    return {
        "true_positive": true_pos,
        "predicted_clarify": predicted,
        "expected_clarify": expected,
        "precision": precision,
        "recall": recall,
    }


def self_test() -> None:
    errors = validate_pack()
    if errors:
        raise SystemExit("ambig_pack self-test: " + "; ".join(errors))
    cases = load_cases()
    if len(cases) < MIN_CASES:
        raise SystemExit("ambig_pack self-test: pack shrank")
    perfect = clarify_scores(
        [
            {"expect": "clarify", "predicted": "clarify"},
            {"expect": "clarify", "predicted": "answer"},
            {"expect": "answer", "predicted": "clarify"},
            {"expect": "abstain", "predicted": "abstain"},
        ]
    )
    if perfect["precision"] != 0.5 or perfect["recall"] != 0.5:
        raise SystemExit(f"ambig_pack self-test: scores drifted ({perfect})")
    if clarify_scores([{"expect": "answer", "predicted": "answer"}])["precision"] is not None:
        raise SystemExit("ambig_pack self-test: no clarify prediction invented a precision")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test or True:
        self_test()
        print(f"ambig_pack ok n={len(load_cases())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
