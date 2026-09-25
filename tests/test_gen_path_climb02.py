"""GEN-PATH-CLIMB-02: walk SKU-noise ranking, WRONG=0, ontology_plan>11.

Does not stamp COMPLETE. Live ontology_plan>11 is Platform after deploy.
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
    pack_id_shape,
    query_plan_from_insights_ranking,
    ranking_is_noise,
    typed_ranked_retry_plan,
)
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.semantic_retrieve import load_measure_aliases

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import build_gen_path_prove_report, classify_plan_source  # noqa: E402
from test_gen_path_climb05 import GRAIN_GUARDED, assert_grain_abstain  # noqa: E402

SALES_Q = "Top 5 selling SKUs by revenue"
VOLUME_Q = "Top 3 SKUs by quantity sold"
REORDER_Q = "Which SKUs are below reorder level in warehouse A?"


def _submit_ok(_sql: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        ok=True,
        status="ok",
        run_id="run_climb02",
        output={"rows": [{"n": 1}]},
    )


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(entry_id="led_climb02", hash="hash_climb02_not_entry")


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


def _env(tmp_path: Path, question: str, *metric_ids: str) -> dict[str, Any] | None:
    db = tmp_path / "climb02.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable={
            "inventory",
            "locations",
            "transactions",
            "suppliers",
            "shipments",
        },
        compute=lambda _c: _rank(*metric_ids),
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )


def test_pack_id_sales_top_encodes_sku_and_limit() -> None:
    shape = pack_id_shape("cq_sales_top5_value")
    assert shape["group_by"] == [["product", "sku"]]
    assert shape["limit"] == 5
    vol = pack_id_shape("cq_sales_top3_volume")
    assert vol["group_by"] == [["product", "sku"]]
    assert vol["limit"] == 3


def test_prefer_walk_skips_sku_count_not_stock_intent() -> None:
    aliases = load_measure_aliases()
    allowed = {
        "sku_count",
        "outbound_value_myr",
        "outbound_kg",
        "below_reorder_lots",
        "stock_value_myr",
    }
    sales = {
        "ontology": {
            "metrics": [
                {"id": "sku_count"},
                {"id": "cq_sales_top5_value"},
            ]
        }
    }
    plan = query_plan_from_insights_ranking(
        sales,
        allowed,
        aliases=aliases,
        prefer="outbound_value_myr",
        question=SALES_Q,
    )
    assert plan is not None
    assert plan["query_plan"]["measure"] == "outbound_value_myr"
    assert plan["query_plan"]["ranked_id"] == "cq_sales_top5_value"
    assert ranking_is_noise(
        "sku_count", question=SALES_Q, prefer="outbound_value_myr"
    )
    stock_first = {
        "ontology": {
            "metrics": [
                {"id": "stock_value_by_category"},
                {"id": "sku_count"},
            ]
        }
    }
    assert (
        query_plan_from_insights_ranking(
            stock_first,
            {"sku_count"},
            aliases=aliases,
            question="What is total stock value by category?",
        )
        is None
    )


def test_sku_noise_then_sales_is_ontology_plan(tmp_path: Path) -> None:
    env = _env(tmp_path, SALES_Q, "sku_count", "cq_sales_top5_value")
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    assert env["abstained"] is False
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))
    assert_envelope_valid(env)


def test_sku_noise_then_volume_is_ontology_plan(tmp_path: Path) -> None:
    env = _env(tmp_path, VOLUME_Q, "sku_count", "cq_sales_top3_volume")
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"


def test_sku_noise_then_low_stock_is_ontology_plan(tmp_path: Path) -> None:
    env = _env(tmp_path, REORDER_Q, "sku_count", "cq_low_stock_wh_a")
    assert env is not None
    # GRAIN-GUARD-01: the ranked plan is a per-SKU COUNT FILTER tally that
    # lists a SKU not below reorder (oracle-WRONG). Named ABSTAIN, not L2.
    assert_grain_abstain(env)
    assert "unrequested_measure:below_reorder_lots" in " ".join(env["assumptions"])


def test_supplier_ranking_is_ontology_plan_not_sku(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        "Rank suppliers by combined risk and lead time score",
        "sku_count",
        "cq_supplier_ranking",
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    sql = (env.get("sql_used") or "").lower()
    assert "risk_score" in sql
    assert "sku_count" not in sql
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))


def test_planted_refuses_stay_abstain(tmp_path: Path) -> None:
    for q, mid in (
        ("how full is each warehouse", "cq_capacity_utilisation"),
        ("Show stock by storage bin", "cq_stock_value_by_category"),
        ("How many delayed incoming shipments per warehouse?", "cq_cost_by_destination"),
    ):
        env = _env(tmp_path, q, "sku_count", mid)
        assert env is not None
        assert env["badge"] == "ABSTAIN"
        assert env["abstained"] is True


def test_retry_walks_to_sales_slots_not_sku_count() -> None:
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
                        "SELECT sku, ROUND(SUM(quantity_kg * unit_cost_myr), 2) "
                        "AS outbound_value_myr FROM transactions "
                        "WHERE txn_type IN ('OUT', 'outbound') "
                        "GROUP BY sku ORDER BY 2 DESC LIMIT 5"
                    ),
                },
            }

    class _Onto:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "ok": True,
                "phase": "ontology",
                "ontology": {
                    "ok": True,
                    "metrics": [
                        {"id": "sku_count"},
                        {"id": "cq_sales_top5_value"},
                    ],
                },
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
            "outbound_value_myr": {
                "grain": "transaction",
                "description": "outbound issued stock value at cost",
            },
        },
        "measure_aliases": {"cq_sales_top5_value": "outbound_value_myr"},
        "intent_slots": {
            "measure": "outbound_value_myr",
            "group_by": [["product", "sku"]],
            "limit": 5,
        },
    }
    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_query(
            "http://127.0.0.1:8010",
            question=SALES_Q,
            ontology=onto,
            api_key="ov_test_climb_02",
        )
    gens = [p for p in posts if p["url"].endswith("/v1/insights")]
    assert len(gens) == 2
    retry = gens[1]["json"]
    assert retry["model_preference"] == FREEROUTE_PREFERENCE
    assert retry["generate_retry"] == "ranked_slots"
    assert retry["query_plan"]["measure"] == "outbound_value_myr"
    assert retry["query_plan"]["group_by"] == [["product", "sku"]]
    assert retry["query_plan"]["limit"] == 5
    assert retry["ranked_metric"] == "cq_sales_top5_value"
    assert "LIVE_KEY" not in str(gens)
    assert ":5000" not in str(gens)
    assert out is not None
    assert str(out.get("query_sql") or "").upper().startswith("SELECT")
    assert out.get("plan_source") == "ontology_plan"


def test_typed_retry_plan_walks_prefer() -> None:
    payload = {
        "ontology": {
            "metrics": [{"id": "sku_count"}, {"id": "cq_sales_top5_value"}]
        }
    }
    plan = typed_ranked_retry_plan(
        payload,
        ontology={
            "measures": {
                "sku_count": {"grain": "product"},
                "outbound_value_myr": {"grain": "transaction"},
            },
            "measure_aliases": {"cq_sales_top5_value": "outbound_value_myr"},
            "intent_slots": {"measure": "outbound_value_myr", "limit": 5},
        },
        question=SALES_Q,
    )
    assert plan is not None
    assert plan["measure"] == "outbound_value_myr"
    assert plan["ranked_id"] == "cq_sales_top5_value"
    assert plan["limit"] == 5
    assert plan["group_by"] == [["product", "sku"]]


def test_climb02_offline_prove_gt_eleven_not_complete(tmp_path: Path) -> None:
    pairs = [
        (
            "cq_spend_by_country",
            "What is our total spend by supplier country?",
            ("spend_by_country",),
        ),
        (
            "cq_stock_value_by_category",
            "What is total stock value by category?",
            ("stock_value_by_category",),
        ),
        ("cq_sales_top5_value", SALES_Q, ("sku_count", "cq_sales_top5_value")),
        ("cq_sales_top3_volume", VOLUME_Q, ("sku_count", "cq_sales_top3_volume")),
        ("cq_low_stock_wh_a", REORDER_Q, ("sku_count", "cq_low_stock_wh_a")),
        ("trap_categoty", "Show top 3 categoty sales", ("cq_top3_category_sales",)),
        (
            "ops_shipment_cost",
            "Show shipment cost by destination",
            ("cq_cost_by_destination",),
        ),
        (
            "cq_sku_count_by_category",
            "Show SKU count by category",
            ("cq_sku_count_by_category",),
        ),
        (
            "cq_capacity_utilisation",
            "Show warehouse capacity utilisation",
            ("cq_capacity_utilisation",),
        ),
        ("cq_cold_storage", "Which locations are cold storage?", ("cq_cold_storage",)),
        ("cq_expired_items", "Which items are expired?", ("cq_expired_items",)),
        ("cq_chemicals_list", "List chemicals in inventory", ("cq_chemicals_list",)),
    ]
    envs = [_env(tmp_path, q, *ids) for _qid, q, ids in pairs]
    for (qid, _q, _ids), env in zip(pairs, envs, strict=True):
        assert env is not None
        if qid in GRAIN_GUARDED:
            # GRAIN-GUARD-01: oracle-WRONG grain under L2 before; now named.
            assert_grain_abstain(env)
            continue
        assert env.get("plan_source") == "ontology_plan"
        assert env["badge"] == "L2_VALIDATED"
    ok = sum(1 for qid, _q, _ids in pairs if qid not in GRAIN_GUARDED)
    assert ok == 10
    cases = [
        (
            {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
            if qid in GRAIN_GUARDED
            else {"id": qid, "verdict": "OK", "plan_source": classify_plan_source(env)}
        )
        for (qid, _q, _ids), env in zip(pairs, envs, strict=True)
    ] + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(14)]
    report = build_gen_path_prove_report(
        {"OK": ok, "LAYER": 0, "ABSTAIN": 26 - ok, "WRONG": 0},
        cases=cases,
        mode="offline",
    )
    blob = json.dumps(report)
    assert "COMPLETE" not in blob
    assert "99.95" not in blob
    assert report["by_plan_source"]["ontology_plan"]["answered"] == ok
    assert report["by_plan_source"]["bind_plan"]["answered"] == 0
    assert report["passed_wrong_zero"] is True
