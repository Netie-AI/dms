"""NUM-HONESTY: numbers on the DMS answer envelope are the query's numbers.

Verifier findings inside dms#277 (CONNECT-ASK-01) and dms#231:

1. A SUM / AVG / MAX over no matching rows returns one NULL row. It was
   certified L2 with text ``total=None``. It is now a named ABSTAIN
   (``null_result``) with no figure. COUNT never returns NULL, so a COUNT
   over no rows still answers 0.
2. A DECIMAL(38,10) result reached the envelope as ``Decimal`` (or, over the
   HTTP wire, as its ``str``), was not harvested into ``values[]``, and the
   rendered ``7.0000000000`` fell to E4 "prose figure not in query result".
   Rows, values and text now share one encoding (Decimal -> float, -0.0 -> 0).
3. ``values[]`` holds distinct figures. ``assert_envelope_valid`` does not
   require it to mirror rows: E4 asks that every prose figure be *in*
   ``values[]``, E13 that include rows equal envelope rows. A 6-row answer
   with 3 distinct figures answers with all 6 rows. No change.

Every case posts ``POST /v1/chat/ask`` on the SPACE-GEN-01 rig (fake Cortex
executes the submitted SQL on a real DuckDB lake) or maps a contract
``AskResponse``, and asserts badge, rendered text and rows. The hostile cases
keep E4 strict: a figure in text that is not in the rows still abstains.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskResponse
from cortex_contract.execution import QueryResult
from dms_executor import map_ask_response_to_envelope
from dms_executor.envelope import (
    assert_envelope_valid,
    build_answer_envelope,
    format_cell,
    normalize_cell,
    null_result_reason,
    numeric_cell,
)
from dms_executor.manifest import ManifestMinter
from test_space_gen_01 import _ask, _reasons, _RecordingCortex, _rig, minter  # noqa: F401

JOIN = (
    "FROM bronze.financial_account a JOIN bronze.financial_district d "
    "ON a.district_id = d.district_id "
)
TOTAL_Q = "What is the total account_id in the district named {d}?"
COUNT_Q = "How many accounts are in the district named Atlantis?"


def _sum_sql(expr: str, district: str) -> str:
    return f"SELECT {expr} AS total_account_id {JOIN}WHERE d.a2 = '{district}'"


def _gen(tmp_path: Path, mint: ManifestMinter, sql: str) -> Any:
    return _rig(tmp_path, mint, {"query_sql": sql, "plan_source": "ontology_plan"})


class _WireCortex(_RecordingCortex):
    """Submit result round-tripped through JSON, as the HTTP client sees it.

    A ``Decimal`` cell crosses the wire as its ``str`` (``"7.0000000000"``).
    """

    def submit(self, req: Any) -> QueryResult:
        res = super().submit(req)
        return QueryResult.model_validate_json(res.model_dump_json())


def _wire(rig: Any) -> Any:
    wire = _WireCortex(warehouse=rig.cortex.warehouse, payload=rig.cortex.payload)
    rig.client.app.state.ask_service._cortex = wire
    rig.client.app.state.cortex = wire
    rig.cortex = wire
    return rig


# --- (1) NULL result ---------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "question"),
    [
        ("SUM(CAST(a.account_id AS DOUBLE))", TOTAL_Q),
        ("SUM(CAST(a.account_id AS DECIMAL(38,10)))", TOTAL_Q),
        ("AVG(CAST(a.account_id AS DOUBLE))", TOTAL_Q.replace("total", "average")),
        ("MAX(CAST(a.account_id AS DOUBLE))", TOTAL_Q.replace("total", "highest")),
    ],
)
def test_null_aggregate_over_no_rows_is_named_abstain_no_figure(
    tmp_path: Path, minter: ManifestMinter, expr: str, question: str  # noqa: F811
) -> None:
    rig = _gen(tmp_path, minter, _sum_sql(expr, "Atlantis"))
    env = _ask(rig, question.format(d="Atlantis"))
    assert env["badge"] == "ABSTAIN", (env["badge"], env["text"])
    assert env["abstained"] is True
    text = str(env["text"])
    assert "None" not in text, text
    assert "gap: null_result" in text, text
    assert "no rows matched" in text, text
    # Not an invented zero either.
    assert "=0" not in text and " 0 " not in text, text
    assert env["rows"] == []
    assert env["values"] == []
    assert "null_result" in _reasons(env)
    # The query did run: this is an honest empty, not a refusal.
    assert len(rig.cortex.executed) == 1


def test_null_measure_beside_a_group_label_is_named_abstain(
    tmp_path: Path, minter: ManifestMinter  # noqa: F811
) -> None:
    sql = (
        "SELECT 'Atlantis' AS district, SUM(CAST(a.account_id AS DOUBLE)) "
        f"AS total_account_id {JOIN}WHERE d.a2 = 'Atlantis'"
    )
    env = _ask(_gen(tmp_path, minter, sql), TOTAL_Q.format(d="Atlantis"))
    # Either the grain gate or the null gate abstains; never an L2 over None.
    assert env["badge"] == "ABSTAIN", (env["badge"], env["text"])
    assert "None" not in str(env["text"])
    assert env["rows"] == [] and env["values"] == []


def test_count_over_no_rows_answers_zero(
    tmp_path: Path, minter: ManifestMinter  # noqa: F811
) -> None:
    sql = f"SELECT COUNT(*) AS account_count {JOIN}WHERE d.a2 = 'Atlantis'"
    env = _ask(_gen(tmp_path, minter, sql), COUNT_Q)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env["text"], env["assumptions"])
    assert env["abstained"] is False
    assert env["rows"] == [{"account_count": 0}]
    assert "account_count=0" in str(env["text"])
    assert [v["value"] for v in env["values"]] == [0.0]


# --- (2) DECIMAL(38,10) and -0.0 ---------------------------------------------


@pytest.mark.parametrize("wire", [False, True], ids=["decimal_object", "http_wire_str"])
@pytest.mark.parametrize(
    ("expr", "want", "shown"),
    [
        # 1 + 2 + 4 over Prague, as DECIMAL(38,10).
        ("SUM(CAST(a.account_id AS DECIMAL(38,10)))", 7.0, ("7.0", "7.0000000000")),
        (
            "CAST(SUM(CAST(a.account_id AS DECIMAL(38,10))) / 8 AS DECIMAL(38,10))",
            0.875,
            ("0.875", "0.8750000000"),
        ),
        # Signed zero: DECIMAL -0 and DOUBLE -0.0.
        (
            "CAST(SUM(CAST(a.account_id AS DECIMAL(38,10))) * 0 * -1 AS DECIMAL(38,10))",
            0.0,
            ("0.0", "0E-10", "0.0000000000"),
        ),
        ("CAST('-0.0' AS DOUBLE) + 0 * SUM(CAST(a.account_id AS DOUBLE))", 0.0, ("0.0",)),
    ],
)
def test_decimal_and_negative_zero_answer_l2_with_matching_text(
    tmp_path: Path,
    minter: ManifestMinter,  # noqa: F811
    expr: str,
    want: float,
    shown: tuple[str, ...],
    wire: bool,
) -> None:
    rig = _gen(tmp_path, minter, _sum_sql(expr, "Prague"))
    if wire:
        rig = _wire(rig)
    env = _ask(rig, TOTAL_Q.format(d="Prague"))
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env["text"], env["assumptions"])
    assert env["abstained"] is False
    assert rig.cortex.executed, "the SQL never reached the (wire) Cortex"
    rows = env["rows"]
    assert len(rows) == 1 and list(rows[0]) == ["total_account_id"], rows
    cell = rows[0]["total_account_id"]
    assert numeric_cell(cell) == want, cell
    text = str(env["text"])
    # The text shows the row's cell, exactly as the row holds it.
    assert f"total_account_id={format_cell(cell)}" in text, (text, cell)
    assert format_cell(cell) in shown, (text, cell)
    assert "-0" not in text, text
    assert "None" not in text
    assert want in [v["value"] for v in env["values"]], env["values"]
    assert "prose figure not in query result" not in _reasons(env)


def test_normalize_cell_encodings() -> None:
    assert normalize_cell(Decimal("12.3400000000")) == 12.34
    assert str(normalize_cell(Decimal("-0E-10"))) == "0.0"
    assert str(normalize_cell(-0.0)) == "0.0"
    assert normalize_cell(3) == 3 and isinstance(normalize_cell(3), int)
    assert normalize_cell("0012") == "0012"
    assert numeric_cell("12.3400000000") == 12.34
    assert numeric_cell("10") is None  # ids/codes travel as plain-integer text
    assert numeric_cell(True) is None
    assert numeric_cell(float("nan")) is None
    big = Decimal("12345678901234567890.1234567890")
    assert "e" not in format_cell(big).lower()


def test_null_result_reason_rules() -> None:
    assert null_result_reason("How many?", [{"n": None}])
    assert null_result_reason("list them", [{"a": None, "b": None}])
    assert null_result_reason("What is the total?", [{"d": "X", "t": None}])
    assert null_result_reason("How many?", [{"n": 0}]) is None
    assert null_result_reason("list emails", [{"name": "A", "email": None}]) is None
    assert null_result_reason("How many?", [{"n": 3, "note": None}]) is None


# --- (3) values[] is distinct, rows are whole ----------------------------------


def test_six_rows_three_distinct_figures_answer_with_all_rows() -> None:
    rows = [{"sku": f"S{i}", "qty": q} for i, q in enumerate([5, 5, 7, 7, 9, 9])]
    resp = AskResponse.model_validate(
        {
            "answer": "Stock by SKU.",
            "audit_id": "aud_numh_6",
            "route": "certified_metric",
            "provenance": {"badge": "certified_metric", "layer": "L0"},
            "sql_used": "SELECT sku, qty FROM stock ORDER BY sku",
            "rows": rows,
        }
    )
    env = map_ask_response_to_envelope(resp, session_id="ses_numh_6")
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED", (env["text"], env["assumptions"])
    assert env["text"] == "Stock by SKU."
    assert env["rows"] == rows
    assert sorted(v["value"] for v in env["values"]) == [5.0, 7.0, 9.0]


# --- hostile: E4 stays strict ------------------------------------------------


def _mapped(answer: str, rows: list[dict[str, Any]], tag: str, **kw: Any) -> dict[str, Any]:
    resp = AskResponse.model_validate(
        {
            "answer": answer,
            "audit_id": f"aud_numh_{tag}",
            "route": "certified_metric",
            "provenance": {"badge": "certified_metric", "layer": "L0"},
            "sql_used": "SELECT SUM(spend) AS total FROM spend",
            "rows": rows,
        }
    )
    env = map_ask_response_to_envelope(resp, session_id=f"ses_numh_{tag}", **kw)
    assert_envelope_valid(env)
    return env


def test_text_figure_not_in_decimal_rows_still_abstains() -> None:
    env = _mapped("Total spend was 99.99.", [{"total": "12.3400000000"}], "h1")
    assert env["badge"] == "ABSTAIN"
    assert "99.99" not in env["text"]
    assert env["rows"] == [] and env["values"] == []


def test_text_figure_matching_decimal_rows_answers() -> None:
    env = _mapped("Total spend was 12.34.", [{"total": "12.3400000000"}], "h2")
    assert env["badge"] == "L0_CERTIFIED", (env["text"], env["assumptions"])
    assert env["text"] == "Total spend was 12.34."
    assert env["rows"] == [{"total": "12.3400000000"}]
    assert [v["value"] for v in env["values"]] == [12.34]


def test_null_row_under_a_confident_engine_badge_abstains() -> None:
    env = _mapped(
        "Total spend was None.",
        [{"total": None}],
        "h3",
        question="What is our total spend?",
    )
    assert env["badge"] == "ABSTAIN"
    assert "gap: null_result" in env["text"]
    assert "None" not in env["text"]
    assert env["rows"] == [] and env["values"] == []


def test_hostile_decimal_rows_with_orphan_prose_abstain_in_builder() -> None:
    env = build_answer_envelope(
        answer_id="a_numh_h4",
        text="Total is 12.34 and margin is 55.50.",
        badge="L2_VALIDATED",
        sql_used="SELECT SUM(x) AS total FROM t",
        rows=[{"total": Decimal("12.3400000000")}],
        audit_id="aud_numh_h4",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert "55.50" not in env["text"]
    assert env["rows"] == [] and env["values"] == []
