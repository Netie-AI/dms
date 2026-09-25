"""GEN-PATH-PROVE-01: plan_source from telemetry, not guessed.

Does not stamp COMPLETE. Does not invent live coverage. Platform owns live prove.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from cortex_client.compute import (
    FREEROUTE_PREFERENCE,
    attach_compute_plan_source,
    compute_query,
    insights_was_reached,
    normalize_insights_compute,
    query_plan_from_insights_ranking,
)
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import Ontology
from dms_executor.semantic_retrieve import bind_plan

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_curated import (  # noqa: E402
    EXIT_CONFIG,
    HOLD_MAY_CLEAR_FIELD,
    QUALIFIED_GEN_COVERAGE_CLAIM,
    build_gen_path_prove_report,
    classify_plan_source,
    main,
    self_check,
)


def _ontology() -> Ontology:
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    o.add_object("lot", "lots", ["lot_id"])
    o.add_object("region", "regions", ["region"])
    o.add_object(
        "product",
        "(SELECT sku, ANY_VALUE(category) AS category FROM lots GROUP BY sku)",
        ["sku"],
    )
    o.add_link("sale_of_lot", "sale", ["sku"], "lot", ["sku"])
    o.add_link("sale_of_product", "sale", ["sku"], "product", ["sku"])
    o.add_link("sale_in_region", "sale", ["region"], "region", ["region"])
    o.add_measure("revenue", "sale", "SUM(f.amount)")
    o.add_measure("sku_count", "product", "COUNT(*)")
    return o


def _seed(path: Path) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE lots (lot_id VARCHAR, sku VARCHAR, category VARCHAR, qty DOUBLE)"
        )
        con.execute(
            "INSERT INTO lots VALUES "
            "('L1','SKU-1','ALPHA',10),('L2','SKU-1','ALPHA',20),"
            "('L3','SKU-1','ALPHA',30),('L4','SKU-2','BETA',40)"
        )
        con.execute(
            "CREATE TABLE sales (txn_id VARCHAR, sku VARCHAR, region VARCHAR, amount DOUBLE)"
        )
        con.execute(
            "INSERT INTO sales VALUES ('T1','SKU-1','North',100),('T2','SKU-2','South',50)"
        )
        con.execute("CREATE TABLE regions (region VARCHAR, country VARCHAR)")
        con.execute("INSERT INTO regions VALUES ('North','MY'),('South','MY')")
    finally:
        con.close()


@pytest.fixture()
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "prove.duckdb"
    _seed(path)
    return path


@pytest.fixture()
def onto(warehouse: Path) -> Ontology:
    loaded = load_verified_ontology(warehouse, _ontology())
    assert loaded is not None
    return loaded


def _submit_ok(_sql: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        ok=True,
        status="ok",
        run_id="run_prove",
        output={"rows": [{"product_category": "ALPHA", "revenue": 100.0}]},
    )


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(entry_id="led_prove", hash="hash_prove_not_entry")


def test_classify_plan_source_does_not_guess_from_assumptions() -> None:
    env = {
        "route": "generated",
        "assumptions": ["compute_fallback:bind_plan", "GEN-01 ontology compile"],
        "sql_used": "SELECT revenue FROM sales",
        "badge": "L2_VALIDATED",
    }
    assert classify_plan_source(env) == "other"
    assert classify_plan_source({"plan_source": "ontology_plan"}) == "ontology_plan"
    assert classify_plan_source({"plan_source": "bind_plan"}) == "bind_plan"
    assert classify_plan_source(None) == "other"


def test_bind_plan_stamps_bind_plan() -> None:
    ctx = {
        "measures": {"revenue": {"grain": "sale", "description": ""}},
        "objects": {"product": {"key": ["sku"]}, "sale": {"key": ["txn_id"]}},
        "columns": {"product": ["sku", "category"], "sale": ["txn_id", "sku", "amount"]},
    }
    out = bind_plan("Top 5 selling SKUs by revenue", ctx)
    assert out is not None
    assert out["plan_source"] == "bind_plan"


def test_compute_client_stamps_ontology_plan_on_typed_plan() -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"query_plan": {"measure": "revenue", "group_by": []}}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(self, *_a: Any, **_k: Any) -> _Resp:
            return _Resp()

    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_query("http://127.0.0.1:8010", question="revenue?")
    assert out is not None
    assert out["plan_source"] == "ontology_plan"


def test_compute_client_posts_insights_generate_before_dms_query() -> None:
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
        out = compute_query(
            "http://127.0.0.1:8010",
            question="revenue by category?",
            api_key="ov_test_route_01",
        )
    assert out is None
    assert [p["url"] for p in posts] == [
        "http://127.0.0.1:8010/v1/insights",
        "http://127.0.0.1:8010/v1/insights/ontology",
        "http://127.0.0.1:8010/dms/query",
    ]
    gen = posts[0]["json"]
    assert gen["generate"] is True
    assert gen["ask"] is False
    assert gen["mode"] == "ontology_plan"
    assert gen["model_preference"] == FREEROUTE_PREFERENCE
    assert gen["consumer"] == "dms"
    assert "LIVE_KEY" not in str(gen)
    assert ":5000" not in str(gen)
    assert "api_key" not in gen
    headers = posts[0]["headers"] or {}
    assert headers["Authorization"] == "Bearer ov_test_route_01"
    assert "ov_test_route_01" not in str(gen)


def test_insights_generate_sql_is_ontology_plan_not_bind(
    onto: Ontology, warehouse: Path
) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            # GRAIN-GUARD-01: grouped by the category the question names.
            "query_sql": (
                'SELECT l."category" AS "product_category", SUM(s.amount) AS "revenue" '
                "FROM sales s LEFT JOIN (SELECT sku, ANY_VALUE(category) AS category "
                'FROM lots GROUP BY sku) l ON s."sku" = l."sku" GROUP BY l."category"'
            ),
            "plan_source": "ontology_plan",
            "values": [{"revenue": 999999}],
            "live_5000_ci": True,
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    assert env["rows"] == [{"product_category": "ALPHA", "revenue": 100.0}]
    assert "999999" not in str(env.get("values") or [])
    assert "live_5000_ci" not in env
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))
    assert_envelope_valid(env)


def test_insights_refuse_without_sql_is_miss_not_green_bind(
    onto: Ontology, warehouse: Path
) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {"ok": False, "status": "REFUSE", "values": []},
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=False,
    )
    assert env is None


def test_insights_unarmed_ranked_metric_is_ontology_plan_not_bind(
    onto: Ontology, warehouse: Path
) -> None:
    env = maybe_generative_ask(
        "How many SKUs do we have in inventory?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "ok": False,
            "status": "REFUSE",
            "phase": "generate",
            "generative": {"ok": False, "sql": None, "climb": {"final": "UNARMED"}},
            "ontology": {
                "ok": True,
                "metrics": [{"id": "sku_count", "importance": {"rank": 1}}],
            },
            "values": [],
            "live_5000_ci": True,
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))
    assert any("insights_ranking:ontology_plan" in str(a) for a in (env.get("assumptions") or []))
    assert_envelope_valid(env)
    report = build_gen_path_prove_report(
        {"OK": 1, "LAYER": 0, "ABSTAIN": 0, "WRONG": 0},
        cases=[{"id": "cq_sku_count", "verdict": "OK", "plan_source": classify_plan_source(env)}],
        mode="offline",
    )
    assert report["by_plan_source"]["ontology_plan"]["answered"] >= 1
    assert "COMPLETE" not in json.dumps(report)


def test_insights_unarmed_without_ranked_measure_does_not_bind(
    onto: Ontology, warehouse: Path
) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "ok": False,
            "status": "REFUSE",
            "phase": "generate",
            "generative": {"ok": False, "climb": {"final": "UNARMED"}},
            "ontology": {"ok": True, "metrics": [{"id": "stock_value_by_category"}]},
            "values": [],
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env.get("plan_source") != "bind_plan"
    assert not any("compute_fallback:bind_plan" in str(a) for a in (env.get("assumptions") or []))


def test_insights_refused_generate_sql_is_not_executed(
    onto: Ontology, warehouse: Path
) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "ok": False,
            "status": "REFUSE",
            "phase": "generate",
            "generative": {
                "ok": False,
                "valid": False,
                "sql": "SELECT secret FROM payroll",
            },
            "values": [],
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env.get("sql_used") is None


def test_compute_client_401_is_insights_reached_not_transport_miss() -> None:
    class _Resp:
        status_code = 401

        def json(self) -> dict[str, Any]:
            return {
                "ok": False,
                "status": "REFUSE",
                "refused": "needs its own OpenVault ov_ key",
                "values": [],
            }

    class _Onto:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "ok": True,
                "phase": "ontology",
                "ontology": {
                    "ok": True,
                    "metrics": [{"id": "sku_count", "importance": {"rank": 1}}],
                },
            }

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(self, *_a: Any, **_k: Any) -> _Resp:
            return _Resp()

        def get(self, *_a: Any, **_k: Any) -> _Onto:
            return _Onto()

    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_query(
            "http://127.0.0.1:8010",
            question="how many skus?",
            api_key="dms-demo-viewer-key",
        )
    assert out is not None
    assert insights_was_reached(out)
    onto = out.get("ontology") or {}
    metrics = onto.get("metrics") or []
    assert metrics and metrics[0]["id"] == "sku_count"
    assert out.get("plan_source") in {None, "", "other", "ontology_plan"}
    assert "LIVE_KEY" not in str(out)
    assert ":5000" not in str(out)


def test_ranking_requires_exact_measure_id() -> None:
    payload = {
        "status": "REFUSE",
        "ontology": {"metrics": [{"id": "sku_count"}]},
    }
    assert query_plan_from_insights_ranking(payload, {"sku_count"}) is not None
    assert query_plan_from_insights_ranking(payload, {"stock_value_myr"}) is None
    assert query_plan_from_insights_ranking(payload, set()) is not None
    skipped = {
        "ontology": {
            "metrics": [
                {"id": "stock_value_by_category"},
                {"id": "sku_count"},
            ]
        }
    }
    assert query_plan_from_insights_ranking(skipped, {"sku_count"}) is None


def test_hostile_insights_sql_abstains_not_l2(
    onto: Ontology, warehouse: Path
) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_sql": "INSERT INTO sales VALUES ('x')",
            "plan_source": "ontology_plan",
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env.get("plan_source") == "ontology_plan"


def test_dms_query_sql_used_without_typed_plan_is_not_ontology_plan() -> None:
    stamped = normalize_insights_compute(
        {"sql_used": "SELECT 1 AS n", "answer": "one", "badge": "governed_metric"}
    )
    assert stamped is None


def test_insights_generate_envelope_normalizes_to_query_sql() -> None:
    out = normalize_insights_compute(
        {
            "ok": True,
            "status": "ABSTAIN",
            "phase": "generate",
            "values": [{"n": 12}],
            "live_5000_ci": True,
            "generative": {
                "ok": True,
                "sql": "SELECT sku, SUM(amount) AS revenue FROM sales GROUP BY sku",
            },
            "sql_used": "SELECT sku, SUM(amount) AS revenue FROM sales GROUP BY sku",
        }
    )
    assert out is not None
    assert out["plan_source"] == "ontology_plan"
    assert out["query_sql"].upper().startswith("SELECT")
    assert out["values"] == []
    assert out["live_5000_ci"] is False


def test_prove_harness_counts_ontology_plan_when_cortex_path_works(
    onto: Ontology, warehouse: Path
) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]},
            "plan_source": "ontology_plan",
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env.get("plan_source") == "ontology_plan"
    report = build_gen_path_prove_report(
        {"OK": 1, "LAYER": 0, "ABSTAIN": 0, "WRONG": 0},
        cases=[{"id": "q1", "verdict": "OK", "plan_source": classify_plan_source(env)}],
        mode="offline",
    )
    assert report["by_plan_source"]["ontology_plan"]["answered"] >= 1
    assert report["by_plan_source"]["bind_plan"]["answered"] == 0
    assert "COMPLETE" not in json.dumps(report)


def test_compute_client_keeps_cortex_bind_plan_mode() -> None:
    stamped = attach_compute_plan_source(
        {"mode": "bind_plan", "query_plan": {"measure": "revenue"}}
    )
    assert stamped["plan_source"] == "bind_plan"
    stamped = attach_compute_plan_source(
        {"plan_source": "other", "query_plan": {"measure": "revenue"}}
    )
    assert stamped["plan_source"] == "other"


def test_unlabeled_compute_plan_is_other_not_guessed_ai(
    onto: Ontology, warehouse: Path
) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]}
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "other"
    assert_envelope_valid(env)


def test_stamped_ontology_plan_survives_on_envelope(
    onto: Ontology, warehouse: Path
) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]},
            "plan_source": "ontology_plan",
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "ontology_plan"
    assert_envelope_valid(env)


def test_hold_yes_only_on_majority_ontology_wrong_zero() -> None:
    yes = build_gen_path_prove_report(
        {"OK": 14, "LAYER": 0, "ABSTAIN": 12, "WRONG": 0},
        cases=(
            [{"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"} for i in range(14)]
            + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(12)]
        ),
        mode="offline",
    )
    blob = json.dumps(yes)
    assert "COMPLETE" not in blob
    assert "99.95" not in blob
    assert yes[HOLD_MAY_CLEAR_FIELD] == "YES"
    assert yes["phase_a_hold_may_clear"] == "YES"
    assert yes["passed_wrong_zero"] is True
    assert yes["by_plan_source"]["ontology_plan"]["answered"] == 14


def test_hold_no_when_bind_plan_is_the_answered_majority() -> None:
    no = build_gen_path_prove_report(
        {"OK": 15, "LAYER": 0, "ABSTAIN": 11, "WRONG": 0},
        cases=(
            [{"id": f"q{i}", "verdict": "OK", "plan_source": "bind_plan"} for i in range(15)]
            + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(11)]
        ),
        mode="offline",
    )
    assert no[HOLD_MAY_CLEAR_FIELD] == "NO"
    assert no["phase_a_hold_may_clear"] == "NO"
    assert no["passed_wrong_zero"] is True
    assert no["by_plan_source"]["bind_plan"]["pack_pct"] == 57.69
    assert no["qualified_claim"]["status"] == "QUALIFIED"


def test_hold_no_when_wrong() -> None:
    no = build_gen_path_prove_report(
        {"OK": 14, "LAYER": 0, "ABSTAIN": 11, "WRONG": 1},
        cases=(
            [{"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"} for i in range(14)]
            + [{"id": "w", "verdict": "WRONG", "plan_source": "ontology_plan"}]
            + [{"id": f"a{i}", "verdict": "ABSTAIN", "plan_source": "other"} for i in range(11)]
        ),
        mode="offline",
    )
    assert no[HOLD_MAY_CLEAR_FIELD] == "NO"
    assert no["passed_wrong_zero"] is False


def test_hold_no_when_pack_rebaselined() -> None:
    no = build_gen_path_prove_report(
        {"OK": 3, "LAYER": 0, "ABSTAIN": 0, "WRONG": 0},
        cases=[{"id": f"q{i}", "verdict": "OK", "plan_source": "ontology_plan"} for i in range(3)],
        mode="offline",
        pack="other_pack",
    )
    assert no[HOLD_MAY_CLEAR_FIELD] == "NO"
    assert "re-baselined" in no["phase_a_hold_may_clear_reason"]


def test_qualified_claim_is_not_a_live_measurement() -> None:
    q = QUALIFIED_GEN_COVERAGE_CLAIM
    assert q["status"] == "QUALIFIED"
    assert q["n"] == 26
    assert q["offline_ab_answered"] == 15
    assert q["offline_ab_answered_pct"] == 57.69
    assert round(100.0 * 15 / 26, 2) == 57.69


def test_offline_prove_path_is_bind_plan_not_cortex_ai() -> None:
    from score_curated import run_ab_curated

    ab = run_ab_curated()
    assert ab["wrong"] == 0
    answered = [
        row
        for row in ab["cases"]
        if row["generative"] in {"OK", "LAYER"}
    ]
    assert answered
    for row in answered:
        assert row["plan_source"] == "bind_plan", row
    prove_cases = [
        {
            "id": row["id"],
            "verdict": row["generative"],
            "plan_source": row["plan_source"],
        }
        for row in ab["cases"]
    ]
    gen = ab["generative"]
    report = build_gen_path_prove_report(
        {
            "OK": gen["ok"],
            "LAYER": gen["layer"],
            "ABSTAIN": gen["abstain"],
            "WRONG": gen["wrong"],
        },
        cases=prove_cases,
        mode="offline",
    )
    blob = json.dumps(report)
    assert "COMPLETE" not in blob
    assert report[HOLD_MAY_CLEAR_FIELD] == "NO"
    assert report["by_plan_source"]["ontology_plan"]["answered"] == 0
    assert report["by_plan_source"]["bind_plan"]["answered"] == gen["answered"]


def test_prove_path_climb_without_url_is_config(monkeypatch) -> None:
    monkeypatch.delenv("DMS_API_BASE", raising=False)
    monkeypatch.delenv("STUDIO_API_BASE", raising=False)
    monkeypatch.delenv("DMS_URL", raising=False)
    assert main(["--prove-path", "--climb"]) == EXIT_CONFIG


def test_self_check_covers_prove_plants() -> None:
    assert self_check() == 0


def test_gen_path_sources_do_not_import_vendor_sdks() -> None:
    forbidden = (
        "dbgpt",
        "db_gpt",
        "mybot",
        "n8n",
        "openwillow",
        "guaca",
        "rakazo",
        "langchain",
        "langgraph",
    )
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "packages/executor/dms_executor/generative_ask.py",
        root / "packages/executor/dms_executor/semantic_retrieve.py",
        root / "packages/cortex_client/cortex_client/compute.py",
        root / "scripts/score_curated.py",
        root / "tests/test_gen_path_prove.py",
        root / "tests/test_gen_path_climb.py",
        root / "tests/test_gen_path_climb07.py",
        root / "tests/test_gen_path_climb08.py",
        root / "tests/test_gen_path_climb09.py",
        root / "tests/test_gen_path_climb10.py",
        root / "tests/test_gen_path_climb11.py",
        root / "tests/test_gen_path_climb12.py",
        root / "tests/test_gen_path_climb13.py",
    ]
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            lower = stripped.lower()
            for bad in forbidden:
                assert bad not in lower, f"{path}: {stripped}"
