"""Run a Space benchmark through the ask path (FEAT-BENCH-SPACE-01).

Outcome labels come from ``grade52.grade_case``, the same function
``scripts/verify/eval_all.py`` imports. Numeric tolerance is
``grade52.ABS_TOL`` (0.005) via ``grade52.cells_equal``: gold's shorter
decimal count wins, and half rounds away from zero. Row order follows
``grade52.order_keys``: no top-level ORDER BY is a multiset; a top-level
ORDER BY keeps that key order. This module does not reimplement either rule.

``execute_gold`` calls ``run_readonly``. Nothing here adds a LIMIT or a
deadline. The ask callable is the product ask path. A failure abstains.
Benchmarks are not written into the verified-query store.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

_REFUSED = frozenset({"statement_not_allowed", "multi_statement"})
_OUTCOME = {
    "CORRECT": "correct",
    "CORRECT_AFTER_YES": "correct",
    "WRONG": "WRONG",
    "REFUSAL_WRONG": "WRONG",
    "CLARIFY": "clarify",
    "RECONFIRM": "reconfirm",
    "ABSTAIN": "abstain",
    "REFUSAL_OK": "abstain",
    "GOLD_BROKEN": "abstain",
    "EMPTY_GOLD": "abstain",
}


def _grader() -> Any:
    root = Path(__file__).resolve().parents[3]
    verify = root / "scripts" / "verify"
    entry = str(verify)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    import grade52

    return grade52


def _ask_failed() -> dict[str, Any]:
    """Ask raised. Abstain through the builder. Do not guess a number."""
    from dms_executor.abstain import build_abstain

    return build_abstain(
        reason="ask_failed",
        stage="ask",
        answer_id="bench-ask-failed",
        text="",
        rows=[],
        values=[],
        route="ask_failed",
        log_ticket=False,
    )


def guard_benchmark_write(*, sql: str) -> None:
    """Same scored-pack block as a verified-query write. Question text is not a key."""
    from dms_executor.skills_quarantine import reject_scored_write

    reject_scored_write({"sql": sql})


class GoldCheck:
    def __init__(self, *, ok: bool, refused: bool, reason: str) -> None:
        self.ok = ok
        self.refused = refused
        self.reason = reason


def check_gold_sql(
    sql: str,
    *,
    grantable: set[str],
    warehouse: Path | None,
) -> GoldCheck:
    """Read-only and grant gates. A write is refused. A grant miss is invalid."""
    from dms_executor.demo_warehouse import SERVING_DIALECT
    from dms_executor.generative_ask import validate_compiled_sql

    why = validate_compiled_sql(
        sql,
        grantable=grantable,
        warehouse=warehouse,
        dialect=SERVING_DIALECT,
    )
    if not why:
        return GoldCheck(ok=True, refused=False, reason="")
    refused = why in _REFUSED or why.startswith("hostile_sql")
    return GoldCheck(ok=False, refused=refused, reason=why)


def execute_gold(
    sql: str, warehouse: Path | None
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """All rows the serving cursor returns. No added cap and no added timeout."""
    if warehouse is None:
        return None, "warehouse_missing"
    from dms_executor.sql_loop import run_readonly

    return run_readonly(sql, warehouse)


def _served_rows(env: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = env.get("rows")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def grade_answer(
    env: Mapping[str, Any],
    gold_rows: list[dict[str, Any]],
    gold_sql: str,
) -> dict[str, Any]:
    """One question. The bucket is ``grade52.grade_case`` unchanged."""
    grader = _grader()
    served = _served_rows(env)
    graded = grader.grade_case(
        gold=gold_rows,
        gold_sql=gold_sql,
        env=env,
        served_rows=served,
        as_of=str(env.get("as_of") or ""),
    )
    bucket = str(graded.get("bucket") or "")
    ordered = False
    try:
        ordered = grader.order_keys(gold_sql) is not None
    except Exception:
        ordered = False
    return {
        "bucket": bucket,
        "outcome": _OUTCOME.get(bucket, "abstain"),
        "reason": str(graded.get("reason") or ""),
        "served_sql": str(env.get("sql_used") or "")[:8000],
        "gold_n": len(gold_rows),
        "served_n": len(served),
        "ordered": ordered,
        "refusal_wrong": bucket == grader.BUCKET_REFUSAL_WRONG,
    }


def tally(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """WRONG is the headline count. Coverage is correct over graded questions."""
    counts = {
        "correct": 0,
        "WRONG": 0,
        "refusal_wrong": 0,
        "reconfirm": 0,
        "clarify": 0,
        "abstain": 0,
        "invalid_gold": 0,
    }
    for row in results:
        outcome = str(row.get("outcome") or "")
        if outcome in counts:
            counts[outcome] += 1
        if row.get("refusal_wrong"):
            counts["refusal_wrong"] += 1
    graded = (
        counts["correct"]
        + counts["WRONG"]
        + counts["reconfirm"]
        + counts["clarify"]
        + counts["abstain"]
    )
    coverage = None if graded == 0 else counts["correct"] / graded
    return {"n": len(results), "graded": graded, "coverage": coverage, **counts}


def _invalid(item: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "benchmark_id": str(item.get("id") or ""),
        "question": str(item.get("question") or ""),
        "served_sql": "",
        "outcome": "invalid_gold",
        "bucket": "INVALID_GOLD",
        "reason": reason,
        "gold_n": 0,
        "served_n": 0,
        "ordered": False,
        "refusal_wrong": False,
    }


def run_items(
    items: Sequence[Mapping[str, Any]],
    ask: Callable[[str], Mapping[str, Any]],
    execute: Callable[[str], tuple[list[dict[str, Any]] | None, str | None]],
    cancelled: Callable[[], bool],
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Grade each item. Cancel is checked before the next item, not on a timer."""
    results: list[dict[str, Any]] = []
    for item in items:
        if cancelled():
            return "cancelled", results
        if str(item.get("status") or "") != "valid":
            row = _invalid(item, str(item.get("invalid_reason") or "invalid_gold"))
        else:
            try:
                env = ask(str(item.get("question") or ""))
            except Exception:
                env = _ask_failed()
            if not isinstance(env, Mapping):
                env = _ask_failed()
            gold_rows, err = execute(str(item.get("gold_sql") or ""))
            if err or gold_rows is None:
                row = _invalid(item, err or "gold_failed")
            else:
                graded = grade_answer(env, gold_rows, str(item.get("gold_sql") or ""))
                row = {
                    "benchmark_id": str(item.get("id") or ""),
                    "question": str(item.get("question") or ""),
                    **graded,
                }
        results.append(row)
        if on_result is not None:
            on_result(row)
    return "done", results
