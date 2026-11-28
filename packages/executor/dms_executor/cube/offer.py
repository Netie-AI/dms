"""Offer approved cubes on the ask prompt and accept a cube plan.

``DMS_METRIC_CUBE`` defaults off. Off, ``attach_catalog`` returns the same
context object and the ask path does not call this module.

The model chooses the cube. Two fits are a clarify the model returns, with
2 to 4 readings. This module does not decide that from the question text.
An unknown member is ``cube_member_unknown`` and is not served. The caller
then continues with normal model SQL.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dms_core.control_plane.cube_store import cube_store, metric_cube_flag
from pydantic import ValidationError

from dms_executor.cube.compile import REASON_UNKNOWN, CubePlanError, compile_plan
from dms_executor.cube.model import Cube, CubePlan
from dms_executor.route_stamp import ROUTE_AI_CUBE, stamp_route

FLAG = "DMS_METRIC_CUBE"
_ROLE = "reason"


def metric_cube_enabled() -> bool:
    """True only when ``DMS_METRIC_CUBE`` is the digit 1."""
    return metric_cube_flag()


def catalog_text(cubes: list[Cube]) -> str:
    """Prompt block. Empty when nothing is approved."""
    approved = [cube for cube in cubes if cube.status == "approved"]
    if not approved:
        return ""
    body = json.dumps(
        [cube.model_dump() for cube in approved],
        sort_keys=True,
        default=str,
    )
    return (
        "Approved metric cubes. Choose one cube and its measures and dimensions "
        "from this list. If two cubes or two measures fit, return a clarify with "
        "2 to 4 readings. Do not guess. Role: " + _ROLE + ". Reply with a JSON object cube_plan "
        "{cube, measures, dimensions, time_dimension, filters, order, limit} "
        "or {clarify: true, readings: [{id, text}]}.\n" + body
    )


def attach_catalog(ctx: dict[str, Any], cubes: list[Cube]) -> dict[str, Any]:
    """Add the catalog to the prompt the model already receives.

    Flag off returns ``ctx`` itself. No approved cube also returns ``ctx``.
    """
    if not metric_cube_enabled():
        return ctx
    block = catalog_text(cubes)
    if not block:
        return ctx
    out = dict(ctx)
    prior = out.get("governed_definitions")
    if isinstance(prior, str) and prior.strip():
        out["governed_definitions"] = prior.rstrip() + "\n" + block
    else:
        out["governed_definitions"] = block
    return out


def load_cubes(space_id: str | None) -> list[Cube]:
    """Approved cubes for one space. A store error is no cubes, not a guess."""
    try:
        rows = cube_store().approved(str(space_id or ""))
    except Exception:
        return []
    cubes: list[Cube] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        body = row.get("body") if isinstance(row.get("body"), dict) else row
        if not isinstance(body, dict):
            continue
        try:
            cube = Cube.model_validate(body)
        except ValidationError:
            continue
        if cube.status == "approved":
            cubes.append(cube)
    return cubes


def arm_metric_cubes(
    ctx: dict[str, Any], *, space_id: str | None
) -> tuple[dict[str, Any], list[Cube]]:
    """Catalog on, and the cubes ``accept_metric_cube`` will compile against."""
    if os.environ.get(FLAG, "0").strip() != "1":
        return ctx, []
    cubes = load_cubes(space_id)
    return attach_catalog(ctx, cubes), cubes


def accept_metric_cube(
    payload: dict[str, Any],
    *,
    cubes: list[Cube],
    serve_sql: Callable[[str], dict[str, Any]],
    asked: str,
    space_id: str | None,
    session_id: str | None,
    dialect: str,
    grantable: set[str] | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any] | None:
    """A served envelope, a clarify, a reconfirm, or None to continue the ladder.

    ``grantable`` and ``warehouse`` are accepted so the caller can pass the
    same ask scope. ``serve_sql`` runs the existing validate and submit gates.
    """
    _ = grantable, warehouse
    body, from_sql = _plan_body(payload)
    if body is None:
        return None
    if body.get("clarify") is True:
        readings = _readings(body.get("readings"))
        if readings is None:
            _reject(payload, "cube_plan_incomplete", from_sql)
            return None
        return _clarify_envelope(asked, readings, space_id=space_id, session_id=session_id)
    try:
        plan = CubePlan.model_validate(body)
    except ValidationError:
        _reject(payload, "cube_plan_incomplete", from_sql)
        return None
    cube = next(
        (item for item in cubes if item.name == plan.cube and item.status == "approved"),
        None,
    )
    if cube is None:
        _reject(payload, REASON_UNKNOWN, from_sql)
        return None
    try:
        sql = compile_plan(plan, cube, dialect=dialect or "duckdb")
    except CubePlanError as exc:
        if exc.reason == "fanout_grain":
            _reject(payload, exc.reason, from_sql)
            return _reconfirm(asked, "fanout_grain", space_id=space_id, session_id=session_id)
        _reject(payload, exc.reason, from_sql)
        return None
    try:
        env = serve_sql(sql)
    except Exception as exc:
        from dms_executor.manifest import OpenVaultTokenError

        if isinstance(exc, OpenVaultTokenError):
            raise
        _reject(payload, "cube_serve_rejected", from_sql)
        return None
    if not isinstance(env, dict) or env.get("abstained") is True:
        _reject(payload, "cube_serve_rejected", from_sql)
        return None
    blocked = _shape_block(env, asked, dialect, space_id=space_id, session_id=session_id)
    if blocked is not None:
        return blocked
    env["plan_origin"] = "ai_cube_plan"
    stamp_route(env, ROUTE_AI_CUBE)
    return _arbitrate(env, asked, space_id=space_id, session_id=session_id)


def _reject(payload: dict[str, Any], reason: str, from_sql: bool) -> None:
    payload["cube_reject"] = reason
    payload.pop("cube_plan", None)
    if from_sql:
        payload["query_sql"] = ""


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _plan_body(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    raw = payload.get("cube_plan")
    if isinstance(raw, str):
        raw = _json_object(raw)
    if isinstance(raw, dict):
        return _unwrap(raw), False
    sql = str(payload.get("query_sql") or "").strip()
    if not sql.startswith("{"):
        return None, False
    parsed = _json_object(sql)
    if not isinstance(parsed, dict):
        return None, False
    if not any(key in parsed for key in ("cube", "clarify", "readings", "cube_plan")):
        return None, False
    return _unwrap(parsed), True


def _unwrap(body: dict[str, Any]) -> dict[str, Any]:
    inner = body.get("cube_plan")
    if isinstance(inner, dict):
        return inner
    return body


def _readings(raw: object) -> list[dict[str, str]] | None:
    if not isinstance(raw, list) or not 2 <= len(raw) <= 4:
        return None
    out: list[dict[str, str]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return None
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            return None
        ident = str(item.get("id") or "").strip()
        if not ident or any(ch.isspace() for ch in ident):
            ident = f"r{index}"
        out.append({"id": ident[:40], "text": text, "sql_preview": ""})
    return out


def _clarify_envelope(
    asked: str,
    readings: list[dict[str, str]],
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid, attach_clarify_block

    lines = ["Which reading should I answer?"]
    lines.extend(f"{index}. {item['text']}" for index, item in enumerate(readings, 1))
    env = build_abstain(
        reason="clarify_ambiguous",
        ticket_reason="clarify_ambiguous",
        question=asked,
        stage="generative",
        answer_id="ans_clarify",
        text="\n".join(lines),
        abstain_reason="clarify_ambiguous",
        rows=[],
        sql_used=None,
        assumptions=["clarify_ambiguous"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="needs_clarification",
    )
    attach_clarify_block(
        env,
        question=asked,
        readings=readings,
        clarify_id="clr_" + uuid.uuid4().hex[:16],
    )
    assert_envelope_valid(env)
    return env


def _reconfirm(
    asked: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.sql_vote import reconfirm_envelope

    env = reconfirm_envelope(
        question=asked,
        why=reason,
        closest_question="",
        space_id=space_id,
        session_id=session_id,
    )
    block = env.get("reconfirm")
    if isinstance(block, dict):
        block["reason_code"] = reason
    env["rows"] = []
    env["sql_used"] = None
    return env


def _shape_block(
    env: dict[str, Any],
    asked: str,
    dialect: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    from dms_executor.shape_check import check_answer_shape

    sql = str(env.get("sql_used") or "")
    rows = env.get("rows") if isinstance(env.get("rows"), list) else []
    try:
        verdict = check_answer_shape(asked, sql, rows=rows, dialect=dialect or None)
    except Exception:
        verdict = {"verdict": "unknown", "reasons": ["shape_call_failed"]}
    kind = str(verdict.get("verdict") or "")
    if kind not in {"misfit", "unknown"}:
        return None
    raw_reasons = verdict.get("reasons")
    reasons = raw_reasons if isinstance(raw_reasons, list) else []
    shown = [str(item) for item in reasons if str(item).strip()]
    env_out = _reconfirm(
        asked,
        "shape_misfit",
        space_id=space_id,
        session_id=session_id,
    )
    block = env_out.get("reconfirm")
    if isinstance(block, dict):
        block["verdict"] = kind
        block["reasons"] = shown
    return env_out


def _arbitrate(
    env: dict[str, Any],
    asked: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.arbiter import ResultSet, arbiter_enabled, serve, table_of

    if not arbiter_enabled():
        return env
    raw_rows = env.get("rows")
    rows = raw_rows if isinstance(raw_rows, list) else []
    columns, data = table_of([row for row in rows if isinstance(row, dict)])
    item = ResultSet(
        sql=str(env.get("sql_used") or ""),
        columns=tuple(columns),
        rows=tuple(tuple(row) for row in data),
        envelope=env,
        executed=True,
    )
    out = serve(question=asked, ai=item, space_id=space_id, session_id=session_id)
    if not isinstance(out, dict) or out.get("abstained") is True:
        return out
    out["plan_origin"] = "ai_cube_plan"
    stamp_route(out, ROUTE_AI_CUBE)
    return out
