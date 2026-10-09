"""Run model SQL on the extract, return the error, retry.

The read-only run is the extract engine (DuckDB). Conjunct comparison and
SQL normalisation use that dialect, supplied by the caller. This module
names no warehouse table or column.

Retry only on a database execution error or a checker flag. Hostile SQL,
multi-statement SQL, file reads, ungranted tables, a missing extract, and
a reply with no SQL abstain at once and are not pasted into a retry prompt.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from cortex_client.compute import (
    cloop_b_enabled,
    generate_model_called,
    insights_call_cap,
    recorded_model_calls,
)

from dms_executor.sql_currency import dropped_conjuncts, sql_byte_equal

#: sqlglot dialect for a connector kind. The extract engine is separate.
_CONNECTOR_DIALECT: dict[str, str] = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "sqlserver": "tsql",
    "mssql": "tsql",
    "tsql": "tsql",
    "duckdb": "duckdb",
}
#: Read-only extract engine. SQL that runs here is compared in this dialect.
EXTRACT_DIALECT = "duckdb"
MAX_SQL_RETRIES = 2
EMPTY_NOTE = "no rows match"
#: VALUE-EXISTS-01 later returns None from ``empty_result_reason`` so the
#: caller can serve this empty answer. Until then the reason is an abstain.
EMPTY_UNVERIFIED = "empty_result_unverified:value_exists_pending"
_NO_RETRY_HEADS = frozenset(
    {"hostile_sql", "ungranted", "multi_statement", "warehouse_missing"}
)
# Key-name segments after splitting on non-alphanumerics. "token" is a
# segment, so "prompt_tokens" is not a secret key.
_SECRET_SEGMENTS = frozenset(
    {
        "secret",
        "password",
        "apikey",
        "authorization",
        "accesstoken",
        "bearer",
        "token",
        "cookie",
        "xapikey",
    }
)
_PII_KEYS = frozenset({"name", "dob", "passport", "contact"})
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_ASSIGNED_KEY = re.compile(r"(?i)\b(?:api[_-]?key|key|token|secret)\s*=\s*\S+")
_SK = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def dialect_for_connector(kind: str | None) -> str:
    """sqlglot dialect name for a connector kind. Unknown kinds use the extract."""
    key = str(kind or "").strip().lower()
    return _CONNECTOR_DIALECT.get(key, EXTRACT_DIALECT)


def extract_dialect(warehouse: Path | None) -> str:
    """Dialect of the engine that executes model SQL.

    The extract is DuckDB even when a source connector was TSQL or another
    warehouse. Comparison uses this dialect, not the source connector.
    ``warehouse`` is unused; the engine does not change per file.
    """
    del warehouse
    return EXTRACT_DIALECT


def empty_result_reason(sql: str) -> str | None:
    """Decision for a checker-clean empty result. Not a retry.

    Returns a pipeline-failure reason. The caller abstains, keeps ``sql`` on
    the stored attempt, and does not serve the rows.

    VALUE-EXISTS-01 integration: when every filter literal is known to exist
    in its column, return None. The caller then serves the empty answer
    (rows [], note ``no rows match``) instead of abstaining. This function
    is the only switch. It does not read a Cortex confirmation field.
    """
    if not isinstance(sql, str):
        return EMPTY_UNVERIFIED
    return EMPTY_UNVERIFIED


def run_readonly(
    sql: str, warehouse: Path
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Execute on the extract. ``(rows, None)`` or ``(None, error)``.

    Uses the serving attach, not a second ``read_only`` connect. DuckDB 1.5
    rejects mixed access modes on one file, which dropped the schema index
    and this execution together. The file lock serializes callers.
    """
    from dms_executor.demo_warehouse import connect_file

    try:
        con = connect_file(Path(warehouse))
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        cur = con.execute(sql)
        desc = cur.description or []
        cols = [d[0] for d in desc]
        rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        return rows, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        con.close()


def _norm_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _key_segments(name: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", name.lower()) if part]


def _secret_key(name: str) -> bool:
    parts = _key_segments(name)
    joined = "".join(parts)
    if joined in _SECRET_SEGMENTS or any(part in _SECRET_SEGMENTS for part in parts):
        return True
    return "apikey" in joined or "accesstoken" in joined


def _high_entropy(token: str) -> bool:
    if len(token) < 24 or any(ch.isspace() for ch in token):
        return False
    kinds = (
        any(ch.islower() for ch in token),
        any(ch.isupper() for ch in token),
        any(ch.isdigit() for ch in token),
    )
    return sum(kinds) >= 2


def _scrub_string(text: str) -> str:
    text = _BEARER.sub("[redacted]", text)
    text = _ASSIGNED_KEY.sub("[redacted]", text)
    text = _SK.sub("[redacted]", text)

    def _repl(match: re.Match[str]) -> str:
        token = match.group(0)
        return "[redacted]" if _high_entropy(token) else token

    return _LONG_TOKEN.sub(_repl, text)


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return {
            str(key): _scrub(item)
            for key, item in value.items()
            if not _secret_key(str(key))
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _drop_named_pii(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _drop_named_pii(item)
            for key, item in value.items()
            if _norm_key(str(key)) not in _PII_KEYS
        }
    if isinstance(value, list):
        return [_drop_named_pii(item) for item in value]
    return value


def _pii_kept(original: Any, masked: Any) -> Any:
    """Drop PII-shaped fields the masker left unchanged."""
    if isinstance(original, dict):
        masked_d = masked if isinstance(masked, dict) else {}
        out: dict[str, Any] = {}
        for key, item in original.items():
            name = str(key)
            if _norm_key(name) in _PII_KEYS:
                got = masked_d.get(name, item)
                if got == item or not (isinstance(got, str) and got.startswith("DMSMASK_")):
                    continue
                out[name] = got
                continue
            out[name] = _pii_kept(item, masked_d.get(name))
        return out
    if isinstance(original, list):
        masked_l = masked if isinstance(masked, list) else []
        return [
            _pii_kept(item, masked_l[idx] if idx < len(masked_l) else None)
            for idx, item in enumerate(original)
        ]
    return masked


def mask_feedback_text(text: str) -> str:
    """Mask row values and key material before a prompt, attempt, or envelope.

    A masker failure stores nothing from the original text.
    """
    scrubbed = _scrub_string(text or "")
    try:
        from dms_core.pii import fail_closed_mask_payload

        got = fail_closed_mask_payload(text=scrubbed)
    except Exception:
        return ""
    if not isinstance(got, dict):
        return ""
    return str(got.get("text") or "")


def _mask_stored(value: Any) -> Any:
    """Run one stored value through the PII masker. Fail closed on the PII fields."""
    scrubbed = _scrub(value)
    try:
        from dms_core.pii import fail_closed_mask_payload

        if isinstance(scrubbed, dict):
            got = fail_closed_mask_payload(rows=[scrubbed])
            rows = got.get("rows") if isinstance(got, dict) else None
            masked = rows[0] if isinstance(rows, list) and rows else {}
            return _pii_kept(scrubbed, masked if isinstance(masked, dict) else {})
        if isinstance(scrubbed, str):
            got = fail_closed_mask_payload(text=scrubbed)
            return str(got.get("text") or "") if isinstance(got, dict) else ""
        if isinstance(scrubbed, list):
            return [_mask_stored(item) for item in scrubbed]
    except Exception:
        return _drop_named_pii(scrubbed)
    return scrubbed


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _key_id(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for name in ("ov_key_id", "key_id", "route_key_id"):
        got = _text(payload.get(name))
        if got:
            return got
    return None


def _tokens(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("tokens")
    if not isinstance(raw, dict):
        raw = payload.get("usage")
    if not isinstance(raw, dict):
        flat: dict[str, Any] = {}
        if "prompt_tokens" in payload:
            flat["prompt_tokens"] = payload.get("prompt_tokens")
        if "completion_tokens" in payload:
            flat["completion_tokens"] = payload.get("completion_tokens")
        raw = flat or None
    if not isinstance(raw, dict):
        return None
    return {str(key): value for key, value in raw.items() if not _secret_key(str(key))}


def _raw_reply(payload: Mapping[str, Any] | None) -> Any:
    if not isinstance(payload, Mapping):
        return None
    if "raw_reply" in payload:
        return payload.get("raw_reply")
    return dict(payload)


def _route_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Key route only from Cortex's ``ov_route``. A learning-store id stays itself."""
    store = _text(payload.get("route_store_id")) if isinstance(payload, Mapping) else None
    route_id = _text(payload.get("route_id")) if isinstance(payload, Mapping) else None
    ov_route = _text(payload.get("ov_route")) if isinstance(payload, Mapping) else None
    out: dict[str, Any] = {
        "ov_route": ov_route,
        "ov_route_reason": None if ov_route else "unattributed",
    }
    if store is not None:
        out["route_store_id"] = store
    if route_id is not None:
        out["route_id"] = route_id
    return out


def loop_entry(
    *,
    prompt: str,
    payload: Mapping[str, Any] | None,
    sql: str | None,
    outcome: str,
    dialect: str,
) -> dict[str, Any]:
    """One model attempt. ``outcome`` is never empty. Secrets are not stored."""
    why = str(outcome or "").strip() or "no_outcome"
    model = _text(payload.get("served_model") if isinstance(payload, Mapping) else None)
    if model is None and isinstance(payload, Mapping):
        model = _text(payload.get("model"))
    provider = _text(payload.get("served_provider") if isinstance(payload, Mapping) else None)
    entry: dict[str, Any] = {
        "prompt": prompt,
        "raw_reply": _raw_reply(payload),
        "model": model,
        "provider": provider,
        "key": _key_id(payload),
        "tokens": _tokens(payload),
        "sql": sql,
        "outcome": why,
        "model_wrote": bool(sql),
        "dialect": dialect,
    }
    entry.update(_route_fields(payload))
    masked_prompt = _mask_stored(entry.get("prompt"))
    entry["prompt"] = masked_prompt if isinstance(masked_prompt, str) else ""
    entry["raw_reply"] = _mask_stored(entry.get("raw_reply"))
    if isinstance(entry.get("sql"), str):
        entry["sql"] = mask_feedback_text(entry["sql"])
    return entry


def feedback_prompt(
    question: str,
    *,
    previous_sql: str | None,
    reason: str | None,
    plan: str | None = None,
) -> str:
    """Prompt for one attempt. A retry appends the previous SQL and the error."""
    base = question
    if plan:
        base = f"{question}\n\nplan:\n{plan}"
    if not reason:
        return base
    safe_reason = mask_feedback_text(reason)
    safe_sql = mask_feedback_text(previous_sql or "")
    return f"{base}\n\nprevious_sql:\n{safe_sql}\n\nfeedback:\n{safe_reason}"


def _no_model_retry(why: str, extra: frozenset[str]) -> bool:
    """True when this checker reason must abstain without another model call."""
    if why in extra:
        return True
    head = why.split(":", 1)[0]
    return head in _NO_RETRY_HEADS


def _can_retry(*, retries: int, used: int, cap: int) -> bool:
    return retries < MAX_SQL_RETRIES and used < cap


def _served_outcome(env: dict[str, Any]) -> str:
    """Log ``served`` only when the envelope is not an abstain."""
    if not env.get("abstained"):
        return "served"
    prefix = "GEN-01: "
    for item in env.get("assumptions") or []:
        text = str(item)
        if text.startswith(prefix):
            reason = text[len(prefix):].strip()
            if reason and reason != "served":
                return reason
    return "abstain"


def _finish_attempt(
    env: dict[str, Any],
    *,
    attempts: list[dict[str, Any]],
    prompt: str,
    payload: dict[str, Any] | None,
    sql: str,
    dialect: str,
) -> dict[str, Any]:
    attempts.append(
        loop_entry(
            prompt=prompt,
            payload=payload,
            sql=sql,
            outcome=_served_outcome(env),
            dialect=dialect,
        )
    )
    return env


def _model_called(payload: dict[str, Any] | None, loop: list[dict[str, Any]] | None) -> bool:
    if generate_model_called(payload):
        return True
    for attempt in loop or []:
        raw = attempt.get("raw_reply")
        if isinstance(raw, dict) and generate_model_called(raw):
            return True
        if attempt.get("model") or attempt.get("provider") or attempt.get("key"):
            return True
    return False


def run_model_loop(
    *,
    question: str,
    ctx: dict[str, Any],
    payload: dict[str, Any] | None,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    warehouse: Path | None,
    dialect: str,
    model_sql: Callable[[dict[str, Any] | None], str | None],
    check: Callable[[str], str | None],
    submit_sql: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    abstain: Callable[..., dict[str, Any]],
    empty_answer: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    attempts: list[dict[str, Any]],
    no_retry_reasons: frozenset[str] = frozenset(),
    calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Checker, read-only run, feedback retry.

    An empty result does not retry. It abstains until VALUE-EXISTS-01
    switches ``empty_result_reason``. Hostile, multi-statement, file-read,
    and ungranted SQL abstain on the first call.
    """
    cap = insights_call_cap()
    retries = 0
    used = recorded_model_calls()
    if used <= 0:
        used = 1
    current = payload if isinstance(payload, dict) else None
    reason: str | None = None
    prev_sql: str | None = None
    protected_sql: str | None = None
    escalated = False
    raw_plan = ctx.get("sql_plan")
    plan: str = raw_plan if isinstance(raw_plan, str) else ""

    def _escalate(outcome: str, sql: str | None) -> bool:
        nonlocal escalated, retries, used, prev_sql, reason, current
        if escalated:
            return False
        escalated = True
        retries += 1
        used += 1
        prev_sql = sql
        reason = outcome
        escalated_prompt = feedback_prompt(
            question, previous_sql=sql, reason=outcome, plan=plan or None
        )
        current = _call(
            compute,
            ctx,
            escalated_prompt,
            reason,
            sql,
            step="escalate",
            trace=calls,
            plan=plan,
        )
        return True

    while True:
        prompt = feedback_prompt(
            question, previous_sql=prev_sql, reason=reason, plan=plan or None
        )
        sql = model_sql(current)
        if not sql:
            attempts.append(
                loop_entry(
                    prompt=prompt,
                    payload=current,
                    sql=None,
                    outcome="no_sql",
                    dialect=dialect,
                )
            )
            if _escalate("no_sql", None):
                continue
            return abstain("no_sql", attempts, sql=None, retries=retries)

        if protected_sql:
            dropped = dropped_conjuncts(protected_sql, sql, dialect)
            if dropped is None:
                flag = "checker:filter_parse_failed"
                attempts.append(
                    loop_entry(
                        prompt=prompt,
                        payload=current,
                        sql=sql,
                        outcome=flag,
                        dialect=dialect,
                    )
                )
                if not _can_retry(retries=retries, used=used, cap=cap):
                    head = f"loop_exhausted:{flag}"
                    if _escalate(head, sql):
                        continue
                    return abstain(head, attempts, sql=sql, retries=retries)
                retries += 1
                used += 1
                prev_sql = sql
                reason = flag
                current = _call(
                    compute, ctx, prompt, reason, sql, step="retry", trace=calls, plan=plan
                )
                continue
            if dropped:
                attempts.append(
                    loop_entry(
                        prompt=prompt,
                        payload=current,
                        sql=sql,
                        outcome="filter_dropped",
                        dialect=dialect,
                    )
                )
                if not _can_retry(retries=retries, used=used, cap=cap):
                    head = "loop_exhausted:filter_dropped"
                    if _escalate(head, sql):
                        continue
                    return abstain(head, attempts, sql=sql, retries=retries)
                retries += 1
                used += 1
                prev_sql = sql
                reason = "filter_dropped"
                current = _call(
                    compute, ctx, prompt, reason, sql, step="retry", trace=calls, plan=plan
                )
                continue

        why = check(sql)
        if why:
            safe_why = mask_feedback_text(why)
            outcome = f"checker:{safe_why}"
            attempts.append(
                loop_entry(
                    prompt=prompt,
                    payload=current,
                    sql=sql,
                    outcome=outcome,
                    dialect=dialect,
                )
            )
            if why in no_retry_reasons:
                return abstain(why, attempts, sql=sql, retries=retries)
            if _no_model_retry(why, no_retry_reasons) or not _can_retry(
                retries=retries, used=used, cap=cap
            ):
                head = (
                    outcome
                    if _no_model_retry(why, no_retry_reasons)
                    else f"loop_exhausted:{outcome}"
                )
                if not _no_model_retry(why, no_retry_reasons) and _escalate(head, sql):
                    continue
                return abstain(head, attempts, sql=sql, retries=retries)
            protected_sql = sql
            retries += 1
            used += 1
            prev_sql = sql
            reason = outcome
            current = _call(
                compute, ctx, prompt, reason, sql, step="retry", trace=calls, plan=plan
            )
            continue

        if warehouse is None or not Path(warehouse).is_file():
            outcome = "checker:warehouse_missing"
            attempts.append(
                loop_entry(
                    prompt=prompt, payload=current, sql=sql, outcome=outcome, dialect=dialect
                )
            )
            return abstain(outcome, attempts, sql=sql, retries=retries)

        rows, exec_err = run_readonly(sql, Path(warehouse))
        if exec_err:
            safe_err = mask_feedback_text(exec_err)
            outcome = f"db_error:{safe_err}"
            attempts.append(
                loop_entry(
                    prompt=prompt, payload=current, sql=sql, outcome=outcome, dialect=dialect
                )
            )
            protected_sql = sql
            if not _can_retry(retries=retries, used=used, cap=cap):
                head = f"loop_exhausted:{outcome}"
                if _escalate(head, sql):
                    continue
                return abstain(head, attempts, sql=sql, retries=retries)
            retries += 1
            used += 1
            prev_sql = sql
            reason = outcome
            current = _call(
                compute, ctx, prompt, reason, sql, step="retry", trace=calls, plan=plan
            )
            continue

        got = rows or []
        if not got:
            decision = empty_result_reason(sql)
            if decision:
                attempts.append(
                    loop_entry(
                        prompt=prompt,
                        payload=current,
                        sql=sql,
                        outcome=decision,
                        dialect=dialect,
                    )
                )
                return abstain(decision, attempts, sql=sql, retries=retries)
            return _finish_attempt(
                empty_answer(sql, attempts),
                attempts=attempts,
                prompt=prompt,
                payload=current,
                sql=sql,
                dialect=dialect,
            )
        env = _finish_attempt(
            submit_sql(sql, attempts),
            attempts=attempts,
            prompt=prompt,
            payload=current,
            sql=sql,
            dialect=dialect,
        )
        return env


def _call(
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    ctx: dict[str, Any],
    prompt: str,
    reason: str,
    previous_sql: str | None,
    *,
    step: str,
    trace: list[dict[str, Any]] | None,
    plan: str,
) -> dict[str, Any] | None:
    from dms_executor.ai_ladder import higher_tier, reported_call

    feedback = {
        "previous_sql": mask_feedback_text(previous_sql or ""),
        "reason": mask_feedback_text(reason),
        "prompt": prompt,
    }
    nxt = dict(ctx)
    nxt["sql_loop_feedback"] = feedback
    nxt["sql_prompt"] = prompt
    nxt["ladder_step"] = step
    nxt.pop("ov_tier", None)
    if step == "escalate":
        nxt["ov_tier"] = higher_tier()
    try:
        got = compute(nxt)
    except Exception:
        got = None
    body = got if isinstance(got, dict) else None
    if trace is not None:
        trace.append(reported_call(step, body))
    return body


def _clear_credit(env: dict[str, Any]) -> None:
    """No model call: drop credit fields so none does not name a model.

    Absent is null for readers. Writing the keys, even as null, changes
    flag-off bytes versus f9ffc3e (those envelopes omitted them) and
    ``ov_key_id`` puts ``ov_`` on the envelope.
    """
    env.pop("served_model", None)
    env.pop("served_provider", None)
    env.pop("ov_key_id", None)


def _stamp_reported(
    env: dict[str, Any], *, model: str, provider: str | None, key: str | None
) -> None:
    env["served_attribution"] = "reported"
    env["served_model"] = model
    if provider:
        env["served_provider"] = provider
    # Main never sends this key. Only the extract loop may add it.
    if key and cloop_b_enabled():
        env["ov_key_id"] = key


def apply_sql_credit(
    env: dict[str, Any],
    payload: dict[str, Any] | None,
    loop: list[dict[str, Any]] | None,
    *,
    dialect: str,
) -> None:
    """Name the model only when the served SQL is one a model attempt wrote.

    With ``DMS_CLOOP_B`` off, this is a no-op besides dropping ``ov_key_id``.
    Main never sends that key, and ``with_served_attribution`` has already
    set ``served_attribution``. The rewrite runs only when the flag is on.
    """
    if not cloop_b_enabled():
        env.pop("ov_key_id", None)
        return
    _apply_sql_credit(env, payload, loop, dialect=dialect)


def _apply_sql_credit(
    env: dict[str, Any],
    payload: dict[str, Any] | None,
    loop: list[dict[str, Any]] | None,
    *,
    dialect: str,
) -> None:
    """Name the model only when the served SQL is one a model attempt wrote.

    Every path sets ``served_attribution``. ``reported`` is a loop attempt,
    or the payload SQL when the loop did not write a different statement,
    and only with a model plus a key or provider. ``missing`` means a model
    call happened and nothing it wrote was served. ``none`` means no model
    call; the credit fields are null.
    """
    served = env.get("sql_used")
    served_s = served if isinstance(served, str) else None
    called = _model_called(payload if isinstance(payload, dict) else None, loop)
    match: dict[str, Any] | None = None
    loop_wrote_other = False
    for attempt in loop or []:
        if not attempt.get("model_wrote"):
            continue
        attempt_dialect = _text(attempt.get("dialect")) or dialect
        same = sql_byte_equal(served_s, _text(attempt.get("sql")), attempt_dialect)
        if same and match is None:
            match = attempt
        elif not same:
            loop_wrote_other = True
    if match is not None:
        model = _text(match.get("model"))
        provider = _text(match.get("provider"))
        key = _text(match.get("key"))
        if model and (key or provider):
            _stamp_reported(env, model=model, provider=provider, key=key)
            return
        env["served_attribution"] = "missing"
        return
    if isinstance(payload, dict) and not loop_wrote_other:
        from cortex_client.compute import insights_query_sql

        raw = _text(payload.get("query_sql")) or insights_query_sql(payload)
        if raw and sql_byte_equal(served_s, raw, dialect):
            model = _text(payload.get("served_model"))
            provider = _text(payload.get("served_provider"))
            key = _key_id(payload)
            if model and (key or provider):
                _stamp_reported(env, model=model, provider=provider, key=key)
                return
            env["served_attribution"] = "missing" if called else "none"
            if env["served_attribution"] == "none":
                _clear_credit(env)
            return
    if called:
        env["served_attribution"] = "missing"
        return
    env["served_attribution"] = "none"
    _clear_credit(env)
