"""Ask back when a question has more than one reading. Default off.

``DMS_AI_CLARIFY`` off: callers do not enter this module.

On, one OpenVault call with role ``reason`` reads the question, the ontology
slice, the schema slice, and verified examples. It returns
``answerable``, ``ambiguous``, or ``unanswerable``. Ambiguous, or a missing
slot, becomes a clarify block with 2 to 4 readings. Each preview is static-
checked and not executed. A pick re-checks grants and sends the chosen
reading through the normal ask path. The preview is not the answer.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ON = frozenset({"1", "true", "yes", "on"})
CLARIFY_ENV = "DMS_AI_CLARIFY"
ROLE_REASON = "reason"
_CATEGORIES = frozenset({"answerable", "ambiguous", "unanswerable"})
_LEASE = frozenset(
    {
        "ServingLeaseCap",
        "ServingLeaseQueueFull",
        "ServingWaitCancelled",
        "ServingDeadlineExceeded",
        "ServingDeadlineReserve",
        "OpenVaultTokenError",
    }
)
_STORE_CAP = 32
_LOCK = threading.Lock()
_PENDING: dict[str, dict[str, Any]] = {}
_ORDER: list[str] = []


def ai_clarify_enabled(env: os._Environ[str] | None = None) -> bool:
    """``DMS_AI_CLARIFY``: off unless set to a true token."""
    source = os.environ if env is None else env
    return source.get(CLARIFY_ENV, "").strip().lower() in _ON


@dataclass
class Pick:
    """A refusal envelope, or a question the caller must ask on the normal path."""

    envelope: dict[str, Any] | None = None
    question: str | None = None
    space_id: str | None = None
    session_id: str | None = None
    tables: list[str] | None = None
    ran: dict[str, Any] | None = None


def clarify_before_sql(
    question: str,
    *,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    ontology: Any,
    space_id: str | None,
    session_id: str | None,
    embedder: Callable[[list[str]], Any] | None = None,
) -> dict[str, Any] | str | None:
    """One understand call.

    ``"answerable"`` means the caller continues with the phrase gates off.
    ``None`` means the model sent no verdict, so the caller keeps those gates.
    A dict is a clarify, an unanswerable reconfirm, or an ungranted refusal.
    Ungranted is never a clarify.
    """
    payload = _call(
        compute,
        {
            "sql_vote_prompt": _understand_prompt(
                question,
                ontology=ontology,
                warehouse=warehouse,
                grantable=grantable,
                dialect=dialect,
                space_id=space_id,
                embedder=embedder,
            ),
            "sql_vote_kind": "understand",
            "route_hint": ROLE_REASON,
        },
    )
    verdict = parse_understand(payload if isinstance(payload, dict) else None)
    # No structured verdict: the caller keeps its existing gates. A silent
    # model is not permission to serve.
    if verdict is None:
        return None
    if verdict["category"] == "answerable" and not verdict["missing"]:
        return "answerable"
    if verdict["category"] == "unanswerable" or not verdict["readings"]:
        return _not_in_data(
            question,
            ontology=ontology,
            grantable=grantable,
            space_id=space_id,
            session_id=session_id,
        )
    checked = _previews(
        verdict["readings"],
        compute=compute,
        grantable=grantable,
        dialect=dialect,
    )
    granted = [item for item in checked if item["gap"] is None]
    refused = [item for item in checked if item["gap"] is not None]
    if not granted:
        if refused:
            return _ungranted(question, space_id=space_id, session_id=session_id)
        return _not_in_data(
            question,
            ontology=ontology,
            grantable=grantable,
            space_id=space_id,
            session_id=session_id,
        )
    return _clarify_envelope(
        question,
        granted,
        space_id=space_id,
        session_id=session_id,
        grantable=grantable,
        dialect=dialect,
    )


def parse_understand(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Structured verdict, or None when the payload is not one.

    A JSON object in ``query_sql`` is a verdict, not SQL, and is cleared.
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("clarify_verdict")
    if isinstance(raw, str):
        raw = _json_object(raw)
    if not isinstance(raw, dict):
        sql = str(payload.get("query_sql") or "").strip()
        parsed = _json_object(sql) if sql.startswith("{") else None
        if isinstance(parsed, dict) and parsed.get("category") is not None:
            payload["query_sql"] = ""
            raw = parsed
        else:
            return None
    category = str(raw.get("category") or "").strip().lower()
    if category not in _CATEGORIES:
        return None
    readings: list[dict[str, Any]] = []
    for item in raw.get("readings") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        readings.append(
            {
                "id": _reading_id(item.get("id"), len(readings) + 1),
                "text": text,
                "slots": _slots(item.get("slots")),
            }
        )
        if len(readings) == 4:
            break
    missing: list[str] = []
    for item in raw.get("missing") or []:
        if isinstance(item, str) and item.strip():
            missing.append(item.strip()[:40])
        if len(missing) == 6:
            break
    return {"category": category, "readings": readings, "missing": missing}


def peek_clarify(clarify_id: str) -> dict[str, Any] | None:
    with _LOCK:
        found = _PENDING.get(str(clarify_id or ""))
    return dict(found) if isinstance(found, dict) else None


def resolve_pick(
    clarify_id: str,
    *,
    reading_id: str | None,
    text: str | None,
    grantable: set[str],
    dialect: str,
) -> Pick:
    """Grant-check a stored reading, or take one free-text question.

    A refusal keeps the card so another reading can be picked. A question
    consumes the card. The preview SQL is not returned as the thing to run.
    """
    with _LOCK:
        pending = _PENDING.get(str(clarify_id or ""))
        if pending is None:
            raise KeyError(clarify_id)
        snapshot = dict(pending)
        readings = list(pending.get("readings") or [])
    typed = " ".join(str(text or "").split())
    if typed:
        if snapshot.get("free_text_ok") is not True:
            raise ValueError("free text already used")
        _drop(clarify_id)
        return Pick(
            question=typed,
            space_id=_str_or_none(snapshot.get("space_id")),
            session_id=_str_or_none(snapshot.get("session_id")),
            tables=_tables(snapshot.get("tables")),
        )
    chosen = None
    for item in readings:
        if isinstance(item, dict) and str(item.get("id") or "") == str(reading_id or ""):
            chosen = item
            break
    if chosen is None:
        raise ValueError("unknown reading")
    gap = _static_gap(str(chosen.get("sql_preview") or ""), grantable, dialect)
    if gap:
        return Pick(
            envelope=_ungranted(
                str(snapshot.get("question") or ""),
                space_id=_str_or_none(snapshot.get("space_id")),
                session_id=_str_or_none(snapshot.get("session_id")),
            )
        )
    _drop(clarify_id)
    return Pick(
        question=_bound_question(chosen),
        space_id=_str_or_none(snapshot.get("space_id")),
        session_id=_str_or_none(snapshot.get("session_id")),
        tables=_tables(snapshot.get("tables")),
    )


def run_pick(
    clarify_id: str,
    *,
    reading_id: str | None,
    text: str | None,
    grantable: set[str],
    dialect: str,
    ask: Callable[..., dict[str, Any]] | None = None,
) -> Pick:
    """``resolve_pick``, then ``ask`` when the pick selected a question."""
    picked = resolve_pick(
        clarify_id,
        reading_id=reading_id,
        text=text,
        grantable=grantable,
        dialect=dialect,
    )
    if picked.question and ask is not None:
        picked.ran = ask(
            picked.question,
            space_id=picked.space_id,
            session_id=picked.session_id,
            tables=picked.tables,
        )
    return picked


def _understand_prompt(
    question: str,
    *,
    ontology: Any,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    space_id: str | None,
    embedder: Callable[[list[str]], Any] | None,
) -> str:
    body = (
        "Classify this question against the schema and ontology. "
        "Return a JSON object and nothing else. "
        "category is answerable, ambiguous, or unanswerable. "
        "readings is a list of 2 to 4 objects with id, text, and slots. "
        "text is one plain line describing that reading. "
        "missing is a list of slot names the question does not fix. "
        "Do not write SQL.\n"
        f"Ontology:\n{_ontology_slice(ontology)}\n"
        f"Schema:\n{_schema_slice(warehouse, grantable)}\n"
        f"Examples:\n{_examples(question, space_id, warehouse, grantable, dialect, embedder)}"
    )
    return f"{body}\nQuestion:\n{question}"


def _preview_prompt(text: str, slots: dict[str, str]) -> str:
    slot_line = json.dumps(slots, sort_keys=True) if slots else "{}"
    return (
        "Write one SELECT for this reading. Use only granted tables and columns. "
        f"Return SQL only.\nSlots:\n{slot_line}\nQuestion:\n{text}"
    )


def _previews(
    readings: list[dict[str, Any]],
    *,
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    grantable: set[str],
    dialect: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in readings[:4]:
        payload = _call(
            compute,
            {
                "sql_vote_prompt": _preview_prompt(str(item["text"]), dict(item["slots"])),
                "sql_vote_kind": "preview",
            },
        )
        sql = _sql_text(payload if isinstance(payload, dict) else None)
        gap = _static_gap(sql, grantable, dialect) if sql else "no_sql"
        out.append({**item, "sql_preview": sql, "gap": gap})
    return out


def _clarify_envelope(
    question: str,
    readings: list[dict[str, Any]],
    *,
    space_id: str | None,
    session_id: str | None,
    grantable: set[str],
    dialect: str,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid, attach_clarify_block

    clarify_id = "clr_" + uuid.uuid4().hex[:16]
    lines = ["Which reading should I answer?"]
    lines.extend(f"{index}. {item['text']}" for index, item in enumerate(readings, 1))
    env = build_abstain(
        reason="clarify_ambiguous",
        ticket_reason="clarify_ambiguous",
        question=question,
        stage="generative",
        answer_id="ans_clarify",
        text="\n".join(lines),
        abstain_reason="clarify_ambiguous",
        rows=[],
        sql_used=None,
        assumptions=["clarify_ambiguous"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="needs_clarification",
    )
    attach_clarify_block(
        env,
        question=question,
        readings=readings,
        clarify_id=clarify_id,
    )
    assert_envelope_valid(env)
    stored = []
    for item in readings:
        stored.append(
            {
                "id": item["id"],
                "text": item["text"],
                "slots": dict(item["slots"]),
                "sql_preview": item["sql_preview"],
            }
        )
    _save(
        clarify_id,
        {
            "question": question,
            "readings": stored,
            "space_id": space_id,
            "session_id": session_id,
            "tables": sorted(grantable),
            "dialect": dialect,
            "free_text_ok": True,
        },
    )
    return env


def _not_in_data(
    question: str,
    *,
    ontology: Any,
    grantable: set[str],
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    available = _available(ontology, grantable)
    shown = ", ".join(available) if available else "nothing granted"
    env = build_abstain(
        reason="not_in_data",
        ticket_reason="not_in_data",
        question=question,
        stage="generative",
        answer_id="ans_not_in_data",
        text=f"This Space cannot answer that. Available: {shown}.",
        abstain_reason="not_in_data",
        rows=[],
        sql_used=None,
        assumptions=["not_in_data", "available:" + shown],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
    )
    env["reconfirm"] = {
        "reason_code": "not_in_data",
        "reason_text": env.get("text") or "",
        "suggested_question": "",
        "available": available,
    }
    assert_envelope_valid(env)
    return env


def _ungranted(
    question: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    env = build_abstain(
        reason="ungranted",
        ticket_reason="ungranted",
        question=question,
        stage="generative",
        answer_id="ans_clarify_ungranted",
        text="That reading uses a table this Space does not grant, so I am not running it.",
        abstain_reason="ungranted",
        rows=[],
        sql_used=None,
        assumptions=["ungranted"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
    )
    assert_envelope_valid(env)
    return env


def _static_gap(sql: str, grantable: set[str], dialect: str) -> str | None:
    from dms_executor.grant_struct import serve_gap

    if not str(sql or "").strip():
        return "no_sql"
    return serve_gap(sql, grantable=set(grantable), dialect=dialect or None)


def _sql_text(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    if payload.get("clarify_verdict") is not None:
        return ""
    sql = str(payload.get("query_sql") or "").strip()
    if not sql or sql.startswith("{"):
        return ""
    return sql


def _call(
    compute: Callable[[dict[str, Any]], dict[str, Any] | None],
    ctx: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        return compute(ctx)
    except Exception as exc:
        if type(exc).__name__ in _LEASE:
            raise
        return None


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _reading_id(raw: Any, index: int) -> str:
    text = str(raw or "").strip()
    if text and len(text) <= 16 and text.replace("_", "").isalnum():
        return text
    return f"r{index}"


def _slots(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in list(raw.items())[:8]:
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(val, bool) or not isinstance(val, (str, int, float)):
            continue
        out[key.strip()[:40]] = str(val)[:80]
    return out


def _ontology_slice(ontology: Any) -> str:
    if ontology is None:
        return "{}"
    measures = []
    for name, measure in list(getattr(ontology, "measures", {}).items())[:12]:
        measures.append(
            {
                "id": str(name),
                "grain": str(getattr(measure, "grain", "") or ""),
            }
        )
    objects = []
    for name, obj in list(getattr(ontology, "objects", {}).items())[:12]:
        objects.append(
            {
                "name": str(name),
                "relation": str(getattr(obj, "relation", "") or ""),
            }
        )
    return json.dumps({"measures": measures, "objects": objects}, sort_keys=True)[:2000]


def _schema_slice(warehouse: Path | None, grantable: set[str]) -> str:
    if warehouse is None or not Path(warehouse).is_file():
        return "{}"
    granted = {name.casefold() for name in grantable}
    try:
        from dms_executor.demo_warehouse import connect_serving

        con = connect_serving(Path(warehouse))
        try:
            rows = con.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return "{}"
    tables: dict[str, list[str]] = {}
    for table, column in rows:
        if str(table).casefold() not in granted:
            continue
        cols = tables.setdefault(str(table), [])
        if len(cols) < 6:
            cols.append(str(column))
        if len(tables) > 6 and len(cols) >= 6:
            break
    kept = [{"name": name, "columns": cols} for name, cols in sorted(tables.items())[:6]]
    return json.dumps({"tables": kept}, sort_keys=True)[:2000]


def _examples(
    question: str,
    space_id: str | None,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
    embedder: Callable[[list[str]], Any] | None,
) -> str:
    try:
        from dms_executor.verified_queries import examples_text, retrieve_verified_examples

        rows = retrieve_verified_examples(
            question,
            space_id=space_id,
            warehouse=warehouse,
            grantable=grantable,
            dialect=dialect,
            embedder=embedder,
        )
        return examples_text(rows[:3])[:2000]
    except Exception as exc:
        if type(exc).__name__ in _LEASE:
            raise
        return ""


def _available(ontology: Any, grantable: set[str]) -> list[str]:
    names: list[str] = []
    if ontology is not None:
        for name in list(getattr(ontology, "measures", {}))[:8]:
            names.append(str(name))
    for table in sorted(grantable):
        if table not in names:
            names.append(table)
        if len(names) >= 8:
            break
    return names[:8]


def _bound_question(reading: dict[str, Any]) -> str:
    text = str(reading.get("text") or "")
    slots = reading.get("slots") if isinstance(reading.get("slots"), dict) else {}
    if not slots:
        return text
    return "slots:\n" + json.dumps(slots, sort_keys=True) + "\nQuestion:\n" + text


def _save(clarify_id: str, pending: dict[str, Any]) -> None:
    with _LOCK:
        _PENDING[clarify_id] = pending
        _ORDER.append(clarify_id)
        while len(_ORDER) > _STORE_CAP:
            old = _ORDER.pop(0)
            _PENDING.pop(old, None)


def _drop(clarify_id: str) -> None:
    with _LOCK:
        _PENDING.pop(str(clarify_id or ""), None)


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _tables(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str)]
