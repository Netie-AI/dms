"""Run model SQL on the extract, return the error, retry.

The read-only run is the extract engine (DuckDB). Conjunct comparison and
SQL normalisation use that dialect, supplied by the caller. This module
names no warehouse table or column.

Retry only on a database execution error or a checker flag. Hostile SQL,
multi-statement SQL, file reads, ungranted tables, and a missing extract
abstain at once and are not pasted into a retry prompt. A timeout, a
provider error, or a reply with no SQL does not compile.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from cortex_client.compute import (
    PLAN_ORIGIN_ONTOLOGY_RANKING,
    cloop_b_enabled,
    generate_model_called,
    insights_call_cap,
    insights_fail_reason,
    insights_timeout_s,
    note_model_call,
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
#: On the compute context only. The seam strips it before the request body.
ATTEMPT_TIMEOUT_KEY = "_attempt_timeout_s"
_DEADLINE_KEY = "_serving_deadline"
#: One share below this is not a model call. It stays with the compile.
_MIN_ATTEMPT_S = 0.05
#: Cooperative stop after the slice. A call that ignores it is discarded.
_CANCEL_GRACE_S = 0.25
_serving_deadline: ContextVar[float | None] = ContextVar(
    "dms_serving_deadline", default=None
)
_attempt_cancel: ContextVar[threading.Event | None] = ContextVar(
    "dms_attempt_cancel", default=None
)
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
    """Execute on a read-only extract connection. ``(rows, None)`` or ``(None, error)``."""
    import duckdb

    try:
        con = duckdb.connect(str(warehouse), read_only=True)
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
    model_calls: int = 1,
) -> dict[str, Any]:
    """One model attempt. ``outcome`` is never empty. Secrets are not stored.

    ``model_calls`` is 1 when this attempt spent one model call, and 0 when
    the attempt was discarded (the serving deadline cancelled it).
    """
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
        "model_calls": 1 if model_calls else 0,
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


def feedback_prompt(question: str, *, previous_sql: str | None, reason: str | None) -> str:
    """Prompt for one attempt. A retry appends the previous SQL and the error."""
    if not reason:
        return question
    safe_reason = mask_feedback_text(reason)
    safe_sql = mask_feedback_text(previous_sql or "")
    return f"{question}\n\nprevious_sql:\n{safe_sql}\n\nfeedback:\n{safe_reason}"


def _no_model_retry(why: str, extra: frozenset[str]) -> bool:
    """True when this checker reason must abstain without another model call."""
    if why in extra:
        return True
    head = why.split(":", 1)[0]
    return head in _NO_RETRY_HEADS


def _can_retry(*, retries: int, used: int, cap: int) -> bool:
    return retries < MAX_SQL_RETRIES and used < cap


def _explain_code(why: str) -> str | None:
    """``explain:BinderException`` with the engine sentence removed.

    None when this checker reason is not an explain failure. The sentence
    stays in the model prompt only.
    """
    if not why.startswith("explain:"):
        return None
    bits = why.split(":", 2)
    name = bits[1].strip() if len(bits) > 1 else ""
    if name.isidentifier():
        return f"explain:{name}"
    return "explain"


class CompileDefer(Exception):
    """Model SQL ran and failed its check. The caller may compile."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ServeHold(Exception):
    """Timeout, provider error, or empty reply. The caller must not compile."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def attempt_cancel_event() -> threading.Event | None:
    """Set when this attempt's slice is over. The call should stop."""
    return _attempt_cancel.get()


def begin_serving_budget(seconds: float | None = None) -> None:
    """Wall clock for one ask's model calls plus the compile reserve."""
    span = insights_timeout_s() if seconds is None else float(seconds)
    _serving_deadline.set(time.monotonic() + span)


def clear_serving_budget() -> None:
    _serving_deadline.set(None)


def attempt_slice(attempts_left: int) -> float | None:
    """Seconds for the next model call.

    The remaining deadline is split across the attempts still allowed and
    one reserve share for the compile. None means only that reserve is left.
    """
    deadline = _serving_deadline.get()
    if deadline is None or attempts_left < 1:
        return None
    remaining = deadline - time.monotonic()
    share = remaining / (attempts_left + 1)
    if share < _MIN_ATTEMPT_S:
        return None
    return share


def deadline_marker() -> dict[str, Any]:
    """A model call that did not return inside its slice."""
    return {
        "phase": "generate",
        "insights_fail": "insights_timeout:generate",
        _DEADLINE_KEY: True,
    }


def is_deadline(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get(_DEADLINE_KEY):
        return True
    reason = str(payload.get("insights_fail") or "")
    return reason.startswith("insights_timeout")


def bound_model_compute(
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """Give each model call one slice of the serving deadline.

    A call still running at the end of its slice is cancelled and not
    counted. The compile reserve is the share ``attempt_slice`` keeps back.

    ponytail: cancel is cooperative (``attempt_cancel_event``). A call that
    ignores it is discarded after a short grace and still not counted.
    Upgrade: cancel the HTTP call, then join.
    """
    begin_serving_budget()
    calls = {"n": 0}

    def wrapped(ctx: dict[str, Any]) -> dict[str, Any] | None:
        left = insights_call_cap() - calls["n"]
        slice_s = attempt_slice(left)
        if slice_s is None or left < 1:
            return deadline_marker()
        body = dict(ctx)
        body[ATTEMPT_TIMEOUT_KEY] = slice_s
        got = _invoke_bounded(compute, body, slice_s)
        if not is_deadline(got):
            calls["n"] += 1
        return got

    return wrapped


def _invoke_bounded(
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    ctx: dict[str, Any],
    slice_s: float,
) -> dict[str, Any] | None:
    box: dict[str, Any] = {}
    cancel = threading.Event()

    def run() -> None:
        _attempt_cancel.set(cancel)
        try:
            box["v"] = compute(ctx)
        except Exception as exc:  # noqa: BLE001 — same miss as a raised compute
            box["e"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(slice_s)
    if thread.is_alive():
        # Discard this future. It does not take a call-cap slot.
        cancel.set()
        thread.join(_CANCEL_GRACE_S)
        return deadline_marker()
    got = box.get("v")
    if isinstance(got, dict) and is_deadline(got):
        return got
    # One finished attempt is one model call on the ask's own counter.
    note_model_call()
    if "e" in box or not isinstance(got, dict):
        return None
    return got


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
) -> dict[str, Any]:
    """Checker, read-only run, feedback retry.

    An empty result does not retry. It abstains until VALUE-EXISTS-01
    switches ``empty_result_reason``. Hostile, multi-statement, file-read,
    and ungranted SQL abstain on the first call.
    """
    cap = insights_call_cap()
    retries = 0
    # One execute or explain failure may ask the model again. The next one compiles.
    error_fed = 0
    used = recorded_model_calls()
    if used <= 0:
        used = 1
    current = payload if isinstance(payload, dict) else None
    reason: str | None = None
    prev_sql: str | None = None
    protected_sql: str | None = None

    while True:
        prompt = feedback_prompt(question, previous_sql=prev_sql, reason=reason)
        sql = model_sql(current)
        if not sql:
            # No statement ran, so this is not a failed check. Do not compile.
            named = (
                insights_fail_reason(current) if isinstance(current, dict) else None
            )
            hold = "provider_error" if current is None or named else "empty_reply"
            attempts.append(
                loop_entry(
                    prompt=prompt,
                    payload=current,
                    sql=None,
                    outcome=hold,
                    dialect=dialect,
                )
            )
            raise ServeHold(hold)

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
                    raise CompileDefer(f"loop_exhausted:{flag}")
                retries += 1
                used += 1
                prev_sql = sql
                reason = flag
                current = _call(
                    compute, ctx, prompt, reason, sql, attempts=attempts, dialect=dialect
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
                    raise CompileDefer("loop_exhausted:filter_dropped")
                retries += 1
                used += 1
                prev_sql = sql
                reason = "filter_dropped"
                current = _call(
                    compute, ctx, prompt, reason, sql, attempts=attempts, dialect=dialect
                )
                continue

        why = check(sql)
        if why:
            explain_code = _explain_code(why)
            if explain_code:
                # The engine sentence is for the model prompt, not the envelope.
                outcome = f"checker:{explain_code}"
                model_reason = f"checker:{why}"
            else:
                safe_why = mask_feedback_text(why)
                outcome = f"checker:{safe_why}"
                model_reason = outcome
            attempts.append(
                loop_entry(
                    prompt=prompt,
                    payload=current,
                    sql=sql,
                    outcome=outcome,
                    dialect=dialect,
                )
            )
            if why in no_retry_reasons or _no_model_retry(why, no_retry_reasons):
                head = why if why in no_retry_reasons else outcome
                return abstain(head, attempts, sql=sql, retries=retries)
            fed_done = explain_code is not None and error_fed >= 1
            if fed_done or not _can_retry(retries=retries, used=used, cap=cap):
                raise CompileDefer(f"loop_exhausted:{outcome}")
            if explain_code is not None:
                error_fed += 1
            protected_sql = sql
            retries += 1
            used += 1
            prev_sql = sql
            reason = outcome
            current = _call(
                compute,
                ctx,
                prompt,
                reason,
                sql,
                attempts=attempts,
                dialect=dialect,
                model_reason=model_reason,
                model_prompt=feedback_prompt(
                    question, previous_sql=sql, reason=model_reason
                ),
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
            # Raw engine text goes to the model prompt only.
            safe_err = mask_feedback_text(exec_err)
            outcome = "db_error"
            model_reason = f"db_error:{safe_err}" if safe_err else outcome
            attempts.append(
                loop_entry(
                    prompt=prompt, payload=current, sql=sql, outcome=outcome, dialect=dialect
                )
            )
            protected_sql = sql
            if error_fed >= 1 or not _can_retry(retries=retries, used=used, cap=cap):
                raise CompileDefer(f"loop_exhausted:{outcome}")
            error_fed += 1
            retries += 1
            used += 1
            prev_sql = sql
            reason = outcome
            current = _call(
                compute,
                ctx,
                prompt,
                reason,
                sql,
                attempts=attempts,
                dialect=dialect,
                model_reason=model_reason,
                model_prompt=feedback_prompt(
                    question, previous_sql=sql, reason=model_reason
                ),
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
    attempts: list[dict[str, Any]] | None = None,
    dialect: str = "",
    model_reason: str | None = None,
    model_prompt: str | None = None,
) -> dict[str, Any] | None:
    # ``model_prompt`` is what the model reads. The stored attempt keeps ``prompt``.
    fed_reason = model_reason if model_reason is not None else reason
    fed_prompt = model_prompt if model_prompt is not None else prompt
    feedback = {
        "previous_sql": mask_feedback_text(previous_sql or ""),
        "reason": mask_feedback_text(fed_reason),
        "prompt": fed_prompt,
    }
    nxt = dict(ctx)
    nxt["sql_loop_feedback"] = feedback
    try:
        got = compute(nxt)
    except (CompileDefer, ServeHold):
        raise
    except Exception:
        raise ServeHold("provider_error") from None
    if is_deadline(got if isinstance(got, dict) else None):
        if attempts is not None:
            attempts.append(
                loop_entry(
                    prompt=prompt,
                    payload=got if isinstance(got, dict) else None,
                    sql=previous_sql,
                    outcome="insights_timeout:generate",
                    dialect=dialect,
                    model_calls=0,
                )
            )
        raise ServeHold("insights_timeout:generate")
    if not isinstance(got, dict):
        raise ServeHold("provider_error")
    return got


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


def _drop_model_credit_when_compile_served(env: dict[str, Any]) -> None:
    """The compile step produced the SQL. Do not name the model as the producer.

    ``plan_origin`` already names that step. Credit fields copied from the
    model payload are removed. Absent keys stay absent.
    """
    if env.get("abstained"):
        return
    if env.get("plan_origin") != PLAN_ORIGIN_ONTOLOGY_RANKING:
        return
    had_model = bool(env.get("served_model") or env.get("served_provider"))
    env.pop("served_model", None)
    env.pop("served_provider", None)
    env.pop("ov_key_id", None)
    if env.get("served_attribution") == "reported":
        env["served_attribution"] = "missing" if had_model else "none"


def apply_sql_credit(
    env: dict[str, Any],
    payload: dict[str, Any] | None,
    loop: list[dict[str, Any]] | None,
    *,
    dialect: str,
) -> None:
    """Name the model only when the served SQL is one a model attempt wrote.

    With ``DMS_CLOOP_B`` off, drop ``ov_key_id`` and, when the ranking compile
    produced the SQL, the model credit. The rewrite of other answers runs
    only when the flag is on.
    """
    if not cloop_b_enabled():
        env.pop("ov_key_id", None)
        _drop_model_credit_when_compile_served(env)
        return
    _apply_sql_credit(env, payload, loop, dialect=dialect)
    _drop_model_credit_when_compile_served(env)


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
