"""Action connectors for a Create app.

Every apply needs a person and an approval ref. A dry run never applies.
Writeback calls the existing action core (``propose_action`` then
``apply_action_version``) when ``DMS_ACTIONS`` is on and a connection is
bound. Salesforce create and update use a secret from the secrets port,
which a deployment binds to OpenVault. Env vars named for Salesforce are
ignored. Connector ``ontology`` is the Palantir-style slot: DMS has no
ontology-action API to call, so both dry run and apply report that.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from dms_core.create_app.flags import salesforce_enabled

_SOBJECT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
_RECORD = re.compile(r"^[A-Za-z0-9]{1,40}$")
_SECRET_KEYS = frozenset(
    {"access_token", "token", "secret", "password", "authorization", "api_key"}
)
HttpFn = Callable[[str, str, dict[str, Any], dict[str, str]], tuple[int, bytes]]


def scrub(value: Any) -> Any:
    """Drop secret-shaped keys before an audit row is stored."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                out[str(key)] = "redacted"
            else:
                out[str(key)] = scrub(item)
        return out
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def _audit(
    sink: list[dict[str, Any]],
    *,
    actor: str,
    action_id: str,
    connector: str,
    status: str,
    code: str,
    confirmed: bool,
    applied: bool,
) -> dict[str, Any]:
    row = {
        "actor": actor,
        "action_id": action_id,
        "connector": connector,
        "status": status,
        "code": code,
        "confirmed": confirmed,
        "applied": applied,
    }
    sink.append(row)
    return row


def _declared(actions: list[dict[str, Any]], action_id: str) -> dict[str, Any] | None:
    for action in actions:
        if action.get("id") == action_id:
            return action
    return None


def _tables(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("tables")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def ontology_result() -> dict[str, Any]:
    """The Palantir slot. No API call is made."""
    return {
        "ok": False,
        "status": "unwired",
        "code": "palantir_ontology_unwired",
        "applied": False,
        "detail": (
            "No Palantir ontology action API is wired. "
            "The ActionConnector slot is ready for a later slice."
        ),
    }


def _salesforce_plan(payload: dict[str, Any]) -> dict[str, Any] | str:
    op = payload.get("op")
    sobject = payload.get("sobject")
    fields = payload.get("fields")
    if op not in ("create", "update"):
        return "salesforce_op_rejected"
    if not isinstance(sobject, str) or _SOBJECT.fullmatch(sobject) is None:
        return "salesforce_sobject_rejected"
    if not isinstance(fields, dict) or not fields:
        return "salesforce_fields_missing"
    record_id = payload.get("record_id")
    if op == "update" and (not isinstance(record_id, str) or _RECORD.fullmatch(record_id) is None):
        return "salesforce_record_missing"
    return {
        "op": op,
        "sobject": sobject,
        "fields": scrub(fields),
        "record_id": record_id if op == "update" else None,
    }


def _load_secret(secret_get: Callable[[str], str | None] | None, ref: str) -> dict[str, Any] | None:
    if secret_get is None:
        return None
    raw = secret_get(ref)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    url = parsed.get("instance_url")
    token = parsed.get("access_token")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    if not isinstance(token, str) or len(token) < 8:
        return None
    return {"instance_url": url.rstrip("/"), "access_token": token}


def salesforce_dry_run(payload: dict[str, Any], *, grants: set[str]) -> dict[str, Any]:
    missing = [name for name in _tables(payload) if name not in grants]
    if missing:
        return {"ok": False, "status": "refused", "code": "ungranted", "applied": False}
    if not salesforce_enabled():
        return {
            "ok": False,
            "status": "refused",
            "code": "salesforce_disabled",
            "applied": False,
        }
    plan = _salesforce_plan(payload)
    if isinstance(plan, str):
        return {"ok": False, "status": "refused", "code": plan, "applied": False}
    return {
        "ok": True,
        "status": "dry_run",
        "code": "dry_run",
        "applied": False,
        "request": {**plan, "authorization": "redacted"},
    }


def salesforce_apply(
    payload: dict[str, Any],
    *,
    grants: set[str],
    secret_get: Callable[[str], str | None] | None,
    http_post: HttpFn | None = None,
) -> dict[str, Any]:
    planned = salesforce_dry_run(payload, grants=grants)
    if not planned["ok"]:
        return planned
    ref = payload.get("cred_ref")
    cred_ref = ref if isinstance(ref, str) and ref.strip() else "salesforce"
    secret = _load_secret(secret_get, cred_ref)
    if secret is None:
        return {
            "ok": False,
            "status": "refused",
            "code": "salesforce_credential_missing",
            "applied": False,
        }
    plan = planned["request"]
    op = str(plan["op"])
    url = secret["instance_url"] + "/services/data/v59.0/sobjects/" + str(plan["sobject"])
    method = "POST"
    if op == "update":
        url = url + "/" + str(plan["record_id"])
        method = "PATCH"
    headers = {
        "Authorization": "Bearer " + secret["access_token"],
        "Content-Type": "application/json",
    }
    send = http_post or _default_http
    try:
        status, _body = send(method, url, dict(plan["fields"]), headers)
    except Exception:
        return {
            "ok": False,
            "status": "refused",
            "code": "salesforce_http_failed",
            "applied": False,
        }
    if status < 200 or status >= 300:
        return {
            "ok": False,
            "status": "refused",
            "code": "salesforce_http_failed",
            "applied": False,
        }
    return {"ok": True, "status": "applied", "code": "applied", "applied": True}


def _default_http(
    method: str, url: str, body: dict[str, Any], headers: dict[str, str]
) -> tuple[int, bytes]:
    import urllib.request

    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return int(res.status), res.read()


def _writeback_spec(payload: dict[str, Any]) -> dict[str, Any] | None:
    nested = payload.get("spec")
    if isinstance(nested, dict):
        return nested
    if "action_type" in payload:
        return payload
    return None


def writeback_check(payload: dict[str, Any], *, grants: set[str]) -> dict[str, Any]:
    from dms_core.control_plane.action_types import ACTION_TYPES

    spec = _writeback_spec(payload)
    if spec is None:
        return {"ok": False, "status": "refused", "code": "action_type_unknown", "applied": False}
    name = spec.get("action_type")
    found = ACTION_TYPES.get(name) if isinstance(name, str) else None
    if found is None:
        return {"ok": False, "status": "refused", "code": "action_type_unknown", "applied": False}
    if found.source_table not in grants:
        return {"ok": False, "status": "refused", "code": "ungranted", "applied": False}
    extra = [table for table in _tables(payload) if table not in grants]
    if extra:
        return {"ok": False, "status": "refused", "code": "ungranted", "applied": False}
    return {
        "ok": True,
        "status": "dry_run",
        "code": "dry_run",
        "applied": False,
        "spec": spec,
        "source_table": found.source_table,
    }


def writeback_apply(
    payload: dict[str, Any],
    *,
    grants: set[str],
    actor: str,
    approval_ref: str,
    conn: Any,
    tenant_id: str,
    space_id: str | None,
    audit_id: str,
) -> dict[str, Any]:
    checked = writeback_check(payload, grants=grants)
    if not checked["ok"]:
        return checked
    from dms_core.control_plane.action_types import actions_enabled

    if not actions_enabled():
        return {"ok": False, "status": "refused", "code": "actions_disabled", "applied": False}
    if conn is None:
        return {"ok": False, "status": "refused", "code": "writeback_unbound", "applied": False}
    from dms_core.control_plane import actions as actions_mod

    tenant = _uuid(tenant_id)
    if tenant is None:
        return {"ok": False, "status": "refused", "code": "tenant_required", "applied": False}
    space = _uuid(space_id) if space_id else None
    source = payload.get("source_value")
    try:
        proposed = actions_mod.propose_action(
            conn,
            tenant_id=tenant,
            space_id=space,
            actor_id=actor,
            spec=checked["spec"],
            source_value=source,
            evidence={"audit_id": audit_id},
        )
        actions_mod.apply_action_version(
            conn,
            tenant_id=tenant,
            idempotency_token=str(proposed["idempotency_token"]),
            actor_id=actor,
            approval_ref=approval_ref,
        )
    except Exception as exc:
        code = getattr(exc, "code", None)
        return {
            "ok": False,
            "status": "refused",
            "code": code if isinstance(code, str) and code else "writeback_failed",
            "applied": False,
        }
    return {"ok": True, "status": "applied", "code": "applied", "applied": True}


def _uuid(value: str | None) -> UUID | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def dry_run_action(
    *,
    actions: list[dict[str, Any]],
    action_id: str,
    payload: dict[str, Any] | None,
    grants: set[str],
    actor: str,
    audit: list[dict[str, Any]],
) -> dict[str, Any]:
    declared = _declared(actions, action_id)
    if declared is None:
        result = {"ok": False, "status": "refused", "code": "action_unknown", "applied": False}
        _audit(
            audit,
            actor=actor,
            action_id=action_id,
            connector="",
            status="refused",
            code="action_unknown",
            confirmed=False,
            applied=False,
        )
        return result
    connector = str(declared.get("connector") or "")
    body = payload if payload else dict(declared.get("payload") or {})
    if connector == "writeback":
        result = writeback_check(body, grants=grants)
    elif connector == "salesforce":
        result = salesforce_dry_run(body, grants=grants)
    elif connector == "ontology":
        result = ontology_result()
    else:
        result = {
            "ok": False,
            "status": "refused",
            "code": "action_connector_unknown",
            "applied": False,
        }
    _audit(
        audit,
        actor=actor,
        action_id=action_id,
        connector=connector,
        status=str(result.get("status") or "refused"),
        code=str(result.get("code") or "refused"),
        confirmed=False,
        applied=False,
    )
    return result


def confirm_action(
    *,
    actions: list[dict[str, Any]],
    action_id: str,
    payload: dict[str, Any] | None,
    grants: set[str],
    actor: str,
    approval_ref: str | None,
    audit: list[dict[str, Any]],
    conn: Any = None,
    tenant_id: str = "",
    space_id: str | None = None,
    audit_id: str = "",
    secret_get: Callable[[str], str | None] | None = None,
    http_post: HttpFn | None = None,
) -> dict[str, Any]:
    """Apply only when the person sent an approval ref of at least 3 characters."""
    declared = _declared(actions, action_id)
    connector = str(declared.get("connector") or "") if declared else ""
    ref = approval_ref.strip() if isinstance(approval_ref, str) else ""
    if declared is None:
        result = {"ok": False, "status": "refused", "code": "action_unknown", "applied": False}
    elif len(ref) < 3:
        result = {
            "ok": True,
            "status": "needs_confirmation",
            "code": "confirmation_required",
            "applied": False,
        }
    else:
        body = payload if payload else dict(declared.get("payload") or {})
        if connector == "writeback":
            result = writeback_apply(
                body,
                grants=grants,
                actor=actor,
                approval_ref=ref,
                conn=conn,
                tenant_id=tenant_id,
                space_id=space_id,
                audit_id=audit_id or action_id,
            )
        elif connector == "salesforce":
            result = salesforce_apply(
                body, grants=grants, secret_get=secret_get, http_post=http_post
            )
        elif connector == "ontology":
            result = ontology_result()
        else:
            result = {
                "ok": False,
                "status": "refused",
                "code": "action_connector_unknown",
                "applied": False,
            }
    _audit(
        audit,
        actor=actor,
        action_id=action_id,
        connector=connector,
        status=str(result.get("status") or "refused"),
        code=str(result.get("code") or "refused"),
        confirmed=len(ref) >= 3,
        applied=bool(result.get("applied")),
    )
    return result
