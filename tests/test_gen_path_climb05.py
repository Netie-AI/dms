"""GEN-PATH-CLIMB-05: certified synonyms beyond frozen 17/26, WRONG=0.

Does not stamp COMPLETE. Live ontology_plan>17 is Platform after deploy.
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
from dms_executor.demo_pack import PACK_METRICS
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from dms_executor.semantic_retrieve import intent_slots, load_measure_aliases

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import (  # noqa: E402
    CLIMB05_LEFTOVER_L0,
    QUALIFIED_GEN_COVERAGE_CLAIM,
    _leftover_l0_unscored,
    build_gen_path_prove_report,
    classify_plan_source,
    load_pack,
)

# Frozen #210 / #212 KEEP_HOLD set. 17 L0s; remaining 9 of 26 are traps.
FROZEN_17: tuple[tuple[str, str], ...] = (
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

# Cortex certified_queries.yaml synonyms. Not PACK_METRICS. Not last_audit_date.
SYNONYM_L0: tuple[tuple[str, str], ...] = (
    ("cq_sku_count_syn_short", "How many SKUs in inventory?"),
    ("cq_sku_count_syn_label", "SKU count in inventory"),
    ("cq_sales_top5_syn_skus", "Top 5 SKUs by revenue"),
    ("cq_top3_category_syn_value", "top 3 categories by sales value"),
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
            run_id="run_climb05",
            output={"rows": rows},
        )

    return _run


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(entry_id="led_climb05", hash="hash_climb05_not_entry")


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
    db = tmp_path / "climb05.duckdb"
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


def _hits(tmp_path: Path, pairs: tuple[tuple[str, str], ...]) -> int:
    n = 0
    for _qid, question in pairs:
        env = _env(tmp_path, question, "sku_count")
        assert env is not None
        src = classify_plan_source(env)
        ok = env["badge"] == "L2_VALIDATED" and src == "ontology_plan"
        if ok:
            n += 1
            assert env["abstained"] is False
            assert env.get("rows")
            assert_envelope_valid(env)
    return n


def test_frozen_17_is_saturated_without_leftover_asks(tmp_path: Path) -> None:
    """#212 diagnosis: scoring the frozen 17 L0s stays at 17."""
    assert len(FROZEN_17) == 17
    assert _hits(tmp_path, FROZEN_17) == 17


def test_certified_synonyms_raise_above_17_without_audit(tmp_path: Path) -> None:
    """Rise does not depend on cq_audit_overdue / last_audit_date."""
    frozen = _hits(tmp_path, FROZEN_17)
    syn = _hits(tmp_path, SYNONYM_L0)
    assert frozen == 17
    assert syn == len(SYNONYM_L0)
    assert frozen + syn > 17
    cases = [
        {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
        for qid, _q in (*FROZEN_17, *SYNONYM_L0)
    ]
    n = 26 + len(SYNONYM_L0)
    report = build_gen_path_prove_report(
        {
            "OK": frozen + syn,
            "LAYER": 0,
            "ABSTAIN": n - (frozen + syn),
            "WRONG": 0,
        },
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
    assert report["by_plan_source"]["ontology_plan"]["answered"] > 17
    assert report["by_plan_source"]["bind_plan"]["answered"] == 0
    assert report["passed_wrong_zero"] is True


def test_leftover_ids_are_in_pack_and_not_pack_metrics() -> None:
    pack = load_pack(
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "curated_ceo"
        / "questions.yaml"
    )
    ids = [str(c["id"]) for c in pack["questions"]]
    for qid in CLIMB05_LEFTOVER_L0:
        assert qid in ids, qid
    assert len(ids) > int(QUALIFIED_GEN_COVERAGE_CLAIM["n"])
    by_id = {str(c["id"]): c for c in pack["questions"]}
    pack_qs = {_norm(m.question) for m in PACK_METRICS}
    for qid, question in SYNONYM_L0:
        assert by_id[qid]["expect"] == "l0"
        assert _norm(str(by_id[qid]["question"])) == _norm(question)
        assert _norm(question) not in pack_qs, qid
    assert not _leftover_l0_unscored([{"id": qid} for qid in ids])
    assert _leftover_l0_unscored([{"id": "cq_sku_count"}]) == list(CLIMB05_LEFTOVER_L0)


def _norm(question: str) -> str:
    return " ".join(question.casefold().split())


def test_categories_synonym_locks_outbound_not_sku() -> None:
    q = "top 3 categories by sales value"
    slots = intent_slots(q, None)
    assert slots.get("measure") == "outbound_value_myr"
    aliases = load_measure_aliases()
    allowed = {
        "sku_count",
        "outbound_value_myr",
        "stock_value_myr",
        "audit_overdue",
    }
    specs = {
        "outbound_value_myr": "outbound sales value",
        "sku_count": "count of unique SKUs",
    }
    pack = overlay_pack_id_from_question(
        q,
        prefer="outbound_value_myr",
        aliases=aliases,
        allowed=allowed,
        specs=specs,
    )
    assert pack is not None
    assert aliases[pack] == "outbound_value_myr"
    plan = query_plan_from_insights_ranking(
        {"ontology": {"metrics": [{"id": "sku_count"}]}},
        allowed,
        aliases=aliases,
        specs=specs,
        prefer="outbound_value_myr",
        question=q,
    )
    assert plan is not None
    assert plan["query_plan"]["measure"] == "outbound_value_myr"
    assert plan["plan_source"] == "ontology_plan"


def test_sku_synonym_locks_sku_count() -> None:
    for q in ("How many SKUs in inventory?", "SKU count in inventory"):
        slots = intent_slots(q, None)
        assert slots.get("measure") == "sku_count", q


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


def test_retry_uses_synonym_slots_not_sku_count() -> None:
    posts: list[dict[str, Any]] = []
    question = "Top 5 SKUs by revenue"

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
                        "AS sales_value_myr FROM transactions "
                        "WHERE txn_type IN ('OUT', 'outbound') "
                        "GROUP BY sku ORDER BY sales_value_myr DESC LIMIT 5"
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
            "outbound_value_myr": {
                "grain": "transaction",
                "description": "outbound sales value",
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
            question=question,
            ontology=onto,
            api_key="ov_test_climb_05",
        )
    gens = [p for p in posts if p["url"].endswith("/v1/insights")]
    assert len(gens) == 2
    retry = gens[1]["json"]
    assert retry["model_preference"] == FREEROUTE_PREFERENCE
    assert retry["generate_retry"] == "ranked_slots"
    assert retry["query_plan"]["measure"] == "outbound_value_myr"
    assert "LIVE_KEY" not in str(gens)
    assert ":5000" not in str(gens)
    assert out is not None
    assert str(out.get("query_sql") or "").upper().startswith("SELECT")
    assert out.get("plan_source") == "ontology_plan"
