"""SCHEMA-RETRY-01: structured repair after a database error.

The ladder runs only when ``DMS_SCHEMA_RETRY`` is on. It parses engine
exceptions (class and vendor code, then the engine's own message shape)
into a typed hint. It does not read the question. The database message
and cell values never leave this module.

Rungs, each only after the previous one failed and the ask budget remains:
1. ``repair_hint`` (kind, identifier, granted candidates)
2. granted schema slice for the referenced tables and their declared joins
3. ``route_hint=escalate`` (a role string; OpenVault picks the model)
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cortex_client.compute import schema_retry_enabled
from cortex_client.insights import schema_context_enabled

from dms_executor.demo_warehouse import SERVING_DIALECT

_REPAIR_LINE = "Fix only the identifier and kind named in repair_hint."
_KINDS = frozenset(
    {
        "missing_column",
        "missing_table",
        "ambiguous_column",
        "type_mismatch",
        "function_missing",
        "syntax",
        "other",
    }
)
_TOP = 8
_IDENT = r"[A-Za-z_][A-Za-z0-9_]{0,63}"
_QUOTED = re.compile(rf"""["'`]({_IDENT})["'`]""")
_DUCKDB_TYPES = frozenset(
    {
        "BinderException",
        "CatalogException",
        "ParserException",
        "ConversionException",
        "InvalidInputException",
    }
)
# Vendor codes. These name the engine failure, not a word in the question.
_PG_KIND = {
    "42703": "missing_column",
    "42P01": "missing_table",
    "42702": "ambiguous_column",
    "42883": "function_missing",
    "42804": "type_mismatch",
    "42846": "type_mismatch",
    "22P02": "type_mismatch",
    "42601": "syntax",
}
_MYSQL_KIND = {
    1054: "missing_column",
    1146: "missing_table",
    1052: "ambiguous_column",
    1305: "function_missing",
    1370: "function_missing",
    1064: "syntax",
    1292: "type_mismatch",
    1366: "type_mismatch",
}
_SQLSERVER_KIND = {
    207: "missing_column",
    208: "missing_table",
    209: "ambiguous_column",
    195: "function_missing",
    2812: "function_missing",
    245: "type_mismatch",
    8114: "type_mismatch",
    102: "syntax",
}
_FROM_JOIN = re.compile(rf"\b(?:from|join)\s+({_IDENT})\b", re.I)
_TABLE_WITH_NAME = re.compile(rf"Table with name ({_IDENT}) does not exist", re.I)
_FUNC_MATCH = re.compile(
    rf"function matches the given name and argument types '({_IDENT})",
    re.I,
)
_FUNC_NAME = re.compile(rf"Function with name ({_IDENT}) does not exist", re.I)
_AFTER_COLON = re.compile(rf":\s*({_IDENT})\b")


def repair_ok_counts(attempts: Sequence[Mapping[str, Any]] | None) -> dict[str, int]:
    """``repair_ok@rungN`` counts from attempt records. Empty when none served."""
    counts: dict[str, int] = {}
    for item in attempts or []:
        if not isinstance(item, Mapping):
            continue
        name = item.get("repair")
        if isinstance(name, str) and name.startswith("repair_ok@rung"):
            counts[name] = counts.get(name, 0) + 1
    return counts


def route_refused(payload: Mapping[str, Any] | None) -> bool:
    """True when OpenVault/Cortex refused the role hint. Not a text scan."""
    if not isinstance(payload, Mapping):
        return False
    for key in ("_insights_http_status", "ai_call_status"):
        if payload.get(key) == 422:
            return True
    return payload.get("route_error") in {"unknown_role", "no_route"}


def format_repair_block(hint: Mapping[str, Any]) -> str:
    """One prompt block. Only kind, identifier, and candidates."""
    safe = {
        "kind": hint.get("kind") if hint.get("kind") in _KINDS else "other",
        "identifier": hint.get("identifier") if isinstance(hint.get("identifier"), str) else None,
        "candidates": _safe_candidates(hint.get("candidates")),
    }
    blob = json.dumps(safe, sort_keys=True, separators=(",", ":"))
    return f"repair_hint:\n{blob}\n{_REPAIR_LINE}"


def format_slice_block(schema_slice: Mapping[str, Any]) -> str:
    """Types, nullability, and joins. Sample values are not in this block."""
    safe = _safe_slice(schema_slice)
    blob = json.dumps(safe, sort_keys=True, separators=(",", ":"))
    return f"schema_slice:\n{blob}"


def parse_engine_error(exc: BaseException) -> tuple[str, str | None]:
    """``(kind, token)``. ``token`` is text from the engine format, or None.

    The caller keeps ``token`` only when that identifier is in the model's SQL.
    """
    state = _pg_state(exc)
    if state in _PG_KIND:
        return _PG_KIND[state], _message_token(str(exc), _PG_KIND[state])
    if type(exc).__name__ in _DUCKDB_TYPES:
        return _parse_duckdb(exc)
    if _is_sqlite(exc):
        return _parse_sqlite(exc)
    errno = _mysql_errno(exc)
    if errno in _MYSQL_KIND:
        return _MYSQL_KIND[errno], _message_token(str(exc), _MYSQL_KIND[errno])
    number = _sqlserver_number(exc)
    if number in _SQLSERVER_KIND:
        return _SQLSERVER_KIND[number], _message_token(str(exc), _SQLSERVER_KIND[number])
    return "other", None


def identifier_in_sql(sql: str, token: str | None, dialect: str) -> str | None:
    """The SQL's own spelling of ``token``, or None when the SQL did not write it."""
    if not isinstance(token, str) or not token:
        return None
    found = _sql_identifiers(sql, dialect)
    return found.get(token.lower())


def is_schema_db_outcome(outcome: str) -> bool:
    """EXPLAIN or execution failure. Grant, hostile, and policy codes are not."""
    text = str(outcome or "")
    if text.startswith("db_error:"):
        return True
    return "explain:" in text


@dataclass
class RungPlan:
    """What the next model call should carry, or an abstain reason."""

    abstain: str | None = None
    rung: str = ""
    kind: str = "other"
    repair_hint: dict[str, Any] | None = None
    schema_slice: dict[str, Any] | None = None
    route_hint: str | None = None
    prompt: str = ""


@dataclass
class SchemaLadder:
    """Per-reading rung counter. The ask budget stays on ``AskCallBudget``."""

    grantable: set[str]
    warehouse: Path | None
    dialect: str
    ontology: Any = None
    space_id: str | None = None
    issued: int = 0
    pending_rung: str | None = None
    prompt_suffix: str = ""
    last_kind: str = "other"
    _columns: list[tuple[str, str, str, bool | None]] = field(default_factory=list)

    def begin(self) -> None:
        """A new reading starts at rung 0. The budget is not reset."""
        self.issued = 0
        self.pending_rung = None
        self.prompt_suffix = ""
        self.last_kind = "other"

    def plan(
        self,
        *,
        outcome: str,
        sql: str,
        attempts: list[dict[str, Any]],
        budget: Any,
        question: str,
    ) -> RungPlan | None:
        """None when this failure is not a schema-retry database error."""
        if not schema_retry_enabled() or not is_schema_db_outcome(outcome):
            return None
        hint = build_repair_hint(
            sql,
            warehouse=self.warehouse,
            grantable=self.grantable,
            dialect=self.dialect,
        )
        kind = str(hint.get("kind") or "other")
        self.last_kind = kind
        if attempts:
            attempts[-1]["error_kind"] = kind
        if not budget.allow() or self.issued >= 3:
            return RungPlan(abstain=f"schema_repair_exhausted:{kind}", kind=kind)
        self.issued += 1
        rung = f"rung{self.issued}"
        schema_slice = None
        route_hint = None
        if self.issued == 2:
            schema_slice = build_schema_slice(
                sql,
                warehouse=self.warehouse,
                grantable=self.grantable,
                dialect=self.dialect,
                ontology=self.ontology,
            )
        elif self.issued == 3:
            route_hint = "escalate"
        from dms_executor.sql_loop import feedback_prompt

        base = feedback_prompt(question, previous_sql=sql, reason=outcome)
        parts = [format_repair_block(hint)]
        if schema_slice is not None:
            parts.append(format_slice_block(schema_slice))
        self.prompt_suffix = "\n".join(parts)
        self.pending_rung = rung
        return RungPlan(
            rung=rung,
            kind=kind,
            repair_hint=hint,
            schema_slice=schema_slice,
            route_hint=route_hint,
            prompt=base + "\n" + self.prompt_suffix,
        )

    def attach_schema(
        self,
        ctx: dict[str, Any],
        question: str,
        schema_slice: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Rung 2 prompt. Samples only through ``prepare_generate_context``."""
        if not schema_context_enabled():
            return ctx
        from dms_executor.schema_context import prepare_generate_context

        described = _renderer_schema(
            self.warehouse,
            self.grantable,
            self.dialect,
            self.space_id,
            schema_slice,
        )
        return prepare_generate_context(
            ctx,
            question=question,
            schema=described,
            grantable=self.grantable,
            ontology=self.ontology,
            dialect=self.dialect,
            space_id=self.space_id,
        )


def build_repair_hint(
    sql: str,
    *,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    exc: BaseException | None = None,
) -> dict[str, Any]:
    """Typed hint. No message text and no cell values."""
    caught = exc if exc is not None else _capture_error(sql, warehouse)
    if caught is None:
        kind, token = "other", None
    else:
        kind, token = parse_engine_error(caught)
    ident = identifier_in_sql(sql, token, dialect)
    candidates = select_candidates(
        kind,
        ident,
        sql=sql,
        warehouse=warehouse,
        grantable=grantable,
        dialect=dialect,
    )
    return {"kind": kind, "identifier": ident, "candidates": candidates}


def select_candidates(
    kind: str,
    identifier: str | None,
    *,
    sql: str,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    columns: Sequence[tuple[str, str, str]] | None = None,
    tables: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """Top 8 granted names. ``columns``/``tables`` override the catalog read."""
    if kind == "missing_table":
        names = list(tables) if tables is not None else _catalog_tables(warehouse)
        granted = [name for name in names if _table_granted(name, grantable, dialect)]
        return _rank(identifier, [(name, "table") for name in granted])
    loaded = list(columns) if columns is not None else _catalog_columns(warehouse)
    refs = {name.lower() for name in _referenced_tables(sql, dialect)}
    if not refs:
        return []
    pool: list[tuple[str, str]] = []
    for table, column, typ in loaded:
        if table.lower() not in refs:
            continue
        if not _column_granted(table, column, grantable, dialect):
            continue
        pool.append((f"{table}.{column}", typ))
    return _rank(identifier, pool)


def build_schema_slice(
    sql: str,
    *,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    ontology: Any = None,
) -> dict[str, Any]:
    """Referenced tables, their granted columns, and declared joins. No samples."""
    refs = _referenced_tables(sql, dialect)
    joins = _declared_joins(ontology)
    names = {name.lower(): name for name in refs}
    for link in joins:
        if link["from"].lower() in names and _table_granted(link["to"], grantable, dialect):
            names.setdefault(link["to"].lower(), link["to"])
        if link["to"].lower() in names and _table_granted(link["from"], grantable, dialect):
            names.setdefault(link["from"].lower(), link["from"])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for table, column, typ, nullable in _catalog_column_rows(warehouse):
        if table.lower() not in names:
            continue
        if not _column_granted(table, column, grantable, dialect):
            continue
        item: dict[str, Any] = {"name": column, "type": typ}
        if nullable is not None:
            item["nullable"] = nullable
        grouped.setdefault(table, []).append(item)
    kept = {table.lower() for table in grouped}
    join_out = [
        link
        for link in joins
        if link["from"].lower() in kept and link["to"].lower() in kept
    ]
    tables_out = [
        {"name": table, "columns": cols}
        for table, cols in sorted(grouped.items())
    ]
    return {"tables": tables_out, "joins": join_out}


def feedback_payload(
    plan: RungPlan,
    *,
    previous_sql: str,
    reason: str,
) -> dict[str, Any]:
    """``sql_loop_feedback`` for one rung. The message is not a field."""
    from dms_executor.sql_loop import mask_feedback_text

    feedback: dict[str, Any] = {
        "previous_sql": mask_feedback_text(previous_sql or ""),
        "reason": mask_feedback_text(reason),
        "prompt": plan.prompt,
    }
    if plan.repair_hint is not None:
        feedback["repair_hint"] = plan.repair_hint
    if plan.schema_slice is not None:
        feedback["schema_slice"] = plan.schema_slice
    return feedback


def _parse_duckdb(exc: BaseException) -> tuple[str, str | None]:
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if name == "ParserException" or "syntax error" in low:
        return "syntax", _quoted(text)
    if name == "ConversionException" or "could not convert" in low:
        return "type_mismatch", _quoted(text)
    if "ambiguous reference to column" in low or "ambiguous column" in low:
        return "ambiguous_column", _quoted(text)
    if (
        "no function matches" in low
        or "function with name" in low
        or "scalar function" in low
    ):
        return "function_missing", _func_token(text)
    if "table" in low and "does not exist" in low:
        return "missing_table", _table_token(text)
    if "column" in low and ("not found" in low or "does not exist" in low):
        return "missing_column", _quoted(text)
    return "other", None


def _parse_sqlite(exc: BaseException) -> tuple[str, str | None]:
    text = str(exc)
    low = text.lower()
    if low.startswith("no such column"):
        return "missing_column", _tail_token(text)
    if low.startswith("no such table"):
        return "missing_table", _tail_token(text)
    if low.startswith("ambiguous column name"):
        return "ambiguous_column", _tail_token(text)
    if low.startswith("no such function"):
        return "function_missing", _tail_token(text)
    if "syntax error" in low:
        return "syntax", None
    return "other", None


def _message_token(text: str, kind: str) -> str | None:
    if kind == "function_missing":
        return _func_token(text) or _quoted(text) or _tail_token(text)
    if kind == "missing_table":
        return _table_token(text) or _quoted(text) or _tail_token(text)
    if kind == "syntax":
        return None
    return _quoted(text) or _tail_token(text)


def _quoted(text: str) -> str | None:
    match = _QUOTED.search(text or "")
    return match.group(1) if match else None


def _table_token(text: str) -> str | None:
    match = _TABLE_WITH_NAME.search(text or "")
    if match:
        return match.group(1)
    return _quoted(text)


def _func_token(text: str) -> str | None:
    for pattern in (_FUNC_MATCH, _FUNC_NAME):
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return _quoted(text)


def _tail_token(text: str) -> str | None:
    match = _AFTER_COLON.search(text or "")
    return match.group(1) if match else None


def _pg_state(exc: BaseException) -> str | None:
    for attr in ("sqlstate", "pgcode"):
        val = getattr(exc, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    diag = getattr(exc, "diag", None)
    if diag is not None:
        val = getattr(diag, "sqlstate", None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _mysql_errno(exc: BaseException) -> int | None:
    val = getattr(exc, "errno", None)
    if isinstance(val, int) and val in _MYSQL_KIND:
        return val
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int) and args[0] in _MYSQL_KIND:
        return args[0]
    return None


def _sqlserver_number(exc: BaseException) -> int | None:
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int) and args[0] in _SQLSERVER_KIND:
        return args[0]
    return None


def _is_sqlite(exc: BaseException) -> bool:
    module = type(exc).__module__
    if module == "sqlite3":
        return True
    return type(exc).__name__ in {"OperationalError", "DatabaseError"} and module == "sqlite3"


def _sql_identifiers(sql: str, dialect: str) -> dict[str, str]:
    """Lower name to the spelling the SQL wrote. Literals are not identifiers."""
    found: dict[str, str] = {}
    try:
        from sqlglot import exp, parse_one

        tree = parse_one(sql or "", read=dialect or SERVING_DIALECT)
    except Exception:
        tree = None
    if tree is not None:
        for node in tree.find_all(exp.Column, exp.Table):
            name = node.name
            if isinstance(name, str) and name:
                found.setdefault(name.lower(), name)
        for node in tree.find_all(exp.Anonymous):
            name = node.name
            if isinstance(name, str) and name:
                found.setdefault(name.lower(), name)
        return found
    for token in re.findall(_IDENT, sql or ""):
        found.setdefault(token.lower(), token)
    return found


def _referenced_tables(sql: str, dialect: str) -> list[str]:
    try:
        from sqlglot import exp, parse_one

        tree = parse_one(sql or "", read=dialect or SERVING_DIALECT)
    except Exception:
        tree = None
    if tree is None:
        return [match.group(1) for match in _FROM_JOIN.finditer(sql or "")]
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}
    names: list[str] = []
    seen: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name or name.lower() in ctes or name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
    return names


def _table_granted(name: str, grantable: set[str], dialect: str) -> bool:
    if not name or not grantable:
        return False
    from dms_executor.grant_struct import relation_gap

    return relation_gap(name, grantable=set(grantable), dialect=dialect) is None


def _column_limits(table: str, grantable: set[str]) -> set[str] | None:
    """None: every column of a granted table. A set: only those column names."""
    heads = {table.lower(), table.lower().split(".")[-1]}
    marked: set[str] = set()
    for item in grantable:
        low = str(item).lower()
        for head in heads:
            prefix = head + "."
            if not low.startswith(prefix):
                continue
            tail = low[len(prefix):]
            if tail and "." not in tail and re.fullmatch(_IDENT, tail):
                marked.add(tail)
    return marked or None


def _column_granted(table: str, column: str, grantable: set[str], dialect: str) -> bool:
    if not _table_granted(table, grantable, dialect):
        return False
    marked = _column_limits(table, grantable)
    if marked is None:
        return True
    return column.lower() in marked


def _rank(identifier: str | None, rows: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    if not identifier:
        return []
    from dms_executor.schema_context import _cosine, _trigrams

    ident = identifier.lower()
    ident_vec = _trigrams(identifier)
    scored: list[tuple[float, str, str]] = []
    seen: set[str] = set()
    for name, typ in rows:
        leaf = name.split(".")[-1]
        if leaf.lower() == ident or name.lower() == ident:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        edit = _edit_score(ident, leaf.lower())
        score = max(edit, _cosine(ident_vec, _trigrams(leaf)))
        scored.append((score, name, typ))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"name": name, "type": typ} for _score, name, typ in scored[:_TOP]]


def _edit_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    dist = _levenshtein(left, right)
    return 1.0 - dist / max(len(left), len(right))


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    if len(left) < len(right):
        left, right = right, left
    prev = list(range(len(right) + 1))
    for i, ca in enumerate(left, 1):
        cur = [i]
        for j, cb in enumerate(right, 1):
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _capture_error(sql: str, warehouse: Path | None) -> BaseException | None:
    """Re-run EXPLAIN, then the statement. The rows are not read."""
    if warehouse is None or not Path(warehouse).is_file() or not str(sql or "").strip():
        return None
    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(Path(warehouse))
    try:
        try:
            con.execute(f"EXPLAIN {sql}")
        except Exception as exc:  # noqa: BLE001 - the engine failure is the hint source
            return exc
        try:
            con.execute(sql)
        except Exception as exc:  # noqa: BLE001 - execution failure, rows are not fetched
            return exc
        return None
    finally:
        con.close()


def _catalog_column_rows(
    warehouse: Path | None,
) -> list[tuple[str, str, str, bool | None]]:
    if warehouse is None or not Path(warehouse).is_file():
        return []
    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(Path(warehouse))
    try:
        try:
            rows = con.execute(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_schema = 'main' "
                "ORDER BY table_name, ordinal_position"
            ).fetchall()
            nullable = True
        except Exception:  # noqa: BLE001 - older catalogs omit is_nullable
            nullable = False
            rows = con.execute(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns WHERE table_schema = 'main' "
                "ORDER BY table_name, ordinal_position"
            ).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    out: list[tuple[str, str, str, bool | None]] = []
    for row in rows:
        table, column, typ = str(row[0]), str(row[1]), str(row[2] or "")
        flag: bool | None = None
        if nullable:
            flag = str(row[3] or "").upper() == "YES"
        out.append((table, column, typ, flag))
    return out


def _catalog_columns(warehouse: Path | None) -> list[tuple[str, str, str]]:
    return [(table, column, typ) for table, column, typ, _flag in _catalog_column_rows(warehouse)]


def _catalog_tables(warehouse: Path | None) -> list[str]:
    seen: list[str] = []
    known: set[str] = set()
    for table, _column, _typ in _catalog_columns(warehouse):
        if table.lower() in known:
            continue
        known.add(table.lower())
        seen.append(table)
    return seen


def _declared_joins(ontology: Any) -> list[dict[str, str]]:
    if ontology is None:
        return []
    from dms_executor.schema_context import ontology_payload

    out: list[dict[str, str]] = []
    for item in ontology_payload(ontology).get("relationships") or []:
        if not isinstance(item, Mapping):
            continue
        left = str(item.get("from") or "")
        right = str(item.get("to") or "")
        left_col = str(item.get("from_column") or "")
        right_col = str(item.get("to_column") or "")
        if left and right and left_col and right_col:
            out.append(
                {
                    "from": left,
                    "from_column": left_col,
                    "to": right,
                    "to_column": right_col,
                }
            )
    return out


def _renderer_schema(
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    space_id: str | None,
    schema_slice: Mapping[str, Any],
) -> dict[str, Any]:
    """Catalog shape for ``prepare_generate_context``. Samples come from its index."""
    from dms_executor.schema_context import lookup_schema

    looked: dict[str, Any] = {}
    try:
        looked, _stamp = lookup_schema(warehouse, grantable, dialect, space_id)
    except Exception:  # noqa: BLE001 - no index means types only
        looked = {}
    samples = _sample_index(looked if isinstance(looked, Mapping) else {})
    keep = {
        str(item.get("name") or "").lower()
        for item in (schema_slice.get("tables") or [])
        if isinstance(item, Mapping) and item.get("name")
    }
    datasets: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for table, column, typ, nullable in _catalog_column_rows(warehouse):
        if keep and table.lower() not in keep:
            continue
        if not _column_granted(table, column, grantable, dialect):
            continue
        col: dict[str, Any] = {
            "name": column,
            "type": typ,
            "description": "",
            "primary_key": False,
            "distinct": None,
            "values": [],
            "samples": [],
        }
        if nullable is not None:
            col["nullable"] = nullable
        cached = samples.get(f"{table}.{column}".lower())
        if cached:
            col["samples"] = list(cached[:3])
        grouped.setdefault(table, []).append(col)
    for table, cols in sorted(grouped.items()):
        datasets.append({"name": table, "description": "", "columns": cols})
    body: dict[str, Any] = {"datasets": datasets, "relationships": []}
    if dialect:
        body["dialect"] = dialect
    return body


def _sample_index(schema: Mapping[str, Any]) -> dict[str, list[str]]:
    """Up to 3 samples already stored on a schema description. No new read."""
    out: dict[str, list[str]] = {}
    datasets = schema.get("datasets")
    if not isinstance(datasets, Sequence) or isinstance(datasets, (str, bytes)):
        return out
    for dataset in datasets:
        if not isinstance(dataset, Mapping):
            continue
        table = str(dataset.get("name") or "")
        columns = dataset.get("columns")
        if not table or not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
            continue
        for column in columns:
            if not isinstance(column, Mapping):
                continue
            name = str(column.get("name") or "")
            if not name:
                continue
            raw = column.get("samples")
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
                raw = column.get("values")
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                continue
            values = [str(item) for item in raw if isinstance(item, str) and item][:3]
            if values:
                out[f"{table}.{name}".lower()] = values
    return out


def _safe_candidates(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        typ = item.get("type")
        if isinstance(name, str) and name and isinstance(typ, str):
            out.append({"name": name, "type": typ})
    return out


def _safe_slice(raw: Mapping[str, Any]) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    for table in raw.get("tables") or []:
        if not isinstance(table, Mapping):
            continue
        name = table.get("name")
        if not isinstance(name, str) or not name:
            continue
        columns: list[dict[str, Any]] = []
        for column in table.get("columns") or []:
            if not isinstance(column, Mapping):
                continue
            col_name = column.get("name")
            col_type = column.get("type")
            if not isinstance(col_name, str) or not isinstance(col_type, str):
                continue
            item: dict[str, Any] = {"name": col_name, "type": col_type}
            if isinstance(column.get("nullable"), bool):
                item["nullable"] = column["nullable"]
            columns.append(item)
        tables.append({"name": name, "columns": columns})
    joins: list[dict[str, str]] = []
    for link in raw.get("joins") or []:
        if not isinstance(link, Mapping):
            continue
        kept = {
            key: link.get(key)
            for key in ("from", "from_column", "to", "to_column")
        }
        if all(isinstance(val, str) and val for val in kept.values()):
            joins.append({key: str(val) for key, val in kept.items()})
    return {"tables": tables, "joins": joins}
