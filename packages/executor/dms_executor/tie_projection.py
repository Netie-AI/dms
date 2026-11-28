"""BIRD-TIES-PROJECTION-01. Default off.

On, three checks run on a served answer. Off, none of them run and the
envelope, the prompt, and the route stay as they were.

1. A served statement with ORDER BY and a numeric LIMIT is executed once
   more with the LIMIT removed, read-only, through the same gate. Rows that
   share the rank key at the cutoff are all returned. The answer text says
   so. A tied set longer than one page is paged: ``complete`` is false,
   ``next_offset`` is set, ``truncated`` stays false. The page is not a cap.
2. The model that writes the SQL also names ``asked_columns``. Served rows
   keep those columns. Other measures move to ``context_columns``.
3. When that model reports two readings and they execute to different row
   sets, the answer is a clarify with both readings and their row counts.

A trailing bare column in ORDER BY is a tie-break, not the rank.
``ORDER BY COUNT(*) DESC, name`` ties on the count.
ponytail: one rank key is rewritten into SQL. Several rank keys still
expand the rows and leave ``sql_used`` as the model wrote it.
Upgrade: the model names the rank keys.
ponytail: a sqlglot miss, or a probe that does not execute, leaves the
served rows. Upgrade: parse with the serving engine.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from dms_executor.demo_warehouse import SERVING_DIALECT

ENV = "DMS_TIE_PROJECTION"
# Same page as connectors.run_live_select. Not a result cap: the next offset
# continues, and truncated stays false.
PAGE_SIZE = 500
_HELPER = "_dms_k"
Execute = Callable[[str], tuple[list[dict[str, Any]] | None, str | None]]
_DESTRUCTIVE = frozenset(
    {
        "statement_not_allowed",
        "hostile_sql",
        "multi_statement",
    }
)
_UNGRANTED = frozenset({"ungranted", "sql_relation_not_granted"})


@dataclass
class ModelReport:
    """Columns and readings the SQL writer returned. Empty when it did not."""

    asked_columns: list[str] | None = None
    readings: list[dict[str, str]] = field(default_factory=list)


@dataclass
class _Key:
    expr: exp.Expression
    desc: bool
    name: str


@dataclass
class _Plan:
    n: int
    offset: int
    keys: list[_Key]
    probe_sql: str


_REPORT: ContextVar[ModelReport | None] = ContextVar("dms_tie_projection", default=None)


def enabled() -> bool:
    """True only when ``DMS_TIE_PROJECTION`` is the digit 1."""
    return os.environ.get(ENV, "0").strip() == "1"


def begin_turn() -> None:
    """Drop a previous ask's report. Flag off does not call this."""
    _REPORT.set(ModelReport())


def current_report() -> ModelReport:
    found = _REPORT.get()
    if found is None:
        found = ModelReport()
        _REPORT.set(found)
    return found


def note_model_payload(payload: dict[str, Any] | None, sql: str | None) -> str | None:
    """Remember asked columns and readings. Return the SELECT to run.

    Flag off returns ``sql`` and does not read the payload.
    """
    if not enabled():
        return sql
    report = current_report()
    _absorb(report, payload)
    if isinstance(payload, dict):
        gen = payload.get("generative")
        if isinstance(gen, dict):
            inner = _absorb_text(report, str(gen.get("sql") or ""))
            if inner and not sql:
                sql = inner
    inner = _absorb_text(report, sql or "")
    if inner:
        sql = inner
    _REPORT.set(report)
    text = (sql or "").strip()
    return text or None


def apply_served(
    env: dict[str, Any],
    *,
    execute: Execute | None = None,
    dialect: str | None = None,
    question: str = "",
    space_id: str | None = None,
    session_id: str | None = None,
    report: ModelReport | None = None,
    page_size: int = PAGE_SIZE,
    grantable: set[str] | None = None,
) -> dict[str, Any]:
    """The served envelope, or the same object when the flag is off.

    ``page_size`` is the transport page, the same idea as the connector
    page. Rows past it stay available at ``next_offset``.
    """
    if not enabled() or not isinstance(env, dict):
        return env
    found = report if report is not None else current_report()
    if len(found.readings) >= 2:
        decided = _two_readings(
            found.readings,
            execute=execute,
            question=question,
            space_id=space_id,
            session_id=session_id,
            grantable=grantable,
            dialect=dialect,
        )
        if decided is not None:
            return decided
    if env.get("abstained") is True:
        return env
    sql = env.get("sql_used")
    rows = _row_list(env.get("rows"))
    if not isinstance(sql, str) or not sql.strip() or not rows:
        return _project_only(
            env,
            rows,
            found,
            question=question,
            space_id=space_id,
            session_id=session_id,
            dialect=dialect or SERVING_DIALECT,
        )
    tied, rewritten = _tie_rows(
        sql,
        rows,
        execute=execute,
        dialect=dialect or SERVING_DIALECT,
    )
    if tied is None:
        return _project_only(
            env,
            rows,
            found,
            question=question,
            space_id=space_id,
            session_id=session_id,
            dialect=dialect or SERVING_DIALECT,
        )
    return _finish(
        env,
        tied,
        sql=rewritten or sql,
        report=found,
        question=question,
        space_id=space_id,
        session_id=session_id,
        page_size=page_size,
        tied_note=True,
        dialect=dialect or SERVING_DIALECT,
    )


def apply_on_warehouse(
    env: dict[str, Any],
    *,
    warehouse: Path | None,
    dialect: str | None,
    question: str,
    space_id: str | None,
    session_id: str | None,
    grantable: set[str] | None,
    page_size: int = PAGE_SIZE,
) -> dict[str, Any]:
    """``apply_served`` with the serving read and the grant gate."""
    if not enabled():
        return env

    def execute(sql: str) -> tuple[list[dict[str, Any]] | None, str | None]:
        if warehouse is None or not Path(warehouse).is_file():
            return None, "warehouse_missing"
        if grantable is not None:
            from dms_executor.grant_struct import serve_gap

            gap = serve_gap(
                sql,
                grantable=set(grantable),
                dialect=dialect or SERVING_DIALECT,
                warehouse=warehouse,
            )
            if gap:
                return None, gap
        from dms_executor.sql_loop import run_readonly

        return run_readonly(sql, Path(warehouse))

    return apply_served(
        env,
        execute=execute,
        dialect=dialect,
        question=question,
        space_id=space_id,
        session_id=session_id,
        page_size=page_size,
        grantable=grantable,
    )


def _absorb(report: ModelReport, payload: dict[str, Any] | None) -> None:
    if not isinstance(payload, dict):
        return
    if "asked_columns" in payload:
        cols = _columns(payload.get("asked_columns"))
        if cols:
            report.asked_columns = cols
    if "readings" in payload:
        found = _readings(payload.get("readings"))
        if found:
            report.readings = found


def _absorb_text(report: ModelReport, text: str) -> str | None:
    parsed = _json_object(text)
    if parsed is None:
        return None
    _absorb(report, parsed)
    inner = str(parsed.get("sql") or "").strip()
    return inner or None


def _json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if not raw.startswith("{"):
        return None
    try:
        got = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(got, dict):
        return None
    return got


def _columns(raw: Any) -> list[str] | None:
    if not isinstance(raw, list) or not raw:
        return None
    if not all(isinstance(item, str) and item.strip() for item in raw):
        return None
    return [item.strip() for item in raw]


def _readings(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sql = str(item.get("sql") or "").strip()
        text = " ".join(str(item.get("text") or "").split())
        ident = str(item.get("id") or "").strip()
        if not sql or not text or not ident:
            continue
        out.append({"id": ident, "text": text, "sql": sql})
        if len(out) == 4:
            break
    return out


def _row_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _two_readings(
    readings: list[dict[str, str]],
    *,
    execute: Execute | None,
    question: str,
    space_id: str | None,
    session_id: str | None,
    grantable: set[str] | None,
    dialect: str | None,
) -> dict[str, Any] | None:
    """A clarify when the row sets differ. None when they are the same set."""
    if execute is None:
        return _reconfirm(
            question,
            space_id,
            session_id,
            "two_readings",
            "Choose which reading to run.",
        )
    executed: list[tuple[dict[str, str], list[dict[str, Any]]]] = []
    for item in readings[:4]:
        rows, err = execute(item["sql"])
        if err:
            kind = _stop_kind(err)
            if kind:
                return _hard(kind, question, space_id, session_id)
            return _reconfirm(
                question,
                space_id,
                session_id,
                "two_readings",
                "Choose which reading to run.",
            )
        executed.append((item, rows or []))
    signatures = {_signature(rows) for _, rows in executed}
    if len(signatures) < 2:
        return None
    shown: list[dict[str, str]] = []
    for item, rows in executed:
        shown.append(
            {
                "id": item["id"],
                "text": f"{item['text']} ({len(rows)} rows)",
                "sql_preview": item["sql"],
            }
        )
    return _clarify(
        question,
        shown,
        space_id=space_id,
        session_id=session_id,
        grantable=grantable,
        dialect=dialect,
    )


def _stop_kind(err: str) -> str | None:
    head = (err or "").split(":", 1)[0]
    if head in _DESTRUCTIVE:
        return "destructive"
    if head in _UNGRANTED:
        return "ungranted"
    return None


def _signature(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        items = tuple(sorted((str(key), _norm(row.get(key))) for key in row))
        lines.append(repr(items))
    lines.sort()
    return "\n".join(lines)


def _norm(value: Any) -> tuple[str, Any]:
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, int):
        return ("i", value)
    if isinstance(value, float):
        return ("f", round(value, 9))
    if isinstance(value, Decimal):
        return ("d", format(value, "f"))
    return ("v", value)


def _tie_rows(
    sql: str,
    served: list[dict[str, Any]],
    *,
    execute: Execute | None,
    dialect: str,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Tied rows and a SQL string that returns that set, or (None, None)."""
    if execute is None:
        return None, None
    plan = _plan(sql, dialect)
    if plan is None:
        return None, None
    probed, err = execute(plan.probe_sql)
    if err or not probed:
        return None, None
    names = [key.name for key in plan.keys]
    if any(name not in probed[0] for name in names):
        return None, None
    start = plan.offset
    end = plan.offset + plan.n
    if start >= len(probed) or end > len(probed):
        return None, None
    cutoff = _key_of(probed[end - 1], names)
    extended = end
    while extended < len(probed) and _key_of(probed[extended], names) == cutoff:
        extended += 1
    if extended == end:
        return None, None
    tied = _public_rows(probed[start:extended])
    if not tied or _same(tied, served):
        return None, None
    rewritten = _rewrite(plan, list(tied[0].keys()), probed[end - 1], dialect)
    return tied, rewritten


def _plan(sql: str, dialect: str) -> _Plan | None:
    try:
        tree = parse_one(sql, read=dialect)
    except Exception:
        return None
    if not isinstance(tree, exp.Select):
        return None
    limit_n = _literal_int(tree.args.get("limit"))
    if limit_n is None or limit_n < 1:
        return None
    order = tree.args.get("order")
    if not isinstance(order, exp.Order) or not order.expressions:
        return None
    offset_node = tree.args.get("offset")
    offset = 0 if offset_node is None else _literal_int(offset_node)
    if offset is None:
        return None
    raw: list[tuple[exp.Expression, bool]] = []
    projections = list(tree.expressions)
    for item in order.expressions:
        expr = _order_expr(item, projections)
        if expr is None:
            return None
        desc = bool(item.args.get("desc")) if isinstance(item, exp.Ordered) else False
        raw.append((expr, desc))
    while len(raw) > 1 and _bare(raw[-1][0]):
        raw.pop()
    if any(_has_agg(expr) for expr, _desc in raw) and not _grouped(tree):
        return None
    keys = [
        _Key(expr=expr, desc=desc, name=f"{_HELPER}{index}")
        for index, (expr, desc) in enumerate(raw)
    ]
    probe = tree.copy()
    probe.set("limit", None)
    probe.set("offset", None)
    added = [exp.alias_(key.expr.copy(), key.name) for key in keys]
    probe.set("expressions", [*probe.expressions, *added])
    try:
        probe_sql = probe.sql(dialect=dialect)
    except Exception:
        return None
    return _Plan(n=limit_n, offset=offset, keys=keys, probe_sql=probe_sql)


def _literal_int(node: exp.Expression | None) -> int | None:
    if node is None:
        return None
    expr = node.args.get("expression")
    if not isinstance(expr, exp.Literal) or expr.is_string:
        return None
    try:
        number = int(str(expr.this))
    except ValueError:
        return None
    if number < 0:
        return None
    return number


def _order_expr(
    item: exp.Expression, projections: list[exp.Expression]
) -> exp.Expression | None:
    node = item.this if isinstance(item, exp.Ordered) else item
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            index = int(str(node.this)) - 1
        except ValueError:
            return None
        if index < 0 or index >= len(projections):
            return None
        found = projections[index]
        return found.this if isinstance(found, exp.Alias) else found
    return node


def _bare(expr: exp.Expression) -> bool:
    node: exp.Expression = expr
    while isinstance(node, (exp.Alias, exp.Paren)):
        node = node.this
    return isinstance(node, exp.Column)


def _has_agg(expr: exp.Expression) -> bool:
    return any(isinstance(node, exp.AggFunc) for node in expr.walk())


def _grouped(select: exp.Select) -> bool:
    return isinstance(select.args.get("group"), exp.Group)


def _key_of(row: dict[str, Any], names: list[str]) -> tuple[Any, ...]:
    return tuple(_norm(row.get(name)) for name in names)


def _public_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                key: value
                for key, value in row.items()
                if not str(key).startswith(_HELPER)
            }
        )
    return out


def _same(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    if len(left) != len(right):
        return False
    return _signature(left) == _signature(right)


def _rewrite(
    plan: _Plan,
    columns: list[str],
    cutoff_row: dict[str, Any],
    dialect: str,
) -> str | None:
    if len(plan.keys) != 1 or not columns:
        return None
    key = plan.keys[0]
    literal = _sql_literal(cutoff_row.get(key.name))
    if literal is None:
        return None
    op = ">=" if key.desc else "<="
    shown = ", ".join(exp.to_identifier(name).sql(dialect=dialect) for name in columns)
    direction = "DESC" if key.desc else "ASC"
    return (
        f"SELECT {shown} FROM ({plan.probe_sql}) AS _dms_tie "
        f"WHERE {key.name} {op} {literal} "
        f"ORDER BY {key.name} {direction}"
    )


def _select_columns(sql: str | None, columns: list[str], dialect: str) -> str | None:
    """A SELECT of only the asked columns. The inner statement stays the model's."""
    if not sql or not columns:
        return sql
    shown = ", ".join(exp.to_identifier(name).sql(dialect=dialect) for name in columns)
    wrapped = f"SELECT {shown} FROM ({sql}) AS _dms_proj"
    try:
        parse_one(wrapped, read=dialect)
    except Exception:
        return sql
    return wrapped


def _sql_literal(value: Any) -> str | None:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _project(
    rows: list[dict[str, Any]], asked: list[str] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, bool]:
    """Rows, context columns, and whether an asked column is missing."""
    if not asked:
        return rows, None, False
    if not rows:
        return rows, None, True
    keys = [key for key in rows[0] if not str(key).startswith(_HELPER)]
    lookup = {key.lower(): key for key in keys}
    if any(name.lower() not in lookup for name in asked):
        return rows, None, True
    keep: list[str] = []
    for name in asked:
        key = lookup[name.lower()]
        if key not in keep:
            keep.append(key)
    extra = [key for key in keys if key not in keep]
    projected = [{key: row.get(key) for key in keep} for row in rows]
    context = None
    if extra:
        context = [
            {"column": col, "values": [row.get(col) for row in rows]} for col in extra
        ]
    return projected, context, False


def _project_only(
    env: dict[str, Any],
    rows: list[dict[str, Any]],
    report: ModelReport,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    dialect: str,
) -> dict[str, Any]:
    if not report.asked_columns:
        return env
    return _finish(
        env,
        rows,
        sql=env.get("sql_used") if isinstance(env.get("sql_used"), str) else None,
        report=report,
        question=question,
        space_id=space_id,
        session_id=session_id,
        page_size=PAGE_SIZE,
        tied_note=False,
        dialect=dialect,
    )


def _finish(
    env: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    sql: str | None,
    report: ModelReport,
    question: str,
    space_id: str | None,
    session_id: str | None,
    page_size: int,
    tied_note: bool,
    dialect: str,
) -> dict[str, Any]:
    projected, context, missing = _project(rows, report.asked_columns)
    if missing:
        return _reconfirm(
            question,
            space_id,
            session_id,
            "projection_mismatch",
            "Return only the columns the question asks for.",
        )
    full = projected
    shown = full
    page: dict[str, Any] | None = None
    if tied_note and page_size > 0 and len(full) > page_size:
        shown = full[:page_size]
        page = {
            "complete": False,
            "next_offset": page_size,
            "truncated": False,
            "bounded": False,
            "tie_row_count": len(full),
        }
        if context:
            context = [
                {"column": item["column"], "values": list(item["values"])[:page_size]}
                for item in context
            ]
    if not tied_note and not context and _same(shown, _row_list(env.get("rows"))):
        return env
    if context and full:
        sql = _select_columns(sql, list(full[0].keys()), dialect)
    if tied_note and page:
        text = (
            f"{len(full)} rows tie at the limit cutoff. "
            "The first page is shown. The rest continues at the next offset. "
            "Nothing was cut."
        )
    elif tied_note:
        text = (
            f"{len(full)} rows tie at the limit cutoff, so the full tied set is shown."
        )
    else:
        text = str(env.get("text") or "")
    if context:
        text = (text + " " if text else "") + "Extra measures are in context_columns."
    assumptions = [item for item in (env.get("assumptions") or []) if isinstance(item, str)]
    if tied_note and "tie_at_cutoff" not in assumptions:
        assumptions.append("tie_at_cutoff")
    if context and "context_columns" not in assumptions:
        assumptions.append("context_columns")
    return _install(
        env,
        rows=shown,
        text=text.strip(),
        sql=sql,
        assumptions=assumptions,
        context=context,
        page=page,
        question=question,
        space_id=space_id,
        session_id=session_id,
    )


def _install(
    env: dict[str, Any],
    *,
    rows: list[dict[str, Any]],
    text: str,
    sql: str | None,
    assumptions: list[str],
    context: list[dict[str, Any]] | None,
    page: dict[str, Any] | None,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.envelope import (
        _ensure_values,
        assert_envelope_valid,
        build_audit_receipt,
        chart_from_rows,
    )

    out = dict(env)
    out["rows"] = rows
    out["text"] = text
    out["sql_used"] = sql
    out["chart"] = chart_from_rows(rows)
    out["assumptions"] = assumptions
    out["values"] = _ensure_values(None, rows, abstained=False)
    out["audit_receipt"] = build_audit_receipt(
        abstained=False,
        badge=str(out.get("badge") or "L2_VALIDATED"),
        rows=rows,
        sql_used=sql,
        assumptions=assumptions,
    )
    if context:
        out["context_columns"] = context
    if page:
        out.update(page)
    try:
        assert_envelope_valid(out)
    except AssertionError:
        return _reconfirm(
            question,
            space_id,
            session_id,
            "tie_envelope",
            "Show every row that ties at the cutoff.",
        )
    return out


def _reconfirm(
    question: str,
    space_id: str | None,
    session_id: str | None,
    why: str,
    suggested: str,
) -> dict[str, Any]:
    from dms_executor.sql_vote import reconfirm_envelope

    env = reconfirm_envelope(
        question=question,
        why=why,
        closest_question=suggested,
        space_id=space_id,
        session_id=session_id,
    )
    block = env.get("reconfirm")
    if isinstance(block, dict):
        block["suggested_question"] = suggested
        block["reason_code"] = why
    return env


def _hard(
    kind: str,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    if kind == "destructive":
        reason = "hostile_sql"
        text = "That reading is not a read of the data, so I am not running it."
    else:
        reason = "ungranted"
        text = "That reading uses a table this Space does not grant, so I am not running it."
    env = build_abstain(
        reason=reason,
        ticket_reason=reason,
        question=question,
        stage="generative",
        answer_id="ans_tie_projection",
        text=text,
        abstain_reason=reason,
        rows=[],
        sql_used=None,
        assumptions=[reason],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
    )
    assert_envelope_valid(env)
    return env


def _clarify(
    question: str,
    readings: list[dict[str, str]],
    *,
    space_id: str | None,
    session_id: str | None,
    grantable: set[str] | None,
    dialect: str | None,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.clarify import _save
    from dms_executor.envelope import assert_envelope_valid, attach_clarify_block

    clarify_id = "clr_" + uuid.uuid4().hex[:16]
    lines = ["Which reading should I answer?"]
    lines.extend(f"{index}. {item['text']}" for index, item in enumerate(readings, 1))
    env = build_abstain(
        reason="clarify_ambiguous",
        ticket_reason="clarify_ambiguous",
        question=question,
        stage="generative",
        answer_id="ans_tie_clarify",
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
        question=question,
        readings=readings,
        clarify_id=clarify_id,
    )
    assert_envelope_valid(env)
    _save(
        clarify_id,
        {
            "question": question,
            "readings": [
                {
                    "id": item["id"],
                    "text": item["text"],
                    "slots": {},
                    "sql_preview": item["sql_preview"],
                }
                for item in readings
            ],
            "space_id": space_id,
            "session_id": session_id,
            "tables": sorted(grantable or []),
            "dialect": dialect or "",
            "free_text_ok": True,
        },
    )
    return env


__all__ = [
    "ENV",
    "PAGE_SIZE",
    "ModelReport",
    "apply_on_warehouse",
    "apply_served",
    "begin_turn",
    "current_report",
    "enabled",
    "note_model_payload",
]
