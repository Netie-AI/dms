"""CCA-06 - the constraint cascade's precision gate, over a golden corpus.

What a cascade can get wrong is not what a query engine gets wrong. There is no
oracle total to compare against here, because a certified cascade has not
computed anything yet; it has decided which filter values an ask is allowed to
run with. So the two numbers are about the filter, not the figure:

  precision-on-answered   of the asks the cascade let through, the share whose
                          binding held every member the corpus demands and none
                          of the values it forbids. Target 100.00 pct. This is a
                          law, not a goal.
  coverage                share of the answerable corpus that certified rather
                          than abstained. Grows by landing encodings and by
                          widening a reviewed pack. Never bought with precision.

The corpus declares its own fixture warehouses (``tests/fixtures/cca_eval/
corpus.json``) and this runner builds each as a throwaway DuckDB. Hand-written
DDL per case would let a new case quietly introduce a schema nobody reviewed,
and a schema is half of what every one of these verdicts turns on.

  Run the gate:
    python scripts/cca_eval.py

  Machine-readable, for CI to keep:
    python scripts/cca_eval.py --json

Exit 1 on a single confidently-wrong binding, regardless of coverage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
for _package in ("packages/executor", "packages/core", "packages/cortex_client", "apps/api"):
    _path = str(ROOT / _package)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dms_executor.cca.binder import norm_value  # noqa: E402
from dms_executor.cca.cascade import CascadeOutcome, run_cascade  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "cca_eval" / "corpus.json"

#: A fixture may name a table or a column, and nothing else. The corpus is in
#: repo and reviewed, but it is still a data file interpolated into DDL, and a
#: gate that would execute whatever a fixture says is not a gate.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Outcome labels. Each names one distinct failure, and they are never summed
#: into a single number: a refusal and a wrong answer cost a buyer different
#: things, and a gate that adds them together stops meaning anything.
OK = "ok"
NOT_ANSWERED = "not answered"
WRONG = "WRONG"
COVERAGE_MISS = "COVERAGE MISS"
ENGAGEMENT_MISS = "ENGAGEMENT MISS"
STAGE_MISS = "STAGE MISS"

FAILURES = (WRONG, COVERAGE_MISS, ENGAGEMENT_MISS, STAGE_MISS)

WHAT_THIS_MEASURES = """\
=== WHAT THIS MEASURES, AND WHAT IT DOES NOT ===
  It calls run_cascade directly against fixture DuckDB warehouses built from the
  corpus. It does not POST to /v1/chat/ask, does not start the API and never
  reaches Cortex, so it proves nothing about the badge, the values, the rows or
  the rendered answer text a customer reads. Rule 10a is met by
  tests/test_cca_cascade.py, which asserts the envelope; this is a measurement
  of one component, and a green run here is consistent with a broken answer path.
  What it does prove: which asks the cascade is willing to let proceed, and
  exactly which landed values each certified stage bound."""

DEFINITIONS = """\
=== SCORING DEFINITIONS ===
  ANSWERED          the cascade certified - it is willing to let the ask proceed.
  WRONG             certified where the corpus said ABSTAIN, or certified while
                    binding a must_not_bind value, or certified while missing an
                    expect_members member, or bound a stage the ask constrains
                    not at all.
  precision-on-answered = 1 - WRONG / ANSWERED.
  COVERAGE MISS     abstained where the corpus said CERTIFIED. A real regression
                    and it fails the run, counted apart from WRONG because a
                    refusal and a confident wrong answer are not the same defect.
  coverage          ANSWERED / cases whose expect is CERTIFIED. Reported beside
                    precision, never mixed into it.
  ENGAGEMENT MISS   engaged on an ask that constrains no cascade stage, or stayed
                    out of one that does. A control that refuses correct work is
                    a failure, not a win (R-0005).
  STAGE MISS        abstained at a different stage than the corpus named, so the
                    customer would be told the wrong encoding is missing.
  NOT ANSWERED      a non-engagement case that correctly did not engage. Neither
                    a pass nor a failure on precision: it is outside the cascade."""


@dataclass(frozen=True)
class CaseResult:
    """One case, its verdict, and the binding that verdict authorised."""

    case_id: str
    kind: str
    expect: str
    verdict: str
    outcome: str
    detail: str
    engaged: bool = False
    stage: str | None = None
    members: tuple[str, ...] = ()
    values: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "class": self.kind,
            "expect": self.expect,
            "verdict": self.verdict,
            "outcome": self.outcome,
            "detail": self.detail,
            "engaged": self.engaged,
            "blocked_at": self.stage,
            "bound_members": list(self.members),
            "bound_values": list(self.values),
        }


@dataclass(frozen=True)
class Summary:
    """The counts, kept separate on purpose. See DEFINITIONS."""

    total: int
    answered: int
    wrong: int
    coverage_misses: int
    engagement_misses: int
    stage_misses: int
    expected_certified: int
    not_answered: int
    failures: tuple[str, ...] = ()

    @property
    def precision_on_answered(self) -> float:
        """1.0 when nothing was answered: an instrument with no answers to grade
        has found no wrong answer, and inventing a 0.0 there would read as a
        defect where there is only silence."""
        if not self.answered:
            return 1.0
        return 1.0 - self.wrong / self.answered

    @property
    def coverage(self) -> float:
        if not self.expected_certified:
            return 1.0
        return self.answered / self.expected_certified

    @property
    def passed(self) -> bool:
        return not (
            self.wrong or self.coverage_misses or self.engagement_misses or self.stage_misses
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "dms.cca_eval",
            "cases": self.total,
            "answered": self.answered,
            "wrong": self.wrong,
            "coverage_misses": self.coverage_misses,
            "engagement_misses": self.engagement_misses,
            "stage_misses": self.stage_misses,
            "not_answered": self.not_answered,
            "expected_certified": self.expected_certified,
            "precision_on_answered": round(self.precision_on_answered, 4),
            "coverage": round(self.coverage, 4),
            "passed": self.passed,
            "failures": list(self.failures),
        }


def load_corpus(path: Path = CORPUS) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def build_warehouse(spec: Mapping[str, Mapping[str, Sequence[str]]], path: Path) -> Path:
    """Materialise one declared warehouse as a DuckDB file.

    Every column is VARCHAR and short columns are padded with NULL to the
    longest one. The binder reads DISTINCT non-null values per column, so row
    alignment carries no meaning here; padding rather than repeating a value
    keeps the fixture from implying a join that does not exist.
    """
    con = duckdb.connect(str(path))
    try:
        for table, columns in spec.items():
            if not _IDENT.match(table):
                raise ValueError(f"fixture table name is not an identifier: {table!r}")
            names = list(columns)
            for name in names:
                if not _IDENT.match(name):
                    raise ValueError(f"fixture column name is not an identifier: {name!r}")
            ddl = ", ".join(f'"{name}" VARCHAR' for name in names)
            con.execute(f'CREATE TABLE "{table}" ({ddl})')
            depth = max((len(columns[name]) for name in names), default=0)
            rows = [
                tuple(
                    columns[name][i] if i < len(columns[name]) else None for name in names
                )
                for i in range(depth)
            ]
            if rows:
                marks = ", ".join("?" for _ in names)
                con.executemany(f'INSERT INTO "{table}" VALUES ({marks})', rows)
    finally:
        con.close()
    return path


def build_warehouses(corpus: Mapping[str, Any], workdir: Path) -> dict[str, Path]:
    """One DuckDB file per declared warehouse, reused by every case naming it."""
    out: dict[str, Path] = {}
    for name, spec in dict(corpus["warehouses"]).items():
        if not _IDENT.match(name):
            raise ValueError(f"fixture warehouse name is not an identifier: {name!r}")
        out[name] = build_warehouse(spec, workdir / f"{name}.duckdb")
    return out


def bound(outcome: CascadeOutcome) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(canonical members, landed values) the certified stages actually bound.

    Read off the results rather than the trace text, because the values are what
    a filter would carry and the members are what a coverage sentence claims.
    """
    members: list[str] = []
    values: list[str] = []
    for res in outcome.results:
        if not res.certified:
            continue
        members += [m for m in res.matched if m not in members]
        values += [v for v in res.values if v not in values]
    return tuple(members), tuple(values)


def _unconstrained_stages(outcome: CascadeOutcome) -> set[str]:
    return {
        str(entry["type"])
        for entry in outcome.trace
        if entry.get("candidate") == "(unconstrained)"
    }


def judge(case: Mapping[str, Any], outcome: CascadeOutcome) -> tuple[str, str]:
    """Classify one case against the corpus. Returns (outcome label, detail)."""
    expect = str(case["expect"])
    members, values = bound(outcome)

    if expect == "NOT_ENGAGED":
        if outcome.engaged:
            return ENGAGEMENT_MISS, (
                "the cascade engaged on an ask that constrains no stage; "
                f"blocked_at={outcome.blocked_at}, bound={list(values)}"
            )
        return NOT_ANSWERED, "did not engage, as it must not"

    if not outcome.engaged:
        return ENGAGEMENT_MISS, (
            "the cascade did not engage, so this ask's constraint was never "
            "certified and never applied"
        )

    if expect == "ABSTAIN":
        if outcome.certified:
            return WRONG, (
                "certified an ask the corpus says is unanswerable here; "
                f"bound {list(values)} as {list(members)}"
            )
        want_stage = case.get("expect_stage")
        if want_stage and outcome.blocked_at != want_stage:
            return STAGE_MISS, (
                f"abstained at {outcome.blocked_at}, corpus names {want_stage}"
            )
        return OK, f"abstained at {outcome.blocked_at}"

    # expect CERTIFIED from here down.
    if not outcome.certified:
        return COVERAGE_MISS, (
            f"abstained at {outcome.blocked_at}: {outcome.blocked_reason}"
        )

    landed = {norm_value(v) for v in values} | {norm_value(m) for m in members}
    forbidden = [v for v in case.get("must_not_bind") or () if norm_value(v) in landed]
    if forbidden:
        return WRONG, f"bound values the corpus forbids: {forbidden} (bound {list(values)})"

    missing = [m for m in case.get("expect_members") or () if norm_value(m) not in landed]
    if missing:
        return WRONG, f"certified without binding {missing} (bound {list(members)})"

    want_free = set(case.get("expect_unconstrained") or ())
    constrained = sorted(want_free - _unconstrained_stages(outcome))
    if constrained:
        return WRONG, (
            f"stage(s) {constrained} were bound although the ask constrains none of them"
        )

    return OK, f"certified {list(members)} as {list(values)}"


def run_case(
    case: Mapping[str, Any], warehouses: Mapping[str, Path], corpus: Mapping[str, Any]
) -> CaseResult:
    name = str(case["warehouse"])
    warehouse = warehouses[name]
    tables = list(dict(corpus["warehouses"])[name])
    outcome = run_cascade(str(case["question"]), warehouse=warehouse, tables=tables)
    label, detail = judge(case, outcome)
    members, values = bound(outcome)
    if not outcome.engaged:
        verdict = "NOT_ENGAGED"
    elif outcome.certified:
        verdict = "CERTIFIED"
    else:
        verdict = "ABSTAIN"
    return CaseResult(
        case_id=str(case["id"]),
        kind=str(case.get("class") or ""),
        expect=str(case["expect"]),
        verdict=verdict,
        outcome=label,
        detail=detail,
        engaged=outcome.engaged,
        stage=outcome.blocked_at,
        members=members,
        values=values,
    )


def score(results: Sequence[CaseResult], corpus: Mapping[str, Any]) -> Summary:
    counts = {label: 0 for label in FAILURES}
    for res in results:
        if res.outcome in counts:
            counts[res.outcome] += 1
    answered = sum(1 for r in results if r.engaged and r.verdict == "CERTIFIED")
    expected_certified = sum(1 for c in corpus["cases"] if c["expect"] == "CERTIFIED")
    return Summary(
        total=len(results),
        answered=answered,
        wrong=counts[WRONG],
        coverage_misses=counts[COVERAGE_MISS],
        engagement_misses=counts[ENGAGEMENT_MISS],
        stage_misses=counts[STAGE_MISS],
        expected_certified=expected_certified,
        not_answered=sum(1 for r in results if r.outcome == NOT_ANSWERED),
        failures=tuple(
            f"{r.case_id}: {r.outcome} - {r.detail}" for r in results if r.outcome in FAILURES
        ),
    )


def evaluate(
    corpus: Mapping[str, Any], workdir: Path
) -> tuple[list[CaseResult], Summary]:
    """Build the fixture warehouses and run every case. The whole gate, reusable.

    The pytest wrapper calls this rather than re-deriving the scoring, so the
    number CI enforces and the number a human reads off the CLI cannot drift.
    """
    warehouses = build_warehouses(corpus, workdir)
    results = [run_case(case, warehouses, corpus) for case in corpus["cases"]]
    return results, score(results, corpus)


def report(results: Sequence[CaseResult], summary: Summary) -> None:
    print(WHAT_THIS_MEASURES)
    print()
    print(DEFINITIONS)
    print()
    print(f"=== CASES ({summary.total}) ===")
    by_class: dict[str, list[CaseResult]] = {}
    for res in results:
        by_class.setdefault(res.kind, []).append(res)
    for kind, group in by_class.items():
        print(f"  -- {kind} ({len(group)})")
        for res in group:
            print(f"     {res.case_id:<46} {res.verdict:<12} {res.outcome:<16} {res.detail}")

    print("\n=== RESULT ===")
    print(
        f"  precision-on-answered  {summary.precision_on_answered * 100:6.2f} pct   "
        f"({summary.answered - summary.wrong}/{summary.answered} answered)"
    )
    print(
        f"  coverage               {summary.coverage * 100:6.2f} pct   "
        f"({summary.answered}/{summary.expected_certified} the corpus says are answerable)"
    )
    print(f"  WRONG                  {summary.wrong}")
    print(f"  COVERAGE MISS          {summary.coverage_misses}")
    print(f"  ENGAGEMENT MISS        {summary.engagement_misses}")
    print(f"  STAGE MISS             {summary.stage_misses}")
    print(f"  NOT ANSWERED           {summary.not_answered}   (non-engagement, outside precision)")

    if summary.failures:
        print("\nFAIL")
        for line in summary.failures:
            print(f"  - {line}")
        return
    print("\nPASS 0 confidently wrong bindings, 0 coverage misses.")
    if summary.not_answered:
        print(
            f"     {summary.not_answered} case(s) did not engage, which is the "
            "corpus asking the cascade to stay out of the way."
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes\n"
            "  0  PASS - 0 WRONG and 0 COVERAGE MISS (and 0 ENGAGEMENT / STAGE MISS,\n"
            "     which are counted apart so neither hides inside the other)\n"
            "  1  FAIL - at least one of those, or a corpus that will not load\n"
        ),
    )
    ap.add_argument(
        "--corpus", type=Path, default=CORPUS, help=f"corpus JSON (default {CORPUS})"
    )
    ap.add_argument("--json", action="store_true", help="emit machine-readable results only")
    ap.add_argument(
        "--out", type=Path, help="also write the machine-readable results to this path"
    )
    args = ap.parse_args(argv)

    try:
        corpus = load_corpus(args.corpus)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL corpus did not load: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="cca_eval_") as tmp:
        results, summary = evaluate(corpus, Path(tmp))

    payload = {
        "measures": WHAT_THIS_MEASURES,
        "summary": summary.as_dict(),
        "cases": [r.as_dict() for r in results],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        report(results, summary)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return 0 if summary.passed else 1


if __name__ == "__main__":
    sys.exit(main())
