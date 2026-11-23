"""Ask reconfirm. The model writes the closest question. No canned question.

A ladder that ends without a checked answer returns ``reconfirm`` on the
envelope: why it would abstain, the question the model wrote, and a token.
Yes runs that question through the normal ask ladder. When
``DMS_RECONFIRM_RUN_HELD`` is on and the pending entry holds validated
SQL, Yes runs that SQL after the grant check and the read-only check.
No answers ``not found in the database`` and appends one pipeline-failure
record.

Ungranted tables, PII, and destructive SQL stay a direct abstain. They do
not get a reconfirm.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

NOT_FOUND = "not found in the database"
_LOCK = threading.Lock()
_PENDING: dict[str, dict[str, Any]] = {}
_HOLDS: dict[str, dict[str, Any]] = {}
_DECLINES = 0
# ponytail: process dict, oldest dropped at 64. Upgrade: tie the hold to the ask deadline.
_HOLD_CAP = 64

Complete = Callable[[str], str]


def reconfirm_run_held_enabled() -> bool:
    """True only when ``DMS_RECONFIRM_RUN_HELD`` is the digit 1. Unset is off."""
    return os.environ.get("DMS_RECONFIRM_RUN_HELD", "0").strip() == "1"


def _reset_reconfirm() -> None:
    global _DECLINES
    with _LOCK:
        _PENDING.clear()
        _HOLDS.clear()
        _DECLINES = 0


def decline_count() -> int:
    with _LOCK:
        return _DECLINES


def pipeline_failure_jsonl_path() -> Path:
    raw = os.environ.get("DMS_PIPELINE_FAILURE_JSONL")
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "dms-pipeline-failures.jsonl"


def _parsed(text: str) -> list[Any]:
    from sqlglot import parse

    from dms_executor.demo_warehouse import SERVING_DIALECT

    try:
        return [tree for tree in parse(text, read=SERVING_DIALECT) if tree is not None]
    except Exception:
        return []


def _one_mutating(text: str) -> bool:
    """True when sqlglot types the text as one write statement.

    An alias or a column fragment is not a statement. Prose that does not
    parse as one write is not destructive.
    """
    from sqlglot import exp

    trees = _parsed(text)
    if len(trees) != 1:
        return False
    return isinstance(
        trees[0],
        (
            exp.Drop,
            exp.Delete,
            exp.Update,
            exp.Insert,
            exp.Alter,
            exp.Create,
            exp.TruncateTable,
            exp.Merge,
            exp.Copy,
        ),
    )


def question_is_destructive(text: str) -> bool:
    """True when the text is one write statement, including after prose."""
    raw = str(text or "").strip().rstrip(";")
    if not raw:
        return False
    if _one_mutating(raw):
        return True
    # ponytail: word tails, only a tail sqlglot types as one write. Ceiling:
    # a long question. Upgrade: tokenize and parse from the write token.
    parts = raw.split()
    for index in range(1, len(parts)):
        if _one_mutating(" ".join(parts[index:]).strip().rstrip(";")):
            return True
    return False


def question_requests_pii(question: str) -> bool:
    """True when a word in the question is a PII column name to the classifier."""
    from dms_core.pii import classify_column

    for raw in str(question or "").split():
        word = raw.strip("?.!\"'():;,").casefold()
        if len(word) < 3:
            continue
        try:
            kind = classify_column(word, ())
        except Exception:
            return True
        if kind is not None:
            return True
    return False


def _refusal_codes(env: dict[str, Any]) -> list[str]:
    """Structured codes already on the envelope. Not the customer sentence."""
    found: list[str] = []
    raw = env.get("abstain_reason")
    if isinstance(raw, str) and raw.strip():
        found.append(raw.strip())
    assumptions = env.get("assumptions") or []
    if isinstance(assumptions, list):
        for item in assumptions:
            text = str(item).strip()
            if text.startswith("validate:"):
                code = text[len("validate:") :].strip()
                if code:
                    found.append(code)
            elif text.startswith("GEN-01:"):
                code = text[len("GEN-01:") :].strip()
                if code:
                    found.append(code)
                # The armed lane writes ``GEN-01: validate:<code>``. ``validate``
                # names the stage, not the refusal: read the code under it too,
                # so ``validate:ungranted`` stays a direct abstain (F114).
                if code.startswith("validate:"):
                    inner = code[len("validate:") :].strip()
                    if inner:
                        found.append(inner)
    return found


def direct_abstain_reason(env: dict[str, Any], question: str) -> str | None:
    """A closed reason that must stay an abstain, or None when reconfirm applies.

    Ungranted, PII, and a non-read statement. The grant code is the one
    ``serve_gap`` / the pack grant check already produced.
    """
    from dms_executor.pipeline_failure import reconfirm_disposition

    if question_requests_pii(question):
        return "pii"
    if question_is_destructive(question):
        return "hostile_sql"
    sql_used = env.get("sql_used")
    if isinstance(sql_used, str) and question_is_destructive(sql_used):
        return "hostile_sql"
    for code in _refusal_codes(env):
        if reconfirm_disposition(code) == "direct":
            return code
    return None


def _unchecked(env: dict[str, Any]) -> bool:
    if env.get("abstained") is True or str(env.get("badge") or "") == "ABSTAIN":
        return True
    return str(env.get("route") or "").casefold() in {
        "abstain",
        "blocked",
        "needs_clarification",
        "refused",
    }


def ontology_text(space_id: str | None) -> str:
    """Ontology the model may use. Space catalog when it exists, else the spine."""
    lines: list[str] = []
    try:
        from dms_executor.space_ontology import load_space_ontology

        onto = load_space_ontology(space_id)
    except Exception:
        onto = None
    if onto is not None:
        for name, measure in onto.measures.items():
            lines.append(
                " ".join(
                    part
                    for part in (
                        str(name),
                        str(getattr(measure, "expression", "") or ""),
                        str(getattr(measure, "description", "") or ""),
                    )
                    if part
                )
            )
        for name in onto.objects:
            lines.append(f"object {name}")
        if lines:
            return "\n".join(lines)
    spine = Path(__file__).with_name("ontology_spine.yaml")
    try:
        return spine.read_text(encoding="utf-8")
    except OSError:
        return ""


def granted_schema_names(space_id: str | None, tables: list[str] | None) -> list[str]:
    """Table names for the prompt. Static grants only, so this does not open the lake.

    ponytail: uploaded tables are missing here. A catalog that does not take the
    serving lease can replace this.
    """
    picked = [str(name) for name in (tables or []) if str(name).strip()]
    if picked:
        return picked
    from dms_executor.demo_grants import (
        DEMO_SPACE_GRANTS,
        canonical_space_id,
        company_default_tables,
    )

    if not space_id:
        return list(company_default_tables())
    entry = DEMO_SPACE_GRANTS.get(canonical_space_id(space_id))
    if entry is None:
        return []
    return list(entry[1])


def schema_text(_warehouse: Path | None, grantable: list[str] | None) -> str:
    """Granted table names. Does not open the lake: a serving holder would block the ask."""
    names = [str(name) for name in (grantable or []) if str(name).strip()]
    return "\n".join(names)


def write_suggested_question(
    *,
    ontology: str,
    schema: str,
    reason: str,
    complete: Complete,
) -> str:
    """The model writes one question. The return value is that text."""
    prompt = (
        "Write the closest question the granted schema can answer.\n"
        "Use only the ontology, the granted schema, and the failure reason.\n"
        "Reply with that one question.\n\n"
        f"Ontology:\n{ontology}\n\n"
        f"Granted schema:\n{schema}\n\n"
        f"Failure reason:\n{reason}\n"
    )
    raw = complete(prompt)
    if not isinstance(raw, str):
        return ""
    stripped = raw.strip()
    if not stripped:
        return ""
    return stripped.splitlines()[0].strip()


def _text_from_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""
    for key in ("suggested_question", "answer", "text"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    gen = payload.get("generative")
    if isinstance(gen, dict):
        for key in ("answer", "text"):
            val = gen.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _existing_suggestion(env: dict[str, Any]) -> str:
    """A closest question the ladder already wrote. Empty when there is none."""
    raw = env.get("reconfirm")
    if not isinstance(raw, dict):
        return ""
    for key in ("suggested_question", "closest_question"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def complete_with_model(cortex: Any, prompt: str) -> str:
    """One completion. Not ``compute_insights``: that is the answer ladder.

    A missing completer writes nothing. No fill-in question. When this ask
    has already used the generate cap, this does not call again.
    """
    from cortex_client.compute import insights_call_cap, recorded_model_calls

    if recorded_model_calls() >= insights_call_cap():
        return ""
    fn = getattr(cortex, "complete", None)
    if not callable(fn):
        return ""
    try:
        return _text_from_payload(fn(prompt))
    except Exception:
        return ""


def _save_pending(pending: dict[str, Any]) -> str:
    from dms_core.ids import mint_id

    token = mint_id("rcf")
    with _LOCK:
        _PENDING[token] = pending
    return token


def peek_pending(token: str) -> dict[str, Any] | None:
    with _LOCK:
        found = _PENDING.get(str(token or ""))
        return dict(found) if isinstance(found, dict) else None


def take_pending(token: str) -> dict[str, Any] | None:
    with _LOCK:
        return _PENDING.pop(str(token or ""), None)


def park_def_validate_hold(
    env: dict[str, Any],
    *,
    sql: str,
    grantable: set[str],
) -> None:
    """Remember SQL for a def_validate reconfirm. Rows and secrets stay out.

    The client envelope keeps the definition. The SQL lives in this process
    until ``apply_reconfirm`` copies it onto the pending entry.
    """
    block = env.get("reconfirm")
    if not isinstance(block, dict):
        return
    statement = str(sql or "").strip()
    definition = block.get("definition")
    code = block.get("code")
    if not statement or not isinstance(definition, str) or not definition.strip():
        return
    if not isinstance(code, str) or not code.strip():
        return
    from dms_core.ids import mint_id

    hold_id = mint_id("hld")
    payload = {
        "sql": statement,
        "grantable": sorted({str(name) for name in grantable if str(name).strip()}),
        "definition": definition.strip(),
        "reason_code": code.strip(),
    }
    with _LOCK:
        if len(_HOLDS) >= _HOLD_CAP:
            _HOLDS.pop(next(iter(_HOLDS)))
        _HOLDS[hold_id] = payload
    block["hold_id"] = hold_id


def take_hold(hold_id: str) -> dict[str, Any] | None:
    with _LOCK:
        found = _HOLDS.pop(str(hold_id or ""), None)
    return dict(found) if isinstance(found, dict) else None


def held_ready(pending: dict[str, Any]) -> bool:
    """True when the pending entry has SQL to run, not only a question to re-ask."""
    sql = pending.get("sql")
    grantable = pending.get("grantable")
    code = pending.get("reason_code")
    definition = pending.get("definition")
    return (
        isinstance(sql, str)
        and bool(sql.strip())
        and isinstance(grantable, list)
        and isinstance(code, str)
        and bool(code.strip())
        and isinstance(definition, str)
        and bool(definition.strip())
    )


def _direct_envelope(env: dict[str, Any], question: str, reason: str) -> dict[str, Any]:
    if _unchecked(env):
        # Leave the ladder's reason in place. A short head would hide it.
        out = dict(env)
        out.pop("reconfirm", None)
        return out
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    text = {
        "pii": "That asks for personal data, so I am not answering it.",
        "hostile_sql": "That is not a read of the database, so I am not running it.",
        "statement_not_allowed": "That is not a read of the database, so I am not running it.",
    }.get(reason, "This Space is not granted that table, so I am not answering it.")
    built = build_abstain(
        reason=reason,
        question=question,
        stage="reconfirm_direct",
        answer_id="ans_direct_abstain",
        text=text,
        abstain_reason=reason,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[reason],
        space_id=env.get("space_id"),
        session_id=env.get("session_id"),
        ask_mode=str(env.get("ask_mode") or "live"),
        route="abstain",
    )
    assert_envelope_valid(built)
    return built


def apply_reconfirm(
    env: dict[str, Any],
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    tables: list[str] | None,
    ask_path: str | None,
    ontology: str,
    schema: str,
    complete: Complete,
) -> dict[str, Any]:
    """Attach ``reconfirm`` when the ladder did not check an answer."""
    if not isinstance(env, dict):
        return env
    direct = direct_abstain_reason(env, question)
    if direct is not None:
        return _direct_envelope(env, question, direct)
    if not _unchecked(env):
        return env
    from dms_executor.abstain import _envelope_reason

    reason = _envelope_reason(env)
    reason_text = str(env.get("text") or "").strip() or reason
    # The in-budget reading already named a question. Do not spend another call.
    suggested = _existing_suggestion(env)
    if not suggested:
        from cortex_client.compute import insights_call_cap, recorded_model_calls

        if recorded_model_calls() >= insights_call_cap():
            suggested = ""
        else:
            try:
                suggested = write_suggested_question(
                    ontology=ontology,
                    schema=schema,
                    reason=reason,
                    complete=complete,
                )
            except Exception:
                suggested = ""
    prior = env.get("reconfirm")
    hold: dict[str, Any] | None = None
    definition = ""
    if isinstance(prior, dict):
        raw_def = prior.get("definition")
        if isinstance(raw_def, str) and raw_def.strip():
            definition = raw_def.strip()
        hold_id = prior.get("hold_id")
        if isinstance(hold_id, str) and hold_id:
            hold = take_hold(hold_id)
            if hold is not None and isinstance(hold.get("definition"), str):
                definition = str(hold["definition"]).strip() or definition
    pending: dict[str, Any] = {
        "suggested_question": suggested,
        "question": question,
        "reason_code": reason,
        "space_id": space_id,
        "session_id": session_id,
        "tables": list(tables) if tables else None,
        "ask_path": ask_path,
    }
    cortex_origin = isinstance(prior, dict) and prior.get("origin") == "cortex"
    if cortex_origin:
        # Yes and No go back to Cortex as plan_sql_confirm (contract 1.5.0+).
        pending["cortex_confirm"] = True
        why = prior.get("why") if isinstance(prior, dict) else None
        if isinstance(why, str) and why.strip():
            reason_text = why.strip()
    if hold is not None:
        pending["sql"] = hold["sql"]
        pending["grantable"] = list(hold["grantable"])
        pending["definition"] = hold["definition"]
        pending["reason_code"] = hold["reason_code"]
    token = _save_pending(pending)
    out = dict(env)
    block: dict[str, Any] = {
        "reason_code": reason,
        "reason_text": reason_text,
        "suggested_question": suggested,
        "token": token,
    }
    if isinstance(prior, dict):
        closest = prior.get("closest_question")
        if isinstance(closest, str) and closest.strip():
            block["closest_question"] = closest.strip()
    if definition:
        block["definition"] = definition
    out["reconfirm"] = block
    from cortex_client.compute import recorded_model_calls

    out["model_calls"] = recorded_model_calls()
    return out


def record_decline(
    *,
    reason: str,
    question: str,
    sql: str | None,
    ask_id: str,
) -> dict[str, Any]:
    """Append one JSONL pipeline-failure record and increment the counter."""
    global _DECLINES
    from dms_executor.pipeline_failure import log_pipeline_failure_ticket

    ticket_id = log_pipeline_failure_ticket(
        reason=reason,
        question=question,
        sql=sql,
        retries=0,
        stage="reconfirm_no",
        ask_id=ask_id,
    )
    with _LOCK:
        _DECLINES += 1
        count = _DECLINES
    record = {
        "ask_id": ask_id or "unspecified",
        "count": count,
        "reason": reason,
        "stage": "reconfirm_no",
        "ticket_id": ticket_id,
    }
    path = pipeline_failure_jsonl_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def decline_envelope(pending: dict[str, Any]) -> dict[str, Any]:
    """No. The sentence is fixed. The ticket is the JSONL record."""
    from dms_core.ids import mint_id

    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    ask_id = mint_id("ans")
    record_decline(
        reason=str(pending.get("reason_code") or "abstain"),
        question=str(pending.get("question") or ""),
        sql=None,
        ask_id=ask_id,
    )
    env = build_abstain(
        reason=str(pending.get("reason_code") or "abstain"),
        question=str(pending.get("question") or ""),
        stage="reconfirm_no",
        ask_id=ask_id,
        answer_id=ask_id,
        text=NOT_FOUND,
        abstain_reason=str(pending.get("reason_code") or "abstain"),
        values=[],
        rows=[],
        sql_used=None,
        assumptions=["reconfirm declined"],
        space_id=pending.get("space_id"),
        session_id=pending.get("session_id"),
        ask_mode="live",
        route="abstain",
    )
    assert_envelope_valid(env)
    return env
