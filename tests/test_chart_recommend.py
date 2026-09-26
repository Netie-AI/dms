"""DMS-VIZ-01 — rule-based chart recommendation, asserted on the envelope.

Unit cases pin one rule branch each. The end-to-end cases run a Cortex
``AskResponse`` through ``map_ask_response_to_envelope`` and assert what the
customer receives: rendered text, the exact rows, values[], the badge, and the
chart spec (kind, encoded fields ⊆ row keys, no inlined numbers). Abstentions
must carry no chart, and E14 must name a hand-built envelope that has one.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest
from cortex_client.models import AskResponse
from dms_executor import map_ask_response_to_envelope
from dms_executor.chart_recommend import VEGA_LITE_SCHEMA, recommend_chart
from dms_executor.envelope import (
    assert_envelope_valid,
    build_answer_envelope,
    chart_from_rows,
)

WAREHOUSE_STOCK = [
    {"warehouse": "Shah Alam", "stock_kg": 4200.5},
    {"warehouse": "Klang", "stock_kg": 3100.25},
    {"warehouse": "Penang", "stock_kg": 1800.75},
    {"warehouse": "Johor Bahru", "stock_kg": 950.0},
]


def _vl_fields(chart: dict[str, Any]) -> set[str]:
    enc = chart["vega_lite"]["encoding"]
    return {ch["field"] for ch in enc.values() if isinstance(ch, dict) and "field" in ch}


def _assert_vl_shape(chart: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    vl = chart["vega_lite"]
    assert vl["$schema"] == VEGA_LITE_SCHEMA
    assert vl["data"] == {"name": "rows"}
    assert vl["mark"] in {"bar", "line", "arc", "point", "text"}
    assert _vl_fields(chart) <= set(rows[0].keys())
    for ch in vl["encoding"].values():
        assert ch["type"] in {"quantitative", "nominal", "temporal", "ordinal"}


# ---------------------------------------------------------------- unit: rules


def test_one_by_one_is_bignum() -> None:
    rows = [{"total_revenue_myr": 128400.5}]
    chart = recommend_chart(rows, "What was total revenue?")
    assert chart is not None
    assert chart["kind"] == "bignum"
    assert chart["value"] == 128400.5
    assert chart["label"] == "total_revenue_myr"
    assert chart["vega_lite"]["mark"] == "text"
    assert chart["vega_lite"]["encoding"]["text"]["field"] == "total_revenue_myr"
    _assert_vl_shape(chart, rows)


def test_one_row_with_a_label_is_still_bignum() -> None:
    rows = [{"metric": "total_revenue", "revenue_myr": 77.25}]
    chart = recommend_chart(rows)
    assert chart is not None and chart["kind"] == "bignum"
    assert chart["value"] == 77.25


def test_month_by_qty_is_line_sorted_in_spec_not_rows() -> None:
    rows = [
        {"month": "2024-03", "qty": 30},
        {"month": "2024-01", "qty": 10},
        {"month": "2024-02", "qty": 20},
    ]
    before = copy.deepcopy(rows)
    chart = recommend_chart(rows, "qty by month")
    assert chart is not None
    assert chart["kind"] == "line"
    assert (chart["x"], chart["y"]) == ("month", "qty")
    enc = chart["vega_lite"]["encoding"]
    assert enc["x"] == {"field": "month", "type": "temporal", "sort": "ascending"}
    assert enc["y"]["type"] == "quantitative"
    assert chart["vega_lite"]["mark"] == "line"
    assert rows == before  # sort lives in the spec; rows are untouched
    _assert_vl_shape(chart, rows)


def test_temporal_detected_by_value_shape() -> None:
    rows = [{"bucket": "2024-01-01", "qty": 1}, {"bucket": "2024-02-01", "qty": 3}]
    chart = recommend_chart(rows)
    assert chart is not None and chart["kind"] == "line"


def test_share_question_with_four_categories_is_pie() -> None:
    chart = recommend_chart(WAREHOUSE_STOCK, "share of stock by warehouse")
    assert chart is not None
    assert chart["kind"] == "pie"
    vl = chart["vega_lite"]
    assert vl["mark"] == "arc"
    assert vl["encoding"]["theta"]["field"] == "stock_kg"
    assert vl["encoding"]["theta"]["type"] == "quantitative"
    assert vl["encoding"]["color"]["field"] == "warehouse"
    assert vl["encoding"]["color"]["type"] == "nominal"
    _assert_vl_shape(chart, WAREHOUSE_STOCK)


def test_same_data_without_share_word_is_bar() -> None:
    chart = recommend_chart(WAREHOUSE_STOCK, "stock by warehouse")
    assert chart is not None
    assert chart["kind"] == "bar"
    assert (chart["x"], chart["y"]) == ("warehouse", "stock_kg")
    enc = chart["vega_lite"]["encoding"]
    assert enc["x"]["field"] == "warehouse" and enc["x"]["type"] == "nominal"
    assert enc["y"]["field"] == "stock_kg" and enc["y"]["type"] == "quantitative"
    _assert_vl_shape(chart, WAREHOUSE_STOCK)


def test_twelve_skus_is_hbar() -> None:
    rows = [{"sku": f"SKU-{i:02d}", "revenue_myr": 100.0 + i} for i in range(12)]
    chart = recommend_chart(rows, "top 12 SKUs by revenue")
    assert chart is not None
    assert chart["kind"] == "hbar"
    enc = chart["vega_lite"]["encoding"]
    assert enc["y"]["field"] == "sku" and enc["y"]["type"] == "nominal"
    assert enc["x"]["field"] == "revenue_myr"
    _assert_vl_shape(chart, rows)


def test_long_labels_force_hbar() -> None:
    rows = [
        {"location": "Shah Alam Cold Room North", "util_pct": 91.5},
        {"location": "Klang", "util_pct": 40.0},
    ]
    chart = recommend_chart(rows)
    assert chart is not None and chart["kind"] == "hbar"


def test_two_measures_is_scatter() -> None:
    rows = [
        {"qty_kg": 10.0, "revenue_myr": 55.5},
        {"qty_kg": 20.0, "revenue_myr": 101.25},
        {"qty_kg": 30.0, "revenue_myr": 160.0},
    ]
    chart = recommend_chart(rows)
    assert chart is not None
    assert chart["kind"] == "scatter"
    assert (chart["x"], chart["y"]) == ("qty_kg", "revenue_myr")
    assert chart["vega_lite"]["mark"] == "point"
    _assert_vl_shape(chart, rows)


@pytest.mark.parametrize(
    "rows",
    [
        [{"sku": "SKU-1", "note": "fragile"}, {"sku": "SKU-2", "note": "cold"}],
        [{"label": "only"}],
        [{"sku": "A", "mixed": 1}, {"sku": "B", "mixed": "n/a"}],
    ],
)
def test_text_only_or_mixed_is_table(rows: list[dict[str, Any]]) -> None:
    chart = recommend_chart(rows)
    assert chart == {"kind": "table", "title": "Result"}
    assert "vega_lite" not in chart


def test_empty_rows_is_none() -> None:
    assert recommend_chart([]) is None
    assert chart_from_rows([]) is None


def test_share_question_with_negative_value_is_not_pie() -> None:
    rows = [dict(r) for r in WAREHOUSE_STOCK]
    rows[2]["stock_kg"] = -12.5
    chart = recommend_chart(rows, "share of stock by warehouse")
    assert chart is not None
    assert chart["kind"] != "pie"
    assert chart["kind"] == "bar"


def test_id_columns_are_not_measures() -> None:
    rows = [
        {"location_id": 1, "qty": 5.5},
        {"location_id": 2, "qty": 7.5},
        {"location_id": 3, "qty": 9.5},
    ]
    chart = recommend_chart(rows)
    # One measure and no dimension: no scatter of an id against qty.
    assert chart is not None and chart["kind"] == "table"


def test_chart_from_rows_delegates_to_recommender() -> None:
    assert chart_from_rows(WAREHOUSE_STOCK, "share of stock") == recommend_chart(
        WAREHOUSE_STOCK, "share of stock"
    )


# ------------------------------------------------------- end to end: envelope


def _numbers_in(text: str) -> set[float]:
    return {float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", text)}


def test_live_answer_envelope_carries_grounded_chart() -> None:
    rows = [
        {"warehouse": "Shah Alam", "stock_kg": 4217.35},
        {"warehouse": "Klang", "stock_kg": 3198.65},
        {"warehouse": "Penang", "stock_kg": 1873.45},
        {"warehouse": "Johor Bahru", "stock_kg": 961.55},
    ]
    resp = AskResponse.model_validate(
        {
            "answer": "Shah Alam holds the most stock at 4,217.35 kg.",
            "audit_id": "aud_viz01",
            "route": "query_skill",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "sql_used": "SELECT warehouse, SUM(qty_kg) AS stock_kg FROM inventory GROUP BY 1",
            "rows": rows,
            "chart_spec": {
                "type": "bar",
                "x_label": "warehouse",
                "y_label": "stock_kg",
                "title": "Stock share",
            },
        }
    )
    env = map_ask_response_to_envelope(
        resp, question="What is the share of stock by warehouse?"
    )
    assert_envelope_valid(env)
    assert env["text"] == "Shah Alam holds the most stock at 4,217.35 kg."
    assert env["rows"] == rows
    assert env["badge"] != "ABSTAIN"
    assert env["abstained"] is False
    value_nums = {v["value"] for v in env["values"]}
    assert {r["stock_kg"] for r in rows} <= value_nums

    chart = env["chart"]
    assert chart["kind"] == "pie"
    assert chart["title"] == "Stock share"
    assert _vl_fields(chart) <= set(env["rows"][0].keys())
    dumped = json.dumps({k: v for k, v in chart.items() if k != "value"})
    dumped = dumped.replace(VEGA_LITE_SCHEMA, "")
    for r in rows:
        assert r["stock_kg"] not in _numbers_in(dumped)
    assert "values" not in chart["vega_lite"]["data"]


def test_live_bignum_value_is_the_only_number_in_the_chart() -> None:
    rows = [{"revenue_myr": 128400.75}]
    resp = AskResponse.model_validate(
        {
            "answer": "Total revenue was RM 128,400.75.",
            "audit_id": "aud_viz01_bn",
            "route": "certified_metric",
            "provenance": {"badge": "certified_metric"},
            "sql_used": "SELECT SUM(revenue_myr) AS revenue_myr FROM t",
            "rows": rows,
        }
    )
    env = map_ask_response_to_envelope(resp, question="What was total revenue?")
    assert_envelope_valid(env)
    assert env["text"] == "Total revenue was RM 128,400.75."
    assert env["rows"] == rows
    assert env["badge"] == "L0_CERTIFIED"
    assert 128400.75 in {v["value"] for v in env["values"]}
    chart = env["chart"]
    assert chart["kind"] == "bignum"
    assert chart["value"] == 128400.75
    rest = json.dumps({k: v for k, v in chart.items() if k != "value"})
    assert "128400.75" not in rest


def test_abstain_response_has_no_chart_and_passes_e14() -> None:
    resp = AskResponse.model_validate(
        {
            "answer": "I cannot answer that from the data this Space holds.",
            "audit_id": "aud_viz01_abs",
            "route": "abstain",
            "abstained": True,
            "badge": "abstain",
            "rows": [{"warehouse": "Klang", "stock_kg": 10.5}],
            "chart_spec": {"type": "bar", "x_label": "warehouse", "y_label": "stock_kg"},
        }
    )
    env = map_ask_response_to_envelope(resp, question="share of stock by warehouse")
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["chart"] is None
    assert env["rows"] == []
    assert env["values"] == []
    assert "cannot answer" in env["text"]
    assert_envelope_valid(env)


def test_space_boundary_refusal_with_confident_badge_has_no_chart() -> None:
    """A manifest refusal that still carries rows and a confident badge."""
    resp = AskResponse.model_validate(
        {
            "answer": (
                "I cannot answer that here - this Space has no access to "
                "'transactions'. Ask in a Space that does, or request the grant."
            ),
            "audit_id": "aud_viz01_space",
            "route": "refused",
            "badge": "session",
            "sql_used": "SELECT warehouse, stock_kg FROM transactions",
            "rows": [
                {"warehouse": "Klang", "stock_kg": 10.5},
                {"warehouse": "Penang", "stock_kg": 20.5},
            ],
        }
    )
    env = map_ask_response_to_envelope(
        resp,
        space_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        session_id="ses_dddddddd",
        question="What is the share of stock by warehouse?",
    )
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["chart"] is None
    assert env["rows"] == []
    assert "no access to 'transactions'" in env["text"]
    assert_envelope_valid(env)


def test_build_envelope_drops_chart_on_abstain() -> None:
    env = build_answer_envelope(
        answer_id="ans_viz01",
        text="Abstained.",
        badge="ABSTAIN",
        rows=WAREHOUSE_STOCK,
        chart=recommend_chart(WAREHOUSE_STOCK, "stock by warehouse"),
    )
    assert env["chart"] is None
    assert_envelope_valid(env)


def _answered(rows: list[dict[str, Any]], chart: dict[str, Any] | None) -> dict[str, Any]:
    return build_answer_envelope(
        answer_id="ans_viz01_ok",
        text="Stock by warehouse.",
        badge="L2_VALIDATED",
        sql_used="SELECT warehouse, stock_kg FROM inventory",
        rows=rows,
        chart=chart,
    )


def test_hand_built_abstain_envelope_with_chart_fails_e14() -> None:
    env = build_answer_envelope(
        answer_id="ans_viz01_hand", text="Abstained.", badge="ABSTAIN"
    )
    assert_envelope_valid(env)
    env["chart"] = {"kind": "bar", "x": "warehouse", "y": "stock_kg", "title": "x"}
    with pytest.raises(AssertionError, match="E14"):
        assert_envelope_valid(env)


def test_e14_rejects_chart_field_not_in_rows() -> None:
    env = _answered(WAREHOUSE_STOCK, recommend_chart(WAREHOUSE_STOCK))
    assert_envelope_valid(env)
    env["chart"] = copy.deepcopy(env["chart"])
    env["chart"]["vega_lite"]["encoding"]["y"]["field"] = "revenue_myr"
    with pytest.raises(AssertionError, match="E14"):
        assert_envelope_valid(env)


def test_e14_rejects_inline_data_values() -> None:
    env = _answered(WAREHOUSE_STOCK, recommend_chart(WAREHOUSE_STOCK))
    env["chart"] = copy.deepcopy(env["chart"])
    env["chart"]["vega_lite"]["data"] = {"values": WAREHOUSE_STOCK}
    with pytest.raises(AssertionError, match="E14"):
        assert_envelope_valid(env)


def test_e14_rejects_bignum_value_not_in_rows() -> None:
    rows = [{"revenue_myr": 42.5}]
    env = _answered(rows, recommend_chart(rows))
    assert_envelope_valid(env)
    env["chart"] = dict(env["chart"], value=99.5)
    with pytest.raises(AssertionError, match="E14"):
        assert_envelope_valid(env)
