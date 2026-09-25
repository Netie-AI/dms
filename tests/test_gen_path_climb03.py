"""GEN-PATH-CLIMB-03: overlay exhausted ranking, WRONG=0, ontology_plan>13.

Does not stamp COMPLETE. Live ontology_plan>13 is Platform after deploy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cortex_client.compute import (
    FREEROUTE_PREFERENCE,
    compute_query,
    overlay_pack_id_from_question,
    pack_id_shape,
    query_plan_from_insights_ranking,
)
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.semantic_retrieve import intent_slots, load_measure_aliases

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import build_gen_path_prove_report, classify_plan_source  # noqa: E402

L0_PAIRS: tuple[tuple[str, str], ...] = (
    ("cq_spend_by_country", "What is our total spend by supplier country?"),
    ("cq_stock_value_by_category", "What is total stock value by category?"),
    ("cq_sales_top5_value", "Top 5 selling SKUs by revenue"),
    ("cq_sku_count", "How many SKUs do we have in inventory?"),
    ("cq_capacity_utilisation", "Show warehouse capacity utilisation"),
    ("cq_sales_top3_volume", "Top 3 SKUs by quantity sold"),
    ("cq_sku_count_by_category", "Show SKU count by category"),
    ("cq_low_stock_wh_a", "Which SKUs are below reorder level in warehouse A?"),
    ("ops_stock_value", "What is total stock value by category?"),
    ("ops_shipment_cost", "Show shipment cost by destination"),
    ("trap_categoty", "Show top 3 categoty sales"),
    ("cq_cold_storage", "Which locations are cold storage?"),
    ("cq_capacity_above_90", "Which locations are above 90 percent capacity?"),
    ("cq_expired_items", "Which items are expired?"),
    ("cq_chemicals_list", "List chemicals in inventory"),
    ("cq_supplier_ranking", "Rank suppliers by combined risk and lead time score"),
    ("cq_cctv_wh_a", "Show the CCTV camera for warehouse A"),
)


def _submit_sql(warehouse: Path) -> Any:
    def _run(sql: str) -> Any:
        from types import SimpleNamespace

        con = connect_file(warehouse)
        try:
            rel = con.execute(sql)
            cols = [d[0] for d in rel.description] if rel.description else []
            rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(
            ok=True,
            status="ok",
            run_id="run_climb03",
            output={"rows": rows},
        )

    return _run


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(entry_id="led_climb03", hash="hash_climb03_not_entry")


def _rank(*metric_ids: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "REFUSE",
        "phase": "generate",
        "generative": {"ok": False, "sql": None, "climb": {"final": "UNARMED"}},
        "ontology": {
            "ok": True,
            "metrics": [
                {"id": mid, "importance": {"rank": i + 1}}
                for i, mid in enumerate(metric_ids)
            ],
        },
        "values": [],
    }


def _env(
    tmp_path: Path,
    question: str,
    *metric_ids: str,
    grantable: set[str] | None = None,
) -> dict[str, Any] | None:
    db = tmp_path / "climb03.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    tables = grantable or {
        "inventory",
        "locations",
        "transactions",
        "suppliers",
        "shipments",
    }
    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=tables,
        compute=lambda _c: _rank(*metric_ids),
        submit=_submit_sql(db),
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )


def test_overlay_picks_prefer_pack_id_not_weaker() -> None:
    aliases = load_measure_aliases()
    allowed = {
        "sku_count",
        "stock_value_myr",
        "utilisation_pct",
        "outbound_kg",
        "below_reorder_lots",
        "below_reorder_kg",
        "shipping_cost_myr",
        "supplier_rank_score",
    }
    specs = {
        "below_reorder_lots": "lots below reorder level",
        "below_reorder_kg": "on-hand kg in lots below reorder level (low stock)",
        "utilisation_pct": "warehouse capacity utilisation percent",
        "outbound_kg": "quantity sold",
        "shipping_cost_myr": "shipment cost",
        "supplier_rank_score": "supplier combined risk and lead time ranking score",
        "stock_value_myr": "stock value inventory spend",
    }
    cold = overlay_pack_id_from_question(
        "Which locations are cold storage?",
        prefer="utilisation_pct",
        aliases=aliases,
        allowed=allowed,
        specs=specs,
    )
    assert cold is not None
    assert "cold" in cold
    assert aliases[cold] == "utilisation_pct"
    volume = overlay_pack_id_from_question(
        "Top 3 SKUs by quantity sold",
        prefer="outbound_kg",
        aliases=aliases,
        allowed=allowed,
        specs=specs,
    )
    assert volume is not None
    assert aliases[volume] == "outbound_kg"
    reorder = overlay_pack_id_from_question(
        "Which SKUs are below reorder level in warehouse A?",
        prefer="below_reorder_kg",
        aliases=aliases,
        allowed=allowed,
        specs=specs,
    )
    assert reorder is not None
    assert aliases[reorder] == "below_reorder_kg"
    assert (
        overlay_pack_id_from_question(
            "How many SKUs do we have in inventory?",
            prefer="sku_count",
            aliases={"stock_value_by_category": "stock_value_myr"},
            allowed=allowed,
            specs=specs,
        )
        is None
    )


def test_exhausted_ranking_overlays_not_abort() -> None:
    aliases = load_measure_aliases()
    allowed = {
        "sku_count",
        "outbound_kg",
        "utilisation_pct",
        "shipping_cost_myr",
        "below_reorder_lots",
        "stock_value_myr",
        "supplier_rank_score",
    }
    only_sku = {"ontology": {"metrics": [{"id": "sku_count"}]}}
    specs = {
        "outbound_kg": "quantity sold / outbound kg",
        "sku_count": "count of unique SKUs",
    }
    plan = query_plan_from_insights_ranking(
        only_sku,
        allowed,
        aliases=aliases,
        specs=specs,
        prefer="outbound_kg",
        question="Top 3 SKUs by quantity sold",
    )
    assert plan is not None
    assert plan["query_plan"]["measure"] == "outbound_kg"
    assert plan["plan_source"] == "ontology_plan"
    abort = query_plan_from_insights_ranking(
        {"ontology": {"metrics": [{"id": "stock_value_by_category"}]}},
        {"sku_count"},
        aliases=aliases,
        question="What is total stock value by category?",
    )
    assert abort is None


def test_pack_id_remaining_l0_shapes() -> None:
    assert pack_id_shape("cq_cold_storage")["group_by"] == [
        ["location", "location_code"]
    ]
    assert pack_id_shape("cq_chemicals_list")["group_by"] == [["product", "sku"]]
    assert pack_id_shape("cq_cctv_wh_a")["group_by"] == [
        ["location", "cctv_camera_id"]
    ]
    assert pack_id_shape("cq_supplier_ranking")["group_by"] == [
        ["supplier", "supplier_id"]
    ]
    assert pack_id_shape("cq_capacity_above_90")["keep_gt"] == 90.0


def test_sku_only_ranking_recovers_l0s(tmp_path: Path) -> None:
    hits = 0
    cases: list[dict[str, Any]] = []
    for qid, question in L0_PAIRS:
        env = _env(tmp_path, question, "sku_count")
        assert env is not None
        src = classify_plan_source(env)
        ok = env["badge"] == "L2_VALIDATED" and src == "ontology_plan"
        if ok:
            hits += 1
            assert env["abstained"] is False
            assert env.get("rows")
            assert_envelope_valid(env)
        cases.append(
            {
                "id": qid,
                "verdict": "OK" if ok else "ABSTAIN",
                "plan_source": src if ok else "other",
            }
        )
    assert hits > 13
    report = build_gen_path_prove_report(
        {
            "OK": hits,
            "LAYER": 0,
            "ABSTAIN": 26 - hits,
            "WRONG": 0,
        },
        cases=cases
        + [
            {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
            for i in range(26 - len(cases))
        ],
        mode="offline",
    )
    blob = json.dumps(report)
    assert "COMPLETE" not in blob
    assert "99.95" not in blob
    assert report["by_plan_source"]["ontology_plan"]["answered"] > 13
    assert report["by_plan_source"]["bind_plan"]["answered"] == 0
    assert report["passed_wrong_zero"] is True


def test_capacity_above_90_keep_gt_has_rows(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        "Which locations are above 90 percent capacity?",
        "sku_count",
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    rows = env.get("rows") or []
    assert len(rows) >= 1
    # Keys only: the list of locations, threshold applied as HAVING in SQL.
    db = tmp_path / "climb03.duckdb"
    con = connect_file(db)
    try:
        want = {
            r[0]
            for r in con.execute(
                "SELECT location_code FROM locations "
                "WHERE 100.0 * current_load_kg / capacity_kg > 90"
            ).fetchall()
        }
    finally:
        con.close()
    assert all(set(row) == {"location_location_code"} for row in rows)
    assert {row["location_location_code"] for row in rows} == want
    for code in want:
        assert code in env["text"]


def test_supplier_ranking_finance_not_ops(tmp_path: Path) -> None:
    q = "Rank suppliers by combined risk and lead time score"
    fin = _env(tmp_path, q, "sku_count")
    assert fin is not None
    assert fin["badge"] == "L2_VALIDATED"
    assert fin.get("plan_source") == "ontology_plan"
    sql = (fin.get("sql_used") or "").lower()
    assert "risk_score" in sql
    assert "lead_time" in sql
    ops = _env(
        tmp_path,
        q,
        "sku_count",
        grantable={"inventory", "locations", "shipments"},
    )
    assert ops is not None
    assert ops["badge"] == "ABSTAIN"
    assert ops.get("sql_used") is None
    assert ops.get("plan_source") != "bind_plan"


def test_planted_refuses_stay_abstain(tmp_path: Path) -> None:
    for q in (
        "how full is each warehouse",
        "Show stock by storage bin",
        "How many delayed incoming shipments per warehouse?",
        "List active alerts across the warehouse network",
        "Which high-risk suppliers have pending shipments?",
        "Just give me last month's number",
        "Are we short on anything the warehouse should worry about?",
    ):
        env = _env(tmp_path, q, "sku_count", "cq_capacity_utilisation")
        assert env is not None
        assert env["badge"] == "ABSTAIN"
        assert env["abstained"] is True
        assert env.get("plan_source") != "bind_plan"


def test_intent_slots_lock_supplier_rank(tmp_path: Path) -> None:
    db = tmp_path / "climb03.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    slots = intent_slots(
        "Rank suppliers by combined risk and lead time score", onto
    )
    assert slots.get("measure") == "supplier_rank_score"
    assert "supplier_rank_score" in onto.measures


def test_retry_uses_overlay_slots_not_sku_count() -> None:
    posts: list[dict[str, Any]] = []

    class _Empty:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "status": "ABSTAIN",
                "phase": "generate",
                "generative": {"ok": False, "sql": None, "climb": {"final": "EMPTY"}},
            }

    class _Sql:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "status": "ABSTAIN",
                "phase": "generate",
                "generative": {
                    "ok": True,
                    "sql": (
                        "SELECT sku, SUM(quantity_kg) AS outbound_kg "
                        "FROM transactions WHERE txn_type IN ('OUT', 'outbound') "
                        "GROUP BY sku ORDER BY 2 DESC LIMIT 3"
                    ),
                },
            }

    class _Onto:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "ok": True,
                "phase": "ontology",
                "ontology": {"ok": True, "metrics": [{"id": "sku_count"}]},
            }

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(
            self, url: str, json: dict[str, Any], headers: dict[str, str] | None = None
        ) -> Any:
            posts.append({"url": url, "json": json, "headers": headers})
            insights = [p for p in posts if p["url"].endswith("/v1/insights")]
            if len(insights) == 1:
                return _Empty()
            return _Sql()

        def get(self, *_a: Any, **_k: Any) -> _Onto:
            return _Onto()

    onto = {
        "measures": {
            "sku_count": {"grain": "product", "description": "count of unique SKUs"},
            "outbound_kg": {
                "grain": "transaction",
                "description": "quantity sold / outbound kg",
            },
        },
        "measure_aliases": {"cq_sales_top3_volume": "outbound_kg"},
        "intent_slots": {
            "measure": "outbound_kg",
            "group_by": [["product", "sku"]],
            "limit": 3,
        },
    }
    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_query(
            "http://127.0.0.1:8010",
            question="Top 3 SKUs by quantity sold",
            ontology=onto,
            api_key="ov_test_climb_03",
        )
    gens = [p for p in posts if p["url"].endswith("/v1/insights")]
    assert len(gens) == 2
    retry = gens[1]["json"]
    assert retry["model_preference"] == FREEROUTE_PREFERENCE
    assert retry["generate_retry"] == "ranked_slots"
    assert retry["query_plan"]["measure"] == "outbound_kg"
    assert retry["query_plan"]["limit"] == 3
    assert "LIVE_KEY" not in str(gens)
    assert ":5000" not in str(gens)
    assert out is not None
    assert str(out.get("query_sql") or "").upper().startswith("SELECT")
    assert out.get("plan_source") == "ontology_plan"
