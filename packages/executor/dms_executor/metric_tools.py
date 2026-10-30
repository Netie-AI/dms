"""Read-only metric tools the model may invoke by name.

Churn / forecast / revenue live here as authored SQL, not as LLM-written
arithmetic. The caller passes a tool name; this module runs the SQL. Unknown
names refuse. Hostile SQL refuses. No UPDATE.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.demo_warehouse import (
    DEMO_TABLES,
    ensure_demo_warehouse,
    execute_sql,
    warehouse_path,
)
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql
from dms_executor.object_views import list_object_names

_SELECT = "select"
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OBJECT_QUAL = re.compile(r"\bobjects\.([A-Za-z_][A-Za-z0-9_]*)\b", re.I)
_PUNCT = re.compile(r"[?!.]+$")
_TIME = re.compile(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?([T\s].*)?$")
_TOOLS_TABLE = "main._metric_tools"
_LOCK = threading.Lock()
_COMPANY = "company-default"


@dataclass(frozen=True)
class MetricTool:
    name: str
    sql: str
    description: str
    aliases: tuple[str, ...] = ()


#: Demo pack. Customer packs replace this list; the LLM never authors entries.
DEMO_TOOLS: tuple[MetricTool, ...] = (
    MetricTool(
        name="stock_on_hand",
        sql="SELECT COALESCE(SUM(quantity_kg), 0) AS stock_on_hand FROM inventory",
        description="Total stock on hand in kg on the demo inventory grain.",
        aliases=("stock on hand",),
    ),
)


class UnknownMetricTool(LookupError):
    """The model named a tool that is not in the allowlist."""


def list_metric_tools(tools: Sequence[MetricTool] | None = None) -> list[dict[str, str]]:
    """Names and descriptions only -- never the SQL -- for a model prompt."""
    catalog = tools if tools is not None else DEMO_TOOLS
    return [{"name": t.name, "description": t.description} for t in catalog]


def call_metric_tool(
    con: duckdb.DuckDBPyConnection,
    name: str,
    *,
    tools: Mapping[str, MetricTool] | None = None,
) -> dict[str, Any]:
    """Execute one allowlisted SELECT. Returns rows + the SQL that ran."""
    catalog = tools if tools is not None else {t.name: t for t in DEMO_TOOLS}
    tool = catalog.get(name)
    if tool is None:
        raise UnknownMetricTool(name)
    sql = tool.sql.strip()
    if not sql.lower().startswith(_SELECT):
        raise SecurityEvent("statement_not_allowed", "metric tools are SELECT only")
    reject_hostile_chat_sql(sql)
    rel = con.execute(sql)
    cols = [d[0] for d in rel.description]
    rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    return {
        "tool": name,
        "sql": sql,
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
    }


def _fold(question: str) -> str:
    """Exact-name match key. Underscores become spaces. No intent regex."""
    folded = " ".join((question or "").lower().replace("_", " ").replace("-", " ").split())
    return _PUNCT.sub("", folded).strip()


def match_metric_tool(
    question: str, tools: Sequence[MetricTool] | None = None
) -> MetricTool | None:
    """Hit only the tool name or an authored alias. Free-text BI asks miss."""
    q = _fold(question)
    if not q:
        return None
    catalog = tools if tools is not None else DEMO_TOOLS
    for tool in catalog:
        keys = {_fold(tool.name), *(_fold(a) for a in tool.aliases)}
        if q in keys:
            return tool
    return None


def _tables_in_sql(sql: str) -> set[str]:
    demo = {tok for tok in _IDENT.findall(sql or "") if tok in DEMO_TABLES}
    objs = {f"objects.{name}" for name in _OBJECT_QUAL.findall(sql or "")}
    return demo | objs


def _scope(space_id: str | None) -> str:
    raw = (space_id or "").strip()
    return raw if raw else _COMPANY


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _json_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{str(k): _cell(v) for k, v in row.items()} for row in rows]


def _looks_time_value(value: Any) -> bool:
    if hasattr(value, "year"):
        return True
    if not isinstance(value, str):
        return False
    return bool(_TIME.match(value.strip()))


def _chart_for_rows(rows: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    if not rows:
        return None
    first = rows[0]
    keys = list(first.keys())
    nums = [
        k
        for k in keys
        if isinstance(first.get(k), (int, float)) and not isinstance(first.get(k), bool)
    ]
    cats = [k for k in keys if k not in nums and isinstance(first.get(k), str)]
    if len(rows) == 1 and len(nums) == 1:
        return {
            "kind": "bignum",
            "value": first[nums[0]],
            "label": title,
            "title": title,
            "y": nums[0],
        }
    if nums and cats:
        y_key, x_key = nums[0], cats[0]
        xs = [row.get(x_key) for row in rows]
        ys = [row.get(y_key) for row in rows]
        if len(rows) >= 2 and all(_looks_time_value(v) for v in xs):
            return {"kind": "line", "x": x_key, "y": y_key, "title": title}
        pie_ok = (
            2 <= len(rows) <= 8
            and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
                for v in ys
            )
        )
        kind = "pie" if pie_ok else "hbar"
        return {"kind": kind, "x": x_key, "y": y_key, "title": title}
    return None


def maybe_metric_ask(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    tables: list[str] | None = None,
    tools: Sequence[MetricTool] | None = None,
) -> dict[str, Any] | None:
    """L0 envelope when the ask is exactly a tool name or alias; else None.

    Same class as verified queries: a human authored the SQL. CCA and Cortex
    never see the hit. Grounded-file asks miss unless every table the tool
    reads is in that grounding list.
    """
    catalog = tools if tools is not None else load_metric_tools(
        space_id=space_id, warehouse=warehouse
    )
    tool = match_metric_tool(question, catalog)
    if tool is None:
        return None
    sql = tool.sql.strip()
    if not sql.lower().startswith(_SELECT):
        return None
    needed = _tables_in_sql(sql)
    allowed = set(grantable) if grantable is not None else set(DEMO_TABLES)
    allowed.update(list_object_names(path=warehouse))
    if needed - allowed:
        return None
    if tables and needed - set(tables):
        return None
    try:
        reject_hostile_chat_sql(sql)
    except SecurityEvent:
        return None
    db = ensure_demo_warehouse(warehouse or warehouse_path())
    rows = _json_rows(execute_sql(sql, path=db))
    chart = _chart_for_rows(rows, tool.name)
    text = f"{tool.description}\nFound {len(rows)} row(s)."
    if rows:
        text += "\n  - " + ", ".join(f"{k}={v}" for k, v in rows[0].items())
    nums = []
    if len(rows) == 1:
        nums = [
            (k, v)
            for k, v in rows[0].items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
    if chart and chart.get("kind") in ("pie", "hbar", "line"):
        x_key = str(chart.get("x") or "")
        y_key = str(chart.get("y") or "")
        if chart.get("kind") == "line":
            last = rows[-1]
            text += (
                f"\n\nInsights:\n- {len(rows)} {x_key} point(s)."
                f"\n- Latest {y_key}: {last.get(y_key)} ({last.get(x_key)})."
            )
        else:
            top = max(
                rows,
                key=lambda r: r.get(y_key) if isinstance(r.get(y_key), (int, float)) else 0,
            )
            text += (
                f"\n\nInsights:\n- {len(rows)} {x_key} group(s)."
                f"\n- Highest {y_key}: {top.get(y_key)} ({top.get(x_key)})."
            )
    elif nums:
        key, val = nums[0]
        text += f"\n\nInsights:\n- {key} is {val} (authored tool {tool.name})."
    env = build_answer_envelope(
        answer_id=f"ans_metric_{tool.name}",
        text=text,
        badge="L0_CERTIFIED",
        abstained=False,
        rows=rows,
        sql_used=sql,
        assumptions=[f"metric tool {tool.name} (authored SELECT, not model SQL)"],
        as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="metric_tool",
        question=question,
        chart=chart,
        grounded_tables=sorted(needed),
        suggestions=[tool.name],
    )
    env["metric_tool"] = tool.name
    assert_envelope_valid(env)
    return env


def _ensure_tools(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TOOLS_TABLE} (
          name VARCHAR PRIMARY KEY,
          space_id VARCHAR NOT NULL,
          sql_text VARCHAR NOT NULL,
          description VARCHAR NOT NULL,
          aliases_json VARCHAR NOT NULL
        )
        """
    )


def load_metric_tools(
    *,
    space_id: str | None = None,
    warehouse: Path | None = None,
) -> tuple[MetricTool, ...]:
    """Demo tools plus steward-registered tools for this Space."""
    db = ensure_demo_warehouse(warehouse or warehouse_path())
    sid = _scope(space_id)
    extra: list[MetricTool] = []
    con = duckdb.connect(str(db))
    try:
        _ensure_tools(con)
        rows = con.execute(
            f"""
            SELECT name, sql_text, description, aliases_json
              FROM {_TOOLS_TABLE}
             WHERE space_id = ?
            """,
            [sid],
        ).fetchall()
    finally:
        con.close()
    for name, sql_text, description, aliases_json in rows:
        try:
            raw = json.loads(aliases_json or "[]")
        except json.JSONDecodeError:
            raw = []
        aliases = tuple(str(a) for a in raw) if isinstance(raw, list) else ()
        extra.append(
            MetricTool(
                name=str(name),
                sql=str(sql_text),
                description=str(description),
                aliases=aliases,
            )
        )
    return DEMO_TOOLS + tuple(extra)


def register_metric_tool(
    *,
    name: str,
    sql: str,
    description: str,
    space_id: str | None = None,
    aliases: Sequence[str] | None = None,
    warehouse: Path | None = None,
) -> dict[str, Any]:
    """Persist one authored SELECT. Objects referenced must already be published."""
    tool_name = (name or "").strip()
    sql_text = (sql or "").strip()
    if not tool_name or not _NAME.match(tool_name):
        raise ValueError("tool_name_invalid")
    if not sql_text.lower().startswith(_SELECT):
        raise SecurityEvent("statement_not_allowed", "metric tools are SELECT only")
    reject_hostile_chat_sql(sql_text)
    needed = _tables_in_sql(sql_text)
    objs = {n for n in needed if n.startswith("objects.")}
    missing = objs - set(list_object_names(path=warehouse))
    if missing:
        raise ValueError(f"unknown_object:{sorted(missing)}")
    db = ensure_demo_warehouse(warehouse or warehouse_path())
    sid = _scope(space_id)
    aliases_json = json.dumps(list(aliases or []))
    with _LOCK:
        con = duckdb.connect(str(db))
        try:
            _ensure_tools(con)
            con.execute(
                f"DELETE FROM {_TOOLS_TABLE} WHERE name = ? AND space_id = ?",
                [tool_name, sid],
            )
            con.execute(
                f"""
                INSERT INTO {_TOOLS_TABLE}
                  (name, space_id, sql_text, description, aliases_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [tool_name, sid, sql_text, description.strip() or tool_name, aliases_json],
            )
        finally:
            con.close()
    return {
        "name": tool_name,
        "sql": sql_text,
        "description": description.strip() or tool_name,
        "space_id": sid,
        "tables": sorted(needed),
    }


def tools_only_abstain(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    warehouse: Path | None = None,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    """Grounded object asks do not reach Cortex. Name a certified tool instead."""
    catalog = load_metric_tools(space_id=space_id, warehouse=warehouse)
    ground = set(tables or [])
    tagged = [
        t.name for t in catalog if _tables_in_sql(t.sql) & ground
    ]
    shown = tagged or [t.name for t in catalog]
    listed = ", ".join(shown) if shown else "(none published)"
    env = build_answer_envelope(
        answer_id="ans_tools_only",
        text=(
            "This object answers through certified tools, not generated SQL. "
            f"Ask by tool name: {listed}."
        ),
        badge="ABSTAIN",
        abstained=True,
        sql_used=None,
        assumptions=["objects grounding is tools-only (no LLM SQL)"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="metric_tool",
        question=question,
        suggestions=shown[:8],
        grounded_tables=sorted(ground),
    )
    assert_envelope_valid(env)
    return env
