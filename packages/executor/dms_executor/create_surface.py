"""CREATE-SURFACE-01. Default off.

``DMS_CREATE`` is on only when it is the digit 1. Off, every entry raises
``CreateDisabled`` before a model call, a read, or a write.

On, the harness builds a live app: one cheap plan, then one component at a
time, then the user confirms the file. Doc and slides are not this module.
OpenVault sees a role string and the
caller key. DMS does not name a model or a provider. Figures come from
executed SQL or from ``forecast_series``, and each one keeps its SQL and
source row. A claim that is not in those figures is not shown. One log row
per run records the model, tokens, and cost only when the response included
them, plus the measured seconds.

Accepted SQL is stored as ``create_accept``. Lookup never replays it.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import statistics
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from dms_executor.demo_warehouse import SERVING_DIALECT

FLAG = "DMS_CREATE"
STORE_ENV = "DMS_CREATE_STORE"
WAREHOUSE_ENV = "DMS_CREATE_WAREHOUSE"
GRANTS_ENV = "DMS_CREATE_GRANTS"
API_KEY_PREFIX = "ov_"
KIND_APP = "app"
KIND_DOC = "doc"
KIND_SLIDES = "slides"
KINDS = frozenset({KIND_APP})
FORECAST_METHODS = frozenset({"naive", "drift", "mean", "seasonal_naive"})
CHART_KINDS = frozenset({"bar", "line"})
ROLE_INTENT = "create_intent"
ROLE_PLAN = "create_plan"
ROLE_GENERATE = "create_generate"
ROLE_IMPROVE = "create_improve"
ROLE_ESCALATE = "escalate"
_ASSUMPTIONS = {
    "naive": (
        "The next points stay at the last observed value. "
        "The band is 1.96 residual standard deviations."
    ),
    "drift": (
        "A straight line fitted to the history continues. "
        "The band is 1.96 residual standard deviations."
    ),
    "mean": (
        "Future points stay at the historical mean. "
        "The band is 1.96 residual standard deviations."
    ),
    "seasonal_naive": (
        "Each point repeats the value from one period earlier. "
        "The band is 1.96 residual standard deviations."
    ),
}
_INTENT_SYSTEM = (
    "Restate the goal, the audience, and the decision this artifact must support. "
    "Reply with a JSON object and nothing else. "
    "Keys are goal, audience, decision, clarify. "
    "goal, audience, and decision are short strings. "
    "clarify is one question string when one fact is missing, otherwise null. "
    "Ask at most one question. Do not write a figure."
)
_PLAN_SYSTEM = (
    "One cheap plan. Reply with a JSON object and nothing else. "
    "Keys are goal, audience, decision, clarify, tables, joins, metrics, "
    "outline, estimated_tokens, estimated_cost. "
    "goal, audience, and decision are short strings. "
    "clarify is one question when one fact is missing, otherwise null. "
    "tables is an array of table names from the message. "
    "joins is an array of objects with keys left, right, on. "
    "metrics is an array of objects with keys name and sql. "
    "outline is an array of objects with keys id and title, one piece each. "
    "estimated_tokens and estimated_cost are numbers for the build, or null. "
    "sql is one read-only SELECT. Use only granted tables. Do not write the artifact."
)
_GENERATE_SYSTEM = (
    "Generate one piece, not the whole artifact. "
    "Reply with a JSON object and nothing else. "
    "Use {{c:COLUMN:ROW}} for a figure from that statement's rows. "
    "Use {{f:ID:FIELD}} for a forecast field. "
    "Do not type a figure yourself. "
    "kind app keys: title, filters, widgets, forecasts. "
    "kind doc keys: title, summary_sql, exec_summary, findings, actions, forecasts. "
    "kind slides keys: title, slides. "
    "A filter has id and sql. The sql returns a column named value. "
    "A widget has id, title, sql, chart. chart has kind, x, y. "
    "kind is bar or line. y is an array of one column. "
    "A finding has text and sql. "
    "A slide has title, insight, notes, and either sql plus chart, or forecast. "
    "A forecast has id, title, sql, time, value, method, horizon. "
    "method is naive, drift, mean, or seasonal_naive. "
    "seasonal_naive also has period. "
    "One insight per slide. Keep every title and insight inside one slide."
)
_STOP_CODES = frozenset(
    {"budget_steps", "budget_tokens", "budget_usd", "budget_exceeded"}
)
_BUDGET_TEXT = {
    "budget_steps": (
        "Stopped. This run reached its step limit. The pieces already checked are kept."
    ),
    "budget_tokens": (
        "Stopped. This run reached its token limit. The pieces already checked are kept."
    ),
    "budget_usd": (
        "Stopped. This run reached its cost limit. The pieces already checked are kept."
    ),
    "budget_exceeded": (
        "Stopped. OpenVault returned 402 budget_exceeded. "
        "The pieces already checked are kept."
    ),
}
_LIMITS = {
    "title": 60,
    "insight": 140,
    "notes": 400,
    "summary": 320,
    "finding": 220,
    "action": 140,
    "widget_title": 60,
    "slides": 8,
    "widgets": 6,
    "findings": 5,
    "actions": 4,
    "filters": 3,
    "horizon": 6,
}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CITE = re.compile(r"\{\{c:([A-Za-z_][A-Za-z0-9_]*):(\d+)\}\}")
_FCITE = re.compile(r"\{\{f:([A-Za-z_][A-Za-z0-9_]*):([A-Za-z_][A-Za-z0-9_]*)\}\}")
_FILTER = re.compile(r"\{\{filter:([A-Za-z_][A-Za-z0-9_]*)\}\}")
_NUM = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w]|\.\d)")
_MONTH = re.compile(r"^\d{4}-\d{2}$")
_FENCE = "```"
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
_LOCK = threading.Lock()
# ponytail: one process lock and a JSON directory. Ceiling is a few dozen
# artifacts on one machine. Upgrade: a Space table in schema dms.
_ROW_CAP = 40

Transport = Callable[[dict[str, Any], str], Any]


class CreateDisabled(RuntimeError):
    """The create flag is off. Nothing ran and nothing was stored."""


class CreateProblem(ValueError):
    """A caller or model failure. ``code`` is the HTTP detail."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CreateCallError(RuntimeError):
    """The model call failed. The message is a code, never a key or a cell."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def create_enabled() -> bool:
    """True only when ``DMS_CREATE`` is the digit 1. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


def forecast_series(
    points: list[tuple[str, float]],
    method: str,
    *,
    horizon: int,
    period: int | None = None,
) -> dict[str, Any] | str:
    """History, forecast, band, assumption, and one-point backtest error.

    The method is the one the model named. This function does not pick a
    different one. A series shorter than 4 points returns ``forecast_too_short``.
    """
    if method not in FORECAST_METHODS:
        return "forecast_method"
    if horizon < 1 or horizon > _LIMITS["horizon"]:
        return "forecast_horizon"
    if method == "seasonal_naive" and (period is None or period < 2 or period > 12):
        return "forecast_period"
    if len(points) < 4:
        return "forecast_too_short"
    labels = [label for label, _value in points]
    values = [value for _label, value in points]
    if method == "seasonal_naive" and period is not None and len(values) - 1 <= period:
        return "forecast_period"
    train = values[:-1]
    predicted = _project(train, method, period, 1)[0]
    actual = values[-1]
    future = _project(values, method, period, horizon)
    sigma = _sigma(values, method, period)
    band = 1.96 * sigma
    ahead = _next_labels(labels, horizon)
    forecast = []
    for index, value in enumerate(future):
        low = value - band
        high = value + band
        forecast.append(
            {
                "time": ahead[index],
                "value": _num(value),
                "low": _num(low),
                "high": _num(high),
            }
        )
    return {
        "method": method,
        "assumptions": _ASSUMPTIONS[method],
        "backtest_error": _num(abs(predicted - actual)),
        "holdout_actual": _num(actual),
        "holdout_predicted": _num(predicted),
        "history": [{"time": label, "value": _num(value)} for label, value in points],
        "forecast": forecast,
    }


def start_session(
    *,
    space_id: str,
    kind: str,
    goal: str,
    audience: str,
    api_key: str,
    actor: str,
    tenant_id: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """One cheap call: intent, outline, and an estimate. The build waits."""
    _require_on()
    space = _space(space_id)
    if kind not in KINDS:
        raise CreateProblem("kind_required")
    goal_text = _short(goal, 400, "goal_required")
    audience_text = _short(audience, 200, "audience_required")
    session = {
        "session_id": "c" + uuid.uuid4().hex,
        "space_id": space,
        "kind": kind,
        "actor": actor,
        "tenant_id": (tenant_id or "").strip(),
        "goal": goal_text,
        "audience": audience_text,
        "step": "intent",
        "shown": False,
        "metered": False,
        "confirmed": False,
        "final": False,
        "files": [],
        "log": None,
        "run_seconds": 0.0,
        "usage_calls": [],
        "steps_used": 0,
        "retries": {},
        "budget": _budget((tenant_id or "").strip()),
        "estimate": {"tokens": None, "cost": None},
        "outline": [],
        "stopped": None,
        "message": None,
        "trace": [],
        "intent": None,
        "plan": None,
        "spec": None,
        "artifact": None,
        "issues": [],
        "changes": [],
        "abstain": None,
        "tables": [],
        "filters": {},
    }
    started = time.perf_counter()
    try:
        _cheap_plan(session, api_key, transport, answer=None, tweak=None)
    except CreateProblem as exc:
        if exc.code not in _STOP_CODES:
            raise
        _stop(session, exc.code)
    _span_add(session, started)
    if session.get("stopped") or session.get("step") == "abstain":
        _write_log(session, _call_list(session), session.get("run_seconds"))
    _trace(session, "intent", "Understanding your question")
    _save(session)
    return _view(session)


def confirm_intent(
    *,
    session_id: str,
    api_key: str,
    answer: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Confirm the card. A pending question is sent once, then dropped."""
    _require_on()
    session = _load(session_id)
    intent = session.get("intent") or {}
    started = time.perf_counter()
    if intent.get("clarify") and not (answer or "").strip():
        raise CreateProblem("answer_required")
    if intent.get("clarify"):
        try:
            _cheap_plan(
                session,
                api_key,
                transport,
                answer=(answer or "").strip(),
                tweak=None,
            )
        except CreateProblem as exc:
            if exc.code not in _STOP_CODES:
                raise
            _stop(session, exc.code)
            _span_add(session, started)
            _write_log(
                session, _call_list(session), session.get("run_seconds")
            )
            _save(session)
            return _view(session)
        intent = session.get("intent") or {}
        if isinstance(intent, dict):
            intent["clarify"] = None
            session["intent"] = intent
    if session.get("step") == "abstain" or session.get("stopped"):
        _span_add(session, started)
        _save(session)
        return _view(session)
    if not _intent_complete(session.get("intent") or {}):
        raise CreateProblem("intent_incomplete")
    session["confirmed"] = True
    session["step"] = "plan" if session.get("outline") else "intent_confirmed"
    _span_add(session, started)
    _trace(session, "intent", "confirmed")
    _save(session)
    return _view(session)


def draft_plan(
    *,
    session_id: str,
    api_key: str,
    tweak: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Publish the cheap plan. A tweak is one more plan call, not a build."""
    _require_on()
    session = _load(session_id)
    if not session.get("confirmed"):
        raise CreateProblem("intent_unconfirmed")
    if not (tweak or "").strip():
        if not session.get("plan"):
            raise CreateProblem("plan_required")
        session["step"] = "plan"
        _save(session)
        return _view(session)
    started = time.perf_counter()
    try:
        _cheap_plan(session, api_key, transport, answer=None, tweak=tweak)
    except CreateProblem as exc:
        if exc.code not in _STOP_CODES:
            raise
        _stop(session, exc.code)
    _span_add(session, started)
    if session.get("step") != "abstain":
        session["step"] = "plan"
    _save(session)
    return _view(session)


_DRAFT_LABEL = {
    KIND_APP: "Drafting the app",
    KIND_DOC: "Drafting the document",
    KIND_SLIDES: "Drafting slides",
}


def draft_events(
    *,
    session_id: str,
    api_key: str,
    transport: Transport | None = None,
) -> Iterator[dict[str, Any]]:
    """Build one outline piece at a time. A failed piece is retried once.

    The file is not written here. ``confirm_final`` does that after the user
    confirms the draft. A budget stop keeps the pieces already checked.
    """
    _require_on()
    session = _load(session_id)
    if session.get("step") not in {"plan", "check", "draft", "ready"}:
        raise CreateProblem("plan_required")
    if not session.get("confirmed"):
        raise CreateProblem("intent_unconfirmed")
    started = time.perf_counter()
    try:
        tables = [str(name) for name in (session.get("tables") or []) if isinstance(name, str)]
        label = "Fetching " + ", ".join(tables) if tables else "Fetching tables"
        yield _event(session, step="fetch", label=label, status="started")
        _catalog(session)
        yield _event(session, step="fetch", label=label, status="done")
        kept: list[str] = []
        for piece in list(session.get("outline") or []):
            block = _budget_block(session)
            if block:
                _stop(session, block)
                break
            yield _event(session, step="sql", label="Writing SQL", status="started", piece=piece)
            try:
                raw = _piece_call(session, piece, api_key, transport, kept, retry=False)
            except CreateProblem as exc:
                if exc.code in _STOP_CODES:
                    _stop(session, exc.code)
                    break
                raise
            yield _event(session, step="sql", label="Writing SQL", status="done", piece=piece)
            yield _event(session, step="check", label="Checking", status="started", piece=piece)
            kept_ok = _try_piece(session, piece, raw, api_key, transport, kept)
            if session.get("stopped") or session.get("step") == "abstain":
                yield _event(
                    session,
                    step="check",
                    label="Checking",
                    status="withheld",
                    piece=piece,
                )
                break
            if kept_ok:
                kept.append(str(piece.get("title") or ""))
            yield _event(
                session,
                step="check",
                label="Checking",
                status="pass" if kept_ok else "fail",
                piece=piece,
            )
        _close_draft(session)
        draft_label = _DRAFT_LABEL.get(str(session.get("kind")), "Drafting")
        yield _event(
            session,
            step="draft",
            label=draft_label,
            status="done" if session.get("shown") else "withheld",
        )
    except Exception:
        _span_add(session, started)
        _write_log(session, _call_list(session), session.get("run_seconds"))
        _save(session)
        raise
    _span_add(session, started)
    _write_log(session, _call_list(session), session.get("run_seconds"))
    _save(session)
    yield _event(
        session,
        step="confirm",
        label="Waiting for confirmation",
        status="waiting",
        session=_view(session),
    )


def generate_artifact(
    *,
    session_id: str,
    api_key: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Run the streamed draft and return the session once it is waiting."""
    view = None
    for event in draft_events(session_id=session_id, api_key=api_key, transport=transport):
        found = event.get("session")
        if isinstance(found, dict):
            view = found
    if view is None:
        raise CreateProblem("plan_required")
    return view


def improve_artifact(
    *,
    session_id: str,
    api_key: str,
    instruction: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Revise one piece. The kept pieces are not sent back and not rebuilt."""
    _require_on()
    session = _load(session_id)
    if not session.get("shown"):
        raise CreateProblem("nothing_to_improve")
    started = time.perf_counter()
    mark = len(session.get("usage_calls") or [])
    text = _short(instruction, 400, "instruction_required")
    piece = {"id": f"improve{int(session.get('steps_used') or 0)}", "title": text[:80]}
    try:
        block = _budget_block(session)
        if block:
            _stop(session, block)
        else:
            raw = _piece_call(
                session,
                piece,
                api_key,
                transport,
                [],
                retry=False,
                instruction=text,
            )
            _try_piece(session, piece, raw, api_key, transport, [], replace_last=True)
            _close_draft(session)
    except CreateProblem as exc:
        if exc.code not in _STOP_CODES:
            raise
        _stop(session, exc.code)
    finally:
        seconds = round(time.perf_counter() - started, 6)
        calls = _call_list(session)[mark:]
        _write_log(session, calls, seconds)
        _save(session)
    return _view(session)


def confirm_final(*, session_id: str, actor: str) -> dict[str, Any]:
    """Write the file after the user confirms a draft that passed review."""
    _require_on()
    session = _load(session_id)
    if session.get("step") != "draft" or not session.get("shown"):
        raise CreateProblem("nothing_to_confirm")
    _write_final(session)
    session["final"] = True
    session["step"] = "ready"
    _trace(session, "file", "done")
    _save(session)
    return accept_artifact(session_id=session_id, actor=actor)


def accept_artifact(*, session_id: str, actor: str) -> dict[str, Any]:
    """Write confirmed SQL as examples and one skill. Neither is an answer."""
    _require_on()
    session = _load(session_id)
    if not session.get("shown") or not session.get("artifact"):
        raise CreateProblem("nothing_to_accept")
    from dms_executor.verified_queries import save_create_example

    stored: list[dict[str, str]] = []
    warehouse = _warehouse()
    grantable = _grantable(session["space_id"], warehouse)
    for label, sql in _spec_sql(session["artifact"]):
        question = f"{session['goal']} :: {label}"
        try:
            row = save_create_example(
                question=question,
                sql=sql,
                space_id=session["space_id"],
                actor=actor,
                warehouse=warehouse,
                grantable=grantable,
                dialect=SERVING_DIALECT,
            )
        except Exception as exc:
            from dms_executor.lake_registry import SERVING_ERRORS

            if isinstance(exc, SERVING_ERRORS):
                raise
            row = None
        if row and row.get("asset_id"):
            stored.append({"asset_id": str(row["asset_id"]), "label": label})
    skill_id = _store_skill(session, [sql for _label, sql in _spec_sql(session["artifact"])])
    session["accepted"] = True
    _trace(session, "accept", "done")
    _save(session)
    view = _view(session)
    view["stored"] = stored
    view["skill_id"] = skill_id
    return view


def read_artifact(*, session_id: str) -> dict[str, Any]:
    """Return the artifact. A moved fingerprint is re-run and diffed."""
    _require_on()
    session = _load(session_id)
    if session.get("shown"):
        _refresh(session)
        _save(session)
    return _view(session)


def query_artifact(
    *,
    session_id: str,
    filters: Mapping[str, str],
) -> dict[str, Any]:
    """Re-run widgets whose SQL names a filter. Values must be options."""
    _require_on()
    session = _load(session_id)
    if not session.get("shown") or session.get("kind") != KIND_APP:
        raise CreateProblem("nothing_to_query")
    chosen = dict(session.get("filters") or {})
    options = _filter_options(session.get("artifact") or {})
    for key, value in filters.items():
        if key not in options or value not in options[key]:
            raise CreateProblem("filter_value")
        chosen[key] = value
    session["filters"] = chosen
    artifact = session.get("artifact") or {}
    for widget in artifact.get("widgets") or []:
        sql = str(widget.get("sql") or "")
        if not sql:
            continue
        bound = _bind_filters(sql, chosen)
        rows, issues, abstain = _execute(session, bound)
        if abstain is not None:
            session["abstain"] = abstain
            session["step"] = "abstain"
            session["shown"] = False
            _save(session)
            return _view(session)
        if issues:
            raise CreateProblem(issues[0])
        widget["rows"] = rows
    _save(session)
    return _view(session)


def export_bytes(*, session_id: str, kind: str) -> tuple[bytes, str]:
    """PPTX or PDF written by ``confirm_final``. A draft is not a file yet."""
    _require_on()
    session = _load(session_id)
    if not session.get("final") or session.get("kind") != KIND_SLIDES:
        raise CreateProblem("confirm_required")
    name = "deck.pptx" if kind == "pptx" else "deck.pdf" if kind == "pdf" else ""
    if not name:
        raise CreateProblem("export_kind")
    path = _store_dir() / session_id / name
    if not path.is_file():
        raise CreateProblem("nothing_to_export")
    media = "application/pdf"
    if kind == "pptx":
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return path.read_bytes(), media


def _require_on() -> None:
    if not create_enabled():
        raise CreateDisabled()


def _materialise(
    session: dict[str, Any],
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
    session["_last_raw"] = _public_spec(raw)
    kind = session["kind"]
    try:
        if kind == KIND_APP:
            return _materialise_app(session, raw)
        if kind == KIND_DOC:
            return _materialise_doc(session, raw)
        return _materialise_slides(session, raw)
    except CreateProblem as exc:
        if exc.code in {"ungranted", "destructive"}:
            raise
        return None, [exc.code], None


def _materialise_app(
    session: dict[str, Any],
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
    title = _title(raw.get("title"))
    issues: list[str] = []
    if title is None:
        issues.append("layout_overflow")
    filters_in = _as_list(raw.get("filters"))
    widgets_in = _as_list(raw.get("widgets"))
    if len(filters_in) > _LIMITS["filters"] or len(widgets_in) > _LIMITS["widgets"]:
        issues.append("layout_clutter")
    if not widgets_in:
        issues.append("layout_clutter")
    forecasts, forecast_issues, abstain = _forecasts(session, raw.get("forecasts"))
    issues.extend(forecast_issues)
    if abstain is not None:
        return None, issues, abstain
    filters: list[dict[str, Any]] = []
    options: dict[str, list[str]] = {}
    for item in filters_in:
        if not isinstance(item, Mapping):
            issues.append("layout_clutter")
            continue
        ident = item.get("id")
        sql = item.get("sql")
        if not isinstance(ident, str) or not _IDENT.fullmatch(ident) or not isinstance(sql, str):
            issues.append("layout_clutter")
            continue
        rows, exec_issues, exec_abstain = _execute(session, sql)
        if exec_abstain is not None:
            return None, issues, exec_abstain
        issues.extend(exec_issues)
        values = [
            str(row.get("value"))
            for row in rows
            if row.get("value") is not None
        ][:12]
        options[ident] = values
        filters.append({"id": ident, "options": values})
    session["filters"] = {key: values[0] for key, values in options.items() if values}
    widgets: list[dict[str, Any]] = []
    for item in widgets_in:
        if not isinstance(item, Mapping):
            issues.append("layout_clutter")
            continue
        built, widget_issues, widget_abstain = _widget(session, item, forecasts)
        issues.extend(widget_issues)
        if widget_abstain is not None:
            return None, issues, widget_abstain
        if built is None:
            continue
        widgets.append(built)
    if any(not _fits(widget["title"], _LIMITS["widget_title"]) for widget in widgets):
        issues.append("layout_overflow")
    issues = _unique(issues)
    if issues or title is None:
        return None, issues or ["layout_overflow"], None
    return (
        {
            "kind": KIND_APP,
            "title": title,
            "filters": filters,
            "widgets": widgets,
            "forecasts": list(forecasts.values()),
        },
        [],
        None,
    )


def _widget(
    session: dict[str, Any],
    item: Mapping[str, Any],
    forecasts: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
    ident = item.get("id")
    title = item.get("title")
    sql = item.get("sql")
    chart = item.get("chart")
    if not isinstance(ident, str) or not _IDENT.fullmatch(ident):
        return None, ["layout_clutter"], None
    if not isinstance(title, str) or not isinstance(sql, str) or not isinstance(chart, Mapping):
        return None, ["layout_clutter"], None
    bound = _bind_filters(sql, session.get("filters") or {})
    rows, issues, abstain = _execute(session, bound)
    if abstain is not None:
        return None, issues, abstain
    chart_issues = _chart_issues(chart, rows)
    sql_text = str(item.get("sql") or "")
    text, used, cites = _bind_text(title, rows, forecasts, sql_text)
    issues.extend(chart_issues)
    issues.extend(_unsupported(text, rows, forecasts, used))
    if issues:
        return None, issues, None
    y_names = chart.get("y") if isinstance(chart.get("y"), list) else []
    y_col = y_names[0] if y_names and isinstance(y_names[0], str) else ""
    return (
        {
            "id": ident,
            "title": text,
            "sql": sql_text,
            "chart": {
                "kind": chart.get("kind"),
                "x": chart.get("x"),
                "y": list(chart.get("y") or []),
            },
            "rows": rows,
            "citations": cites + _column_citations(rows, y_col, sql_text),
        },
        [],
        None,
    )


def _materialise_doc(
    session: dict[str, Any],
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
    title = _title(raw.get("title"))
    issues: list[str] = []
    if title is None:
        issues.append("layout_overflow")
    summary_sql = raw.get("summary_sql")
    summary = raw.get("exec_summary")
    findings_in = _as_list(raw.get("findings"))
    actions_in = _as_list(raw.get("actions"))
    if not isinstance(summary_sql, str) or not isinstance(summary, str):
        issues.append("layout_clutter")
    if not findings_in or len(findings_in) > _LIMITS["findings"]:
        issues.append("layout_clutter")
    if not actions_in or len(actions_in) > _LIMITS["actions"]:
        issues.append("layout_clutter")
    forecasts, forecast_issues, abstain = _forecasts(session, raw.get("forecasts"))
    issues.extend(forecast_issues)
    if abstain is not None:
        return None, issues, abstain
    summary_rows: list[dict[str, Any]] = []
    if isinstance(summary_sql, str):
        summary_rows, exec_issues, exec_abstain = _execute(session, summary_sql)
        issues.extend(exec_issues)
        if exec_abstain is not None:
            return None, issues, exec_abstain
    bound_summary = ""
    summary_cites: list[dict[str, Any]] = []
    summary_sql_text = summary_sql if isinstance(summary_sql, str) else ""
    if isinstance(summary, str):
        bound_summary, used, summary_cites = _bind_text(
            summary, summary_rows, forecasts, summary_sql_text
        )
        issues.extend(_unsupported(bound_summary, summary_rows, forecasts, used))
        if not _fits(bound_summary, _LIMITS["summary"]):
            issues.append("layout_overflow")
    findings: list[dict[str, Any]] = []
    for item in findings_in:
        if not isinstance(item, Mapping):
            issues.append("layout_clutter")
            continue
        text = item.get("text")
        sql = item.get("sql")
        if not isinstance(text, str) or not isinstance(sql, str):
            issues.append("layout_clutter")
            continue
        rows, exec_issues, exec_abstain = _execute(session, sql)
        issues.extend(exec_issues)
        if exec_abstain is not None:
            return None, issues, exec_abstain
        bound, used, cites = _bind_text(text, rows, forecasts, sql)
        issues.extend(_unsupported(bound, rows, forecasts, used))
        if not _fits(bound, _LIMITS["finding"]):
            issues.append("layout_overflow")
        findings.append(
            {"text": bound, "template": text, "sql": sql, "rows": rows, "citations": cites}
        )
    actions: list[str] = []
    action_cites: list[dict[str, Any]] = []
    evidence = summary_rows + [row for finding in findings for row in finding["rows"]]
    for item in actions_in:
        if not isinstance(item, str):
            issues.append("layout_clutter")
            continue
        bound, used, cites = _bind_text(item, evidence, forecasts, "")
        issues.extend(_unsupported(bound, evidence, forecasts, used))
        if not _fits(bound, _LIMITS["action"]):
            issues.append("layout_overflow")
        actions.append(bound)
        action_cites.extend(cites)
    issues = _unique(issues)
    if issues or title is None or not bound_summary or not findings or not actions:
        return None, issues or ["layout_clutter"], None
    return (
        {
            "kind": KIND_DOC,
            "title": title,
            "exec_summary": bound_summary,
            "summary_sql": summary_sql_text,
            "summary_citations": summary_cites,
            "findings": findings,
            "actions": actions,
            "action_citations": action_cites,
            "forecasts": list(forecasts.values()),
        },
        [],
        None,
    )


def _materialise_slides(
    session: dict[str, Any],
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
    title = _title(raw.get("title"))
    issues: list[str] = []
    if title is None:
        issues.append("layout_overflow")
    slides_in = _as_list(raw.get("slides"))
    if not slides_in or len(slides_in) > _LIMITS["slides"]:
        issues.append("layout_clutter")
    forecasts, forecast_issues, abstain = _forecasts(session, raw.get("forecasts"))
    issues.extend(forecast_issues)
    if abstain is not None:
        return None, issues, abstain
    slides: list[dict[str, Any]] = []
    for item in slides_in:
        if not isinstance(item, Mapping):
            issues.append("layout_clutter")
            continue
        built, slide_issues, slide_abstain = _slide(session, item, forecasts)
        issues.extend(slide_issues)
        if slide_abstain is not None:
            return None, issues, slide_abstain
        if built is not None:
            slides.append(built)
    issues = _unique(issues)
    if issues or title is None or not slides:
        return None, issues or ["layout_clutter"], None
    return (
        {
            "kind": KIND_SLIDES,
            "title": title,
            "template": "clean",
            "slides": slides,
            "forecasts": list(forecasts.values()),
        },
        [],
        None,
    )


def _slide(
    session: dict[str, Any],
    item: Mapping[str, Any],
    forecasts: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
    title = item.get("title")
    insight = item.get("insight")
    notes = item.get("notes")
    if not isinstance(title, str) or not isinstance(insight, str) or not isinstance(notes, str):
        return None, ["layout_clutter"], None
    has_chart = isinstance(item.get("sql"), str) and isinstance(item.get("chart"), Mapping)
    forecast_id = item.get("forecast")
    has_forecast = isinstance(forecast_id, str)
    if has_chart == has_forecast:
        return None, ["layout_clutter"], None
    rows: list[dict[str, Any]] = []
    chart = None
    issues: list[str] = []
    abstain = None
    if has_chart:
        rows, issues, abstain = _execute(session, str(item.get("sql")))
        if abstain is not None:
            return None, issues, abstain
        chart_raw = item.get("chart")
        if isinstance(chart_raw, Mapping):
            issues.extend(_chart_issues(chart_raw, rows))
            chart = {
                "kind": chart_raw.get("kind"),
                "x": chart_raw.get("x"),
                "y": list(chart_raw.get("y") or []),
            }
    forecast = forecasts.get(forecast_id) if has_forecast and isinstance(forecast_id, str) else None
    if has_forecast and forecast is None:
        issues.append("forecast_method")
    sql_text = str(item.get("sql") or "") if has_chart else ""
    if has_forecast and isinstance(forecast, Mapping):
        sql_text = str(forecast.get("sql") or sql_text)
    bound_title, used_title, cites_title = _bind_text(title, rows, forecasts, sql_text)
    bound_insight, used_insight, cites_insight = _bind_text(insight, rows, forecasts, sql_text)
    bound_notes, used_notes, cites_notes = _bind_text(notes, rows, forecasts, sql_text)
    for text, used in (
        (bound_title, used_title),
        (bound_insight, used_insight),
        (bound_notes, used_notes),
    ):
        issues.extend(_unsupported(text, rows, forecasts, used))
    if not _fits(bound_title, _LIMITS["title"]) or not _fits(bound_insight, _LIMITS["insight"]):
        issues.append("layout_overflow")
    if not _fits(bound_notes, _LIMITS["notes"]):
        issues.append("layout_overflow")
    if issues:
        return None, _unique(issues), None
    return (
        {
            "title": bound_title,
            "insight": bound_insight,
            "notes": bound_notes,
            "title_template": title,
            "insight_template": insight,
            "notes_template": notes,
            "sql": sql_text,
            "chart": chart,
            "rows": rows,
            "forecast": forecast,
            "citations": _slide_citations(
                cites_title + cites_insight + cites_notes, chart, rows, sql_text, forecast
            ),
        },
        [],
        None,
    )


def _forecasts(
    session: dict[str, Any],
    raw: Any,
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, Any] | None]:
    found: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    if raw is None:
        return found, issues, None
    if not isinstance(raw, list):
        return found, ["layout_clutter"], None
    for item in raw:
        if not isinstance(item, Mapping):
            issues.append("layout_clutter")
            continue
        ident = item.get("id")
        sql = item.get("sql")
        method = item.get("method")
        time_col = item.get("time")
        value_col = item.get("value")
        horizon = item.get("horizon", 3)
        period = item.get("period")
        title = _as_str(item.get("title"), "Forecast")
        if not isinstance(ident, str) or not _IDENT.fullmatch(ident) or not isinstance(sql, str):
            issues.append("forecast_method")
            continue
        if (
            not isinstance(method, str)
            or not isinstance(time_col, str)
            or not isinstance(value_col, str)
        ):
            issues.append("forecast_method")
            continue
        if not isinstance(horizon, int) or isinstance(horizon, bool):
            issues.append("forecast_horizon")
            continue
        rows, exec_issues, abstain = _execute(session, sql)
        issues.extend(exec_issues)
        if abstain is not None:
            return found, issues, abstain
        points: list[tuple[str, float]] = []
        for row in rows:
            number = _as_float(row.get(value_col))
            label = row.get(time_col)
            if number is None or not isinstance(label, str):
                continue
            points.append((label, number))
        period_int = period if isinstance(period, int) and not isinstance(period, bool) else None
        built = forecast_series(points, method, horizon=horizon, period=period_int)
        if isinstance(built, str):
            issues.append(built)
            continue
        built["id"] = ident
        built["title"] = title[: _LIMITS["title"]]
        built["sql"] = sql
        built["time"] = time_col
        built["value_column"] = value_col
        built["horizon"] = horizon
        built["period"] = period_int
        built["citations"] = _forecast_citations(built)
        found[ident] = built
    return found, issues, None


def _execute(
    session: dict[str, Any],
    sql: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
    from dms_executor.grant_struct import serve_gap
    from dms_executor.sql_loop import run_readonly

    text = (sql or "").strip()
    warehouse = _warehouse()
    grantable = _grantable(session["space_id"], warehouse)
    gap = serve_gap(text, grantable=grantable, dialect=SERVING_DIALECT)
    if gap:
        kind = _abstain_kind(gap)
        if kind:
            return [], [], _abstain(session, kind, text)
        return [], [f"sql_refused:{gap}"], None
    first, err = run_readonly(text, warehouse)
    if err or first is None:
        return [], [err or "sql_failed"], None
    second, err2 = run_readonly(text, warehouse)
    if err2 or second is None or _canon_rows(first) != _canon_rows(second):
        return [], ["rerun_mismatch"], None
    return [_json_row(row) for row in first[:_ROW_CAP]], [], None


def _abstain_kind(gap: str) -> str | None:
    if gap in {"statement_not_allowed", "multi_statement"} or gap.startswith("hostile_sql"):
        return "destructive"
    if gap == "ungranted:unparsed":
        return None
    if (
        gap == "ungranted"
        or gap.startswith("ungranted:")
        or gap.startswith("sql_relation_not_granted")
    ):
        return "ungranted"
    if gap.startswith("sql_relation_unresolved"):
        return "ungranted"
    return None


def _abstain(session: dict[str, Any], kind: str, sql: str) -> dict[str, Any]:
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    if kind == "destructive":
        text = "This statement is not a read. It was not run."
        reason = "destructive"
    else:
        text = "This read uses a table the Space does not grant. No figure was returned."
        reason = "ungranted"
    env = build_abstain(
        reason=reason,
        question=str(session.get("goal") or ""),
        sql=sql,
        stage="create",
        answer_id=f"ans_create_{session['session_id'][:8]}",
        text=text,
        abstain_reason=reason,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[reason],
        space_id=session.get("space_id"),
        session_id=session.get("session_id"),
        ask_mode="live",
        route="abstain",
        log_ticket=False,
    )
    assert_envelope_valid(env)
    return env


def _bind_plan(
    session: dict[str, Any],
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    grantable = _grantable(session["space_id"], _warehouse())
    tables_in = _as_list(raw.get("tables"))
    tables: list[dict[str, Any]] = []
    catalog = {row["name"]: row for row in _catalog(session)}
    for name in tables_in:
        if not isinstance(name, str) or name not in grantable:
            if isinstance(name, str) and name not in grantable:
                return {}, _abstain(session, "ungranted", f"SELECT * FROM {name}")
            continue
        tables.append(catalog.get(name) or {"name": name, "columns": [], "row_count": None})
    joins_in = _as_list(raw.get("joins"))
    joins: list[dict[str, Any]] = []
    for item in joins_in:
        if not isinstance(item, Mapping):
            continue
        left = item.get("left")
        right = item.get("right")
        on = item.get("on")
        if not all(isinstance(part, str) for part in (left, right, on)):
            continue
        joins.append({"left": left, "right": right, "on": on, "verified": False})
    metrics_in = _as_list(raw.get("metrics"))
    metrics: list[dict[str, Any]] = []
    for item in metrics_in:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        sql = item.get("sql")
        if not isinstance(name, str) or not isinstance(sql, str):
            continue
        rows, issues, abstain = _execute(session, sql)
        if abstain is not None:
            return {}, abstain
        metrics.append(
            {
                "name": name[:80],
                "sql": sql,
                "rows": rows[:5],
                "issues": issues,
            }
        )
    return {"tables": tables, "joins": joins, "metrics": metrics}, None


def _bind_text(
    text: str,
    rows: list[dict[str, Any]],
    forecasts: Mapping[str, dict[str, Any]],
    sql: str = "",
) -> tuple[str, set[str], list[dict[str, Any]]]:
    used: set[str] = set()
    citations: list[dict[str, Any]] = []

    def cite(match: re.Match[str]) -> str:
        column, index_text = match.group(1), match.group(2)
        index = int(index_text)
        if index >= len(rows) or column not in rows[index]:
            return match.group(0)
        rendered = _render(rows[index][column])
        if rendered is None:
            return match.group(0)
        used.add(rendered)
        citations.append(
            {
                "value": rendered,
                "sql": sql,
                "column": column,
                "row_index": index,
                "source_row": dict(rows[index]),
            }
        )
        return rendered

    def forecast_cite(match: re.Match[str]) -> str:
        block = forecasts.get(match.group(1))
        if block is None:
            return match.group(0)
        field = match.group(2)
        rendered = _forecast_field(block, field)
        if rendered is None:
            return match.group(0)
        used.add(rendered)
        citations.append(
            {
                "value": rendered,
                "sql": str(block.get("sql") or sql),
                "field": field,
                "source_row": _forecast_source(block, field),
            }
        )
        return rendered

    out = _CITE.sub(cite, text)
    out = _FCITE.sub(forecast_cite, out)
    return out, used, citations


def _unsupported(
    text: str,
    rows: list[dict[str, Any]],
    forecasts: Mapping[str, dict[str, Any]],
    used: set[str],
) -> list[str]:
    del rows, forecasts
    if "{{" in text:
        return ["unsupported_claim"]
    for match in _NUM.finditer(text):
        token = match.group(1)
        if token not in used and not _same_number(token, used):
            return ["unsupported_claim"]
    return []


def _column_citations(
    rows: list[dict[str, Any]],
    column: str,
    sql: str,
) -> list[dict[str, Any]]:
    if not column:
        return []
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        rendered = _render(row.get(column))
        if not rendered:
            continue
        out.append(
            {
                "value": rendered,
                "sql": sql,
                "column": column,
                "row_index": index,
                "source_row": dict(row),
            }
        )
    return out


def _slide_citations(
    cites: list[dict[str, Any]],
    chart: Mapping[str, Any] | None,
    rows: list[dict[str, Any]],
    sql: str,
    forecast: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    out = list(cites)
    if isinstance(chart, Mapping) and rows:
        y_names = chart.get("y") if isinstance(chart.get("y"), list) else []
        y_col = y_names[0] if y_names and isinstance(y_names[0], str) else ""
        out.extend(_column_citations(rows, y_col, sql))
    if isinstance(forecast, Mapping):
        out.extend(list(forecast.get("citations") or []))
    return out


def _forecast_citations(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    sql = str(block.get("sql") or "")
    out: list[dict[str, Any]] = []
    for index, point in enumerate(block.get("history") or []):
        if not isinstance(point, Mapping):
            continue
        rendered = _render(point.get("value"))
        if rendered:
            out.append(
                {
                    "value": rendered,
                    "sql": sql,
                    "field": "history",
                    "row_index": index,
                    "source_row": dict(point),
                }
            )
    for index, point in enumerate(block.get("forecast") or []):
        if not isinstance(point, Mapping):
            continue
        for key in ("value", "low", "high"):
            rendered = _render(point.get(key))
            if rendered:
                out.append(
                    {
                        "value": rendered,
                        "sql": sql,
                        "field": key,
                        "row_index": index,
                        "source_row": dict(point),
                    }
                )
    for field in ("backtest_error", "holdout_actual", "holdout_predicted"):
        rendered = _render(block.get(field))
        if rendered:
            out.append(
                {
                    "value": rendered,
                    "sql": sql,
                    "field": field,
                    "source_row": {field: block.get(field)},
                }
            )
    return out


def _forecast_source(block: Mapping[str, Any], field: str) -> dict[str, Any]:
    point = _FORECAST_POINT.fullmatch(field)
    if point:
        index = int(point.group(2))
        rows = block.get("forecast") or []
        if index < len(rows) and isinstance(rows[index], Mapping):
            return dict(rows[index])
    return {field: block.get(field)}


def _chart_issues(chart: Mapping[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    kind = chart.get("kind")
    x = chart.get("x")
    y = chart.get("y")
    if kind not in CHART_KINDS or not isinstance(x, str) or not isinstance(y, list) or len(y) != 1:
        return ["layout_clutter"]
    y_name = y[0]
    if not isinstance(y_name, str):
        return ["layout_clutter"]
    if rows and (x not in rows[0] or y_name not in rows[0]):
        return ["layout_clutter"]
    return []


def _context(session: dict[str, Any]) -> dict[str, Any]:
    catalog = _catalog(session)
    examples = _examples(session)
    skills = _skills(session)
    ontology = _ontology()
    return {
        "kind": session["kind"],
        "goal": session.get("goal"),
        "audience": session.get("audience"),
        "decision": (session.get("intent") or {}).get("decision"),
        "tables": [
            {
                "name": row["name"],
                "columns": row["columns"],
                "row_count": row["row_count"],
            }
            for row in catalog
        ],
        "ontology": ontology,
        "examples": examples,
        "skills": skills,
    }


def _catalog(session: dict[str, Any]) -> list[dict[str, Any]]:
    from dms_executor.sql_loop import run_readonly

    warehouse = _warehouse()
    grantable = _grantable(session["space_id"], warehouse)
    out: list[dict[str, Any]] = []
    for name in sorted(grantable):
        if not _IDENT.fullmatch(name):
            continue
        columns, _err = run_readonly(
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = 'main' AND table_name = '{name}' ORDER BY column_name",
            warehouse,
        )
        counted, _err2 = run_readonly(f"SELECT count(*) AS n FROM {_q(name)}", warehouse)
        cols = []
        for row in columns or []:
            col = row.get("column_name")
            typ = row.get("data_type")
            if isinstance(col, str) and isinstance(typ, str):
                cols.append({"name": col, "type": typ})
        count = None
        if counted:
            count = _as_int(counted[0].get("n"))
        out.append({"name": name, "columns": cols, "row_count": count})
    return out


def _examples(session: dict[str, Any]) -> list[dict[str, str]]:
    try:
        from dms_executor.verified_queries import retrieve_verified_examples

        rows = retrieve_verified_examples(
            str(session.get("goal") or ""),
            space_id=session.get("space_id"),
            warehouse=_warehouse(),
            grantable=_grantable(session["space_id"], _warehouse()),
            dialect=SERVING_DIALECT,
            force=True,
        )
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for row in rows[:3]:
        question = str(row.get("question") or "")
        sql = str(row.get("sql") or "")
        if question and sql:
            out.append({"question": question, "sql": sql})
    return out


def _skills(session: dict[str, Any]) -> list[dict[str, Any]]:
    from dms_executor.verified_queries import rank_texts

    rows = _skill_rows(session["space_id"])
    if not rows:
        return []
    docs = [
        f"{row.get('name', '')} {row.get('goal', '')} {' '.join(row.get('sqls') or [])}"
        for row in rows
    ]
    ranked = rank_texts(str(session.get("goal") or ""), docs, None, limit=3, floor=0.0)
    out = []
    for _score, index in ranked:
        row = rows[index]
        out.append(
            {
                "name": row.get("name"),
                "kind": row.get("kind"),
                "sqls": list(row.get("sqls") or [])[:4],
            }
        )
    return out


def _cheap_plan(
    session: dict[str, Any],
    api_key: str,
    transport: Transport | None,
    *,
    answer: str | None,
    tweak: str | None,
) -> None:
    payload = _context(session)
    payload["task"] = "plan"
    if answer:
        payload["answer"] = answer
    if tweak and tweak.strip():
        payload["tweak"] = tweak.strip()[:400]
    raw = _call_model(ROLE_PLAN, payload, api_key, transport, session)
    intent = _parse_intent(raw)
    session["intent"] = intent
    if intent.get("goal"):
        session["goal"] = intent["goal"]
    if intent.get("audience"):
        session["audience"] = intent["audience"]
    session["estimate"] = {
        "tokens": _optional_num(raw.get("estimated_tokens")),
        "cost": _optional_num(raw.get("estimated_cost")),
    }
    if intent.get("clarify") and not answer:
        session["step"] = "intent"
        session["outline"] = []
        session["plan"] = None
        _trace(session, "intent", "question")
        return
    plan, abstain = _bind_plan(session, raw)
    if abstain is not None:
        session["abstain"] = abstain
        session["step"] = "abstain"
        session["shown"] = False
        session["plan"] = None
        session["outline"] = []
        _trace(session, "plan", "abstain")
        return
    outline = _fit_outline(session, _outline_from(raw))
    plan["outline"] = outline
    session["plan"] = plan
    session["outline"] = outline
    session["tables"] = [str(row["name"]) for row in plan.get("tables") or []]
    session["abstain"] = None
    session["step"] = "intent"
    _trace(session, "plan", "estimate")


def _outline_from(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for item in _as_list(raw.get("outline")):
        title = ""
        ident = ""
        if isinstance(item, str):
            title = item
        elif isinstance(item, Mapping):
            got_title = item.get("title")
            got_id = item.get("id")
            if isinstance(got_title, str):
                title = got_title
            if isinstance(got_id, str):
                ident = got_id
        else:
            continue
        if not title.strip():
            continue
        if not _IDENT.fullmatch(ident):
            ident = f"p{len(found) + 1}"
        found.append({"id": ident, "title": title.strip()[:80]})
    if not found:
        found.append({"id": "main", "title": "Main"})
    return found


def _fit_outline(session: dict[str, Any], outline: list[dict[str, str]]) -> list[dict[str, str]]:
    del session
    return outline[: int(_LIMITS["slides"])]


def _optional_num(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value


def _budget(tenant_id: str) -> dict[str, int | float]:
    budget: dict[str, int | float] = {
        "max_tokens": int(_env_num("DMS_CREATE_MAX_TOKENS", 8000, True)),
        "max_usd": float(_env_num("DMS_CREATE_MAX_USD", 1.0, False)),
        "max_steps": int(_env_num("DMS_CREATE_MAX_STEPS", 8, True)),
    }
    raw = os.environ.get("DMS_CREATE_BUDGETS", "").strip()
    if not raw or not tenant_id:
        return budget
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return budget
    if not isinstance(parsed, dict):
        return budget
    row = parsed.get(tenant_id)
    if not isinstance(row, dict):
        return budget
    for key, whole in (("max_tokens", True), ("max_usd", False), ("max_steps", True)):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            continue
        budget[key] = int(value) if whole else float(value)
    return budget


def _env_num(name: str, default: int | float, whole: bool) -> int | float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value: int | float = int(raw) if whole else float(raw)
    except ValueError:
        return default
    if value < 0:
        return default
    return value


def _budget_block(session: dict[str, Any]) -> str | None:
    budget = session.get("budget") or _budget(str(session.get("tenant_id") or ""))
    if int(session.get("steps_used") or 0) >= int(budget["max_steps"]):
        return "budget_steps"
    calls = [row for row in (session.get("usage_calls") or []) if isinstance(row, dict)]
    tokens = _total_usage(calls, "total_tokens")
    if isinstance(tokens, (int, float)) and not isinstance(tokens, bool):
        if tokens >= float(budget["max_tokens"]):
            return "budget_tokens"
    cost = _total_usage(calls, "cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        if cost >= float(budget["max_usd"]):
            return "budget_usd"
    return None


def _count_step(session: dict[str, Any]) -> None:
    session["steps_used"] = int(session.get("steps_used") or 0) + 1


def _spend(session: dict[str, Any]) -> dict[str, Any]:
    calls = [row for row in (session.get("usage_calls") or []) if isinstance(row, dict)]
    return {
        "tokens": _total_usage(calls, "total_tokens"),
        "usd": _total_usage(calls, "cost"),
        "steps": int(session.get("steps_used") or 0),
    }


def _stop(session: dict[str, Any], code: str) -> None:
    session["stopped"] = code
    session["message"] = _BUDGET_TEXT.get(code, code)
    if session.get("artifact"):
        session["shown"] = True
        session["final"] = False
        session["step"] = "draft"
        return
    if session.get("step") != "abstain":
        session["shown"] = False
        session["step"] = "check"


def _event(owner: dict[str, Any], **fields: Any) -> dict[str, Any]:
    fields["spend"] = _spend(owner)
    return fields


def _piece_call(
    session: dict[str, Any],
    piece: Mapping[str, Any],
    api_key: str,
    transport: Transport | None,
    kept: list[str],
    *,
    retry: bool,
    instruction: str | None = None,
) -> dict[str, Any]:
    payload = _context(session)
    payload["task"] = "piece_retry" if retry else "piece"
    payload["piece"] = {"id": piece.get("id"), "title": piece.get("title")}
    payload["kept_titles"] = list(kept)
    payload["plan"] = _plan_brief(session.get("plan") or {})
    role = ROLE_GENERATE
    if instruction:
        payload["instruction"] = instruction
        role = ROLE_IMPROVE
    return _call_model(role, payload, api_key, transport, session)


def _try_piece(
    session: dict[str, Any],
    piece: Mapping[str, Any],
    raw: Mapping[str, Any],
    api_key: str,
    transport: Transport | None,
    kept: list[str],
    *,
    replace_last: bool = False,
) -> bool:
    artifact, issues, abstain = _materialise(session, raw)
    if abstain is not None:
        session["abstain"] = abstain
        session["step"] = "abstain"
        session["shown"] = bool(session.get("artifact"))
        session["issues"] = []
        return False
    if issues or artifact is None:
        piece_id = str(piece.get("id") or "piece")
        retries = session.setdefault("retries", {})
        if int(retries.get(piece_id) or 0) >= 1:
            session["issues"] = issues or ["piece_failed"]
            return False
        if _budget_block(session):
            _stop(session, _budget_block(session) or "budget_steps")
            session["issues"] = issues or ["piece_failed"]
            return False
        retries[piece_id] = 1
        try:
            repaired = _piece_call(session, piece, api_key, transport, kept, retry=True)
        except CreateProblem as exc:
            if exc.code in _STOP_CODES:
                _stop(session, exc.code)
            session["issues"] = issues or ["piece_failed"]
            return False
        artifact, issues, abstain = _materialise(session, repaired)
        if abstain is not None:
            session["abstain"] = abstain
            session["step"] = "abstain"
            session["shown"] = bool(session.get("artifact"))
            return False
        if issues or artifact is None:
            session["issues"] = issues or ["piece_failed"]
            return False
    if artifact is None:
        return False
    _keep_piece(session, artifact, replace_last=replace_last)
    session["issues"] = []
    return True


def _keep_piece(session: dict[str, Any], artifact: dict[str, Any], *, replace_last: bool) -> None:
    current = session.get("artifact")
    if not isinstance(current, dict) or current.get("kind") != artifact.get("kind"):
        session["artifact"] = artifact
        session["shown"] = True
        return
    kind = current.get("kind")
    if kind == KIND_SLIDES:
        _extend_list(current, "slides", artifact.get("slides") or [], replace_last)
    elif kind == KIND_DOC:
        if artifact.get("exec_summary") and (replace_last or not current.get("exec_summary")):
            current["exec_summary"] = artifact.get("exec_summary")
            current["summary_sql"] = artifact.get("summary_sql")
            current["summary_citations"] = list(artifact.get("summary_citations") or [])
        _extend_list(current, "findings", artifact.get("findings") or [], replace_last)
        _extend_list(current, "actions", artifact.get("actions") or [], replace_last)
    else:
        _extend_list(current, "widgets", artifact.get("widgets") or [], replace_last)
        _extend_list(current, "filters", artifact.get("filters") or [], replace_last)
    _merge_forecasts(current, artifact)
    session["artifact"] = current
    session["shown"] = True


def _extend_list(current: dict[str, Any], key: str, extra: list[Any], replace_last: bool) -> None:
    base = list(current.get(key) or [])
    added = list(extra)
    if replace_last and base:
        current[key] = base[:-1] + added[:1]
        return
    current[key] = base + added


def _merge_forecasts(current: dict[str, Any], artifact: Mapping[str, Any]) -> None:
    found: dict[str, dict[str, Any]] = {}
    for row in current.get("forecasts") or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            found[row["id"]] = row
    for row in artifact.get("forecasts") or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            found[row["id"]] = row
    current["forecasts"] = list(found.values())


def _close_draft(session: dict[str, Any]) -> None:
    if session.get("step") == "abstain":
        session["shown"] = bool(session.get("artifact"))
        return
    if session.get("artifact"):
        session["shown"] = True
        session["final"] = False
        session["step"] = "draft"
        session["fingerprint"] = _fingerprint(session)
        _trace(session, "generate", "shown")
        return
    session["shown"] = False
    if not session.get("stopped"):
        session["step"] = "check"
    _trace(session, "generate", "withheld")


def _call_model(
    role: str,
    payload: dict[str, Any],
    api_key: str,
    transport: Transport | None,
    session: dict[str, Any],
) -> dict[str, Any]:
    if (
        not api_key.startswith(API_KEY_PREFIX)
        or " " in api_key
        or len(api_key) <= len(API_KEY_PREFIX)
    ):
        raise CreateProblem("ov_api_key_missing")
    fn = transport or default_transport
    plan = [role, role, ROLE_ESCALATE]
    saw_429 = False
    step = 0
    last = "ov_unreachable"
    while step < len(plan):
        role_try = plan[step]
        step += 1
        if role_try == ROLE_ESCALATE and not saw_429:
            break
        block = _budget_block(session)
        if block:
            raise CreateProblem(block)
        try:
            raw = fn(_request_body(role_try, payload), api_key)
        except CreateCallError as exc:
            _count_step(session)
            last = exc.code
            if exc.code == "budget_exceeded":
                raise CreateProblem("budget_exceeded") from None
            if exc.code == "ov_http_429":
                saw_429 = True
                continue
            raise CreateProblem(exc.code) from None
        content, usage = _split_call(raw)
        parsed = _parse_model(content)
        _count_step(session)
        if parsed is None:
            raise CreateProblem("model_unreadable")
        session.setdefault("usage_calls", []).append(usage)
        session["metered"] = True
        return parsed
    raise CreateProblem(last)


def _request_body(role: str, payload: dict[str, Any]) -> dict[str, Any]:
    system = _INTENT_SYSTEM
    if role == ROLE_PLAN:
        system = _PLAN_SYSTEM
    elif role in {ROLE_GENERATE, ROLE_IMPROVE, ROLE_ESCALATE}:
        system = _GENERATE_SYSTEM
    body = {
        "role": role,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(payload, sort_keys=True, default=str),
            },
        ],
        "schema": "CreateSpec",
    }
    _reject_model_keys(body)
    return body


def default_transport(body: dict[str, Any], api_key: str) -> Any:
    """POST the body with the caller key. The error code never carries either."""
    import httpx

    base = os.environ.get("OPENVAULT_URL", "").strip().rstrip("/")
    if not base:
        raise CreateCallError("openvault_url_missing")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = httpx.post(
            base + "/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise CreateCallError("ov_unauthorized") from None
        if status == 402:
            raise CreateCallError("budget_exceeded") from None
        raise CreateCallError(f"ov_http_{status}") from None
    except httpx.HTTPError:
        raise CreateCallError("ov_unreachable") from None
    try:
        data = response.json()
    except ValueError:
        raise CreateCallError("model_unreadable") from None
    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
        message = data["choices"][0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            try:
                parsed = json.loads(_strip_fence(content))
            except json.JSONDecodeError:
                raise CreateCallError("model_unreadable") from None
            return {"content": parsed, "usage": _usage_from(_usage_payload(data))}
        if isinstance(content, dict):
            return {"content": content, "usage": _usage_from(_usage_payload(data))}
    payload = data if isinstance(data, dict) else {}
    return {"content": data, "usage": _usage_from(_usage_payload(payload))}


def _parse_model(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(_strip_fence(raw))
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    return {key: value for key, value in raw.items() if key not in _MODEL_KEYS}


def _parse_intent(raw: Mapping[str, Any]) -> dict[str, Any]:
    goal = _as_str(raw.get("goal"))
    audience = _as_str(raw.get("audience"))
    decision = _as_str(raw.get("decision"))
    clarify_raw = raw.get("clarify")
    clarify = clarify_raw if isinstance(clarify_raw, str) else None
    if clarify is not None:
        clarify = clarify.strip()[:200] or None
    return {
        "goal": goal.strip()[:400],
        "audience": audience.strip()[:200],
        "decision": decision.strip()[:240],
        "clarify": clarify,
    }


def _intent_complete(intent: Mapping[str, Any]) -> bool:
    return bool(intent.get("goal") and intent.get("audience") and intent.get("decision"))


def _refresh(session: dict[str, Any]) -> None:
    current = _fingerprint(session)
    previous = session.get("fingerprint")
    if current == previous:
        session["changes"] = []
        return
    artifact = session.get("artifact") or {}
    changes: list[dict[str, str]] = []
    if artifact.get("kind") == KIND_APP:
        for index, widget in enumerate(artifact.get("widgets") or []):
            sql = str(widget.get("sql") or "")
            if not sql:
                continue
            bound = _bind_filters(sql, session.get("filters") or {})
            rows, issues, abstain = _execute(session, bound)
            if abstain is not None or issues:
                continue
            changes.extend(_diff_rows(f"widgets.{index}", widget.get("rows") or [], rows))
            widget["rows"] = rows
    elif artifact.get("kind") == KIND_DOC:
        forecasts = {row["id"]: row for row in artifact.get("forecasts") or [] if row.get("id")}
        for index, finding in enumerate(artifact.get("findings") or []):
            rows, issues, abstain = _execute(session, str(finding.get("sql") or ""))
            if abstain is not None or issues:
                continue
            changes.extend(_diff_rows(f"findings.{index}", finding.get("rows") or [], rows))
            finding["rows"] = rows
            template = finding.get("template")
            if isinstance(template, str):
                bound, _used, cites = _bind_text(
                    template, rows, forecasts, str(finding.get("sql") or "")
                )
                finding["text"] = bound
                finding["citations"] = cites
    elif artifact.get("kind") == KIND_SLIDES:
        for index, slide in enumerate(artifact.get("slides") or []):
            sql = str(slide.get("sql") or "")
            if not sql:
                continue
            rows, issues, abstain = _execute(session, sql)
            if abstain is not None or issues:
                continue
            changes.extend(_diff_rows(f"slides.{index}", slide.get("rows") or [], rows))
            slide["rows"] = rows
    session["changes"] = changes
    session["fingerprint"] = current
    session["artifact"] = artifact


def _diff_rows(prefix: str, before: list[Any], after: list[Any]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for index, (left, right) in enumerate(zip(before, after, strict=False)):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            continue
        for key in left:
            old = _render(left.get(key))
            new = _render(right.get(key))
            if old is None or new is None or old == new:
                continue
            changes.append({"path": f"{prefix}.{index}.{key}", "before": old, "after": new})
    for index, right in enumerate(after[len(before) :], start=len(before)):
        if not isinstance(right, Mapping):
            continue
        for key, value in right.items():
            rendered = _render(value)
            if rendered is None:
                continue
            changes.append({"path": f"{prefix}.{index}.{key}", "before": "", "after": rendered})
    return changes


def _fingerprint(session: dict[str, Any]) -> str:
    from dms_executor.sql_loop import run_readonly

    warehouse = _warehouse()
    parts: list[str] = []
    for name in sorted(session.get("tables") or []):
        if not _IDENT.fullmatch(str(name)):
            continue
        rows, _err = run_readonly(f"SELECT count(*) AS n FROM {_q(str(name))}", warehouse)
        count = rows[0]["n"] if rows else "err"
        parts.append(f"{name}:{count}")
    parts.append(json.dumps(_ontology(), sort_keys=True, default=str))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _store_skill(session: dict[str, Any], sqls: list[str]) -> str:
    skill_id = uuid.uuid4().hex[:16]
    row = {
        "skill_id": skill_id,
        "space_id": session["space_id"],
        "kind": session["kind"],
        "name": (session.get("artifact") or {}).get("title") or session["kind"],
        "goal": session.get("goal"),
        "audience": session.get("audience"),
        "decision": (session.get("intent") or {}).get("decision"),
        "sqls": sqls,
    }
    path = _store_dir() / "skills.json"
    with _LOCK:
        current = _read_json(path)
        rows = current if isinstance(current, list) else []
        rows.append(row)
        _write_json(path, rows)
    return skill_id


def _skill_rows(space_id: str) -> list[dict[str, Any]]:
    current = _read_json(_store_dir() / "skills.json")
    if not isinstance(current, list):
        return []
    return [row for row in current if isinstance(row, dict) and row.get("space_id") == space_id]


def _spec_sql(artifact: Mapping[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for index, finding in enumerate(artifact.get("findings") or []):
        sql = finding.get("sql")
        if isinstance(sql, str) and sql.strip():
            found.append((f"finding-{index}", sql))
    for widget in artifact.get("widgets") or []:
        sql = widget.get("sql")
        if isinstance(sql, str) and sql.strip():
            found.append((str(widget.get("id") or "widget"), sql))
    for index, slide in enumerate(artifact.get("slides") or []):
        sql = slide.get("sql")
        if isinstance(sql, str) and sql.strip():
            found.append((f"slide-{index}", sql))
    for block in artifact.get("forecasts") or []:
        sql = block.get("sql")
        if isinstance(sql, str) and sql.strip():
            found.append((f"forecast-{block.get('id')}", sql))
    return found


def _view(session: dict[str, Any]) -> dict[str, Any]:
    artifact = session.get("artifact") if session.get("shown") else None
    return {
        "session_id": session["session_id"],
        "space_id": session["space_id"],
        "kind": session["kind"],
        "step": session["step"],
        "shown": bool(session.get("shown")),
        "final": bool(session.get("final")),
        "files": list(session.get("files") or []),
        "metered": bool(session.get("metered")),
        "intent": session.get("intent"),
        "plan": session.get("plan"),
        "artifact": artifact,
        "issues": list(session.get("issues") or []),
        "changes": list(session.get("changes") or []),
        "abstain": session.get("abstain"),
        "trace": list(session.get("trace") or []),
        "filters": dict(session.get("filters") or {}),
        "log": session.get("log"),
        "estimate": session.get("estimate") or {"tokens": None, "cost": None},
        "spend": _spend(session),
        "stopped": session.get("stopped"),
        "message": session.get("message"),
        "outline": list(session.get("outline") or []),
    }


def _span_add(session: dict[str, Any], started: float) -> None:
    elapsed = time.perf_counter() - started
    prior = session.get("run_seconds")
    base = prior if isinstance(prior, (int, float)) and not isinstance(prior, bool) else 0.0
    session["run_seconds"] = round(float(base) + elapsed, 6)
    session["seconds_measured"] = True


def _split_call(raw: Any) -> tuple[Any, dict[str, Any]]:
    if isinstance(raw, dict) and "content" in raw and isinstance(raw.get("usage"), dict):
        return raw.get("content"), _usage_from(raw["usage"])
    return raw, _usage_from({})


def _usage_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    usage = data.get("usage")
    merged: dict[str, Any] = dict(usage) if isinstance(usage, dict) else {}
    if "model" not in merged and isinstance(data.get("model"), str):
        merged["model"] = data["model"]
    if "cost" not in merged and "cost" in data:
        merged["cost"] = data.get("cost")
    return merged


def _usage_from(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Copy model, tokens, and cost only when the response included them."""
    out: dict[str, Any] = {
        "model": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cost": None,
    }
    model = raw.get("model")
    if isinstance(model, str) and model.strip():
        out["model"] = model.strip()[:120]
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    cost = raw.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
        out["cost"] = cost
    return out


def _total_usage(calls: list[dict[str, Any]], key: str) -> int | float | None:
    if not calls:
        return None
    values: list[int | float] = []
    for row in calls:
        value = row.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        values.append(value)
    return sum(values)


def _call_list(session: dict[str, Any]) -> list[Any]:
    raw = session.get("usage_calls")
    if isinstance(raw, list):
        return list(raw)
    return []


def _write_log(session: dict[str, Any], calls: list[Any], seconds: Any) -> None:
    clean = [row for row in calls if isinstance(row, dict)]
    models = [row.get("model") for row in clean if isinstance(row.get("model"), str)]
    measured: int | float | None
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        measured = None
    else:
        measured = seconds
    if measured is not None and not session.get("seconds_measured"):
        measured = None
    row = {
        "session_id": session["session_id"],
        "space_id": session["space_id"],
        "kind": session["kind"],
        "model": models[-1] if models else None,
        "prompt_tokens": _total_usage(clean, "prompt_tokens"),
        "completion_tokens": _total_usage(clean, "completion_tokens"),
        "total_tokens": _total_usage(clean, "total_tokens"),
        "cost": _total_usage(clean, "cost"),
        "seconds": measured,
        "steps": int(session.get("steps_used") or 0),
        "stopped": session.get("stopped"),
    }
    path = _store_dir() / "runs.jsonl"
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
    session["log"] = row


def _write_final(session: dict[str, Any]) -> None:
    artifact = session.get("artifact") or {}
    folder = _store_dir() / str(session["session_id"])
    folder.mkdir(parents=True, exist_ok=True)
    kind = artifact.get("kind")
    if kind == KIND_SLIDES:
        (folder / "deck.pptx").write_bytes(_pptx(artifact))
        (folder / "deck.pdf").write_bytes(_pdf(artifact))
        session["files"] = ["deck.pptx", "deck.pdf"]
        return
    if kind == KIND_DOC:
        (folder / "report.pdf").write_bytes(_pdf_pages(_doc_pages(artifact)))
        (folder / "report.json").write_text(
            json.dumps(artifact, default=str),
            encoding="utf-8",
        )
        session["files"] = ["report.pdf", "report.json"]
        return
    (folder / "app.json").write_text(json.dumps(artifact, default=str), encoding="utf-8")
    session["files"] = ["app.json"]


def _doc_pages(artifact: Mapping[str, Any]) -> list[list[str]]:
    pages: list[list[str]] = [
        [str(artifact.get("title") or ""), "", str(artifact.get("exec_summary") or "")]
    ]
    for finding in artifact.get("findings") or []:
        if not isinstance(finding, Mapping):
            continue
        lines = [str(finding.get("text") or ""), str(finding.get("sql") or "")]
        for cite in finding.get("citations") or []:
            if isinstance(cite, Mapping):
                lines.append(f"{cite.get('value')} {cite.get('sql')}")
        pages.append(lines[:18])
    return pages or [["Empty"]]


def _save(session: dict[str, Any]) -> None:
    clean = {key: value for key, value in session.items() if key != "_last_raw"}
    _write_json(_store_dir() / f"{session['session_id']}.json", clean)


def _load(session_id: str) -> dict[str, Any]:
    _require_on()
    if not _IDENT.fullmatch(session_id):
        raise CreateProblem("session_required")
    raw = _read_json(_store_dir() / f"{session_id}.json")
    if not isinstance(raw, dict) or raw.get("session_id") != session_id:
        raise CreateProblem("session_required")
    return raw


def _store_dir() -> Path:
    raw = os.environ.get(STORE_ENV, "").strip()
    path = Path(raw) if raw else Path("data") / "create"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _warehouse() -> Path:
    raw = os.environ.get(WAREHOUSE_ENV, "").strip()
    if raw:
        return Path(raw)
    from dms_executor.demo_warehouse import warehouse_path

    return warehouse_path()


def _grantable(space_id: str, warehouse: Path) -> set[str]:
    raw = os.environ.get(GRANTS_ENV, "").strip()
    if raw:
        return {part.strip() for part in raw.split(",") if part.strip()}
    from dms_executor import Executor

    return set(Executor(cortex=None, warehouse_path=warehouse).grantable_tables(space_id=space_id))


def _ontology() -> list[dict[str, str]]:
    raw = os.environ.get("DMS_CREATE_ONTOLOGY", "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name:
            out.append({"name": name})
    return out


def _project(
    values: list[float],
    method: str,
    period: int | None,
    horizon: int,
) -> list[float]:
    out: list[float] = []
    for step in range(1, horizon + 1):
        if method == "naive":
            out.append(values[-1])
        elif method == "mean":
            out.append(sum(values) / len(values))
        elif method == "drift":
            intercept, slope = _fit_line(values)
            out.append(intercept + slope * (len(values) + step - 1))
        elif method == "seasonal_naive" and period:
            idx = len(values) - period + (step - 1) % period
            out.append(values[idx])
        else:
            out.append(values[-1])
    return out


def _fit_line(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 1:
        return values[0], 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    var_x = sum((index - mean_x) ** 2 for index in range(n))
    if var_x == 0:
        return mean_y, 0.0
    cov = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    slope = cov / var_x
    return mean_y - slope * mean_x, slope


def _sigma(values: list[float], method: str, period: int | None) -> float:
    residuals: list[float] = []
    if method == "drift":
        intercept, slope = _fit_line(values)
        residuals = [value - (intercept + slope * index) for index, value in enumerate(values)]
    elif method == "mean":
        mean = sum(values) / len(values)
        residuals = [value - mean for value in values]
    elif method == "naive":
        residuals = [values[index] - values[index - 1] for index in range(1, len(values))]
    elif method == "seasonal_naive" and period:
        residuals = [values[index] - values[index - period] for index in range(period, len(values))]
    if len(residuals) < 2:
        return 0.0
    return float(statistics.pstdev(residuals))


def _next_labels(labels: list[str], horizon: int) -> list[str]:
    if labels and all(_MONTH.fullmatch(label) for label in labels):
        year_text, month_text = labels[-1].split("-")
        year = int(year_text)
        month = int(month_text)
        out: list[str] = []
        for _ in range(horizon):
            month += 1
            if month == 13:
                month = 1
                year += 1
            out.append(f"{year:04d}-{month:02d}")
        return out
    return [f"next-{index + 1}" for index in range(horizon)]


def _forecast_field(block: Mapping[str, Any], field: str) -> str | None:
    if field in {"backtest_error", "holdout_actual", "holdout_predicted"}:
        return _render(block.get(field))
    point = _FORECAST_POINT.fullmatch(field)
    if not point:
        return None
    name, index_text = point.group(1), int(point.group(2))
    rows = block.get("forecast")
    if not isinstance(rows, list) or index_text >= len(rows):
        return None
    row = rows[index_text]
    if not isinstance(row, Mapping) or name not in {"value", "low", "high"}:
        return None
    return _render(row.get(name))


_FORECAST_POINT = re.compile(r"(value|low|high)_(\d+)")


def _bind_filters(sql: str, chosen: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = chosen.get(key)
        if value is None:
            return match.group(0)
        return "'" + value.replace("'", "''") + "'"

    return _FILTER.sub(replace, sql)


def _filter_options(artifact: Mapping[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for item in artifact.get("filters") or []:
        if isinstance(item, Mapping) and isinstance(item.get("id"), str):
            values = _as_list(item.get("options"))
            out[item["id"]] = {str(value) for value in values}
    return out


def _pptx(artifact: Mapping[str, Any]) -> bytes:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    ink = RGBColor(0x1A, 0x23, 0x32)
    accent = RGBColor(0x0D, 0x6B, 0x5C)
    for slide in artifact.get("slides") or []:
        board = prs.slides.add_slide(blank)
        _textbox(
            board,
            slide["title"],
            Inches(0.7),
            Inches(0.45),
            Inches(12),
            Inches(0.8),
            32,
            accent,
            True,
        )
        _textbox(
            board,
            slide["insight"],
            Inches(0.7),
            Inches(1.35),
            Inches(12),
            Inches(1.1),
            18,
            ink,
            False,
        )
        _chart_on_slide(board, slide, CategoryChartData, XL_CHART_TYPE, Inches)
        notes = board.notes_slide.notes_text_frame
        notes.text = str(slide.get("notes") or "")
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _textbox(
    slide: Any,
    text: str,
    left: Any,
    top: Any,
    width: Any,
    height: Any,
    size: int,
    color: Any,
    bold: bool,
) -> None:
    from pptx.util import Pt

    shape = slide.shapes.add_textbox(left, top, width, height)
    frame = shape.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.text = text
    paragraph.font.size = Pt(size)
    paragraph.font.bold = bold
    paragraph.font.color.rgb = color
    paragraph.font.name = "Calibri"


def _chart_on_slide(
    slide: Any,
    spec: Mapping[str, Any],
    chart_data: Any,
    chart_type: Any,
    inches: Any,
) -> None:
    forecast = spec.get("forecast")
    chart = spec.get("chart")
    rows = spec.get("rows") if isinstance(spec.get("rows"), list) else []
    if isinstance(forecast, Mapping) and forecast.get("history"):
        data = chart_data()
        history = forecast["history"]
        future = forecast.get("forecast") or []
        data.categories = [point["time"] for point in history] + [point["time"] for point in future]
        history_values = [point["value"] for point in history]
        future_values = [point["value"] for point in future]
        data.add_series("Value", history_values + future_values)
        data.add_series("Low", history_values + [point["low"] for point in future])
        data.add_series("High", history_values + [point["high"] for point in future])
        slide.shapes.add_chart(
            chart_type.LINE,
            inches(0.7),
            inches(2.7),
            inches(11.8),
            inches(4.2),
            data,
        )
        return
    if not isinstance(chart, Mapping) or not rows:
        return
    y_name = chart["y"][0]
    data = chart_data()
    data.categories = [str(row.get(chart["x"])) for row in rows]
    data.add_series(str(y_name), [_as_float(row.get(y_name)) or 0 for row in rows])
    kind = chart_type.LINE if chart.get("kind") == "line" else chart_type.COLUMN_CLUSTERED
    slide.shapes.add_chart(kind, inches(0.7), inches(2.7), inches(11.8), inches(4.2), data)


def _pdf(artifact: Mapping[str, Any]) -> bytes:
    pages: list[list[str]] = []
    for slide in artifact.get("slides") or []:
        lines = [str(slide.get("title") or ""), "", str(slide.get("insight") or "")]
        forecast = slide.get("forecast")
        if isinstance(forecast, Mapping):
            lines.append(str(forecast.get("assumptions") or ""))
            lines.append(f"Backtest error {forecast.get('backtest_error')}")
        for row in (slide.get("rows") or [])[:8]:
            if isinstance(row, Mapping):
                lines.append(", ".join(f"{key}={value}" for key, value in row.items()))
        pages.append(lines[:18])
    return _pdf_pages(pages or [["Empty"]])


def _pdf_pages(pages: list[list[str]]) -> bytes:
    objects: list[bytes] = []

    def add(body: str) -> int:
        objects.append(body.encode("latin-1", errors="replace"))
        return len(objects)

    font_id = 0
    page_ids: list[int] = []
    content_ids: list[int] = []
    # Font is object 1 after we know ids. Build contents first, then rewrite.
    # Simpler: reserve by appending in order.
    kids: list[int] = []
    content_bodies: list[str] = []
    for lines in pages:
        commands = ["BT", "/F1 22 Tf", "72 740 Td"]
        first = True
        for line in lines:
            safe = _pdf_text(line)[:110]
            if first:
                commands.append(f"({safe}) Tj")
                first = False
            else:
                commands.append("0 -28 Td")
                commands.append(f"({safe}) Tj")
        commands.append("ET")
        content_bodies.append("\n".join(commands))
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_id = font
    for body in content_bodies:
        content_ids.append(add(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream"))
    for content_id in content_ids:
        page_ids.append(
            add(
                "<< /Type /Page /Parent 0 0 R /MediaBox [0 0 842 595] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            )
        )
        kids.append(page_ids[-1])
    kids_text = " ".join(f"{kid} 0 R" for kid in kids)
    pages_id = add(f"<< /Type /Pages /Count {len(kids)} /Kids [{kids_text}] >>")
    for index, page_id in enumerate(page_ids):
        objects[page_id - 1] = (
            objects[page_id - 1]
            .decode("latin-1")
            .replace("/Parent 0 0 R", f"/Parent {pages_id} 0 R")
            .encode("latin-1")
        )
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
    return _pdf_pack(objects, catalog)


def _pdf_pack(objects: list[bytes], root: int) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {len(objects) + 1} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF".encode(
            "ascii"
        )
    )
    return bytes(out)


def _pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _public_spec(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in _MODEL_KEYS}


def _plan_brief(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tables": [row.get("name") for row in plan.get("tables") or [] if isinstance(row, Mapping)],
        "joins": plan.get("joins") or [],
        "metrics": [
            {"name": row.get("name"), "sql": row.get("sql")}
            for row in plan.get("metrics") or []
            if isinstance(row, Mapping)
        ],
    }


def _trace(session: dict[str, Any], step: str, status: str) -> None:
    session.setdefault("trace", []).append({"step": step, "status": status})


def _title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not _fits(text, _LIMITS["title"]):
        return None
    return text


def _fits(text: str, limit: int) -> bool:
    if len(text) > limit or text.count("\n") > 3:
        return False
    return True


def _short(value: str, limit: int, code: str) -> str:
    text = (value or "").strip()
    if not text:
        raise CreateProblem(code)
    return text[:limit]


def _space(space_id: str) -> str:
    text = (space_id or "").strip()
    if not text or len(text) > 128:
        raise CreateProblem("space_required")
    return text


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _render(value: Any) -> str | None:
    number = _as_float(value)
    if number is None:
        return None
    return _render_float(number)


def _render_float(value: float) -> str:
    if not math.isfinite(value):
        return ""
    rounded = round(value, 4)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


def _num(value: float) -> int | float:
    rendered = _render_float(value)
    if rendered and "." not in rendered:
        return int(rendered)
    return float(rendered) if rendered else 0


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _row_numbers(rows: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for row in rows:
        for value in row.values():
            rendered = _render(value)
            if rendered:
                found.add(rendered)
            elif isinstance(value, str):
                for match in _NUM.finditer(value):
                    found.add(match.group(1))
    return found


def _same_number(token: str, allowed: set[str]) -> bool:
    try:
        left = float(token)
    except ValueError:
        return False
    for item in allowed:
        try:
            if math.isclose(left, float(item), rel_tol=0, abs_tol=1e-6):
                return True
        except ValueError:
            continue
    return False


def _json_row(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        number = _as_float(value)
        if isinstance(value, str) or value is None:
            out[str(key)] = value
        elif number is not None and float(number).is_integer():
            out[str(key)] = int(number)
        elif number is not None:
            out[str(key)] = number
        else:
            out[str(key)] = str(value)
    return out


def _canon_rows(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, sort_keys=True, default=str)


def _strip_fence(text: str) -> str:
    raw = text.strip()
    if not raw.startswith(_FENCE):
        return raw
    lines = raw.splitlines()
    if lines and lines[0].startswith(_FENCE):
        lines = lines[1:]
    if lines and lines[-1].strip() == _FENCE:
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _reject_model_keys(body: Mapping[str, Any]) -> None:
    found = _MODEL_KEYS.intersection(body)
    if found:
        raise CreateProblem("model_field")


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
