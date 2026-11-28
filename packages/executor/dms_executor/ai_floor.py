"""DMS_AI_FLOOR. Default off. The digit 1 only.

On, the ask does not run the fixed lanes (follow-up, verified query, pack,
compile, ranking, second reading, qualifier or join holds, contract ask).
The model is the analyst. This module is the harness:

  a role, then <schema>, <ontology>, <examples>, <question>
  the model thinks and calls the run_sql tool once
  the harness executes on the lake that serves the query, then checks
  grant, one read-only SELECT, PII and injection, and a row shape
  a database miss is a tool_result in the next question, then role escalate

Abstain only when the SQL is ungranted or not a read-only SELECT, or when
the model stops with no governed result. The abstain names the first failure.

Off, callers do not enter. Unset, 0, true, yes, and on are off.
This module names no provider and no model id.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from dms_executor.demo_warehouse import SERVING_DIALECT

AI_FLOOR_ENV = "DMS_AI_FLOOR"
# Canonical tool. ``execute_sql`` is the same call under the sell-profile name.
TOOL_NAME = "run_sql"
_TOOL_NAMES = frozenset({TOOL_NAME, "execute_sql"})
# The shared schema pack is 480 tokens. Measures take that budget first, and
# the join columns the model needs are what gets dropped, so it returns no SQL.
# 4000 fits the demo spine, joins included. A space with hundreds of columns
# still ranks inside this cap.
# ponytail: one budget for every question. Upgrade: a per-table cap when a
# space no longer fits.
_SCHEMA_TOKENS = 4000
# ponytail: one call on a hit, two more only after a miss. Upgrade: a
# longer cap when a measured pack shows the third call still helps.
_MAX_CALLS = 3
_DEADLINE_MARGIN_S = 0.25
_CHECKS = ("grant", "read_only", "explain", "shape")
_ROLE = (
    "You are the analyst for this Space. "
    "The harness executes SQL, caps the rows, and checks the grant. "
    "You do not state a figure. "
    "Think, then call the run_sql tool with one read-only SELECT. "
    "When a tool_result arrives, call run_sql again if the SQL should change. "
    "Stop when the result answers the question."
)
_TOOL_SCHEMA = json.dumps(
    {
        "name": "run_sql",
        "description": (
            "Run one read-only SELECT on this Space lake. "
            "The harness returns rows or the database error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    }
)
_SELECT_HEAD = re.compile(r"(?is)^\s*(with|select)\b")
_TOOL_CALL = re.compile(
    r'"name"\s*:\s*"(run_sql|execute_sql)".{0,800}?"sql"\s*:\s*"((?:\\.|[^"\\])*)"',
    re.DOTALL,
)
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


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _public_reason(reason: str) -> str:
    """Stable abstain code. The first one is what the envelope keeps."""
    text = str(reason or "").strip()
    low = text.lower()
    if "binderexception" in low:
        return "explain_binder_error"
    if low.startswith("insights_timeout") or low == "generate_timeout":
        return "generate_timeout"
    if "serving_deadline_reserve" in low:
        return "serving_deadline_reserve"
    return text or "no_sql"


def _generate_slice(index: int) -> float | None:
    """Seconds this generate may use. None when no ask deadline is bound.

    The first call starts at once. The cap is the 8s Insights default, and
    never the 60s ceiling, and never more than the time still left.
    ``index`` is the attempt, kept so a later miss sees a fresh remainder.
    """
    from cortex_client.compute import INSIGHTS_ASK_TIMEOUT_SECONDS

    from dms_executor.lake_registry import serving_time_left_s

    _ = index
    left = serving_time_left_s()
    if left is None:
        return None
    usable = left - _DEADLINE_MARGIN_S
    if usable <= 0:
        return 0.0
    return min(float(INSIGHTS_ASK_TIMEOUT_SECONDS), usable)


def _ontology_text(onto: Any) -> str:
    """Physical measures and joins. Object names that are not tables stay out."""
    from dms_executor.schema_context import ontology_payload

    payload = ontology_payload(onto)
    lines: list[str] = []
    for measure in payload.get("measures") or []:
        if not isinstance(measure, dict):
            continue
        name = str(measure.get("name") or "").strip()
        grain = str(measure.get("grain") or "").strip()
        expr = " ".join(str(measure.get("expression") or "").split())
        if name and expr:
            lines.append(f"measure {name} grain {grain} expression {expr}")
    for link in payload.get("relationships") or []:
        if not isinstance(link, dict):
            continue
        left = str(link.get("from") or "").strip()
        right = str(link.get("to") or "").strip()
        src = str(link.get("from_column") or "").strip()
        dst = str(link.get("to_column") or "").strip()
        if left and right and src and dst:
            lines.append(f"{left}.{src} = {right}.{dst}")
    return "\n".join(lines)


def _sql_in_tool_text(blob: str) -> str | None:
    match = _TOOL_CALL.search(blob)
    if not match or match.group(1) not in _TOOL_NAMES:
        return None
    try:
        sql = str(json.loads(f'"{match.group(2)}"')).strip()
    except json.JSONDecodeError:
        sql = match.group(2).strip()
    return sql or None


def _tool_sql(payload: dict[str, Any] | None) -> str | None:
    """SQL from a run_sql tool call, or a SELECT the engine already lifted."""
    if not isinstance(payload, dict):
        return None
    direct = str(payload.get("query_sql") or "").strip()
    parsed = _sql_in_tool_text(direct) if direct else None
    if parsed:
        return parsed
    # A non-SELECT in query_sql is still the tool call. The safety layer
    # refuses it. Dropping it here would hide the statement as no_sql.
    if direct:
        return direct
    blobs: list[str] = []
    for key in ("text", "answer", "completion", "output"):
        val = payload.get(key)
        if isinstance(val, str):
            blobs.append(val)
    gen = payload.get("generative")
    if isinstance(gen, dict):
        for key in ("sql", "text", "raw"):
            val = gen.get(key)
            if isinstance(val, str):
                blobs.append(val)
    for blob in blobs:
        found = _sql_in_tool_text(blob)
        if found:
            return found
        stripped = blob.strip()
        if _SELECT_HEAD.match(stripped):
            return stripped
    return None


def _fail_name(body: dict[str, Any] | None) -> str:
    if isinstance(body, dict):
        raw = str(body.get("insights_fail") or "").strip()
        if raw:
            return raw
    return "no_sql"


def _tool_error(sql: str, error: str) -> str:
    from dms_executor.sql_loop import mask_feedback_text

    detail = mask_feedback_text(error) or error
    return (
        "<tool_result>\n"
        "<tool_name>run_sql</tool_name>\n"
        f"<sql>{_xml(sql)}</sql>\n"
        f"<error>{_xml(detail)}</error>\n"
        "</tool_result>"
    )


def _context(
    question: str,
    *,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    space_id: str | None,
) -> tuple[str, str, str, list[str]]:
    """Schema, ontology, and verified examples. A lookup miss is an empty block."""
    from dms_core.ask_stream import note_fetch, note_stage

    schema = ""
    tables: list[str] = []
    note_stage("understand", "started")
    onto = _ontology(warehouse, space_id)
    note_stage("understand", "done")
    ontology = _ontology_text(onto)
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
            schema = prompt.strip()
        note_stage("find", "done")
        for name in tables:
            note_fetch(name)
    except Exception as exc:
        from dms_executor.lake_registry import SERVING_ERRORS

        note_stage("find", "failed")
        if isinstance(exc, SERVING_ERRORS):
            raise
    examples = ""
    try:
        from dms_executor.verified_queries import examples_text, retrieve_verified_examples

        rows = retrieve_verified_examples(
            question,
            space_id=space_id,
            warehouse=warehouse,
            grantable=grantable,
            dialect=dialect,
            force=True,
            seed=False,
        )
        examples = examples_text(rows).strip()
    except Exception as exc:
        from dms_executor.lake_registry import SERVING_ERRORS

        if isinstance(exc, SERVING_ERRORS):
            raise
    return schema, ontology, examples, tables


def _catalog(
    question: str,
    schema: str,
    ontology: str,
    examples: str,
    *,
    tool_result: str,
    escalate: bool,
    timeout: float | None,
) -> dict[str, Any]:
    # Static prefix, then the question. Anthropic prompt caching marks the
    # prefix ephemeral and leaves the question after the breakpoint. The
    # Insights body has no cache_control field on this pin, so the marker
    # stays on the catalog and is not posted.
    static = "\n".join(
        [
            _ROLE,
            "",
            "<schema>",
            _xml(schema),
            "</schema>",
            "",
            "<ontology>",
            _xml(ontology),
            "</ontology>",
            "",
            "<examples>",
            _xml(examples),
            "</examples>",
            "",
            "<tools>",
            _TOOL_SCHEMA,
            "</tools>",
        ]
    )
    asked = static + "\n\n<question>\n" + _xml(question.strip()) + "\n</question>"
    if tool_result:
        asked = f"{asked}\n\n{tool_result}"
    catalog: dict[str, Any] = {
        "sql_vote_prompt": asked,
        "sql_vote_kind": "escalate" if escalate else "direct",
        "floor_cache": {"type": "ephemeral"},
    }
    if escalate:
        catalog["route_hint"] = "escalate"
    if timeout is not None:
        catalog["floor_timeout_s"] = timeout
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
    stages_ms: dict[str, int] | None = None,
) -> dict[str, Any]:
    ms = int((time.perf_counter() - started) * 1000)
    env["ai_floor"] = {
        "ms": ms,
        "calls": calls,
        "executes": executes,
        "stage": stage,
        "checks": list(checks),
        "stages_ms": dict(stages_ms or {}),
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
    stages_ms: dict[str, int] | None = None,
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
        stages_ms=stages_ms,
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
    """The model calls run_sql. The harness executes, caps, governs, and judges."""
    from dms_core.ask_stream import note_stage

    from dms_executor.generative_ask import validate_compiled_sql
    from dms_executor.lake_registry import SERVING_ERRORS
    from dms_executor.manifest import OpenVaultTokenError
    from dms_executor.verified_queries import rows_from_submit_result

    started = time.perf_counter()
    spoken = dialect or SERVING_DIALECT
    calls = 0
    executes = 0
    stage = "generate"
    stages_ms: dict[str, int] = {}
    first_reason: str | None = None

    def _lap(name: str, began: float) -> None:
        ms = int((time.perf_counter() - began) * 1000)
        if name not in stages_ms:
            stages_ms[name] = ms
            return
        n = 2
        while f"{name}_{n}" in stages_ms:
            n += 1
        stages_ms[f"{name}_{n}"] = ms

    def _stop(reason: str) -> dict[str, Any]:
        return _abstain(
            question,
            reason,
            space_id=space_id,
            session_id=session_id,
            started=started,
            calls=calls,
            executes=executes,
            stage=stage,
            stages_ms=stages_ms,
        )

    tick = time.perf_counter()
    try:
        schema, ontology, examples, _tables = _context(
            question,
            warehouse=warehouse,
            grantable=set(grantable),
            dialect=spoken,
            space_id=space_id,
        )
    except SERVING_ERRORS as exc:
        code = str(getattr(exc, "code", "") or "serving_deadline_reserve")
        _lap("context", tick)
        return _stop(_public_reason(code))
    _lap("context", tick)

    def _note(reason: str) -> None:
        nonlocal first_reason
        if first_reason is None:
            first_reason = _public_reason(reason)

    tool_result = ""
    misses = 0
    for index in range(_MAX_CALLS):
        timeout = _generate_slice(index)
        if timeout == 0.0:
            _note("serving_deadline_reserve")
            break
        if misses >= 2:
            stage = "escalate"
        elif index == 0:
            stage = "generate"
        else:
            stage = "repair"
        calls += 1
        if stage == "escalate":
            note_stage("escalate", "started")
        note_stage("write", "started")
        catalog = _catalog(
            question,
            schema,
            ontology,
            examples,
            tool_result=tool_result,
            escalate=stage == "escalate",
            timeout=timeout,
        )
        tick = time.perf_counter()
        try:
            payload = compute(catalog)
        except SERVING_ERRORS as exc:
            _lap("generate", tick)
            code = str(getattr(exc, "code", "") or "serving_deadline_reserve")
            _note(code)
            break
        except OpenVaultTokenError:
            raise
        except Exception:
            payload = None
        _lap("generate", tick)
        body = payload if isinstance(payload, dict) else None
        sql = _tool_sql(body)
        if not sql:
            _note(_fail_name(body))
            misses += 1
            tool_result = _tool_error("", _fail_name(body))
            note_stage("write", "failed")
            continue
        note_stage("write", "done")
        tick = time.perf_counter()
        why = validate_compiled_sql(
            sql,
            grantable=set(grantable),
            warehouse=warehouse,
            dialect=spoken,
        )
        _lap("check", tick)
        if why:
            note_stage("check", "failed")
            if _direct(why):
                return _stop(why)
            _note(why)
            misses += 1
            tool_result = _tool_error(sql, why)
            continue
        note_stage("run", "started")
        tick = time.perf_counter()
        try:
            result = submit(sql)
        except SERVING_ERRORS as exc:
            _lap("run", tick)
            code = str(getattr(exc, "code", "") or "serving_deadline_reserve")
            _note(code)
            break
        except OpenVaultTokenError:
            raise
        except Exception:
            _lap("run", tick)
            _note("submit_failed")
            misses += 1
            tool_result = _tool_error(sql, "submit_failed")
            note_stage("run", "failed")
            continue
        executes += 1
        if getattr(result, "ok", None) is False:
            _lap("run", tick)
            _note("submit_failed")
            misses += 1
            tool_result = _tool_error(sql, "submit_failed")
            note_stage("run", "failed")
            continue
        note_stage("run", "done")
        rows = rows_from_submit_result(result)
        shape = _shape(rows)
        _lap("run", tick)
        if shape:
            _note(shape)
            misses += 1
            tool_result = _tool_error(sql, shape)
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
                stages_ms=stages_ms,
            )
        if isinstance(published, str):
            if _direct(published):
                return _stop(published)
            _note(published)
            misses += 1
            tool_result = _tool_error(sql, published)
            continue
    return _stop(first_reason or "no_sql")


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
