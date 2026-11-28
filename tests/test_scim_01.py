"""SCIM-01. Flag off is a no-op. On, Entra-shaped SCIM calls provision users.

The bearer lives in the test vault double. Product code stores the hash and
the cred_ref. These tests assert the rendered SCIM resource, the role, the
budget, and the space list, not only the SQL.
"""

from __future__ import annotations

import json
import logging

import pytest
from dms_api.app import create_app
from dms_api.auth_sso import provision_tenant, provision_user, reset_for_tests
from dms_api.scim_vault import OpenVaultScim, set_vault
from dms_api.settings import get_settings
from dms_core.governance.store import get_store as gov_store
from dms_core.governance.store import reset_store as reset_gov
from dms_core.scim.store import get_store, reset_store, set_rate_limit_for_tests
from fastapi.testclient import TestClient

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WAREHOUSE = "dddddddd-dddd-dddd-dddd-dddddddddddd"
USER = "urn:ietf:params:scim:schemas:core:2.0:User"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
PASSWORD = "correct-horse-12"
SECRET = "super-secret-value"


class MemoryVault:
    """Test double. Not a product store. The product client is OpenVaultScim."""

    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def put(self, tenant_id: str, token: str) -> str:
        ref = "dms/scim-test"
        self.saved[ref] = token
        return ref

    def reveal(self, cred_ref: str) -> str:
        return self.saved[cred_ref]


def setup_function() -> None:
    reset_store()
    reset_gov()
    reset_for_tests()
    set_vault(MemoryVault())


def teardown_function() -> None:
    reset_store()
    reset_gov()
    reset_for_tests()
    set_vault(None)
    set_rate_limit_for_tests(120)


def _allow_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex_client.gate import ComplianceDecision

    def allow(*, action: str, **_: object) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    import dms_api.routes.auth_sso as auth_routes
    import dms_api.routes.scim as scim_routes

    monkeypatch.setattr(scim_routes, "compliance_gate", allow)
    monkeypatch.setattr(auth_routes, "compliance_gate", allow)


def _client(
    monkeypatch: pytest.MonkeyPatch, *, scim: str = "1", sso: str | None = None
) -> TestClient:
    monkeypatch.setenv("DMS_SCIM", scim)
    if sso is None:
        monkeypatch.delenv("DMS_SSO", raising=False)
    else:
        monkeypatch.setenv("DMS_SSO", sso)
    _allow_gate(monkeypatch)
    return TestClient(create_app())


def _issue(client: TestClient, headers: dict[str, str] | None = None) -> str:
    res = client.post("/v1/scim/admin/token", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cred_ref"] == "dms/scim-test"
    assert body["token"]
    return str(body["token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _scim(
    client: TestClient,
    method: str,
    path: str,
    token: str,
    body: dict[str, object] | None = None,
    params: dict[str, str] | None = None,
) -> object:
    headers = {**_auth(token), "content-type": "application/scim+json"}
    content = None if body is None else json.dumps(body)
    return client.request(method, path, content=content, headers=headers, params=params)


def _sample(
    user_name: str = "TestUser@example.com", department: str = "Finance"
) -> dict[str, object]:
    return {
        "schemas": [USER, ENTERPRISE],
        "externalId": "0a21f0f2-8d2a-4f8e-bf98-7363c4aed4ef",
        "userName": user_name,
        "active": True,
        "displayName": "Test User",
        "emails": [{"primary": True, "type": "work", "value": user_name}],
        "name": {"familyName": "familyName", "givenName": "givenName"},
        "roles": [],
        ENTERPRISE: {"department": department, "manager": {"value": "mgr-1"}},
    }


def _map(client: TestClient, **extra: object) -> dict[str, object]:
    body = {
        "kind": "department",
        "source": "Finance",
        "directory_role": "analyst",
        "gov_role": "member",
        "priority": 50,
        "spaces": [FINANCE],
        "daily_tokens": 1000,
        "ask": True,
        "create": True,
        "connectors": True,
    }
    body.update(extra)
    res = client.put("/v1/scim/admin/maps", json=body)
    assert res.status_code == 200, res.text
    return res.json()["map"]


def test_only_the_digit_1_mounts_scim_and_spaces_stay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SCIM", raising=False)
    monkeypatch.delenv("DMS_SSO", raising=False)
    off = TestClient(create_app())
    spaces = off.get("/v1/spaces").json()
    assert len(spaces["spaces"]) == 2
    for raw in ("0", "true", "yes", "on", " 1 ", "01"):
        monkeypatch.setenv("DMS_SCIM", raw)
        client = TestClient(create_app())
        assert client.get("/scim/v2/ServiceProviderConfig").status_code == 404
        assert client.get("/v1/scim/admin/maps").status_code == 404
        assert client.get("/v1/spaces").json() == spaces


def test_missing_bearer_is_401_and_does_not_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    guess = "not-a-real-token"
    res = client.get("/scim/v2/Users", headers={"Authorization": f"Bearer {guess}"})
    assert res.status_code == 401
    assert res.json()["detail"] == "unauthorized"
    assert guess not in res.text
    assert "application/scim+json" in res.headers["content-type"]


def test_service_provider_config_matches_entra(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    res = client.get("/scim/v2/ServiceProviderConfig", headers=_auth(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patch"]["supported"] is True
    assert body["filter"]["supported"] is True
    assert body["filter"]["maxResults"] == 200
    assert body["bulk"]["supported"] is False
    assert body["changePassword"]["supported"] is False
    assert body["sort"]["supported"] is False
    assert body["etag"]["supported"] is False
    assert body["authenticationSchemes"][0]["type"] == "oauthbearertoken"


def test_resource_types_and_schemas_list_user_name(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    types = client.get("/scim/v2/ResourceTypes", headers=_auth(token))
    assert types.status_code == 200
    names = {row["name"] for row in types.json()["Resources"]}
    assert names == {"User", "Group"}
    one = client.get("/scim/v2/ResourceTypes/User", headers=_auth(token))
    assert one.status_code == 200
    assert one.json()["endpoint"] == "/Users"
    schemas = client.get("/scim/v2/Schemas", headers=_auth(token))
    assert schemas.status_code == 200
    user = next(row for row in schemas.json()["Resources"] if row["name"] == "User")
    assert "schemas" in user
    assert "userName" in {attr["name"] for attr in user["attributes"]}
    enterprise = next(row for row in schemas.json()["Resources"] if row["name"] == "EnterpriseUser")
    assert "department" in {attr["name"] for attr in enterprise["attributes"]}


def test_create_get_filter_and_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    created = _scim(client, "POST", "/scim/v2/Users", token, _sample())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["userName"] == "TestUser@example.com"
    assert body["active"] is True
    assert body[ENTERPRISE]["department"] == "Finance"
    assert body[ENTERPRISE]["manager"]["value"] == "mgr-1"
    assert body["externalId"] == "0a21f0f2-8d2a-4f8e-bf98-7363c4aed4ef"
    assert SECRET not in created.text
    assert created.headers["location"].endswith(f"/Users/{body['id']}")
    got = client.get(f"/scim/v2/Users/{body['id']}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]
    found = client.get(
        "/scim/v2/Users",
        params={"filter": 'userName  eq  "testuser@example.com"'},
        headers=_auth(token),
    )
    assert found.status_code == 200
    assert found.json()["totalResults"] == 1
    assert found.json()["Resources"][0]["id"] == body["id"]
    missing = client.get(
        "/scim/v2/Users",
        params={"filter": 'userName eq "nobody@example.com"'},
        headers=_auth(token),
    )
    assert missing.json()["totalResults"] == 0
    assert missing.json()["Resources"] == []
    again = _scim(client, "POST", "/scim/v2/Users", token, _sample())
    assert again.status_code == 409
    assert again.json()["scimType"] == "uniqueness"
    bad = client.get(
        "/scim/v2/Users",
        params={"filter": 'userName co "test"'},
        headers=_auth(token),
    )
    assert bad.status_code == 400
    assert bad.json()["scimType"] == "invalidFilter"


def test_patch_ops_are_case_insensitive_and_entra_shaped(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    user_id = _scim(client, "POST", "/scim/v2/Users", token, _sample()).json()["id"]

    def patch(ops: list[dict[str, object]]) -> dict[str, object]:
        res = _scim(
            client,
            "PATCH",
            f"/scim/v2/Users/{user_id}",
            token,
            {"schemas": [PATCH], "Operations": ops},
        )
        assert res.status_code == 200, res.text
        return res.json()

    assert patch([{"op": "Replace", "path": "active", "value": False}])["active"] is False
    assert patch([{"op": "REPLACE", "path": "active", "value": True}])["active"] is True
    renamed = patch([{"op": "Add", "path": "displayName", "value": "Added Name"}])
    assert renamed["displayName"] == "Added Name"
    dept = patch(
        [
            {
                "op": "replace",
                "path": f"{ENTERPRISE}:department",
                "value": "Ops",
            }
        ]
    )
    assert dept[ENTERPRISE]["department"] == "Ops"
    assert patch([{"op": "replace", "path": "active", "value": "False"}])["active"] is False
    mailed = patch(
        [{"op": "replace", "path": 'emails[type eq "work"].value', "value": "work@example.com"}]
    )
    assert mailed["emails"][0]["value"] == "work@example.com"
    pathless = patch([{"op": "replace", "value": {"active": True, "displayName": "Pathless"}}])
    assert pathless["active"] is True
    assert pathless["displayName"] == "Pathless"


def test_pagination_start_index_and_count(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    for index in range(3):
        res = _scim(
            client,
            "POST",
            "/scim/v2/Users",
            token,
            _sample(f"user{index}@example.com", "Ops"),
        )
        assert res.status_code == 201, res.text
    page = client.get(
        "/scim/v2/Users",
        params={"startIndex": "2", "count": "1"},
        headers=_auth(token),
    )
    body = page.json()
    assert body["totalResults"] == 3
    assert body["startIndex"] == 2
    assert body["itemsPerPage"] == 1
    assert len(body["Resources"]) == 1
    past = client.get(
        "/scim/v2/Users",
        params={"startIndex": "50", "count": "1"},
        headers=_auth(token),
    )
    assert past.json()["totalResults"] == 3
    assert past.json()["Resources"] == []
    zero = client.get(
        "/scim/v2/Users",
        params={"startIndex": "0", "count": "1"},
        headers=_auth(token),
    )
    assert zero.json()["startIndex"] == 1
    assert zero.json()["itemsPerPage"] == 1


def test_delete_deactivates_and_get_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    user_id = _scim(client, "POST", "/scim/v2/Users", token, _sample()).json()["id"]
    deleted = client.delete(f"/scim/v2/Users/{user_id}", headers=_auth(token))
    assert deleted.status_code == 204
    assert deleted.content == b""
    got = client.get(f"/scim/v2/Users/{user_id}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["active"] is False
    found = client.get(
        "/scim/v2/Users",
        params={"filter": 'userName eq "TestUser@example.com"'},
        headers=_auth(token),
    )
    assert found.json()["totalResults"] == 1
    assert found.json()["Resources"][0]["active"] is False


def test_put_replaces_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    user_id = _scim(client, "POST", "/scim/v2/Users", token, _sample()).json()["id"]
    body = _sample()
    body["displayName"] = "Replaced"
    body[ENTERPRISE] = {"department": "Replaced Dept"}
    res = _scim(client, "PUT", f"/scim/v2/Users/{user_id}", token, body)
    assert res.status_code == 200, res.text
    assert res.json()["displayName"] == "Replaced"
    assert res.json()[ENTERPRISE]["department"] == "Replaced Dept"
    assert res.json()[ENTERPRISE].get("manager") is None


def test_password_is_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    body = _sample()
    body["password"] = SECRET
    res = _scim(client, "POST", "/scim/v2/Users", token, body)
    assert res.status_code == 201, res.text
    assert SECRET not in res.text
    blob = json.dumps([user.__dict__ for user in get_store()._users.values()], default=str)
    assert SECRET not in blob


def test_groups_add_remove_filter_and_excluded_members(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    user_id = _scim(client, "POST", "/scim/v2/Users", token, _sample()).json()["id"]
    created = _scim(
        client,
        "POST",
        "/scim/v2/Groups",
        token,
        {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "displayName": "Engineers"},
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]
    added = _scim(
        client,
        "PATCH",
        f"/scim/v2/Groups/{group_id}",
        token,
        {
            "schemas": [PATCH],
            "Operations": [
                {"op": "Add", "path": "members", "value": [{"value": user_id}]}
            ],
        },
    )
    assert added.status_code == 200, added.text
    assert added.json()["members"][0]["value"] == user_id
    hidden = client.get(
        f"/scim/v2/Groups/{group_id}",
        params={"excludedAttributes": "members"},
        headers=_auth(token),
    )
    assert "members" not in hidden.json()
    found = client.get(
        "/scim/v2/Groups",
        params={"filter": 'displayName eq "engineers"'},
        headers=_auth(token),
    )
    assert found.json()["totalResults"] == 1
    removed = _scim(
        client,
        "PATCH",
        f"/scim/v2/Groups/{group_id}",
        token,
        {
            "schemas": [PATCH],
            "Operations": [{"op": "Remove", "path": f'members[value eq "{user_id}"]'}],
        },
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["members"] == []
    missing = _scim(
        client,
        "PATCH",
        f"/scim/v2/Groups/{group_id}",
        token,
        {
            "schemas": [PATCH],
            "Operations": [{"op": "Add", "path": "members", "value": [{"value": "no-such"}]}],
        },
    )
    assert missing.status_code == 400
    assert missing.json()["scimType"] == "invalidValue"
    put = _scim(
        client,
        "PUT",
        f"/scim/v2/Groups/{group_id}",
        token,
        {"displayName": "Engineers", "members": [{"value": user_id}]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["members"][0]["value"] == user_id


def test_mapping_assigns_role_budget_and_ignores_a_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    _map(client)
    _map(
        client,
        kind="department",
        source="Finance",
        directory_role="steward",
        priority=10,
        spaces=[WAREHOUSE],
    )
    token = _issue(client)
    exact = _scim(client, "POST", "/scim/v2/Users", token, _sample("exact@example.com")).json()
    prefix = _scim(
        client, "POST", "/scim/v2/Users", token, _sample("prefix@example.com", "Finance Ops")
    ).json()
    other = _scim(
        client, "POST", "/scim/v2/Users", token, _sample("other@example.com", "Other")
    ).json()
    listed = {row["user_name"]: row for row in client.get("/v1/scim/admin/users").json()["users"]}
    assert listed[exact["userName"]]["role"] == "steward"
    assert listed[exact["userName"]]["spaces"] == [WAREHOUSE]
    assert listed[prefix["userName"]]["role"] == "viewer"
    assert listed[prefix["userName"]]["spaces"] is None
    assert listed[other["userName"]]["role"] == "viewer"
    tenant = get_settings().dms_tenant_id
    winner = get_store().user_by_name(tenant, "exact@example.com")
    assert winner is not None
    assignment = gov_store().assignment(tenant, winner.id)
    assert assignment is not None
    assert assignment.daily_tokens == 1000
    assert assignment.role == "member"
    assert assignment.updated_by == "scim"
    other = get_store().user_by_name(tenant, "other@example.com")
    assert other is not None
    assert gov_store().assignment(tenant, other.id) is None


def test_group_mapping_beats_a_higher_priority_number(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    _map(client, priority=50, directory_role="analyst")
    _map(
        client,
        kind="group",
        source="Engineers",
        directory_role="steward",
        priority=5,
        spaces=[FINANCE, WAREHOUSE],
    )
    token = _issue(client)
    created = _scim(client, "POST", "/scim/v2/Users", token, _sample("both@example.com"))
    user_id = created.json()["id"]
    group = _scim(
        client,
        "POST",
        "/scim/v2/Groups",
        token,
        {"displayName": "Engineers", "members": [{"value": user_id}]},
    )
    assert group.status_code == 201, group.text
    listed = client.get("/v1/scim/admin/users").json()["users"]
    row = next(item for item in listed if item["user_name"] == "both@example.com")
    assert row["role"] == "steward"
    assert row["spaces"] == [FINANCE, WAREHOUSE]


def test_deactivate_revokes_the_session_and_the_next_login(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = get_settings().dms_tenant_id
    provision_tenant(tenant, "company")
    provision_user(email="admin@example.com", password=PASSWORD, role="admin", tenant_id=tenant)
    provision_user(email="hire@example.com", password=PASSWORD, role="viewer", tenant_id=tenant)
    client = _client(monkeypatch, sso="1")
    admin = client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": PASSWORD},
    )
    assert admin.status_code == 200, admin.text
    admin_headers = _auth(admin.json()["token"])
    saved = client.put(
        "/v1/scim/admin/maps",
        headers=admin_headers,
        json={
            "kind": "department",
            "source": "Finance",
            "directory_role": "analyst",
            "gov_role": "member",
            "priority": 10,
            "spaces": [FINANCE],
            "daily_tokens": 1000,
        },
    )
    assert saved.status_code == 200, saved.text
    token = _issue(client, admin_headers)
    hire = client.post(
        "/v1/auth/login",
        json={"email": "hire@example.com", "password": PASSWORD},
    )
    assert hire.status_code == 200, hire.text
    hire_headers = _auth(hire.json()["token"])
    created = _scim(client, "POST", "/scim/v2/Users", token, _sample("hire@example.com"))
    assert created.status_code == 201, created.text
    again = client.post(
        "/v1/auth/login",
        json={"email": "hire@example.com", "password": PASSWORD},
    )
    assert again.status_code == 200, again.text
    assert again.json()["role"] == "analyst"
    spaces = client.get("/v1/spaces", headers=_auth(again.json()["token"]))
    assert [row["id"] for row in spaces.json()["spaces"]] == [FINANCE]
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", "ready02-service-token")
    service = client.get("/v1/spaces", headers=_auth("ready02-service-token"))
    assert service.status_code == 200, service.text
    assert {row["id"] for row in service.json()["spaces"]} == {FINANCE, WAREHOUSE}
    blocked = client.get("/v1/spaces", headers=_auth(token))
    assert blocked.status_code == 401
    assert blocked.json()["detail"]["code"] == "auth_required"
    assert token not in blocked.text
    user_id = created.json()["id"]
    patched = _scim(
        client,
        "PATCH",
        f"/scim/v2/Users/{user_id}",
        token,
        {"schemas": [PATCH], "Operations": [{"op": "Replace", "path": "active", "value": False}]},
    )
    assert patched.status_code == 200
    assert client.get("/v1/auth/me", headers=hire_headers).status_code == 401
    assert client.get("/v1/auth/me", headers=_auth(again.json()["token"])).status_code == 401
    refused = client.post(
        "/v1/auth/login",
        json={"email": "hire@example.com", "password": PASSWORD},
    )
    assert refused.status_code == 401
    assert refused.json()["detail"]["code"] == "auth_required"


def test_viewer_cannot_rotate_and_status_hides_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = get_settings().dms_tenant_id
    provision_tenant(tenant, "company")
    provision_user(email="view@example.com", password=PASSWORD, role="viewer", tenant_id=tenant)
    provision_user(email="boss@example.com", password=PASSWORD, role="admin", tenant_id=tenant)
    client = _client(monkeypatch, sso="1")
    viewer = client.post(
        "/v1/auth/login", json={"email": "view@example.com", "password": PASSWORD}
    )
    assert viewer.status_code == 200, viewer.text
    denied = client.post("/v1/scim/admin/token", headers=_auth(viewer.json()["token"]))
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "role_forbidden"
    boss = client.post(
        "/v1/auth/login", json={"email": "boss@example.com", "password": PASSWORD}
    )
    token = _issue(client, _auth(boss.json()["token"]))
    status = client.get("/v1/scim/admin/token", headers=_auth(boss.json()["token"]))
    assert status.status_code == 200
    assert status.json()["issued"] is True
    assert token not in status.text
    events = client.get("/v1/scim/admin/audit", headers=_auth(boss.json()["token"]))
    assert events.status_code == 200
    assert token not in events.text
    blob = json.dumps(
        {key: row.__dict__ for key, row in get_store()._tokens.items()},
        default=str,
    )
    assert token not in blob
    assert get_store()._tokens[tenant].sha256 != token


def test_rotate_invalidates_the_old_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    first = _issue(client)
    second = _issue(client)
    assert first != second
    old = client.get("/scim/v2/ServiceProviderConfig", headers=_auth(first))
    assert old.status_code == 401
    assert first not in old.text
    new = client.get("/scim/v2/ServiceProviderConfig", headers=_auth(second))
    assert new.status_code == 200


def test_rate_limit_answers_429(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    set_rate_limit_for_tests(2)
    first = client.get("/scim/v2/ServiceProviderConfig", headers=_auth(token))
    second = client.get("/scim/v2/ServiceProviderConfig", headers=_auth(token))
    third = client.get("/scim/v2/ServiceProviderConfig", headers=_auth(token))
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.headers["retry-after"] == "60"
    assert token not in third.text
    assert token not in str(get_store()._hits)


def test_create_fails_closed_when_the_gate_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    import cortex_client
    import dms_api.routes.scim as scim_routes

    monkeypatch.setattr(scim_routes, "compliance_gate", cortex_client.compliance_gate)
    res = _scim(client, "POST", "/scim/v2/Users", token, _sample("gated@example.com"))
    assert res.status_code == 403
    assert res.json()["detail"] == "gate_unavailable"
    assert get_store().users(get_settings().dms_tenant_id) == []


def test_reads_still_answer_when_the_gate_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    import cortex_client
    import dms_api.routes.scim as scim_routes

    monkeypatch.setattr(scim_routes, "compliance_gate", cortex_client.compliance_gate)
    res = client.get("/scim/v2/ServiceProviderConfig", headers=_auth(token))
    assert res.status_code == 200, res.text
    denied = client.post("/v1/scim/admin/token")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "gate_unavailable"
    assert token not in denied.text


def test_openvault_put_sends_the_secret_and_does_not_log_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    captured: dict[str, object] = {}

    class Response:
        status_code = 201

        def json(self) -> dict[str, str]:
            return {"id": "kid1"}

    class Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def request(
            self,
            method: str,
            url: str,
            json: dict[str, str] | None = None,
            headers: dict[str, str] | None = None,
        ) -> Response:
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return Response()

    monkeypatch.setattr("dms_api.scim_vault.httpx.Client", Client)
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", "service-token-not-the-scim-bearer")
    secret = "scim-bearer-plaintext"
    with caplog.at_level(logging.DEBUG):
        ref = OpenVaultScim("http://127.0.0.1:9").put(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", secret
        )
    assert ref == "dms/kid1"
    assert captured["json"]["secret"] == secret
    assert str(captured["url"]).endswith("/api/keys")
    assert secret not in caplog.text
    assert captured["headers"]["Authorization"] == "Bearer service-token-not-the-scim-bearer"


def test_audit_redacts_a_bearer_shaped_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    token = _issue(client)
    _scim(client, "POST", "/scim/v2/Users", token, _sample())
    events = client.get("/v1/scim/admin/audit").json()["events"]
    assert any(row["action"] == "user.create" for row in events)
    assert token not in json.dumps(events)
    tenant = get_settings().dms_tenant_id
    get_store().audit(tenant, "user.create", "id", "scim", f"Bearer {token} sk-live")
    details = [row.detail for row in get_store().audits(tenant)]
    assert "redacted" in details
    assert token not in "".join(details)
