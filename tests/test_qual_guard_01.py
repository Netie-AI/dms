"""QUAL-GUARD-01 / dms#290: named abstain when a qualifier is dropped.

Seeded fixtures only. No LLM, no network, no keys. Does not edit
scripts/score_curated.py. Does not invent ontology grains.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import yaml
from cortex_client.compute import (
    FREEROUTE_PREFERENCE,
    compute_insights,
    typed_ranked_retry_plan,
)
from cortex_client.qualifiers import (
    extract_qualifiers,
    retry_plan_covers_qualifiers,
    unhonored_qualifier_reason,
)
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import (
    NOTE_FALLBACK_GENERATE_EMPTY,
    NOTE_FALLBACK_VALIDATE_PREFIX,
    NOTE_INSIGHTS_RANKING,
    load_verified_ontology,
    maybe_generative_ask,
)
from dms_executor.ontology import Ontology, demo_ontology

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"

MONTH_Q = "What is revenue per month?"
WEEK_Q = "What is revenue per week?"
QUARTER_Q = "What is revenue per quarter?"
YEAR_Q = "What is revenue per year?"
SUPPLIER_Q = "What is revenue by supplier?"
FILTER_Q = "What is revenue for 'ACME'?"
MONTH_SQL = (
    "SELECT date_trunc('month', ts) AS month, SUM(amount) AS revenue "
    "FROM sales GROUP BY 1"
)
SKU_SQL = "SELECT sku, SUM(amount) AS revenue FROM sales GROUP BY sku"
BAD_SQL = "SELECT 1 AS n FROM not_a_granted_table"


def _submit_ok(sql: str) -> Any:
    return SimpleNamespace(
        ok=True,
        status="ok",
        run_id="run_qual_guard",
        output={"rows": [{"month": "2024-01-01", "revenue": 100.0}]},
    )


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_qual_guard", hash="hash_qual_guard_not_entry")


def _rank(*metric_ids: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "REFUSE",
        "phase": "generate",
        "generative": {"ok": False, "sql": None, "climb": {"final": "EMPTY"}},
        "ontology": {
            "ok": True,
            "metrics": [
                {"id": mid, "importance": {"rank": i + 1}}
                for i, mid in enumerate(metric_ids)
            ],
        },
        "values": [],
        "generate_legs": {
            "count": 2,
            "legs": [{"returned": "nothing"}, {"returned": "nothing"}],
        },
    }


def _tiny_ontology() -> Ontology:
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
    return o


def _seed_tiny(path: Path) -> None:
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
            "CREATE TABLE sales ("
            "txn_id VARCHAR, sku VARCHAR, region VARCHAR, amount DOUBLE, ts DATE)"
        )
        con.execute(
            "INSERT INTO sales VALUES "
            "('T1','SKU-1','North',100,'2024-01-15'),"
            "('T2','SKU-2','South',50,'2024-02-15')"
        )
        con.execute("CREATE TABLE regions (region VARCHAR, country VARCHAR)")
        con.execute("INSERT INTO regions VALUES ('North','MY'),('South','MY')")
    finally:
        con.close()


def _ask(
    question: str,
    *,
    warehouse: Path,
    onto: Ontology,
    compute: Any,
    grantable: set[str] | None = None,
    submit: Any = None,
) -> dict[str, Any] | None:
    return maybe_generative_ask(
        question,
        warehouse=warehouse,
        grantable=grantable or {"sales", "lots", "regions"},
        compute=compute,
        submit=submit or _submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )


def test_extract_time_grains_and_ly_forms() -> None:
    for q, grain in (
        ("revenue per month", "month"),
        ("revenue by week", "week"),
        ("revenue each quarter", "quarter"),
        ("revenue per year", "year"),
        ("daily revenue", "day"),
        ("weekly revenue", "week"),
        ("monthly revenue", "month"),
        ("quarterly revenue", "quarter"),
        ("yearly revenue", "year"),
    ):
        got = extract_qualifiers(q)
        assert ( "time_grain", grain) in got, (q, got)


def test_last_month_is_time_filter_not_grain() -> None:
    got = extract_qualifiers("Just give me last month's number")
    kinds = {k for k, _v in got}
    assert "time_filter" in kinds
    assert "time_grain" not in kinds


def test_by_supplier_country_is_country_not_supplier() -> None:
    got = extract_qualifiers("What is our total spend by supplier country?")
    assert ("group_by", "country") in got
    assert ("group_by", "supplier") not in got


def test_by_revenue_is_not_a_group() -> None:
    got = extract_qualifiers("Top 5 selling SKUs by revenue")
    assert all(k != "group_by" or v != "revenue" for k, v in got)


def test_coverage_month_week_quarter_year_uncovered_and_covered() -> None:
    for grain, q in (
        ("month", MONTH_Q),
        ("week", WEEK_Q),
        ("quarter", QUARTER_Q),
        ("year", YEAR_Q),
    ):
        why = unhonored_qualifier_reason(q, sql=SKU_SQL, plan={"group_by": []})
        assert why == f"unhonored_qualifier:time_grain={grain}"
        sql = (
            f"SELECT date_trunc('{grain}', ts) AS bucket, SUM(amount) AS revenue "
            "FROM sales GROUP BY 1"
        )
        assert unhonored_qualifier_reason(q, sql=sql) is None
        plan = {"measure": "revenue", "group_by": [["day", grain]], "filters": []}
        assert unhonored_qualifier_reason(q, plan=plan) is None


def test_coverage_by_supplier_uncovered_and_covered() -> None:
    why = unhonored_qualifier_reason(SUPPLIER_Q, sql=SKU_SQL, plan={"group_by": []})
    assert why == "unhonored_qualifier:group_by=supplier"
    sql = "SELECT supplier_id, SUM(amount) AS revenue FROM sales GROUP BY supplier_id"
    assert unhonored_qualifier_reason(SUPPLIER_Q, sql=sql) is None
    plan = {
        "measure": "revenue",
        "group_by": [["supplier", "supplier_id"]],
        "filters": [],
    }
    assert unhonored_qualifier_reason(SUPPLIER_Q, plan=plan) is None


def test_coverage_named_filter_uncovered_and_covered() -> None:
    why = unhonored_qualifier_reason(FILTER_Q, sql=SKU_SQL, plan={"filters": []})
    assert why == "unhonored_qualifier:named_filter=ACME"
    sql = "SELECT SUM(amount) AS revenue FROM sales WHERE supplier_id = 'ACME'"
    assert unhonored_qualifier_reason(FILTER_Q, sql=sql) is None
    plan = {
        "measure": "revenue",
        "group_by": [],
        "filters": [["supplier", "supplier_id", "=", "ACME"]],
    }
    assert unhonored_qualifier_reason(FILTER_Q, plan=plan) is None


def test_ranked_plan_without_month_abstains(tmp_path: Path) -> None:
    db = tmp_path / "qual.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    env = maybe_generative_ask(
        MONTH_Q,
        warehouse=db,
        grantable={
            "inventory",
            "locations",
            "transactions",
            "suppliers",
            "shipments",
        },
        compute=lambda _c: _rank("cq_top3_category_sales"),
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    blob = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "unhonored_qualifier:time_grain=month" in blob
    text = str(env.get("text") or "")
    assert "unhonored_qualifier:time_grain=month" in text
    assert NOTE_FALLBACK_GENERATE_EMPTY in blob
    assert NOTE_INSIGHTS_RANKING in blob
    legs = env.get("generate_legs") or {}
    assert legs.get("count") == 2
    assert [x.get("returned") for x in (legs.get("legs") or [])] == [
        "nothing",
        "nothing",
    ]


def test_ranked_plan_with_month_bucket_passes_coverage() -> None:
    plan = {
        "measure": "revenue",
        "group_by": [["day", "month"]],
        "filters": [],
    }
    assert unhonored_qualifier_reason(MONTH_Q, plan=plan) is None


def test_generate_sql_with_month_bucket_answers(tmp_path: Path) -> None:
    db = tmp_path / "qual.duckdb"
    _seed_tiny(db)
    onto = load_verified_ontology(db, _tiny_ontology())
    assert onto is not None
    env = _ask(
        MONTH_Q,
        warehouse=db,
        onto=onto,
        compute=lambda _c: {
            "query_sql": MONTH_SQL,
            "plan_source": "ontology_plan",
            "plan_origin": "generate_sql",
            "generate_legs": {"count": 1, "legs": [{"returned": "sql"}]},
        },
        submit=lambda _sql: SimpleNamespace(
            ok=True,
            status="ok",
            run_id="run_qual_month",
            output={"rows": [{"month": "2024-01-01", "revenue": 100.0}]},
        ),
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["rows"]
    assert "unhonored_qualifier" not in str(env.get("assumptions") or [])


def test_route_a_monthly_generate_empty_named_abstain(tmp_path: Path) -> None:
    db = tmp_path / "qual.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    env = maybe_generative_ask(
        MONTH_Q,
        warehouse=db,
        grantable={
            "inventory",
            "locations",
            "transactions",
            "suppliers",
            "shipments",
        },
        compute=lambda _c: _rank("cq_top3_category_sales"),
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    blob = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "unhonored_qualifier:time_grain=month" in blob
    assert NOTE_FALLBACK_GENERATE_EMPTY in blob
    assert NOTE_INSIGHTS_RANKING in blob
    assert NOTE_FALLBACK_VALIDATE_PREFIX not in blob
    assert "unhonored_qualifier:time_grain=month" in str(env.get("text") or "")
    legs = env.get("generate_legs") or {}
    assert int(legs.get("count") or 0) >= 1


def test_route_b_monthly_validate_fail_keeps_reason(tmp_path: Path) -> None:
    db = tmp_path / "qual.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    env = maybe_generative_ask(
        MONTH_Q,
        warehouse=db,
        grantable={
            "inventory",
            "locations",
            "transactions",
            "suppliers",
            "shipments",
        },
        compute=lambda _c: {
            "query_sql": BAD_SQL,
            "plan_source": "ontology_plan",
            "plan_origin": "generate_sql",
            "ontology": {
                "ok": True,
                "metrics": [{"id": "cq_top3_category_sales"}],
            },
            "generate_legs": {"count": 1, "legs": [{"returned": "sql"}]},
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    notes = env.get("assumptions") or []
    blob = " ".join(str(a) for a in notes)
    assert "unhonored_qualifier:time_grain=month" in blob
    assert any(str(a).startswith(NOTE_FALLBACK_VALIDATE_PREFIX) for a in notes)
    assert "ungranted" in blob
    assert NOTE_INSIGHTS_RANKING in blob
    assert NOTE_FALLBACK_GENERATE_EMPTY not in blob
    legs = env.get("generate_legs") or {}
    assert legs.get("returned") is None
    assert [x.get("returned") for x in (legs.get("legs") or [])] == ["sql"]
    assert "ungranted" in str(legs.get("validate_reason") or "")
    assert "unhonored_qualifier:time_grain=month" in str(env.get("text") or "")


def test_ranked_retry_adds_month_or_skips() -> None:
    posts: list[dict[str, Any]] = []

    class _Empty:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "status": "ABSTAIN",
                "phase": "generate",
                "generative": {"ok": False, "sql": None, "climb": {"final": "EMPTY"}},
                "ontology": {
                    "ok": True,
                    "metrics": [{"id": "cq_top3_category_sales"}],
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
        ) -> _Empty:
            posts.append({"url": url, "json": json, "headers": headers})
            return _Empty()

        def get(self, *_a: Any, **_k: Any) -> _Empty:
            return _Empty()

    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_insights(
            "http://127.0.0.1:8010",
            question=MONTH_Q,
            ontology={
                "measures": {"outbound_value_myr": {"grain": "transaction"}},
                "measure_aliases": {"cq_top3_category_sales": "outbound_value_myr"},
                "intent_slots": {"measure": "outbound_value_myr"},
            },
            api_key="ov_test_qual_guard",
        )
    gens = [p for p in posts if str(p["url"]).endswith("/v1/insights")]
    assert gens
    assert gens[0]["json"]["model_preference"] == FREEROUTE_PREFERENCE
    if len(gens) > 1:
        retry_plan = gens[1]["json"]["query_plan"]
        blob = str(retry_plan).lower()
        assert "month" in blob
        assert retry_plan_covers_qualifiers(retry_plan, MONTH_Q) is True
    else:
        # Named filter / uncovered plan skipped the retry instead of dropping grain.
        plan = typed_ranked_retry_plan(
            {
                "ontology": {
                    "metrics": [{"id": "cq_top3_category_sales"}],
                }
            },
            ontology={
                "measures": {"outbound_value_myr": {"grain": "transaction"}},
                "measure_aliases": {"cq_top3_category_sales": "outbound_value_myr"},
            },
            question=MONTH_Q,
        )
        assert plan is None or retry_plan_covers_qualifiers(plan, MONTH_Q)
    assert out is not None
    legs = out.get("generate_legs") or {}
    assert int(legs.get("count") or 0) >= 1
    assert "LIVE_KEY" not in str(posts)
    assert ":5000" not in str(posts)


def test_retry_skips_when_named_filter_missing() -> None:
    posts: list[dict[str, Any]] = []

    class _Empty:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "status": "ABSTAIN",
                "phase": "generate",
                "generative": {"ok": False, "sql": None, "climb": {"final": "EMPTY"}},
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
        ) -> _Empty:
            posts.append({"url": url, "json": json})
            return _Empty()

        def get(self, *_a: Any, **_k: Any) -> _Empty:
            return _Empty()

    with patch("cortex_client.compute.httpx.Client", _Client):
        compute_insights(
            "http://127.0.0.1:8010",
            question=FILTER_Q,
            ontology={"intent_slots": {"measure": "revenue"}},
            api_key="ov_test_qual_guard_filter",
        )
    gens = [p for p in posts if str(p["url"]).endswith("/v1/insights")]
    assert len(gens) == 1
    assert gens[0]["json"].get("generate_retry") != "ranked_slots"


def test_pack_questions_do_not_gain_time_grain() -> None:
    pack = yaml.safe_load(PACK.read_text(encoding="utf-8")) or {}
    grown: list[str] = []
    for case in pack.get("questions") or []:
        qid = str(case.get("id") or "")
        got = extract_qualifiers(str(case.get("question") or ""))
        grains = [v for k, v in got if k == "time_grain"]
        if grains:
            grown.append(qid)
    assert grown == []


def test_by_supplier_sql_without_dim_abstains(tmp_path: Path) -> None:
    db = tmp_path / "qual.duckdb"
    _seed_tiny(db)
    onto = load_verified_ontology(db, _tiny_ontology())
    assert onto is not None
    env = _ask(
        SUPPLIER_Q,
        warehouse=db,
        onto=onto,
        compute=lambda _c: {
            "query_sql": SKU_SQL,
            "plan_source": "ontology_plan",
            "plan_origin": "generate_sql",
        },
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    blob = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert "unhonored_qualifier:group_by=supplier" in blob
    assert "unhonored_qualifier:group_by=supplier" in str(env.get("text") or "")
