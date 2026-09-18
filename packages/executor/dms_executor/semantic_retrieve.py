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
_SPINE_PATH = Path(__file__).with_name("ontology_spine.yaml")
_WH_A = re.compile(r"\b(warehouse a|wh-a)\b", re.I)
_COLD = re.compile(r"cold[\s-]?storage", re.I)
_STILL_UNTYPED = re.compile(
    r"\b(delayed|alerts?|storage bin|high-risk|pending shipment|risk and lead)\b",
    re.I,
)
_ABOVE_PCT = re.compile(r"above\s+(\d{1,2})\s+percent", re.I)


def load_ontology_spine(path: Path | None = None) -> dict[str, Any] | None:
    """Slot-name YAML pack for retrieve. No measure SQL. None if unreadable."""
    target = path or _SPINE_PATH
    if not target.is_file():
        return None
    try:
        import yaml
    except ImportError:
        return None
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or data.get("kind") != "dms.ontology_spine":
        return None
    return data


def load_measure_aliases(path: Path | None = None) -> dict[str, str]:
    """Cortex pack metric id -> DMS measure. Slot names only. No SQL."""
    raw = (load_ontology_spine(path) or {}).get("measure_aliases") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in raw.items():
        src = str(key or "").strip()
        dest = str(val or "").strip()
        if src and dest:
            out[src] = dest
    return out


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


def intent_slots(question: str, ontology: Ontology | None = None) -> dict[str, Any]:
    """Locked measure / group / limit / keep_gt for Cortex generate. No SQL."""
    q = question or ""
    lock = _locked_measure(q)
    if lock and ontology is not None and lock not in ontology.measures:
        lock = None
    hinted = _group_from_hints(q)
    top = _TOP_N.search(q)
    above = _ABOVE_PCT.search(q)
    out: dict[str, Any] = {}
    if lock:
        out["measure"] = lock
    if hinted:
        out["group_by"] = hinted
    if top:
        out["limit"] = int(top.group(1))
    if above:
        out["keep_gt"] = float(above.group(1))
    return out


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
    spine = load_ontology_spine()
    if spine and onto is not None:
        allowed = {str(x) for x in (spine.get("measures") or [])}
        onto_names = set(onto.measures)
        # Demo retrieve uses the YAML pack as allowlist. A test/custom ontology
        # with other measure names keeps those names (spine is not a ceiling).
        if allowed and onto_names <= allowed:
            keep_m = [name for name in keep_m if name in allowed]
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


def _one_value(warehouse: Path, sql: str) -> str | None:
    con = connect_file(warehouse)
    try:
        rows = con.execute(sql).fetchall()
    except Exception:  # noqa: BLE001 -- empty retrieve, do not 503
        return None
    finally:
        con.close()
    vals = [str(r[0]) for r in rows if r and r[0] is not None]
    uniq = list(dict.fromkeys(vals))
    return uniq[0] if len(uniq) == 1 else None


def lookup_bound_values(question: str, warehouse: Path | None) -> dict[str, str]:
    """Lake encodings for typed filters. Not a certified-pack lookup."""
    if warehouse is None or not Path(warehouse).is_file():
        return {}
    qn = (question or "").lower()
    path = Path(warehouse)
    out: dict[str, str] = {}
    if _WH_A.search(question or ""):
        code = _one_value(
            path,
            "SELECT location_code FROM locations WHERE "
            "lower(CAST(location_code AS VARCHAR)) = 'wh-a' "
            "OR lower(CAST(name AS VARCHAR)) = 'warehouse a' LIMIT 3",
        )
        if code:
            out["location.location_code"] = code
    if "chemical" in qn:
        cat = _one_value(
            path,
            "SELECT DISTINCT CAST(category AS VARCHAR) FROM inventory "
            "WHERE lower(CAST(category AS VARCHAR)) LIKE '%chemical%' LIMIT 3",
        )
        if cat:
            out["lot.category"] = cat
            out["product.category"] = cat
    return out


def _has_col(context: dict[str, Any], obj: str, col: str) -> bool:
    cols = context.get("columns") or {}
    got = cols.get(obj) or []
    return col in got


def typed_filters(question: str, context: dict[str, Any]) -> list[list[Any]] | None:
    """None = needs a filter we cannot type. [] = no extra filters."""
    qn = (question or "").lower()
    bound = context.get("bound_values") or {}
    filters: list[list[Any]] = []
    if _COLD.search(question or ""):
        if not _has_col(context, "location", "is_cold_storage"):
            return None
        filters.append(["location", "is_cold_storage", "=", True])
    if re.search(r"\bexpir", qn):
        if not _has_col(context, "lot", "expiry_date"):
            return None
        from datetime import date

        filters.append(["lot", "expiry_date", "<", date.today().isoformat()])
    if _WH_A.search(question or ""):
        code = bound.get("location.location_code")
        if not code or not _has_col(context, "location", "location_code"):
            return None
        filters.append(["location", "location_code", "=", code])
    if "chemical" in qn:
        cat = bound.get("lot.category") or bound.get("product.category")
        obj = "lot" if _has_col(context, "lot", "category") else "product"
        if not cat or not _has_col(context, obj, "category"):
            return None
        filters.append([obj, "category", "=", cat])
    if ("cctv" in qn or "camera" in qn) and not _has_col(
        context, "location", "cctv_camera_id"
    ):
        return None
    if "reorder" in qn and "below_reorder_lots" not in (context.get("measures") or {}):
        return None
    return filters


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
    bound = lookup_bound_values(question, warehouse)
    aliases = load_measure_aliases()
    if aliases and ontology is not None:
        keep_m = onto_slice.setdefault("measures", {})
        for alias_id, dest in aliases.items():
            if dest not in ontology.measures:
                continue
            if not (_score(alias_id, toks) or _score(dest, toks)):
                continue
            spec = ontology.measures[dest]
            keep_m[dest] = {"grain": spec.grain, "description": spec.description}
            grain = spec.grain
            if grain in ontology.objects:
                onto_slice.setdefault("objects", {}).setdefault(
                    grain, {"key": list(ontology.objects[grain].key)}
                )
    lock = _locked_measure(question)
    if lock and ontology is not None and lock in ontology.measures:
        spec = ontology.measures[lock]
        onto_slice.setdefault("measures", {})[lock] = {
            "grain": spec.grain,
            "description": spec.description,
        }
        grain = spec.grain
        if grain in ontology.objects:
            onto_slice.setdefault("objects", {}).setdefault(
                grain, {"key": list(ontology.objects[grain].key)}
            )
    cache = (ontology.__dict__.get("_column_cache") or {}) if ontology else {}
    extras = {
        "location": ["is_cold_storage", "location_code", "cctv_camera_id", "name"],
        "lot": ["expiry_date", "category", "reorder_level_kg", "quantity_kg"],
        "product": ["category", "sku"],
    }
    cols = dict(onto_slice.get("columns") or {})
    for obj, names in extras.items():
        have = set(cache.get(obj) or [])
        add = [n for n in names if n in have]
        if not add:
            continue
        cur = list(cols.get(obj) or [])
        for name in add:
            if name not in cur:
                cur.append(name)
        cols[obj] = cur
        if ontology is not None and obj in ontology.objects:
            onto_slice.setdefault("objects", {}).setdefault(
                obj, {"key": list(ontology.objects[obj].key)}
            )
    onto_slice["columns"] = cols
    methods = ["summarize"]
    if load_ontology_spine():
        methods.insert(0, "spine_yaml")
    if schema:
        methods.insert(0, "schema_sql")
    if onto_slice.get("measures") or onto_slice.get("objects"):
        methods.insert(0, "ontology")
    if encodings or bound:
        methods.insert(0, "sql_filter_values")
    if schema and (onto_slice.get("measures") or onto_slice.get("objects")):
        methods.insert(0, "hybrid_fuse")
    parts: dict[str, Any] = {
        "verified": bool(ontology is not None and ontology.verified),
        "methods": methods,
        "schema": schema,
        "encodings": encodings,
        "bound_values": bound,
        "intent_slots": intent_slots(question, ontology),
        "measure_aliases": aliases,
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
    if _COLD.search(question or ""):
        return "utilisation_pct"
    if _ABOVE_PCT.search(question or "") or "90 percent" in qn:
        return "utilisation_pct"
    if "cctv" in qn or "camera" in qn:
        return "utilisation_pct"
    if "reorder" in qn:
        return "below_reorder_lots"
    if "expir" in qn or "chemical" in qn:
        return "stock_value_myr"
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

    Time language and leftover untyped traps miss. Typed lake filters bind.
    Tied top measures are unsure. A by/per/each or top-N ask with no bound
    dimension misses rather than blocking Cortex.
    """
    if not isinstance(context, dict):
        return None
    q = question or ""
    if _TIME.search(q) or _STILL_UNTYPED.search(q):
        return None
    filters = typed_filters(q, context)
    if filters is None:
        return None
    measures = context.get("measures") or {}
    if not isinstance(measures, dict) or not measures:
        return None
    lock = _locked_measure(q)
    entity = bool(_ENTITY_PREFIX.search(q))
    if entity and not filters and not lock:
        return None
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
        return {"unsure": True, "plan_source": "bind_plan"}
    measure = ranked[0][1]
    grain = str((measures.get(measure) or {}).get("grain") or "")
    needs_dim = bool(_NEEDS_DIM.search(q))
    top = _TOP_N.search(q)
    hinted = _group_from_hints(q)
    group_by: list[list[str]] = []
    qn = q.lower()
    if "cctv" in qn or "camera" in qn:
        if _has_col(context, "location", "cctv_camera_id"):
            group_by = [["location", "cctv_camera_id"]]
    elif _COLD.search(q) or (entity and "location" in qn):
        if _has_col(context, "location", "location_code"):
            group_by = [["location", "location_code"]]
    elif "expir" in qn or "chemical" in qn or "reorder" in qn:
        group_by = [["product", "sku"]]
    elif hinted:
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
                    return {"unsure": True, "plan_source": "bind_plan"}
            _sc, obj, col = dim_cands[0]
            group_by = [[obj, col]]
    if measure == "utilisation_pct" and not group_by:
        group_by = [["location", "location_code"]]
    if (needs_dim or top) and not group_by:
        return None
    if (
        not needs_dim
        and not top
        and not entity
        and not filters
        and measure != "utilisation_pct"
    ):
        group_by = []
    limit = int(top.group(1)) if top else 50
    plan: dict[str, Any] = {"measure": measure, "group_by": group_by, "limit": limit}
    if filters:
        plan["filters"] = filters
    above = _ABOVE_PCT.search(q)
    if above:
        plan["keep_gt"] = float(above.group(1))
    return {"query_plan": plan, "plan_source": "bind_plan"}


def slots_for_measure(
    question: str,
    context: dict[str, Any] | None,
    measure: str,
) -> dict[str, Any] | None:
    """Retrieve-typed group/filter/limit for a Cortex-ranked DMS measure.

    plan_source stays ontology_plan. Not a bind_plan answer. A by/per/top-N
    or which/list/rank ask with no typed dimension misses (WRONG=0).
    """
    mid = str(measure or "").strip()
    if not isinstance(context, dict) or not mid:
        return None
    measures = context.get("measures") or {}
    if not isinstance(measures, dict):
        return None
    spec = measures.get(mid)
    if not isinstance(spec, dict):
        spec = {"grain": "", "description": ""}
    locked = dict(context)
    locked["measures"] = {mid: spec}
    bound = bind_plan(question, locked)
    q = question or ""
    needs_dim = bool(
        _NEEDS_DIM.search(q) or _TOP_N.search(q) or _ENTITY_PREFIX.search(q)
    )
    if not isinstance(bound, dict) or bound.get("unsure") is True:
        if needs_dim:
            return None
        return {
            "query_plan": {"measure": mid, "group_by": [], "filters": []},
            "plan_source": "ontology_plan",
        }
    plan = bound.get("query_plan")
    if not isinstance(plan, dict):
        return None
    out = dict(plan)
    out["measure"] = mid
    return {"query_plan": out, "plan_source": "ontology_plan"}


__all__ = [
    "MAX_CONTEXT_CHARS",
    "bind_plan",
    "intent_slots",
    "load_measure_aliases",
    "load_ontology_spine",
    "question_tokens",
    "retrieve_short_context",
    "slots_for_measure",
]
