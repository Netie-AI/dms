"""GRAIN-GUARD-01: L2_VALIDATED only when grain and columns match the question.

dms#231 Phase 1/1b: 15 oracle-wrong answers on the 52-question curated pack
all carried a green L2 badge. The SQL parsed, validated and executed, so the
generated path certified it: ``GROUP BY capacity_kg`` for "capacity
utilisation", per-SKU rows where a total was asked, and list asks ("which
locations ...", "show the CCTV camera ...") answered with an aggregate the
question never named.

Every case here goes through ``POST /v1/chat/ask`` (ask_path=generative) and
asserts on the customer envelope: badge, abstained, rendered text and rows.
SQL is only the fixture input. The fake Cortex returns the generated SQL from
Insights and executes submits on the demo warehouse, so the rows are real.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.sql_grain import grain_mismatch_reason, scalar_rows_reason
from fastapi.testclient import TestClient

CAPACITY_Q = "Show warehouse capacity utilisation"
LOW_STOCK_Q = "Which SKUs are below reorder level in warehouse A?"
CCTV_Q = "Show the CCTV camera for warehouse A"
TOTAL_WH_A_Q = "What is the total stock quantity in warehouse A?"
COLD_Q = "Which locations are cold storage?"

#: Phase 1 SQL the generated path certified (verbatim from the offline
#: prove-path run on main@0577275).
CAPACITY_BY_CAPACITY_KG_SQL = (
    'SELECT f."capacity_kg" AS "location_capacity_kg", '
    "ROUND(100.0 * SUM(f.current_load_kg) / NULLIF(SUM(f.capacity_kg), 0), 1) "
    'AS "utilisation_pct" FROM locations f GROUP BY f."capacity_kg" '
    'ORDER BY "utilisation_pct" DESC LIMIT 50'
)
CCTV_EXTRA_MEASURE_SQL = (
    'SELECT f."cctv_camera_id" AS "location_cctv_camera_id", '
    "ROUND(100.0 * SUM(f.current_load_kg) / NULLIF(SUM(f.capacity_kg), 0), 1) "
    "AS \"utilisation_pct\" FROM locations f WHERE f.\"location_code\" = 'WH-A' "
    'GROUP BY f."cctv_camera_id" ORDER BY "utilisation_pct" DESC LIMIT 50'
)
CCTV_EXTRA_COLUMN_SQL = (
    "SELECT location_code, cctv_camera_id, capacity_kg FROM locations "
    "WHERE location_code = 'WH-A'"
)
LOW_STOCK_COUNT_FILTER_SQL = (
    'SELECT d0."sku" AS "product_sku", COUNT(*) FILTER (WHERE f.quantity_kg < '
    'f.reorder_level_kg AND COALESCE(f.reorder_level_kg, 0) > 0) AS "below_reorder_lots" '
    "FROM inventory f LEFT JOIN (SELECT sku, ANY_VALUE(category) AS category "
    'FROM inventory GROUP BY sku) d0 ON f."sku" = d0."sku" '
    'WHERE (f."location_id") IN (SELECT d0."location_id" FROM locations d0 '
    "WHERE d0.\"location_code\" = 'WH-A') GROUP BY d0.\"sku\" "
    'ORDER BY "below_reorder_lots" DESC LIMIT 50'
)
COLD_EXTRA_MEASURE_SQL = (
    'SELECT f."location_code" AS "location_location_code", '
    "ROUND(100.0 * SUM(f.current_load_kg) / NULLIF(SUM(f.capacity_kg), 0), 1) "
    'AS "utilisation_pct" FROM locations f WHERE f."is_cold_storage" = TRUE '
    'GROUP BY f."location_code" ORDER BY "utilisation_pct" DESC LIMIT 50'
)
COLD_ORACLE_SQL = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
TOTAL_PER_SKU_SQL = (
    "SELECT i.sku, SUM(i.quantity_kg) AS quantity_kg FROM inventory i "
    "JOIN locations l ON i.location_id = l.location_id "
    "WHERE l.location_code = 'WH-A' GROUP BY i.sku"
)

#: Curated-pack oracles (tests/fixtures/curated_ceo/oracles.yaml).
CAPACITY_ORACLE_SQL = (
    "SELECT location_code, ROUND(100.0 * current_load_kg / capacity_kg, 1) AS pct_used "
    "FROM locations"
)
CCTV_ORACLE_SQL = (
    "SELECT location_code, cctv_camera_id FROM locations WHERE location_code = 'WH-A'"
)
LOW_STOCK_ORACLE_SQL = (
    "SELECT i.sku, i.quantity_kg FROM inventory i "
    "JOIN locations l ON i.location_id = l.location_id "
    "WHERE i.quantity_kg < i.reorder_level_kg AND i.reorder_level_kg > 0 "
    "AND l.location_code = 'WH-A' ORDER BY i.quantity_kg ASC"
)
TOTAL_WH_A_SQL = (
    "SELECT SUM(i.quantity_kg) AS total_quantity_kg FROM inventory i "
    "JOIN locations l ON i.location_id = l.location_id WHERE l.location_code = 'WH-A'"
)


@dataclass
class _InsightsCortex:
    """Insights returns ``query_sql``; submit executes it on the demo lake."""

    warehouse: Path
    query_sql: str = ""
    insights: list[str] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **_kw: Any) -> dict[str, Any]:
        self.insights.append(question)
        return {"query_sql": self.query_sql, "plan_source": "ontology_plan"}

    def compute_query(self, question: str, **_kw: Any) -> None:
        raise AssertionError("ask lane called compute_query /dms/query")

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind != "sql":
            return QueryResult(ok=True, status="bound", run_id="run_grain_bind")
        sql = str((getattr(req, "body", None) or {}).get("sql") or "")
        self.executed.append(sql)
        con = duckdb.connect(str(self.warehouse), read_only=True)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_grain_sql", output={"rows": rows})

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_grain_01", hash="hash_grain_01_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        raise AssertionError("generative lane reached Cortex contract ask")


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-09-25T00:00:00+00:00",
            expires_at="2026-09-25T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    path = tmp_path / "grain_guard.duckdb"
    ensure_demo_warehouse(path)
    return path


FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"


def _post(
    minter: ManifestMinter, lake: Path, question: str, sql: str, *, space: str = FINANCE
) -> tuple[dict[str, Any], _InsightsCortex]:
    cortex = _InsightsCortex(warehouse=lake, query_sql=sql)
    app = create_app()
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    r = TestClient(app).post(
        "/v1/chat/ask",
        json={
            "question": question,
            "session_id": "ses_grain_guard_01",
            "space_id": space,
            "ask_path": "generative",
        },
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    assert cortex.insights == [question], "the generated path was not reached"
    return env, cortex


def _oracle_rows(lake: Path, sql: str) -> list[dict[str, Any]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [str(c[0]) for c in (cur.description or [])]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _multiset(rows: list[dict[str, Any]]) -> Counter[tuple[str, ...]]:
    return Counter(tuple(sorted(str(v) for v in row.values())) for row in rows)


def _reasons(env: dict[str, Any]) -> str:
    return " ".join(str(a) for a in env.get("assumptions") or [])


def _assert_grain_abstain(env: dict[str, Any], prefix: str, *figures: str) -> None:
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"))
    assert env["abstained"] is True
    assert env["values"] == []
    assert env["rows"] == []
    text = str(env.get("text") or "")
    assert f"gap: {prefix}" in text, text
    assert prefix in _reasons(env), env.get("assumptions")
    for fig in figures:
        assert fig not in text, (fig, text)


def _assert_l2_matches_oracle(env: dict[str, Any], lake: Path, oracle_sql: str) -> None:
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), _reasons(env))
    assert env["abstained"] is False
    oracle = _oracle_rows(lake, oracle_sql)
    assert oracle, "oracle returned no rows"
    assert _multiset(env["rows"]) == _multiset(oracle), (env["rows"], oracle)
    text = str(env.get("text") or "")
    assert f"Found {len(oracle)} row(s)." in text, text
    for row in oracle:
        for v in row.values():
            assert str(v) in text, (v, text)
    assert env["values"], "L2 answer with no values"
    assert env["audit_id"] == "led_grain_01"
    assert env["sql_used"] == oracle_sql
    # Not asserted: contributing_sources / drillthrough_token. The generated
    # path has never minted either on main (only the Cortex contract ask
    # carries them); inventing a token here would be a lying affordance.


# --- 1. unrequested grain -----------------------------------------------------


def test_capacity_grouped_by_capacity_kg_abstains_unrequested_grain(
    minter: ManifestMinter, lake: Path
) -> None:
    env, cortex = _post(minter, lake, CAPACITY_Q, CAPACITY_BY_CAPACITY_KG_SQL)
    _assert_grain_abstain(env, "unrequested_grain:capacity_kg", "97.8", "90000", "72.0")
    assert "capacity_kg" in env["text"]
    assert cortex.executed == [], "a wrong-grain query must not execute"


# --- 2. scalar expected -------------------------------------------------------


def test_total_asked_but_per_sku_rows_abstains_scalar_expected(
    minter: ManifestMinter, lake: Path
) -> None:
    env, cortex = _post(minter, lake, TOTAL_WH_A_Q, TOTAL_PER_SKU_SQL)
    # One row per SKU would come back (1200 and 80); neither may reach the
    # customer. A grouped query on a total ask is refused before it runs.
    assert len(_oracle_rows(lake, TOTAL_PER_SKU_SQL)) == 2
    _assert_grain_abstain(env, "grain_mismatch:scalar_expected", "1200", "80", "1280")
    assert cortex.executed == []


def test_low_stock_count_filter_per_sku_abstains(minter: ManifestMinter, lake: Path) -> None:
    """The curated low_stock_wh_a ask: per-SKU tally incl. a SKU not below reorder."""
    env, _ = _post(minter, lake, LOW_STOCK_Q, LOW_STOCK_COUNT_FILTER_SQL)
    _assert_grain_abstain(env, "unrequested_measure:below_reorder_lots", "RS622XK")


# --- 3. unrequested columns ---------------------------------------------------


def test_cctv_with_extra_utilisation_measure_abstains(
    minter: ManifestMinter, lake: Path
) -> None:
    env, _ = _post(minter, lake, CCTV_Q, CCTV_EXTRA_MEASURE_SQL)
    _assert_grain_abstain(env, "unrequested_measure:utilisation_pct", "72.0", "CAM-A-01")


def test_cctv_with_extra_plain_column_abstains(minter: ManifestMinter, lake: Path) -> None:
    env, _ = _post(minter, lake, CCTV_Q, CCTV_EXTRA_COLUMN_SQL)
    _assert_grain_abstain(env, "unrequested_column:capacity_kg", "100000", "CAM-A-01")


def test_which_list_with_extra_measure_returns_only_requested_columns(
    minter: ManifestMinter, lake: Path
) -> None:
    """Acceptance 3, second branch: never L2 over the extra-column row set."""
    env, _ = _post(minter, lake, COLD_Q, COLD_EXTRA_MEASURE_SQL)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"))
    assert env["abstained"] is False
    assert env["rows"] == [{"location_location_code": "WH-C"}]
    assert _multiset(env["rows"]) == _multiset(_oracle_rows(lake, COLD_ORACLE_SQL))
    text = str(env.get("text") or "")
    assert "WH-C" in text
    assert "96.7" not in text and "utilisation_pct" not in text, text
    assert all("utilisation_pct" not in str(v) for v in env["values"]), env["values"]
    assert "dropped unrequested column(s) utilisation_pct" in _reasons(env)
    # The audited SQL is the one whose rows were shown (no hidden figure).
    assert "utilisation_pct" not in str(env["sql_used"]).split(" FROM (", 1)[0]
    assert _multiset(_oracle_rows(lake, env["sql_used"])) == _multiset(env["rows"])


# --- 4. matching grain keeps L2 ------------------------------------------------


@pytest.mark.parametrize(
    ("question", "sql", "space"),
    [
        (CAPACITY_Q, CAPACITY_ORACLE_SQL, FINANCE),
        (CCTV_Q, CCTV_ORACLE_SQL, FINANCE),
        (LOW_STOCK_Q, LOW_STOCK_ORACLE_SQL, OPS),
        (TOTAL_WH_A_Q, TOTAL_WH_A_SQL, OPS),
    ],
)
def test_matching_grain_keeps_l2_with_oracle_rows(
    minter: ManifestMinter, lake: Path, question: str, sql: str, space: str
) -> None:
    env, _ = _post(minter, lake, question, sql, space=space)
    _assert_l2_matches_oracle(env, lake, sql)


# --- unit: the gate itself -------------------------------------------------------


def test_breakdown_and_ranking_asks_are_not_scalar() -> None:
    rows = [{"a": 1}, {"a": 2}]
    for q in (
        "What is total stock value by category?",
        "What is our total spend by supplier country?",
        "how many SKUs per category",
        "Top 3 SKUs by quantity sold",
        "total revenue 2025 vs 2024",
    ):
        assert scalar_rows_reason(q, rows) is None, q
    assert scalar_rows_reason("How many SKUs do we have in inventory?", rows) == (
        "grain_mismatch:scalar_expected"
    )
    assert scalar_rows_reason("How many SKUs do we have in inventory?", rows[:1]) is None


def test_requested_grain_and_named_measure_pass() -> None:
    by_cap = (
        "SELECT capacity_kg, SUM(current_load_kg) / SUM(capacity_kg) AS util "
        "FROM locations GROUP BY capacity_kg"
    )
    assert grain_mismatch_reason("utilisation by capacity", by_cap) is None
    assert grain_mismatch_reason("Show warehouse capacity utilisation", by_cap) == (
        "unrequested_grain:capacity_kg"
    )
    util = (
        "SELECT location_code, ROUND(100.0 * SUM(current_load_kg) / SUM(capacity_kg), 1) "
        "AS utilisation_pct FROM locations WHERE location_code = 'WH-A' GROUP BY 1"
    )
    assert grain_mismatch_reason("Show the utilisation for warehouse A", util) is None
    assert grain_mismatch_reason("Which warehouse has the highest utilisation?", util) is None
    assert grain_mismatch_reason("Show stock ; not sql (", "not sql (") is None


# --- verifier probes (round 2): bypasses closed, legit asks kept ---------------

WH_B_STOCK_SQL = (
    "SELECT i.sku, SUM(i.quantity_kg) AS {alias} FROM inventory i "
    "WHERE i.location_id = 'WH-B' GROUP BY i.sku"
)


@pytest.mark.parametrize(
    ("question", "sql", "prefix", "figures"),
    [
        # trim would launder an unfiltered row set: every warehouse "almost full"
        (
            "Which warehouses are almost full?",
            "SELECT location_code, ROUND(100.0*SUM(current_load_kg)/SUM(capacity_kg),1) "
            "AS utilisation_pct FROM locations GROUP BY location_code",
            "unrequested_measure:utilisation_pct",
            ("WH-A", "WH-B", "WH-D"),
        ),
        # every SKU listed as below reorder
        (
            "Which SKUs are below reorder level?",
            "SELECT sku, SUM(quantity_kg) AS on_hand_kg FROM inventory GROUP BY sku",
            "unrequested_measure:on_hand_kg",
            ("RS622XK",),
        ),
        # per-SKU grain on a total ask, hidden by LIMIT 1 (1200, true total 1280)
        (
            TOTAL_WH_A_Q,
            TOTAL_PER_SKU_SQL + " ORDER BY 2 DESC LIMIT 1",
            "grain_mismatch:scalar_expected",
            ("1200", "1280", "RS622XK"),
        ),
        # unrequested grain that is not an aggregate input
        (
            CAPACITY_Q,
            "SELECT is_cold_storage, ROUND(100.0*SUM(current_load_kg)/SUM(capacity_kg),1) "
            "AS utilisation_pct FROM locations GROUP BY is_cold_storage",
            "unrequested_grain:is_cold_storage",
            ("60.5", "96.7"),
        ),
        # COUNT FILTER tally wrapped in a derived table
        (
            LOW_STOCK_Q,
            "SELECT * FROM (SELECT sku, COUNT(*) FILTER (WHERE quantity_kg < reorder_level_kg) "
            "AS below FROM inventory WHERE location_id = 'WH-A' GROUP BY sku) t",
            "unrequested_measure:below",
            ("RS622XK",),
        ),
    ],
)
def test_round2_bypasses_abstain_named(
    minter: ManifestMinter,
    lake: Path,
    question: str,
    sql: str,
    prefix: str,
    figures: tuple[str, ...],
) -> None:
    env, _ = _post(minter, lake, question, sql, space=OPS)
    _assert_grain_abstain(env, prefix, *figures)


@pytest.mark.parametrize(
    ("question", "sql"),
    [
        ("Show stock levels in warehouse B", WH_B_STOCK_SQL.format(alias="on_hand_kg")),
        ("Show inventory in warehouse B", WH_B_STOCK_SQL.format(alias="quantity_kg")),
        ("List the stock on hand in warehouse B", WH_B_STOCK_SQL.format(alias="quantity_kg")),
        (
            "Which warehouses hold more than one SKU?",
            "SELECT location_id, COUNT(DISTINCT sku) AS sku_count FROM inventory "
            "GROUP BY location_id HAVING COUNT(DISTINCT sku) > 1",
        ),
    ],
)
def test_round2_legit_asks_keep_l2_with_their_figure(
    minter: ManifestMinter, lake: Path, question: str, sql: str
) -> None:
    """The figure the question asks for is kept, and the rows are the oracle's."""
    env, _ = _post(minter, lake, question, sql, space=OPS)
    _assert_l2_matches_oracle(env, lake, sql)
    oracle = _oracle_rows(lake, sql)
    assert len(oracle[0]) == 2 and len(env["rows"][0]) == 2, env["rows"]
