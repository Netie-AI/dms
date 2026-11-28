"""Import examples and benchmarks. Neither table is the verified-query replay store.

Examples become retrieval rows only after a steward approves them, and only
while the import flag is on. Benchmarks are never read by the ask path.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.semantic_import.flags import enabled

_EXAMPLES = "main._semantic_import_examples"
_BENCHMARKS = "main._semantic_import_benchmarks"
_LOCK = threading.Lock()
_ABSENT: set[str] = set()


def reset_state() -> None:
    """Test hook. Drops the process cache of a missing table."""
    _ABSENT.clear()


def write_examples(
    rows: list[dict[str, Any]],
    *,
    space_id: str,
    warehouse: Path,
    actor: str,
    provenance: str,
) -> list[str]:
    if not rows:
        return []
    _forget(warehouse)
    ids: list[str] = []
    with _LOCK:
        con = _writer(warehouse)
        try:
            _ensure_examples(con)
            for row in rows:
                asset_id = f"imp_{uuid.uuid4().hex[:16]}"
                con.execute(
                    f"""
                    INSERT INTO {_EXAMPLES}
                      (asset_id, space_id, name, ask_text, sql_text,
                       provenance, status, actor, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'candidate', ?, ?)
                    """,
                    [
                        asset_id,
                        space_id,
                        str(row.get("name") or ""),
                        str(row.get("question") or ""),
                        str(row.get("sql") or ""),
                        provenance,
                        actor,
                        datetime.now(UTC),
                    ],
                )
                ids.append(asset_id)
        finally:
            con.close()
    return ids


def write_benchmarks(rows: list[dict[str, Any]], *, space_id: str, warehouse: Path) -> list[str]:
    if not rows:
        return []
    ids: list[str] = []
    with _LOCK:
        con = _writer(warehouse)
        try:
            _ensure_benchmarks(con)
            for row in rows:
                asset_id = f"bench_{uuid.uuid4().hex[:16]}"
                con.execute(
                    f"""
                    INSERT INTO {_BENCHMARKS}
                      (asset_id, space_id, name, ask_text, sql_text, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'stored', ?)
                    """,
                    [
                        asset_id,
                        space_id,
                        str(row.get("name") or ""),
                        str(row.get("question") or ""),
                        str(row.get("sql") or ""),
                        datetime.now(UTC),
                    ],
                )
                ids.append(asset_id)
        finally:
            con.close()
    return ids


def approve_examples(*, space_id: str, actor: str, warehouse: Path) -> int:
    _forget(warehouse)
    with _LOCK:
        con = _writer(warehouse)
        try:
            _ensure_examples(con)
            updated = con.execute(
                f"""
                UPDATE {_EXAMPLES}
                SET status = 'approved', actor = ?
                WHERE space_id = ? AND status = 'candidate'
                RETURNING asset_id
                """,
                [actor, space_id],
            ).fetchall()
        finally:
            con.close()
    return len(updated)


def example_rows(warehouse: Path, space_id: str) -> list[dict[str, Any]]:
    return _read(warehouse, space_id, _EXAMPLES, _ensure_examples)


def benchmark_rows(warehouse: Path, space_id: str) -> list[dict[str, Any]]:
    return _read(warehouse, space_id, _BENCHMARKS, _ensure_benchmarks)


def approved_for_prompt(
    ask_text: str,
    *,
    space_id: str,
    warehouse: Path,
    grantable: set[str],
    dialect: str | None,
) -> list[dict[str, Any]]:
    """Few-shot rows. Empty unless the flag is on and a steward approved them."""
    if not enabled():
        return []
    key = f"{warehouse}:{space_id}"
    if key in _ABSENT:
        return []
    try:
        rows = _approved(warehouse, space_id)
    except Exception:
        return []
    if rows is None:
        _ABSENT.add(key)
        return []
    if not rows:
        return []
    from dms_executor.grant_struct import serve_gap

    kept = [
        row
        for row in rows
        if serve_gap(str(row.get("sql") or ""), grantable=set(grantable), dialect=dialect) is None
    ]
    if not kept:
        return []
    from dms_executor.verified_queries import rank_texts

    ranked = rank_texts(
        ask_text,
        [str(row.get("question") or "") for row in kept],
        None,
        limit=3,
        floor=0.0,
    )
    return [_prompt_row(kept[index]) for _score, index in ranked]


def _prompt_row(row: dict[str, Any]) -> dict[str, Any]:
    """Same public shape as a retrieved verified example. Not a stored answer."""
    return {
        "asset_id": str(row.get("asset_id") or ""),
        "question": str(row.get("question") or ""),
        "sql": str(row.get("sql") or ""),
        "schema_hash": "",
        "provenance": str(row.get("provenance") or ""),
    }


def _approved(warehouse: Path, space_id: str) -> list[dict[str, Any]] | None:
    """Approved rows, or None when the table has not been created."""
    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(Path(warehouse))
    try:
        if not _exists(con, "_semantic_import_examples"):
            return None
        fetched = con.execute(
            f"""
            SELECT asset_id, name, ask_text, sql_text, provenance, status
            FROM {_EXAMPLES}
            WHERE space_id = ? AND status = 'approved'
            """,
            [space_id],
        ).fetchall()
    finally:
        con.close()
    return [_public(row) for row in fetched]


def _read(warehouse: Path, space_id: str, table: str, ensure: Any) -> list[dict[str, Any]]:
    del ensure
    from dms_executor.demo_warehouse import connect_serving

    bare = table.split(".")[-1]
    con = connect_serving(Path(warehouse))
    try:
        if not _exists(con, bare):
            return []
        fetched = con.execute(
            f"""
            SELECT asset_id, name, ask_text, sql_text, provenance, status
            FROM {table}
            WHERE space_id = ?
            """,
            [space_id],
        ).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    return [_public(row) for row in fetched]


def _public(row: tuple[Any, ...]) -> dict[str, Any]:
    provenance = row[4] if len(row) > 4 else ""
    return {
        "asset_id": str(row[0] or ""),
        "name": str(row[1] or ""),
        "question": str(row[2] or ""),
        "sql": str(row[3] or ""),
        "provenance": str(provenance or ""),
        "status": str(row[5] or "") if len(row) > 5 else "",
    }


def _writer(warehouse: Path) -> duckdb.DuckDBPyConnection:
    from dms_executor.demo_warehouse import connect_file

    return connect_file(Path(warehouse), write=True)


def _ensure_examples(con: duckdb.DuckDBPyConnection) -> None:
    from dms_executor.lake_schema import catalog_lock

    with catalog_lock:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_EXAMPLES} (
              asset_id VARCHAR PRIMARY KEY,
              space_id VARCHAR NOT NULL,
              name VARCHAR,
              ask_text VARCHAR,
              sql_text VARCHAR,
              provenance VARCHAR,
              status VARCHAR,
              actor VARCHAR,
              created_at TIMESTAMPTZ
            )
            """
        )


def _ensure_benchmarks(con: duckdb.DuckDBPyConnection) -> None:
    from dms_executor.lake_schema import catalog_lock

    with catalog_lock:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_BENCHMARKS} (
              asset_id VARCHAR PRIMARY KEY,
              space_id VARCHAR NOT NULL,
              name VARCHAR,
              ask_text VARCHAR,
              sql_text VARCHAR,
              provenance VARCHAR,
              status VARCHAR,
              created_at TIMESTAMPTZ
            )
            """
        )


def _exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    found = con.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE lower(table_name) = ?
        """,
        [table.lower()],
    ).fetchone()
    return found is not None


def _forget(warehouse: Path) -> None:
    prefix = f"{warehouse}:"
    for key in [item for item in _ABSENT if item.startswith(prefix)]:
        _ABSENT.discard(key)
