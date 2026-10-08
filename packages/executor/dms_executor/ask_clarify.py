"""ASK-GUIDE-01. Guided readings on an ambiguity abstain. Default off.

``DMS_ASK_CLARIFY`` unset or 0 leaves every ask envelope untouched. On, an
ambiguity abstain gains ``clarify.options`` compiled from the verified
ontology and validated (grants, read-only, EXPLAIN). Nothing in that list
is executed. A confirmed pick is recompiled here and handed to
``_submit_validated``, the same door a normal ontology ask uses before
``L2_VALIDATED``.

#392 (``rank_window_unhandled_terms``) and GEN-INTENT
(``intent_shape_mismatch``) are not on this main. They apply to a pick
once they are checked inside ``_submit_validated`` or
``build_answer_envelope`` (which ``_l2_envelope`` already calls). There is
no second guard chain.

Swap: founder turns the env on only after a tier:full check. This module
never sets the env.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from dms_core.ask import AskServiceError, ask_clarify_enabled

from dms_executor.generative_ask import (
    _abstain,
    _submit_validated,
    load_verified_ontology,
    validate_compiled_sql,
)
from dms_executor.ontology import CompiledQuery, Ontology, Refusal

# Prefix match, one list. Some heads are not produced on this main yet
# (ambiguous_measure:none, rank_window_unhandled_terms, intent_shape_mismatch).
AMBIGUITY_REASON_PREFIXES: tuple[str, ...] = (
    "question is too vague or time-unbounded to ground",
    "ambiguous_measure:",
    "unknown_measure:",
    "rank_window_unhandled_terms",
    "intent_shape_mismatch",
    "exact-match miss",
)

_MAX_OPTIONS = 4
_TIME_GRAINS = frozenset({"day", "week", "month", "quarter", "year"})
# ponytail: first verified measures, one entity each, stop at 4. Ceiling:
# a wide ontology wants a question-token rank. Upgrade: rank slots from the
# verified ontology only. Do not read the score pack, oracles, or query skills.


def named_abstain_reason(env: dict[str, Any]) -> str:
    """Same order as the Studio reader: abstain_reason, then GEN-01 note, then text."""
    direct = env.get("abstain_reason")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    notes = env.get("assumptions") or []
    if isinstance(notes, str):
        notes = [notes]
    for note in notes:
        text = str(note).strip()
        if text.startswith("GEN-01: "):
            return text[len("GEN-01: ") :]
    for note in notes:
        text = str(note).strip()
        if text:
            return text
    first = str(env.get("text") or "").strip().split("\n", 1)[0]
    return first[:160]


def reason_is_ambiguity(reason: str) -> bool:
    text = str(reason or "").strip()
    if not text:
        return False
    return any(text.startswith(prefix) for prefix in AMBIGUITY_REASON_PREFIXES)


def public_plan(raw: Any) -> dict[str, Any]:
    """Slots only. Client SQL text is dropped."""
    if not isinstance(raw, dict):
        return {"measure": "", "entity": None, "filter": None, "time_grain": None}
    entity = raw.get("entity")
    entity_out: dict[str, str] | None = None
    if isinstance(entity, dict) and entity.get("object") and entity.get("column"):
        entity_out = {"object": str(entity["object"]), "column": str(entity["column"])}
    grain = raw.get("time_grain")
    grain_out = str(grain) if isinstance(grain, str) and grain.strip() else None
    filt = raw.get("filter")
    return {
        "measure": str(raw.get("measure") or "").strip(),
        "entity": entity_out,
        "filter": None if filt is None else filt,
        "time_grain": grain_out,
    }


def option_id(plan: dict[str, Any]) -> str:
    blob = json.dumps(public_plan(plan), sort_keys=True, separators=(",", ":"), default=str)
    return "opt_" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def _sentence(spec_description: str, spec_name: str, grain_name: str, plan: dict[str, Any]) -> str:
    label = (spec_description or spec_name).strip().split(".")[0].strip() or spec_name
    if label:
        label = label[0].lower() + label[1:]
    text = f"I'll compute {label}"
    time_grain = plan.get("time_grain")
    entity = plan.get("entity")
    if isinstance(time_grain, str) and time_grain:
        text += f" by {time_grain}"
    elif isinstance(entity, dict) and entity.get("object"):
        column = str(entity.get("column") or "").replace("_", " ")
        obj = str(entity["object"])
        # Name the column so the qualifier guard sees the same token the SQL groups by.
        text += f" by {obj} {column}" if column and column not in obj else f" by {obj}"
    text += f" from {grain_name}."
    return text


def _column_for(onto: Ontology, obj_name: str, cols: dict[Any, Any]) -> str | None:
    obj = onto.objects.get(obj_name)
    if obj is None:
        return None
    known = cols.get(obj_name)
    if isinstance(known, set) and known:
        extra = sorted(known - set(obj.key))
        if extra:
            return extra[0]
    if obj.key:
        return obj.key[0]
    return None


def _plan_for(measure: str, slot: tuple[str, str] | None) -> dict[str, Any]:
    entity = None
    grain = None
    if slot is not None:
        obj_name, column = slot
        entity = {"object": obj_name, "column": column}
        if column in _TIME_GRAINS:
            grain = column
    return {
        "measure": measure,
        "entity": entity,
        "filter": None,
        "time_grain": grain,
    }


def compile_plan(onto: Ontology, plan: dict[str, Any]) -> CompiledQuery | Refusal:
    """Recompile server-side from slots. Ignores any SQL the client sent."""
    slots = public_plan(plan)
    group: list[tuple[str, str]] = []
    entity = slots.get("entity")
    if isinstance(entity, dict):
        group.append((entity["object"], entity["column"]))
    grain = slots.get("time_grain")
    if isinstance(grain, str) and grain and not any(col == grain for _, col in group):
        if "day" in onto.objects:
            group.append(("day", grain))
    return onto.compile(
        slots["measure"],
        group_by=tuple(group),
        filters=(),
        limit=None,
    )


def build_options(
    onto: Ontology | None,
    *,
    grantable: set[str],
    warehouse: Path | None,
) -> list[dict[str, Any]]:
    """Up to 4 readings. Compile + validate. Do not execute."""
    if onto is None or not onto.verified:
        return []
    cols = onto.__dict__.get("_column_cache") or {}
    out: list[dict[str, Any]] = []
    allowed = set(grantable)
    for measure_name in sorted(onto.measures):
        spec = onto.measures[measure_name]
        slots: list[tuple[str, str] | None] = [None]
        for obj_name in sorted(onto.objects):
            if obj_name == spec.grain:
                continue
            column = _column_for(onto, obj_name, cols)
            if column:
                slots.append((obj_name, column))
            if len(slots) > 3:
                break
        for slot in slots:
            if len(out) >= _MAX_OPTIONS:
                return out
            plan = _plan_for(measure_name, slot)
            compiled = compile_plan(onto, plan)
            if not isinstance(compiled, CompiledQuery):
                continue
            why = validate_compiled_sql(
                compiled.sql, grantable=allowed, warehouse=warehouse
            )
            if why:
                continue
            text = _sentence(spec.description, spec.name, spec.grain, plan)
            out.append(
                {
                    "id": option_id(plan),
                    "plan": public_plan(plan),
                    "text": text,
                    "sql_preview": compiled.sql,
                }
            )
    return out


def _verified(warehouse: Path | None) -> Ontology | None:
    onto = load_verified_ontology(warehouse)
    if onto is None or not onto.verified:
        return None
    return onto


def resolve_lake(preferred: Path | None) -> Path | None:
    if preferred is not None and Path(preferred).is_file():
        return Path(preferred)
    from dms_executor.demo_warehouse import warehouse_path

    candidate = warehouse_path()
    return candidate if candidate.is_file() else None


def _tier1(env: dict[str, Any]) -> dict[str, Any] | None:
    values = env.get("values") or []
    if not isinstance(values, list):
        return None
    for item in values:
        if not isinstance(item, dict):
            continue
        raw = item.get("value")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        return {"label": str(item.get("label") or "value"), "value": raw}
    return None


def _tier3(onto: Ontology | None) -> list[str]:
    if onto is None or not onto.verified:
        return []
    lines: list[str] = []
    for name in sorted(onto.measures):
        spec = onto.measures[name]
        label = (spec.description or spec.name).strip().split(".")[0].strip() or spec.name
        lines.append(f"What is {label}?")
        if len(lines) >= 3:
            break
    if len(lines) < 2:
        for name in sorted(onto.objects):
            if name in {m.grain for m in onto.measures.values()}:
                continue
            lines.append(f"What is {lines[0][:-1] if lines else 'the measure'} by {name}?")
            if len(lines) >= 2:
                break
    if len(lines) < 2:
        return []
    return lines[:3]


def _row_fact(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in rows[:4]:
        bits = [f"{k}={v}" for k, v in row.items()]
        if bits:
            parts.append(", ".join(bits))
    if not parts:
        return ""
    return "Comparison from executed SQL: " + "; ".join(parts) + "."


def _tier2(
    onto: Ontology | None,
    *,
    main_sql: str | None,
    grantable: set[str],
    warehouse: Path | None,
    space_id: str | None,
    session_id: str | None,
    submit: Any,
    ledger_append: Any,
) -> dict[str, Any] | None:
    """One comparison reading through the same validate + guard door. Omit on any miss."""
    if onto is None or submit is None or ledger_append is None:
        return None
    options = build_options(onto, grantable=grantable, warehouse=warehouse)
    grouped = next((opt for opt in options if isinstance(opt["plan"].get("entity"), dict)), None)
    if grouped is None:
        return None
    compiled = compile_plan(onto, grouped["plan"])
    if not isinstance(compiled, CompiledQuery):
        return None
    # The main answer already ran this statement. A second copy is not a comparison.
    if " ".join(compiled.sql.split()) == " ".join(str(main_sql or "").split()):
        return None
    # Same sequence as a normal ontology ask: validate_compiled_sql, then
    # _submit_validated (unhonored qualifier, currency, ledger, envelope guards).
    env = run_validated_reading(
        compiled,
        question=str(grouped["text"]),
        grantable=grantable,
        warehouse=warehouse,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
    )
    if env.get("badge") != "L2_VALIDATED" or env.get("abstained"):
        return None
    rows = [r for r in (env.get("rows") or []) if isinstance(r, dict)]
    fact = _row_fact(rows)
    if not fact:
        return None
    return {"text": fact, "sql": str(env.get("sql_used") or compiled.sql), "rows": rows}


def build_insights(
    env: dict[str, Any],
    *,
    onto: Ontology | None,
    grantable: set[str],
    warehouse: Path | None,
    space_id: str | None,
    session_id: str | None,
    submit: Any,
    ledger_append: Any,
) -> dict[str, Any]:
    insights: dict[str, Any] = {}
    tier1 = _tier1(env)
    if tier1 is not None:
        insights["tier1"] = tier1
    tier2 = _tier2(
        onto,
        main_sql=env.get("sql_used") if isinstance(env.get("sql_used"), str) else None,
        grantable=grantable,
        warehouse=warehouse,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
    )
    if tier2 is not None:
        insights["tier2"] = tier2
    tier3 = _tier3(onto)
    if tier3:
        insights["tier3"] = tier3
    return insights


def run_validated_reading(
    compiled: CompiledQuery,
    *,
    question: str,
    grantable: set[str],
    warehouse: Path | None,
    space_id: str | None,
    session_id: str | None,
    submit: Any,
    ledger_append: Any,
) -> dict[str, Any]:
    """Grants / read-only / EXPLAIN, then the normal pre-L2 guard door."""
    why = validate_compiled_sql(
        compiled.sql, grantable=set(grantable), warehouse=warehouse
    )
    if why:
        return _abstain(
            question,
            f"validate:{why}",
            space_id=space_id,
            session_id=session_id,
        )
    return _submit_validated(
        compiled.sql,
        question=question,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
        notes=tuple(compiled.notes),
        plan_source="ontology_plan",
        measure=compiled.measure,
        coverage=compiled.coverage,
        where_paths=compiled.where_paths,
        warehouse=warehouse,
    )


def apply_ask_guide(
    env: dict[str, Any],
    *,
    warehouse: Path | None,
    grantable: set[str],
    space_id: str | None,
    session_id: str | None,
    submit: Any = None,
    ledger_append: Any = None,
) -> dict[str, Any]:
    """Flag off: return the same object, no ``clarify`` key.

    Flag on + ambiguity abstain: add ``clarify.options``.
    Flag on + answered: add insight tiers. Tier 2 only when its own SQL
    validates and executes. Non-ambiguity abstains gain nothing.
    """
    if not ask_clarify_enabled():
        return env
    if not isinstance(env, dict):
        return env
    abstained = bool(env.get("abstained")) or env.get("badge") == "ABSTAIN"
    onto = _verified(warehouse)
    if abstained:
        if not reason_is_ambiguity(named_abstain_reason(env)):
            return env
        options = build_options(onto, grantable=set(grantable), warehouse=warehouse)
        out = dict(env)
        out["clarify"] = {"options": options}
        return out
    insights = build_insights(
        env,
        onto=onto,
        grantable=set(grantable),
        warehouse=warehouse,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
    )
    if not insights:
        return env
    out = dict(env)
    out["insights"] = insights
    return out


def confirm_reading(
    *,
    option_id_value: str,
    plan: dict[str, Any],
    warehouse: Path | None,
    grantable: set[str],
    space_id: str | None,
    session_id: str | None,
    submit: Any,
    ledger_append: Any,
) -> dict[str, Any]:
    """Recompile the offered reading and run it through the normal ask door.

    Refuses when the flag is off. Never executes client SQL text.
    """
    if not ask_clarify_enabled():
        raise AskServiceError("clarify_disabled", "DMS_ASK_CLARIFY is off")
    onto = _verified(warehouse)
    if onto is None:
        return _abstain(
            "confirmed reading",
            "ontology_unverified",
            space_id=space_id,
            session_id=session_id,
        )
    offered = build_options(onto, grantable=set(grantable), warehouse=warehouse)
    client = public_plan(plan)
    match = next((opt for opt in offered if opt["id"] == option_id_value), None)
    if match is None or public_plan(match["plan"]) != client:
        raise AskServiceError(
            "clarify_plan_mismatch",
            "option id does not match a server reading",
        )
    compiled = compile_plan(onto, match["plan"])
    if isinstance(compiled, Refusal):
        return _abstain(
            str(match["text"]),
            f"{compiled.reason}: {compiled.detail}",
            space_id=space_id,
            session_id=session_id,
        )
    if not isinstance(compiled, CompiledQuery):
        return _abstain(
            str(match["text"]),
            "compile_failed",
            space_id=space_id,
            session_id=session_id,
        )
    env = run_validated_reading(
        compiled,
        question=str(match["text"]),
        grantable=set(grantable),
        warehouse=warehouse,
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
    )
    if env.get("abstained") or env.get("badge") == "ABSTAIN":
        return env
    guided = apply_ask_guide(
        env,
        warehouse=warehouse,
        grantable=set(grantable),
        space_id=space_id,
        session_id=session_id,
        submit=submit,
        ledger_append=ledger_append,
    )
    return guided


__all__ = [
    "AMBIGUITY_REASON_PREFIXES",
    "apply_ask_guide",
    "build_options",
    "confirm_reading",
    "named_abstain_reason",
    "option_id",
    "public_plan",
    "reason_is_ambiguity",
    "resolve_lake",
]
