"""Space-scoped verified question→SQL assets (VQ-02).

Steward-registered, not Cortex pack YAML. Match is exact normalize plus
synonyms stored on the asset — not a product regex cascade (VQ-01 owns pack
match). Leading underscore hides the table from Library listings.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.demo_ask import normalize_ask_question
from dms_executor.demo_grants import canonical_space_id
from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse, warehouse_path
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql

#: Leading underscore keeps this out of list_bronze_tables / Library tree.
_TABLE = "main._verified_queries"
_LOCK = threading.Lock()
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_KNOWN = frozenset(DEMO_TABLES)
_COMPANY = "company-default"


def scope_key(space_id: str | None) -> str:
    """Durable Space key. Blank / omitted is company-default, never 'all Spaces'."""
    raw = (space_id or "").strip()
    if not raw or raw == _COMPANY:
        return _COMPANY
    return canonical_space_id(raw)


def normalize_verified_question(question: str) -> str:
    return " ".join(normalize_ask_question(question).casefold().split())


def _grantable(space_id: str | None, warehouse: Path | None) -> set[str]:
    from dms_executor import Executor

    lookup = None if scope_key(space_id) == _COMPANY else space_id
    return set(Executor(cortex=None, warehouse_path=warehouse).grantable_tables(space_id=lookup))


def _named_warehouse_tables(sql: str) -> set[str]:
    # ponytail: identifier scan against DEMO_TABLES — upgrade to sqlglot in this
    # package if stewards start registering CTE-heavy SQL that name-shadows tables.
    return {tok for tok in _IDENT.findall(sql or "") if tok in _KNOWN}


def _sql_outside_space(sql: str, grantable: set[str]) -> set[str]:
    return _named_warehouse_tables(sql) - grantable


def _ensure(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
          asset_id VARCHAR PRIMARY KEY,
          space_id VARCHAR NOT NULL,
          question VARCHAR NOT NULL,
          question_norm VARCHAR NOT NULL,
          sql_text VARCHAR NOT NULL,
          synonyms_json VARCHAR NOT NULL,
          created_at TIMESTAMPTZ
        )
        """
    )


def _connect(path: Path | None) -> duckdb.DuckDBPyConnection:
    db = ensure_demo_warehouse(path)
    # Write-mode: mixed read_only=True vs RW on one file 500s DuckDB.
    return duckdb.connect(str(db))


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _json_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{str(k): _cell(v) for k, v in row.items()} for row in rows]


def _as_of() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _synonyms_norm(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in raw or []:
        n = normalize_verified_question(str(item))
        if n and n not in out:
            out.append(n)
    return out


def _row_out(row: tuple[Any, ...]) -> dict[str, Any]:
    created = row[6]
    created_s = created.isoformat() if hasattr(created, "isoformat") else str(created)
    syn = json.loads(row[5] or "[]")
    return {
        "asset_id": row[0],
        "space_id": row[1],
        "question": row[2],
        "sql": row[4],
        "synonyms": syn if isinstance(syn, list) else [],
        "created_at": created_s,
    }


def register_verified_query(
    *,
    space_id: str,
    question: str,
    sql: str,
    synonyms: list[str] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Persist a Space-scoped Q→SQL asset after hostile + grant checks."""
    sid = scope_key(space_id)
    q = (question or "").strip()
    sql_text = (sql or "").strip()
    if not sid:
        raise ValueError("space_id_required")
    if not q:
        raise ValueError("question_required")
    if not sql_text:
        raise ValueError("sql_required")
    try:
        reject_hostile_chat_sql(sql_text)
    except SecurityEvent as exc:
        raise ValueError(f"hostile_sql:{exc.code}") from exc
    db = path or warehouse_path()
    grantable = _grantable(space_id, db)
    leaked = _sql_outside_space(sql_text, grantable)
    if leaked:
        raise ValueError(f"sql_not_in_space:{','.join(sorted(leaked))}")
    qn = normalize_verified_question(q)
    syn = _synonyms_norm(synonyms)
    asset_id = f"vq_{uuid.uuid4().hex[:16]}"
    created = datetime.now(UTC)
    with _LOCK:
        con = _connect(db)
        try:
            _ensure(con)
            con.execute(
                f"DELETE FROM {_TABLE} WHERE space_id = ? AND question_norm = ?",
                [sid, qn],
            )
            con.execute(
                f"""
                INSERT INTO {_TABLE}
                  (asset_id, space_id, question, question_norm, sql_text, synonyms_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [asset_id, sid, q, qn, sql_text, json.dumps(syn), created],
            )
        finally:
            con.close()
    return {
        "asset_id": asset_id,
        "space_id": sid,
        "question": q,
        "sql": sql_text,
        "synonyms": syn,
        "created_at": created.isoformat(),
    }


def list_verified_queries(*, space_id: str, path: Path | None = None) -> list[dict[str, Any]]:
    sid = scope_key(space_id)
    db = path or warehouse_path()
    con = _connect(db)
    try:
        _ensure(con)
        rows = con.execute(
            f"""
            SELECT asset_id, space_id, question, question_norm, sql_text, synonyms_json, created_at
            FROM {_TABLE}
            WHERE space_id = ?
            ORDER BY created_at DESC
            """,
            [sid],
        ).fetchall()
    finally:
        con.close()
    return [_row_out(r) for r in rows]


def _hit(question: str, row: tuple[Any, ...]) -> bool:
    qn = normalize_verified_question(question)
    if not qn:
        return False
    if row[3] == qn:
        return True
    syn = json.loads(row[5] or "[]")
    return qn in syn if isinstance(syn, list) else False


def lookup_verified_query(
    question: str,
    *,
    space_id: str | None = None,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    tables: list[str] | None = None,
) -> dict[str, str] | None:
    """Return ``{asset_id, sql}`` for a Space hit. Never executes SQL."""
    if tables:
        return None
    sid = scope_key(space_id)
    db = warehouse or warehouse_path()
    con = _connect(db)
    try:
        _ensure(con)
        rows = con.execute(
            f"""
            SELECT asset_id, space_id, question, question_norm, sql_text, synonyms_json, created_at
            FROM {_TABLE}
            WHERE space_id = ?
            """,
            [sid],
        ).fetchall()
    finally:
        con.close()
    match = next((r for r in rows if _hit(question, r)), None)
    if match is None:
        return None
    sql_text = str(match[4])
    allowed = grantable if grantable is not None else _grantable(space_id, db)
    if _sql_outside_space(sql_text, allowed):
        return None
    try:
        reject_hostile_chat_sql(sql_text)
    except SecurityEvent:
        return None
    return {"asset_id": str(match[0]), "sql": sql_text}


def rows_from_submit_result(result: Any) -> list[dict[str, Any]]:
    """Pull row dicts from a Cortex submit QueryResult. Empty if unreadable."""
    out = getattr(result, "output", None)
    if out is None and isinstance(result, dict):
        out = result.get("output")
    if isinstance(out, list):
        return _json_rows([r for r in out if isinstance(r, dict)])
    if isinstance(out, dict):
        raw = out.get("rows")
        if raw is None:
            raw = out.get("data")
        if isinstance(raw, list):
            return _json_rows([r for r in raw if isinstance(r, dict)])
    extra = getattr(result, "additional_properties", None)
    if isinstance(extra, dict):
        raw = extra.get("rows")
        if isinstance(raw, list):
            return _json_rows([r for r in raw if isinstance(r, dict)])
    return []


def envelope_from_verified_submit(
    *,
    asset_id: str,
    sql_text: str,
    result: Any,
    question: str,
    space_id: str | None = None,
    session_id: str | None = None,
    audit_id: str | None = None,
) -> dict[str, Any]:
    """L0 envelope from Cortex-executed steward SQL. Caller must have submitted."""
    out_rows = rows_from_submit_result(result)
    run_id = getattr(result, "run_id", None) or ""
    receipt = (audit_id or "").strip() or (str(run_id).strip() if run_id else "")
    if not receipt:
        receipt = f"cortex_submit_{asset_id}"
    text = f"Found {len(out_rows)} row(s)."
    if out_rows:
        text += "\n" + "\n".join(
            "  - " + ", ".join(f"{k}={v}" for k, v in row.items()) for row in out_rows[:12]
        )
    env = build_answer_envelope(
        answer_id=f"ans_{asset_id}",
        text=text,
        badge="L0_CERTIFIED",
        abstained=False,
        rows=out_rows,
        sql_used=sql_text,
        assumptions=[
            "verified question registered in Studio for this Space",
            "executed via Cortex submit",
        ],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="verified_query",
        question=question,
        audit_id=receipt,
    )
    assert_envelope_valid(env)
    return env


def maybe_verified_ask(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    tables: list[str] | None = None,
    submit: Callable[[str], Any] | None = None,
    ledger_append: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any] | None:
    """L0 envelope when this Space has a matching asset executed via Cortex.

    ``submit`` must be Cortex HTTP submit of the registered SQL. ``ledger_append``
    must be Cortex HTTP ledger. Missing either does not fall back to local DuckDB
    (F83). Grounded-file asks skip this path. Foreign Spaces miss on ``space_id``.
    """
    hit = lookup_verified_query(
        question,
        space_id=space_id,
        warehouse=warehouse,
        grantable=grantable,
        tables=tables,
    )
    if hit is None:
        return None
    if submit is None or ledger_append is None:
        return None
    result = submit(hit["sql"])
    ok = getattr(result, "ok", None)
    if ok is False:
        return None
    if getattr(result, "output", None) is None:
        # Bind-shaped submit (no SQL output) must not stamp L0.
        return None
    run_id = str(getattr(result, "run_id", None) or "")
    led = ledger_append({"sql": hit["sql"], "run_id": run_id})
    entry_id = getattr(led, "entry_id", None) if led is not None else None
    if not (isinstance(entry_id, str) and entry_id.strip()):
        return None
    led_hash = getattr(led, "hash", None)
    if not (isinstance(led_hash, str) and led_hash.strip()) or led_hash == entry_id:
        return None
    return envelope_from_verified_submit(
        asset_id=hit["asset_id"],
        sql_text=hit["sql"],
        result=result,
        question=question,
        space_id=space_id,
        session_id=session_id,
        audit_id=entry_id.strip(),
    )
