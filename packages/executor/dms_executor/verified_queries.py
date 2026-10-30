"""Space-scoped verified question to SQL assets (VQ-02, VQ-RAG-01).

Steward-registered, not Cortex pack YAML. With ``DMS_VQ_RAG`` off (the
default), match is exact normalize plus synonyms stored on the asset, and
``maybe_verified_ask`` replays that SQL through Cortex. With the flag on,
that replay returns nothing. Similar rows are few-shot examples for the SQL
writer. Similarity is an embedding from the Cortex client when it exposes
one (OpenVault still picks the model) and Okapi BM25 otherwise. No word list.

Save-on-Yes stores one row per question: the question, the SQL, a schema
hash, and a provenance token (``yes:reconfirm`` or ``yes:vote``). Revoked
rows are not retrieved. A listed pack hash, SQL hash, or result hash is not
written. Leading underscore hides the table from Library listings.

Every read goes through ``_rows_for_space``, which applies
``filter_retrieved_rows``. Writes whose pack hash, SQL content hash, or
result hash is listed raise ``scored_pack_write_blocked``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.demo_ask import normalize_ask_question
from dms_executor.demo_grants import canonical_space_id
from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse, warehouse_path
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.grant_struct import serve_gap
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql
from dms_executor.skills_quarantine import (
    canonical_pack_hash,
    filter_retrieved_rows,
    reject_scored_write,
)

#: Leading underscore keeps this out of list_bronze_tables / Library tree.
_TABLE = "main._verified_queries"
_LOCK = threading.Lock()
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_TOKEN = re.compile(r"\w+", re.UNICODE)
_KNOWN = frozenset(DEMO_TABLES)
_COMPANY = "company-default"
_FLAG = "DMS_VQ_RAG"
_FLAG_ON = frozenset({"1", "true", "yes", "on"})
_SAVE_PROVENANCE = frozenset({"yes:reconfirm", "yes:vote"})
_SKIP_ROUTES = frozenset({"verified_query", "l1_governed_metric"})
# ponytail: fixed k. Upgrade: a caller token budget.
_EXAMPLE_LIMIT = 3
_BM25_K1 = 1.5
_BM25_B = 0.75
logger = logging.getLogger(__name__)


def scope_key(space_id: str | None) -> str:
    """Durable Space key. Blank / omitted is company-default, never 'all Spaces'."""
    raw = (space_id or "").strip()
    if not raw or raw == _COMPANY:
        return _COMPANY
    return canonical_space_id(raw)


def normalize_verified_question(question: str) -> str:
    return " ".join(normalize_ask_question(question).casefold().split())


def vq_rag_enabled() -> bool:
    """``DMS_VQ_RAG`` is off unless set to a true token."""
    return os.environ.get(_FLAG, "").strip().lower() in _FLAG_ON


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
    from dms_executor.lake_schema import catalog_lock

    # First CREATE from two cursors write-write conflicts. Later IF NOT EXISTS does not.
    with catalog_lock:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
              asset_id VARCHAR PRIMARY KEY,
              space_id VARCHAR NOT NULL,
              question VARCHAR NOT NULL,
              question_norm VARCHAR NOT NULL,
              sql_text VARCHAR NOT NULL,
              synonyms_json VARCHAR NOT NULL,
              created_at TIMESTAMPTZ,
              pack_hash VARCHAR,
              schema_hash VARCHAR,
              provenance VARCHAR,
              revoked_at TIMESTAMPTZ
            )
            """
        )
        # Tables created before pack_hash existed. CREATE IF NOT EXISTS does not
        # add the column. NULL means no provenance: the filter then uses the SQL
        # content hash and, when result hashes are configured, the result hash.
        names = {
            str(row[0]).lower()
            for row in con.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = '_verified_queries'
                """
            ).fetchall()
        }
        if "pack_hash" not in names:
            con.execute(f"ALTER TABLE {_TABLE} ADD COLUMN pack_hash VARCHAR")
            names.add("pack_hash")
        for column, decl in (
            ("schema_hash", "VARCHAR"),
            ("provenance", "VARCHAR"),
            ("revoked_at", "TIMESTAMPTZ"),
        ):
            if column not in names:
                con.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {column} {decl}")


def _connect(path: Path | None, *, write: bool = False) -> duckdb.DuckDBPyConnection:
    from dms_executor.demo_warehouse import connect_file, connect_serving

    db = ensure_demo_warehouse(path)
    if write:
        return connect_file(db, write=True)
    return connect_serving(db)


def _cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        # DuckDB DECIMAL. A string here is invisible to E4, so a true SUM
        # reads as an uncited figure.
        return float(value)
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


def _stored_row(row: tuple[Any, ...]) -> dict[str, Any]:
    created = row[6]
    created_s = created.isoformat() if hasattr(created, "isoformat") else str(created)
    syn = json.loads(row[5] or "[]")
    revoked = row[10] if len(row) > 10 else None
    if revoked is not None and hasattr(revoked, "isoformat"):
        revoked = revoked.isoformat()
    return {
        "asset_id": row[0],
        "space_id": row[1],
        "question": row[2],
        "question_norm": row[3],
        "sql": row[4],
        "synonyms": syn if isinstance(syn, list) else [],
        "created_at": created_s,
        "pack_hash": row[7] if len(row) > 7 else None,
        "schema_hash": row[8] if len(row) > 8 else None,
        "provenance": row[9] if len(row) > 9 else None,
        "revoked_at": revoked,
    }


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": row["asset_id"],
        "space_id": row["space_id"],
        "question": row["question"],
        "sql": row["sql"],
        "synonyms": row["synonyms"],
        "created_at": row["created_at"],
    }


def _rows_for_space(db: Path, sid: str) -> list[dict[str, Any]]:
    """Load one Space on the serving connection, then drop scored-pack rows.

    Register creates the table. Lookup does not: a missing table is no rows.
    A table from before a column existed is read with a null for that column.
    """

    def _expr(names: set[str], column: str) -> str:
        return column if column in names else "NULL"

    con = _connect(db)
    try:
        try:
            found = con.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = '_verified_queries'
                """
            ).fetchall()
        except duckdb.CatalogException:
            return []
        names = {str(row[0]).lower() for row in found}
        if "asset_id" not in names:
            return []
        try:
            raw = con.execute(
                f"""
                SELECT asset_id, space_id, question, question_norm, sql_text,
                       synonyms_json, created_at, {_expr(names, "pack_hash")},
                       {_expr(names, "schema_hash")}, {_expr(names, "provenance")},
                       {_expr(names, "revoked_at")}
                FROM {_TABLE}
                WHERE space_id = ?
                ORDER BY created_at DESC
                """,
                [sid],
            ).fetchall()
        except duckdb.CatalogException:
            return []
    finally:
        con.close()
    return filter_retrieved_rows((_stored_row(row) for row in raw), warehouse=db)


def register_verified_query(
    *,
    space_id: str,
    question: str,
    sql: str,
    synonyms: list[str] | None = None,
    pack_hash: str | None = None,
    path: Path | None = None,
    dialect: str | None = None,
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
    db = path or warehouse_path()
    grantable = _grantable(space_id, db)
    # Statement count before the hostile scanner, both inside serve_gap.
    # A plain ungranted demo table keeps the sql_not_in_space sentence.
    gap = serve_gap(sql_text, grantable=grantable, dialect=dialect)
    # A plain ungranted relation still reaches the demo-table sentence.
    # ``ungranted:file`` and ``ungranted:unparsed`` do too; anything else raises.
    if gap and gap != "ungranted" and not str(gap).startswith("ungranted:"):
        raise ValueError(gap)
    leaked = _sql_outside_space(sql_text, grantable)
    if leaked:
        raise ValueError(f"sql_not_in_space:{','.join(sorted(leaked))}")
    if gap:
        raise ValueError(gap)
    qn = normalize_verified_question(q)
    syn = _synonyms_norm(synonyms)
    carried = canonical_pack_hash(pack_hash)
    # Before DELETE: a blocked rewrite must not drop the row already stored.
    # Question text is not an exclusion input.
    reject_scored_write({"sql": sql_text, "pack_hash": carried}, warehouse=db)
    asset_id = f"vq_{uuid.uuid4().hex[:16]}"
    created = datetime.now(UTC)
    with _LOCK:
        con = _connect(db, write=True)
        try:
            _ensure(con)
            con.execute(
                f"DELETE FROM {_TABLE} WHERE space_id = ? AND question_norm = ?",
                [sid, qn],
            )
            con.execute(
                f"""
                INSERT INTO {_TABLE}
                  (asset_id, space_id, question, question_norm, sql_text,
                   synonyms_json, created_at, pack_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [asset_id, sid, q, qn, sql_text, json.dumps(syn), created, carried],
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
    return [_public_row(row) for row in _rows_for_space(db, sid)]


def openvault_embedder(client: Any) -> Callable[[list[str]], Any] | None:
    """The client's embed callable, or None.

    Called with the texts only. No model name and no provider key. OpenVault
    still chooses the model when the client has this method.
    """
    if client is None:
        return None
    fn = getattr(client, "embed", None)
    if not callable(fn):
        fn = getattr(client, "embeddings", None)
    if not callable(fn):
        return None

    def _embed(texts: list[str]) -> Any:
        return fn(list(texts))

    return _embed


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").casefold())


def _bm25_scores(query: str, docs: list[str]) -> list[float]:
    """Okapi BM25. Tokens are word characters. No stop list."""
    q_tokens = _tokens(query)
    if not q_tokens or not docs:
        return [0.0] * len(docs)
    tokenized = [_tokens(doc) for doc in docs]
    n = len(docs)
    avgdl = sum(len(toks) for toks in tokenized) / n
    df: dict[str, int] = {}
    for toks in tokenized:
        for tok in set(toks):
            df[tok] = df.get(tok, 0) + 1
    scores: list[float] = []
    for toks in tokenized:
        tf: dict[str, int] = {}
        for tok in toks:
            tf[tok] = tf.get(tok, 0) + 1
        dl = len(toks) or 1
        score = 0.0
        for tok in q_tokens:
            freq = tf.get(tok, 0)
            if not freq:
                continue
            n_q = df.get(tok, 0)
            idf = math.log(1.0 + (n - n_q + 0.5) / (n_q + 0.5))
            denom = freq + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * dl / avgdl)
            score += idf * (freq * (_BM25_K1 + 1.0)) / denom
        scores.append(score)
    return scores


def _as_vectors(raw: Any, n: int) -> list[list[float]] | None:
    if not isinstance(raw, list) or len(raw) != n:
        return None
    out: list[list[float]] = []
    width = 0
    for item in raw:
        vec = item
        if isinstance(item, dict):
            vec = item.get("embedding")
            if vec is None:
                vec = item.get("vector")
        if not isinstance(vec, (list, tuple)) or not vec:
            return None
        try:
            nums = [float(x) for x in vec]
        except (TypeError, ValueError):
            return None
        if width and len(nums) != width:
            return None
        width = len(nums)
        out.append(nums)
    return out


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        na += a * a
        nb += b * b
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _rank_examples(
    question: str,
    rows: list[dict[str, Any]],
    embedder: Callable[[list[str]], Any] | None,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    texts = [question, *[str(row.get("question") or "") for row in rows]]
    vectors = None
    if embedder is not None:
        try:
            vectors = _as_vectors(embedder(texts), len(texts))
        except Exception:
            logger.warning("verified_example_embed_unavailable")
            vectors = None
    if vectors is not None:
        scored = [(_cosine(vectors[0], vectors[i + 1]), row) for i, row in enumerate(rows)]
    else:
        scored = list(
            zip(
                _bm25_scores(question, [str(row.get("question") or "") for row in rows]),
                rows,
                strict=True,
            )
        )
    kept = [(score, row) for score, row in scored if score > 0.0]
    kept.sort(key=lambda item: item[0], reverse=True)
    return [row for _score, row in kept[:_EXAMPLE_LIMIT]]


def _example_ok(row: dict[str, Any], allowed: set[str], dialect: str | None) -> bool:
    if row.get("revoked_at"):
        return False
    sql_text = str(row.get("sql") or "")
    if _sql_outside_space(sql_text, allowed):
        return False
    try:
        reject_hostile_chat_sql(sql_text)
    except SecurityEvent:
        return False
    return serve_gap(sql_text, grantable=allowed, dialect=dialect) is None


def _example_public(row: dict[str, Any]) -> dict[str, Any]:
    schema = row.get("schema_hash")
    provenance = row.get("provenance")
    return {
        "asset_id": str(row.get("asset_id") or ""),
        "question": str(row.get("question") or ""),
        "sql": str(row.get("sql") or ""),
        "schema_hash": str(schema) if isinstance(schema, str) else "",
        "provenance": str(provenance) if isinstance(provenance, str) else "",
    }


def examples_text(rows: list[dict[str, Any]]) -> str:
    """Few-shot block. Empty when there is nothing to show the writer."""
    parts: list[str] = []
    for row in rows:
        question = " ".join(str(row.get("question") or "").split())
        sql = " ".join(str(row.get("sql") or "").split())
        if question and sql:
            parts.append(f"Question: {question}\nSQL: {sql}")
    return "\n".join(parts)


def retrieve_verified_examples(
    question: str,
    *,
    space_id: str | None = None,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    dialect: str | None = None,
    embedder: Callable[[list[str]], Any] | None = None,
) -> list[dict[str, Any]]:
    """Similar Space examples for the SQL writer. Never an answer to execute.

    Off when ``DMS_VQ_RAG`` is off. Revoked rows and scored-pack rows are absent.
    """
    if not vq_rag_enabled():
        return []
    q = (question or "").strip()
    if not q:
        return []
    try:
        sid = scope_key(space_id)
        db = warehouse or warehouse_path()
        allowed = grantable if grantable is not None else _grantable(space_id, db)
        rows = [row for row in _rows_for_space(db, sid) if _example_ok(row, allowed, dialect)]
    except Exception as exc:
        from dms_executor.lake_registry import SERVING_ERRORS

        if isinstance(exc, SERVING_ERRORS):
            raise
        logger.warning("verified_example_retrieve_unavailable")
        return []
    return [_example_public(row) for row in _rank_examples(q, rows, embedder)]


def _schema_hash(
    warehouse: Path | None,
    grantable: set[str] | None,
    dialect: str | None,
) -> str:
    try:
        from dms_executor.schema_context import read_catalog

        fingerprint, _body = read_catalog(warehouse, grantable, dialect)
    except Exception:
        logger.warning("verified_example_schema_hash_unavailable")
        return ""
    return fingerprint if isinstance(fingerprint, str) else ""


def _provenance_ok(provenance: str) -> bool:
    """``yes:reconfirm``, ``yes:vote``, or ``yes:definition:<code>``."""
    if provenance in _SAVE_PROVENANCE:
        return True
    prefix = "yes:definition:"
    if not provenance.startswith(prefix):
        return False
    code = provenance[len(prefix) :]
    return bool(code) and code.isidentifier()


def maybe_save_checked_sql(
    *,
    question: str,
    sql: str,
    space_id: str | None,
    warehouse: Path | None,
    grantable: set[str] | None,
    dialect: str | None,
    provenance: str,
    served_route: str | None = None,
    abstained: bool = False,
) -> dict[str, Any] | None:
    """Store one checked answer. None when the flag is off or the write is refused.

    A failure here does not change the answer the caller already has.
    """
    if not vq_rag_enabled() or abstained:
        return None
    if not _provenance_ok(provenance):
        return None
    if (served_route or "").strip() in _SKIP_ROUTES:
        return None
    try:
        return _save_example(
            question=question,
            sql=sql,
            space_id=space_id,
            warehouse=warehouse,
            grantable=grantable,
            dialect=dialect,
            provenance=provenance,
        )
    except Exception:
        logger.warning("verified_example_not_stored")
        return None


def _save_example(
    *,
    question: str,
    sql: str,
    space_id: str | None,
    warehouse: Path | None,
    grantable: set[str] | None,
    dialect: str | None,
    provenance: str,
) -> dict[str, Any] | None:
    sid = scope_key(space_id)
    q = (question or "").strip()
    sql_text = (sql or "").strip()
    if not sid or not q or not sql_text:
        return None
    db = warehouse or warehouse_path()
    allowed = grantable if grantable is not None else _grantable(space_id, db)
    gap = serve_gap(sql_text, grantable=allowed, dialect=dialect)
    if gap:
        return None
    if _sql_outside_space(sql_text, allowed):
        return None
    try:
        reject_hostile_chat_sql(sql_text)
    except SecurityEvent:
        return None
    # Before DELETE: a blocked rewrite must not drop the row already stored.
    reject_scored_write({"sql": sql_text, "pack_hash": None}, warehouse=db)
    schema_hash = _schema_hash(db, allowed, dialect) or None
    qn = normalize_verified_question(q)
    asset_id = f"vq_{uuid.uuid4().hex[:16]}"
    created = datetime.now(UTC)
    with _LOCK:
        con = _connect(db, write=True)
        try:
            _ensure(con)
            con.execute(
                f"DELETE FROM {_TABLE} WHERE space_id = ? AND question_norm = ?",
                [sid, qn],
            )
            con.execute(
                f"""
                INSERT INTO {_TABLE}
                  (asset_id, space_id, question, question_norm, sql_text,
                   synonyms_json, created_at, pack_hash, schema_hash,
                   provenance, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                [asset_id, sid, q, qn, sql_text, "[]", created, None, schema_hash, provenance],
            )
        finally:
            con.close()
    return {
        "asset_id": asset_id,
        "space_id": sid,
        "question": q,
        "sql": sql_text,
        "schema_hash": schema_hash or "",
        "provenance": provenance,
        "created_at": created.isoformat(),
    }


def revoke_verified_example(
    *,
    asset_id: str,
    space_id: str,
    path: Path | None = None,
) -> bool:
    """Mark one Space example revoked. A missing row is False."""
    sid = scope_key(space_id)
    token = (asset_id or "").strip()
    if not sid or not token:
        return False
    db = path or warehouse_path()
    with _LOCK:
        con = _connect(db, write=True)
        try:
            _ensure(con)
            updated = con.execute(
                f"""
                UPDATE {_TABLE}
                SET revoked_at = ?
                WHERE asset_id = ? AND space_id = ? AND revoked_at IS NULL
                RETURNING asset_id
                """,
                [datetime.now(UTC), token, sid],
            ).fetchall()
        finally:
            con.close()
    return bool(updated)


def _hit(question: str, row: dict[str, Any]) -> bool:
    qn = normalize_verified_question(question)
    if not qn:
        return False
    if row.get("question_norm") == qn:
        return True
    syn = row.get("synonyms") or []
    return qn in syn if isinstance(syn, list) else False


def lookup_verified_query(
    question: str,
    *,
    space_id: str | None = None,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    tables: list[str] | None = None,
    dialect: str | None = None,
) -> dict[str, str] | None:
    """Return ``{asset_id, sql}`` for a Space hit. Does not run SQL as the answer.

    ``DMS_VQ_RAG`` on: always None. The row is a few-shot example, not a replay.
    """
    if vq_rag_enabled() or tables:
        return None
    sid = scope_key(space_id)
    db = warehouse or warehouse_path()
    match = next(
        (
            row
            for row in _rows_for_space(db, sid)
            if not row.get("revoked_at") and _hit(question, row)
        ),
        None,
    )
    if match is None:
        return None
    sql_text = str(match["sql"])
    allowed = grantable if grantable is not None else _grantable(space_id, db)
    if _sql_outside_space(sql_text, allowed):
        return None
    try:
        reject_hostile_chat_sql(sql_text)
    except SecurityEvent:
        return None
    gap = serve_gap(sql_text, grantable=allowed, dialect=dialect)
    if gap:
        return None
    return {"asset_id": str(match["asset_id"]), "sql": sql_text}


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
    from dms_executor.route_stamp import ROUTE_VERIFIED, stamp_route

    stamp_route(env, ROUTE_VERIFIED)
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
    dialect: str | None = None,
) -> dict[str, Any] | None:
    """L0 envelope when this Space has a matching asset executed via Cortex.

    ``submit`` must be Cortex HTTP submit of the registered SQL. ``ledger_append``
    must be Cortex HTTP ledger. Missing either does not fall back to local DuckDB
    (F83). Grounded-file asks skip this path. Foreign Spaces miss on ``space_id``.
    ``DMS_VQ_RAG`` on: None. Stored SQL is not the answer.
    """
    if vq_rag_enabled():
        return None
    hit = lookup_verified_query(
        question,
        space_id=space_id,
        warehouse=warehouse,
        grantable=grantable,
        tables=tables,
        dialect=dialect,
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
