"""FDE-SCENARIOS-01: multi-company, multi-engine eval packs for forward deployment.

Five synthetic companies (``tests/fde_scenarios/scenarios.py``) with 15-25 hard,
human-phrased questions each (``tests/fde_scenarios/<company>/questions.yaml``) and
gold answers (``answers.json``) derived from gold SQL on the DuckDB serving dialect.

Modes (exit 0 PASS, 1 FAIL, 2 CONFIG, 3 BLOCKED):

  # Stack-free, CI: rebuild every dataset in DuckDB, run every gold SQL, compare
  # with the frozen answers. Proves the fixtures, not DMS.
  python scripts/verify/fde_scenarios.py --self-test

  # Re-derive answers.json after an intentional dataset/question change.
  python scripts/verify/fde_scenarios.py --freeze

  # Load the companies into a real engine (one database per company, fde_<company>).
  python scripts/verify/fde_scenarios.py --load postgresql --host 127.0.0.1 --port 5432 --user U
  python scripts/verify/fde_scenarios.py --load mysql --host 127.0.0.1 --port 3306 --user U
  python scripts/verify/fde_scenarios.py --load sqlite --out-dir /tmp/fde

  # Extract fidelity: pull each company back out through DMS's own connector
  # (dms_executor.db_connector, extract-only) into a scratch bronze warehouse, then
  # run the gold SQL there. Gold rows must equal the frozen answers. Measures that a
  # source engine lands without changing an answer (types, decimals, dates, keys).
  python scripts/verify/fde_scenarios.py --fidelity postgresql --host ... --user ...

  # Live: ask a running DMS each question in a Space that holds that company's data
  # and grade the envelope against gold rows. Real numbers only; unset config is
  # CONFIG, never PASS.
  DMS_API_BASE=http://127.0.0.1:8090 python scripts/verify/fde_scenarios.py \
      --live --spaces spaces.json [--company retail] [--json out.json]

The source password is read only from ``FDE_SOURCE_PASSWORD`` and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = ROOT / "tests" / "fde_scenarios"
SCRIPTS = ROOT / "scripts"
for p in (str(PACK_DIR), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import scenarios  # noqa: E402
from scenarios import COMPANIES, Dataset, Table  # noqa: E402

EXIT_PASS, EXIT_FAIL, EXIT_CONFIG, EXIT_BLOCKED = 0, 1, 2, 3
ANSWERABLE = "answer"
REFUSE_EXPECT = frozenset({"clarify", "refuse"})

_DDL: dict[str, dict[str, str]] = {
    "duckdb": {
        "int": "BIGINT",
        "text": "VARCHAR",
        "date": "DATE",
        "ts": "TIMESTAMP",
        "dec": "DECIMAL(12,2)",
        "rate": "DECIMAL(12,6)",
    },
    "sqlite": {
        "int": "INTEGER",
        "text": "TEXT",
        "date": "DATE",
        "ts": "TIMESTAMP",
        "dec": "NUMERIC(12,2)",
        "rate": "NUMERIC(12,6)",
    },
    "postgresql": {
        "int": "BIGINT",
        "text": "VARCHAR(200)",
        "date": "DATE",
        "ts": "TIMESTAMP",
        "dec": "NUMERIC(12,2)",
        "rate": "NUMERIC(12,6)",
    },
    "mysql": {
        "int": "BIGINT",
        "text": "VARCHAR(200)",
        "date": "DATE",
        "ts": "DATETIME",
        "dec": "DECIMAL(12,2)",
        "rate": "DECIMAL(12,6)",
    },
    "sqlserver": {
        "int": "BIGINT",
        "text": "NVARCHAR(200)",
        "date": "DATE",
        "ts": "DATETIME2",
        "dec": "DECIMAL(12,2)",
        "rate": "DECIMAL(12,6)",
    },
}


def _q(engine: str, ident: str) -> str:
    if engine == "mysql":
        return "`" + ident.replace("`", "``") + "`"
    if engine == "sqlserver":
        return "[" + ident.replace("]", "]]") + "]"
    return '"' + ident.replace('"', '""') + '"'


def create_sql(engine: str, t: Table, *, with_fks: bool = True) -> str:
    types = _DDL[engine]
    cols = [f"{_q(engine, c)} {types[k]}" for c, k in t.columns]
    cols.append("PRIMARY KEY (" + ", ".join(_q(engine, c) for c in t.pk) + ")")
    if with_fks:
        for col, ref, refcol in t.fks:
            cols.append(
                f"FOREIGN KEY ({_q(engine, col)}) "
                f"REFERENCES {_q(engine, ref)} ({_q(engine, refcol)})"
            )
    return f"CREATE TABLE {_q(engine, t.name)} (\n  " + ",\n  ".join(cols) + "\n)"


def _ordered(ds: Dataset) -> list[Table]:
    """Parents before children so FKs can be declared at CREATE time."""
    done: list[Table] = []
    names: set[str] = set()
    pending = list(ds.tables)
    while pending:
        progressed = False
        for t in list(pending):
            refs = {ref for _, ref, _ in t.fks if ref != t.name}
            if refs <= names:
                done.append(t)
                names.add(t.name)
                pending.remove(t)
                progressed = True
        if not progressed:
            raise RuntimeError("FK cycle in scenario")
    return done


# --------------------------------------------------------------------------- DuckDB
def build_duckdb(company: str, path: Path) -> Path:
    import duckdb

    ds = scenarios.build(company)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    try:
        for t in _ordered(ds):
            # Oracle copy: no FKs (DuckDB rejects a self-reference in one bulk insert).
            con.execute(create_sql("duckdb", t, with_fks=False))
            if t.rows:
                # Columnar bulk load: executemany is row-at-a-time in DuckDB.
                import pyarrow as pa

                cols = list(zip(*t.rows, strict=True))
                arrow = pa.table(
                    {name: list(vals) for (name, _), vals in zip(t.columns, cols, strict=True)}
                )
                con.register("_fde_rows", arrow)
                con.execute(f"INSERT INTO {_q('duckdb', t.name)} SELECT * FROM _fde_rows")
                con.unregister("_fde_rows")
    finally:
        con.close()
    return path


# --------------------------------------------------------------------------- pack I/O
def load_questions(company: str) -> list[dict[str, Any]]:
    import yaml

    data = yaml.safe_load((PACK_DIR / company / "questions.yaml").read_text(encoding="utf-8"))
    qs = list(data.get("questions") or [])
    for q in qs:
        q.setdefault("expect", ANSWERABLE)
    return qs


def load_pack_meta(company: str) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load((PACK_DIR / company / "questions.yaml").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if k != "questions"}


def answers_path(company: str) -> Path:
    return PACK_DIR / company / "answers.json"


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def run_select(db: Path, sql: str) -> tuple[list[str], list[list[Any]]]:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = [[_jsonable(v) for v in r] for r in cur.fetchall()]
    finally:
        con.close()
    return cols, rows


def _norm_cell(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float, Decimal)):
        return round(float(v), 2)
    if isinstance(v, str):
        try:
            return round(float(v), 2)
        except ValueError:
            return v.strip()
    return str(v)


def rows_match(
    got: Sequence[Sequence[Any]], gold: Sequence[Sequence[Any]], *, ordered: bool
) -> bool:
    """Values only, column names ignored, numbers to 2dp. Multiset unless ordered."""
    a = [tuple(_norm_cell(v) for v in r) for r in got]
    b = [tuple(_norm_cell(v) for v in r) for r in gold]
    if len(a) != len(b):
        return False
    if ordered:
        return a == b
    key = lambda t: tuple((x is None, str(type(x)), str(x)) for x in t)  # noqa: E731
    return sorted(a, key=key) == sorted(b, key=key)


def gold_for(company: str, db: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for q in load_questions(company):
        if q["expect"] != ANSWERABLE:
            continue
        cols, rows = run_select(db, q["gold_sql"])
        out[q["id"]] = {"columns": cols, "rows": rows}
    return out


def freeze(companies: Iterable[str]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for c in companies:
            db = build_duckdb(c, Path(tmp) / f"{c}.duckdb")
            gold = gold_for(c, db)
            answers_path(c).write_text(
                json.dumps(gold, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"[FROZEN] {c}: {len(gold)} gold answers")
    return EXIT_PASS


def _ordered_q(q: dict[str, Any]) -> bool:
    return bool(q.get("ordered"))


def _tied_at_limit(db: Path, sql: str) -> bool:
    """A ``LIMIT n`` gold whose n-th and (n+1)-th rows tie on the sort value is ambiguous."""
    import re

    m = re.search(r"LIMIT (\d+)\s*$", sql.strip())
    if not m:
        return False
    n = int(m.group(1))
    _, rows = run_select(db, sql.strip()[: m.start()] + f"LIMIT {n + 1}")
    return len(rows) > n and rows[n - 1][-1] == rows[n][-1]


def self_test(companies: Iterable[str]) -> int:
    bad = 0
    total = 0
    with tempfile.TemporaryDirectory() as tmp:
        for c in companies:
            qs = load_questions(c)
            ids = [q["id"] for q in qs]
            if len(ids) != len(set(ids)):
                print(f"[FAIL] {c}: duplicate question ids")
                bad += 1
            n_ans = sum(1 for q in qs if q["expect"] == ANSWERABLE)
            if not 15 <= len(qs) <= 25:
                print(f"[FAIL] {c}: {len(qs)} questions, pack wants 15-25")
                bad += 1
            frozen = json.loads(answers_path(c).read_text(encoding="utf-8"))
            db = build_duckdb(c, Path(tmp) / f"{c}.duckdb")
            for q in qs:
                if q["expect"] in REFUSE_EXPECT:
                    if q.get("gold_sql"):
                        print(f"[FAIL] {c}/{q['id']}: a {q['expect']} case carries gold_sql")
                        bad += 1
                    continue
                total += 1
                _, rows = run_select(db, q["gold_sql"])
                want = (frozen.get(q["id"]) or {}).get("rows")
                if want is None:
                    print(f"[FAIL] {c}/{q['id']}: no frozen answer")
                    bad += 1
                elif not rows:
                    print(f"[FAIL] {c}/{q['id']}: gold SQL returns no rows")
                    bad += 1
                elif not rows_match(rows, want, ordered=_ordered_q(q)):
                    print(f"[FAIL] {c}/{q['id']}: gold drifted from answers.json")
                    bad += 1
                elif _tied_at_limit(db, q["gold_sql"]):
                    print(f"[FAIL] {c}/{q['id']}: top-N cut falls on a tie (two right answers)")
                    bad += 1
            print(f"[OK] {c}: {len(qs)} questions ({n_ans} answerable)")
    print(
        f"self-test: {total} gold answers re-derived, {bad} failures "
        "(fixture check, not a DMS score)"
    )
    return EXIT_FAIL if bad else EXIT_PASS


# --------------------------------------------------------------------------- engines
def _password() -> str:
    return os.environ.get("FDE_SOURCE_PASSWORD", "")


def _engine_connect(engine: str, args: argparse.Namespace, database: str | None) -> Any:
    if engine == "postgresql":
        import psycopg

        return psycopg.connect(
            host=args.host,
            port=args.port or 5432,
            user=args.user,
            password=_password(),
            dbname=database or "postgres",
            autocommit=True,
        )
    if engine == "mysql":
        import pymysql

        return pymysql.connect(
            host=args.host,
            port=args.port or 3306,
            user=args.user,
            password=_password(),
            database=database,
            autocommit=True,
        )
    if engine == "sqlserver":
        import pyodbc

        driver = os.environ.get("FDE_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
        conn = (
            f"DRIVER={{{driver}}};SERVER={args.host},{args.port or 1433};"
            f"DATABASE={database or 'master'};UID={args.user};PWD={{{_password()}}};"
            "Encrypt=no;TrustServerCertificate=yes;"
        )
        return pyodbc.connect(conn, autocommit=True)
    raise SystemExit(f"unsupported engine {engine}")


def load_engine(engine: str, companies: Iterable[str], args: argparse.Namespace) -> int:
    for c in companies:
        ds = scenarios.build(c)
        dbname = f"fde_{c}"
        if engine == "sqlite":
            import sqlite3

            out = Path(args.out_dir or ".") / f"{dbname}.sqlite"
            if out.exists():
                print(f"[SKIP] {out} exists; refusing to overwrite (no deletes)")
                continue
            con = sqlite3.connect(str(out))
            con.execute("PRAGMA foreign_keys = ON")
            for t in _ordered(ds):
                con.execute(create_sql("sqlite", t))
                ph = ", ".join("?" for _ in t.columns)
                con.executemany(
                    f'INSERT INTO "{t.name}" VALUES ({ph})',
                    [tuple(_sqlite_cell(v) for v in r) for r in t.rows],
                )
            con.commit()
            con.close()
            print(f"[LOADED] sqlite {out}")
            continue
        admin = _engine_connect(engine, args, None)
        cur = admin.cursor()
        if engine == "postgresql":
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute(f'CREATE DATABASE "{dbname}"')
        elif engine == "sqlserver":
            cur.execute("SELECT 1 FROM sys.databases WHERE name = ?", (dbname,))
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute(f"CREATE DATABASE [{dbname}]")
        else:
            cur.execute("SHOW DATABASES LIKE %s", (dbname,))
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute(f"CREATE DATABASE `{dbname}`")
        admin.close()
        if exists:
            print(f"[SKIP] {engine} {dbname} exists; refusing to overwrite (no deletes)")
            continue
        con = _engine_connect(engine, args, dbname)
        cur = con.cursor()
        if engine == "sqlserver":
            cur.fast_executemany = True
        for t in _ordered(ds):
            cur.execute(create_sql(engine, t))
            ph = ", ".join(("?" if engine == "sqlserver" else "%s") for _ in t.columns)
            cols = ", ".join(_q(engine, cname) for cname, _ in t.columns)
            stmt = f"INSERT INTO {_q(engine, t.name)} ({cols}) VALUES ({ph})"
            for i in range(0, len(t.rows), 1000):
                cur.executemany(stmt, t.rows[i : i + 1000])
        if args.grant_readonly:
            _grant_readonly(engine, cur, dbname, args.grant_readonly)
        con.close()
        print(f"[LOADED] {engine} {dbname}: {len(ds.tables)} tables")
    return EXIT_PASS


def _sqlite_cell(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    return v


def _grant_readonly(engine: str, cur: Any, dbname: str, role: str) -> None:
    """SELECT-only for the extract login. The role must already exist."""
    if engine == "postgresql":
        cur.execute(f'GRANT CONNECT ON DATABASE "{dbname}" TO "{role}"')
        cur.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        cur.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"')
    elif engine == "mysql":
        cur.execute(f"GRANT SELECT ON `{dbname}`.* TO %s@'%%'", (role,))
    elif engine == "sqlserver":
        cur.execute(f"CREATE USER [{role}] FOR LOGIN [{role}]")
        cur.execute(f"ALTER ROLE db_datareader ADD MEMBER [{role}]")


# --------------------------------------------------------------------------- fidelity
def fidelity(engine: str, companies: Iterable[str], args: argparse.Namespace) -> int:
    """Extract through dms_executor.db_connector, then grade gold SQL on the bronze copy."""
    from dms_executor.db_connector import SourceConfig, ingest_source_database

    results: dict[str, Any] = {}
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for c in companies:
            frozen = json.loads(answers_path(c).read_text(encoding="utf-8"))
            wh = Path(tmp) / f"bronze_{engine}_{c}.duckdb"
            cfg_kwargs: dict[str, Any] = {}
            if engine == "sqlite":
                cfg_kwargs = {
                    "host": "localhost",
                    "database": str(Path(args.out_dir or ".") / f"fde_{c}.sqlite"),
                }
            else:
                cfg_kwargs = {"host": args.host, "port": args.port, "database": f"fde_{c}"}
            cfg = SourceConfig(
                kind=engine,
                user=args.user or "",
                password=_password(),  # type: ignore[arg-type]
                encrypt=False,
                trust_server_certificate=True,
                **cfg_kwargs,
            )
            extract = ingest_source_database(cfg, path=wh, space_id=f"fde-{c}")
            names = {
                p.source.split("#", 1)[1].split(".", 1)[1]: p.bronze_table for p in extract.pulls
            }
            untyped = {
                p.bronze_table: p.untyped_columns for p in extract.pulls if p.untyped_columns
            }
            view_db = Path(tmp) / f"views_{engine}_{c}.duckdb"
            _bronze_views(wh, view_db, names)
            ok = mismatch = err = 0
            details = []
            for q in load_questions(c):
                if q["expect"] != ANSWERABLE:
                    continue
                try:
                    _, rows = run_select(view_db, q["gold_sql"])
                except Exception as exc:  # noqa: BLE001 - reported per question
                    err += 1
                    details.append(
                        {
                            "id": q["id"],
                            "verdict": "ERROR",
                            "reason": type(exc).__name__ + ": " + str(exc)[:200],
                        }
                    )
                    continue
                if rows_match(rows, frozen[q["id"]]["rows"], ordered=_ordered_q(q)):
                    ok += 1
                else:
                    mismatch += 1
                    details.append({"id": q["id"], "verdict": "MISMATCH"})
            n_keys = len(extract.keys.foreign_keys)
            n_pk = len(extract.keys.primary_keys)
            results[c] = {
                "tables": len(extract.pulls),
                "pk_tables": n_pk,
                "fk_columns": n_keys,
                "gold_ok": ok,
                "mismatch": mismatch,
                "error": err,
                "untyped": untyped,
                "details": details,
            }
            want_pk = len(scenarios.build(c).tables)
            want_fk = sum(len(t.fks) for t in scenarios.build(c).tables)
            keys_ok = n_pk == want_pk and n_keys == want_fk
            results[c]["keys_expected"] = {"pk_tables": want_pk, "fk_columns": want_fk}
            company_fail = bool(mismatch or err or (args.expect_keys and not keys_ok))
            if company_fail:
                bad += 1
            if not keys_ok:
                print(
                    f"[KEYS] {engine} {c}: read PK on {n_pk}/{want_pk} tables, "
                    f"FK cols {n_keys}/{want_fk}" + ("" if args.expect_keys else " (not enforced)")
                )
            print(
                f"[{'FAIL' if company_fail else 'OK'}] {engine} {c}: "
                f"{len(extract.pulls)} tables, PK on {n_pk}, FK cols {n_keys}, "
                f"gold {ok} ok / {mismatch} mismatch / {err} error"
                + (f", untyped {sum(len(v) for v in untyped.values())} cols" if untyped else "")
            )
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, default=str) + "\n")
    return EXIT_FAIL if bad else EXIT_PASS


def _bronze_views(warehouse: Path, view_db: Path, names: dict[str, str]) -> None:
    """Bare source table names as views over the landed bronze tables."""
    import duckdb

    con = duckdb.connect(str(view_db))
    try:
        con.execute(f"ATTACH '{warehouse}' AS wh (READ_ONLY)")
        for src, bronze in names.items():
            schema, _, tbl = bronze.rpartition(".")
            rel = f'wh."{schema or "bronze"}"."{tbl}"' if schema else f'wh.bronze."{tbl}"'
            cols = [
                r[0]
                for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
                if r[0] not in ("_src", "_ingest_id")
            ]
            sel = ", ".join('"' + col.replace('"', '""') + '"' for col in cols)
            con.execute(f'CREATE TABLE "{src}" AS SELECT {sel} FROM {rel}')
        con.execute("DETACH wh")
    finally:
        con.close()


# --------------------------------------------------------------------------- live
def _confident(env: dict[str, Any]) -> bool:
    from score_curated import is_confident

    return bool(is_confident(env))


def judge_live(
    q: dict[str, Any], env: dict[str, Any], gold: dict[str, Any] | None
) -> tuple[str, str]:
    """CORRECT | WRONG | ABSTAIN | RECONFIRM. Demo fallback is WRONG."""
    if env.get("demo_fallback_used"):
        return "WRONG", "demo_fallback_used"
    confident = _confident(env)
    if q["expect"] in REFUSE_EXPECT:
        return ("WRONG", "confident_on_" + q["expect"]) if confident else ("ABSTAIN", "")
    if not confident:
        if env.get("reconfirm") or env.get("needs_confirm"):
            return "RECONFIRM", ""
        return "ABSTAIN", str(env.get("abstain_reason") or env.get("reason") or "")[:120]
    rows = env.get("rows") or env.get("values") or []
    got = [list(r.values()) if isinstance(r, dict) else list(r) for r in rows]
    if gold is None:
        return "WRONG", "no_gold"
    if rows_match(got, gold["rows"], ordered=_ordered_q(q)):
        return "CORRECT", ""
    return "WRONG", f"rows_mismatch:{len(got)}/{len(gold['rows'])}"


def live(companies: Iterable[str], args: argparse.Namespace) -> int:
    base = os.environ.get("DMS_API_BASE", "").rstrip("/")
    if not base or not args.spaces:
        print("[CONFIG] DMS_API_BASE and --spaces are required for --live (not a PASS)")
        return EXIT_CONFIG
    import httpx

    spaces = json.loads(Path(args.spaces).read_text())
    report: dict[str, Any] = {}
    any_wrong = False
    headers = {}
    token = os.environ.get("DMS_API_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for c in companies:
        sid = spaces.get(c)
        if not sid:
            print(f"[SKIP] {c}: no Space id in {args.spaces}")
            continue
        frozen = json.loads(answers_path(c).read_text(encoding="utf-8"))
        tally = {"CORRECT": 0, "WRONG": 0, "ABSTAIN": 0, "RECONFIRM": 0, "ERROR": 0}
        rows_out = []
        for q in load_questions(c):
            try:
                resp = httpx.post(
                    f"{base}/v1/chat/ask",
                    json={"question": q["question"], "space_id": sid},
                    timeout=args.timeout,
                    headers=headers,
                )
                env = (
                    resp.json()
                    if resp.headers.get("content-type", "").startswith("application/json")
                    else {}
                )
                if resp.status_code >= 400 and not env.get("badge"):
                    verdict, why = "ERROR", f"http_{resp.status_code}"
                else:
                    verdict, why = judge_live(q, env, frozen.get(q["id"]))
            except Exception as exc:  # noqa: BLE001
                verdict, why, env = "ERROR", type(exc).__name__, {}
            tally[verdict] += 1
            rows_out.append(
                {
                    "id": q["id"],
                    "expect": q["expect"],
                    "verdict": verdict,
                    "reason": why,
                    "badge": env.get("badge"),
                    "sql": (env.get("sql") or "")[:2000],
                }
            )
            print(f"  {c}/{q['id']}: {verdict} {why}")
        answered = tally["CORRECT"] + tally["WRONG"]
        report[c] = {
            "tally": tally,
            "answered": answered,
            "n": sum(tally.values()),
            "cases": rows_out,
        }
        any_wrong |= tally["WRONG"] > 0
        print(
            f"[{c}] n={sum(tally.values())} answered={answered} correct={tally['CORRECT']} "
            f"WRONG={tally['WRONG']} abstain={tally['ABSTAIN']} reconfirm={tally['RECONFIRM']} "
            f"error={tally['ERROR']}"
        )
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1) + "\n")
    return EXIT_FAIL if any_wrong else EXIT_PASS


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--build-duckdb", metavar="DIR")
    mode.add_argument("--load", choices=["postgresql", "mysql", "sqlserver", "sqlite"])
    mode.add_argument("--fidelity", choices=["postgresql", "mysql", "sqlserver", "sqlite"])
    mode.add_argument("--live", action="store_true")
    ap.add_argument("--company", action="append", choices=COMPANIES)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int)
    ap.add_argument("--user")
    ap.add_argument("--out-dir")
    ap.add_argument("--grant-readonly", metavar="ROLE")
    ap.add_argument("--spaces")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--json")
    ap.add_argument(
        "--expect-keys",
        action="store_true",
        help="--fidelity: FAIL unless every declared PK and FK column was read",
    )
    args = ap.parse_args(argv)
    companies = args.company or list(COMPANIES)
    if args.self_test:
        return self_test(companies)
    if args.freeze:
        return freeze(companies)
    if args.build_duckdb:
        out = Path(args.build_duckdb)
        out.mkdir(parents=True, exist_ok=True)
        for c in companies:
            print(build_duckdb(c, out / f"fde_{c}.duckdb"))
        return EXIT_PASS
    if args.load:
        return load_engine(args.load, companies, args)
    if args.fidelity:
        return fidelity(args.fidelity, companies, args)
    return live(companies, args)


if __name__ == "__main__":
    raise SystemExit(main())
