"""NL19-EXPLAIN-REPAIR. Default off. Digit 1 only.

On, an EXPLAIN failure is not a bare abstain. The repair turn carries the
DuckDB error class and a scrubbed message, the granted physical schema,
low-cardinality enum values, and the ask-ladder rung. How many repairs
run is the insights call budget that is still left. The same SQL stops
the loop. What remains is an ask-back with a suggested question.

The envelope debug field and the audit receipt store the failing SQL
hash, the error class, the scrubbed message, and the repair count.
SQL text stays off that viewer surface. The in-process audit row gains
SQL text only for a non-viewer role whose statement is granted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENV = "DMS_EXPLAIN_REPAIR_TRACE"
# ponytail: a column with more than 8 distinct non-null values is not an enum
# hint. Raise this only if a real status domain exceeds 8 members.
_ENUM_CAP = 8
_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VALUE = re.compile(r"^[A-Za-z0-9_./:-]{1,40}$")
_SINGLE = re.compile(r"'([^']*)'")
_DOUBLE = re.compile(r'"([^"]*)"')
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MESSAGE_CAP = 2000
_LEASE = frozenset(
    {
        "ServingLeaseCap",
        "ServingLeaseQueueFull",
        "ServingWaitCancelled",
        "ServingDeadlineExceeded",
        "ServingDeadlineReserve",
        "OpenVaultTokenError",
    }
)
_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_AUDITS: list[dict[str, Any]] = []


@dataclass
class Facts:
    grantable: set[str]
    warehouse: Path | None
    dialect: str
    space_id: str | None
    session_id: str | None


@dataclass
class Trace:
    sql: str
    sql_hash: str
    error_class: str
    error_message: str
    repair_attempts: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "sql_hash": self.sql_hash,
            "error_class": self.error_class,
            "error_message": self.error_message,
            "repair_attempts": self.repair_attempts,
        }


_FACTS: ContextVar[Facts | None] = ContextVar("dms_explain_repair_facts", default=None)
_TRACE: ContextVar[Trace | None] = ContextVar("dms_explain_repair_trace", default=None)


def enabled() -> bool:
    """True only when ``DMS_EXPLAIN_REPAIR_TRACE`` is the digit 1."""
    return os.environ.get(ENV, "0").strip() == "1"


def clear_trace() -> None:
    _TRACE.set(None)


def bind_facts(
    *,
    grantable: set[str],
    warehouse: Path | None,
    dialect: str,
    space_id: str | None,
    session_id: str | None,
) -> None:
    _FACTS.set(
        Facts(
            grantable=set(grantable),
            warehouse=warehouse,
            dialect=dialect,
            space_id=space_id,
            session_id=session_id,
        )
    )


def facts_bound() -> bool:
    return _FACTS.get() is not None


def audit_records() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(item) for item in _AUDITS]


def clear_audits() -> None:
    with _LOCK:
        _AUDITS.clear()


def stamp_envelope(env: dict[str, Any] | None) -> dict[str, Any] | None:
    """Copy the pending trace onto debug and the audit receipt. Flag off: no-op."""
    if not enabled() or not isinstance(env, dict):
        return env
    trace = _TRACE.get()
    if trace is None:
        return env
    _TRACE.set(None)
    body = trace.public()
    debug = env.get("debug")
    merged = dict(debug) if isinstance(debug, dict) else {}
    merged["explain_repair"] = dict(body)
    env["debug"] = merged
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        receipt["explain_repair"] = dict(body)
    _write_audit(trace, body)
    return env


def repair_explain(
    *,
    asked: str,
    sql: str,
    why: str,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    ctx: dict[str, Any],
    check: Callable[[str], str | None],
    submit_ok: Callable[[str, dict[str, Any] | None], dict[str, Any]],
    abstain_direct: Callable[[str, str], dict[str, Any]],
    budget: Any = None,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    dialect: str | None = None,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Repair while the call budget remains. A repeated SQL asks back."""
    from dms_executor.ask_ladder import RUNGS, is_direct_abstain
    from dms_executor.sql_loop import mask_feedback_text

    facts = _FACTS.get()
    lake = warehouse if warehouse is not None else (facts.warehouse if facts else None)
    allowed = (
        set(grantable) if grantable is not None else (set(facts.grantable) if facts else set())
    )
    kind = dialect if dialect is not None else (facts.dialect if facts else "")
    space = space_id if space_id is not None else (facts.space_id if facts else None)
    session = session_id if session_id is not None else (facts.session_id if facts else None)
    if budget is None:
        budget = _fresh_budget()
    schema_text, enums, names = _schema_block(lake, allowed, kind)
    error_class, error_message = _capture(sql, lake, names, why)
    trace = Trace(
        sql=sql.strip(),
        sql_hash=_hash(sql),
        error_class=error_class,
        error_message=error_message,
    )
    seen = {_norm(sql)}
    current = sql.strip()
    last_payload: dict[str, Any] | None = None
    attempts = 0
    while budget.allow():
        attempts += 1
        rung = _rung(attempts, RUNGS)
        prompt = _prompt(
            asked=asked,
            sql=current,
            error_class=trace.error_class,
            error_message=trace.error_message,
            schema_text=schema_text,
            enum_text=_enum_text(enums),
            rung=rung,
        )
        if not budget.take():
            attempts -= 1
            break
        body = dict(ctx)
        body.pop("route_hint", None)
        body["sql_vote_prompt"] = prompt
        body["sql_vote_kind"] = "repair"
        body["sql_loop_feedback"] = {
            "previous_sql": mask_feedback_text(current),
            "reason": mask_feedback_text(f"{trace.error_class}: {trace.error_message}"),
            "prompt": prompt,
            "ask_ladder_rung": rung,
        }
        if rung == "escalate":
            body["route_hint"] = "escalate"
        got = _call(compute, body)
        last_payload = got
        nxt = _sql_of(got)
        if not nxt or _norm(nxt) in seen:
            break
        seen.add(_norm(nxt))
        reason = check(nxt)
        if not reason:
            trace.repair_attempts = attempts
            _TRACE.set(trace)
            return submit_ok(nxt, got)
        if is_direct_abstain(reason):
            trace.repair_attempts = attempts
            _TRACE.set(trace)
            return abstain_direct(reason, nxt)
        if not str(reason).startswith("explain:"):
            break
        current = nxt.strip()
        error_class, error_message = _capture(current, lake, names, str(reason))
        trace = Trace(
            sql=current,
            sql_hash=_hash(current),
            error_class=error_class,
            error_message=error_message,
        )
    trace.repair_attempts = attempts
    _TRACE.set(trace)
    return _askback(
        asked=asked,
        trace=trace,
        enums=enums,
        payload=last_payload,
        space_id=space,
        session_id=session,
    )


def _fresh_budget() -> Any:
    from dms_executor.sql_loop import AskCallBudget

    budget = AskCallBudget()
    used = 1
    from dms_executor.scale_miss import calls_used
    from dms_executor.scale_miss import enabled as scale_on

    if scale_on():
        used = max(used, calls_used())
    budget.used = min(used, budget.cap)
    return budget


def _rung(attempt: int, rungs: tuple[str, ...]) -> str:
    from dms_executor.ask_ladder import current_climb

    session = current_climb()
    base = 0
    if session is not None and session.index < len(rungs):
        base = session.index
    index = base + max(attempt, 1) - 1
    if index >= len(rungs):
        return rungs[-1]
    return rungs[index]


def _prompt(
    *,
    asked: str,
    sql: str,
    error_class: str,
    error_message: str,
    schema_text: str,
    enum_text: str,
    rung: str,
) -> str:
    return (
        "The previous SELECT failed EXPLAIN. "
        "Write one SELECT that fixes that failure. "
        "Use only granted tables and columns. "
        "Use stored values exactly as listed.\n"
        f"error_class:\n{error_class}\n"
        f"error_message:\n{error_message}\n"
        f"ask_ladder_rung:\n{rung}\n"
        f"granted_schema:\n{schema_text}\n"
        f"enum_values:\n{enum_text}\n"
        f"previous_sql:\n{sql}\n"
        f"Question:\n{asked}"
    )


def _schema_block(
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
) -> tuple[str, list[tuple[str, list[str]]], set[str]]:
    from dms_executor.schema_retry import _catalog_column_rows, _column_granted

    rows: list[tuple[str, str, str]] = []
    names: set[str] = set()
    for table, column, typ, _flag in _catalog_column_rows(warehouse):
        if not _column_granted(table, column, grantable, dialect or "duckdb"):
            continue
        rows.append((table, column, typ))
        names.add(table)
        names.add(column)
    rows.sort(key=lambda item: (item[0].lower(), item[1].lower()))
    lines = [f"{table}.{column} {typ}".rstrip() for table, column, typ in rows]
    enums = _enums(warehouse, rows)
    return "\n".join(lines), enums, names


def _enums(
    warehouse: Path | None,
    rows: list[tuple[str, str, str]],
) -> list[tuple[str, list[str]]]:
    if warehouse is None or not Path(warehouse).is_file():
        return []
    from dms_executor.bronze import quote_ident
    from dms_executor.demo_warehouse import connect_serving

    picked: list[tuple[str, str, str]] = []
    for table, column, typ in rows:
        if not _text_type(typ):
            continue
        try:
            quote_ident(table)
            quote_ident(column)
        except ValueError:
            continue
        picked.append((table, column, typ))
    if not picked:
        return []
    con = connect_serving(Path(warehouse))
    out: list[tuple[str, list[str]]] = []
    try:
        for table, column, _typ in picked:
            values = _distinct(con, table, column)
            if values is None:
                continue
            out.append((f"{table}.{column}", values))
    finally:
        con.close()
    return out


def _distinct(con: Any, table: str, column: str) -> list[str] | None:
    from dms_executor.bronze import quote_ident

    statement = (
        f"SELECT DISTINCT {quote_ident(column)} FROM {quote_ident(table)} "
        f"WHERE {quote_ident(column)} IS NOT NULL LIMIT {_ENUM_CAP + 1}"
    )
    try:
        fetched = con.execute(statement).fetchall()
    except Exception:
        return None
    if len(fetched) > _ENUM_CAP:
        return None
    values: list[str] = []
    for row in fetched:
        if not row or row[0] is None:
            continue
        text = str(row[0])
        if not _VALUE.fullmatch(text):
            return None
        values.append(text)
    if not values:
        return None
    return sorted(set(values))


def _text_type(typ: str) -> bool:
    name = str(typ or "").upper()
    return any(token in name for token in ("CHAR", "TEXT", "STRING", "ENUM"))


def _enum_text(enums: list[tuple[str, list[str]]]) -> str:
    return "\n".join(f"{name}: {' | '.join(values)}" for name, values in enums)


def _capture(
    sql: str,
    warehouse: Path | None,
    names: set[str],
    why: str,
) -> tuple[str, str]:
    if warehouse is None or not Path(warehouse).is_file():
        return _class_from_why(why), ""
    from dms_executor.demo_warehouse import connect_serving
    from dms_executor.generative_ask import _refusal_before_explain

    keep = _keep_names(sql, names)
    con = connect_serving(Path(warehouse))
    try:
        gate = _refusal_before_explain(con, sql)
        if gate:
            return _class_from_why(gate), _scrub("", keep, sql)
        con.execute(f"EXPLAIN {sql}")
    except Exception as exc:  # noqa: BLE001 - the class and scrubbed message are the trace
        return _safe_class(type(exc).__name__), _scrub(str(exc), keep, sql)
    finally:
        con.close()
    return _class_from_why(why), ""


def _keep_names(sql: str, names: set[str]) -> set[str]:
    found = {name.lower() for name in names if name}
    for match in _WORD.finditer(sql or ""):
        found.add(match.group(0).lower())
    return found


def _scrub(message: str, keep: set[str], sql: str) -> str:
    from dms_executor.sql_loop import mask_feedback_text

    def _one(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner.lower() in keep:
            return match.group(0)
        quote = match.group(0)[0]
        return f"{quote}[value]{quote}"

    text = message or ""
    stripped = sql.strip()
    if stripped:
        text = text.replace(stripped, "")
    text = re.sub(r"LINE \d+:[^\n]*", "", text)
    text = _DOUBLE.sub(_one, _SINGLE.sub(_one, text))
    text = mask_feedback_text(text)
    text = " ".join(text.split())
    if len(text) > _MESSAGE_CAP:
        return text[:_MESSAGE_CAP] + " [cut]"
    return text


def _class_from_why(why: str) -> str:
    parts = [part.strip() for part in str(why or "").split(":") if part.strip()]
    for part in parts:
        if part == "explain":
            continue
        if _CLASS.fullmatch(part):
            return part
    return "Error"


def _safe_class(name: str) -> str:
    if _CLASS.fullmatch(name or ""):
        return name
    return "Error"


def _hash(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode()).hexdigest()


def _norm(sql: str) -> str:
    return " ".join(str(sql or "").split())


def _sql_of(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("query_sql") or "").strip()


def _reading(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("suggested_question", "closest_question"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().splitlines()[0].strip()[:180]
    return ""


def _call(
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    body: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        got = compute(body)
    except Exception as exc:
        if type(exc).__name__ in _LEASE:
            raise
        return None
    return got if isinstance(got, dict) else None


def _askback(
    *,
    asked: str,
    trace: Trace,
    enums: list[tuple[str, list[str]]],
    payload: dict[str, Any] | None,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    model_reading = _reading(payload)
    if enums:
        column, values = enums[0]
        shown = ", ".join(values)
        fallback = f"Which value of {column} should the SELECT keep?"
        fact = f"{column} stores {shown}."
    else:
        fallback = "Which granted column should the SELECT read?"
        fact = "The binder did not bind the SELECT to a granted column."
    reading = model_reading or fallback
    text = f"{reading} Missing fact: {fact}"
    env = build_abstain(
        reason="explain_repair",
        question=asked,
        stage="explain_repair",
        answer_id="ans_explain_repair",
        text=text,
        rows=[],
        sql_used=None,
        assumptions=[
            "explain_repair",
            f"error_class: {trace.error_class}",
        ],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    env["reconfirm"] = {
        "why": "explain_repair",
        "reason_code": "explain_repair",
        "reason_text": text,
        "suggested_question": reading,
        "closest_question": reading,
        "missing_fact": fact,
    }
    assert_envelope_valid(env)
    return env


def _write_audit(trace: Trace, body: dict[str, Any]) -> None:
    row = dict(body)
    visible = _sql_for_role(trace.sql)
    if visible:
        row["sql_text"] = visible
    with _LOCK:
        _AUDITS.append(row)
    _LOG.warning("explain_repair %s", json.dumps(body, sort_keys=True))


def _sql_for_role(sql: str) -> str | None:
    """SQL text for a non-viewer audit row. The envelope never receives it."""
    role = os.environ.get("DMS_ACTOR_ROLE", "viewer").strip().lower() or "viewer"
    if role == "viewer":
        return None
    facts = _FACTS.get()
    if facts is None or not sql.strip():
        return None
    from dms_executor.grant_struct import serve_gap

    gap = serve_gap(
        sql,
        grantable=set(facts.grantable),
        dialect=facts.dialect or None,
    )
    if gap:
        return None
    return sql.strip()
