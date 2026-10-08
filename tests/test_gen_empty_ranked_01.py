"""GEN-EMPTY-RANKED-01: fallback:generate_empty must not drop a qualifier.

Offline stub only. Cortex 279cbd85 ignores query_plan, ranked_metric, and
generate_retry, so this httpx fake ignores the POST body. It is not a guided
leg-2. Every assertion is on the Executor.live_ask envelope (text, rows, and
assert_envelope_valid).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cortex_client.compute import ranked_measure_tokens
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

CATALOG = [
    "cq_sales_top5_value",
    "cq_sales_top3_volume",
    "cq_top3_category_sales",
    "cq_sku_count",
    "cq_sku_count_by_category",
    "stock_value_by_category",
    "inventory_worth_by_category",
    "cq_chemicals_list",
    "cq_supplier_ranking",
    "supplier_ranking",
    "cq_cost_by_destination",
    "freight_spend_per_destination",
    "cq_cold_storage",
    "cq_expired_items",
    "total_spend",
    "spend_by_country",
    "cq_low_stock_wh_a",
    "cq_capacity_utilisation",
    "cq_capacity_above_90",
    "cq_cctv_wh_a",
    "cq_audit_overdue",
]

# Invalid generate SQL, then fallback:validate (the gate must not touch these).
BINDER = {
    "Top 5 SKUs by revenue",
    "top 3 categories by sales value",
    "top 3 category sales",
}

TOP5 = [
    ("SKU-BETA", 8312.5),
    ("SKU-ALPHA", 5670.0),
    ("RS622XK", 3915.0),
    ("SKU-EPSILON", 2925.0),
    ("SKU-GAMMA", 2640.0),
]
TOP3_CAT = [
    ("PACKAGING", 13982.5),
    ("RAW", 4955.0),
    ("PARTS", 4077.0),
]
_SAVED: list[dict[str, Any]] = []


def _ranking(question: str) -> list[str]:
    qtoks = ranked_measure_tokens(question)
    ranked = sorted(
        CATALOG,
        key=lambda mid: (
            -len(qtoks & ranked_measure_tokens(mid)),
            CATALOG.index(mid),
        ),
    )
    # Zero-overlap asks resolve the first id. Ungrouped outbound is that id,
    # which is the 25654.5 total on 72df50d8.
    if qtoks and not any(qtoks & ranked_measure_tokens(mid) for mid in ranked):
        ranked = ["outbound_value_myr", *ranked]
    return ranked


class _Http:
    """Ignores the POST body. Cortex does not honor DMS retry fields."""

    def __init__(self, question: str) -> None:
        self.question = question

    def __call__(self, *a: Any, **k: Any) -> _Http:
        return self

    def __enter__(self) -> _Http:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def post(self, url: str, json: Any = None, headers: Any = None) -> Any:
        if self.question in BINDER:
            gen: dict[str, Any] = {
                "ok": True,
                "sql": "SELECT missing_col FROM inventory",
            }
        else:
            gen = {"ok": False, "sql": None, "climb": {"final": "EMPTY"}}
        body = {
            "phase": "generate",
            "status": "ABSTAIN",
            "generative": gen,
            "values": [],
            "served_provider": "stub",
            "served_model": "stub-empty",
        }
        return type("R", (), {"status_code": 200, "json": lambda self: body})()

    def get(self, url: str, params: Any = None, headers: Any = None) -> Any:
        body = {
            "ok": True,
            "phase": "ontology",
            "ontology": {"metrics": [{"id": mid} for mid in _ranking(self.question)]},
        }
        return type("R", (), {"status_code": 200, "json": lambda self: body})()


class _Cortex:
    def __init__(self, db: Path) -> None:
        self.db = db

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        from unittest.mock import patch

        from cortex_client.compute import compute_insights

        http = _Http(question)
        with patch("cortex_client.compute.httpx.Client", http):
            return compute_insights(
                "http://127.0.0.1:8010",
                question=question,
                session_id=kwargs.get("session_id"),
                space_id=kwargs.get("space_id"),
                ontology=kwargs.get("ontology"),
                api_key="fake-key",
            )

    def submit(self, req: Any) -> QueryResult:
        plan = getattr(req, "plan", None) or {}
        if isinstance(plan, dict) and plan.get("kind") == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_bind")
        sql = ""
        body = getattr(req, "body", None) or {}
        if isinstance(body, dict):
            sql = str(body.get("sql") or "")
        con = connect_file(self.db)
        try:
            rel = con.execute(sql)
            cols = [d[0] for d in rel.description] if rel.description else []
            rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_empty", output={"rows": rows})

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_empty", hash="hash_empty_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        return AskResponse(
            answer="CONTRACT_ASK_FALLBACK",
            badge="certified",
            sql_used="SELECT 1 AS marker",
            rows=[{"marker": 1}],
            audit_id="aud_empty",
            route="sql",
        )


def _mint(acl: SessionAcl) -> Manifest:
    return Manifest(
        session_id=acl.session_id,
        org_id=acl.org_id,
        space_id=acl.space_id,
        pool_id=acl.pool_id,
        issuer_key_id="test-kid",
        allowed_paths=list(acl.allowed_paths),
        row_predicates=dict(acl.row_predicates),
        issued_at="2026-10-08T00:00:00+00:00",
        expires_at="2026-10-08T01:00:00+00:00",
        signature="dGVzdHNpZw",
    )


def _executor(tmp_path: Path) -> Executor:
    db = tmp_path / "empty_ranked.duckdb"
    ensure_demo_warehouse(db)
    minter = ManifestMinter()
    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return Executor(cortex=_Cortex(db), minter=minter, warehouse_path=db)  # type: ignore[arg-type]


def _ask(exe: Executor, question: str, session_id: str = "ses_empty") -> dict[str, Any]:
    env = exe.live_ask(question, session_id=session_id, ask_path="generative")
    assert_envelope_valid(env)
    _SAVED.append(
        {
            "q": question,
            "session": session_id,
            "badge": env.get("badge"),
            "abstained": env.get("abstained"),
            "attr": env.get("served_attribution"),
            "origin": env.get("plan_origin"),
            "text": env.get("text"),
            "assumptions": env.get("assumptions"),
            "sql": env.get("sql_used"),
            "rows": env.get("rows"),
        }
    )
    return env


def _blob(env: dict[str, Any]) -> str:
    return " ".join(
        [
            str(env.get("text") or ""),
            " ".join(str(a) for a in (env.get("assumptions") or [])),
            str(env.get("sql_used") or ""),
        ]
    )


def _assert_named_abstain(env: dict[str, Any], word: str) -> None:
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert env.get("served_attribution") == "none"
    assert f"ungrounded_qualifier:{word}" in _blob(env)
    assert "25654" not in _blob(env)
    assert "fallback:generate_empty" in _blob(env)


def _assert_served(env: dict[str, Any]) -> None:
    assert env["abstained"] is False
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_origin") == "ontology_ranking"
    # Empty generate discarded the model output. Never reported.
    assert env.get("served_attribution") == "none"
    assert "fallback:generate_empty" in _blob(env)


def _skus(env: dict[str, Any]) -> list[tuple[Any, Any]]:
    out = []
    for row in env["rows"]:
        sku = row.get("product_sku")
        val = next(v for k, v in row.items() if k != "product_sku")
        out.append((sku, val))
    return out


def test_wh_b_revenue_keeps_warehouse(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "top 5 skus by revenue at WH-B")
    _assert_served(env)
    sql = str(env["sql_used"])
    assert "WH-B" in sql
    assert "outbound_value_myr" in sql
    got = _skus(env)
    assert ("SKU-BETA", 8312.5) in got
    assert ("SKU-ALPHA", 5670.0) in got
    assert "RS622XK" not in {s for s, _v in got}
    assert "ungrounded_qualifier:" not in str(env["text"])


def test_penang_abstains(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "show me every supplier in Penang")
    _assert_named_abstain(env, "penang")


def test_night_shift_abstains(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "top 5 skus by pallets moved on night shift")
    _assert_named_abstain(env, "pallets")


def test_raw_stock_keeps_category(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "top 3 RAW skus by stock value")
    _assert_served(env)
    sql = str(env["sql_used"])
    assert "RAW" in sql
    assert "stock_value_myr" in sql
    got = _skus(env)
    assert ("RS622XK", 5400.0) in got
    assert ("RS622XKR", 416.0) in got
    assert "SKU-BETA" not in {s for s, _v in got}


def test_excluding_packaging(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "top 4 skus by sales excluding packaging")
    _assert_served(env)
    sql = str(env["sql_used"]).upper()
    assert "PACKAGING" in sql
    assert "<>" in sql or "!=" in sql
    assert "OUTBOUND_VALUE_MYR" in sql
    banned = {s for s, _v in _skus(env)}
    assert "SKU-ALPHA" not in banned
    assert "SKU-BETA" not in banned
    assert ("RS622XK", 3915.0) in _skus(env)


def test_except_sku_gamma_drops_that_sku(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "top 5 skus by revenue except SKU-GAMMA")
    got = _skus(env)
    assert "SKU-GAMMA" not in {s for s, _v in got}, got
    assert len(got) == 5, got
    _assert_served(env)
    sql = str(env["sql_used"]).upper()
    assert "SKU-GAMMA" in sql
    assert "<>" in sql or "NOT IN" in sql
    assert "OUTBOUND_VALUE_MYR" in sql


def test_excluding_two_skus_is_case_insensitive(tmp_path: Path) -> None:
    env = _ask(
        _executor(tmp_path),
        "top 5 skus by revenue excluding sku-gamma and sku-alpha",
    )
    banned = {s for s, _v in _skus(env)}
    assert "SKU-GAMMA" not in banned, _skus(env)
    assert "SKU-ALPHA" not in banned, _skus(env)
    assert len(env["rows"]) == 5, _skus(env)
    _assert_served(env)
    sql = str(env["sql_used"]).upper()
    assert "SKU-GAMMA" in sql
    assert "SKU-ALPHA" in sql
    assert "<>" in sql or "NOT IN" in sql
    assert "OUTBOUND_VALUE_MYR" in sql


def test_chemicals_revenue_is_not_stock_value(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "top 5 skus by revenue for chemicals")
    assert _skus(env) == [("SKU-GAMMA", 2640.0)], (env.get("sql_used"), _skus(env))
    sql = str(env["sql_used"])
    assert "stock_value_myr" not in sql
    assert "outbound_value_myr" in sql
    assert "CHEMICALS" in sql
    _assert_served(env)


def test_spelled_five_does_not_use_default_limit(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "top five skus by revenue")
    assert env["rows"] == [], (env.get("sql_used"), env["rows"])
    _assert_named_abstain(env, "five")


def test_second_page_does_not_serve_page_one(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "second page of top skus by revenue")
    assert env["rows"] == [], (env.get("sql_used"), env["rows"])
    _assert_named_abstain(env, "second")


def test_chemicals_by_sales_is_not_stock_value(tmp_path: Path) -> None:
    env = _ask(_executor(tmp_path), "top 5 chemicals SKUs by sales")
    _assert_served(env)
    sql = str(env["sql_used"])
    assert "stock_value_myr" not in sql
    assert "outbound_value_myr" in sql
    assert "CHEMICALS" in sql
    assert _skus(env) == [("SKU-GAMMA", 2640.0)]


@pytest.mark.parametrize("question,word", [
    ("show me the next 5", "next"),
    ("next 5 please", "next"),
    ("and the ones after that?", "ones"),
])
def test_empty_followup_fresh_session(tmp_path: Path, question: str, word: str) -> None:
    env = _ask(_executor(tmp_path), question, session_id=f"fresh-{word}")
    _assert_named_abstain(env, word)


def test_empty_followup_after_total_stock(tmp_path: Path) -> None:
    exe = _executor(tmp_path)
    parent = _ask(exe, "total stock value", session_id="after-stock")
    assert_envelope_valid(parent)
    for question, word in (
        ("show me the next 5", "next"),
        ("next 5 please", "next"),
        ("and the ones after that?", "ones"),
    ):
        env = _ask(exe, question, session_id="after-stock")
        _assert_named_abstain(env, word)
        assert env["rows"] != parent.get("rows") or parent.get("rows") == []


def test_replay_eight_ontology_ranking(tmp_path: Path) -> None:
    exe = _executor(tmp_path)
    binder_rows = {
        "Top 5 SKUs by revenue": TOP5,
        "top 3 categories by sales value": TOP3_CAT,
        "top 3 category sales": TOP3_CAT,
    }
    for question, expect in binder_rows.items():
        env = _ask(exe, question, session_id="replay-binder")
        assert env["abstained"] is False
        assert env["badge"] == "L2_VALIDATED"
        assert env.get("plan_origin") == "ontology_ranking"
        assert "fallback:validate:" in _blob(env)
        if "SKU" in question:
            assert _skus(env) == expect
        else:
            assert [(r["product_category"], r["outbound_value_myr"]) for r in env["rows"]] == expect
        # fallback:validate still reports the model. generate_empty does not.
        assert env.get("served_attribution") == "reported"

    empty_top5 = _ask(exe, "Top 5 selling SKUs by sales", session_id="replay-empty")
    _assert_served(empty_top5)
    assert _skus(empty_top5) == TOP5

    categoty = _ask(exe, "top 3 categoty sales", session_id="replay-empty")
    _assert_served(categoty)
    assert [(r["product_category"], r["outbound_value_myr"]) for r in categoty["rows"]] == TOP3_CAT

    count = _ask(exe, "How many SKUs in inventory?", session_id="replay-empty")
    _assert_served(count)
    assert count["rows"] == [{"sku_count": 7}]

    per_cat = _ask(exe, "how many SKUs per category", session_id="replay-empty")
    _assert_served(per_cat)
    assert {(r["product_category"], r["sku_count"]) for r in per_cat["rows"]} == {
        ("RAW", 2),
        ("PACKAGING", 2),
        ("PARTS", 2),
        ("CHEMICALS", 1),
    }

    freight = _ask(exe, "freight spend per destination", session_id="replay-empty")
    _assert_served(freight)
    assert [
        (r["location_location_code"], r["shipping_cost_myr"]) for r in freight["rows"]
    ] == [("WH-B", 1130.0), ("WH-A", 540.0), ("WH-D", 460.0), ("WH-C", 190.0)]


@pytest.mark.parametrize(
    "question",
    [
        "revenue by sku at warehouse B",
        "top 3 skus by sales in WH-A",
        "suppliers in Penang",
        "top 5 skus by revenue in 2024",
        "best skus by revenue last month",
        "skus by sales excluding chemicals",
        "top 4 RAW skus by revenue",
        "show every supplier in SG",
        "most skus by freight spend at WH-D",
        "top 5 skus by pallets this week",
        "what are the top skus by sales for packaging",
        "night shift revenue by sku",
    ],
)
def test_extra_phrasings_predicate_or_named_abstain(tmp_path: Path, question: str) -> None:
    env = _ask(_executor(tmp_path), question, session_id="extra")
    blob = _blob(env)
    if env["abstained"]:
        assert env["rows"] == []
        assert env.get("served_attribution") == "none"
        assert "ungrounded_qualifier:" in blob or "unhonored_qualifier:" in blob
        return
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("served_attribution") == "none"
    sql = str(env.get("sql_used") or "")
    assert sql
    if "WH-B" in question or "warehouse B" in question:
        assert "WH-B" in sql
        assert "RS622XK" not in {s for s, _v in _skus(env)}
    if "WH-A" in question:
        assert "WH-A" in sql
        assert "SKU-BETA" not in {s for s, _v in _skus(env)}
    if "excluding chemicals" in question:
        assert "CHEMICALS" in sql
        assert "SKU-GAMMA" not in {s for s, _v in _skus(env)}
    if "RAW" in question:
        assert "RAW" in sql
        assert "outbound_value_myr" in sql
        assert "SKU-BETA" not in {s for s, _v in _skus(env)}
    if question.endswith("in SG"):
        assert "SG" in sql
    if "WH-D" in question:
        assert "WH-D" in sql
        assert "shipping_cost_myr" in sql
    if "for packaging" in question:
        assert "PACKAGING" in sql
        assert "RS622XK" not in {s for s, _v in _skus(env)}


# Spelled-out limits, non-digit ids, and stray digits. Same allow-list as the
# generate_empty gate: a word that is not a known measure, grain, or category
# value abstains. On 72df50d8 the first, third, fourth, and fifth served a
# ranking that dropped the qualifier.
_BLIND = (
    ("top five raw skus by stock value except SKU-GAMMA", ("ungrounded_qualifier:five",)),
    ("top 3 by sales in 2025", ("unhonored_qualifier:time_filter=year=2025",)),
    ("next three after that", ("ungrounded_qualifier:next",)),
    ("top 5 skus by revenue at WH-B except BETA", ("ungrounded_qualifier:beta",)),
    (
        "top 4 skus by sales excluding packaging and sku 10023",
        ("ungrounded_qualifier:10023",),
    ),
    (
        "top three chemicals by sales last month",
        (
            "ungrounded_qualifier:three",
            "unhonored_qualifier:time_filter=last_1_month",
        ),
    ),
)


@pytest.mark.parametrize(("question", "needles"), _BLIND)
def test_blind_spot_named_abstain_or_not_served(
    tmp_path: Path, question: str, needles: tuple[str, ...]
) -> None:
    env = _ask(_executor(tmp_path), question, session_id="blind")
    blob = _blob(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert env.get("sql_used") in (None, "")
    assert env.get("served_attribution") == "none"
    assert any(needle in blob for needle in needles), blob
    assert "25654" not in blob


# Written here from the demo tables. Not the compiler's SQL.
_WH_B_SQL = """
SELECT t.sku AS product_sku,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS outbound_value_myr
FROM transactions t
JOIN locations l ON t.location_id = l.location_id
WHERE t.txn_type = 'outbound' AND l.location_code = 'WH-B'
GROUP BY t.sku
ORDER BY outbound_value_myr DESC
"""
_RAW_SQL = """
SELECT sku AS product_sku,
       ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS stock_value_myr
FROM inventory
WHERE category = 'RAW'
GROUP BY sku
ORDER BY stock_value_myr DESC
"""
_EXCL_PACK_SQL = """
SELECT t.sku AS product_sku,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS outbound_value_myr
FROM transactions t
JOIN (SELECT DISTINCT sku, category FROM inventory) c ON t.sku = c.sku
WHERE t.txn_type = 'outbound' AND c.category <> 'PACKAGING'
GROUP BY t.sku
ORDER BY outbound_value_myr DESC
LIMIT 4
"""
_CHEM_SALES_SQL = """
SELECT t.sku AS product_sku,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS outbound_value_myr
FROM transactions t
JOIN (SELECT DISTINCT sku, category FROM inventory) c ON t.sku = c.sku
WHERE t.txn_type = 'outbound' AND c.category = 'CHEMICALS'
GROUP BY t.sku
ORDER BY outbound_value_myr DESC
"""
_EXCEPT_GAMMA_SQL = """
SELECT t.sku AS product_sku,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS outbound_value_myr
FROM transactions t
WHERE t.txn_type = 'outbound' AND t.sku <> 'SKU-GAMMA'
GROUP BY t.sku
ORDER BY outbound_value_myr DESC
LIMIT 5
"""
_EXCEPT_GAMMA_ALPHA_SQL = """
SELECT t.sku AS product_sku,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS outbound_value_myr
FROM transactions t
WHERE t.txn_type = 'outbound'
  AND t.sku NOT IN ('SKU-GAMMA', 'SKU-ALPHA')
GROUP BY t.sku
ORDER BY outbound_value_myr DESC
LIMIT 5
"""
_CHEM_REVENUE_SQL = """
SELECT t.sku AS product_sku,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS outbound_value_myr
FROM transactions t
JOIN (SELECT DISTINCT sku, category FROM inventory) c ON t.sku = c.sku
WHERE t.txn_type = 'outbound' AND c.category = 'CHEMICALS'
GROUP BY t.sku
ORDER BY outbound_value_myr DESC
LIMIT 5
"""
_EXCEPT_DELTA_SQL = """
SELECT t.sku AS product_sku,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS outbound_value_myr
FROM transactions t
WHERE t.txn_type = 'outbound' AND t.sku <> 'SKU-DELTA'
GROUP BY t.sku
ORDER BY outbound_value_myr DESC
LIMIT 5
"""
_EXCEPT_BETA_SQL = """
SELECT t.sku AS product_sku,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS outbound_value_myr
FROM transactions t
WHERE t.txn_type = 'outbound' AND t.sku <> 'SKU-BETA'
GROUP BY t.sku
ORDER BY outbound_value_myr DESC
LIMIT 5
"""
_EXCL_PACK_CHEM_SQL = """
SELECT t.sku AS product_sku,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS outbound_value_myr
FROM transactions t
JOIN (SELECT DISTINCT sku, category FROM inventory) c ON t.sku = c.sku
WHERE t.txn_type = 'outbound'
  AND c.category <> 'PACKAGING'
  AND c.category <> 'CHEMICALS'
GROUP BY t.sku
ORDER BY outbound_value_myr DESC
LIMIT 3
"""


def _lake_rows(db: Path, sql: str) -> list[dict[str, Any]]:
    con = connect_file(db)
    try:
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description] if rel.description else []
        out = []
        for row in rel.fetchall():
            item: dict[str, Any] = {}
            for col, val in zip(cols, row, strict=True):
                if isinstance(val, bool):
                    item[col] = val
                elif isinstance(val, (int, float)):
                    item[col] = float(val)
                else:
                    item[col] = val
            out.append(item)
        return out
    finally:
        con.close()


def _as_float_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item: dict[str, Any] = {}
        for col, val in row.items():
            if isinstance(val, bool):
                item[col] = val
            elif isinstance(val, (int, float)):
                item[col] = float(val)
            else:
                item[col] = val
        out.append(item)
    return out


def test_new_served_rows_match_independent_sql(tmp_path: Path) -> None:
    db = tmp_path / "empty_ranked.duckdb"
    exe = _executor(tmp_path)
    checks = (
        ("top 5 skus by revenue at WH-B", _WH_B_SQL),
        ("top 3 RAW skus by stock value", _RAW_SQL),
        ("top 4 skus by sales excluding packaging", _EXCL_PACK_SQL),
        ("top 5 chemicals SKUs by sales", _CHEM_SALES_SQL),
        ("top 5 skus by revenue except SKU-GAMMA", _EXCEPT_GAMMA_SQL),
        (
            "top 5 skus by revenue excluding sku-gamma and sku-alpha",
            _EXCEPT_GAMMA_ALPHA_SQL,
        ),
        ("top 5 skus by revenue for chemicals", _CHEM_REVENUE_SQL),
        ("top 5 skus by revenue except sku-gamma", _EXCEPT_GAMMA_SQL),
        ("top 5 skus by revenue for the chemicals category", _CHEM_REVENUE_SQL),
        ("top 5 skus by revenue without SKU-DELTA", _EXCEPT_DELTA_SQL),
        ("what are the best chemicals by revenue", _CHEM_REVENUE_SQL),
        (
            "top 3 skus by revenue excluding packaging and chemicals",
            _EXCL_PACK_CHEM_SQL,
        ),
        ("show me top 5 skus by revenue except SKU-BETA", _EXCEPT_BETA_SQL),
    )
    for question, sql in checks:
        env = _ask(exe, question, session_id="indep")
        _assert_served(env)
        assert env.get("plan_origin") == "ontology_ranking"
        assert _as_float_rows(env["rows"]) == _lake_rows(db, sql)


@pytest.fixture(scope="module", autouse=True)
def _dump_envelopes() -> Any:
    yield
    Path("/tmp/head_envelopes.json").write_text(
        json.dumps(_SAVED, default=str, indent=2), encoding="utf-8"
    )
