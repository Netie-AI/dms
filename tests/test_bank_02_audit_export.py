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

import csv
import hashlib
import io
import json
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
    """Live ask, no fallback, no database, and no OpenVault contact at all."""
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    # create_app() starts an Executor, which probes OpenVault at the settings URL
    # (default http://127.0.0.1:5000). Report it offline: no test here talks to a vault.
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

    def _append(self, body: dict[str, Any]) -> str:
        prev = self.chain[-1]["hash"] if self.chain else ""
        entry_id = f"led_{len(self.chain) + 1}"
        self.chain.append({"id": entry_id, "body": body, "prev": prev, "hash": _link(prev, body)})
        return entry_id

    def submit(self, req: Any) -> QueryResult:
        self._bound[req.manifest.session_id] = set(req.manifest.row_predicates)
        return QueryResult(ok=True, status="bound", run_id="run-1")

    def ask(self, req: Any) -> AskResponse:
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
            question="a b",
            reason="seed",
            actor=DEPLOYMENT_ACTOR,
            actor_kind="deployment",
            space_id=None,
        )
    )

    r = _export(client, format="jsonl")

    assert len(r.text.splitlines()) == 1, "U+2028 must not split a record"
    assert json.loads(r.text)["question"] == "a b"


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
