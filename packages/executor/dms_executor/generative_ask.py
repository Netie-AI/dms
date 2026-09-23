"""GEN-01 — ontology-grounded generative ask + execute-validate.

Exact-match VQ/pack stays first. Retrieve a short schema/ontology context,
hand it to a caller-supplied ``compute`` planner, fill typed slots, compile,
validate, then Cortex-submit. Unsure or validate-fail is ABSTAIN. A compute
miss returns None (existing contract ask still runs).

GEN-03: the live ask path has no planner. Cortex POST /dms/query ignores the
ontology context and returns no typed plan (KB F-0055), so ``Executor.live_ask``
passes a closed, no-network seam and ``bind_on_miss=False``. Only offline
harnesses and compile-path tests supply a planner here.

Does not expand certified exact-match packs. Does not invent provider keys.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cortex_client.compute import (
    insights_was_reached,
    query_plan_from_insights_ranking,
)

from dms_executor.demo_ask import _is_predictive, normalize_ask_question
from dms_executor.demo_pack import is_uncertified_paraphrase
from dms_executor.demo_warehouse import DEMO_TABLES, connect_file, warehouse_path
from dms_executor.envelope import (
    _relation_bare,
    _sql_cited_labels,
    asked_calendar_years,
    assert_envelope_valid,
    build_answer_envelope,
)
from dms_executor.gen_path_refuse import (
    customer_abstain_text,
    ranking_missing_metric_gap,
)
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql
from dms_executor.ontology import (
    CompiledQuery,
    Coverage,
    Ontology,
    Refusal,
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
    slots_for_measure,
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
    """Verify against the lake. Unverified ontologies cannot answer (compile refuses).

    Does not reseed. A test warehouse must already hold the relations the
    ontology names — ``ensure_demo_warehouse`` would drop them.
    """
    if warehouse is None or not Path(warehouse).is_file():
        return None
    target = onto if onto is not None else demo_ontology(Path(warehouse))
    con = connect_file(Path(warehouse))
    try:
        violations = target.verify(con)
    finally:
        con.close()
    if violations or not target.verified:
        return None
    return target


def unverified_reason(declared: Ontology | None) -> str:
    """``ontology_unverified``, plus the checks verify() failed when known.

    A bare "ontology_unverified" abstains honestly but tells the steward
    nothing; the violation (e.g. business_key_unique on customer_code) is the
    thing they can fix.
    """
    failed = (declared.__dict__.get("_violations") or []) if declared is not None else []
    parts = [f"{v.check} {v.subject}: {v.detail}" for v in failed[:3]]
    return "ontology_unverified" + (": " + "; ".join(parts) if parts else "")


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
    return {_relation_bare(n) for n in _sql_cited_labels(sql) if _relation_bare(n)}


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
    named = cited_relations(sql)
    missing = {t for t in named if t not in grantable and f"warehouse_{t}" not in grantable}
    if missing:
        return f"ungranted:{','.join(sorted(missing))}"
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


def _as_of() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _abstain(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
    plan_source: str = PLAN_SOURCE_OTHER,
) -> dict[str, Any]:
    env = build_answer_envelope(
        answer_id="ans_gen01_abstain",
        text=customer_abstain_text(reason),
        badge="ABSTAIN",
        abstained=True,
        rows=[],
        sql_used=None,
        assumptions=[f"GEN-01: {reason}"],
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
) -> dict[str, Any]:
    out_rows = rows_from_submit_result(result)
    text = f"Found {len(out_rows)} row(s)."
    if out_rows:
        text += "\n" + "\n".join(
            "  - " + ", ".join(f"{k}={v}" for k, v in row.items()) for row in out_rows[:12]
        )
    env = build_answer_envelope(
        answer_id="ans_gen01",
        text=text,
        badge="L2_VALIDATED",
        abstained=False,
        rows=out_rows,
        sql_used=sql,
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
        return with_plan_source(env, plan_source)
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
    env = with_plan_source(env, plan_source)
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
) -> dict[str, Any]:
    if not coverage_valid(coverage):
        return _abstain(
            question,
            "coverage_invalid: numeric answer missing include/exclude/unsure",
            space_id=space_id,
            session_id=session_id,
            plan_source=plan_source,
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
    )


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

    onto = ontology
    if onto is None:
        onto = load_verified_ontology(lake)
    elif lake is not None and not onto.verified:
        onto = load_verified_ontology(lake, onto)
    allowed = grantable if grantable is not None else set(_KNOWN)
    # Short retrieved context only -- not the full ontology dump.
    ctx = retrieve_short_context(
        q, warehouse=lake, grantable=allowed, ontology=onto
    )
    try:
        payload = compute(ctx)
    except Exception:  # noqa: BLE001 — compute miss, do not 503 the steward
        payload = None

    kind = parse_compute_plan(payload)
    fallback_note: str | None = None
    source = plan_source_from_payload(payload)
    ranked_slots: dict[str, Any] | None = None
    if kind in {"miss", "sql"}:
        ranked_slots = ontology_plan_from_ranking(q, payload, onto=onto, ctx=ctx)
    if kind == "miss" and ranked_slots is not None:
        payload = {**(payload if isinstance(payload, dict) else {}), **ranked_slots}
        kind = parse_compute_plan(payload)
        source = PLAN_SOURCE_ONTOLOGY
        fallback_note = "insights_ranking:ontology_plan"
    if kind == "miss" and ranked_slots is None:
        # GEN-PATH-REFUSE-01: Cortex ranked the intended metric, DMS cannot
        # compile it. Named ABSTAIN — do not bind_plan or Cortex.ask a guess.
        gap = ranking_missing_metric_gap(q, payload, onto=onto)
        if gap:
            return _abstain(
                q,
                gap,
                space_id=space_id,
                session_id=session_id,
                plan_source=source if source != PLAN_SOURCE_BIND else PLAN_SOURCE_OTHER,
            )
    if kind == "unsure":
        return _abstain(
            q,
            "compute abstained (unsure)",
            space_id=space_id,
            session_id=session_id,
            plan_source=source,
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
        return multi_env
    if kind == "sql":
        sql = query_sql_from_payload(payload)
        if source == PLAN_SOURCE_OTHER:
            source = PLAN_SOURCE_ONTOLOGY
        if not sql:
            return _abstain(
                q, "query_sql was empty",
                space_id=space_id, session_id=session_id, plan_source=source,
            )
        why = validate_compiled_sql(sql, grantable=allowed, warehouse=lake)
        if why:
            if why.startswith("hostile_sql:") or ranked_slots is None:
                return _abstain(
                    q, f"validate:{why}",
                    space_id=space_id, session_id=session_id, plan_source=source,
                )
            # Climb: invalid SELECT is not authority. Ranked DMS slots may be.
            payload = {**(payload if isinstance(payload, dict) else {}), **ranked_slots}
            kind = parse_compute_plan(payload)
            source = PLAN_SOURCE_ONTOLOGY
            fallback_note = "insights_ranking:ontology_plan"
        else:
            return _submit_validated(
                sql,
                question=q,
                space_id=space_id,
                session_id=session_id,
                submit=submit,
                ledger_append=ledger_append,
                notes=("GEN-01 Cortex ontology_plan SQL",),
                plan_source=source,
                coverage=coverage_from_sql_path(sql=sql),
            )
    if kind != "plan":
        # Offline harness only (bind_on_miss): bind from retrieved ontology
        # when Cortex Insights was not reached. An Insights REFUSE
        # (unarmed / A-0009 / no SQL) is not a transport miss -- bind_plan
        # over it is what left #201 at ontology_plan=0 / bind_plan=15.
        # Product path must miss into Cortex.ask so certified VQ/L0 still run.
        # Multi-grain already ran above; bind_plan stays non-confident.
        # live_ask always passes bind_on_miss=False (GEN-03).
        if insights_was_reached(payload if isinstance(payload, dict) else None):
            if bind_on_miss:
                return _abstain(
                    q,
                    "insights generate did not return a typed plan or SQL",
                    space_id=space_id,
                    session_id=session_id,
                    plan_source=source if source != PLAN_SOURCE_BIND else PLAN_SOURCE_OTHER,
                )
            return None
        if not bind_on_miss:
            return None
        payload = bind_plan(q, ctx)
        kind = parse_compute_plan(payload)
        source = PLAN_SOURCE_BIND
        if kind == "unsure":
            return _abstain(
                q, "retrieve bind abstained (unsure)",
                space_id=space_id, session_id=session_id,
                plan_source=source,
            )
        if kind != "plan":
            methods = ",".join(str(m) for m in (ctx.get("methods") or []))
            return _abstain(
                q,
                f"query_plan was not typed after retrieve ({methods})",
                space_id=space_id,
                session_id=session_id,
                plan_source=source,
            )
        fallback_note = "compute_fallback:bind_plan"

    assert isinstance(payload, dict)
    plan = plan_from_payload(payload)
    if plan is None:
        return _abstain(
            q, "query_plan was not typed",
            space_id=space_id, session_id=session_id, plan_source=source,
        )
    if onto is None or not onto.verified:
        return _abstain(
            q, unverified_reason(ontology),
            space_id=space_id, session_id=session_id, plan_source=source,
        )

    compiled = onto.compile(
        plan.measure,
        group_by=plan.group_by,
        filters=plan.filters,
        via=plan.via,
        limit=plan.limit,
    )
    if isinstance(compiled, Refusal):
        return _abstain(
            q, f"{compiled.reason}: {compiled.detail}",
            space_id=space_id, session_id=session_id, plan_source=source,
        )
    if not isinstance(compiled, CompiledQuery):
        return _abstain(
            q, "compile_failed",
            space_id=space_id, session_id=session_id, plan_source=source,
        )
    if compiled.existential:
        return _abstain(
            q, "existential many-to-many filter: ask path will not choose a reading",
            space_id=space_id, session_id=session_id, plan_source=source,
        )

    why = validate_compiled_sql(compiled.sql, grantable=allowed, warehouse=lake)
    if why:
        return _abstain(
            q, f"validate:{why}",
            space_id=space_id, session_id=session_id, plan_source=source,
        )
    return _submit_validated(
        compiled.sql,
        question=q,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        notes=tuple([*compiled.notes, *([fallback_note] if fallback_note else [])]),
        plan_source=source,
        keep_gt=plan.keep_gt,
        measure=plan.measure,
        coverage=compiled.coverage,
        where_paths=compiled.where_paths,
    )
