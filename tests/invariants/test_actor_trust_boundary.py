"""DR-0004 Option A - identity is resolved server-side, never from a request.

The Confirmation section of DR-0004 names two assertions that must exist before the
record may move from ``proposed`` to ``accepted``:

  1. no request field and no request header can determine a ledger actor;
  2. under Option B only, a request with no credential is refused.

This file supplies (1). (2) is deliberately absent - Option A was chosen, and a test
asserting a credential check would be asserting a control that does not exist.

R-0001: these assert on what a client actually sees - the HTTP response and the request
schema - not on an internal call. A caller cannot send a field that pydantic does not
declare, and cannot send a header the middleware refuses.
"""

from __future__ import annotations

import pytest
from dms_api.middleware_actor import IDENTITY_HEADERS
from fastapi.testclient import TestClient


#: Cheap route that exists and does no network probing. ``/health`` would work too but
#: it live-probes Cortex and OpenVault, which is seconds per call and unrelated to what
#: is under test here.
PROBE_PATH = "/openapi.json"


@pytest.fixture(scope="module")
def client() -> TestClient:
    from dms_api.app import create_app

    return TestClient(create_app())


@pytest.mark.parametrize("header", IDENTITY_HEADERS)
def test_an_identity_header_is_refused_not_ignored(client: TestClient, header: str) -> None:
    """A request that names its own identity is turned away, and the refusal says why.

    Ignoring the header would be the silent form (R-0011): the caller believes it acted
    as someone, the server believes otherwise, and nothing on the wire says which.
    """
    resp = client.get(PROBE_PATH, headers={header: "someone-else"})

    assert resp.status_code == 400, (
        f"{header} was accepted or ignored - identity must come from configuration"
    )
    body = resp.json()
    assert body["code"] == "identity_header_not_accepted"
    assert header in body["headers"]
    assert "DR-0004" == body["decision_record"]


def test_the_same_request_without_the_header_is_fine(client: TestClient) -> None:
    """R-0005 - the control must refuse the header, not the traffic."""
    resp = client.get(PROBE_PATH)
    assert resp.status_code == 200


def test_identity_headers_are_refused_case_insensitively(client: TestClient) -> None:
    """HTTP header names are case-insensitive; a control that is not would be a hole."""
    resp = client.get(PROBE_PATH, headers={"X-DMS-Role": "admin"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "identity_header_not_accepted"


def test_no_request_field_can_name_the_ledger_actor() -> None:
    """A-0005 companion to the header check, asserted on the request schema itself.

    ``GoldSignBody`` used to declare ``steward_id``, which became the ledger actor. The
    field is gone; this pins it gone. Together with the header tests above, both halves
    of "identity never arrives on a request" are covered.
    """
    from dms_api.routes.pipelines import GoldSignBody

    declared = set(GoldSignBody.model_fields)
    forbidden = {"steward_id", "actor", "actor_user_id", "tenant_id", "role", "user_id"}
    leaked = declared & forbidden
    assert not leaked, f"GoldSignBody declares identity fields a caller could set: {leaked}"


def test_the_middleware_that_read_headers_is_gone() -> None:
    """The rejected Option C was to wire the old header middleware through.

    DR-0004 records that as rejected so it is not proposed again. If the class comes
    back, this fails and points at the record rather than at a preference.
    """
    import dms_api.middleware_actor as mod

    assert not hasattr(mod, "DevActorMiddleware"), (
        "DevActorMiddleware is back. DR-0004 Option C was rejected: x-dms-role is "
        "caller-supplied and unverified, so honouring it is privilege escalation."
    )

