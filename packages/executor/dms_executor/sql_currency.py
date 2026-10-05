"""Currency unit gate for the generative ask path (A2-05 / dms#261).

sqlglot lives here only. Swap: replace this module with a Cortex HTTP
verify-unit call or another dialect parser. generative_ask calls
currency_mismatch_reason. The ask envelope lazily imports
served_column_sources. A parse failure yields no traced source.
SELECT *, t.*, a star CTE, or UNION is expanded only when the caller
passes the connection schema. No schema, a schema qualify cannot use,
or a column that is still untraced: that column stays masked.
This module does not load a schema and does not read DEMO_TABLES.
No FX conversion. Mismatch or unresolved currency is ABSTAIN.
``tables_read`` (BANK-02 / dms#269) also lives here so the parser keeps one swap
point: it names the tables an executed statement reads, for the audit export.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp, parse_one
from sqlglot.lineage import lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope, traverse_scope

from dms_executor.demo_warehouse import connect_file

_DIALECT = "duckdb"

# Unambiguous ISO 4217 tokens. Skip codes that are common English words (TRY, ALL, TOP).
_ISO = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CNY",
        "MYR",
        "SGD",
        "AUD",
        "CAD",
        "HKD",
        "NZD",
        "INR",
        "KRW",
        "THB",
        "IDR",
        "PHP",
        "VND",
        "TWD",
        "CHF",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "CZK",
        "HUF",
        "RUB",
        "BRL",
        "MXN",
        "ZAR",
        "AED",
        "SAR",
        "QAR",
        "KWD",
        "BHD",
        "OMR",
        "EGP",
        "NGN",
        "KES",
        "ILS",
        "PKR",
        "BDT",
        "LKR",
        "NPR",
        "CNH",
    }
)

_ALIAS_TO_ISO: dict[str, str] = {
    "usd": "USD",
    "eur": "EUR",
    "gbp": "GBP",
    "jpy": "JPY",
    "cny": "CNY",
    "cnh": "CNY",
    "rmb": "CNY",
    "myr": "MYR",
    "rm": "MYR",
    "sgd": "SGD",
    "aud": "AUD",
    "cad": "CAD",
    "hkd": "HKD",
    "nzd": "NZD",
    "inr": "INR",
    "dollar": "USD",
    "dollars": "USD",
    "euro": "EUR",
    "euros": "EUR",
    "pound": "GBP",
    "pounds": "GBP",
    "sterling": "GBP",
    "yuan": "CNY",
    "renminbi": "CNY",
    "ringgit": "MYR",
    "ringgits": "MYR",
    "yen": "JPY",
    "rupee": "INR",
    "rupees": "INR",
}

_WORD_ISO: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdollars?\b", re.I), "USD"),
    (re.compile(r"\beuros?\b", re.I), "EUR"),
    (re.compile(r"\bpounds?\b", re.I), "GBP"),
    (re.compile(r"\bsterling\b", re.I), "GBP"),
    (re.compile(r"\byuan\b", re.I), "CNY"),
    (re.compile(r"\brenminbi\b", re.I), "CNY"),
    (re.compile(r"\bringgits?\b", re.I), "MYR"),
    (re.compile(r"\byen\b", re.I), "JPY"),
    (re.compile(r"\brupees?\b", re.I), "INR"),
)

# Whole-token ISO after NFKC. Underscore keeps revenue_usd from counting as USD.
_ISO_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(sorted(_ISO)) + r")(?![A-Za-z0-9_])",
    re.I,
)

_CURRENCY_COL = frozenset(
    {
        "currency",
        "currencyiso",
        "currencycode",
        "currencyid",
        "ccy",
        "ccycode",
        "ccyiso",
        "currcode",
        "isocurrency",
        "iso4217",
    }
)

_FIRST_ARG_FUNCS = frozenset(
    {
        "round",
        "ceil",
        "ceiling",
        "floor",
        "abs",
        "trunc",
        "truncate",
        "cast",
        "trycast",
        "sign",
        "to_char",
        "strftime",
    }
)

_NUMERIC_PREFIXES = (
    "INTEGER",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "HUGEINT",
    "UHUGEINT",
    "UINTEGER",
    "UBIGINT",
    "USMALLINT",
    "UTINYINT",
    "DOUBLE",
    "FLOAT",
    "REAL",
    "DECIMAL",
    "NUMERIC",
    "NUMBER",
    "HUGEINT",
)

_VALUE_ISO: dict[str, str] = {
    "USD": "USD",
    "US DOLLAR": "USD",
    "US DOLLARS": "USD",
    "US$": "USD",
    "$": "USD",
    "EUR": "EUR",
    "EURO": "EUR",
    "EUROS": "EUR",
    "€": "EUR",
    "GBP": "GBP",
    "POUND": "GBP",
    "POUNDS": "GBP",
    "STERLING": "GBP",
    "£": "GBP",
    "JPY": "JPY",
    "YEN": "JPY",
    "CNY": "CNY",
    "CNH": "CNY",
    "RMB": "CNY",
    "YUAN": "CNY",
    "RENMINBI": "CNY",
    "MYR": "MYR",
    "RM": "MYR",
    "RINGGIT": "MYR",
}


@dataclass(frozen=True)
class SourceColumn:
    table: str
    column: str


def asked_currency(question: str) -> str | None:
    """ISO code if the question names exactly one currency, else None."""
    found = asked_currencies(question)
    return next(iter(found)) if len(found) == 1 else None


def asked_currencies(question: str) -> frozenset[str]:
    """ISO codes named in the question (words, symbols, ISO tokens, fullwidth)."""
    raw = unicodedata.normalize("NFKC", question or "")
    found: set[str] = set()
    if "$" in raw:
        found.add("USD")
    if "€" in raw:
        found.add("EUR")
    if "£" in raw:
        found.add("GBP")
    if re.search(r"(?<![A-Za-z0-9_])RM(?![A-Za-z0-9_])", raw):
        found.add("MYR")
    for pat, iso in _WORD_ISO:
        if pat.search(raw):
            found.add(iso)
    for m in _ISO_TOKEN.finditer(raw):
        token = m.group(1).upper()
        if token in _ISO:
            found.add("CNY" if token == "CNH" else token)
    return frozenset(found)


def currency_mismatch_reason(
    question: str,
    sql: str,
    *,
    warehouse: Path | None,
) -> str | None:
    """None if the question names no currency, or every numeric output matches.

    Else a ``currency_mismatch:`` reason for ABSTAIN (fail closed).
    """
    named = asked_currencies(question)
    if not named:
        return None
    if len(named) > 1:
        codes = " and ".join(sorted(named))
        return (
            f"currency_mismatch: question names {codes}; "
            f"no conversion is certified"
        )
    asked = next(iter(named))
    try:
        tree = parse_one(sql or "", read=_DIALECT)
    except Exception:  # noqa: BLE001 — fail closed on any parse error
        return (
            f"currency_mismatch: SQL could not be parsed; "
            f"no {asked} conversion is certified"
        )
    if tree is None:
        return (
            f"currency_mismatch: SQL could not be parsed; "
            f"no {asked} conversion is certified"
        )
    try:
        root = build_scope(tree)
    except Exception:  # noqa: BLE001
        return (
            f"currency_mismatch: a numeric output could not be traced "
            f"to a source column; no {asked} conversion is certified"
        )
    if root is None:
        return (
            f"currency_mismatch: a numeric output could not be traced "
            f"to a source column; no {asked} conversion is certified"
        )
    schema = _load_schema(warehouse)
    rel_unit = _load_relation_units(warehouse, schema)
    outputs = _numeric_outputs(root, schema)
    if outputs is None:
        return (
            f"currency_mismatch: a numeric output could not be traced "
            f"to a source column; no {asked} conversion is certified"
        )
    if not outputs:
        return (
            f"currency_mismatch: the measure's currency/unit is unknown; "
            f"no {asked} conversion is certified"
        )
    known: list[tuple[SourceColumn, str]] = []
    for col in outputs:
        unit, conflict = _column_unit(col, schema, rel_unit)
        if conflict:
            claim = _suffix_iso(col.column) or "unknown"
            stored = rel_unit.get(col.table, (None, True))[0] or "unknown"
            return (
                f"currency_mismatch: column {col.column} claims {claim} but "
                f"currency is {stored}; the measure is unverified; "
                f"no {asked} conversion is certified"
            )
        if unit is None:
            continue
        if unit != asked:
            return (
                f"currency_mismatch: revenue is stored in {unit}; "
                f"no {asked} conversion is certified"
            )
        known.append((col, unit))
    if not known:
        return (
            f"currency_mismatch: the measure's currency/unit is unknown; "
            f"no {asked} conversion is certified"
        )
    # A matching suffix without catalog/currency-column evidence cannot
    # rule out a lying_column (revenue_usd holding MYR).
    if not schema:
        return (
            f"currency_mismatch: cannot read the warehouse to verify "
            f"currency; no {asked} conversion is certified"
        )
    return None


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _norm_ident(name: str) -> str:
    return re.sub(
        r"[^a-z0-9]",
        "",
        unicodedata.normalize("NFKC", name or "").casefold(),
    )


def is_currency_column(name: str) -> bool:
    return _norm_ident(name) in _CURRENCY_COL


def _suffix_iso(name: str) -> str | None:
    n = unicodedata.normalize("NFKC", name or "").strip().strip('"')
    n = n.replace("-", "_")
    parts = [p for p in n.split("_") if p]
    for part in parts:
        iso = _ALIAS_TO_ISO.get(part.casefold())
        if iso:
            return iso
        up = part.upper()
        if up in _ISO:
            return "CNY" if up == "CNH" else up
    return None


def _iso_from_value(raw: object) -> str | None:
    if raw is None:
        return None
    text = unicodedata.normalize("NFKC", str(raw)).strip().upper()
    if not text:
        return None
    if text in _VALUE_ISO:
        return _VALUE_ISO[text]
    mapped = _ALIAS_TO_ISO.get(text.casefold())
    if mapped:
        return mapped
    if text in _ISO:
        return "CNY" if text == "CNH" else text
    return None


def _is_numeric_type(data_type: str | None) -> bool | None:
    if not data_type:
        return None
    up = str(data_type).upper().split("(", 1)[0].strip()
    if any(up.startswith(p) for p in _NUMERIC_PREFIXES):
        return True
    if up in {
        "VARCHAR",
        "TEXT",
        "CHAR",
        "BPCHAR",
        "BOOLEAN",
        "BOOL",
        "DATE",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "BLOB",
        "UUID",
        "JSON",
        "INTERVAL",
        "TIME",
    }:
        return False
    return None


def _schema_cols(schema: dict[str, dict[str, str]], table: str) -> dict[str, str] | None:
    if table in schema:
        return schema[table]
    key = table.casefold()
    for name, cols in schema.items():
        if name.casefold() == key:
            return cols
    return None


def _col_type(schema: dict[str, dict[str, str]], table: str, column: str) -> str | None:
    cols = _schema_cols(schema, table)
    if not cols:
        return None
    if column in cols:
        return cols[column]
    key = column.casefold()
    for name, typ in cols.items():
        if name.casefold() == key:
            return typ
    return None


def _load_schema(warehouse: Path | None) -> dict[str, dict[str, str]]:
    if warehouse is None or not Path(warehouse).is_file():
        return {}
    con = connect_file(Path(warehouse))
    out: dict[str, dict[str, str]] = {}
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = 'main'"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    finally:
        con.close()
    for table, column, data_type in rows:
        out.setdefault(str(table), {})[str(column)] = str(data_type)
    return out


def _load_relation_units(
    warehouse: Path | None,
    schema: dict[str, dict[str, str]],
) -> dict[str, tuple[str | None, bool]]:
    """table -> (iso or None, conflict)."""
    result: dict[str, tuple[str | None, bool]] = {}
    if warehouse is None or not Path(warehouse).is_file() or not schema:
        return result
    con = connect_file(Path(warehouse))
    try:
        for table, cols in schema.items():
            ccy_cols = [c for c in cols if is_currency_column(c)]
            if not ccy_cols:
                continue
            isos: set[str] = set()
            conflict = False
            for col in ccy_cols:
                iso, bad = _distinct_iso(con, table, col)
                if bad:
                    conflict = True
                    break
                if iso:
                    isos.add(iso)
            if conflict or len(isos) > 1:
                result[table] = (None, True)
            elif len(isos) == 1:
                result[table] = (next(iter(isos)), False)
    except Exception:  # noqa: BLE001 — fail closed at caller if missing
        return {}
    finally:
        con.close()
    return result


def _distinct_iso(con: Any, table: str, column: str) -> tuple[str | None, bool]:
    """(iso, conflict). conflict if nulls, mixed, or unreadable."""
    sql = (
        f"SELECT COUNT(*) AS n, "
        f"COUNT({_q(column)}) AS n_nonnull, "
        f"COUNT(DISTINCT UPPER(TRIM(CAST({_q(column)} AS VARCHAR)))) AS n_distinct, "
        f"MIN(UPPER(TRIM(CAST({_q(column)} AS VARCHAR)))) AS lo "
        f"FROM {_q(table)}"
    )
    try:
        row = con.execute(sql).fetchone()
    except Exception:  # noqa: BLE001
        return None, True
    if not row:
        return None, True
    n, n_nonnull, n_distinct, lo = row
    if int(n or 0) == 0 or int(n_nonnull or 0) < int(n or 0):
        return None, True
    if int(n_distinct or 0) != 1:
        return None, True
    iso = _iso_from_value(lo)
    if iso is None:
        return None, True
    return iso, False


def _column_unit(
    col: SourceColumn,
    schema: dict[str, dict[str, str]],
    rel_unit: dict[str, tuple[str | None, bool]],
) -> tuple[str | None, bool]:
    suffix = _suffix_iso(col.column)
    rel = rel_unit.get(col.table)
    if rel is None:
        key = col.table.casefold()
        for name, val in rel_unit.items():
            if name.casefold() == key:
                rel = val
                break
    rel_iso, rel_conflict = rel if rel is not None else (None, False)
    if rel_conflict:
        return None, True
    if suffix and rel_iso and suffix != rel_iso:
        return None, True
    return suffix or rel_iso, False


def _leaf_scopes(scope: Scope) -> list[Scope]:
    unions = getattr(scope, "union_scopes", None) or []
    if unions:
        out: list[Scope] = []
        for item in unions:
            out.extend(_leaf_scopes(item))
        return out
    return [scope]


def _numeric_outputs(
    root: Scope,
    schema: dict[str, dict[str, str]],
) -> list[SourceColumn] | None:
    """Resolved source columns of numeric select outputs, or None if untraced."""
    collected: list[SourceColumn] = []
    saw_numeric = False
    for leaf in _leaf_scopes(root):
        sel = leaf.expression
        if not isinstance(sel, exp.Select):
            return None
        items = list(sel.expressions or [])
        if not items:
            return None
        for item in items:
            inner = item.unalias() if isinstance(item, exp.Alias) else item
            if isinstance(inner, exp.Star):
                return None
            resolved = _resolve_expr(inner, leaf, schema, frozenset())
            if resolved is None:
                return None
            if not _output_is_numeric(inner, resolved, schema):
                continue
            saw_numeric = True
            if not resolved:
                return None
            collected.extend(resolved)
    if not saw_numeric:
        return []
    # Unique by (table, column) preserving order.
    seen: set[tuple[str, str]] = set()
    uniq: list[SourceColumn] = []
    for col in collected:
        key = (col.table.casefold(), col.column.casefold())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(col)
    return uniq


def _output_is_numeric(
    node: exp.Expression,
    columns: list[SourceColumn],
    schema: dict[str, dict[str, str]],
) -> bool:
    if isinstance(
        node,
        (
            exp.AggFunc,
            exp.Sum,
            exp.Avg,
            exp.Min,
            exp.Max,
            exp.Count,
            exp.Add,
            exp.Sub,
            exp.Mul,
            exp.Div,
            exp.Mod,
            exp.Anonymous,
        ),
    ):
        return True
    if isinstance(node, exp.Literal) and node.is_number:
        return True
    if not columns:
        return True
    flags = [_is_numeric_type(_col_type(schema, c.table, c.column)) for c in columns]
    if flags and all(f is False for f in flags):
        return False
    return True


def _resolve_expr(
    node: exp.Expression | None,
    scope: Scope,
    schema: dict[str, dict[str, str]],
    seen: frozenset[str],
) -> list[SourceColumn] | None:
    if node is None:
        return []
    if not isinstance(node, exp.Expression):
        return []
    if isinstance(node, exp.Alias):
        return _resolve_expr(node.unalias(), scope, schema, seen)
    if isinstance(node, exp.Paren):
        return _resolve_expr(node.this, scope, schema, seen)
    if isinstance(node, exp.Star):
        return None
    if isinstance(node, exp.Null):
        return []
    if isinstance(node, exp.Boolean):
        return []
    if isinstance(node, exp.Literal):
        if node.is_number:
            try:
                val = float(node.this)
            except (TypeError, ValueError):
                return None
            if val == 0:
                return []
            return None
        return []
    if isinstance(node, exp.Column):
        col = _resolve_column(node, scope, schema, seen)
        return None if col is None else [col]
    if isinstance(node, exp.Case):
        parts: list[SourceColumn] = []
        for iff in node.args.get("ifs") or []:
            piece = _resolve_expr(iff.args.get("true"), scope, schema, seen)
            if piece is None:
                return None
            parts.extend(piece)
        default = _resolve_expr(node.args.get("default"), scope, schema, seen)
        if default is None:
            return None
        parts.extend(default)
        return parts
    if isinstance(node, exp.Window):
        return _resolve_expr(node.this, scope, schema, seen)
    if isinstance(node, exp.Subquery):
        inner = node.this
        sub = _subquery_scope(scope, node)
        if sub is None:
            try:
                sub = build_scope(inner)
            except Exception:  # noqa: BLE001
                return None
        if sub is None:
            return None
        outputs = _numeric_outputs(sub, schema)
        return None if outputs is None else list(outputs)
    if isinstance(node, exp.Select):
        try:
            sub = build_scope(node)
        except Exception:  # noqa: BLE001
            return None
        if sub is None:
            return None
        outputs = _numeric_outputs(sub, schema)
        return None if outputs is None else list(outputs)
    if isinstance(node, exp.Func):
        key = str(node.key or "").lower()
        if key in _FIRST_ARG_FUNCS or isinstance(node, (exp.Cast, exp.TryCast, exp.Round)):
            return _resolve_expr(node.this, scope, schema, seen)
        chunks: list[SourceColumn] = []
        args: list[exp.Expression | None] = []
        if isinstance(node.this, exp.Expression):
            args.append(node.this)
        args.extend(a for a in list(node.expressions or []) if isinstance(a, exp.Expression))
        if not args:
            return None
        for arg in args:
            skip = isinstance(arg, (exp.DataType, exp.Identifier))
            if skip:
                got: list[SourceColumn] | None = []
            else:
                got = _resolve_expr(arg, scope, schema, seen)
            if got is None:
                return None
            chunks.extend(got)
        if isinstance(node, exp.Count) and not chunks:
            return None
        return chunks
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg, exp.Distinct)):
        chunks = []
        for arg in node.iter_expressions():
            piece = _resolve_expr(arg, scope, schema, seen)
            if piece is None:
                return None
            chunks.extend(piece)
        return chunks
    # Unknown node: walk children; empty walk is unresolved.
    chunks = []
    children = list(node.iter_expressions())
    if not children:
        return None
    for arg in children:
        piece = _resolve_expr(arg, scope, schema, seen)
        if piece is None:
            return None
        chunks.extend(piece)
    return chunks


def _subquery_scope(parent: Scope, node: exp.Expression) -> Scope | None:
    inner = node.this if isinstance(node, exp.Subquery) else node
    for sc in list(parent.subquery_scopes or []):
        if sc.expression is inner or sc.expression is node:
            return sc
    return None


def _resolve_column(
    col: exp.Column,
    scope: Scope,
    schema: dict[str, dict[str, str]],
    seen: frozenset[str],
) -> SourceColumn | None:
    name = str(col.name or "")
    if not name:
        return None
    alias = str(col.table or "")
    if alias:
        src = _source_by_alias(scope, alias)
        if src is None:
            return None
        return _from_source(src, name, schema, seen)
    sources = list(scope.sources.items())
    if len(sources) == 1:
        return _from_source(sources[0][1], name, schema, seen)
    matches: list[SourceColumn] = []
    for _key, src in sources:
        got = _from_source(src, name, schema, seen)
        if got is not None and _source_has_column(src, name, schema, got):
            matches.append(got)
        elif got is not None and not schema:
            matches.append(got)
    if len(matches) == 1:
        return matches[0]
    # Unique schema owner among FROM tables.
    owners: list[SourceColumn] = []
    for _key, src in sources:
        table = _physical_table(src)
        if table is None:
            continue
        cols = _schema_cols(schema, table)
        if cols and any(c.casefold() == name.casefold() for c in cols):
            owners.append(SourceColumn(table=table, column=_actual_col(cols, name)))
    if len(owners) == 1:
        return owners[0]
    return None


def _actual_col(cols: dict[str, str], name: str) -> str:
    if name in cols:
        return name
    key = name.casefold()
    for c in cols:
        if c.casefold() == key:
            return c
    return name


def _source_by_alias(scope: Scope, alias: str) -> object | None:
    if alias in scope.sources:
        return scope.sources[alias]
    key = alias.casefold().strip('"')
    for name, src in scope.sources.items():
        if str(name).casefold().strip('"') == key:
            return src
    return None


def _physical_table(src: object) -> str | None:
    if isinstance(src, exp.Table):
        return str(src.name or "")
    return None


def _source_has_column(
    src: object,
    name: str,
    schema: dict[str, dict[str, str]],
    got: SourceColumn,
) -> bool:
    table = _physical_table(src)
    if table is None:
        return True
    cols = _schema_cols(schema, table)
    if not cols:
        return True
    return any(c.casefold() == name.casefold() for c in cols)


def _from_source(
    src: object,
    col_name: str,
    schema: dict[str, dict[str, str]],
    seen: frozenset[str],
) -> SourceColumn | None:
    if isinstance(src, exp.Table):
        table = str(src.name or "")
        if not table:
            return None
        cols = _schema_cols(schema, table)
        actual = _actual_col(cols, col_name) if cols else col_name
        return SourceColumn(table=table, column=actual)
    if isinstance(src, Scope):
        sel = src.expression
        if not isinstance(sel, exp.Select):
            return None
        mark = str(id(src))
        if mark in seen:
            return None
        nxt = seen | {mark}
        for item in sel.expressions or []:
            alias = str(item.alias_or_name or "")
            inner = item.unalias() if isinstance(item, exp.Alias) else item
            if alias and alias.casefold() == col_name.casefold():
                resolved = _resolve_expr(inner, src, schema, nxt)
                if resolved is None or len(resolved) != 1:
                    # A CTE output that is an aggregate of one column is still one source.
                    if resolved is not None and len(resolved) >= 1:
                        return resolved[0]
                    return None
                return resolved[0]
            if (
                isinstance(inner, exp.Column)
                and str(inner.name).casefold() == col_name.casefold()
                and not alias
            ):
                return _resolve_column(inner, src, schema, nxt)
        return None
    return None


def _outer_select(tree: exp.Expression) -> exp.Select | None:
    node = tree.this if isinstance(tree, exp.Subquery) else tree
    if isinstance(node, exp.Select):
        return node
    return None


def _output_select(tree: exp.Expression) -> exp.Select | None:
    """Leftmost select, so a qualified UNION still has output names."""
    node = tree.this if isinstance(tree, exp.Subquery) else tree
    while isinstance(node, exp.Union):
        node = node.this
    if isinstance(node, exp.Select):
        return node
    return None


def _projection_is_star(proj: exp.Expression) -> bool:
    inner = proj.this if isinstance(proj, exp.Alias) else proj
    if isinstance(inner, exp.Star):
        return True
    return isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star)


def _lineage_leaves(node: Any) -> frozenset[str] | None:
    """Source names such as ``patients.birth_date``. None if a star blocks proof."""
    found: list[str] = []
    unresolved = False

    def walk(current: Any) -> None:
        nonlocal unresolved
        expr = getattr(current, "expression", None)
        if isinstance(expr, exp.Star):
            unresolved = True
            return
        kids = list(getattr(current, "downstream", None) or [])
        if kids:
            for kid in kids:
                walk(kid)
            return
        name = str(getattr(current, "name", "") or "")
        if not name or "*" in name.split("."):
            unresolved = True
            return
        found.append(name)

    walk(node)
    if unresolved or not found:
        return None
    return frozenset(found)


def served_column_sources(
    sql: str,
    schema: dict[str, Any] | None = None,
) -> dict[str, frozenset[str]]:
    """Outer output name -> source column names from sqlglot lineage.

    ``schema`` is the connection the answer ran against. The caller passes
    it. This function does not load one. When qualify can use it, ``*``,
    ``t.*``, star CTEs, and UNION branches expand to real source columns
    before lineage. An empty set means that output could not be traced.
    No schema, a schema qualify cannot use, a parse failure, or a column
    that is still a star: that output stays untraced, so a date stays
    masked and a passport shape is not a proven code source.
    No-SQL answers do not call this.
    """
    try:
        tree = parse_one(sql or "", read=_DIALECT)
    except Exception:
        return {}
    sql_for_lineage = sql
    qualified = False
    if schema:
        try:
            expanded = qualify(tree.copy(), schema=schema, dialect=_DIALECT)
        except Exception:
            expanded = None
        if expanded is not None:
            sql_for_lineage = expanded.sql(dialect=_DIALECT)
            tree = expanded
            qualified = True
    select = _output_select(tree) if qualified else _outer_select(tree)
    if select is None:
        return {}
    out: dict[str, frozenset[str]] = {}
    for proj in select.expressions:
        if _projection_is_star(proj):
            continue
        alias = str(proj.alias_or_name or "")
        if not alias:
            continue
        key = alias.casefold()
        try:
            node = lineage(alias, sql_for_lineage, dialect=_DIALECT)
        except Exception:
            out[key] = frozenset()
            continue
        sources = _lineage_leaves(node)
        out[key] = sources if sources is not None else frozenset()
    return out


#: ``tables_read`` parses at most this many characters. Beyond it, and on a parse failure,
#: it scans for names instead. 20,000 characters is a long hand-written or generated
#: statement; a CTE chain or UNION of that size parses in about a tenth of a second.
TABLES_PARSE_CAP = 20_000
#: The scan reads no more than this, and reports no more than ``_MAX_SCANNED_TABLES`` names.
_SCAN_CAP = 1_000_000
_MAX_SCANNED_TABLES = 500
#: Bounded name scan: the identifier after FROM / JOIN / INTO / UPDATE / TABLE. Linear: every
#: repeat is bounded or possessive.
_TABLE_SCAN = re.compile(
    r"(?i)\b(?:from|join|into|update|table)\s{1,16}+(?:only\s{1,16}+)?"
    r'(?P<n>"[^"\n]{1,128}+"|`[^`\n]{1,128}+`|[A-Za-z_][\w.$]{0,255}+)'
)
_NOT_A_TABLE = frozenset({"select", "lateral", "values", "unnest", "table", "only"})


def _scan_tables(text: str) -> tuple[str, ...]:
    """Table names after FROM / JOIN / INTO / UPDATE / TABLE, with no parse. Bounded."""
    names: dict[str, str] = {}
    for m in _TABLE_SCAN.finditer(text[:_SCAN_CAP]):
        name = m.group("n").strip('"`')
        if name and name.casefold() not in _NOT_A_TABLE:
            names.setdefault(name.casefold(), name)
            if len(names) >= _MAX_SCANNED_TABLES:
                break
    return tuple(sorted(names.values(), key=str.casefold))


def _table_label(node: exp.Table) -> str:
    """``catalog.db.name`` as written; a table function is ``read_csv()``.

    The function name only. Its arguments (a file path) are in the executed SQL
    column already and are not a table name.
    """
    if isinstance(node.this, exp.Func):
        return f"{str(node.this.sql_name()).lower()}()"
    return ".".join(p for p in (node.catalog, node.db, node.name) if p)


def tables_read_checked(sql: str | None) -> tuple[tuple[str, ...], bool]:
    """``(tables, approximate)``: the tables an executed statement reads, sorted.

    A name is a CTE reference only where sqlglot's scope resolves it to a CTE (or
    derived table) of an enclosing query. That is scope, not spelling:
    ``WITH sales AS (SELECT * FROM sales) SELECT * FROM sales`` reads the base
    table ``sales`` inside the CTE's own body and the CTE outside it, so
    ``sales`` is reported. Excluding by name dropped it, and an audit row that
    says "read nothing" for a query that read something is a false record.

    Never parses unbounded input. sqlglot is pure Python and its cost grows faster
    than the statement (a 400,000 character CTE chain took ten seconds), and the audit
    record is written on the request path. A statement longer than ``TABLES_PARSE_CAP``,
    or one sqlglot cannot parse or scope, gets the bounded identifier scan instead
    (``_scan_tables``): looser (a CTE name, or "from x" inside a string literal, would
    count) but never empty for SQL that plainly names a table, and ``approximate`` is
    True so the audit row says so. A comment-only statement, such as the
    document-retrieval stub, reads none.
    """
    text = (sql or "").strip()
    if not text:
        return (), False
    if len(text) > TABLES_PARSE_CAP:
        return _scan_tables(text), True
    try:
        trees = [t for t in sqlglot.parse(text, dialect=_DIALECT) if t is not None]
        seen: dict[str, str] = {}
        for tree in trees:
            for scope in traverse_scope(tree):
                for node in scope.tables:
                    # Only an unqualified name can be a CTE: ``main.t`` is the base
                    # table even when a CTE called ``t`` is in scope.
                    unqualified = not node.db and not node.catalog
                    if unqualified and isinstance(scope.sources.get(node.alias_or_name), Scope):
                        continue  # a CTE or derived table of an enclosing query
                    label = _table_label(node)
                    if label:
                        seen.setdefault(label.casefold(), label)
    except Exception:  # noqa: BLE001 - any parser or scope failure takes the scan
        return _scan_tables(text), True
    return tuple(sorted(seen.values(), key=str.casefold)), False


def tables_read(sql: str | None) -> tuple[str, ...]:
    """The tables an executed statement reads (see ``tables_read_checked``)."""
    return tables_read_checked(sql)[0]


__all__ = [
    "SourceColumn",
    "asked_currencies",
    "asked_currency",
    "currency_mismatch_reason",
    "is_currency_column",
    "served_column_sources",
    "tables_read",
    "tables_read_checked",
]
