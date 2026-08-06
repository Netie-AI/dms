"""Warehouse tree must respect Space grants (SPACE-UI / F18)."""

from __future__ import annotations

from dms_executor.demo_grants import DEMO_SPACE_GRANTS
from dms_executor.warehouse_browse import list_warehouse_tables


def test_finance_warehouse_excludes_shipments() -> None:
    finance = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    tables = {t["table"] for t in list_warehouse_tables(space_id=finance)}
    assert "transactions" in tables
    assert "shipments" not in tables
    assert tables <= set(DEMO_SPACE_GRANTS[finance][1])


def test_warehouse_ops_excludes_transactions() -> None:
    ops = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    tables = {t["table"] for t in list_warehouse_tables(space_id=ops)}
    assert "shipments" in tables
    assert "transactions" not in tables
    assert tables <= set(DEMO_SPACE_GRANTS[ops][1])


def test_unscoped_lists_all_demo_tables() -> None:
    tables = {t["table"] for t in list_warehouse_tables()}
    assert "transactions" in tables
    assert "shipments" in tables
