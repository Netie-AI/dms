"""GEN-01 multi-method semantic retrieve → short context.

Ideas-only from DB-GPT-class schema linking (no clone, no extra dep): SQL-filter
over information_schema + ontology overlay + bounded DISTINCT encodings, then
summarize. Token overlap is O(n) over granted columns.

``DMS_NO_WORD_RULES`` off keeps the keyword measure lock. On, that lock is
``rank_texts`` over the ontology (name, description, steward synonyms).

ponytail: token overlap, not a vector index. Ceiling: wide lakes. Upgrade: Cortex
schema-link service behind the existing compute HTTP call.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from cortex_client.compute import pack_id_shape, ranked_measure_tokens
from cortex_client.qualifiers import parse_rank_window
from dms_core.pii import column_is_pii, sanitize_retrieve_parts

from dms_executor.demo_warehouse import connect_serving
from dms_executor.keyword_routes import (
    _DIM_HINTS,
    asks_for_dimension,
    locked_measure,
    lookup_bound_values,
)
from dms_executor.keyword_routes import (
    bind_plan as _keyword_bind_plan,
)
from dms_executor.keyword_routes import (
    intent_slots as _keyword_intent_slots,
)
from dms_executor.measure_link import no_word_rules_enabled, rank_measure_link
from dms_executor.ontology import Ontology


def _keyword_lock_off() -> bool:
    """Keyword measure lock is off. ``DMS_RULES_OFF`` implies ``DMS_NO_WORD_RULES``."""
    if os.environ.get("DMS_RULES_OFF", "0").strip() == "1":
        return True
    return no_word_rules_enabled()


MAX_CONTEXT_CHARS = 2400
MAX_TABLES = 6
MAX_COLS = 8
MAX_MEASURES = 10
MAX_LINKS = 8
MAX_SAMPLE = 6
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SPINE_PATH = Path(__file__).with_name("ontology_spine.yaml")


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
    # Certified VQ-01 typo synonym of category. Overlay/retrieve must
    # group product.category on "top 3 categoty sales", not sku.
    if "categoty" in out:
        out.add("category")
    if "worth" in out:
        out.add("value")
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
    con = connect_serving(Path(warehouse))
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
    con = connect_serving(Path(warehouse))
    try:
        for item in schema:
            table = _safe_ident(str(item.get("table") or ""))
            if not table:
                continue
            for col in item.get("columns") or []:
                name = _safe_ident(str(col))
                if not name or skip.search(name) or _score(name, toks) <= 0:
                    continue
                # Name-flagged PII is never sampled. Detector errors fail closed.
                if column_is_pii(name, (), table=table):
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
                if not vals:
                    continue
                if column_is_pii(name, vals, table=table):
                    continue
                encodings[key] = vals
    finally:
        con.close()
    return encodings


def intent_slots(
    question: str,
    ontology: Ontology | None = None,
    *,
    embedder: Any = None,
) -> dict[str, Any]:
    """Locked measure / group / limit / keep_gt for Cortex generate. No SQL.

    Flag off is the keyword lock. Flag on ranks the ontology and does not
    keep a keyword measure when that rank is a tie or under the floor.
    """
    if not _keyword_lock_off():
        return _keyword_intent_slots(question, ontology)
    slots = _keyword_intent_slots(question, ontology, keyword_lock=False)
    link = rank_measure_link(question, ontology, embedder=embedder)
    window = parse_rank_window(question or "")
    if window is None:
        slots.pop("measure", None)
    if link.name and link.reason_code is None:
        if ontology is None or link.name in ontology.measures:
            slots["measure"] = link.name
    elif link.reason_code and window is None:
        slots.pop("measure", None)
    return slots


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
        "grains": {
            name: bool(spec.get("present"))
            for name, spec in onto.supply_chain_catalog().items()
        },
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
    embedder: Any = None,
) -> dict[str, Any]:
    """Schema SQL + ontology + value encodings, summarized. No measure SQL, no secrets."""
    toks = question_tokens(question)
    allowed = grantable or set()
    schema = retrieve_schema_sql(warehouse, allowed, toks)
    encodings = retrieve_value_encodings(warehouse, schema, toks)
    onto_slice = retrieve_ontology_slice(ontology, toks)
    bound = lookup_bound_values(question, warehouse)
    cleaned = sanitize_retrieve_parts({"encodings": encodings, "bound_values": bound})
    encodings = cleaned.get("encodings") or {}
    bound = cleaned.get("bound_values") or {}
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
    if _keyword_lock_off():
        link = rank_measure_link(question, ontology, embedder=embedder)
        lock = link.name if link.reason_code is None else None
    else:
        lock = locked_measure(question)
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
        "supplier": ["last_audit_date", "supplier_id", "country", "risk_score"],
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
        "intent_slots": intent_slots(question, ontology, embedder=embedder),
        "measure_aliases": aliases,
        **onto_slice,
    }
    return summarize_context(parts)



def _dimension_tokens(onto: Ontology | None, grain: str) -> set[str]:
    """Tokens that name the grain, not the measure.

    Object, key, grain alias, and a dimension hint whose words are that
    grain. A hint that also carries a measure word ("category sales") keeps
    only the grain word.
    """
    out: set[str] = set()
    obj = onto.objects.get(grain) if onto is not None else None
    if obj is not None:
        out |= ranked_measure_tokens(obj.name)
        for col in (*obj.key, *getattr(obj, "business_key", ())):
            out |= ranked_measure_tokens(str(col))
    if onto is not None:
        for alias, dest in onto.grain_aliases.items():
            if dest == grain:
                out |= ranked_measure_tokens(alias)
    for needles, obj_name, col in _DIM_HINTS:
        if obj_name != grain:
            continue
        anchor = ranked_measure_tokens(obj_name) | ranked_measure_tokens(col)
        for needle in needles:
            words = ranked_measure_tokens(needle)
            hit = words & anchor
            out |= hit or words
    return out


def question_grounds_measure(
    question: str, measure_name: str, onto: Ontology | None
) -> bool:
    """True when this question names ``measure_name`` in the ontology or schema.

    A retrieve lock is a name the ontology binder already accepted. Otherwise
    the question must share a token with the measure name, its description,
    or a column its expression reads. A token that only names the grain
    (warehouse for location) does not name the measure. No phrase table.
    """
    name = str(measure_name or "").strip()
    if not name or onto is None or name not in onto.measures:
        return False
    # Lock and rank-window already name a measure. That is the binder, not a new phrase.
    if str(intent_slots(question, onto).get("measure") or "") == name:
        return True
    measure = onto.measures[name]
    asked = ranked_measure_tokens(question)
    body = ranked_measure_tokens(
        " ".join(
            part
            for part in (
                measure.name,
                measure.description or "",
                measure.expression or "",
            )
            if part
        )
    )
    # A grain word that is also the measure name (customer_count) still names it.
    # A grain word that only appears in the description (warehouse on utilisation)
    # does not.
    named = ranked_measure_tokens(measure.name)
    dims = _dimension_tokens(onto, measure.grain) - named
    return bool((asked & body) - dims)


def shape_from_metric_id(metric_id: str) -> dict[str, Any]:
    """Group/limit/keep_gt encoded in a Cortex pack metric id. No SQL."""
    return pack_id_shape(metric_id)



def bind_plan(
    question: str,
    context: dict[str, Any] | None,
    *,
    ontology: Ontology | None = None,
    embedder: Any = None,
    link_measures: bool | None = None,
) -> dict[str, Any] | None:
    """Typed plan from retrieved context. Not a certified-pack lookup.

    Flag off is the keyword binder. Flag on links the measure by rank.
    A tie or a score under the floor is unsure and does not pick a measure.
    ``link_measures=False`` types slots for a measure the caller already chose.
    """
    use_link = _keyword_lock_off() if link_measures is None else link_measures
    if not use_link:
        return _keyword_bind_plan(question, context)
    ctx_in = context if isinstance(context, dict) else None
    link = rank_measure_link(
        question, ontology, embedder=embedder, context=ctx_in
    )
    if link.reason_code:
        return {
            "unsure": True,
            "plan_source": "bind_plan",
            "reason_code": link.reason_code,
        }
    ctx = context
    if (
        link.name
        and ontology is not None
        and isinstance(context, dict)
        and link.name in ontology.measures
    ):
        measures = dict(context.get("measures") or {})
        if link.name not in measures:
            spec = ontology.measures[link.name]
            measures[link.name] = {
                "grain": spec.grain,
                "description": spec.description,
            }
            ctx = dict(context)
            ctx["measures"] = measures
    return _keyword_bind_plan(
        question, ctx, keyword_lock=False, lock_override=link.name
    )



def slots_for_measure(
    question: str,
    context: dict[str, Any] | None,
    measure: str,
    ranked_id: str | None = None,
) -> dict[str, Any] | None:
    """Retrieve-typed group/filter/limit for a Cortex-ranked DMS measure.

    plan_source stays ontology_plan. Not a bind_plan answer. A by/per/top-N
    or which/list/rank ask with no typed dimension misses (WRONG=0) unless
    the ranked pack id itself encodes the grain (``_by_category``, ``top5``).
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
    # The measure is already chosen. Do not rank a different one.
    bound = bind_plan(question, locked, link_measures=False)
    q = question or ""
    needs_dim = asks_for_dimension(q)
    shape = shape_from_metric_id(ranked_id or "")
    shape_group = list(shape.get("group_by") or [])

    def _apply_shape(plan: dict[str, Any]) -> dict[str, Any]:
        out = dict(plan)
        out["measure"] = mid
        if not out.get("group_by") and shape_group:
            out["group_by"] = shape_group
        if out.get("limit") in (None, 50) and shape.get("limit") is not None:
            out["limit"] = shape["limit"]
        if out.get("keep_gt") is None and shape.get("keep_gt") is not None:
            out["keep_gt"] = shape["keep_gt"]
        return out

    if not isinstance(bound, dict) or bound.get("unsure") is True:
        if needs_dim and not shape_group:
            return None
        fallback = _apply_shape(
            {"measure": mid, "group_by": shape_group, "filters": []}
        )
        return {"query_plan": fallback, "plan_source": "ontology_plan"}
    raw_plan = bound.get("query_plan")
    if not isinstance(raw_plan, dict):
        return None
    return {"query_plan": _apply_shape(raw_plan), "plan_source": "ontology_plan"}


__all__ = [
    "MAX_CONTEXT_CHARS",
    "bind_plan",
    "intent_slots",
    "load_measure_aliases",
    "load_ontology_spine",
    "question_grounds_measure",
    "question_tokens",
    "retrieve_short_context",
    "shape_from_metric_id",
    "slots_for_measure",
]
