"""GEN-INTENT-01 replay: eight ranking-fallback answers stay row-identical.

Captured on the demo warehouse. These questions are not list intent, so
empty generate and explain:BinderException still climb. Rows are the
6f7139a3 compile (ontology.py compile() is unchanged for a named measure).

Does not open oracles.yaml, questions.yaml, or a golden file. Question
text and expected rows are pinned here.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dms_executor.demo_grants import DEMO_SPACE_GRANTS
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology, is_list_intent
from dms_executor.semantic_retrieve import intent_slots, measure_lock_token

# Entity-word lock that still answers. Not an abstain.
# ambiguous_measure:none owns any change to this lock.
NAMED_LEFTOVER_ENTITY_LOCKS = frozenset({"cq_top3_category_syn_typo"})

_FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_TOP5 = [
    {"product_sku": "SKU-BETA", "outbound_value_myr": 8312.5},
    {"product_sku": "SKU-ALPHA", "outbound_value_myr": 5670.0},
    {"product_sku": "RS622XK", "outbound_value_myr": 3915.0},
    {"product_sku": "SKU-EPSILON", "outbound_value_myr": 2925.0},
    {"product_sku": "SKU-GAMMA", "outbound_value_myr": 2640.0},
]
_TOP3 = [
    {"product_category": "PACKAGING", "outbound_value_myr": 13982.5},
    {"product_category": "RAW", "outbound_value_myr": 4955.0},
    {"product_category": "PARTS", "outbound_value_myr": 4077.0},
]
# shape, id, space, metric, question, measure, lock token, kind, rows
_CASES: tuple[tuple[str, str, str, str, str, str, str, str, list[dict[str, Any]]], ...] = (
    (
        "binder",
        "cq_sales_top5_syn_skus",
        "finance",
        "cq_sales_top5_value",
        "Top 5 SKUs by revenue",
        "outbound_value_myr",
        "revenue",
        "measure",
        _TOP5,
    ),
    (
        "binder",
        "cq_top3_category_syn_value",
        "finance",
        "cq_top3_category_sales",
        "top 3 categories by sales value",
        "outbound_value_myr",
        "sales",
        "measure",
        _TOP3,
    ),
    (
        "binder",
        "cq_top3_category_syn_plain",
        "finance",
        "cq_top3_category_sales",
        "top 3 category sales",
        "outbound_value_myr",
        "sales",
        "measure",
        _TOP3,
    ),
    (
        "empty",
        "cq_sales_top5_syn_sales",
        "finance",
        "cq_sales_top5_value",
        "Top 5 selling SKUs by sales",
        "outbound_value_myr",
        "selling",
        "measure",
        _TOP5,
    ),
    (
        "empty",
        "cq_top3_category_syn_typo",
        "finance",
        "cq_top3_category_sales",
        "top 3 categoty sales",
        "outbound_value_myr",
        "categoty",
        "entity",
        _TOP3,
    ),
    (
        "empty",
        "ops_sku_count_syn_short",
        "ops",
        "cq_sku_count",
        "How many SKUs in inventory?",
        "sku_count",
        "how many",
        "measure",
        [{"sku_count": 7}],
    ),
    (
        "empty",
        "cq_sku_count_by_category_per",
        "finance",
        "cq_sku_count_by_category",
        "how many SKUs per category",
        "sku_count",
        "how many",
        "measure",
        [
            {"product_category": "PACKAGING", "sku_count": 2},
            {"product_category": "RAW", "sku_count": 2},
            {"product_category": "PARTS", "sku_count": 2},
            {"product_category": "CHEMICALS", "sku_count": 1},
        ],
    ),
    (
        "empty",
        "ops_freight_spend_destination",
        "ops",
        "cq_cost_by_destination",
        "freight spend per destination",
        "shipping_cost_myr",
        "freight",
        "measure",
        [
            {"location_location_code": "WH-B", "shipping_cost_myr": 1130.0},
            {"location_location_code": "WH-A", "shipping_cost_myr": 540.0},
            {"location_location_code": "WH-D", "shipping_cost_myr": 460.0},
            {"location_location_code": "WH-C", "shipping_cost_myr": 190.0},
        ],
    ),
)


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_replay", hash="hash_replay_not_entry")


def _submit(db: Path):
    def _run(sql: str) -> Any:
        con = connect_file(db)
        try:
            rel = con.execute(sql)
            cols = [d[0] for d in rel.description] if rel.description else []
            rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(
            ok=True, status="ok", run_id="run_replay", output={"rows": rows}
        )

    return _run


def _metrics(metric_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "metrics": [{"id": metric_id, "importance": {"rank": 1}}],
    }


def _empty(metric_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "REFUSE",
        "phase": "generate",
        "generative": {"ok": False, "sql": None, "climb": {"final": "EMPTY"}},
        "ontology": _metrics(metric_id),
        "values": [],
    }


def _binder(metric_id: str) -> dict[str, Any]:
    sql = "SELECT missing_col FROM inventory"
    return {
        "query_sql": sql,
        "plan_source": "ontology_plan",
        "phase": "generate",
        "generative": {"ok": True, "sql": sql},
        "ontology": _metrics(metric_id),
    }


def _num(value: Any) -> Any:
    if isinstance(value, str):
        return value
    number = float(value)
    if number.is_integer():
        return int(number)
    return round(number, 2)


def _norm(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _num(val) for key, val in row.items()} for row in rows]


def test_ranking_fallback_rows_stay(tmp_path: Path) -> None:
    db = tmp_path / "replay.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None and onto.verified
    spaces = {"finance": _FINANCE, "ops": _OPS}
    grants = {
        "finance": set(DEMO_SPACE_GRANTS[_FINANCE][1]),
        "ops": set(DEMO_SPACE_GRANTS[_OPS][1]),
    }
    for (
        shape, case_id, space, metric_id, question, measure, word, kind, want,
    ) in _CASES:
        assert not is_list_intent(question), case_id
        assert measure_lock_token(question) == word, case_id
        assert intent_slots(question, onto).get("measure") == measure, case_id
        if case_id in NAMED_LEFTOVER_ENTITY_LOCKS:
            assert kind == "entity", case_id
            assert word == "categoty", case_id
        else:
            assert kind == "measure", case_id
        payload = _binder(metric_id) if shape == "binder" else _empty(metric_id)
        env = maybe_generative_ask(
            question,
            space_id=spaces[space],
            warehouse=db,
            grantable=grants[space],
            compute=lambda _c, payload=payload: payload,
            submit=_submit(db),
            ledger_append=_ledger,
            ontology=onto,
            bind_on_miss=False,
        )
        assert env is not None, case_id
        assert_envelope_valid(env)
        assert env["badge"] == "L2_VALIDATED", case_id
        assert env.get("abstained") is not True, case_id
        assert env.get("plan_origin") == "ontology_ranking", case_id
        notes = " ".join(str(item) for item in (env.get("assumptions") or []))
        if case_id in NAMED_LEFTOVER_ENTITY_LOCKS:
            assert env["badge"] == "L2_VALIDATED", case_id
            assert env.get("abstained") is not True, case_id
        if shape == "binder":
            # CI check: explain:BinderException is not the ungranted no-climb.
            assert "fallback:validate:explain:BinderException" in notes, case_id
            assert "ungranted:" not in notes, case_id
        else:
            assert "fallback:generate_empty" in notes, case_id
            assert "generate_empty_no_ranking_answer" not in notes, case_id
        got = _norm(list(env.get("rows") or []))
        pinned = _norm(want)
        if case_id == "cq_sku_count_by_category_per":
            # Three categories tie at 2. ORDER BY the count does not
            # break that tie, so compare the row set.
            def _cat(row: dict[str, Any]) -> str:
                return str(row.get("product_category"))

            assert sorted(got, key=_cat) == sorted(pinned, key=_cat), case_id
        else:
            assert got == pinned, case_id
