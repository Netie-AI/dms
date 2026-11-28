"""DMS_LEARNED_MEMORY. Default off. The digit 1 only.

A Yes or Fix stores SQL only when this process already served that answer,
the confirmer is the asker, and the question and intent match that answer.
A Yes on a generic reading has a different question or no governed intent,
so it stores nothing.

Retrieved rows are few-shot ``<examples>``. An exact question with one
intent is re-executed live. Stored rows are not kept and are not served.
Two intents for one question clarify. Another asker's SQL is not retrieved.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FLAG = "DMS_LEARNED_MEMORY"
_EXAMPLE_CAP = 3
_LOCK = threading.Lock()


class MemoryDisabled(RuntimeError):
    """The flag is off. Nothing was stored."""


class MemoryRefused(ValueError):
    """The confirm did not bind. Nothing was stored."""


@dataclass
class _Governed:
    answer_id: str
    question: str
    intent: str
    sql: str
    asker: str
    space_id: str


@dataclass
class _Saved:
    question: str
    intent: str
    sql: str
    asker: str
    space_id: str


_GOVERNED: dict[str, _Governed] = {}
_SAVED: dict[tuple[str, str, str, str], _Saved] = {}


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``DMS_LEARNED_MEMORY`` is the digit 1."""
    source = os.environ if env is None else env
    return str(source.get(FLAG) or "").strip() == "1"


def reset() -> None:
    with _LOCK:
        _GOVERNED.clear()
        _SAVED.clear()


def intent_for(sql: str, dialect: str) -> str:
    """The answer's intent is its normalised SQL, not a reading of the question."""
    from dms_executor.sql_vote import normalize_sql

    return "answer:" + normalize_sql(sql or "", dialect or "")


def note_served(
    env: dict[str, Any],
    *,
    question: str,
    sql: str,
    asker: str | None,
    space_id: str | None,
    dialect: str,
) -> None:
    """Remember a governed answer so only that asker can confirm it.

    Flag off does not write and does not add a key.
    """
    if not enabled():
        return
    who = (asker or "").strip()
    asked = question or ""
    statement = (sql or "").strip()
    if not who or not asked or not statement:
        return
    answer_id = str(env.get("answer_id") or env.get("audit_id") or "").strip()
    if not answer_id:
        return
    intent = intent_for(statement, dialect)
    row = _Governed(
        answer_id=answer_id,
        question=asked,
        intent=intent,
        sql=statement,
        asker=who,
        space_id=(space_id or "").strip(),
    )
    with _LOCK:
        _GOVERNED[answer_id] = row
    env["learned_memory"] = {
        "answer_id": answer_id,
        "intent": intent,
        "asker": who,
        "question": asked,
    }


def _require() -> None:
    if not enabled():
        raise MemoryDisabled()


def _bound(answer_id: str, question: str, intent: str, confirmer: str) -> _Governed:
    _require()
    with _LOCK:
        rec = _GOVERNED.get((answer_id or "").strip())
    if rec is None:
        raise MemoryRefused("not_governed")
    who = (confirmer or "").strip()
    if not who or who != rec.asker:
        raise MemoryRefused("not_own_confirm")
    if (question or "") != rec.question:
        raise MemoryRefused("generic_reading")
    if not intent or intent != rec.intent:
        raise MemoryRefused("intent_mismatch")
    return rec


def _put(rec: _Governed, sql: str) -> None:
    row = _Saved(
        question=rec.question,
        intent=rec.intent,
        sql=sql,
        asker=rec.asker,
        space_id=rec.space_id,
    )
    key = (rec.space_id, rec.asker, rec.question, rec.intent)
    with _LOCK:
        _SAVED[key] = row


def confirm(
    *,
    answer_id: str,
    question: str,
    intent: str,
    confirmer: str,
) -> dict[str, Any]:
    """Yes. Stores the governed SQL. A generic reading stores nothing."""
    rec = _bound(answer_id, question, intent, confirmer)
    _put(rec, rec.sql)
    return {"saved": True, "answer_id": rec.answer_id, "sql": rec.sql}


def apply_fix(
    *,
    answer_id: str,
    question: str,
    intent: str,
    confirmer: str,
    sql: str,
    warehouse: Path,
    grantable: set[str],
    dialect: str,
    interpretation: str = "",
) -> dict[str, Any]:
    """Fix. Re-gates the edited SQL and runs it. The interpretation is ignored.

    The returned rows are the live run. Nothing stored is returned.
    """
    _ = interpretation
    rec = _bound(answer_id, question, intent, confirmer)
    from dms_executor.generative_ask import validate_compiled_sql
    from dms_executor.sql_loop import run_readonly

    statement = (sql or "").strip()
    why = validate_compiled_sql(
        statement,
        grantable=set(grantable),
        warehouse=warehouse,
        dialect=dialect,
    )
    if why:
        raise MemoryRefused(why)
    rows, err = run_readonly(statement, Path(warehouse))
    if err or rows is None:
        raise MemoryRefused(err or "submit_failed")
    _put(rec, statement)
    return {"saved": True, "sql": statement, "rows": rows}


def examples_context(
    question: str,
    *,
    space_id: str | None,
    asker: str | None,
) -> str:
    """Few-shot block for this asker. Empty when the flag is off."""
    _ = question
    if not enabled():
        return ""
    who = (asker or "").strip()
    sid = (space_id or "").strip()
    if not who:
        return ""
    with _LOCK:
        rows = [
            row
            for row in _SAVED.values()
            if row.asker == who and row.space_id == sid
        ]
    if not rows:
        return ""
    parts: list[str] = []
    for row in rows[:_EXAMPLE_CAP]:
        parts.append(f"Question: {row.question}\nSQL: {row.sql}")
    return "<examples>\n" + "\n".join(parts) + "\n</examples>"


def exact_action(
    question: str,
    *,
    space_id: str | None,
    asker: str | None,
) -> tuple[str, str]:
    """``('run', sql)``, ``('clarify', '')``, or ``('miss', '')``.

    Flag off is a miss. Two intents do not pick a SQL.
    """
    if not enabled():
        return ("miss", "")
    who = (asker or "").strip()
    sid = (space_id or "").strip()
    asked = question or ""
    if not who or not asked:
        return ("miss", "")
    with _LOCK:
        hits = [
            row
            for row in _SAVED.values()
            if row.asker == who and row.space_id == sid and row.question == asked
        ]
    if not hits:
        return ("miss", "")
    intents = {row.intent for row in hits}
    if len(intents) != 1:
        return ("clarify", "")
    return ("run", hits[0].sql)


def saved_sql(question: str, *, space_id: str | None, asker: str | None) -> list[str]:
    """Stored SQL texts for tests. Rows are not stored."""
    who = (asker or "").strip()
    sid = (space_id or "").strip()
    with _LOCK:
        return [
            row.sql
            for row in _SAVED.values()
            if row.asker == who and row.space_id == sid and row.question == (question or "")
        ]
