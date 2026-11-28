"""DMS_AI_FLOOR_DIRECT. Default off. The digit 1 only.

The AI floor without the Cortex think step. One harness, any database:

  describe the database that executes (tables, columns, keys, a few values)
  retrieve the tables that fit the question when the schema is large
  OpenVault role ``sql`` writes one SELECT
  check: one read-only SELECT, granted tables, then execute read-only
  every failed attempt (SQL, the exact database error, the model) is kept
  and fed into the next attempt; from attempt 3 the role is ``reason``
  (OpenVault picks the stronger model)

The database does every sum and grouping. At most ``ROW_CAP`` rows return.
Abstain only on an ungranted table or a statement that is not a read-only
SELECT. A miss after the last attempt states no figure and keeps the first
failure reason. No provider and no model id are named here.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

DIRECT_ENV = "DMS_AI_FLOOR_DIRECT"
ATTEMPTS_ENV = "DMS_AI_FLOOR_ATTEMPTS"
TIMEOUT_ENV = "DMS_AI_FLOOR_CALL_TIMEOUT_S"
SCHEMA_CHARS_ENV = "DMS_AI_FLOOR_SCHEMA_CHARS"
ROW_CAP = 200
DEFAULT_ATTEMPTS = 4
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_SCHEMA_CHARS = 40000  # about 10k tokens; a small schema goes in whole
ESCALATE_FROM = 3
SAMPLE_VALUES = 3
MAX_OUT_TOKENS = 1500


def enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get(DIRECT_ENV) or "").strip() == "1"


def _env_num(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# ---------------------------------------------------------------- database
class Db:
    """Read-only handle on the database that executes. DuckDB or SQLite."""

    def __init__(self, path: Path | str, dialect: str | None = None) -> None:
        self.path = Path(path)
        self.dialect = dialect or (
            "sqlite" if self.path.suffix in {".sqlite", ".db", ".sqlite3"} else "duckdb"
        )
        self.con: Any
        if self.dialect == "sqlite":
            self.con = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
            )
        else:
            # Lake files open only through the registry: one read-only parent
            # per file, so this never clashes with serving or ingest.
            from dms_executor.lake_registry import open_lake

            self.con = open_lake(self.path)

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:  # noqa: BLE001
            pass

    def run(self, sql: str, cap: int = ROW_CAP) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        cur = self.con.execute(sql)
        cols = [c[0] for c in (cur.description or [])]
        rows = cur.fetchmany(cap + 1)
        return cols, [tuple(r) for r in rows[:cap]], len(rows) > cap

    def tables(self) -> list[str]:
        if self.dialect == "sqlite":
            q = (
                "select name from sqlite_master where type in ('table','view') and "
                "name not like 'sqlite_%' order by name"
            )
        else:
            q = (
                "select table_schema, table_name from information_schema.tables where "
                "table_schema not in ('information_schema','pg_catalog') order by 1, 2"
            )
            return [
                n if sch == "main" else f"{sch}.{n}" for sch, n in self.con.execute(q).fetchall()
            ]
        return [r[0] for r in self.con.execute(q).fetchall()]

    @staticmethod
    def _split(table: str) -> tuple[str, str]:
        if "." in table:
            sch, name = table.split(".", 1)
            return sch, name
        return "main", table

    def _ref(self, table: str) -> str:
        if self.dialect != "sqlite" and "." in table:
            sch, name = self._split(table)
            return f'"{sch}"."{name}"'
        return f'"{table}"'

    def columns(self, table: str) -> list[tuple[str, str, bool]]:
        if self.dialect == "sqlite":
            rows = self.con.execute(f'pragma table_info("{table}")').fetchall()
            return [(r[1], r[2] or "", bool(r[5])) for r in rows]
        sch, name = self._split(table)
        rows = self.con.execute(
            "select column_name, data_type from information_schema.columns where "
            "table_schema = ? and table_name = ? order by ordinal_position",
            [sch, name],
        ).fetchall()
        return [(r[0], r[1], False) for r in rows]

    def foreign_keys(self, table: str) -> list[tuple[str, str, str]]:
        if self.dialect == "sqlite":
            try:
                rows = self.con.execute(f'pragma foreign_key_list("{table}")').fetchall()
            except Exception:  # noqa: BLE001
                return []
            return [(r[3], r[2], r[4] or "") for r in rows]
        try:
            rows = self.con.execute(
                "select constraint_text from duckdb_constraints() where table_name = ? "
                "and constraint_type = 'FOREIGN KEY'",
                [self._split(table)[1]],
            ).fetchall()
        except Exception:  # noqa: BLE001
            return []
        out = []
        for (text,) in rows:
            m = re.search(r"FOREIGN KEY \(([^)]*)\) REFERENCES (\w+)\(([^)]*)\)", str(text))
            if m:
                out.append((m.group(1), m.group(2), m.group(3)))
        return out

    def ddl(self, table: str) -> str:
        if self.dialect != "sqlite":
            return ""
        try:
            row = self.con.execute(
                "select sql from sqlite_master where type='table' and name = ?", [table]
            ).fetchone()
        except Exception:  # noqa: BLE001
            return ""
        return str(row[0]).strip() if row and row[0] else ""

    def example_rows(self, table: str, k: int = 3) -> str:
        try:
            cur = self.con.execute(f"select * from {self._ref(table)} limit {k}")
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
        except Exception:  # noqa: BLE001
            return ""
        if not rows:
            return ""
        body = "\n".join("\t".join(str(v)[:60] for v in r) for r in rows)
        return f"\n/* {len(rows)} example rows:\n" + "\t".join(cols) + "\n" + body + "\n*/"

    def samples(self, table: str, column: str, k: int = SAMPLE_VALUES) -> list[str]:
        q = (
            f'select distinct "{column}" from {self._ref(table)} '
            f'where "{column}" is not null limit {k}'
        )
        try:
            return [str(r[0])[:40] for r in self.con.execute(q).fetchall()]
        except Exception:  # noqa: BLE001
            return []


def _quote(dialect: str) -> str:
    return "SQLite" if dialect == "sqlite" else "DuckDB"


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for word in _WORD.findall(text.lower().replace("_", " ")):
        out.add(word)
        if len(word) > 4 and word.endswith("s"):
            out.add(word[:-1])
    return out


def describe(
    db: Db, question: str, grantable: set[str] | None, budget: int
) -> tuple[str, list[str], int]:
    """Schema text for the prompt, the tables in it, and how many tables exist.

    Every granted table goes in when it fits the budget. Otherwise tables are
    ranked by name and column overlap with the question (generic retrieval),
    and foreign-key neighbours of the picked tables follow them in.
    """
    names = [t for t in db.tables() if grantable is None or t.lower() in grantable]
    blocks: dict[str, str] = {}
    fks: dict[str, list[tuple[str, str, str]]] = {}
    score: dict[str, float] = {}
    q = _tokens(question)
    for t in names:
        cols = db.columns(t)
        fks[t] = db.foreign_keys(t)
        lines = [f"TABLE {t} ("]
        hit = len(q & _tokens(t)) * 3.0
        for name, typ, pk in cols:
            extra = " PRIMARY KEY" if pk else ""
            vals = ""
            if "char" in typ.lower() or "text" in typ.lower() or typ == "":
                sample = db.samples(t, name)
                if sample:
                    vals = " e.g. " + ", ".join(repr(v) for v in sample)
            lines.append(f"  {name} {typ}{extra}{vals}")
            hit += len(q & _tokens(name))
        for col, ref, refcol in fks[t]:
            lines.append(f"  FOREIGN KEY ({col}) REFERENCES {ref}({refcol})")
        lines.append(")")
        # The database's own DDL keeps column comments and constraints the
        # owner wrote; a few real rows show formats (the published
        # text-to-SQL prompt shape). Both are generic, from the DB itself.
        native = db.ddl(t)
        head = native if native else "\n".join(lines)
        blocks[t] = head + db.example_rows(t)
        score[t] = hit
    total = sum(len(b) for b in blocks.values())
    if total <= budget:
        chosen = names
    else:
        ranked = sorted(names, key=lambda t: -score[t])
        chosen, used = [], 0
        for t in ranked:
            if t in chosen:
                continue
            for cand in [t] + [ref for _c, ref, _r in fks.get(t, []) if ref in blocks]:
                if cand in chosen or used + len(blocks[cand]) > budget:
                    continue
                chosen.append(cand)
                used += len(blocks[cand])
    return "\n\n".join(blocks[t] for t in chosen), chosen, len(names)


# ---------------------------------------------------------------- checks
_SELECT_HEAD = re.compile(r"(?is)^\s*(with|select)\b")


def check_sql(sql: str, dialect: str, grantable: set[str] | None) -> str | None:
    """None when the SQL is one read-only SELECT over granted tables."""
    import sqlglot
    from sqlglot import exp

    try:
        trees = [t for t in sqlglot.parse(sql, read=dialect) if t is not None]
    except Exception as exc:  # noqa: BLE001
        return f"sql_not_analyzable: {str(exc)[:200]}"
    if len(trees) != 1:
        return "multi_statement"
    tree = trees[0]
    if not isinstance(
        tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)
    ) and not _SELECT_HEAD.match(sql):
        return "statement_not_allowed"
    for bad in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command):
        if tree.find(bad) is not None:
            return "statement_not_allowed"
    if grantable is not None:
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        for table in tree.find_all(exp.Table):
            name = table.name.lower()
            qual = f"{table.db}.{table.name}".lower() if table.db else name
            if name and name not in ctes and name not in grantable and qual not in grantable:
                return f"ungranted: {table.name}"
    return None


_DIRECT_STOP = ("ungranted", "statement_not_allowed", "multi_statement")


_DECLINE = re.compile(r"^(CANNOT|ASK)\s*:\s*(.+)$", re.S | re.I)


def _extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    body = (m.group(1) if m else text).strip().rstrip(";").strip()
    # A fenced block always goes to the checker, so a DELETE is named
    # statement_not_allowed instead of hiding behind no_sql.
    return body if (m or _SELECT_HEAD.match(body)) else ""


# ---------------------------------------------------------------- model call
def ov_chat(role: str, messages: list[dict[str, str]], *, timeout: float) -> dict[str, Any]:
    """OpenVault chat. Routing stays in OpenVault: only the role is sent."""
    key = ""
    for name in ("OPENVAULT_API_KEY", "CORTEX_API_KEY"):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            key = raw
            break
    base = (os.environ.get("OPENVAULT_URL") or "http://127.0.0.1:5000").rstrip("/")
    body = {"role": role, "messages": messages, "max_tokens": MAX_OUT_TOKENS, "temperature": 0}
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.loads(f.read())


# ---------------------------------------------------------------- harness
def _prompt(
    question: str,
    schema: str,
    dialect: str,
    attempts: Sequence[Mapping[str, Any]],
    today: str | None = None,
) -> list[dict[str, str]]:
    system = (
        f"You write one read-only {_quote(dialect)} SELECT that answers the "
        f"user's question from the database below. "
        "Let the database do every count, sum, average and grouping. Use only "
        "tables and columns that exist below. "
        "Reply with exactly one SQL query in a ```sql block and nothing else. "
        "Only when no SQL can answer it, reply with one line instead: "
        "CANNOT: <reason> if the question asks to change data or the database "
        "has no data for it, or "
        "ASK: <one clarifying question> if it can be read in more than one "
        "materially different way."
    )
    if today:
        system += f" Today is {today}."
    user = [f"DATABASE ({_quote(dialect)}):\n{schema}", "", f"QUESTION: {question}"]
    failed = [a for a in attempts if a.get("error")]
    if failed:
        user.append("")
        user.append("EARLIER ATTEMPTS THAT FAILED (fix the cause; do not repeat them):")
        for i, a in enumerate(failed, 1):
            user.append(f"-- attempt {i} SQL:\n{a.get('sql') or '(no SQL in the reply)'}")
            user.append(f"-- database / checker error: {a.get('error')}")
    ran = [a for a in attempts if a.get("result_preview")]
    if ran:
        user.append("")
        user.append(
            "EARLIER ATTEMPTS THAT RAN (not yet accepted; check each answers the "
            "question exactly, then return the best SQL):"
        )
        for a in ran:
            pv = a["result_preview"]
            user.append(f"-- SQL:\n{a.get('sql')}")
            user.append(
                f"-- returned {pv['row_count']} row(s), columns {pv['columns']}, first "
                f"rows {pv['rows']}"
            )
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(user)}]


def run_direct(
    question: str,
    *,
    db: Db,
    grantable: set[str] | None = None,
    prior_attempts: Sequence[Mapping[str, Any]] = (),
    chat: Callable[..., dict[str, Any]] = ov_chat,
    max_attempts: int | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Run the floor. Returns rows, sql, every attempt, and per-stage times."""
    started = time.perf_counter()
    grant = {g.lower() for g in grantable} if grantable is not None else None
    max_attempts = int(max_attempts or _env_num(ATTEMPTS_ENV, DEFAULT_ATTEMPTS))
    timeout = _env_num(TIMEOUT_ENV, DEFAULT_TIMEOUT_S)
    t0 = time.perf_counter()
    schema, chosen, n_tables = describe(
        db, question, grant, int(_env_num(SCHEMA_CHARS_ENV, DEFAULT_SCHEMA_CHARS))
    )
    stages: dict[str, float] = {"schema_ms": round((time.perf_counter() - t0) * 1000, 1)}
    attempts: list[dict[str, Any]] = []

    def try_sql(sql: str, rec: dict[str, Any]) -> dict[str, Any] | None:
        t = time.perf_counter()
        why = check_sql(sql, db.dialect, grant)
        rec["check_ms"] = round((time.perf_counter() - t) * 1000, 1)
        if why:
            rec["error"] = why
            return None
        t = time.perf_counter()
        try:
            cols, rows, truncated = db.run(sql)
        except Exception as exc:  # noqa: BLE001
            rec["exec_ms"] = round((time.perf_counter() - t) * 1000, 1)
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
            return None
        rec["exec_ms"] = round((time.perf_counter() - t) * 1000, 1)
        rec["rows_returned"] = len(rows)
        rec["truncated"] = truncated
        return {"columns": cols, "rows": rows, "truncated": truncated}

    # Earlier failed attempts are kept. Their SQL is re-run here first so the
    # next model call sees this database's real error, not a stale one.
    for prior in prior_attempts:
        sql = str(prior.get("sql") or "").strip()
        rec: dict[str, Any] = {
            "n": len(attempts),
            "source": "prior",
            "sql": sql,
            "prior_error": prior.get("error"),
            "model": prior.get("model"),
        }
        attempts.append(rec)
        if sql:
            res = try_sql(sql, rec)
            if res is not None:
                # A prior SQL that runs is not served unseen: the model sees it
                # with its result head and decides (WRONG must stay 0).
                rec["result_preview"] = {
                    "columns": res["columns"],
                    "rows": [list(map(str, r))[:8] for r in res["rows"][:5]],
                    "row_count": len(res["rows"]),
                }
        else:
            rec["error"] = str(prior.get("error") or "no_sql")
    calls = 0
    while calls < max_attempts:
        calls += 1
        role = "reason" if calls >= ESCALATE_FROM else "sql"
        messages = _prompt(question, schema, db.dialect, attempts, today)
        rec = {
            "n": len(attempts),
            "source": "model",
            "role": role,
            "prompt_chars": sum(len(m["content"]) for m in messages),
        }
        attempts.append(rec)
        t = time.perf_counter()
        try:
            reply = chat(role, messages, timeout=timeout)
        except urllib.error.HTTPError as exc:
            rec["call_ms"] = round((time.perf_counter() - t) * 1000, 1)
            rec["error"] = f"model_http_{exc.code}"
            if exc.code in (401, 402, 403):
                rec["stop"] = True
                break
            continue
        except Exception as exc:  # noqa: BLE001
            rec["call_ms"] = round((time.perf_counter() - t) * 1000, 1)
            rec["error"] = f"model_call_failed: {type(exc).__name__}"
            continue
        rec["call_ms"] = round((time.perf_counter() - t) * 1000, 1)
        usage = reply.get("usage") or {}
        rec["model"] = reply.get("served_model") or reply.get("model")
        rec["provider"] = reply.get("served_provider")
        rec["tokens_in"] = usage.get("prompt_tokens")
        rec["tokens_out"] = usage.get("completion_tokens")
        rec["cost_usd"] = reply.get("est_cost_usd")
        text = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        decline = _DECLINE.match(text.strip())
        if decline and "```" not in text:
            # The model says no SQL can answer it. Keep its words; do not force SQL.
            kind, why = decline.group(1).upper(), decline.group(2).strip()[:400]
            rec["error"] = ("model_asks: " if kind == "ASK" else "model_cannot: ") + why
            out = {
                "abstained": True,
                "reason": rec["error"],
                "last_reason": rec["error"],
                "rows": [],
                "sql": None,
                "attempts": attempts,
                "stages": _stages(stages, attempts, started),
                "tables_given": chosen,
                "tables_total": n_tables,
                "schema_chars": len(schema),
            }
            if kind == "ASK":
                out["ask"] = why
            return out
        sql = _extract_sql(text)
        rec["sql"] = sql
        if not sql:
            rec["error"] = "no_sql_in_reply: " + text.strip()[:300]
            continue
        res = try_sql(sql, rec)
        if res is not None:
            return _done(question, res, sql, attempts, stages, started, chosen, n_tables, schema)
        if any(str(rec.get("error", "")).startswith(s) for s in _DIRECT_STOP):
            break
    first = next((a["error"] for a in attempts if a.get("error")), "no_attempt")
    return {
        "abstained": True,
        "reason": first,
        "last_reason": attempts[-1].get("error") if attempts else None,
        "rows": [],
        "sql": None,
        "attempts": attempts,
        "stages": _stages(stages, attempts, started),
        "tables_given": chosen,
        "tables_total": n_tables,
        "schema_chars": len(schema),
    }


def _stages(
    stages: dict[str, float], attempts: list[dict[str, Any]], started: float
) -> dict[str, float]:
    out = dict(stages)
    out["model_ms"] = round(sum(a.get("call_ms") or 0 for a in attempts), 1)
    out["check_ms"] = round(sum(a.get("check_ms") or 0 for a in attempts), 1)
    out["exec_ms"] = round(sum(a.get("exec_ms") or 0 for a in attempts), 1)
    out["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return out


def _done(
    question, res, sql, attempts, stages, started, chosen, n_tables, schema
) -> dict[str, Any]:
    cols = res["columns"]
    rows = [
        dict(zip(cols, [v.isoformat() if hasattr(v, "isoformat") else v for v in r]))
        for r in res["rows"]
    ]
    return {
        "abstained": False,
        "rows": rows,
        "row_list": [list(r) for r in res["rows"]],
        "truncated": res["truncated"],
        "sql": sql,
        "attempts": attempts,
        "stages": _stages(stages, attempts, started),
        "tables_given": chosen,
        "tables_total": n_tables,
        "schema_chars": len(schema),
    }


def floor_compute(
    question: str,
    *,
    db_path: str | Path,
    grantable: set[str] | None,
    dialect: str = "duckdb",
    today: str | None = None,
    chat: Callable[..., dict[str, Any]] = ov_chat,
) -> Callable[[Any], dict[str, Any]]:
    """A ``compute`` seam for ``ai_floor.run_floor`` that asks OV role sql directly.

    Each call is one model attempt. Every earlier attempt (SQL, exact database
    error, result preview) is kept in this closure and fed into the next one,
    so nothing a failed try learned is thrown away. The database is the one
    the answer executes on, opened read-only.
    """
    kept: list[dict[str, Any]] = []

    def compute(_catalog: Any) -> dict[str, Any]:
        db = Db(db_path, "sqlite" if str(dialect).lower() == "sqlite" else "duckdb")
        try:
            out = run_direct(
                question,
                db=db,
                grantable=grantable,
                prior_attempts=list(kept),
                chat=chat,
                max_attempts=1,
                today=today,
            )
        finally:
            db.close()
        fresh = [a for a in out["attempts"] if a.get("source") == "model"]
        kept.extend(fresh)
        model = next((a.get("model") for a in reversed(fresh) if a.get("model")), None)
        if out.get("abstained") or not out.get("sql"):
            return {"error": str(out.get("reason") or "no accepted SQL"), "served_model": model}
        return {"query_sql": out["sql"], "served_model": model, "ai_floor_direct": True}

    compute.kept = kept  # type: ignore[attr-defined]
    return compute
