"""A0007-01 (#72): omitting ``space_id`` must not skip the Space check.

The Space switcher's empty option is labelled "Company (default ACL)"
(``apps/ui/src/components/TopBar.tsx:53``), so a request with no ``space_id`` asks for
the company-wide scope. The server read it as "skip the check" instead, and the two
readings differ on exactly one table in the demo warehouse: ``alerts``, which **no**
Space grants. Every named Space refused it with a 403 while the unnamed read returned
its rows.

These assert on the HTTP response a client actually receives (R-0001), not on the
helper underneath, because the helper is reachable by four routes and the customer only
ever sees the response.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WAREHOUSE_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"

#: Granted by no Space in ``DEMO_SPACE_GRANTS``, and therefore ungrantable under any
#: scope. Derived rather than hardcoded in the test body so that adding it to a Space
#: makes these tests skip rather than fail with a misleading message.
ORPHAN_TABLE = "alerts"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "wh.duckdb"))
    from dms_api.app import create_app

    return TestClient(create_app())


def _orphan_is_still_orphaned() -> bool:
    from dms_executor.demo_grants import DEMO_SPACE_GRANTS

    return not any(ORPHAN_TABLE in granted for _n, granted in DEMO_SPACE_GRANTS.values())


def test_a_table_no_space_grants_is_refused_with_no_space_id(client: TestClient) -> None:
    """The regression. Pre-fix this returned 200 with rows; it must refuse.

    Verified able to fail: against the pre-fix commit the same request answers
    ``200`` with 5 rows, the first being an operational alert naming a location.
    """
    if not _orphan_is_still_orphaned():
        pytest.skip(f"{ORPHAN_TABLE} was granted to a Space; pick another orphan")

    resp = client.get(f"/v1/library/warehouse/{ORPHAN_TABLE}/preview", params={"limit": 500})

    assert resp.status_code == 403, (
        f"{ORPHAN_TABLE} is granted by no Space and every named Space refuses it, "
        f"but the company default served it: {resp.status_code}"
    )
    detail = resp.json()["detail"]
    assert detail["code"] == "warehouse_not_in_space"
    assert detail["scope"] == "company-default", (
        "the refusal must name the scope it applied - 'company-default' and 'unscoped' "
        "are different claims and the response should not be ambiguous"
    )


def test_the_refusal_is_structured_never_an_empty_200(client: TestClient) -> None:
    """CLAUDE.md hard rule 12 - an empty 200 reads as a table that has no rows."""
    resp = client.get(f"/v1/library/warehouse/{ORPHAN_TABLE}/preview", params={"limit": 500})
    assert resp.status_code != 200
    detail = resp.json()["detail"]
    assert set(detail) >= {"code", "message", "scope"}


def test_company_default_still_serves_what_a_space_grants(client: TestClient) -> None:
    """R-0005 - the control must refuse the ungranted table, not the Library page.

    The UI browses with no Space selected, so if this breaks, the hardening broke the
    product and the regression would arrive wearing the appearance of a win.
    """
    resp = client.get("/v1/library/warehouse/transactions/preview", params={"limit": 500})

    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"], "company default must still return rows for a granted table"
    assert body["scope"] == "company-default"


def test_a_named_space_still_refuses_another_spaces_table(client: TestClient) -> None:
    """The pre-existing check must survive the change that generalised it."""
    resp = client.get(
        "/v1/library/warehouse/transactions/preview",
        params={"limit": 500, "space_id": WAREHOUSE_OPS},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["scope"] == f"space:{WAREHOUSE_OPS}"


def test_a_granted_table_names_the_space_it_was_read_under(client: TestClient) -> None:
    resp = client.get(
        "/v1/library/warehouse/transactions/preview",
        params={"limit": 500, "space_id": FINANCE},
    )
    assert resp.status_code == 200
    assert resp.json()["scope"] == f"space:{FINANCE}"


def test_a_nonexistent_table_does_not_reveal_that_it_is_nonexistent(
    client: TestClient,
) -> None:
    """403 for both "no such table" and "not granted" - otherwise the pair is an oracle.

    404 for a missing table and 403 for a real-but-ungranted one lets a caller map the
    warehouse by status code alone, without reading a single row.
    """
    missing = client.get("/v1/library/warehouse/no_such_table_xyz/preview")
    ungranted = client.get(f"/v1/library/warehouse/{ORPHAN_TABLE}/preview")

    assert missing.status_code == ungranted.status_code == 403
    assert missing.json()["detail"]["code"] == ungranted.json()["detail"]["code"]


def test_an_unreachable_gate_does_not_refuse_the_read_but_the_scope_still_holds(
    client: TestClient,
) -> None:
    """The two controls fail differently, on purpose.

    ``gatekeeping.py`` states the posture: an unreachable gate must not refuse a read
    the way it refuses a write. These tests run with no Cortex, so every request here
    already has ``gate_unavailable``. That must cost the audit record, never the
    boundary - so a granted table still answers and an ungranted one still refuses,
    both with no gate reachable.
    """
    assert os.environ.get("CORTEX_URL") in (None, "", "http://127.0.0.1:8010")

    granted = client.get("/v1/library/warehouse/transactions/preview", params={"limit": 5})
    ungranted = client.get(f"/v1/library/warehouse/{ORPHAN_TABLE}/preview", params={"limit": 5})

    assert granted.status_code == 200, "an unreachable gate refused a legitimate read"
    assert ungranted.status_code == 403, "an unreachable gate dropped the scope check"
