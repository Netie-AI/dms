"""DMS_AI_FIRST. Default off.

On, a confident L1 pack hit and an ontology compile are context for the
model. Their SQL is not submitted and not served. Ungranted tables, PII,
and destructive SQL stay a direct abstain.

The per-ask list is the stage-1 retrieve. A later store (see
``docs/plans/AI_SQL_SHIFT.md``) keeps the same row shape, per Space, and
sets ``revoked`` so a row drops out of the prompt. A revoked row is never
executed.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from typing import Any

from cortex_client.insights import VERIFIED_EXAMPLES_FIELD

AI_FIRST_ENV = "DMS_AI_FIRST"
_ON = frozenset({"1", "true", "yes", "on"})
# ponytail: first examples in ask order, no ranker. Upgrade: retrieve by
# similarity once the per-Space store exists.
EXAMPLE_CAP = 4
NO_MODEL_SQL = "ai_first_no_model_sql"

_EXAMPLES: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "dms_ai_first_examples", default=None
)


def ai_first_enabled(env: Any = None) -> bool:
    """True only when ``DMS_AI_FIRST`` is a true token. Unset is off."""
    src = os.environ if env is None else env
    return str(src.get(AI_FIRST_ENV) or "").strip().lower() in _ON


def begin_example_turn() -> Token[list[dict[str, Any]] | None]:
    return _EXAMPLES.set([])


def end_example_turn(token: Token[list[dict[str, Any]] | None]) -> None:
    _EXAMPLES.reset(token)


def current_examples() -> list[dict[str, Any]]:
    """Live rows for this ask. Revoked rows are already dropped."""
    return [dict(item) for item in (_EXAMPLES.get() or [])]


def note_example(example: dict[str, Any]) -> None:
    """Remember one example. No turn, a revoke, or a repeat SQL is a no-op."""
    bucket = _EXAMPLES.get()
    if bucket is None or example.get("revoked") is True:
        return
    sql = str(example.get("sql") or "").strip()
    if not sql:
        return
    if any(str(item.get("sql") or "").strip() == sql for item in bucket):
        return
    if len(bucket) >= EXAMPLE_CAP:
        return
    bucket.append(dict(example))


def pack_example(metric: Any, space_id: str | None) -> dict[str, Any]:
    """L1 phrase SQL as a seed. The metric is not executed here."""
    return {
        "source": "l1",
        "question": str(getattr(metric, "question", "") or ""),
        "sql": str(getattr(metric, "sql", "") or ""),
        "space_id": space_id,
        "revoked": False,
        "provenance": {
            "lane": "l1",
            "metric_id": str(getattr(metric, "metric_id", "") or ""),
        },
    }


def compile_example(
    sql: str,
    *,
    question: str,
    space_id: str | None,
    route: str,
) -> dict[str, Any]:
    return {
        "source": "compile",
        "question": question,
        "sql": sql,
        "space_id": space_id,
        "revoked": False,
        "provenance": {"lane": "compile", "served_route": route},
    }


def with_examples(catalog: dict[str, Any]) -> dict[str, Any]:
    """Copy ``catalog`` and attach the live examples. Empty stays untouched."""
    rows = current_examples()
    if not rows:
        return catalog
    out = dict(catalog)
    out[VERIFIED_EXAMPLES_FIELD] = rows
    return out


def example_public() -> dict[str, Any]:
    """Count and sources for the envelope. The SQL stays in the model prompt."""
    sources: list[str] = []
    for row in current_examples():
        src = str(row.get("source") or "")
        if src and src not in sources:
            sources.append(src)
    return {"examples": len(current_examples()), "sources": sources}


def stamp_examples(env: dict[str, Any]) -> dict[str, Any]:
    if ai_first_enabled():
        env["ai_first"] = example_public()
    return env


def no_model_envelope(
    question: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Plain abstain. No figure. The HTTP layer can still offer reconfirm."""
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    env = build_abstain(
        reason=NO_MODEL_SQL,
        question=question,
        stage="generative",
        answer_id="ans_ai_first",
        text=(
            "I don't have a checked answer for that yet, so I am not stating a figure."
        ),
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[f"GEN-01: {NO_MODEL_SQL}"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    assert_envelope_valid(env)
    return stamp_examples(env)
