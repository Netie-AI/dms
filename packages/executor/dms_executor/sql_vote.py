"""A second SQL reading. Agreement is rows from two prompts that do not match.

The ladder and ``validate_compiled_sql`` stay in the caller. This module
compares. It does not classify a question, and it does not compile SQL.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

# One marker so the second prompt cannot hash-equal the first. Not a list.
_SECOND_READING = "reading=2\n"
_RECONFIRM_MARK = "reading=reconfirm\n"
_DISAGREE = "the two readings did not return the same rows"


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


def reading_prompt(question: str, *, reading: int) -> str:
    """Prompt for one reading. Reading 2 is the question plus one marker."""
    q = question or ""
    if reading == 1:
        return q
    return _SECOND_READING + q


def reconfirm_prompt(question: str, detail: str) -> str:
    """Ask the model for a JSON object. The caller parses JSON, not prose."""
    return (
        f"{_RECONFIRM_MARK}{question or ''}\n{detail}\n"
        'Reply with a JSON object with keys why and closest_question.\n'
    )


def prompts_independent(first: str, second: str) -> bool:
    return prompt_hash(first) != prompt_hash(second)


def normalize_sql(sql: str, dialect: str) -> str:
    """Parser normal form. A statement that does not parse folds on whitespace."""
    import sqlglot

    text = sql or ""
    try:
        tree = sqlglot.parse_one(text, read=dialect or None)
    except Exception:
        return " ".join(text.split()).strip().removesuffix(";").lower()
    try:
        return tree.sql(dialect=dialect or None, normalize=True)
    except Exception:
        return " ".join(text.split()).strip().removesuffix(";").lower()


def sql_equivalent(left: str, right: str, dialect: str) -> bool:
    return normalize_sql(left, dialect) == normalize_sql(right, dialect)


def _cell(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        return round(float(value), 4)
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def result_key(rows: list[dict[str, Any]]) -> frozenset[tuple[Any, ...]]:
    """Rows as a set of value tuples in column order. Names are ignored."""
    return frozenset(tuple(_cell(v) for v in row.values()) for row in rows)


def rows_equal(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return bool(left) and bool(right) and result_key(left) == result_key(right)


def readings_agree(
    *,
    prompt_a: str,
    prompt_b: str,
    sql_a: str,
    sql_b: str,
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    dialect: str,
) -> bool:
    """True when the prompts differ and the rows match.

    Normalized identical SQL with those rows is agreement. The same prompt
    is one reading, so it does not agree with itself.
    """
    if not prompts_independent(prompt_a, prompt_b):
        return False
    same_rows = rows_equal(rows_a, rows_b)
    # Identical SQL with different rows is not agreement. Identical SQL
    # with the same rows is agreement, when the prompts differ.
    if sql_equivalent(sql_a, sql_b, dialect) and not same_rows:
        return False
    return same_rows


def disagreement_detail(assumptions: list[Any] | None) -> str:
    """The ladder's named reason, or the disagreement sentence."""
    prefix = "GEN-01: "
    for item in assumptions or []:
        text = str(item)
        if text.startswith(prefix):
            body = text[len(prefix) :].strip()
            if body:
                return body
    return _DISAGREE


def reconfirm_from_payload(payload: dict[str, Any] | None) -> tuple[str, str] | None:
    """``(why, closest_question)`` from a model object. Prose is not scanned."""
    if not isinstance(payload, dict):
        return None
    raw: Any = payload.get("reconfirm")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    why = raw.get("why")
    closest = raw.get("closest_question")
    if not isinstance(why, str) or not why.strip():
        return None
    if not isinstance(closest, str) or not closest.strip():
        return None
    return why.strip(), closest.strip()


def reconfirm_envelope(
    *,
    question: str,
    why: str,
    closest_question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Disagreement the customer can answer. Yes runs ``closest_question``."""
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    reason = (why or _DISAGREE).strip() or _DISAGREE
    closest = (closest_question or "").strip()
    named = closest if closest else "the model did not name one"
    text = f"{reason} Closest answerable question: {named}. Yes runs that question."
    env = build_abstain(
        reason=reason,
        question=question,
        stage="sql_vote",
        answer_id="ans_sql_vote_reconfirm",
        text=text,
        rows=[],
        sql_used=None,
        values=[],
        assumptions=[f"reconfirm: {reason}"],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    assert_envelope_valid(env)
    env["reconfirm"] = {"why": reason, "closest_question": closest}
    env["reconfirm_yes"] = "yes"
    return env


__all__ = [
    "disagreement_detail",
    "prompt_hash",
    "reading_prompt",
    "readings_agree",
    "reconfirm_envelope",
    "reconfirm_from_payload",
    "reconfirm_prompt",
    "rows_equal",
    "sql_equivalent",
]
