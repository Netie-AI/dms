"""Ungranted relation names stay off the user-visible abstain fields.

The name is a whole identifier of any length. Ids are not rewritten.
The ticket line still carries the name, and it does not carry the
question, the SQL, or a database error.
"""

from __future__ import annotations

import json
import logging
import re

import dms_executor.pipeline_failure as tickets
import pytest
from dms_executor.abstain import build_abstain

_VISIBLE = ("abstain_reason", "assumptions", "message", "messages", "text")


def _visible(env: dict) -> str:
    picked = {key: env.get(key) for key in _VISIBLE}
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        unsure = receipt.get("unsure")
        if isinstance(unsure, dict):
            picked["why"] = unsure.get("why")
    loop = env.get("loop")
    if isinstance(loop, list):
        picked["outcomes"] = [
            item.get("outcome") for item in loop if isinstance(item, dict)
        ]
    return json.dumps(picked)


def _ticket(caplog: pytest.LogCaptureFixture) -> dict:
    lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.getMessage().startswith("pipeline_failure ")
    ]
    assert lines, caplog.text
    return json.loads(lines[-1].split("pipeline_failure ", 1)[1])


@pytest.fixture(autouse=True)
def _clear() -> None:
    tickets._reset_pipeline_failures()


@pytest.mark.parametrize("name", ["t", "po", "sku"])
def test_short_table_name_never_echoes(
    name: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fewer than four characters is still a name. Ids stay. Ticket keeps it."""
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    question = "how many pallets moved"
    sql = "SELECT 1"
    env = build_abstain(
        reason=f"ungranted:{name}",
        question=question,
        sql=sql,
        stage="followup",
        space_id=name,
        session_id=name,
        answer_id="ans_followup",
        text=f"Follow-up cannot use {name}.",
        assumptions=[
            f"validate:ungranted:{name}",
            "sku_count stays beside a shorter name",
        ],
        abstain_reason=f"ungranted:{name}",
    )
    env["message"] = f"ungranted:{name}"
    env["messages"] = [f"Follow-up cannot use {name}."]
    env = build_abstain(
        reason=f"ungranted:{name}",
        question=question,
        sql=sql,
        stage="followup",
        ask_id="ans_followup",
        demote=env,
    )
    blob = _visible(env)
    assert re.search(
        rf"(?i)(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", blob
    ) is None
    assert "sku_count" in blob
    assert env["space_id"] == name
    assert env["session_id"] == name
    assert env["answer_id"] == "ans_followup"
    ticket = _ticket(caplog)
    assert name in ticket["names"]
    raw = json.dumps(ticket)
    assert question not in raw
    assert sql not in raw
    assert "ticket_id" in env


def test_qualified_name_and_followup_text_are_scrubbed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    env = build_abstain(
        reason="ungranted_table:bronze.po",
        question="sheet total",
        stage="bronze",
        space_id="space-po",
        session_id="ses_po_1",
        text="ABSTAIN ungranted_table:bronze.po",
        assumptions=["validate:ungranted_table:bronze.po"],
        answer_id="ans_bronze",
    )
    env["message"] = "needs bronze.po"
    env = build_abstain(
        reason="ungranted_table:bronze.po",
        question="sheet total",
        stage="bronze",
        ask_id="ans_bronze",
        demote=env,
    )
    blob = _visible(env)
    assert "bronze.po" not in blob
    assert "po" not in blob
    assert env["space_id"] == "space-po"
    assert env["session_id"] == "ses_po_1"
    ticket = _ticket(caplog)
    assert "bronze.po" in ticket["names"]
    assert "sheet total" not in json.dumps(ticket)


def test_flag_off_adds_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    plain = build_abstain(
        reason="no_path",
        question="how many pallets moved",
        text="I cannot certify that.",
        assumptions=["no_path"],
        space_id="po",
        session_id="po",
        answer_id="ans_plain",
    )
    named = build_abstain(
        reason="ungranted:po",
        question="how many pallets moved",
        text="I cannot certify po.",
        assumptions=["validate:ungranted:po"],
        space_id="po",
        session_id="po",
        answer_id="ans_named",
    )
    assert "ticket_id" not in plain
    assert "ticket_id" not in named
    assert set(named) == set(plain)
    assert named["space_id"] == "po"
    assert "po" not in _visible(named)


def test_reason_codes_file_and_unparsed_stay() -> None:
    for code in ("ungranted:file", "ungranted:unparsed"):
        env = build_abstain(
            reason=code,
            text=code,
            assumptions=[code],
            abstain_reason=code,
            answer_id="ans_code",
        )
        assert code in env["text"]
        assert code in env["assumptions"][0]


def test_db_error_text_stays_out_of_the_ticket(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    caplog.set_level(logging.WARNING, logger="dms_executor.pipeline_failure")
    marker = "boom-secret-7c2e"
    question = "how many pallets moved"
    build_abstain(
        reason=f"db_error:ConversionException: {marker}",
        question=question,
        sql=f"SELECT '{marker}'",
        text=f"db_error: {marker}",
        assumptions=[marker],
        answer_id="ans_db",
    )
    ticket = _ticket(caplog)
    raw = json.dumps(ticket)
    assert "names" not in ticket
    assert marker not in raw
    assert question not in raw
    assert "SELECT" not in raw
