"""Score one Mini-Dev record. Contaminated rows are not in the denominator.

Outcomes are CORRECT, WRONG, ASKBACK, ABSTAIN, ERROR. CORRECT is official EX
only. AI versus rule uses ``scripts/verify/grade52.py`` ``path_group``.
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bench.bird.ex import calculate_ex, calculate_ex_normalised, nearest_rank, result_tuples

OUTCOMES = ("CORRECT", "WRONG", "ASKBACK", "ABSTAIN", "ERROR")
SERVED = frozenset({"CORRECT", "WRONG"})
ABSTAIN_CODES = frozenset(
    {
        "sql_relation_not_granted",
        "path_not_allowed",
        "ungranted",
        "outside_grounded_scope",
        "destructive_sql",
        "destructive",
        "statement_not_allowed",
    }
)
_STATUS_TOKENS = frozenset({"402", "429"})


def contaminated(status: int | None, error: str | None = None) -> bool:
    """402 or 429 on the status or as its own token in an error code.

    This reads the status and the error code. It does not read the question.
    """
    if status in (402, 429):
        return True
    if not error:
        return False
    cleaned = error.replace("/", " ").replace(",", " ").replace(":", " ")
    return any(part in _STATUS_TOKENS for part in cleaned.split())


def is_askback(env: Mapping[str, Any]) -> bool:
    """A typed reconfirm or a clarify card. An empty suggestion is not one."""
    block = env.get("reconfirm")
    if isinstance(block, dict):
        suggested = block.get("suggested_question")
        if isinstance(suggested, str) and suggested.strip():
            return True
        closest = block.get("closest_question")
        if isinstance(closest, str) and closest.strip():
            return True
    clarify = env.get("clarify")
    if isinstance(clarify, dict) and (clarify.get("clarify_id") or clarify.get("readings")):
        return True
    return False


def is_abstain(env: Mapping[str, Any]) -> bool:
    if env.get("abstained") is True:
        return True
    return str(env.get("badge") or "") == "ABSTAIN"


def confirm_token(env: Mapping[str, Any]) -> str:
    block = env.get("reconfirm")
    if not isinstance(block, dict):
        return ""
    token = block.get("token")
    if not isinstance(token, str):
        return ""
    return token.strip()


def _grade52():
    verify = Path(__file__).resolve().parents[2] / "verify"
    root = str(verify)
    if root not in sys.path:
        sys.path.insert(0, root)
    import grade52

    return grade52


def attribute(env: Mapping[str, Any] | None, model_sql: str | None = None) -> str:
    """``ai``, ``rule``, or ``unattributed`` via grade52.path_group."""
    if not isinstance(env, Mapping) or not env:
        return "unattributed"
    grader = _grade52()
    return str(grader.path_group(grader.serve_path(env, model_sql)))


def _done(
    outcome: str,
    official: int,
    normalised: int,
    reason: str,
    *,
    path: str = "unattributed",
) -> dict[str, Any]:
    return {
        "contaminated": False,
        "outcome": outcome,
        "ex": official,
        "ex_norm": normalised,
        "reason": reason,
        "path": path if outcome in SERVED else "unattributed",
    }


def classify(
    *,
    http_status: int | None,
    error: str | None,
    envelope: Mapping[str, Any] | None,
    predicted: Sequence[Any] | None,
    gold_rows: Sequence[Any] | None,
    pred_error: str | None,
    gold_error: str | None,
    detail_code: str | None = None,
    model_sql: str | None = None,
) -> dict[str, Any]:
    """One record. A contaminated record has no outcome and is not scored."""
    if contaminated(http_status, error) or contaminated(None, detail_code):
        return {
            "contaminated": True,
            "outcome": None,
            "ex": None,
            "ex_norm": None,
            "reason": "rate_or_payment",
            "path": "unattributed",
        }
    if detail_code in ABSTAIN_CODES or pred_error == "destructive_sql":
        return _done("ABSTAIN", 0, 0, detail_code or pred_error or "abstain")
    if gold_error == "destructive_sql":
        return _done("ERROR", 0, 0, "gold_destructive")
    if http_status == 0 or (isinstance(http_status, int) and http_status >= 400):
        return _done("ERROR", 0, 0, error or "http")
    env = envelope if isinstance(envelope, Mapping) else {}
    if env and is_askback(env):
        return _done("ASKBACK", 0, 0, "askback")
    if env and is_abstain(env):
        return _done("ABSTAIN", 0, 0, "abstain")
    if gold_error or pred_error:
        return _done("ERROR", 0, 0, gold_error or pred_error or "error")
    if predicted is None or gold_rows is None:
        return _done("ERROR", 0, 0, "no_rows")
    try:
        official = calculate_ex(result_tuples(predicted), result_tuples(gold_rows))
        normalised = calculate_ex_normalised(predicted, gold_rows)
    except TypeError:
        return _done("ERROR", 0, 0, "unhashable")
    outcome = "CORRECT" if official == 1 else "WRONG"
    path = attribute(env, model_sql) if env else "unattributed"
    return _done(outcome, official, normalised, "ex", path=path)


def latest_by_id(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Last line per id wins, so a retry replaces a contaminated attempt."""
    order: list[str] = []
    chosen: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("id"))
        if key not in chosen:
            order.append(key)
        chosen[key] = dict(record)
    return [chosen[key] for key in order]


def report(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Official EX over scored rows. Contaminated ids are listed for retry."""
    latest = latest_by_id(records)
    scored = [row for row in latest if not row.get("contaminated")]
    counts: Counter[str] = Counter(str(row.get("outcome") or "") for row in scored)
    n = len(scored)
    correct = counts["CORRECT"]
    norm_correct = sum(1 for row in scored if row.get("ex_norm") == 1)
    paths: Counter[str] = Counter(
        str(row.get("path") or "unattributed") for row in scored if row.get("outcome") in SERVED
    )
    durations = [
        float(row["seconds"])
        for row in scored
        if isinstance(row.get("seconds"), (int, float)) and not isinstance(row.get("seconds"), bool)
    ]
    retry = [str(row.get("id")) for row in latest if row.get("contaminated")]
    return {
        "n_scored": n,
        "n_contaminated": len(retry),
        "outcomes": {name: int(counts.get(name, 0)) for name in OUTCOMES},
        "ex": None if n == 0 else correct / n,
        "ex_normalised": None if n == 0 else norm_correct / n,
        "paths": {
            "ai": int(paths.get("ai", 0)),
            "rule": int(paths.get("rule", 0)),
            "unattributed": int(paths.get("unattributed", 0)),
        },
        "p50": nearest_rank(durations, 50),
        "p95": nearest_rank(durations, 95),
        "retry_ids": retry,
        "pass": False,
        "target": None,
    }


def format_report(summary: Mapping[str, Any]) -> str:
    outcomes = summary.get("outcomes") or {}
    paths = summary.get("paths") or {}
    ex = summary.get("ex")
    norm = summary.get("ex_normalised")
    ex_s = "n/a" if ex is None else f"{ex:.4f}"
    norm_s = "n/a" if norm is None else f"{norm:.4f}"
    p50 = summary.get("p50")
    p95 = summary.get("p95")
    p50_s = "n/a" if p50 is None else f"{p50:.3f}"
    p95_s = "n/a" if p95 is None else f"{p95:.3f}"
    return (
        f"n={summary.get('n_scored')} ex={ex_s} ex_norm={norm_s} "
        f"CORRECT={outcomes.get('CORRECT', 0)} WRONG={outcomes.get('WRONG', 0)} "
        f"ASKBACK={outcomes.get('ASKBACK', 0)} ABSTAIN={outcomes.get('ABSTAIN', 0)} "
        f"ERROR={outcomes.get('ERROR', 0)} contaminated={summary.get('n_contaminated')} "
        f"ai={paths.get('ai', 0)} rule={paths.get('rule', 0)} "
        f"unattributed={paths.get('unattributed', 0)} p50={p50_s} p95={p95_s}"
    )
