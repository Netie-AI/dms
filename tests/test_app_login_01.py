"""UX-SIGNIN-01. Flag DMS_APP_LOGIN defaults off.

Off: auth routes are absent and /v1/spaces stays open.
On: email and password, session cookie, CSRF, rate limit, request access,
and a JSON 401/403 a person can read. No mail is sent. The password is not
logged and is not echoed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from cortex_client.gate import ComplianceDecision
from dms_api.app_login import enabled, hash_password, membership_for_email, parse_accounts
from fastapi.testclient import TestClient

PASSWORD = "test-password"
ROOT = Path(__file__).resolve().parents[1]


def _allow(**kwargs: object) -> ComplianceDecision:
    action = kwargs.get("action")
    return ComplianceDecision(
        allowed=True,
        reason="ok",
        action=action if isinstance(action, str) else "app_login",
    )


def _deny(**kwargs: object) -> ComplianceDecision:
    action = kwargs.get("action")
    return ComplianceDecision(
        allowed=False,
        reason="gate_refused",
        action=action if isinstance(action, str) else "app_login",
    )


@pytest.fixture
def accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    hashed = hash_password(PASSWORD)
    path = tmp_path / "accounts.txt"
    path.write_text(
        "\n".join(
            [
                f"buyer@example.com viewer {hashed}",
                f"steward@example.com steward {hashed}",
                "plain@example.com viewer not-a-hash",
                "# comment",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DMS_APP_LOGIN_ACCOUNTS_FILE", str(path))
    return hashed


def _app(monkeypatch: pytest.MonkeyPatch, *, flag: str | None, limit: str | None = None):
    if flag is None:
        monkeypatch.delenv("DMS_APP_LOGIN", raising=False)
    else:
        monkeypatch.setenv("DMS_APP_LOGIN", flag)
    if limit is None:
        monkeypatch.delenv("DMS_APP_LOGIN_ATTEMPT_LIMIT", raising=False)
    else:
        monkeypatch.setenv("DMS_APP_LOGIN_ATTEMPT_LIMIT", limit)
    monkeypatch.setattr("dms_api.routes.app_login.compliance_gate", _allow)
    from dms_api.app import create_app

    return create_app()


def _csrf(client: TestClient) -> str:
    res = client.get("/v1/auth/csrf")
    assert res.status_code == 200
    token = res.json()["token"]
    assert isinstance(token, str) and token
    return token


def _login(client: TestClient, email: str, password: str):
    token = _csrf(client)
    return client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
        headers={"x-csrf-token": token},
    )


def test_flag_off_leaves_the_app_open(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch, flag=None)
    assert app.state.app_login is None
    assert "/v1/auth/login" not in app.openapi()["paths"]
    with TestClient(app) as client:
        missing = client.post(
            "/v1/auth/login",
            json={"email": "buyer@example.com", "password": PASSWORD},
        )
        assert missing.status_code == 404
        assert client.get("/v1/spaces").status_code == 200
        assert client.get("/health").status_code == 200
    assert enabled() is False


def test_flag_is_not_enabled_in_deploy() -> None:
    for folder in ("deploy", ".github"):
        for path in (ROOT / folder).rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in {".png", ".jpg", ".woff", ".woff2"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "DMS_APP_LOGIN=1" not in text, path
            assert 'DMS_APP_LOGIN: "1"' not in text, path


def test_plaintext_accounts_are_skipped() -> None:
    found = parse_accounts("buyer@example.com viewer not-a-hash\n")
    assert found == {}
    assert membership_for_email(None, None, "buyer@example.com") is None


def test_login_sets_an_httponly_session(
    monkeypatch: pytest.MonkeyPatch, accounts: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    app = _app(monkeypatch, flag="1")
    with TestClient(app) as client:
        bad = _login(client, "buyer@example.com", "wrong-password")
        assert bad.status_code == 401
        assert bad.json()["code"] == "sign_in_failed"
        assert "request access" in bad.json()["message"]
        assert "dms_session" not in bad.headers.get("set-cookie", "")
        assert PASSWORD not in bad.text

        unknown = _login(client, "nobody@example.com", PASSWORD)
        assert unknown.status_code == 401
        assert unknown.json()["code"] == "sign_in_failed"

        ok = _login(client, "Buyer@Example.com", PASSWORD)
        assert ok.status_code == 200
        assert ok.json()["email"] == "buyer@example.com"
        assert ok.json()["role"] == "viewer"
        cookie = ok.headers["set-cookie"].lower()
        assert "dms_session=" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert PASSWORD not in cookie
        assert accounts not in ok.text
        assert PASSWORD not in ok.text

        session = client.get("/v1/auth/session")
        assert session.status_code == 200
        assert session.json()["authenticated"] is True
        assert session.json()["role"] == "viewer"

        spaces = client.get("/v1/spaces")
        assert spaces.status_code == 200
    assert PASSWORD not in caplog.text
    assert accounts not in caplog.text


def test_missing_session_is_a_readable_401(monkeypatch: pytest.MonkeyPatch, accounts: str) -> None:
    del accounts
    app = _app(monkeypatch, flag="1")
    with TestClient(app) as client:
        res = client.get("/v1/spaces")
        assert res.status_code == 401
        body = res.json()
        assert body["code"] == "sign_in_required"
        assert "Sign in" in body["message"]
        assert "popup" in body["message"]
        assert client.get("/health").status_code == 200
        session = client.get("/v1/auth/session")
        assert session.status_code == 200
        assert session.json()["authenticated"] is False
        assert session.json()["enabled"] is True


def test_login_without_csrf_is_refused(monkeypatch: pytest.MonkeyPatch, accounts: str) -> None:
    del accounts
    app = _app(monkeypatch, flag="1")
    with TestClient(app) as client:
        res = client.post(
            "/v1/auth/login",
            json={"email": "buyer@example.com", "password": PASSWORD},
        )
        assert res.status_code == 403
        assert res.json()["code"] == "form_expired"
        assert "dms_session" not in res.headers.get("set-cookie", "")


def test_a_validation_error_does_not_echo_the_password(
    monkeypatch: pytest.MonkeyPatch, accounts: str
) -> None:
    del accounts
    app = _app(monkeypatch, flag="1")
    with TestClient(app) as client:
        res = client.post(
            "/v1/auth/login",
            json={"email": "buyer@example.com", "password": PASSWORD, "extra": "nope"},
        )
        assert res.status_code == 422
        assert PASSWORD not in res.text


def test_rate_limit_and_gate_refusal(monkeypatch: pytest.MonkeyPatch, accounts: str) -> None:
    del accounts
    app = _app(monkeypatch, flag="1", limit="2")
    with TestClient(app) as client:
        assert _login(client, "buyer@example.com", "wrong-password").status_code == 401
        assert _login(client, "buyer@example.com", "wrong-password").status_code == 401
        blocked = _login(client, "buyer@example.com", "wrong-password")
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "too_many_attempts"
        assert PASSWORD not in blocked.text

    denied_app = _app(monkeypatch, flag="1")
    monkeypatch.setattr("dms_api.routes.app_login.compliance_gate", _deny)
    with TestClient(denied_app) as client:
        refused = _login(client, "buyer@example.com", PASSWORD)
        assert refused.status_code == 403
        assert refused.json()["code"] == "access_denied"
        assert "dms_session" not in refused.headers.get("set-cookie", "")
        assert PASSWORD not in refused.text


def test_request_access_is_filed_for_a_steward(
    monkeypatch: pytest.MonkeyPatch, accounts: str
) -> None:
    del accounts
    app = _app(monkeypatch, flag="1")
    with TestClient(app) as client:
        token = _csrf(client)
        filed = client.post(
            "/v1/auth/request-access",
            json={
                "name": "A Buyer",
                "email": "buyer.desk@example.com",
                "company": "Northwind",
                "note": "Need the warehouse space",
            },
            headers={"x-csrf-token": token},
        )
        assert filed.status_code == 200
        assert filed.json()["code"] == "request_filed"
        assert "emailed" not in filed.json()["message"].lower()
        route_src = (ROOT / "apps/api/dms_api/routes/app_login.py").read_text(encoding="utf-8")
        assert "smtplib" not in route_src
        assert client.get("/v1/auth/access-requests").status_code == 401

        viewer = _login(client, "buyer@example.com", PASSWORD)
        assert viewer.status_code == 200
        denied = client.get("/v1/auth/access-requests")
        assert denied.status_code == 403
        assert denied.json()["code"] == "access_denied"
        assert "steward" in denied.json()["message"]

        token = _csrf(client)
        out = client.post("/v1/auth/logout", headers={"x-csrf-token": token})
        assert out.status_code == 200
        assert client.get("/v1/auth/session").json()["authenticated"] is False

        steward = _login(client, "steward@example.com", PASSWORD)
        assert steward.status_code == 200
        listed = client.get("/v1/auth/access-requests")
        assert listed.status_code == 200
        rows = listed.json()["requests"]
        assert rows[0]["email"] == "buyer.desk@example.com"
        assert rows[0]["note"] == "Need the warehouse space"
        assert "smtp" not in listed.text.lower()


def test_membership_role_wins(monkeypatch: pytest.MonkeyPatch, accounts: str) -> None:
    del accounts
    monkeypatch.setattr(
        "dms_api.app_login.membership_for_email",
        lambda *_args, **_kwargs: ("user-from-store", "admin"),
    )
    app = _app(monkeypatch, flag="1")
    with TestClient(app) as client:
        ok = _login(client, "buyer@example.com", PASSWORD)
        assert ok.status_code == 200
        assert ok.json()["role"] == "admin"
        assert "user-from-store" not in ok.text


def test_identity_header_is_still_refused(monkeypatch: pytest.MonkeyPatch, accounts: str) -> None:
    del accounts
    app = _app(monkeypatch, flag="1")
    with TestClient(app) as client:
        res = client.get("/health", headers={"x-dms-role": "admin"})
        assert res.status_code == 400
        assert res.json()["code"] == "identity_header_not_accepted"
