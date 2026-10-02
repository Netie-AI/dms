"""Red-team data builder: three byte-equivalent DuckDB copies per family.

``dms``    the DMS lake: what the real Executor reads (grants, EXPLAIN, ontology verify).
``cortex`` what the stub Cortex ``submit`` executes model SQL on (the engine's file).
``gold``   pristine, only ever opened read_only: the reference the grader compares to.

All three are ``ensure_demo_warehouse`` seed + the family's optional extension SQL
(INSERT/UPDATE/DELETE/ALTER on the six demo tables only, synthetic rows) and, when
a sheet lane is wanted, the Finance workbooks ingested offline.

``ensure_demo_warehouse`` DROPs and reseeds the six tables on the FIRST call per
path per process, so the extension is applied after that first call and the lake
path is then in the process's seeded set: later asks do not reseed it. One
process per family keeps that property simple.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from sqlglot import expressions as exp

SIX_TABLES = ("locations", "suppliers", "inventory", "shipments", "alerts", "transactions")
FINANCE_SPACE_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_ENV_SCRUB = ("DATABASE_URL", "CORTEX_WAREHOUSE_DB", "DMS_ORACLE_WAREHOUSE", "CORTEX_HOME")

#: Statement kinds an extension may use. Everything else (CREATE, DROP, COPY, ATTACH,
#: INSTALL, LOAD, PRAGMA, SET, a bare Command) is refused.
_ALLOWED_STATEMENTS = ("Insert", "Update", "Delete", "Alter", "AlterTable")
_BLOCKED_WORDS = re.compile(
    r"\b(attach|detach|copy|install|load|pragma|export|import|read_csv|read_parquet|read_json|"
    r"glob|create|drop|truncate|call|checkpoint)\b",
    re.IGNORECASE,
)
_WAL_SUFFIXES = (".wal",)


class ExtensionSqlError(ValueError):
    """The extension SQL file is not allowed. Raised at build time, never swallowed."""


@dataclass
class FamilyDb:
    family: str
    root: Path
    dms: Path
    cortex: Path
    gold: Path
    ext_sql_path: Path | None = None
    ext_statements: int = 0
    with_sheets: bool = False
    table_counts: dict[str, int] = field(default_factory=dict)
    gold_sha256: str = ""
    sheet_tables: tuple[str, ...] = ()

    def header(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "ext_sql": str(self.ext_sql_path) if self.ext_sql_path else None,
            "ext_statements": self.ext_statements,
            "with_sheets": self.with_sheets,
            "table_counts": self.table_counts,
            "gold_sha256": self.gold_sha256,
            "sheet_tables": list(self.sheet_tables),
        }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def split_sql_statements(text: str) -> list[str]:
    """Split on ``;`` outside single quotes, dropping ``--`` comments."""
    out: list[str] = []
    buf: list[str] = []
    in_str = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    buf.append("'")
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == "-" and text[i : i + 2] == "--":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def check_extension_sql(statements: list[str]) -> None:
    """Refuse any extension statement that is not DML/ALTER on the six demo tables."""
    for n, stmt in enumerate(statements, start=1):
        tag = f"extension statement {n}: {stmt[:80]!r}"
        if _BLOCKED_WORDS.search(re.sub(r"'(?:[^']|'')*'", "''", stmt)):
            raise ExtensionSqlError(f"{tag}: contains a blocked keyword")
        try:
            parsed = sqlglot.parse_one(stmt, read="duckdb")
        except Exception as exc:  # noqa: BLE001
            raise ExtensionSqlError(f"{tag}: does not parse ({type(exc).__name__})") from exc
        kind = type(parsed).__name__
        if kind not in _ALLOWED_STATEMENTS:
            raise ExtensionSqlError(
                f"{tag}: {kind} is not allowed; only INSERT/UPDATE/DELETE/ALTER on {SIX_TABLES}"
            )
        ctes = {c.alias_or_name.lower() for c in parsed.find_all(exp.CTE)}
        tables = list(parsed.find_all(exp.Table))
        if not tables:
            raise ExtensionSqlError(f"{tag}: names no table")
        for t in tables:
            name = (t.name or "").lower()
            if not isinstance(t.this, exp.Identifier):
                raise ExtensionSqlError(f"{tag}: table function or expression is not allowed")
            if t.args.get("db") or t.args.get("catalog"):
                raise ExtensionSqlError(f"{tag}: schema/catalog-qualified table {t.sql()!r}")
            if name in ctes:
                continue
            if name not in SIX_TABLES:
                raise ExtensionSqlError(
                    f"{tag}: table {name!r} is outside the six demo tables {SIX_TABLES}"
                )


def table_counts(path: Path, tables: tuple[str, ...] = SIX_TABLES) -> dict[str, int]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return {t: int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables}
    finally:
        con.close()


def run_gold(
    gold_path: Path, sql: str
) -> tuple[list[dict[str, Any]] | None, str | None, list[str] | None]:
    """Run a gold SELECT read-only. Returns (rows, error, column_names)."""
    if not re.match(r"^\s*\(*\s*(select|with)\b", sql or "", re.IGNORECASE):
        return None, "not_select", None
    try:
        con = duckdb.connect(str(gold_path), read_only=True)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}:{exc}"[:240], None
    try:
        cur = con.execute(sql)
        cols = [str(d[0]) for d in cur.description or []]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        return rows, None, cols
    except Exception as exc:  # noqa: BLE001
        msg = " ".join(str(exc).split())[:240]
        return None, f"{type(exc).__name__}:{msg}", None
    finally:
        con.close()


@contextmanager
def scrubbed_env() -> Iterator[None]:
    """No serving-file env, no Postgres, no bronze sync: ingest writes only the scratch lake."""
    saved = {k: os.environ.get(k) for k in (*_ENV_SCRUB, "DMS_SYNC_BRONZE")}
    try:
        for k in _ENV_SCRUB:
            os.environ.pop(k, None)
        os.environ["DMS_SYNC_BRONZE"] = "0"
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _fixture_workbooks(repo: Path) -> list[tuple[str, bytes]]:
    fx = repo / "tests" / "fixtures"
    files = [(p.name, p.read_bytes()) for p in sorted((fx / "hostile_score").glob("*.xlsx"))]
    q3 = fx / "ingest" / "15_q3_sales_export.xlsx"
    if q3.is_file():
        files.append((q3.name, q3.read_bytes()))
    return files


def _apply_extension(dms: Path, statements: list[str]) -> None:
    from dms_executor.demo_warehouse import connect_file

    con = connect_file(dms)
    try:
        for n, stmt in enumerate(statements, start=1):
            try:
                con.execute(stmt)
            except Exception as exc:  # noqa: BLE001
                raise ExtensionSqlError(
                    f"extension statement {n} failed: {type(exc).__name__}: "
                    f"{' '.join(str(exc).split())[:200]} :: {stmt[:100]!r}"
                ) from exc
        con.execute("CHECKPOINT")
    finally:
        con.close()


def build_family_db(
    family: str,
    ext_sql_path: str | Path | None,
    root: str | Path,
    *,
    with_sheets: bool = False,
    repo: str | Path | None = None,
) -> FamilyDb:
    """Build ``dms`` / ``cortex`` / ``gold`` for one family under a fresh directory.

    ``ext_sql_path`` None means seed only. A fresh unique subdirectory is made
    under ``root`` every call, so nothing is ever overwritten or reused.
    """
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"rt-{family}-", dir=str(base)))
    dms = work / "dms.duckdb"
    ensure_demo_warehouse(dms)  # first call this process: DROP + reseed. Extension goes AFTER.

    statements: list[str] = []
    ext = Path(ext_sql_path) if ext_sql_path else None
    if ext is not None:
        if not ext.is_file():
            raise ExtensionSqlError(f"extension SQL file not found: {ext}")
        statements = split_sql_statements(ext.read_text(encoding="utf-8"))
        check_extension_sql(statements)  # fails loudly BEFORE anything is applied
        _apply_extension(dms, statements)

    sheet_tables: tuple[str, ...] = ()
    if with_sheets:
        from dms_executor.batch_ingest import ingest_batch
        from dms_executor.bronze import list_bronze_tables

        repo_root = Path(repo) if repo else Path(__file__).resolve().parents[2]
        files = _fixture_workbooks(repo_root)
        with scrubbed_env():
            ingest_batch(files, path=dms, space_id=FINANCE_SPACE_ID, database_url="")
            sheet_tables = tuple(
                t["table"] for t in list_bronze_tables(path=dms, space_id=FINANCE_SPACE_ID)
            )

    for suffix in _WAL_SUFFIXES:
        leftover = dms.with_name(dms.name + suffix)
        if leftover.exists():
            raise RuntimeError(f"unexpected {leftover.name}: lake not checkpointed, copy unsafe")
    cortex = work / "cortex.duckdb"
    gold = work / "gold.duckdb"
    shutil.copyfile(dms, cortex)
    shutil.copyfile(dms, gold)
    return FamilyDb(
        family=family,
        root=work,
        dms=dms,
        cortex=cortex,
        gold=gold,
        ext_sql_path=ext,
        ext_statements=len(statements),
        with_sheets=with_sheets,
        table_counts=table_counts(gold),
        gold_sha256=sha256_file(gold),
        sheet_tables=sheet_tables,
    )
