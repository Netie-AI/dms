"""Space-scoped steward knowledge (BIRD-EVIDENCE-01).

``DMS_SPACE_KNOWLEDGE`` defaults to off. Off, nothing is stored, nothing is
retrieved, and the ask prompt is unchanged.

On, a steward proposes a note (free text plus an optional SQL fragment),
then approves it. A revision is a new row with the next version. Approving
that row revokes the one it supersedes. Retrieval returns approved rows for
that Space only. The model sees the top notes and an optional per-ask
context string. Qualifier gates and PII extraction keep the question.

Ranking is an embedding when the caller has one, else an OpenVault call
with role ``rank`` when that host is configured, else Okapi BM25. No word
list. The SQL fragment is an example for the writer, never a stored answer.
A listed scored statement is refused on write and dropped on read, including
when the same statement arrives as per-ask context. Both use
``skills_quarantine`` (``reject_scored_write`` / ``filter_retrieved_rows``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from cortex_client.insights import SPACE_KNOWLEDGE_MARK

from dms_executor.demo_warehouse import warehouse_path
from dms_executor.verified_queries import rank_texts, scope_key

FLAG = "DMS_SPACE_KNOWLEDGE"
ROLE = "rank"
HEADER = "space knowledge; not a filter"
_TABLE = "main._space_knowledge"
_LOCK = threading.Lock()
_K = 3
_BODY_CAP = 4000
_SQL_CAP = 2000
_MODEL_KEYS = frozenset(
    {
        "model",
        "model_id",
        "model_name",
        "served_model",
        "served_provider",
        "provider",
        "strict",
    }
)
logger = logging.getLogger(__name__)


class KnowledgeDisabled(RuntimeError):
    """The flag is off. Nothing was read and nothing was stored."""


def space_knowledge_enabled() -> bool:
    """True only when ``DMS_SPACE_KNOWLEDGE`` is the digit 1. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


def _require_on() -> None:
    if not space_knowledge_enabled():
        raise KnowledgeDisabled()


def _now() -> datetime:
    return datetime.now(UTC)


def _clean_body(body: str) -> str:
    text = " ".join((body or "").split())
    if not text:
        raise ValueError("note_required")
    if len(text) > _BODY_CAP:
        raise ValueError("note_too_long")
    return text


def _clean_sql(sql: str | None, *, warehouse: Path) -> str | None:
    if sql is None:
        return None
    text = " ".join(str(sql).split())
    if not text:
        return None
    if len(text) > _SQL_CAP:
        raise ValueError("sql_fragment_too_long")
    from dms_executor.reconfirm import question_is_destructive

    if question_is_destructive(text):
        raise ValueError("sql_fragment_rejected")
    from dms_executor.skills_quarantine import reject_scored_write

    reject_scored_write({"sql_fragment": text}, warehouse=warehouse)
    return text


def _connect(path: Path, *, write: bool) -> duckdb.DuckDBPyConnection:
    from dms_executor.demo_warehouse import connect_file, connect_serving

    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        return connect_file(path, write=True)
    return connect_serving(path)


def _ensure(con: duckdb.DuckDBPyConnection) -> None:
    from dms_executor.lake_schema import catalog_lock

    with catalog_lock:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
              note_id VARCHAR PRIMARY KEY,
              space_id VARCHAR NOT NULL,
              body VARCHAR NOT NULL,
              sql_fragment VARCHAR,
              status VARCHAR NOT NULL,
              version INTEGER NOT NULL,
              supersedes VARCHAR,
              created_at TIMESTAMPTZ,
              approved_at TIMESTAMPTZ,
              actor VARCHAR,
              approved_by VARCHAR,
              revoked_at TIMESTAMPTZ
            )
            """
        )


def _stamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _public(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "note_id": row[0],
        "space_id": row[1],
        "body": row[2],
        "sql_fragment": row[3] or "",
        "status": row[4],
        "version": int(row[5]),
        "supersedes": row[6] or "",
        "created_at": _stamp(row[7]) or "",
        "approved_at": _stamp(row[8]) or "",
        "actor": row[9] or "",
        "approved_by": row[10] or "",
        "revoked_at": _stamp(row[11]) or "",
    }


_COLUMNS = (
    "note_id, space_id, body, sql_fragment, status, version, supersedes, "
    "created_at, approved_at, actor, approved_by, revoked_at"
)


def _rows(con: duckdb.DuckDBPyConnection, sid: str) -> list[dict[str, Any]]:
    try:
        raw = con.execute(
            f"""
            SELECT {_COLUMNS}
            FROM {_TABLE}
            WHERE space_id = ?
            ORDER BY version, created_at
            """,
            [sid],
        ).fetchall()
    except duckdb.CatalogException:
        return []
    return [_public(row) for row in raw]


def _one(con: duckdb.DuckDBPyConnection, note_id: str) -> dict[str, Any] | None:
    try:
        raw = con.execute(
            f"SELECT {_COLUMNS} FROM {_TABLE} WHERE note_id = ?",
            [note_id],
        ).fetchone()
    except duckdb.CatalogException:
        return None
    if raw is None:
        return None
    return _public(raw)


def propose_note(
    *,
    space_id: str,
    body: str,
    sql_fragment: str | None = None,
    actor: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Store one proposed note. It is not retrieved until a steward approves it."""
    _require_on()
    sid = scope_key(space_id)
    if not sid:
        raise ValueError("space_id_required")
    db = path or warehouse_path()
    text = _clean_body(body)
    fragment = _clean_sql(sql_fragment, warehouse=db)
    note_id = f"kn_{uuid.uuid4().hex[:16]}"
    created = _now()
    with _LOCK:
        con = _connect(db, write=True)
        try:
            _ensure(con)
            con.execute(
                f"""
                INSERT INTO {_TABLE}
                  (note_id, space_id, body, sql_fragment, status, version,
                   supersedes, created_at, actor)
                VALUES (?, ?, ?, ?, 'proposed', 1, NULL, ?, ?)
                """,
                [note_id, sid, text, fragment, created, (actor or "").strip() or None],
            )
        finally:
            con.close()
    return {
        "note_id": note_id,
        "space_id": sid,
        "body": text,
        "sql_fragment": fragment or "",
        "status": "proposed",
        "version": 1,
        "supersedes": "",
        "actor": (actor or "").strip(),
    }


def approve_note(
    *,
    note_id: str,
    actor: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """Approve one proposed note. The row it supersedes is revoked."""
    _require_on()
    who = (actor or "").strip()
    if not who:
        raise ValueError("actor_required")
    nid = (note_id or "").strip()
    if not nid:
        raise ValueError("note_id_required")
    db = path or warehouse_path()
    approved = _now()
    with _LOCK:
        con = _connect(db, write=True)
        try:
            _ensure(con)
            row = _one(con, nid)
            if row is None:
                raise ValueError("note_not_found")
            if row["revoked_at"]:
                raise ValueError("note_not_proposed")
            if row["status"] == "approved":
                return row
            if row["status"] != "proposed":
                raise ValueError("note_not_proposed")
            previous = str(row["supersedes"] or "")
            con.execute(
                f"""
                UPDATE {_TABLE}
                SET status = 'approved', approved_at = ?, approved_by = ?
                WHERE note_id = ?
                """,
                [approved, who, nid],
            )
            if previous:
                con.execute(
                    f"""
                    UPDATE {_TABLE}
                    SET revoked_at = ?
                    WHERE note_id = ? AND revoked_at IS NULL
                    """,
                    [approved, previous],
                )
            out = _one(con, nid)
        finally:
            con.close()
    if out is None:
        raise ValueError("note_not_found")
    return out


def revise_note(
    *,
    note_id: str,
    body: str,
    sql_fragment: str | None = None,
    actor: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """A new proposed version. The previous row stays until this one is approved."""
    _require_on()
    nid = (note_id or "").strip()
    if not nid:
        raise ValueError("note_id_required")
    db = path or warehouse_path()
    text = _clean_body(body)
    fragment = _clean_sql(sql_fragment, warehouse=db)
    created = _now()
    new_id = f"kn_{uuid.uuid4().hex[:16]}"
    with _LOCK:
        con = _connect(db, write=True)
        try:
            _ensure(con)
            row = _one(con, nid)
            if row is None or row["revoked_at"]:
                raise ValueError("note_not_found")
            version = int(row["version"]) + 1
            con.execute(
                f"""
                INSERT INTO {_TABLE}
                  (note_id, space_id, body, sql_fragment, status, version,
                   supersedes, created_at, actor)
                VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?)
                """,
                [
                    new_id,
                    row["space_id"],
                    text,
                    fragment,
                    version,
                    nid,
                    created,
                    (actor or "").strip() or None,
                ],
            )
        finally:
            con.close()
    return {
        "note_id": new_id,
        "space_id": row["space_id"],
        "body": text,
        "sql_fragment": fragment or "",
        "status": "proposed",
        "version": version,
        "supersedes": nid,
        "actor": (actor or "").strip(),
    }


def list_notes(*, space_id: str, path: Path | None = None) -> list[dict[str, Any]]:
    """Current notes for one Space. Revoked rows are omitted."""
    _require_on()
    sid = scope_key(space_id)
    db = path or warehouse_path()
    if not db.is_file():
        return []
    con = _connect(db, write=False)
    try:
        rows = _rows(con, sid)
    finally:
        con.close()
    return [row for row in rows if not row["revoked_at"]]


def _approved(path: Path, sid: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        con = _connect(path, write=False)
    except Exception:
        logger.warning("space_knowledge_unreadable")
        return []
    try:
        rows = _rows(con, sid)
    except Exception:
        logger.warning("space_knowledge_unreadable")
        return []
    finally:
        con.close()
    return [
        row
        for row in rows
        if row["status"] == "approved" and not row["revoked_at"]
    ]


def _doc(row: dict[str, Any]) -> str:
    parts = [str(row.get("body") or "")]
    fragment = str(row.get("sql_fragment") or "").strip()
    if fragment:
        parts.append(fragment)
    return "\n".join(parts)


def _reject_model_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _MODEL_KEYS:
                raise ValueError("model_key_refused")
            _reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_model_keys(item)


def rank_request_body(question: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
    """OpenVault body. Role ``rank``. No model id and no provider."""
    user = {
        "question": question if isinstance(question, str) else "",
        "notes": [
            {
                "note_id": str(row.get("note_id") or ""),
                "body": str(row.get("body") or ""),
            }
            for row in notes
        ],
    }
    body: dict[str, Any] = {
        "role": ROLE,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Order the note ids that help write SQL for the question. "
                    "Reply with a JSON list of note ids, best first."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    user, sort_keys=True, separators=(",", ":")
                ),
            },
        ],
    }
    _reject_model_keys(body)
    return body


def _id_list(payload: Any) -> list[str] | None:
    if isinstance(payload, list):
        return [str(item) for item in payload if isinstance(item, str) and item]
    if isinstance(payload, dict):
        for key in ("note_ids", "ids"):
            raw = payload.get(key)
            if isinstance(raw, list):
                return [str(item) for item in raw if isinstance(item, str) and item]
        content = payload.get("content")
        if isinstance(content, str):
            try:
                return _id_list(json.loads(content))
            except json.JSONDecodeError:
                return None
    if isinstance(payload, str):
        try:
            return _id_list(json.loads(payload))
        except json.JSONDecodeError:
            return None
    return None


def notes_in_rank_order(
    payload: Any, notes: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """Notes named by a rank reply, best first. None when the reply is unusable."""
    ids = _id_list(payload)
    if ids is None:
        return None
    by_id = {str(row["note_id"]): row for row in notes}
    out: list[dict[str, Any]] = []
    for nid in ids:
        row = by_id.get(nid)
        if row is not None and row not in out:
            out.append(row)
        if len(out) >= _K:
            break
    return out


def _rank(
    question: str,
    notes: list[dict[str, Any]],
    embedder: Callable[[list[str]], Any] | None,
    ranker: Callable[[str, list[dict[str, Any]]], Any] | None,
) -> list[dict[str, Any]]:
    if not notes:
        return []
    if embedder is not None:
        ranked = rank_texts(
            question,
            [_doc(row) for row in notes],
            embedder,
            limit=_K,
            floor=0.0,
        )
        return [notes[index] for _score, index in ranked]
    if ranker is not None:
        try:
            payload = ranker(question, notes)
        except Exception:
            logger.warning("space_knowledge_rank_unavailable")
            payload = None
        ordered = notes_in_rank_order(payload, notes) if payload is not None else None
        if ordered:
            return ordered
    ranked = rank_texts(
        question,
        [_doc(row) for row in notes],
        None,
        limit=_K,
        floor=0.0,
    )
    return [notes[index] for _score, index in ranked]


def _default_ranker() -> Callable[[str, list[dict[str, Any]]], Any] | None:
    """Role ``rank`` when OpenVault is configured. None skips the call."""
    if not os.environ.get("OPENVAULT_URL", "").strip():
        return None

    def _rank_call(question: str, notes: list[dict[str, Any]]) -> Any:
        from dms_executor.grain_ai import default_transport

        body = rank_request_body(question, notes)
        return default_transport(body)

    return _rank_call


def retrieve_notes(
    question: str,
    *,
    space_id: str | None,
    warehouse: Path | None = None,
    embedder: Callable[[list[str]], Any] | None = None,
    ranker: Callable[[str, list[dict[str, Any]]], Any] | None = None,
) -> list[dict[str, Any]]:
    """Top approved notes for this Space. Empty when the flag is off."""
    if not space_knowledge_enabled():
        return []
    q = (question or "").strip()
    if not q:
        return []
    db = warehouse or warehouse_path()
    notes = _approved(db, scope_key(space_id))
    if not notes:
        return []
    from dms_executor.skills_quarantine import filter_retrieved_rows

    notes = filter_retrieved_rows(notes, warehouse=db)
    if not notes:
        return []
    chooser = ranker if ranker is not None else _default_ranker()
    return _rank(q, notes, embedder, chooser)


def _visible_context(ask_context: str | None, warehouse: Path | None) -> str:
    """Per-ask context, or empty when that text is a listed scored statement."""
    text = " ".join((ask_context or "").split())
    if not text:
        return ""
    from dms_executor.skills_quarantine import filter_retrieved_rows

    kept = filter_retrieved_rows([{"sql_fragment": text}], warehouse=warehouse)
    if not kept:
        return ""
    return text


def _render(
    notes: list[dict[str, Any]],
    ask_context: str | None,
    warehouse: Path | None,
) -> str:
    lines: list[str] = []
    for row in notes:
        lines.append("note: " + str(row.get("body") or ""))
        fragment = str(row.get("sql_fragment") or "").strip()
        if fragment:
            lines.append("sql: " + fragment)
    context = _visible_context(ask_context, warehouse)
    if context:
        lines.append("ask context:")
        lines.append(context)
    if not lines:
        return ""
    return SPACE_KNOWLEDGE_MARK + "\n".join(lines) + "\n"


def knowledge_prompt(
    question: str,
    *,
    space_id: str | None = None,
    warehouse: Path | None = None,
    embedder: Callable[[list[str]], Any] | None = None,
    ranker: Callable[[str, list[dict[str, Any]]], Any] | None = None,
    ask_context: str | None = None,
) -> str:
    """Prompt block for the SQL writer. Empty when the flag is off.

    The block starts with ``SPACE_KNOWLEDGE_MARK``. Callers append it to the
    model question and do not pass it to qualifier or PII gates.
    """
    if not space_knowledge_enabled():
        return ""
    db = warehouse or warehouse_path()
    notes = retrieve_notes(
        question,
        space_id=space_id,
        warehouse=db,
        embedder=embedder,
        ranker=ranker,
    )
    return _render(notes, ask_context, db)
