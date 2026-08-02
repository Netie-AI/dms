"""Demo warehouse + demo ask router tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from dms_executor.demo_ask import answer_demo_question
from dms_executor.demo_warehouse import ensure_demo_warehouse, total_outbound_revenue


@pytest.fixture()
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "dms_demo.duckdb"
    ensure_demo_warehouse(path)
    return path


def test_seed_idempotent(warehouse: Path) -> None:
    ensure_demo_warehouse(warehouse)
    ensure_demo_warehouse(warehouse)
    assert warehouse.is_file()


def test_total_revenue_stable(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    # clear seed cache so env path is used
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(warehouse)
    total = total_outbound_revenue(path=warehouse)
    # Hand-computed from seed outbound rows (schema v2)
    expected = (
        400 * 4.50
        + 350 * 4.50
        + 200 * 5.20
        + 1500 * 2.10
        + 800 * 2.10
        + 600 * 8.75
        + 220 * 12.00
        + 100 * 8.75
        + 180 * 6.40
        + 900 * 3.25
        + 250 * 8.75
        + 120 * 4.50
        + 400 * 2.10
    )
    assert total == pytest.approx(expected)


def test_divide_revenue_by_5(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(warehouse)
    total = total_outbound_revenue(path=warehouse)
    # Patch execute path used by answer_demo_question
    monkeypatch.setattr(
        "dms_executor.demo_ask.total_outbound_revenue",
        lambda path=None: total_outbound_revenue(path=warehouse),
    )
    env = answer_demo_question("Divide the revenue by 5")
    assert env["badge"] == "L2_VALIDATED"
    assert env["badge"] not in ("L0_CERTIFIED", "L1_GOVERNED_METRIC")
    values = {v["id"]: v["value"] for v in env["values"]}
    assert values["v_rev"] == pytest.approx(round(total, 2))
    assert values["v_scaled"] == pytest.approx(round(total / 5, 2))
    assert env["chart"]["kind"] == "bar"


def test_exclude_multiple_skus_and_bare_beta(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(warehouse)

    env = answer_demo_question(
        "excluding SKU-ALPHA and SKU-BETA, top 5 selling SKUs by revenue"
    )
    assert env["badge"] == "L2_VALIDATED"
    skus = [r["sku"].upper() for r in env["rows"]]
    assert "SKU-ALPHA" not in skus
    assert "SKU-BETA" not in skus
    assert "excluded:" in " ".join(env.get("assumptions") or []).lower() or any(
        "excluded" in a.lower() for a in (env.get("assumptions") or [])
    )
    text = env["text"].upper()
    assert "SKU-ALPHA" not in text
    assert "SKU-BETA" not in text

    bare = answer_demo_question("ignoring BETA top 5 selling SKUs by revenue")
    assert bare["badge"] == "L2_VALIDATED"
    bare_skus = [r["sku"].upper() for r in bare["rows"]]
    assert "SKU-BETA" not in bare_skus
    assert any("SKU-BETA" in a.upper() for a in (bare.get("assumptions") or []))


def test_removing_sku_and_bare_top_n(warehouse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(warehouse)

    env = answer_demo_question("removing the SKU-BETA what is the top 5 revnue")
    assert env["badge"] == "L2_VALIDATED"
    skus = [r["sku"].upper() for r in env["rows"]]
    assert "SKU-BETA" not in skus
    assert "SKU-BETA" not in env["text"].upper()

    top10 = answer_demo_question("what about top 10")
    assert top10["badge"] == "L2_VALIDATED"
    assert len(top10["rows"]) <= 10
    assert len(top10["rows"]) >= 1

    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(warehouse)
    env = answer_demo_question("What is the meaning of life?")
    assert env["badge"] == "ABSTAIN"
    assert env["values"] == []
    assert env["suggestions"]


def test_forecast_never_answers_with_historical_total(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same class as Cortex live-path forecast abstain — demo must not invent."""
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(warehouse)
    for q in (
        "what will next quarter revenue be",
        "forecast Q3 demand for our top SKU",
        "predict next month sales",
    ):
        env = answer_demo_question(q)
        assert env["badge"] == "ABSTAIN", q
        assert env["abstained"] is True
        assert env["values"] == []
        assert env.get("sql_used") in (None, "")