"""DMS_AI_FLOOR. Default off. The digit 1 only.

On, the ask does not run the fixed lanes (follow-up, verified query, pack,
compile, ranking, second reading, qualifier or join holds, contract ask).
One harness runs instead:

  schema text, catalog joins, and retrieved verified examples
  one generate
  execute
  check: grant, one read-only SELECT, EXPLAIN, and a column shape
  one error-fed retry
  one escalate, route_hint ``escalate`` (OpenVault picks the model)

Abstain only when the SQL is ungranted or not a read-only SELECT.
A miss after the escalate states no figure.

Off, callers do not enter. Unset, 0, true, yes, and on are off.
This module names no provider and no model id.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from dms_executor.demo_warehouse import SERVING_DIALECT

AI_FLOOR_ENV = "DMS_AI_FLOOR"
# The shared schema pack is 480 tokens. Measures take that budget first, and
# the join columns the model needs are what gets dropped, so it returns no SQL.
# 4000 fits the demo spine, joins included. A space with hundreds of columns
# still ranks inside this cap.
# ponytail: one budget for every question. Upgrade: a per-table cap when a
# space no longer fits.
_SCHEMA_TOKENS = 4000
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


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``DMS_AI_FLOOR`` is the digit 1."""
    source = os.environ if env is None else env
    return str(source.get(AI_FLOOR_ENV) or "").strip() == "1"


def _head(reason: str) -> str:
    return str(reason or "").strip().lower().split(":", 1)[0].strip()


def _direct(reason: str) -> bool:
    """Ungranted table or destructive SQL. A checker prefix still counts."""
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


def _ontology(warehouse: Path | None, space_id: str | None) -> Any:
    """In-process ontology for the prompt. A store miss is None, not an abstain."""
    from dms_executor.demo_grants import is_demo_space
    from dms_executor.lake_registry import SERVING_ERRORS

    try:
        if warehouse is not None and is_demo_space(space_id):
            from dms_executor.ontology import demo_ontology

            return demo_ontology(warehouse)
        from dms_executor.space_ontology import load_space_ontology

        return load_space_ontology(space_id)
    except SERVING_ERRORS:
        raise
    except Exception:
        return None


def _granted_tables(described: Any, grantable: set[str]) -> list[str]:
    """Bare names the catalog read that this Space grants. Cap 6."""
    datasets = described.get("datasets") if isinstance(described, dict) else None
    if not isinstance(datasets, list):
        return []
    granted = {item.lower() for item in grantable}
    names: list[str] = []
    for item in datasets:
        if not isinstance(item, dict):
            continue
        bare = str(item.get("name") or "").rsplit(".", 1)[-1]
        if not bare or bare.lower() not in granted or bare in names:
            continue
        names.append(bare)
        if len(names) >= 6:
            break
    return names


def _context(
    question: str,
    *,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    space_id: str | None,
) -> tuple[str, list[str]]:
    """Schema, ontology, and verified examples. A lookup miss is an empty block."""
    from dms_core.ask_stream import note_fetch, note_stage

    blocks: list[str] = []
    tables: list[str] = []
    note_stage("understand", "started")
    onto = _ontology(warehouse, space_id)
    note_stage("understand", "done")
    note_stage("find", "started")
    try:
        from cortex_client.insights import schema_context_is_empty

        from dms_executor.schema_context import build_schema_context, read_catalog

        _fingerprint, described = read_catalog(warehouse, grantable, dialect)
        tables = _granted_tables(described, grantable)
        built = build_schema_context(
            question,
            described,
            ontology=onto,
            grantable=grantable,
            space_id=space_id,
            max_tokens=_SCHEMA_TOKENS,
        )
        prompt = built.prompt
        if isinstance(prompt, str) and prompt.strip() and not schema_context_is_empty(prompt):
            blocks.append(prompt.strip())
        note_stage("find", "done")
        for name in tables:
            note_fetch(name)
    except Exception as exc:
        from dms_executor.lake_registry import SERVING_ERRORS

        note_stage("find", "failed")
        if isinstance(exc, SERVING_ERRORS):
            raise
    try:
        from dms_executor.verified_queries import examples_text, retrieve_verified_examples

        rows = retrieve_verified_examples(
            question,
            space_id=space_id,
            warehouse=warehouse,
            grantable=grantable,
            dialect=dialect,
            force=True,
        )
        text = examples_text(rows)
        if text.strip():
            blocks.append("verified_examples:\n" + text.strip())
    except Exception as exc:
        from dms_executor.lake_registry import SERVING_ERRORS

        if isinstance(exc, SERVING_ERRORS):
            raise
    return "\n\n".join(blocks), tables


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
        previous = feedback.get("previous_sql") or ""
        reason = feedback.get("reason") or ""
        asked = f"{asked}\n\nprevious_sql:\n{previous}\n\nfeedback:\n{reason}"
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


def _shape(rows: list[dict[str, Any]]) -> str | None:
    """None when the result has columns. Empty is not a figure."""
    if not rows:
        return "empty_result"
    for row in rows:
        if not isinstance(row, dict) or not row:
            return "shape"
    return None


def _stamp(
    env: dict[str, Any],
    *,
    started: float,
    calls: int,
    executes: int,
    stage: str,
    checks: tuple[str, ...],
) -> dict[str, Any]:
    ms = int((time.perf_counter() - started) * 1000)
    env["ai_floor"] = {
        "ms": ms,
        "calls": calls,
        "executes": executes,
        "stage": stage,
        "checks": list(checks),
    }
    assumptions = [str(item) for item in (env.get("assumptions") or [])]
    assumptions.append(
        f"ai_floor: ms={ms} calls={calls} executes={executes} stage={stage}"
    )
    if checks:
        assumptions.append("ai_floor: checks=" + ",".join(checks))
    env["assumptions"] = assumptions
    return env


def _abstain(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
    started: float,
    calls: int,
    executes: int,
    stage: str,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    env = build_abstain(
        reason=reason,
        question=question,
        stage="generative",
        answer_id="ans_ai_floor",
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
    return _stamp(
        env,
        started=started,
        calls=calls,
        executes=executes,
        stage=stage,
        checks=(),
    )


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
    """Ledger, then the L2 envelope. A ledger miss is a reason, not a figure."""
    from cortex_client.compute import PLAN_ORIGIN_GENERATE_SQL

    from dms_executor.generative_ask import (
        _l2_envelope,
        with_plan_origin,
        with_plan_source,
    )
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
            notes=("ai_floor",),
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
    return env


def _named_model(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    raw = payload.get("served_model")
    return isinstance(raw, str) and bool(raw.strip())


def run_floor(
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
    """Generate, execute, check, one retry, then role ``escalate``."""
    from dms_core.ask_stream import note_stage

    from dms_executor.generative_ask import query_sql_from_payload, validate_compiled_sql
    from dms_executor.lake_registry import SERVING_ERRORS
    from dms_executor.manifest import OpenVaultTokenError
    from dms_executor.sql_loop import mask_feedback_text
    from dms_executor.verified_queries import rows_from_submit_result

    started = time.perf_counter()
    spoken = dialect or SERVING_DIALECT
    context, _tables = _context(
        question,
        warehouse=warehouse,
        grantable=set(grantable),
        dialect=spoken,
        space_id=space_id,
    )
    feedback: dict[str, str] | None = None
    calls = 0
    executes = 0
    stage = "generate"
    last_reason = "no_sql"
    # ``stage`` is rebound by the loop. The name stays for the miss stamp.
    for stage in ("generate", "repair", "escalate"):
        calls += 1
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
                return _abstain(
                    question,
                    why,
                    space_id=space_id,
                    session_id=session_id,
                    started=started,
                    calls=calls,
                    executes=executes,
                    stage=stage,
                )
            feedback = {
                "previous_sql": sql,
                "reason": mask_feedback_text(why),
            }
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
        executes += 1
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
            return _stamp(
                published,
                started=started,
                calls=calls,
                executes=executes,
                stage=stage,
                checks=_CHECKS,
            )
        last_reason = published
        if _direct(published):
            return _abstain(
                question,
                published,
                space_id=space_id,
                session_id=session_id,
                started=started,
                calls=calls,
                executes=executes,
                stage=stage,
            )
        feedback = {"previous_sql": sql, "reason": mask_feedback_text(published)}
    return _abstain(
        question,
        last_reason,
        space_id=space_id,
        session_id=session_id,
        started=started,
        calls=calls,
        executes=executes,
        stage=stage,
    )


def offline_label(
    warehouse: Path,
    gold_sql: str,
    candidate_sql: str,
    grantable: set[str],
    *,
    dialect: str = SERVING_DIALECT,
) -> str:
    """correct, wrong, abstain, or unscored. No model call and no network."""
    from dms_executor.generative_ask import validate_compiled_sql
    from dms_executor.sql_loop import run_readonly

    why = validate_compiled_sql(
        candidate_sql,
        grantable=set(grantable),
        warehouse=warehouse,
        dialect=dialect,
    )
    if why and _direct(why):
        return "abstain"
    if why:
        return "unscored"
    rows, err = run_readonly(candidate_sql, Path(warehouse))
    gold, gold_err = run_readonly(gold_sql, Path(warehouse))
    if err or gold_err or rows is None or gold is None:
        return "unscored"
    if _same_rows(rows, gold):
        return "correct"
    return "wrong"


def _same_rows(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    def _key(row: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(key), str(val)) for key, val in row.items()))

    return sorted(_key(row) for row in left) == sorted(_key(row) for row in right)
