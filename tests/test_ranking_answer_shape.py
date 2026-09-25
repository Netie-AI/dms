"""Ranking-fallback answer shape (52-question live prove, 15 WRONG every run).

The unarmed / generate-refused lane compiles a Cortex-ranked metric onto the
DMS ontology. On origin/main that lane answered "which X ..." questions with
an extra measure column (``WH-C, utilisation_pct``), grouped warehouse
capacity utilisation by ``capacity_kg``, kept zero rows of a COUNT FILTER
(``RS622XK, below_reorder_lots=0``), dropped the WH-A key from the CCTV
lookup, and labelled total spend ``stock_value_myr``.

Every case here runs the real compile against the demo warehouse, executes
the SQL, and judges the DMS envelope rows with the unmodified curated-pack
judge (``oracle_row_match.rows_mismatch_reason``) and the rendered text.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from cortex_client.qualifiers import unhonored_qualifier_reason
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import Refusal, demo_ontology

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from oracle_row_match import rows_mismatch_reason, run_oracle_select  # noqa: E402

ORACLES = yaml.safe_load(
    (ROOT / "tests" / "fixtures" / "curated_ceo" / "oracles.yaml").read_text(
        encoding="utf-8"
    )
)["oracles"]
GRANTS = {"inventory", "locations", "transactions", "suppliers", "shipments"}

# (pack id, question) for every ranking-fallback WRONG on the live prove,
# plus the mislabelled spend answer.
CASES: tuple[tuple[str, str], ...] = (
    ("cq_cold_storage", "Which locations are cold storage?"),
    ("cq_capacity_above_90", "Which locations are above 90 percent capacity?"),
    ("cq_expired_items", "Which items are expired?"),
    ("cq_chemicals_list", "List chemicals in inventory"),
    ("cq_audit_overdue", "Which suppliers have an audit overdue?"),
    ("cq_cctv_wh_a", "Show the CCTV camera for warehouse A"),
    ("cq_capacity_utilisation", "Show warehouse capacity utilisation"),
    ("cq_low_stock_wh_a", "Which SKUs are below reorder level in warehouse A?"),
    ("cq_spend_by_country", "What is our total spend by supplier country?"),
)
# The answer columns the customer sees, per case: entity keys for list asks,
# key + measure (named for the question) otherwise. The oracle judge compares
# values, not labels, so the label is asserted here.
EXPECT_COLS: dict[str, set[str]] = {
    "cq_cold_storage": {"location_location_code"},
    "cq_capacity_above_90": {"location_location_code"},
    "cq_expired_items": {"product_sku"},
    "cq_chemicals_list": {"product_sku"},
    "cq_audit_overdue": {"supplier_supplier_id"},
    "cq_cctv_wh_a": {"location_location_code", "location_cctv_camera_id"},
    "cq_capacity_utilisation": {"location_location_code", "utilisation_pct"},
    "cq_low_stock_wh_a": {"product_sku", "below_reorder_kg"},
    "cq_spend_by_country": {"supplier_country", "spend_myr"},
}
# Answers that were right before must stay right (no regression by shape).
# These are regression guards: they pass on origin/main by design.
KEEP_CASES: tuple[tuple[str, str], ...] = (
    ("cq_supplier_ranking", "Rank suppliers by combined risk and lead time score"),
    ("cq_stock_value_by_category", "What is total stock value by category?"),
    ("cq_sales_top5_value", "Top 5 selling SKUs by revenue"),
    ("cq_sku_count", "How many SKUs do we have in inventory?"),
    ("cq_sku_count_by_category", "Show SKU count by category"),
)


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("shape") / "demo.duckdb"
    ensure_demo_warehouse(db)
    return db


def _submit(db: Path) -> Any:
    def submit(sql: str) -> Any:
        con = connect_file(db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_shape", output={"rows": rows})

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_shape", hash="hash_shape_not_entry")


def _ranking(metric_id: str) -> Any:
    # Unarmed Cortex: generate refused, GET /v1/insights/ontology ranked ids.
    return lambda _ctx: {
        "phase": "ontology",
        "insights_reached": True,
        "ontology": {"metrics": [{"id": metric_id}]},
    }


def _ask(db: Path, question: str, metric_id: str) -> dict[str, Any] | None:
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    return maybe_generative_ask(
        question,
        warehouse=db,
        grantable=GRANTS,
        compute=_ranking(metric_id),
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
    )


def _assert_matches_oracle(db: Path, qid: str, env: dict[str, Any] | None) -> None:
    assert env is not None, qid
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED", (qid, env.get("assumptions"))
    assert env["abstained"] is False
    assert env.get("plan_source") == "ontology_plan"
    assert env.get("plan_origin") == "ontology_ranking"
    sql = str(ORACLES[qid]["sql"])
    gold, err = run_oracle_select(db, sql)
    assert err is None and gold, (qid, err)
    rows = env["rows"]
    assert rows_mismatch_reason(rows, gold, sql=sql) is None, (qid, rows, gold)
    # The customer reads the text: every gold cell value is rendered.
    for row in gold:
        for cell in row.values():
            assert str(cell) in env["text"], (qid, cell, env["text"])
    assert env["text"].startswith(f"Found {len(gold)} row(s).")


@pytest.mark.parametrize(("qid", "question"), CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("ranked", ["pack_id", "noise_sku_count"])
def test_ranking_fallback_answer_matches_oracle(
    lake: Path, qid: str, question: str, ranked: str
) -> None:
    metric = str(ORACLES[qid]["cortex_id"]) if ranked == "pack_id" else "sku_count"
    env = _ask(lake, question, metric)
    _assert_matches_oracle(lake, qid, env)
    assert env is not None
    for row in env["rows"]:
        assert set(row) == EXPECT_COLS[qid], (qid, row)


@pytest.mark.parametrize(("qid", "question"), KEEP_CASES, ids=[c[0] for c in KEEP_CASES])
def test_measure_answers_keep_their_measure(lake: Path, qid: str, question: str) -> None:
    env = _ask(lake, question, str(ORACLES[qid]["cortex_id"]))
    _assert_matches_oracle(lake, qid, env)


@pytest.mark.parametrize(
    ("question", "metric", "keys"),
    [
        ("Which locations are cold storage?", "cq_cold_storage", {"location_location_code"}),
        ("Which items are expired?", "cq_expired_items", {"product_sku"}),
        ("List chemicals in inventory", "cq_chemicals_list", {"product_sku"}),
        ("Which suppliers have an audit overdue?", "cq_audit_overdue", {"supplier_supplier_id"}),
        (
            "Show the CCTV camera for warehouse A",
            "cq_cctv_wh_a",
            {"location_location_code", "location_cctv_camera_id"},
        ),
    ],
)
def test_which_questions_project_only_entity_keys(
    lake: Path, question: str, metric: str, keys: set[str]
) -> None:
    env = _ask(lake, question, metric)
    assert env is not None and env["abstained"] is False
    assert env["rows"]
    for row in env["rows"]:
        assert set(row) == keys, row
    for noise in ("utilisation_pct", "stock_value_myr", "audit_overdue"):
        assert noise not in env["text"]


def test_below_reorder_list_drops_zero_rows(lake: Path) -> None:
    env = _ask(lake, "Which SKUs are below reorder level in warehouse A?", "cq_low_stock_wh_a")
    assert env is not None and env["abstained"] is False
    skus = [r["product_sku"] for r in env["rows"]]
    # RS622XK is in WH-A but 1200 kg >= 500 kg reorder: not a member.
    assert skus == ["RS622XKR"]
    assert "RS622XK," not in env["text"] and "RS622XK\n" not in env["text"]
    assert env["rows"][0]["below_reorder_kg"] == 80.0
    assert "HAVING" in (env.get("sql_used") or "")


def test_spend_is_labelled_spend_not_stock_value(lake: Path) -> None:
    env = _ask(lake, "What is our total spend by supplier country?", "cq_spend_by_country")
    assert env is not None and env["abstained"] is False
    assert all("spend_myr" in row for row in env["rows"])
    assert "stock_value_myr" not in env["text"]
    assert "spend_myr=" in env["text"]


def test_capacity_utilisation_is_keyed_by_location(lake: Path) -> None:
    env = _ask(lake, "Show warehouse capacity utilisation", "cq_capacity_utilisation")
    assert env is not None and env["abstained"] is False
    assert all(set(r) == {"location_location_code", "utilisation_pct"} for r in env["rows"])
    assert "capacity_kg" not in (env.get("sql_used") or "").split("GROUP BY")[-1]
    assert "WH-A" in env["text"]


def test_grain_mismatch_abstains_named() -> None:
    q = "Show warehouse capacity utilisation"
    plan = {"measure": "utilisation_pct", "group_by": [["location", "capacity_kg"]]}
    assert unhonored_qualifier_reason(q, plan=plan) == "unhonored_qualifier:grain=warehouse"
    sql = (
        "SELECT f.capacity_kg, ROUND(100.0 * SUM(f.current_load_kg) / SUM(f.capacity_kg), 1) "
        "FROM locations f GROUP BY f.capacity_kg"
    )
    assert unhonored_qualifier_reason(q, sql=sql) == "unhonored_qualifier:grain=warehouse"
    ok = "SELECT location_code, ROUND(100.0 * current_load_kg / capacity_kg, 1) FROM locations"
    assert unhonored_qualifier_reason(q, sql=ok) is None
    assert (
        unhonored_qualifier_reason(
            "Which locations are cold storage?",
            sql="SELECT location_code FROM locations WHERE is_cold_storage",
        )
        is None
    )


def test_grain_mismatch_plan_abstains_on_envelope(lake: Path) -> None:
    onto = load_verified_ontology(lake, demo_ontology(lake))
    submitted: list[str] = []

    def submit(sql: str) -> Any:
        submitted.append(sql)
        raise AssertionError("grain-mismatched plan must not execute")

    env = maybe_generative_ask(
        "Show warehouse capacity utilisation",
        warehouse=lake,
        grantable=GRANTS,
        compute=lambda _c: {
            "plan_source": "ontology_plan",
            "query_plan": {
                "measure": "utilisation_pct",
                "group_by": [["location", "capacity_kg"]],
                "filters": [],
            },
        },
        submit=submit,
        ledger_append=_ledger,
        ontology=onto,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert "unhonored_qualifier:grain=warehouse" in " ".join(env["assumptions"])
    assert submitted == []


def test_compile_keys_only_needs_a_key(lake: Path) -> None:
    onto = load_verified_ontology(lake, demo_ontology(lake))
    assert onto is not None
    got = onto.compile("utilisation_pct", keys_only=True)
    assert isinstance(got, Refusal)
    assert got.reason == "bad_projection"
