"""DMS_BIG_SCHEMA. Default off. The digit 1 only.

On, the AI floor sends a compact schema map: top tables, their columns,
types, join keys, measures, and a few distinct samples. The full catalog
stays out of the prompt. SQL runs in the database. Aggregates (SUM, COUNT,
AVG, MIN, MAX, GROUP BY), filters, and joins are the statement's job.
At most 200 rows come back. A SQL error is repaired once from the error
and the schema, then role ``escalate``. No model id. No question phrases.

Off, callers do not enter. Unset, 0, true, yes, and on are off.

ponytail: token count is len/4. Ceiling: a model tokenizer disagrees.
Upgrade: count with the tokenizer of the model that writes the SQL.
ponytail: one FK hop between two retrieved tables. Ceiling: a join that
needs two bridges is not pulled in. Upgrade: a budgeted path of length 3.
ponytail: BM25 unless the caller passes an embedder. OpenVault still
picks the embedding model when one is passed. Upgrade: a stored index
when a space has more catalogs than this scan wants to score.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from dms_executor.verified_queries import rank_texts

ENV = "DMS_BIG_SCHEMA"
ROW_CAP = 200
TOKEN_BUDGET = 8000
PRIMARY_K = 6
NEXT_K = 4
SAMPLE_N = 3
MAX_COLUMNS = 12
FIELD_CHARS = 80
SAMPLE_CHARS = 32
_CHARS_PER_TOKEN = 4
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_LOGGER = logging.getLogger(__name__)

# One standing instruction. Not chosen from the question text.
_STANDING = (
    "Write one read-only SELECT. Push filters, joins, and aggregates "
    "(SUM, COUNT, AVG, MIN, MAX, GROUP BY) into SQL so the database "
    "returns the figure. Do not select a full table. An overview returns "
    "summary stats computed in SQL: row counts, min, max, and top "
    f"categories. At most {ROW_CAP} rows come back."
)

_repair: ContextVar[str] = ContextVar("dms_big_schema_repair", default="")
_meta: ContextVar[dict[str, Any] | None] = ContextVar("dms_big_schema_meta", default=None)


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``DMS_BIG_SCHEMA`` is the digit 1."""
    source = os.environ if env is None else env
    return str(source.get(ENV) or "").strip() == "1"


def estimate_tokens(text: str) -> int:
    """Upper bound. Four characters per token."""
    if not text:
        return 0
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def repair_schema() -> str:
    """Next schema block for one repair. Empty when there is nothing further."""
    return _repair.get()


def last_meta() -> dict[str, Any] | None:
    """Tables and token counts for the envelope. No prompt and no cell values."""
    raw = _meta.get()
    return dict(raw) if isinstance(raw, dict) else None


def note_executed(sql: str, *, returned: int, row_count: int) -> None:
    """Record whether the statement pushed aggregation, and how many rows came back."""
    meta = dict(_meta.get() or {})
    meta["aggregated"] = pushes_aggregation(sql)
    meta["row_cap"] = ROW_CAP
    meta["returned"] = returned
    meta["row_count"] = row_count
    _meta.set(meta)


@dataclass(frozen=True)
class SchemaSlice:
    """The prompt the model sees, and the full-catalog size it replaced."""

    prompt: str
    tokens: int
    full_tokens: int
    picked: tuple[str, ...]
    next_prompt: str
    next_names: tuple[str, ...]


@dataclass(frozen=True)
class _Col:
    name: str
    type_name: str
    description: str
    primary_key: bool
    samples: tuple[str, ...]
    fk: str


@dataclass(frozen=True)
class _Table:
    name: str
    description: str
    columns: tuple[_Col, ...]


@dataclass(frozen=True)
class _Rel:
    left: str
    left_col: str
    right: str
    right_col: str


def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:limit]


def _loose(text: str) -> str:
    return text.replace("_", " ")


def _ident(name: str) -> bool:
    return bool(name) and _IDENT.fullmatch(name) is not None


def _allowed(name: str, grantable: set[str] | None) -> bool:
    if grantable is None:
        return True
    bare = name.rsplit(".", 1)[-1].casefold()
    granted = {str(item).rsplit(".", 1)[-1].casefold() for item in grantable}
    return bare in granted


def _samples(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return ()
    out: list[str] = []
    for item in items:
        text = _clean(item, SAMPLE_CHARS)
        if text and text not in out:
            out.append(text)
        if len(out) >= SAMPLE_N:
            break
    return tuple(out)


def _relations(raw: Any) -> list[_Rel]:
    if not isinstance(raw, list):
        return []
    found: list[_Rel] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        left = _clean(item.get("from") or item.get("from_object"), 64)
        right = _clean(item.get("to") or item.get("to_object"), 64)
        left_col = _clean(item.get("from_column") or item.get("from_columns"), 64)
        right_col = _clean(item.get("to_column") or item.get("to_columns"), 64)
        if isinstance(item.get("from_columns"), (list, tuple)) and item.get("from_columns"):
            left_col = _clean(item["from_columns"][0], 64)
        if isinstance(item.get("to_columns"), (list, tuple)) and item.get("to_columns"):
            right_col = _clean(item["to_columns"][0], 64)
        if _ident(left) and _ident(right) and _ident(left_col) and _ident(right_col):
            found.append(_Rel(left, left_col, right, right_col))
    return found


def _fk_of(table: str, column: str, rels: list[_Rel]) -> str:
    for rel in rels:
        if rel.left == table and rel.left_col == column:
            return f"{rel.right}.{rel.right_col}"
        if rel.right == table and rel.right_col == column:
            return f"{rel.left}.{rel.left_col}"
    return ""


def _edges(rels: list[_Rel]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for rel in rels:
        graph.setdefault(rel.left, set()).add(rel.right)
        graph.setdefault(rel.right, set()).add(rel.left)
    return graph


def _bridges(picked: list[str], edges: dict[str, set[str]]) -> list[str]:
    """One intermediate on the FK path between two retrieved tables."""
    have = set(picked)
    found: list[str] = []
    for index, left in enumerate(picked):
        for right in picked[index + 1 :]:
            for mid in sorted(edges.get(left, ())):
                if mid in have or mid in found or mid == right:
                    continue
                if right in edges.get(mid, ()):
                    found.append(mid)
                    break
    return found


def _measures(catalog: Mapping[str, Any], ontology: Any, picked: set[str]) -> list[str]:
    rows: list[dict[str, str]] = []
    raw = catalog.get("measures")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                rows.append(
                    {
                        "name": _clean(item.get("name"), 64),
                        "grain": _clean(item.get("grain"), 64),
                        "expression": _clean(item.get("expression"), FIELD_CHARS),
                    }
                )
    stored = getattr(ontology, "measures", None) or {}
    if isinstance(stored, dict):
        for name, measure in list(stored.items())[:16]:
            rows.append(
                {
                    "name": _clean(getattr(measure, "name", name), 64),
                    "grain": _clean(getattr(measure, "grain", ""), 64),
                    "expression": _clean(getattr(measure, "expression", ""), FIELD_CHARS),
                }
            )
    lines: list[str] = []
    names = {item.casefold() for item in picked}
    for item in rows:
        blob = f"{item['grain']} {item['expression']}".casefold()
        idents = {tok.casefold() for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", blob)}
        if item["name"] and (item["grain"].casefold() in names or names & idents):
            lines.append(
                f"measure {item['name']} grain={item['grain']} expr={item['expression']}"
            )
        if len(lines) >= 8:
            break
    return lines


def _column(table: str, raw: Mapping[str, Any], rels: list[_Rel]) -> _Col | None:
    name = _clean(raw.get("name"), 64)
    if not _ident(name):
        return None
    samples = _samples(raw.get("samples"))
    if not samples:
        samples = _samples(raw.get("values"))
    return _Col(
        name=name,
        type_name=_clean(raw.get("type") or raw.get("data_type"), 32) or "text",
        description=_clean(raw.get("description"), FIELD_CHARS),
        primary_key=bool(raw.get("primary_key")),
        samples=samples,
        fk=_fk_of(table, name, rels),
    )


def _table(raw: Mapping[str, Any], rels: list[_Rel]) -> _Table | None:
    name = _clean(raw.get("name"), 64)
    if not _ident(name):
        return None
    columns: list[_Col] = []
    raw_cols = raw.get("columns")
    if isinstance(raw_cols, list):
        for item in raw_cols:
            if isinstance(item, dict):
                col = _column(name, item, rels)
                if col is not None and col.name not in {c.name for c in columns}:
                    columns.append(col)
    return _Table(
        name=name,
        description=_clean(raw.get("description"), FIELD_CHARS),
        columns=tuple(columns),
    )


def _doc(table: _Table) -> str:
    parts = [table.name, table.description]
    for col in table.columns:
        parts.extend([col.name, col.type_name, col.description, " ".join(col.samples)])
    return _loose(" ".join(part for part in parts if part))


def _keep_columns(
    table: _Table, question: str, embedder: Callable[[list[str]], Any] | None
) -> _Table:
    """A small table sends every column. A wide table keeps keys, then the ranked rest."""
    if len(table.columns) <= MAX_COLUMNS:
        return table
    keys = [col for col in table.columns if col.primary_key or col.fk]
    rest = [col for col in table.columns if col not in keys]
    if rest:
        docs = [_loose(" ".join([col.name, col.type_name, col.description])) for col in rest]
        ranked = rank_texts(_loose(question), docs, embedder, limit=len(rest), floor=0.0)
        ordered = [rest[index] for _score, index in ranked]
        # A zero score is absent. Keep a few unnamed columns so the table is not keys only.
        if len(ordered) < min(4, len(rest)):
            for col in rest:
                if col not in ordered:
                    ordered.append(col)
                if len(ordered) >= min(4, len(rest)):
                    break
        rest = ordered
    kept = (keys + rest)[:MAX_COLUMNS]
    return _Table(table.name, table.description, tuple(kept))


def _fill_samples(
    table: _Table, sample_fn: Callable[[str, str], list[str]] | None
) -> _Table:
    if sample_fn is None:
        return table
    columns: list[_Col] = []
    for col in table.columns:
        samples = col.samples
        if not samples:
            try:
                samples = _samples(sample_fn(table.name, col.name))
            except Exception:
                samples = ()
        columns.append(
            _Col(
                col.name,
                col.type_name,
                col.description,
                col.primary_key,
                samples,
                col.fk,
            )
        )
    return _Table(table.name, table.description, tuple(columns))


def _join_lines(names: set[str], rels: list[_Rel], also: set[str] | None = None) -> list[str]:
    lines: list[str] = []
    other = names if also is None else names | also
    for rel in rels:
        left_in = rel.left in names or rel.left in other
        right_in = rel.right in names or rel.right in other
        if also is None:
            if rel.left in names and rel.right in names:
                lines.append(
                    f"join {rel.left}.{rel.left_col} = {rel.right}.{rel.right_col}"
                )
        elif left_in and right_in and (rel.left in names or rel.right in names):
            lines.append(f"join {rel.left}.{rel.left_col} = {rel.right}.{rel.right_col}")
    return lines


def _render(
    tables: list[_Table],
    joins: list[str],
    measures: list[str],
    *,
    standing: bool,
) -> str:
    lines: list[str] = [_STANDING] if standing else []
    lines.append("schema:")
    for table in tables:
        head = f"table {table.name}"
        if table.description:
            head = f"{head}: {table.description}"
        lines.append(head)
        for col in table.columns:
            bits = [f"  {col.name} {col.type_name}"]
            if col.primary_key:
                bits.append("primary_key")
            if col.fk:
                bits.append(f"fk={col.fk}")
            if col.samples:
                bits.append("samples: " + ", ".join(col.samples))
            lines.append(" ".join(bits))
    lines.extend(joins)
    lines.extend(measures)
    return "\n".join(lines)


def _truncate(text: str, budget: int) -> str:
    cap = budget * _CHARS_PER_TOKEN
    if len(text) <= cap:
        return text
    short = text[:cap]
    if " " in short:
        short = short.rsplit(" ", 1)[0]
    return short


def _note_picked(names: list[str]) -> None:
    try:
        from dms_core.ask_stream import note_picked

        note_picked(names)
    except Exception:
        return


def retrieve(
    question: str,
    catalog: Mapping[str, Any],
    *,
    embedder: Callable[[list[str]], Any] | None = None,
    ontology: Any = None,
    sample_fn: Callable[[str, str], list[str]] | None = None,
    max_tokens: int = TOKEN_BUDGET,
) -> SchemaSlice:
    """Top tables plus the FK bridge between them. The rest stays out."""
    described = catalog if isinstance(catalog, Mapping) else {}
    rels = _relations(described.get("relationships"))
    tables: list[_Table] = []
    seen: set[str] = set()
    raw_sets = described.get("datasets")
    if isinstance(raw_sets, list):
        for item in raw_sets:
            if not isinstance(item, dict):
                continue
            table = _table(item, rels)
            if table is None or table.name in seen:
                continue
            seen.add(table.name)
            tables.append(table)
    by_name = {table.name: table for table in tables}
    docs = [_doc(table) for table in tables]
    ranked = rank_texts(
        _loose(question or ""), docs, embedder, limit=max(len(docs), 1), floor=0.0
    )
    lexical = [tables[index].name for _score, index in ranked][:PRIMARY_K]
    edges = _edges(rels)
    full = _render(tables, _join_lines(set(by_name), rels), [], standing=False)
    full_tokens = estimate_tokens(full)
    ranked_names = [tables[index].name for _score, index in ranked]
    warm = list(lexical)
    warm.extend(name for name in _bridges(warm, edges) if name not in warm)
    for name in ranked_names:
        if name not in warm:
            warm.append(name)
        if len(warm) >= PRIMARY_K + NEXT_K + 4:
            break
    prepared: dict[str, _Table] = {}
    for name in warm:
        if name in by_name:
            prepared[name] = _fill_samples(
                _keep_columns(by_name[name], question, embedder), sample_fn
            )
    chosen = list(lexical)
    budget = max(1, int(max_tokens))
    rendered_tables: list[_Table] = []
    text = _STANDING
    while True:
        bridges = _bridges(chosen, edges)
        order = chosen + [name for name in bridges if name not in chosen]
        rendered_tables = [prepared[name] for name in order if name in prepared]
        names = {table.name for table in rendered_tables}
        text = _render(
            rendered_tables,
            _join_lines(names, rels),
            _measures(described, ontology, names),
            standing=True,
        )
        if estimate_tokens(text) <= budget or not chosen:
            if estimate_tokens(text) > budget:
                text = _truncate(text, budget)
            break
        chosen.pop()
    picked = [table.name for table in rendered_tables]
    sent = set(picked)
    next_names: list[str] = []
    for name in ranked_names:
        if name not in sent:
            next_names.append(name)
        if len(next_names) >= NEXT_K:
            break
    next_tables = [prepared[name] for name in next_names if name in prepared]
    next_text = ""
    if next_tables:
        next_set = {table.name for table in next_tables}
        next_text = _render(
            next_tables,
            _join_lines(next_set, rels, sent),
            _measures(described, ontology, next_set),
            standing=False,
        )
        if estimate_tokens(next_text) > budget:
            next_text = _truncate(next_text, budget)
    tokens = estimate_tokens(text)
    _repair.set(next_text)
    _meta.set(
        {
            "tables": picked,
            "tokens": tokens,
            "full_tokens": full_tokens,
            "row_cap": ROW_CAP,
            "next_tables": next_names,
        }
    )
    _note_picked(picked)
    _LOGGER.info(
        "big_schema_picked tables=%s tokens=%s full_tokens=%s",
        ",".join(picked),
        tokens,
        full_tokens,
    )
    return SchemaSlice(
        prompt=text,
        tokens=tokens,
        full_tokens=full_tokens,
        picked=tuple(picked),
        next_prompt=next_text,
        next_names=tuple(next_names),
    )


def slice_for_ask(
    question: str,
    described: Mapping[str, Any],
    *,
    ontology: Any = None,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    embedder: Callable[[list[str]], Any] | None = None,
) -> SchemaSlice | None:
    """Catalog slice for one ask. A read miss returns None so the caller can fall back."""
    try:
        _note_picked([])
        body: dict[str, Any] = {
            "datasets": [],
            "relationships": [],
            "measures": [],
        }
        raw_sets = described.get("datasets") if isinstance(described, Mapping) else None
        if isinstance(raw_sets, list):
            for item in raw_sets:
                if not isinstance(item, dict):
                    continue
                name = _clean(item.get("name"), 64)
                if _ident(name) and _allowed(name, grantable):
                    body["datasets"].append(item)
        raw_rels = described.get("relationships") if isinstance(described, Mapping) else None
        if isinstance(raw_rels, list):
            body["relationships"].extend(raw_rels)
        raw_measures = described.get("measures") if isinstance(described, Mapping) else None
        if isinstance(raw_measures, list):
            body["measures"].extend(raw_measures)
        names = {str(item.get("name")) for item in body["datasets"] if isinstance(item, dict)}
        if warehouse is not None and Path(warehouse).is_file() and names:
            try:
                from dms_executor.schema_context import _catalog_foreign_keys

                for left_t, left_c, right_t, right_c in _catalog_foreign_keys(
                    Path(warehouse), grantable, names
                ):
                    body["relationships"].append(
                        {
                            "from": left_t,
                            "from_column": left_c,
                            "to": right_t,
                            "to_column": right_c,
                        }
                    )
            except Exception:
                _LOGGER.warning("big_schema_fk_unavailable")

        def _sample(table: str, column: str) -> list[str]:
            if warehouse is None:
                return []
            return _distinct_samples(warehouse, table, column)

        return retrieve(
            question,
            body,
            embedder=embedder,
            ontology=ontology,
            sample_fn=_sample if warehouse is not None else None,
        )
    except Exception:
        _LOGGER.warning("big_schema_slice_failed")
        return None


def _distinct_samples(warehouse: Path, table: str, column: str) -> list[str]:
    """At most three distinct values. Never the column body."""
    if not _ident(table) or not _ident(column) or not Path(warehouse).is_file():
        return []
    from dms_executor.bronze import quote_ident
    from dms_executor.demo_warehouse import connect_serving

    try:
        quoted_table = quote_ident(table)
        quoted_column = quote_ident(column)
        con = connect_serving(Path(warehouse))
    except Exception:
        return []
    try:
        rows = con.execute(
            f"SELECT DISTINCT CAST({quoted_column} AS VARCHAR) "
            f"FROM {quoted_table} "
            f"WHERE {quoted_column} IS NOT NULL "
            f"ORDER BY 1 LIMIT {SAMPLE_N}"
        ).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    return [str(row[0]) for row in rows if row and row[0] is not None]


def _limit_of(tree: exp.Expression) -> int | None:
    node = tree.args.get("limit")
    if not isinstance(node, exp.Limit):
        return None
    expr = node.expression
    if isinstance(expr, exp.Literal) and expr.is_int:
        try:
            return int(expr.this)
        except (TypeError, ValueError):
            return None
    return None


def prepare_sql(
    sql: str, *, cap: int = ROW_CAP, dialect: str = "duckdb"
) -> tuple[str | None, str | None]:
    """``(sql, None)`` when it is one read-only query capped at ``cap``.

    A tighter LIMIT stays. Anything that is not one query is ``read_only``
    and is not wrapped. The model's text is nested, not reprinted.
    """
    text = (sql or "").strip().rstrip(";")
    if not text:
        return None, "read_only"
    try:
        trees = [node for node in sqlglot.parse(text, read=dialect) if node is not None]
    except Exception:
        return None, "read_only"
    if len(trees) != 1 or not isinstance(trees[0], exp.Query):
        return None, "read_only"
    current = _limit_of(trees[0])
    if current is not None and 0 < current <= cap:
        return text, None
    return f"SELECT * FROM ({text}) AS _dms_bounded LIMIT {int(cap)}", None


def pushes_aggregation(sql: str, dialect: str = "duckdb") -> bool:
    """True when the statement has an aggregate or GROUP BY. From the SQL tree."""
    try:
        trees = [node for node in sqlglot.parse(sql, read=dialect) if node is not None]
    except Exception:
        return False
    if len(trees) != 1:
        return False
    tree = trees[0]
    if tree.find(exp.Group) is not None:
        return True
    return tree.find(exp.AggFunc) is not None


def bound_rows(
    rows: list[dict[str, Any]] | None, cap: int = ROW_CAP
) -> tuple[list[dict[str, Any]], int, bool]:
    """At most ``cap`` row dicts, the original length, and whether the cap hit."""
    clean = [row for row in (rows or []) if isinstance(row, dict)]
    count = len(clean)
    return clean[:cap], count, count > cap


def cap_result(
    result: Any, rows: list[dict[str, Any]] | None, cap: int = ROW_CAP
) -> tuple[list[dict[str, Any]], Any]:
    """Write the capped rows back onto a submit result. The rest is not returned."""
    capped, count, _hit = bound_rows(rows, cap)
    output = getattr(result, "output", None)
    if isinstance(output, dict):
        output["rows"] = capped
        output["row_count"] = count
        output["row_cap"] = cap
    return capped, result
