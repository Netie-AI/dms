"""#75: an upload whose serving sync failed must not report a bare ``ingested=N``.

Bronze landing and chat being able to read it are two different events. The copy into
the serving warehouse used to fail into a ``logger.warning`` and nowhere else, four
lines above a receipt that went on reporting ``ingested=N``. A customer uploaded a
file, was told it landed, asked a question, and got an abstention with nothing on
screen connecting the two.

R-0011: a degradation visible in a log line and nowhere in the output is a lie.
R-0001: these assert on the receipt the customer receives from
``POST /v1/studio/ingest-batch``, not on the internal ``maybe_sync_bronze_to_serving``
call. Asserting the internal call is necessary and insufficient - it was already being
made correctly; the defect was that its answer never reached the customer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dms_core.triage import SYNC_FAILED, SYNC_NOT_ATTEMPTED, SYNC_OK, ServingSync, TriageReceipt
from fastapi.testclient import TestClient

CSV = b"sku,qty\nSKU-A,3\nSKU-B,5\n"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DMS_ASK_MODE", "demo")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "wh.duckdb"))

    # Ingest is a mutation, so the gate correctly fails closed with no Cortex reachable
    # (gatekeeping.py: an ungated, unaudited write is not legitimate work being
    # refused). Stub it allowed, the same way tests/test_studio_space_ingest.py does -
    # what is under test here is the receipt, not the gate.
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        studio_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )

    from dms_api.app import create_app

    return TestClient(create_app())


def _upload(client: TestClient, name: str = "stock.csv") -> dict:
    resp = client.post(
        "/v1/studio/ingest-batch",
        files=[("files", (name, CSV, "text/csv"))],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_receipt_carries_a_serving_sync_state(client: TestClient) -> None:
    """The field must exist on the customer-facing response, always."""
    body = _upload(client)

    assert body["ingested"] >= 1
    assert body["serving_sync"] is not None, (
        "an omitted serving_sync reads as 'fine' - not attempted is a state, not an absence"
    )
    assert body["serving_sync"]["state"] in {SYNC_OK, SYNC_FAILED, SYNC_NOT_ATTEMPTED}


def test_not_attempted_is_distinct_from_success(client: TestClient) -> None:
    """Three outcomes, not two. Under pytest no serving warehouse is configured.

    Reporting "no chat warehouse configured" as ok would be the same lie in a quieter
    voice: the caller would infer chat can see the upload, and nothing said otherwise.
    """
    body = _upload(client)
    sync = body["serving_sync"]

    assert sync["state"] == SYNC_NOT_ATTEMPTED
    assert sync["visible_to_chat"] is False, (
        "not_attempted must not claim visibility - it is not success"
    )
    assert sync["detail"], "a state with no explanation cannot be acted on"


def test_a_failed_sync_says_so_in_the_summary_a_human_reads() -> None:
    """The summary line is what the Studio page shows. It must carry the bad news.

    Asserted on ``to_dict()`` because that is exactly what the route returns as
    ``summary`` - see ``apps/api/dms_api/routes/studio.py``.
    """
    receipt = TriageReceipt(
        files_seen=3,
        ingested=3,
        need_attention=0,
        ingest_id="ing_1",
        serving_sync=ServingSync(
            state=SYNC_FAILED,
            status="locked",
            detail="serving warehouse is held by another process",
            action="Run: python scripts/sync_bronze_to_serving.py",
        ),
    )

    body = receipt.to_dict()
    summary = body["summary"]

    assert "3 ingested" in summary
    assert "cannot see" in summary, (
        f"a failed publish must be legible in the summary, got: {summary!r}"
    )
    assert "sync_bronze_to_serving" in summary, "the operator action must be on the receipt"
    assert body["serving_sync"]["state"] == SYNC_FAILED
    assert body["serving_sync"]["visible_to_chat"] is False


def test_a_successful_sync_is_a_positive_statement_not_silence() -> None:
    """Absence of bad news must not be the only evidence of good news."""
    receipt = TriageReceipt(
        files_seen=1,
        ingested=1,
        need_attention=0,
        ingest_id="ing_2",
        serving_sync=ServingSync(state=SYNC_OK, status="copied", detail="1 table(s) published."),
    )

    body = receipt.to_dict()
    assert "chat can see them" in body["summary"]
    assert body["serving_sync"]["visible_to_chat"] is True


def test_nothing_ingested_says_nothing_about_publishing() -> None:
    """R-0005 adjacent: do not add noise to a receipt that has no news.

    "0 ingested, chat cannot see them" would be technically true and useless.
    """
    receipt = TriageReceipt(
        files_seen=1,
        ingested=0,
        need_attention=1,
        ingest_id="ing_3",
        serving_sync=ServingSync(state=SYNC_NOT_ATTEMPTED),
    )
    summary = receipt.to_dict()["summary"]
    assert "chat" not in summary.lower()


def test_a_table_chat_cannot_see_abstains_on_the_envelope(client: TestClient) -> None:
    """The other half of #75, asserted on the customer envelope (CLAUDE.md 10a, E1-E9).

    A green badge over an unsynced upload would be a P0. It is not one today, and this
    pins why: a table that never reached the serving warehouse is simply not there to
    answer from, so the ask path abstains rather than inventing a figure.

    That means the defect #75 fixes was never a wrong *answer* - it was a wrong
    *receipt*. The abstention was always correct and always unexplained. This test
    guards the half that was already right, so a later change cannot make the unsynced
    case answer confidently while the receipt work stays green.
    """
    from dms_executor.envelope import assert_envelope_valid

    resp = client.post(
        "/v1/chat/ask",
        json={"question": "what is the total quantity in an_unsynced_table_xyz?"},
    )
    assert resp.status_code == 200, resp.text
    env = resp.json()

    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN", (
        f"a table chat cannot see was answered under badge {env['badge']!r}"
    )
    assert env["values"] == [], "an abstention must carry no figure (E9)"


def test_a_failed_sync_never_reports_a_bare_ingested_count() -> None:
    """The exact defect, stated as an assertion.

    Pre-fix, this summary was "3 files - 3 ingested - 0 need attention" with the
    failure only in a log. That string is not false; it is just not the truth the
    customer needed.
    """
    failed = TriageReceipt(
        files_seen=3,
        ingested=3,
        need_attention=0,
        ingest_id="ing_4",
        serving_sync=ServingSync(
            state=SYNC_FAILED,
            status="locked",
            detail="held by another process",
            action="Run: python scripts/sync_bronze_to_serving.py",
        ),
    ).to_dict()

    assert failed["summary"] != (
        "3 files · 3 ingested · 0 need attention"
    ), "the receipt is still reporting a bare ingested count over a failed publish"
