"""NL19-NO-SQL-GENERATE. Default off. Digit 1 only.

When generate returns no SQL, one more generate runs. The prompt carries
the previous refusal reason and every granted table's physical column
names and enum values. SQL from that call is checked and served. A second
empty generate is an ask-back with a non-empty suggestion. It is not the
bare certify refusal.

Ungranted tables, PII, and destructive SQL do not retry. DMS does not
name a model. The question text is not scanned.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

ENV = "DMS_GENERATE_RETRY_ASKBACK"
_GIVE_UP = frozenset(
    {
        "no_sql",
        "insights_refused",
        "insights_no_sql_no_ranking",
        "ladder_exhausted",
        "schema_repair_exhausted",
        "no_plan",
        "query_sql_empty",
        "cannot_certify",
    }
)
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
_SKIP_SCHEMA = frozenset({"information_schema", "pg_catalog", "system"})
_RETRY = (
    "The previous generate returned no SQL.\n"
    "Write one SELECT that answers the question.\n"
    "Use only granted tables and columns.\n"
    "The schema lists every granted physical column and its enum values."
)
_ASK = (
    "I could not certify a query yet. "
    "Reply yes to run this reading, or ask it in your own words: "
)
_FALLBACK = "Show one measure for one granted table in this Space."


class Scope:
    """One ask. Filled before the opening generate. Empty until then."""

    def __init__(self) -> None:
        self.compute: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None
        self.ctx: dict[str, Any] = {}
        self.question = ""
        self.warehouse: Path | None = None
        self.dialect = ""
        self.grantable: set[str] = set()
        self.ontology: Any = None
        self.space_id: str | None = None
        self.session_id: str | None = None
        self.submit: Callable[[str], Any] | None = None
        self.ledger_append: Callable[[dict[str, Any]], Any] | None = None
        self.calls = 0
        self.tried = False
        self.recovering = False
        self.last_reason = "no_sql"
        self.audit: dict[str, Any] | None = None


_SCOPE: ContextVar[Scope | None] = ContextVar("dms_generate_retry", default=None)


def enabled() -> bool:
    """True only when ``DMS_GENERATE_RETRY_ASKBACK`` is the digit 1."""
    return os.environ.get(ENV, "0").strip() == "1"


def bind_scope(scope: Scope | None) -> Token[Scope | None]:
    return _SCOPE.set(scope)


def reset_scope(token: Token[Scope | None]) -> None:
    _SCOPE.reset(token)


def current_scope() -> Scope | None:
    return _SCOPE.get()


def current_audit() -> dict[str, Any] | None:
    scope = _SCOPE.get()
    if scope is None or not isinstance(scope.audit, dict):
        return None
    return dict(scope.audit)


def arm_scope(
    *,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None] | None,
    ctx: dict[str, Any],
    question: str,
    warehouse: Path | None,
    dialect: str,
    grantable: set[str],
    ontology: Any,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[[str], Any] | None,
    ledger_append: Callable[[dict[str, Any]], Any] | None,
) -> None:
    """Remember this ask so a later empty generate can retry once."""
    scope = _SCOPE.get()
    if scope is None:
        return
    scope.compute = compute
    scope.ctx = ctx
    scope.question = question
    scope.warehouse = warehouse
    scope.dialect = dialect
    scope.grantable = set(grantable)
    scope.ontology = ontology
    scope.space_id = space_id
    scope.session_id = session_id
    scope.submit = submit
    scope.ledger_append = ledger_append
    if scope.calls < 1:
        scope.calls = 1


def refusal_class(reason: str) -> str:
    """Pipeline reason class. The question text is not read."""
    text = " ".join(str(reason or "").split())
    if "cannot certify" in text.casefold():
        return "cannot_certify"
    from dms_executor.pipeline_failure import _reason_code

    code = _reason_code(text)
    if code != "unspecified":
        return code
    from dms_executor.ask_ladder import named_code

    named = named_code(text)
    if named != "unspecified":
        return named
    return "no_sql"


def is_give_up(reason: str) -> bool:
    """Empty generate, or a ladder that ended with no SQL. Not a direct refusal."""
    from dms_executor.ask_ladder import is_direct_abstain

    if is_direct_abstain(reason):
        return False
    head = refusal_class(reason).split(":", 1)[0]
    return head in _GIVE_UP


def opening_reason(payload: dict[str, Any] | None) -> str:
    """Why the opening generate produced no SQL. Payload fields only."""
    from cortex_client.compute import insights_fail_reason

    fail = insights_fail_reason(payload if isinstance(payload, dict) else None)
    if fail:
        return fail
    if isinstance(payload, dict):
        status = str(payload.get("status") or "").upper()
        if status == "REFUSE" or payload.get("ok") is False:
            return "insights_refused"
    return "no_sql"


def recover_empty_generate(reason: str) -> dict[str, Any] | None:
    """Served envelope when the retry wrote SQL.

    None when the flag is off, the reason is a direct refusal, or the retry
    is still empty. An empty retry is remembered so the terminal give-up
    can ask back. Ranking and compile still run in between.
    """
    if not enabled():
        return None
    from dms_executor.ask_ladder import is_direct_abstain

    if is_direct_abstain(reason):
        return None
    return _recover(reason)


def recover_give_up(
    reason: str,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """Retry a bare give-up, or an ask-back when that retry is still empty."""
    if not enabled() or not is_give_up(reason):
        return None
    scope = _SCOPE.get()
    if scope is None or scope.recovering:
        return None
    if question:
        scope.question = question
    scope.space_id = space_id
    scope.session_id = session_id
    if scope.tried:
        return _askback(scope, reason)
    env = _recover(reason)
    if isinstance(env, dict) and env.get("abstained") is not True:
        return env
    if scope.tried:
        return _askback(scope, reason)
    return None


def terminal_askback() -> dict[str, Any] | None:
    """Ask-back after an empty retry when nothing else served. None otherwise."""
    if not enabled():
        return None
    scope = _SCOPE.get()
    if scope is None or not scope.tried or scope.recovering:
        return None
    env = _askback(scope, scope.last_reason or "no_sql")
    audit = env.get("generate_audit")
    if isinstance(audit, dict):
        stamped = dict(audit)
        stamped["defer"] = True
        env["generate_audit"] = stamped
    return env


def peel_deferred_askback(
    env: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Hold a terminal ask-back so a later grant check can still refuse.

    A give-up ask-back that replaced a certify refusal is not deferred.
    """
    if not isinstance(env, dict):
        return env, None
    audit = env.get("generate_audit")
    if not isinstance(audit, dict) or not audit.get("defer"):
        return env, None
    clean = {key: value for key, value in audit.items() if key != "defer"}
    env["generate_audit"] = clean
    return None, env


def prefer_askback(
    env: dict[str, Any],
    held: dict[str, Any] | None,
) -> dict[str, Any]:
    """Use the ask-back only when the downstream result is a bare give-up."""
    if held is None:
        return env
    if env.get("abstained") is not True:
        return env
    reason = str(env.get("abstain_reason") or "")
    from dms_executor.ask_ladder import is_direct_abstain

    if reason and is_direct_abstain(reason):
        return env
    text = str(env.get("text") or "").casefold()
    if is_give_up(reason) or text.startswith("generative miss:"):
        return held
    if "cannot certify an ontology-grounded query" in text:
        return held
    return env


def granted_schema_block(
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
) -> str:
    """Physical column names and enum values for every granted table.

    Enum members come from the type catalog, not from table rows. A read
    failure is an empty block. The exception text is not logged.
    """
    if warehouse is None or not Path(warehouse).is_file() or not grantable:
        return ""
    try:
        columns = _catalog_columns(warehouse)
        labels = _enum_catalog(warehouse)
    except Exception as exc:  # noqa: BLE001 - catalog miss is an empty block
        _LOG.warning("granted_schema_unread %s", type(exc).__name__)
        return ""
    lines = ["GRANTED SCHEMA"]
    wrote = False
    for key in sorted(columns):
        schema, table = key
        if not _granted(schema, table, grantable, dialect):
            continue
        wrote = True
        shown = table if schema.lower() in {"main", "public", ""} else f"{schema}.{table}"
        lines.append(f"- {shown}")
        for name, typ in columns[key]:
            members = _enum_members(typ, labels)
            if members:
                lines.append(f"  - {name} {typ} enum={', '.join(members)}")
            else:
                lines.append(f"  - {name} {typ}")
    if not wrote:
        return ""
    return "\n".join(lines)


def append_granted_schema(
    text: str,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
) -> str:
    """The same prompt when the flag is off. On, the schema block is inserted."""
    if not enabled():
        return text
    block = granted_schema_block(warehouse, grantable, dialect)
    if not block:
        return text
    marker = "\nQuestion:\n"
    if marker in text:
        head, tail = text.rsplit(marker, 1)
        return f"{head.rstrip()}\n\n{block}{marker}{tail}"
    return f"{text.rstrip()}\n\n{block}"


def _recover(reason: str) -> dict[str, Any] | None:
    scope = _SCOPE.get()
    if scope is None or scope.compute is None or scope.recovering:
        return None
    if scope.tried:
        return _askback(scope, reason)
    scope.tried = True
    scope.recovering = True
    scope.last_reason = reason or "no_sql"
    try:
        block = granted_schema_block(scope.warehouse, scope.grantable, scope.dialect)
        prompt = _retry_prompt(scope.question, reason, block)
        ctx = dict(scope.ctx)
        ctx["sql_vote_prompt"] = prompt
        ctx["sql_vote_kind"] = "generate_retry"
        if block:
            ctx["schema_context"] = block
        try:
            payload = scope.compute(ctx)
        except Exception as exc:
            if type(exc).__name__ in _LEASE:
                raise
            payload = None
        scope.calls += 1
        from dms_executor.generative_ask import query_sql_from_payload

        sql = query_sql_from_payload(payload if isinstance(payload, dict) else None)
        _remember(scope, "generate_retry", reason)
        if not sql:
            return None
        from dms_executor.generative_ask import validate_compiled_sql

        why = validate_compiled_sql(
            sql,
            grantable=set(scope.grantable),
            warehouse=scope.warehouse,
            dialect=scope.dialect or None,
        )
        if why:
            from dms_executor.ask_ladder import is_direct_abstain

            if is_direct_abstain(why):
                return _direct(scope, why, sql)
            return _askback(scope, reason)
        if scope.submit is None or scope.ledger_append is None:
            return _askback(scope, reason)
        env = _serve(scope, sql, payload if isinstance(payload, dict) else None)
        if isinstance(env, dict) and env.get("abstained") is not True:
            _attach(env, scope.audit)
            return env
        if isinstance(env, dict):
            from dms_executor.ask_ladder import is_direct_abstain

            shown = str(env.get("abstain_reason") or env.get("reason_code") or "")
            if is_direct_abstain(shown):
                return env
        return _askback(scope, reason)
    finally:
        scope.recovering = False


def _serve(scope: Scope, sql: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    from dms_executor.generative_ask import (
        PLAN_ORIGIN_GENERATE_SQL,
        PLAN_SOURCE_OTHER,
        _sql_route,
        _submit_validated,
    )
    from dms_executor.ontology import coverage_from_sql_path

    note = _note(scope.audit)
    return _submit_validated(
        sql,
        question=scope.question,
        space_id=scope.space_id,
        session_id=scope.session_id,
        submit=scope.submit,  # type: ignore[arg-type]
        ledger_append=scope.ledger_append,  # type: ignore[arg-type]
        notes=("generate_retry", note),
        plan_source=PLAN_SOURCE_OTHER,
        coverage=coverage_from_sql_path(sql=sql),
        warehouse=scope.warehouse,
        grantable=set(scope.grantable),
        plan_origin=PLAN_ORIGIN_GENERATE_SQL,
        served_route=_sql_route(payload),
    )


def _direct(scope: Scope, reason: str, sql: str) -> dict[str, Any]:
    from dms_executor.generative_ask import PLAN_SOURCE_OTHER, _abstain

    return _abstain(
        scope.question,
        reason,
        space_id=scope.space_id,
        session_id=scope.session_id,
        plan_source=PLAN_SOURCE_OTHER,
        sql=sql,
    )


def _retry_prompt(asked: str, reason: str, block: str) -> str:
    from dms_executor.sql_loop import mask_feedback_text

    feedback = mask_feedback_text(reason or "no_sql")
    body = f"{_RETRY}\n\nrefusal:\n{feedback}"
    if block:
        body = f"{body}\n\n{block}"
    return f"{body.rstrip()}\nQuestion:\n{asked or ''}"


def _suggestion(scope: Scope) -> str:
    measures = getattr(scope.ontology, "measures", None)
    if isinstance(measures, dict):
        for name in measures:
            label = " ".join(str(name).replace("_", " ").split())
            if label:
                return f"Show {label} for this Space."
    for name in sorted(str(item) for item in scope.grantable):
        label = name.split(".")[-1].strip()
        if label:
            return f"Show one measure from {label}."
    return _FALLBACK


def _askback(scope: Scope, reason: str) -> dict[str, Any]:
    from datetime import UTC, datetime

    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    _remember(scope, "askback", reason)
    suggestion = _suggestion(scope)
    if not suggestion.strip():
        suggestion = _FALLBACK
    text = _ASK + suggestion
    code = refusal_class(reason)
    env = build_abstain(
        reason=code,
        question=scope.question,
        stage="generative",
        answer_id="ans_generate_retry_askback",
        text=text,
        rows=[],
        sql_used=None,
        values=[],
        assumptions=[f"generate_retry: {code}", _note(scope.audit)],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=scope.space_id,
        session_id=scope.session_id,
        ask_mode="live",
        route="generated",
    )
    env["reconfirm"] = {
        "reason_code": code,
        "reason_text": text,
        "suggested_question": suggestion,
        "closest_question": suggestion,
    }
    _attach(env, scope.audit)
    assert_envelope_valid(env)
    return env


def _remember(scope: Scope, rung: str, reason: str) -> None:
    audit = {
        "legs": scope.calls if scope.calls > 0 else 1,
        "rung": rung,
        "refusal_class": refusal_class(reason),
    }
    scope.audit = audit
    _LOG.warning("generate_audit %s", json.dumps(audit, sort_keys=True))


def _note(audit: dict[str, Any] | None) -> str:
    if not isinstance(audit, dict):
        return "generate_audit legs=0 rung=none refusal_class=no_sql"
    return (
        f"generate_audit legs={audit.get('legs')} "
        f"rung={audit.get('rung')} refusal_class={audit.get('refusal_class')}"
    )


def _attach(env: dict[str, Any], audit: dict[str, Any] | None) -> None:
    if isinstance(audit, dict):
        env["generate_audit"] = dict(audit)


def _catalog_columns(warehouse: Path) -> dict[tuple[str, str], list[tuple[str, str]]]:
    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(Path(warehouse))
    try:
        rows = con.execute(
            "SELECT table_schema, table_name, column_name, data_type "
            "FROM information_schema.columns "
            "ORDER BY table_schema, table_name, ordinal_position"
        ).fetchall()
    finally:
        con.close()
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for schema, table, column, typ in rows:
        schema_s = str(schema or "")
        table_s = str(table or "")
        column_s = str(column or "")
        if not table_s or not column_s or schema_s.lower() in _SKIP_SCHEMA:
            continue
        grouped.setdefault((schema_s, table_s), []).append((column_s, str(typ or "unknown")))
    return grouped


def _enum_catalog(warehouse: Path) -> dict[str, list[str]]:
    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(Path(warehouse))
    try:
        try:
            rows = con.execute(
                "SELECT type_name, labels FROM duckdb_types() WHERE logical_type = 'ENUM'"
            ).fetchall()
        except Exception:  # noqa: BLE001 - this engine has no enum catalog
            return {}
    finally:
        con.close()
    out: dict[str, list[str]] = {}
    for name, labels in rows:
        members = _label_list(labels)
        if not members:
            continue
        key = str(name or "").strip()
        if key:
            out[key.casefold()] = members
    return out


def _label_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        quoted = _quoted_pieces(raw)
        if quoted:
            return quoted
        text = raw.replace("\n", " ").strip()
        return [text] if text else []
    if isinstance(raw, (bytes, bytearray)):
        return []
    try:
        items = list(raw)
    except TypeError:
        return []
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).replace("\n", " ").strip()
        if text:
            out.append(text)
    return out


def _quoted_pieces(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in text:
        if quote:
            if ch == quote:
                piece = "".join(buf).strip()
                if piece:
                    out.append(piece)
                buf = []
                quote = ""
            else:
                buf.append(ch)
        elif ch in {"'", '"'}:
            quote = ch
    return out


def _enum_members(typ: str, labels: dict[str, list[str]]) -> list[str]:
    raw = " ".join(str(typ or "").split())
    head = raw.split("(", 1)[0].strip()
    for key in (raw.casefold(), head.casefold()):
        found = labels.get(key)
        if found:
            return list(found)
    if head.casefold() == "enum" or "'" in raw or '"' in raw:
        return _quoted_pieces(raw)
    return []


def _granted(schema: str, table: str, grantable: set[str], dialect: str) -> bool:
    from dms_executor.schema_retry import _table_granted

    if _table_granted(table, grantable, dialect):
        return True
    qualified = f"{schema}.{table}" if schema else table
    if qualified == table:
        return False
    return _table_granted(qualified, grantable, dialect)
