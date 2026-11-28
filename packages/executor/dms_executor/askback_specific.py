"""Ask-back readings that name this question. Flag default off.

``DMS_ASKBACK_SPECIFIC`` is the digit 1 only. Off, callers do not enter.

On, a reading is a model reply (OpenVault role ``reason``) for this question
and the granted schema. Each reading carries the SELECT it would run and the
rows from a read-only probe. A reading with no runnable SQL is not offered,
and neither is the canned Space filler. No runnable reading means a plain
refusal: grant more data or rephrase. There is no Yes button.

Yes runs that reading's SQL through ``validated_sql_reason`` and the compile
gate. The stored example uses the asked question.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

ENV = "DMS_ASKBACK_SPECIFIC"
ROLE = "reason"
_CLOSED_TEXT = (
    "I could not write a reading of that question on the granted schema. "
    "Grant more data or rephrase the question."
)
_RAISE = frozenset(
    {
        "ServingLeaseCap",
        "ServingLeaseQueueFull",
        "ServingWaitCancelled",
        "ServingDeadlineExceeded",
        "ServingDeadlineReserve",
        "OpenVaultTokenError",
    }
)
# ponytail: four offered readings. Upgrade: the clarify store cap.
_READING_CAP = 4


def enabled() -> bool:
    """True only when ``DMS_ASKBACK_SPECIFIC`` is the digit 1."""
    return os.environ.get(ENV, "0").strip() == "1"


def grounded_generate_prompt(turn: Any) -> str:
    """SQL instruction. Schema is already on it. Kept for callers that want the text."""
    return str(generate_request(turn)["sql_vote_prompt"])


def generate_request(turn: Any) -> dict[str, Any]:
    """SCALE_MISS generate body. Context is attached before the model is called.

    The question text carries the schema, because this call replaces the asked
    text. ``schema_context`` is the same prompt, so the insights shortlist is
    the granted schema. ``vq_examples`` is what the seam appends. A second
    copy of the schema in the question is the ceiling: the shortlist field is
    dropped when ``DMS_SCHEMA_CONTEXT`` is off. Upgrade: drop the question
    copy once that field is the only prompt the engine shows the model.
    """
    from cortex_client.insights import SCHEMA_CONTEXT_FIELD

    question = str(getattr(turn, "question", "") or "")
    schema, onto, examples = _writer_context(turn)
    parts = [
        "Write one SELECT that answers the question. "
        "Use only the granted schema. Return the SQL.",
    ]
    if schema:
        parts.append(schema)
    if onto:
        parts.append("Ontology:\n" + onto)
    parts.append(f"Question:\n{question}")
    ctx: dict[str, Any] = {
        "sql_vote_prompt": "\n".join(parts),
        "sql_vote_kind": "direct",
    }
    if schema:
        ctx[SCHEMA_CONTEXT_FIELD] = schema
    if examples:
        ctx["vq_examples"] = examples
    for key, value in _catalog_fields(turn).items():
        ctx.setdefault(key, value)
    return ctx


def _writer_context(turn: Any) -> tuple[str, str, str]:
    return _schema_prompt(turn), _ontology_prompt(turn), _examples_prompt(turn)


def _schema_prompt(turn: Any) -> str:
    """Granted schema prompt. Column names when the catalog cannot be built."""
    from dms_executor.schema_context import (
        build_schema_context,
        lookup_schema,
        schema_context_is_empty,
    )

    grantable = set(getattr(turn, "grantable", None) or ())
    warehouse = getattr(turn, "warehouse", None)
    path = warehouse if isinstance(warehouse, Path) else None
    dialect = str(getattr(turn, "dialect", "") or "") or None
    space_id = getattr(turn, "space_id", None)
    question = str(getattr(turn, "question", "") or "")
    ontology = getattr(turn, "ontology", None)
    try:
        described, _stamp = lookup_schema(path, grantable, dialect, space_id)
        built = build_schema_context(
            question,
            described,
            ontology=ontology,
            grantable=grantable,
            space_id=space_id,
        )
    except Exception as exc:
        if type(exc).__name__ in _RAISE:
            raise
        return schema_block(turn)
    if schema_context_is_empty(built.prompt):
        return schema_block(turn)
    return built.prompt


def _ontology_prompt(turn: Any) -> str:
    ontology = getattr(turn, "ontology", None)
    if ontology is None:
        return ""
    try:
        from dms_executor.shape_check import ontology_slice

        text = json.dumps(ontology_slice(ontology), sort_keys=True, default=str)
    except Exception as exc:
        if type(exc).__name__ in _RAISE:
            raise
        return ""
    if text in ("", "{}"):
        return ""
    return text[:4000]


def _examples_prompt(turn: Any) -> str:
    """Retrieved verified examples. Empty when that store has none for this ask."""
    from dms_executor.verified_queries import examples_text, retrieve_verified_examples

    question = str(getattr(turn, "question", "") or "")
    warehouse = getattr(turn, "warehouse", None)
    path = warehouse if isinstance(warehouse, Path) else None
    grantable = set(getattr(turn, "grantable", None) or ())
    dialect = str(getattr(turn, "dialect", "") or "") or None
    try:
        rows = retrieve_verified_examples(
            question,
            space_id=getattr(turn, "space_id", None),
            warehouse=path,
            grantable=grantable,
            dialect=dialect,
        )
    except Exception as exc:
        if type(exc).__name__ in _RAISE:
            raise
        return ""
    return examples_text(rows)


def _catalog_fields(turn: Any) -> dict[str, Any]:
    """Space ontology keys the insights body already accepts. Empty otherwise."""
    ontology = getattr(turn, "ontology", None)
    grantable = set(getattr(turn, "grantable", None) or ())
    try:
        from dms_executor.space_ontology import Ontology, space_catalog
    except Exception as exc:
        if type(exc).__name__ in _RAISE:
            raise
        return {}
    if not isinstance(ontology, Ontology):
        return {}
    try:
        block = space_catalog(ontology, [], grantable)
    except Exception as exc:
        if type(exc).__name__ in _RAISE:
            raise
        return {}
    return {
        key: block[key]
        for key in ("objects", "links", "measures")
        if key in block
    }


def schema_block(turn: Any) -> str:
    """Granted tables and columns. Names only when the lake cannot be read."""
    grantable = getattr(turn, "grantable", None)
    names = sorted({str(name) for name in (grantable or set()) if str(name).strip()})
    warehouse = getattr(turn, "warehouse", None)
    path = Path(warehouse) if isinstance(warehouse, Path) else None
    if path is None or not path.is_file() or not names:
        return "\n".join(names)
    wanted = {name.casefold() for name in names}
    try:
        from dms_executor.demo_warehouse import connect_serving

        con = connect_serving(path)
        try:
            rows = con.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:
        if type(exc).__name__ in _RAISE:
            raise
        return "\n".join(names)
    lines: list[str] = []
    seen: set[str] = set()
    for table, column in rows:
        label = str(table)
        if label.casefold() not in wanted:
            continue
        seen.add(label.casefold())
        lines.append(f"{label}.{column}")
    for name in names:
        if name.casefold() not in seen:
            lines.append(name)
    return "\n".join(lines)


def specific_askback(
    question: str,
    *,
    why: str,
    turn: Any,
    space_id: str | None,
    session_id: str | None,
    margin: bool,
) -> dict[str, Any]:
    """Model readings, or a closed card when none of them run."""
    from datetime import UTC, datetime

    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    payload = getattr(turn, "payload", None) if turn is not None else None
    blocked = _budget_blocked()
    readings_payload = None
    if turn is not None and not blocked:
        readings_payload = _call(
            turn,
            {
                "sql_vote_prompt": _readings_prompt(question, schema_block(turn)),
                "sql_vote_kind": "readings",
                "route_hint": ROLE,
            },
        )
    source = payload if isinstance(payload, dict) else readings_payload
    leg = classify_leg(source, margin=margin or blocked)
    runnable = _probe(turn, _parse_readings(readings_payload))
    as_of = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    legs = _stamp_legs(source, leg)
    if not runnable:
        env = build_abstain(
            reason=why,
            question=question,
            stage="generative",
            answer_id="ans_askback_specific",
            text=_CLOSED_TEXT,
            rows=[],
            sql_used=None,
            values=[],
            assumptions=[
                f"reconfirm: {why}",
                "askback_specific:closed",
                f"generate_outcome:{leg['outcome']}",
            ],
            as_of=as_of,
            space_id=space_id,
            session_id=session_id,
            ask_mode="live",
            route="abstain",
        )
        assert_envelope_valid(env)
        env["askback_specific"] = "closed"
        env["generate_legs"] = legs
        return env
    texts = [str(item["text"]) for item in runnable]
    text = "Which reading should I run? " + " | ".join(texts)
    env = build_abstain(
        reason=why,
        question=question,
        stage="generative",
        answer_id="ans_askback_specific",
        text=text,
        rows=[],
        sql_used=None,
        values=[],
        assumptions=[
            f"reconfirm: {why}",
            *[f"reading: {item}" for item in texts],
            "askback_specific",
        ],
        as_of=as_of,
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    assert_envelope_valid(env)
    grantable = getattr(turn, "grantable", None)
    warehouse = getattr(turn, "warehouse", None)
    env["reconfirm"] = {
        "why": why,
        "reason_code": why,
        "reason_text": text,
        "suggested_question": texts[0],
        "closest_question": texts[0],
        "question": question,
        "readings": runnable,
        "reading_sql": runnable[0]["sql"],
        "grantable": sorted(str(name) for name in (grantable or set()) if str(name).strip()),
        "warehouse": str(warehouse) if isinstance(warehouse, Path) else "",
        "askback_specific": True,
    }
    env["askback_specific"] = "open"
    env["generate_legs"] = legs
    return env


def classify_leg(payload: Any, *, margin: bool) -> dict[str, str]:
    """One generate leg that did not yield SQL. No question text is read."""
    leg: dict[str, str]
    if not isinstance(payload, dict):
        outcome = "over_budget" if margin else "error"
        leg = {"returned": "nothing", "outcome": outcome}
        if outcome == "error":
            leg["error_class"] = "missing_payload"
        if margin and outcome != "over_budget":
            leg["margin"] = "over_budget"
        return leg
    if payload.get("error_class") == "token_overflow":
        leg = {
            "returned": "nothing",
            "outcome": "token_overflow",
            "error_class": "token_overflow",
        }
    else:
        fail = payload.get("insights_fail")
        fail_s = fail.strip() if isinstance(fail, str) else ""
        sql = str(payload.get("query_sql") or "").strip()
        if fail_s.startswith("insights_timeout") or fail_s.startswith("insights_call_cap"):
            outcome = "error"
            error_class = fail_s.split(":", 1)[0]
        elif sql and not _parses(sql):
            outcome = "parse_failure"
            error_class = "parse_failure"
        else:
            outcome = "empty_text"
            error_class = fail_s or "empty_text"
        leg = {"returned": "nothing", "outcome": outcome, "error_class": error_class}
    if margin:
        leg["margin"] = "over_budget"
    return leg


def _readings_prompt(question: str, schema: str) -> str:
    return (
        "Reply with a JSON object and nothing else. "
        "The key readings is an array. "
        "Each item has text and sql. "
        "text is one reading of this question. "
        "sql is one read-only SELECT for that reading, using only the granted schema.\n"
        f"Granted schema:\n{schema}\n"
        f"Question:\n{question}"
    )


def _budget_blocked() -> bool:
    from cortex_client.ask_clock import generate_budget_left_s

    left = generate_budget_left_s()
    return left is not None and left <= 0


def _call(turn: Any, ctx: dict[str, Any]) -> dict[str, Any] | None:
    compute = getattr(turn, "compute", None)
    if not callable(compute):
        return None
    try:
        payload = compute(ctx)
    except Exception as exc:
        if type(exc).__name__ in _RAISE:
            raise
        return None
    return payload if isinstance(payload, dict) else None


def _canned() -> frozenset[str]:
    from dms_executor.scale_miss import _FILL

    return frozenset(_FILL)


def _parse_readings(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("readings")
    if not isinstance(raw, list):
        for key in ("query_sql", "text", "answer", "content"):
            text = payload.get(key)
            if isinstance(text, str):
                parsed = _json_dict(text)
                if isinstance(parsed, dict) and isinstance(parsed.get("readings"), list):
                    raw = parsed["readings"]
                    break
    if not isinstance(raw, list):
        return []
    canned = _canned()
    out: list[dict[str, str]] = []
    for item in raw:
        text = ""
        sql = ""
        if isinstance(item, dict):
            text = " ".join(str(item.get("text") or "").split())
            sql = str(item.get("sql") or "").strip()
        elif isinstance(item, str):
            text = " ".join(item.split())
        if not text or not sql or text in canned:
            continue
        out.append({"text": text, "sql": sql})
        if len(out) == _READING_CAP:
            break
    return out


def _probe(turn: Any, readings: list[dict[str, str]]) -> list[dict[str, Any]]:
    if turn is None or not readings:
        return []
    warehouse = getattr(turn, "warehouse", None)
    if not isinstance(warehouse, Path) or not warehouse.is_file():
        return []
    raw_grants = getattr(turn, "grantable", None) or set()
    grantable = {str(name) for name in raw_grants if str(name).strip()}
    dialect = str(getattr(turn, "dialect", "") or "") or None
    from dms_executor.generative_ask import validate_compiled_sql
    from dms_executor.sql_loop import run_readonly

    kept: list[dict[str, Any]] = []
    for item in readings:
        sql = item["sql"]
        try:
            why = validate_compiled_sql(
                sql,
                grantable=grantable,
                warehouse=warehouse,
                dialect=dialect,
            )
            if why:
                continue
            rows, err = run_readonly(sql, warehouse)
        except Exception as exc:
            if type(exc).__name__ in _RAISE:
                raise
            continue
        if err or rows is None:
            continue
        shown = [_json_row(row) for row in rows]
        kept.append(
            {
                "text": item["text"],
                "sql": sql,
                "row_count": len(shown),
                "rows": shown,
            }
        )
    return kept


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _cell(value) for key, value in row.items()}


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _parses(sql: str) -> bool:
    import sqlglot

    try:
        return sqlglot.parse_one(sql, read="duckdb") is not None
    except Exception:
        return False


def _json_dict(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _stamp_legs(payload: Any, leg: dict[str, str]) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("generate_legs"), dict):
        raw = payload["generate_legs"]
        items = raw.get("legs") if isinstance(raw.get("legs"), list) else []
        legs: list[dict[str, Any]] = []
        stamped = False
        for item in items:
            if not isinstance(item, dict):
                continue
            copy = dict(item)
            if not stamped and str(copy.get("returned") or "nothing") == "nothing":
                copy["outcome"] = leg["outcome"]
                if leg.get("error_class"):
                    copy["error_class"] = leg["error_class"]
                if leg.get("margin"):
                    copy["margin"] = leg["margin"]
                stamped = True
            legs.append(copy)
        if not legs:
            legs = [dict(leg)]
        elif not stamped:
            legs.append(dict(leg))
        return {"count": len(legs), "legs": legs}
    return {"count": 1, "legs": [dict(leg)]}
