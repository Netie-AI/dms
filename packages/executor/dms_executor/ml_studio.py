"""Question to a saved modelling run. OpenVault plans and explains.

The harness checks the SQL is one aggregated read, then fits in
``ml_fit``. Raw rows are not put in the planner prompt or the insight
prompt. Credentials stay in OpenVault: the chat call sends a role only.

ponytail: runs are JSON files under DMS_ML_STUDIO_DIR with one process
lock. Upgrade: a file lock when more than one worker shares the directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dms_executor.ml_fit import (
    MAX_ROWS,
    TASKS,
    enabled,
    extensions,
    fit_frame,
    register_task,
)

__all__ = [
    "enabled",
    "execute_run",
    "extensions",
    "insight_messages",
    "plan_messages",
    "read_run",
    "register_task",
    "start_run",
]

_LOCK = threading.Lock()
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCHEMA_SQL = """
SELECT table_schema, table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY table_schema, table_name, ordinal_position
"""
_PLAN_SYSTEM = (
    "You plan one modelling job from a question and a schema map. "
    "Reply with one JSON object and nothing else. "
    "Keys: task, target, features, time_column, season, sql, candidates. "
    "task is forecast, classification, regression, or clustering. "
    "sql is one read-only SELECT that aggregates in the database. "
    "It must use SUM, AVG, COUNT, MIN, or MAX. "
    "Use only tables and columns from the schema. Do not include cell values. "
    "candidates is a subset of the allowed models. "
    "forecast: seasonal_naive, ets, gboost_lag. "
    "classification: logistic, gboost, rf. "
    "regression: linear, gboost, rf. "
    "clustering: kmeans. "
    "Reinforcement learning is out of scope."
)
_INSIGHT_SYSTEM = (
    "You explain a finished model comparison. Use only the numbers in the JSON. "
    "Do not invent a metric, a row, or a result. Do not say PASS. "
    "If proven_solution is false, say it is not a proven solution. "
    "Write one short paragraph."
)


def default_chat(role: str, messages: list[dict[str, str]], timeout: float) -> dict[str, Any]:
    """OpenVault chat. The body is role and messages. Routing stays in OpenVault."""
    from dms_executor.ai_floor_direct import ov_chat

    return ov_chat(role, messages, timeout=timeout)


def store_dir() -> Path:
    raw = os.environ.get("DMS_ML_STUDIO_DIR")
    path = Path(raw) if raw else Path(tempfile.gettempdir()) / "dms_ml_studio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def start_run(
    *,
    question: str,
    actor: str | None = None,
    event_id: str | None = None,
    chat: Any = None,
    inline: bool = False,
) -> dict[str, Any]:
    """Queue a run and return the record. The fit continues in the background."""
    if not enabled():
        raise PermissionError("flag_off")
    run_id = uuid.uuid4().hex
    doc = {
        "run_id": run_id,
        "status": "queued",
        "question": question,
        "card": None,
        "error": None,
        "created": _now(),
    }
    _write(run_id, doc)

    def work() -> None:
        _update(run_id, status="running")
        try:
            card = execute_run(
                question=question,
                actor=actor,
                event_id=event_id,
                chat=chat or default_chat,
            )
            status = str(card.get("status") or "failed")
            if status not in {"succeeded", "refused", "failed"}:
                status = "failed"
            _update(run_id, status=status, card=card, finished=_now())
        except Exception as exc:  # noqa: BLE001 - store the class, never the text
            _update(run_id, status="failed", error=type(exc).__name__, finished=_now())

    if inline:
        work()
    else:
        threading.Thread(target=work, name="dms-ml-studio", daemon=True).start()
    found = read_run(run_id)
    return found or doc


def read_run(run_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        return None
    path = store_dir() / f"{run_id}.json"
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def execute_run(
    *,
    question: str,
    actor: str | None = None,
    event_id: str | None = None,
    chat: Any = None,
) -> dict[str, Any]:
    """Plan, aggregate, fit, and ask for a paragraph. No raw rows leave the process."""
    talk = chat or default_chat
    if not enabled():
        return _refused("flag_off")
    from dms_executor.demo_warehouse import warehouse_path

    path = warehouse_path()
    if not path.is_file():
        return _refused("warehouse_missing")
    schema, known, err = describe_schema(path)
    if err:
        return _failed(err)
    plan, why = plan_job(question, schema, talk)
    if why or plan is None:
        return _refused(why or "plan_invalid")
    sql = str(plan["sql"])
    checked = check_model_sql(sql, known)
    if checked:
        return _refused(checked)
    from dms_executor.sql_loop import run_readonly

    fetched, sql_err = run_readonly(_cap_sql(sql), path)
    if sql_err:
        return _failed(_safe_code(sql_err))
    rows = fetched or []
    if len(rows) > MAX_ROWS:
        return _refused("row_budget")
    columns, frame = _frame(rows)
    card = fit_frame(
        task=str(plan["task"]),
        columns=columns,
        rows=frame,
        target=plan.get("target") or None,
        features=list(plan.get("features") or []),
        time_column=plan.get("time_column") or None,
        season=int(plan.get("season") or 12),
    )
    card["planner_candidates"] = list(plan.get("candidates") or [])
    card["audit"] = {
        "action": "studio.ml_run",
        "actor": actor or "",
        "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
        "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
        "row_count": card.get("row_count"),
        "task": card.get("task"),
        "proven_solution": card.get("proven_solution"),
        "gate_event_id": event_id,
    }
    return apply_insight(card, talk)


def describe_schema(path: Path) -> tuple[list[dict[str, str]], set[str], str | None]:
    from dms_executor.sql_loop import run_readonly

    rows, err = run_readonly(_SCHEMA_SQL, path)
    if err:
        return [], set(), _safe_code(err)
    compact: list[dict[str, str]] = []
    known: set[str] = set()
    for row in rows or []:
        schema = str(row.get("table_schema") or "")
        table = str(row.get("table_name") or "")
        column = str(row.get("column_name") or "")
        if not table or not column:
            continue
        known.add(table.lower())
        known.add(f"{schema}.{table}".lower())
        if len(compact) >= 500:
            continue
        compact.append(
            {
                "table_schema": schema,
                "table_name": table,
                "column_name": column,
                "data_type": str(row.get("data_type") or ""),
            }
        )
    return compact, known, None


def plan_messages(question: str, schema: list[dict[str, str]]) -> list[dict[str, str]]:
    lines = [
        f"{row['table_schema']}.{row['table_name']}.{row['column_name']} {row['data_type']}"
        for row in schema[:400]
    ]
    text = "\n".join(lines)
    if len(text) > 16000:
        text = text[:16000]
    if len(schema) > 400:
        text += f"\n({len(schema) - 400} columns omitted)"
    user = f"SCHEMA:\n{text}\n\nQUESTION:\n{question}"
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": user},
    ]


def plan_job(
    question: str, schema: list[dict[str, str]], chat: Any
) -> tuple[dict[str, Any] | None, str | None]:
    """Ask the model for a plan. A bad reply is refused. The question is not parsed."""
    messages = plan_messages(question, schema)
    for attempt in range(2):
        try:
            reply = chat("reason", messages, timeout=30)
        except Exception:  # noqa: BLE001
            return None, "model_unreachable"
        parsed = _parse_json(_content(reply))
        if parsed is None:
            if attempt == 0:
                messages = [
                    {"role": "system", "content": _PLAN_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            messages[1]["content"]
                            + "\n\nThe last reply was not one JSON object. "
                            + "Reply with the JSON object only."
                        ),
                    },
                ]
                continue
            return None, "plan_invalid"
        why = _validate_plan(parsed)
        if why:
            return None, why
        return parsed, None
    return None, "plan_invalid"


def check_model_sql(sql: str, known: set[str]) -> str | None:
    """None when the statement is one aggregated read over known tables."""
    from dms_executor.ai_floor_direct import check_sql

    why = check_sql(sql, "duckdb", {name.lower() for name in known})
    if why:
        if why.startswith("sql_not_analyzable"):
            return "sql_not_analyzable"
        if why.startswith("ungranted"):
            return "unknown_table"
        return why
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001
        return "sql_not_analyzable"
    if tree is None or not any(True for _ in tree.find_all(exp.AggFunc)):
        return "not_aggregated"
    return None


def insight_messages(card: dict[str, Any]) -> list[dict[str, str]]:
    """Metric JSON only. Rows, SQL, and the question stay out of this prompt."""
    payload = {
        "task": card.get("task"),
        "proven_solution": card.get("proven_solution"),
        "winner": card.get("winner"),
        "baseline": card.get("baseline"),
        "result_metric": card.get("result_metric"),
        "baseline_metric": card.get("baseline_metric"),
        "models": [
            {
                "name": row.get("name"),
                "held_out": row.get("held_out"),
                "selection": row.get("selection"),
            }
            for row in card.get("models") or []
            if isinstance(row, dict)
        ],
        "row_count": card.get("row_count"),
        "leakage": (card.get("eda") or {}).get("leakage"),
        "status": card.get("status"),
        "reason": card.get("reason"),
    }
    return [
        {"role": "system", "content": _INSIGHT_SYSTEM},
        {"role": "user", "content": json.dumps(payload, sort_keys=True, default=str)},
    ]


def apply_insight(card: dict[str, Any], chat: Any) -> dict[str, Any]:
    if card.get("status") not in {"succeeded", "refused"}:
        card["insights"] = None
        card["insights_status"] = "not_run"
        return card
    try:
        reply = chat("summarize", insight_messages(card), timeout=20)
    except Exception:  # noqa: BLE001
        card["insights"] = None
        card["insights_status"] = "model_unreachable"
        return card
    text = _content(reply).strip()
    if not text:
        card["insights"] = None
        card["insights_status"] = "empty_reply"
        return card
    card["insights"] = text[:2000]
    card["insights_status"] = "ok"
    return card


def _validate_plan(obj: dict[str, Any]) -> str | None:
    task = obj.get("task")
    if task == "reinforcement" or task not in TASKS:
        return "task_out_of_scope"
    sql = obj.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return "plan_invalid"
    target = obj.get("target") or ""
    if target and not _IDENT.fullmatch(str(target)):
        return "plan_invalid"
    time_column = obj.get("time_column") or ""
    if time_column and not _IDENT.fullmatch(str(time_column)):
        return "plan_invalid"
    features = obj.get("features") or []
    if not isinstance(features, list) or any(not _IDENT.fullmatch(str(name)) for name in features):
        return "plan_invalid"
    season = obj.get("season", 12)
    if season is None:
        season = 12
    if isinstance(season, float) and season.is_integer():
        season = int(season)
    if isinstance(season, bool) or not isinstance(season, int) or not 2 <= season <= 52:
        return "plan_invalid"
    obj["season"] = season
    obj["target"] = str(target)
    obj["time_column"] = str(time_column)
    obj["features"] = [str(name) for name in features]
    candidates = obj.get("candidates") or []
    if not isinstance(candidates, list):
        return "plan_invalid"
    obj["candidates"] = [str(name) for name in candidates if isinstance(name, str)]
    if task == "clustering":
        obj["target"] = ""
    elif not obj["target"]:
        return "plan_invalid"
    if task == "forecast" and not obj["time_column"]:
        return "plan_invalid"
    return None


def _parse_json(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def _content(reply: Any) -> str:
    if not isinstance(reply, dict):
        return ""
    choices = reply.get("choices") or []
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or "")


def _cap_sql(sql: str) -> str:
    body = sql.strip().rstrip(";")
    return f"SELECT * FROM ({body}) AS ml_agg LIMIT {MAX_ROWS + 1}"


def _frame(rows: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    if not rows:
        return [], []
    columns = [str(key) for key in rows[0].keys()]
    return columns, [[row.get(column) for column in columns] for row in rows]


def _refused(reason: str) -> dict[str, Any]:
    return {"status": "refused", "reason": reason, "proven_solution": False, "models": []}


def _failed(reason: str) -> dict[str, Any]:
    return {"status": "failed", "reason": reason, "proven_solution": False, "models": []}


def _safe_code(err: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_:.+-]{1,80}", err):
        return err
    return "sql_failed"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _path(run_id: str) -> Path:
    return store_dir() / f"{run_id}.json"


def _write(run_id: str, doc: dict[str, Any]) -> None:
    path = _path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(doc, default=str), encoding="utf-8")
        tmp.replace(path)


def _update(run_id: str, **patch: Any) -> None:
    with _LOCK:
        current = read_run(run_id) or {"run_id": run_id}
        current.update(patch)
        path = _path(run_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, default=str), encoding="utf-8")
        tmp.replace(path)
