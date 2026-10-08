"""Demo warehouse governed metrics for leftover + VQ-03 certified asks.

Exact-question match, same F83 posture as VQ-02: Cortex submit + ledger, no
local DuckDB fallback. The rich lake is Cortex
(``/var/cortex/data/dms_demo.duckdb``). DMS local is a thin reseed and is not
the answer source.

Not steward VQ-02: these ids are the product pack, not Studio-registered.
VQ-03 (#170) extras are Cortex ``certified_queries.yaml`` SQL, exact phrase
only. VQ-04 (#176): planted refuse phrases are not certified synonyms and
must not ship L1 even when Cortex ``route_to_metric`` would green them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from dms_executor.demo_ask import normalize_ask_question
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.manifest import OpenVaultTokenError, SecurityEvent, reject_hostile_chat_sql
from dms_executor.verified_queries import rows_from_submit_result

SPEND_BY_COUNTRY_Q = "What is our total spend by supplier country?"
STOCK_BY_CATEGORY_Q = "What is total stock value by category?"
TOTAL_SPEND_Q = "What is our total spend?"
CAPACITY_UTILISATION_Q = "Show warehouse capacity utilisation"
LOW_STOCK_WH_A_Q = "Which SKUs are below reorder level in warehouse A?"
SHIPMENT_COST_Q = "Show shipment cost by destination"
COLD_STORAGE_Q = "Which locations are cold storage?"
CAPACITY_ABOVE_90_Q = "Which locations are above 90 percent capacity?"
EXPIRED_ITEMS_Q = "Which items are expired?"
CCTV_WH_A_Q = "Show the CCTV camera for warehouse A"
HOW_FULL_TRAP_Q = "how full is each warehouse"
DELAYED_COUNT_TRAP_Q = "How many delayed incoming shipments per warehouse?"
STOCK_BY_BIN_TRAP_Q = "Show stock by storage bin"

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
CAPACITY_UTILISATION_SQL = (
    "SELECT location_code, ROUND(100.0 * current_load_kg / capacity_kg, 1) "
    "AS pct_used FROM locations"
)
LOW_STOCK_WH_A_SQL = (
    "SELECT i.sku, i.quantity_kg FROM inventory i "
    "JOIN locations l ON i.location_id = l.location_id "
    "WHERE i.quantity_kg < i.reorder_level_kg AND i.reorder_level_kg > 0 "
    "AND l.location_code = 'WH-A' ORDER BY i.quantity_kg ASC"
)
SHIPMENT_COST_SQL = (
    "SELECT l.location_code, SUM(s.cost_myr) AS total_cost_myr "
    "FROM shipments s JOIN locations l ON s.destination_location_id = l.location_id "
    "GROUP BY l.location_code"
)
COLD_STORAGE_SQL = (
    "SELECT location_code FROM locations WHERE is_cold_storage = true"
)
CAPACITY_ABOVE_90_SQL = (
    "SELECT location_code FROM locations "
    "WHERE 100.0 * current_load_kg / capacity_kg > 90"
)
EXPIRED_ITEMS_SQL = (
    "SELECT sku FROM inventory "
    "WHERE expiry_date IS NOT NULL AND CAST(expiry_date AS DATE) < CURRENT_DATE"
)
CCTV_WH_A_SQL = (
    "SELECT location_code, cctv_camera_id FROM locations WHERE location_code = 'WH-A'"
)


@dataclass(frozen=True)
class PackMetric:
    metric_id: str
    question: str
    sql: str
    tables: tuple[str, ...]


_BASE_PACK_METRICS: tuple[PackMetric, ...] = (
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
    PackMetric(
        metric_id="cq_capacity_utilisation",
        question=CAPACITY_UTILISATION_Q,
        sql=CAPACITY_UTILISATION_SQL,
        tables=("locations",),
    ),
    PackMetric(
        metric_id="cq_low_stock_wh_a",
        question=LOW_STOCK_WH_A_Q,
        sql=LOW_STOCK_WH_A_SQL,
        tables=("inventory", "locations"),
    ),
    PackMetric(
        metric_id="cq_cost_by_destination",
        question=SHIPMENT_COST_Q,
        sql=SHIPMENT_COST_SQL,
        tables=("shipments", "locations"),
    ),
    PackMetric(
        metric_id="cq_cold_storage",
        question=COLD_STORAGE_Q,
        sql=COLD_STORAGE_SQL,
        tables=("locations",),
    ),
    PackMetric(
        metric_id="cq_capacity_above_90",
        question=CAPACITY_ABOVE_90_Q,
        sql=CAPACITY_ABOVE_90_SQL,
        tables=("locations",),
    ),
    PackMetric(
        metric_id="cq_expired_items",
        question=EXPIRED_ITEMS_Q,
        sql=EXPIRED_ITEMS_SQL,
        tables=("inventory",),
    ),
    PackMetric(
        metric_id="cq_cctv_wh_a",
        question=CCTV_WH_A_Q,
        sql=CCTV_WH_A_SQL,
        tables=("locations",),
    ),
)


def _score_fixture_dir() -> Path:
    """Repo fixture that already copies Cortex certified_queries. Not a second pack.

    Never read at import: the API image does not ship ``tests/``. The pack
    holds oracle answers, so it must not become package data either.
    """
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "curated_ceo"


def curated_pack_present() -> bool:
    """True when both curated_ceo fixture files are on disk.

    A missing file is an empty pack, same as ``curated_l0_question_norms``.
    It is not an error and it is not read at import.
    """
    root = _score_fixture_dir()
    return (root / "questions.yaml").is_file() and (root / "oracles.yaml").is_file()


def _collapse_sql(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    # The score oracle binds $as_of. The certified query runs CURRENT_DATE.
    return " ".join(raw.split()).replace("$as_of", "CURRENT_DATE")


def _tables_in_sql(sql: str) -> tuple[str, ...]:
    from dms_executor.verified_queries import _named_warehouse_tables

    return tuple(sorted(_named_warehouse_tables(sql)))


# Score-pack exact phrases that are not already one of the ten base metrics.
# Climb rise and synonym ids stay off this list: those questions are scored
# as ontology_plan, and climb gates assert they are not PACK_METRICS.
# cq_sales_top5_value stays off: the product lane contract ask owns that
# phrase (one session bind, badge L0_CERTIFIED). cq_chemicals_list stays
# off for the same fall-through. Not every expect:l0 row.
SCORE_PACK_EXACT_IDS: frozenset[str] = frozenset(
    {
        "cq_sku_count",
        "cq_sales_top3_volume",
        "cq_sku_count_by_category",
        "cq_supplier_ranking",
        "trap_categoty",
    }
)


def _norm(question: str) -> str:
    return " ".join(normalize_ask_question(question).casefold().split())


def load_score_pack_metrics(
    base: tuple[PackMetric, ...] = _BASE_PACK_METRICS,
    ids: frozenset[str] = SCORE_PACK_EXACT_IDS,
) -> tuple[PackMetric, ...]:
    """Allowlisted score-pack ids whose question text is not already a base metric.

    Reads the curated_ceo fixture (question + oracle SQL). An id not in
    ``ids`` is not a metric. Same question text keeps the first metric.
    """
    root = _score_fixture_dir()
    if not curated_pack_present():
        return ()
    import yaml

    questions = yaml.safe_load((root / "questions.yaml").read_text(encoding="utf-8")) or {}
    oracles = yaml.safe_load((root / "oracles.yaml").read_text(encoding="utf-8")) or {}
    oracle_rows = oracles.get("oracles") or {}
    taken = {_norm(m.question) for m in base}
    extra: list[PackMetric] = []
    for case in questions.get("questions") or []:
        qid = str(case.get("id") or "")
        if qid not in ids:
            continue
        question = str(case.get("question") or "").strip()
        qn = _norm(question)
        if not qn or qn in taken:
            continue
        sql = _collapse_sql((oracle_rows.get(qid) or {}).get("sql"))
        if not sql:
            continue
        tables = _tables_in_sql(sql)
        if not tables:
            continue
        extra.append(
            PackMetric(
                metric_id=qid,
                question=question,
                sql=sql,
                tables=tables,
            )
        )
        taken.add(qn)
    return tuple(extra)


# Climb 10-13 snapshot this tuple. Do not append here.
PACK_METRICS: tuple[PackMetric, ...] = _BASE_PACK_METRICS


@lru_cache(maxsize=1)
def score_pack_exact_metrics() -> tuple[PackMetric, ...]:
    """Loaded on the first pack lookup. A missing pack is an empty tuple."""
    return load_score_pack_metrics()


def __getattr__(name: str) -> Any:
    if name == "SCORE_PACK_EXACT_METRICS":
        return score_pack_exact_metrics()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _exact_pack_metrics() -> tuple[PackMetric, ...]:
    return PACK_METRICS + score_pack_exact_metrics()


# Exact planted refuse from curated_ceo. Not regex. Cortex certify boundary:
# cq_capacity_utilisation has no synonym; delayed_incoming_per_wh is TARGET.
# Live L1 leak: vocabulary "how full" -> capacity utilisation, and
# route_to_metric delayed+per+warehouse -> count_by_destination.
_UNCERTIFIED_PARAPHRASE = frozenset(
    {
        _norm(HOW_FULL_TRAP_Q),
        _norm(DELAYED_COUNT_TRAP_Q),
        _norm(STOCK_BY_BIN_TRAP_Q),
    }
)


def is_uncertified_paraphrase(question: str | None) -> bool:
    qn = _norm(question or "")
    return bool(qn) and qn in _UNCERTIFIED_PARAPHRASE


def uncertified_refuse_text(question: str | None) -> str:
    qn = _norm(question or "")
    if qn == _norm(HOW_FULL_TRAP_Q):
        return (
            "I cannot certify that phrasing. It is not a certified synonym of "
            "warehouse capacity utilisation. Ask "
            f"'{CAPACITY_UTILISATION_Q}'."
        )
    if qn == _norm(STOCK_BY_BIN_TRAP_Q):
        return (
            "I cannot certify stock by storage bin. No Cortex certified query "
            "pins that grain. A governed number here would be a guess."
        )
    return (
        "I cannot certify delayed-incoming counts per warehouse. That golden is "
        "TARGET, not a Cortex certified query. A governed number here would be a guess."
    )


def maybe_uncertified_refuse_ask(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """ABSTAIN envelope when the ask is a planted uncertified paraphrase.

    Exact phrase only, same ``_norm`` as pack match. Does not grow an intent
    cascade. Cortex L1 must not green these to raise coverage.
    """
    if not is_uncertified_paraphrase(question):
        return None
    env = build_answer_envelope(
        answer_id="ans_uncertified_paraphrase",
        text=uncertified_refuse_text(question),
        badge="ABSTAIN",
        abstained=True,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=[
            "uncertified paraphrase: not on certified_queries.yaml",
            "fail-closed; Cortex L1 synonym/regex is not a certify boundary",
        ],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
        question=question,
        suggestions=(
            [CAPACITY_UTILISATION_Q]
            if _norm(question) == _norm(HOW_FULL_TRAP_Q)
            else []
        ),
    )
    assert_envelope_valid(env)
    return env


def _as_of() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grant_covers(table: str, allowed: set[str]) -> bool:
    """True when the Space grant names the table or its Cortex warehouse_ alias."""
    if table in allowed:
        return True
    return f"warehouse_{table}" in allowed


def match_pack_phrase(
    question: str,
    *,
    tables: list[str] | None = None,
) -> PackMetric | None:
    """Exact pack phrase, before grants / Cortex SQL / ledger.

    Grounded-file asks are not pack matches. Grant coverage is a later step.
    """
    if tables:
        return None
    qn = _norm(question)
    if not qn:
        return None
    return next((m for m in _exact_pack_metrics() if _norm(m.question) == qn), None)


def lookup_pack_metric(
    question: str,
    *,
    grantable: set[str] | None = None,
    tables: list[str] | None = None,
) -> PackMetric | None:
    """Return the pack metric when the phrase matches and grants cover it.

    The phrase set is the ten base metrics plus the score-pack allowlist.
    A Space that does not grant every table the SQL names is not a hit here
    (Warehouse Ops vs suppliers). ``maybe_pack_ask`` names that as ``grants
    fail`` instead of treating it as no match. Cortex ``warehouse_<table>``
    aliases count as the same grant. Column presence is Cortex's job on
    submit -- do not probe the thin DMS local file.
    """
    hit = match_pack_phrase(question, tables=tables)
    if hit is None:
        return None
    allowed = grantable if grantable is not None else set()
    if any(not _grant_covers(t, allowed) for t in hit.tables):
        return None
    try:
        reject_hostile_chat_sql(hit.sql)
    except SecurityEvent:
        return None
    return hit


def _curated_pack_path() -> Path:
    return _score_fixture_dir() / "questions.yaml"


@lru_cache(maxsize=1)
def curated_l0_question_norms() -> frozenset[str]:
    """Normalised curated_ceo questions whose expect is l0.

    The file is the score pack. A missing file means this process cannot
    tell a curated l0 ask from any other question.
    """
    path = _curated_pack_path()
    if not path.is_file():
        return frozenset()
    try:
        import yaml
    except ImportError:
        return frozenset()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    norms: set[str] = set()
    for row in data.get("questions") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("expect") or "").lower() != "l0":
            continue
        n = _norm(str(row.get("question") or ""))
        if n:
            norms.add(n)
    return frozenset(norms)


def is_curated_l0_without_pack_metric(question: str) -> bool:
    """True when a curated l0 ask is absent from the exact pack (step 1 miss)."""
    if match_pack_phrase(question) is not None:
        return False
    n = _norm(question)
    return bool(n) and n in curated_l0_question_norms()


def curated_pack_metric_miss(
    question: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Named ABSTAIN for step 1: exact-match / pack-metric miss.

    The exact pack (base metrics plus the score-pack allowlist) has no phrase
    for this curated l0 question. ``cq_sku_count`` is on that allowlist, so
    this miss is a still-unregistered phrase. The rendered text must not be
    a generic generative abstain.
    """
    text = (
        "exact-match miss: pack-metric miss. "
        "The exact pack has no metric for this curated question, "
        "so the pack lookup did not match. "
        "This is not a generic generative abstain."
    )
    env = build_answer_envelope(
        answer_id="ans_curated_step",
        text=text,
        badge="ABSTAIN",
        abstained=True,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=["exact-match miss", "pack-metric miss"],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
        question=question,
    )
    assert_envelope_valid(env)
    return env


def _curated_step_refusal(
    question: str,
    failed_step: str,
    *,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Named ABSTAIN. The phrase matched; this later step did not.

    The rendered text names ``exact match ok`` and the failed step
    (``grants fail`` / ``Cortex SQL fail`` / ``ledger fail``). Later lanes
    must not run: a generative abstain would hide the miss.
    """
    text = (
        "The curated pack matched this question (exact match ok) "
        f"but a later step failed: {failed_step}. "
        "I am not answering from a generative fallback."
    )
    env = build_answer_envelope(
        answer_id="ans_curated_step",
        text=text,
        badge="ABSTAIN",
        abstained=True,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=["exact match ok", failed_step, "no generative fallback"],
        as_of=_as_of(),
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
        question=question,
    )
    assert_envelope_valid(env)
    return env


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

    Missing submit/ledger does not fall back to local DuckDB (F83). A phrase
    match whose grants, Cortex SQL, or ledger step fails is a named ABSTAIN.
    None means the phrase did not match, so a later lane may run.
    """
    phrase = match_pack_phrase(question, tables=tables)
    if phrase is None:
        return None
    try:
        reject_hostile_chat_sql(phrase.sql)
    except SecurityEvent:
        return None
    hit = lookup_pack_metric(
        question,
        grantable=grantable,
        tables=tables,
    )
    if hit is None:
        return _curated_step_refusal(
            question, "grants fail", space_id=space_id, session_id=session_id
        )
    if submit is None or ledger_append is None:
        return _curated_step_refusal(
            question, "Cortex SQL fail", space_id=space_id, session_id=session_id
        )
    try:
        result = submit(hit.sql)
    except OpenVaultTokenError:
        raise
    except Exception:  # noqa: BLE001
        return _curated_step_refusal(
            question, "Cortex SQL fail", space_id=space_id, session_id=session_id
        )
    ok = getattr(result, "ok", None)
    if ok is False or getattr(result, "output", None) is None:
        return _curated_step_refusal(
            question, "Cortex SQL fail", space_id=space_id, session_id=session_id
        )
    run_id = str(getattr(result, "run_id", None) or "")
    try:
        led = ledger_append({"sql": hit.sql, "run_id": run_id})
    except Exception:  # noqa: BLE001
        return _curated_step_refusal(
            question, "ledger fail", space_id=space_id, session_id=session_id
        )
    entry_id = getattr(led, "entry_id", None) if led is not None else None
    if not (isinstance(entry_id, str) and entry_id.strip()):
        return _curated_step_refusal(
            question, "ledger fail", space_id=space_id, session_id=session_id
        )
    led_hash = getattr(led, "hash", None)
    if not (isinstance(led_hash, str) and led_hash.strip()) or led_hash == entry_id:
        return _curated_step_refusal(
            question, "ledger fail", space_id=space_id, session_id=session_id
        )
    return envelope_from_pack_submit(
        metric=hit,
        result=result,
        question=question,
        space_id=space_id,
        session_id=session_id,
        audit_id=entry_id.strip(),
    )
