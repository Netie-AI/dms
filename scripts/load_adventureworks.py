"""Restore SQL Server .bak backups and land them in the lake, with validity asserted.

Why this exists
---------------
The demo warehouse is six hand-generated tables with a single fan-out hazard. It
was enough to find the ~15x inflation bug, and it is not enough to claim anything
about a product that has to survive a real schema. AdventureWorks is a real
normalised OLTP schema (71 tables, composite keys, genuine many-to-many links)
shipped alongside a real star schema in AdventureWorksDW, which makes it the
cheapest honest test of whether the semantic layer holds up on data nobody here
shaped to be convenient.

A ``.bak`` is a SQL Server backup, not a file format anything else reads. There
is no way to open one without SQL Server, so this starts a throwaway container,
restores into it, and extracts to Parquet. The container is a build tool, not a
runtime dependency: nothing in DMS talks to SQL Server after this script exits.

What "check validity" means here
--------------------------------
Not "did the rows arrive". Row counts agreeing tells you the copy worked, which
is the least interesting thing that can be true. The checks that matter are the
ones a semantic layer will later *rely on*, because a declaration that is trusted
and false is how a metric silently inflates:

  row_count      the copy is complete           - table level
  pk_unique      a declared key is really unique - the "one" side of every join
  fk_intact      no orphaned foreign keys        - joins do not silently drop rows
  null_profile   which columns are nullable in practice, not in the DDL

``pk_unique`` is the important one, and it is where this goes further than the
vendors. Databricks' metric views let you declare ``rely.at_most_one_match`` and
their own documentation says it is "not validated at runtime. If the join
produces a fan-out, measures return incorrect results." A declaration nobody
checks is a comment. Here the check runs, and a failure is loud.

  python scripts/load_adventureworks.py --restore
  python scripts/load_adventureworks.py --extract
  python scripts/load_adventureworks.py --validate
  python scripts/load_adventureworks.py --all

Exit 0 only when every requested stage completed and every validity check held.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LAKE = ROOT / "data" / "lake"
REPORTS = ROOT / "data" / "lake" / "_reports"

CONTAINER = os.environ.get("AW_CONTAINER", "dms-mssql-adventureworks")
# SQL Server restores forward, never backward: an engine can read a backup taken
# on its own version or older, and refuses anything newer with error 3169. The
# AdventureWorks*2025 backups carry database version 998 (SQL Server 2025), so a
# 2022 image (version 958) cannot read them, while a 2025 image reads all three.
# Pin the newest, not the oldest.
IMAGE = os.environ.get("AW_IMAGE", "mcr.microsoft.com/mssql/server:2025-latest")
_DB_VERSION = {998: "2025", 957: "2022", 904: "2019", 869: "2017", 852: "2016"}
PORT = int(os.environ.get("AW_PORT", "14330"))
# A throwaway local container needs a password that satisfies SQL Server's policy.
# It is a build tool with no data of ours in it; override if the default offends.
SA_PASSWORD = os.environ.get("AW_SA_PASSWORD", "Dms_Local_Restore_2026!")

BACKUP_DIR = Path(os.environ.get("AW_BACKUP_DIR", str(Path.home() / "Downloads")))
DATABASES = [
    ("AdventureWorks2025", "AdventureWorks2025.bak"),
    ("AdventureWorksDW2025", "AdventureWorksDW2025.bak"),
    ("AdventureWorksLT2022", "AdventureWorksLT2022.bak"),
]


class StageFailed(Exception):
    """A stage did not complete. Never swallowed - R-0002."""


def _backup_mounts() -> list[str]:
    """One read-only bind per backup file, never the whole directory."""
    mounts: list[str] = []
    missing: list[str] = []
    for _db, bak in DATABASES:
        src = BACKUP_DIR / bak
        if not src.is_file():
            missing.append(str(src))
            continue
        mounts.append("-v")
        mounts.append(f"{src.as_posix()}:/backups/{bak}:ro")
    if missing:
        raise StageFailed(
            "backup files not found: "
            + "; ".join(missing)
            + " - set AW_BACKUP_DIR if they live elsewhere."
        )
    return mounts


# --------------------------------------------------------------------------
# container
# --------------------------------------------------------------------------


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )


def container_state() -> str:
    got = _docker("inspect", "-f", "{{.State.Status}}", CONTAINER, timeout=30)
    return got.stdout.strip() if got.returncode == 0 else "absent"


def ensure_container() -> None:
    """Start the restore container, mounting the backup directory read-only."""
    state = container_state()
    if state == "running":
        print(f"  container {CONTAINER} already running")
    elif state in {"exited", "created", "paused"}:
        print(f"  container {CONTAINER} is {state}; starting")
        got = _docker("start", CONTAINER)
        if got.returncode != 0:
            raise StageFailed(f"docker start failed: {got.stderr.strip()[:300]}")
    else:
        print(f"  creating {CONTAINER} from {IMAGE} on port {PORT}")
        got = _docker(
            "run", "-d", "--name", CONTAINER,
            "-e", "ACCEPT_EULA=Y",
            "-e", f"MSSQL_SA_PASSWORD={SA_PASSWORD}",
            "-e", "MSSQL_PID=Developer",
            "-p", f"{PORT}:1433",
            # Mount the three backups individually, read-only, rather than the
            # directory that holds them. The default backup directory is a user's
            # Downloads folder: bind-mounting it hands the container every
            # unrelated file in there, including any .env someone happened to
            # save. Narrow the blast radius to the files this actually needs.
            *_backup_mounts(),
            IMAGE,
            timeout=300,
        )
        if got.returncode != 0:
            raise StageFailed(f"docker run failed: {got.stderr.strip()[:400]}")

    _wait_for_sql()


def _wait_for_sql(attempts: int = 180) -> None:
    """SQL Server takes tens of seconds to accept connections on first boot.

    The 2025 image takes well over two minutes on a cold start - the first
    --all run failed here at 120s with the lake still unbuilt. Six minutes is
    a property of the image, not of any one run (R-0004).
    """
    import pymssql

    last = ""
    for i in range(attempts):
        try:
            conn = pymssql.connect(
                server="127.0.0.1", port=str(PORT), user="sa",
                password=SA_PASSWORD, timeout=5, login_timeout=5,
            )
            conn.close()
            print(f"  sql server accepting connections after {i * 2}s")
            return
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {str(exc)[:120]}"
            time.sleep(2)
    raise StageFailed(f"sql server never came up after {attempts * 2}s - {last}")


def connect(database: str = "master") -> Any:
    """Always autocommit. RESTORE cannot run inside a transaction.

    pymssql opens an implicit transaction by default, and a RESTORE issued
    inside one does not error - it blocks, which reads as a hung script with no
    request visible in sys.dm_exec_requests. Set at the connection rather than
    at the one call site that first hit it, because every DDL statement here has
    the same constraint (R-0004).
    """
    import pymssql

    conn = pymssql.connect(
        server="127.0.0.1", port=str(PORT), user="sa",
        password=SA_PASSWORD, database=database, timeout=600, login_timeout=30,
    )
    conn.autocommit(True)
    return conn


def _rows(sql: str, database: str = "master") -> list[tuple[Any, ...]]:
    conn = connect(database)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return list(cur.fetchall())
    finally:
        conn.close()


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------


def restore_one(db: str, bak: str) -> None:
    """RESTORE with MOVE, deriving the logical file names rather than guessing them.

    Backups carry their own logical file names, and they differ between these
    three files. Hardcoding ``AdventureWorks2022_Data`` works until it does not,
    and the failure is a restore error rather than a wrong number, so it is a
    safe thing to derive.
    """
    src = BACKUP_DIR / bak
    if not src.is_file():
        raise StageFailed(f"backup not found: {src}")

    existing = _rows(f"SELECT COUNT(*) FROM sys.databases WHERE name = '{db}'")
    if existing and existing[0][0]:
        print(f"  {db:<22} already restored")
        return

    files = _rows(f"RESTORE FILELISTONLY FROM DISK = '/backups/{bak}'")
    if not files:
        raise StageFailed(f"{bak}: FILELISTONLY returned nothing - unreadable backup")

    moves = []
    for row in files:
        logical, ftype = str(row[0]), str(row[2]).upper()
        ext = "mdf" if ftype == "D" else "ldf"
        safe = "".join(c for c in logical if c.isalnum() or c in "._-")
        moves.append(f"MOVE '{logical}' TO '/var/opt/mssql/data/{safe}.{ext}'")

    sql = (
        f"RESTORE DATABASE [{db}] FROM DISK = '/backups/{bak}' WITH "
        + ", ".join(moves)
        + ", REPLACE, STATS = 25"
    )
    print(f"  {db:<22} restoring {src.stat().st_size / 1e6:.0f} MB "
          f"({len(files)} logical files)")
    conn = connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
        except Exception as exc:  # noqa: BLE001
            raise StageFailed(_explain_restore_error(db, bak, exc)) from exc
        while cur.nextset():
            pass
    finally:
        conn.close()
    print(f"  {db:<22} restored")


def _explain_restore_error(db: str, bak: str, exc: Exception) -> str:
    """Turn error 3169 into the sentence that says what to do about it.

    A raw pymssql traceback about "database version 998" tells a reader nothing
    actionable. The number is a SQL Server release, the fix is one environment
    variable, and a gate that knows both should say so rather than making the
    next person search for it.
    """
    text = str(exc)
    if "3169" not in text and "incompatible" not in text.lower():
        return f"{db}: restore failed - {text[:400]}"
    import re as _re

    found = _re.findall(r"version (\d{3,4})", text)
    backup_v, engine_v = (found + ["?", "?"])[:2]
    made_on = _DB_VERSION.get(int(backup_v), f"database version {backup_v}") \
        if backup_v.isdigit() else backup_v
    running = _DB_VERSION.get(int(engine_v), f"database version {engine_v}") \
        if engine_v.isdigit() else engine_v
    return (
        f"{db}: {bak} was taken on SQL Server {made_on}, and this container runs "
        f"SQL Server {running}. SQL Server restores forward only. Use a newer "
        f"image and recreate the container:\n"
        f"    docker rm -f {CONTAINER}\n"
        f"    AW_IMAGE=mcr.microsoft.com/mssql/server:{made_on}-latest "
        f"python scripts/load_adventureworks.py --restore"
    )


def stage_restore() -> int:
    print("=== RESTORE ===")
    ensure_container()
    for db, bak in DATABASES:
        restore_one(db, bak)
    got = _rows("SELECT name FROM sys.databases WHERE database_id > 4 ORDER BY name")
    print(f"  databases now present: {', '.join(r[0] for r in got)}")
    return 0


# --------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------


def user_tables(db: str) -> list[tuple[str, str, int]]:
    """(schema, table, row_count) for every user table, from catalog views.

    Row count comes from sys.partitions rather than COUNT(*): it is the number
    the source itself believes, so comparing the extracted count against it is a
    real check rather than the same query run twice.
    """
    return [
        (str(s), str(t), int(n))
        for s, t, n in _rows(
            """
            SELECT s.name, t.name, SUM(p.rows)
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
            WHERE t.is_ms_shipped = 0
            GROUP BY s.name, t.name
            ORDER BY s.name, t.name
            """,
            db,
        )
    ]


def schema_metadata(db: str) -> dict[str, Any]:
    """Primary keys and foreign keys, taken from the source catalog.

    Captured at extract time on purpose. Once the data is Parquet in a lake, the
    relationships are gone - a Parquet file knows its columns and nothing about
    what they mean or what they point at. Every semantic layer worth the name is
    built on exactly this metadata, and inferring it later from column names is
    how a join gets invented.

    Note these are the source's *declarations*. That is not the same as them
    being true of the extracted data, which is what validate_lake.py is for.
    """
    pks: dict[str, list[str]] = {}
    for schema, table, col in _rows(
        """
        SELECT s.name, t.name, c.name
        FROM sys.key_constraints kc
        JOIN sys.tables t ON t.object_id = kc.parent_object_id
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        JOIN sys.index_columns ic ON ic.object_id = t.object_id
                                 AND ic.index_id = kc.unique_index_id
        JOIN sys.columns c ON c.object_id = t.object_id AND c.column_id = ic.column_id
        WHERE kc.type = 'PK' AND t.is_ms_shipped = 0
        ORDER BY s.name, t.name, ic.key_ordinal
        """,
        db,
    ):
        pks.setdefault(f"{schema}.{table}", []).append(str(col))

    fks: list[dict[str, Any]] = []
    for name, ps, pt, pc, rs, rt, rc in _rows(
        """
        SELECT fk.name, ps.name, pt.name, pc.name, rs.name, rt.name, rc.name
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
        JOIN sys.tables pt ON pt.object_id = fkc.parent_object_id
        JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
        JOIN sys.columns pc ON pc.object_id = pt.object_id
                           AND pc.column_id = fkc.parent_column_id
        JOIN sys.tables rt ON rt.object_id = fkc.referenced_object_id
        JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
        JOIN sys.columns rc ON rc.object_id = rt.object_id
                           AND rc.column_id = fkc.referenced_column_id
        ORDER BY fk.name, fkc.constraint_column_id
        """,
        db,
    ):
        fks.append(
            {
                "name": str(name),
                "from_table": f"{ps}.{pt}",
                "from_column": str(pc),
                "to_table": f"{rs}.{rt}",
                "to_column": str(rc),
            }
        )
    return {"primary_keys": pks, "foreign_keys": fks}


def extract_one(db: str) -> dict[str, Any]:
    import pandas as pd

    out_dir = LAKE / db
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = user_tables(db)
    print(f"  {db:<22} {len(tables)} user tables")

    written: list[dict[str, Any]] = []
    skipped: list[str] = []
    conn = connect(db)
    try:
        for schema, table, declared in tables:
            target = out_dir / f"{schema}.{table}.parquet"
            try:
                frame = pd.read_sql(f'SELECT * FROM [{schema}].[{table}]', conn)
            except Exception as exc:  # noqa: BLE001
                # R-0011: a skipped table is stated, never silently absent. Some
                # AdventureWorks columns are spatial or hierarchyid types that do
                # not cross the wire; the table is named so nobody later reads the
                # lake as complete.
                skipped.append(f"{schema}.{table} ({type(exc).__name__}: {str(exc)[:80]})")
                continue
            frame.to_parquet(target, index=False)
            written.append(
                {
                    "schema": schema,
                    "table": table,
                    "declared_rows": declared,
                    "extracted_rows": int(len(frame)),
                    "columns": int(frame.shape[1]),
                    "path": str(target.relative_to(ROOT)),
                }
            )
    finally:
        conn.close()

    meta = schema_metadata(db)
    print(f"  {db:<22} wrote {len(written)} parquet files, "
          f"{len(meta['primary_keys'])} PKs, {len(meta['foreign_keys'])} FK columns"
          + (f", SKIPPED {len(skipped)}" if skipped else ""))
    for s in skipped:
        print(f"       skipped: {s}")
    return {"database": db, "tables": written, "skipped": skipped, **meta}


def stage_extract() -> int:
    print("=== EXTRACT ===")
    ensure_container()
    LAKE.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    manifests = []
    for db, _ in DATABASES:
        present = _rows(f"SELECT COUNT(*) FROM sys.databases WHERE name = '{db}'")
        if not (present and present[0][0]):
            raise StageFailed(f"{db} is not restored; run --restore first")
        manifests.append(extract_one(db))
    (REPORTS / "extract_manifest.json").write_text(
        json.dumps(manifests, indent=2), encoding="utf-8"
    )
    total = sum(len(m["tables"]) for m in manifests)
    skipped = sum(len(m["skipped"]) for m in manifests)
    print(f"  total {total} tables in the lake, {skipped} skipped")
    print(f"  manifest: {(REPORTS / 'extract_manifest.json').relative_to(ROOT)}")
    return 1 if skipped and os.environ.get("AW_STRICT") else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--restore", action="store_true", help="start the container and restore")
    ap.add_argument("--extract", action="store_true", help="pull every table to parquet")
    ap.add_argument("--validate", action="store_true", help="assert the lake is sound")
    ap.add_argument("--all", action="store_true", help="restore, extract, validate")
    ap.add_argument("--stop", action="store_true", help="stop the restore container")
    args = ap.parse_args(argv)

    if args.stop:
        print(_docker("stop", CONTAINER).stdout.strip() or "not running")
        return 0
    if not any([args.restore, args.extract, args.validate, args.all]):
        ap.error("pick a stage: --restore, --extract, --validate or --all")

    try:
        if args.restore or args.all:
            rc = stage_restore()
            if rc:
                return rc
        if args.extract or args.all:
            rc = stage_extract()
            if rc:
                return rc
        if args.validate or args.all:
            from validate_lake import stage_validate  # noqa: PLC0415

            rc = stage_validate()
            if rc:
                return rc
    except StageFailed as exc:
        print(f"\nFAIL {exc}")
        return 2
    print("\nPASS every requested stage completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
