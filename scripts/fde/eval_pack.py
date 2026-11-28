"""FDE-EVAL-PACK-01: eval packs with steward definitions, tie-aware gold, and a raw-LLM control.

A grader that invents a definition marks a defensible answer WRONG, and a grader
that keeps one arbitrary ``LIMIT n`` row marks the other tied row WRONG. This
script does neither.

* Definitions come from a tenant YAML file. Each one has a name, a sentence,
  and a SQL fragment. Question templates name the definitions they use.
  The gold records that citation. A reading that is not cited is not gold.
* An alternative defensible reading is stored on the question as ``alt``.
  Matching it grades ``correct_alt``, which is reported separately from
  ``correct``. Anything else that was served is ``WRONG``.
* An ordered gold with ``LIMIT n`` is expanded to the tied set at the cutoff.
  A served answer is correct when it is a legal top-n pick from that set
  (every row strictly above the cutoff, plus any fill from the cutoff tie)
  or the full tied set. Order among tied rows does not matter.
* Gold SQL runs on the source database through a read-only login. A DMS lake
  file is refused. The password stays in the env var the file names.
* The raw-LLM arm sends one OpenVault request per question. The body carries
  the role string ``fde_eval``, the schema, and the steward definitions.
  It does not name a provider or a model. One shot: a bad reply or a SQL
  error is ``reconfirm``, not a guessed number.
* The DMS arm is graded by the same function. ``serve_path`` is grouped with
  ``grade52.path_group`` into AI vs rule.
* Building a pack appends a hash of every gold SQL and question to
  ``DMS_SCORED_ITEM_HASHES``. ``reject_scored_write`` and
  ``filter_retrieved_rows`` already refuse that list. No second registry.

    eval_pack.py build --definitions D.yaml --templates Q.yaml --out pack.json
    eval_pack.py grade --definitions D.yaml --pack pack.json --arm raw --out R.json
    eval_pack.py grade --pack pack.json --arm dms --results rows.json --out R.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import sqlglot
import yaml
from sqlglot import exp
from sqlglot.errors import ParseError

RAW_ROLE = "fde_eval"
CHAT_PATH = "/v1/chat/completions"
BUCKETS = ("correct", "correct_alt", "WRONG", "reconfirm", "abstain")
KINDS = ("sqlite", "postgresql", "mysql", "sqlserver")
DIALECT = {"sqlite": "sqlite", "postgresql": "postgres", "mysql": "mysql", "sqlserver": "tsql"}
_PLACE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_MODEL_KEYS = frozenset({"model", "provider", "strict"})
_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
    exp.Merge,
)
_READ_ONLY_SQL = {
    "postgresql": "SET default_transaction_read_only = on",
    "mysql": "SET SESSION TRANSACTION READ ONLY",
}
_SYSTEM = (
    "You write one read-only SELECT for the question. "
    "The steward definitions are the only meanings you may apply. "
    "If more than one reading is defensible, do not pick one: "
    "set decision to reconfirm and name the readings. "
    "If the schema cannot answer, set decision to abstain. "
    "Reply with one JSON object with keys decision, sql, and note. "
    "decision is answer, reconfirm, or abstain. "
    "sql is one SELECT when decision is answer, otherwise null."
)


class EvalPackError(ValueError):
    """The pack, the login, or the model reply cannot be graded as specified."""


@dataclass(frozen=True)
class SourceSpec:
    """A read-only source login. The password is not stored here."""

    kind: str
    database: str
    host: str = ""
    port: int | None = None
    user: str = ""
    password_env: str = ""
    encrypt: bool = True
    trust_server_certificate: bool = False
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    connect_timeout: int = 30

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise EvalPackError(f"source.kind must be one of {KINDS}")
        if not self.database:
            raise EvalPackError("source.database is required")
        refuse_lake(self.database)


def describe(spec: SourceSpec) -> str:
    if spec.kind == "sqlite":
        return f"sqlite:///{spec.database}"
    port = spec.port or {"postgresql": 5432, "mysql": 3306, "sqlserver": 1433}[spec.kind]
    return f"{spec.kind}://{spec.host}:{port}/{spec.database}"


def refuse_lake(database: str) -> None:
    """Gold is executed on the source login. The DMS lake is not a source."""
    low = database.replace("\\", "/").lower()
    name = low.rsplit("/", 1)[-1]
    if name.endswith(".duckdb") or "/bronze/" in f"/{low}/":
        raise EvalPackError("gold runs on the source login, not the DMS lake")


def _password(spec: SourceSpec) -> str:
    if not spec.password_env:
        return ""
    return os.environ.get(spec.password_env, "")


def _redact(spec: SourceSpec, exc: BaseException) -> EvalPackError:
    secret = _password(spec)
    text = str(exc)
    if secret and secret in text:
        text = text.replace(secret, "[redacted]")
    return EvalPackError(
        f"could not connect to {describe(spec)} ({type(exc).__name__}: {text[:180]})"
    )


def _sqlite_ro(spec: SourceSpec) -> sqlite3.Connection:
    path = Path(spec.database)
    if not path.is_file():
        raise EvalPackError(f"SQLite file not found: {describe(spec)}")
    try:
        con = sqlite3.connect(
            f"file:{quote(str(path))}?mode=ro", uri=True, timeout=spec.connect_timeout
        )
        con.execute("PRAGMA query_only = ON")
    except Exception as exc:
        raise _redact(spec, exc) from None
    return con


def _apply_read_only(con: Any, kind: str) -> None:
    sql = _READ_ONLY_SQL.get(kind)
    if sql:
        cur = con.cursor()
        try:
            cur.execute(sql)
        finally:
            cur.close()


@contextmanager
def open_source(spec: SourceSpec):
    """Open the source read-only and close it on the way out. Never a lake file."""
    refuse_lake(spec.database)
    secret = _password(spec)
    con: Any
    try:
        if spec.kind == "sqlite":
            con = _sqlite_ro(spec)
        elif spec.kind == "postgresql":
            import psycopg

            con = psycopg.connect(
                host=spec.host,
                port=spec.port or 5432,
                user=spec.user,
                password=secret,
                dbname=spec.database,
                connect_timeout=spec.connect_timeout,
                autocommit=True,
            )
            _apply_read_only(con, spec.kind)
        elif spec.kind == "mysql":
            import pymysql

            con = pymysql.connect(
                host=spec.host,
                port=spec.port or 3306,
                user=spec.user,
                password=secret,
                database=spec.database,
                connect_timeout=spec.connect_timeout,
                autocommit=True,
            )
            _apply_read_only(con, spec.kind)
        else:
            import pyodbc

            server = f"{spec.host},{spec.port or 1433}"
            parts = [
                "DRIVER={" + spec.odbc_driver.replace("}", "}}") + "}",
                "SERVER={" + server.replace("}", "}}") + "}",
                "DATABASE={" + spec.database.replace("}", "}}") + "}",
                "UID={" + spec.user.replace("}", "}}") + "}",
                "PWD={" + secret.replace("}", "}}") + "}",
                f"Encrypt={'yes' if spec.encrypt else 'no'}",
                f"TrustServerCertificate={'yes' if spec.trust_server_certificate else 'no'}",
                f"Connection Timeout={int(spec.connect_timeout)}",
            ]
            con = pyodbc.connect(";".join(parts) + ";", readonly=True, autocommit=True)
    except EvalPackError:
        raise
    except Exception as exc:
        raise _redact(spec, exc) from None
    try:
        yield con
    finally:
        try:
            con.close()
        except Exception:
            pass


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise EvalPackError(f"{path}: yaml root must be a mapping")
    return data


def _definition(row: Mapping[str, Any], *, where: str) -> dict[str, str]:
    name = str(row.get("name") or "")
    sentence = str(row.get("sentence") or "").strip()
    sql = str(row.get("sql") or "").strip()
    if not _NAME.fullmatch(name):
        raise EvalPackError(f"{where}: definition name must be a lowercase identifier")
    if not sentence:
        raise EvalPackError(f"{where}: definition {name} needs a sentence")
    if not sql or "{" in sql or ";" in sql:
        raise EvalPackError(f"{where}: definition {name} needs one SQL expression")
    return {"name": name, "sentence": sentence, "sql": sql}


def load_definitions(path: Path) -> list[dict[str, str]]:
    rows = _yaml(path).get("definitions")
    if not isinstance(rows, list) or not rows:
        raise EvalPackError(f"{path}: definitions must be a non-empty list")
    out = [_definition(row, where=str(path)) for row in rows if isinstance(row, Mapping)]
    if len(out) != len(rows):
        raise EvalPackError(f"{path}: each definition must be a mapping")
    if len({d["name"] for d in out}) != len(out):
        raise EvalPackError(f"{path}: duplicate definition name")
    return out


def load_source(path: Path) -> SourceSpec:
    src = _yaml(path).get("source")
    if not isinstance(src, Mapping):
        raise EvalPackError(f"{path}: source must be a mapping")
    if "password" in src:
        raise EvalPackError(f"{path}: put the password in an env var and name it in password_env")
    database = str(src.get("database") or "")
    kind = str(src.get("kind") or "")
    if kind == "sqlite" and database and not Path(database).is_absolute():
        database = str((path.parent / database).resolve())
    return SourceSpec(
        kind=kind,
        database=database,
        host=str(src.get("host") or ""),
        port=int(src["port"]) if src.get("port") else None,
        user=str(src.get("user") or ""),
        password_env=str(src.get("password_env") or ""),
        encrypt=bool(src.get("encrypt", True)),
        trust_server_certificate=bool(src.get("trust_server_certificate", False)),
        odbc_driver=str(src.get("odbc_driver") or "ODBC Driver 18 for SQL Server"),
    )


def _alt(row: Mapping[str, Any], *, where: str) -> dict[str, Any]:
    name = str(row.get("name") or "")
    sentence = str(row.get("sentence") or "").strip()
    sql = str(row.get("sql") or "").strip()
    uses = [str(n) for n in (row.get("uses") or [])]
    if not _NAME.fullmatch(name):
        raise EvalPackError(f"{where}: alt name must be a lowercase identifier")
    if not sentence or not sql:
        raise EvalPackError(f"{where}: alt {name} needs a sentence and SQL")
    return {
        "name": name,
        "sentence": sentence,
        "sql": sql,
        "uses": uses,
        "ordered": bool(row.get("ordered", False)),
    }


def load_templates(path: Path) -> list[dict[str, Any]]:
    rows = _yaml(path).get("questions")
    if not isinstance(rows, list) or not rows:
        raise EvalPackError(f"{path}: questions must be a non-empty list")
    out = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise EvalPackError(f"{path}: each question must be a mapping")
        qid = str(row.get("id") or "")
        if not qid or qid in seen:
            raise EvalPackError(f"{path}: question id missing or duplicate")
        seen.add(qid)
        question = str(row.get("question") or "").strip()
        gold_sql = str(row.get("gold_sql") or "").strip()
        template = str(row.get("template") or "").strip()
        if not question or not gold_sql or not template:
            raise EvalPackError(f"{path}: {qid} needs template, question, and gold_sql")
        alts = [_alt(a, where=f"{path}:{qid}") for a in (row.get("alt") or [])]
        out.append(
            {
                "id": qid,
                "template": template,
                "question": question,
                "gold_sql": gold_sql,
                "uses": [str(n) for n in (row.get("uses") or [])],
                "ordered": bool(row.get("ordered", False)),
                "alt": alts,
            }
        )
    return out


def _parse(sql: str, dialect: str) -> exp.Expression:
    try:
        return sqlglot.parse_one(sql, read=dialect)
    except ParseError as exc:
        raise EvalPackError("SQL did not parse as one statement") from exc


def _one_select(sql: str, dialect: str) -> str:
    text = sql.strip()
    if text.endswith(";"):
        text = text[:-1].strip()
    if ";" in text:
        raise EvalPackError("only one statement is allowed")
    tree = _parse(text, dialect)
    if not isinstance(tree, exp.Query) or any(isinstance(node, _FORBIDDEN) for node in tree.walk()):
        raise EvalPackError("only a read-only SELECT is allowed")
    return text


def _check_fragment(sql: str, dialect: str) -> None:
    if ";" in sql or "{" in sql:
        raise EvalPackError("definition SQL fragment must be one expression")
    tree = _parse(sql, dialect)
    if isinstance(tree, (exp.Query, *_FORBIDDEN)):
        raise EvalPackError("definition SQL fragment must be an expression")


def render(
    sql: str,
    by_name: Mapping[str, Mapping[str, str]],
    uses: Sequence[str],
    *,
    dialect: str,
) -> str:
    """Substitute cited fragments. The placeholder set must equal ``uses``."""
    found = _PLACE.findall(sql)
    if len(uses) != len(set(uses)) or set(found) != set(uses):
        raise EvalPackError("definition citation does not match the SQL placeholders")
    missing = [name for name in uses if name not in by_name]
    if missing:
        raise EvalPackError(f"unknown definition: {missing[0]}")
    out = sql
    for name in uses:
        _check_fragment(by_name[name]["sql"], dialect)
        out = out.replace("{" + name + "}", "(" + by_name[name]["sql"] + ")")
    if _PLACE.search(out):
        raise EvalPackError("unresolved definition placeholder")
    return out


def _cite(by_name: Mapping[str, Mapping[str, str]], uses: Sequence[str]) -> list[dict[str, str]]:
    return [{"name": name, "sentence": by_name[name]["sentence"]} for name in uses]


def run_select(con: Any, sql: str, *, dialect: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    text = _one_select(sql, dialect)
    cur = con.cursor()
    try:
        cur.execute(text)
        cols = [str(d[0]) for d in (cur.description or [])]
        return cols, [tuple(row) for row in cur.fetchall()]
    finally:
        cur.close()


def _limit_n(tree: exp.Expression) -> int | None:
    limit = tree.args.get("limit")
    if limit is None:
        return None
    expr = limit.expression
    if isinstance(expr, exp.Literal) and expr.is_int:
        return int(expr.this)
    raise EvalPackError("LIMIT must be an integer")


def _order_indexes(tree: exp.Expression, dialect: str) -> tuple[int, ...]:
    order = tree.args.get("order")
    if order is None:
        raise EvalPackError("ordered gold with LIMIT needs ORDER BY")
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        raise EvalPackError("ordered gold needs a SELECT")
    cols = list(select.expressions)
    indexes: list[int] = []
    for ordered in order.expressions:
        expr = ordered.this
        if isinstance(expr, exp.Literal) and expr.is_int:
            idx = int(expr.this) - 1
            if not 0 <= idx < len(cols):
                raise EvalPackError("ORDER BY ordinal is outside the select list")
            indexes.append(idx)
            continue
        target = expr.sql(dialect=dialect)
        found = None
        for i, col in enumerate(cols):
            if col.alias_or_name == expr.alias_or_name or col.sql(dialect=dialect) == target:
                found = i
                break
        if found is None:
            raise EvalPackError("ORDER BY key is not in the select list")
        indexes.append(found)
    return tuple(indexes)


def _key(row: Sequence[Any], indexes: Sequence[int]) -> tuple[Any, ...]:
    return tuple(row[i] for i in indexes)


def tie_aware(con: Any, sql: str, *, ordered: bool, dialect: str) -> dict[str, Any]:
    """Run gold on ``con``. Expand an ordered LIMIT n to the cutoff tie."""
    text = _one_select(sql, dialect)
    tree = _parse(text, dialect)
    limit_n = _limit_n(tree)
    if not ordered or limit_n is None:
        cols, rows = run_select(con, text, dialect=dialect)
        return _gold_body(
            text, cols, rows, ordered=ordered, tie_expanded=False, limit_n=limit_n, head_count=None
        )
    indexes = _order_indexes(tree, dialect)
    tree.set("limit", None)
    cols, rows = run_select(con, tree.sql(dialect=dialect), dialect=dialect)
    if len(rows) <= limit_n:
        return _gold_body(
            text, cols, rows, ordered=True, tie_expanded=False, limit_n=limit_n, head_count=None
        )
    cutoff = _key(rows[limit_n - 1], indexes)
    head = 0
    kept: list[tuple[Any, ...]] = []
    for row in rows:
        if _key(row, indexes) == cutoff:
            break
        kept.append(row)
        head += 1
    for row in rows[head:]:
        if _key(row, indexes) != cutoff:
            break
        kept.append(row)
    expanded = len(kept) > limit_n
    return _gold_body(
        text,
        cols,
        kept,
        ordered=True,
        tie_expanded=expanded,
        limit_n=limit_n,
        head_count=head if expanded else None,
    )


def _gold_body(
    sql: str,
    cols: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    ordered: bool,
    tie_expanded: bool,
    limit_n: int | None,
    head_count: int | None,
) -> dict[str, Any]:
    if not rows:
        raise EvalPackError("gold SQL returns no rows")
    return {
        "sql": sql,
        "columns": list(cols),
        "rows": [list(row) for row in rows],
        "ordered": ordered,
        "tie_expanded": tie_expanded,
        "limit_n": limit_n,
        "head_count": head_count,
    }


def build_pack(
    definitions: Sequence[Mapping[str, str]],
    templates: Sequence[Mapping[str, Any]],
    con: Any,
    *,
    dialect: str,
) -> dict[str, Any]:
    by_name = {d["name"]: d for d in definitions}
    if len(by_name) != len(definitions):
        raise EvalPackError("duplicate definition name")
    for item in definitions:
        _check_fragment(item["sql"], dialect)
    questions = []
    for template in templates:
        sql = render(template["gold_sql"], by_name, template["uses"], dialect=dialect)
        body = tie_aware(con, sql, ordered=bool(template["ordered"]), dialect=dialect)
        alts = []
        for alt in template.get("alt") or []:
            alt_sql = render(alt["sql"], by_name, alt["uses"], dialect=dialect)
            alt_body = tie_aware(con, alt_sql, ordered=bool(alt.get("ordered")), dialect=dialect)
            alts.append(
                {
                    "name": alt["name"],
                    "sentence": alt["sentence"],
                    "uses": _cite(by_name, alt["uses"]),
                    **alt_body,
                }
            )
        questions.append(
            {
                "id": template["id"],
                "template": template["template"],
                "question": template["question"],
                "uses": _cite(by_name, template["uses"]),
                "alt": alts,
                **body,
            }
        )
    pack = {"definitions": [dict(d) for d in definitions], "questions": questions}
    pack["scored_item_hashes"] = register_scored_items(pack)
    return pack


def _scored_texts(pack: Mapping[str, Any]) -> list[str]:
    """Gold SQL, alt SQL, and the question. Same strings a later write would store."""
    texts: list[str] = []
    for item in pack.get("questions") or []:
        texts.append(str(item.get("question") or ""))
        texts.append(str(item.get("sql") or ""))
        for alt in item.get("alt") or []:
            if isinstance(alt, Mapping):
                texts.append(str(alt.get("sql") or ""))
    return [text for text in texts if text.strip()]


def register_scored_items(pack: Mapping[str, Any]) -> list[str]:
    """Append this pack's item hashes to the scored-hash list.

    ``DMS_SCORED_ITEM_HASHES`` is the list ``reject_scored_write`` and
    ``filter_retrieved_rows`` already read. A hash already there stays.
    When ``DMS_SCORED_ITEM_HASHES_FILE`` names a file, the same hashes are
    appended there. That file is the other reader of this list, not a new one.
    """
    from dms_executor.skills_quarantine import SCORED_ITEM_HASHES_ENV, item_content_hash

    fresh: list[str] = []
    for text in _scored_texts(pack):
        digest = item_content_hash(text)
        if digest not in fresh:
            fresh.append(digest)
    raw = os.environ.get(SCORED_ITEM_HASHES_ENV, "")
    parts = [part for part in raw.replace(",", " ").split() if part]
    for digest in fresh:
        if digest not in parts:
            parts.append(digest)
    os.environ[SCORED_ITEM_HASHES_ENV] = ",".join(parts)
    file_env = os.environ.get("DMS_SCORED_ITEM_HASHES_FILE", "").strip()
    if file_env:
        _append_scored_hash_file(file_env, fresh)
    return fresh


def _append_scored_hash_file(path: str, hashes: Sequence[str]) -> None:
    file = Path(path)
    existing = file.read_text(encoding="utf-8") if file.is_file() else ""
    parts = [part for part in existing.replace(",", " ").split() if part]
    for digest in hashes:
        if digest not in parts:
            parts.append(digest)
    file.write_text("\n".join(parts) + ("\n" if parts else ""), encoding="utf-8")


def build_on_source(
    definitions: Sequence[Mapping[str, str]],
    templates: Sequence[Mapping[str, Any]],
    spec: SourceSpec,
) -> dict[str, Any]:
    """Build the pack by running every gold on the read-only source login."""
    with open_source(spec) as con:
        pack = build_pack(definitions, templates, con, dialect=DIALECT[spec.kind])
    pack["source"] = describe(spec)
    return pack


def _cell(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else round(value, 6)
    return value


def _norm(rows: Sequence[Sequence[Any]]) -> list[tuple[Any, ...]]:
    return [tuple(_cell(c) for c in row) for row in rows]


def _equal(
    got: Sequence[tuple[Any, ...]], gold: Sequence[tuple[Any, ...]], *, ordered: bool
) -> bool:
    if ordered:
        return list(got) == list(gold)
    return Counter(got) == Counter(gold)


def _contains(container: Sequence[tuple[Any, ...]], items: Sequence[tuple[Any, ...]]) -> bool:
    have = Counter(container)
    need = Counter(items)
    return all(have[key] >= count for key, count in need.items())


def _accepts(spec: Mapping[str, Any], rows: Sequence[Sequence[Any]] | None) -> bool:
    if rows is None:
        return False
    got = _norm(rows)
    pool = _norm(spec["rows"])
    if not spec.get("tie_expanded"):
        return _equal(got, pool, ordered=bool(spec.get("ordered")))
    n = int(spec["limit_n"])
    head = int(spec["head_count"] or 0)
    if not _contains(got, pool[:head]):
        return False
    if not _contains(pool, got):
        return False
    return len(got) == n or len(got) == len(pool)


def align_rows(rows: Sequence[Any] | None, columns: Sequence[str]) -> list[tuple[Any, ...]] | None:
    if rows is None:
        return None
    if not rows:
        return []
    first = rows[0]
    if isinstance(first, Mapping):
        return [tuple(row.get(col) for col in columns) for row in rows]
    return [tuple(row) for row in rows]


def grade_rows(item: Mapping[str, Any], rows: Sequence[Sequence[Any]] | None, decision: str) -> str:
    """Grade one served decision. The question text is not read."""
    if decision == "reconfirm":
        return "reconfirm"
    if decision == "abstain":
        return "abstain"
    if decision != "answer":
        return "reconfirm"
    aligned = align_rows(rows, item.get("columns") or [])
    if _accepts(item, aligned):
        return "correct"
    for alt in item.get("alt") or []:
        if _accepts(alt, align_rows(rows, alt.get("columns") or [])):
            return "correct_alt"
    return "WRONG"


def _grade52():
    root = str(Path(__file__).resolve().parents[1] / "verify")
    if root not in sys.path:
        sys.path.insert(0, root)
    import grade52

    return grade52


def path_group(path: str) -> str:
    """AI vs rule, the same groups ``grade52.path_group`` uses."""
    return _grade52().path_group(path)


def summarize(cases: Sequence[Mapping[str, Any]], *, arm: str) -> dict[str, Any]:
    tally = {name: 0 for name in BUCKETS}
    correct_paths = {"ai": 0, "rule": 0, "unattributed": 0}
    served_paths = {"ai": 0, "rule": 0, "unattributed": 0}
    times: list[float] = []
    for case in cases:
        bucket = str(case["bucket"])
        if bucket not in tally:
            raise EvalPackError(f"unknown bucket: {bucket}")
        tally[bucket] += 1
        group = str(case["path"])
        if group not in correct_paths:
            raise EvalPackError(f"unknown path: {group}")
        times.append(float(case["t_ms"]))
        if bucket in ("correct", "correct_alt", "WRONG"):
            served_paths[group] += 1
        if bucket == "correct":
            correct_paths[group] += 1
    if sum(tally.values()) != len(cases):
        raise EvalPackError("buckets do not add up to n")
    return {
        "arm": arm,
        "n": len(cases),
        **tally,
        "ai": correct_paths["ai"],
        "rule": correct_paths["rule"],
        "unattributed": correct_paths["unattributed"],
        "paths_correct": correct_paths,
        "paths_served": served_paths,
        "p50_ms": None if not times else float(statistics.median(times)),
        "cases": [dict(case) for case in cases],
    }


def report_line(report: Mapping[str, Any]) -> str:
    p50 = report["p50_ms"]
    shown = "none" if p50 is None else f"{p50:.3f}"
    return (
        f"arm={report['arm']} n={report['n']} correct={report['correct']} "
        f"correct_alt={report['correct_alt']} WRONG={report['WRONG']} "
        f"reconfirm={report['reconfirm']} abstain={report['abstain']} "
        f"ai={report['ai']} rule={report['rule']} p50_ms={shown}"
    )


def schema_text(con: Any, kind: str) -> str:
    cur = con.cursor()
    try:
        if kind == "sqlite":
            cur.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            return "\n\n".join(str(row[0]) for row in cur.fetchall() if row[0])
        if kind == "postgresql":
            cur.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY table_name, ordinal_position"
            )
        elif kind == "mysql":
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION"
            )
        else:
            cur.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA', 'sys') "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION"
            )
        return "\n".join(f"{row[0]}.{row[1]} {row[2]}" for row in cur.fetchall())
    finally:
        cur.close()


def _reject_model_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _MODEL_KEYS:
                raise EvalPackError("model key refused")
            _reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_model_keys(item)


def raw_body(
    schema: str, definitions: Sequence[Mapping[str, str]], question: str
) -> dict[str, Any]:
    """One OpenVault request. Role string only. Definitions are included. Alts are not."""
    lines = [f"- {item['name']}: {item['sentence']}\n  SQL: {item['sql']}" for item in definitions]
    user = "SCHEMA:\n" + schema + "\n\nDEFINITIONS:\n" + "\n".join(lines)
    user += "\n\nQUESTION:\n" + question + "\n"
    body = {
        "role": RAW_ROLE,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
    }
    _reject_model_keys(body)
    return body


def parse_model_reply(payload: Any) -> tuple[str, str | None]:
    """Read the model's JSON decision. Prose and a missing decision reconfirm."""
    content: Any = payload
    if (
        isinstance(payload, dict)
        and isinstance(payload.get("choices"), list)
        and payload["choices"]
    ):
        message = payload["choices"][0].get("message") or {}
        content = message.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return "reconfirm", None
    if not isinstance(content, dict):
        return "reconfirm", None
    decision = content.get("decision")
    if decision not in ("answer", "reconfirm", "abstain"):
        return "reconfirm", None
    if decision != "answer":
        return decision, None
    sql = content.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return "reconfirm", None
    return "answer", sql


def run_raw(
    pack: Mapping[str, Any],
    con: Any,
    schema: str,
    transport: Callable[[Mapping[str, Any]], Any],
    *,
    dialect: str,
) -> dict[str, Any]:
    """Grade the raw-LLM arm. One transport call per question, no retry."""
    cases = []
    for item in pack["questions"]:
        body = raw_body(schema, pack["definitions"], item["question"])
        started = time.perf_counter()
        try:
            reply = transport(body)
            decision, sql = parse_model_reply(reply)
        except Exception:
            decision, sql = "reconfirm", None
        rows = None
        if decision == "answer" and sql:
            try:
                _, rows = run_select(con, sql, dialect=dialect)
            except Exception:
                decision, rows = "reconfirm", None
        elapsed = (time.perf_counter() - started) * 1000.0
        cases.append(
            {
                "id": item["id"],
                "bucket": grade_rows(item, rows, decision),
                "path": "ai",
                "t_ms": elapsed,
            }
        )
    return summarize(cases, arm="raw")


def from_envelope(env: Mapping[str, Any], *, t_ms: float = 0, qid: str = "") -> dict[str, Any]:
    """Turn one DMS ask envelope into a result the shared grader accepts."""
    grader = _grade52()
    reconfirm = env.get("reconfirm")
    if isinstance(reconfirm, Mapping) and reconfirm:
        decision = "reconfirm"
    elif env.get("abstained") is True or env.get("abstain_reason"):
        decision = "abstain"
    else:
        decision = "answer"
    return {
        "id": qid or str(env.get("id") or ""),
        "rows": env.get("rows") or [],
        "decision": decision,
        "serve_path": grader.serve_path(env),
        "t_ms": t_ms,
    }


def run_dms(pack: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Grade DMS results with the same buckets as the raw arm."""
    by_id = {item["id"]: item for item in pack["questions"]}
    cases = []
    for result in results:
        item = by_id[str(result["id"])]
        cases.append(
            {
                "id": item["id"],
                "bucket": grade_rows(
                    item, result.get("rows"), str(result.get("decision") or "answer")
                ),
                "path": path_group(str(result.get("serve_path") or "unattributed")),
                "t_ms": float(result.get("t_ms") or 0),
            }
        )
    return summarize(cases, arm="dms")


def openvault_transport(
    url: str, key_env: str = "OPENVAULT_API_KEY"
) -> Callable[[Mapping[str, Any]], Any]:
    """POST one chat completion. The key is read from the env and not logged."""
    base = url.strip().rstrip("/")
    if not base:
        raise EvalPackError("openvault_url_missing")

    def send(body: Mapping[str, Any]) -> Any:
        import httpx

        key = os.environ.get(key_env, "").strip()
        if not key:
            key = os.environ.get("CORTEX_API_KEY", "").strip()
        if not key.startswith("ov_"):
            raise EvalPackError("openvault_key_missing")
        try:
            response = httpx.post(
                base + CHAT_PATH,
                headers={"Authorization": "Bearer " + key},
                json=dict(body),
                timeout=60.0,
            )
            response.raise_for_status()
        except EvalPackError:
            raise
        except Exception as exc:
            raise EvalPackError(f"openvault_http_{type(exc).__name__}") from None
        return response.json()

    return send


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=1, default=str) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="run gold on the read-only source and write a pack")
    build.add_argument("--definitions", type=Path, required=True)
    build.add_argument("--templates", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    grade = sub.add_parser("grade", help="grade the raw arm or a DMS results file")
    grade.add_argument("--pack", type=Path, required=True)
    grade.add_argument("--arm", choices=("raw", "dms"), required=True)
    grade.add_argument("--definitions", type=Path)
    grade.add_argument("--results", type=Path)
    grade.add_argument("--openvault-url", default=os.environ.get("OPENVAULT_URL", ""))
    grade.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "build":
        spec = load_source(args.definitions)
        pack = build_on_source(
            load_definitions(args.definitions), load_templates(args.templates), spec
        )
        _write(args.out, pack)
        print(f"pack questions={len(pack['questions'])} source={pack['source']}")
        return 0
    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    if args.arm == "dms":
        if args.results is None:
            raise SystemExit("eval_pack: --arm dms needs --results")
        results = json.loads(args.results.read_text(encoding="utf-8"))
        report = run_dms(pack, results)
    else:
        if args.definitions is None:
            raise SystemExit("eval_pack: --arm raw needs --definitions for the read-only login")
        spec = load_source(args.definitions)
        transport = openvault_transport(args.openvault_url)
        with open_source(spec) as con:
            report = run_raw(
                pack,
                con,
                schema_text(con, spec.kind),
                transport,
                dialect=DIALECT[spec.kind],
            )
    _write(args.out, report)
    print(report_line(report))
    return 1 if report["WRONG"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
