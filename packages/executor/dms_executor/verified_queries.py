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


def maybe_verified_ask(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    tables: list[str] | None = None,
) -> dict[str, Any] | None:
    """L0 envelope when this Space has a matching registered asset; else None.

    Grounded-file asks skip this path — the steward asset is Space-scoped, not a
    file pin. Foreign Spaces miss because the scan is ``WHERE space_id = ?``.
    """
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
    from dms_executor.demo_warehouse import execute_sql

    raw = execute_sql(sql_text, path=db)
    out_rows = _json_rows(raw)
    text = f"Found {len(out_rows)} row(s)."
    if out_rows:
        text += "\n" + "\n".join(
            "  - " + ", ".join(f"{k}={v}" for k, v in row.items()) for row in out_rows[:12]
        )
    env = build_answer_envelope(
        answer_id=f"ans_{match[0]}",
        text=text,
        badge="L0_CERTIFIED",
        abstained=False,
        rows=out_rows,
        sql_used=sql_text,
        assumptions=["verified question registered in Studio for this Space"],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="verified_query",
        question=question,
    )
    assert_envelope_valid(env)
    return env
