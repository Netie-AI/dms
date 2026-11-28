"""READY-02: DMS_SSO off is the current API. On, every GET but the allowlist is 401.

The allowlist is ``auth_sso.ALLOWED_GET_PATHS``. A new unauthenticated GET fails
this file. Roles are admin, steward, analyst, and viewer. Companies do not see
each other's spaces, library files, or ask-audit export. The OpenVault service
bearer still calls the deployment tenant.
"""

from __future__ import annotations

import base64
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from dms_api.auth_sso import (
    ALLOWED_GET_PATHS,
    AuthError,
    Principal,
    bind_principal,
    hash_password,
    reset_for_tests,
    reset_principal,
    unauthenticated_get_allowed,
    verify_rs256,
)
from dms_api.middleware_actor import RejectIdentityHeadersMiddleware
from dms_api.routes.audit import ACTOR_BASIS
from dms_core.control_plane.ask_audit import AskAuditRecord
from fastapi.testclient import TestClient

_PARAM = re.compile(r"\{[^}]+\}")
_PASSWORD = "correct-horse-12"
_SECRET = "unit-test-oidc-secret"
_TENANT_B = "11111111-1111-4111-8111-111111111111"
_TENANT_C = "22222222-2222-4222-8222-222222222222"
_EXPLICIT_GETS = frozenset(
    {
        "/health",
        "/v1/auth/oidc/google/start",
        "/v1/auth/oidc/google/callback",
        "/v1/auth/oidc/entra/start",
        "/v1/auth/oidc/entra/callback",
        "/v1/auth/oidc/okta/start",
        "/v1/auth/oidc/okta/callback",
    }
)


def _concrete(path: str) -> str:
    return _PARAM.sub("probe", path)


def _walk_gets(routes: object, found: list[str]) -> None:
    """Every GET, including ones FastAPI keeps on an included router."""
    for route in routes:  # type: ignore[union-attr]
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if path and "GET" in methods:
            found.append(path)
        nested = getattr(route, "original_router", None)
        if nested is not None:
            _walk_gets(nested.routes, found)
        sub = getattr(route, "routes", None)
        if sub:
            _walk_gets(sub, found)


def _get_paths(client: TestClient) -> list[str]:
    found: list[str] = []
    _walk_gets(client.app.routes, found)
    return found


def _code(response) -> str | None:
    if "json" not in response.headers.get("content-type", ""):
        return None
    detail = response.json().get("detail")
    if isinstance(detail, dict):
        return detail.get("code")
    return None


@pytest.fixture
def sso(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DMS_SSO", "1")
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", "ready02-service-token")
    reset_for_tests()
    from dms_api.app import create_app
    from dms_api.tenant_scope import clear_for_tests

    app = create_app()
    with TestClient(app) as client:
        yield client
    clear_for_tests()
    reset_for_tests()


def _user(email: str, role: str, tenant: str) -> None:
    from dms_api.auth_sso import provision_tenant, provision_user

    if tenant not in ("already",):
        try:
            provision_tenant(tenant, tenant)
        except AuthError:
            pass
    provision_user(email=email, password=_PASSWORD, role=role, tenant_id=tenant)


def _login(client: TestClient, email: str, company: str | None = None) -> str:
    body: dict[str, str] = {"email": email, "password": _PASSWORD}
    if company is not None:
        body["company"] = company
    res = client.post("/v1/auth/login", json=body)
    assert res.status_code == 200, res.text
    return res.json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_flag_off_is_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset and any value other than the digit 1 mount the same API."""
    monkeypatch.delenv("DMS_SSO", raising=False)
    from dms_api.app import create_app

    with TestClient(create_app()) as plain:
        spaces = plain.get("/v1/spaces").content
        spec = plain.get("/openapi.json").content
        export = plain.get("/v1/audit/export")
        names = [m.cls.__name__ for m in plain.app.user_middleware]
    assert "AuthRequiredMiddleware" not in names
    assert b"/v1/auth/login" not in spec
    assert export.status_code == 200
    assert export.headers["X-Audit-Actor-Basis"] == ACTOR_BASIS

    monkeypatch.setenv("DMS_SSO", "true")
    with TestClient(create_app()) as other:
        assert other.get("/v1/spaces").content == spaces
        assert other.get("/openapi.json").content == spec
        assert other.get("/v1/audit/export").headers["X-Audit-Actor-Basis"] == ACTOR_BASIS
        assert [m.cls.__name__ for m in other.app.user_middleware] == names


def test_allowlist_is_explicit_and_every_other_get_is_401(sso: TestClient) -> None:
    assert ALLOWED_GET_PATHS == _EXPLICIT_GETS
    seen = _get_paths(sso)
    assert "/v1/audit/export" in seen
    assert "/health" in seen
    for path in seen:
        hit = _concrete(path)
        res = sso.get(hit)
        if unauthenticated_get_allowed(hit):
            assert _code(res) != "auth_required", path
        else:
            assert res.status_code == 401, path
            assert _code(res) == "auth_required"
    for path in ("/openapi.json", "/docs", "/redoc"):
        res = sso.get(path)
        assert res.status_code == 401
        assert _code(res) == "auth_required"
    for path in ALLOWED_GET_PATHS:
        res = sso.get(path)
        assert _code(res) != "auth_required", path


def test_service_bearer_still_reaches_the_deployment(sso: TestClient) -> None:
    denied = sso.get("/v1/audit/export")
    assert denied.status_code == 401
    ok = sso.get("/v1/audit/export", headers=_auth("ready02-service-token"))
    assert ok.status_code == 200
    assert ok.headers["X-Audit-Actor-Basis"] == ACTOR_BASIS
    bad = sso.get("/v1/spaces", headers=_auth("not-the-service-token"))
    assert bad.status_code == 401
    spaces = sso.get("/v1/spaces", headers=_auth("ready02-service-token"))
    assert spaces.status_code == 200
    names = {row["name"] for row in spaces.json()["spaces"]}
    assert names == {"Finance", "Warehouse Ops"}
    # The service bearer keeps the calls it had. The role gate is not what stops a write.
    write = sso.post(
        "/v1/spaces",
        json={"name": "From service"},
        headers=_auth("ready02-service-token"),
    )
    assert write.status_code == 403
    assert _code(write) != "role_forbidden"


def test_identity_header_stays_a_400_with_a_valid_bearer(sso: TestClient) -> None:
    res = sso.get(
        "/v1/spaces",
        headers={**_auth("ready02-service-token"), "x-dms-role": "admin"},
    )
    assert res.status_code == 400
    assert res.json()["code"] == "identity_header_not_accepted"
    assert RejectIdentityHeadersMiddleware.__name__


def test_login_roles_and_a_422_does_not_echo_the_password(sso: TestClient) -> None:
    _user("view@example.com", "viewer", _TENANT_B)
    _user("ana@example.com", "analyst", _TENANT_B)
    _user("stew@example.com", "steward", _TENANT_B)
    _user("adm@example.com", "admin", _TENANT_B)
    leaked = sso.post(
        "/v1/auth/login",
        json={"email": "view@example.com", "password": _PASSWORD, "extra": True},
    )
    assert leaked.status_code == 422
    assert _PASSWORD not in leaked.text

    viewer = _login(sso, "view@example.com")
    analyst = _login(sso, "ana@example.com")
    steward = _login(sso, "stew@example.com")
    admin = _login(sso, "adm@example.com")
    me = sso.get("/v1/auth/me", headers=_auth(viewer))
    assert me.status_code == 200
    assert me.json()["role"] == "viewer"
    assert me.json()["kind"] == "user"

    blocked = sso.post("/v1/spaces", json={"name": "Nope"}, headers=_auth(viewer))
    assert blocked.status_code == 403
    assert _code(blocked) == "role_forbidden"
    ask = sso.post("/v1/chat/ask", json={}, headers=_auth(viewer))
    assert _code(ask) == "role_forbidden"
    analyst_ask = sso.post("/v1/chat/ask", json={}, headers=_auth(analyst))
    assert analyst_ask.status_code == 422
    assert _code(analyst_ask) != "role_forbidden"
    for token in (steward, admin):
        res = sso.post("/v1/spaces", json={"name": "Pilot"}, headers=_auth(token))
        assert res.status_code == 403
        assert _code(res) != "role_forbidden"


def test_two_companies_do_not_see_each_other(sso: TestClient) -> None:
    from dms_api.auth_sso import provision_tenant

    provision_tenant(_TENANT_B, "Beta")
    provision_tenant(_TENANT_C, "Ceta")
    _user("b@example.com", "steward", _TENANT_B)
    _user("c@example.com", "steward", _TENANT_C)
    principal = Principal(
        user_id="beta",
        email="b@example.com",
        tenant_id=_TENANT_B,
        role="steward",
        kind="user",
    )
    token = bind_principal(principal)
    try:
        made = sso.app.state.space_store.create("Only Beta")
        sso.app.state.ask_audit_store.record(
            AskAuditRecord(
                ask_id="ready02-beta-ask",
                asked_at=datetime.now(UTC),
                actor="beta",
                actor_kind="person",
                space_id=made.id,
                question="tenant-b-private-question",
                executed_sql="select 1",
                tables_read=(),
                badge="abstain",
                badge_level="",
                abstain_reason="",
                row_count=0,
                cortex_entry_id="",
                ask_mode="live",
            )
        )
    finally:
        reset_principal(token)

    beta = _auth(_login(sso, "b@example.com"))
    ceta = _auth(_login(sso, "c@example.com"))
    service = _auth("ready02-service-token")

    beta_names = {row["name"] for row in sso.get("/v1/spaces", headers=beta).json()["spaces"]}
    ceta_names = {row["name"] for row in sso.get("/v1/spaces", headers=ceta).json()["spaces"]}
    service_names = {row["name"] for row in sso.get("/v1/spaces", headers=service).json()["spaces"]}
    assert "Only Beta" in beta_names
    assert "Finance" not in beta_names
    assert ceta_names == set()
    assert service_names == {"Finance", "Warehouse Ops"}
    assert "Only Beta" not in service_names

    assert sso.get(f"/v1/spaces/{made.id}", headers=beta).status_code == 200
    assert sso.get(f"/v1/spaces/{made.id}", headers=ceta).status_code == 404
    assert sso.get(f"/v1/spaces/{made.id}", headers=service).status_code == 404

    beta_export = sso.get("/v1/audit/export", headers=beta).text
    ceta_export = sso.get("/v1/audit/export", headers=ceta).text
    service_export = sso.get("/v1/audit/export", headers=service).text
    assert "tenant-b-private-question" in beta_export
    assert "tenant-b-private-question" not in ceta_export
    assert "tenant-b-private-question" not in service_export
    assert sso.get("/v1/audit/export", headers=beta).headers["X-Audit-Actor-Basis"] != ACTOR_BASIS

    beta_files = sso.get("/v1/library/sources", headers=beta).text
    service_files = sso.get("/v1/library/sources", headers=service).text
    assert "Finance/workbooks/sales_q3.xlsx" not in beta_files
    assert "Finance/workbooks/sales_q3.xlsx" in service_files


def test_login_requires_a_tenant_when_the_user_has_two(sso: TestClient) -> None:
    from dms_api.auth_sso import provision_tenant, provision_user

    provision_tenant(_TENANT_B, "Beta")
    provision_tenant(_TENANT_C, "Ceta")
    provision_user(email="both@example.com", password=_PASSWORD, role="viewer", tenant_id=_TENANT_B)
    provision_user(email="both@example.com", password=_PASSWORD, role="admin", tenant_id=_TENANT_C)
    missing = sso.post("/v1/auth/login", json={"email": "both@example.com", "password": _PASSWORD})
    assert missing.status_code == 409
    assert _code(missing) == "company_required"
    token = _login(sso, "both@example.com", _TENANT_C)
    assert sso.get("/v1/auth/me", headers=_auth(token)).json()["role"] == "admin"


def test_ask_audit_names_the_verified_user(sso: TestClient) -> None:
    from dms_api.ask_audit import _recorded_actor, _recorded_kind
    from dms_api.settings import get_settings

    _user("view@example.com", "viewer", _TENANT_B)
    token = _login(sso, "view@example.com")
    me = sso.get("/v1/auth/me", headers=_auth(token)).json()
    principal = Principal(
        user_id=me["user_id"],
        email="view@example.com",
        tenant_id=_TENANT_B,
        role="viewer",
        kind="user",
    )
    handle = bind_principal(principal)
    try:
        assert _recorded_actor(get_settings()) == me["user_id"]
        assert _recorded_kind() == "person"
    finally:
        reset_principal(handle)
    assert _recorded_kind() == "deployment"


def test_session_expires(sso: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.auth_sso as auth

    _user("view@example.com", "viewer", _TENANT_B)
    monkeypatch.setattr(auth, "_now", lambda: 10_000.0)
    token = _login(sso, "view@example.com")
    assert sso.get("/v1/auth/me", headers=_auth(token)).status_code == 200
    monkeypatch.setattr(auth, "_now", lambda: 10_000.0 + 13 * 60 * 60)
    assert sso.get("/v1/auth/me", headers=_auth(token)).status_code == 401


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwt(key: rsa.RSAPrivateKey, payload: dict, *, alg: str = "RS256", kid: str = "k1") -> str:
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    header_b = _b64(json.dumps(header, separators=(",", ":")).encode())
    payload_b = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signing = f"{header_b}.{payload_b}".encode()
    if alg == "none":
        return f"{header_b}.{payload_b}."
    sig = key.sign(signing, padding.PKCS1v15(), hashes.SHA256())
    return f"{header_b}.{payload_b}.{_b64(sig)}"


def _jwks(key: rsa.RSAPrivateKey) -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": "k1",
                "alg": "RS256",
                "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
            }
        ]
    }


def test_oidc_google_entra_and_okta(sso: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from dms_api.auth_sso import provision_tenant

    provision_tenant(_TENANT_B, "Beta")
    monkeypatch.setenv("DMS_OIDC_GOOGLE_CLIENT_ID", "unit-test-client")
    monkeypatch.setenv("DMS_OIDC_GOOGLE_CRED_REF", "oidc/google")
    monkeypatch.setenv("DMS_OIDC_ENTRA_CLIENT_ID", "unit-test-client")
    monkeypatch.setenv("DMS_OIDC_ENTRA_CRED_REF", "oidc/entra")
    monkeypatch.setenv("DMS_OIDC_ENTRA_TENANT", _TENANT_B)
    monkeypatch.setenv("DMS_OIDC_OKTA_CLIENT_ID", "unit-test-client")
    monkeypatch.setenv("DMS_OIDC_OKTA_CRED_REF", "oidc/okta")
    monkeypatch.setenv("DMS_OIDC_OKTA_ISSUER", "https://example.okta.com/oauth2/default")

    import dms_api.auth_sso as auth

    auth.set_secret_loader(lambda ref: _SECRET)
    redirect = "http://127.0.0.1:9/cb"
    entra = sso.get(
        "/v1/auth/oidc/entra/start",
        params={"redirect_uri": redirect},
        follow_redirects=False,
    )
    assert entra.status_code == 302
    entra_loc = entra.headers["location"]
    assert f"login.microsoftonline.com/{_TENANT_B}/oauth2/v2.0/authorize" in entra_loc
    assert _SECRET not in entra.headers["location"]
    okta = sso.get(
        "/v1/auth/oidc/okta/start",
        params={"redirect_uri": redirect},
        follow_redirects=False,
    )
    assert okta.headers["location"].startswith(
        "https://example.okta.com/oauth2/default/v1/authorize?"
    )
    assert _SECRET not in okta.headers["location"]

    start = sso.get(
        "/v1/auth/oidc/google/start",
        params={"redirect_uri": redirect},
        follow_redirects=False,
    )
    assert start.status_code == 302
    location = start.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert _SECRET not in location
    query = parse_qs(urlsplit(location).query)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = _jwt(
        key,
        {
            "iss": "https://accounts.google.com",
            "aud": "unit-test-client",
            "exp": now + 300,
            "nonce": query["nonce"][0],
            "email": "ada@example.com",
            "roles": ["analyst", "viewer"],
            "tenant_id": _TENANT_B,
        },
    )
    seen: dict[str, str] = {}

    def _exchange(cfg, code, redirect_uri, secret):
        seen["secret"] = secret
        seen["url"] = cfg.token_url
        assert secret not in cfg.token_url
        return {"id_token": token}

    monkeypatch.setattr(auth, "_exchange_code", _exchange)
    monkeypatch.setattr(auth, "_fetch_jwks", lambda url: _jwks(key))
    done = sso.get(
        "/v1/auth/oidc/google/callback",
        params={"code": "unit-code", "state": query["state"][0]},
    )
    assert done.status_code == 200, done.text
    assert seen["secret"] == _SECRET
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    me = sso.get("/v1/auth/me", headers=_auth(done.json()["token"]))
    assert me.json()["role"] == "analyst"
    assert me.json()["tenant_id"] == _TENANT_B
    assert me.json()["email"] == "ada@example.com"


def test_oidc_rejects_alg_none() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _jwt(key, {"iss": "x", "aud": "y", "exp": 9, "nonce": "n"}, alg="none")
    with pytest.raises(AuthError) as caught:
        verify_rs256(
            token,
            jwks=_jwks(key),
            issuer="x",
            audience="y",
            nonce="n",
            now=1,
        )
    assert caught.value.code == "oidc_token_rejected"


def test_bootstrap_file_uses_a_hash(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    digest = hash_password(_PASSWORD)
    assert _PASSWORD not in digest
    path = tmp_path / "bootstrap.txt"
    path.write_text(
        "\n".join(
            [
                f"tenant {_TENANT_B} Beta Co",
                f"user ada@example.com steward {_TENANT_B} {digest}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DMS_SSO", "1")
    monkeypatch.setenv("DMS_SSO_BOOTSTRAP_FILE", str(path))
    reset_for_tests()
    from dms_api.app import create_app

    with TestClient(create_app()) as client:
        token = _login(client, "ada@example.com")
        me = client.get("/v1/auth/me", headers=_auth(token))
        assert me.json()["role"] == "steward"
        assert me.json()["tenant_id"] == _TENANT_B
    reset_for_tests()
