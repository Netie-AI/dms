"""GEN-01 — ontology-grounded generative ask + execute-validate.

Exact-match VQ/pack stays first. Retrieve a short schema/ontology context,
hand it to a caller-supplied ``compute`` planner, fill typed slots, compile,
validate, then Cortex-submit. Unsure or validate-fail is ABSTAIN. A compute
miss returns None (existing contract ask still runs).

GEN-RESTORE-01: ``Executor.live_ask`` passes an Insights-only compute seam
(generate + ontology ranking + one ranked retry) with ``bind_on_miss=False``.
No ask lane POSTs ``/dms/query``. Insights fail-closed reasons never bind.
Cortex POST /dms/query still ignores ontology context (KB F-0055);
``compute_query`` stays for CONTRACT-FAKE-01.

Does not expand certified exact-match packs. Does not invent provider keys.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import sqlglot
from cortex_client.ask_clock import (
    attach_timings,
    ensure_clock,
    note_prompt_ctx,
    release_clock,
    span,
)
from cortex_client.compute import (
    INSIGHTS_FAIL_AI_ERROR,
    INSIGHTS_FAIL_UNAUTHORIZED,
    PLAN_ORIGIN_GENERATE_SQL,
    PLAN_ORIGIN_ONTOLOGY_COMPILE,
    PLAN_ORIGIN_ONTOLOGY_RANKING,
    PLAN_ORIGINS,
    SERVED_LEG_KEYS,
    cloop_b_enabled,
    gen01_rules_enabled,
    generate_model_called,
    insights_budget_stop,
    insights_fail_reason,
    insights_query_sql,
    insights_was_reached,
    ontology_ranked_lane_enabled,
    query_plan_from_insights_ranking,
    recorded_model_calls,
    schema_retry_enabled,
    typed_query_plan,
)
from cortex_client.insights import SCHEMA_CONTEXT_FIELD, schema_context_enabled
from cortex_client.qualifiers import (
    parse_rank_window,
    rank_window_group,
    rank_window_measure,
    rank_window_shape_reason,
    unhonored_qualifier_reason,
)
from dms_core.ask_stream import note_rungs, note_stage
from dms_core.pii import mask_unknown_keys
from sqlglot import exp

from dms_executor.abstain import build_abstain
from dms_executor.demo_ask import _is_predictive, normalize_ask_question
from dms_executor.demo_pack import is_uncertified_paraphrase
from dms_executor.demo_warehouse import (
    DEMO_TABLES,
    SERVING_DIALECT,
    connect_serving,
    sql_has_reserved_as_of,
    warehouse_path,
)
from dms_executor.envelope import (
    RESERVED_PARAM_AS_OF,
    _relation_bare,
    _sql_cited_labels,
    asked_calendar_years,
    assert_envelope_valid,
    build_answer_envelope,
    chart_from_rows,
    reserved_as_of_abstain,
)
from dms_executor.gen_path_refuse import (
    customer_abstain_text,
    ranking_missing_metric_gap,
)
from dms_executor.grant_struct import (
    customer_grant_reason,
    serve_gap,
    sql_refusal_envelope,
    sqlglot_dialect,
    structural_grant_stop,
)
from dms_executor.ground_defs import (
    GroundedDef,
    build_grounding,
    ground_defs_enabled,
    mismatch_envelope,
)
from dms_executor.keyword_routes import vague_question
from dms_executor.manifest import OpenVaultTokenError
from dms_executor.measure_link import no_word_rules_enabled, reconfirm_for_reason
from dms_executor.ontology import (
    CompiledQuery,
    Coverage,
    LinkType,
    Ontology,
    Refusal,
    Violation,
    WherePath,
    coverage_from_sql_path,
    coverage_valid,
    demo_ontology,
    detect_supply_chain_grains,
    missing_join_for_ungranted,
    onto_from_data_enabled,
    try_compile_multi_grain,
)
from dms_executor.route_stamp import (
    ROUTE_COMPILE,
    ROUTE_LADDER,
    ROUTE_MODEL,
    freeze_route,
    stamp_route,
)
from dms_executor.schema_context import (
    SCHEMA_CONTEXT_ENVELOPE_KEY,
    SCHEMA_INDEX_STAMP_KEY,
    join_key_facts,
    prepare_generate_context,
)
from dms_executor.semantic_retrieve import (
    bind_plan,
    intent_slots,
    load_measure_aliases,
    question_grounds_measure,
    retrieve_short_context,
    slots_for_measure,
)
from dms_executor.space_ontology import (
    REASON_NO_DECLARED_MEASURE,
    REASON_UNVERIFIED_JOIN,
    budget_space_block,
    relation_columns,
    space_catalog,
    unverified_join_reason,
)
from dms_executor.sql_currency import currency_mismatch_reason, is_multi_statement
from dms_executor.sql_loop import (
    EMPTY_NOTE,
    EMPTY_UNVERIFIED,
    AskCallBudget,
    apply_sql_credit,
    loop_entry,
    run_model_loop,
    run_readonly,
    typed_db_error,
    written_names,
)
from dms_executor.sql_vote import (
    disagreement_detail,
    empty_plan,
    ontology_json,
    reading_prompt,
    readings_agree,
    reconfirm_envelope,
    reconfirm_from_payload,
    reconfirm_prompt,
)
from dms_executor.stated_values import dropped_stated_value_reason
from dms_executor.verified_queries import (
    examples_text,
    maybe_save_checked_sql,
    retrieve_verified_examples,
    rows_from_submit_result,
    vq_rag_enabled,
)

_KNOWN = frozenset(DEMO_TABLES)
PLAN_SOURCE_ONTOLOGY = "ontology_plan"
PLAN_SOURCE_BIND = "bind_plan"
PLAN_SOURCE_OTHER = "other"
PLAN_SOURCES = frozenset(
    {PLAN_SOURCE_ONTOLOGY, PLAN_SOURCE_BIND, PLAN_SOURCE_OTHER}
)
NOTE_INSIGHTS_RANKING = "insights_ranking:ontology_plan"
NOTE_FALLBACK_GENERATE_EMPTY = "fallback:generate_empty"
NOTE_FALLBACK_VALIDATE_PREFIX = "fallback:validate:"
NOTE_RANK_WINDOW = "rank_window:deterministic_compile (no generate call)"
_ENTITY_NOUN = {"sku": "SKUs", "supplier": "suppliers", "category": "categories",
                "warehouse": "warehouses", "plant": "plants"}
# Cortex#269 ROUTER-1 Insights fingerprint. Copy as received; never infer.
SETUP_FIELD_KEYS: tuple[str, ...] = (
    "served_provider",
    "served_model",
    "served_local",
    "learn_enabled",
    "learn_source",
    "route_store_id",
)
SERVED_ATTR_REPORTED = "reported"
SERVED_ATTR_MISSING = "missing"
SERVED_ATTR_NONE = "none"
SERVED_ATTR_DIAG_ENV = "DMS_SERVED_ATTR_DIAG"


def normalize_plan_source(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    return val if val in PLAN_SOURCES else PLAN_SOURCE_OTHER


def plan_source_from_payload(payload: dict[str, Any] | None) -> str:
    """Producer stamp only. Missing or unknown is other — do not infer from SQL."""
    if not isinstance(payload, dict):
        return PLAN_SOURCE_OTHER
    return normalize_plan_source(payload.get("plan_source"))


def with_plan_source(env: dict[str, Any], source: str) -> dict[str, Any]:
    env["plan_source"] = normalize_plan_source(source)
    return env


def normalize_plan_origin(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    return val if val in PLAN_ORIGINS else ""


def plan_origin_from_payload(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    return normalize_plan_origin(payload.get("plan_origin"))


def with_plan_origin(env: dict[str, Any], origin: str) -> dict[str, Any]:
    val = normalize_plan_origin(origin)
    if val:
        env["plan_origin"] = val
    return env


def generate_legs_view(
    payload: dict[str, Any] | None, *, validate_reason: str | None = None
) -> dict[str, Any]:
    """How many generate legs ran, and whether each returned SQL, a plan, or nothing."""
    legs: list[dict[str, Any]]
    count: int
    if isinstance(payload, dict) and isinstance(payload.get("generate_legs"), dict):
        raw = payload["generate_legs"]
        raw_legs = raw.get("legs") if isinstance(raw.get("legs"), list) else []
        legs = []
        for item in raw_legs:
            if isinstance(item, dict):
                got = str(item.get("returned") or "nothing").strip().lower()
            else:
                got = "nothing"
            if got not in {"sql", "plan", "nothing", "timeout"}:
                got = "nothing"
            leg: dict[str, Any] = {"returned": got}
            if isinstance(item, dict):
                # SERVED-ATTR-01: per-leg attribution, as Cortex reported it.
                for key in SERVED_LEG_KEYS:
                    if key in item:
                        leg[key] = item[key]
            legs.append(leg)
        count = int(raw.get("count") or len(legs))
        if not validate_reason:
            leftover = str(raw.get("validate_reason") or "").strip()
            validate_reason = leftover or None
    elif isinstance(payload, dict):
        if insights_query_sql(payload) or query_sql_from_payload(payload):
            got = "sql"
        elif typed_query_plan(payload) is not None:
            got = "plan"
        else:
            got = "nothing"
        legs = [{"returned": got}]
        count = 1
    else:
        legs = []
        count = 0
    out: dict[str, Any] = {"count": count, "legs": legs}
    if validate_reason:
        out["validate_reason"] = validate_reason
    return out


def with_setup_fields(
    env: dict[str, Any] | None, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Copy Cortex Insights setup fields onto the envelope exactly as received.

    A null stays null. A missing key stays absent. Never default or guess.
    dms#264 harness fingerprint compare is parked; this is copy-through only.
    """
    if env is None or not isinstance(payload, dict):
        return env
    for key in SETUP_FIELD_KEYS:
        if key in payload:
            env[key] = payload[key]
    return env


def served_attribution(payload: dict[str, Any] | None) -> str:
    """SERVED-ATTR-01 (dms#305): reported / missing / none for one ask.

    ``reported``: Cortex sent both ``served_provider`` and ``served_model``
    on the payload and on every generate leg.
    ``missing``: a generate call may have reached a model and either field is
    absent or null - the grid runner labels that attempt INVALID.
    ``none``: no model was called. Never inferred from logs, config or SQL.
    A ranking-only answer whose climb is UNARMED / NO_KEY / REFUSED_AUTH, or
    a bearer refuse that made no HTTP call, is ``none`` even when
    ``generate_legs`` count is greater than zero.
    """
    if not isinstance(payload, dict):
        return SERVED_ATTR_NONE
    raw = payload.get("generate_legs")
    legs = raw.get("legs") if isinstance(raw, dict) else None
    # Per call: one leg without attribution (a retry, a timeout) is missing
    # even when the last leg's stamp reached the top level.
    legs_ok = not isinstance(legs, list) or all(
        isinstance(leg, dict) and leg.get("served_provider") and leg.get("served_model")
        for leg in legs
    )
    if payload.get("served_provider") and payload.get("served_model") and legs_ok:
        return SERVED_ATTR_REPORTED
    if (
        generate_model_called(payload)
        or payload.get("served_provider")
        or payload.get("served_model")
    ):
        return SERVED_ATTR_MISSING
    return SERVED_ATTR_NONE


def served_diag_enabled() -> bool:
    """Swap: a prove run turns on ``DMS_SERVED_ATTR_DIAG`` to see which side
    dropped ``served_*``; customer deploys never set it. Read per call."""
    import os

    raw = os.environ.get(SERVED_ATTR_DIAG_ENV, "0")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


#: Origins of SQL DMS compiled itself (multi-grain where-paths, ranking slots).
#: The served SQL is not a model's, whatever the Insights payload carried.
COMPILE_PLAN_ORIGINS = frozenset({PLAN_ORIGIN_ONTOLOGY_COMPILE, PLAN_ORIGIN_ONTOLOGY_RANKING})
#: The model credit copied from an Insights payload onto an envelope.
MODEL_CREDIT_KEYS: tuple[str, ...] = (
    "served_provider",
    "served_model",
    "served_local",
    "ov_key_id",
)


def with_served_attribution(
    env: dict[str, Any] | None, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Stamp ``served_attribution`` on any ask envelope.

    When a generate call ran, its setup fields, ``generate_legs`` and a
    budget-stop ``insights_fail`` are copied onto envelopes that lack them
    (the Cortex contract-ask fallback after a generative miss). The diagnostic
    flag adds the Insights payload's top-level key names and their count,
    never a value.

    An envelope whose ``plan_origin`` names a DMS compile credits no model:
    DMS wrote the served SQL, not the model. ``served_attribution`` is
    ``missing`` when a generate call may have reached a model (nothing it
    wrote was served), else ``none``. Every compile origin, ranking included,
    drops the copied model fields. The route stamp stays compile, and a model
    name on that stamp would claim the model wrote SQL it did not.
    ``generate_legs`` still records the call. Every ask envelope passes here,
    so a later copy of the payload cannot re-credit a compile.
    """
    if not isinstance(env, dict):
        return env
    if isinstance(payload, dict):
        with_setup_fields(env, payload)
        env.setdefault("generate_legs", generate_legs_view(payload))
        stop = insights_budget_stop(payload)
        if stop:
            env.setdefault("insights_fail", stop)
        if served_diag_enabled():
            keys = sorted(str(k) for k in payload)
            env["served_payload_keys"] = {"count": len(keys), "keys": keys}
    env["served_attribution"] = served_attribution(payload)
    origin = str(env.get("plan_origin") or "")
    if origin in COMPILE_PLAN_ORIGINS:
        for key in MODEL_CREDIT_KEYS:
            env.pop(key, None)
        called = generate_model_called(payload)
        env["served_attribution"] = SERVED_ATTR_MISSING if called else SERVED_ATTR_NONE
    return env


def where_paths_for_envelope(paths: Sequence[WherePath]) -> list[dict[str, Any]]:
    """JSON-ready where-paths for the ask envelope. Empty if none."""
    return [
        {
            "grain": p.grain,
            "target": p.target,
            "hops": list(p.hops),
            "steps": list(p.steps),
            "importance": int(p.importance),
            "cardinality": p.cardinality,
        }
        for p in paths
    ]


def _multi_grain_measure(
    question: str,
    onto: Ontology | None,
    payload: dict[str, Any] | None,
) -> str | None:
    """Intent lock first, ranked plan measure second. Not bind_plan."""
    lock = str(intent_slots(question, onto).get("measure") or "").strip()
    if lock:
        return lock
    raw = payload.get("query_plan") if isinstance(payload, dict) else None
    if isinstance(raw, dict):
        ranked = str(raw.get("measure") or "").strip()
        if ranked:
            return ranked
    return None


@dataclass(frozen=True)
class QueryPlan:
    measure: str
    group_by: tuple[tuple[str, str], ...] = ()
    filters: tuple[tuple[str, str, str, Any], ...] = ()
    via: dict[str, str] | None = None
    limit: int | None = 50
    keep_gt: float | None = None
    offset: int | None = None


def ontology_catalog(onto: Ontology) -> dict[str, Any]:
    """Slot vocabulary for Cortex compute. Measure expressions stay off the wire."""
    cols = onto.__dict__.get("_column_cache") or {}
    return {
        "verified": bool(onto.verified),
        "measures": {
            m.name: {"grain": m.grain, "description": m.description}
            for m in onto.measures.values()
        },
        "objects": {o.name: {"key": list(o.key)} for o in onto.objects.values()},
        "links": {
            name: {
                "from": link.from_object,
                "to": link.to_object,
                "cardinality": link.cardinality,
            }
            for name, link in onto.links.items()
        },
        "columns": {str(k): sorted(v) for k, v in cols.items() if isinstance(v, set)},
        "grains": onto.supply_chain_catalog(),
    }


def load_verified_ontology(
    warehouse: Path | None,
    onto: Ontology | None = None,
    *,
    space_id: str | None = None,
) -> Ontology | None:
    """Verify against the lake. Failed subjects stay marked; the rest can answer.

    A caller-declared ontology that fails verify is returned with those
    subjects marked (A2-06). The default demo ontology (``onto is None``)
    still returns None on failure so lakes it was never declared for
    (BIRD, live product) keep today's SQL behaviour.

    A customer Space does not fall back to ``demo_ontology``. The caller
    abstains with a named reason instead of compiling the demo measures.
    ``space_id`` omitted is the demo Space, which is every caller that
    predates the scope gate.

    Does not reseed. A test warehouse must already hold the relations the
    ontology names. ``ensure_demo_warehouse`` would drop them.
    """
    if warehouse is None or not Path(warehouse).is_file():
        return None
    if onto is not None:
        target = onto
        declared = True
    elif onto_from_data_enabled():
        from dms_core.control_plane.onto_data import use_ask_space

        with use_ask_space(space_id):
            target = Ontology.from_store()
        declared = True
    else:
        from dms_executor.demo_grants import is_demo_space

        if not is_demo_space(space_id):
            return None
        target = demo_ontology(Path(warehouse))
        declared = False
    con = connect_serving(Path(warehouse))
    try:
        violations = target.verify(con)
    finally:
        con.close()
    if not violations and target.verified:
        return target
    if not declared:
        return None
    return target


def cached_verify_violations(onto: Ontology) -> list[Violation] | None:
    """Violations left by ``Ontology.verify``, or None if that cache is absent.

    ``verify()`` writes ``self.__dict__["_violations"]`` (same non-field slot
    as ``_column_cache``). Missing or None is fail-closed: the ask must not
    compile or execute. An empty list means verify ran and found nothing.
    """
    if "_violations" not in onto.__dict__:
        return None
    raw = onto.__dict__["_violations"]
    if raw is None:
        return None
    return list(raw)


def declared_ontology_violations(warehouse: Path | None, onto: Ontology) -> list[Violation]:
    """Verify a caller-declared ontology against the lake and keep the evidence.

    Empty when the lake is missing (nothing to measure) or the ontology passed.
    Prefer ``load_verified_ontology`` when the caller also needs the scoped
    ontology. Does not change link cardinality.
    """
    if warehouse is None or not Path(warehouse).is_file():
        return []
    con = connect_serving(Path(warehouse))
    try:
        return list(onto.verify(con))
    finally:
        con.close()


def violation_reason(violations: Sequence[Violation], *, limit: int = 3) -> str:
    """``ontology_unverified: <check> on <subject>: <detail>`` -- the named gap."""
    named = [f"{v.check} on {v.subject}: {v.detail}" for v in violations[:limit]]
    more = len(violations) - len(named)
    if more > 0:
        named.append(f"+{more} more")
    return "ontology_unverified: " + "; ".join(named) if named else "ontology_unverified"


def _canonical_object(onto: Ontology, name: str) -> str | None:
    resolved = onto.resolve_object(name)
    if isinstance(resolved, str):
        return resolved
    return name if name in onto.objects else None


def _declared_paths(onto: Ontology, start: str, target: str) -> list[list[LinkType]]:
    """Acyclic declared paths, ignoring cardinality. Subject-touch only."""
    if start == target:
        return [[]]
    found: list[list[LinkType]] = []
    stack: list[tuple[str, list[LinkType], frozenset[str]]] = [
        (start, [], frozenset({start}))
    ]
    while stack:
        obj, path, seen = stack.pop()
        for link in onto.links.values():
            if link.from_object != obj or link.to_object in seen:
                continue
            nxt = path + [link]
            if link.to_object == target:
                found.append(nxt)
            else:
                stack.append((link.to_object, nxt, seen | {link.to_object}))
    return found


def _plan_dests(plan: QueryPlan, onto: Ontology) -> set[str]:
    names = [obj for obj, _col in plan.group_by]
    names.extend(obj for obj, _col, _op, _val in plan.filters)
    if plan.via:
        names.extend(plan.via)
    dests: set[str] = set()
    for obj in names:
        name = _canonical_object(onto, obj)
        if name:
            dests.add(name)
    return dests


def _plan_used_subjects(
    plan: QueryPlan, onto: Ontology, grain: str
) -> tuple[set[str], set[str]]:
    """Objects and link names the typed plan uses (grain, hops, destinations)."""
    dests = _plan_dests(plan, onto)
    objects = {grain} | dests
    links: set[str] = set()
    for dest in dests:
        if dest == grain:
            continue
        for path in _declared_paths(onto, grain, dest):
            for hop in path:
                links.add(hop.name)
                objects.add(hop.from_object)
                objects.add(hop.to_object)
    return objects, links


def violations_cited_by_plan(
    plan: QueryPlan, onto: Ontology, violations: Sequence[Violation]
) -> list[Violation]:
    """Violations whose failed subject the typed plan uses.

    A failed link is cited whenever the plan's path uses it, whatever the
    grain. A failed object is cited when it is the grain, a hop, or a
    group_by / filter / via destination.
    """
    spec = onto.measures.get(plan.measure)
    if spec is None:
        return []
    objects, links = _plan_used_subjects(plan, onto, spec.grain)
    hit: list[Violation] = []
    for v in violations:
        if v.subject in onto.links:
            if v.subject in links:
                hit.append(v)
            continue
        if v.subject in objects:
            hit.append(v)
    return hit


def _violation_relations(onto: Ontology, v: Violation) -> set[str]:
    """Bare relations a violation's subject (link or object) reads."""
    objs: list[str] = []
    link = onto.links.get(v.subject)
    if link is not None:
        objs = [link.from_object, link.to_object]
    elif v.subject in onto.objects:
        objs = [v.subject]
    out: set[str] = set()
    for name in objs:
        obj = onto.objects.get(name)
        if obj is not None:
            out |= cited_relations(f"SELECT * FROM {obj.relation}")
    return out


def violations_cited_by_sql(
    sql: str, onto: Ontology, violations: Sequence[Violation]
) -> list[Violation]:
    """Violations whose every relation the SQL reads (A2-02).

    Failed link: both of its relations appear. Failed object: its relation
    appears. SQL that never reads those relations is not made wrong by them.
    """
    named = cited_relations(sql)
    hit: list[Violation] = []
    for v in violations:
        rels = _violation_relations(onto, v)
        if rels and rels <= named:
            hit.append(v)
    return hit


def _rows_gt(rows: list[dict[str, Any]], measure: str, keep_gt: float) -> list[dict[str, Any]]:
    """Keep rows whose measure (or sole numeric cell) is above keep_gt."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        val: Any = row.get(measure)
        if val is None:
            nums = [
                v
                for v in row.values()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
            val = nums[0] if len(nums) == 1 else None
        try:
            if val is not None and float(val) > keep_gt:
                out.append(row)
        except (TypeError, ValueError):
            continue
    return out


def query_sql_from_payload(payload: dict[str, Any] | None) -> str | None:
    """Cortex Insights generate SQL only (``query_sql``). Not /dms/query sql_used."""
    if not isinstance(payload, dict):
        return None
    sql = str(payload.get("query_sql") or "").strip()
    return sql or None


def parse_compute_plan(payload: dict[str, Any] | None) -> str:
    """Return miss | unsure | plan | sql. Plan body is payload['query_plan'] when plan."""
    if not isinstance(payload, dict):
        return "miss"
    if payload.get("unsure") is True or payload.get("abstain") is True:
        return "unsure"
    plan = payload.get("query_plan")
    if isinstance(plan, dict) and str(plan.get("measure") or "").strip():
        return "plan"
    if query_sql_from_payload(payload):
        return "sql"
    return "miss"


def plan_from_payload(payload: dict[str, Any]) -> QueryPlan | None:
    raw = payload.get("query_plan")
    if not isinstance(raw, dict):
        return None
    measure = str(raw.get("measure") or "").strip()
    if not measure:
        return None
    group_by = _pairs(raw.get("group_by"))
    filters = _filters(raw.get("filters"))
    if group_by is None or filters is None:
        return None
    via_raw = raw.get("via")
    via = (
        {str(k): str(v) for k, v in via_raw.items()}
        if isinstance(via_raw, dict)
        else None
    )
    limit = raw.get("limit", 50)
    lim = int(limit) if isinstance(limit, int) else 50
    keep_raw = raw.get("keep_gt")
    keep_gt = float(keep_raw) if isinstance(keep_raw, (int, float)) else None
    # Offset 0 is no window; a negative one reaches compile and is refused there.
    off_raw = raw.get("offset")
    offset = off_raw if type(off_raw) is int and off_raw != 0 else None
    return QueryPlan(
        measure=measure,
        group_by=group_by,
        filters=filters,
        via=via,
        limit=lim,
        keep_gt=keep_gt,
        offset=offset,
    )


def _pairs(raw: Any) -> tuple[tuple[str, str], ...] | None:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    out: list[tuple[str, str]] = []
    for item in raw:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            return None
        out.append((str(item[0]), str(item[1])))
    return tuple(out)


def _filters(raw: Any) -> tuple[tuple[str, str, str, Any], ...] | None:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    out: list[tuple[str, str, str, Any]] = []
    for item in raw:
        if not (isinstance(item, (list, tuple)) and len(item) == 4):
            return None
        out.append((str(item[0]), str(item[1]), str(item[2]), item[3]))
    return tuple(out)


def cited_relations(sql: str) -> set[str]:
    return {_relation_bare(n) for n in _sql_cited_labels(sql) if _relation_bare(n)}


def validate_compiled_sql(
    sql: str,
    *,
    grantable: set[str],
    warehouse: Path | None,
    dialect: str | None = None,
) -> str | None:
    """None if the compiled SQL may be submitted. Else a reason (do not execute).

    Grants are one ``serve_gap`` call: statement count, the hostile scanner,
    then the allow-list. A miss returns here, before EXPLAIN. EXPLAIN itself
    runs on the DuckDB serving copy. When the caller omitted the dialect and
    that file is present, the check uses the serving dialect and names a
    missing table (``ungranted:<table>``). An explicit dialect keeps the bare
    code. The warehouse is not passed into ``serve_gap``: a qualifier this
    file does not know stays a grant miss, and the contract ask is what binds
    a qualifier against the catalog.
    """
    note_stage("validate_explain", "started")
    try:
        return _validate_compiled_sql(
            sql, grantable=grantable, warehouse=warehouse, dialect=dialect
        )
    finally:
        note_stage("validate_explain", "done")


def _validate_compiled_sql(
    sql: str,
    *,
    grantable: set[str],
    warehouse: Path | None,
    dialect: str | None = None,
) -> str | None:
    with span("validate_explain"):
        if sql_has_reserved_as_of(sql):
            return RESERVED_PARAM_AS_OF
        cite = dialect is None and warehouse is not None and Path(warehouse).is_file()
        read = dialect if dialect is not None else (SERVING_DIALECT if cite else None)
        gap = serve_gap(
            sql, grantable=grantable, dialect=read, cite_missing=cite
        )
        if gap:
            return gap
        if warehouse is None or not Path(warehouse).is_file():
            return "warehouse_missing"
        # EXPLAIN runs on the pooled serving cursor (read-only, external access
        # off). Refuse anything that is not one SELECT before EXPLAIN runs.
        con = connect_serving(Path(warehouse))
        try:
            gate = _refusal_before_explain(con, sql)
            if gate:
                return gate
            con.execute(f"EXPLAIN {sql}")
        except Exception as exc:  # noqa: BLE001
            return f"explain:{type(exc).__name__}"
        finally:
            con.close()
        return None


REASON_UNTYPED_NUMERIC = "untyped_numeric"


def untyped_numeric_reason(sql: str, warehouse: Path | None) -> str | None:
    """``untyped_numeric:<table>.<col>`` when the SQL reads a declared-numeric text column.

    dms#277 F-e: a column the source declared numeric that could not land typed
    (bare ``money`` with a currency symbol, a value that would not fit) is
    VARCHAR, and text orders ``'9.50'`` above ``'100.25'``. MAX, ORDER BY, a
    comparison or a SUM over it would be a confident wrong figure, so any SQL
    naming it is refused, named, until the column is re-typed at the source.
    A registry that cannot be read refuses too (fail closed).
    """
    if warehouse is None or not Path(warehouse).is_file():
        return None
    labels = _sql_cited_labels(sql) or []
    read: set[str] = set()
    for label in dict.fromkeys(str(x).strip().lower() for x in labels):
        schema, _, bare = label.rpartition(".")
        if schema == "bronze" or (not schema and bare not in DEMO_TABLES):
            read.add(f"bronze.{bare}")
    if not read:
        return None
    from dms_executor.bronze import untyped_numeric_columns

    try:
        flagged = untyped_numeric_columns(read, path=Path(warehouse))
    except Exception:  # noqa: BLE001 - cannot vouch for the columns: refuse
        return f"{REASON_UNTYPED_NUMERIC}:registry_unreadable"
    if not flagged:
        return None
    try:
        named = {
            c.name.lower()
            for root in sqlglot.parse(sql, read=SERVING_DIALECT)
            if root is not None
            for c in root.find_all(exp.Column)
        }
    except Exception:  # noqa: BLE001
        return f"{REASON_UNTYPED_NUMERIC}:unparsed"
    hits = sorted(f"{t}.{c}" for t, cols in flagged.items() for c in cols if c in named)
    if not hits:
        return None
    return f"{REASON_UNTYPED_NUMERIC}:{','.join(hits)}"


def _refusal_before_explain(con: Any, sql: str) -> str | None:
    """None when ``sql`` is one SELECT. Otherwise a refusal, and nothing ran."""
    import duckdb

    try:
        stmts = con.extract_statements(sql)
    except Exception as exc:  # noqa: BLE001 - a syntax error, retryable as on main
        return f"explain:{type(exc).__name__}"
    if len(stmts) != 1:
        return "multi_statement"
    if stmts[0].type != duckdb.StatementType.SELECT:
        return "statement_not_allowed"
    return None


def _explain_error_code(sql: str, warehouse: Path | None) -> str | None:
    """Typed code for an EXPLAIN error: ``Class`` or ``Class:name``.

    Never the DuckDB message. A binder error prints an ENUM column's member
    list, and a PIVOT binds on its column's distinct values, so the message
    can carry cell values. A name, when given, is one the SQL wrote.
    """
    if warehouse is None or not Path(warehouse).is_file():
        return None
    con = connect_serving(Path(warehouse))
    try:
        con.execute(f"EXPLAIN {sql}")
    except Exception as exc:  # noqa: BLE001
        return typed_db_error(exc, written_names(con, sql))
    finally:
        con.close()
    return None


def _as_of() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grant_ticket_reason(
    reason: str, sql: str | None, grantable: set[str], dialect: str | None
) -> str | None:
    """Ticket text that still names the relation. The visible code stays bare.

    ``ungranted:`` is written once. A second copy would make the scrub read
    the word ``ungranted`` as a table and cut it out of ``checker:ungranted``.
    """
    if not isinstance(sql, str) or not sql.strip():
        return None
    heads = [part.strip() for part in str(reason or "").split(":") if part.strip()]
    if not heads:
        return None
    if "ungranted" not in heads and "sql_relation_not_granted" not in heads:
        return None
    cited = _cited_ungranted(sql, grantable, dialect)
    if not cited or ":" not in cited:
        return None
    tail = cited.split(":", 1)[1]
    if not tail or tail in heads:
        return None
    if "ungranted" in heads:
        return f"{reason}:{tail}"
    return f"{reason}:{cited}"


def _cited_ungranted(sql: str, grantable: set[str], dialect: str | None) -> str | None:
    """Relation tail for the ticket. The visible code stays bare."""
    named = serve_gap(sql, grantable=set(grantable), dialect=dialect, cite_missing=True)
    if (
        isinstance(named, str)
        and named.startswith("ungranted:")
        and named not in {"ungranted:file", "ungranted:unparsed"}
    ):
        return named
    return None


def _sql_route(payload: dict[str, Any] | None) -> str:
    """Model SQL is the model route only when the producer named the model.

    No ``served_model`` is missing attribution (SERVED-ATTR-01): no route is
    stamped, so the envelope stays unattributed. It is never a rule path.
    """
    raw = payload.get("served_model") if isinstance(payload, dict) else None
    name = str(raw).strip() if isinstance(raw, str) else ""
    return ROUTE_MODEL if name else ""


def _abstain(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
    plan_source: str = PLAN_SOURCE_OTHER,
    notes: Sequence[str] = (),
    sql: str | None = None,
    retries: int = 0,
    stage: str = "generative",
    ticket_reason: str | None = None,
    named: str | None = None,
) -> dict[str, Any]:
    from dms_executor.ask_ladder import ask_ladder_enabled, current_climb

    session = current_climb()
    if (
        session is not None
        and not session.climbing
        and stage in {"generative", "ai_call"}
    ):
        served = session.on_abstain(reason, sql, stage)
        if isinstance(served, dict) and served.get("abstained") is not True:
            return served
        code = session.exhausted_reason(reason)
        session.finalizing = True
        session.climbing = True
        try:
            return _abstain(
                question,
                code,
                space_id=space_id,
                session_id=session_id,
                plan_source=plan_source,
                notes=(*notes, *session.notes()),
                sql=sql,
                retries=retries,
                stage=stage,
                ticket_reason=code,
                named=code,
            )
        finally:
            session.climbing = False
            session.finalizing = False
    log_ticket = not (
        session is not None and session.climbing and not session.finalizing
    )
    env = build_abstain(
        reason=reason,
        ticket_reason=ticket_reason,
        abstain_reason=named,
        log_ticket=log_ticket,
        question=question,
        sql=sql,
        retries=retries,
        stage=stage,
        answer_id="ans_gen01_abstain",
        text=customer_abstain_text(reason),
        rows=[],
        sql_used=None,
        assumptions=[f"GEN-01: {reason}", *[n for n in notes if str(n).strip()]],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    if named and log_ticket and ask_ladder_enabled() and not cloop_b_enabled():
        try:
            from dms_executor.pipeline_failure import log_pipeline_failure_ticket

            ticket_id = log_pipeline_failure_ticket(
                reason=ticket_reason or named,
                question=question,
                sql=sql,
                retries=retries,
                stage=stage,
                ask_id=str(env.get("answer_id") or ""),
            )
            if isinstance(ticket_id, str) and ticket_id:
                env["ticket_id"] = ticket_id
        except Exception:
            pass
    note_stage(stage, "done")
    _offer_rowset_sql(env, question, reason, sql)
    if isinstance(env.get("reconfirm"), dict):
        note_stage("reconfirm", "done")
    assert_envelope_valid(env)
    # build_abstain already wrote abstain:<stage>. Successes stamp their own.
    return with_plan_source(env, plan_source)


def _l2_envelope(
    *,
    sql: str,
    result: Any,
    question: str,
    space_id: str | None,
    session_id: str | None,
    audit_id: str,
    notes: Sequence[str],
    plan_source: str = PLAN_SOURCE_OTHER,
    coverage: Coverage | None = None,
    where_paths: Sequence[WherePath] = (),
    plan_origin: str = "",
    lead: str = "",
    served_route: str = "",
) -> dict[str, Any]:
    out_rows = rows_from_submit_result(result)
    text = f"{lead}\n" if lead else ""
    text += f"Found {len(out_rows)} row(s)."
    if out_rows:
        text += "\n" + "\n".join(
            "  - " + ", ".join(f"{k}={v}" for k, v in row.items()) for row in out_rows[:12]
        )
    # Same row-based builder as the Cortex contract path when Cortex omits chart.
    chart = chart_from_rows(out_rows)
    env = build_answer_envelope(
        answer_id="ans_gen01",
        text=text,
        badge="L2_VALIDATED",
        abstained=False,
        rows=out_rows,
        sql_used=sql,
        chart=chart,
        assumptions=[
            "GEN-01 ontology compile",
            "executed via Cortex submit after validate",
            *notes,
        ],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
        question=question,
        audit_id=audit_id,
        grounded_tables=sorted(cited_relations(sql)),
    )
    if env.get("abstained"):
        assert_envelope_valid(env)
        env = with_plan_origin(with_plan_source(env, plan_source), plan_origin)
        if served_route:
            stamp_route(env, served_route)
        return env
    if not coverage_valid(coverage) or coverage is None:
        return _abstain(
            question,
            "coverage_invalid: numeric answer missing include/exclude/unsure",
            space_id=space_id,
            session_id=session_id,
            plan_source=plan_source,
        )
    stamped = coverage
    env["coverage"] = stamped.as_dict()
    env["assumptions"] = [
        *list(env.get("assumptions") or []),
        *stamped.assumption_lines(),
    ]
    if os.environ.get("DMS_FK_ORPHAN_DISCLOSE", "0").strip() == "1":
        from dms_executor.join_repair import stamp_orphan_exclusion

        stamp_orphan_exclusion(env)
    assert_envelope_valid(env)
    env = with_plan_origin(with_plan_source(env, plan_source), plan_origin)
    if where_paths:
        env["where_paths"] = where_paths_for_envelope(where_paths)
    # Shadow only. A check failure is recorded and the serve above stands.
    try:
        from dms_executor.sql_grounds import served_check_shadow

        env["served_check_shadow"] = served_check_shadow(sql)
    except Exception as exc:  # noqa: BLE001 — shadow never changes the serve
        version = ""
        try:
            from dms_executor.sql_grounds import CHECKER_VERSION

            version = CHECKER_VERSION
        except Exception:
            version = ""
        env["served_check_shadow"] = {
            "checker_version": version,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if served_route:
        stamp_route(env, served_route)
    return env


def validated_sql_reason(
    sql: str,
    *,
    question: str,
    warehouse: Path | None,
    grantable: set[str] | None,
    space_onto: Ontology | None = None,
    declared_violations: Sequence[Violation] | None = None,
    columns_of: Mapping[str, set[str]] | None = None,
    dialect: str | None = None,
) -> str | None:
    """Pre-execute gates shared with ``_submit_validated``.

    Qualifier, a stated value, currency, reserved ``$as_of``, and an untyped
    numeric. None means those gates passed. Coverage and the compile-only
    hold stay with the caller.

    A join repair passes ``space_onto`` so this same call also re-runs the
    join rule and ``validate_compiled_sql``. Callers that omit it keep the
    previous checks only.
    """
    if os.environ.get("DMS_QUALIFIER_AI", "0").strip() == "1":
        from dms_executor.qualifier_ai import deciding_qualifier_reason

        gap = deciding_qualifier_reason(question, sql, warehouse, grantable)
    else:
        gap = unhonored_qualifier_reason(question, sql=sql) or dropped_stated_value_reason(
            question, sql, warehouse, grantable=grantable
        )
    if gap:
        return gap
    ccy_why = currency_mismatch_reason(question, sql, warehouse=warehouse)
    if ccy_why:
        return ccy_why
    if sql_has_reserved_as_of(sql):
        return RESERVED_PARAM_AS_OF
    numeric = untyped_numeric_reason(sql, warehouse)
    if numeric:
        return numeric
    if space_onto is None or columns_of is None:
        return None
    join_why = unverified_join_reason(
        sql, space_onto, list(declared_violations or ()), columns_of=columns_of
    )
    if join_why:
        return join_why
    if grantable is None:
        return None
    return validate_compiled_sql(
        sql, grantable=set(grantable), warehouse=warehouse, dialect=dialect
    )


def _submit_validated(
    sql: str,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[[str], Any],
    ledger_append: Callable[[dict[str, Any]], Any],
    notes: Sequence[str],
    plan_source: str,
    keep_gt: float | None = None,
    measure: str | None = None,
    coverage: Coverage | None = None,
    where_paths: Sequence[WherePath] = (),
    warehouse: Path | None = None,
    plan_origin: str = "",
    lead: str = "",
    served_route: str = "",
    grantable: set[str] | None = None,
    hint: str = "",
    qualifier_notes: tuple[str, ...] = (),
) -> dict[str, Any]:
    def _sql_checks(statement: str) -> dict[str, Any] | None:
        """Qualifier, currency, and numeric checks. None means the SQL may be served."""
        if not coverage_valid(coverage):
            return _abstain(
                question,
                "coverage_invalid: numeric answer missing include/exclude/unsure",
                space_id=space_id,
                session_id=session_id,
                plan_source=plan_source,
                notes=notes,
            )
        blocked = validated_sql_reason(
            statement, question=question, warehouse=warehouse, grantable=grantable
        )
        if blocked == RESERVED_PARAM_AS_OF:
            return reserved_as_of_abstain(
                space_id=space_id,
                session_id=session_id,
                route="generated",
                question=question,
            )
        if blocked:
            return _abstain(
                question,
                blocked,
                space_id=space_id,
                session_id=session_id,
                plan_source=plan_source,
                notes=() if str(blocked).startswith("currency_mismatch") else notes,
            )
        return None

    stopped = _sql_checks(sql)
    if stopped is not None:
        return stopped
    from dms_executor.ai_first import (
        NO_MODEL_SQL,
        ai_first_enabled,
        compile_example,
        note_example,
    )

    if ai_first_enabled() and served_route in {ROUTE_COMPILE, ROUTE_LADDER}:
        note_example(
            compile_example(
                sql, question=question, space_id=space_id, route=served_route
            )
        )
        return _abstain(
            question,
            NO_MODEL_SQL,
            space_id=space_id,
            session_id=session_id,
            plan_source=plan_source,
            notes=notes,
        )
    if served_route == ROUTE_COMPILE and _scale_miss_on():
        from dms_executor.scale_miss import compile_override

        replacement = compile_override(
            sql,
            question,
            grantable=grantable,
            warehouse=warehouse,
        )
        if replacement:
            sql = replacement
            served_route = ROUTE_MODEL
            plan_origin = PLAN_ORIGIN_GENERATE_SQL
            notes = (*tuple(notes), "scale_miss:ai_over_compile")
            # Same checks as the first SQL. A failed replacement is not served.
            stopped = _sql_checks(sql)
            if stopped is not None:
                return stopped
    if os.environ.get("DMS_QUALIFIER_AI", "0").strip() == "1":
        from dms_executor.qualifier_ai import review_candidate

        reviewed = review_candidate(
            question,
            sql,
            hint=hint,
            warehouse=warehouse,
            grantable=grantable,
            space_id=space_id,
            session_id=session_id,
            served_route=served_route,
            extra_notes=qualifier_notes,
        )
        if reviewed["action"] == "reconfirm" and isinstance(reviewed.get("envelope"), dict):
            return reviewed["envelope"]
        if reviewed["action"] == "abstain":
            return _abstain(
                question,
                str(reviewed.get("reason") or "qualifier_ai"),
                space_id=space_id,
                session_id=session_id,
                plan_source=plan_source,
                notes=notes,
                sql=sql,
            )
        rewritten = reviewed.get("sql")
        if reviewed.get("repaired") and isinstance(rewritten, str) and rewritten.strip():
            sql = rewritten.strip()
            if served_route in {ROUTE_COMPILE, ROUTE_LADDER}:
                served_route = ROUTE_MODEL
                if not plan_origin:
                    plan_origin = PLAN_ORIGIN_GENERATE_SQL
            stopped = _sql_checks(sql)
            if stopped is not None:
                return stopped
    try:
        with span("submit_execute"):
            result = submit(sql)
    except OpenVaultTokenError:
        raise
    except Exception:  # noqa: BLE001
        return _abstain(
            question, "submit_failed",
            space_id=space_id, session_id=session_id, plan_source=plan_source,
        )
    if getattr(result, "ok", None) is False or getattr(result, "output", None) is None:
        return _abstain(
            question, "submit_had_no_rows",
            space_id=space_id, session_id=session_id, plan_source=plan_source,
        )
    if not rows_from_submit_result(result):
        from dms_executor.ask_ladder import current_climb

        # Empty is not a certified figure. The ladder feeds it back. Flag off
        # (no session) still returns the zero-row envelope below.
        if current_climb() is not None:
            return _abstain(
                question,
                EMPTY_UNVERIFIED,
                space_id=space_id,
                session_id=session_id,
                plan_source=plan_source,
                notes=notes,
            )
    if keep_gt is not None:
        kept = _rows_gt(rows_from_submit_result(result), str(measure or ""), keep_gt)
        if not kept:
            return _abstain(
                question, "validate:keep_gt_empty",
                space_id=space_id, session_id=session_id, plan_source=plan_source,
            )
        result = SimpleNamespace(
            ok=True,
            status=getattr(result, "status", "ok"),
            run_id=getattr(result, "run_id", "") or "",
            output={"rows": kept},
        )
    run_id = str(getattr(result, "run_id", None) or "")
    try:
        led = ledger_append({"sql": sql, "run_id": run_id})
    except Exception:  # noqa: BLE001
        return _abstain(
            question, "ledger_append_failed",
            space_id=space_id, session_id=session_id, plan_source=plan_source,
        )
    entry_id = getattr(led, "entry_id", None) if led is not None else None
    led_hash = getattr(led, "hash", None)
    if not (isinstance(entry_id, str) and entry_id.strip()):
        return _abstain(
            question, "ledger_entry_missing",
            space_id=space_id, session_id=session_id, plan_source=plan_source,
        )
    if not (isinstance(led_hash, str) and led_hash.strip()) or led_hash == entry_id:
        return _abstain(
            question, "ledger_hash_missing",
            space_id=space_id, session_id=session_id, plan_source=plan_source,
        )
    return _l2_envelope(
        sql=sql,
        result=result,
        question=question,
        space_id=space_id,
        session_id=session_id,
        audit_id=entry_id.strip(),
        notes=notes,
        plan_source=plan_source,
        coverage=coverage,
        where_paths=where_paths,
        plan_origin=plan_origin,
        lead=lead,
        served_route=served_route,
    )


def ontology_plan_from_ranking(
    question: str,
    payload: dict[str, Any] | None,
    *,
    onto: Ontology | None,
    ctx: dict[str, Any],
) -> dict[str, Any] | None:
    """Cortex ranked metric + retrieve slots. ontology_plan, not bind_plan."""
    if not isinstance(payload, dict) or onto is None or not onto.measures:
        return None
    prefer = intent_slots(question, onto).get("measure")
    prefer_s = str(prefer).strip() if prefer else ""
    ranked = query_plan_from_insights_ranking(
        payload,
        set(onto.measures),
        aliases=load_measure_aliases(),
        specs={name: (m.description or "") for name, m in onto.measures.items()},
        prefer=prefer_s or None,
        question=question,
    )
    if ranked is None:
        return None
    raw = ranked.get("query_plan")
    if not isinstance(raw, dict):
        return None
    measure = str(raw.get("measure") or "").strip()
    if not measure or measure not in onto.measures:
        return None
    spec = onto.measures[measure]
    ctx.setdefault("measures", {})[measure] = {
        "grain": spec.grain,
        "description": spec.description,
    }
    ranked_id = str(raw.get("ranked_id") or "").strip() or None
    return slots_for_measure(question, ctx, measure, ranked_id=ranked_id)


def path_miss_envelope(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
    sql: str | None = None,
) -> dict[str, Any]:
    """Isolated A/B miss: ABSTAIN, do not mix Cortex/pack into the other lane."""
    return _abstain(
        question,
        reason,
        space_id=space_id,
        session_id=session_id,
        plan_source=PLAN_SOURCE_OTHER,
        sql=sql,
    )


def _offer_rowset_sql(
    env: dict[str, Any], question: str, reason: str, sql: str | None
) -> None:
    """One-table set operation: show the generated SQL and ask back.

    The badge stays the abstain envelope every reconfirm card uses. The SQL
    is the card definition, so the customer sees the statement. A fan-out
    join does not come here.
    """
    statement = str(sql or "").strip()
    if not statement or "rowset_reconfirm" not in str(reason or ""):
        return
    block = env.get("reconfirm")
    if not isinstance(block, dict):
        block = {}
    block["definition"] = statement
    block["reason_code"] = str(reason)
    block["why"] = str(reason)
    block["suggested_question"] = question
    block["closest_question"] = question
    env["reconfirm"] = block


def _rules_off() -> bool:
    """Digit 1 only. The ontology compiler stays when the flag is off."""
    return os.environ.get("DMS_RULES_OFF", "0").strip() == "1"


def _scale_miss_on() -> bool:
    from dms_executor.scale_miss import enabled

    return enabled()


def _scale_turn_ready() -> bool:
    from dms_executor.scale_miss import turn_ready

    return turn_ready()


def _scale_ladder_reserve() -> bool:
    from dms_executor.scale_miss import ladder_reserves

    return ladder_reserves()


def _rules_off_hold(
    question: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """No model SQL, and the question-word compiler must not serve.

    The stub has nothing to execute for the model. Reconfirm, do not submit.
    """
    env = _abstain(
        question,
        "rules_off",
        space_id=space_id,
        session_id=session_id,
        plan_source=PLAN_SOURCE_ONTOLOGY,
    )
    env["reconfirm"] = {
        "why": "rules_off",
        "reason_code": "rules_off",
        "suggested_question": question,
        "closest_question": question,
    }
    return env


def _try_multi_grain_envelope(
    q: str,
    *,
    onto: Ontology | None,
    payload: dict[str, Any] | None,
    allowed: set[str],
    lake: Path | None,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[[str], Any],
    ledger_append: Callable[[dict[str, Any]], Any],
    dialect: str | None = None,
    space_onto: Ontology | None = None,
    declared: Ontology | None = None,
    declared_violations: Sequence[Violation] | None = None,
    grounded: Sequence[GroundedDef] | None = None,
    embedder: Any = None,
) -> dict[str, Any] | _CompileRefused | None:
    """≥2 grains: ranked where-paths + importance, or named ABSTAIN.

    Runs before one-grain GEN-01 plan/SQL. bind_plan is not this path.
    None means this ask is not a multi-grain compile (caller continues).
    ``DMS_RULES_OFF`` does not compile this lane.
    """
    if _rules_off():
        return None
    honour_block = _compile_blocked()
    if honour_block is not None:
        return honour_block
    lock = _multi_grain_measure(q, onto, payload)
    multi = try_compile_multi_grain(
        onto,
        lock or None,
        q,
        grantable=allowed,
        dialect=dialect,
        space_id=space_id,
        session_id=session_id,
    )
    if multi is None:
        return None
    if isinstance(multi, dict):
        return multi
    source = PLAN_SOURCE_ONTOLOGY
    if isinstance(multi, Refusal):
        return _abstain(
            q, f"{multi.reason}: {multi.detail}",
            space_id=space_id, session_id=session_id, plan_source=source,
        )
    if multi.existential:
        return _abstain(
            q, "existential many-to-many filter: ask path will not choose a reading",
            space_id=space_id, session_id=session_id, plan_source=source,
        )
    if sql_has_reserved_as_of(multi.sql):
        return reserved_as_of_abstain(
            space_id=space_id,
            session_id=session_id,
            route="generated",
            question=q,
        )
    why = validate_compiled_sql(
        multi.sql, grantable=allowed, warehouse=lake, dialect=dialect
    )
    # Not one SELECT: refused first, before any other branch reads `why`.
    if why in {"multi_statement", "statement_not_allowed", "sql_not_analyzable"}:
        return _abstain(
            q, why,
            space_id=space_id, session_id=session_id, plan_source=source,
            sql=multi.sql,
        )
    if why == RESERVED_PARAM_AS_OF:
        return reserved_as_of_abstain(
            space_id=space_id,
            session_id=session_id,
            route="generated",
            question=q,
        )
    if why:
        from dms_executor.grain_ai import grain_ai_enabled

        if grain_ai_enabled():
            used = tuple(path.grain for path in multi.where_paths)
        else:
            used = detect_supply_chain_grains(q)
        gap = missing_join_for_ungranted(why, used)
        return _abstain(
            q, gap or f"validate:{why}",
            space_id=space_id, session_id=session_id, plan_source=source,
        )
    from dms_executor.ai_first import ai_first_enabled, compile_example, note_example

    ungrounded = _ungrounded_measure(q, multi.measure, onto, embedder=embedder)
    if ungrounded:
        # Do not serve it, and do not hand it to the model as an example.
        if ai_first_enabled():
            return None
        held = reconfirm_for_reason(
            ungrounded, question=q, space_id=space_id, session_id=session_id
        )
        if held is not None:
            return held
        return _abstain(
            q, ungrounded,
            space_id=space_id, session_id=session_id, plan_source=source,
        )
    blocked = ai_lane_sql_check(
        multi.sql,
        question=q,
        grantable=allowed,
        warehouse=lake,
        dialect=dialect,
        space_id=space_id,
        session_id=session_id,
        space_onto=space_onto,
        declared=declared,
        declared_violations=list(declared_violations or []),
        grounded=grounded,
    )
    if blocked:
        # Direct refusals stay abstains. Any other checker miss is not a
        # serve: the caller hands the question to the AI ladder.
        if _checker_is_direct(blocked):
            return _abstain(
                q,
                blocked,
                space_id=space_id,
                session_id=session_id,
                plan_source=source,
                sql=multi.sql,
            )
        return _CompileRefused(reason=blocked, sql=multi.sql)
    if ai_first_enabled():
        note_example(
            compile_example(
                multi.sql, question=q, space_id=space_id, route=ROUTE_COMPILE
            )
        )
        return None
    return _commit_compile(
        multi.sql,
        question=q,
        onto=onto,
        dialect=dialect,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        notes=tuple([*multi.notes, "ontology_compile:where+importance"]),
        plan_source=source,
        measure=multi.measure,
        coverage=multi.coverage,
        where_paths=multi.where_paths,
        warehouse=lake,
        served_route=ROUTE_COMPILE,
        grantable=allowed,
    )


def rank_window_ask(
    q: str,
    *,
    onto: Ontology | None,
    allowed: set[str],
    lake: Path | None,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[[str], Any],
    ledger_append: Callable[[dict[str, Any]], Any],
    dialect: str | None = None,
    space_onto: Ontology | None = None,
    declared: Ontology | None = None,
    declared_violations: Sequence[Violation] | None = None,
    grounded: Sequence[GroundedDef] | None = None,
) -> dict[str, Any] | _CompileRefused | None:
    """RANK-WINDOW-01: "excluding top 3, next 5 SKUs" without a generate call.

    Grammar parse + verified ontology compile, then the shared Cortex submit
    and ledger path. Does not call bind_plan, intent_slots,
    ontology_plan_from_ranking or slots_for_measure. None only when the ask
    is not a rank window and has no stray digit outside a top/next/ranks
    span, or it is a clean window but no verified ontology is loaded. A
    window with a filter, a second noun, a leftover number, no limit or no
    measure word abstains here, so it never reaches Insights. plan_source is
    ``other``: neither Cortex nor keyword bind produced this plan.
    """

    def _no(reason: str) -> dict[str, Any]:
        return _abstain(
            q, reason, space_id=space_id, session_id=session_id,
            plan_source=PLAN_SOURCE_OTHER, notes=(NOTE_RANK_WINDOW,),
        )

    # Shape covers offset windows and a top/next/ranks phrase whose only
    # problem is a digit outside that phrase. Abstain before Insights either way.
    win = parse_rank_window(q)
    shape = rank_window_shape_reason(q)
    if shape:
        if _qualifier_note_only(shape):
            return None
        return _no(shape)
    if win is None:
        return None
    if onto is None or not onto.verified:
        return None
    pair = rank_window_group(win)
    if pair is None:
        if _qualifier_note_only("rank_window_unhandled_terms:no_entity"):
            return None
        return _no("rank_window_unhandled_terms:no_entity")
    specs = {name: m.description or "" for name, m in onto.measures.items()}
    measure, why, reading = rank_window_measure(q, specs)
    if why or measure is None:
        if why and _qualifier_note_only(why):
            return None
        return _no(why or "unknown_measure")
    if win.limit is None:
        if _qualifier_note_only("rank_window_open_ended"):
            return None
        return _no("rank_window_open_ended")
    honour_block = _compile_blocked()
    if honour_block is not None:
        return honour_block
    compiled = onto.compile(measure, group_by=[pair], limit=win.limit, offset=win.offset)
    if isinstance(compiled, Refusal):
        return _no(f"{compiled.reason}: {compiled.detail}")
    bad = validate_compiled_sql(
        compiled.sql, grantable=allowed, warehouse=lake, dialect=dialect
    )
    if bad in {"multi_statement", "statement_not_allowed", "sql_not_analyzable"}:
        return _no(bad)
    if bad:
        return _no(f"validate:{bad}")
    noun = _ENTITY_NOUN.get(str(win.entity), str(win.entity))
    lead = (
        f"Ranks {win.label} of {noun}; ranks 1-{win.offset} are excluded. "
        f"{reading[:1].upper()}{reading[1:]}."
    )
    blocked = ai_lane_sql_check(
        compiled.sql,
        question=q,
        grantable=allowed,
        warehouse=lake,
        dialect=dialect,
        space_id=space_id,
        session_id=session_id,
        space_onto=space_onto,
        declared=declared,
        declared_violations=list(declared_violations or []),
        grounded=grounded,
    )
    if blocked:
        if _checker_is_direct(blocked):
            return _no(blocked)
        # Not a serve. The caller continues into the AI pipeline.
        return _CompileRefused(reason=blocked, sql=compiled.sql)
    from dms_executor.ai_first import ai_first_enabled, compile_example, note_example

    if ai_first_enabled():
        note_example(
            compile_example(
                compiled.sql, question=q, space_id=space_id, route=ROUTE_LADDER
            )
        )
        return None
    return _commit_compile(
        compiled.sql,
        question=q,
        onto=onto,
        dialect=dialect,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        notes=(*compiled.notes, NOTE_RANK_WINDOW, f"measure: {reading}"),
        plan_source=PLAN_SOURCE_OTHER,
        measure=measure,
        coverage=compiled.coverage,
        warehouse=lake,
        grantable=allowed,
        lead=lead,
        served_route=ROUTE_LADDER,
    )


def _ungrounded_measure(
    question: str,
    measure: str | None,
    onto: Ontology | None,
    *,
    embedder: Any = None,
) -> str | None:
    """Gap when a compile would serve a measure the question does not name.

    None when there is no measure to judge, or the question grounds it in
    the ontology or the columns the measure expression reads. The caller
    abstains. Reconfirm offers Yes. This is not a phrase list.

    ``DMS_NO_WORD_RULES`` on: only the unique ranked measure is grounded.
    A tie, a score under the floor, or a different measure reconfirms.
    """
    name = str(measure or "").strip()
    # A name the ontology does not declare is unknown_measure, owned by compile.
    if not name or onto is None or name not in onto.measures:
        return None
    if no_word_rules_enabled():
        from dms_executor.measure_link import (
            REASON_MISMATCH,
            rank_measure_link,
        )

        link = rank_measure_link(question, onto, embedder=embedder)
        if link.reason_code:
            return link.reason_code
        if link.name and link.name != name:
            return REASON_MISMATCH
        if link.name == name:
            return None
    if question_grounds_measure(question, name, onto):
        return None
    return f"ungrounded_measure:{name}"


def _compile_maybe_unverified(onto: Ontology, plan: QueryPlan) -> CompiledQuery | Refusal:
    """Compile a plan that does not touch a failed subject (A2-06).

    ``Ontology.compile`` still blanket-refuses when ``verified`` is False.
    A shallow copy lifts only that flag so the caller's ontology is not
    mutated. Shared link objects keep the cardinality ``verify()`` set;
    failed links stay unverified and compile will not join through them.
    """
    scoped = copy.copy(onto)
    scoped.verified = True
    return scoped.compile(
        plan.measure,
        group_by=plan.group_by,
        filters=plan.filters,
        via=plan.via,
        limit=plan.limit,
        offset=plan.offset,
    )


_SPACE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _space_table_ok(name: str) -> bool:
    parts = str(name).split(".")
    return len(parts) in (1, 2) and all(_SPACE_IDENT.fullmatch(p) for p in parts)


def _space_insights_body(
    allowed: set[str], space: dict[str, Any], schema_context: str | None = None
) -> dict[str, Any]:
    """Insights body for a Space that has its own ontology.

    ``source`` is ``space``. Objects, verified links and declared measures come
    from the stored ontology. Demo retrieve context is not mixed in. The
    granted-schema prompt (``DMS_SCHEMA_CONTEXT``) is carried when one was built,
    and counted in the budget below.
    """
    tables = sorted(t for t in allowed if _space_table_ok(t))
    out: dict[str, Any] = {
        "source": "space",
        "tables": tables,
        "schema": [{"table": t} for t in tables],
    }
    if schema_context:
        out[SCHEMA_CONTEXT_FIELD] = schema_context
    # Budgeted inside Cortex's caller-ontology limits. A cut is marked.
    out.update(budget_space_block(space, used=len(json.dumps(out, default=str))))
    return out


@dataclass(frozen=True)
class _CompileRefused:
    """Compile SQL failed the AI-lane checker. Not an envelope and not a serve."""

    reason: str
    sql: str


_DIRECT_CHECK_HEADS = frozenset(
    {
        "multi_statement",
        "statement_not_allowed",
        "sql_not_analyzable",
        "hostile_sql",
        "ungranted",
        "sql_relation_not_granted",
        "pii",
        "reserved_param",
    }
)


def _checker_is_direct(reason: str) -> bool:
    """Ungranted, hostile, and non-SELECT stay abstains. Everything else can climb."""
    head = str(reason or "").split(":", 1)[0]
    return head in _DIRECT_CHECK_HEADS or str(reason or "").startswith("hostile_sql")


_HONOUR_REASONS = frozenset(
    {"compile_not_honoured", "shape_guard", "compile_honour_unknown"}
)
_HONOUR_BLOCK: ContextVar[_CompileRefused | None] = ContextVar(
    "dms_compile_honour_block", default=None
)
_DESTRUCTIVE: ContextVar[tuple[str, str] | None] = ContextVar(
    "dms_compile_honour_destructive", default=None
)


def _honour_enabled() -> bool:
    from dms_executor.compile_honour import compile_honour_enabled

    return compile_honour_enabled()


def _reset_honour_state() -> tuple[Any, Any]:
    return (_HONOUR_BLOCK.set(None), _DESTRUCTIVE.set(None))


def _release_honour_state(tokens: tuple[Any, Any]) -> None:
    _HONOUR_BLOCK.reset(tokens[0])
    _DESTRUCTIVE.reset(tokens[1])


def _note_honour_block(refused: _CompileRefused) -> None:
    _HONOUR_BLOCK.set(refused)


def _compile_blocked() -> _CompileRefused | None:
    if not _honour_enabled():
        return None
    return _HONOUR_BLOCK.get()


def _destructive_status(question: str) -> str:
    """off, safe, hostile, or unknown. Flag off does not call the model."""
    if not _honour_enabled():
        return "off"
    cached = _DESTRUCTIVE.get()
    if cached is not None and cached[0] == question:
        return cached[1]
    from dms_executor.compile_honour import ast_non_select, classify_destructive

    ast_hit = ast_non_select(question)
    verdict = classify_destructive(question)["verdict"]
    if ast_hit or verdict == "destructive":
        status = "hostile"
    elif verdict == "safe":
        status = "safe"
    else:
        status = "unknown"
    _DESTRUCTIVE.set((question, status))
    return status


def _refuse_destructive(
    question: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """Hostile abstain, or None.

    Not the compliance gate and not ``question_is_destructive``. Those stay
    where they are. This only abstains when the model says destructive or
    the text is a typed non-SELECT, and it does that before compile.
    Unknown is not a destructive verdict. The ask continues, and a compile
    serve still has to pass the honour check.
    """
    if _destructive_status(question) != "hostile":
        return None
    return _abstain(
        question,
        "hostile_sql",
        space_id=space_id,
        session_id=session_id,
        plan_source=PLAN_SOURCE_OTHER,
    )


def _measure_facts(onto: Ontology | None, name: str | None) -> dict[str, str] | None:
    measure_name = str(name or "").strip()
    if onto is None or not measure_name or measure_name not in onto.measures:
        return None
    spec = onto.measures[measure_name]
    return {
        "name": measure_name,
        "grain": str(getattr(spec, "grain", "") or ""),
        "expression": str(getattr(spec, "expression", "") or ""),
        "description": str(getattr(spec, "description", "") or ""),
    }


def _commit_compile(
    sql: str,
    *,
    question: str,
    onto: Ontology | None,
    dialect: str | None,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[[str], Any],
    ledger_append: Callable[[dict[str, Any]], Any],
    notes: Sequence[str],
    plan_source: str,
    measure: str | None = None,
    coverage: Coverage | None = None,
    where_paths: Sequence[WherePath] = (),
    warehouse: Path | None = None,
    plan_origin: str = "",
    lead: str = "",
    served_route: str = "",
    grantable: set[str] | None = None,
    keep_gt: float | None = None,
    qualifier_notes: tuple[str, ...] = (),
) -> dict[str, Any] | _CompileRefused:
    """Serve compiled SQL. Flag on: honour check, then the shape guard.

    A miss is not an abstain. The caller hands the question to the AI leg.
    Flag off is ``_submit_validated`` with the same arguments.
    """
    if _honour_enabled():
        from dms_executor.compile_honour import check_honour, shape_guard_hit

        checked = check_honour(
            question,
            sql,
            dialect=dialect,
            measure=_measure_facts(onto, measure),
        )
        if checked.get("verdict") != "honoured":
            refused = _CompileRefused(reason="compile_not_honoured", sql=sql)
            _note_honour_block(refused)
            return refused
        env = _submit_validated(
            sql,
            question=question,
            space_id=space_id,
            session_id=session_id,
            submit=submit,
            ledger_append=ledger_append,
            notes=notes,
            plan_source=plan_source,
            keep_gt=keep_gt,
            measure=measure,
            coverage=coverage,
            where_paths=where_paths,
            warehouse=warehouse,
            plan_origin=plan_origin,
            lead=lead,
            served_route=served_route,
            grantable=grantable,
            qualifier_notes=qualifier_notes,
        )
        if shape_guard_hit(env):
            refused = _CompileRefused(reason="shape_guard", sql=sql)
            _note_honour_block(refused)
            return refused
        return env
    return _submit_validated(
        sql,
        question=question,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        notes=notes,
        plan_source=plan_source,
        keep_gt=keep_gt,
        measure=measure,
        coverage=coverage,
        where_paths=where_paths,
        warehouse=warehouse,
        plan_origin=plan_origin,
        lead=lead,
        served_route=served_route,
        grantable=grantable,
        qualifier_notes=qualifier_notes,
    )


def _compile_askback(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Typed reconfirm after the AI leg. The suggested question is non-empty."""
    env = _abstain(
        question,
        reason,
        space_id=space_id,
        session_id=session_id,
        plan_source=PLAN_SOURCE_ONTOLOGY,
        stage="extract_loop",
    )
    suggested = str(question or "").strip()
    env["reconfirm"] = {
        "why": reason,
        "reason_code": reason,
        "suggested_question": suggested,
        "closest_question": suggested,
    }
    assert_envelope_valid(env)
    return env


def _direct_refusal(env: Mapping[str, Any]) -> bool:
    codes: list[str] = []
    raw = env.get("abstain_reason")
    if isinstance(raw, str) and raw.strip():
        codes.append(raw.strip())
    assumptions = env.get("assumptions") or []
    if isinstance(assumptions, list):
        for item in assumptions:
            text = str(item).strip()
            if text.startswith("GEN-01:"):
                codes.append(text[len("GEN-01:") :].strip())
            elif text.startswith("validate:"):
                codes.append(text[len("validate:") :].strip())
    for code in codes:
        head = code.split(":", 1)[0]
        if head in _DIRECT_CHECK_HEADS or code.startswith("hostile_sql"):
            return True
    return False


def _askback_if_honour_miss(
    env: dict[str, Any],
    reason: str,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    if reason not in _HONOUR_REASONS or env.get("abstained") is not True:
        return env
    if _direct_refusal(env):
        return env
    return _compile_askback(question, reason, space_id=space_id, session_id=session_id)


JOIN_CONFIRM_CODE = "join_unconfirmed"


def _no_ontology_file_join(sql: str, dialect: str | None = None) -> str | None:
    """The file-join case: two base tables and a schema other than main.

    Demo SQL names main tables and does not hit this. One table, including
    a table inside a CTE, does not hit this. Callers do not abstain on it.
    ``DMS_SCHEMA_CONTEXT`` turns it into a confirm card. The flag off leaves
    the SQL on the path it already had.
    """
    read = dialect or SERVING_DIALECT
    try:
        trees = [tree for tree in sqlglot.parse(sql or "", read=read) if tree is not None]
    except Exception:  # noqa: BLE001 - the parser refusal is the other checker
        return None
    if len(trees) != 1:
        return None
    root = trees[0]
    cte_names = {
        str(cte.alias_or_name).lower()
        for cte in root.find_all(exp.CTE)
        if getattr(cte, "alias_or_name", None)
    }
    reads = 0
    outside_main = False
    for table in root.find_all(exp.Table):
        name = str(table.name or "")
        db = table.args.get("db")
        schema = str(getattr(db, "name", "") or "") if db is not None else ""
        if not schema and name.lower() in cte_names:
            continue
        if not name:
            continue
        reads += 1
        if schema and schema.lower() != "main":
            outside_main = True
    if reads >= 2 and outside_main:
        return JOIN_CONFIRM_CODE
    return None


def _join_definition(sql: str, keys: list[dict[str, str]]) -> str:
    """The card body: catalog keys, then the SQL. No question words."""
    lines = ["Proposed join keys:"]
    if not keys:
        lines.append("- none reported")
    for key in keys:
        marks = [
            f"{key[side + '_table']}.{key[side + '_column']} {key[side + '_key']}"
            for side in ("left", "right")
            if key.get(side + "_key")
        ]
        if key.get("reported"):
            marks.append(str(key["reported"]))
        suffix = f" ({', '.join(marks)})" if marks else ""
        left = f"{key['left_table']}.{key['left_column']} {key.get('left_type', '')}"
        right = f"{key['right_table']}.{key['right_column']} {key.get('right_type', '')}"
        lines.append(f"- {left} = {right}{suffix}".rstrip())
    lines.append("SQL:")
    lines.append((sql or "").strip())
    if keys:
        lines.append(
            "Yes runs this SQL. Each key above is filed as a proposed ontology "
            "link for a steward. It is not approved."
        )
    else:
        lines.append(
            "Yes runs this SQL. No catalog key was reported, so no ontology link is filed."
        )
    return "\n".join(lines)


def _join_confirm_envelope(
    *,
    question: str,
    sql: str,
    grantable: set[str],
    warehouse: Path | None,
    dialect: str | None,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Confirm card for a granted join that has no stored ontology."""
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.reconfirm import park_join_confirm

    keys = join_key_facts(warehouse, grantable, sql, dialect)
    definition = _join_definition(sql, keys)
    env = build_abstain(
        reason=JOIN_CONFIRM_CODE,
        question=question,
        stage="join_confirm",
        answer_id="ans_join_confirm",
        text=definition,
        rows=[],
        sql_used=None,
        values=[],
        assumptions=[f"reconfirm: {JOIN_CONFIRM_CODE}"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
        log_ticket=False,
    )
    env["reconfirm"] = {
        "reason_code": JOIN_CONFIRM_CODE,
        "reason_text": definition,
        "definition": definition,
        "suggested_question": question,
        "why": JOIN_CONFIRM_CODE,
        "closest_question": question,
    }
    park_join_confirm(
        env,
        sql=sql,
        grantable=grantable,
        links=[
            {
                "left_table": key["left_table"],
                "left_column": key["left_column"],
                "right_table": key["right_table"],
                "right_column": key["right_column"],
            }
            for key in keys
        ],
    )
    assert_envelope_valid(env)
    return env


def ai_lane_sql_check(
    sql: str,
    *,
    question: str,
    grantable: set[str],
    warehouse: Path | None,
    dialect: str | None,
    space_id: str | None,
    session_id: str | None,
    space_onto: Ontology | None,
    declared: Ontology | None,
    declared_violations: Sequence[Violation],
    grounded: Sequence[GroundedDef] | None = None,
    held: dict[str, Any] | None = None,
) -> str | None:
    """The extract loop's checker. Compile serves call this before submit.

    None means the SQL may be served. A reason means it must not. The
    caller does not add a word list: every branch here already ran on
    model SQL. ``held`` keeps a def_validate or definition envelope the
    loop already returns instead of a bare reason.
    """
    box = held if held is not None else {}
    if (
        os.environ.get("DMS_ONTO_GAP_PROPOSE", "0").strip() == "1"
        and os.environ.get("DMS_ONTO_PG", "0").strip() == "1"
    ):
        from dms_executor.onto_gap import ask_ontology, undeclared_aggregate_reason

        undeclared = undeclared_aggregate_reason(sql, ask_ontology())
        if undeclared:
            return undeclared
    from dms_executor.def_validate import validate_generated_sql

    gated = validate_generated_sql(
        sql,
        grantable=grantable,
        question=question,
        space_id=space_id,
        session_id=session_id,
    )
    if gated is not None:
        box["provenance"] = gated.provenance
        if gated.outcome != "pass":
            if gated.outcome == "reconfirm" and isinstance(gated.envelope, dict):
                from dms_executor.reconfirm import park_def_validate_hold

                park_def_validate_hold(gated.envelope, sql=sql, grantable=grantable)
            box["env"] = gated.envelope
            return gated.reason
        box.pop("env", None)
    if dialect is not None and is_multi_statement(sql, dialect):
        return "multi_statement"
    if space_onto is not None:
        # ONTO-DERIVE-01: the Space join rule is the named refusal, and it
        # runs before EXPLAIN so a grant or syntax miss cannot hide it.
        try:
            cols = relation_columns(space_onto, warehouse, sorted(grantable))
        except Exception:  # noqa: BLE001 - a lake we cannot read proves no join
            return f"{REASON_UNVERIFIED_JOIN}:check_unavailable"
        join_why = unverified_join_reason(
            sql, space_onto, declared_violations, columns_of=cols
        )
        if join_why:
            return join_why
    why = validate_compiled_sql(
        sql, grantable=grantable, warehouse=warehouse, dialect=dialect
    )
    if why and why.startswith("explain:"):
        code = _explain_error_code(sql, warehouse)
        if code:
            why = f"explain:{code}"
    if why:
        return why
    # A granted file join with no stored ontology is not an abstain. The
    # schema-context flag asks the user to confirm it. An ungranted name
    # already returned above.
    if (
        space_onto is None
        and schema_context_enabled()
        and _no_ontology_file_join(sql, dialect)
    ):
        box["env"] = _join_confirm_envelope(
            question=question,
            sql=sql,
            grantable=grantable,
            warehouse=warehouse,
            dialect=dialect,
            space_id=space_id,
            session_id=session_id,
        )
        return JOIN_CONFIRM_CODE
    if declared is not None:
        broken = violations_cited_by_sql(sql, declared, declared_violations)
        if broken:
            if os.environ.get("DMS_FK_ORPHAN_DISCLOSE", "0").strip() == "1":
                from dms_executor.join_repair import orphan_reconfirm

                held_orphans = orphan_reconfirm(
                    broken,
                    declared,
                    question=question,
                    space_id=space_id,
                    session_id=session_id,
                )
                if held_orphans is not None:
                    box["env"] = held_orphans
                    return "fk_orphan_hold"
            return violation_reason(broken)
    grounded_defs = list(grounded or [])
    if grounded_defs:
        blocked = mismatch_envelope(
            sql,
            grounded_defs,
            question=question,
            space_id=space_id,
            session_id=session_id,
        )
        if blocked is not None:
            box["env"] = blocked
            return "definition_mismatch"
    return None


def _run_extract_loop(
    *,
    question: str,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    warehouse: Path | None,
    dialect: str,
    grantable: set[str],
    declared: Ontology | None,
    declared_violations: list[Violation],
    space_onto: Ontology | None = None,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[[str], Any],
    ledger_append: Callable[[dict[str, Any]], Any],
    attempts: list[dict[str, Any]],
    budget: AskCallBudget,
    grounded: Sequence[GroundedDef] | None = None,
) -> dict[str, Any]:
    """Read-only extract loop. Empty rows do not retry. Ranking does not serve."""

    held: dict[str, Any] = {}
    grounded_defs = list(grounded or [])

    def model_sql(body: dict[str, Any] | None) -> str | None:
        if not isinstance(body, dict):
            return None
        return query_sql_from_payload(body) or insights_query_sql(body)

    def check(sql: str) -> str | None:
        return ai_lane_sql_check(
            sql,
            question=question,
            grantable=grantable,
            warehouse=warehouse,
            dialect=dialect,
            space_id=space_id,
            session_id=session_id,
            space_onto=space_onto,
            declared=declared,
            declared_violations=declared_violations,
            grounded=grounded_defs,
            held=held,
        )

    def vote_rows(sql: str, _attempts: list[dict[str, Any]]) -> dict[str, Any]:
        return _vote_rows(
            sql,
            warehouse,
            question=question,
            space_id=space_id,
            session_id=session_id,
        )

    def submit_sql(sql: str, _attempts: list[dict[str, Any]]) -> dict[str, Any]:
        env = _submit_validated(
            sql,
            question=question,
            space_id=space_id,
            session_id=session_id,
            submit=submit,
            ledger_append=ledger_append,
            notes=("GEN-01 Cortex ontology_plan SQL",),
            plan_source=PLAN_SOURCE_ONTOLOGY,
            coverage=coverage_from_sql_path(sql=sql),
            warehouse=warehouse,
            grantable=grantable,
            plan_origin=PLAN_ORIGIN_GENERATE_SQL,
            served_route=_sql_route(payload),
        )
        prov = held.get("provenance")
        if isinstance(prov, dict):
            env["ontology_provenance"] = dict(prov)
        return env

    def abstain(
        reason: str,
        _attempts: list[dict[str, Any]],
        *,
        sql: str | None = None,
        retries: int = 0,
    ) -> dict[str, Any]:
        held_env = held.get("env")
        if isinstance(held_env, dict):
            return held_env
        if (
            sql
            and os.environ.get("DMS_JOIN_DECLARED_FK", "0").strip() == "1"
        ):
            from dms_executor.join_flags import join_repair_stop

            if join_repair_stop(reason):
                from dms_executor.join_repair import resolve_join_repair

                decided = resolve_join_repair(
                    reason,
                    sql,
                    question=question,
                    space_onto=space_onto,
                    violations=declared_violations,
                    warehouse=warehouse,
                    grantable=grantable,
                    dialect=dialect,
                    space_id=space_id,
                    session_id=session_id,
                )
                if isinstance(decided, dict):
                    return decided
                if isinstance(decided, str):
                    return submit_sql(decided, _attempts)
        if reason == RESERVED_PARAM_AS_OF:
            env = reserved_as_of_abstain(
                space_id=space_id,
                session_id=session_id,
                route="generated",
                question=question,
                sql=sql,
                retries=retries,
                stage="extract_loop",
            )
        else:
            # The loop outcome the customer reads stays the bare checker code.
            # The ticket keeps the relation serve_gap named (NAME-ECHO-01).
            ticket_reason = _grant_ticket_reason(reason, sql, grantable, dialect)
            env = _abstain(
                question,
                reason,
                space_id=space_id,
                session_id=session_id,
                plan_source=PLAN_SOURCE_ONTOLOGY,
                sql=sql,
                retries=retries,
                stage="extract_loop",
                ticket_reason=ticket_reason,
            )
            if (
                str(reason).startswith("schema_repair_exhausted:")
                or reason == "sql_dialect_unknown"
            ):
                env["reconfirm"] = {
                    "reason_code": str(reason),
                    "why": str(reason),
                    "closest_question": "",
                }
                note_stage("reconfirm", "done")
        prov = held.get("provenance")
        if isinstance(prov, dict) and isinstance(env, dict):
            env["ontology_provenance"] = dict(prov)
        return env

    def empty_answer(sql: str, _attempts: list[dict[str, Any]]) -> dict[str, Any]:
        # Reached only when empty_result_reason returns None (VALUE-EXISTS-01).
        env = build_answer_envelope(
            answer_id="ans_gen01",
            text=EMPTY_NOTE,
            badge="L2_VALIDATED",
            abstained=False,
            rows=[],
            sql_used=sql,
            assumptions=[EMPTY_NOTE],
            as_of=_as_of(),
            space_id=space_id,
            session_id=session_id,
            ask_mode="live",
            route="generated",
            question=question,
            grounded_tables=sorted(cited_relations(sql)),
        )
        assert_envelope_valid(env)
        stamped = with_plan_origin(
            with_plan_source(env, PLAN_SOURCE_ONTOLOGY),
            PLAN_ORIGIN_GENERATE_SQL,
        )
        route = _sql_route(payload)
        if route:
            stamp_route(stamped, route)
        prov = held.get("provenance")
        if isinstance(prov, dict):
            stamped["ontology_provenance"] = dict(prov)
        return stamped

    # Rows only. The customer submit waits until the second reading agrees,
    # so a split does not ledger a number the envelope does not serve.
    direct = reading_prompt(question, reading=1)
    schema_ladder = None
    if schema_retry_enabled() or _scale_miss_on():
        from dms_executor.schema_retry import SchemaLadder

        schema_ladder = SchemaLadder(
            grantable=set(grantable),
            warehouse=warehouse,
            dialect=dialect,
            ontology=space_onto or declared,
            space_id=space_id,
        )
    first = run_model_loop(
        question=direct,
        ctx=ctx,
        payload=payload,
        compute=compute,
        warehouse=warehouse,
        dialect=dialect,
        model_sql=model_sql,
        check=check,
        submit_sql=vote_rows,
        abstain=abstain,
        empty_answer=empty_answer,
        attempts=attempts,
        no_retry_reasons=frozenset(
            {
                RESERVED_PARAM_AS_OF,
                "pii",
                "def_validate",
                "definition_mismatch",
                JOIN_CONFIRM_CODE,
            }
        ),
        budget=budget,
        schema_ladder=schema_ladder,
    )
    if first.get("abstained") or not first.get("rows"):
        return first
    # The second reading can change the served SQL. It stays on the path.
    note_stage("second_reading", "started")
    try:
        with span("second_reading"):
            return _second_reading(
                first,
                question=question,
                ctx=ctx,
                compute=compute,
                warehouse=warehouse,
                dialect=dialect,
                model_sql=model_sql,
                check=check,
                abstain=abstain,
                empty_answer=empty_answer,
                publish=submit_sql,
                attempts=attempts,
                space_id=space_id,
                session_id=session_id,
                grantable=grantable,
                budget=budget,
                schema_ladder=schema_ladder,
            )
    finally:
        note_stage("second_reading", "done")


def _vote_rows(
    sql: str,
    warehouse: Path | None,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Rows for the comparison. The customer submit happens after agreement."""
    note_stage("sql_vote", "started")
    try:
        return _vote_rows_body(
            sql,
            warehouse,
            question=question,
            space_id=space_id,
            session_id=session_id,
        )
    finally:
        note_stage("sql_vote", "done")


def _vote_rows_body(
    sql: str,
    warehouse: Path | None,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    if warehouse is None or not Path(warehouse).is_file():
        return _abstain(
            question,
            "warehouse_missing",
            space_id=space_id,
            session_id=session_id,
            sql=sql,
            stage="sql_vote",
        )
    with span("submit_execute"):
        rows, err = run_readonly(sql, Path(warehouse))
    if err or not rows:
        return _abstain(
            question,
            err or EMPTY_UNVERIFIED,
            space_id=space_id,
            session_id=session_id,
            sql=sql,
            stage="sql_vote",
        )
    return {"abstained": False, "rows": rows, "sql_used": sql}


def _second_reading(
    first: dict[str, Any],
    *,
    question: str,
    ctx: dict[str, Any],
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    warehouse: Path | None,
    dialect: str,
    model_sql: Callable[[dict[str, Any] | None], str | None],
    check: Callable[[str], str | None],
    abstain: Callable[..., dict[str, Any]],
    empty_answer: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    publish: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    attempts: list[dict[str, Any]],
    space_id: str | None,
    session_id: str | None,
    grantable: set[str],
    budget: AskCallBudget,
    schema_ladder: Any = None,
) -> dict[str, Any]:
    """One plan-then-SQL prompt. Same checker. Same call cap as the first reading."""
    first_sql = str(first.get("sql_used") or "")
    if not budget.allow():
        for item in reversed(attempts):
            if item.get("outcome") == "served":
                item["outcome"] = f"insights_call_cap:{budget.cap}"
                break
        return _abstain(
            question,
            f"insights_call_cap:{budget.cap}",
            space_id=space_id,
            session_id=session_id,
            sql=first_sql,
            stage="sql_vote",
        )
    second_prompt = reading_prompt(
        question, reading=2, plan=empty_plan(), ontology=ontology_json(ctx)
    )
    ctx2 = dict(ctx)
    ctx2.pop("sql_loop_feedback", None)
    ctx2.pop("route_hint", None)
    ctx2["sql_vote_prompt"] = second_prompt
    ctx2["sql_vote_kind"] = "reading"
    budget.take()
    try:
        payload = compute(ctx2)
    except Exception:  # noqa: BLE001 - a missed second reading reconfirms
        payload = None
    second_attempts: list[dict[str, Any]] = []

    def vote_submit(sql: str, _attempts: list[dict[str, Any]]) -> dict[str, Any]:
        return _vote_rows(
            sql,
            warehouse,
            question=question,
            space_id=space_id,
            session_id=session_id,
        )

    second = run_model_loop(
        question=second_prompt,
        ctx=ctx2,
        payload=payload if isinstance(payload, dict) else None,
        compute=compute,
        warehouse=warehouse,
        dialect=dialect,
        model_sql=model_sql,
        check=check,
        submit_sql=vote_submit,
        abstain=abstain,
        empty_answer=empty_answer,
        attempts=second_attempts,
        no_retry_reasons=frozenset(
            {
                RESERVED_PARAM_AS_OF,
                "pii",
                "def_validate",
                "definition_mismatch",
                JOIN_CONFIRM_CODE,
            }
        ),
        recover_failures=True,
        budget=budget,
        schema_ladder=schema_ladder,
    )
    note_rungs(second_attempts)
    second_sql = str(second.get("sql_used") or "")
    raw_first = first.get("rows")
    raw_second = second.get("rows")
    first_rows = cast(list[dict[str, Any]], raw_first if isinstance(raw_first, list) else [])
    second_rows = cast(list[dict[str, Any]], raw_second if isinstance(raw_second, list) else [])
    checked = check(first_sql) is None and check(second_sql) is None
    if checked and readings_agree(
        prompt_a=reading_prompt(question, reading=1),
        prompt_b=second_prompt,
        sql_a=first_sql,
        sql_b=second_sql,
        rows_a=first_rows,
        rows_b=second_rows,
        dialect=dialect,
        grantable=grantable,
    ):
        published = publish(first_sql, attempts)
        if published.get("abstained"):
            for item in reversed(attempts):
                if item.get("outcome") == "served":
                    item["outcome"] = disagreement_detail(published.get("assumptions"))
                    break
        else:
            route = published.get("served_route")
            maybe_save_checked_sql(
                question=question,
                sql=first_sql,
                space_id=space_id,
                warehouse=warehouse,
                grantable=grantable,
                dialect=dialect,
                provenance="yes:vote",
                served_route=route.strip() if isinstance(route, str) else "",
            )
        return published
    detail = disagreement_detail(second.get("assumptions") if second.get("abstained") else None)
    why, closest = detail, ""
    rec = None
    if budget.allow():
        ctx3 = dict(ctx)
        ctx3.pop("sql_loop_feedback", None)
        ctx3.pop("route_hint", None)
        ctx3.pop("vq_examples", None)
        ctx3["sql_vote_prompt"] = reconfirm_prompt(question, detail)
        ctx3["sql_vote_kind"] = "reconfirm"
        budget.take()
        try:
            rec = compute(ctx3)
        except Exception:  # noqa: BLE001 - the offer still names the disagreement
            rec = None
    parsed = reconfirm_from_payload(rec if isinstance(rec, dict) else None)
    if parsed:
        why, closest = parsed
    note_stage("reconfirm", "done")
    return reconfirm_envelope(
        question=question,
        why=why,
        closest_question=closest,
        space_id=space_id,
        session_id=session_id,
    )


def _ladder_after_ai_error(
    *,
    question: str,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    warehouse: Path | None,
    dialect: str,
    grantable: set[str],
    declared: Ontology | None,
    declared_violations: list[Violation],
    space_onto: Ontology | None = None,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[[str], Any],
    ledger_append: Callable[[dict[str, Any]], Any],
    attempts: list[dict[str, Any]],
    budget: AskCallBudget,
    grounded: Sequence[GroundedDef] | None = None,
) -> dict[str, Any]:
    """Retry generate once, then the extract checker. No rule-builder compile.

    The retry carries the same schema and ontology context as the first
    call (``ctx`` is the Insights context the first call was made with).
    ``budget`` is the ask's one cap: the opening call already took a call,
    the retry takes from what is left, and the extract loop shares the same
    budget. A second AI-call error, or SQL the checker will not accept,
    abstains. It does not compile a ranked ontology plan.
    """
    current: dict[str, Any] | None = payload
    if budget.take():
        nxt_ctx = dict(ctx)
        nxt_ctx["sql_loop_feedback"] = {
            "previous_sql": "",
            "reason": INSIGHTS_FAIL_AI_ERROR,
            "prompt": question,
        }
        try:
            got = compute(nxt_ctx)
        except Exception:  # noqa: BLE001 — retry miss, then abstain
            got = None
        if isinstance(got, dict):
            current = got
    sql = None
    if isinstance(current, dict) and insights_fail_reason(current) != INSIGHTS_FAIL_AI_ERROR:
        sql = query_sql_from_payload(current) or insights_query_sql(current)
    if sql and isinstance(current, dict):
        return _run_extract_loop(
            question=question,
            ctx=ctx,
            payload=current,
            compute=compute,
            warehouse=warehouse,
            dialect=dialect,
            grantable=grantable,
            declared=declared,
            declared_violations=declared_violations,
            space_onto=space_onto,
            space_id=space_id,
            session_id=session_id,
            submit=submit,
            ledger_append=ledger_append,
            attempts=attempts,
            budget=budget,
            grounded=grounded,
        )
    return _abstain(
        question,
        INSIGHTS_FAIL_AI_ERROR,
        space_id=space_id,
        session_id=session_id,
        plan_source=PLAN_SOURCE_ONTOLOGY,
        retries=1 if budget.used > 1 else 0,
        stage="extract_loop",
    )


def _hand_compile_to_ai(
    *,
    question: str,
    ctx: dict[str, Any],
    payload: dict[str, Any] | None,
    rejected_sql: str,
    reason: str,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    warehouse: Path | None,
    dialect: str,
    grantable: set[str],
    declared: Ontology | None,
    declared_violations: list[Violation],
    space_onto: Ontology | None,
    space_id: str | None,
    session_id: str | None,
    submit: Callable[[str], Any],
    ledger_append: Callable[[dict[str, Any]], Any],
    attempts: list[dict[str, Any]],
    budget: AskCallBudget,
    grounded: Sequence[GroundedDef] | None = None,
) -> dict[str, Any]:
    """Compile SQL failed the AI-lane checker. Do not serve it.

    One generate call carries the rejected SQL and the checker reason,
    then the extract loop (execute, check, schema repair when that flag
    is on, escalate when that flag is on). No rule-builder compile. A
    miss abstains with the checker reason and no rows. Reconfirm is the
    route's job when that envelope is still unchecked.
    """
    current: dict[str, Any] | None = payload if isinstance(payload, dict) else None
    if budget.take():
        nxt_ctx = dict(ctx)
        nxt_ctx["sql_loop_feedback"] = {
            "previous_sql": rejected_sql,
            "reason": f"checker:{reason}",
            "prompt": question,
        }
        try:
            got = compute(nxt_ctx)
        except Exception:  # noqa: BLE001 — retry miss, then abstain
            got = None
        if isinstance(got, dict):
            current = got
    sql = None
    if isinstance(current, dict):
        sql = query_sql_from_payload(current) or insights_query_sql(current)
    if sql and isinstance(current, dict):
        env = _run_extract_loop(
            question=question,
            ctx=ctx,
            payload=current,
            compute=compute,
            warehouse=warehouse,
            dialect=dialect,
            grantable=grantable,
            declared=declared,
            declared_violations=declared_violations,
            space_onto=space_onto,
            space_id=space_id,
            session_id=session_id,
            submit=submit,
            ledger_append=ledger_append,
            attempts=attempts,
            budget=budget,
            grounded=grounded,
        )
        return _askback_if_honour_miss(
            env,
            reason,
            question=question,
            space_id=space_id,
            session_id=session_id,
        )
    if reason in _HONOUR_REASONS:
        return _compile_askback(
            question, reason, space_id=space_id, session_id=session_id
        )
    return _abstain(
        question,
        reason,
        space_id=space_id,
        session_id=session_id,
        plan_source=PLAN_SOURCE_ONTOLOGY,
        stage="extract_loop",
    )


def _maybe_generative_ask_impl(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    tables: list[str] | None = None,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    submit: Callable[[str], Any] | None = None,
    ledger_append: Callable[[dict[str, Any]], Any] | None = None,
    ontology: Ontology | None = None,
    bind_on_miss: bool = False,
    demo_ontology_allowed: bool = True,
    dialect: str | None = None,
    embedder: Callable[[list[str]], Any] | None = None,
    demo_gate: bool = True,
    ask_context: str | None = None,
) -> dict[str, Any] | None:
    """L2 when retrieve+plan compiles and validate passes. ABSTAIN when unsure.

    Compute receives a short retrieved context, not the full ontology dump.
    Cortex Insights generate (ontology_plan) may return a typed plan or SELECT
    SQL; SQL still goes through hostile/grant/EXPLAIN then Cortex submit.
    When generate is unarmed, a Cortex-ranked metric id that resolves onto
    the DMS ontology (exact, cq_ strip, same-intent alias, token overlap)
    is compiled as ontology_plan with retrieve-typed slots (not bind_plan).
    Ranking walk: skip noise ids; with a retrieve prefer lock, also skip a
    resolvable-but-wrong id such as sku_count on a revenue/SKU-list ask.
    When the walk exhausts, overlay a question-matched Cortex pack id onto
    the prefer lock (not bind_plan; not skip-to-weaker on abort). Leftover
    Cortex certified ids such as ``cq_audit_overdue`` overlay onto the
    verified ``audit_overdue`` measure. Planted refuses stay ABSTAIN.
    Invalid generate SELECT may climb via those slots; hostile SQL does not.
    Cortex compute miss may bind_plan when ``bind_on_miss`` (offline harness
    only) and Insights was not reached. ``Executor.live_ask`` always passes
    ``bind_on_miss=False`` (GEN-03). Product path leaves miss as None so Cortex
    certified ask still runs. Explicit compute unsure is not overridden.
    Predictive asks and a named year 2099 abstain before compile (bar (2)
    KEEP_HOLD: no all-time history under a forecast / 2099 badge).
    When the ask names >=2 supply-chain grains (sku/supplier/plant/lane/day),
    ``try_compile_multi_grain`` runs before one-grain GEN-01 plan or SQL
    short-circuit: ranked where-paths + importance on a *granted* join, or
    honest ABSTAIN naming ``missing_join`` / the grain (never bare
    ``validate:ungranted:...``). bind_plan is not that confident path.
    File-grounded asks skip.
    """
    if tables or compute is None or submit is None or ledger_append is None:
        return None
    q = normalize_ask_question(question)
    if not q:
        return None
    # Model SQL is checked and EXPLAINed on the DuckDB serving copy. A caller
    # that already handed us that file and no dialect is that engine. No file
    # and no dialect stays unknown: there is no silent fallback.
    if dialect is None and warehouse is not None and Path(warehouse).is_file():
        dialect = SERVING_DIALECT
    resolved = sqlglot_dialect(dialect)
    if resolved is None:
        return sql_refusal_envelope(
            reason="sql_dialect_unknown",
            space_id=space_id,
            session_id=session_id,
            route="generated",
            question=q,
        )
    dialect = resolved
    # Armed product asks pass demo_gate=False. The planted paraphrase list
    # stays in front of test fakes; it does not abstain a CortexClient ask.
    # DMS_AI_CLARIFY on: the model reading list decides. Those two checkers
    # do not run, including when the model sends nothing.
    from dms_executor.ask_ladder import ask_ladder_enabled
    from dms_executor.clarify import ai_clarify_enabled

    clarify_on = ai_clarify_enabled()
    ladder_on = ask_ladder_enabled()

    def _phrase_hold() -> dict[str, Any] | None:
        if demo_gate and is_uncertified_paraphrase(q):
            return _abstain(
                q, "uncertified paraphrase: not a generative certify boundary",
                space_id=space_id, session_id=session_id,
            )
        if vague_question(q) and not _scale_miss_on():
            return _abstain(
                q, "question is too vague or time-unbounded to ground",
                space_id=space_id, session_id=session_id,
            )
        if "2099" in asked_calendar_years(q):
            return _abstain(
                q, "year 2099 is not a certified period; all-time history is not that year",
                space_id=space_id, session_id=session_id,
            )
        return None

    # DMS_RULES_OFF is the same skip: a question word must not decide.
    if not clarify_on and not ladder_on and not _rules_off():
        held_phrase = _phrase_hold()
        if held_phrase is not None:
            return held_phrase
        if _is_predictive(q):
            return _abstain(
                q, "predictive: history is not a forecast",
                space_id=space_id, session_id=session_id,
            )

    lake: Path | None
    if warehouse is not None:
        lake = Path(warehouse)
    else:
        candidate = warehouse_path()
        lake = candidate if candidate.is_file() else None
    if lake is not None and not lake.is_file():
        lake = None

    if os.environ.get("DMS_FK_ORPHAN_DISCLOSE", "0").strip() == "1":
        from dms_executor.join_flags import bind_orphan_space, bind_serve_ontology

        bind_orphan_space(space_id)
        bind_serve_ontology(None)
    onto = ontology
    # A2-02/A2-06: a caller-declared ontology that FAILED verify keeps its
    # evidence and stays loaded with failed subjects marked. The default
    # demo ontology (ontology=None) still drops on failure: a lake it was
    # never declared for (BIRD) must keep answering generated SQL as before.
    declared: Ontology | None = None
    declared_violations: list[Violation] = []
    verify_cache_missing = False
    if onto is None:
        onto = load_verified_ontology(lake, space_id=space_id)
    elif lake is not None and not onto.verified:
        loaded = load_verified_ontology(lake, onto)
        if loaded is not None:
            onto = loaded
            cached = cached_verify_violations(loaded)
            if not loaded.verified and cached is None:
                # verify left verified=False but the evidence slot is gone.
                verify_cache_missing = True
                declared = loaded
            else:
                declared_violations = cached or []
                if declared_violations or not loaded.verified:
                    declared = loaded
        else:
            declared_violations = declared_ontology_violations(lake, onto)
            if declared_violations or not onto.verified:
                declared = onto
                onto = None
    allowed = grantable if grantable is not None else set(_KNOWN)
    if (
        os.environ.get("DMS_ONTO_GAP_PROPOSE", "0").strip() == "1"
        and os.environ.get("DMS_ONTO_PG", "0").strip() == "1"
    ):
        from dms_executor.onto_gap import bind_gap_context

        bind_gap_context(
            question=q,
            warehouse=str(lake) if lake is not None else "",
            grantable=allowed,
            ontology=onto,
            space_id=space_id,
            session_id=session_id,
        )
    if _scale_miss_on():
        from dms_executor.scale_miss import begin as scale_begin

        stopped = scale_begin(
            question=q,
            compute=compute,
            ontology=onto,
            warehouse=lake,
            grantable=set(allowed),
            dialect=dialect,
            space_id=space_id,
            session_id=session_id,
        )
        if isinstance(stopped, dict):
            return stopped
    # SCALE-MISS-01 already generated. Clarify must not spend the clock first.
    if clarify_on and compute is not None and not _scale_miss_on():
        from dms_executor.clarify import clarify_before_sql

        clarified = clarify_before_sql(
            q,
            compute=compute,
            warehouse=lake,
            grantable=set(allowed),
            dialect=dialect,
            ontology=onto,
            space_id=space_id,
            session_id=session_id,
            embedder=embedder,
        )
        if isinstance(clarified, dict):
            return clarified
        if clarified is None:
            return _climb_after_silent_verdict(
                q,
                compute=compute,
                submit=submit,
                ledger_append=ledger_append,
                warehouse=lake,
                dialect=dialect,
                grantable=set(allowed),
                ontology=onto,
                space_id=space_id,
                session_id=session_id,
            )
    # A Space ontology (never the demo one). Compile serves run the same
    # join rule the AI lane runs, so this is known before the rank window.
    space_onto = (declared or onto) if not demo_ontology_allowed else None
    if os.environ.get("DMS_FK_ORPHAN_DISCLOSE", "0").strip() == "1":
        from dms_executor.join_flags import bind_serve_ontology

        bind_serve_ontology(space_onto or onto)
    hostile = _refuse_destructive(q, space_id=space_id, session_id=session_id)
    if isinstance(hostile, dict):
        return hostile
    held_refusal: _CompileRefused | None = None
    ranked_env = rank_window_ask(
        q, onto=onto, allowed=allowed, lake=lake, space_id=space_id,
        session_id=session_id, submit=submit, ledger_append=ledger_append,
        dialect=dialect,
        space_onto=space_onto,
        declared=declared,
        declared_violations=declared_violations,
    )
    if isinstance(ranked_env, _CompileRefused):
        held_refusal = ranked_env
        ranked_env = None
    if ranked_env is not None:
        ranked_env["generate_legs"] = generate_legs_view(None)
        return with_served_attribution(ranked_env, None)
    # Short retrieved context only -- not the full ontology dump.
    # Schema work runs only when DMS_SCHEMA_CONTEXT is on. Off: no
    # introspection, no sampling, no prompt, no value index.
    if _scale_miss_on():
        from dms_executor.scale_miss import budget_stop as scale_budget_stop

        stopped = scale_budget_stop(q, space_id=space_id, session_id=session_id)
        if isinstance(stopped, dict):
            return stopped
    note_stage("retrieve_context", "started")
    with span("retrieve_context"):
        ctx = retrieve_short_context(
            q,
            warehouse=lake,
            grantable=allowed,
            ontology=onto,
            embedder=embedder,
        )
    note_stage("retrieve_context", "done")
    envelope_prompt = ""
    index_stamp = ""
    if schema_context_enabled():
        note_stage("schema_context", "started")
        with span("schema_context"):
            ctx = prepare_generate_context(
            ctx,
            question=q,
            serving=lake,
            grantable=allowed,
            ontology=onto,
            dialect=dialect,
            space_id=space_id,
        )
        # The model prompt stays on schema_context. The envelope copy and
        # the index stamp are not part of the Insights catalog.
        held_envelope = ctx.pop(SCHEMA_CONTEXT_ENVELOPE_KEY, "")
        if isinstance(held_envelope, str):
            envelope_prompt = held_envelope
        held_stamp = ctx.pop(SCHEMA_INDEX_STAMP_KEY, "")
        if isinstance(held_stamp, str):
            index_stamp = held_stamp
        note_stage("schema_context", "done")
    # ONTO-DERIVE-01: a Space's own ontology (never the demo one) whose joins
    # generated SQL must follow. Measured against the lake as it is.
    space_block = (
        space_catalog(space_onto, declared_violations, allowed)
        if space_onto is not None
        else None
    )
    # What Insights is asked with. A Space asks with its own ontology, and so does
    # every retry of the extract loop: a retry on the demo context would put the
    # model on another ontology than the one the join rule checks against.
    insights_ctx = (
        _space_insights_body(allowed, space_block, ctx.get(SCHEMA_CONTEXT_FIELD))
        if space_block is not None
        else ctx
    )
    example_rows: list[dict[str, Any]] = []
    if vq_rag_enabled():
        example_rows = retrieve_verified_examples(
            q,
            space_id=space_id,
            warehouse=lake,
            grantable=allowed,
            dialect=dialect,
            embedder=embedder,
        )
        block = examples_text(example_rows)
        if block:
            insights_ctx = dict(insights_ctx)
            insights_ctx["vq_examples"] = block
    # Flag off: this list stays empty and the catalog is not copied.
    # On the demo path `onto` is the demo ontology. Outside demo it is the
    # Space's own, never a demo ontology loaded beside it.
    grounded: list[GroundedDef] = []
    if ground_defs_enabled():
        defs_onto = onto if demo_ontology_allowed else space_onto
        note_stage("ground_defs", "started")
        with span("ground_defs"):
            text, grounded = build_grounding(
            q,
            defs_onto,
            embedder=embedder,
            examples=example_rows,
            grantable=allowed,
        )
        note_stage("ground_defs", "done")
        if text:
            insights_ctx = dict(insights_ctx)
            insights_ctx["governed_definitions"] = text
            # The rows already sit in the governed block. A second Examples
            # section would send the same SQL twice.
            insights_ctx.pop("vq_examples", None)
    from dms_executor.space_knowledge import knowledge_prompt, space_knowledge_enabled

    # Flag off: the catalog is not copied and the context string is ignored.
    if space_knowledge_enabled():
        block = knowledge_prompt(
            q,
            space_id=space_id,
            warehouse=lake,
            embedder=embedder,
            ask_context=ask_context,
        )
        if block:
            insights_ctx = dict(insights_ctx)
            insights_ctx["space_knowledge"] = block
    from dms_executor.ai_first import ai_first_enabled, with_examples

    held_preview: dict[str, Any] | None = None
    if ai_first_enabled():
        # Compile seeds known before the model call. A success is an example
        # (the helper returns None). An abstain is kept and returned once
        # the envelope stamp exists. understand_question runs in the Insights
        # seam when these rows are lifted onto the question.
        preview = _try_multi_grain_envelope(
            q,
            onto=onto,
            payload=None,
            allowed=allowed,
            lake=lake,
            space_id=space_id,
            session_id=session_id,
            submit=submit,
            ledger_append=ledger_append,
            dialect=dialect,
            space_onto=space_onto,
            declared=declared,
            declared_violations=declared_violations,
            grounded=grounded,
            embedder=embedder,
        )
        if isinstance(preview, _CompileRefused):
            held_refusal = preview
            preview = None
        if preview is not None:
            held_preview = preview
        insights_ctx = with_examples(insights_ctx)
        ctx = with_examples(ctx)
    _compute_fn = compute

    def _timed_compute(ctx: dict[str, Any]) -> dict[str, Any] | None:
        before = recorded_model_calls()
        note_stage("insights_generate", "started")
        try:
            with span("insights_generate"):
                try:
                    if _compute_fn is None:
                        return None
                    return _compute_fn(ctx)
                finally:
                    if recorded_model_calls() == before:
                        note_prompt_ctx(ctx)
        finally:
            note_stage("insights_generate", "done")

    compute = _timed_compute
    vote_budget: AskCallBudget | None = None
    ladder_on = ask_ladder_enabled() and not ontology_ranked_lane_enabled()
    if not ontology_ranked_lane_enabled() and (cloop_b_enabled() or ladder_on):
        # Direct SQL. Reading 2 is a different instruction under the same cap.
        if cloop_b_enabled():
            insights_ctx["sql_vote_prompt"] = reading_prompt(q, reading=1)
            insights_ctx["sql_vote_kind"] = "direct"
        vote_budget = AskCallBudget()
    try:
        if held_preview is not None:
            payload = None
        elif _scale_miss_on() and _scale_turn_ready():
            from dms_executor.scale_miss import cached_payload, calls_used

            payload = cached_payload()
            if vote_budget is not None:
                vote_budget.used = max(vote_budget.used, calls_used())
        elif vote_budget is not None and not vote_budget.take():
            payload = None
        else:
            payload = compute(insights_ctx)
    except Exception as exc:  # noqa: BLE001 — compute miss, do not 503 the steward
        from dms_executor.lake_registry import (
            ServingDeadlineExceeded,
            ServingDeadlineReserve,
            ServingLeaseCap,
            ServingLeaseQueueFull,
            ServingWaitCancelled,
        )

        if isinstance(
            exc,
            (
                ServingLeaseCap,
                ServingLeaseQueueFull,
                ServingWaitCancelled,
                ServingDeadlineExceeded,
                ServingDeadlineReserve,
            ),
        ):
            raise
        payload = None
    # Freeze the Insights payload. Later bind_plan overwrite must not invent
    # or drop Cortex setup fields. Ranking merge keeps these keys.
    setup_src = payload if isinstance(payload, dict) else None
    trail_notes: list[str] = []
    validate_why: str | None = None
    loop_attempts: list[dict[str, Any]] = []
    gate_state: dict[str, Any] = {}
    if ladder_on and vote_budget is not None and submit is not None and ledger_append is not None:
        from dms_executor.ask_ladder import ClimbSession, bind_climb

        bind_climb(
            ClimbSession(
                compute=compute,
                budget=vote_budget,
                ctx=insights_ctx,
                reserve=_scale_ladder_reserve(),
                question=q,
                warehouse=lake,
                dialect=dialect,
                grantable=set(allowed),
                space_id=space_id,
                session_id=session_id,
                ontology=onto,
                attempts=loop_attempts,
                check=lambda sql: validate_compiled_sql(
                    sql, grantable=set(allowed), warehouse=lake, dialect=dialect
                ),
                submit=lambda sql, payload: _submit_validated(
                    sql,
                    question=q,
                    space_id=space_id,
                    session_id=session_id,
                    submit=submit,
                    ledger_append=ledger_append,
                    notes=(),
                    plan_source=PLAN_SOURCE_OTHER,
                    coverage=coverage_from_sql_path(sql=sql),
                    warehouse=lake,
                    grantable=set(allowed),
                    served_route=_sql_route(payload if isinstance(payload, dict) else None),
                ),
            )
        )

    def _stamp(env: dict[str, Any] | None) -> dict[str, Any] | None:
        env = with_setup_fields(env, setup_src)
        if not isinstance(env, dict):
            return env
        env["generate_legs"] = generate_legs_view(
            setup_src, validate_reason=validate_why
        )
        # ontology_plan with no origin is Cortex's label for model SQL.
        # Leaving the origin empty makes that label look like a compile
        # beside the model that was called.
        if (
            env.get("abstained") is True
            and str(env.get("plan_source") or "") == PLAN_SOURCE_ONTOLOGY
            and not str(env.get("plan_origin") or "").strip()
            and generate_model_called(setup_src)
        ):
            with_plan_origin(env, PLAN_ORIGIN_GENERATE_SQL)
        stamped = with_served_attribution(env, setup_src)
        if not isinstance(stamped, dict):
            return stamped
        note_rungs(loop_attempts)
        if loop_attempts:
            stamped["loop"] = list(loop_attempts)
        apply_sql_credit(stamped, setup_src, loop_attempts, dialect=dialect)
        if envelope_prompt or index_stamp:
            if envelope_prompt:
                stamped[SCHEMA_CONTEXT_FIELD] = envelope_prompt
            if index_stamp:
                stamped["index_stamp"] = index_stamp
            # Through PII-01, not after it. Hint values are already tokens.
            stamped = mask_unknown_keys(stamped)
        from dms_executor.ai_first import stamp_examples

        stamp_examples(stamped)
        prov = gate_state.get("provenance")
        if isinstance(prov, dict) and isinstance(stamped, dict):
            stamped["ontology_provenance"] = dict(prov)
        return freeze_route(stamped)

    def _after_miss() -> dict[str, Any] | None:
        from dms_executor.ask_ladder import current_climb

        session = current_climb()
        if session is not None:
            served = session.finish_miss()
            if isinstance(served, dict) and served.get("abstained") is not True:
                return _stamp(served)
        return _stamp(None)

    if held_preview is not None:
        return _stamp(with_plan_origin(held_preview, PLAN_ORIGIN_ONTOLOGY_COMPILE))

    if verify_cache_missing:
        return _stamp(
            _abstain(
                q,
                "ontology_unverified",
                space_id=space_id,
                session_id=session_id,
            )
        )

    kind = parse_compute_plan(payload)
    source = plan_source_from_payload(payload)
    origin = plan_origin_from_payload(payload)
    budget_stop = insights_budget_stop(payload if isinstance(payload, dict) else None)
    if budget_stop:
        # Before ranking/multi-grain: a timeout or cap stop never compiles a plan.
        # Flag on: a partial or an ask-back. Never the timeout abstain.
        if _scale_miss_on():
            from dms_executor.scale_miss import budget_stop_envelope

            return _stamp(
                budget_stop_envelope(
                    q,
                    payload if isinstance(payload, dict) else None,
                    space_id=space_id,
                    session_id=session_id,
                )
            )
        return _stamp(
            _abstain(
                q,
                budget_stop,
                space_id=space_id,
                session_id=session_id,
                plan_source=source if source != PLAN_SOURCE_BIND else PLAN_SOURCE_OTHER,
                stage="insights_budget",
            )
        )
    if (
        isinstance(payload, dict)
        and insights_fail_reason(payload) == INSIGHTS_FAIL_AI_ERROR
        and not gen01_rules_enabled()
    ):
        # The checker never saw model SQL. Do not compile the rule builder.
        if cloop_b_enabled():
            if compute is None or submit is None or ledger_append is None:
                return None
            if vote_budget is None:
                # The ranked lane made the opening call without a budget.
                vote_budget = AskCallBudget()
                vote_budget.take()
            return _stamp(
                _ladder_after_ai_error(
                    question=q,
                    ctx=insights_ctx,
                    payload=payload,
                    compute=compute,
                    warehouse=lake,
                    dialect=dialect,
                    grantable=allowed,
                    declared=declared,
                    declared_violations=declared_violations,
                    space_onto=space_onto,
                    space_id=space_id,
                    session_id=session_id,
                    submit=submit,
                    ledger_append=ledger_append,
                    attempts=loop_attempts,
                    budget=vote_budget,
                    grounded=grounded,
                )
            )
        return _stamp(
            _abstain(
                q,
                INSIGHTS_FAIL_AI_ERROR,
                space_id=space_id,
                session_id=session_id,
                plan_source=source if source != PLAN_SOURCE_BIND else PLAN_SOURCE_OTHER,
                stage="ai_call",
            )
        )
    if (
        isinstance(payload, dict)
        and insights_fail_reason(payload) == INSIGHTS_FAIL_UNAUTHORIZED
        and not gen01_rules_enabled()
    ):
        # Auth failure is not a model miss. Do not compile the rule builder.
        return _stamp(
            _abstain(
                q,
                INSIGHTS_FAIL_UNAUTHORIZED,
                space_id=space_id,
                session_id=session_id,
                plan_source=source if source != PLAN_SOURCE_BIND else PLAN_SOURCE_OTHER,
                stage="ai_call",
            )
        )
    if (
        cloop_b_enabled()
        and not ontology_ranked_lane_enabled()
        and isinstance(payload, dict)
    ):
        # Ranking compile stays off this path. Model SQL runs on the extract.
        sql_in = query_sql_from_payload(payload)
        would_rank = kind == "miss" and ontology_plan_from_ranking(
            q, payload, onto=onto, ctx=ctx
        ) is not None
        if kind == "sql" or would_rank:
            if sql_in and sql_has_reserved_as_of(sql_in):
                return _stamp(
                    reserved_as_of_abstain(
                        space_id=space_id,
                        session_id=session_id,
                        route="generated",
                        question=q,
                        sql=sql_in,
                        stage="extract_loop",
                    )
                )
            if vote_budget is None:
                vote_budget = AskCallBudget()
                vote_budget.take()
            return _stamp(
                _run_extract_loop(
                    question=q,
                    ctx=insights_ctx,
                    payload=payload,
                    compute=compute,
                    warehouse=lake,
                    dialect=dialect,
                    grantable=allowed,
                    declared=declared,
                    declared_violations=declared_violations,
                    space_onto=space_onto,
                    space_id=space_id,
                    session_id=session_id,
                    submit=submit,
                    ledger_append=ledger_append,
                    attempts=loop_attempts,
                    budget=vote_budget,
                    grounded=grounded,
                )
            )
    ranked_slots: dict[str, Any] | None = None
    if kind in {"miss", "sql"}:
        ranked_slots = ontology_plan_from_ranking(q, payload, onto=onto, ctx=ctx)
    if kind == "miss" and _rules_off() and ranked_slots is not None:
        # Insights returned no SQL. Ranking would compile from the question.
        # That compiler does not serve while DMS_RULES_OFF is on.
        return _stamp(_rules_off_hold(q, space_id=space_id, session_id=session_id))
    if kind == "miss" and ranked_slots is not None:
        payload = {**(payload if isinstance(payload, dict) else {}), **ranked_slots}
        kind = parse_compute_plan(payload)
        source = PLAN_SOURCE_ONTOLOGY
        origin = PLAN_ORIGIN_ONTOLOGY_RANKING
        trail_notes = [NOTE_INSIGHTS_RANKING, NOTE_FALLBACK_GENERATE_EMPTY]
    if kind == "miss" and ranked_slots is None:
        # GEN-PATH-REFUSE-01: Cortex ranked the intended metric, DMS cannot
        # compile it. Named ABSTAIN — do not bind_plan or Cortex.ask a guess.
        gap = ranking_missing_metric_gap(q, payload, onto=onto)
        if gap:
            return _stamp(
                _abstain(
                    q,
                    gap,
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source if source != PLAN_SOURCE_BIND else PLAN_SOURCE_OTHER,
                    stage="serve_gap",
                )
            )
    if kind == "unsure":
        return _stamp(
            _abstain(
                q,
                "compute abstained (unsure)",
                space_id=space_id,
                session_id=session_id,
                plan_source=source,
            )
        )
    # ≥2 supply-chain grains: compile ranked where-paths BEFORE one-grain
    # GEN-01 plan/SQL. Live ranking fills kind=plan sku-only; that must
    # not drop plant/day/lane/supplier (#249 / #234 KEEP_HOLD).
    if kind == "sql":
        early_sql = query_sql_from_payload(payload if isinstance(payload, dict) else None)
        if early_sql and sql_has_reserved_as_of(early_sql):
            return _stamp(
                reserved_as_of_abstain(
                    space_id=space_id,
                    session_id=session_id,
                    route="generated",
                    question=q,
                )
            )
    def _ai_budget() -> AskCallBudget:
        nonlocal vote_budget
        if vote_budget is None:
            vote_budget = AskCallBudget()
        return vote_budget

    def _handoff(refused: _CompileRefused) -> dict[str, Any]:
        budget = _ai_budget()
        body = payload if isinstance(payload, dict) else None
        has_sql = bool(
            body and (query_sql_from_payload(body) or insights_query_sql(body))
        )
        # A charged opening call that returned a plan is not the SQL reading.
        # Give that slot back so generate plus the second reading fit the cap.
        if not has_sql and budget.used > 0:
            budget.used -= 1
        return _hand_compile_to_ai(
            question=q,
            ctx=insights_ctx,
            payload=payload if isinstance(payload, dict) else None,
            rejected_sql=refused.sql,
            reason=refused.reason,
            compute=compute,
            warehouse=lake,
            dialect=dialect,
            grantable=allowed,
            declared=declared,
            declared_violations=declared_violations,
            space_onto=space_onto,
            space_id=space_id,
            session_id=session_id,
            submit=submit,
            ledger_append=ledger_append,
            attempts=loop_attempts,
            budget=budget,
            grounded=grounded,
        )

    multi_env = _try_multi_grain_envelope(
        q,
        onto=onto,
        payload=payload if isinstance(payload, dict) else None,
        allowed=allowed,
        lake=lake,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        dialect=dialect,
        space_onto=space_onto,
        declared=declared,
        declared_violations=declared_violations,
        grounded=grounded,
        embedder=embedder,
    )
    if isinstance(multi_env, _CompileRefused):
        if multi_env.reason in _HONOUR_REASONS:
            return _stamp(_handoff(multi_env))
        held_refusal = multi_env
        multi_env = None
    if multi_env is not None:
        # DMS's own multi-grain compile is served. Any model SQL in the
        # payload never ran, so its generate origin and model do not apply.
        return _stamp(with_plan_origin(multi_env, PLAN_ORIGIN_ONTOLOGY_COMPILE))
    if kind == "sql" and held_refusal is not None and isinstance(payload, dict):
        # The compile candidate failed the checker. Run the model SQL through
        # the same extract loop (check, execute, repair, escalate).
        sql_budget = _ai_budget()
        if sql_budget.used == 0:
            sql_budget.take()
        return _stamp(
            _run_extract_loop(
                question=q,
                ctx=insights_ctx,
                payload=payload,
                compute=compute,
                warehouse=lake,
                dialect=dialect,
                grantable=allowed,
                declared=declared,
                declared_violations=declared_violations,
                space_onto=space_onto,
                space_id=space_id,
                session_id=session_id,
                submit=submit,
                ledger_append=ledger_append,
                attempts=loop_attempts,
                budget=sql_budget,
                grounded=grounded,
            )
        )
    if kind == "sql":
        sql = query_sql_from_payload(payload)
        if source == PLAN_SOURCE_OTHER:
            source = PLAN_SOURCE_ONTOLOGY
        if not origin:
            origin = PLAN_ORIGIN_GENERATE_SQL
        if not sql:
            return _stamp(
                _abstain(
                    q, "query_sql was empty",
                    space_id=space_id, session_id=session_id, plan_source=source,
                )
            )
        from dms_executor.def_validate import validate_generated_sql

        gated = validate_generated_sql(
            sql,
            grantable=allowed,
            question=q,
            space_id=space_id,
            session_id=session_id,
        )
        if gated is not None and gated.outcome != "pass":
            gate_state["provenance"] = gated.provenance
            if isinstance(gated.envelope, dict):
                if gated.outcome == "reconfirm":
                    from dms_executor.reconfirm import park_def_validate_hold

                    park_def_validate_hold(
                        gated.envelope, sql=sql, grantable=allowed
                    )
                return _stamp(gated.envelope)
            return _stamp(
                _abstain(
                    q,
                    gated.reason or "def_validate",
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source,
                    sql=sql,
                )
            )
        if gated is not None:
            gate_state["provenance"] = gated.provenance
        why = validate_compiled_sql(
            sql, grantable=allowed, warehouse=lake, dialect=dialect
        )
        # Not one SELECT: refused first, before any other branch reads `why`.
        if why in {"multi_statement", "statement_not_allowed", "sql_not_analyzable"}:
            return _stamp(
                _abstain(
                    q, why,
                    space_id=space_id, session_id=session_id, plan_source=source,
                    sql=sql,
                )
            )
        broken = (
            violations_cited_by_sql(sql, declared, declared_violations)
            if declared is not None and not why
            else []
        )
        if broken:
            # The lake's declared ontology says this join is not safe (an
            # orphan FK misattributes or drops rows). SQL is no exemption.
            # A steward threshold above the measured ratio reconfirms instead.
            if os.environ.get("DMS_FK_ORPHAN_DISCLOSE", "0").strip() == "1":
                from dms_executor.join_repair import orphan_reconfirm

                held_orphans = orphan_reconfirm(
                    broken,
                    declared,
                    question=q,
                    space_id=space_id,
                    session_id=session_id,
                )
                if held_orphans is not None:
                    return _stamp(held_orphans)
            return _stamp(
                _abstain(
                    q,
                    violation_reason(broken),
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source,
                )
            )
        if why == RESERVED_PARAM_AS_OF:
            return _stamp(
                reserved_as_of_abstain(
                    space_id=space_id,
                    session_id=session_id,
                    route="generated",
                    question=q,
                )
            )
        join_why: str | None = None
        cols: dict[str, set[str]] | None = None
        # The join rule is the named refusal. An EXPLAIN or grant miss must
        # not hide it behind a generic validate reason.
        if space_onto is not None:
            try:
                cols = relation_columns(space_onto, lake, sorted(allowed))
            except Exception:  # noqa: BLE001 - a lake we cannot read proves no join
                join_why = f"{REASON_UNVERIFIED_JOIN}:check_unavailable"
            else:
                join_why = unverified_join_reason(
                    sql, space_onto, declared_violations, columns_of=cols
                )
        elif (
            not why
            and schema_context_enabled()
            and _no_ontology_file_join(sql, dialect)
        ):
            # Granted file join, no stored ontology. Confirm the keys and
            # the SQL. Do not serve the number and do not abstain.
            return _stamp(
                _join_confirm_envelope(
                    question=q,
                    sql=sql,
                    grantable=allowed,
                    warehouse=lake,
                    dialect=dialect,
                    space_id=space_id,
                    session_id=session_id,
                )
            )
        if join_why:
            # No guessed joins: on a Space with a derived ontology, generated
            # SQL may join two relations only over a link the source declared
            # and verify() measured. A confident join on anything else is the
            # plausible wrong number this layer exists to refuse.
            # Flag on, four fixable shapes get one repair, then the same gates.
            if os.environ.get("DMS_JOIN_DECLARED_FK", "0").strip() == "1":
                from dms_executor.join_repair import resolve_join_repair

                decided = resolve_join_repair(
                    join_why,
                    sql,
                    question=q,
                    space_onto=space_onto,
                    violations=declared_violations,
                    warehouse=lake,
                    grantable=set(allowed),
                    dialect=dialect,
                    space_id=space_id,
                    session_id=session_id,
                    columns_of=cols,
                )
                if isinstance(decided, dict):
                    return _stamp(decided)
                if isinstance(decided, str):
                    sql = decided
                    join_why = None
                    why = None
            if join_why:
                return _stamp(
                    _abstain(
                        q,
                        join_why,
                        space_id=space_id,
                        session_id=session_id,
                        plan_source=source,
                        sql=sql,
                    )
                )
        if why:
            shown = customer_grant_reason(why)
            cited = (
                _cited_ungranted(sql, allowed, dialect)
                if shown == "ungranted" or why == "sql_relation_not_granted"
                else None
            )
            if cloop_b_enabled():
                loop_attempts.append(
                    loop_entry(
                        prompt=q,
                        payload=payload if isinstance(payload, dict) else None,
                        sql=sql,
                        outcome=f"checker:{shown}",
                        dialect=dialect,
                    )
                )
            # A file or a script that did not parse must not climb. The climb
            # would hand the reject text, including an object name, back out.
            if (
                why.startswith("hostile_sql:")
                or structural_grant_stop(why)
                or ranked_slots is None
            ):
                return _stamp(
                    _abstain(
                        q, f"validate:{shown}",
                        space_id=space_id, session_id=session_id, plan_source=source,
                        notes=trail_notes,
                        ticket_reason=(
                            f"validate:{cited}" if cited and cited != shown else None
                        ),
                    )
                )
            # Climb: invalid SELECT is not authority. Ranked DMS slots may be.
            # Keep the reject reason on the envelope (QUAL-GUARD-01 route B).
            validate_why = why
            payload = {**(payload if isinstance(payload, dict) else {}), **ranked_slots}
            kind = parse_compute_plan(payload)
            source = PLAN_SOURCE_ONTOLOGY
            origin = PLAN_ORIGIN_ONTOLOGY_RANKING
            trail_notes = [
                NOTE_INSIGHTS_RANKING,
                f"{NOTE_FALLBACK_VALIDATE_PREFIX}{why}",
            ]
        else:
            if (
                os.environ.get("DMS_ONTO_GAP_PROPOSE", "0").strip() == "1"
                and os.environ.get("DMS_ONTO_PG", "0").strip() == "1"
            ):
                from dms_executor.onto_gap import undeclared_aggregate_reason

                undeclared = undeclared_aggregate_reason(sql, onto)
                if undeclared:
                    return _stamp(
                        _abstain(
                            q,
                            undeclared,
                            space_id=space_id,
                            session_id=session_id,
                            plan_source=source,
                            sql=sql,
                        )
                    )
            if grounded:
                blocked = mismatch_envelope(
                    sql,
                    grounded,
                    question=q,
                    space_id=space_id,
                    session_id=session_id,
                )
                if blocked is not None:
                    return _stamp(blocked)
            sql_route = _sql_route(payload if isinstance(payload, dict) else None)
            return _stamp(
                _submit_validated(
                    sql,
                    question=q,
                    space_id=space_id,
                    session_id=session_id,
                    submit=submit,
                    ledger_append=ledger_append,
                    notes=("GEN-01 Cortex ontology_plan SQL",),
                    plan_source=source,
                    coverage=coverage_from_sql_path(sql=sql),
                    warehouse=lake,
                    grantable=allowed,
                    plan_origin=origin,
                    served_route=sql_route,
                )
            )
    if kind != "plan":
        # Named Insights fail-closed: never bind. Product Cortex.ask still
        # runs only on a transport miss (no insights_fail stamp).
        fail = insights_fail_reason(payload if isinstance(payload, dict) else None)
        if fail:
            return _stamp(
                _abstain(
                    q,
                    fail,
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source if source != PLAN_SOURCE_BIND else PLAN_SOURCE_OTHER,
                )
            )
        # Offline harness only (bind_on_miss): bind from retrieved ontology
        # when Cortex Insights was not reached. An Insights REFUSE
        # (unarmed / A-0009 / no SQL) is not a transport miss -- bind_plan
        # over it is what left #201 at ontology_plan=0 / bind_plan=15.
        # Product path must miss into Cortex.ask so certified VQ/L0 still run.
        # Multi-grain already ran above; bind_plan stays non-confident.
        # live_ask always passes bind_on_miss=False (GEN-03).
        if insights_was_reached(payload if isinstance(payload, dict) else None):
            if bind_on_miss:
                return _stamp(
                    _abstain(
                        q,
                        "insights generate did not return a typed plan or SQL",
                        space_id=space_id,
                        session_id=session_id,
                        plan_source=source if source != PLAN_SOURCE_BIND else PLAN_SOURCE_OTHER,
                    )
                )
            if held_refusal is not None:
                return _stamp(_handoff(held_refusal))
            return _after_miss()
        if not bind_on_miss:
            if held_refusal is not None:
                return _stamp(_handoff(held_refusal))
            return _after_miss()
        payload = bind_plan(q, ctx, ontology=onto, embedder=embedder)
        kind = parse_compute_plan(payload)
        source = PLAN_SOURCE_BIND
        origin = ""
        if kind == "unsure":
            code = ""
            if isinstance(payload, dict):
                code = str(payload.get("reason_code") or "")
            blocked = reconfirm_for_reason(
                code, question=q, space_id=space_id, session_id=session_id
            )
            if blocked is not None:
                return _stamp(blocked)
            return _stamp(
                _abstain(
                    q, "retrieve bind abstained (unsure)",
                    space_id=space_id, session_id=session_id,
                    plan_source=source,
                )
            )
        if kind != "plan":
            methods = ",".join(str(m) for m in (ctx.get("methods") or []))
            return _stamp(
                _abstain(
                    q,
                    f"query_plan was not typed after retrieve ({methods})",
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source,
                )
            )
        fallback_note = "compute_fallback:bind_plan"
        trail_notes = [fallback_note]

    assert isinstance(payload, dict)
    plan = plan_from_payload(payload)
    if plan is None:
        return _stamp(
            _abstain(
                q, "query_plan was not typed",
                space_id=space_id, session_id=session_id, plan_source=source,
                notes=trail_notes,
            )
        )
    raw_plan = payload.get("query_plan")
    plan_body = raw_plan if isinstance(raw_plan, dict) else None
    plan_notes: tuple[str, ...] = ()
    if os.environ.get("DMS_QUALIFIER_AI", "0").strip() == "1":
        from dms_executor.qualifier_ai import NOTE_KINDS, is_candidate_note

        full = unhonored_qualifier_reason(q, plan=plan_body)
        gap = unhonored_qualifier_reason(q, plan=plan_body, skip_kinds=NOTE_KINDS)
        if is_candidate_note(full):
            plan_notes = (str(full),)
    else:
        gap = unhonored_qualifier_reason(q, plan=plan_body)
    if gap:
        return _stamp(
            _abstain(
                q,
                gap,
                space_id=space_id,
                session_id=session_id,
                plan_source=source,
                notes=trail_notes,
            )
        )
    if (
        space_onto is not None
        and plan.measure
        and plan.measure not in space_onto.measures
    ):
        # Measures are never derived for a SQL-source Space (NEEDS_FOUNDER):
        # a plan that needs one the Space has not declared is a named gap.
        return _stamp(
            _abstain(
                q,
                f"{REASON_NO_DECLARED_MEASURE}: {plan.measure} is not a measure this "
                "Space declares",
                space_id=space_id,
                session_id=session_id,
                plan_source=source,
                notes=trail_notes,
            )
        )
    if onto is None and not demo_ontology_allowed and declared is None:
        return _stamp(
            _abstain(
                q,
                "missing_ontology: this Space has no verified ontology, so a "
                "typed plan cannot compile; only validated generated SQL can answer",
                space_id=space_id,
                session_id=session_id,
                plan_source=source,
                notes=trail_notes,
            )
        )
    if onto is None or (not onto.verified and not declared_violations):
        return _stamp(
            _abstain(
                q,
                violation_reason(declared_violations),
                space_id=space_id,
                session_id=session_id,
                plan_source=source,
            )
        )
    if not onto.verified:
        touched = violations_cited_by_plan(plan, onto, declared_violations)
        if touched:
            return _stamp(
                _abstain(
                    q,
                    violation_reason(touched),
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source,
                )
            )

    honour_block = _compile_blocked()
    if honour_block is not None:
        return _stamp(_handoff(honour_block))
    ungrounded = _ungrounded_measure(q, plan.measure, onto, embedder=embedder)
    if ungrounded:
        blocked = reconfirm_for_reason(
            ungrounded, question=q, space_id=space_id, session_id=session_id
        )
        if blocked is not None:
            return _stamp(blocked)
        return _stamp(
            _abstain(
                q,
                ungrounded,
                space_id=space_id,
                session_id=session_id,
                plan_source=source,
                notes=trail_notes,
            )
        )
    compiled = _compile_maybe_unverified(onto, plan)
    if isinstance(compiled, Refusal):
        return _stamp(
            _abstain(
                q, f"{compiled.reason}: {compiled.detail}",
                space_id=space_id, session_id=session_id, plan_source=source,
            )
        )
    if not isinstance(compiled, CompiledQuery):
        return _stamp(
            _abstain(
                q, "compile_failed",
                space_id=space_id, session_id=session_id, plan_source=source,
            )
        )
    if compiled.existential:
        return _stamp(
            _abstain(
                q, "existential many-to-many filter: ask path will not choose a reading",
                space_id=space_id, session_id=session_id, plan_source=source,
            )
        )

    why = validate_compiled_sql(
        compiled.sql, grantable=allowed, warehouse=lake, dialect=dialect
    )
    # Not one SELECT: refused first, before any other branch reads `why`.
    if why in {"multi_statement", "statement_not_allowed", "sql_not_analyzable"}:
        return _stamp(
            _abstain(
                q, why,
                space_id=space_id, session_id=session_id, plan_source=source,
                sql=compiled.sql,
            )
        )
    if why == RESERVED_PARAM_AS_OF:
        return _stamp(
            reserved_as_of_abstain(
                space_id=space_id,
                session_id=session_id,
                route="generated",
                question=q,
            )
        )
    if why:
        shown = customer_grant_reason(why)
        cited = (
            _cited_ungranted(compiled.sql, allowed, dialect)
            if shown == "ungranted" or why == "sql_relation_not_granted"
            else None
        )
        return _stamp(
            _abstain(
                q, f"validate:{shown}",
                space_id=space_id, session_id=session_id, plan_source=source,
                ticket_reason=(f"validate:{cited}" if cited and cited != shown else None),
            )
        )
    if not origin and source != PLAN_SOURCE_BIND:
        origin = (
            PLAN_ORIGIN_ONTOLOGY_RANKING
            if NOTE_INSIGHTS_RANKING in trail_notes
            else PLAN_ORIGIN_GENERATE_SQL
        )
    checker_why = ai_lane_sql_check(
        compiled.sql,
        question=q,
        grantable=allowed,
        warehouse=lake,
        dialect=dialect,
        space_id=space_id,
        session_id=session_id,
        space_onto=space_onto,
        declared=declared,
        declared_violations=declared_violations,
        grounded=grounded,
    )
    if checker_why:
        if _checker_is_direct(checker_why):
            return _stamp(
                _abstain(
                    q,
                    checker_why,
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source,
                    sql=compiled.sql,
                )
            )
        return _stamp(_handoff(_CompileRefused(reason=checker_why, sql=compiled.sql)))
    served = _commit_compile(
        compiled.sql,
        question=q,
        onto=onto,
        dialect=dialect,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        notes=tuple([*compiled.notes, *trail_notes]),
        plan_source=source,
        keep_gt=plan.keep_gt,
        measure=plan.measure,
        coverage=compiled.coverage,
        where_paths=compiled.where_paths,
        warehouse=lake,
        grantable=allowed,
        plan_origin=origin,
        served_route=ROUTE_COMPILE,
        qualifier_notes=plan_notes,
    )
    if isinstance(served, _CompileRefused):
        return _stamp(_handoff(served))
    return _stamp(served)


def _climb_after_silent_verdict(
    question: str,
    *,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    submit: Callable[[str], Any] | None,
    ledger_append: Callable[[dict[str, Any]], Any] | None,
    warehouse: Path | None,
    dialect: str,
    grantable: set[str],
    ontology: Any,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """No understand verdict. Climb repair, context, plan, then escalate.

    The last rung sends route_hint escalate. OpenVault picks the model.
    Every rung empty is a reconfirm from build_abstain. No phrase checker.
    """
    from dms_executor.ask_ladder import ClimbSession, bind_climb, is_direct_abstain
    from dms_executor.sql_loop import AskCallBudget

    code = "no_sql"
    notes: tuple[str, ...] = ()
    served: dict[str, Any] | None = None
    if submit is not None and ledger_append is not None:
        attempts: list[dict[str, Any]] = []
        session = ClimbSession(
            compute=compute,
            budget=AskCallBudget(),
            ctx={},
            question=question,
            warehouse=warehouse,
            dialect=dialect,
            grantable=set(grantable),
            space_id=space_id,
            session_id=session_id,
            ontology=ontology,
            attempts=attempts,
            check=lambda sql: validate_compiled_sql(
                sql,
                grantable=set(grantable),
                warehouse=warehouse,
                dialect=dialect,
            ),
            submit=lambda sql, payload: _submit_validated(
                sql,
                question=question,
                space_id=space_id,
                session_id=session_id,
                submit=submit,
                ledger_append=ledger_append,
                notes=(),
                plan_source=PLAN_SOURCE_OTHER,
                coverage=coverage_from_sql_path(sql=sql),
                warehouse=warehouse,
                grantable=set(grantable),
                served_route=_sql_route(payload if isinstance(payload, dict) else None),
            ),
            reserve=False,
        )
        bind_climb(session)
        served = session.finish_miss()
        code = session.exhausted_reason("no_sql")
        notes = session.notes()
    if isinstance(served, dict) and served.get("abstained") is not True:
        return served
    env = build_abstain(
        reason=code,
        ticket_reason=code,
        abstain_reason=code,
        question=question,
        stage="generative",
        answer_id="ans_gen01_abstain",
        text=customer_abstain_text(code),
        rows=[],
        sql_used=None,
        assumptions=[f"GEN-01: {code}", *notes],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    if not is_direct_abstain(code):
        env["reconfirm"] = {
            "reason_code": code,
            "reason_text": str(env.get("text") or ""),
            "suggested_question": "",
        }
    assert_envelope_valid(env)
    return env


def _qualifier_note_only(reason: str | None) -> bool:
    """Flag on: this lexical reason is a note, so the rank compiler does not serve it."""
    if os.environ.get("DMS_QUALIFIER_AI", "0").strip() != "1":
        return False
    from dms_executor.qualifier_ai import is_candidate_note

    return is_candidate_note(reason)


def maybe_generative_ask(
    *args: Any, **kwargs: Any
) -> dict[str, Any] | None:
    """Bind the ask clock, then stamp ``timings`` on the envelope."""
    from dms_core.control_plane.onto_data import use_ask_space

    from dms_executor.ask_ladder import bind_climb, reset_climb
    from dms_executor.scale_miss import bind_turn, reset_turn

    raw_space = kwargs.get("space_id")
    space_id = raw_space if isinstance(raw_space, str) else None
    token = ensure_clock()
    climb = bind_climb(None)
    turn_token = bind_turn(None)
    honour_tokens = _reset_honour_state()
    repair_token = None
    if os.environ.get("DMS_QUALIFIER_AI", "0").strip() == "1":
        asked = kwargs.get("question")
        if not isinstance(asked, str) and args:
            asked = args[0]
        compute = kwargs.get("compute")
        if isinstance(asked, str) and callable(compute):
            from dms_executor.qualifier_ai import (
                bind_qualifier_repair,
                repair_from_compute,
            )

            repair_token = bind_qualifier_repair(repair_from_compute(compute, asked))
    try:
        with use_ask_space(space_id):
            env = _maybe_generative_ask_impl(*args, **kwargs)
        if isinstance(env, dict):
            attach_timings(env)
        return env
    finally:
        if repair_token is not None:
            from dms_executor.qualifier_ai import reset_qualifier_repair

            reset_qualifier_repair(repair_token)
        _release_honour_state(honour_tokens)
        reset_turn(turn_token)
        reset_climb(climb)
        release_clock(token)
