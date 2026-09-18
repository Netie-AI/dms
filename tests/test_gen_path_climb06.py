"""GEN-PATH-CLIMB-06: dual-flat 17/26 root-cause + rise L0s, WRONG=0.

Does not stamp COMPLETE. Live ontology_plan>17 is Platform after deploy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cortex_client.compute import FREEROUTE_PREFERENCE, compute_query
from dms_api.app import create_app
from dms_api.routes.health import GEN_PATH_CLIMB
from dms_api.settings import get_settings
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import (  # noqa: E402
    CLIMB06_RISE_IDS,
    CLIMB06_RISE_L0,
    QUALIFIED_GEN_COVERAGE_CLAIM,
    build_gen_path_prove_report,
    classify_plan_source,
    leftover_rise_not_ontology,
    live_climb_gate,
    load_pack,
    merge_pack_questions,
)
from test_gen_path_climb05 import FROZEN_17, SYNONYM_L0  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
PLANTED_9: tuple[str, ...] = (
    "ops_spend_boundary",
    "trap_last_month",
    "trap_short_paraphrase",
    "trap_alerts_ungranted",
    "trap_high_risk_pending",
    "ops_supplier_rank_boundary",
    "trap_delayed_count",
    "trap_stock_by_bin",
    "trap_how_full_synonym",
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
            run_id="run_climb06",
            output={"rows": rows},
        )

    return _run


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(entry_id="led_climb06", hash="hash_climb06_not_entry")


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
    db = tmp_path / "climb06.duckdb"
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


def test_frozen_26_structurally_caps_at_17() -> None:
    """Remaining 9/26 are planted traps. Greening them is WRONG."""
    pack = load_pack(PACK)
    by_id = {str(c["id"]): c for c in pack["questions"]}
    assert len(FROZEN_17) == 17
    for qid in PLANTED_9:
        assert by_id[qid]["expect"] in {"abstain", "refuse"}, qid
    assert len(PLANTED_9) == 9
    assert len(FROZEN_17) + len(PLANTED_9) == int(QUALIFIED_GEN_COVERAGE_CLAIM["n"])


def test_dual_flat_was_unasked_rise_l0s(tmp_path: Path) -> None:
    """#214 diagnosis completed: 3/4 synonyms already compile on #210 locks."""
    frozen = _hits(tmp_path, FROZEN_17)
    syn = _hits(tmp_path, SYNONYM_L0)
    assert frozen == 17
    assert syn == len(SYNONYM_L0)
    assert frozen + syn > 17


def test_merge_injects_rise_l0s_into_frozen_26() -> None:
    frozen = [
        {"id": f"q{i}", "space": "finance", "expect": "abstain", "question": "x"}
        for i in range(26)
    ]
    merged = merge_pack_questions(frozen)
    ids = [str(c["id"]) for c in merged]
    for qid in CLIMB06_RISE_IDS:
        assert qid in ids, qid
    assert len(merged) > 26
    assert leftover_rise_not_ontology([{"id": qid} for qid in ids]) == list(
        CLIMB06_RISE_IDS
    )


def test_live_gate_fails_flat_17_even_if_rise_qids_asked() -> None:
    """#214 leftover check only required ids in cases. Asked+ABSTAIN stayed 17."""
    cases = [
        {"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"}
        for i in range(17)
    ] + [
        {"id": qid, "verdict": "ABSTAIN", "plan_source": "other"}
        for qid in CLIMB06_RISE_IDS
    ]
    n = 26 + len(CLIMB06_RISE_IDS)
    cases += [
        {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
        for i in range(n - len(cases))
    ]
    report = build_gen_path_prove_report(
        {"OK": 17, "LAYER": 0, "ABSTAIN": n - 17, "WRONG": 0},
        cases=cases,
        mode="live",
    )
    why = live_climb_gate(report)
    assert why is not None
    assert "rise L0s not ontology_plan" in why
    assert report["by_plan_source"]["ontology_plan"]["answered"] == 17
    blob = json.dumps(report)
    assert "COMPLETE" not in blob
    assert "99.95" not in blob


def test_live_gate_passes_when_rise_l0s_are_ontology_plan() -> None:
    cases = [
        {"id": qid, "verdict": "OK", "plan_source": "ontology_plan"}
        for qid, _q in (*FROZEN_17, *SYNONYM_L0)
    ]
    n = 26 + len(SYNONYM_L0)
    cases += [
        {"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"}
        for i in range(n - len(cases))
    ]
    report = build_gen_path_prove_report(
        {
            "OK": 17 + len(SYNONYM_L0),
            "LAYER": 0,
            "ABSTAIN": n - (17 + len(SYNONYM_L0)),
            "WRONG": 0,
        },
        cases=cases,
        mode="live",
    )
    assert live_climb_gate(report) is None
    assert report["by_plan_source"]["ontology_plan"]["answered"] > 17
    assert report["passed_wrong_zero"] is True


def test_health_advertises_climb_identity() -> None:
    get_settings.cache_clear()
    client = TestClient(create_app())
    body = client.get("/health").json()
    climb = body["gen_path_climb"]
    assert climb["issue"] == 216
    assert climb["frozen_n"] == 26
    assert climb["n"] == 31
    assert climb["rise_l0"] == list(CLIMB06_RISE_IDS)
    assert GEN_PATH_CLIMB["rise_l0"] == list(CLIMB06_RISE_IDS)
    pack = load_pack(PACK)
    assert len(pack["questions"]) == climb["n"]
    for qid in climb["rise_l0"]:
        assert qid in {str(c["id"]) for c in pack["questions"]}


def test_rise_questions_are_not_pack_metrics() -> None:
    from dms_executor.demo_pack import PACK_METRICS

    pack_qs = {" ".join(m.question.casefold().split()) for m in PACK_METRICS}
    for case in CLIMB06_RISE_L0:
        qn = " ".join(str(case["question"]).casefold().split())
        assert qn not in pack_qs, case["id"]
        assert case["expect"] == "l0"


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
    question = "How many SKUs in inventory?"

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
                    "sql": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
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
        },
        "measure_aliases": {"cq_sku_count": "sku_count"},
        "intent_slots": {"measure": "sku_count"},
    }
    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_query(
            "http://127.0.0.1:8010",
            question=question,
            ontology=onto,
            api_key="ov_test_climb_06",
        )
    gens = [p for p in posts if p["url"].endswith("/v1/insights")]
    assert len(gens) == 2
    assert gens[1]["json"]["model_preference"] == FREEROUTE_PREFERENCE
    assert "LIVE_KEY" not in str(gens)
    assert ":5000" not in str(gens)
    assert out is not None
    assert str(out.get("query_sql") or "").upper().startswith("SELECT")
    assert out.get("plan_source") == "ontology_plan"
