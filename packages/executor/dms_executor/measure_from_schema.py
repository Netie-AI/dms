"""NL19-SCHEMA-ONTO-GAP. Default off. Digit 1 only.

On, an ontology compile of ``unknown_measure`` or ``missing_metric`` asks the
model to write SQL from the granted schema and retrieved verified examples.
``no_path`` and ``missing_join`` propose that SQL on the existing join confirm
card. A dropped qualifier is one model check and repair, then the same gates.
The model SQL is not a stored answer. DMS does not name a model.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

ENV = "DMS_MEASURE_FROM_SCHEMA"
SCHEMA_KIND = "schema_sql"
QUALIFIER_KIND = "qualifier_check"
SQL_HEADS = frozenset({"unknown_measure", "missing_metric"})
JOIN_HEADS = frozenset({"no_path", "missing_join"})


class Session:
    """One ask. Bound only while the flag is on."""

    def __init__(self, compute: Callable[..., Any] | None) -> None:
        self.compute = compute
        self.ctx: dict[str, Any] = {}
        self.repairs = 0
        self.warehouse: Path | None = None
        self.grantable: set[str] = set()
        self.dialect = ""
        self.space_id: str | None = None
        self.ontology: Any = None
        self.embedder: Any = None
        self.question = ""


_SESSION: ContextVar[Session | None] = ContextVar("dms_measure_from_schema", default=None)


def enabled() -> bool:
    """True only when ``DMS_MEASURE_FROM_SCHEMA`` is the digit 1."""
    return os.environ.get(ENV, "0").strip() == "1"


def bind_session(session: Session | None) -> Token[Session | None]:
    return _SESSION.set(session)


def reset_session(token: Token[Session | None]) -> None:
    _SESSION.reset(token)


def current_session() -> Session | None:
    return _SESSION.get()


def handoff_head(reason: str) -> str | None:
    """Compile head this flag hands to the model, or None."""
    head = str(reason or "").strip().split(":", 1)[0].strip()
    if head in SQL_HEADS or head in JOIN_HEADS:
        return head
    return None


class SchemaGap:
    """Compile refused with a head this flag hands onward. Not an envelope."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


def granted_schema_prompt(
    question: str,
    *,
    warehouse: Path | None,
    grantable: set[str] | None,
    dialect: str | None,
    ontology: Any,
    space_id: str | None,
) -> str:
    """Granted catalog prompt. Does not flip ``DMS_SCHEMA_CONTEXT``."""
    from cortex_client.insights import schema_context_is_empty

    from dms_executor.schema_context import build_schema_context, read_catalog

    try:
        _fingerprint, plain = read_catalog(warehouse, grantable, dialect)
        built = build_schema_context(
            question,
            plain if isinstance(plain, dict) else {},
            ontology=ontology,
            grantable=set(grantable) if grantable is not None else None,
            space_id=space_id,
        )
    except Exception:  # noqa: BLE001 -- a catalog miss still asks the model
        return ""
    if schema_context_is_empty(built.prompt):
        return ""
    return built.prompt


def example_block(
    question: str,
    *,
    space_id: str | None,
    warehouse: Path | None,
    grantable: set[str] | None,
    dialect: str | None,
    embedder: Any,
) -> str:
    """Retrieved examples for the writer. Never SQL to execute."""
    from dms_executor.verified_queries import examples_text, retrieve_verified_examples

    rows = retrieve_verified_examples(
        question,
        space_id=space_id,
        warehouse=warehouse,
        grantable=grantable,
        dialect=dialect,
        embedder=embedder,
        force=True,
    )
    return examples_text(rows)


def writer_context(
    ctx: dict[str, Any] | None,
    *,
    question: str,
    warehouse: Path | None,
    grantable: set[str] | None,
    dialect: str | None,
    ontology: Any,
    space_id: str | None,
    embedder: Any,
) -> dict[str, Any]:
    """Copy of the ask context plus schema and examples. No model id."""
    from cortex_client.insights import SCHEMA_CONTEXT_FIELD

    out = dict(ctx or {})
    if not str(out.get(SCHEMA_CONTEXT_FIELD) or "").strip():
        prompt = granted_schema_prompt(
            question,
            warehouse=warehouse,
            grantable=grantable,
            dialect=dialect,
            ontology=ontology,
            space_id=space_id,
        )
        if prompt:
            out[SCHEMA_CONTEXT_FIELD] = prompt
    if not str(out.get("vq_examples") or "").strip():
        block = example_block(
            question,
            space_id=space_id,
            warehouse=warehouse,
            grantable=grantable,
            dialect=dialect,
            embedder=embedder,
        )
        if block:
            out["vq_examples"] = block
    out["sql_vote_kind"] = SCHEMA_KIND
    out.pop("model", None)
    out.pop("provider", None)
    out.pop("strict", None)
    return out


def _sql_from(payload: Any) -> str:
    from cortex_client.compute import insights_query_sql

    from dms_executor.generative_ask import query_sql_from_payload

    if not isinstance(payload, dict):
        return ""
    return query_sql_from_payload(payload) or insights_query_sql(payload) or ""


def ask_sql(compute: Callable[..., Any], ctx: dict[str, Any]) -> str:
    try:
        got = compute(ctx)
    except Exception:  # noqa: BLE001 -- a miss is an ask-back, not a traceback
        return ""
    return _sql_from(got)


def reads_two_base_tables(sql: str, dialect: str | None) -> bool:
    """True when the statement reads two base tables. CTEs are not tables."""
    import sqlglot
    from sqlglot import exp

    from dms_executor.demo_warehouse import SERVING_DIALECT

    read = dialect or SERVING_DIALECT
    try:
        trees = [tree for tree in sqlglot.parse(sql or "", read=read) if tree is not None]
    except Exception:  # noqa: BLE001
        return False
    if len(trees) != 1:
        return False
    root = trees[0]
    cte_names = {
        str(cte.alias_or_name).lower()
        for cte in root.find_all(exp.CTE)
        if getattr(cte, "alias_or_name", None)
    }
    reads = 0
    for table in root.find_all(exp.Table):
        name = str(table.name or "")
        db = table.args.get("db")
        schema = str(getattr(db, "name", "") or "") if db is not None else ""
        if not schema and name.lower() in cte_names:
            continue
        if name:
            reads += 1
    return reads >= 2


def ask_back(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Typed reconfirm. The suggested question is the ask, and it is not empty."""
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.generative_ask import PLAN_SOURCE_ONTOLOGY, _abstain

    suggested = " ".join(str(question or "").split())
    env = _abstain(
        question,
        reason,
        space_id=space_id,
        session_id=session_id,
        plan_source=PLAN_SOURCE_ONTOLOGY,
        stage="schema_measure",
    )
    env["reconfirm"] = {
        "reason_code": reason,
        "reason_text": str(env.get("text") or ""),
        "suggested_question": suggested,
        "why": str(reason).split(":", 1)[0],
        "closest_question": suggested,
    }
    assert_envelope_valid(env)
    return env


def qualifier_reconfirm(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    return ask_back(question, reason, space_id=space_id, session_id=session_id)


def try_qualifier_repair(sql: str, question: str, reason: str) -> str | None:
    """One model check. Returns replacement SQL, or None when there is nothing to run.

    ponytail: one repair per ask. A second miss reconfirms. Upgrade: the extract loop.
    """
    sess = current_session()
    if sess is None or sess.compute is None or sess.repairs >= 1:
        return None
    sess.repairs += 1
    from cortex_client.insights import SCHEMA_CONTEXT_FIELD

    base = dict(sess.ctx)
    if not str(base.get(SCHEMA_CONTEXT_FIELD) or "").strip():
        prompt = granted_schema_prompt(
            question,
            warehouse=sess.warehouse,
            grantable=sess.grantable,
            dialect=sess.dialect,
            ontology=sess.ontology,
            space_id=sess.space_id,
        )
        if prompt:
            base[SCHEMA_CONTEXT_FIELD] = prompt
    if not str(base.get("vq_examples") or "").strip():
        block = example_block(
            question,
            space_id=sess.space_id,
            warehouse=sess.warehouse,
            grantable=sess.grantable,
            dialect=sess.dialect,
            embedder=sess.embedder,
        )
        if block:
            base["vq_examples"] = block
    base["sql_vote_kind"] = QUALIFIER_KIND
    base["route_hint"] = "reason"
    base.pop("model", None)
    base.pop("provider", None)
    base.pop("strict", None)
    base["sql_loop_feedback"] = {
        "previous_sql": sql,
        "reason": reason,
        "prompt": question,
    }
    repaired = ask_sql(sess.compute, base)
    if repaired and repaired != sql:
        return repaired
    return None


def _direct_abstain(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
    sql: str,
) -> dict[str, Any]:
    from dms_executor.generative_ask import PLAN_SOURCE_ONTOLOGY, _abstain

    return _abstain(
        question,
        reason,
        space_id=space_id,
        session_id=session_id,
        plan_source=PLAN_SOURCE_ONTOLOGY,
        sql=sql,
        stage="schema_measure",
    )


def _serve_model_sql(
    sql: str,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[..., Any],
    ledger_append: Callable[..., Any],
    warehouse: Path | None,
    grantable: set[str],
) -> dict[str, Any]:
    from cortex_client.compute import PLAN_ORIGIN_GENERATE_SQL

    from dms_executor.generative_ask import PLAN_SOURCE_ONTOLOGY, _submit_validated
    from dms_executor.ontology import coverage_from_sql_path

    return _submit_validated(
        sql,
        question=question,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        notes=("schema_measure:model_sql",),
        plan_source=PLAN_SOURCE_ONTOLOGY,
        coverage=coverage_from_sql_path(sql=sql),
        warehouse=warehouse,
        plan_origin=PLAN_ORIGIN_GENERATE_SQL,
        grantable=grantable,
    )


def _after_gates(
    sql: str,
    *,
    question: str,
    reason: str,
    head: str,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[..., Any],
    ledger_append: Callable[..., Any],
    warehouse: Path | None,
    dialect: str | None,
    grantable: set[str],
) -> dict[str, Any]:
    """Existing pre-execute gates, then serve or the join card. Not a rule compile."""
    from dms_executor.generative_ask import (
        _checker_is_direct,
        _join_confirm_envelope,
        validate_compiled_sql,
        validated_sql_reason,
    )

    why = validate_compiled_sql(sql, grantable=grantable, warehouse=warehouse, dialect=dialect)
    if why:
        if _checker_is_direct(why) or str(why).startswith("hostile_sql"):
            return _direct_abstain(question, why, space_id=space_id, session_id=session_id, sql=sql)
        return ask_back(question, why, space_id=space_id, session_id=session_id)
    gap = validated_sql_reason(sql, question=question, warehouse=warehouse, grantable=grantable)
    if gap and str(gap).startswith("unhonored_qualifier:"):
        repaired = try_qualifier_repair(sql, question, str(gap))
        if repaired and repaired != sql:
            return _after_gates(
                repaired,
                question=question,
                reason=reason,
                head=head,
                space_id=space_id,
                session_id=session_id,
                submit=submit,
                ledger_append=ledger_append,
                warehouse=warehouse,
                dialect=dialect,
                grantable=grantable,
            )
        return ask_back(question, str(gap), space_id=space_id, session_id=session_id)
    if gap:
        return _direct_abstain(
            question, str(gap), space_id=space_id, session_id=session_id, sql=sql
        )
    if head in JOIN_HEADS:
        if reads_two_base_tables(sql, dialect):
            return _join_confirm_envelope(
                question=question,
                sql=sql,
                grantable=grantable,
                warehouse=warehouse,
                dialect=dialect,
                space_id=space_id,
                session_id=session_id,
            )
        return ask_back(question, reason, space_id=space_id, session_id=session_id)
    return _serve_model_sql(
        sql,
        question=question,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        warehouse=warehouse,
        grantable=grantable,
    )


def run_schema_handoff(
    *,
    question: str,
    reason: str,
    ctx: dict[str, Any],
    compute: Callable[..., Any],
    warehouse: Path | None,
    dialect: str,
    grantable: set[str],
    ontology: Any,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[..., Any],
    ledger_append: Callable[..., Any],
    embedder: Any = None,
) -> dict[str, Any]:
    """One SQL write from schema and examples, then the existing gates."""
    head = handoff_head(reason) or (
        "unhonored_qualifier" if str(reason).startswith("unhonored_qualifier") else ""
    )
    nxt = writer_context(
        ctx,
        question=question,
        warehouse=warehouse,
        grantable=grantable,
        dialect=dialect,
        ontology=ontology,
        space_id=space_id,
        embedder=embedder,
    )
    sess = current_session()
    if sess is not None:
        sess.ctx = nxt
        sess.compute = compute
        sess.warehouse = warehouse
        sess.grantable = set(grantable)
        sess.dialect = dialect
        sess.space_id = space_id
        sess.ontology = ontology
        sess.embedder = embedder
        sess.question = question
    call = dict(nxt)
    call["sql_loop_feedback"] = {
        "previous_sql": "",
        "reason": reason,
        "prompt": question,
    }
    sql = ask_sql(compute, call)
    if not sql:
        return ask_back(question, reason, space_id=space_id, session_id=session_id)
    return _after_gates(
        sql,
        question=question,
        reason=reason,
        head=head,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        warehouse=warehouse,
        dialect=dialect,
        grantable=set(grantable),
    )


__all__ = [
    "ENV",
    "JOIN_HEADS",
    "QUALIFIER_KIND",
    "SCHEMA_KIND",
    "SQL_HEADS",
    "SchemaGap",
    "Session",
    "ask_back",
    "bind_session",
    "current_session",
    "enabled",
    "example_block",
    "granted_schema_prompt",
    "handoff_head",
    "qualifier_reconfirm",
    "reset_session",
    "run_schema_handoff",
    "try_qualifier_repair",
    "writer_context",
]
