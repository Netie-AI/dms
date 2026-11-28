"""Create harness. The provider generates. DMS governs.

``DMS_CREATE`` is on only when it is the digit 1. The shared adapter is
``dms_core.create_generator``. This module runs tools, budgets, versions,
and the judge. Confirm writes the pptx, pdf, and html. It does not invent the words.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from dms_core.create_generator import (
    GeneratorError,
    allowed_from,
    complete,
    direct_reading,
    holds_direct_bar,
    judge_artifact,
    prompt_digest,
    score_artifact,
    skills_for,
    splice_section,
    template_text,
    validate_call,
)

from dms_executor.demo_warehouse import SERVING_DIALECT

FLAG = "DMS_CREATE"
STORE_ENV = "DMS_CREATE_STORE"
WAREHOUSE_ENV = "DMS_CREATE_WAREHOUSE"
GRANTS_ENV = "DMS_CREATE_GRANTS"
KIND_APP = "app"
KIND_DOC = "doc"
KIND_SLIDES = "slides"
KINDS = frozenset({KIND_APP, KIND_DOC, KIND_SLIDES})
_TOOLS = frozenset(
    {"run_sql", "fetch_ontology", "chart", "render_doc", "sign_request", "save_artifact"}
)
_STOP_CODES = frozenset({"runaway_steps", "runaway_usd", "budget_exceeded"})
_RUNAWAY_STEPS = 60
_ROW_CAP = 40
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOCK = threading.Lock()
_BUDGET_TEXT = {
    "runaway_steps": "Stopped. This run hit the runaway step guard. Saved versions stay.",
    "runaway_usd": "Stopped. This run hit the runaway cost ceiling. Saved versions stay.",
    "budget_exceeded": "Stopped. OpenVault returned 402 budget_exceeded. Saved versions stay.",
}
_WARN = "Spend is past twice the estimate. The build continues."


class CreateDisabled(RuntimeError):
    """The create flag is off. Nothing ran and nothing was stored."""


class CreateProblem(ValueError):
    """A caller or provider failure. ``code`` is the HTTP detail."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def create_enabled() -> bool:
    return os.environ.get(FLAG, "0").strip() == "1"


def start_session(
    *,
    space_id: str,
    kind: str,
    goal: str,
    audience: str,
    api_key: str,
    actor: str,
    tenant_id: str = "",
    style: str = "",
    template: str = "plain",
    brand_colours: str = "",
    brand_fonts: str = "",
    brand_logo: str = "",
    brand_tone: str = "",
    inputs: list[str] | None = None,
    examples: list[dict[str, str]] | None = None,
    tools: dict[str, bool] | None = None,
    workbook: bool = False,
    prompt_version: str = "1",
) -> dict[str, Any]:
    """Open a session. No provider call."""
    _require_on()
    _key(api_key)
    if kind not in KINDS:
        raise CreateProblem("kind_required")
    try:
        template_text(template or "plain")
    except GeneratorError as exc:
        raise CreateProblem(exc.code) from None
    guide = _guide(tenant_id)
    brand = {
        "colours": brand_colours or str((guide.get("brand") or {}).get("colours") or ""),
        "fonts": brand_fonts or str((guide.get("brand") or {}).get("fonts") or ""),
        "logo": brand_logo or str((guide.get("brand") or {}).get("logo") or ""),
        "tone": brand_tone or str((guide.get("brand") or {}).get("tone") or ""),
    }
    session = {
        "session_id": uuid.uuid4().hex,
        "space_id": space_id,
        "tenant_id": tenant_id,
        "actor": actor,
        "kind": kind,
        "goal": goal,
        "audience": audience,
        "style_prompt": style,
        "template": template or "plain",
        "brand": brand,
        "inputs": list(inputs or []),
        "examples": list(examples if examples is not None else guide.get("examples") or []),
        "tools": dict(tools if tools is not None else guide.get("tools") or {}),
        "workbook": bool(workbook),
        "prompt_version": prompt_version or "1",
        "step": "intake",
        "shown": False,
        "final": False,
        "artifact": None,
        "versions": [],
        "attempts": [],
        "audit": [],
        "issues": [],
        "changes": [],
        "usage_calls": [],
        "steps_used": 0,
        "repaired": False,
        "stopped": None,
        "warning": None,
        "estimate": {"tokens": None, "cost": None},
        "started": time.time(),
    }
    try:
        session["catalog"] = _catalog(session)
    except CreateProblem:
        raise
    _save(session)
    return _view(session)


def generate_artifact(
    *, session_id: str, api_key: str, inputs: list[str] | None = None
) -> dict[str, Any]:
    _require_on()
    _key(api_key)
    session = _load(session_id)
    if inputs is not None:
        session["inputs"] = list(inputs)
    session["repaired"] = False
    return _governed(session, api_key, role="generate", instruction=None)


def improve_artifact(
    *, session_id: str, api_key: str, instruction: str, section_id: str
) -> dict[str, Any]:
    """One targeted edit. Prior versions stay."""
    _require_on()
    _key(api_key)
    session = _load(session_id)
    if not session.get("artifact"):
        raise CreateProblem("nothing_to_edit")
    if not section_id:
        raise CreateProblem("section_missing")
    session["repaired"] = False
    return _governed(
        session,
        api_key,
        role="improve",
        instruction=instruction,
        section_id=section_id,
    )


def restore_version(*, session_id: str, version_id: int, actor: str) -> dict[str, Any]:
    _require_on()
    del actor
    session = _load(session_id)
    prior = next(
        (item for item in session.get("versions") or [] if item.get("id") == version_id),
        None,
    )
    if not isinstance(prior, dict):
        raise CreateProblem("version_missing")
    _push_version(session, prior.get("artifact") or {}, note=f"restore {version_id}")
    session["shown"] = True
    session["step"] = "draft"
    _save(session)
    return _view(session)


def confirm_final(*, session_id: str, actor: str) -> dict[str, Any]:
    _require_on()
    session = _load(session_id)
    if session.get("step") != "draft" or not session.get("shown"):
        raise CreateProblem("nothing_to_confirm")
    artifact = session.get("artifact") or {}
    folder = _store_dir() / session_id
    from dms_executor.create_files import one_page_requested, render_artifact

    asked = " ".join(
        [str(session.get("goal") or ""), str(session.get("style_prompt") or "")]
    )
    session["files"] = render_artifact(
        folder,
        artifact,
        kind=str(session.get("kind") or ""),
        one_page=one_page_requested(asked),
    )
    session["final"] = True
    session["step"] = "ready"
    _save(session)
    return accept_artifact(session_id=session_id, actor=actor)


def accept_artifact(*, session_id: str, actor: str) -> dict[str, Any]:
    _require_on()
    session = _load(session_id)
    if not session.get("shown") or not session.get("artifact"):
        raise CreateProblem("nothing_to_accept")
    from dms_executor.verified_queries import save_create_example

    stored: list[dict[str, str]] = []
    warehouse = _warehouse()
    grantable = _grantable(session["space_id"], warehouse)
    for label, sql in _part_sql(session["artifact"]):
        try:
            row = save_create_example(
                question=f"{session['goal']} :: {label}",
                sql=sql,
                space_id=session["space_id"],
                actor=actor,
                warehouse=warehouse,
                grantable=grantable,
                dialect=SERVING_DIALECT,
            )
        except Exception as exc:
            from dms_executor.lake_registry import SERVING_ERRORS

            if isinstance(exc, SERVING_ERRORS):
                raise
            row = None
        if row and row.get("asset_id"):
            stored.append({"asset_id": str(row["asset_id"]), "label": label})
    session["accepted"] = True
    _save(session)
    view = _view(session)
    view["stored"] = stored
    return view


def read_artifact(*, session_id: str) -> dict[str, Any]:
    _require_on()
    session = _load(session_id)
    if session.get("shown") and session.get("artifact"):
        _refresh(session)
        _save(session)
    return _view(session)


def save_guide(*, tenant_id: str, guide: Mapping[str, Any]) -> dict[str, Any]:
    _require_on()
    current = _guide(tenant_id)
    for key in ("brand", "guides", "tools", "examples", "templates"):
        if key in guide:
            current[key] = guide[key]
    path = _store_dir() / "tenants"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{_safe_tenant(tenant_id)}.json").write_text(
        json.dumps(current, indent=2, default=str), encoding="utf-8"
    )
    return current


def read_guide(*, tenant_id: str) -> dict[str, Any]:
    _require_on()
    return _guide(tenant_id)


def draft_events(*, session_id: str, api_key: str) -> Iterator[dict[str, Any]]:
    _require_on()
    session = _load(session_id)
    yield _event("schema", "Reading the schema", "started", session)
    yield _event("schema", "Reading the schema", "done", session)
    yield _event("generate", "Asking the generator", "started", session)
    view = generate_artifact(session_id=session_id, api_key=api_key)
    status = "stopped" if view.get("stopped") else "done"
    yield _event("generate", "Asking the generator", status, view)
    yield _event("check", "Checking", "done", view)
    label = view.get("message") or "Waiting for confirmation"
    yield _event("draft", str(label), "done" if view.get("shown") else "stopped", view)


def apply_tool(session: dict[str, Any], name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate, allowlist, budget, audit, then run one client tool."""
    issues = validate_call(name, arguments)
    enabled = session.get("tools") or {}
    if name in _TOOLS and enabled.get(name, True) is False:
        issues = ["tool_disabled", *issues]
    block = _budget_block(session)
    if block:
        issues = [block, *issues]
    if issues:
        _audit(session, name, False, issues)
        _count_step(session)
        return {"error": issues[0]}
    _count_step(session)
    if name == "run_sql":
        result = _tool_sql(session, str(arguments.get("sql") or ""))
    elif name == "fetch_ontology":
        result = {"ontology": _ontology()}
    elif name == "chart":
        result = {"accepted": True, "spec": dict(arguments)}
    elif name == "render_doc":
        picked = skills_for(str(arguments.get("format") or session["kind"]))
        result = {"skills": picked["anthropic"], "rendered_by": "provider"}
    elif name == "sign_request":
        result = {"digest": prompt_digest(str(arguments.get("label") or ""))}
    elif name == "save_artifact":
        artifact = arguments.get("artifact")
        if not isinstance(artifact, dict):
            _audit(session, name, False, ["artifact_missing"])
            return {"error": "artifact_missing"}
        version = _push_version(session, artifact, note=str(arguments.get("note") or "tool"))
        result = {"version": version}
    else:
        result = {"error": "tool_unknown"}
    _audit(session, name, "error" not in result, [])
    return result


def _governed(
    session: dict[str, Any],
    api_key: str,
    *,
    role: str,
    instruction: str | None,
    section_id: str | None = None,
) -> dict[str, Any]:
    denied = _unknown_inputs(session)
    if denied:
        session["step"] = "abstain"
        session["shown"] = False
        session["abstain"] = _abstain(session, "ungranted", denied)
        _save(session)
        return _view(session)
    turn = _turn(session, api_key, role=role, instruction=instruction, section_id=section_id)
    if turn is None:
        _save(session)
        return _view(session)
    artifact, issues = _from_turn(session, turn, section_id)
    if issues and issues[0] in {"ungranted", "destructive"}:
        session["step"] = "abstain"
        session["shown"] = False
        session["issues"] = issues
        session["artifact"] = None
        _save(session)
        return _view(session)
    if issues and not session.get("repaired"):
        session["repaired"] = True
        repair = _turn(
            session,
            api_key,
            role="improve",
            instruction=instruction,
            section_id=section_id,
            judge_issues=issues,
        )
        if repair is None:
            _save(session)
            return _view(session)
        artifact, issues = _from_turn(session, repair, section_id)
    scored = _score_pair(session, artifact) if isinstance(artifact, dict) else None
    if scored is not None:
        session["score"] = {
            "create": scored["create"],
            "direct": scored["direct"],
            "holds": scored["holds"],
        }
    if issues or artifact is None:
        session["shown"] = False
        session["issues"] = issues or ["model_unreadable"]
        session["artifact"] = None
        session["step"] = "ask"
    elif scored is not None and not scored["holds"]:
        session["shown"] = False
        session["issues"] = ["below_direct"]
        session["artifact"] = None
        session["step"] = "ask"
    else:
        note = instruction or "generate"
        _push_version(session, artifact, note=note)
        session["shown"] = True
        session["issues"] = []
        session["step"] = "draft"
    _warn(session)
    _write_log(session)
    _save(session)
    return _view(session)


def _turn(
    session: dict[str, Any],
    api_key: str,
    *,
    role: str,
    instruction: str | None,
    section_id: str | None,
    judge_issues: list[str] | None = None,
) -> Any:
    block = _budget_block(session)
    if block:
        _stop(session, block)
        return None
    spec = _spec(session, instruction, section_id, judge_issues)
    context = _context(session)
    style = _style(session)
    try:
        turn = complete(
            spec,
            context,
            style,
            api_key=api_key,
            role=role,
            enabled=session.get("tools") or {},
            prompt_version=str(session.get("prompt_version") or "1"),
        )
    except GeneratorError as exc:
        if exc.code == "budget_exceeded":
            _stop(session, "budget_exceeded")
            return None
        raise CreateProblem(exc.code) from None
    _count_step(session)
    _remember(session, turn)
    if turn.tool_calls and turn.artifact is None:
        follow = _run_tools(session, api_key, turn, role, spec, context, style)
        return follow or turn
    return turn


def _run_tools(
    session: dict[str, Any],
    api_key: str,
    turn: Any,
    role: str,
    spec: dict[str, Any],
    context: dict[str, Any],
    style: dict[str, Any],
) -> Any:
    results = []
    for call in turn.tool_calls:
        if call.get("name") == "edit_section":
            results.append({"name": "edit_section", "input": call.get("input")})
            continue
        results.append(
            {
                "name": call.get("name"),
                "result": apply_tool(session, str(call.get("name") or ""), call.get("input") or {}),
            }
        )
    edit = next((item for item in results if item.get("name") == "edit_section"), None)
    if edit is not None:
        tool_input = edit.get("input") or {}
        try:
            artifact = splice_section(
                session.get("artifact") or {},
                str(tool_input.get("section_id") or ""),
                tool_input.get("replacement") or {},
            )
        except GeneratorError as exc:
            raise CreateProblem(exc.code) from None
        turn.artifact = artifact
        return turn
    block = _budget_block(session)
    if block:
        _stop(session, block)
        return None
    spec = dict(spec)
    spec["tool_results"] = results
    try:
        follow = complete(
            spec,
            context,
            style,
            api_key=api_key,
            role=role,
            enabled=session.get("tools") or {},
            prompt_version=str(session.get("prompt_version") or "1"),
        )
    except GeneratorError as exc:
        if exc.code == "budget_exceeded":
            _stop(session, "budget_exceeded")
            return None
        raise CreateProblem(exc.code) from None
    _count_step(session)
    _remember(session, follow)
    return follow


def _from_turn(
    session: dict[str, Any], turn: Any, section_id: str | None
) -> tuple[dict[str, Any] | None, list[str]]:
    artifact = turn.artifact
    if section_id and isinstance(artifact, dict) and artifact.get("parts"):
        prior = session.get("artifact") or {}
        part = next(
            (
                item
                for item in artifact.get("parts") or []
                if isinstance(item, dict) and item.get("id") == section_id
            ),
            None,
        )
        if isinstance(prior, dict) and prior.get("parts") and isinstance(part, dict):
            try:
                artifact = splice_section(prior, section_id, part)
            except GeneratorError as exc:
                return None, [exc.code]
    if not isinstance(artifact, dict):
        return None, ["model_unreadable"]
    traced, sql_issues = _trace(session, artifact)
    if sql_issues and sql_issues[0] in {"ungranted", "destructive"}:
        return None, sql_issues
    issues = judge_artifact(
        traced,
        kind=str(session["kind"]),
        brand=session.get("brand") or {},
        allowed_numbers=_allowed(session, traced),
    )
    issues.extend(sql_issues)
    if turn.estimated_cost is not None:
        session["estimate"] = {"tokens": turn.tokens, "cost": turn.estimated_cost}
    return traced, issues


def _trace(
    session: dict[str, Any], artifact: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    for part in artifact.get("parts") or []:
        if not isinstance(part, dict):
            continue
        sql = part.get("sql")
        if isinstance(sql, str) and sql.strip():
            rows, sql_issues, abstain = _guard_sql(session, sql)
            if abstain is not None:
                session["abstain"] = abstain
                return artifact, [str(abstain.get("abstain_reason") or "ungranted")]
            if sql_issues:
                issues.extend(sql_issues)
            else:
                part["rows"] = rows
                part["citations"] = [
                    {"value": str(value), "sql": sql, "source_row": row}
                    for row in rows
                    for value in row.values()
                    if value not in (None, "")
                ]
        for chart in part.get("charts") or []:
            if not isinstance(chart, dict):
                continue
            chart_sql = chart.get("sql")
            if not isinstance(chart_sql, str) or not chart_sql.strip():
                continue
            rows, sql_issues, abstain = _guard_sql(session, chart_sql)
            if abstain is not None:
                session["abstain"] = abstain
                return artifact, [str(abstain.get("abstain_reason") or "ungranted")]
            if sql_issues:
                issues.extend(sql_issues)
                continue
            chart["rows"] = rows
    return artifact, issues


def _score_pair(session: dict[str, Any], artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Create points against the same words read as a direct Claude answer."""
    overview_raw = session.get("overview")
    overview: dict[str, Any] = overview_raw if isinstance(overview_raw, dict) else {}
    rows = _cited_rows(artifact)
    brand = session.get("brand") or {}
    kind = str(session["kind"])
    create = score_artifact(
        artifact,
        kind=kind,
        brand=brand,
        allowed_numbers=allowed_from(rows, overview),
    )
    direct = score_artifact(
        direct_reading(artifact),
        kind=kind,
        brand=brand,
        allowed_numbers=allowed_from([], overview),
    )
    create_points = int(create["points"])
    direct_points = int(direct["points"])
    return {
        "create": create_points,
        "direct": direct_points,
        "holds": holds_direct_bar(create_points, direct_points),
    }


def _cited_rows(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for part in artifact.get("parts") or []:
        if not isinstance(part, dict):
            continue
        rows.extend(item for item in part.get("rows") or [] if isinstance(item, dict))
        for chart in part.get("charts") or []:
            if isinstance(chart, dict):
                rows.extend(item for item in chart.get("rows") or [] if isinstance(item, dict))
    return rows


def _allowed(session: dict[str, Any], artifact: Mapping[str, Any]) -> set[str]:
    rows = _cited_rows(artifact)
    overview_raw = session.get("overview")
    overview: dict[str, Any] = overview_raw if isinstance(overview_raw, dict) else {}
    return allowed_from(rows, overview)


def _context(session: dict[str, Any]) -> dict[str, Any]:
    catalog = _selected(session)
    data = [_overview(row) for row in catalog]
    session["overview"] = {"data": data}
    picked = skills_for(str(session["kind"]), workbook=bool(session.get("workbook")))
    return {
        "schema": [
            {"name": row["name"], "columns": row["columns"], "row_count": row["row_count"]}
            for row in catalog
        ],
        "data": data,
        "ontology": _ontology(),
        "choices": {
            "inputs": [row["name"] for row in catalog],
            "template": session.get("template"),
            "instruction": session.get("instruction"),
        },
        "examples": session.get("examples") or [],
        "skills": picked,
    }


def _style(session: dict[str, Any]) -> dict[str, Any]:
    from dms_core.create_generator import prompt_library

    library = prompt_library(str(session.get("prompt_version") or "1"))
    guides = dict(library.get("guides") or {})
    custom = _guide(str(session.get("tenant_id") or "")).get("guides") or {}
    if isinstance(custom, dict):
        guides.update(custom)
    return {
        "prompt": session.get("style_prompt") or "",
        "template": template_text(str(session.get("template") or "plain")),
        "brand": session.get("brand") or {},
        "guide": guides.get(str(session["kind"]), ""),
    }


def _spec(
    session: dict[str, Any],
    instruction: str | None,
    section_id: str | None,
    judge_issues: list[str] | None,
) -> dict[str, Any]:
    spec = {
        "kind": session["kind"],
        "goal": session["goal"],
        "audience": session["audience"],
        "workbook": bool(session.get("workbook")),
    }
    if instruction:
        spec["edit"] = {
            "section_id": section_id,
            "instruction": instruction,
            "prior": session.get("artifact"),
        }
    if judge_issues:
        spec["judge"] = {"issues": judge_issues}
    return spec


def _unknown_inputs(session: dict[str, Any]) -> str:
    grantable = _grantable(session["space_id"], _warehouse())
    for name in session.get("inputs") or []:
        if name not in grantable:
            return str(name)
    return ""


def _selected(session: dict[str, Any]) -> list[dict[str, Any]]:
    catalog = session.get("catalog") or _catalog(session)
    wanted = set(session.get("inputs") or [])
    if not wanted:
        return list(catalog)
    return [row for row in catalog if row.get("name") in wanted]


def _catalog(session: dict[str, Any]) -> list[dict[str, Any]]:
    from dms_executor.sql_loop import run_readonly

    warehouse = _warehouse()
    out: list[dict[str, Any]] = []
    for name in sorted(_grantable(session["space_id"], warehouse)):
        if not _IDENT.fullmatch(name):
            continue
        columns, _err = run_readonly(
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = 'main' AND table_name = '{name}' ORDER BY column_name",
            warehouse,
        )
        counted, _err2 = run_readonly(f"SELECT count(*) AS n FROM {_q(name)}", warehouse)
        cols = []
        for row in columns or []:
            col = row.get("column_name")
            typ = row.get("data_type")
            if isinstance(col, str) and isinstance(typ, str):
                cols.append({"name": col, "type": typ})
        count = _as_int(counted[0].get("n")) if counted else None
        out.append({"name": name, "columns": cols, "row_count": count})
    return out


def _overview(row: Mapping[str, Any]) -> dict[str, Any]:
    from dms_executor.sql_loop import run_readonly

    name = str(row.get("name") or "")
    warehouse = _warehouse()
    table = _q(name)
    stats: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    for col in row.get("columns") or []:
        if not isinstance(col, Mapping):
            continue
        col_name = col.get("name")
        typ = col.get("type")
        if not isinstance(col_name, str) or not _IDENT.fullmatch(col_name):
            continue
        quoted = _q(col_name)
        if isinstance(typ, str) and _numeric_type(typ):
            found, err = run_readonly(
                f"SELECT count({quoted}) AS n, min({quoted}) AS min_v, "
                f"max({quoted}) AS max_v, sum({quoted}) AS sum_v FROM {table}",
                warehouse,
            )
            if err or not found:
                continue
            stats.append({"column": col_name, **_json_row(found[0])})
            continue
        found, err = run_readonly(
            f"SELECT {quoted} AS value, count(*) AS n FROM {table} "
            "GROUP BY 1 ORDER BY n DESC LIMIT 8",
            warehouse,
        )
        if err or not found:
            continue
        groups.append({"column": col_name, "values": [_json_row(item) for item in found[:8]]})
    return {"name": name, "row_count": row.get("row_count"), "stats": stats, "groups": groups}


def _tool_sql(session: dict[str, Any], sql: str) -> dict[str, Any]:
    rows, issues, abstain = _execute(session, sql)
    if abstain is not None:
        return {"error": abstain.get("abstain_reason")}
    if issues:
        return {"error": issues[0]}
    return {"rows": rows}


def _guard_sql(
    session: dict[str, Any], sql: str
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
    """Read-only guard. A write abstains. A read-only multi-statement is a format miss."""
    return _execute(session, sql)


def _execute(
    session: dict[str, Any], sql: str
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
    from dms_executor.grant_struct import serve_gap
    from dms_executor.sql_loop import run_readonly

    text = (sql or "").strip()
    warehouse = _warehouse()
    grantable = _grantable(session["space_id"], warehouse)
    gap = serve_gap(text, grantable=grantable, dialect=SERVING_DIALECT)
    if gap:
        kind = _abstain_kind(gap, text)
        if kind:
            return [], [], _abstain(session, kind, text)
        if gap == "multi_statement":
            return [], ["sql_format:multi_statement"], None
        return [], [f"sql_refused:{gap}"], None
    first, err = run_readonly(text, warehouse)
    if err or first is None:
        return [], [err or "sql_failed"], None
    second, err2 = run_readonly(text, warehouse)
    if err2 or second is None or _canon(first) != _canon(second):
        return [], ["rerun_mismatch"], None
    return [_json_row(row) for row in first[:_ROW_CAP]], [], None


def _abstain_kind(gap: str, sql: str = "") -> str | None:
    if gap == "multi_statement":
        if _reads_only(sql):
            return None
        return "destructive"
    if gap == "statement_not_allowed" or gap.startswith("hostile_sql"):
        return "destructive"
    if gap == "ungranted:unparsed":
        return None
    if (
        gap == "ungranted"
        or gap.startswith("ungranted:")
        or gap.startswith("sql_relation_not_granted")
    ):
        return "ungranted"
    if gap.startswith("sql_relation_unresolved"):
        return "ungranted"
    return None


def _reads_only(sql: str) -> bool:
    """True when every statement is a query. A write in the batch is not."""
    from sqlglot import exp, parse

    try:
        trees = [tree for tree in parse(sql, read=SERVING_DIALECT) if tree is not None]
    except Exception:
        return False
    if len(trees) < 2:
        return False
    return all(isinstance(tree, (exp.Query, exp.Values)) for tree in trees)


def _abstain(session: dict[str, Any], kind: str, sql: str) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    if kind == "destructive":
        text = "This statement is not a read. It was not run."
        reason = "destructive"
    else:
        text = "This read uses a table the Space does not grant. No figure was returned."
        reason = "ungranted"
    env = build_abstain(
        reason=reason,
        question=str(session.get("goal") or ""),
        sql=sql,
        stage="create",
        answer_id=f"ans_create_{session['session_id'][:8]}",
        text=text,
        abstain_reason=reason,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[reason],
        space_id=session.get("space_id"),
        session_id=session.get("session_id"),
        ask_mode="live",
        route="abstain",
        log_ticket=False,
    )
    assert_envelope_valid(env)
    return env


def _refresh(session: dict[str, Any]) -> None:
    artifact = session.get("artifact") or {}
    changes: list[dict[str, str]] = []
    for index, part in enumerate(artifact.get("parts") or []):
        if not isinstance(part, dict):
            continue
        sql = str(part.get("sql") or "")
        if not sql:
            continue
        rows, issues, abstain = _execute(session, sql)
        if abstain is not None or issues:
            continue
        before = json.dumps(part.get("rows") or [], sort_keys=True, default=str)
        after = json.dumps(rows, sort_keys=True, default=str)
        if before != after:
            changes.append({"path": f"parts.{index}", "before": before, "after": after})
            part["rows"] = rows
    session["changes"] = changes


def _push_version(session: dict[str, Any], artifact: Mapping[str, Any], note: str) -> int:
    versions = list(session.get("versions") or [])
    version_id = len(versions) + 1
    versions.append({"id": version_id, "note": note, "artifact": json.loads(json.dumps(artifact))})
    session["versions"] = versions
    session["artifact"] = json.loads(json.dumps(artifact))
    session["current_version"] = version_id
    return version_id


def _remember(session: dict[str, Any], turn: Any) -> None:
    session.setdefault("usage_calls", []).append(
        {
            "cost": turn.cost,
            "tokens": turn.tokens,
            "prompt_tokens": turn.prompt_tokens,
            "completion_tokens": turn.completion_tokens,
            "model": turn.model,
            "provider": turn.provider,
        }
    )
    session.setdefault("attempts", []).append(
        {
            "prompt": turn.prompt,
            "cost": turn.cost,
            "tokens": turn.tokens,
            "provider": turn.provider,
        }
    )
    if turn.estimated_cost is not None and (session.get("estimate") or {}).get("cost") is None:
        session["estimate"] = {"tokens": turn.tokens, "cost": turn.estimated_cost}


def _budget(session: dict[str, Any]) -> dict[str, float]:
    del session
    return {
        "max_usd": float(_env_num("DMS_CREATE_MAX_USD", 10.0, False)),
        "max_steps": float(_env_num("DMS_CREATE_MAX_STEPS", _RUNAWAY_STEPS, True)),
    }


def _env_num(name: str, default: float, whole: bool) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        return float(default)
    if whole:
        return float(int(value))
    return value


def _budget_block(session: dict[str, Any]) -> str | None:
    limits = _budget(session)
    if int(session.get("steps_used") or 0) >= int(limits["max_steps"]):
        return "runaway_steps"
    spend = _spend_usd(session)
    if spend is not None and spend >= limits["max_usd"]:
        return "runaway_usd"
    return None


def _warn(session: dict[str, Any]) -> None:
    estimate = (session.get("estimate") or {}).get("cost")
    spend = _spend_usd(session)
    if (
        isinstance(estimate, (int, float))
        and estimate > 0
        and spend is not None
        and spend >= 2 * float(estimate)
    ):
        session["warning"] = _WARN


def _stop(session: dict[str, Any], code: str) -> None:
    session["stopped"] = code
    session["message"] = _BUDGET_TEXT.get(code, code)
    session["shown"] = bool(session.get("artifact"))
    session["step"] = "stopped"


def _count_step(session: dict[str, Any]) -> None:
    session["steps_used"] = int(session.get("steps_used") or 0) + 1


def _spend_usd(session: dict[str, Any]) -> float | None:
    costs: list[float] = []
    for item in session.get("usage_calls") or []:
        if not isinstance(item, dict):
            continue
        cost = item.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            costs.append(float(cost))
    if not costs:
        return None
    return float(sum(costs))


def _spend(session: dict[str, Any]) -> dict[str, Any]:
    tokens = [
        item.get("tokens")
        for item in session.get("usage_calls") or []
        if isinstance(item, dict) and isinstance(item.get("tokens"), int)
    ]
    return {
        "tokens": sum(tokens) if tokens else None,
        "usd": _spend_usd(session),
        "steps": int(session.get("steps_used") or 0),
    }


def _audit(session: dict[str, Any], name: str, ok: bool, issues: list[str]) -> None:
    session.setdefault("audit", []).append({"tool": name, "ok": ok, "issues": issues})


def _event(step: str, label: str, status: str, session: Mapping[str, Any] | None) -> dict[str, Any]:
    payload: dict[str, Any] | None = None
    spend: dict[str, Any] = {}
    warning = None
    if isinstance(session, dict) and session.get("session_id"):
        if isinstance(session.get("attempts"), list):
            payload = _view(dict(session))
            spend = _spend(dict(session))
        else:
            payload = dict(session)
            found = session.get("spend")
            spend = dict(found) if isinstance(found, dict) else {}
        warning = session.get("warning")
    return {
        "step": step,
        "label": label,
        "status": status,
        "session": payload,
        "spend": spend,
        "warning": warning,
    }


def _view(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session["session_id"],
        "space_id": session["space_id"],
        "kind": session["kind"],
        "step": session["step"],
        "shown": bool(session.get("shown")),
        "final": bool(session.get("final")),
        "catalog": list(session.get("catalog") or []),
        "brand": dict(session.get("brand") or {}),
        "template": session.get("template"),
        "tools": dict(session.get("tools") or {}),
        "artifact": session.get("artifact") if session.get("shown") else None,
        "versions": [
            {"id": item.get("id"), "note": item.get("note")}
            for item in session.get("versions") or []
            if isinstance(item, dict)
        ],
        "current_version": session.get("current_version"),
        "issues": list(session.get("issues") or []),
        "changes": list(session.get("changes") or []),
        "abstain": session.get("abstain"),
        "audit": list(session.get("audit") or []),
        "attempts": len(session.get("attempts") or []),
        "estimate": session.get("estimate") or {"tokens": None, "cost": None},
        "spend": _spend(session),
        "stopped": session.get("stopped"),
        "message": session.get("message"),
        "warning": session.get("warning"),
        "score": session.get("score"),
        "log": _log(session),
    }


def _log(session: dict[str, Any]) -> dict[str, Any]:
    calls = [item for item in session.get("usage_calls") or [] if isinstance(item, dict)]

    def _sum(key: str) -> int | None:
        found: list[int] = []
        for item in calls:
            value = item.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                found.append(value)
        return sum(found) if found else None

    costs: list[float] = []
    for item in calls:
        cost = item.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            costs.append(float(cost))
    models = [item["model"] for item in calls if isinstance(item.get("model"), str)]
    seconds = round(time.time() - float(session.get("started") or time.time()), 6)
    return {
        "model": models[-1] if models else None,
        "prompt_tokens": _sum("prompt_tokens"),
        "completion_tokens": _sum("completion_tokens"),
        "total_tokens": _sum("tokens"),
        "cost": float(sum(costs)) if costs else None,
        "seconds": seconds,
        "provider": calls[-1].get("provider") if calls else None,
    }


def _write_log(session: dict[str, Any]) -> None:
    path = _store_dir() / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "session_id": session["session_id"],
        "kind": session["kind"],
        "attempts": [
            {
                "cost": item.get("cost"),
                "tokens": item.get("tokens"),
                "provider": item.get("provider"),
                "prompt_sha256": prompt_digest(str(item.get("prompt") or "")),
            }
            for item in session.get("attempts") or []
            if isinstance(item, dict)
        ],
        "audit": session.get("audit") or [],
    }
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")


def _part_sql(artifact: Mapping[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for part in artifact.get("parts") or []:
        if isinstance(part, dict) and isinstance(part.get("sql"), str) and part["sql"].strip():
            found.append((str(part.get("id") or "part"), part["sql"]))
    return found


def _guide(tenant_id: str) -> dict[str, Any]:
    path = _store_dir() / "tenants" / f"{_safe_tenant(tenant_id)}.json"
    if not path.is_file():
        return {"brand": {}, "guides": {}, "tools": {}, "examples": [], "templates": {}}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"brand": {}, "guides": {}, "tools": {}, "examples": [], "templates": {}}
    return loaded if isinstance(loaded, dict) else {}


def _save(session: dict[str, Any]) -> None:
    path = _store_dir() / f"{session['session_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, default=str), encoding="utf-8")


def _load(session_id: str) -> dict[str, Any]:
    path = _store_dir() / f"{session_id}.json"
    if not path.is_file():
        raise CreateProblem("session_missing")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise CreateProblem("session_missing")
    return loaded


def _store_dir() -> Path:
    raw = os.environ.get(STORE_ENV, "").strip()
    if not raw:
        raise CreateProblem("store_missing")
    return Path(raw)


def _warehouse() -> Path:
    raw = os.environ.get(WAREHOUSE_ENV, "").strip()
    if not raw:
        raise CreateProblem("warehouse_missing")
    return Path(raw)


def _grantable(space_id: str, warehouse: Path) -> set[str]:
    raw = os.environ.get(GRANTS_ENV, "").strip()
    if raw:
        return {part.strip() for part in raw.split(",") if part.strip()}
    from dms_executor import Executor

    return set(Executor(cortex=None, warehouse_path=warehouse).grantable_tables(space_id=space_id))


def _ontology() -> list[dict[str, str]]:
    raw = os.environ.get("DMS_CREATE_ONTOLOGY", "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, str]] = []
    for item in parsed:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            out.append({"name": item["name"]})
    return out


def _require_on() -> None:
    if not create_enabled():
        raise CreateDisabled(FLAG)


def _key(api_key: str) -> None:
    if not api_key.startswith("ov_") or " " in api_key or len(api_key) <= 3:
        raise CreateProblem("ov_api_key_missing")


def _numeric_type(typ: str) -> bool:
    text = typ.upper()
    if any(token in text for token in ("CHAR", "TEXT", "DATE", "TIME", "BOOL", "BLOB")):
        return False
    return any(token in text for token in ("INT", "DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL"))


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _json_row(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            out[str(key)] = float(value)
        elif isinstance(value, float) and value.is_integer():
            out[str(key)] = int(value)
        else:
            out[str(key)] = value
    return out


def _canon(rows: list[Any]) -> str:
    return json.dumps(rows, sort_keys=True, default=str)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _safe_tenant(tenant_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", tenant_id) or "default"
    return cleaned
