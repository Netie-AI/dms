"""R1-SHAPE-CHECK-01. Default off.

On, one OpenVault call with role ``reason`` decides whether the executed
result matches the question's shape. The reply is ``{verdict, reasons,
fix_hint}``. SQL facts (ORDER BY, LIMIT, GROUP BY, aggregate) come from
the sqlglot tree. They are handed to the model. This module does not
compare them to words in the question.

Off, ``check_answer_shape`` returns ``skipped`` and sends nothing.

A transport failure or a reply that is not the JSON object is ``unknown``.
The caller reconfirms. It does not serve.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlglot import exp, parse_one

from dms_executor.demo_warehouse import SERVING_DIALECT

SHAPE_CHECK_ENV = "DMS_SHAPE_CHECK"
ROLE_ALIAS = "reason"
SCHEMA_NAME = "AnswerShapeReply"
_SAMPLE_CAP = 5
_REASON_CAP = 8
_TEXT_CAP = 200
_CELL_CAP = 80
_GROUP_CAP = 16
_FENCE = "```"
_VERDICTS = frozenset({"fit", "misfit", "unknown"})
_SYSTEM = (
    "Decide whether this executed result answers the question's shape. "
    "You receive the question, the SQL, SQL facts, column names and types, "
    "row_count, up to 5 sample rows, and an ontology slice of grain keys and measures. "
    "The facts are has_order_by, limit, group_by, and aggregate. They were computed "
    "from the SQL only. Use them. "
    "A ranked result needs ORDER BY and LIMIT of the requested size. "
    "A requested grain needs one row per grain key, not a single total. "
    "An aggregate is not a row listing. A time window has to be applied. "
    "Units have to match the measure. "
    "Reply with one JSON object and nothing else. "
    "Keys are verdict, reasons, fix_hint. "
    "verdict is fit, misfit, or unknown. "
    "reasons is an array of short strings. "
    "fix_hint is one sentence a SQL writer can apply, or null when the verdict is fit. "
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
API_KEY_ENVS = ("OPENVAULT_API_KEY", "CORTEX_API_KEY")
API_KEY_PREFIX = "ov_"
URL_ENV = "OPENVAULT_URL"
CHAT_PATH = "/v1/chat/completions"

Transport = Callable[[dict[str, Any]], Any]
_TRANSPORT: ContextVar[Transport | None] = ContextVar("dms_shape_check_transport", default=None)


class ShapeCallError(RuntimeError):
    """The shape call could not be sent. The message is a code, never a cell."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AnswerShapeReply(BaseModel):
    """Schema sent as ``response_format``. The parser does not trust a coerce."""

    model_config = ConfigDict(extra="ignore")
    verdict: str
    reasons: list[str] = []
    fix_hint: str | None = None


def shape_check_enabled() -> bool:
    """True only when ``DMS_SHAPE_CHECK`` is the digit 1. Unset is off."""
    return os.environ.get(SHAPE_CHECK_ENV, "0").strip() == "1"


def bind_shape_transport(transport: Transport) -> Token[Transport | None]:
    return _TRANSPORT.set(transport)


def reset_shape_transport(token: Token[Transport | None]) -> None:
    _TRANSPORT.reset(token)


def sql_shape_facts(sql: str, dialect: str | None = None) -> dict[str, Any]:
    """ORDER BY, LIMIT, GROUP BY, and aggregate presence. From the SQL tree.

    The question is not an argument. A parse failure is ``parsed`` false and
    empty facts, not a guess about what the SQL meant.
    """
    empty: dict[str, Any] = {
        "parsed": False,
        "has_order_by": False,
        "limit": None,
        "group_by": [],
        "aggregate": False,
    }
    tree = _parse(sql, dialect)
    if tree is None:
        return empty
    order = tree.args.get("order")
    group = tree.args.get("group")
    names: list[str] = []
    if isinstance(group, exp.Group):
        for node in group.expressions[:_GROUP_CAP]:
            try:
                text = node.sql(dialect=dialect or SERVING_DIALECT)
            except Exception:
                text = ""
            text = text.strip()
            if text:
                names.append(text[:_TEXT_CAP])
    return {
        "parsed": True,
        "has_order_by": isinstance(order, exp.Order) and bool(order.expressions),
        "limit": _limit_value(tree.args.get("limit")),
        "group_by": names,
        "aggregate": tree.find(exp.AggFunc) is not None,
    }


def ontology_slice(ontology: Any) -> dict[str, Any]:
    """Grain keys and measures. Nothing is chosen by reading the question."""
    if isinstance(ontology, Mapping) and (
        isinstance(ontology.get("grain_keys"), list) or isinstance(ontology.get("measures"), list)
    ):
        return {
            "grain_keys": _clean_grains(ontology.get("grain_keys")),
            "measures": _clean_measures(ontology.get("measures")),
        }
    objects = getattr(ontology, "objects", None)
    measures = getattr(ontology, "measures", None)
    grains: list[dict[str, Any]] = []
    if isinstance(objects, Mapping):
        for name, obj in list(objects.items())[:32]:
            key = getattr(obj, "key", ()) or ()
            grains.append({"object": str(name), "keys": [str(part) for part in key]})
    measure_rows: list[dict[str, str]] = []
    if isinstance(measures, Mapping):
        for name, measure in list(measures.items())[:32]:
            measure_rows.append(
                {
                    "name": str(getattr(measure, "name", name)),
                    "grain": str(getattr(measure, "grain", "")),
                    "expression": str(getattr(measure, "expression", ""))[:_TEXT_CAP],
                }
            )
    return {"grain_keys": grains, "measures": measure_rows}


def request_body(
    *,
    question: str,
    sql: str,
    columns: Sequence[Mapping[str, Any]],
    row_count: int,
    sample_rows: Sequence[Mapping[str, Any]],
    ontology: Any,
    dialect: str | None,
) -> dict[str, Any]:
    """OpenVault body. Role ``reason``. No model id and no ``strict``."""
    facts = sql_shape_facts(sql, dialect)
    samples = [dict(row) for row in list(sample_rows)[:_SAMPLE_CAP] if isinstance(row, Mapping)]
    user = {
        "question": question if isinstance(question, str) else "",
        "sql": sql if isinstance(sql, str) else "",
        "facts": facts,
        "columns": _public_columns(columns),
        "row_count": _whole(row_count, 0),
        "sample_rows": samples,
        "ontology": ontology_slice(ontology),
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
                "schema": AnswerShapeReply.model_json_schema(),
            },
        },
    }
    _reject_model_keys(body)
    return body


def check_answer_shape(
    question: str,
    sql: str,
    *,
    rows: Sequence[Mapping[str, Any]] | None = None,
    columns: Sequence[Mapping[str, Any]] | None = None,
    row_count: int | None = None,
    sample_rows: Sequence[Mapping[str, Any]] | None = None,
    ontology: Any = None,
    dialect: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """One OpenVault call, or ``skipped`` when the flag is off.

    ``rows`` are masked with ``ask_column_mask`` before they are sent.
    An unreadable reply is ``unknown``.
    """
    if not shape_check_enabled():
        return {"verdict": "skipped", "reasons": [], "fix_hint": None}
    held = [row for row in (rows or []) if isinstance(row, Mapping)]
    described = _public_columns(columns) if columns is not None else _columns_from_rows(held)
    count = _whole(row_count, len(held))
    if sample_rows is None:
        samples = _masked_samples(held, described, _single_table(sql, dialect))
    else:
        samples = _masked_samples(
            [row for row in sample_rows if isinstance(row, Mapping)],
            described,
            _single_table(sql, dialect),
        )
    body = request_body(
        question=question,
        sql=sql,
        columns=described,
        row_count=count,
        sample_rows=samples,
        ontology=ontology,
        dialect=dialect,
    )
    fn = transport if transport is not None else (_TRANSPORT.get() or default_transport)
    try:
        payload = fn(body)
    except ShapeCallError:
        return _unknown("shape_call_failed")
    except Exception:
        return _unknown("shape_call_failed")
    return parse_shape_reply(payload)


def shape_rung(
    question: str,
    sql: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Same return as ``check_answer_shape``. The ask ladder can call this."""
    return check_answer_shape(question, sql, **kwargs)


def parse_shape_reply(payload: Any) -> dict[str, Any]:
    """``{verdict, reasons, fix_hint}``. Anything else is ``unknown``."""
    try:
        data = _unwrap(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _unknown("unparseable")
    if not isinstance(data, dict):
        return _unknown("unparseable")
    verdict = data.get("verdict")
    if verdict not in _VERDICTS:
        return _unknown("unparseable")
    raw = data.get("reasons")
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        return _unknown("unparseable")
    reasons = [item.strip()[:_TEXT_CAP] for item in raw[:_REASON_CAP] if item.strip()]
    hint = data.get("fix_hint")
    if hint is not None and not isinstance(hint, str):
        return _unknown("unparseable")
    if verdict != "misfit":
        hint = None
    elif isinstance(hint, str):
        hint = hint.strip()[:_TEXT_CAP] or None
    return {"verdict": verdict, "reasons": reasons, "fix_hint": hint}


def load_api_key() -> str:
    """The ``ov_`` key OpenVault accepts for chat. Empty when none is set."""
    for name in API_KEY_ENVS:
        raw = os.environ.get(name, "").strip()
        if raw.startswith(API_KEY_PREFIX):
            return raw
    return ""


def endpoint_url() -> str:
    base = os.environ.get(URL_ENV, "").strip().rstrip("/")
    if not base:
        raise ShapeCallError("openvault_url_missing")
    return base + CHAT_PATH


def default_transport(body: dict[str, Any]) -> Any:
    """POST the body. No ``ov_`` key raises before anything is sent.

    The code never carries the key or a cell value.
    """
    import httpx

    key = load_api_key()
    if not key:
        raise ShapeCallError("ov_api_key_missing")
    try:
        url = endpoint_url()
    except ShapeCallError:
        raise
    headers = {"Authorization": f"Bearer {key}"}
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=60.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise ShapeCallError("ov_unauthorized") from None
        raise ShapeCallError(f"ov_http_{status}") from None
    except httpx.HTTPError:
        raise ShapeCallError("ov_unreachable") from None
    try:
        data = response.json()
    except ValueError:
        raise ShapeCallError("shape_unreadable") from None
    return data


def _whole(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _unknown(reason: str) -> dict[str, Any]:
    return {"verdict": "unknown", "reasons": [reason], "fix_hint": None}


def _parse(sql: str, dialect: str | None) -> Any:
    if not isinstance(sql, str) or not sql.strip():
        return None
    try:
        return parse_one(sql, read=dialect or SERVING_DIALECT)
    except Exception:
        return None


def _limit_value(node: Any) -> int | None:
    if not isinstance(node, exp.Limit):
        return None
    expr = node.expression
    if isinstance(expr, exp.Literal) and expr.is_int:
        try:
            return int(expr.this)
        except (TypeError, ValueError):
            return None
    return None


def _single_table(sql: str, dialect: str | None) -> str | None:
    tree = _parse(sql, dialect)
    if tree is None:
        return None
    names: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    if len(names) == 1:
        return names[0]
    return None


def _columns_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    names: list[str] = []
    for row in rows:
        for key in row:
            if isinstance(key, str) and key and key not in names:
                names.append(key)
    return [{"name": name, "type": _column_type(rows, name)} for name in names]


def _column_type(rows: Sequence[Mapping[str, Any]], name: str) -> str:
    seen: list[str] = []
    for row in rows:
        if name not in row:
            continue
        kind = _value_type(row.get(name))
        if kind is None:
            continue
        if kind not in seen:
            seen.append(kind)
        if len(seen) > 1:
            return "OTHER"
    if not seen:
        return "UNKNOWN"
    return seen[0]


def _value_type(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, str):
        return "VARCHAR"
    return "OTHER"


def _public_columns(columns: Sequence[Mapping[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for col in columns or []:
        if not isinstance(col, Mapping):
            continue
        name = col.get("name")
        typ = col.get("type")
        if isinstance(name, str) and name:
            out.append({"name": name, "type": typ if isinstance(typ, str) and typ else "UNKNOWN"})
    return out


def _masked_samples(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[Mapping[str, str]],
    table: str | None,
) -> list[dict[str, Any]]:
    from dms_executor.ask_column_mask import column_masked

    described = _public_columns(columns)
    if not described:
        described = _columns_from_rows(rows)
    out: list[dict[str, Any]] = []
    for row in list(rows)[:_SAMPLE_CAP]:
        item: dict[str, Any] = {}
        for col in described:
            name = col["name"]
            present = name in row
            raw = row.get(name) if present else None
            hidden = column_masked(
                table,
                name,
                type_name=col.get("type"),
                values=[raw] if present else None,
            )
            item[name] = None if hidden else _json_cell(raw)
        out.append(item)
    return out


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
        return value[:_CELL_CAP]
    return str(value)[:_CELL_CAP]


def _clean_grains(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:32]:
        if isinstance(item, Mapping):
            keys = item.get("keys")
            key_list = [str(part) for part in keys] if isinstance(keys, list) else []
            out.append({"object": str(item.get("object") or ""), "keys": key_list})
        elif isinstance(item, str) and item:
            out.append({"object": item, "keys": []})
    return out


def _clean_measures(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw[:32]:
        if not isinstance(item, Mapping):
            continue
        out.append(
            {
                "name": str(item.get("name") or "")[:_TEXT_CAP],
                "grain": str(item.get("grain") or "")[:_TEXT_CAP],
                "expression": str(item.get("expression") or "")[:_TEXT_CAP],
            }
        )
    return out


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
                raise ShapeCallError("model_key_refused")
            _reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_model_keys(item)


__all__ = [
    "AnswerShapeReply",
    "SHAPE_CHECK_ENV",
    "bind_shape_transport",
    "check_answer_shape",
    "ontology_slice",
    "parse_shape_reply",
    "request_body",
    "reset_shape_transport",
    "shape_check_enabled",
    "shape_rung",
    "sql_shape_facts",
]
