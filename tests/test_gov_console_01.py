"""DMS-GOV-01. The flag is the digit 1. Off, the ask and the upload are unchanged."""

from __future__ import annotations

from datetime import UTC, datetime

from cortex_client.gate import ComplianceDecision
from dms_api.app import create_app
from dms_api.gov_ov import set_reader
from dms_api.settings import get_settings
from dms_core.control_plane.ask_audit import AskAuditRecord
from dms_core.governance import enabled
from dms_core.governance.enforce import KeyCap, UsageEvent, UsageRead
from dms_core.governance.store import Assignment, Policy, get_store, reset_store
from fastapi.testclient import TestClient

_SECRET = "k7Qm" + "2Lp9VxR4"
_EMAIL = "ada.lovelace" + "@" + "example.com"


def _allow(**_kwargs: object) -> ComplianceDecision:
    return ComplianceDecision(True, "ok", "test")


def _password_line() -> bytes:
    return f"password = {_SECRET}\n".encode()


def _aws_line() -> str:
    return "AKIA" + "IOSFODNN7EXAMPLE"


def _pat() -> str:
    return "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def setup_function() -> None:
    reset_store()
    set_reader(None)


def teardown_function() -> None:
    reset_store()
    set_reader(None)


def test_only_the_digit_one_enables_it(monkeypatch) -> None:
    for value in ("", "0", "true", "yes", "on", " 1 ", "1 "):
        monkeypatch.setenv("DMS_GOV_CONSOLE", value)
        assert enabled() is False
    monkeypatch.setenv("DMS_GOV_CONSOLE", "1")
    assert enabled() is True


def test_flag_off_mounts_no_gov_route_and_keeps_the_upload(monkeypatch) -> None:
    monkeypatch.delenv("DMS_GOV_CONSOLE", raising=False)
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(path.startswith("/v1/gov") for path in paths)
    seen: dict[str, bytes] = {}

    def fake_batch(payloads, space_id=None):  # noqa: ANN001
        seen["data"] = payloads[0][1]
        return {
            "files_seen": 1,
            "ingested": 1,
            "need_attention": 0,
            "files": [],
            "ingest_id": "i1",
        }

    monkeypatch.setattr("dms_api.routes.studio.compliance_gate", _allow)
    monkeypatch.setattr("dms_api.routes.studio.batch_ingest", fake_batch)
    client = TestClient(app)
    response = client.post(
        "/v1/studio/ingest",
        files={"file": ("notes.csv", _password_line(), "text/csv")},
    )
    assert response.status_code == 200
    assert seen["data"] == _password_line()
    assert get_store().findings(get_settings().dms_tenant_id) == []


def test_flag_off_ask_ignores_a_budget(monkeypatch) -> None:
    monkeypatch.delenv("DMS_GOV_CONSOLE", raising=False)
    called = {"n": 0}

    def fake_answer(*_args, **_kwargs):  # noqa: ANN002
        called["n"] += 1
        return {"badge": "ABSTAIN", "abstained": True, "answer": "held", "rows": [], "values": []}

    monkeypatch.setattr("dms_api.routes.chat.compliance_gate", _allow)
    monkeypatch.setattr("dms_api.routes.chat._answer_ask", fake_answer)
    settings = get_settings()
    get_store().upsert_assignment(
        Assignment(
            settings.dms_tenant_id,
            settings.dms_actor_user_id,
            "",
            "member",
            daily_usd=0,
            monthly_usd=0,
            daily_tokens=0,
            monthly_tokens=0,
            allow_ask=False,
        )
    )
    client = TestClient(create_app())
    response = client.post("/v1/chat/ask", json={"question": "how many pallets"})
    assert response.status_code == 200
    assert called["n"] == 1


def _app_on(monkeypatch) -> TestClient:
    monkeypatch.setenv("DMS_GOV_CONSOLE", "1")
    return TestClient(create_app())


def _cap(monkeypatch, **caps: object) -> TestClient:
    client = _app_on(monkeypatch)
    settings = get_settings()
    get_store().upsert_assignment(
        Assignment(
            tenant_id=settings.dms_tenant_id,
            user_id=settings.dms_actor_user_id,
            email="",
            role="member",
            daily_usd=caps.get("daily_usd"),  # type: ignore[arg-type]
            monthly_usd=None,
            daily_tokens=caps.get("daily_tokens"),  # type: ignore[arg-type]
            monthly_tokens=None,
            allow_ask=bool(caps.get("allow_ask", True)),
        )
    )
    return client


def _read(events: tuple[UsageEvent, ...] = (), keys: tuple[KeyCap, ...] = ()) -> UsageRead:
    return UsageRead(ok=True, events=events, keys=keys)


def _event(*, tokens: int, cost: float | None) -> UsageEvent:
    return UsageEvent("evt-1", datetime.now(UTC), tokens, cost, "claude")


def test_budget_stops_the_ask_before_the_model(monkeypatch) -> None:
    called = {"n": 0}

    def fake_answer(*_args, **_kwargs):  # noqa: ANN002
        called["n"] += 1
        return {"badge": "ABSTAIN", "abstained": True, "answer": "held"}

    monkeypatch.setattr("dms_api.routes.chat.compliance_gate", _allow)
    monkeypatch.setattr("dms_api.routes.chat._answer_ask", fake_answer)
    set_reader(lambda _user, _since: _read((_event(tokens=20, cost=4.0),)))
    client = _cap(monkeypatch, daily_usd=1.0, daily_tokens=100)
    blocked = client.post("/v1/chat/ask", json={"question": "how many pallets"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "gov_budget"
    assert "budget" in blocked.json()["detail"]["message"]
    assert called["n"] == 0

    reset_store()
    under = UsageEvent("evt-2", datetime.now(UTC), 2, 0.1, "claude")
    set_reader(lambda _user, _since: _read((under,)))
    client = _cap(monkeypatch, daily_usd=1.0, daily_tokens=100)
    allowed = client.post("/v1/chat/ask", json={"question": "how many pallets"})
    assert allowed.status_code == 200
    assert called["n"] == 1


def test_feature_off_message(monkeypatch) -> None:
    monkeypatch.setattr("dms_api.routes.chat.compliance_gate", _allow)
    monkeypatch.setattr(
        "dms_api.routes.chat._answer_ask",
        lambda *_a, **_k: {"badge": "ABSTAIN", "abstained": True},
    )
    client = _cap(monkeypatch, allow_ask=False)
    response = client.post("/v1/chat/ask", json={"question": "how many"})
    assert response.status_code == 403
    body = response.json()["detail"]
    assert body["code"] == "gov_feature_off"
    assert "Ask is turned off" in body["message"]


def test_usd_only_unpriced_stops_and_token_cap_still_counts(monkeypatch) -> None:
    monkeypatch.setattr("dms_api.routes.chat.compliance_gate", _allow)
    monkeypatch.setattr(
        "dms_api.routes.chat._answer_ask",
        lambda *_a, **_k: {"badge": "ABSTAIN", "abstained": True},
    )
    set_reader(lambda _user, _since: _read((_event(tokens=3, cost=None),)))
    client = _cap(monkeypatch, daily_usd=1.0)
    stopped = client.post("/v1/chat/ask", json={"question": "how many"})
    assert stopped.status_code == 429
    assert stopped.json()["detail"]["code"] == "gov_usd_unpriced"

    reset_store()
    set_reader(lambda _user, _since: _read((_event(tokens=3, cost=None),)))
    client = _cap(monkeypatch, daily_usd=1.0, daily_tokens=100)
    allowed = client.post("/v1/chat/ask", json={"question": "how many"})
    assert allowed.status_code == 200


def test_provider_key_cap_stops_the_ask(monkeypatch) -> None:
    monkeypatch.setattr("dms_api.routes.chat.compliance_gate", _allow)
    monkeypatch.setattr(
        "dms_api.routes.chat._answer_ask",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("answer ran")),
    )
    set_reader(lambda _user, _since: _read((_event(tokens=1, cost=0.01),), (KeyCap(100, 100),)))
    client = _cap(monkeypatch, daily_tokens=1000)
    response = client.post("/v1/chat/ask", json={"question": "how many"})
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "gov_key_cap"


def test_upload_block_keeps_the_secret_out_of_the_record(monkeypatch) -> None:
    monkeypatch.setattr("dms_api.routes.studio.compliance_gate", _allow)
    monkeypatch.setattr(
        "dms_api.routes.studio.batch_ingest",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ingest ran")),
    )
    client = _app_on(monkeypatch)
    secret = _password_line()
    response = client.post(
        "/v1/studio/ingest",
        files={"file": ("notes.csv", secret, "text/csv")},
    )
    assert response.status_code == 422
    assert _SECRET not in response.text
    tenant = get_settings().dms_tenant_id
    findings = get_store().findings(tenant)
    assert findings
    assert any(row.finding_type.startswith("secret:") for row in findings)
    alerts = get_store().alerts(tenant)
    assert alerts
    recorded = "".join(row.finding_type + row.filename + row.file_sha256 for row in findings)
    blob = response.text + recorded
    blob += "".join(row.summary for row in alerts)
    assert _SECRET not in blob
    assert "The value was not stored." in alerts[0].summary


def test_vetted_rules_match_a_key_a_token_and_a_private_key() -> None:
    from dms_core.governance.scan import scan_text

    kinds, redacted = scan_text("password = " + _SECRET)
    assert any(kind.startswith("secret:") for kind in kinds)
    assert _SECRET not in redacted
    _kinds, aws = scan_text("key " + _aws_line())
    assert "secret:aws-access-token" in _kinds
    assert _aws_line() not in aws
    _kinds, pat = scan_text("token " + _pat())
    assert "secret:github-pat" in _kinds
    assert _pat() not in pat
    pem = (
        "-----BEGIN "
        + "RSA PRIVATE KEY-----\n"
        + ("A" * 80)
        + "\n-----END "
        + "RSA PRIVATE KEY-----"
    )
    kinds, hidden = scan_text(pem)
    assert "secret:private-key" in kinds
    assert "PRIVATE KEY" not in hidden


def test_redact_and_warn(monkeypatch) -> None:
    monkeypatch.setenv("DMS_GOV_CONSOLE", "1")
    from dms_api.gov_guard import guard_bytes

    settings = get_settings()
    payload = _password_line()
    get_store().set_upload_rule(settings.dms_tenant_id, Policy("redact", False))
    redacted = guard_bytes(settings, filename="notes.csv", data=payload)
    assert _SECRET.encode() not in redacted
    assert b"[redacted]" in redacted

    reset_store()
    get_store().set_upload_rule(settings.dms_tenant_id, Policy("warn", False))
    warned = guard_bytes(settings, filename="notes.csv", data=payload)
    assert warned == payload
    findings = get_store().findings(settings.dms_tenant_id)
    assert findings and findings[0].action == "warn"
    assert _SECRET not in findings[0].finding_type


def test_manager_sees_the_question_and_a_member_does_not(monkeypatch) -> None:
    client = _app_on(monkeypatch)
    monkeypatch.setattr("dms_api.routes.gov.compliance_gate", _allow)
    question = "how many pallets moved for " + _EMAIL
    app = client.app
    app.state.ask_audit_store.record(
        AskAuditRecord(
            ask_id="ask-gov-1",
            asked_at=datetime.now(UTC),
            actor="person-1",
            actor_kind="person",
            space_id="",
            question=question,
            executed_sql="select 1",
            tables_read=(),
            badge="validated",
            badge_level="",
            abstain_reason="",
            row_count=3,
            cortex_entry_id="",
            ask_mode="live",
        )
    )
    seen = client.get("/v1/gov/audit")
    assert seen.status_code == 200
    body = seen.json()["rows"][0]
    assert body["question"] == question
    assert body["sql"] == "select 1"
    assert body["row_count"] == 3
    assert "email" in body["pii"]

    settings = get_settings()
    get_store().set_upload_rule(settings.dms_tenant_id, Policy("block", True))
    redacted = client.get("/v1/gov/audit")
    assert _EMAIL not in redacted.text
    assert "DMSMASK" in redacted.text

    get_store().upsert_assignment(
        Assignment(
            settings.dms_tenant_id,
            settings.dms_actor_user_id,
            "",
            "member",
            None,
            None,
            None,
            None,
        )
    )
    denied = client.get("/v1/gov/audit")
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "gov_manager_required"
    assert question not in denied.text
    assert _EMAIL not in denied.text


def test_spend_csv_has_tokens_cost_and_model(monkeypatch) -> None:
    client = _app_on(monkeypatch)
    monkeypatch.setattr("dms_api.routes.gov.compliance_gate", _allow)
    set_reader(lambda _user, _since: _read((_event(tokens=9, cost=0.2),)))
    response = client.get("/v1/gov/spend.csv")
    assert response.status_code == 200
    assert "tokens" in response.text
    assert "cost_usd" in response.text
    assert "model" in response.text
    assert "claude" in response.text
    assert "9" in response.text
