"""Read-only gold and predicted SQL on the source SQLite file.

The connector never writes the customer's file. A write attempt is
``destructive_sql`` and is not executed. There is no row cap and no
statement timeout: ``fetchall`` returns every row the engine produced.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

SAMPLE_ROWS = 3


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _uri(path: Path) -> str:
    return f"file:{quote(str(path.resolve()))}?mode=ro"


def _write_refused(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return "readonly" in text or "read-only" in text


def execute_readonly(path: Path, sql: str) -> tuple[list[tuple[Any, ...]] | None, str | None]:
    """Run one statement. Returns ``(rows, None)`` or ``(None, code)``.

    The code is a short name. The exception text is not returned: SQLite
    sometimes echoes a bound value.
    """
    statement = sql.strip() if isinstance(sql, str) else ""
    if not statement:
        return None, "empty_sql"
    try:
        con = sqlite3.connect(_uri(path), uri=True)
    except sqlite3.Error:
        return None, "sqlite_open"
    try:
        try:
            con.execute("PRAGMA query_only = ON")
            cur = con.execute(statement)
        except sqlite3.Error as exc:
            if _write_refused(exc):
                return None, "destructive_sql"
            return None, "sqlite_error"
        if cur.description is None:
            return None, "no_result"
        return [tuple(row) for row in cur.fetchall()], None
    finally:
        con.close()


def list_tables(path: Path) -> list[str]:
    con = sqlite3.connect(_uri(path), uri=True)
    try:
        con.execute("PRAGMA query_only = ON")
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    finally:
        con.close()
    return [str(row[0]) for row in rows]


def schema_block(path: Path, *, sample_rows: int = SAMPLE_ROWS) -> str:
    """CREATE text plus at most ``sample_rows`` rows per table.

    The sample cap is the baseline prompt. ``sample_rows=0`` is names and
    types only: no ``SELECT`` and no cell values. Scoring does not use this text.
    """
    if sample_rows == 0:
        limit = 0
    elif isinstance(sample_rows, int) and sample_rows > 0:
        limit = sample_rows
    else:
        limit = SAMPLE_ROWS
    con = sqlite3.connect(_uri(path), uri=True)
    try:
        con.execute("PRAGMA query_only = ON")
        names = [
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        chunks: list[str] = []
        for name in names:
            ident = quote_ident(name)
            info = list(con.execute(f"PRAGMA table_info({ident})"))
            cols = [(str(col[1]) + (" " + str(col[2]) if col[2] else "")).strip() for col in info]
            chunks.append(f"CREATE TABLE {ident} ({', '.join(cols)});")
            if not limit:
                continue
            cur = con.execute(f"SELECT * FROM {ident} LIMIT {int(limit)}")
            headers = [item[0] for item in cur.description or []]
            sample = [list(row) for row in cur.fetchall()]
            chunks.append(
                "sample "
                + json.dumps({"columns": headers, "rows": sample}, default=str, ensure_ascii=True)
            )
        return "\n".join(chunks)
    finally:
        con.close()


def locate_db(db_root: Path, db_id: str) -> Path:
    """``{id}/{id}.sqlite`` or a flat ``{id}.sqlite``. Rejects path pieces."""
    if not db_id or db_id in {".", ".."} or "/" in db_id or "\\" in db_id:
        raise ValueError("bad_db_id")
    candidates = (
        db_root / db_id / f"{db_id}.sqlite",
        db_root / db_id / f"{db_id}.sqlite3",
        db_root / f"{db_id}.sqlite",
        db_root / f"{db_id}.sqlite3",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(db_id)


def list_databases(db_root: Path) -> list[tuple[str, Path]]:
    """One entry per database file. Nested ``{id}/{id}.sqlite`` wins over flat."""
    nested: list[tuple[str, Path]] = []
    for path in sorted(db_root.glob("*/*.sqlite")):
        if path.parent.name == path.stem and path.is_file():
            nested.append((path.stem, path))
    if nested:
        return nested
    return [(path.stem, path) for path in sorted(db_root.glob("*.sqlite")) if path.is_file()]


def relative_database(root: Path, db_file: Path) -> str:
    return db_file.resolve().relative_to(root.resolve()).as_posix()
