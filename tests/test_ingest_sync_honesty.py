"""INGEST-SYNC-01 (#75) - a receipt may not report success the upload did not have.

`ingested=N` only ever meant "landed in bronze". When ingest and serving are two
DuckDB files, bronze can land and chat still not see the table: the receipt said
`ingested=1`, the next question abstained, and nothing on screen connected the
two. The failure went to `logger.warning`, which is not a place a customer looks
(R-0011 - a silent fallback is a lie).

Three outcomes, deliberately not two. "not_attempted" is not a synonym for ok -
it means no serving warehouse is configured, so nothing was copied and nothing
was verified.

`maybe_sync_bronze_to_serving` returns None under pytest by design, so each state
is forced explicitly here rather than hoped for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from dms_core.triage import TriageReceipt
from dms_executor.warehouse_identity import SyncResult

CSV = b"sku,amount\nA,10\nB,20\n"


def _ingest(monkeypatch: pytest.MonkeyPatch, sync: Any, tmp_path: Path) -> dict[str, Any]:
    import dms_executor.batch_ingest as bi

    monkeypatch.setattr(bi, "maybe_sync_bronze_to_serving", lambda *_a, **_k: sync)
    return bi.ingest_batch([("sync_probe.csv", CSV)], path=tmp_path / "w.duckdb").to_dict()


def test_a_failed_sync_is_on_the_receipt_not_only_in_a_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect: ingested=1 with no hint that chat cannot see it."""
    failed = SyncResult(
        status="locked",
        ingest=tmp_path / "w.duckdb",
        serving=tmp_path / "serving.duckdb",
        error="serving file is locked by another process",
    )
    r = _ingest(monkeypatch, failed, tmp_path)

    assert r["ingested"] == 1, "precondition: bronze did land"
    assert r["serving_sync"] == "failed"
    assert r["chat_can_see_it"] is False
    # A non-technical reader has to understand it from the summary alone.
    assert "cannot see" in r["summary"].lower()
    assert "sync_bronze_to_serving" in r["summary"], "the summary must name the fix"


def test_not_attempted_is_reported_apart_from_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Third outcome. Nothing was copied AND nothing was verified.

    Reporting this as ok is exactly how the original silence began.
    """
    r = _ingest(monkeypatch, None, tmp_path)

    assert r["ingested"] == 1
    assert r["serving_sync"] == "not_attempted"
    assert r["chat_can_see_it"] is False, "not attempted is not a positive statement"
    assert "not attempted" in r["summary"].lower()
    assert "cannot see" not in r["summary"].lower(), "must not read as a failure either"


def test_a_successful_sync_says_so_positively(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Absence of bad news is an inference. The receipt makes it a statement."""
    ok = SyncResult(
        status="copied",
        ingest=tmp_path / "w.duckdb",
        serving=tmp_path / "serving.duckdb",
        copied=["bronze.sync_probe"],
    )
    r = _ingest(monkeypatch, ok, tmp_path)

    assert r["serving_sync"] == "ok"
    assert r["chat_can_see_it"] is True
    assert "chat can see it" in r["summary"].lower()


def test_one_warehouse_is_success_not_a_skipped_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`same_file` means chat reads the file ingest just wrote. Nothing to copy."""
    same = SyncResult(
        status="same_file", ingest=tmp_path / "w.duckdb", serving=tmp_path / "w.duckdb"
    )
    r = _ingest(monkeypatch, same, tmp_path)

    assert r["serving_sync"] == "not_needed"
    assert r["chat_can_see_it"] is True


def test_the_summary_stays_quiet_when_nothing_was_ingested() -> None:
    """No upload, no sync claim either way - the field must not invent news."""
    r = TriageReceipt(files_seen=1, ingested=0, need_attention=1).to_dict()

    assert r["serving_sync"] == "not_attempted"
    assert "not attempted" not in r["summary"].lower()
    assert "cannot see" not in r["summary"].lower()


def test_asking_about_an_unsynced_upload_abstains_on_the_envelope() -> None:
    """The P0 half: a green badge over an unsynced upload is the failure.

    A table that landed in bronze but never reached the serving warehouse is not
    there for the engine to read, so the executed query comes back with zero
    rows. Hard rule 12 already demotes that - this pins the guarantee to *this*
    scenario so a future change to the empty-result path cannot quietly reopen
    it, and asserts it where CLAUDE.md 10a requires: on the customer envelope
    through `assert_envelope_valid`, not on the SQL.

    Named honestly: the empty-result rule is what does the work here. This test
    does not prove the engine cannot answer an unsynced table from some *other*
    table - that would need a live two-warehouse stack, and it is not what this
    asserts.
    """
    from cortex_client.models import AskResponse
    from dms_executor import map_ask_response_to_envelope
    from dms_executor.envelope import assert_envelope_valid

    resp = AskResponse.model_validate(
        {
            "answer": "Total amount is 0.00.",
            "audit_id": "aud_unsynced",
            "route": "query_skill",
            "provenance": {"badge": "query_skill", "layer": "L2"},
            "sql_used": "SELECT SUM(amount) AS total FROM bronze.sync_probe",
            "rows": [],
        }
    )
    env = map_ask_response_to_envelope(resp, space_id="sp_x", session_id="ses_unsynced")

    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN", "a green badge over an unsynced upload is a P0"
    assert not env["values"], "an abstention must not ship an uncited figure"
    assert_envelope_valid(env)
