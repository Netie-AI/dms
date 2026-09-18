"""GEN-02 k-scale: FreeRoute ranked retry + metric-id slots. WRONG=0.

Does not stamp COMPLETE. Live ontology_plan rise is Platform after deploy.
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
    generate_retry_eligible,
    query_plan_from_insights_ranking,
    typed_query_plan,
)
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.semantic_retrieve import (
    load_measure_aliases,
    shape_from_metric_id,
    slots_for_measure,
    retrieve_short_context,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import build_gen_path_prove_report, classify_plan_source  # noqa: E402


def _submit_ok(_sql: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        ok=True,
        status="ok",
        run_id="run_kscale",
        output={"rows": [{"n": 1}]},
    )


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(entry_id="led_kscale", hash="hash_kscale_not_entry")


def _rank(*metric_ids: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "REFUSE",
        "phase": "generate",
        "generative": {"ok": False, "sql": None, "climb": {"final": "UNARMED"}},
        "ontology": {
            "ok": True,
            "metrics": [{"id": mid, "importance": {"rank": i + 1}} for i, mid in enumerate(metric_ids)],
        },
        "values": [],
    }


def _env(tmp_path: Path, question: str, *metric_ids: str) -> dict[str, Any] | None:
    db = tmp_path / "kscale.duckdb"
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


def test_shape_from_pack_id_encodes_group_and_top() -> None:
    shape = shape_from_metric_id("cq_top3_category_sales")
    assert shape["group_by"] == [["product", "category"]]
    assert shape["limit"] == 3
    sku = shape_from_metric_id("cq_sku_count")
    assert sku["group_by"] == []
    dest = shape_from_metric_id("cq_cost_by_destination")
    assert dest["group_by"] == [["location", "location_code"]]


def test_alias_top3_category_sales_is_outbound_not_sku() -> None:
    aliases = load_measure_aliases()
    allowed = {"outbound_value_myr", "sku_count"}
    plan = query_plan_from_insights_ranking(
        {"ontology": {"metrics": [{"id": "cq_top3_category_sales"}, {"id": "sku_count"}]}},
        allowed,
        aliases=aliases,
    )
    assert plan is not None
    assert plan["query_plan"]["measure"] == "outbound_value_myr"
    assert plan["plan_source"] == "ontology_plan"


def test_ranking_walk_skips_noise_not_intent() -> None:
    aliases = load_measure_aliases()
    allowed = {"sku_count", "stock_value_myr"}
    skipped = {
        "ontology": {
            "metrics": [
                {"id": "cq_active_alerts"},
                {"id": "sku_count"},
            ]
        }
    }
    assert (
        query_plan_from_insights_ranking(
            skipped, allowed, aliases=aliases, question="How many SKUs do we have?"
        )["query_plan"]["measure"]
        == "sku_count"
    )
    assert (
        query_plan_from_insights_ranking(
            skipped, {"sku_count"}, aliases=aliases
        )
        is None
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


def test_typo_category_sales_is_ontology_plan(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        "Show top 3 categoty sales",
        "cq_top3_category_sales",
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    assert env["abstained"] is False
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))
    assert_envelope_valid(env)


def test_noise_then_sku_is_ontology_plan(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        "How many SKUs do we have in inventory?",
        "cq_active_alerts",
        "sku_count",
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"


def test_supplier_ranking_stays_abstain(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        "Rank suppliers by combined risk and lead time score",
        "cq_supplier_ranking",
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env.get("sql_used") is None
    assert env.get("plan_source") != "bind_plan"


def test_planted_storage_bin_stays_abstain(tmp_path: Path) -> None:
    env = _env(tmp_path, "Show stock by storage bin", "cq_stock_value_by_category")
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True


def test_how_full_synonym_stays_abstain(tmp_path: Path) -> None:
    env = _env(tmp_path, "how full is each warehouse", "cq_capacity_utilisation")
    assert env is not None
    assert env["badge"] == "ABSTAIN"


def test_slots_use_ranked_id_when_question_typos(tmp_path: Path) -> None:
    db = tmp_path / "kscale.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    q = "Show top 3 categoty sales"
    ctx = retrieve_short_context(
        q,
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        ontology=onto,
    )
    slotted = slots_for_measure(q, ctx, "outbound_value_myr", ranked_id="cq_top3_category_sales")
    assert slotted is not None
    assert slotted["plan_source"] == "ontology_plan"
    assert slotted["query_plan"]["measure"] == "outbound_value_myr"
    assert slotted["query_plan"]["group_by"] == [["product", "category"]]
    assert slotted["query_plan"]["limit"] == 3


def test_nested_climb_plan_is_typed() -> None:
    plan = typed_query_plan(
        {
            "generative": {
                "ok": False,
                "sql": None,
                "climb": {"final": "SLOTS", "query_plan": {"measure": "sku_count"}},
            }
        }
    )
    assert plan is not None
    assert plan["measure"] == "sku_count"


def test_unarmed_is_not_retry_eligible() -> None:
    payload = _rank("sku_count")
    assert generate_retry_eligible(payload) is False
    armed_empty = {
        "status": "ABSTAIN",
        "phase": "generate",
        "generative": {"ok": False, "sql": None, "climb": {"final": "EMPTY"}},
        "ontology": {"metrics": [{"id": "sku_count"}]},
    }
    assert generate_retry_eligible(armed_empty) is True


def test_freeroute_retry_sends_ranked_slots_and_sql() -> None:
    posts: list[dict[str, Any]] = []

    class _Unarmed:
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
                "generative": {"ok": True, "sql": "SELECT COUNT(*) AS n FROM inventory"},
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

        def post(self, url: str, json: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
            posts.append({"url": url, "json": json, "headers": headers})
            if len([p for p in posts if "/v1/insights" in p["url"] and "ontology" not in p["url"]]) == 1:
                return _Unarmed()
            return _Sql()

        def get(self, *_a: Any, **_k: Any) -> _Onto:
            return _Onto()

    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_query(
            "http://127.0.0.1:8010",
            question="How many SKUs do we have in inventory?",
            ontology={"intent_slots": {"measure": "sku_count"}},
            api_key="ov_test_kscale_02",
        )
    gens = [p for p in posts if p["url"].endswith("/v1/insights")]
    assert len(gens) == 2
    assert gens[0]["json"]["model_preference"] == FREEROUTE_PREFERENCE
    assert gens[1]["json"]["generate_retry"] == "ranked_slots"
    assert gens[1]["json"]["ranked_metric"] == "sku_count"
    assert gens[1]["json"]["query_plan"]["measure"] == "sku_count"
    assert gens[1]["json"]["model_preference"] == FREEROUTE_PREFERENCE
    assert "LIVE_KEY" not in str(gens)
    assert ":5000" not in str(gens)
    assert "api_key" not in gens[1]["json"]
    assert out is not None
    assert str(out.get("query_sql") or "").upper().startswith("SELECT")
    assert out.get("plan_source") == "ontology_plan"


def test_kscale_offline_prove_gt_nine_not_complete(tmp_path: Path) -> None:
    sku = _env(tmp_path, "How many SKUs do we have in inventory?", "sku_count")
    stock = _env(
        tmp_path,
        "What is total stock value by category?",
        "stock_value_by_category",
    )
    sales = _env(tmp_path, "Show top 3 categoty sales", "cq_top3_category_sales")
    assert sku and stock and sales
    cases = [
        {"id": "cq_sku_count", "verdict": "OK", "plan_source": classify_plan_source(sku)},
        {
            "id": "cq_stock_value_by_category",
            "verdict": "OK",
            "plan_source": classify_plan_source(stock),
        },
        {
            "id": "trap_categoty",
            "verdict": "OK",
            "plan_source": classify_plan_source(sales),
        },
    ] + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(23)]
    report = build_gen_path_prove_report(
        {"OK": 3, "LAYER": 0, "ABSTAIN": 23, "WRONG": 0},
        cases=cases,
        mode="offline",
    )
    blob = json.dumps(report)
    assert "COMPLETE" not in blob
    assert "99.95" not in blob
    assert report["by_plan_source"]["ontology_plan"]["answered"] >= 3
    assert report["by_plan_source"]["bind_plan"]["answered"] == 0
    assert report["passed_wrong_zero"] is True
    for env in (sku, stock, sales):
        assert env.get("plan_source") == "ontology_plan"
        assert env["badge"] == "L2_VALIDATED"
