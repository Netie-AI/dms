"""Run model SQL on the extract, return the error, retry.

The read-only run is the extract engine. Conjunct comparison and SQL
normalisation use the connector's sqlglot dialect, supplied by the caller.
This module names no warehouse table or column.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from cortex_client.compute import insights_call_cap, recorded_model_calls

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
#: Read-only extract engine. Used when the registry has no single connector.
EXTRACT_DIALECT = "duckdb"
MAX_SQL_RETRIES = 2
EMPTY_NOTE = "no rows match"
_SECRET_PARTS = ("secret", "password", "api_key", "authorization", "access_token")


def dialect_for_connector(kind: str | None) -> str:
    """sqlglot dialect name for a connector kind. Unknown kinds use the extract."""
    key = str(kind or "").strip().lower()
    return _CONNECTOR_DIALECT.get(key, EXTRACT_DIALECT)


def _scheme(filename: str) -> str | None:
    if "://" not in filename:
        return None
    head = filename.split("://", 1)[0].strip().lower()
    return head or None


def extract_dialect(warehouse: Path | None) -> str:
    """Dialect for SQL the model wrote against this extract.

    A SQL-pull filename is ``kind://host:port/db``. One shared connector kind
    selects that sqlglot dialect. A file extract, a missing registry, or mixed
    kinds use the extract engine.
    """
    if warehouse is None or not Path(warehouse).is_file():
        return EXTRACT_DIALECT
    import duckdb

    try:
        con = duckdb.connect(str(warehouse), read_only=True)
    except Exception:
        return EXTRACT_DIALECT
    try:
        rows = con.execute(
            "SELECT filename FROM bronze._ingest_registry"
        ).fetchall()
    except Exception:
        return EXTRACT_DIALECT
    finally:
        con.close()
    kinds = {
        scheme
        for (filename,) in rows
        if isinstance(filename, str)
        for scheme in [_scheme(filename)]
        if scheme
    }
    if len(kinds) == 1:
        return dialect_for_connector(next(iter(kinds)))
    return EXTRACT_DIALECT


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


def _secret_key(name: str) -> bool:
    low = name.lower()
    return any(part in low for part in _SECRET_PARTS)


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _strip(v)
            for k, v in value.items()
            if not _secret_key(str(k))
        }
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _key_id(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for name in ("ov_key_id", "key_id", "route_key_id", "ov_route"):
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
        return None
    return {str(k): v for k, v in raw.items() if not _secret_key(str(k))}


def _raw_reply(payload: Mapping[str, Any] | None) -> Any:
    if not isinstance(payload, Mapping):
        return None
    if "raw_reply" in payload:
        return _strip(payload.get("raw_reply"))
    keep: dict[str, Any] = {}
    for name in (
        "query_sql",
        "sql",
        "phase",
        "status",
        "generative",
        "served_model",
        "served_provider",
        "ov_key_id",
        "ov_route",
        "tokens",
        "usage",
    ):
        if name in payload:
            keep[name] = payload[name]
    return _strip(keep)


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
    return {
        "prompt": prompt,
        "raw_reply": _raw_reply(payload),
        "ov_route": _text(payload.get("ov_route") if isinstance(payload, Mapping) else None)
        or _text(payload.get("route_id") if isinstance(payload, Mapping) else None)
        or _text(payload.get("route_store_id") if isinstance(payload, Mapping) else None),
        "model": model,
        "provider": provider,
        "key": _key_id(payload),
        "tokens": _tokens(payload),
        "sql": sql,
        "outcome": why,
        "model_wrote": bool(sql),
        "dialect": dialect,
    }


def feedback_prompt(question: str, *, previous_sql: str | None, reason: str | None) -> str:
    """Prompt for one attempt. A retry appends the previous SQL and the error."""
    if not reason:
        return question
    return (
        f"{question}\n\nprevious_sql:\n{previous_sql or ''}\n\nfeedback:\n{reason}"
    )


def confirms_empty(payload: Mapping[str, Any] | None) -> bool:
    """Structured confirmation on the model reply. Not a phrase match."""
    if not isinstance(payload, Mapping):
        return False
    if payload.get("empty_confirmed") is True:
        return True
    gen = payload.get("generative")
    return isinstance(gen, Mapping) and gen.get("empty_confirmed") is True


def _can_retry(*, retries: int, used: int, cap: int) -> bool:
    return retries < MAX_SQL_RETRIES and used < cap


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
    abstain: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    empty_answer: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    attempts: list[dict[str, Any]],
    no_retry_reasons: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Checker, read-only run, feedback retry. Empty rows do not retry.

    ``attempts`` is filled in place, one entry per model call, each with a
    non-empty outcome.
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

    while True:
        prompt = feedback_prompt(question, previous_sql=prev_sql, reason=reason)
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
            if not _can_retry(retries=retries, used=used, cap=cap):
                return abstain("loop_exhausted:no_sql", attempts)
            retries += 1
            used += 1
            prev_sql = None
            reason = "no_sql"
            current = _call(compute, ctx, prompt, reason, None)
            continue

        if protected_sql:
            dropped = dropped_conjuncts(protected_sql, sql, dialect)
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
                    return abstain("loop_exhausted:filter_dropped", attempts)
                retries += 1
                used += 1
                prev_sql = sql
                reason = "filter_dropped"
                current = _call(compute, ctx, prompt, reason, sql)
                continue

        why = check(sql)
        if why:
            outcome = f"checker:{why}"
            attempts.append(
                loop_entry(
                    prompt=prompt,
                    payload=current,
                    sql=sql,
                    outcome=outcome,
                    dialect=dialect,
                )
            )
            protected_sql = sql
            if why in no_retry_reasons or not _can_retry(retries=retries, used=used, cap=cap):
                head = why if why in no_retry_reasons else f"loop_exhausted:{outcome}"
                return abstain(head, attempts)
            retries += 1
            used += 1
            prev_sql = sql
            reason = outcome
            current = _call(compute, ctx, prompt, reason, sql)
            continue

        if warehouse is None or not Path(warehouse).is_file():
            err = "warehouse_missing"
            outcome = f"db_error:{err}"
            attempts.append(
                loop_entry(
                    prompt=prompt, payload=current, sql=sql, outcome=outcome, dialect=dialect
                )
            )
            protected_sql = sql
            if not _can_retry(retries=retries, used=used, cap=cap):
                return abstain(f"loop_exhausted:{outcome}", attempts)
            retries += 1
            used += 1
            prev_sql = sql
            reason = outcome
            current = _call(compute, ctx, prompt, reason, sql)
            continue

        rows, exec_err = run_readonly(sql, Path(warehouse))
        if exec_err:
            outcome = f"db_error:{exec_err}"
            attempts.append(
                loop_entry(
                    prompt=prompt, payload=current, sql=sql, outcome=outcome, dialect=dialect
                )
            )
            protected_sql = sql
            if not _can_retry(retries=retries, used=used, cap=cap):
                return abstain(f"loop_exhausted:{outcome}", attempts)
            retries += 1
            used += 1
            prev_sql = sql
            reason = outcome
            current = _call(compute, ctx, prompt, reason, sql)
            continue

        protected_sql = sql
        got = rows or []
        if not got:
            # Empty is not a retry. Confirmed empty is the answer.
            if confirms_empty(current):
                attempts.append(
                    loop_entry(
                        prompt=prompt,
                        payload=current,
                        sql=sql,
                        outcome="served",
                        dialect=dialect,
                    )
                )
                return empty_answer(sql, attempts)
            attempts.append(
                loop_entry(
                    prompt=prompt,
                    payload=current,
                    sql=sql,
                    outcome="empty",
                    dialect=dialect,
                )
            )
            return submit_sql(sql, attempts)
        attempts.append(
            loop_entry(
                prompt=prompt,
                payload=current,
                sql=sql,
                outcome="served",
                dialect=dialect,
            )
        )
        return submit_sql(sql, attempts)


def _call(
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    ctx: dict[str, Any],
    prompt: str,
    reason: str,
    previous_sql: str | None,
) -> dict[str, Any] | None:
    feedback = {
        "previous_sql": previous_sql or "",
        "reason": reason,
        "prompt": prompt,
    }
    nxt = dict(ctx)
    nxt["sql_loop_feedback"] = feedback
    try:
        got = compute(nxt)
    except Exception:
        return None
    return got if isinstance(got, dict) else None


def apply_sql_credit(
    env: dict[str, Any],
    payload: dict[str, Any] | None,
    loop: list[dict[str, Any]] | None,
    *,
    dialect: str,
) -> None:
    """Name the model and key only when the served SQL is one they wrote.

    Otherwise ``served_attribution`` is the explicit value ``none``.
    """
    served = env.get("sql_used")
    served_s = served if isinstance(served, str) else None
    match: dict[str, Any] | None = None
    for attempt in loop or []:
        if not attempt.get("model_wrote"):
            continue
        attempt_dialect = _text(attempt.get("dialect")) or dialect
        if sql_byte_equal(served_s, _text(attempt.get("sql")), attempt_dialect):
            match = attempt
            break
    if match is None and isinstance(payload, dict):
        from cortex_client.compute import insights_query_sql

        raw = _text(payload.get("query_sql")) or insights_query_sql(payload)
        if raw and sql_byte_equal(served_s, raw, dialect):
            match = {
                "model": _text(payload.get("served_model")),
                "provider": _text(payload.get("served_provider")),
                "key": _key_id(payload),
                "from_payload": True,
            }
    if match is None:
        env["served_attribution"] = "none"
        return
    model = _text(match.get("model"))
    provider = _text(match.get("provider"))
    key = _text(match.get("key"))
    if match.get("from_payload"):
        if key:
            env["ov_key_id"] = key
        return
    if model and (key or provider):
        env["served_attribution"] = "reported"
        env["served_model"] = model
        if provider:
            env["served_provider"] = provider
        if key:
            env["ov_key_id"] = key
        return
    env["served_attribution"] = "missing"
