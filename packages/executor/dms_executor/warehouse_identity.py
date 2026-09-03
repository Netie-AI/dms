"""One ingest warehouse, one serving warehouse — or an explicit bronze copy.

TAS-DMS §6 (measured 2026-08-02): Studio ingest writes bronze into
``DMS_WAREHOUSE_DB`` (default ``<repo>/data/dms_demo.duckdb``). Cortex answers
from a different file (``CORTEX_HOME/data/dms_demo.duckdb``). An uploaded sheet
is then unreachable from ``POST /v1/chat/ask`` — a silent miss.

These must not become one file by default. ``demo_warehouse`` seeds
``txn_type='outbound'``; the engine warehouse uses ``'OUT'``. Pointing ingest at
the engine file reseeds and drops the engine's extra tables.

Swap scenario for ``CORTEX_WAREHOUSE_DB`` / ``DMS_ORACLE_WAREHOUSE``: when the
engine is pointed at ``DMS_WAREHOUSE_DB`` (Cortex#41 / one-file appliance), both
resolvers return the same path and sync is a no-op. Until then, copy bronze
into the file chat actually reads.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

import duckdb

from dms_executor.bronze import _ensure_registry
from dms_executor.demo_warehouse import warehouse_path
from dms_executor.duckdb_scalar import scalar_int
from dms_executor.lake_schema import ensure_lake_schemas

# Always quoted. Ingest stems keep leading digits (15_q3_sales_export_Q3).
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
# Do NOT reintroduce a DEMO_TABLES filter over bronze. The demo seed lives in the
# serving file's ``main`` schema and this module only ever writes ``bronze.<name>``,
# so the two cannot collide. There used to be one, and a customer table named
# ``transactions`` (or inventory / locations / suppliers / shipments / alerts / meta —
# names real schemas are full of) was dropped by the sync while the receipt still
# reported ``copied`` and Studio still listed it. Chat then answered from the 15-row
# synthetic demo table under a green badge: CLAUDE.md rule 12, exactly.
# Locked by test_a_customer_table_named_like_a_demo_table_is_not_silently_dropped.
_WIN_ENGINE = Path(r"D:\Cortex") / "data" / "dms_demo.duckdb"


class BronzeRow(TypedDict):
    table: str
    row_count: int


def _env_path(*names: str) -> Path | None:
    for name in names:
        raw = os.environ.get(name)
        if raw and raw.strip():
            return Path(raw.strip())
    return None


def ingest_warehouse_path() -> Path:
    """File Studio ingest writes. ``DMS_WAREHOUSE_DB`` or repo ``data/dms_demo.duckdb``."""
    return warehouse_path()


def discovered_engine_warehouse() -> Path | None:
    """File Cortex is expected to read, when it exists on disk.

    Discovery only — never create this file. Used by ``--check`` and the CLI
    default target so a missing env var cannot hide the two-file TAS bug.
    """
    explicit = _env_path("CORTEX_WAREHOUSE_DB", "DMS_ORACLE_WAREHOUSE")
    if explicit is not None:
        return explicit
    home = os.environ.get("CORTEX_HOME")
    if home:
        candidate = Path(home) / "data" / "dms_demo.duckdb"
        if candidate.is_file():
            return candidate
    if _WIN_ENGINE.is_file():
        return _WIN_ENGINE
    return None


def explicit_engine_warehouse() -> Path | None:
    """Serving target only when the operator set it. Tests must not inherit a laptop path."""
    return _env_path("CORTEX_WAREHOUSE_DB", "DMS_ORACLE_WAREHOUSE")


def serving_warehouse_path() -> Path:
    """File chat is configured to read. Falls back to the ingest file (single-warehouse)."""
    return discovered_engine_warehouse() or ingest_warehouse_path()


def paths_are_same(ingest: Path | None = None, serving: Path | None = None) -> bool:
    src = (ingest or ingest_warehouse_path()).resolve()
    dst = (serving or serving_warehouse_path()).resolve()
    return src == dst


def _q(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"refusing non-identifier table name: {name!r}")
    return f'"{name}"'


def list_bronze_readonly(path: Path) -> list[BronzeRow]:
    """Bronze user tables without ``ensure_demo_warehouse`` (must not reseed the engine file)."""
    db = Path(path)
    if not db.is_file():
        return []
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT table_name FROM information_schema.tables
             WHERE table_schema = 'bronze'
               AND table_name NOT LIKE '\\_%' ESCAPE '\\'
             ORDER BY table_name
            """
        ).fetchall()
        out: list[BronzeRow] = []
        for (raw_name,) in rows:
            name = str(raw_name)
            n = scalar_int(con.execute(f"SELECT COUNT(*) FROM bronze.{_q(name)}").fetchone())
            out.append({"table": f"bronze.{name}", "row_count": n})
        return out
    except duckdb.Error:  # missing schema / unreadable file => no bronze
        return []
    finally:
        con.close()


def bronze_missing_from_serving(
    ingest: Path | None = None,
    serving: Path | None = None,
) -> list[str]:
    """Bronze tables (or row counts) in ingest that serving cannot see.

    Same resolved path => nothing missing. This is the S4 regression: ingest
    landing in a DB chat cannot read.
    """
    src = (ingest or ingest_warehouse_path()).resolve()
    dst = (serving or serving_warehouse_path()).resolve()
    if src == dst:
        return []
    have = {t["table"]: t["row_count"] for t in list_bronze_readonly(src)}
    if not have:
        return []
    seen = {t["table"]: t["row_count"] for t in list_bronze_readonly(dst)}
    return [
        name
        for name, n in have.items()
        if seen.get(name) != n
    ]


@dataclass
class SyncResult:
    status: str
    ingest: Path
    serving: Path | None
    copied: list[str] = field(default_factory=list)
    #: Tables the sync deliberately did not copy, with the reason. A skip that is not
    #: reported is indistinguishable from a table that arrived, which is how a
    #: customer's `transactions` upload used to disappear behind a green receipt.
    skipped: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.skipped and self.status in {"same_file", "copied", "nothing_to_copy"}


def identity_check(
    ingest: Path | None = None,
    serving: Path | None = None,
) -> tuple[bool, str]:
    """Return (ok, detail). Fails when ingest bronze is missing from serving."""
    src = (ingest or ingest_warehouse_path()).resolve()
    dst_raw = serving if serving is not None else discovered_engine_warehouse()
    if dst_raw is None:
        return True, f"single warehouse {src}"
    dst = Path(dst_raw).resolve()
    if src == dst:
        return True, f"single warehouse {src}"
    if not dst.is_file():
        bronze = list_bronze_readonly(src)
        if bronze:
            return False, (
                f"ingest {src} has bronze {sorted(t['table'] for t in bronze)} "
                f"but serving {dst} does not exist"
            )
        return True, f"two paths, no bronze yet: ingest={src} serving={dst}"
    missing = bronze_missing_from_serving(src, dst)
    if missing:
        return False, (
            f"ingest bronze not in serving: {missing} "
            f"(ingest={src} serving={dst})"
        )
    return True, f"bronze aligned ingest={src} serving={dst}"


def sync_bronze_to_serving(
    *,
    ingest: Path | None = None,
    serving: Path | None = None,
) -> SyncResult:
    """Copy bronze user tables from ingest DuckDB into the serving DuckDB.

    Copies every bronze user table, including ones whose name matches a demo table —
    the demo seed is in ``main``, this writes ``bronze``, so they cannot collide.
    Does not create a missing serving file (that would invent an engine warehouse).
    Anything not copied is reported in ``SyncResult.skipped``, never dropped silently.
    """
    src = (ingest or ingest_warehouse_path()).resolve()
    dst_raw = serving if serving is not None else discovered_engine_warehouse()
    if dst_raw is None:
        return SyncResult(status="no_engine", ingest=src, serving=None)
    dst = Path(dst_raw).resolve()
    if src == dst:
        return SyncResult(status="same_file", ingest=src, serving=dst)
    if not src.is_file():
        return SyncResult(
            status="ingest_missing", ingest=src, serving=dst, error="ingest warehouse missing"
        )
    if not dst.is_file():
        return SyncResult(
            status="engine_missing",
            ingest=src,
            serving=dst,
            error="serving warehouse missing — will not create it",
        )

    copied: list[str] = []
    skipped: list[str] = []
    con = None
    try:
        # Windows exclusive lock: Cortex holds the serving file. connect()
        # throws before ATTACH, so it must sit inside this try or ingest 500s
        # after bronze already landed.
        con = duckdb.connect(str(dst))
        ensure_lake_schemas(con)
        _ensure_registry(con)
        con.execute(f"ATTACH '{src.as_posix()}' AS ingest_wh (READ_ONLY)")
        names = [
            str(r[0])
            for r in con.execute(
                """
                SELECT table_name FROM information_schema.tables
                 WHERE table_catalog = 'ingest_wh'
                   AND table_schema = 'bronze'
                   AND table_name NOT LIKE '\\_%' ESCAPE '\\'
                """
            ).fetchall()
        ]
        for name in names:
            if not _IDENT.match(name):
                # A skip must never be silent. This used to `continue`, so a table the
                # customer had successfully ingested simply never arrived and the
                # receipt still said "ok".
                skipped.append(f"bronze.{name}: not a bare identifier")
                continue
            q = _q(name)
            # Build beside, then swap inside a transaction. DROP-then-CREATE as two
            # autocommitted statements leaves a window in which an ask sees no table
            # at all, and a mid-loop failure leaves the serving file half-applied.
            stg = _q(f"_sync_{name}")
            con.execute(f"DROP TABLE IF EXISTS bronze.{stg}")
            con.execute(f"CREATE TABLE bronze.{stg} AS SELECT * FROM ingest_wh.bronze.{q}")
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute(f"DROP TABLE IF EXISTS bronze.{q}")
                con.execute(f"ALTER TABLE bronze.{stg} RENAME TO {q}")
            except Exception:  # noqa: BLE001
                con.execute("ROLLBACK")
                con.execute(f"DROP TABLE IF EXISTS bronze.{stg}")
                raise
            con.execute("COMMIT")
            copied.append(f"bronze.{name}")
        # Registry rows travel with the tables so serving-side grants/lists agree.
        reg_n = scalar_int(
            con.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                 WHERE table_catalog = 'ingest_wh'
                   AND table_schema = 'bronze'
                   AND table_name = '_ingest_registry'
                """
            ).fetchone()
        )
        if reg_n > 0 and copied:
            stems = [t.split(".", 1)[1] for t in copied]
            con.execute(
                "DELETE FROM bronze._ingest_registry WHERE table_name IN ({})".format(
                    ", ".join("?" * len(stems))
                ),
                stems,
            )
            con.execute(
                """
                INSERT INTO bronze._ingest_registry
                SELECT * FROM ingest_wh.bronze._ingest_registry
                 WHERE table_name IN ({})
                """.format(", ".join("?" * len(stems))),
                stems,
            )
        if not copied:
            return SyncResult(
                status="nothing_to_copy", ingest=src, serving=dst, skipped=skipped
            )
        return SyncResult(
            status="copied", ingest=src, serving=dst, copied=copied, skipped=skipped
        )
    except Exception as exc:  # noqa: BLE001 — lock / attach must not crash ingest
        err = str(exc)
        busy = "being used by another process" in err or "lock" in err.lower()
        status = "locked" if busy else "error"
        return SyncResult(
            status=status,
            ingest=src,
            serving=dst,
            copied=copied,
            skipped=skipped,
            error=err[:300],
        )
    finally:
        if con is not None:
            con.close()


def maybe_sync_bronze_to_serving(ingest: Path | None = None) -> SyncResult | None:
    """Auto-copy after Studio ingest when an explicit serving path is set.

    Skips under pytest so a laptop ``CORTEX_WAREHOUSE_DB`` cannot receive test
    bronze. Discovery-only paths are not auto-synced — the operator (or the
    start script) must name the serving file.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    if os.environ.get("DMS_SYNC_BRONZE", "1").strip().lower() in {"0", "false", "no"}:
        return None
    dst = explicit_engine_warehouse()
    if dst is None:
        return None
    src = (ingest or ingest_warehouse_path()).resolve()
    if src == dst.resolve():
        return SyncResult(status="same_file", ingest=src, serving=dst.resolve())
    return sync_bronze_to_serving(ingest=src, serving=dst)
