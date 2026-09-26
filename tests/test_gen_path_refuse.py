"""GEN-PATH-REFUSE-01: fail-closed ABSTAIN with named gap. WRONG=0.

Missing ontology path / missing metric never ships a confident badge.
Does not stamp COMPLETE. Does not reseat GEN-03 ask_path 400.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.gen_path_refuse import (
    customer_abstain_text,
    gap_reason_name,
    ranking_missing_metric_gap,
)
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import Ontology, demo_ontology
from dms_executor.semantic_retrieve import bind_plan, retrieve_short_context

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_curated import judge  # noqa: E402

PROFIT_Q = "What is profit by location_code?"
MISSING_METRIC_ID = "cq_gross_profit"
FINANCE_GRANT = {"inventory", "locations", "transactions", "suppliers"}


def _submit_trap(sql: str) -> Any:
    raise AssertionError(f"missing-metric path executed SQL: {sql[:80]}")


def _ledger_ok(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_refuse01", hash="hash_refuse01_not_entry")


def _submit_ok(_sql: str) -> Any:
    return SimpleNamespace(
        ok=True,
        status="ok",
        run_id="run_refuse01",
        output={"rows": [{"location_location_code": "WH-A", "utilisation_pct": 91.0}]},
    )


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
    o.add_object("orphan", "regions", ["region"])
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
            "CREATE TABLE sales (txn_id VARCHAR, sku VARCHAR, region VARCHAR, amount DOUBLE)"
        )
        con.execute(
            "INSERT INTO sales VALUES ('T1','SKU-1','North',100),('T2','SKU-2','South',50)"
        )
        con.execute("CREATE TABLE regions (region VARCHAR, country VARCHAR)")
        con.execute("INSERT INTO regions VALUES ('North','MY'),('South','MY')")
    finally:
        con.close()


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


def _assert_named_gap(env: dict[str, Any], token: str) -> None:
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env.get("sql_used") in (None, "")
    text = str(env.get("text") or "")
    assert "cannot certify" in text.lower()
    assert "gap:" in text.lower()
    assert token in text
    notes = " ".join(str(a) for a in (env.get("assumptions") or []))
    assert token in notes or token in text
    assert_envelope_valid(env)
    assert judge({"expect": "l0"}, env) == "ABSTAIN"
    assert judge({"expect": "refuse"}, env) == "ABSTAIN"
    assert judge({"expect": "refuse"}, env) != "WRONG"


def test_customer_text_names_gap_not_generic() -> None:
    # SPACE-GEN-01: a reason outside GAP_REASONS is still named in the text;
    # the unnamed sentence hid 110 of 500 BIRD refusals' causes.
    other = customer_abstain_text("submit_failed")
    assert "gap: submit_failed" in other
    named = customer_abstain_text("unknown_measure: no measure named 'profit'")
    assert "gap:" in named
    assert "profit" in named
    assert gap_reason_name("no_path: no chain from sale") == "no_path"
    assert gap_reason_name("coverage_invalid: missing include") == "coverage_invalid"
    assert gap_reason_name("submit_failed") is None


def test_unknown_measure_abstains_with_named_gap(tmp_path: Path) -> None:
    db = tmp_path / "refuse01_tiny.duckdb"
    _seed_tiny(db)
    onto = load_verified_ontology(db, _tiny_ontology())
    assert onto is not None
    env = maybe_generative_ask(
        "What is profit by product category?",
        warehouse=db,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_plan": {"measure": "profit", "group_by": [["product", "category"]]}
        },
        submit=_submit_trap,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    _assert_named_gap(env, "unknown_measure")
    assert "profit" in str(env["text"])


def test_no_path_abstains_with_named_gap(tmp_path: Path) -> None:
    db = tmp_path / "refuse01_nopath.duckdb"
    _seed_tiny(db)
    onto = load_verified_ontology(db, _tiny_ontology())
    assert onto is not None
    env = maybe_generative_ask(
        "What is revenue by orphan country?",
        warehouse=db,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_plan": {"measure": "revenue", "group_by": [["orphan", "country"]]}
        },
        submit=_submit_trap,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    _assert_named_gap(env, "no_path")
    assert "orphan" in str(env["text"])


def test_fanout_path_abstains_with_named_gap(tmp_path: Path) -> None:
    db = tmp_path / "refuse01_fanout.duckdb"
    _seed_tiny(db)
    onto = load_verified_ontology(db, _tiny_ontology())
    assert onto is not None
    env = maybe_generative_ask(
        "What is revenue by lot category?",
        warehouse=db,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_plan": {"measure": "revenue", "group_by": [["lot", "category"]]}
        },
        submit=_submit_trap,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    _assert_named_gap(env, "fanout_refused")


def test_ontology_unverified_names_the_gap() -> None:
    o = _tiny_ontology()
    assert not o.verified
    env = maybe_generative_ask(
        "What is revenue by product category?",
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]}
        },
        submit=_submit_trap,
        ledger_append=_ledger_ok,
        ontology=o,
        tables=None,
        warehouse=None,
    )
    _assert_named_gap(env, "ontology_unverified")


def test_keyword_bind_would_answer_profit_as_utilisation(tmp_path: Path) -> None:
    """R-0007 control: bind_plan still guesses. REFUSE-01 must not use it."""
    db = tmp_path / "refuse01_bind.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    ctx = retrieve_short_context(
        PROFIT_Q, warehouse=db, grantable=FINANCE_GRANT, ontology=onto
    )
    bound = bind_plan(PROFIT_Q, ctx)
    assert bound is not None
    assert bound.get("unsure") is not True
    assert bound["query_plan"]["measure"] == "utilisation_pct"


def test_ranked_missing_metric_gap_names_cortex_id(tmp_path: Path) -> None:
    db = tmp_path / "refuse01_rank.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    payload = _rank(MISSING_METRIC_ID)
    gap = ranking_missing_metric_gap(PROFIT_Q, payload, onto=onto)
    assert gap is not None
    assert "unknown_measure" in gap
    assert MISSING_METRIC_ID in gap
    recovered = ranking_missing_metric_gap(
        "What is total stock value by category?",
        _rank("stock_value_by_category"),
        onto=onto,
    )
    assert recovered is None


def test_ranked_missing_metric_abstains_not_bind_plan(tmp_path: Path) -> None:
    db = tmp_path / "refuse01_ask.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        PROFIT_Q,
        warehouse=db,
        grantable=FINANCE_GRANT,
        compute=lambda _c: _rank(MISSING_METRIC_ID),
        submit=_submit_trap,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    _assert_named_gap(env, "unknown_measure")
    assert MISSING_METRIC_ID in str(env["text"])
    assert env.get("plan_source") != "bind_plan"
    assert not any("bind_plan" in str(a) for a in (env.get("assumptions") or []))


def test_product_path_missing_metric_does_not_fall_through(tmp_path: Path) -> None:
    """Product lane used to return None (Cortex.ask). Missing metric is ABSTAIN."""
    db = tmp_path / "refuse01_product.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        PROFIT_Q,
        warehouse=db,
        grantable=FINANCE_GRANT,
        compute=lambda _c: _rank(MISSING_METRIC_ID),
        submit=_submit_trap,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=False,
    )
    assert env is not None
    _assert_named_gap(env, "unknown_measure")


def test_known_measure_still_compiles(tmp_path: Path) -> None:
    db = tmp_path / "refuse01_ok.duckdb"
    _seed_tiny(db)
    onto = load_verified_ontology(db, _tiny_ontology())
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=db,
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
    assert env["abstained"] is False
    assert env["rows"]
    assert "gap:" not in str(env["text"]).lower()
    assert_envelope_valid(env)
    assert judge({"expect": "l2"}, env) != "WRONG"


def test_transport_miss_still_binds_known_measure(tmp_path: Path) -> None:
    """REFUSE-01 does not kill GEN-02 bind_on_miss for a known ontology measure."""
    db = tmp_path / "refuse01_bindok.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    env = maybe_generative_ask(
        "What is total stock value by category?",
        warehouse=db,
        grantable=FINANCE_GRANT,
        compute=lambda _c: None,
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env.get("plan_source") == "bind_plan"
    assert judge({"expect": "l2"}, env) != "WRONG"


def test_judge_still_marks_confident_wrong() -> None:
    """R-0007: if the gap path shipped L2 with a number, WRONG fires."""
    fake = {
        "badge": "L2_VALIDATED",
        "abstained": False,
        "rows": [{"utilisation_pct": 91.0}],
        "text": "Found 1 row(s). utilisation_pct=91.0",
    }
    assert judge({"expect": "refuse"}, fake) == "WRONG"
    assert judge({"expect": "l0"}, fake) == "LAYER"
