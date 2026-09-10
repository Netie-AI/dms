"""DR-0002 multi-turn: compute-or-abstain on the prior envelope's values.

``average of them`` and ``add N`` have no warehouse meaning without the parent
ask. Numbers come from the prior turn's ``values[]`` — never a silent demo
figure. Arithmetic is executed as SQL in this package (hard rule 7).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dms_executor.demo_warehouse import execute_sql
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

_AVG = re.compile(r"^\s*average of them\s*[.?]?\s*$", re.I)
_ADD = re.compile(r"^\s*add\s+(-?\d+(?:\.\d+)?)\s*[.?]?\s*$", re.I)


def _as_of() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(question: str) -> str:
    return (question or "").strip()


def is_followup_question(question: str) -> bool:
    q = _norm(question)
    return bool(_AVG.match(q) or _ADD.match(q))


def numeric_values(env: dict[str, Any] | None) -> list[float]:
    out: list[float] = []
    for item in (env or {}).get("values") or []:
        if not isinstance(item, dict):
            continue
        val = item.get("value")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        out.append(float(val))
    return out


def turn_key(session_id: str | None, space_id: str | None) -> tuple[str, str] | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    return (sid, (space_id or "").strip())


def snapshot_turn(env: dict[str, Any]) -> dict[str, Any] | None:
    if env.get("abstained") is True:
        return None
    nums = numeric_values(env)
    if not nums:
        return None
    return {"values": nums, "sql_used": env.get("sql_used")}


def _abstain(*, space_id: str | None, session_id: str | None, why: str) -> dict[str, Any]:
    env = build_answer_envelope(
        answer_id="ans_followup_abstain",
        text=(
            "I cannot compute that follow-up without a prior numeric answer in "
            "this session. Ask the parent question first."
        ),
        badge="ABSTAIN",
        abstained=True,
        assumptions=["session follow-up abstained — no invent", why],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="followup",
        rows=[],
        values=[],
    )
    assert_envelope_valid(env)
    return env


def _pack_compute(
    *,
    sql: str,
    rows: list[dict[str, Any]],
    text: str,
    space_id: str | None,
    session_id: str | None,
    question: str,
    why: str,
) -> dict[str, Any]:
    env = build_answer_envelope(
        answer_id="ans_followup",
        text=text,
        badge="L2_VALIDATED",
        abstained=False,
        rows=rows,
        sql_used=sql,
        assumptions=[
            "computed from prior turn values",
            why,
        ],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="followup",
        question=question,
    )
    assert_envelope_valid(env)
    return env


def maybe_followup(
    question: str,
    *,
    prior: dict[str, Any] | None,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
    tables: list[str] | None = None,
) -> dict[str, Any] | None:
    """Envelope for a follow-up, or None when this ask is not a follow-up."""
    if tables:
        return None
    q = _norm(question)
    avg = _AVG.match(q)
    add = _ADD.match(q)
    if not avg and not add:
        return None
    nums = list((prior or {}).get("values") or [])
    nums = [float(n) for n in nums if isinstance(n, (int, float)) and not isinstance(n, bool)]
    if avg:
        if len(nums) < 1:
            return _abstain(
                space_id=space_id,
                session_id=session_id,
                why="average of them: no prior numeric values",
            )
        expr = " + ".join(f"{n:.10g}" for n in nums)
        sql = f"SELECT ROUND(({expr}) / {len(nums)}.0, 2) AS average_myr"
        try:
            rows = execute_sql(sql, path=warehouse)
        except Exception:  # noqa: BLE001
            return _abstain(
                space_id=space_id,
                session_id=session_id,
                why="average of them: compute failed",
            )
        avg_v = float(rows[0]["average_myr"]) if rows else 0.0
        return _pack_compute(
            sql=sql,
            rows=rows,
            text=f"Average of the prior {len(nums)} figures is RM {avg_v:,.2f}.",
            space_id=space_id,
            session_id=session_id,
            question=question,
            why="average of them",
        )
    delta = float(add.group(1))  # type: ignore[union-attr]
    if len(nums) != 1:
        return _abstain(
            space_id=space_id,
            session_id=session_id,
            why="add N: prior turn was not a single scalar",
        )
    sql = f"SELECT ROUND({nums[0]:.10g} + {delta:.10g}, 2) AS adjusted_myr"
    try:
        rows = execute_sql(sql, path=warehouse)
    except Exception:  # noqa: BLE001
        return _abstain(
            space_id=space_id,
            session_id=session_id,
            why="add N: compute failed",
        )
    total = float(rows[0]["adjusted_myr"]) if rows else 0.0
    return _pack_compute(
        sql=sql,
        rows=rows,
        text=f"Prior figure plus {delta:g} is RM {total:,.2f}.",
        space_id=space_id,
        session_id=session_id,
        question=question,
        why=f"add {delta:g}",
    )
