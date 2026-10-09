"""One confirm step after the ask pipeline would abstain.

The ladder (generate, execute, check, retry) stays in front of this module.
This module does not write SQL. It asks the same OpenVault free-key model
for a plain reason and one suggested question, or it refuses.

Flag ``DMS_ASK_RECONFIRM`` defaults off. Off means this module is not called.

Direct refusal, and only these, skip the suggestion: an ungranted table, a
person or PII or private column, a destructive or write statement, and
``sql_dialect_unknown``. Those come from the grant, mask, and gate results
already on the attempt. ``serving_deadline_exceeded``, ``serving_lease_cap``,
and ``serving_deadline_reserve`` get the same confirm step as any other
stuck ask. Lease cap is a real capacity hit. The reserve means the ask
arrived too late to finish in time. Those two do not share a sentence.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from dms_core.ids import mint_id
from dms_core.pii import column_is_pii, fail_closed_mask_payload

from dms_executor.abstain import build_abstain
from dms_executor.envelope import assert_envelope_valid

_TTL_S = 900
_SAMPLE_CAP = 8
_TEXT_TYPES = frozenset(
    {
        "VARCHAR",
        "CHAR",
        "BPCHAR",
        "TEXT",
        "STRING",
        "BLOB",
        "JSON",
        "UUID",
    }
)
# Checker and gate heads. Not question phrases.
_HARD = frozenset(
    {
        "ungranted",
        "ungranted_table",
        "hostile_sql",
        "statement_not_allowed",
        "pii_column",
        "private_column",
        "sql_dialect_unknown",
        "destructive",
    }
)
_CAPACITY = frozenset(
    {
        "serving_deadline_exceeded",
        "serving_deadline_reserve",
        "serving_lease_cap",
    }
)
_CAPACITY_TEXT = {
    "serving_deadline_exceeded": (
        "This took too long on our side, so I stopped before answering."
    ),
    # Real capacity hit. Not the late-arrival reserve.
    "serving_lease_cap": (
        "The server is at capacity right now, so I stopped before answering."
    ),
    # Arrived too late to start. Not a capacity sentence. #420 owns the raise.
    "serving_deadline_reserve": (
        "This ask came in too late to finish in time, so I stopped before answering."
    ),
}
_CAPACITY_SAY = {
    "serving_deadline_exceeded": (
        "This stop took too long on our side. Say that in the reason."
    ),
    "serving_lease_cap": (
        "The server is at capacity right now. Say that in the reason."
    ),
    "serving_deadline_reserve": (
        "This ask arrived too late to finish in time. Say that in the reason."
    ),
}
_NOT_FOUND = "Not found in the database."
_STUCK = "I could not answer that from the data this Space can read."
_DIALECT = "I can't answer this because the database dialect is not set up."


def reconfirm_enabled() -> bool:
    raw = os.environ.get("DMS_ASK_RECONFIRM", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def dialect_known(warehouse: Path | None) -> bool:
    """True when the extract dialect parses a trivial statement.

    A blank or unusable dialect is ``sql_dialect_unknown``. This does not
    pick a fallback dialect.
    """
    from dms_executor.sql_currency import normalize_sql
    from dms_executor.sql_loop import extract_dialect

    name = str(extract_dialect(warehouse) or "").strip()
    if not name:
        return False
    return normalize_sql("SELECT 1", name) is not None


def serving_capacity_reason(payload: dict[str, Any] | None) -> str | None:
    """Serving stop carried on a generate payload, or None.

    Deadline, reserve, and lease cap are our limit, not a user refusal.
    The caller abstains with the code, and the confirm step explains it.
    The reserve is a late arrival. It is not the lease-cap sentence.
    """
    if not isinstance(payload, dict):
        return None
    raw = str(payload.get("insights_fail") or payload.get("serving_stop") or "").strip()
    head = raw.split(":", 1)[0].strip().lower()
    if head in _CAPACITY:
        return head
    return None


def pii_column_reason(sql: str, dialect: str) -> str | None:
    """``pii_column`` when cited columns fail the mask. Name signal only."""
    for table, column in _cited_columns(sql, dialect):
        try:
            flagged = column_is_pii(column, (), table=table or None)
        except Exception:
            flagged = True
        if flagged:
            return "pii_column"
    return None


def hard_refusal_code(env: dict[str, Any]) -> str | None:
    """A direct-refusal head already on the attempt, or None.

    Reads reason codes the grant, mask, and gate checks wrote. Does not
    read the question.
    """
    blob = _reason_blob(env)
    for token in re.split(r"[^a-z0-9_]+", blob.lower()):
        if token in _HARD:
            return token
    return None


def dialect_abstain(
    question: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Setup gap. One pipeline-failure ticket. No suggestion."""
    env = build_abstain(
        reason="sql_dialect_unknown",
        question=question,
        sql=None,
        retries=0,
        stage="pipeline",
        abstain_reason="sql_dialect_unknown",
        answer_id="ans_dialect_unknown",
        text=_DIALECT,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=["sql_dialect_unknown"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
    )
    assert_envelope_valid(env)
    return env


def offer_reconfirm(
    env: dict[str, Any],
    *,
    store: dict[str, Any],
    model: Any,
    warehouse: Path | None,
    grantable: set[str],
    space_id: str | None,
    session_id: str | None,
    original: str,
) -> dict[str, Any] | None:
    """Confirm envelope, or None to keep ``env``.

    None when the flag is off, the envelope is not an abstain, it is already
    a clarify or confirm, or the attempt is a direct refusal.
    """
    if not reconfirm_enabled():
        return None
    if not isinstance(env, dict) or not env.get("abstained"):
        return None
    status = str(env.get("status") or "")
    if status in {"clarify", "confirm"}:
        return None
    if hard_refusal_code(env):
        return scrub_ungranted(env, warehouse=warehouse, grantable=grantable)
    code = _safe_code(env)
    try:
        prompt = _prompt(
            original=original,
            code=code,
            warehouse=warehouse,
            grantable=grantable,
        )
        raw = model(prompt) if model is not None else None
    except Exception:
        raw = None
    reason, suggestion = _parsed(raw)
    reason_s = _visible(reason)
    suggestion_s = _visible(suggestion)
    if code in _CAPACITY:
        if not reason_s:
            reason_s = _CAPACITY_TEXT[code]
        if not suggestion_s:
            suggestion_s = _visible(original) or _STUCK
    if not reason_s or not suggestion_s:
        return None
    hidden = _ungranted_names(warehouse, grantable)
    reason_s = _strip_names(reason_s, hidden)
    suggestion_s = _strip_names(suggestion_s, hidden)
    if code in _CAPACITY and not reason_s:
        reason_s = _CAPACITY_TEXT[code]
    if not reason_s or not suggestion_s:
        return None
    confirm_id = mint_id("clr")
    out = _confirm_envelope(
        confirm_id=confirm_id,
        reason=reason_s,
        suggestion=suggestion_s,
        original=original,
        space_id=space_id,
        session_id=session_id,
        code=code,
    )
    store[confirm_id] = {
        "confirm_id": confirm_id,
        "suggested_question": suggestion_s,
        "original": original,
        "space_id": space_id,
        "session_id": session_id,
        "created": time.time(),
    }
    return scrub_ungranted(out, warehouse=warehouse, grantable=grantable)


def resolve_reconfirm(
    store: dict[str, Any],
    *,
    confirm_id: str,
    choice: str | None,
    space_id: str | None,
    session_id: str | None,
    fallback_question: str,
) -> dict[str, Any] | str:
    """A stored question on yes, or an abstain envelope.

    Yes returns the stored suggestion. The caller runs it through the
    pipeline. No returns the not-found abstain.
    """
    from dms_executor.ask_clarify import minted_clarify_id

    if not minted_clarify_id(confirm_id):
        return _plain_abstain(
            "confirm_unknown",
            "That confirmation is not known. Ask the question again.",
            question=fallback_question,
            space_id=space_id,
            session_id=session_id,
        )
    attempt = store.get(confirm_id)
    if not isinstance(attempt, dict):
        return _plain_abstain(
            "confirm_unknown",
            "That confirmation is not known. Ask the question again.",
            question=fallback_question,
            space_id=space_id,
            session_id=session_id,
        )
    age = time.time() - float(attempt.get("created") or 0)
    if age > _TTL_S:
        store.pop(confirm_id, None)
        return _plain_abstain(
            "confirm_expired",
            "That confirmation expired. Ask the question again.",
            question=fallback_question,
            space_id=space_id,
            session_id=session_id,
        )
    picked = str(choice or "").strip().lower()
    if picked == "no":
        store.pop(confirm_id, None)
        return _plain_abstain(
            "not_found",
            _NOT_FOUND,
            question=str(attempt.get("original") or fallback_question),
            space_id=space_id,
            session_id=session_id,
        )
    if picked != "yes":
        return _plain_abstain(
            "confirm_unknown",
            "That confirmation is not known. Ask the question again.",
            question=fallback_question,
            space_id=space_id,
            session_id=session_id,
        )
    suggestion = str(attempt.get("suggested_question") or "").strip()
    store.pop(confirm_id, None)
    if not suggestion:
        return _plain_abstain(
            "confirm_unknown",
            "That confirmation is not known. Ask the question again.",
            question=fallback_question,
            space_id=space_id,
            session_id=session_id,
        )
    return suggestion


def scrub_ungranted(
    env: dict[str, Any],
    *,
    warehouse: Path | None,
    grantable: set[str],
) -> dict[str, Any]:
    """Drop ungranted table names from user-visible strings."""
    names = _ungranted_names(warehouse, grantable)
    if not names:
        return env
    return _scrub_obj(env, names)


def _plain_abstain(
    reason: str,
    text: str,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    env = build_abstain(
        reason=reason,
        question=question,
        sql=None,
        retries=0,
        stage="reconfirm" if reason == "not_found" else "confirm",
        abstain_reason=reason,
        answer_id=f"ans_{reason}",
        text=text,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[reason],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
    )
    assert_envelope_valid(env)
    return env


def _confirm_envelope(
    *,
    confirm_id: str,
    reason: str,
    suggestion: str,
    original: str,
    space_id: str | None,
    session_id: str | None,
    code: str,
) -> dict[str, Any]:
    env = build_abstain(
        reason="reconfirm",
        question=original,
        sql=None,
        retries=0,
        stage="reconfirm",
        abstain_reason="reconfirm",
        answer_id=f"ans_{confirm_id}",
        text=reason,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=["reconfirm"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="confirm",
    )
    env["status"] = "confirm"
    env["confirm_id"] = confirm_id
    env["confirm_reason"] = reason
    env["suggested_question"] = suggestion
    env["original_question"] = _visible(original) or ""
    if code in _CAPACITY:
        env["confirm_code"] = code
    assert_envelope_valid(env)
    return env


def _reason_blob(env: dict[str, Any]) -> str:
    parts: list[str] = []
    raw = env.get("abstain_reason")
    if isinstance(raw, str):
        parts.append(raw)
    for item in env.get("assumptions") or []:
        parts.append(str(item))
    return " ".join(parts)


def _safe_code(env: dict[str, Any]) -> str:
    blob = _reason_blob(env).lower()
    for token in re.split(r"[^a-z0-9_]+", blob):
        if token in _CAPACITY:
            return token
    for token in re.split(r"[^a-z0-9_]+", blob):
        if token and token not in _HARD and re.fullmatch(r"[a-z][a-z0-9_]*", token):
            if token in {
                "abstain",
                "no_sql",
                "unknown_measure",
                "loop_exhausted",
                "empty_result_unverified",
                "checker",
                "db_error",
                "warehouse_missing",
            }:
                return token
    return "abstain"


def _parsed(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    if "suggested_question" in payload or "reason" in payload:
        return str(payload.get("reason") or ""), str(payload.get("suggested_question") or "")
    raw = payload.get("raw")
    if not isinstance(raw, str):
        return "", ""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = re.sub(r"(?i)^json", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("reason") or ""), str(data.get("suggested_question") or "")


def _visible(text: str | None) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    try:
        got = fail_closed_mask_payload(text=raw, rows=[])
    except Exception:
        return ""
    if not isinstance(got, dict):
        return ""
    out = got.get("text")
    if not isinstance(out, str):
        return ""
    return _drop_sql(out.strip())


def _drop_sql(text: str) -> str:
    """Drop a reason that is itself a statement. Leave plain sentences."""
    from dms_executor.sql_currency import normalize_sql
    from dms_executor.sql_loop import extract_dialect

    if not text:
        return ""
    dialect = str(extract_dialect(None) or "").strip()
    if dialect and normalize_sql(text, dialect) is not None and _looks_like_sql(text):
        return ""
    kept = [line for line in text.splitlines() if not _looks_like_sql(line.strip())]
    return "\n".join(kept).strip()


def _looks_like_sql(text: str) -> bool:
    return bool(re.match(r"(?is)^(with|select|insert|update|delete|drop|create)\b", text))


def _prompt(
    *,
    original: str,
    code: str,
    warehouse: Path | None,
    grantable: set[str],
) -> str:
    shown = _visible(original)
    lines = [
        "The ask could not be answered.",
        f"Stop code: {code}.",
        "Write JSON with two keys: reason, suggested_question.",
        "reason is one short plain sentence. No SQL. No database error text.",
        "No person, passport, name, or contact values.",
        "suggested_question is one question the granted schema can answer.",
        "Use only the granted columns listed below.",
    ]
    if code in _CAPACITY:
        lines.append(_CAPACITY_SAY[code])
        lines.append("Suggest either retrying the ask or one narrower question.")
    lines.append(f"Ask: {shown}")
    lines.extend(_schema_lines(warehouse, grantable))
    return "\n".join(lines)


def _schema_lines(warehouse: Path | None, grantable: set[str]) -> list[str]:
    lines = ["Granted schema:"]
    hidden = _ungranted_names(warehouse, grantable)
    for table, column, type_name in _columns(warehouse):
        if table not in grantable:
            continue
        if _named(table, hidden) or _named(column, hidden):
            continue
        try:
            private = column_is_pii(column, (), table=table)
        except Exception:
            private = True
        line = f"{table}.{column} {type_name}"
        if private or _text_type(type_name):
            lines.append(line)
            continue
        samples = _samples(warehouse, table, column)
        if samples:
            lines.append(line + " samples: " + ", ".join(samples))
        else:
            lines.append(line)
    return lines


def _text_type(type_name: str) -> bool:
    head = type_name.strip().upper().split("(", 1)[0].strip()
    return head in _TEXT_TYPES


def _columns(warehouse: Path | None) -> list[tuple[str, str, str]]:
    if warehouse is None or not Path(warehouse).is_file():
        return []
    from dms_executor.demo_warehouse import connect_file
    from dms_executor.semantic_retrieve import _safe_ident

    try:
        con = connect_file(Path(warehouse))
    except Exception:
        return []
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'main'"
        ).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    out: list[tuple[str, str, str]] = []
    for table_name, column_name, data_type in rows:
        table = _safe_ident(str(table_name))
        column = _safe_ident(str(column_name))
        if table and column:
            out.append((table, column, str(data_type or "")))
    return out


def _samples(warehouse: Path | None, table: str, column: str) -> list[str]:
    from dms_executor.semantic_retrieve import _safe_ident

    if warehouse is None or not _safe_ident(table) or not _safe_ident(column):
        return []
    from dms_executor.demo_warehouse import connect_file

    try:
        con = connect_file(Path(warehouse))
    except Exception:
        return []
    try:
        fetched = con.execute(
            f"SELECT DISTINCT CAST({column} AS VARCHAR) FROM {table} "
            f"WHERE {column} IS NOT NULL LIMIT {_SAMPLE_CAP}"
        ).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    raw = [str(row[0]) for row in fetched if row and row[0] is not None]
    if not raw:
        return []
    try:
        got = fail_closed_mask_payload(text=" | ".join(raw), rows=[])
    except Exception:
        return []
    if not isinstance(got, dict):
        return []
    text = got.get("text")
    if not isinstance(text, str) or not text.strip():
        return []
    if text.strip().startswith("DMSMASK_") and " | " not in text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def _ungranted_names(warehouse: Path | None, grantable: set[str]) -> set[str]:
    names: set[str] = set()
    for table, _column, _type_name in _columns(warehouse):
        if table not in grantable and len(table) >= 4:
            names.add(table)
    return names


def _named(value: str, names: set[str]) -> bool:
    folded = value.casefold()
    return any(name.casefold() == folded for name in names)


def _strip_names(text: str, names: set[str]) -> str:
    out = text
    hit = False
    for name in sorted(names, key=len, reverse=True):
        updated = re.sub(
            rf"(?i)(?<![A-Za-z0-9_])[`'\"]?(?:[A-Za-z_][A-Za-z0-9_]*\.)?"
            rf"{re.escape(name)}[`'\"]?(?![A-Za-z0-9_])",
            "",
            out,
        )
        if updated != out:
            hit = True
            out = updated
    if not hit:
        return text
    return " ".join(out.split()).strip()


def _scrub_obj(value: Any, names: set[str]) -> Any:
    if isinstance(value, str):
        return _strip_names(value, names)
    if isinstance(value, dict):
        return {_scrub_obj(key, names): _scrub_obj(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_obj(item, names) for item in value]
    return value


def _cited_columns(sql: str, dialect: str) -> list[tuple[str, str]]:
    from sqlglot import exp, parse_one

    name = str(dialect or "").strip()
    if not name:
        return []
    try:
        tree = parse_one(sql or "", read=name)
    except Exception:
        return []
    if tree is None:
        return []
    found: list[tuple[str, str]] = []
    for column in tree.find_all(exp.Column):
        col = str(column.name or "").strip()
        table = str(column.table or "").strip()
        if col:
            found.append((table, col))
    return found
