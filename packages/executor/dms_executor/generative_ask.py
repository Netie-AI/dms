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
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import sqlglot
from cortex_client.compute import (
    INSIGHTS_FAIL_EMPTY,
    PLAN_ORIGIN_GENERATE_SQL,
    PLAN_ORIGIN_ONTOLOGY_RANKING,
    PLAN_ORIGINS,
    classify_insights_fail,
    insights_fail_reason,
    insights_query_sql,
    insights_was_reached,
    query_plan_from_insights_ranking,
    typed_query_plan,
)
from cortex_client.qualifiers import unhonored_qualifier_reason
from sqlglot import exp

from dms_executor.demo_ask import _is_predictive, normalize_ask_question
from dms_executor.demo_pack import is_uncertified_paraphrase
from dms_executor.demo_warehouse import DEMO_TABLES, connect_file, warehouse_path
from dms_executor.envelope import (
    _relation_bare,
    _sql_cited_labels,
    asked_calendar_years,
    assert_envelope_valid,
    build_answer_envelope,
    chart_from_rows,
    render_row_lines,
)
from dms_executor.gen_path_refuse import (
    cortex_refusal_gap,
    customer_abstain_text,
    ranking_missing_metric_gap,
)
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql
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
    try_compile_multi_grain,
)
from dms_executor.semantic_retrieve import (
    bind_plan,
    intent_slots,
    load_measure_aliases,
    retrieve_short_context,
    retrieve_space_context,
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
from dms_executor.sql_currency import currency_mismatch_reason
from dms_executor.sql_grain import (
    grain_mismatch_reason,
    real_table_labels,
    relation_name_parts,
    resolve_declared_relations,
    rows_mismatch_reason,
)
from dms_executor.verified_queries import rows_from_submit_result

_KNOWN = frozenset(DEMO_TABLES)
_UNSURE_ASK = re.compile(
    r"\b(worry about|is this good|just give me)\b",
    re.I,
)
PLAN_SOURCE_ONTOLOGY = "ontology_plan"
PLAN_SOURCE_BIND = "bind_plan"
PLAN_SOURCE_OTHER = "other"
PLAN_SOURCES = frozenset(
    {PLAN_SOURCE_ONTOLOGY, PLAN_SOURCE_BIND, PLAN_SOURCE_OTHER}
)
NOTE_INSIGHTS_RANKING = "insights_ranking:ontology_plan"
NOTE_FALLBACK_GENERATE_EMPTY = "fallback:generate_empty"
NOTE_FALLBACK_VALIDATE_PREFIX = "fallback:validate:"
# Cortex#269 ROUTER-1 Insights fingerprint. Copy as received; never infer.
SETUP_FIELD_KEYS: tuple[str, ...] = (
    "served_provider",
    "served_model",
    "served_local",
    "learn_enabled",
    "learn_source",
    "route_store_id",
)


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
    legs: list[dict[str, str]]
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
            if got not in {"sql", "plan", "nothing"}:
                got = "nothing"
            legs.append({"returned": got})
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


def load_verified_ontology(warehouse: Path | None, onto: Ontology | None = None) -> Ontology | None:
    """Verify against the lake. Failed subjects stay marked; the rest can answer.

    A caller-declared ontology that fails verify is returned with those
    subjects marked (A2-06). The default demo ontology (``onto is None``)
    still returns None on failure so lakes it was never declared for
    (BIRD, live product) keep today's SQL behaviour.

    Does not reseed. A test warehouse must already hold the relations the
    ontology names — ``ensure_demo_warehouse`` would drop them.
    """
    if warehouse is None or not Path(warehouse).is_file():
        return None
    if onto is not None:
        target = onto
        declared = True
    else:
        target = demo_ontology(Path(warehouse))
        declared = False
    con = connect_file(Path(warehouse))
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
    con = connect_file(Path(warehouse))
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


def is_empty_generation(payload: dict[str, Any] | None) -> bool:
    """Insights returned a payload with nothing in it to plan from.

    Empty output (``{}``) or an empty ``query_sql``, with no typed plan, no
    ranking, no refusal reason and no other named Insights failure. ``None``
    (a transport miss: compute raised or was never wired) is not this.
    """
    if not isinstance(payload, dict):
        return False
    if query_sql_from_payload(payload) or cortex_refusal_gap(payload) is not None:
        return False
    stamped = insights_fail_reason(payload)
    if stamped is not None and stamped != INSIGHTS_FAIL_EMPTY:
        return False
    return classify_insights_fail(payload) == INSIGHTS_FAIL_EMPTY


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
    return QueryPlan(
        measure=measure,
        group_by=group_by,
        filters=filters,
        via=via,
        limit=lim,
        keep_gt=keep_gt,
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
    """Bare names of the real relations ``sql`` reads.

    Scope analysis first (``real_table_labels``: a CTE name is not a relation,
    a same-named real table in another scope is). The FROM/JOIN regex is the
    fallback only for SQL the analysis refuses, which never reaches submit.
    """
    labels = real_table_labels(sql)
    if labels is None:
        labels = _sql_cited_labels(sql)
    return {_relation_bare(n) for n in labels if _relation_bare(n)}


def validate_compiled_sql(
    sql: str,
    *,
    grantable: set[str],
    warehouse: Path | None,
) -> str | None:
    """None if the compiled SQL may be submitted. Else a reason (do not execute)."""
    try:
        reject_hostile_chat_sql(sql)
    except SecurityEvent as exc:
        return f"hostile_sql:{exc.code}"
    # SHARED NAMING RULE (SPACE-GEN-01 round 2): every relation must be one the
    # grant declares, qualified exactly as declared, or a bare name exactly
    # one declared table carries (resolved to it). CTE aliases are not
    # tables. The parse tree reads a comma join and ``FROM/**/t``; a statement
    # it cannot read is one this check cannot prove granted (fails closed).
    resolved, why = resolve_declared_relations(sql, grantable)
    if why:
        return why
    sql = resolved
    if warehouse is None or not Path(warehouse).is_file():
        return "warehouse_missing"
    con = connect_file(Path(warehouse))
    try:
        con.execute(f"EXPLAIN {sql}")
    except Exception as exc:  # noqa: BLE001
        return f"explain:{type(exc).__name__}"
    finally:
        con.close()
    return None


REASON_SOURCE_TRUNCATED = "source_truncated"


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
    labels = real_table_labels(sql) or []
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
            for root in sqlglot.parse(sql, read="duckdb")
            if root is not None
            for c in root.find_all(exp.Column)
        }
    except Exception:  # noqa: BLE001
        return f"{REASON_UNTYPED_NUMERIC}:unparsed"
    hits = sorted(f"{t}.{c}" for t, cols in flagged.items() for c in cols if c in named)
    if not hits:
        return None
    return f"{REASON_UNTYPED_NUMERIC}:{','.join(hits)}"


def truncated_source_reason(sql: str, warehouse: Path | None) -> str | None:
    """``source_truncated:<t>`` when the SQL reads a source the row cap cut short.

    The ingest registry records ``truncated`` per pulled table; this is the
    only place the generative path reads it. A ``bronze.<t>`` relation is
    matched by its table name; a bare name only when it is not a demo table,
    so a demo ``transactions`` is never mistaken for a truncated upload.
    """
    if warehouse is None or not Path(warehouse).is_file():
        return None
    from dms_executor.bronze import lookup_ingest_watermarks

    marks = {str(k).lower(): v for k, v in lookup_ingest_watermarks(path=Path(warehouse)).items()}
    if not marks:
        return None
    # The parse tree's real relations: a CTE alias is not a pulled source.
    labels = real_table_labels(sql) or []
    cut: set[str] = set()
    for label in dict.fromkeys(str(x).strip().lower() for x in labels):
        schema, _, bare = label.rpartition(".")
        if schema and schema != "bronze":
            continue
        if not schema and bare in DEMO_TABLES:
            continue
        rec = marks.get(f"bronze.{bare}") if schema else marks.get(bare)
        if rec and rec.get("truncated") is True:
            cut.add(bare)
    if not cut:
        return None
    return f"{REASON_SOURCE_TRUNCATED}:{','.join(sorted(cut))}"


def _as_of() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _abstain(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
    plan_source: str = PLAN_SOURCE_OTHER,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    env = build_answer_envelope(
        answer_id="ans_gen01_abstain",
        text=customer_abstain_text(reason),
        badge="ABSTAIN",
        abstained=True,
        rows=[],
        sql_used=None,
        assumptions=[f"GEN-01: {reason}", *[n for n in notes if str(n).strip()]],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
        question=question,
    )
    assert_envelope_valid(env)
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
) -> dict[str, Any]:
    out_rows = rows_from_submit_result(result)
    text = f"Found {len(out_rows)} row(s)."
    if out_rows:
        text += "\n" + render_row_lines(out_rows)
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
        return with_plan_origin(with_plan_source(env, plan_source), plan_origin)
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
    assert_envelope_valid(env)
    env = with_plan_origin(with_plan_source(env, plan_source), plan_origin)
    if where_paths:
        env["where_paths"] = where_paths_for_envelope(where_paths)
    return env


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
) -> dict[str, Any]:
    if not coverage_valid(coverage):
        return _abstain(
            question,
            "coverage_invalid: numeric answer missing include/exclude/unsure",
            space_id=space_id,
            session_id=session_id,
            plan_source=plan_source,
            notes=notes,
        )
    gap = unhonored_qualifier_reason(question, sql=sql)
    if gap:
        return _abstain(
            question,
            gap,
            space_id=space_id,
            session_id=session_id,
            plan_source=plan_source,
            notes=notes,
        )
    ccy_why = currency_mismatch_reason(question, sql, warehouse=warehouse)
    if ccy_why:
        return _abstain(
            question,
            ccy_why,
            space_id=space_id,
            session_id=session_id,
            plan_source=plan_source,
        )
    # GRAIN-GUARD-01: L2 only over the grain and columns the question asked,
    # fail closed on a shape the gate cannot analyse. No rows are trimmed.
    grain_why = grain_mismatch_reason(question, sql)
    if grain_why:
        return _abstain(
            question,
            grain_why,
            space_id=space_id,
            session_id=session_id,
            plan_source=plan_source,
            notes=notes,
        )
    # SPACE-GEN-01: a source loaded under the row cap is not the whole table.
    # A COUNT / SUM / lookup over it would be stamped L2 on part of the data
    # (BIRD ``trans``: 500,000 of 1,056,320 rows). Named ABSTAIN, before submit.
    trunc_why = truncated_source_reason(sql, warehouse) or untyped_numeric_reason(
        sql, warehouse
    )
    if trunc_why:
        return _abstain(
            question,
            trunc_why,
            space_id=space_id,
            session_id=session_id,
            plan_source=plan_source,
            notes=notes,
        )
    try:
        result = submit(sql)
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
    scalar_why = rows_mismatch_reason(question, sql, rows_from_submit_result(result))
    if scalar_why:
        return _abstain(
            question,
            scalar_why,
            space_id=space_id,
            session_id=session_id,
            plan_source=plan_source,
            notes=notes,
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
) -> dict[str, Any]:
    """Isolated A/B miss: ABSTAIN, do not mix Cortex/pack into the other lane."""
    return _abstain(
        question,
        reason,
        space_id=space_id,
        session_id=session_id,
        plan_source=PLAN_SOURCE_OTHER,
    )


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
) -> dict[str, Any] | None:
    """≥2 grains: ranked where-paths + importance, or named ABSTAIN.

    Runs before one-grain GEN-01 plan/SQL. bind_plan is not this path.
    None means this ask is not a multi-grain compile (caller continues).
    """
    lock = _multi_grain_measure(q, onto, payload)
    multi = try_compile_multi_grain(onto, lock or None, q, grantable=allowed)
    if multi is None:
        return None
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
    why = validate_compiled_sql(multi.sql, grantable=allowed, warehouse=lake)
    if why:
        gap = missing_join_for_ungranted(why, detect_supply_chain_grains(q))
        return _abstain(
            q, gap or f"validate:{why}",
            space_id=space_id, session_id=session_id, plan_source=source,
        )
    return _submit_validated(
        multi.sql,
        question=q,
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
    )


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
    )


#: ``ontology.source`` on the Insights body (SHARED NAMING RULE). Cortex uses
#: its pack ranking and certified formulas only for ``demo``; for ``space`` it
#: never consults ``packs/dms`` metrics.
ONTOLOGY_SOURCE_DEMO = "demo"
ONTOLOGY_SOURCE_SPACE = "space"


def generation_catalog(
    ctx: dict[str, Any],
    allowed: set[str],
    *,
    demo: bool,
    space: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The retrieved context as sent to Cortex, with the declared catalog.

    ``tables`` is every relation this turn may read, each exactly as granted
    (``bronze.schools`` qualified, a demo table bare); names that break the
    naming rule are not sent. ``source`` is explicit: Cortex must not guess
    the demo from column names. The retrieved context itself is unchanged.
    """
    tables = sorted(
        {".".join(p) for p in (relation_name_parts(t) for t in allowed) if p}
    )
    out = {
        **ctx,
        "source": ONTOLOGY_SOURCE_DEMO if demo else ONTOLOGY_SOURCE_SPACE,
        "tables": tables,
    }
    if space is not None and not demo:
        # ONTO-DERIVE-01: the Space's stored, re-measured ontology. Objects
        # with keys, only links that verified, measures only if declared.
        # Budgeted inside Cortex's caller-ontology limits: an ontology Cortex
        # rejects would 4xx every ask on the Space. A cut is marked.
        out.update(budget_space_block(space, used=len(json.dumps(out, default=str))))
    return out


def maybe_generative_ask(
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
) -> dict[str, Any] | None:
    """L2 when retrieve+plan compiles and validate passes. ABSTAIN when unsure.

    SPACE-GEN-01: ``tables`` (a user grounding selection) narrows ``grantable``
    and the retrieved context; it no longer skips generation. A selection that
    leaves nothing granted is a named ABSTAIN, never a wider read.
    ``demo_ontology_allowed=False`` (a Space whose data is not the demo lake)
    never loads the supply-chain demo ontology: its measures and links are not
    this Space's, so ranking or a typed plan cannot compile against them, and a
    typed plan with no Space ontology is ``missing_ontology``.

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
    """
    if compute is None or submit is None or ledger_append is None:
        return None
    q = normalize_ask_question(question)
    if not q:
        return None
    if is_uncertified_paraphrase(q):
        return _abstain(
            q, "uncertified paraphrase: not a generative certify boundary",
            space_id=space_id, session_id=session_id,
        )
    if _UNSURE_ASK.search(q):
        return _abstain(
            q, "question is too vague or time-unbounded to ground",
            space_id=space_id, session_id=session_id,
        )
    if _is_predictive(q):
        return _abstain(
            q, "predictive: history is not a forecast",
            space_id=space_id, session_id=session_id,
        )
    if "2099" in asked_calendar_years(q):
        return _abstain(
            q, "year 2099 is not a certified period; all-time history is not that year",
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

    allowed = set(grantable) if grantable is not None else set(_KNOWN)
    selection = [str(t) for t in (tables or []) if t]
    if selection:
        picked = set(selection)
        allowed = {t for t in allowed if t in picked}
        if not allowed:
            return _abstain(
                q,
                "ungranted: none of the selected tables is granted to this Space",
                space_id=space_id,
                session_id=session_id,
            )

    onto = ontology
    # A2-02/A2-06: a caller-declared ontology that FAILED verify keeps its
    # evidence and stays loaded with failed subjects marked. The default
    # demo ontology (ontology=None) still drops on failure: a lake it was
    # never declared for (BIRD) must keep answering generated SQL as before.
    declared: Ontology | None = None
    declared_violations: list[Violation] = []
    verify_cache_missing = False
    if onto is None:
        onto = load_verified_ontology(lake) if demo_ontology_allowed else None
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
    # Demo: short retrieved context only -- not the full ontology dump.
    # Space (SPACE-GEN-01 round 3): Cortex treats the columns it is sent as
    # the column allowlist, so the Space's whole granted catalog goes, every
    # column of each table (capped with an explicit truncated flag), and no
    # demo-pack measure_aliases / measures / bound lake values ride along.
    if demo_ontology_allowed:
        ctx = retrieve_short_context(
            q, warehouse=lake, grantable=allowed, ontology=onto
        )
    else:
        ctx = retrieve_space_context(q, warehouse=lake, grantable=allowed)
    # ONTO-DERIVE-01: a Space's own ontology (never the demo one) whose joins
    # generated SQL must follow. Measured just above, against the lake as it is.
    space_onto = (declared or onto) if not demo_ontology_allowed else None
    space_block = (
        space_catalog(space_onto, declared_violations, allowed)
        if space_onto is not None
        else None
    )
    try:
        payload = compute(
            generation_catalog(
                ctx, allowed, demo=demo_ontology_allowed, space=space_block
            )
        )
    except Exception:  # noqa: BLE001 — compute miss, do not 503 the steward
        payload = None
    # Freeze the Insights payload. Later bind_plan overwrite must not invent
    # or drop Cortex setup fields. Ranking merge keeps these keys.
    setup_src = payload if isinstance(payload, dict) else None
    trail_notes: list[str] = []
    validate_why: str | None = None

    def _stamp(env: dict[str, Any] | None) -> dict[str, Any] | None:
        env = with_setup_fields(env, setup_src)
        if not isinstance(env, dict):
            return env
        env["generate_legs"] = generate_legs_view(
            setup_src, validate_reason=validate_why
        )
        return env

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
    ranked_slots: dict[str, Any] | None = None
    if kind in {"miss", "sql"}:
        ranked_slots = ontology_plan_from_ranking(q, payload, onto=onto, ctx=ctx)
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
    )
    if multi_env is not None:
        return _stamp(with_plan_origin(multi_env, origin))
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
        why = validate_compiled_sql(sql, grantable=allowed, warehouse=lake)
        if not why:
            # Submit what was validated: a bare name resolved to the declared
            # ``schema.table`` (SHARED NAMING RULE), never the demo relation
            # DuckDB would pick for the bare name.
            sql = resolve_declared_relations(sql, allowed)[0]
        broken = (
            violations_cited_by_sql(sql, declared, declared_violations)
            if declared is not None and not why
            else []
        )
        if broken:
            # The lake's declared ontology says this join is not safe (an
            # orphan FK misattributes or drops rows). SQL is no exemption.
            return _stamp(
                _abstain(
                    q,
                    violation_reason(broken),
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source,
                )
            )
        join_why: str | None = None
        if space_onto is not None and not why:
            try:
                cols = relation_columns(space_onto, lake, sorted(allowed))
            except Exception:  # noqa: BLE001 - a lake we cannot read proves no join
                join_why = f"{REASON_UNVERIFIED_JOIN}:check_unavailable"
            else:
                join_why = unverified_join_reason(
                    sql, space_onto, declared_violations, columns_of=cols
                )
        if join_why:
            # No guessed joins: on a Space with a derived ontology, generated
            # SQL may join two relations only over a link the source declared
            # and verify() measured. A confident join on anything else is the
            # plausible wrong number this layer exists to refuse.
            return _stamp(
                _abstain(
                    q,
                    join_why,
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source,
                )
            )
        if why:
            if why.startswith("hostile_sql:") or ranked_slots is None:
                return _stamp(
                    _abstain(
                        q, f"validate:{why}",
                        space_id=space_id, session_id=session_id, plan_source=source,
                        notes=trail_notes,
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
                    plan_origin=origin,
                )
            )
    if kind != "plan":
        # SPACE-GEN-01 round 3: on a Space, Cortex refusing the generated SQL
        # against the caller catalog ("table X is not in the caller catalog")
        # is the answer: a named gap, never a fall-through to the contract
        # ask's generic text. The customer reads a DMS-named code; Cortex's
        # raw words (which may name a table the model invented) stay in
        # assumptions.
        refusal = (
            cortex_refusal_gap(payload if isinstance(payload, dict) else None)
            if not demo_ontology_allowed
            else None
        )
        if refusal is not None:
            gap, raw = refusal
            return _stamp(
                _abstain(
                    q,
                    gap,
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source if source != PLAN_SOURCE_BIND else PLAN_SOURCE_OTHER,
                    notes=[f"Cortex refuse_reason: {raw}"],
                )
            )
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
            return _stamp(None)
        if not bind_on_miss:
            return _stamp(None)
        payload = bind_plan(q, ctx)
        kind = parse_compute_plan(payload)
        source = PLAN_SOURCE_BIND
        origin = ""
        if kind == "unsure":
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
    gap = unhonored_qualifier_reason(
        q, plan=raw_plan if isinstance(raw_plan, dict) else None
    )
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

    why = validate_compiled_sql(compiled.sql, grantable=allowed, warehouse=lake)
    if why:
        return _stamp(
            _abstain(
                q, f"validate:{why}",
                space_id=space_id, session_id=session_id, plan_source=source,
            )
        )
    if not origin and source != PLAN_SOURCE_BIND:
        origin = (
            PLAN_ORIGIN_ONTOLOGY_RANKING
            if NOTE_INSIGHTS_RANKING in trail_notes
            else PLAN_ORIGIN_GENERATE_SQL
        )
    return _stamp(
        _submit_validated(
            compiled.sql,
            question=q,
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
            plan_origin=origin,
        )
    )
