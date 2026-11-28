"""NL19-COMPILE-WRONG. Default off. Digit 1 only.

On, two OpenVault calls use role ``reason``. The first decides whether the
question asks to change stored data. A sqlglot statement that is not a
SELECT refuses as well. The second, after a measure is compiled, decides
whether that SQL honours grouping, filters, entity, aggregation, and time.
The facts are read from the SQL tree and the compiled measure. They are
not compared to the question in Python.

Off, neither call is sent. Compile serves as it did.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlglot import exp, parse

from dms_executor.demo_warehouse import SERVING_DIALECT

ENV = "DMS_COMPILE_HONOUR_CHECK"
ROLE_ALIAS = "reason"
DESTRUCTIVE_SCHEMA = "DestructiveIntentReply"
HONOUR_SCHEMA = "CompileHonourReply"
_TEXT_CAP = 200
_LIST_CAP = 16
_FENCE = "```"
_HONOUR_KEYS = ("grouping", "filters", "entity", "aggregation", "time")
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
_NON_SELECT = (
    exp.DML,
    exp.DDL,
    exp.Drop,
    exp.Alter,
    exp.Set,
    exp.TruncateTable,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Analyze,
    exp.Use,
    exp.Describe,
)
_TIME_NODES = tuple(
    node
    for name in (
        "Date",
        "Timestamp",
        "Time",
        "TsOrDs",
        "Datetime",
        "CurrentDate",
        "CurrentTimestamp",
        "DateTrunc",
        "TimestampTrunc",
        "DateAdd",
        "DateSub",
        "TimeAdd",
        "TimeSub",
    )
    if isinstance(node := getattr(exp, name, None), type)
)
_DESTRUCTIVE_SYSTEM = (
    "Decide whether the user is asking to change stored data. "
    "You receive the question text. "
    "destructive is true when the request would remove, replace, or write rows or tables. "
    "destructive is false when the request only reads. "
    "Reply with one JSON object and nothing else. "
    "Keys are destructive and reasons. "
    "destructive is a boolean. "
    "reasons is an array of short strings. "
    "Do not name a model or a provider."
)
_HONOUR_SYSTEM = (
    "Decide whether this compiled SQL honours the question. "
    "You receive the question, the SQL, and column semantics taken from the SQL tree "
    "and the compiled measure. "
    "Judge grouping, filters, entity, aggregation, and time. "
    "Each value is true only when the SQL honours that part. "
    "A part the question does not ask for is true. "
    "Reply with one JSON object and nothing else. "
    "Keys are grouping, filters, entity, aggregation, time, and reasons. "
    "The five judgements are booleans. "
    "reasons is an array of short strings. "
    "Do not name a model or a provider."
)
_SHAPE_MARKS = (
    "shape mismatch (E10/FF-01)",
    "shape mismatch (E12/ANS-02)",
)

_SQLGLOT_LOG = logging.getLogger("sqlglot")

Transport = Callable[[dict[str, Any]], Any]
_TRANSPORT: ContextVar[Transport | None] = ContextVar("dms_compile_honour_transport", default=None)


class CompileHonourError(RuntimeError):
    """The honour call could not be sent. The message is a code, never a cell."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DestructiveIntentReply(BaseModel):
    """Schema sent as ``response_format``. The parser does not coerce."""

    model_config = ConfigDict(extra="ignore")
    destructive: bool
    reasons: list[str] = []


class CompileHonourReply(BaseModel):
    """Schema sent as ``response_format``. The parser does not coerce."""

    model_config = ConfigDict(extra="ignore")
    grouping: bool
    filters: bool
    entity: bool
    aggregation: bool
    time: bool
    reasons: list[str] = []


def compile_honour_enabled() -> bool:
    """True only when ``DMS_COMPILE_HONOUR_CHECK`` is the digit 1. Unset is off."""
    return os.environ.get(ENV, "0").strip() == "1"


def bind_compile_honour_transport(transport: Transport) -> Token[Transport | None]:
    return _TRANSPORT.set(transport)


def reset_compile_honour_transport(token: Token[Transport | None]) -> None:
    _TRANSPORT.reset(token)


def ast_non_select(text: str, dialect: str | None = None) -> bool:
    """True when sqlglot types a statement that is not a SELECT.

    A parse error is not a statement. A fallback ``Command`` is not a
    typed statement. Those stay for the model.
    """
    raw = str(text or "").strip()
    if not raw:
        return False
    try:
        with _quiet_sqlglot():
            trees = [
                tree
                for tree in parse(raw, read=dialect or SERVING_DIALECT)
                if tree is not None
            ]
    except Exception:
        return False
    return any(_non_select_statement(tree) for tree in trees)


def column_semantics(
    sql: str,
    *,
    dialect: str | None = None,
    measure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Grouping, filters, entity, aggregation, and time. From the SQL tree.

    The question is not an argument. A parse failure is empty facts.
    """
    read = dialect or SERVING_DIALECT
    empty: dict[str, Any] = {
        "parsed": False,
        "tables": [],
        "select": [],
        "group_by": [],
        "aggregates": [],
        "filters": {"where": "", "having": ""},
        "entity": [],
        "aggregation": [],
        "time": [],
        "order_by": [],
        "limit": None,
        "measure": _measure_public(measure),
    }
    try:
        with _quiet_sqlglot():
            trees = [tree for tree in parse(sql or "", read=read) if tree is not None]
    except Exception:
        return empty
    if len(trees) != 1 or not isinstance(trees[0], exp.Query):
        return empty
    tree = trees[0]
    tables = _names(tree.find_all(exp.Table), read)
    group = _group_names(tree, read)
    aggregates = _agg_names(tree, read)
    where = _clause(tree, "where", read)
    having = _clause(tree, "having", read)
    order = tree.args.get("order")
    order_by: list[str] = []
    if isinstance(order, exp.Order):
        order_by = _sql_list(order.expressions, read)
    return {
        "parsed": True,
        "tables": tables,
        "select": _select_names(tree, read),
        "group_by": group,
        "aggregates": aggregates,
        "filters": {"where": where, "having": having},
        "entity": tables,
        "aggregation": aggregates,
        "time": _time_names(tree, read),
        "order_by": order_by,
        "limit": _limit_value(tree.args.get("limit")),
        "measure": _measure_public(measure),
    }


def destructive_body(question: str) -> dict[str, Any]:
    """OpenVault body. Role ``reason``. No model id."""
    user = {"question": question if isinstance(question, str) else ""}
    return _body(DESTRUCTIVE_SCHEMA, DestructiveIntentReply, _DESTRUCTIVE_SYSTEM, user)


def honour_body(
    *,
    question: str,
    sql: str,
    semantics: Mapping[str, Any],
) -> dict[str, Any]:
    """OpenVault body. Role ``reason``. No model id."""
    user = {
        "question": question if isinstance(question, str) else "",
        "sql": sql if isinstance(sql, str) else "",
        "semantics": dict(semantics),
    }
    return _body(HONOUR_SCHEMA, CompileHonourReply, _HONOUR_SYSTEM, user)


def classify_destructive(
    question: str,
    *,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """One call, or ``skipped`` when the flag is off.

    ``destructive`` / ``safe`` / ``unknown``. An unreadable reply is unknown.
    """
    if not compile_honour_enabled():
        return {"verdict": "skipped", "reasons": []}
    body = destructive_body(question)
    payload = _send(body, transport)
    if payload is None:
        return {"verdict": "unknown", "reasons": ["compile_honour_call_failed"]}
    return _parse_destructive(payload)


def check_honour(
    question: str,
    sql: str,
    *,
    dialect: str | None = None,
    measure: Mapping[str, Any] | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """One call, or ``skipped`` when the flag is off.

    ``honoured`` only when all five judgements are true. Anything else is
    ``not_honoured`` or ``unknown``.
    """
    if not compile_honour_enabled():
        return {"verdict": "skipped", "reasons": []}
    semantics = column_semantics(sql, dialect=dialect, measure=measure)
    body = honour_body(question=question, sql=sql, semantics=semantics)
    payload = _send(body, transport)
    if payload is None:
        return {"verdict": "unknown", "reasons": ["compile_honour_call_failed"]}
    return _parse_honour(payload)


def shape_guard_hit(env: Mapping[str, Any] | None) -> bool:
    """True when the existing E10 or E12 guard already abstained this envelope."""
    if not isinstance(env, Mapping) or env.get("abstained") is not True:
        return False
    assumptions = env.get("assumptions") or []
    if not isinstance(assumptions, list):
        return False
    blob = "\n".join(str(item) for item in assumptions)
    return any(mark in blob for mark in _SHAPE_MARKS)


@contextmanager
def _quiet_sqlglot() -> Iterator[None]:
    """sqlglot logs the source text when it falls back. That text is the question."""
    level = _SQLGLOT_LOG.level
    _SQLGLOT_LOG.setLevel(logging.ERROR)
    try:
        yield
    finally:
        _SQLGLOT_LOG.setLevel(level)


def _non_select_statement(tree: exp.Expression) -> bool:
    if isinstance(tree, exp.Query) or isinstance(tree, exp.Command):
        return False
    return isinstance(tree, _NON_SELECT)


def _body(
    name: str,
    model: type[BaseModel],
    system: str,
    user: Mapping[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "role": ROLE_ALIAS,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(user, sort_keys=True, separators=(",", ":"), default=str),
            },
        ],
        "schema": name,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "schema": model.model_json_schema(),
            },
        },
    }
    _reject_model_keys(body)
    return body


def _send(body: dict[str, Any], transport: Transport | None) -> Any | None:
    fn = transport if transport is not None else (_TRANSPORT.get() or _default_transport)
    try:
        return fn(body)
    except CompileHonourError:
        return None
    except Exception:
        return None


def _default_transport(body: dict[str, Any]) -> Any:
    from dms_executor.shape_check import ShapeCallError
    from dms_executor.shape_check import default_transport as post_json

    try:
        return post_json(body)
    except ShapeCallError as exc:
        raise CompileHonourError(str(exc.code)) from None


def _parse_destructive(payload: Any) -> dict[str, Any]:
    data = _object(payload)
    if data is None or type(data.get("destructive")) is not bool:
        return {"verdict": "unknown", "reasons": ["unparseable"]}
    reasons = _reasons(data.get("reasons"))
    if reasons is None:
        return {"verdict": "unknown", "reasons": ["unparseable"]}
    verdict = "destructive" if data["destructive"] else "safe"
    return {"verdict": verdict, "reasons": reasons}


def _parse_honour(payload: Any) -> dict[str, Any]:
    data = _object(payload)
    if data is None:
        return {"verdict": "unknown", "reasons": ["unparseable"]}
    flags: dict[str, bool] = {}
    for key in _HONOUR_KEYS:
        value = data.get(key)
        if type(value) is not bool:
            return {"verdict": "unknown", "reasons": ["unparseable"]}
        flags[key] = value
    reasons = _reasons(data.get("reasons"))
    if reasons is None:
        return {"verdict": "unknown", "reasons": ["unparseable"]}
    verdict = "honoured" if all(flags.values()) else "not_honoured"
    return {"verdict": verdict, "reasons": reasons, **flags}


def _object(payload: Any) -> dict[str, Any] | None:
    try:
        data = _unwrap(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _reasons(raw: Any) -> list[str] | None:
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        return None
    return [item.strip()[:_TEXT_CAP] for item in raw[:_LIST_CAP] if item.strip()]


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
                raise CompileHonourError("model_key_refused")
            _reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_model_keys(item)


def _measure_public(measure: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(measure, Mapping):
        return None
    name = measure.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return {
        "name": name.strip()[:_TEXT_CAP],
        "grain": _clip(measure.get("grain")),
        "expression": _clip(measure.get("expression")),
        "description": _clip(measure.get("description")),
    }


def _clip(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:_TEXT_CAP]


def _names(nodes: Any, dialect: str) -> list[str]:
    out: list[str] = []
    for node in nodes:
        text = ""
        try:
            text = node.sql(dialect=dialect)
        except Exception:
            text = str(getattr(node, "name", "") or "")
        text = text.strip()
        if text and text not in out:
            out.append(text[:_TEXT_CAP])
        if len(out) >= _LIST_CAP:
            break
    return out


def _sql_list(nodes: Any, dialect: str) -> list[str]:
    out: list[str] = []
    for node in list(nodes)[:_LIST_CAP]:
        try:
            text = node.sql(dialect=dialect).strip()
        except Exception:
            text = ""
        if text:
            out.append(text[:_TEXT_CAP])
    return out


def _group_names(tree: exp.Expression, dialect: str) -> list[str]:
    group = tree.args.get("group")
    if not isinstance(group, exp.Group):
        return []
    return _sql_list(group.expressions, dialect)


def _select_names(tree: exp.Expression, dialect: str) -> list[str]:
    if not isinstance(tree, exp.Select):
        return []
    return _sql_list(tree.expressions, dialect)


def _agg_names(tree: exp.Expression, dialect: str) -> list[str]:
    return _sql_list(list(tree.find_all(exp.AggFunc)), dialect)


def _time_names(tree: exp.Expression, dialect: str) -> list[str]:
    nodes: list[Any] = []
    for kind in _TIME_NODES:
        nodes.extend(tree.find_all(kind))
    return _sql_list(nodes, dialect)


def _clause(tree: exp.Expression, name: str, dialect: str) -> str:
    node = tree.args.get(name)
    if node is None:
        return ""
    try:
        return node.sql(dialect=dialect).strip()[:_TEXT_CAP]
    except Exception:
        return ""


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
