"""SCALE-MISS-01. Default off. Digit 1 only.

On, the first model call is SQL generation, before clarify and retrieval.
A step that has only the ask-back margin left returns an ask-back, or a
partial envelope when one was stored. It does not abstain as a timeout.

EXPLAIN failures are sent back once with schema and ontology context, then
once more with ``route_hint=escalate``. OpenVault picks the route. DMS does
not name a model.

An empty generate retries with a plan, then SQL from that plan, before any
ask-back. A vague question asks back with 2 or 3 readings. Compile SQL stays.
When a checked model SQL exists and the shape judge says the compile SQL is
a misfit, the model SQL is what gets submitted.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

ENV = "DMS_SCALE_MISS"
MARGIN_S = 2.0
_LEASE = frozenset(
    {
        "ServingLeaseCap",
        "ServingLeaseQueueFull",
        "ServingWaitCancelled",
        "OpenVaultTokenError",
    }
)
_DEADLINE = frozenset({"ServingDeadlineExceeded", "ServingDeadlineReserve"})
_FILL = (
    "Show one measure for this Space.",
    "Break that measure down by one granted column.",
    "Limit the reading to one granted table.",
)


class Turn:
    """One ask. Cleared by ``maybe_generative_ask``."""

    def __init__(self) -> None:
        self.ready = False
        self.payload: dict[str, Any] | None = None
        self.checked_sql = ""
        self.sql_so_far = ""
        self.plan_tried = False
        self.repairs = 0
        self.calls = 0
        self.steps: list[str] = []
        self.partial: dict[str, Any] | None = None
        self.compute: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None
        self.ontology: Any = None
        self.warehouse: Path | None = None
        self.grantable: set[str] = set()
        self.dialect = ""
        self.space_id: str | None = None
        self.session_id: str | None = None
        self.question = ""


_TURN: ContextVar[Turn | None] = ContextVar("dms_scale_miss", default=None)


def enabled() -> bool:
    """True only when ``DMS_SCALE_MISS`` is the digit 1."""
    import os

    return os.environ.get(ENV, "0").strip() == "1"


def ladder_reserves() -> bool:
    """The climb keeps a call back only while this flag is off."""
    return not enabled()


def bind_turn(turn: Turn | None) -> Token[Turn | None]:
    return _TURN.set(turn)


def reset_turn(token: Token[Turn | None]) -> None:
    _TURN.reset(token)


def current() -> Turn | None:
    return _TURN.get()


def turn_ready() -> bool:
    turn = _TURN.get()
    return turn is not None and turn.ready


def cached_payload() -> dict[str, Any] | None:
    turn = _TURN.get()
    if turn is None:
        return None
    return turn.payload


def calls_used() -> int:
    turn = _TURN.get()
    if turn is None:
        return 0
    return turn.calls


def over_budget() -> bool:
    """True when only the ask-back margin remains. No clock means not over."""
    if not enabled():
        return False
    import time

    from cortex_client.ask_clock import current_clock

    clock = current_clock()
    if clock is None or clock.deadline is None:
        return False
    return (clock.deadline - time.monotonic()) <= MARGIN_S


def begin(
    *,
    question: str,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None] | None,
    ontology: Any,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """First SQL attempt, or an ask-back. None means the caller continues.

    Generation is recorded before any retrieval the caller still has to do.
    """
    from dms_core.ask_stream import note_stage

    from dms_executor.keyword_routes import vague_question

    turn = Turn()
    turn.question = question
    turn.compute = compute
    turn.ontology = ontology
    turn.warehouse = warehouse
    turn.grantable = set(grantable)
    turn.dialect = dialect
    turn.space_id = space_id
    turn.session_id = session_id
    _TURN.set(turn)
    if vague_question(question):
        turn.steps.append("askback")
        note_stage("reconfirm", "done")
        return _askback(
            question,
            why="vague_question",
            interpretations=_offer_readings(turn),
            space_id=space_id,
            session_id=session_id,
        )
    if compute is None:
        return None
    if over_budget():
        turn.steps.append("askback")
        note_stage("reconfirm", "done")
        return _askback(
            question,
            why="no_sql",
            interpretations=_offer_readings(turn),
            space_id=space_id,
            session_id=session_id,
        )
    turn.steps.append("generate")
    note_stage("insights_generate", "started")
    from dms_executor.askback_specific import enabled as specific_enabled
    from dms_executor.askback_specific import grounded_generate_prompt
    from dms_executor.sql_vote import reading_prompt

    vote_prompt = reading_prompt(question, reading=1)
    if specific_enabled():
        vote_prompt = grounded_generate_prompt(turn)
    payload = _invoke(
        turn,
        {
            "sql_vote_prompt": vote_prompt,
            "sql_vote_kind": "direct",
        },
    )
    note_stage("insights_generate", "done")
    sql = _sql_of(payload)
    if not sql:
        payload, sql = _plan_then_sql(turn)
    if sql:
        payload, sql = _repair_explain(turn, payload, sql)
    if sql:
        turn.sql_so_far = sql
    turn.payload = payload if isinstance(payload, dict) else None
    if sql and _passes(turn, sql):
        turn.checked_sql = sql
    turn.ready = True
    return None


def budget_stop(
    question: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """Ask-back or a stored partial. None when this step still has time."""
    if not enabled() or not over_budget():
        return None
    return stop_envelope(question, space_id=space_id, session_id=session_id)


def stop_envelope(
    question: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Budget expiry. A stored partial wins. Otherwise an ask-back.

    The envelope does not use a serving-deadline abstain reason.
    """
    from dms_core.ask_stream import note_stage

    turn = _TURN.get()
    if turn is not None and isinstance(turn.partial, dict):
        note_stage("generate", "done")
        return turn.partial
    if turn is not None:
        turn.steps.append("askback")
    note_stage("reconfirm", "done")
    return _askback(
        question,
        why="no_sql",
        interpretations=_offer_readings(turn),
        space_id=space_id,
        session_id=session_id,
    )


def remember_partial(env: dict[str, Any]) -> None:
    """A finished step's envelope. A later budget stop returns it."""
    turn = _TURN.get()
    if turn is None or not isinstance(env, dict):
        return
    turn.partial = env


def compile_override(
    sql: str,
    question: str,
    *,
    grantable: set[str] | None,
    warehouse: Path | None,
) -> str | None:
    """Model SQL when the judge calls the compile SQL a misfit.

    No checked model SQL, or any other verdict, keeps the compile SQL.
    The replacement still has to pass the same checker.
    """
    turn = _TURN.get()
    if not enabled() or turn is None:
        return None
    ai = turn.checked_sql.strip()
    if not ai or ai == str(sql or "").strip():
        return None
    if _judge(turn, question, sql) != "misfit":
        return None
    if not _passes(turn, ai, grantable=grantable, warehouse=warehouse):
        return None
    return ai


def fill_empty_sql(
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    ctx: dict[str, Any],
    question: str,
) -> dict[str, Any] | None:
    """One plan-then-SQL retry. None when that retry already ran or is empty."""
    turn = _TURN.get()
    if turn is None:
        turn = Turn()
        turn.question = question
        turn.compute = compute
        _TURN.set(turn)
    elif turn.compute is None:
        turn.compute = compute
    if turn.plan_tried:
        return None
    payload, sql = _plan_then_sql(turn, ctx=ctx)
    if sql and isinstance(payload, dict):
        turn.payload = payload
        if _passes(turn, sql):
            turn.checked_sql = sql
        return payload
    return None


def empty_sql_askback(question: str) -> dict[str, Any]:
    """Ask-back after the plan-then-SQL retry. Not a bare refusal."""
    turn = _TURN.get()
    space_id = turn.space_id if turn is not None else None
    session_id = turn.session_id if turn is not None else None
    return _askback(
        question,
        why="no_sql",
        interpretations=_offer_readings(turn),
        space_id=space_id,
        session_id=session_id,
    )


def budget_stop_envelope(
    question: str,
    payload: dict[str, Any] | None,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Insights budget stop. A stored partial, or an ask-back. Not an abstain.

    The card says what was understood and the SQL so far, and offers
    continue and narrow it. It does not use the timeout reason.
    """
    turn = _TURN.get()
    if (
        turn is not None
        and isinstance(turn.partial, dict)
        and turn.partial.get("abstained") is not True
        and str(turn.partial.get("badge") or "") != "ABSTAIN"
    ):
        return turn.partial
    sql = ""
    if turn is not None and turn.sql_so_far.strip():
        sql = turn.sql_so_far.strip()
    elif isinstance(payload, dict):
        sql = str(payload.get("query_sql") or "").strip()
    understood = _understood(payload)
    return _budget_card(
        question,
        understood=understood,
        sql=sql,
        space_id=space_id,
        session_id=session_id,
    )


def _understood(payload: dict[str, Any] | None) -> str:
    """Measure and tables from the plan. Empty when the plan named neither."""
    plan: dict[str, Any] | None = None
    if isinstance(payload, dict):
        from dms_executor.sql_vote import vote_plan_from_payload

        parsed = vote_plan_from_payload(payload)
        if isinstance(parsed, dict):
            plan = parsed
        elif isinstance(payload.get("query_plan"), dict):
            plan = payload["query_plan"]
    if not isinstance(plan, dict):
        return "I have not locked a measure yet."
    measure = _safe(str(plan.get("measure") or ""))
    raw_tables = plan.get("tables")
    names = []
    if isinstance(raw_tables, list):
        for item in raw_tables[:3]:
            label = _safe(str(item))
            if label:
                names.append(label)
    if measure and names:
        return f"Understood measure {measure} on {', '.join(names)}."
    if measure:
        return f"Understood measure {measure}."
    return "I have not locked a measure yet."


def _budget_card(
    question: str,
    *,
    understood: str,
    sql: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from datetime import UTC, datetime

    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    text = f"I can continue, or narrow it. {understood}"
    env = build_abstain(
        reason="budget_askback",
        question=question,
        stage="generative",
        answer_id="ans_scale_miss_budget",
        text=text,
        rows=[],
        sql_used=None,
        values=[],
        assumptions=["reconfirm: budget", "action: continue", "action: narrow it"],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    assert_envelope_valid(env)
    env["reconfirm"] = {
        "why": "budget",
        "reason_code": "budget",
        "reason_text": text,
        "suggested_question": question,
        "closest_question": question,
        "understood": understood,
        "sql": sql,
        "actions": [
            {"id": "continue", "label": "continue"},
            {"id": "narrow", "label": "narrow it"},
        ],
    }
    return env


def explain_askback(
    question: str, *, space_id: str | None, session_id: str | None
) -> dict[str, Any]:
    """Repair is exhausted. Ask back. Do not serve the failed SQL."""
    turn = _TURN.get()
    return _askback(
        question,
        why="explain",
        interpretations=_offer_readings(turn),
        space_id=space_id,
        session_id=session_id,
    )


def _plan_then_sql(
    turn: Turn,
    *,
    ctx: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    from dms_executor.sql_vote import plan_prompt, reading_prompt, vote_plan_from_payload

    turn.plan_tried = True
    turn.steps.append("plan")
    base = dict(ctx or {})
    base["sql_vote_prompt"] = plan_prompt(turn.question, _onto_text(turn.ontology))
    base["sql_vote_kind"] = "plan"
    base.pop("route_hint", None)
    payload = _invoke(turn, base)
    plan = vote_plan_from_payload(payload if isinstance(payload, dict) else None)
    if plan is None and isinstance(payload, dict):
        raw = payload.get("query_plan")
        plan = raw if isinstance(raw, dict) else None
    sql = _sql_of(payload)
    if isinstance(plan, dict) and plan and not sql:
        turn.steps.append("plan_solve")
        solve = dict(base)
        solve["sql_vote_prompt"] = reading_prompt(
            turn.question,
            reading=2,
            plan=plan,
            ontology=_onto_text(turn.ontology),
        )
        solve["sql_vote_kind"] = "plan_solve"
        solve.pop("route_hint", None)
        solved = _invoke(turn, solve)
        solved_sql = _sql_of(solved)
        if solved_sql:
            return solved if isinstance(solved, dict) else None, solved_sql
    return payload if isinstance(payload, dict) else None, sql


def _repair_explain(
    turn: Turn,
    payload: dict[str, Any] | None,
    sql: str,
) -> tuple[dict[str, Any] | None, str]:
    from dms_executor.ask_ladder import is_direct_abstain

    why = _passes_reason(turn, sql)
    if not why or is_direct_abstain(why) or not str(why).startswith("explain:"):
        return payload, sql
    for escalate in (False, True):
        turn.repairs += 1
        turn.steps.append("escalate" if escalate else "repair")
        nxt = _invoke(turn, _repair_ctx(turn, sql, why, escalate=escalate))
        nxt_sql = _sql_of(nxt)
        if not nxt_sql:
            continue
        payload = nxt if isinstance(nxt, dict) else payload
        sql = nxt_sql
        why = _passes_reason(turn, sql)
        if not why or not str(why).startswith("explain:"):
            return payload, sql
    return payload, sql


def _repair_ctx(turn: Turn, sql: str, why: str, *, escalate: bool) -> dict[str, Any]:
    extra = _richer(turn, sql)
    prompt = (
        "The previous SELECT failed EXPLAIN. "
        "Write one SELECT that fixes that failure. "
        "Use only granted tables and columns.\n"
        f"feedback:\n{why}\n"
        f"previous_sql:\n{sql}\n"
        f"{extra}\n"
        f"Question:\n{turn.question}"
    )
    ctx: dict[str, Any] = {
        "sql_vote_prompt": prompt,
        "sql_vote_kind": "escalate" if escalate else "repair",
        "sql_loop_feedback": {"previous_sql": sql, "reason": why},
    }
    if escalate:
        ctx["route_hint"] = "escalate"
    return ctx


def _richer(turn: Turn, sql: str) -> str:
    from dms_executor.demo_warehouse import SERVING_DIALECT

    dialect = turn.dialect or SERVING_DIALECT
    parts: list[str] = []
    try:
        from dms_executor.ask_ladder import richer_context_block

        block = richer_context_block(
            turn.question,
            sql,
            warehouse=turn.warehouse,
            grantable=set(turn.grantable),
            dialect=dialect,
            ontology=turn.ontology,
            space_id=turn.space_id,
        )
        if block:
            parts.append(block)
    except Exception:
        pass
    try:
        from dms_executor.schema_retry import build_schema_slice, format_slice_block

        sliver = build_schema_slice(
            sql,
            warehouse=turn.warehouse,
            grantable=set(turn.grantable),
            dialect=dialect,
            ontology=turn.ontology,
        )
        text = format_slice_block(sliver)
        if text.strip():
            parts.append(text)
    except Exception:
        pass
    return "\n".join(parts)


def _judge(turn: Turn, question: str, sql: str) -> str:
    prompt = (
        "Decide whether the SQL answers the question's shape. "
        "Reply with a JSON object whose only key is verdict. "
        "verdict is fit, misfit, or unknown.\n"
        f"SQL:\n{sql}\n"
        f"Question:\n{question}"
    )
    payload = _invoke(
        turn,
        {
            "sql_vote_prompt": prompt,
            "sql_vote_kind": "shape",
            "route_hint": "reason",
        },
    )
    return _verdict(payload)


def _verdict(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "unknown"
    raw = payload.get("verdict")
    if raw in {"fit", "misfit", "unknown"}:
        return str(raw)
    for key in ("query_sql", "text", "answer", "content"):
        text = payload.get(key)
        if not isinstance(text, str):
            continue
        found = _json_dict(text)
        if isinstance(found, dict) and found.get("verdict") in {"fit", "misfit", "unknown"}:
            return str(found["verdict"])
    return "unknown"


def _offer_readings(turn: Turn | None) -> list[str]:
    """Flag off keeps the canned list. On, the specific card writes its own."""
    from dms_executor.askback_specific import enabled as specific_enabled

    if specific_enabled():
        return []
    if turn is None:
        return list(_FILL[:2])
    return _readings(turn)


def _readings(turn: Turn | None) -> list[str]:
    found: list[str] = []
    if turn is not None and turn.compute is not None:
        prompt = (
            "Reply with a JSON object whose key readings is an array of "
            "2 or 3 concrete questions this schema can answer.\n"
            f"Question:\n{turn.question}"
        )
        payload = _invoke(
            turn,
            {
                "sql_vote_prompt": prompt,
                "sql_vote_kind": "readings",
                "route_hint": "reason",
            },
        )
        found = _reading_list(payload)
    if len(found) < 2:
        found.extend(_measure_readings(turn.ontology if turn is not None else None))
    cleaned = [_safe(item) for item in found]
    cleaned = [item for item in cleaned if item]
    for filler in _FILL:
        if len(cleaned) >= 2:
            break
        if filler not in cleaned:
            cleaned.append(filler)
    return cleaned[:3]


def _reading_list(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("readings")
    if not isinstance(raw, list):
        for key in ("query_sql", "text", "answer", "content"):
            text = payload.get(key)
            if isinstance(text, str):
                parsed = _json_dict(text)
                if isinstance(parsed, dict):
                    raw = parsed.get("readings")
                    break
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        if len(out) == 3:
            break
    return out


def _measure_readings(ontology: Any) -> list[str]:
    measures = getattr(ontology, "measures", None)
    if not isinstance(measures, dict):
        return []
    out: list[str] = []
    for name in list(measures)[:3]:
        label = _safe(str(name).replace("_", " "))
        if label:
            out.append(f"Show {label} for this Space.")
    return out


def _safe(text: str) -> str:
    kept = "".join(ch for ch in str(text or "") if not ch.isdigit())
    return " ".join(kept.split())[:180]


def _askback(
    question: str,
    *,
    why: str,
    interpretations: list[str],
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.askback_specific import enabled as specific_enabled
    from dms_executor.askback_specific import specific_askback

    if specific_enabled():
        return specific_askback(
            question,
            why=why,
            turn=_TURN.get(),
            space_id=space_id,
            session_id=session_id,
            margin=over_budget(),
        )
    from datetime import UTC, datetime

    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    shown = [item for item in interpretations if item][:3]
    for filler in _FILL:
        if len(shown) >= 2:
            break
        if filler not in shown:
            shown.append(filler)
    shown = shown[:3]
    text = "I need one of these readings: " + " | ".join(shown)
    env = build_abstain(
        reason=why,
        question=question,
        stage="generative",
        answer_id="ans_scale_miss_askback",
        text=text,
        rows=[],
        sql_used=None,
        values=[],
        assumptions=[f"reconfirm: {why}", *[f"reading: {item}" for item in shown]],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    assert_envelope_valid(env)
    env["reconfirm"] = {
        "why": why,
        "reason_code": why,
        "reason_text": text,
        "suggested_question": shown[0],
        "closest_question": shown[0],
        "interpretations": shown,
    }
    return env


def _invoke(turn: Turn, ctx: dict[str, Any]) -> dict[str, Any] | None:
    if turn.compute is None:
        return None
    turn.calls += 1
    try:
        payload = turn.compute(ctx)
    except Exception as exc:
        if type(exc).__name__ in _DEADLINE:
            raise
        if type(exc).__name__ in _LEASE:
            raise
        return None
    return payload if isinstance(payload, dict) else None


def _sql_of(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    if payload.get("verdict") or payload.get("readings"):
        return ""
    return str(payload.get("query_sql") or "").strip()


def _passes(
    turn: Turn,
    sql: str,
    *,
    grantable: set[str] | None = None,
    warehouse: Path | None = None,
) -> bool:
    return _passes_reason(turn, sql, grantable=grantable, warehouse=warehouse) is None


def _passes_reason(
    turn: Turn,
    sql: str,
    *,
    grantable: set[str] | None = None,
    warehouse: Path | None = None,
) -> str | None:
    from dms_executor.generative_ask import validate_compiled_sql

    allowed = set(turn.grantable) if grantable is None else set(grantable)
    lake = turn.warehouse if warehouse is None else warehouse
    try:
        return validate_compiled_sql(
            sql,
            grantable=allowed,
            warehouse=lake,
            dialect=turn.dialect or None,
        )
    except Exception:
        return "explain:check_failed"


def _onto_text(ontology: Any) -> str:
    try:
        from dms_executor.shape_check import ontology_slice

        return json.dumps(ontology_slice(ontology), sort_keys=True, default=str)[:4000]
    except Exception:
        return "{}"


def _json_dict(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
