"""Warehouse tree must respect Space grants (SPACE-UI / F18)."""

from __future__ import annotations

from dms_executor.demo_grants import COMPANY_SCOPED, DEMO_SPACE_GRANTS
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


def test_company_default_is_wider_than_any_one_space_but_still_bounded() -> None:
    """No ``space_id`` is the company default ACL, not the absence of a check.

    Renamed from ``test_unscoped_lists_all_demo_tables``. The old name claimed the
    unscoped helper returns *all* demo tables while only asserting that two of them
    were present - so it read as a licence for the helper to return everything, and
    the helper duly did, including ``alerts``, which no Space grants (KB attack
    A-0007). The two assertions it did make are still true and still here: the company
    default spans both Spaces, so it is genuinely wider than either.

    What is added is the bound. A table no Space grants is ungrantable under every
    scope, named or not, so it must not appear here either.
    """
    tables = {t["table"] for t in list_warehouse_tables()}

    # Wider than any single Space: one table from each of the two.
    assert "transactions" in tables  # Finance only
    assert "shipments" in tables  # Warehouse Ops only

    granted_by_some_space = set(COMPANY_SCOPED)
    for _name, granted in DEMO_SPACE_GRANTS.values():
        granted_by_some_space |= set(granted)

    orphans = tables - granted_by_some_space
    assert not orphans, (
        f"the company default served {sorted(orphans)}, which no Space grants - "
        "every named Space refuses these, so no scope can justify returning them"
    )
