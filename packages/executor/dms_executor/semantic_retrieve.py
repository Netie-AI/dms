"""GEN-01 multi-method semantic retrieve → short context.

Ideas-only from DB-GPT-class schema linking (no clone, no embeddings, no extra
dep): SQL-filter over information_schema + ontology overlay + bounded DISTINCT
encodings, then summarize. Token overlap is O(n) over granted columns.

ponytail: token overlap, not a vector index. Ceiling: wide lakes. Upgrade: Cortex
schema-link service behind the existing compute HTTP call.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dms_executor.demo_warehouse import connect_file
from dms_executor.ontology import Ontology

MAX_CONTEXT_CHARS = 2400
MAX_TABLES = 6
MAX_COLS = 8
MAX_MEASURES = 10
MAX_LINKS = 8
MAX_SAMPLE = 6
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TIME = re.compile(
    r"\b(last|this|next|month|week|year|yesterday|today|ago|ytd|qtd)\b",
    re.I,
)
_NEEDS_DIM = re.compile(r"\b(by|per|each|grouped|across)\b", re.I)
_ENTITY_PREFIX = re.compile(r"^\s*(which|list|rank)\b", re.I)
_UNTYPED = re.compile(
    r"\b(above\s+\d|below\s+\d|90 percent|cold[\s-]?storage|expired|expir(?:y|ed)|"
    r"warehouse a\b|wh-a\b|cctv|camera|delayed|reorder|storage bin|"
    r"\balerts?\b|chemicals?\b|high-risk|pending shipment|risk and lead)\b",
    re.I,
)
_TOP_N = re.compile(r"\btop\s+(\d{1,2})\b", re.I)
_DIM_HINTS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("by country", "supplier country"), "supplier", "country"),
    (("by destination", "by location"), "location", "location_code"),
    (("by category",), "product", "category"),
    (("by sku", "selling sku", "skus by"), "product", "sku"),
)
_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "our",
        "we",
        "is",
        "are",
        "in",
        "on",
        "for",
        "to",
        "and",
        "or",
        "do",
        "have",
        "what",
        "which",
        "me",
        "please",
        "does",
        "with",
        "from",
        "that",
        "this",
        "be",
        "as",
        "at",
        "it",
    }
)


def question_tokens(question: str) -> set[str]:
    out: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", (question or "").lower()):
        if raw in _STOP or len(raw) < 2:
            continue
        out.add(raw)
        if len(raw) > 3 and raw.endswith("s"):
            out.add(raw[:-1])
    return out


def _score(name: str, toks: set[str]) -> int:
    parts = re.findall(r"[a-z0-9]+", (name or "").lower().replace("_", " "))
    stems: set[str] = set()
    for part in parts:
        if part in _STOP:
            continue
        stems.add(part)
        if len(part) > 3 and part.endswith("s"):
            stems.add(part[:-1])
    return len(stems & toks)


def _safe_ident(name: str) -> str | None:
    return name if _IDENT.match(name) else None


def retrieve_schema_sql(
    warehouse: Path | None,
    grantable: set[str],
    toks: set[str],
) -> list[dict[str, Any]]:
    """SQL-filter: granted tables/columns from information_schema, scored in Python."""
    tables = sorted(t for t in grantable if _safe_ident(t))
    if warehouse is None or not Path(warehouse).is_file() or not tables:
        return []
    listed = ", ".join("'" + t.replace("'", "''") + "'" for t in tables)
    sql = (
        "SELECT table_name, column_name FROM information_schema.columns "
        f"WHERE table_name IN ({listed})"
    )
    con = connect_file(Path(warehouse))
    try:
        rows = con.execute(sql).fetchall()
    except Exception:  # noqa: BLE001 -- empty retrieve, do not 503
        return []
    finally:
        con.close()
    by_table: dict[str, list[str]] = {}
    table_score: dict[str, int] = {}
    for table_name, column_name in rows:
        table = _safe_ident(str(table_name))
        col = _safe_ident(str(column_name))
        if not table or not col:
            continue
        sc = _score(table, toks) + _score(col, toks)
        if sc <= 0 and _score(table, toks) <= 0:
            continue
        by_table.setdefault(table, [])
        if col not in by_table[table] and (_score(col, toks) > 0 or _score(table, toks) > 0):
            if _score(col, toks) > 0 or len(by_table[table]) < 2:
                by_table[table].append(col)
        table_score[table] = table_score.get(table, 0) + sc
    ranked = sorted(table_score, key=lambda t: (-table_score[t], t))[:MAX_TABLES]
    out: list[dict[str, Any]] = []
    for table in ranked:
        cols = by_table.get(table) or []
        out.append({"table": table, "columns": cols[:MAX_COLS], "score": table_score[table]})
    return out


def retrieve_value_encodings(
    warehouse: Path | None,
    schema: list[dict[str, Any]],
    toks: set[str],
) -> dict[str, list[str]]:
    """Perfect-SQL-as-filter: DISTINCT samples for retrieved dimension columns."""
    if warehouse is None or not Path(warehouse).is_file():
        return {}
    skip = re.compile(r"(amount|qty|quantity|cost|kg|myr|score|load|capacity|date|id)$", re.I)
    encodings: dict[str, list[str]] = {}
    con = connect_file(Path(warehouse))
    try:
        for item in schema:
            table = _safe_ident(str(item.get("table") or ""))
            if not table:
                continue
            for col in item.get("columns") or []:
                name = _safe_ident(str(col))
                if not name or skip.search(name) or _score(name, toks) <= 0:
                    continue
                key = f"{table}.{name}"
                try:
                    fetched = con.execute(
                        f"SELECT DISTINCT CAST({name} AS VARCHAR) FROM {table} "
                        f"WHERE {name} IS NOT NULL LIMIT {MAX_SAMPLE}"
                    ).fetchall()
                except Exception:  # noqa: BLE001
                    continue
                vals = [str(r[0]) for r in fetched if r and r[0] is not None]
                if vals:
                    encodings[key] = vals
    finally:
        con.close()
    return encodings


def retrieve_ontology_slice(onto: Ontology | None, toks: set[str]) -> dict[str, Any]:
    if onto is None:
        return {"measures": {}, "objects": {}, "links": {}, "columns": {}}
    scored_m: list[tuple[int, str]] = []
    for m in onto.measures.values():
        sc = _score(m.name, toks) + _score(m.description or "", toks) + _score(m.grain, toks)
        if sc:
            scored_m.append((sc, m.name))
    scored_m.sort(reverse=True)
    keep_m = [name for _sc, name in scored_m[:MAX_MEASURES]]
    keep_obj: set[str] = set()
    for name in keep_m:
        keep_obj.add(onto.measures[name].grain)
    for obj in onto.objects.values():
        if _score(obj.name, toks):
            keep_obj.add(obj.name)
    cols_cache: dict[str, set[str]] = onto.__dict__.get("_column_cache") or {}
    for obj_name, cols in cols_cache.items():
        if obj_name in keep_obj:
            continue
        if any(_score(str(c), toks) for c in cols):
            keep_obj.add(str(obj_name))
    measures = {
        name: {
            "grain": onto.measures[name].grain,
            "description": onto.measures[name].description,
        }
        for name in keep_m
    }
    objects = {
        name: {"key": list(onto.objects[name].key)}
        for name in keep_obj
        if name in onto.objects
    }
    links: dict[str, Any] = {}
    for name, link in onto.links.items():
        if link.from_object in keep_obj and link.to_object in keep_obj:
            links[name] = {
                "from": link.from_object,
                "to": link.to_object,
                "cardinality": link.cardinality,
            }
            if len(links) >= MAX_LINKS:
                break
    columns = {
        str(obj): sorted(c for c in cols if _score(str(c), toks) or obj in keep_obj)[:MAX_COLS]
        for obj, cols in cols_cache.items()
        if obj in keep_obj and isinstance(cols, set)
    }
    return {
        "measures": measures,
        "objects": objects,
        "links": links,
        "columns": columns,
    }


def summarize_context(parts: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(parts, default=str, sort_keys=True)
    if len(blob) <= MAX_CONTEXT_CHARS:
        return parts
    trimmed = dict(parts)
    trimmed["encodings"] = {}
    blob = json.dumps(trimmed, default=str, sort_keys=True)
    if len(blob) <= MAX_CONTEXT_CHARS:
        trimmed["summarized"] = True
        return trimmed
    schema = list(trimmed.get("schema") or [])[:3]
    trimmed["schema"] = schema
    trimmed["summarized"] = True
    return trimmed


def retrieve_short_context(
    question: str,
    *,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    ontology: Ontology | None = None,
) -> dict[str, Any]:
    """Schema SQL + ontology + value encodings, summarized. No measure SQL, no secrets."""
    toks = question_tokens(question)
    allowed = grantable or set()
    schema = retrieve_schema_sql(warehouse, allowed, toks)
    encodings = retrieve_value_encodings(warehouse, schema, toks)
    onto_slice = retrieve_ontology_slice(ontology, toks)
    methods = ["summarize"]
    if schema:
        methods.insert(0, "schema_sql")
    if onto_slice.get("measures") or onto_slice.get("objects"):
        methods.insert(0, "ontology")
    if encodings:
        methods.insert(0, "sql_filter_values")
    if schema and (onto_slice.get("measures") or onto_slice.get("objects")):
        methods.insert(0, "hybrid_fuse")
    parts: dict[str, Any] = {
        "verified": bool(ontology is not None and ontology.verified),
        "methods": methods,
        "schema": schema,
        "encodings": encodings,
        **onto_slice,
    }
    return summarize_context(parts)


def _locked_measure(question: str) -> str | None:
    """One measure the question names, or None. Not a certified-pack lookup."""
    qn = (question or "").lower()
    if "quantity sold" in qn or "qty sold" in qn:
        return "outbound_kg"
    if "sku count" in qn or "how many sku" in qn or "how many unique sku" in qn:
        return "sku_count"
    if "shipment cost" in qn or "freight" in qn:
        return "shipping_cost_myr"
    if "capacity utilisation" in qn or "capacity utilization" in qn:
        return "utilisation_pct"
    if "stock value" in qn:
        return "stock_value_myr"
    if "total spend" in qn or "spend by" in qn:
        return "stock_value_myr"
    if "revenue" in qn or "selling sku" in qn:
        return "outbound_value_myr"
    return None


def _group_from_hints(question: str) -> list[list[str]] | None:
    qn = (question or "").lower()
    hits = [
        [obj, col]
        for needles, obj, col in _DIM_HINTS
        if any(n in qn for n in needles)
    ]
    if not hits:
        return None
    unique: list[list[str]] = []
    for pair in hits:
        if pair not in unique:
            unique.append(pair)
    if len(unique) > 1:
        return None
    return unique


def bind_plan(question: str, context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Typed plan from retrieved context. Not a certified-pack lookup.

    Time language and untyped filters miss (contract ask may still run). Tied
    top measures are unsure. Entity list/which/rank misses. A by/per/each or
    top-N ask with no bound dimension misses rather than blocking Cortex.
    """
    if not isinstance(context, dict):
        return None
    q = question or ""
    if _TIME.search(q) or _UNTYPED.search(q) or _ENTITY_PREFIX.search(q):
        return None
    measures = context.get("measures") or {}
    if not isinstance(measures, dict) or not measures:
        return None
    lock = _locked_measure(q)
    toks = question_tokens(q)
    ranked = sorted(
        (
            (
                _score(name, toks) * 2
                + _score(str((spec or {}).get("description") or ""), toks),
                name,
            )
            for name, spec in measures.items()
            if isinstance(spec, dict)
        ),
        reverse=True,
    )
    ranked = [(sc, name) for sc, name in ranked if sc > 0]
    if lock and lock in measures:
        ranked = [(sc, name) for sc, name in ranked if name == lock] or [(1, lock)]
    if not ranked:
        return None
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return {"unsure": True}
    measure = ranked[0][1]
    grain = str((measures.get(measure) or {}).get("grain") or "")
    needs_dim = bool(_NEEDS_DIM.search(q))
    top = _TOP_N.search(q)
    hinted = _group_from_hints(q)
    group_by: list[list[str]] = []
    if hinted:
        group_by = hinted
    else:
        objects = context.get("objects") or {}
        columns = context.get("columns") or {}
        candidates: list[tuple[int, str, str]] = []
        if isinstance(objects, dict) and isinstance(columns, dict):
            for obj_name, spec in objects.items():
                keys = list((spec or {}).get("key") or []) if isinstance(spec, dict) else []
                cols = set(keys) | set(columns.get(obj_name) or [])
                for col in cols:
                    col_s = str(col)
                    col_sc = _score(col_s, toks)
                    if col_sc <= 0:
                        continue
                    obj_sc = _score(str(obj_name), toks)
                    candidates.append((col_sc + obj_sc, str(obj_name), col_s))
        if candidates:
            dim_cands = [c for c in candidates if c[1] != grain] or candidates
            dim_cands.sort(reverse=True)
            if len(dim_cands) > 1 and dim_cands[0][0] == dim_cands[1][0] and (
                dim_cands[0][1], dim_cands[0][2]
            ) != (dim_cands[1][1], dim_cands[1][2]):
                if dim_cands[0][2] != dim_cands[1][2]:
                    return {"unsure": True}
            _sc, obj, col = dim_cands[0]
            group_by = [[obj, col]]
    if measure == "utilisation_pct" and not group_by:
        group_by = [["location", "location_code"]]
    if (needs_dim or top) and not group_by:
        return None
    if not needs_dim and not top and measure != "utilisation_pct":
        group_by = []
    limit = int(top.group(1)) if top else 50
    return {"query_plan": {"measure": measure, "group_by": group_by, "limit": limit}}


__all__ = [
    "MAX_CONTEXT_CHARS",
    "bind_plan",
    "question_tokens",
    "retrieve_short_context",
]
