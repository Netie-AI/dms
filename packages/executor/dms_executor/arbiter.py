"""DMS_AI_ARBITER. Default off.

COMPILE-CHECK already decides whether a compile SQL may be submitted. This
module does not take that lane and does not turn ``DMS_AI_FIRST`` on.

The arbiter runs only when a compile SQL and a model SQL both exist and
their result sets differ. Equal sets serve the model SQL, stamped ``ai``
with ``agreed_with=compile``. Different sets call
``shape_check.check_answer_shape``. One ``fit`` wins. Both, neither, or a
missing checker reconfirm ``arbiter_disagree`` and serve no rows.

A missing ``shape_check`` module is a verdict of unknown, not a crash and
not a permission to keep either candidate.

This module does not read the question for a phrase, does not parse SQL
into shape facts, and does not name a provider or a model.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from dms_executor.route_stamp import (
    ROUTE_AI,
    ROUTE_AI_EXAMPLE,
    ROUTE_CERTIFIED,
    ROUTE_RULE,
    freeze_route,
)

ARBITER_ENV = "DMS_AI_ARBITER"
_ON = frozenset({"1", "true", "yes", "on"})
_AI_ROUTES = frozenset({"model", "ai", "ai_with_example"})
_AI_ORIGINS = frozenset({"generate_sql", "model_sql", "ai"})
_COMPILE_ORIGINS = frozenset({"ontology_compile", "ontology_ranking", "ontology_plan"})
_SERVED = frozenset({ROUTE_AI, ROUTE_AI_EXAMPLE, ROUTE_CERTIFIED, ROUTE_RULE})
_SAMPLE = 5


@dataclass(frozen=True)
class ResultSet:
    """One executed candidate. ``executed`` is false when the SQL did not run."""

    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    envelope: dict[str, Any] | None
    executed: bool


def arbiter_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``DMS_AI_ARBITER`` is a true token. Unset is off."""
    src = os.environ if env is None else env
    return str(src.get(ARBITER_ENV) or "").strip().lower() in _ON


@contextmanager
def compile_context():
    """No-op. Compile submit stays with COMPILE-CHECK. The env is not touched."""
    yield


def _cell(value: Any) -> str:
    if isinstance(value, bool):
        return "b1" if value else "b0"
    if isinstance(value, int):
        return "n" + format(float(value), ".12g")
    if isinstance(value, float):
        return "n" + format(value, ".12g")
    if value is None:
        return "z"
    return "s" + str(value)


def _canon_row(row: Sequence[Any]) -> tuple[str, ...]:
    return tuple(_cell(item) for item in row)


def results_equal(left: ResultSet, right: ResultSet) -> bool:
    """Column count, row count, then values after a stable sort."""
    if len(left.columns) != len(right.columns):
        return False
    if len(left.rows) != len(right.rows):
        return False
    return sorted(_canon_row(row) for row in left.rows) == sorted(
        _canon_row(row) for row in right.rows
    )


def table_of(rows: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    columns: list[str] = []
    kept: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        kept.append(row)
        for key in row:
            name = str(key)
            if name not in columns:
                columns.append(name)
    data = [[row.get(col) for col in columns] for row in kept]
    return columns, data


def _executed(item: ResultSet | None) -> bool:
    return item is not None and item.executed


def _servable(item: ResultSet | None) -> bool:
    if not _executed(item) or item is None:
        return False
    env = item.envelope
    return isinstance(env, dict) and env.get("abstained") is not True


def _fold_sql(sql: str) -> str:
    return " ".join(str(sql or "").split())


def _ai_token(item: ResultSet, examples: Sequence[Mapping[str, Any]]) -> str:
    folded = _fold_sql(item.sql)
    for row in examples:
        if folded and folded == _fold_sql(str(row.get("sql") or "")):
            return ROUTE_AI_EXAMPLE
    return ROUTE_AI


def _preview(role: str, item: ResultSet) -> dict[str, Any]:
    """SQL, columns, and a count. Cell values stay out of the envelope."""
    return {
        "role": role,
        "sql": item.sql,
        "columns": list(item.columns),
        "row_count": len(item.rows),
    }


def _reconfirm(
    question: str,
    reason_code: str,
    previews: list[dict[str, Any]],
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain

    if reason_code == "compile_without_ai":
        text = "I don't have a checked model answer, so I am not stating a figure."
    else:
        text = "The checked results disagree, so I am not stating a figure."
    env = build_abstain(
        reason=reason_code,
        question=question,
        stage="reconfirm_direct",
        answer_id="ans_arbiter",
        text=text,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[reason_code],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
    )
    env["reconfirm"] = {
        "reason_code": reason_code,
        "reason_text": text,
        "suggested_question": "",
        "previews": previews,
    }
    env["rows"] = []
    env["values"] = []
    env["sql_used"] = None
    return env


def _materialize(
    item: ResultSet,
    question: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    env = item.envelope
    if isinstance(env, dict) and env.get("abstained") is not True:
        return dict(env)
    from dms_executor.envelope import build_answer_envelope

    columns = list(item.columns)
    dict_rows = [
        {columns[i]: row[i] for i in range(len(columns)) if i < len(row)} for row in item.rows
    ]
    return build_answer_envelope(
        answer_id="ans_arbiter_compile",
        text="Checked answer.",
        badge="L2_VALIDATED",
        abstained=False,
        rows=dict_rows,
        sql_used=item.sql,
        assumptions=["arbiter: shape_fits"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
        question=question,
        audit_id="ans_arbiter_compile",
    )


def _verdict_of(got: Any) -> str:
    verdict = getattr(got, "verdict", None)
    if verdict is None and isinstance(got, Mapping):
        verdict = got.get("verdict")
    text = str(verdict or "").strip().lower()
    # R1-SHAPE-CHECK-01 says "fit". The ticket word is "fits". Same decision.
    if text == "fit":
        return "fits"
    if text in {"fits", "misfit", "unknown"}:
        return text
    return "unknown"


def _check_pair(
    question: str,
    left: ResultSet,
    right: ResultSet,
    ontology_slice: Mapping[str, Any],
) -> tuple[str, str] | None:
    """None when the checker module is missing. A call error is ``unknown``."""
    try:
        from dms_executor.shape_check import check_answer_shape
    except ImportError:
        return None
    if not callable(check_answer_shape):
        return None

    def one(item: ResultSet) -> str:
        columns = list(item.columns)
        sample = [list(row) for row in item.rows[:_SAMPLE]]
        shaped = [
            {columns[i]: row[i] for i in range(len(columns)) if i < len(row)} for row in sample
        ]
        try:
            try:
                # shape_check.py (R1-SHAPE-CHECK-01) is keyword-only after sql.
                got = check_answer_shape(
                    question,
                    item.sql,
                    columns=[{"name": name} for name in columns],
                    sample_rows=shaped,
                    row_count=len(item.rows),
                    ontology=dict(ontology_slice),
                )
            except TypeError:
                got = check_answer_shape(
                    question,
                    item.sql,
                    columns,
                    sample,
                    len(item.rows),
                    dict(ontology_slice),
                )
        except Exception:
            return "unknown"
        return _verdict_of(got)

    return one(left), one(right)


def _pick(
    question: str,
    left: ResultSet,
    right: ResultSet,
    ontology_slice: Mapping[str, Any],
) -> str:
    pair = _check_pair(question, left, right, ontology_slice)
    if pair is None:
        return "missing"
    left_ok = pair[0] == "fits"
    right_ok = pair[1] == "fits"
    if left_ok and not right_ok:
        return "left"
    if right_ok and not left_ok:
        return "right"
    return "reconfirm"


def _from_pick(
    question: str,
    left: ResultSet,
    right: ResultSet,
    ontology_slice: Mapping[str, Any],
    *,
    left_route: str,
    right_route: str,
    left_role: str,
    right_role: str,
    space_id: str | None,
    session_id: str | None,
) -> tuple[dict[str, Any], str, str]:
    previews = [_preview(left_role, left), _preview(right_role, right)]
    picked = _pick(question, left, right, ontology_slice)
    if picked == "missing":
        # Missing checker is not a fit. Compile rows are not served.
        env = _reconfirm(
            question,
            "arbiter_disagree",
            previews,
            space_id=space_id,
            session_id=session_id,
        )
        return env, ROUTE_RULE, "shape_check_missing"
    if picked == "left":
        env = _materialize(left, question, space_id=space_id, session_id=session_id)
        if env.get("abstained") is True:
            env = _reconfirm(
                question,
                "arbiter_disagree",
                previews,
                space_id=space_id,
                session_id=session_id,
            )
            return env, ROUTE_RULE, "arbiter_disagree"
        return env, left_route, "shape_fits"
    if picked == "right":
        env = _materialize(right, question, space_id=space_id, session_id=session_id)
        if env.get("abstained") is True:
            env = _reconfirm(
                question,
                "arbiter_disagree",
                previews,
                space_id=space_id,
                session_id=session_id,
            )
            return env, ROUTE_RULE, "arbiter_disagree"
        return env, right_route, "shape_fits"
    env = _reconfirm(
        question,
        "arbiter_disagree",
        previews,
        space_id=space_id,
        session_id=session_id,
    )
    return env, ROUTE_RULE, "arbiter_disagree"


def serve(
    *,
    question: str,
    ai: ResultSet | None = None,
    compile: ResultSet | None = None,
    certified: ResultSet | None = None,
    ontology_slice: Mapping[str, Any] | None = None,
    examples: Sequence[Mapping[str, Any]] | None = None,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Choose the served envelope. Every return sets ``served_route``."""
    onto = dict(ontology_slice or {})
    ex = list(examples or [])
    if _executed(certified) and _executed(ai) and certified is not None and ai is not None:
        if results_equal(certified, ai) and _servable(certified):
            out = dict(certified.envelope or {})
            out.pop("agreed_with", None)
            out["agreed_with"] = "ai"
            out["served_route"] = ROUTE_CERTIFIED
            out["route_reason"] = "certified_agreed"
            freeze_route(out)
            return out
        out, route, reason = _from_pick(
            question,
            certified,
            ai,
            onto,
            left_route=ROUTE_CERTIFIED,
            right_route=_ai_token(ai, ex),
            left_role="certified",
            right_role="ai",
            space_id=space_id,
            session_id=session_id,
        )
        out.pop("agreed_with", None)
        out["served_route"] = route
        out["route_reason"] = reason
        freeze_route(out)
        return out
    if _executed(compile) and _executed(ai) and compile is not None and ai is not None:
        if results_equal(compile, ai) and _servable(ai):
            out = dict(ai.envelope or {})
            out.pop("agreed_with", None)
            out["agreed_with"] = "compile"
            out["served_route"] = ROUTE_AI
            out["route_reason"] = "agreed_with_compile"
            freeze_route(out)
            return out
        out, route, reason = _from_pick(
            question,
            compile,
            ai,
            onto,
            left_route=ROUTE_RULE,
            right_route=_ai_token(ai, ex),
            left_role="compile",
            right_role="ai",
            space_id=space_id,
            session_id=session_id,
        )
        out.pop("agreed_with", None)
        out["served_route"] = route
        out["route_reason"] = reason
        freeze_route(out)
        return out
    if _servable(certified) and certified is not None and not _executed(ai):
        out = dict(certified.envelope or {})
        out.pop("agreed_with", None)
        out["served_route"] = ROUTE_CERTIFIED
        out["route_reason"] = "certified_no_ai_sql"
        freeze_route(out)
        return out
    if _servable(ai) and ai is not None and not _executed(compile):
        out = dict(ai.envelope or {})
        out.pop("agreed_with", None)
        out["served_route"] = _ai_token(ai, ex)
        out["route_reason"] = "ai_only"
        freeze_route(out)
        return out
    if compile is not None:
        out = _reconfirm(
            question,
            "compile_without_ai",
            [_preview("compile", compile)],
            space_id=space_id,
            session_id=session_id,
        )
        out["served_route"] = ROUTE_RULE
        out["route_reason"] = "compile_without_ai"
        freeze_route(out)
        return out
    out = _reconfirm(
        question,
        "no_candidate",
        [],
        space_id=space_id,
        session_id=session_id,
    )
    out["served_route"] = ROUTE_RULE
    out["route_reason"] = "no_candidate"
    freeze_route(out)
    return out


def serve_certified(env: dict[str, Any], *, question: str) -> dict[str, Any]:
    """L1 serve when no model SQL ran. Disagreement is ``serve`` with both sets."""
    raw = env.get("rows")
    rows = raw if isinstance(raw, list) else []
    columns, data = table_of(rows)
    item = ResultSet(
        sql=str(env.get("sql_used") or ""),
        columns=tuple(columns),
        rows=tuple(tuple(row) for row in data),
        envelope=env,
        executed=True,
    )
    return serve(
        question=question,
        certified=item,
        space_id=env.get("space_id") if isinstance(env.get("space_id"), str) else None,
        session_id=env.get("session_id") if isinstance(env.get("session_id"), str) else None,
    )


def _is_compile_env(env: Mapping[str, Any]) -> bool:
    if env.get("abstained") is True:
        return False
    origin = str(env.get("plan_origin") or "").strip().lower()
    if origin in _AI_ORIGINS:
        return False
    route = str(env.get("served_route") or "").strip().lower()
    rung = str(env.get("ladder_rung") or "").strip().lower()
    if route == "ontology_compile" or rung == "compile":
        return True
    return origin in _COMPILE_ORIGINS


def _ai_sql(env: Mapping[str, Any]) -> str:
    if env.get("abstained") is True or _is_compile_env(env):
        return ""
    sql = str(env.get("sql_used") or "").strip()
    if not sql or sql.startswith("--"):
        return ""
    route = str(env.get("served_route") or "").strip().lower()
    origin = str(env.get("plan_origin") or "").strip().lower()
    if route in _AI_ROUTES or origin in _AI_ORIGINS:
        return sql
    return ""


def _compile_sql(examples: Sequence[Mapping[str, Any]]) -> str:
    for row in examples:
        if str(row.get("source") or "") != "compile":
            continue
        sql = str(row.get("sql") or "").strip()
        if sql:
            return sql
    return ""


def _from_envelope(env: Mapping[str, Any], *, sql: str) -> ResultSet:
    raw = env.get("rows")
    rows = raw if isinstance(raw, list) else []
    columns, data = table_of(rows)
    return ResultSet(
        sql=sql,
        columns=tuple(columns),
        rows=tuple(tuple(row) for row in data),
        envelope=dict(env),
        executed=True,
    )


def _held(sql: str) -> ResultSet:
    return ResultSet(sql=sql, columns=(), rows=(), envelope=None, executed=False)


def _run_sql(submit: Callable[[str], Any], sql: str) -> tuple[list[str], list[list[Any]]] | None:
    try:
        result = submit(sql)
    except Exception:
        return None
    if getattr(result, "ok", None) is False:
        return None
    from dms_executor.verified_queries import rows_from_submit_result

    columns, data = table_of(rows_from_submit_result(result))
    return columns, data


def gate_generative(
    env: dict[str, Any] | None,
    **kwargs: Any,
) -> dict[str, Any] | None:
    """Flag off returns the same envelope object. Flag on runs the arbiter."""
    if not arbiter_enabled():
        return env
    return apply_generative(env, **kwargs)


def apply_generative(
    env: dict[str, Any] | None,
    *,
    question: str,
    submit: Callable[[str], Any],
    space_id: str | None = None,
    session_id: str | None = None,
    ontology_slice: Mapping[str, Any] | None = None,
    examples: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Stamp or replace the generative envelope. ``None`` means the lane missed."""
    if examples is None:
        from dms_executor.ai_first import current_examples

        held_examples = current_examples()
    else:
        held_examples = [dict(row) for row in examples if isinstance(row, Mapping)]
    compile_sql = _compile_sql(held_examples)
    onto = dict(ontology_slice or {})
    ai_sql = _ai_sql(env) if isinstance(env, dict) else ""
    # One SQL is not a disagreement. Compile-only and model-only stay as served.
    if not ai_sql or not compile_sql or not isinstance(env, dict):
        return env
    ai_item = _from_envelope(env, sql=ai_sql)
    ran = _run_sql(submit, compile_sql)
    if ran is None:
        # Cannot compare. Do not serve a guess.
        return serve(
            question=question,
            compile=_held(compile_sql),
            examples=held_examples,
            ontology_slice=onto,
            space_id=space_id,
            session_id=session_id,
        )
    columns, data = ran
    compile_item = ResultSet(
        sql=compile_sql,
        columns=tuple(columns),
        rows=tuple(tuple(row) for row in data),
        envelope=None,
        executed=True,
    )
    return serve(
        question=question,
        ai=ai_item,
        compile=compile_item,
        examples=held_examples,
        ontology_slice=onto,
        space_id=space_id,
        session_id=session_id,
    )


def served_routes() -> frozenset[str]:
    return _SERVED
