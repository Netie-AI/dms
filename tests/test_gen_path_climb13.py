"""GEN-PATH-CLIMB-13: unused parent-SQL leftover past ontology_plan=39, WRONG=0.

Does not stamp COMPLETE. Live ontology_plan>39 is Platform after deploy.
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
    query_plan_from_insights_ranking,
)
from dms_api.app import create_app
from dms_api.routes.health import GEN_PATH_CLIMB
from dms_api.settings import get_settings
from dms_executor.demo_pack import PACK_METRICS
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.semantic_retrieve import intent_slots, load_measure_aliases
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import (  # noqa: E402
    CLIMB06_RISE_IDS,
    CLIMB07_RISE_IDS,
    CLIMB08_RISE_IDS,
    CLIMB09_RISE_IDS,
    CLIMB10_RISE_IDS,
    CLIMB11_RISE_IDS,
    CLIMB12_RISE_IDS,
    CLIMB13_RISE_IDS,
    CLIMB13_RISE_L0,
    QUALIFIED_GEN_COVERAGE_CLAIM,
    build_gen_path_prove_report,
    classify_plan_source,
    leftover_ids_not_ontology,
    leftover_rise_not_ontology,
    live_climb_gate,
    load_pack,
    merge_pack_questions,
)
from test_gen_path_climb05 import (  # noqa: E402
    FROZEN_17,
    GRAIN_GUARDED,
    SYNONYM_L0,
    assert_grain_abstain,
    guarded_ids,
    honest,
)
from test_gen_path_climb07 import CLIMB07_SYNONYM_L0  # noqa: E402
from test_gen_path_climb08 import CLIMB08_SYNONYM_L0  # noqa: E402
from test_gen_path_climb09 import CLIMB09_SYNONYM_L0  # noqa: E402
from test_gen_path_climb10 import CLIMB10_SYNONYM_L0  # noqa: E402
from test_gen_path_climb11 import CLIMB11_SYNONYM_L0  # noqa: E402
from test_gen_path_climb12 import CLIMB12_SYNONYM_L0  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"

CLIMB13_SYNONYM_L0: tuple[tuple[str, str], ...] = (
    ("cq_sku_count_by_category_per", "how many SKUs per category"),
    ("cq_stock_value_worth", "what is our inventory worth per category"),
    ("ops_freight_spend_destination", "freight spend per destination"),
)

OPS_GRANT = {"locations", "inventory", "shipments"}
_PACK_METRIC_IDS = {m.metric_id for m in PACK_METRICS}


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
            run_id="run_climb13",
            output={"rows": rows},
        )

    return _run


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(entry_id="led_climb13", hash="hash_climb13_not_entry")


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
    db = tmp_path / "climb13.duckdb"
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


def _hits(
    tmp_path: Path,
    pairs: tuple[tuple[str, str], ...],
    *,
    grantable: set[str] | None = None,
) -> int:
    n = 0
    for qid, question in pairs:
        tables = OPS_GRANT if qid.startswith("ops_") else grantable
        env = _env(tmp_path, question, "sku_count", grantable=tables)
        assert env is not None
        if qid in GRAIN_GUARDED:
            assert_grain_abstain(env)
            continue
        src = classify_plan_source(env)
        ok = env["badge"] == "L2_VALIDATED" and src == "ontology_plan"
        if ok:
            n += 1
            assert env["abstained"] is False
            assert env.get("rows")
            assert_envelope_valid(env)
    return n


def test_n49_structurally_caps_at_39() -> None:
    """39/49 = frozen 17 + climb-06 4 + 07-12 3 each."""
    pack = load_pack(PACK)
    ids = [str(c["id"]) for c in pack["questions"]]
    assert len(FROZEN_17) == 17
    assert len(SYNONYM_L0) == 4
    assert len(CLIMB07_SYNONYM_L0) == 3
    assert len(CLIMB08_SYNONYM_L0) == 3
    assert len(CLIMB09_SYNONYM_L0) == 3
    assert len(CLIMB10_SYNONYM_L0) == 3
    assert len(CLIMB11_SYNONYM_L0) == 3
    assert len(CLIMB12_SYNONYM_L0) == 3
    assert len(ids) > 49
    assert len(ids) > int(QUALIFIED_GEN_COVERAGE_CLAIM["n"])


def test_unused_certified_leftover_raises_above_39(tmp_path: Path) -> None:
    frozen = _hits(tmp_path, FROZEN_17)
    syn06 = _hits(tmp_path, SYNONYM_L0)
    syn07 = _hits(tmp_path, CLIMB07_SYNONYM_L0)
    syn08 = _hits(tmp_path, CLIMB08_SYNONYM_L0)
    syn09 = _hits(tmp_path, CLIMB09_SYNONYM_L0)
    syn10 = _hits(tmp_path, CLIMB10_SYNONYM_L0)
    syn11 = _hits(tmp_path, CLIMB11_SYNONYM_L0)
    syn12 = _hits(tmp_path, CLIMB12_SYNONYM_L0)
    syn13 = _hits(tmp_path, CLIMB13_SYNONYM_L0)
    assert frozen == honest(FROZEN_17)
    assert syn06 == honest(SYNONYM_L0)
    assert syn07 == honest(CLIMB07_SYNONYM_L0)
    assert syn08 == honest(CLIMB08_SYNONYM_L0)
    assert syn09 == honest(CLIMB09_SYNONYM_L0)
    assert syn10 == honest(CLIMB10_SYNONYM_L0)
    assert syn11 == honest(CLIMB11_SYNONYM_L0)
    assert syn12 == honest(CLIMB12_SYNONYM_L0)
    assert syn13 == honest(CLIMB13_SYNONYM_L0)
    prior = frozen + syn06 + syn07 + syn08 + syn09 + syn10 + syn11 + syn12
    assert prior == honest(
        FROZEN_17,
        SYNONYM_L0,
        CLIMB07_SYNONYM_L0,
        CLIMB08_SYNONYM_L0,
        CLIMB09_SYNONYM_L0,
        CLIMB10_SYNONYM_L0,
        CLIMB11_SYNONYM_L0,
        CLIMB12_SYNONYM_L0,
    )
    assert prior + syn13 > prior
    cases = [
        (
            {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
            if qid in GRAIN_GUARDED
            else {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
        )
        for qid, _q in (
            *FROZEN_17,
            *SYNONYM_L0,
            *CLIMB07_SYNONYM_L0,
            *CLIMB08_SYNONYM_L0,
            *CLIMB09_SYNONYM_L0,
            *CLIMB10_SYNONYM_L0,
            *CLIMB11_SYNONYM_L0,
            *CLIMB12_SYNONYM_L0,
            *CLIMB13_SYNONYM_L0,
        )
    ]
    n = (
        26
        + len(SYNONYM_L0)
        + len(CLIMB07_SYNONYM_L0)
        + len(CLIMB08_SYNONYM_L0)
        + len(CLIMB09_SYNONYM_L0)
        + len(CLIMB10_SYNONYM_L0)
        + len(CLIMB11_SYNONYM_L0)
        + len(CLIMB12_SYNONYM_L0)
        + len(CLIMB13_SYNONYM_L0)
    )
    ok = prior + syn13
    ok_hits = ok
    report = build_gen_path_prove_report(
        {"OK": ok, "LAYER": 0, "ABSTAIN": n - ok, "WRONG": 0},
        cases=cases
        + [
            {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
            for i in range(n - len(cases))
        ],
        mode="offline",
    )
    blob = json.dumps(report)
    assert "COMPLETE" not in blob
    assert "99.95" not in blob
    assert report["by_plan_source"]["ontology_plan"]["answered"] == ok_hits
    assert report["by_plan_source"]["bind_plan"]["answered"] == 0
    assert report["passed_wrong_zero"] is True
    assert leftover_rise_not_ontology(cases) == []
    assert leftover_ids_not_ontology(cases, CLIMB07_RISE_IDS) == guarded_ids(CLIMB07_RISE_IDS)
    assert leftover_ids_not_ontology(cases, CLIMB08_RISE_IDS) == guarded_ids(CLIMB08_RISE_IDS)
    assert leftover_ids_not_ontology(cases, CLIMB09_RISE_IDS) == guarded_ids(CLIMB09_RISE_IDS)
    assert leftover_ids_not_ontology(cases, CLIMB10_RISE_IDS) == guarded_ids(CLIMB10_RISE_IDS)
    assert leftover_ids_not_ontology(cases, CLIMB11_RISE_IDS) == guarded_ids(CLIMB11_RISE_IDS)
    assert leftover_ids_not_ontology(cases, CLIMB12_RISE_IDS) == guarded_ids(CLIMB12_RISE_IDS)
    assert leftover_ids_not_ontology(cases, CLIMB13_RISE_IDS) == guarded_ids(CLIMB13_RISE_IDS)
    why = live_climb_gate(report)
    assert why is not None
    # GRAIN-GUARD-01 round 3: ops_chemicals_list (climb-09 rise) answered
    # "list chemicals" with a stock-value figure (oracle-WRONG); it abstains
    # named now (no trim), so the live gate fails on climb-09 itself.
    assert "climb-09 rise L0s not ontology_plan" in why, why
    assert "ops_chemicals_list" in why, why


def test_merge_injects_climb13_into_n49() -> None:
    prior49 = [
        {"id": f"q{i}", "space": "finance", "expect": "abstain", "question": "x"}
        for i in range(49)
    ]
    merged = merge_pack_questions(prior49)
    ids = [str(c["id"]) for c in merged]
    for qid in CLIMB13_RISE_IDS:
        assert qid in ids, qid
    assert len(merged) > 49
    assert leftover_ids_not_ontology(
        [{"id": qid} for qid in ids], CLIMB13_RISE_IDS
    ) == list(CLIMB13_RISE_IDS)


def test_live_gate_fails_flat_39_even_if_climb12_hits() -> None:
    cases = [
        {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
        for qid, _q in (
            *FROZEN_17,
            *SYNONYM_L0,
            *CLIMB07_SYNONYM_L0,
            *CLIMB08_SYNONYM_L0,
            *CLIMB09_SYNONYM_L0,
            *CLIMB10_SYNONYM_L0,
            *CLIMB11_SYNONYM_L0,
            *CLIMB12_SYNONYM_L0,
        )
    ] + [
        {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
        for qid in CLIMB13_RISE_IDS
    ]
    n = 49 + len(CLIMB13_RISE_IDS)
    cases += [
        {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
        for i in range(n - len(cases))
    ]
    report = build_gen_path_prove_report(
        {"OK": 39, "LAYER": 0, "ABSTAIN": n - 39, "WRONG": 0},
        cases=cases,
        mode="live",
    )
    why = live_climb_gate(report)
    assert why is not None
    assert "climb-13 rise L0s not ontology_plan" in why
    assert report["by_plan_source"]["ontology_plan"]["answered"] == 39
    blob = json.dumps(report)
    assert "COMPLETE" not in blob
    assert "99.95" not in blob


def test_live_gate_passes_when_climb13_rise_is_ontology_plan() -> None:
    cases = [
        {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
        for qid, _q in (
            *FROZEN_17,
            *SYNONYM_L0,
            *CLIMB07_SYNONYM_L0,
            *CLIMB08_SYNONYM_L0,
            *CLIMB09_SYNONYM_L0,
            *CLIMB10_SYNONYM_L0,
            *CLIMB11_SYNONYM_L0,
            *CLIMB12_SYNONYM_L0,
            *CLIMB13_SYNONYM_L0,
        )
    ]
    n = (
        26
        + len(SYNONYM_L0)
        + len(CLIMB07_SYNONYM_L0)
        + len(CLIMB08_SYNONYM_L0)
        + len(CLIMB09_SYNONYM_L0)
        + len(CLIMB10_SYNONYM_L0)
        + len(CLIMB11_SYNONYM_L0)
        + len(CLIMB12_SYNONYM_L0)
        + len(CLIMB13_SYNONYM_L0)
    )
    cases += [
        {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
        for i in range(n - len(cases))
    ]
    ok = (
        17
        + len(SYNONYM_L0)
        + len(CLIMB07_SYNONYM_L0)
        + len(CLIMB08_SYNONYM_L0)
        + len(CLIMB09_SYNONYM_L0)
        + len(CLIMB10_SYNONYM_L0)
        + len(CLIMB11_SYNONYM_L0)
        + len(CLIMB12_SYNONYM_L0)
        + len(CLIMB13_SYNONYM_L0)
    )
    report = build_gen_path_prove_report(
        {"OK": ok, "LAYER": 0, "ABSTAIN": n - ok, "WRONG": 0},
        cases=cases,
        mode="live",
    )
    assert live_climb_gate(report) is None
    assert report["by_plan_source"]["ontology_plan"]["answered"] > 39
    assert leftover_rise_not_ontology(cases) == []
    assert leftover_ids_not_ontology(cases, CLIMB13_RISE_IDS) == []


def test_health_advertises_climb13_identity() -> None:
    get_settings.cache_clear()
    client = TestClient(create_app())
    body = client.get("/health").json()
    climb = body["gen_path_climb"]
    assert climb["issue"] == 231
    assert climb["ticket"] == "GEN-PATH-CLIMB-13"
    assert climb["frozen_n"] == 26
    assert climb["prior_n"] == 49
    assert climb["n"] == 52
    assert climb["rise_l0"] == list(CLIMB13_RISE_IDS)
    assert GEN_PATH_CLIMB["rise_l0"] == list(CLIMB13_RISE_IDS)
    pack = load_pack(PACK)
    assert len(pack["questions"]) == climb["n"]
    for qid in (
        *CLIMB06_RISE_IDS,
        *CLIMB07_RISE_IDS,
        *CLIMB08_RISE_IDS,
        *CLIMB09_RISE_IDS,
        *CLIMB10_RISE_IDS,
        *CLIMB11_RISE_IDS,
        *CLIMB12_RISE_IDS,
        *CLIMB13_RISE_IDS,
    ):
        assert qid in {str(c["id"]) for c in pack["questions"]}


def test_rise_questions_are_not_pack_expansion() -> None:
    """Parent-SQL leftover of Cortex certified L0s. PACK_METRICS is unchanged.

    Prove-path is generative (skips pack). Same parent SQL as live L0s.
    """
    assert _PACK_METRIC_IDS == {
        "spend_by_country",
        "stock_value_by_category",
        "total_spend",
        "cq_capacity_utilisation",
        "cq_low_stock_wh_a",
        "cq_cost_by_destination",
        "cq_cold_storage",
        "cq_capacity_above_90",
        "cq_expired_items",
        "cq_cctv_wh_a",
    }
    spaces = {str(c["id"]): c["space"] for c in CLIMB13_RISE_L0}
    assert spaces["cq_sku_count_by_category_per"] == "finance"
    assert spaces["cq_stock_value_worth"] == "finance"
    assert spaces["ops_freight_spend_destination"] == "ops"
    for case in CLIMB13_RISE_L0:
        assert case["expect"] == "l0"
        assert str(case["id"]) not in _PACK_METRIC_IDS


def test_per_category_sku_locks_sku_group() -> None:
    q = "how many SKUs per category"
    slots = intent_slots(q, None)
    assert slots.get("measure") == "sku_count"
    assert slots.get("group_by") == [["product", "category"]]
    aliases = load_measure_aliases()
    allowed = {
        "sku_count",
        "outbound_value_myr",
        "stock_value_myr",
        "shipping_cost_myr",
        "audit_overdue",
    }
    specs = {
        "sku_count": "count of unique SKUs",
        "stock_value_myr": "on-hand stock value",
    }
    pack = overlay_pack_id_from_question(
        q,
        prefer="sku_count",
        aliases=aliases,
        allowed=allowed,
        specs=specs,
    )
    assert pack is not None
    assert aliases[pack] == "sku_count"
    assert "category" in pack


def test_inventory_worth_locks_stock_not_sku() -> None:
    q = "what is our inventory worth per category"
    slots = intent_slots(q, None)
    assert slots.get("measure") == "stock_value_myr"
    assert slots.get("group_by") == [["product", "category"]]
    aliases = load_measure_aliases()
    allowed = {
        "sku_count",
        "outbound_value_myr",
        "stock_value_myr",
        "shipping_cost_myr",
        "audit_overdue",
    }
    specs = {
        "stock_value_myr": "on-hand stock value",
        "sku_count": "count of unique SKUs",
    }
    pack = overlay_pack_id_from_question(
        q,
        prefer="stock_value_myr",
        aliases=aliases,
        allowed=allowed,
        specs=specs,
    )
    assert pack is not None
    assert aliases[pack] == "stock_value_myr"
    plan = query_plan_from_insights_ranking(
        {"ontology": {"metrics": [{"id": "sku_count"}]}},
        allowed,
        aliases=aliases,
        specs=specs,
        prefer="stock_value_myr",
        question=q,
    )
    assert plan is not None
    assert plan["query_plan"]["measure"] == "stock_value_myr"
    assert plan["plan_source"] == "ontology_plan"
    ranked_id = str(plan["query_plan"].get("ranked_id") or "")
    assert "sku_count" not in ranked_id


def test_freight_spend_locks_shipping_not_sku() -> None:
    q = "freight spend per destination"
    slots = intent_slots(q, None)
    assert slots.get("measure") == "shipping_cost_myr"
    assert slots.get("group_by") == [["location", "location_code"]]
    aliases = load_measure_aliases()
    allowed = {
        "sku_count",
        "outbound_value_myr",
        "stock_value_myr",
        "shipping_cost_myr",
        "audit_overdue",
    }
    specs = {
        "shipping_cost_myr": "shipment cost / freight billed",
        "sku_count": "count of unique SKUs",
    }
    pack = overlay_pack_id_from_question(
        q,
        prefer="shipping_cost_myr",
        aliases=aliases,
        allowed=allowed,
        specs=specs,
    )
    assert pack is not None
    assert aliases[pack] == "shipping_cost_myr"
    plan = query_plan_from_insights_ranking(
        {"ontology": {"metrics": [{"id": "sku_count"}]}},
        allowed,
        aliases=aliases,
        specs=specs,
        prefer="shipping_cost_myr",
        question=q,
    )
    assert plan is not None
    assert plan["query_plan"]["measure"] == "shipping_cost_myr"
    assert plan["plan_source"] == "ontology_plan"
    ranked_id = str(plan["query_plan"].get("ranked_id") or "")
    assert "sku_count" not in ranked_id


def test_leftover_l0s_compile_on_granted_tables(tmp_path: Path) -> None:
    env = _env(tmp_path, "how many SKUs per category", "sku_count")
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert classify_plan_source(env) == "ontology_plan"
    env = _env(tmp_path, "what is our inventory worth per category", "sku_count")
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert classify_plan_source(env) == "ontology_plan"
    env = _env(
        tmp_path,
        "freight spend per destination",
        "sku_count",
        grantable=OPS_GRANT,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert classify_plan_source(env) == "ontology_plan"
    assert env.get("rows")
    assert_envelope_valid(env)


def test_ops_spend_and_rank_stay_abstain(tmp_path: Path) -> None:
    for q in (
        "What is our total spend by supplier country?",
        "Rank suppliers by combined risk and lead time score",
    ):
        env = _env(tmp_path, q, "sku_count", grantable=OPS_GRANT)
        assert env is not None
        assert env["badge"] == "ABSTAIN"
        assert env["abstained"] is True


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
        env = _env(tmp_path, q, "sku_count", "cq_sales_top5_value")
        assert env is not None
        assert env["badge"] == "ABSTAIN"
        assert env["abstained"] is True
        assert env.get("plan_source") != "bind_plan"


def test_retry_still_free_normal_not_second_vault() -> None:
    posts: list[dict[str, Any]] = []
    question = "what is our inventory worth per category"

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
                        "SELECT category, SUM(quantity_kg * unit_cost_myr) "
                        "AS total_value_myr FROM inventory GROUP BY category"
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
            "stock_value_myr": {
                "grain": "lot",
                "description": "on-hand stock value",
            },
        },
        "measure_aliases": {"inventory_worth_by_category": "stock_value_myr"},
        "intent_slots": {
            "measure": "stock_value_myr",
            "group_by": [["product", "category"]],
        },
    }
    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_query(
            "http://127.0.0.1:8010",
            question=question,
            ontology=onto,
            api_key="ov_test_climb_13",
        )
    gens = [p for p in posts if p["url"].endswith("/v1/insights")]
    assert len(gens) == 2
    retry = gens[1]["json"]
    assert retry["model_preference"] == FREEROUTE_PREFERENCE
    assert retry["generate_retry"] == "ranked_slots"
    assert retry["query_plan"]["measure"] == "stock_value_myr"
    assert "LIVE_KEY" not in str(gens)
    assert ":5000" not in str(gens)
    assert out is not None
    assert str(out.get("query_sql") or "").upper().startswith("SELECT")
    assert out.get("plan_source") == "ontology_plan"
