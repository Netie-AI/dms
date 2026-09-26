"""GEN-PATH-CLIMB-01: more ontology_plan hits, WRONG=0, not bind-dominant.

Does not stamp COMPLETE. Live ontology_plan>1 is Platform after deploy.
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
    query_plan_from_insights_ranking,
    resolve_ranked_measure,
)
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.semantic_retrieve import (
    intent_slots,
    load_measure_aliases,
    retrieve_short_context,
    slots_for_measure,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import build_gen_path_prove_report, classify_plan_source  # noqa: E402


def _submit_ok(_sql: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        ok=True,
        status="ok",
        run_id="run_climb",
        output={"rows": [{"n": 1}]},
    )


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(entry_id="led_climb", hash="hash_climb_not_entry")


def _rank(metric_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "REFUSE",
        "phase": "generate",
        "generative": {"ok": False, "sql": None, "climb": {"final": "UNARMED"}},
        "ontology": {"ok": True, "metrics": [{"id": metric_id, "importance": {"rank": 1}}]},
        "values": [],
    }


def _env(tmp_path: Path, question: str, metric_id: str, **kwargs: Any) -> dict[str, Any] | None:
    db = tmp_path / "climb.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    grants = {
        "inventory",
        "locations",
        "transactions",
        "suppliers",
        "shipments",
    }
    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=grants,
        compute=lambda _c: _rank(metric_id),
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
        **kwargs,
    )


def test_alias_resolves_same_intent_not_weaker_id() -> None:
    aliases = load_measure_aliases()
    allowed = {"stock_value_myr", "sku_count"}
    assert (
        resolve_ranked_measure(
            "stock_value_by_category",
            allowed,
            aliases=aliases,
        )
        == "stock_value_myr"
    )
    assert (
        resolve_ranked_measure(
            "stock_value_by_category",
            {"sku_count"},
            aliases=aliases,
        )
        is None
    )
    skipped = {
        "ontology": {
            "metrics": [
                {"id": "stock_value_by_category"},
                {"id": "sku_count"},
            ]
        }
    }
    assert query_plan_from_insights_ranking(skipped, {"sku_count"}, aliases=aliases) is None
    plan = query_plan_from_insights_ranking(skipped, allowed, aliases=aliases)
    assert plan is not None
    assert plan["query_plan"]["measure"] == "stock_value_myr"
    assert plan["plan_source"] == "ontology_plan"


def test_prefer_lock_refuses_unrelated_ranking() -> None:
    aliases = load_measure_aliases()
    assert (
        resolve_ranked_measure(
            "stock_value_by_category",
            {"stock_value_myr", "sku_count"},
            aliases=aliases,
            prefer="sku_count",
        )
        is None
    )


def test_retrieve_intent_slots_and_aliases(tmp_path: Path) -> None:
    db = tmp_path / "climb.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    q = "What is total stock value by category?"
    ctx = retrieve_short_context(
        q,
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        ontology=onto,
    )
    slots = intent_slots(q, onto)
    assert slots.get("measure") == "stock_value_myr"
    assert ctx.get("intent_slots", {}).get("measure") == "stock_value_myr"
    assert ctx.get("measure_aliases", {}).get("stock_value_by_category") == "stock_value_myr"
    assert "stock_value_myr" in (ctx.get("measures") or {})
    blob = str(ctx).lower()
    assert "sum(" not in blob
    assert "live_key" not in blob
    assert ":5000" not in blob


def test_slots_overlay_group_by_on_ranked_measure(tmp_path: Path) -> None:
    db = tmp_path / "climb.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    q = "What is total stock value by category?"
    ctx = retrieve_short_context(
        q,
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        ontology=onto,
    )
    slotted = slots_for_measure(q, ctx, "stock_value_myr")
    assert slotted is not None
    assert slotted["plan_source"] == "ontology_plan"
    assert slotted["query_plan"]["measure"] == "stock_value_myr"
    assert slotted["query_plan"]["group_by"] == [["product", "category"]]


def test_ranked_alias_is_ontology_plan_not_bind(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        "What is total stock value by category?",
        "stock_value_by_category",
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    assert env["abstained"] is False
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))
    assert any("insights_ranking:ontology_plan" in str(a) for a in (env.get("assumptions") or []))
    assert_envelope_valid(env)


def test_two_ranked_asks_are_ontology_plan_gt_one(tmp_path: Path) -> None:
    sku = _env(tmp_path, "How many SKUs do we have in inventory?", "sku_count")
    stock = _env(
        tmp_path,
        "What is total stock value by category?",
        "stock_value_by_category",
    )
    assert sku is not None and stock is not None
    assert sku.get("plan_source") == "ontology_plan"
    assert stock.get("plan_source") == "ontology_plan"
    assert sku["badge"] == "L2_VALIDATED"
    assert stock["badge"] == "L2_VALIDATED"
    report = build_gen_path_prove_report(
        {"OK": 2, "LAYER": 0, "ABSTAIN": 24, "WRONG": 0},
        cases=[
            {
                "id": "cq_sku_count",
                "verdict": "OK",
                "plan_source": classify_plan_source(sku),
            },
            {
                "id": "cq_stock_value_by_category",
                "verdict": "OK",
                "plan_source": classify_plan_source(stock),
            },
        ]
        + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(24)],
        mode="offline",
    )
    blob = json.dumps(report)
    assert "COMPLETE" not in blob
    assert "99.95" not in blob
    assert report["by_plan_source"]["ontology_plan"]["answered"] >= 2
    assert report["by_plan_source"]["bind_plan"]["answered"] == 0
    assert report["passed_wrong_zero"] is True


def test_invalid_generate_sql_climbs_via_ranking(tmp_path: Path) -> None:
    db = tmp_path / "climb.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        "How many SKUs do we have in inventory?",
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        compute=lambda _c: {
            "query_sql": "SELECT 1 AS n FROM not_a_granted_table",
            "plan_source": "ontology_plan",
            "status": "ABSTAIN",
            "phase": "generate",
            "generative": {
                "ok": True,
                "sql": "SELECT 1 AS n FROM not_a_granted_table",
            },
            "ontology": {"ok": True, "metrics": [{"id": "sku_count"}]},
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    assert "not_a_granted_table" not in (env.get("sql_used") or "")
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))


def test_hostile_sql_does_not_climb_to_ranking(tmp_path: Path) -> None:
    db = tmp_path / "climb.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        "How many SKUs do we have in inventory?",
        warehouse=db,
        grantable={"inventory", "locations", "transactions", "suppliers"},
        compute=lambda _c: {
            "query_sql": "INSERT INTO inventory VALUES ('x')",
            "plan_source": "ontology_plan",
            "ontology": {"ok": True, "metrics": [{"id": "sku_count"}]},
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env.get("sql_used") is None


def test_planted_refuse_still_abstains(tmp_path: Path) -> None:
    env = _env(tmp_path, "how full is each warehouse", "cq_capacity_utilisation")
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True


def test_ungranted_spend_does_not_green(tmp_path: Path) -> None:
    db = tmp_path / "climb.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        "What is our total spend by supplier country?",
        warehouse=db,
        grantable={"inventory", "locations", "shipments"},
        compute=lambda _c: _rank("spend_by_country"),
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env.get("plan_source") != "bind_plan"


def test_compute_forwards_intent_slots_and_keeps_freeroute() -> None:
    posts: list[dict[str, Any]] = []

    class _Resp:
        status_code = 404

        def json(self) -> dict[str, Any]:
            return {}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(
            self, url: str, json: dict[str, Any], headers: dict[str, str] | None = None
        ) -> _Resp:
            posts.append({"url": url, "json": json, "headers": headers})
            return _Resp()

        def get(
            self,
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> _Resp:
            posts.append({"url": url, "json": params or {}, "headers": headers})
            return _Resp()

    with patch("cortex_client.compute.httpx.Client", _Client):
        compute_query(
            "http://127.0.0.1:8010",
            question="How many SKUs do we have in inventory?",
            ontology={
                "intent_slots": {"measure": "sku_count"},
                "measures": {"sku_count": {"grain": "product"}},
            },
            api_key="ov_test_climb_01",
        )
    gen = posts[0]["json"]
    assert gen["model_preference"] == FREEROUTE_PREFERENCE
    assert gen["intent_slots"]["measure"] == "sku_count"
    assert "LIVE_KEY" not in str(gen)
    assert ":5000" not in str(gen)
    assert "api_key" not in gen


def test_generate_sql_still_fetches_ranking() -> None:
    class _Gen:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "status": "ABSTAIN",
                "phase": "generate",
                "generative": {"ok": True, "sql": "SELECT sku FROM inventory"},
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

        def post(self, *_a: Any, **_k: Any) -> _Gen:
            return _Gen()

        def get(self, *_a: Any, **_k: Any) -> _Onto:
            return _Onto()

    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_query(
            "http://127.0.0.1:8010", question="how many skus?", api_key="fake-key01-test-token"
        )
    assert out is not None
    assert str(out.get("query_sql") or "").upper().startswith("SELECT")
    metrics = (out.get("ontology") or {}).get("metrics") or []
    assert metrics and metrics[0]["id"] == "sku_count"
    assert out.get("plan_source") == "ontology_plan"
    assert "LIVE_KEY" not in str(out)
    assert ":5000" not in str(out)
