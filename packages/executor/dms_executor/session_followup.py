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

from dms_executor.abstain import build_abstain
from dms_executor.demo_warehouse import execute_sql, sql_has_reserved_as_of, stamp_engine_clock
from dms_executor.envelope import (
    assert_envelope_valid,
    build_answer_envelope,
    reserved_as_of_abstain,
)
from dms_executor.grant_struct import (
    customer_grant_reason,
    serve_gap,
    sql_refusal_envelope,
)

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
    stamp = env.get("index_stamp")
    stamp_s = stamp if isinstance(stamp, str) and stamp else ""
    if env.get("abstained") is True:
        if stamp_s:
            return {"index_stamp": stamp_s}
        return None
    nums = numeric_values(env)
    if not nums:
        if stamp_s:
            return {"index_stamp": stamp_s}
        return None
    out: dict[str, Any] = {"values": nums, "sql_used": env.get("sql_used")}
    if stamp_s:
        out["index_stamp"] = stamp_s
    return out


def _abstain(*, space_id: str | None, session_id: str | None, why: str) -> dict[str, Any]:
    env = build_abstain(
        reason="abstain",
        stage="followup",
        answer_id="ans_followup_abstain",
        text=(
            "I cannot compute that follow-up without a prior numeric answer in "
            "this session. Ask the parent question first."
        ),
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
    stamp_engine_clock(env)
    return env


def _followup_execute(
    sql: str,
    *,
    warehouse: Path | None,
    space_id: str | None,
    session_id: str | None,
    question: str,
    grantable: set[str] | None = None,
    dialect: str | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Rows, or a reserved_param abstain. The placeholder never reaches DuckDB."""
    if sql_has_reserved_as_of(sql):
        return None, reserved_as_of_abstain(
            space_id=space_id,
            session_id=session_id,
            route="followup",
            question=question,
        )
    gap = serve_gap(
        sql,
        grantable=set(grantable or ()),
        dialect=dialect,
    )
    if gap:
        return None, sql_refusal_envelope(
            reason=gap,
            shown=customer_grant_reason(gap),
            space_id=space_id,
            session_id=session_id,
            route="followup",
            question=question,
        )
    return execute_sql(sql, path=warehouse, product=True), None


def run_followup_sql(
    sql: str,
    *,
    warehouse: Path | None,
    space_id: str | None,
    session_id: str | None,
    question: str,
    why: str,
    text: str,
    grantable: set[str] | None = None,
    dialect: str | None = None,
) -> dict[str, Any]:
    """Follow-up SQL. A real $as_of placeholder abstains and does not run."""
    rows, refused = _followup_execute(
        sql,
        warehouse=warehouse,
        space_id=space_id,
        session_id=session_id,
        question=question,
        grantable=grantable,
        dialect=dialect,
    )
    if refused is not None:
        return refused
    return _pack_compute(
        sql=sql,
        rows=rows or [],
        text=text,
        space_id=space_id,
        session_id=session_id,
        question=question,
        why=why,
    )


def _followup_relation(
    grantable: set[str] | None, prior: dict[str, Any] | None
) -> str | None:
    """A granted base table for a follow-up constant. None if the space grants none.

    The figure is already known. The statement still has to read a granted
    table, or the grant check refuses it.
    """
    grants = {str(name).strip() for name in (grantable or ()) if str(name).strip()}
    cited = [str(name).strip() for name in ((prior or {}).get("grounded_tables") or [])]
    for name in [item for item in cited if item in grants] + sorted(grants):
        if name.isidentifier():
            return name
    return None


def maybe_followup(
    question: str,
    *,
    prior: dict[str, Any] | None,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
    tables: list[str] | None = None,
    grantable: set[str] | None = None,
    dialect: str | None = None,
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
        relation = _followup_relation(grantable, prior)
        if relation is None:
            return _abstain(
                space_id=space_id,
                session_id=session_id,
                why="average of them: no granted table",
            )
        sql = (
            f"SELECT ROUND(({expr}) / {len(nums)}.0, 2) AS average_myr "
            f"FROM {relation} LIMIT 1"
        )
        try:
            rows, refused = _followup_execute(
                sql,
                warehouse=warehouse,
                space_id=space_id,
                session_id=session_id,
                question=question,
                grantable=grantable,
                dialect=dialect,
            )
        except Exception:  # noqa: BLE001
            return _abstain(
                space_id=space_id,
                session_id=session_id,
                why="average of them: compute failed",
            )
        if refused is not None:
            return refused
        avg_v = float(rows[0]["average_myr"]) if rows else 0.0
        return _pack_compute(
            sql=sql,
            rows=rows or [],
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
    relation = _followup_relation(grantable, prior)
    if relation is None:
        return _abstain(
            space_id=space_id,
            session_id=session_id,
            why="add N: no granted table",
        )
    sql = (
        f"SELECT ROUND({nums[0]:.10g} + {delta:.10g}, 2) AS adjusted_myr "
        f"FROM {relation} LIMIT 1"
    )
    try:
        rows, refused = _followup_execute(
            sql,
            warehouse=warehouse,
            space_id=space_id,
            session_id=session_id,
        question=question,
        grantable=grantable,
        dialect=dialect,
    )
    except Exception:  # noqa: BLE001
        return _abstain(
            space_id=space_id,
            session_id=session_id,
            why="add N: compute failed",
        )
    if refused is not None:
        return refused
    total = float(rows[0]["adjusted_myr"]) if rows else 0.0
    return _pack_compute(
        sql=sql,
        rows=rows or [],
        text=f"Prior figure plus {delta:g} is RM {total:,.2f}.",
        space_id=space_id,
        session_id=session_id,
        question=question,
        why=f"add {delta:g}",
    )
