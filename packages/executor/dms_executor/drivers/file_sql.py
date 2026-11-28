"""Aggregate a local CSV or Parquet file in DuckDB.

The file is registered as a table. The SQL is the aggregation. DuckDB does
not receive an object-store URI. ``read_csv_auto`` and ``read_parquet`` run
only on the temp file this module just wrote, and only for a name that
already passed the identifier check.
"""

from __future__ import annotations

import csv
import io
import re
import threading
from pathlib import Path
from typing import Any

from dms_executor.drivers.limits import ROW_CAP, STATEMENT_TIMEOUT_S

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def object_stem(key: str) -> tuple[str | None, str]:
    """Identifier and suffix for a flat object name. A path is skipped."""
    if "/" in key or "\\" in key:
        return None, ""
    suffix = Path(key).suffix.lower()
    stem = Path(key).stem
    if _IDENT.fullmatch(stem) is None:
        return None, suffix
    return stem, suffix


def sniff_csv(body: bytes) -> list[dict[str, str]]:
    """Header names and a sniffed type. The sample cell is not returned."""
    text = body.decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    sample = rows[1] if len(rows) > 1 else []
    columns: list[dict[str, str]] = []
    for index, name in enumerate(rows[0]):
        cleaned = name.strip()
        if _IDENT.fullmatch(cleaned) is None:
            continue
        kind = "VARCHAR"
        token = sample[index].strip() if index < len(sample) else ""
        if token and _numeric(token):
            kind = "DOUBLE"
        columns.append({"name": cleaned, "type": kind})
    return columns


def sniff_parquet(body: bytes) -> list[dict[str, str]]:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return []
    try:
        schema = pq.read_schema(io.BytesIO(body))
    except Exception:
        return []
    columns: list[dict[str, str]] = []
    for field in schema:
        name = str(field.name)
        if _IDENT.fullmatch(name) is None:
            continue
        columns.append({"name": name, "type": str(field.type)[:80]})
    return columns


def _numeric(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


class FileSqlError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def run_bounded(fn: Any, timeout_s: float) -> Any:
    """Run ``fn`` on a thread. On timeout, return the code ``timeout``."""
    holder: dict[str, Any] = {}

    def work() -> None:
        try:
            holder["value"] = fn()
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        holder["timed_out"] = True
        return holder
    return holder


def aggregate(
    files: dict[str, Path],
    sql: str,
    *,
    timeout_s: float = STATEMENT_TIMEOUT_S,
) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    """Return columns, rows, and whether the row cap cut the result.

    The caller has already refused a file over the byte cap. This function
    does not read past the temp files it is given.
    """
    import duckdb

    for name in files:
        if _IDENT.fullmatch(name) is None:
            raise FileSqlError("bad_table_name")
    con = duckdb.connect(":memory:")
    timed_out = False

    def work() -> tuple[list[str], list[tuple[Any, ...]]]:
        for name, path in files.items():
            suffix = path.suffix.lower()
            if suffix == ".csv":
                reader = "read_csv_auto"
            elif suffix == ".parquet":
                reader = "read_parquet"
            else:
                raise FileSqlError("unsupported_object")
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM {reader}(?)", [str(path)])
        wrapped = f"SELECT * FROM ({sql}) AS _dms_cap LIMIT {ROW_CAP + 1}"
        cursor = con.execute(wrapped)
        description = cursor.description or []
        columns = [str(item[0]) for item in description]
        rows = [tuple(row) for row in cursor.fetchall()]
        return columns, rows

    try:
        holder = run_bounded(work, timeout_s)
        if holder.get("timed_out"):
            timed_out = True
            try:
                con.interrupt()
            except Exception:
                pass
            raise FileSqlError("timeout")
        if "error" in holder:
            err = holder["error"]
            if isinstance(err, FileSqlError):
                raise err
            raise FileSqlError("bad_request") from None
        columns, rows = holder["value"]
    finally:
        try:
            con.close()
        except Exception:
            pass
    if timed_out:
        raise FileSqlError("timeout")
    truncated = len(rows) > ROW_CAP
    return columns, rows[:ROW_CAP], truncated
