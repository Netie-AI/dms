"""BANK-02 (dms#269): GET /v1/audit/export - the auditor's record of every ask.

Asserted on what an auditor receives: the CSV or JSONL body and its headers, from
a real ``POST /v1/chat/ask`` through the real recording path. Not on the stored
row, not on a helper's return value (CLAUDE.md hard rules 10 and 10a).

Both required tests of the ticket are here and each fails on the parent commit
(R-0007), where the route does not exist and answers 404:

- ``test_a_planted_ask_is_exported_with_every_field``
- ``test_editing_a_ledger_entry_marks_the_export_unverified``

The Cortex in these tests keeps its ledger as a real hash chain, so "edit a ledger
entry" is an actual edit that ``verify_ledger`` actually detects, not a flag
flipped on a mock.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from cortex_client.gate import ComplianceDecision
from cortex_client.models import AskResponse, LedgerVerifyResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_api import settings as settings_mod
from dms_api.app import create_app
from dms_core.ask import AskServiceError
from dms_core.control_plane.ask_audit import (
    InMemoryAskAuditStore,
    record_from_error,
    scrub,
)
from dms_core.control_plane.ask_audit_export import COLUMNS, neutralise_formula
from dms_executor import Executor, tables_read_by_sql
from dms_executor.manifest import ManifestMinter, SessionAcl
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WAREHOUSE_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
DEPLOYMENT_ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

WHERE_IS_SKU = "Where is SKU-00397 stored?"
REVENUE = "What was our revenue last month?"
_NEEDS = {REVENUE: "transactions", WHERE_IS_SKU: "locations"}


@pytest.fixture(scope="module")
def _warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One scratch warehouse for the module: seeding it per test costs ~5s each."""
    return tmp_path_factory.mktemp("bank02") / "wh.duckdb"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch, _warehouse: Path):
    """Live ask, no fallback, no database.

    Each ``create_app()`` these tests make starts an Executor that probes OpenVault at
    the settings URL (default http://127.0.0.1:5000); that probe is reported offline
    here. This runs AFTER ``dms_api`` is imported, and importing it runs a
    module-level ``create_app()`` that probes before any fixture exists, so this does
    not make the module vault-free. That root cause predates BANK-02.
    """
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setattr("dms_executor.probe_openvault", lambda **_kw: (None, ""))
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(_warehouse))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings_mod.get_settings.cache_clear()
    yield
    settings_mod.get_settings.cache_clear()


# --- a Cortex whose ledger is a hash chain -----------------------------------


def _link(prev: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True)).encode()).hexdigest()


@dataclass
class LedgerCortex:
    """Enforces the bound manifest like Cortex, and logs every ask on a real chain."""

    chain: list[dict[str, Any]] = field(default_factory=list)
    _bound: dict[str, set[str]] = field(default_factory=dict)
    verify_raises: bool = False
    #: "normal"; "abstain_after_sql" (the engine runs SQL, then abstains); "no_sql"
    #: (an answer that reports no SQL at all).
    mode: str = "normal"

    def _append(self, body: dict[str, Any]) -> str:
        prev = self.chain[-1]["hash"] if self.chain else ""
        entry_id = f"led_{len(self.chain) + 1}"
        self.chain.append({"id": entry_id, "body": body, "prev": prev, "hash": _link(prev, body)})
        return entry_id

    def submit(self, req: Any) -> QueryResult:
        self._bound[req.manifest.session_id] = set(req.manifest.row_predicates)
        return QueryResult(ok=True, status="bound", run_id="run-1")

    def ledger_append(self, req: Any) -> Any:
        """Append like Cortex: the response carries the entry's seq (contract LedgerEntry)."""
        from cortex_client.models import LedgerAppendResponse

        entry_id = self._append({"event": req.event_type, "payload": req.payload})
        return LedgerAppendResponse(
            entry_id=entry_id, hash=self.chain[-1]["hash"], seq=len(self.chain)
        )

    def ask(self, req: Any) -> AskResponse:
        if self.mode == "abstain_after_sql":
            entry = self._append({"event": "ask.executed", "sql": "SELECT SUM(amount)"})
            return AskResponse.model_validate(
                {
                    "answer": "I could not stand behind that figure.",
                    "abstained": True,
                    "badge": "abstain",
                    "route": "abstain",
                    "sql_used": "SELECT SUM(amount) FROM transactions",
                    "rows": [{"total": 42.0}],
                    "audit_id": entry,
                }
            )
        if self.mode == "pii_sql":
            entry = self._append({"event": "ask.executed"})
            return AskResponse.model_validate(
                {
                    "answer": "Done.",
                    "abstained": False,
                    "badge": "certified",
                    "sql_used": "SELECT name FROM customers WHERE email = 'alice.tan@example.com'",
                    "rows": [{"orders": 2}],
                    "audit_id": entry,
                    "route": "sql",
                }
            )
        if self.mode == "no_sql":
            entry = self._append({"event": "ask.answered"})
            return AskResponse.model_validate(
                {
                    "answer": "Done.",
                    "abstained": False,
                    "badge": "certified",
                    "rows": [],
                    "route": "sql",
                    "audit_id": entry,
                }
            )
        needed = _NEEDS[req.question]
        readable = self._bound.get(req.session_id, set())
        if needed not in readable:
            entry = self._append({"event": "ask.refused", "question": req.question})
            return AskResponse.model_validate(
                {
                    "answer": f"I cannot answer that here: no access to {needed!r} in this Space.",
                    "abstained": True,
                    "badge": "abstain",
                    "rows": [],
                    "route": "refused",
                    "audit_id": entry,
                }
            )
        sql = f"SELECT * FROM {needed}"
        entry = self._append({"event": "ask.executed", "sql": sql})
        return AskResponse.model_validate(
            {
                "answer": "RM 128,400.00 across 412 outbound movements.",
                "abstained": False,
                "badge": "certified",
                "sql_used": sql,
                "rows": [{"revenue_myr": 128400.0}, {"revenue_myr": 9.5}],
                "audit_id": entry,
                "route": "sql",
            }
        )

    def verify_ledger(self) -> LedgerVerifyResponse:
        if self.verify_raises:
            raise RuntimeError("cortex unreachable at http://cortex.internal:8010")
        prev = ""
        for i, e in enumerate(self.chain, start=1):
            if e["prev"] != prev or e["hash"] != _link(prev, e["body"]):
                return LedgerVerifyResponse(ok=False, first_break=e["id"], checked=i)
            prev = e["hash"]
        return LedgerVerifyResponse(ok=True, checked=len(self.chain))


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-08-02T00:00:00+00:00",
            expires_at="2026-08-02T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def _real_stack(minter: ManifestMinter) -> tuple[TestClient, Any, LedgerCortex]:
    """The real route, real Executor and real envelope builder over a fake Cortex."""
    cortex = LedgerCortex()
    app = create_app()
    app.state.cortex = cortex
    app.state.ask_service = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    return TestClient(app), app, cortex


class StubAsk:
    """An ask service that returns or raises exactly what a test plants."""

    def __init__(self, outcome: dict[str, Any] | Exception) -> None:
        self.outcome = outcome

    def live_ask(self, question: str, **_: Any) -> dict[str, Any]:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _stub_stack(
    outcome: dict[str, Any] | Exception, cortex: Any | None = None
) -> tuple[TestClient, Any]:
    app = create_app()
    app.state.cortex = cortex if cortex is not None else LedgerCortex()
    app.state.ask_service = StubAsk(outcome)
    return TestClient(app), app


def _envelope(**over: Any) -> dict[str, Any]:
    from dms_executor import build_answer_envelope

    kwargs: dict[str, Any] = {
        "answer_id": "ans_stub",
        "text": "Found 1 row(s).",
        "badge": "L2_VALIDATED",
        "sql_used": "SELECT SUM(qty) AS q FROM inventory",
        "rows": [{"q": 7}],
        "audit_id": "led_stub",
        "space_id": FINANCE,
        "ask_mode": "live",
    }
    kwargs.update(over)
    return build_answer_envelope(**kwargs)


def _ask(client: TestClient, question: str, **body: Any) -> Any:
    return client.post("/v1/chat/ask", json={"question": question, "space_id": FINANCE, **body})


def _export(client: TestClient, **params: str) -> Any:
    return client.get("/v1/audit/export", params=params)


def _csv_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text, newline="")))


# --- the ticket's required tests ----------------------------------------------


def test_a_planted_ask_is_exported_with_every_field(minter: ManifestMinter) -> None:
    client, _app, _cortex = _real_stack(minter)

    asked = _ask(client, WHERE_IS_SKU, session_id="ses_bank02")
    assert asked.status_code == 200
    answer = asked.json()
    assert answer["badge"] == "L0_CERTIFIED" and answer["abstained"] is False

    r = _export(client, format="csv")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    rows = _csv_rows(r.text)
    assert len(rows) == 1
    row = rows[0]
    assert list(row) == list(COLUMNS)
    # timestamp
    stamp = datetime.fromisoformat(row["asked_at"])
    assert stamp.tzinfo is not None
    assert abs((datetime.now(UTC) - stamp).total_seconds()) < 120
    # actor, and whether it is a person or the deployment identity
    assert row["actor"] == DEPLOYMENT_ACTOR
    assert row["actor_kind"] == "deployment"
    # question text, executed SQL, tables read
    assert row["question"] == WHERE_IS_SKU
    assert row["executed_sql"] == "SELECT * FROM locations"
    assert row["executed_sql"] == answer["sql_used"]
    assert row["tables_read"] == "locations"
    # badge, abstain reason, row count, ledger pointer
    assert row["badge"] == "validated"
    assert row["badge_level"] == "L0_CERTIFIED"
    assert row["abstain_reason"] == ""
    assert row["row_count"] == "2" and int(row["row_count"]) == len(answer["rows"])
    assert row["cortex_entry_id"] == "led_1" and row["cortex_entry_id"] == answer["audit_id"]
    assert row["space_id"] == FINANCE
    assert row["ask_mode"] == "live"
    # the chain verified, and the file says so
    assert row["export_verified"] == "true"
    assert row["ledger_verify_status"] == "ok"
    assert row["ledger_first_break"] == ""
    assert row["ledger_entries_checked"] == "1"
    assert datetime.fromisoformat(row["verified_at"]).tzinfo is not None
    assert r.headers["x-audit-export-verified"] == "true"
    assert "not a person" in r.headers["x-audit-actor-basis"]


def test_editing_a_ledger_entry_marks_the_export_unverified(minter: ManifestMinter) -> None:
    client, _app, cortex = _real_stack(minter)
    assert _ask(client, WHERE_IS_SKU, session_id="ses_a").status_code == 200
    assert _ask(client, WHERE_IS_SKU, session_id="ses_b").status_code == 200

    before = _export(client, format="csv")
    assert before.headers["x-audit-export-verified"] == "true"
    assert {r["export_verified"] for r in _csv_rows(before.text)} == {"true"}

    # Edit the first entry in place. The chain no longer recomputes.
    cortex.chain[0]["body"]["sql"] = "SELECT 1 -- edited after the fact"

    after = _export(client, format="csv")

    assert after.status_code == 200
    rows = _csv_rows(after.text)
    assert len(rows) == 2, "an unverified export still carries the rows; it says it is unverified"
    for row in rows:
        assert row["export_verified"] == "false"
        assert row["ledger_verify_status"] == "break"
        assert row["ledger_first_break"] == "led_1"
    assert after.headers["x-audit-export-verified"] == "false"
    assert after.headers["x-audit-ledger-verify"] == "break"


# --- the other outcomes an auditor must see ------------------------------------


def test_an_abstain_is_exported_as_an_abstain_with_its_reason(minter: ManifestMinter) -> None:
    client, _app, _cortex = _real_stack(minter)

    answer = _ask(client, REVENUE, space_id=WAREHOUSE_OPS, session_id="ses_ops").json()
    assert answer["abstained"] is True and answer["badge"] == "ABSTAIN"

    row = _csv_rows(_export(client).text)[0]

    assert row["badge"] == "abstain"
    assert row["badge_level"] == "ABSTAIN"
    assert "transactions" in row["abstain_reason"], row["abstain_reason"]
    assert row["row_count"] == "0"
    assert row["executed_sql"] == "" and row["tables_read"] == ""
    assert row["space_id"] == WAREHOUSE_OPS


def test_an_ask_that_errored_is_exported_as_an_error() -> None:
    client, _app = _stub_stack(AskServiceError("submit_failed", "status='failed' error=boom"))

    asked = _ask(client, "What is total spend?")
    assert asked.status_code == 502

    row = _csv_rows(_export(client).text)[0]

    assert row["badge"] == "error"
    assert row["abstain_reason"] == "http_502:submit_failed"
    assert row["row_count"] == "0"
    assert row["executed_sql"] == "" and row["cortex_entry_id"] == ""
    assert row["question"] == "What is total spend?"


def test_a_label_that_is_not_a_ledger_entry_is_not_exported_as_one() -> None:
    """An abstain carries ``audit_id == answer_id``: a label, not an entry."""
    env = _envelope(
        answer_id="ans_gen01_abstain",
        badge="ABSTAIN",
        abstained=True,
        sql_used=None,
        rows=[],
        audit_id=None,
        text="I could not plan that.",
        assumptions=["GEN-01: ledger_entry_missing"],
    )
    assert env["audit_id"] == "ans_gen01_abstain"
    client, _app = _stub_stack(env)
    _ask(client, "an unanswerable question")

    row = _csv_rows(_export(client).text)[0]

    assert row["cortex_entry_id"] == ""
    assert row["abstain_reason"].startswith("GEN-01: ledger_entry_missing; I could not plan")


def test_jsonl_carries_the_same_fields_one_ask_per_line(minter: ManifestMinter) -> None:
    client, _app, _cortex = _real_stack(minter)
    _ask(client, WHERE_IS_SKU, session_id="ses_1")
    _ask(client, REVENUE, space_id=WAREHOUSE_OPS, session_id="ses_2")

    r = _export(client, format="jsonl")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = r.text.splitlines()
    assert len(lines) == 2
    first, second = (json.loads(x) for x in lines)
    assert list(first) == list(COLUMNS)
    assert first["tables_read"] == ["locations"]
    assert first["row_count"] == 2 and first["badge"] == "validated"
    assert first["export_verified"] is True and first["ledger_entries_checked"] == 2
    assert second["badge"] == "abstain" and second["tables_read"] == []


# --- who is the actor ----------------------------------------------------------


def test_the_actor_is_the_deployment_never_a_request_field() -> None:
    client, _app = _stub_stack(_envelope())

    # An ask body that names an actor. The model has no such field, so it is dropped.
    asked = client.post(
        "/v1/chat/ask",
        json={"question": "How many units?", "space_id": FINANCE, "actor": "mallory"},
    )
    assert asked.status_code == 200

    r = _export(client, format="jsonl")
    row = json.loads(r.text.splitlines()[0])

    assert row["actor"] == DEPLOYMENT_ACTOR
    assert row["actor_kind"] == "deployment"
    assert "mallory" not in r.text
    # And the identity headers are refused outright, so no route reads them (DR-0004).
    spoof = client.get("/v1/audit/export", headers={"x-dms-actor-id": "mallory"})
    assert spoof.status_code == 400


# --- gate ----------------------------------------------------------------------


class _SpyStore(InMemoryAskAuditStore):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def list_between(self, **kw: Any):  # type: ignore[override]
        self.reads += 1
        return super().list_between(**kw)


def test_the_export_is_gated_before_any_row_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.audit as audit_routes

    calls: list[dict[str, Any]] = []

    def refuse(*, action: str, **kw: Any) -> ComplianceDecision:
        calls.append({"action": action, **kw})
        return ComplianceDecision(allowed=False, reason="gate_refused", action=action)

    monkeypatch.setattr(audit_routes, "compliance_gate", refuse)
    client, app = _stub_stack(_envelope())
    spy = _SpyStore()
    app.state.ask_audit_store = spy

    r = _export(client)

    assert r.status_code == 403
    assert r.json()["detail"] == "gate_refused"
    assert [c["action"] for c in calls] == ["audit.export"]
    assert spy.reads == 0, "a refused export must not have read the audit store"


def test_the_export_is_a_read_so_an_unreachable_gate_does_not_refuse_it() -> None:
    """mutation=False, like GET /v1/audit/ledger. Not fail-open for a *refusal*:
    only for "no decision could be reached", which the other audit reads allow."""
    client, app = _stub_stack(_envelope())  # LedgerCortex has no base_url: gate_unavailable
    spy = _SpyStore()
    app.state.ask_audit_store = spy

    r = _export(client)

    assert r.status_code == 200
    assert spy.reads == 1


# --- unverified is the default for anything but a clean verify ------------------


def test_an_unreachable_ledger_is_unverified_not_verified_and_not_a_500() -> None:
    cortex = LedgerCortex(verify_raises=True)
    client, _app = _stub_stack(_envelope(), cortex=cortex)
    _ask(client, "How many units?")

    r = _export(client)

    assert r.status_code == 200
    row = _csv_rows(r.text)[0]
    assert row["export_verified"] == "false"
    assert row["ledger_verify_status"] == "unavailable"
    assert r.headers["x-audit-export-verified"] == "false"
    assert "cortex.internal" not in r.text, "a transport error must not leak into the file"


def test_a_missing_cortex_client_is_unverified() -> None:
    app = create_app()
    app.state.cortex = None
    app.state.ask_service = StubAsk(_envelope())
    client = TestClient(app)
    app.state.ask_audit_store.record(
        record_from_error(
            question="q",
            reason="seed",
            actor=DEPLOYMENT_ACTOR,
            actor_kind="deployment",
            space_id=None,
        )
    )

    row = _csv_rows(_export(client).text)[0]

    assert row["export_verified"] == "false" and row["ledger_verify_status"] == "unavailable"


# --- secrets -------------------------------------------------------------------

# Obviously synthetic. Built here, never real, and never expected to survive.
_FAKE_PW = "fake-pw-not-real-7731"
_FAKE_DSN = f"postgresql://dms_reader:{_FAKE_PW}@db.internal:5432/sales"
_FAKE_MODEL_KEY = "sk-FAKEFAKEFAKEFAKE0123"
_FAKE_BEARER = "Bearer FAKEbearerTOKEN0123456789"
_FAKE_PAIR = "password=fake-pair-pw-9912"


def test_a_secret_typed_into_an_ask_is_absent_from_the_export() -> None:
    question = (
        f"Connect with {_FAKE_DSN} and use {_FAKE_MODEL_KEY}, header {_FAKE_BEARER}, "
        f"also {_FAKE_PAIR}. What is total spend?"
    )
    leaky_sql = f"SELECT * FROM inventory WHERE api_key = '{_FAKE_MODEL_KEY}' -- {_FAKE_PAIR}"
    client, _app = _stub_stack(_envelope(sql_used=leaky_sql))

    assert _ask(client, question).status_code == 200

    secrets = (_FAKE_PW, _FAKE_MODEL_KEY, "FAKEbearerTOKEN0123456789", "fake-pair-pw-9912")
    for fmt in ("csv", "jsonl"):
        r = _export(client, format=fmt)
        blob = r.text + json.dumps(dict(r.headers))
        for secret in secrets:
            assert secret not in blob, f"{secret!r} leaked into the {fmt} export"
    # The ask itself is still recorded, with the secret replaced rather than the row dropped.
    row = _csv_rows(_export(client).text)[0]
    assert "[redacted]" in row["question"] and "total spend" in row["question"]
    assert "[redacted]" in row["executed_sql"]
    assert row["tables_read"] == "inventory"


def test_scrub_is_idempotent_and_leaves_ordinary_text_alone() -> None:
    plain = "What was revenue by region for FY2025? SELECT region, SUM(amt) FROM sales GROUP BY 1"
    assert scrub(plain) == plain
    # A predicate on a column that merely has a secret-sounding name is not a secret.
    predicate = "SELECT id FROM users WHERE token IS NULL OR password IS NOT NULL"
    assert scrub(predicate) == predicate
    once = scrub(f"{_FAKE_DSN} {_FAKE_PAIR} {_FAKE_BEARER}")
    assert scrub(once) == once
    assert _FAKE_PW not in once


# --- CSV is RFC 4180 and safe in a spreadsheet ----------------------------------

_HOSTILE = [
    '=HYPERLINK("http://attacker.example/x","click")',
    "+1+1",
    "-2+3",
    "@SUM(A1:A9)",
    "\t=1+1",
]


def test_csv_is_rfc_4180_and_neutralises_spreadsheet_formulas() -> None:
    awkward = 'one, two "quoted"\nsecond line'
    client, app = _stub_stack(_envelope())
    store = app.state.ask_audit_store
    for q in [*_HOSTILE, awkward]:
        store.record(
            record_from_error(
                question=q,
                reason="seed",
                actor=DEPLOYMENT_ACTOR,
                actor_kind="deployment",
                space_id=FINANCE,
            )
        )

    r = _export(client, format="csv")

    text = r.text
    assert text.startswith(",".join(COLUMNS) + "\r\n"), "a header row, CRLF terminated"
    assert text.endswith("\r\n")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows[0] == list(COLUMNS)
    assert all(len(row) == len(COLUMNS) for row in rows), "every record has every field"
    questions = {row[COLUMNS.index("question")] for row in rows[1:]}
    # Formula leads are prefixed so Excel reads text, and nothing else is altered.
    for q in _HOSTILE:
        assert "'" + q in questions, q
        assert q not in questions, f"{q!r} would be read as a formula"
    # A comma, a quote and a newline survive a round trip exactly.
    assert awkward in questions
    # Raw CRs or bare newlines may appear only inside a quoted field: the parse above
    # already proved the field count, which a stray terminator would break.
    assert neutralise_formula("plain") == "plain"
    assert neutralise_formula("") == ""


def test_jsonl_is_not_altered_for_spreadsheets() -> None:
    client, app = _stub_stack(_envelope())
    app.state.ask_audit_store.record(
        record_from_error(
            question="=1+1",
            reason="seed",
            actor=DEPLOYMENT_ACTOR,
            actor_kind="deployment",
            space_id=None,
        )
    )

    row = json.loads(_export(client, format="jsonl").text.splitlines()[0])

    assert row["question"] == "=1+1"


def test_jsonl_keeps_unicode_line_separators_inside_the_line() -> None:
    client, app = _stub_stack(_envelope())
    app.state.ask_audit_store.record(
        record_from_error(
            question="a\u2028b",
            reason="seed",
            actor=DEPLOYMENT_ACTOR,
            actor_kind="deployment",
            space_id=None,
        )
    )

    r = _export(client, format="jsonl")

    assert len(r.text.splitlines()) == 1, "U+2028 must not split a record"
    assert json.loads(r.text)["question"] == "a\u2028b"


# --- range, size, completeness --------------------------------------------------


def _seed(app: Any, when: str, question: str) -> None:
    rec = record_from_error(
        question=question,
        reason="seed",
        actor=DEPLOYMENT_ACTOR,
        actor_kind="deployment",
        space_id=None,
        asked_at=datetime.fromisoformat(when).replace(tzinfo=UTC),
    )
    app.state.ask_audit_store.record(rec)


def test_from_is_inclusive_to_is_exclusive_and_a_bare_date_is_the_whole_day() -> None:
    client, app = _stub_stack(_envelope())
    _seed(app, "2026-10-01T23:59:59", "before")
    _seed(app, "2026-10-02T00:00:00", "start-of-day")
    _seed(app, "2026-10-02T18:30:00", "evening")
    _seed(app, "2026-10-03T00:00:00", "next-day")

    def questions(**params: str) -> list[str]:
        return [r["question"] for r in _csv_rows(_export(client, **params).text)]

    assert questions(**{"from": "2026-10-02", "to": "2026-10-02"}) == ["start-of-day", "evening"]
    assert questions(**{"from": "2026-10-02T00:00:00", "to": "2026-10-03T00:00:00"}) == [
        "start-of-day",
        "evening",
    ]
    assert questions(**{"to": "2026-10-02T00:00:00"}) == ["before"]
    assert questions() == ["before", "start-of-day", "evening", "next-day"]


def test_a_bad_or_inverted_range_is_refused() -> None:
    client, _app = _stub_stack(_envelope())

    bad = _export(client, **{"from": "last tuesday"})
    inverted = _export(client, **{"from": "2026-10-05", "to": "2026-10-04"})

    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_range"
    assert inverted.status_code == 422 and inverted.json()["detail"]["code"] == "invalid_range"


def test_an_oversize_export_is_refused_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.audit as audit_routes

    monkeypatch.setattr(audit_routes, "MAX_EXPORT_ROWS", 2)
    client, app = _stub_stack(_envelope())
    for n in range(3):
        _seed(app, f"2026-10-0{n + 1}T10:00:00", f"q{n}")

    refused = _export(client)
    narrowed = _export(client, **{"from": "2026-10-02"})

    assert refused.status_code == 413
    assert refused.json()["detail"]["code"] == "export_too_large"
    assert narrowed.status_code == 200 and len(_csv_rows(narrowed.text)) == 2


def test_asks_that_never_reached_the_gate_are_not_recorded() -> None:
    client, _app = _stub_stack(_envelope())

    lane = client.post(
        "/v1/chat/ask",
        json={"question": "q", "space_id": FINANCE, "ask_path": "generative"},
    )
    unknown = client.post("/v1/chat/ask", json={"question": "q", "space_id": "sp_missing"})

    assert lane.status_code == 400 and unknown.status_code == 404
    exported = _export(client)
    assert exported.status_code == 200
    assert _csv_rows(exported.text) == []
    assert exported.text.startswith(",".join(COLUMNS)), "an empty export still has its header"


class _FailingStore(InMemoryAskAuditStore):
    def record(self, rec: Any) -> None:
        raise RuntimeError("audit database is down")


def test_a_failed_audit_write_does_not_break_the_ask_and_is_reported() -> None:
    client, app = _stub_stack(_envelope())
    app.state.ask_audit_store = _FailingStore()

    asked = _ask(client, "How many units?")

    assert asked.status_code == 200, "an audit write failure must not turn a read into a 500"
    r = _export(client)
    assert _csv_rows(r.text) == []
    assert r.headers["x-audit-unrecorded-asks"] == "1", (
        "the export must say that an ask is missing from it"
    )
    assert r.headers["x-audit-store"] == "memory"


def test_the_export_says_which_store_served_it() -> None:
    client, _app = _stub_stack(_envelope())

    r = _export(client)

    assert r.headers["x-audit-store"] == "memory"
    assert r.headers["x-audit-unrecorded-asks"] == "0"
    assert r.headers["cache-control"] == "no-store"


# --- the one parser that names the tables ---------------------------------------


@pytest.mark.parametrize(
    ("sql", "tables"),
    [
        ("SELECT SUM(amount) FROM transactions WHERE x > 1", ("transactions",)),
        (
            "WITH a AS (SELECT * FROM inventory) SELECT * FROM a JOIN locations l ON l.id = a.id",
            ("inventory", "locations"),
        ),
        ('SELECT * FROM main.sales s JOIN "Other Table" o ON 1=1', ("main.sales", "Other Table")),
        ("SELECT * FROM t1 UNION ALL SELECT * FROM T1", ("t1",)),
        ("SELECT '=cmd' AS q FROM transactions; SELECT * FROM alerts", ("alerts", "transactions")),
        # A CTE name is excluded only OUTSIDE that CTE's own body (sqlglot scope).
        ("WITH sales AS (SELECT * FROM sales WHERE x = 1) SELECT SUM(a) FROM sales", ("sales",)),
        ("WITH a AS (SELECT * FROM t1), b AS (SELECT * FROM a) SELECT * FROM b", ("t1",)),
        (
            "WITH sales AS (SELECT 1) SELECT * FROM sales s JOIN sales_detail d ON 1=1",
            ("sales_detail",),
        ),
        (
            "WITH RECURSIVE t AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM t WHERE n < 5) "
            "SELECT * FROM t",
            (),
        ),
        ("SELECT * FROM (SELECT * FROM orders) o JOIN customers c ON 1=1", ("customers", "orders")),
        ("-- document retrieval (no SQL)", ()),
        ("SELECT 1", ()),
        ("", ()),
        (None, ()),
    ],
)
def test_tables_read_names_what_the_statement_reads(
    sql: str | None, tables: tuple[str, ...]
) -> None:
    assert tables_read_by_sql(sql) == tables


# === Independent-verify fixes (H1, H2, M1, M2, M3, M5, M6, L1, L2) ================
#
# Each test below fails on 93bc9eb (the head these fixes were made against). New
# symbols are imported inside the tests so a missing one fails that test, not the
# whole module.


# --- H1: what ran, not what the customer was shown -------------------------------


def test_sql_that_ran_before_an_abstain_is_exported_as_what_ran(minter: ManifestMinter) -> None:
    client, _app, cortex = _real_stack(minter)
    cortex.mode = "abstain_after_sql"

    answer = _ask(client, WHERE_IS_SKU, session_id="ses_h1").json()
    # The customer is shown no SQL and no rows. That divergence is the thing under test.
    assert answer["abstained"] is True
    assert answer["sql_used"] is None and answer["rows"] == []

    row = _csv_rows(_export(client).text)[0]

    assert row["badge"] == "abstain"
    assert row["executed_sql"] == "SELECT SUM(amount) FROM transactions"
    assert row["tables_read"] == "transactions"
    assert row["row_count"] == "1"


def test_a_placeholder_is_never_exported_as_sql(minter: ManifestMinter) -> None:
    # (a) an envelope that carries only the document-retrieval stub
    client, _app = _stub_stack(_envelope(sql_used="-- document retrieval (no SQL)", rows=[]))
    shown = _ask(client, "Summarise the supplier contract").json()
    assert shown["sql_used"] == "-- document retrieval (no SQL)"
    row = _csv_rows(_export(client).text)[0]
    assert row["executed_sql"] == "" and row["tables_read"] == ""

    # (b) the live path's own stub, for an answer whose engine reported no SQL
    client, _app, cortex = _real_stack(minter)
    cortex.mode = "no_sql"
    _ask(client, WHERE_IS_SKU, session_id="ses_nosql")
    row = _csv_rows(_export(client).text)[0]
    assert row["executed_sql"] == "", row["executed_sql"]
    assert row["tables_read"] == ""


def test_every_statement_that_ran_is_recorded_in_order() -> None:
    from dms_api.ask_audit import _evidence

    sql, tables, rows = _evidence(
        None, [("SELECT 1 FROM a", 1), ("SELECT 2 FROM b JOIN a ON 1=1;", 3)]
    )

    assert sql == "SELECT 1 FROM a;\nSELECT 2 FROM b JOIN a ON 1=1"
    assert tables == ("a", "b")
    assert rows == 3


# --- H2: "verified" says only what it can ----------------------------------------


def test_the_verify_scope_is_stated_on_every_row_and_in_a_header(minter: ManifestMinter) -> None:
    from dms_core.control_plane.ask_audit_export import VERIFY_SCOPE

    client, _app, _cortex = _real_stack(minter)
    _ask(client, WHERE_IS_SKU, session_id="ses_scope")

    r = _export(client)

    assert VERIFY_SCOPE == "chain_integrity_only; rows are not matched to ledger entries"
    assert _csv_rows(r.text)[0]["ledger_verify_scope"] == VERIFY_SCOPE
    assert r.headers["x-audit-verify-scope"] == VERIFY_SCOPE


def test_an_empty_ledger_does_not_verify_an_export() -> None:
    # verify_ledger() on an empty chain answers ok with checked=0.
    cortex = LedgerCortex()
    assert cortex.verify_ledger().ok and cortex.verify_ledger().checked == 0
    client, _app = _stub_stack(_envelope(), cortex=cortex)  # its row points at led_stub
    _ask(client, "How many units?")

    pointing = _export(client)

    row = _csv_rows(pointing.text)[0]
    assert row["export_verified"] == "false"
    assert row["ledger_verify_status"] == "incomplete"
    assert pointing.headers["x-audit-export-verified"] == "false"

    # And a file with no pointer at all is still not "verified" by a chain that checked nothing.
    unpointed = _envelope(
        answer_id="ans_x", badge="ABSTAIN", abstained=True, sql_used=None, rows=[], audit_id=None
    )
    client, _app = _stub_stack(unpointed, cortex=LedgerCortex())
    _ask(client, "an unanswerable question")
    row = _csv_rows(_export(client).text)[0]
    assert row["cortex_entry_id"] == ""
    assert row["export_verified"] == "false" and row["ledger_verify_status"] == "incomplete"


def test_a_ledger_that_lost_its_tail_does_not_verify_an_export(minter: ManifestMinter) -> None:
    client, _app, cortex = _real_stack(minter)
    _ask(client, WHERE_IS_SKU, session_id="ses_t1")
    _ask(client, WHERE_IS_SKU, session_id="ses_t2")
    assert _csv_rows(_export(client).text)[0]["export_verified"] == "true"

    cortex.chain.pop()  # the last entry vanishes; what remains still hashes cleanly
    assert cortex.verify_ledger().ok and cortex.verify_ledger().checked == 1

    r = _export(client)

    rows = _csv_rows(r.text)
    assert len(rows) == 2
    assert {x["cortex_entry_id"] for x in rows} == {"led_1", "led_2"}
    for row in rows:
        assert row["export_verified"] == "false"
        assert row["ledger_verify_status"] == "incomplete"
    assert r.headers["x-audit-export-verified"] == "false"


# --- M1: the secret shapes that used to leak -------------------------------------

_B64 = base64.b64encode(b"fakeuser:FakeSecretValue0123456789xyz").decode()
_BLOB = "Zm9vYmFyQmF6MDEyMzQ1Njc4OWFiY0RFRmdoSUprbG1u"
_KEY_BODY = "\n".join(
    [
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj",
        "MzEfYyjiWA4R4/M2bS1GB4t7NXp98C3SC6dVMvDuictGeurT8jNbvJZHtCSuYEvu",
        "NMoSfm76oqFvAp8Gy0iz5sxjZfm",
    ]
)

#: (shape, text containing a fake secret, the secret that must not survive)
_SECRET_SHAPES = [
    (
        "url password with slash",
        "postgres://svc:FAKEpw/with/slash99@db.internal:5432/x",
        "FAKEpw/with/slash99",
    ),
    ("url password with at", "mysql://svc:FAKE@pw77xyz@db.internal/x", "FAKE@pw77xyz"),
    ("url encoded password", "postgres://svc:FAKE%40pw%2Fenc55@db/x", "FAKE%40pw%2Fenc55"),
    ("authorization basic", f"Authorization: Basic {_B64}", _B64),
    ("json api_key", '{"api_key":"FAKEapikey123456"}', "FAKEapikey123456"),
    ("json password", '{"password": "FAKEjsonpw 99"}', "FAKEjsonpw 99"),
    ("DB_PASSWORD", "DB_PASSWORD=FAKEdbpw12345", "FAKEdbpw12345"),
    (
        "OPENAI_API_KEY without sk-",
        "OPENAI_API_KEY=FAKEnoprefix1234567890",
        "FAKEnoprefix1234567890",
    ),
    (
        "aws_secret_access_key",
        "aws_secret_access_key=FAKEawssecret0123456789",
        "FAKEawssecret0123456789",
    ),
    ("PGPASSWORD", "PGPASSWORD=FAKEpgpw99 psql -h db", "FAKEpgpw99"),
    ("name split by a comment", "pass/**/word=FAKEsplit1234", "FAKEsplit1234"),
    ("comment around the operator", "password /* c */ =FAKEcomment1234", "FAKEcomment1234"),
    ("cyrillic lookalike letters", "\u0440\u0430ssword=FAKEcyr1234", "FAKEcyr1234"),
    ("fullwidth equals", "password\uff1dFAKEfullwidth1234", "FAKEfullwidth1234"),
    (
        "unquoted multi-word value",
        "password: correct horse battery staple. What is revenue?",
        "battery",
    ),
    ("bare base64 blob", f"here it is {_BLOB} thanks", _BLOB),
    ("password LIKE", "SELECT 1 FROM u WHERE password LIKE 'FAKElikepw99%'", "FAKElikepw99"),
    ("headerless private key body", f"key:\n{_KEY_BODY}\nend", "AoIBAQC7VJTUt9Us8cKj"),
    # Shapes the first version already caught: kept so they cannot regress.
    ("mixed-case password", "PaSsWoRd=FAKEcase1234", "FAKEcase1234"),
    ("sk-ant key", "use sk-ant-api03-FAKE0123456789abcdefgh now", "FAKE0123456789abcdefgh"),
    ("X-API-Key header", "X-API-Key: FAKEhdr1234567", "FAKEhdr1234567"),
    ("ODBC Pwd", "Server=db;Uid=app;Pwd=FAKEodbc1234;Database=x", "FAKEodbc1234"),
    (
        "JDBC password",
        "jdbc:postgresql://db/x?user=app&password=FAKEjdbc1234&ssl=true",
        "FAKEjdbc1234",
    ),
    ("bearer token", "Bearer FAKEbearerTOKEN0123456789", "FAKEbearerTOKEN0123456789"),
    ("token is", "my token is FAKEtokenis9876", "FAKEtokenis9876"),
]

#: Shapes found by the second independent verify. Each is also tried inside a SQL literal
#: and a SQL comment (see ``_placements``).
_SECRET_SHAPES += [
    ("two words, the first", "password = FAKEZQX9one twoFAKEZQX9", "FAKEZQX9one"),
    ("two words, the second", "password = FAKEZQX9one twoFAKEZQX9", "twoFAKEZQX9"),
    ("curl -u", "curl -u svc:FAKEZQX9curl https://api.example.com/x", "FAKEZQX9curl"),
    (
        "percent-encoded pair",
        "https://x.example/login?password%3DFAKEZQX9pct%26user%3Dbob",
        "FAKEZQX9pct",
    ),
    ("json nested", '{"a":{"b":{"password":"FAKEZQX9nest"}}}', "FAKEZQX9nest"),
    ("json escaped inside a string", '{\\"password\\":\\"FAKEZQX9esc\\"}', "FAKEZQX9esc"),
    ("json number", '{"password": 87654321}', "87654321"),
    ("CREATE USER WITH PASSWORD", "CREATE USER bob WITH PASSWORD 'FAKEZQX9cu'", "FAKEZQX9cu"),
    ("IDENTIFIED BY", "ALTER USER bob IDENTIFIED BY 'FAKEZQX9idb'", "FAKEZQX9idb"),
    ("Cookie header", "Cookie: session=FAKEZQX9sess; theme=dark", "FAKEZQX9sess"),
    ("SET PASSWORD = PASSWORD()", "SET PASSWORD = PASSWORD('FAKEZQX9sp')", "FAKEZQX9sp"),
]


def _placements() -> list[tuple[str, str, bool, str]]:
    """Every shape as a question, inside a SQL string literal, and inside SQL comments."""
    out: list[tuple[str, str, bool, str]] = []
    for shape, text, secret in _SECRET_SHAPES:
        quoted = text.replace("'", "''")
        out.append((f"{shape} | question", text, False, secret))
        out.append(
            (
                f"{shape} | sql literal",
                f"SELECT * FROM inventory WHERE note = '{quoted}'",
                True,
                secret,
            )
        )
        if "/*" not in text:  # a comment cannot hold another comment
            out.append((f"{shape} | sql block comment", f"SELECT 1 /* {text} */", True, secret))
            if "\n" not in text:
                out.append((f"{shape} | sql line comment", f"SELECT 1 -- {text}", True, secret))
    return out


_PLACEMENTS = _placements()


@pytest.mark.parametrize(
    ("where", "text", "sql", "secret"), _PLACEMENTS, ids=[x[0] for x in _PLACEMENTS]
)
def test_no_secret_shape_survives_the_scrub(where: str, text: str, sql: bool, secret: str) -> None:
    from dms_core.control_plane.ask_audit import scrub_counted

    clean, removed = scrub_counted(text, sql=sql)

    assert secret not in clean, f"{where}: {clean!r}"
    assert removed >= 1
    assert scrub_counted(clean, sql=sql) == (clean, 0), "a second pass changes nothing"


def test_every_secret_shape_is_absent_from_the_export_and_counted() -> None:
    question = "\n".join(text for _s, text, _x in _SECRET_SHAPES) + "\nWhat is total spend?"
    sql = "SELECT 1 FROM inventory WHERE api_key = 'FAKEsqlkey123456' -- password=FAKEsqlcomment99"
    client, _app = _stub_stack(_envelope(sql_used=sql))

    assert _ask(client, question).status_code == 200

    csv_text = _export(client).text
    jsonl_text = _export(client, format="jsonl").text
    for _shape, _text, secret in _SECRET_SHAPES:
        assert secret not in csv_text and secret not in jsonl_text, secret
    assert "FAKEsqlkey123456" not in csv_text and "FAKEsqlcomment99" not in csv_text
    row = _csv_rows(csv_text)[0]
    assert int(row["redactions"]) >= len({t for _s, t, _x in _SECRET_SHAPES}), (
        "the file says text was altered"
    )
    assert "total spend" in row["question"]


# --- M2: ordinary text and SQL must come through untouched -----------------------


def test_ordinary_text_sql_and_table_names_are_not_rewritten() -> None:
    asks = [
        "What are our risk-weighted assets",
        "Show bearer securities by issuer",
        "The authorization is pending for the Q3 filing",
    ]
    sql = (
        'SELECT a.id FROM "risk-weighted_assets" a JOIN b ON a.id = b.id '
        "WHERE token = t2.token AND secret = 0"
    )
    client, _app = _stub_stack(_envelope(sql_used=sql))
    for q in asks:
        assert _ask(client, q).status_code == 200

    rows = _csv_rows(_export(client).text)

    assert [r["question"] for r in rows] == asks
    for r in rows:
        assert r["executed_sql"] == sql
        assert r["tables_read"] == "b;risk-weighted_assets"
        assert r["redactions"] == "0"


def test_scrub_leaves_a_comparison_and_a_null_test_alone() -> None:
    from dms_core.control_plane.ask_audit import scrub

    for text in (
        "SELECT id FROM users WHERE token IS NULL OR password IS NOT NULL",
        "bearer securities",
        "authorization is pending",
        "risk-weighted_assets",
    ):
        assert scrub(text) == text
    assert (
        scrub("WHERE token = t2.token AND secret = 0", sql=True)
        == "WHERE token = t2.token AND secret = 0"
    )


# --- M3: a hung audit database must not hang the ask ------------------------------


def test_a_hung_audit_database_does_not_hang_the_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_core.control_plane.ask_audit as aa

    monkeypatch.setattr(aa, "CONNECT_TIMEOUT_S", 2, raising=False)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)  # accepts at the kernel and never answers a byte
    port = listener.getsockname()[1]
    try:
        client, app = _stub_stack(_envelope())
        store = aa.PostgresAskAuditStore(
            f"postgresql://dms@127.0.0.1:{port}/dms",
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        app.state.ask_audit_store = store
        out: dict[str, Any] = {}

        def run() -> None:
            t0 = time.monotonic()
            out["first"] = _ask(client, "How many units?").status_code
            out["first_s"] = time.monotonic() - t0
            t1 = time.monotonic()
            out["second"] = _ask(client, "How many units again?").status_code
            out["second_s"] = time.monotonic() - t1

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=20)

        assert not worker.is_alive(), "the ask hung on an audit database that never answers"
        assert out["first"] == 200 and out["second"] == 200
        assert out["first_s"] < 10
        assert out["second_s"] < 2, "after one timeout the store backs off instead of waiting again"
        assert store.dropped == 2, "a timed-out write is an unrecorded ask, counted"
    finally:
        listener.close()


def test_an_audit_store_that_cannot_answer_is_a_503_not_a_partial_file() -> None:
    class _Broken(InMemoryAskAuditStore):
        def list_between(self, **_kw: Any):  # type: ignore[override]
            raise RuntimeError("postgres at db.internal:5432 is down")

    client, app = _stub_stack(_envelope())
    app.state.ask_audit_store = _Broken()

    r = _export(client)

    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "audit_store_unavailable"
    assert "db.internal" not in r.text


# --- M5: the file says how complete it is ----------------------------------------


class _FlakyStore(InMemoryAskAuditStore):
    """Loses the first write, then works."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def record(self, rec: Any) -> None:
        if not self.failed:
            self.failed = True
            raise RuntimeError("audit database blipped")
        super().record(rec)


def test_a_saved_file_carries_the_store_and_the_unrecorded_count() -> None:
    client, app = _stub_stack(_envelope())
    app.state.ask_audit_store = _FlakyStore()
    _ask(client, "the ask whose row was lost")
    _ask(client, "the ask that was recorded")

    csv_row = _csv_rows(_export(client).text)
    jsonl_row = [json.loads(x) for x in _export(client, format="jsonl").text.splitlines()]

    assert [r["question"] for r in csv_row] == ["the ask that was recorded"]
    assert csv_row[0]["store_backend"] == "memory"
    assert csv_row[0]["unrecorded_asks_since_start"] == "1"
    assert jsonl_row[0]["store_backend"] == "memory"
    assert jsonl_row[0]["unrecorded_asks_since_start"] == 1


# --- M6: the record is bounded, the ask is not -----------------------------------


def test_a_huge_question_is_recorded_cut_and_marked() -> None:
    from dms_core.control_plane.ask_audit import QUESTION_CAP

    client, app = _stub_stack(_envelope())
    for _ in range(5):
        assert _ask(client, "x" * 2_000_000).status_code == 200, "the ask itself still accepts it"

    stored = app.state.ask_audit_store.list_between(since=None, until=None, limit=100)

    assert len(stored) == 5
    assert sum(len(r.question) for r in stored) < 5 * (QUESTION_CAP + 100)
    for r in stored:
        assert r.question.startswith("x" * 100)
        assert "[truncated: original was 2000000 chars]" in r.question
        assert r.truncated is True
    assert {r["truncated"] for r in _csv_rows(_export(client).text)} == {"true"}


def test_a_huge_sql_text_is_recorded_cut_and_marked() -> None:
    from dms_core.control_plane.ask_audit import SQL_CAP

    sql = "SELECT 1 FROM inventory -- " + "y" * 200_000
    client, _app = _stub_stack(_envelope(sql_used=sql))
    _ask(client, "How many units?")

    row = _csv_rows(_export(client).text)[0]

    assert len(row["executed_sql"]) < SQL_CAP + 100
    assert "[truncated: original was " in row["executed_sql"] and row["truncated"] == "true"
    assert row["tables_read"] == "inventory", "tables come from the whole statement, not the cut"


def test_the_memory_store_is_bounded_and_counts_what_it_evicts() -> None:
    store = InMemoryAskAuditStore(max_bytes=5_000)
    for _ in range(10):
        store.record(
            record_from_error(
                question="q" * 1000,
                reason="seed",
                actor=DEPLOYMENT_ACTOR,
                actor_kind="deployment",
                space_id=None,
            )
        )

    kept = store.list_between(since=None, until=None, limit=100)

    assert 0 < len(kept) < 10
    assert store.dropped == 10 - len(kept), "an evicted row is an unrecorded ask, counted"


# --- L1: range parsing is strict and never a 500 ---------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"to": "9999-12-31"},
        {"from": "0001-01-01T00:00:00+14:00"},
        {"from": "9999-12-31T23:59:59-14:00"},
        {"to": "20261005"},
        {"from": "2026-10-05T25:00:00"},
        {"from": "2026-13-01"},
        {"from": "yesterday"},
    ],
)
def test_a_range_that_is_not_a_real_date_is_422_never_500(params: dict[str, str]) -> None:
    client, _app = _stub_stack(_envelope())

    r = _export(client, **params)

    assert r.status_code == 422, (params, r.status_code)
    assert r.json()["detail"]["code"] == "invalid_range"


def test_the_two_spellings_of_a_day_agree() -> None:
    client, app = _stub_stack(_envelope())
    _seed(app, "2026-10-05T09:00:00", "on the fifth")
    _seed(app, "2026-10-06T09:00:00", "on the sixth")

    by_date = _csv_rows(_export(client, **{"from": "2026-10-05", "to": "2026-10-05"}).text)
    by_time = _csv_rows(
        _export(client, **{"from": "2026-10-05T00:00:00Z", "to": "2026-10-06T00:00:00Z"}).text
    )

    assert [r["question"] for r in by_date] == ["on the fifth"]
    assert [r["question"] for r in by_time] == [r["question"] for r in by_date]


# --- L2: the CSV formula guard sees through whitespace and lookalikes -------------

_DISGUISED = [
    " =1+1",
    "\xa0=1+1",
    "\uff1d1+1",
    "\u200b=1+1",
    "\u3000@SUM(A1)",
    " \t-2+3",
    "\ufeff+1",
]


@pytest.mark.parametrize("lead", _DISGUISED)
def test_a_disguised_formula_lead_is_neutralised(lead: str) -> None:
    assert neutralise_formula(lead) == "'" + lead


def test_a_disguised_formula_is_neutralised_in_the_export() -> None:
    client, app = _stub_stack(_envelope())
    for q in _DISGUISED[:3]:
        app.state.ask_audit_store.record(
            record_from_error(
                question=q,
                reason="seed",
                actor=DEPLOYMENT_ACTOR,
                actor_kind="deployment",
                space_id=None,
            )
        )

    questions = {r["question"] for r in _csv_rows(_export(client).text)}

    assert questions == {"'" + q for q in _DISGUISED[:3]}


# === Verify round 2: N2 N3 N4 N5 N6 N7, M1 and M2 remainders ========================
#
# Each test below fails on 7c86f5d. New symbols are imported inside the tests.


# --- N2: a secret inside a SQL string literal ---------------------------------------


def test_a_secret_inside_a_sql_string_literal_is_removed() -> None:
    sql = "SELECT * FROM inventory WHERE note = 'PaSsWoRd=FAKEZQX9pw7'"
    client, _app = _stub_stack(_envelope(sql_used=sql))
    _ask(client, "How many units?")

    row = _csv_rows(_export(client).text)[0]

    assert "FAKEZQX9pw7" not in row["executed_sql"]
    assert row["executed_sql"].startswith("SELECT * FROM inventory WHERE note = '")
    assert int(row["redactions"]) >= 1
    assert row["tables_read"] == "inventory"


# --- N3: no personal data in the audit export that the envelope masks -----------------


def test_the_export_holds_no_personal_data_the_envelope_masks(minter: ManifestMinter) -> None:
    client, _app, cortex = _real_stack(minter)
    cortex.mode = "pii_sql"

    answer = _ask(
        client, "Does alice.tan@example.com have any open orders?", session_id="ses_pii"
    ).json()
    # The customer envelope masks the address in the SQL it shows.
    assert "alice.tan@example.com" not in json.dumps(answer)
    assert "DMSMASK_email" in answer["sql_used"]

    row = _csv_rows(_export(client).text)[0]

    for field_name in ("question", "executed_sql"):
        assert "alice.tan@example.com" not in row[field_name], field_name
        assert "DMSMASK_email" in row[field_name], field_name
    # Table and column names are intact.
    assert row["tables_read"] == "customers"
    assert "FROM customers WHERE email = " in row["executed_sql"]
    assert int(row["redactions"]) >= 2


def test_a_url_password_is_removed_whichever_order_the_masker_ran() -> None:
    from dms_core.control_plane.ask_audit_scrub import mask_pii_counted, scrub_counted

    raw = "see postgresql://u:FAKEZQX9pa/ss@db.internal/x for details"

    # Secrets first, then the masker: the order the recorder uses.
    clean, _ = scrub_counted(raw)
    masked, _ = mask_pii_counted(clean)
    assert "FAKEZQX9pa" not in masked

    # Masker first: it turned the email-shaped tail "ss@db.internal" into a token, so
    # there is no "@host" left. The password prefix must still go.
    premasked, _ = mask_pii_counted(raw)
    assert "DMSMASK_email" in premasked, "the premise: the masker really did eat the tail"
    after, removed = scrub_counted(premasked)
    assert "FAKEZQX9pa" not in after and removed >= 1


def test_the_recorder_masks_before_it_stores_and_scrubs_first(minter: ManifestMinter) -> None:
    client, _app = _stub_stack(
        _envelope(
            sql_used="SELECT 1 FROM inventory WHERE dsn = 'postgresql://u:FAKEZQX9pa/ss@db.internal/x'"
        )
    )
    _ask(client, "How many units? postgresql://u:FAKEZQX9pa/ss@db.internal/x")

    row = _csv_rows(_export(client).text)[0]

    assert "FAKEZQX9pa" not in row["question"] + row["executed_sql"]


# --- N4: the ledger seq a row points at must be inside the verified chain --------------


class AppendingAsk:
    """Appends its own ledger entry the way the certified paths do, and traces it."""

    def __init__(self, cortex: LedgerCortex) -> None:
        self.cortex = cortex

    def live_ask(self, question: str, **_: Any) -> dict[str, Any]:
        from cortex_client.models import LedgerAppendRequest
        from dms_executor import executed_trace

        executed_trace.begin()
        entry = self.cortex.ledger_append(
            LedgerAppendRequest(event_type="ask.verified_query", payload={"q": question}, actor="x")
        )
        executed_trace.record("SELECT 1 FROM inventory", 1)
        executed_trace.record_ledger(entry.entry_id, entry.seq)
        return _envelope(audit_id=entry.entry_id, answer_id=f"ans_{entry.entry_id}")

    def take_executed(self) -> Any:
        from dms_executor import executed_trace

        return executed_trace.take()


def test_a_ledger_that_lost_an_entry_it_points_at_does_not_verify(
    minter: ManifestMinter,
) -> None:
    from cortex_client.models import LedgerAppendRequest

    cortex = LedgerCortex()
    for n in range(2):  # two amend.confirm entries that no ask points at
        cortex.ledger_append(
            LedgerAppendRequest(event_type="amend.confirm", payload={"n": n}, actor="steward")
        )
    app = create_app()
    app.state.cortex = cortex
    app.state.ask_service = AppendingAsk(cortex)
    client = TestClient(app)
    for n in range(3):
        assert _ask(client, f"ask {n}").status_code == 200

    healthy = _csv_rows(_export(client).text)
    assert [r["ledger_seq"] for r in healthy] == ["3", "4", "5"]
    assert {r["export_verified"] for r in healthy} == {"true"}

    cortex.chain.pop()  # the last ask's entry is gone: 4 entries remain, 3 asks point
    assert cortex.verify_ledger().ok and cortex.verify_ledger().checked == 4

    r = _export(client)

    rows = _csv_rows(r.text)
    assert len(rows) == 3
    for row in rows:
        assert row["export_verified"] == "false"
        assert row["ledger_verify_status"] == "incomplete"
    assert r.headers["x-audit-export-verified"] == "false"


def test_the_executor_records_the_ledger_seq_it_appended(minter: ManifestMinter) -> None:
    from dms_executor import executed_trace

    cortex = LedgerCortex()
    exe = Executor(cortex=cortex, minter=minter)  # type: ignore[arg-type]
    executed_trace.begin()

    appended = exe._ledger_verified_query(
        asset_sql="SELECT 1", run_id="run-1", space_id=None, session_id=None
    )

    assert appended.seq == 1
    assert exe.take_executed().ledger == (("led_1", 1),)


# --- N5: a CTE named t does not hide main.t -----------------------------------------


@pytest.mark.parametrize(
    ("sql", "tables"),
    [
        ("WITH t AS (SELECT 1 AS a) SELECT * FROM t JOIN main.t ON 1=1", ("main.t",)),
        ("WITH t AS (SELECT 1 AS a) SELECT * FROM t x JOIN main.t y ON 1=1", ("main.t",)),
        ("WITH t AS (SELECT * FROM main.t) SELECT * FROM t", ("main.t",)),
        ("WITH t AS (SELECT 1 AS a) SELECT * FROM t", ()),
    ],
)
def test_a_cte_name_does_not_hide_a_qualified_table(sql: str, tables: tuple[str, ...]) -> None:
    assert tables_read_by_sql(sql) == tables


# --- N6: scrub, then cut ------------------------------------------------------------


_MARKER_AT_END = re.compile(r"\.\.\.\[truncated: original was \d+ chars\]$")


def test_a_secret_straddling_the_question_cut_is_removed_whole() -> None:
    from dms_core.control_plane.ask_audit import QUESTION_CAP

    cases = [
        # a URL password that starts before the cut and ends after it
        "a" * (QUESTION_CAP - 20) + " postgresql://u:FAKEZQX9" + "c" * 60 + "@db.internal/x tail",
        # a key=value whose value runs across the cut
        "a" * (QUESTION_CAP - 14) + " password=FAKEZQX9" + "d" * 40 + " end",
    ]
    client, _app = _stub_stack(_envelope())
    for q in cases:
        assert _ask(client, q).status_code == 200

    rows = _csv_rows(_export(client).text)

    assert len(rows) == 2
    for row in rows:
        assert "FAKEZQX9" not in row["question"], row["question"][-120:]
        assert "[redacted]" in row["question"]
        assert _MARKER_AT_END.search(row["question"]), row["question"][-80:]
        assert row["truncated"] == "true"


def test_a_secret_straddling_the_sql_cut_is_removed_whole() -> None:
    from dms_core.control_plane.ask_audit import SQL_CAP

    lead = "SELECT 1 FROM inventory WHERE note = '" + "z" * (SQL_CAP - 78)
    # the password starts before the cut and ends after it; text follows, so the
    # scrubbed statement is still longer than the cap and is cut
    sql = lead + " postgresql://u:FAKEZQX9" + "c" * 80 + "@db.internal/x " + "y" * 600 + "'"
    client, _app = _stub_stack(_envelope(sql_used=sql))
    _ask(client, "How many units?")

    row = _csv_rows(_export(client).text)[0]

    assert "FAKEZQX9" not in row["executed_sql"], row["executed_sql"][-120:]
    assert "[redacted]" in row["executed_sql"]
    assert _MARKER_AT_END.search(row["executed_sql"])
    assert row["truncated"] == "true"
    assert row["tables_read"] == "inventory"


# --- N7: a stale trace is never the next request's evidence ------------------------------


class TracedStubAsk(StubAsk):
    def take_executed(self) -> Any:
        from dms_executor import executed_trace

        return executed_trace.take()


def test_a_trace_left_behind_is_not_exported_by_the_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dms_api.routes.chat as chat_routes
    from dms_executor import executed_trace

    def refuse(*, action: str, **_kw: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=False, reason="gate_refused", action=action)

    monkeypatch.setattr(chat_routes, "compliance_gate", refuse)
    app = create_app()
    app.state.cortex = LedgerCortex()
    app.state.ask_service = TracedStubAsk(_envelope())

    @app.get("/_leave_a_trace")
    def leave_a_trace() -> dict[str, bool]:
        # A path that begins a trace and never takes it, on the worker thread that
        # the next request will reuse.
        executed_trace.begin()
        executed_trace.record("SELECT secret FROM another_customers_table", 9)
        executed_trace.record_ledger("led_stale", 99)
        return {"ok": True}

    client = TestClient(app)
    for n in range(6):
        assert client.get("/_leave_a_trace").status_code == 200
        assert _ask(client, f"refused ask {n}").status_code == 403

    rows = _csv_rows(_export(client).text)

    assert len(rows) == 6
    for row in rows:
        assert row["badge"] == "error"
        assert row["executed_sql"] == "" and row["tables_read"] == "" and row["row_count"] == "0"
        assert "another_customers_table" not in row["executed_sql"]


# --- M1 and M2 remainders as one table ---------------------------------------------------

#: Ordinary text and SQL that must come through unchanged, with redactions=0.
_PROSE_UNCHANGED = [
    "What are our risk-weighted assets",
    "Show bearer securities by issuer",
    "The authorization is pending for the Q3 filing",
    "Password: reset required for 12 users this week",
    "Credentials: expired for 3 vendors",
    "Token: 5 per customer per day limit",
    "Our token is ERC20X1 based",
    "How many users reset their password this week?",
    "What was revenue by region for FY2025?",
]


@pytest.mark.parametrize("text", _PROSE_UNCHANGED)
def test_ordinary_prose_comes_through_unchanged(text: str) -> None:
    from dms_core.control_plane.ask_audit import scrub_counted

    assert scrub_counted(text) == (text, 0)
    # and inside a SQL literal, where it is read the same way
    sql = f"SELECT * FROM notes WHERE body = '{text.replace(chr(39), chr(39) * 2)}'"
    assert scrub_counted(sql, sql=True) == (sql, 0)


def test_ordinary_prose_is_exported_unchanged_with_no_redactions() -> None:
    client, _app = _stub_stack(_envelope())
    for text in _PROSE_UNCHANGED:
        assert _ask(client, text).status_code == 200

    rows = _csv_rows(_export(client).text)

    assert [r["question"] for r in rows] == _PROSE_UNCHANGED
    assert {r["redactions"] for r in rows} == {"0"}
