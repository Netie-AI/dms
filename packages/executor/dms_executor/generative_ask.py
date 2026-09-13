"""GEN-01 — ontology-grounded generative ask + execute-validate.

Exact-match VQ/pack stays first. Retrieve a short schema/ontology context,
send it to Cortex compute (FreeRoute stays in Cortex), fill typed slots,
compile, validate, then Cortex-submit. Unsure or validate-fail is ABSTAIN.
Missing compute client is a miss (existing contract ask still runs).

Does not expand certified exact-match packs. Does not invent provider keys.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dms_executor.demo_ask import normalize_ask_question
from dms_executor.demo_pack import is_uncertified_paraphrase
from dms_executor.demo_warehouse import DEMO_TABLES, connect_file, warehouse_path
from dms_executor.envelope import (
    _relation_bare,
    _sql_cited_labels,
    assert_envelope_valid,
    build_answer_envelope,
)
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql
from dms_executor.ontology import CompiledQuery, Ontology, Refusal, demo_ontology
from dms_executor.semantic_retrieve import bind_plan, retrieve_short_context
from dms_executor.verified_queries import rows_from_submit_result

_KNOWN = frozenset(DEMO_TABLES)
_UNSURE_ASK = re.compile(
    r"\b(worry about|is this good|just give me)\b",
    re.I,
)


@dataclass(frozen=True)
class QueryPlan:
    measure: str
    group_by: tuple[tuple[str, str], ...] = ()
    filters: tuple[tuple[str, str, str, Any], ...] = ()
    via: dict[str, str] | None = None
    limit: int | None = 50


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


def parse_compute_plan(payload: dict[str, Any] | None) -> str:
    """Return miss | unsure | plan. Plan body is payload['query_plan'] when plan."""
    if not isinstance(payload, dict):
        return "miss"
    if payload.get("unsure") is True or payload.get("abstain") is True:
        return "unsure"
    plan = payload.get("query_plan")
    if isinstance(plan, dict) and str(plan.get("measure") or "").strip():
        return "plan"
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
    return QueryPlan(measure=measure, group_by=group_by, filters=filters, via=via, limit=lim)


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
) -> dict[str, Any]:
    env = build_answer_envelope(
        answer_id="ans_gen01_abstain",
        text=(
            "I cannot certify an ontology-grounded query for that question, "
            "so I am not executing one."
        ),
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
    return env


def _l2_envelope(
    *,
    sql: str,
    result: Any,
    question: str,
    space_id: str | None,
    session_id: str | None,
    audit_id: str,
    notes: Sequence[str],
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
    assert_envelope_valid(env)
    return env


def path_miss_envelope(
    question: str,
    reason: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Isolated A/B miss: ABSTAIN, do not mix Cortex/pack into the other lane."""
    return _abstain(question, reason, space_id=space_id, session_id=session_id)


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
    Cortex compute miss may bind_plan when ``bind_on_miss`` (isolated gen lane).
    Product path leaves miss as None so Cortex certified ask still runs.
    Explicit compute unsure is not overridden. File-grounded asks skip.
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
    if kind == "unsure":
        return _abstain(q, "compute abstained (unsure)", space_id=space_id, session_id=session_id)
    if kind != "plan":
        # Isolated gen (ask_path=generative): bind from retrieved ontology.
        # Product path must miss into Cortex.ask so certified VQ/L0 still run.
        if not bind_on_miss:
            return None
        payload = bind_plan(q, ctx)
        kind = parse_compute_plan(payload)
        if kind == "unsure":
            return _abstain(
                q, "retrieve bind abstained (unsure)",
                space_id=space_id, session_id=session_id,
            )
        if kind != "plan":
            return None
        fallback_note = "compute_fallback:bind_plan"

    assert isinstance(payload, dict)
    plan = plan_from_payload(payload)
    if plan is None:
        return _abstain(q, "query_plan was not typed", space_id=space_id, session_id=session_id)
    if onto is None or not onto.verified:
        return _abstain(q, "ontology_unverified", space_id=space_id, session_id=session_id)

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
            space_id=space_id, session_id=session_id,
        )
    if not isinstance(compiled, CompiledQuery):
        return _abstain(q, "compile_failed", space_id=space_id, session_id=session_id)
    if compiled.existential:
        return _abstain(
            q, "existential many-to-many filter: ask path will not choose a reading",
            space_id=space_id, session_id=session_id,
        )

    why = validate_compiled_sql(compiled.sql, grantable=allowed, warehouse=lake)
    if why:
        return _abstain(q, f"validate:{why}", space_id=space_id, session_id=session_id)

    try:
        result = submit(compiled.sql)
    except Exception:  # noqa: BLE001
        return _abstain(q, "submit_failed", space_id=space_id, session_id=session_id)
    if getattr(result, "ok", None) is False or getattr(result, "output", None) is None:
        return _abstain(q, "submit_had_no_rows", space_id=space_id, session_id=session_id)
    run_id = str(getattr(result, "run_id", None) or "")
    try:
        led = ledger_append({"sql": compiled.sql, "run_id": run_id})
    except Exception:  # noqa: BLE001
        return _abstain(q, "ledger_append_failed", space_id=space_id, session_id=session_id)
    entry_id = getattr(led, "entry_id", None) if led is not None else None
    led_hash = getattr(led, "hash", None)
    if not (isinstance(entry_id, str) and entry_id.strip()):
        return _abstain(q, "ledger_entry_missing", space_id=space_id, session_id=session_id)
    if not (isinstance(led_hash, str) and led_hash.strip()) or led_hash == entry_id:
        return _abstain(q, "ledger_hash_missing", space_id=space_id, session_id=session_id)
    return _l2_envelope(
        sql=compiled.sql,
        result=result,
        question=q,
        space_id=space_id,
        session_id=session_id,
        audit_id=entry_id.strip(),
        notes=tuple([*compiled.notes, *([fallback_note] if fallback_note else [])]),
    )
