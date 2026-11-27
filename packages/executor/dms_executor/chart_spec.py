"""CHART-SPEC-01. Default off.

On, one OpenVault call with role ``summarize`` sees column names, types,
and row count. Cell values never enter that prompt. The reply is a typed
spec ``{kind, x, y, series}``. A spec that names a missing column, or that
does not parse, is dropped. The UI then uses today's column-type chart.

Off, ``attach_chart_spec`` returns the same envelope and sends nothing.
The call does not name a model or a provider. OpenVault routes ``summarize``.

Idea only: a model proposes the chart and the user can adjust it, and a
name that is not a result column is dropped. Not a port of another repo.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from decimal import Decimal
from typing import Any

CHART_SPEC_ENV = "DMS_CHART_SPEC"
ROLE_ALIAS = "summarize"
SCHEMA_NAME = "ChartSpec"
_KINDS = ("bar", "line", "pie", "stacked", "multi_line", "scatter")
_CAT = frozenset({"text", "date"})
# Cell shape, not question text. Year-only is not a date (same shape as the UI).
_ISO_DATE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
_SYSTEM = (
    "Choose one chart for this result. "
    "You receive column names, types, and row_count. "
    "You do not receive cell values. "
    "Reply with a JSON object and nothing else. "
    "Keys are kind, x, y, series. "
    "kind is one of bar, line, pie, stacked, multi_line, scatter. "
    "x is one column name from the list. "
    "y is an array of column names whose type is number. "
    "series is one column name or null. "
    "Use only names from the list. "
    "Do not include a number."
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
REFUSE_KEY_MISSING = "ov_api_key_missing"
REFUSE_UNAUTHORIZED = "ov_unauthorized"
REFUSE_UNREACHABLE = "ov_unreachable"
_FENCE = "```"

Transport = Callable[[dict[str, Any]], Any]
_TRANSPORT: ContextVar[Transport | None] = ContextVar("dms_chart_spec_transport", default=None)


class ChartSpecError(RuntimeError):
    """The chart call could not be sent. The message is a code, never a cell."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def chart_spec_enabled() -> bool:
    """True only when ``DMS_CHART_SPEC`` is the digit 1. Unset is off."""
    return os.environ.get(CHART_SPEC_ENV, "0").strip() == "1"


def bind_chart_transport(transport: Transport) -> Token[Transport | None]:
    return _TRANSPORT.set(transport)


def reset_chart_transport(token: Token[Transport | None]) -> None:
    _TRANSPORT.reset(token)


def column_profile(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Names, types, and row count. Cell values are not copied out."""
    names: list[str] = []
    counted = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        counted += 1
        for key in row:
            if isinstance(key, str) and key and key not in names:
                names.append(key)
    columns = [{"name": name, "type": _column_type(rows, name)} for name in names]
    return {"columns": columns, "row_count": counted}


def request_body(profile: Mapping[str, Any]) -> dict[str, Any]:
    """OpenVault body. Role ``summarize``. No model id and no cell values."""
    safe_columns: list[dict[str, str]] = []
    raw_columns = profile.get("columns")
    if isinstance(raw_columns, list):
        for col in raw_columns:
            if not isinstance(col, Mapping):
                continue
            name = col.get("name")
            typ = col.get("type")
            if isinstance(name, str) and name and isinstance(typ, str):
                safe_columns.append({"name": name, "type": typ})
    count = profile.get("row_count")
    row_count = count if isinstance(count, int) and not isinstance(count, bool) else 0
    user = json.dumps(
        {"columns": safe_columns, "row_count": row_count},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    body: dict[str, Any] = {
        "role": ROLE_ALIAS,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "schema": SCHEMA_NAME,
    }
    _reject_model_keys(body)
    return body


def validate_spec(spec: Any, profile: Mapping[str, Any]) -> dict[str, Any] | None:
    """A spec whose columns exist and whose shape can be drawn, else None.

    Extra keys are dropped, including any number the model added.
    """
    try:
        payload = _unwrap(spec)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    parsed = _parse(payload)
    if parsed is None:
        return None
    kind, x, y, series = parsed
    types = _types(profile)
    if not _shape_ok(kind, x, y, series, types):
        return None
    return {"kind": kind, "x": x, "y": list(y), "series": series}


def adjust_catalog(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Kinds and columns that appear in at least one spec ``validate_spec`` accepts.

    ponytail: pairwise scan of the result's columns. Ceiling is a very wide
    result (hundreds of columns). Upgrade: emit the type sets directly.
    """
    types = _types(profile)
    dummy = {
        "columns": [{"name": name, "type": typ} for name, typ in types.items()],
        "row_count": 0,
    }
    kinds: list[str] = []
    xs: list[str] = []
    ys: list[str] = []
    series: list[str] = []
    for kind in _KINDS:
        found = False
        for candidate in _candidates(kind, types):
            ok = validate_spec(candidate, dummy)
            if ok is None:
                continue
            found = True
            if ok["x"] not in xs:
                xs.append(ok["x"])
            for name in ok["y"]:
                if name not in ys:
                    ys.append(name)
            chosen = ok.get("series")
            if isinstance(chosen, str) and chosen not in series:
                series.append(chosen)
        if found:
            kinds.append(kind)
    return {"kinds": kinds, "x": xs, "y": ys, "series": series}


def propose(
    profile: Mapping[str, Any],
    transport: Transport | None = None,
) -> dict[str, Any] | None:
    """One call. Flag off, or any failure, returns None and does not retry."""
    if not chart_spec_enabled():
        return None
    body = request_body(profile)
    fn = transport if transport is not None else (_TRANSPORT.get() or default_transport)
    try:
        payload = fn(body)
    except ChartSpecError:
        return None
    except Exception:
        return None
    return validate_spec(payload, profile)


def attach_chart_spec(env: dict[str, Any]) -> dict[str, Any]:
    """Flag off: the same object. Flag on: ``chart_spec`` and ``chart_adjust``.

    ``chart`` and ``route`` are not rewritten. A missing column leaves
    ``chart_spec`` unset so the UI keeps today's chart.
    """
    if not chart_spec_enabled():
        return env
    if env.get("abstained") is True:
        return env
    rows = env.get("rows")
    if not isinstance(rows, list) or not rows:
        return env
    profile = column_profile(rows)
    catalog = adjust_catalog(profile)
    if not catalog["kinds"]:
        return env
    proposal = propose(profile)
    out = dict(env)
    out["chart_adjust"] = catalog
    if proposal is not None:
        out["chart_spec"] = proposal
    return out


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
        raise ChartSpecError("openvault_url_missing")
    return base + CHAT_PATH


def default_transport(body: dict[str, Any]) -> Any:
    """POST the body. The code never carries the key or the reply text."""
    import httpx

    url = endpoint_url()
    key = load_api_key()
    if not key:
        raise ChartSpecError(REFUSE_KEY_MISSING)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=8.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise ChartSpecError(REFUSE_UNAUTHORIZED) from None
        raise ChartSpecError(f"ov_http_{status}") from None
    except httpx.HTTPError:
        raise ChartSpecError(REFUSE_UNREACHABLE) from None
    try:
        data = response.json()
    except ValueError:
        raise ChartSpecError("chart_spec_unreadable") from None
    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
        message = data["choices"][0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            try:
                return json.loads(_strip_fence(content))
            except json.JSONDecodeError:
                raise ChartSpecError("chart_spec_unreadable") from None
        if isinstance(content, dict):
            return content
    return data


def _column_type(rows: Sequence[Mapping[str, Any]], name: str) -> str:
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or name not in row:
            continue
        kind = _value_type(row[name])
        if kind is None:
            continue
        seen.add(kind)
        if len(seen) > 1:
            return "other"
    if not seen:
        return "empty"
    return next(iter(seen))


def _value_type(val: Any) -> str | None:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return "other"
    if isinstance(val, (int, float, Decimal)):
        return "number"
    if isinstance(val, str):
        if _ISO_DATE.match(val.strip()):
            return "date"
        return "text"
    return "other"


def _types(profile: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    columns = profile.get("columns")
    if not isinstance(columns, list):
        return out
    for col in columns:
        if not isinstance(col, Mapping):
            continue
        name = col.get("name")
        typ = col.get("type")
        if isinstance(name, str) and name and isinstance(typ, str) and name not in out:
            out[name] = typ
    return out


def _shape_ok(
    kind: str,
    x: str,
    y: list[str],
    series: str | None,
    types: Mapping[str, str],
) -> bool:
    if x not in types or any(name not in types for name in y):
        return False
    if series is not None and series not in types:
        return False
    if any(types[name] != "number" for name in y):
        return False
    if len(set(y)) != len(y) or x in y:
        return False
    if series is not None and (series == x or series in y):
        return False
    xt = types[x]
    cat = xt in _CAT
    if kind in {"bar", "pie"}:
        return cat and len(y) == 1 and series is None
    if kind == "line":
        return xt in {"text", "date", "number"} and len(y) == 1 and series is None
    if kind == "scatter":
        return xt == "number" and len(y) == 1 and series is None
    if kind == "stacked":
        if not cat:
            return False
    elif kind == "multi_line":
        if xt not in {"text", "date", "number"}:
            return False
    else:
        return False
    if series is None:
        return len(y) >= 2
    return len(y) == 1 and types.get(series) in _CAT


def _parse(payload: Any) -> tuple[str, str, list[str], str | None] | None:
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")
    if kind not in _KINDS:
        return None
    x = payload.get("x")
    if not isinstance(x, str) or not x:
        return None
    raw_y = payload.get("y")
    if isinstance(raw_y, str):
        raw_y = [raw_y]
    if not isinstance(raw_y, list) or not raw_y:
        return None
    y: list[str] = []
    for item in raw_y:
        if not isinstance(item, str) or not item:
            return None
        y.append(item)
    series = payload.get("series", None)
    if series is None or series == "":
        series_out: str | None = None
    elif isinstance(series, str):
        series_out = series
    else:
        return None
    return kind, x, y, series_out


def _candidates(kind: str, types: Mapping[str, str]) -> list[dict[str, Any]]:
    numbers = [name for name, typ in types.items() if typ == "number"]
    cats = [name for name, typ in types.items() if typ in _CAT]
    axes = [name for name, typ in types.items() if typ in {"text", "date", "number"}]
    out: list[dict[str, Any]] = []
    if kind in {"bar", "pie"}:
        for x in cats:
            for y in numbers:
                out.append({"kind": kind, "x": x, "y": [y]})
    elif kind == "line":
        for x in axes:
            for y in numbers:
                if x != y:
                    out.append({"kind": kind, "x": x, "y": [y]})
    elif kind == "scatter":
        for x in numbers:
            for y in numbers:
                if x != y:
                    out.append({"kind": kind, "x": x, "y": [y]})
    else:
        xs = cats if kind == "stacked" else axes
        for axis in xs:
            measures = [name for name in numbers if name != axis]
            if len(measures) >= 2:
                out.append({"kind": kind, "x": axis, "y": measures})
            for measure in measures:
                for series in cats:
                    out.append({"kind": kind, "x": axis, "y": [measure], "series": series})
    return out


def _reject_model_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _MODEL_KEYS:
                raise ChartSpecError("model_key_refused")
            _reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_model_keys(item)


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, str):
        return json.loads(_strip_fence(payload))
    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return json.loads(_strip_fence(content))
                if isinstance(content, dict):
                    return content
    return payload


def _strip_fence(text: str) -> str:
    """Drop one opening fence line and one trailing fence. No regex."""
    body = text.strip()
    if not body.startswith(_FENCE):
        return body
    newline = body.find("\n")
    if newline < 0:
        return body
    rest = body[newline + 1 :].rstrip()
    if rest.endswith(_FENCE):
        rest = rest[: -len(_FENCE)].rstrip()
    return rest
