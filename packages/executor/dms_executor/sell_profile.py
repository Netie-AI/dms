"""DMS_SELL_PROFILE. Default off. The digit 1 only.

On, Studio /ask skips the fixed lanes and calls the AI floor
(``dms_executor.ai_floor.run_floor``) when that module is present.
Until it lands, the same generate, read-only execute, check, one repair,
and ``route_hint`` escalate run here. Table picking calls
``dms_executor.big_schema.schema_for_ask`` when that module is present.
This module does not edit either module's internals and names no model.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dms_executor.demo_warehouse import SERVING_DIALECT

SELL_PROFILE_ENV = "DMS_SELL_PROFILE"
ROW_CAP = 20
_CHECKS = ("grant", "read_only", "explain", "shape")
_READ_ONLY_HEADS = frozenset(
    {
        "hostile_sql",
        "statement_not_allowed",
        "multi_statement",
        "sql_not_analyzable",
    }
)
_GRANT_HEADS = frozenset(
    {
        "ungranted",
        "sql_relation_not_granted",
        "path_not_allowed",
    }
)
# READY-06 entry points. The first one that exists is the one we call.
_SCHEMA_FNS = ("schema_for_ask", "relevant_schema", "pick_schema")


def enabled(env: dict[str, str] | None = None) -> bool:
    """True only when ``DMS_SELL_PROFILE`` is the digit 1."""
    source = os.environ if env is None else env
    return str(source.get(SELL_PROFILE_ENV) or "").strip() == "1"


def _head(reason: str) -> str:
    return str(reason or "").strip().lower().split(":", 1)[0].strip()


def _direct(reason: str) -> bool:
    from dms_executor.ask_ladder import is_direct_abstain
    from dms_executor.grant_struct import structural_grant_stop

    if structural_grant_stop(reason) or is_direct_abstain(reason):
        return True
    return _head(reason) in _GRANT_HEADS | _READ_ONLY_HEADS


def _sentence(reason: str) -> str:
    head = _head(reason)
    if head in _GRANT_HEADS:
        return (
            "I can't answer this: the query reads a table this Space does "
            "not grant. No figure was returned."
        )
    if head in _READ_ONLY_HEADS:
        return (
            "I can't answer this: that statement is not a read-only query. "
            "No figure was returned."
        )
    return "I don't have a checked answer for that yet, so I am not stating a figure."


def _schema_text(
    question: str,
    *,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    space_id: str | None,
) -> str:
    """Relevant schema. Big-schema entry when it is installed, else the catalog prompt."""
    try:
        import dms_executor.big_schema as big_schema
    except ImportError:
        big_schema = None
    if big_schema is not None:
        for name in _SCHEMA_FNS:
            fn = getattr(big_schema, name, None)
            if not callable(fn):
                continue
            try:
                got = fn(
                    question,
                    warehouse=warehouse,
                    grantable=grantable,
                    dialect=dialect,
                    space_id=space_id,
                )
            except Exception:
                continue
            if isinstance(got, str) and got.strip():
                return got.strip()
            if isinstance(got, dict):
                prompt = got.get("prompt")
                if isinstance(prompt, str) and prompt.strip():
                    return prompt.strip()
    try:
        from dms_executor.schema_context import build_schema_context, read_catalog

        _fingerprint, described = read_catalog(warehouse, grantable, dialect)
        built = build_schema_context(
            question,
            described,
            grantable=grantable,
            space_id=space_id,
        )
        prompt = getattr(built, "prompt", None)
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    except Exception as exc:
        from dms_executor.lake_registry import SERVING_ERRORS

        if isinstance(exc, SERVING_ERRORS):
            raise
    return ""


def _clarify_block(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Structured ask-back. Ids and labels come from the payload."""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("clarify")
    if not isinstance(raw, dict):
        return None
    question = raw.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    readings: list[dict[str, str]] = []
    supplied = raw.get("readings")
    if isinstance(supplied, list):
        for item in supplied:
            if not isinstance(item, dict):
                continue
            rid = item.get("reading_id")
            label = item.get("label")
            if isinstance(rid, str) and rid.strip() and isinstance(label, str) and label.strip():
                readings.append({"reading_id": rid.strip(), "label": label.strip()})
    else:
        options = raw.get("options")
        if isinstance(options, list):
            n = 0
            for item in options:
                if isinstance(item, str) and item.strip():
                    n += 1
                    readings.append({"reading_id": f"r{n}", "label": item.strip()})
    if not readings:
        return None
    return {
        "clarify_id": "sell",
        "question": question.strip(),
        "readings": readings[:3],
    }


def _catalog(
    question: str,
    context: str,
    *,
    feedback: dict[str, str] | None,
    escalate: bool,
) -> dict[str, Any]:
    asked = question.strip()
    if context:
        asked = f"{asked}\n\n{context}"
    if feedback:
        asked = (
            f"{asked}\n\nprevious_sql:\n{feedback.get('previous_sql') or ''}"
            f"\n\nfeedback:\n{feedback.get('reason') or ''}"
        )
    catalog: dict[str, Any] = {
        "sql_vote_prompt": asked,
        "sql_vote_kind": "escalate" if escalate else "direct",
    }
    if feedback:
        catalog["sql_loop_feedback"] = {
            "previous_sql": feedback.get("previous_sql") or "",
            "reason": feedback.get("reason") or "",
        }
    if escalate:
        catalog["route_hint"] = "escalate"
    return catalog


def _abstain(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    env = build_abstain(
        reason=reason,
        question=question,
        stage="generative",
        answer_id="ans_sell_profile",
        text=_sentence(reason),
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[f"GEN-01: {reason}"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    assert_envelope_valid(env)
    return env


def _publish(
    sql: str,
    result: Any,
    payload: dict[str, Any] | None,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    ledger_append: Callable[[dict[str, Any]], Any],
) -> dict[str, Any] | str:
    from cortex_client.compute import PLAN_ORIGIN_GENERATE_SQL

    from dms_executor.generative_ask import _l2_envelope, with_plan_origin, with_plan_source
    from dms_executor.ontology import coverage_from_sql_path
    from dms_executor.route_stamp import ROUTE_MODEL, stamp_route

    run_id = str(getattr(result, "run_id", "") or "")
    try:
        led = ledger_append({"sql": sql, "run_id": run_id})
    except Exception:
        return "ledger_append_failed"
    entry_id = getattr(led, "entry_id", None) if led is not None else None
    led_hash = getattr(led, "hash", None)
    if not (isinstance(entry_id, str) and entry_id.strip()):
        return "ledger_entry_missing"
    if not (isinstance(led_hash, str) and led_hash.strip()) or led_hash == entry_id:
        return "ledger_hash_missing"
    try:
        env = _l2_envelope(
            sql=sql,
            result=result,
            question=question,
            space_id=space_id,
            session_id=session_id,
            audit_id=entry_id.strip(),
            notes=("sell_profile",),
            plan_source="other",
            coverage=coverage_from_sql_path(sql=sql),
            plan_origin=PLAN_ORIGIN_GENERATE_SQL,
            served_route=ROUTE_MODEL if _named_model(payload) else "",
        )
    except Exception:
        return "envelope_failed"
    if env.get("abstained") is True:
        return str(env.get("abstain_reason") or "coverage_invalid")
    with_plan_source(env, "other")
    with_plan_origin(env, PLAN_ORIGIN_GENERATE_SQL)
    if _named_model(payload):
        stamp_route(env, ROUTE_MODEL)
        for key in ("served_model", "served_provider", "served_local"):
            if isinstance(payload, dict) and key in payload:
                env[key] = payload[key]
    plain = payload.get("answer") if isinstance(payload, dict) else None
    if isinstance(plain, str) and plain.strip() and not any(ch.isdigit() for ch in plain):
        env["text"] = plain.strip() + "\n" + str(env.get("text") or "")
    return env


def _named_model(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    raw = payload.get("served_model")
    return isinstance(raw, str) and bool(raw.strip())


def _shape(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return "empty_result"
    for row in rows:
        if not isinstance(row, dict) or not row:
            return "shape"
    return None


def _harness(
    question: str,
    *,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    submit: Callable[[str], Any],
    ledger_append: Callable[[dict[str, Any]], Any],
    warehouse: Path | None,
    grantable: set[str],
    dialect: str | None,
    space_id: str | None,
    session_id: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Generate, execute, check, one retry, then role escalate. No rule lane."""
    from dms_core.ask_stream import note_stage

    from dms_executor.generative_ask import query_sql_from_payload, validate_compiled_sql
    from dms_executor.lake_registry import SERVING_ERRORS
    from dms_executor.manifest import OpenVaultTokenError
    from dms_executor.sql_loop import mask_feedback_text
    from dms_executor.verified_queries import rows_from_submit_result

    spoken = dialect or SERVING_DIALECT
    note_stage("understand", "started")
    context = _schema_text(
        question,
        warehouse=warehouse,
        grantable=set(grantable),
        dialect=spoken,
        space_id=space_id,
    )
    note_stage("understand", "done")
    note_stage("find", "started")
    note_stage("find", "done")
    feedback: dict[str, str] | None = None
    last_reason = "no_sql"
    stage = "generate"
    for stage in ("generate", "repair", "escalate"):
        if stage == "escalate":
            note_stage("escalate", "started")
        note_stage("write", "started")
        catalog = _catalog(
            question,
            context,
            feedback=feedback,
            escalate=stage == "escalate",
        )
        try:
            payload = compute(catalog)
        except SERVING_ERRORS:
            raise
        except OpenVaultTokenError:
            raise
        except Exception:
            payload = None
        body = payload if isinstance(payload, dict) else None
        sql = query_sql_from_payload(body)
        asked = _clarify_block(body)
        if asked is not None and not sql:
            note_stage("write", "done")
            env = _abstain(
                question,
                "clarify",
                space_id=space_id,
                session_id=session_id,
            )
            env["text"] = asked["question"]
            env["clarify"] = asked
            return env, []
        if not sql:
            last_reason = "no_sql"
            feedback = {"previous_sql": "", "reason": "no_sql"}
            note_stage("write", "failed")
            continue
        note_stage("write", "done")
        why = validate_compiled_sql(
            sql,
            grantable=set(grantable),
            warehouse=warehouse,
            dialect=spoken,
        )
        if why:
            last_reason = why
            note_stage("check", "failed")
            if _direct(why):
                return (
                    _abstain(
                        question,
                        why,
                        space_id=space_id,
                        session_id=session_id,
                    ),
                    [],
                )
            feedback = {"previous_sql": sql, "reason": mask_feedback_text(why)}
            continue
        note_stage("run", "started")
        try:
            result = submit(sql)
        except SERVING_ERRORS:
            raise
        except OpenVaultTokenError:
            raise
        except Exception:
            last_reason = "submit_failed"
            feedback = {"previous_sql": sql, "reason": "submit_failed"}
            note_stage("run", "failed")
            continue
        if getattr(result, "ok", None) is False:
            last_reason = "submit_failed"
            feedback = {"previous_sql": sql, "reason": "submit_failed"}
            note_stage("run", "failed")
            continue
        note_stage("run", "done")
        rows = rows_from_submit_result(result)
        shape = _shape(rows)
        note_stage("check", "started")
        if shape:
            last_reason = shape
            feedback = {"previous_sql": sql, "reason": shape}
            note_stage("check", "failed")
            continue
        note_stage("check", "done")
        published = _publish(
            sql,
            result,
            body,
            question=question,
            space_id=space_id,
            session_id=session_id,
            ledger_append=ledger_append,
        )
        if isinstance(published, dict):
            return published, list(_CHECKS)
        last_reason = published
        if _direct(published):
            return (
                _abstain(
                    question,
                    published,
                    space_id=space_id,
                    session_id=session_id,
                ),
                [],
            )
        feedback = {"previous_sql": sql, "reason": mask_feedback_text(published)}
    return (
        _abstain(
            question,
            last_reason,
            space_id=space_id,
            session_id=session_id,
        ),
        [],
    )


def _attach_chart(env: dict[str, Any], payload: dict[str, Any] | None) -> None:
    if env.get("abstained") is True or not isinstance(payload, dict):
        return
    rows = env.get("rows")
    if not isinstance(rows, list) or not rows:
        return
    from dms_executor.chart_spec import column_profile, validate_spec

    profile = column_profile(rows)
    spec = validate_spec(payload.get("chart_spec") or payload.get("chart"), profile)
    if spec is None:
        return
    env["chart_spec"] = spec
    env["chart"] = spec


def _decorate(
    env: dict[str, Any],
    payload: dict[str, Any] | None,
    checks: list[str],
) -> dict[str, Any]:
    block = _clarify_block(payload)
    if block is not None and env.get("abstained") is True and not env.get("sql_used"):
        env["clarify"] = block
        env["text"] = block["question"]
    _attach_chart(env, payload)
    env["sell_profile"] = True
    env["sell_row_cap"] = ROW_CAP
    if checks:
        env["sell_checks"] = list(checks)
    return env


def run_sell(
    question: str,
    *,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    submit: Callable[[str], Any],
    ledger_append: Callable[[dict[str, Any]], Any],
    warehouse: Path | None,
    grantable: set[str],
    dialect: str | None,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Studio ask for the sell profile. Calls ``run_floor`` when it is installed."""
    from dms_core.ask_stream import note_stage

    captured: dict[str, Any] = {}

    def watching(catalog: dict[str, Any]) -> dict[str, Any] | None:
        payload = compute(catalog)
        if isinstance(payload, dict):
            captured["last"] = payload
        return payload

    started = time.perf_counter()
    try:
        from dms_executor.ai_floor import run_floor as floor_run
    except ImportError:
        floor_run = None
    if floor_run is not None:
        env = floor_run(
            question,
            compute=watching,
            submit=submit,
            ledger_append=ledger_append,
            warehouse=warehouse,
            grantable=grantable,
            dialect=dialect,
            space_id=space_id,
            session_id=session_id,
        )
        raw = env.get("ai_floor") if isinstance(env, dict) else None
        checks = list(raw.get("checks") or []) if isinstance(raw, dict) else []
    else:
        env, checks = _harness(
            question,
            compute=watching,
            submit=submit,
            ledger_append=ledger_append,
            warehouse=warehouse,
            grantable=grantable,
            dialect=dialect,
            space_id=space_id,
            session_id=session_id,
        )
    if isinstance(env, dict):
        last = captured.get("last")
        _decorate(env, last if isinstance(last, dict) else None, checks)
        env["sell_ms"] = int((time.perf_counter() - started) * 1000)
    note_stage("answer", "done")
    return env
