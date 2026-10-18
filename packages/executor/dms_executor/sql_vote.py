"""A second SQL reading. Agreement is equal rows from two different instructions.

The SQL strings do not have to match. Each one must read a granted table, so a
literal such as ``SELECT 3`` does not agree. The caller also runs
``validate_compiled_sql`` on both. One insights cap covers both readings and
every retry.

Reading 1 asks for SQL. Reading 2 asks for a plan of tables, joins, measure,
and filters, then for SQL that follows that plan. A hash change or a short
suffix is not a second instruction. The insights body has one
``model_preference`` and no temperature or seed, so this module does not
invent a second model.

The ladder and ``validate_compiled_sql`` stay in the caller. This module
compares. It does not classify a question, and it does not compile SQL.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

# ponytail: 80 characters. A one-line marker fits; a second instruction does not.
# Upgrade: a template id on the prompt if a third reading is added.
_WRAPPER_MAX = 80
_QUESTION = "\nQuestion:\n"
_RECONFIRM_MARK = "reading=reconfirm\n"
_DISAGREE = "the two readings did not return the same rows"
_ONTO_KEYS = ("objects", "links", "joins", "measures", "columns", "schema")
_DIRECT = (
    "Write one SELECT that answers the question. "
    "Use only tables and columns that appear in the ontology. "
    "Return the SQL. Do not write a plan."
)
_PLAN = (
    "From the ontology, write a JSON object and nothing else. "
    "Keys are tables, joins, measure, filters. "
    "tables is a list of table names. "
    "joins is a list of objects with left, right, and on. "
    "measure is one string. filters is a list of strings. "
    "Do not write SQL."
)
_FROM_PLAN = (
    "Follow the plan below. The plan names the tables, the joins, "
    "the measure, and the filters. Write one SELECT that uses only those. "
    "Do not add a table, a join, a measure, or a filter the plan does not name. "
    "Return the SQL."
)


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


def empty_plan() -> dict[str, Any]:
    """No invented tables. The SQL prompt still uses the plan template."""
    return {"tables": [], "joins": [], "measure": "", "filters": []}


def ontology_json(ctx: dict[str, Any] | None) -> str:
    """Structural ontology already on the ask context. No phrase scan."""
    if not isinstance(ctx, dict):
        return "{}"
    picked = {key: ctx[key] for key in _ONTO_KEYS if key in ctx}
    return json.dumps(picked, sort_keys=True, default=str)


def plan_prompt(question: str, ontology: str) -> str:
    """Reading 2, first call: a plan, not SQL."""
    return (
        f"{_PLAN}\nOntology:\n{ontology or '{}'}\n{_QUESTION}{question or ''}"
    )


def reading_prompt(
    question: str,
    *,
    reading: int,
    plan: dict[str, Any] | None = None,
    ontology: str = "",
) -> str:
    """SQL instruction for one reading. Reading 2 includes the ontology and a plan."""
    q = question or ""
    if reading == 1:
        return f"{_DIRECT}{_QUESTION}{q}"
    body = json.dumps(plan if isinstance(plan, dict) else empty_plan(), sort_keys=True)
    return f"{_FROM_PLAN}\nOntology:\n{ontology or '{}'}\nPlan:\n{body}{_QUESTION}{q}"


def vote_plan_from_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """A plan object, or None. SQL in the payload is not a plan."""
    if not isinstance(payload, dict):
        return None
    raw: Any = payload.get("vote_plan")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    tables = raw.get("tables")
    joins = raw.get("joins")
    measure = raw.get("measure")
    filters = raw.get("filters")
    if not isinstance(tables, list) or not all(isinstance(item, str) for item in tables):
        return None
    if not isinstance(joins, list):
        return None
    cleaned: list[dict[str, str]] = []
    for item in joins:
        if not isinstance(item, dict):
            return None
        left = item.get("left")
        right = item.get("right")
        on = item.get("on")
        if not isinstance(left, str) or not isinstance(right, str) or not isinstance(on, str):
            return None
        cleaned.append({"left": left.strip(), "right": right.strip(), "on": on.strip()})
    if not isinstance(measure, str):
        return None
    if not isinstance(filters, list) or not all(isinstance(item, str) for item in filters):
        return None
    return {
        "tables": [item.strip() for item in tables],
        "joins": cleaned,
        "measure": measure.strip(),
        "filters": [item.strip() for item in filters],
    }


def reconfirm_prompt(question: str, detail: str) -> str:
    """Ask the model for a JSON object. The caller parses JSON, not prose."""
    return (
        f"{_RECONFIRM_MARK}{question or ''}\n{detail}\n"
        "Reply with a JSON object with keys why and closest_question.\n"
    )


def _short_wrapper(left: str, right: str) -> bool:
    """True when one text is the other plus a short prefix or suffix."""
    if not left or not right or left == right:
        return False
    longer, shorter = (left, right) if len(left) >= len(right) else (right, left)
    if not (longer.startswith(shorter) or longer.endswith(shorter)):
        return False
    return (len(longer) - len(shorter)) <= _WRAPPER_MAX


def prompts_differ_in_substance(first: str, second: str) -> bool:
    """True when the instruction templates differ, not only a hash or a suffix.

    The question after ``Question:`` must be the same. A prompt that is the
    other prompt plus a short marker is the same framing.
    """
    left = (first or "").strip()
    right = (second or "").strip()
    if not left or not right or left == right or _short_wrapper(left, right):
        return False
    if _QUESTION not in left or _QUESTION not in right:
        return False
    head_left, tail_left = left.split(_QUESTION, 1)
    head_right, tail_right = right.split(_QUESTION, 1)
    head_left, head_right = head_left.strip(), head_right.strip()
    if tail_left.strip() != tail_right.strip():
        return False
    if not head_left or not head_right or head_left == head_right:
        return False
    return not _short_wrapper(head_left, head_right)


def prompts_independent(first: str, second: str) -> bool:
    """Independent means a different instruction, not a different hash."""
    return prompts_differ_in_substance(first, second)


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


def _reads_granted(sql: str, grantable: set[str], dialect: str) -> bool:
    """True when ``serve_gap`` accepts the statement.

    A literal such as ``SELECT 3`` is ungranted. The FROM/JOIN regex misses a
    Unicode name and a ``TABLE`` form that the grant parser allows.
    """
    from dms_executor.grant_struct import serve_gap

    granted = {name for name in grantable if name and str(name).strip()}
    if not granted:
        return False
    return serve_gap(sql, grantable=granted, dialect=dialect or None) is None


def readings_agree(
    *,
    prompt_a: str,
    prompt_b: str,
    sql_a: str,
    sql_b: str,
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    dialect: str,
    grantable: set[str],
) -> bool:
    """True when independent prompts return the same rows.

    The SQL does not have to match. Each statement must read at least one
    granted table, so ``SELECT 3`` does not agree. The caller also requires
    ``validate_compiled_sql`` to accept both statements.
    """
    if not prompts_independent(prompt_a, prompt_b):
        return False
    if not _reads_granted(sql_a, grantable, dialect) or not _reads_granted(
        sql_b, grantable, dialect
    ):
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
    "empty_plan",
    "ontology_json",
    "plan_prompt",
    "prompt_hash",
    "prompts_differ_in_substance",
    "reading_prompt",
    "readings_agree",
    "reconfirm_envelope",
    "reconfirm_from_payload",
    "reconfirm_prompt",
    "rows_equal",
    "sql_equivalent",
    "vote_plan_from_payload",
]
