"""Demo warehouse governed metrics for the DR-0002 leftover asks.

Exact-question match, same F83 posture as VQ-02: Cortex submit + ledger, no
local DuckDB fallback. The rich lake is Cortex
(``/var/cortex/data/dms_demo.duckdb``). DMS local is a thin reseed and is not
the answer source.

Not steward VQ-02: these ids are the product pack, not Studio-registered.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from dms_executor.demo_ask import normalize_ask_question
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql
from dms_executor.verified_queries import rows_from_submit_result

SPEND_BY_COUNTRY_Q = "What is our total spend by supplier country?"
STOCK_BY_CATEGORY_Q = "What is total stock value by category?"
TOTAL_SPEND_Q = "What is our total spend?"

SPEND_BY_COUNTRY_SQL = (
    "SELECT s.country, ROUND(SUM(i.quantity_kg * i.unit_cost_myr), 2) "
    "AS total_spend_myr FROM inventory AS i JOIN suppliers AS s "
    "ON i.supplier_id = s.supplier_id GROUP BY s.country "
    "ORDER BY total_spend_myr DESC, s.country ASC"
)
STOCK_BY_CATEGORY_SQL = (
    "SELECT category, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS stock_value_myr "
    "FROM inventory GROUP BY category "
    "ORDER BY stock_value_myr DESC, category ASC"
)
TOTAL_SPEND_SQL = (
    "SELECT ROUND(COALESCE(SUM(i.quantity_kg * i.unit_cost_myr), 0), 2) "
    "AS total_spend_myr FROM inventory AS i JOIN suppliers AS s "
    "ON i.supplier_id = s.supplier_id"
)


@dataclass(frozen=True)
class PackMetric:
    metric_id: str
    question: str
    sql: str
    tables: tuple[str, ...]


PACK_METRICS: tuple[PackMetric, ...] = (
    PackMetric(
        metric_id="spend_by_country",
        question=SPEND_BY_COUNTRY_Q,
        sql=SPEND_BY_COUNTRY_SQL,
        tables=("inventory", "suppliers"),
    ),
    PackMetric(
        metric_id="stock_value_by_category",
        question=STOCK_BY_CATEGORY_Q,
        sql=STOCK_BY_CATEGORY_SQL,
        tables=("inventory",),
    ),
    PackMetric(
        metric_id="total_spend",
        question=TOTAL_SPEND_Q,
        sql=TOTAL_SPEND_SQL,
        tables=("inventory", "suppliers"),
    ),
)


def _norm(question: str) -> str:
    return " ".join(normalize_ask_question(question).casefold().split())


def _as_of() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def lookup_pack_metric(
    question: str,
    *,
    grantable: set[str] | None = None,
    tables: list[str] | None = None,
) -> PackMetric | None:
    """Return the pack metric for this exact ask, or None.

    Grounded-file asks skip. A Space that does not grant every table the SQL
    names skips (Warehouse Ops vs suppliers). Column presence is Cortex's job
    on submit — do not probe the thin DMS local file.
    """
    if tables:
        return None
    qn = _norm(question)
    if not qn:
        return None
    hit = next((m for m in PACK_METRICS if _norm(m.question) == qn), None)
    if hit is None:
        return None
    allowed = grantable if grantable is not None else set()
    if any(t not in allowed for t in hit.tables):
        return None
    try:
        reject_hostile_chat_sql(hit.sql)
    except SecurityEvent:
        return None
    return hit


def envelope_from_pack_submit(
    *,
    metric: PackMetric,
    result: Any,
    question: str,
    space_id: str | None = None,
    session_id: str | None = None,
    audit_id: str | None = None,
) -> dict[str, Any]:
    out_rows = rows_from_submit_result(result)
    run_id = getattr(result, "run_id", None) or ""
    receipt = (audit_id or "").strip() or (str(run_id).strip() if run_id else "")
    if not receipt:
        receipt = f"cortex_submit_{metric.metric_id}"
    text = f"Found {len(out_rows)} row(s)."
    if out_rows:
        text += "\n" + "\n".join(
            "  - " + ", ".join(f"{k}={v}" for k, v in row.items()) for row in out_rows[:12]
        )
    env = build_answer_envelope(
        answer_id=f"ans_{metric.metric_id}",
        text=text,
        badge="L1_GOVERNED_METRIC",
        abstained=False,
        rows=out_rows,
        sql_used=metric.sql,
        assumptions=[
            f"governed metric {metric.metric_id}",
            "executed via Cortex submit",
        ],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="governed_metric",
        question=question,
        audit_id=receipt,
        grounded_tables=list(metric.tables),
    )
    assert_envelope_valid(env)
    return env


def maybe_pack_ask(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    grantable: set[str] | None = None,
    tables: list[str] | None = None,
    submit: Callable[[str], Any] | None = None,
    ledger_append: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any] | None:
    """L1 envelope when the demo pack matches and Cortex executed the SQL.

    Missing submit/ledger does not fall back to local DuckDB (F83). Submit
    failures miss rather than 503 — leftover class is honest 200 ABSTAIN.
    """
    hit = lookup_pack_metric(
        question,
        grantable=grantable,
        tables=tables,
    )
    if hit is None:
        return None
    if submit is None or ledger_append is None:
        return None
    try:
        result = submit(hit.sql)
    except Exception:  # noqa: BLE001
        return None
    ok = getattr(result, "ok", None)
    if ok is False:
        return None
    if getattr(result, "output", None) is None:
        return None
    run_id = str(getattr(result, "run_id", None) or "")
    try:
        led = ledger_append({"sql": hit.sql, "run_id": run_id})
    except Exception:  # noqa: BLE001
        return None
    entry_id = getattr(led, "entry_id", None) if led is not None else None
    if not (isinstance(entry_id, str) and entry_id.strip()):
        return None
    led_hash = getattr(led, "hash", None)
    if not (isinstance(led_hash, str) and led_hash.strip()) or led_hash == entry_id:
        return None
    return envelope_from_pack_submit(
        metric=hit,
        result=result,
        question=question,
        space_id=space_id,
        session_id=session_id,
        audit_id=entry_id.strip(),
    )
