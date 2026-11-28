"""One SQL repair for a fixable join refusal, and orphan exclusion lines.

``DMS_JOIN_DECLARED_FK`` off: ``resolve_join_repair`` returns None and the
caller keeps today's refusal. On, one OpenVault call with role ``sql``
rewrites the SQL. The repaired statement is served only after
``validated_sql_reason`` passes, including the join rule. A second refusal
is a typed reconfirm that names the two tables. No model id is sent.

``DMS_FK_ORPHAN_DISCLOSE`` off: ``stamp_orphan_exclusion`` does not touch
the envelope. On, a link verified with orphans adds its exclusion line.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.join_flags import (
    fk_orphan_disclose_enabled,
    join_declared_fk_enabled,
    repairable_join_reason,
)
from dms_executor.ontology import Ontology, Violation

ROLE = "sql"
SCHEMA_NAME = "JoinRepairReply"
_TRANSPORT: ContextVar[Callable[[dict[str, Any]], Any] | None] = ContextVar(
    "dms_join_repair_transport", default=None
)
_BANNED = frozenset(
    {"model", "model_id", "model_name", "provider", "served_model", "served_provider", "strict"}
)
_SYSTEM = (
    "Rewrite the SQL so the named join refusal no longer applies. "
    "You receive the refusal and the SQL. "
    "A parent value that repeats after a join must be aggregated before the join, "
    "counted with COUNT of the child rows, or filtered with EXISTS. "
    "Reply with one JSON object and nothing else. The only key is sql. "
    "sql is one read-only SELECT. Do not name a model or a provider."
)


def bind_repair_transport(transport: Callable[[dict[str, Any]], Any]) -> Token[Any]:
    return _TRANSPORT.set(transport)


def reset_repair_transport(token: Token[Any]) -> None:
    _TRANSPORT.reset(token)


def repair_request(sql: str, reason: str) -> dict[str, Any]:
    """OpenVault body. Role ``sql``. No model id and no provider."""
    user = {"refusal": reason, "sql": sql}
    body: dict[str, Any] = {
        "role": ROLE,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": json.dumps(user, sort_keys=True, separators=(",", ":")),
            },
        ],
        "schema": SCHEMA_NAME,
    }
    _reject_route_fields(body)
    return body


def resolve_join_repair(
    reason: str,
    sql: str,
    *,
    question: str,
    space_onto: Ontology | None,
    violations: Sequence[Violation],
    warehouse: Path | None,
    grantable: set[str] | None,
    dialect: str | None,
    space_id: str | None,
    session_id: str | None,
    columns_of: Mapping[str, set[str]] | None = None,
    transport: Callable[[dict[str, Any]], Any] | None = None,
) -> str | dict[str, Any] | None:
    """None means the caller keeps today's refusal.

    A string is repaired SQL that passed every gate. A dict is the reconfirm
    envelope for a second refusal.
    """
    if not join_declared_fk_enabled() or not repairable_join_reason(reason):
        return None
    if space_onto is None:
        return _reconfirm(sql, question, space_id, session_id, reason, "no ontology")
    cols = columns_of
    if cols is None:
        try:
            from dms_executor.space_ontology import relation_columns

            cols = relation_columns(space_onto, warehouse, sorted(grantable or []))
        except Exception:  # noqa: BLE001 - an unreadable lake cannot prove the repair
            return _reconfirm(sql, question, space_id, session_id, reason, "columns unread")
    new_sql = _generate(sql, reason, transport)
    if not new_sql:
        return _reconfirm(sql, question, space_id, session_id, reason, "no repair")
    blocked = _gates(
        new_sql,
        question=question,
        space_onto=space_onto,
        violations=violations,
        warehouse=warehouse,
        grantable=grantable,
        dialect=dialect,
        columns_of=cols,
    )
    if blocked:
        return _reconfirm(sql, question, space_id, session_id, reason, blocked)
    return new_sql


def orphan_reconfirm(
    violations: Sequence[Violation],
    onto: Ontology | None,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """A typed reconfirm when every cited violation is an over-threshold orphan hold.

    Flag off, or any other violation, returns None so the caller abstains as today.
    """
    if not fk_orphan_disclose_enabled() or onto is None or not violations:
        return None
    if any(v.check != "fk_orphan_hold" for v in violations):
        return None
    holds = onto.__dict__.get("_orphan_holds") or {}
    rows = []
    for v in violations:
        info = holds.get(v.subject)
        if not isinstance(info, dict):
            return None
        rows.append(info)
    return _orphan_envelope(rows, question, space_id, session_id)


def stamp_orphan_exclusion(env: dict[str, Any], onto: Ontology | None = None) -> dict[str, Any]:
    """Add the exclusion line when the served SQL joins a disclosed link.

    Flag off returns the same dict without reading it.
    """
    if not fk_orphan_disclose_enabled() or env.get("abstained"):
        return env
    target = onto
    if target is None:
        from dms_executor.join_flags import current_serve_ontology

        target = current_serve_ontology()
    if not isinstance(target, Ontology):
        return env
    sql = str(env.get("sql_used") or "")
    lines = _exclude_lines(target, sql)
    if not lines:
        return env
    receipt = env.get("audit_receipt")
    if not isinstance(receipt, dict):
        return env
    exclude = receipt.get("exclude")
    if not isinstance(exclude, dict):
        return env
    reasons = [item for item in (exclude.get("reasons") or []) if isinstance(item, dict)]
    for line in lines:
        reasons.append({"kind": "orphan", "detail": line})
    exclude["reasons"] = reasons
    exclude["status"] = "filters"
    note = "orphan rows on a verified link are excluded by the join and named here"
    why = str(exclude.get("why") or "")
    exclude["why"] = f"{why}; {note}" if why else note
    return env


def _gates(
    sql: str,
    *,
    question: str,
    space_onto: Ontology,
    violations: Sequence[Violation],
    warehouse: Path | None,
    grantable: set[str] | None,
    dialect: str | None,
    columns_of: Mapping[str, set[str]],
) -> str | None:
    from dms_executor.generative_ask import validated_sql_reason

    return validated_sql_reason(
        sql,
        question=question,
        warehouse=warehouse,
        grantable=set(grantable or ()),
        space_onto=space_onto,
        declared_violations=violations,
        columns_of=columns_of,
        dialect=dialect,
    )


def _generate(
    sql: str, reason: str, transport: Callable[[dict[str, Any]], Any] | None
) -> str | None:
    body = repair_request(sql, reason)
    fn = transport if transport is not None else _TRANSPORT.get()
    if fn is None:
        fn = _default_transport
    try:
        payload = fn(body)
    except Exception:  # noqa: BLE001 - a missed repair is a reconfirm, not a guess
        return None
    text = _sql_from_payload(payload)
    if not text or not _one_select(text):
        return None
    return text


def _one_select(sql: str) -> bool:
    try:
        trees = [t for t in sqlglot.parse(sql, read=SERVING_DIALECT) if t is not None]
    except Exception:  # noqa: BLE001
        return False
    return len(trees) == 1 and isinstance(trees[0], exp.Select)


def _sql_from_payload(payload: Any) -> str | None:
    data: Any = payload
    if isinstance(payload, dict) and isinstance(payload.get("choices"), list):
        try:
            data = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict):
        sql = data.get("sql")
        if isinstance(sql, str) and sql.strip():
            return sql.strip()
    return None


def _relations(sql: str) -> list[str]:
    try:
        tree = sqlglot.parse_one(sql, read=SERVING_DIALECT)
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue
        parts = [p for p in (table.db, table.name) if p]
        label = ".".join(parts)
        if label and label not in out:
            out.append(label)
    return out


def _pair(sql: str) -> tuple[str, str]:
    names = _relations(sql)
    if len(names) >= 2:
        return names[0], names[1]
    if len(names) == 1:
        return names[0], names[0]
    return "the first table", "the second table"


def _code(reason: str) -> str:
    marker = "unverified_join:"
    idx = reason.find(marker)
    tail = reason[idx + len(marker) :] if idx >= 0 else reason
    head = tail.split("(", 1)[0].strip()
    return head or "join"


def _reconfirm(
    sql: str,
    question: str,
    space_id: str | None,
    session_id: str | None,
    reason: str,
    second: str,
) -> dict[str, Any]:
    from dms_executor.sql_vote import reconfirm_envelope

    left, right = _pair(sql)
    code = _code(reason)
    why = (
        f"The join of {left} and {right} was refused ({code}) and the repair "
        f"was refused again ({second}). No number was served."
    )
    closest = f"List {left} and {right} separately, without joining them."
    env = reconfirm_envelope(
        question=question,
        why=why,
        closest_question=closest,
        space_id=space_id,
        session_id=session_id,
    )
    block = env.get("reconfirm")
    if isinstance(block, dict):
        block["reason_code"] = "join_repair"
        block["suggested_question"] = closest
    return env


def _orphan_envelope(
    rows: list[dict[str, Any]],
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    from dms_executor.sql_vote import reconfirm_envelope

    bits = []
    for info in rows:
        bits.append(
            f"{int(info['orphan_rows'])} {info['child_leaf']} rows "
            f"({int(info['orphan_keys'])} distinct keys) of {int(info['child_rows'])} "
            f"reference a missing parent on {info['child']} to {info['parent']} "
            f"(ratio {float(info['ratio']):.6f}, steward threshold {float(info['threshold']):.6f})"
        )
    why = "Orphan rows are above the steward threshold. " + " ".join(bits)
    why = why + " No number was served."
    child = str(rows[0].get("child_leaf") or "the child table")
    parent = str(rows[0].get("parent_leaf") or "the parent table")
    closest = (
        f"How many {parent} rows does the join match, and how many {child} rows are excluded?"
    )
    env = reconfirm_envelope(
        question=question,
        why=why,
        closest_question=closest,
        space_id=space_id,
        session_id=session_id,
    )
    block = env.get("reconfirm")
    if isinstance(block, dict):
        block["reason_code"] = "fk_orphan_hold"
        block["suggested_question"] = closest
    return env


def _exclude_lines(onto: Ontology, sql: str) -> list[str]:
    disclosed = onto.__dict__.get("_orphan_links") or {}
    if not disclosed or not sql.strip():
        return []
    from dms_executor.generative_ask import cited_relations

    named = cited_relations(sql)
    lines: list[str] = []
    for name, info in disclosed.items():
        link = onto.links.get(name)
        if link is None or not isinstance(info, dict):
            continue
        rels: set[str] = set()
        for obj_name in (link.from_object, link.to_object):
            obj = onto.objects.get(obj_name)
            if obj is not None:
                rels |= cited_relations(f"SELECT * FROM {obj.relation}")
        if rels and rels <= named:
            line = str(info.get("line") or "").strip()
            if line:
                lines.append(line)
    return lines


def _reject_route_fields(body: Mapping[str, Any]) -> None:
    found = _BANNED.intersection(body)
    if found:
        raise RuntimeError("route_field")


def _default_transport(body: dict[str, Any]) -> Any:
    """POST the body. No timeout. The error is a code, never a cell or a key."""
    import httpx

    key = ""
    for name in ("OPENVAULT_API_KEY", "CORTEX_API_KEY"):
        raw = os.environ.get(name, "").strip()
        if raw.startswith("ov_"):
            key = raw
            break
    if not key:
        raise RuntimeError("ov_api_key_missing")
    base = os.environ.get("OPENVAULT_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("openvault_url_missing")
    try:
        response = httpx.post(
            base + "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=None,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        raise RuntimeError("ov_unreachable") from None
    try:
        return response.json()
    except ValueError:
        raise RuntimeError("repair_unreadable") from None
