"""Answer spec from a different route role than the SQL writer.

DMS does not pick or name a model. The spec call is the existing
Insights generate route with ``role=intent_spec``. Writer calls use
``role=sql_writer``. OpenVault #148 routes those roles to different
models. A second route, if one is pinned before that lands, lives in
OpenVault config, not in this module.

``check_sql_against_spec`` is the checker ``run_model_loop`` can call.
``apply_intent_spec`` does that check, one retry budget, and names the
abstain. The caller builds the envelope with ``build_abstain``. This
module does not log a ticket and does not mint an id. ``DMS_INTENT_SPEC``
defaults off; callers skip this module entirely when it is off.

Span check is case and whitespace only. A field whose spans are not
substrings of the question is dropped and logged. A kept filter span
must sit entirely inside one SQL filter literal; a shorter literal is
a narrowed span and the ask is not served. Nothing is guessed from
question words.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from dms_core.pii import fail_closed_mask_payload

from dms_executor.sql_grounds import RankBound, SqlGrounds, sql_grounds

_log = logging.getLogger(__name__)

ROLE_SQL_WRITER = "sql_writer"
ROLE_INTENT_SPEC = "intent_spec"
ENV_FLAG = "DMS_INTENT_SPEC"
# Same ceiling as C-LOOP-B MAX_SQL_RETRIES. Does not change DMS_INSIGHTS_CALL_CAP.
MAX_SPEC_RETRIES = 2
_ROUTE_KEYS = ("dms_route_role", "dms_route_prompt", "dms_route_single_shot")
_SECRET_PARTS = ("secret", "password", "api_key", "authorization", "access_token")


def intent_spec_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``DMS_INTENT_SPEC`` is ``1``. Default off."""
    src = os.environ if env is None else env
    return str(src.get(ENV_FLAG) or "").strip() == "1"


def route_overrides(catalog: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Split routing keys off a retrieve catalog. Absent keys leave it as-is."""
    if not isinstance(catalog, dict):
        return catalog, {}
    extra = {key: catalog[key] for key in _ROUTE_KEYS if key in catalog}
    if not extra:
        return catalog, {}
    cleaned = {key: value for key, value in catalog.items() if key not in _ROUTE_KEYS}
    return cleaned, extra


@dataclass(frozen=True)
class SpecFilter:
    description: str
    literal: str
    spans: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerSpec:
    measure: str | None = None
    direction: str | None = None
    n: int | None = None
    offset: int | None = None
    filters: tuple[SpecFilter, ...] = ()
    grain: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "measure": self.measure,
            "direction": self.direction,
            "n": self.n,
            "offset": self.offset,
            "filters": [
                {
                    "description": item.description,
                    "literal": item.literal,
                    "spans": list(item.spans),
                }
                for item in self.filters
            ],
            "grain": self.grain,
        }


@dataclass(frozen=True)
class ParsedSpec:
    spec: AnswerSpec
    dropped: tuple[dict[str, str], ...]
    unverified: str | None
    model: str | None
    provider: str | None
    route: str | None
    key: str | None
    tokens: dict[str, Any] | None


@dataclass(frozen=True)
class IntentDecision:
    sql: str | None
    payload: dict[str, Any] | None
    abstain_reason: str | None
    attempt: dict[str, Any]
    offset_approved: bool = False
    retries: int = 0
    rejected_sql: str | None = None


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _span_ok(span: str, question: str) -> bool:
    folded = _norm(span)
    return bool(folded) and folded in _norm(question)


def _secret_key(name: str) -> bool:
    low = name.lower()
    return any(part in low for part in _SECRET_PARTS)


def masked_samples(
    rows: list[dict[str, Any]] | None,
    text_columns: set[str] | None,
) -> list[dict[str, Any]]:
    """Drop text columns, then mask whatever remains. No samples when empty."""
    text = {str(name).casefold() for name in (text_columns or set())}
    kept: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        kept.append(
            {str(key): value for key, value in row.items() if str(key).casefold() not in text}
        )
    kept = [row for row in kept if row]
    if not kept:
        return []
    masked = fail_closed_mask_payload(rows=kept)
    out = masked.get("rows")
    if not isinstance(out, list):
        return []
    return [row for row in out if isinstance(row, dict)]


def spec_prompt(
    question: str,
    *,
    samples: list[dict[str, Any]] | None = None,
    text_columns: set[str] | None = None,
) -> str:
    """Instructions plus the question. Samples are masked. Text columns are omitted."""
    lines = [
        "Return one JSON object and nothing else.",
        "Keys: measure, direction, n, offset, filters, grain.",
        "direction is asc, desc, or none.",
        "n and offset are integers or null.",
        "filters is a list of objects with description, literal, and spans.",
        "measure, direction, n, offset, and grain each have value and spans.",
        "Every span is a verbatim substring copied from the question.",
        "A filter span is the question phrase one filter literal must cover in full.",
        "question:",
        question,
    ]
    masked = masked_samples(samples, text_columns)
    if masked:
        lines.append("samples:")
        lines.append(json.dumps(masked, default=str))
    return "\n".join(lines)


def _json_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        got = json.loads(text)
    except json.JSONDecodeError:
        return None
    return got if isinstance(got, dict) else None


def _spec_body(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("intent_spec")
    if isinstance(raw, dict):
        return raw
    parsed = _json_object(raw)
    if parsed is not None:
        return parsed
    for key in ("answer", "text", "content"):
        parsed = _json_object(payload.get(key))
        if parsed is None:
            continue
        if any(
            name in parsed
            for name in ("measure", "direction", "n", "offset", "filters", "grain")
        ):
            return parsed
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _lookup(payload: Mapping[str, Any] | None, names: tuple[str, ...]) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for name in names:
        if _secret_key(name):
            continue
        got = _text(payload.get(name))
        if got:
            return got
    gen = payload.get("generative")
    if isinstance(gen, Mapping):
        for name in names:
            got = _text(gen.get(name))
            if got:
                return got
    return None


def _model_id(payload: Mapping[str, Any] | None) -> str | None:
    return _lookup(payload, ("served_model", "model"))


def _tokens(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("tokens")
    if not isinstance(raw, dict):
        raw = payload.get("usage")
    if not isinstance(raw, dict):
        return None
    return {str(key): value for key, value in raw.items() if not _secret_key(str(key))}


def _spans(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str) and item.strip()]
    return []


def _field_map(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    return None


def _keep_spans(spans: list[str], question: str) -> bool:
    return any(_span_ok(span, question) for span in spans)


def _drop(dropped: list[dict[str, str]], field: str, reason: str) -> None:
    dropped.append({"field": field, "reason": reason})
    _log.info("intent_spec dropped_field field=%s reason=%s", field, reason)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _parse_fields(
    body: Mapping[str, Any], question: str
) -> tuple[AnswerSpec, tuple[dict[str, str], ...]]:
    dropped: list[dict[str, str]] = []
    measure: str | None = None
    direction: str | None = None
    limit: int | None = None
    offset: int | None = None
    grain: str | None = None
    filters: list[SpecFilter] = []

    raw_measure = _field_map(body.get("measure"))
    if raw_measure is not None:
        spans = _spans(raw_measure.get("spans"))
        if _keep_spans(spans, question):
            measure = _text(raw_measure.get("value"))
        elif spans or raw_measure.get("value") not in (None, ""):
            _drop(dropped, "measure", "span_not_in_question")

    raw_direction = _field_map(body.get("direction"))
    if raw_direction is not None:
        spans = _spans(raw_direction.get("spans"))
        value = _text(raw_direction.get("value"))
        folded = value.casefold() if value else ""
        if folded not in {"asc", "desc", "none"}:
            if value is not None or spans:
                _drop(dropped, "direction", "invalid_value")
        elif not _keep_spans(spans, question):
            _drop(dropped, "direction", "span_not_in_question")
        else:
            direction = folded

    for name, target in (("n", "n"), ("offset", "offset")):
        raw = _field_map(body.get(name))
        if raw is None:
            continue
        spans = _spans(raw.get("spans"))
        if raw.get("value") is None and not spans:
            continue
        number = _positive_int(raw.get("value"))
        if number is None:
            _drop(dropped, target, "invalid_value")
            continue
        if not _keep_spans(spans, question):
            _drop(dropped, target, "span_not_in_question")
            continue
        if name == "n":
            limit = number
        else:
            offset = number

    raw_filters = body.get("filters")
    if isinstance(raw_filters, list):
        for index, item in enumerate(raw_filters):
            field = f"filters[{index}]"
            if not isinstance(item, dict):
                _drop(dropped, field, "invalid_value")
                continue
            spans = _spans(item.get("spans"))
            literal = item.get("literal")
            description = _text(item.get("description")) or ""
            if not isinstance(literal, str) or not literal.strip():
                _drop(dropped, field, "no_literal")
                continue
            cited = tuple(span for span in spans if _span_ok(span, question))
            if not cited:
                _drop(dropped, field, "span_not_in_question")
                continue
            filters.append(SpecFilter(description, literal.strip(), cited))

    raw_grain = _field_map(body.get("grain"))
    if raw_grain is not None:
        spans = _spans(raw_grain.get("spans"))
        if _keep_spans(spans, question):
            grain = _text(raw_grain.get("value"))
        elif spans or raw_grain.get("value") not in (None, ""):
            _drop(dropped, "grain", "span_not_in_question")

    spec = AnswerSpec(measure, direction, limit, offset, tuple(filters), grain)
    return spec, tuple(dropped)


def parse_spec_payload(
    payload: Mapping[str, Any] | None,
    question: str,
    *,
    writer_payload: Mapping[str, Any] | None,
) -> ParsedSpec:
    """Accept a route payload. Fail closed when the models cannot be told apart."""
    model = _model_id(payload)
    provider = _lookup(payload, ("served_provider", "provider"))
    route = _lookup(payload, ("ov_route", "route_id", "route_store_id"))
    key = _lookup(payload, ("ov_key_id", "key_id", "route_key_id"))
    tokens = _tokens(payload)
    empty = AnswerSpec()
    writer = _model_id(writer_payload)
    if not isinstance(payload, Mapping):
        return ParsedSpec(empty, (), "spec_missing", model, provider, route, key, tokens)
    body = _spec_body(payload)
    if body is None:
        return ParsedSpec(empty, (), "spec_missing", model, provider, route, key, tokens)
    if not model:
        spec, dropped = _parse_fields(body, question)
        return ParsedSpec(spec, dropped, "model_unreported", model, provider, route, key, tokens)
    if not writer:
        spec, dropped = _parse_fields(body, question)
        return ParsedSpec(
            spec, dropped, "writer_model_unreported", model, provider, route, key, tokens
        )
    if model == writer:
        spec, dropped = _parse_fields(body, question)
        return ParsedSpec(spec, dropped, "same_model", model, provider, route, key, tokens)
    spec, dropped = _parse_fields(body, question)
    return ParsedSpec(spec, dropped, None, model, provider, route, key, tokens)


def _attempt(parsed: ParsedSpec, outcome: str) -> dict[str, Any]:
    return {
        "role": ROLE_INTENT_SPEC,
        "model": parsed.model,
        "provider": parsed.provider,
        "route": parsed.route,
        "key": parsed.key,
        "tokens": parsed.tokens,
        "spec": parsed.spec.as_dict(),
        "dropped_fields": [dict(item) for item in parsed.dropped],
        "outcome": outcome,
    }


def _ints(literals: tuple[str, ...]) -> list[int] | None:
    found: list[int] = []
    for literal in literals:
        try:
            found.append(int(str(literal)))
        except ValueError:
            return None
    return found


def _one_bound(bound: RankBound) -> tuple[int | None, int | None, str | None]:
    nums = _ints(bound.literals)
    if not nums:
        return None, None, "SQL limit is unclear"
    if bound.operator == "lte" and len(nums) == 1 and nums[0] >= 0:
        return nums[0], 0, None
    if bound.operator == "lt" and len(nums) == 1 and nums[0] >= 1:
        return nums[0] - 1, 0, None
    if bound.operator == "eq" and len(nums) == 1 and nums[0] >= 1:
        return 1, nums[0] - 1, None
    if bound.operator == "gte" and len(nums) == 1:
        return None, None, "SQL limit is unclear"
    if bound.operator == "between" and len(nums) >= 2 and nums[1] >= nums[0] >= 1:
        return nums[1] - nums[0] + 1, nums[0] - 1, None
    return None, None, "SQL limit is unclear"


def _paired_bounds(bounds: tuple[RankBound, ...]) -> tuple[int | None, int | None, str | None]:
    lowers = [item for item in bounds if item.operator in {"gt", "gte"}]
    uppers = [item for item in bounds if item.operator in {"lt", "lte"}]
    if len(bounds) != 2 or len(lowers) != 1 or len(uppers) != 1:
        return None, None, "SQL limit is unclear"
    lo = _ints(lowers[0].literals)
    hi = _ints(uppers[0].literals)
    if not lo or not hi:
        return None, None, "SQL limit is unclear"
    start = lo[0] + 1 if lowers[0].operator == "gt" else lo[0]
    end = hi[0] - 1 if uppers[0].operator == "lt" else hi[0]
    if end < start or start < 1:
        return None, None, "SQL limit is unclear"
    return end - start + 1, start - 1, None


def _window_limit(grounds: SqlGrounds) -> tuple[int | None, int | None, str | None]:
    if not grounds.rank_bounds:
        return None, None, None
    if len(grounds.rank_bounds) == 1:
        return _one_bound(grounds.rank_bounds[0])
    return _paired_bounds(grounds.rank_bounds)


def _limit_offset(grounds: SqlGrounds) -> tuple[int | None, int | None, str | None]:
    if grounds.limit is not None or grounds.offset is not None:
        return grounds.limit, 0 if grounds.offset is None else grounds.offset, None
    limit, offset, unclear = _window_limit(grounds)
    if unclear or limit is not None:
        return limit, offset, unclear
    if len(grounds.derived_limits) == 1:
        return grounds.derived_limits[0], 0, None
    if len(grounds.derived_limits) > 1:
        return None, None, "SQL limit is unclear"
    return None, 0, None


def _sql_direction(grounds: SqlGrounds) -> tuple[str | None, bool]:
    if grounds.order_by:
        return grounds.order_by[0][1], False
    dirs = [item.direction for item in grounds.rank_bounds] or [
        item.direction for item in grounds.rank_windows
    ]
    if not dirs:
        return None, False
    if len(set(dirs)) == 1:
        return dirs[0], False
    return None, True


def _has_literal(grounds: SqlGrounds, literal: str) -> bool:
    want = _norm(literal)
    if not want:
        return False
    for conjunct in grounds.conjuncts:
        for item in conjunct.literals:
            if _norm(item) == want:
                return True
    return False


def _literal_covers_span(literal: str, span: str) -> bool:
    """True when the whole cited span sits inside this one filter literal.

    Case and whitespace only. A shorter literal is a narrowed span, not a cover.
    """
    cited = _norm(span)
    got = _norm(literal)
    return bool(cited and got and cited in got)


def _span_coverage_gaps(grounds: SqlGrounds, spec: AnswerSpec) -> list[str]:
    """Cited filter spans that no SQL literal covers in full."""
    literals = [item for conjunct in grounds.conjuncts for item in conjunct.literals]
    gaps: list[str] = []
    for filt in spec.filters:
        for span in filt.spans:
            if any(_literal_covers_span(literal, span) for literal in literals):
                continue
            gaps.append(f"spec span {span!r} is not fully covered by a filter literal")
    return gaps


def check_sql_against_spec(
    sql: str,
    spec: AnswerSpec,
    *,
    dialect: str = "duckdb",
) -> str | None:
    """Plain mismatch reason, or None when the SQL agrees with the spec.

    C-LOOP-B (#405) passes this as ``run_model_loop``'s ``check`` once that
    loop is on the branch. A returned string is the retry feedback.
    """
    grounds = sql_grounds(sql, dialect=dialect)
    if grounds.unclear:
        return "SQL structure is unclear"
    if grounds.contradiction:
        return "contradiction"
    reasons: list[str] = []
    direction, direction_unclear = _sql_direction(grounds)
    if direction_unclear and spec.direction in {"asc", "desc", "none"}:
        reasons.append("SQL order is unclear")
    elif spec.direction in {"asc", "desc"}:
        word = "ascending" if spec.direction == "asc" else "descending"
        if direction is None:
            reasons.append(f"spec says {word}, SQL has no order")
        elif direction != spec.direction:
            reasons.append(f"spec says {word}, SQL orders {direction.upper()}")
    elif spec.direction == "none" and direction is not None:
        limit, offset, _unclear = _limit_offset(grounds)
        if limit is not None or (offset or 0) > 0:
            reasons.append(f"spec says no direction, SQL orders {direction.upper()}")
    if spec.n is not None or spec.offset is not None:
        limit, offset, unclear = _limit_offset(grounds)
        if unclear:
            reasons.append(unclear)
        else:
            if spec.n is not None and limit != spec.n:
                shown = "none" if limit is None else str(limit)
                reasons.append(f"spec says limit {spec.n}, SQL limits {shown}")
            if spec.offset is not None and (offset or 0) != spec.offset:
                got = 0 if offset is None else offset
                reasons.append(f"spec says offset {spec.offset}, SQL offset is {got}")
    for item in spec.filters:
        if not _has_literal(grounds, item.literal):
            reasons.append(f"spec filter {item.literal!r} is not in the SQL")
    reasons.extend(_span_coverage_gaps(grounds, spec))
    if not reasons:
        return None
    return "; ".join(reasons)


def _sql_of(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for name in ("query_sql", "sql"):
        got = _text(payload.get(name))
        if got:
            return got
    gen = payload.get("generative")
    if isinstance(gen, Mapping):
        got = _text(gen.get("query_sql")) or _text(gen.get("sql"))
        if got:
            return got
    return None


def feedback_prompt(question: str, previous_sql: str, reason: str) -> str:
    """Retry text: the question, the SQL, and the checker reason."""
    return f"{question}\n\nprevious_sql:\n{previous_sql}\n\nfeedback:\n{reason}"


def apply_intent_spec(
    *,
    question: str,
    sql: str,
    writer_payload: Mapping[str, Any] | None,
    spec_fetch: Callable[[str], Mapping[str, Any] | None],
    retry_fetch: Callable[[str], Mapping[str, Any] | None] | None,
    dialect: str = "duckdb",
    samples: list[dict[str, Any]] | None = None,
    text_columns: set[str] | None = None,
) -> IntentDecision:
    """Fetch the spec, check, retry, or name an abstain. Never serves a mismatch."""
    prompt = spec_prompt(question, samples=samples, text_columns=text_columns)
    try:
        spec_payload = spec_fetch(prompt)
    except Exception:  # noqa: BLE001 — a broken spec route is unavailable
        spec_payload = None
    parsed = parse_spec_payload(
        spec_payload if isinstance(spec_payload, Mapping) else None,
        question,
        writer_payload=writer_payload,
    )
    reason: str | None
    if parsed.unverified:
        reason = f"intent_spec_unverified:{parsed.unverified}"
        return IntentDecision(
            None,
            None,
            reason,
            _attempt(parsed, reason),
            False,
            retries=0,
            rejected_sql=sql,
        )
    reason = check_sql_against_spec(sql, parsed.spec, dialect=dialect)
    current_sql = sql
    current_payload: Mapping[str, Any] | None = writer_payload
    retries = 0
    while reason and retry_fetch is not None and retries < MAX_SPEC_RETRIES:
        retries += 1
        try:
            nxt = retry_fetch(feedback_prompt(question, current_sql, reason))
        except Exception:  # noqa: BLE001
            nxt = None
        nxt_sql = _sql_of(nxt if isinstance(nxt, Mapping) else None)
        current_payload = nxt if isinstance(nxt, Mapping) else None
        if not nxt_sql:
            reason = "no_sql"
            continue
        current_sql = nxt_sql
        reason = check_sql_against_spec(current_sql, parsed.spec, dialect=dialect)
    if reason:
        named = f"intent_spec_mismatch:{reason}"
        return IntentDecision(
            None,
            dict(current_payload or {}),
            named,
            _attempt(parsed, named),
            False,
            retries=retries,
            rejected_sql=current_sql,
        )
    payload_out = dict(current_payload) if isinstance(current_payload, Mapping) else None
    return IntentDecision(
        current_sql,
        payload_out,
        None,
        _attempt(parsed, "checked"),
        parsed.spec.offset is not None,
        retries=retries,
    )
