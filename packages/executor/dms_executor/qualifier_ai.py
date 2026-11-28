"""BIRD-QUALIFIER-AI-01. Default off.

On, named_filter, time_filter, time_grain, ungrounded_qualifier, and
rank_window checks only produce notes. Every candidate is sent to OpenVault
role ``sql_verify``, notes or not. The call judges the SQL against the
question, the steward hint, the schema, EXPLAIN, and a LIMITed probe.
Not honored repairs once. Unsure reconfirms. No key, an error, an empty
reply, or 402/429 after OpenVault's own ladder is unsure, then a typed
reconfirm, and is not served. A literal the verifier cites must be in
the granted column.

Off, ``review_candidate`` returns the SQL unchanged and sends nothing.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlglot import exp, parse_one

from dms_executor.demo_warehouse import SERVING_DIALECT

QUALIFIER_AI_ENV = "DMS_QUALIFIER_AI"
ROLE_ALIAS = "sql_verify"
SCHEMA_NAME = "QualifierVerifyReply"
NOTE_KINDS = frozenset({"named_filter", "time_filter", "time_grain", "rank_window"})
_PROBE_LIMIT = 5
_FENCE = "```"
_VERDICTS = frozenset({"honored", "not_honored", "unsure"})
_RULE_ROUTES = frozenset({"ontology_compile", "ladder_rung"})
_DIRECT_HEADS = frozenset(
    {
        "ungranted",
        "statement_not_allowed",
        "multi_statement",
        "sql_not_analyzable",
        "sql_relation_not_granted",
    }
)
_SYSTEM = (
    "Check whether this SQL answers the question. "
    "You receive the question, an optional steward hint, the SQL, "
    "the schema of the tables the SQL reads, an EXPLAIN plan, "
    "a small read-only sample, and candidate notes. "
    "The notes are not a verdict. "
    "List each requirement you find. "
    "For each one set verdict to honored, not_honored, or unsure, "
    "and sql_fragment to the SQL that honors it, or null. "
    "When a requirement is a filter value, set column to table.column "
    "and literal to the value the SQL filters on. "
    "If you are not sure, verdict is unsure and suggested_question is "
    "a concrete question the user can confirm. "
    "Reply with one JSON object and nothing else. "
    "Keys are requirements and suggested_question. "
    "Do not name a model or a provider."
)
_MODEL_KEYS = frozenset(
    {
        "model",
        "model_id",
        "model_name",
        "served_model",
        "served_provider",
        "provider",
        "strict",
    }
)
_CONFIRM = "Confirm whether this SQL answers the question."
API_KEY_ENVS = ("OPENVAULT_API_KEY", "CORTEX_API_KEY")
API_KEY_PREFIX = "ov_"
URL_ENV = "OPENVAULT_URL"
CHAT_PATH = "/v1/chat/completions"

Transport = Callable[[dict[str, Any]], Any]
Repair = Callable[[str, str], str | None]
_TRANSPORT: ContextVar[Transport | None] = ContextVar("dms_qualifier_ai_transport", default=None)
_REPAIR: ContextVar[Repair | None] = ContextVar("dms_qualifier_ai_repair", default=None)


class QualifierCallError(RuntimeError):
    """The verifier call could not be sent. The message is a code, never a cell."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RequirementVerdict(BaseModel):
    """One requirement. The parser does not trust a coerce."""

    model_config = ConfigDict(extra="ignore")
    requirement: str
    verdict: str
    sql_fragment: str | None = None
    column: str | None = None
    literal: str | None = None


class QualifierVerifyReply(BaseModel):
    """Schema sent as ``response_format``."""

    model_config = ConfigDict(extra="ignore")
    requirements: list[RequirementVerdict] = []
    suggested_question: str | None = None


def qualifier_ai_enabled() -> bool:
    """True only when ``DMS_QUALIFIER_AI`` is the digit 1. Unset is off."""
    return os.environ.get(QUALIFIER_AI_ENV, "0").strip() == "1"


def is_candidate_note(reason: str | None) -> bool:
    """True when this reason is one of the checks that no longer decide."""
    text = str(reason or "").strip()
    if text.startswith("ungrounded_qualifier:"):
        return True
    if text.startswith("rank_window_unhandled_terms:"):
        return True
    if text.startswith("rank_window_entity_mismatch:"):
        return True
    if text == "rank_window_open_ended" or text.startswith("rank_window_open_ended:"):
        return True
    prefix = "unhonored_qualifier:"
    if not text.startswith(prefix):
        return False
    kind = text[len(prefix) :].split("=", 1)[0]
    return kind in NOTE_KINDS


def bind_qualifier_transport(transport: Transport) -> Token[Transport | None]:
    return _TRANSPORT.set(transport)


def reset_qualifier_transport(token: Token[Transport | None]) -> None:
    _TRANSPORT.reset(token)


def bind_qualifier_repair(repair: Repair) -> Token[Repair | None]:
    return _REPAIR.set(repair)


def reset_qualifier_repair(token: Token[Repair | None]) -> None:
    _REPAIR.reset(token)


def deciding_qualifier_reason(
    asked: str,
    sql: str,
    warehouse: Path | None,
    grantable: set[str] | None,
) -> str | None:
    """Qualifier reasons that still decide. The note kinds are skipped.

    A stated-value named filter is a note, collected by ``candidate_notes``.
    """
    from cortex_client.qualifiers import unhonored_qualifier_reason

    _ = warehouse, grantable
    return unhonored_qualifier_reason(asked, sql=sql, skip_kinds=NOTE_KINDS)


def candidate_notes(
    asked: str,
    sql: str | None,
    *,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    plan: dict[str, Any] | None = None,
) -> list[str]:
    """Reasons the lexical checks would have raised. They do not decide."""
    from cortex_client.qualifiers import (
        rank_window_shape_reason,
        unhonored_qualifier_reason,
    )

    found: list[str] = []
    shape = rank_window_shape_reason(asked)
    if is_candidate_note(shape):
        found.append(str(shape))
    gap = unhonored_qualifier_reason(asked, sql=sql, plan=plan)
    if is_candidate_note(gap) and gap not in found:
        found.append(str(gap))
    if warehouse is not None and isinstance(sql, str) and sql.strip():
        from dms_executor.stated_values import dropped_stated_value_reason

        try:
            stated = dropped_stated_value_reason(asked, sql, warehouse, grantable=grantable)
        except Exception:
            stated = None
        if is_candidate_note(stated) and stated not in found:
            found.append(str(stated))
    return found


def request_body(
    *,
    asked: str,
    hint: str,
    sql: str,
    schema: Sequence[Mapping[str, Any]],
    explain: str,
    sample_rows: Sequence[Mapping[str, Any]],
    notes: Sequence[str],
) -> dict[str, Any]:
    """OpenVault body. Role ``sql_verify``. No model id and no ``strict``."""
    user = {
        "question": asked if isinstance(asked, str) else "",
        "hint": hint if isinstance(hint, str) else "",
        "sql": sql if isinstance(sql, str) else "",
        "schema": [dict(item) for item in schema],
        "explain": explain if isinstance(explain, str) else "",
        "sample_rows": [dict(row) for row in sample_rows],
        "notes": [str(item) for item in notes if str(item).strip()],
    }
    body: dict[str, Any] = {
        "role": ROLE_ALIAS,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": json.dumps(user, sort_keys=True, separators=(",", ":"), default=str),
            },
        ],
        "schema": SCHEMA_NAME,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": SCHEMA_NAME,
                "schema": QualifierVerifyReply.model_json_schema(),
            },
        },
    }
    _reject_model_keys(body)
    return body


def review_candidate(
    asked: str,
    sql: str,
    *,
    hint: str = "",
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    space_id: str | None = None,
    session_id: str | None = None,
    served_route: str = "",
    extra_notes: Sequence[str] = (),
    transport: Transport | None = None,
    repair: Repair | None = None,
    allow_repair: bool = True,
    repaired: bool = False,
) -> dict[str, Any]:
    """Serve, reconfirm, or abstain. Flag off serves the SQL and calls nothing."""
    if not qualifier_ai_enabled():
        return _result("serve", sql, notes=[], repaired=False)
    notes = list(extra_notes)
    for note in candidate_notes(asked, sql, warehouse=warehouse, grantable=grantable):
        if note not in notes:
            notes.append(note)
    fn = transport if transport is not None else _TRANSPORT.get()
    fixer = repair if repair is not None else _REPAIR.get()
    # Flag on: sql_verify runs on every candidate. Missing key, transport
    # error, empty reply, and 402/429 are unsure. They do not serve.
    try:
        probed = _probe(sql, warehouse)
        schema = _schema(sql, warehouse, grantable)
        body = request_body(
            asked=asked,
            hint=hint,
            sql=sql,
            schema=schema,
            explain=str(probed.get("explain") or ""),
            sample_rows=list(probed.get("sample_rows") or []),
            notes=notes,
        )
        payload = fn(body) if fn is not None else default_transport(body)
        parsed = parse_qualifier_reply(payload)
    except QualifierCallError:
        parsed = _unsure("verifier_unavailable")
    except Exception:
        parsed = _unsure("verifier_unavailable")
    parsed["requirements"] = _probe_literals(
        list(parsed.get("requirements") or []),
        schema=_schema(sql, warehouse, grantable),
        warehouse=warehouse,
        grantable=grantable,
    )
    return _decide(
        asked,
        sql,
        parsed,
        notes=notes,
        hint=hint,
        warehouse=warehouse,
        grantable=grantable,
        space_id=space_id,
        session_id=session_id,
        served_route=served_route,
        transport=fn,
        repair=fixer,
        allow_repair=allow_repair,
        repaired=repaired,
    )


def parse_qualifier_reply(payload: Any) -> dict[str, Any]:
    """Requirements plus an optional suggested question. Else unsure."""
    try:
        data = _unwrap(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _unsure("unparseable")
    if not isinstance(data, dict):
        return _unsure("unparseable")
    raw = data.get("requirements")
    if not isinstance(raw, list):
        return _unsure("unparseable")
    requirements: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            return _unsure("unparseable")
        verdict = _normalize_verdict(item.get("verdict"))
        requirement = item.get("requirement")
        if verdict is None or not isinstance(requirement, str) or not requirement.strip():
            return _unsure("unparseable")
        fragment = item.get("sql_fragment")
        column = item.get("column")
        literal = item.get("literal")
        if fragment is not None and not isinstance(fragment, str):
            return _unsure("unparseable")
        if column is not None and not isinstance(column, str):
            return _unsure("unparseable")
        if literal is not None and not isinstance(literal, str):
            return _unsure("unparseable")
        requirements.append(
            {
                "requirement": requirement.strip(),
                "verdict": verdict,
                "sql_fragment": fragment.strip() if isinstance(fragment, str) else None,
                "column": column.strip() if isinstance(column, str) and column.strip() else None,
                "literal": literal if isinstance(literal, str) and literal != "" else None,
            }
        )
    if not requirements:
        return _unsure("no_requirements")
    suggested = data.get("suggested_question")
    if suggested is not None and not isinstance(suggested, str):
        return _unsure("unparseable")
    return {
        "requirements": requirements,
        "suggested_question": suggested.strip() if isinstance(suggested, str) else "",
    }


def repair_from_compute(compute: Callable[[dict[str, Any]], Any], asked: str) -> Repair:
    """One generator call. The feedback rides the existing repair slot."""

    def repair(previous: str, feedback: str) -> str | None:
        from dms_executor.sql_loop import feedback_prompt, mask_feedback_text

        safe = mask_feedback_text(feedback)
        prompt = feedback_prompt(asked, previous_sql=previous, reason=safe)
        payload = {
            "sql_loop_feedback": {
                "previous_sql": previous,
                "reason": safe,
                "prompt": prompt,
                "repair_hint": {"kind": "qualifier_ai", "fix_hint": safe},
            }
        }
        try:
            got = compute(payload)
        except Exception:
            return None
        return _sql_from_payload(got)

    return repair


def note_reconfirm(
    asked: str,
    *,
    space_id: str | None,
    session_id: str | None,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """Typed reconfirm. The suggested question is never empty."""
    return _reconfirm(
        asked,
        requirement=_CONFIRM,
        suggested=asked,
        space_id=space_id,
        session_id=session_id,
        notes=notes,
    )


def load_api_key() -> str:
    """The ``ov_`` key OpenVault accepts for chat. Empty when none is set."""
    return _api_key()


def endpoint_url() -> str:
    base = os.environ.get(URL_ENV, "").strip().rstrip("/")
    if not base:
        raise QualifierCallError("openvault_url_missing")
    return base + CHAT_PATH


def default_transport(body: dict[str, Any]) -> Any:
    """POST the body. No ``ov_`` key raises before anything is sent."""
    import httpx

    key = _api_key()
    if not key:
        raise QualifierCallError("ov_api_key_missing")
    try:
        url = endpoint_url()
    except QualifierCallError:
        raise
    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=None)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise QualifierCallError("ov_unauthorized") from None
        # 402 and 429 are terminal: OpenVault already walked its ladder.
        raise QualifierCallError(f"ov_http_{status}") from None
    except httpx.HTTPError:
        raise QualifierCallError("ov_unreachable") from None
    try:
        return response.json()
    except ValueError:
        raise QualifierCallError("qualifier_unreadable") from None


def _decide(
    asked: str,
    sql: str,
    parsed: dict[str, Any],
    *,
    notes: list[str],
    hint: str,
    warehouse: Path | None,
    grantable: set[str] | None,
    space_id: str | None,
    session_id: str | None,
    served_route: str,
    transport: Transport | None,
    repair: Repair | None,
    allow_repair: bool,
    repaired: bool,
) -> dict[str, Any]:
    requirements = list(parsed.get("requirements") or [])
    suggested = str(parsed.get("suggested_question") or "")
    kinds = {str(item.get("verdict") or "") for item in requirements}
    if "not_honored" in kinds and allow_repair and repair is not None:
        feedback = _feedback(requirements)
        try:
            rewritten = repair(sql, feedback)
        except Exception:
            rewritten = None
        if isinstance(rewritten, str) and rewritten.strip() and rewritten.strip() != sql.strip():
            structural = _structural_reason(
                rewritten, asked=asked, warehouse=warehouse, grantable=grantable
            )
            if structural:
                if _direct(structural):
                    return _result("abstain", sql, notes=notes, repaired=False, reason=structural)
                return _result(
                    "reconfirm",
                    sql,
                    notes=notes,
                    repaired=False,
                    envelope=_reconfirm(
                        asked,
                        requirement=_plain(requirements, suggested),
                        suggested=suggested or asked,
                        space_id=space_id,
                        session_id=session_id,
                        notes=notes,
                    ),
                )
            return review_candidate(
                asked,
                rewritten.strip(),
                hint=hint,
                warehouse=warehouse,
                grantable=grantable,
                space_id=space_id,
                session_id=session_id,
                served_route=served_route,
                extra_notes=notes,
                transport=transport,
                repair=repair,
                allow_repair=False,
                repaired=True,
            )
        return _result(
            "reconfirm",
            sql,
            notes=notes,
            repaired=False,
            envelope=_reconfirm(
                asked,
                requirement=_plain(requirements, suggested),
                suggested=suggested or asked,
                space_id=space_id,
                session_id=session_id,
                notes=notes,
            ),
        )
    if "not_honored" in kinds or "unsure" in kinds or not requirements:
        return _result(
            "reconfirm",
            sql,
            notes=notes,
            repaired=repaired,
            envelope=_reconfirm(
                asked,
                requirement=_plain(requirements, suggested),
                suggested=suggested or asked,
                space_id=space_id,
                session_id=session_id,
                notes=notes,
            ),
        )
    if notes and served_route in _RULE_ROUTES and not repaired:
        return _result(
            "reconfirm",
            sql,
            notes=notes,
            repaired=False,
            envelope=_reconfirm(
                asked,
                requirement=_plain(requirements, suggested),
                suggested=suggested or asked,
                space_id=space_id,
                session_id=session_id,
                notes=notes,
            ),
        )
    return _result("serve", sql, notes=notes, repaired=repaired)


def _structural_reason(
    sql: str,
    *,
    asked: str,
    warehouse: Path | None,
    grantable: set[str] | None,
) -> str | None:
    from dms_executor.generative_ask import validate_compiled_sql, validated_sql_reason

    why = validate_compiled_sql(
        sql,
        grantable=set(grantable or ()),
        warehouse=warehouse,
        dialect=SERVING_DIALECT if warehouse is not None else None,
    )
    if why:
        return why
    return validated_sql_reason(sql, question=asked, warehouse=warehouse, grantable=grantable)


def _direct(reason: str) -> bool:
    head = str(reason or "").split(":", 1)[0]
    if head in _DIRECT_HEADS or head.startswith("ungranted"):
        return True
    return reason.startswith("validate:ungranted")


def _plain(requirements: Sequence[Mapping[str, Any]], suggested: str) -> str:
    for item in requirements:
        if str(item.get("verdict") or "") in {"not_honored", "unsure"}:
            text = str(item.get("requirement") or "").strip()
            if text:
                return text
    if suggested.strip():
        return suggested.strip()
    return _CONFIRM


def _feedback(requirements: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for item in requirements:
        if str(item.get("verdict") or "") == "honored":
            continue
        requirement = str(item.get("requirement") or "").strip()
        fragment = item.get("sql_fragment")
        piece = requirement
        if isinstance(fragment, str) and fragment.strip():
            piece = f"{requirement} sql_fragment: {fragment.strip()}"
        if piece:
            lines.append(piece)
    return "\n".join(lines) or _CONFIRM


def _reconfirm(
    asked: str,
    *,
    requirement: str,
    suggested: str,
    space_id: str | None,
    session_id: str | None,
    notes: Sequence[str],
) -> dict[str, Any]:
    from dms_executor.envelope import assert_envelope_valid
    from dms_executor.sql_vote import reconfirm_envelope

    why = requirement.strip() or _CONFIRM
    closest = suggested.strip() or asked.strip() or why
    env = reconfirm_envelope(
        question=asked,
        why=why,
        closest_question=closest,
        space_id=space_id,
        session_id=session_id,
    )
    block = env.get("reconfirm")
    if isinstance(block, dict):
        block["suggested_question"] = closest
        block["reason_code"] = "qualifier_ai"
        block["requirement"] = why
    assumptions = [item for item in (env.get("assumptions") or []) if isinstance(item, str)]
    for note in notes:
        line = f"qualifier_note: {note}"
        if line not in assumptions:
            assumptions.append(line)
    env["assumptions"] = assumptions
    env["rows"] = []
    env["values"] = []
    env["sql_used"] = None
    assert_envelope_valid(env)
    return env


def _result(
    action: str,
    sql: str,
    *,
    notes: list[str],
    repaired: bool,
    envelope: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "sql": sql,
        "notes": list(notes),
        "repaired": repaired,
        "envelope": envelope,
        "reason": reason,
    }


def _unsure(code: str) -> dict[str, Any]:
    return {
        "requirements": [
            {
                "requirement": _CONFIRM,
                "verdict": "unsure",
                "sql_fragment": None,
                "column": None,
                "literal": None,
            }
        ],
        "suggested_question": "",
        "code": code,
    }


def _probe_literals(
    requirements: list[dict[str, Any]],
    *,
    schema: Sequence[Mapping[str, Any]],
    warehouse: Path | None,
    grantable: set[str] | None,
) -> list[dict[str, Any]]:
    if warehouse is None or not Path(warehouse).is_file():
        out: list[dict[str, Any]] = []
        for item in requirements:
            if item.get("literal") is not None and item.get("verdict") == "honored":
                out.append(_missing_literal(item))
            else:
                out.append(item)
        return out
    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(Path(warehouse))
    try:
        checked: list[dict[str, Any]] = []
        for item in requirements:
            literal = item.get("literal")
            if item.get("verdict") != "honored" or not isinstance(literal, str):
                checked.append(item)
                continue
            if _literal_in_column(con, schema, str(item.get("column") or ""), literal, grantable):
                checked.append(item)
            else:
                checked.append(_missing_literal(item))
        return checked
    finally:
        con.close()


def _missing_literal(item: Mapping[str, Any]) -> dict[str, Any]:
    """A cited value that is not in the granted column cannot pass."""
    return {
        "requirement": "a cited filter value is not in the granted column",
        "verdict": "not_honored",
        "sql_fragment": item.get("sql_fragment"),
        "column": item.get("column"),
        "literal": None,
    }


def _literal_in_column(
    con: Any,
    schema: Sequence[Mapping[str, Any]],
    column: str,
    literal: str,
    grantable: set[str] | None,
) -> bool:
    pair = _resolve_column(schema, column, grantable)
    if pair is None:
        return False
    table, name = pair
    from dms_executor.bronze import quote_ident

    statement = f"SELECT 1 FROM {_quoted_table(table)} WHERE {quote_ident(name)} = ? LIMIT 1"
    if _probe_hit(con, statement, literal):
        return True
    number = _whole_number(literal)
    if number is None:
        return False
    return _probe_hit(con, statement, number)


def _probe_hit(con: Any, statement: str, value: Any) -> bool:
    try:
        row = con.execute(statement, [value]).fetchone()
    except Exception:
        return False
    return row is not None


def _whole_number(literal: str) -> int | None:
    body = literal[1:] if literal.startswith("-") else literal
    if not body or not body.isdigit():
        return None
    try:
        return int(literal)
    except ValueError:
        return None


def _resolve_column(
    schema: Sequence[Mapping[str, Any]],
    column: str,
    grantable: set[str] | None,
) -> tuple[str, str] | None:
    text = column.strip()
    if not text:
        return None
    if "." in text:
        table, name = text.rsplit(".", 1)
        if _column_granted(schema, table, name, grantable):
            return table, name
        return None
    hits: list[tuple[str, str]] = []
    for item in schema:
        table = str(item.get("table") or "")
        if _column_on(item, text) and _granted(table, grantable):
            hits.append((table, text))
    if len(hits) == 1:
        return hits[0]
    return None


def _column_granted(
    schema: Sequence[Mapping[str, Any]],
    table: str,
    column: str,
    grantable: set[str] | None,
) -> bool:
    if not _granted(table, grantable):
        return False
    for item in schema:
        if _same_table(str(item.get("table") or ""), table) and _column_on(item, column):
            return True
    return False


def _column_on(item: Mapping[str, Any], column: str) -> bool:
    raw = item.get("columns")
    if not isinstance(raw, list):
        return False
    for col in raw:
        if isinstance(col, Mapping) and str(col.get("name") or "") == column:
            return True
    return False


def _same_table(left: str, right: str) -> bool:
    return left == right or left.split(".")[-1] == right.split(".")[-1]


def _granted(table: str, grantable: set[str] | None) -> bool:
    if grantable is None:
        return True
    bare = table.split(".")[-1]
    allowed = {str(item) for item in grantable}
    allowed |= {item.split(".")[-1] for item in list(allowed)}
    folded = {item.casefold() for item in allowed}
    names = {table, bare, table.casefold(), bare.casefold()}
    return bool(names & allowed) or bool(names & folded)


def _schema(sql: str, warehouse: Path | None, grantable: set[str] | None) -> list[dict[str, Any]]:
    tree = _one_select(sql)
    if tree is None or warehouse is None or not Path(warehouse).is_file():
        return []
    from dms_executor.demo_warehouse import connect_serving

    tables: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if not isinstance(name, str) or not name:
            continue
        full = f"{table.db}.{name}" if table.db else name
        if full not in tables and _granted(full, grantable):
            tables.append(full)
    con = connect_serving(Path(warehouse))
    try:
        out: list[dict[str, Any]] = []
        for full in tables:
            try:
                described = con.execute(f"DESCRIBE {_quoted_table(full)}").fetchall()
            except Exception:
                continue
            columns = []
            for row in described:
                if not row:
                    continue
                columns.append(
                    {
                        "name": str(row[0]),
                        "type": str(row[1]) if len(row) > 1 else "",
                    }
                )
            out.append({"table": full, "columns": columns})
        return out
    finally:
        con.close()


def _probe(sql: str, warehouse: Path | None) -> dict[str, Any]:
    empty: dict[str, Any] = {"explain": "", "explain_error": "", "sample_rows": []}
    tree = _one_select(sql)
    if tree is None or warehouse is None or not Path(warehouse).is_file():
        empty["explain_error"] = "not_a_select" if tree is None else "warehouse_missing"
        return empty
    from dms_executor.demo_warehouse import connect_serving

    limited = tree.copy()
    limited.set("limit", exp.Limit(expression=exp.Literal.number(_PROBE_LIMIT)))
    con = connect_serving(Path(warehouse))
    try:
        explain, explain_error = _explain(con, sql)
        sample = _sample(con, limited.sql(dialect=SERVING_DIALECT), _one_table(tree))
        return {"explain": explain, "explain_error": explain_error, "sample_rows": sample}
    finally:
        con.close()


def _explain(con: Any, sql: str) -> tuple[str, str]:
    try:
        cursor = con.execute("EXPLAIN " + sql)
        lines = []
        for row in cursor.fetchall():
            lines.append(" ".join("" if part is None else str(part) for part in row))
        return "\n".join(lines), ""
    except Exception as exc:
        return "", type(exc).__name__


def _sample(con: Any, sql: str, table: str | None) -> list[dict[str, Any]]:
    try:
        cursor = con.execute(sql)
        description = cursor.description or []
        names = [str(item[0]) for item in description if item]
        rows = cursor.fetchall()
    except Exception:
        return []
    from dms_executor.ask_column_mask import column_masked

    out: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for index, name in enumerate(names):
            raw = row[index] if index < len(row) else None
            hidden = column_masked(table, name, values=[raw])
            item[name] = None if hidden else _json_cell(raw)
        out.append(item)
    return out


def _one_table(tree: Any) -> str | None:
    names: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    if len(names) == 1:
        return names[0]
    return None


def _one_select(sql: str) -> Any:
    if not isinstance(sql, str) or not sql.strip():
        return None
    try:
        tree = parse_one(sql, read=SERVING_DIALECT)
    except Exception:
        return None
    if not isinstance(tree, exp.Query):
        return None
    return tree


def _quoted_table(full: str) -> str:
    from dms_executor.bronze import quote_ident

    return ".".join(quote_ident(part) for part in full.split(".") if part)


def _json_cell(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, str):
        return value
    return str(value)


def _sql_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    sql = payload.get("query_sql")
    if isinstance(sql, str) and sql.strip():
        return sql.strip()
    from cortex_client.compute import insights_query_sql

    found = insights_query_sql(payload)
    if isinstance(found, str) and found.strip():
        return found.strip()
    return None


def _api_key() -> str:
    for name in API_KEY_ENVS:
        raw = os.environ.get(name, "").strip()
        if raw.startswith(API_KEY_PREFIX):
            return raw
    return ""


def _normalize_verdict(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace(" ", "_")
    if text in _VERDICTS:
        return text
    return None


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, str):
        return _loads_reply(payload)
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return _loads_reply(content)
        if isinstance(content, dict):
            return content
    return payload


def _loads_reply(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        stripped = _strip_one_fence(content)
        if stripped == content:
            raise
        return json.loads(stripped)


def _strip_one_fence(text: str) -> str:
    """Drop one opening fence line and one trailing fence. No regex."""
    body = text.strip()
    if not body.startswith(_FENCE):
        return text
    newline = body.find("\n")
    if newline < 0:
        return text
    rest = body[newline + 1 :].rstrip()
    if rest.endswith(_FENCE):
        rest = rest[: -len(_FENCE)].rstrip()
    return rest


def _reject_model_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _MODEL_KEYS:
                raise QualifierCallError("model_key_refused")
            _reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_model_keys(item)


__all__ = [
    "QUALIFIER_AI_ENV",
    "NOTE_KINDS",
    "bind_qualifier_repair",
    "bind_qualifier_transport",
    "candidate_notes",
    "deciding_qualifier_reason",
    "is_candidate_note",
    "note_reconfirm",
    "parse_qualifier_reply",
    "qualifier_ai_enabled",
    "repair_from_compute",
    "request_body",
    "reset_qualifier_repair",
    "reset_qualifier_transport",
    "review_candidate",
]
