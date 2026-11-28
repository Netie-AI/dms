"""SCIM 2.0 wire format (RFC 7643 / RFC 7644) plus the Entra quirks.

Entra sends ``Add`` / ``Replace`` / ``Remove``, ``application/scim+json``,
``emails[type eq "work"].value``, and ``members[value eq "id"]``. Attribute
names and ``op`` are matched case-insensitively. ``userName`` equality is
case-insensitive. Unknown attributes are ignored so an extra Entra field
does not 400 the provisioning cycle. ``password`` is ignored and never stored.

No company names and no role table live here.
"""

from __future__ import annotations

import re
from typing import Any

CORE_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
CORE_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST_RESPONSE = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SP_CONFIG = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
RESOURCE_TYPE = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"
MEDIA = "application/scim+json"

_MAX_PAGE = 200
_DEFAULT_PAGE = 100
_MISSING = object()
_EQ = re.compile(
    r"""^\s*([A-Za-z][\w.]*)\s+eq\s+(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'|(\S+))\s*$""",
    re.IGNORECASE,
)
_EMAIL_WORK = re.compile(
    r"""^emails\s*\[\s*type\s+eq\s+["']work["']\s*\]\.value$""",
    re.IGNORECASE,
)
_MEMBER_EQ = re.compile(
    r"""^members\s*\[\s*value\s+eq\s+["']([^"']+)["']\s*\]\s*$""",
    re.IGNORECASE,
)
_ENT_MARK = "extension:enterprise:2.0:user:"
_USER_FILTERS = frozenset({"username", "externalid", "displayname", "active"})
_GROUP_FILTERS = frozenset({"displayname", "externalid"})
_FIELDS = {
    "username": "user_name",
    "externalid": "external_id",
    "active": "active",
    "displayname": "display_name",
    "name.givenname": "given_name",
    "name.familyname": "family_name",
    "department": "department",
    "manager": "manager",
    "emails": "emails",
}
_IGNORED = frozenset(
    {
        "id",
        "meta",
        "schemas",
        "groups",
        "password",
        "title",
        "nickname",
        "preferredlanguage",
        "locale",
        "timezone",
        "profileurl",
        "usertype",
        "roles",
        "phonenumbers",
        "addresses",
        "ims",
        "photos",
        "entitlements",
        "x509certificates",
        "name.formatted",
        "name.middlename",
        "name.honorificprefix",
        "name.honorificsuffix",
        "employeenumber",
        "costcenter",
        "organization",
        "division",
    }
)


class ScimError(Exception):
    """A SCIM error. ``detail`` is a short label, never a header or a secret."""

    def __init__(self, status: int, detail: str, scim_type: str | None = None) -> None:
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        super().__init__(detail)


def error_body(exc: ScimError) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemas": [ERROR],
        "status": str(exc.status),
        "detail": exc.detail,
    }
    if exc.scim_type:
        body["scimType"] = exc.scim_type
    return body


def _ci(obj: dict[str, Any], name: str) -> Any:
    want = name.casefold()
    for key, value in obj.items():
        if str(key).casefold() == want:
            return value
    return _MISSING


def _leaf(path: str) -> str:
    text = path.strip().strip('"').strip()
    low = text.casefold()
    idx = low.find(_ENT_MARK)
    if idx >= 0:
        return low[idx + len(_ENT_MARK) :]
    return low


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in {"true", "1"}:
            return True
        if text in {"false", "0"}:
            return False
    raise ScimError(400, "active", "invalidValue")


def _check_name(text: str, label: str) -> str:
    clean = text.strip()
    if not clean or len(clean) > 320 or any(ch in clean for ch in "\r\n\x00"):
        raise ScimError(400, label, "invalidValue")
    return clean


def _manager_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()[:320]
    if isinstance(value, dict):
        raw = _ci(value, "value")
        if isinstance(raw, str):
            return raw.strip()[:320]
    return ""


def _emails(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"value": value.strip(), "type": "work", "primary": True}]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append({"value": item.strip(), "type": "work", "primary": not out})
        elif isinstance(item, dict):
            raw = _ci(item, "value")
            if isinstance(raw, str) and raw.strip():
                kind = _ci(item, "type")
                primary = _ci(item, "primary")
                out.append(
                    {
                        "value": raw.strip(),
                        "type": kind if isinstance(kind, str) else "work",
                        "primary": bool(primary) if primary is not _MISSING else False,
                    }
                )
    return out[:20]


def _set_attr(user: Any, path: str, value: Any, *, remove: bool) -> None:
    attr = _leaf(path)
    if _EMAIL_WORK.match(attr):
        if remove:
            user.emails = []
            return
        if isinstance(value, str):
            user.emails = _emails(value)
        return
    if attr in _IGNORED:
        return
    field = _FIELDS.get(attr)
    if field is None:
        return
    if remove:
        if field == "active":
            raise ScimError(400, "active", "invalidValue")
        if field == "emails":
            user.emails = []
        else:
            setattr(user, field, "")
        return
    if field == "active":
        user.active = _as_bool(value)
        return
    if field == "manager":
        user.manager = _manager_value(value)
        return
    if field == "emails":
        user.emails = _emails(value)
        return
    if isinstance(value, dict):
        inner = _ci(value, "value")
        value = inner if isinstance(inner, str) else ""
    if not isinstance(value, str):
        return
    text = value.strip()
    if field == "user_name":
        text = _check_name(text, "userName")
    setattr(user, field, text[:320])


def _apply_object(user: Any, value: dict[str, Any], *, remove: bool) -> None:
    for key, item in value.items():
        low = str(key).casefold()
        if low == ENTERPRISE.casefold() and isinstance(item, dict):
            _apply_object(user, item, remove=remove)
            continue
        if low == "name" and isinstance(item, dict):
            for name_key, name_value in item.items():
                _set_attr(user, f"name.{name_key}", name_value, remove=remove)
            continue
        _set_attr(user, str(key), item, remove=remove)


def load_user(user: Any, body: dict[str, Any]) -> None:
    """Copy a create or replace body onto ``user``. ``password`` is not a field."""
    if not isinstance(body, dict):
        raise ScimError(400, "json", "invalidSyntax")
    _apply_object(user, body, remove=False)


def require_user_name(user: Any) -> None:
    _check_name(str(getattr(user, "user_name", "") or ""), "userName")


def blank_user(user: Any) -> None:
    user.user_name = ""
    user.external_id = ""
    user.display_name = ""
    user.given_name = ""
    user.family_name = ""
    user.emails = []
    user.department = ""
    user.manager = ""
    user.active = True


def operations(body: dict[str, Any]) -> list[dict[str, Any]]:
    """``Operations`` or ``operations``. Each ``op`` is returned as given."""
    raw = _ci(body, "Operations")
    if raw is _MISSING or not isinstance(raw, list):
        raise ScimError(400, "Operations", "invalidSyntax")
    ops: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ScimError(400, "Operations", "invalidSyntax")
        ops.append(item)
    if not ops:
        raise ScimError(400, "Operations", "invalidSyntax")
    return ops


def _op_name(op: dict[str, Any]) -> str:
    raw = _ci(op, "op")
    name = str(raw if raw is not _MISSING else "").strip().casefold()
    if name not in {"add", "replace", "remove"}:
        raise ScimError(400, "op", "invalidSyntax")
    return name


def apply_user_ops(user: Any, ops: list[dict[str, Any]]) -> None:
    for op in ops:
        name = _op_name(op)
        path = _ci(op, "path")
        value = _ci(op, "value")
        if path is _MISSING or (isinstance(path, str) and not path.strip()):
            if name == "remove" or not isinstance(value, dict):
                raise ScimError(400, "path", "noTarget")
            _apply_object(user, value, remove=False)
            continue
        if not isinstance(path, str):
            raise ScimError(400, "path", "invalidPath")
        _set_attr(user, path, None if value is _MISSING else value, remove=name == "remove")


def member_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ScimError(400, "members", "invalidValue")
    found: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            found.append(item.strip())
        elif isinstance(item, dict):
            raw = _ci(item, "value")
            if not isinstance(raw, str) or not raw.strip():
                raise ScimError(400, "members", "invalidValue")
            found.append(raw.strip())
        else:
            raise ScimError(400, "members", "invalidValue")
    return found


def _require_known(ids: list[str], known: Any) -> list[str]:
    for uid in ids:
        if not known(uid):
            raise ScimError(400, "members", "invalidValue")
    return ids


def apply_group_ops(group: Any, ops: list[dict[str, Any]], known: Any) -> None:
    for op in ops:
        name = _op_name(op)
        path = _ci(op, "path")
        value = None if _ci(op, "value") is _MISSING else _ci(op, "value")
        if path is _MISSING or (isinstance(path, str) and not path.strip()):
            if name == "remove" or not isinstance(value, dict):
                raise ScimError(400, "path", "noTarget")
            display = _ci(value, "displayName")
            if isinstance(display, str):
                group.display_name = _check_name(display, "displayName")
            external = _ci(value, "externalId")
            if isinstance(external, str):
                group.external_id = external.strip()[:320]
            members = _ci(value, "members")
            if members is not _MISSING:
                group.members = _require_known(member_ids(members), known)
            continue
        if not isinstance(path, str):
            raise ScimError(400, "path", "invalidPath")
        matched = _MEMBER_EQ.match(path.strip())
        if matched:
            uid = matched.group(1).strip()
            if name == "remove":
                group.members = [item for item in group.members if item != uid]
            else:
                _require_known([uid], known)
                if uid not in group.members:
                    group.members.append(uid)
            continue
        low = _leaf(path)
        if low == "members":
            ids = member_ids(value)
            if name == "remove":
                drop = set(ids)
                group.members = [item for item in group.members if item not in drop]
            elif name == "replace":
                group.members = _require_known(ids, known)
            else:
                for uid in _require_known(ids, known):
                    if uid not in group.members:
                        group.members.append(uid)
            continue
        if low == "displayname" and isinstance(value, str) and name != "remove":
            group.display_name = _check_name(value, "displayName")
            continue
        if low == "externalid" and isinstance(value, str) and name != "remove":
            group.external_id = value.strip()[:320]


def require_display_name(group: Any) -> None:
    _check_name(str(getattr(group, "display_name", "") or ""), "displayName")


def parse_filter(raw: str | None, *, kind: str) -> tuple[str, str] | None:
    if raw is None or not str(raw).strip():
        return None
    matched = _EQ.match(str(raw).strip())
    if matched is None:
        raise ScimError(400, "filter", "invalidFilter")
    attr = matched.group(1).casefold()
    quoted = matched.group(2)
    if quoted is None:
        quoted = matched.group(3)
    if quoted is None:
        quoted = matched.group(4)
    allowed = _USER_FILTERS if kind == "user" else _GROUP_FILTERS
    if attr not in allowed:
        raise ScimError(400, "filter", "invalidFilter")
    return attr, quoted.replace('\\"', '"').replace("\\'", "'")


def matches(kind: str, row: Any, filt: tuple[str, str] | None) -> bool:
    if filt is None:
        return True
    attr, expected = filt
    if attr == "active":
        return bool(row.active) is _as_bool(expected)
    if kind == "group":
        actual = {"displayname": row.display_name, "externalid": row.external_id}[attr]
    else:
        actual = {
            "username": row.user_name,
            "externalid": row.external_id,
            "displayname": row.display_name,
        }[attr]
    return str(actual).casefold() == expected.casefold()


def parse_page(start_index: str | None, count: str | None) -> tuple[int, int]:
    start = 1
    if start_index not in (None, ""):
        try:
            start = int(str(start_index))
        except ValueError as exc:
            raise ScimError(400, "startIndex", "invalidValue") from exc
        if start < 1:
            start = 1
    size = _DEFAULT_PAGE
    if count not in (None, ""):
        try:
            size = int(str(count))
        except ValueError as exc:
            raise ScimError(400, "count", "invalidValue") from exc
        if size < 0:
            raise ScimError(400, "count", "invalidValue")
        size = min(size, _MAX_PAGE)
    return start, size


def list_response(resources: list[dict[str, Any]], start: int, count: int) -> dict[str, Any]:
    begin = start - 1
    chunk = resources[begin : begin + count] if count else []
    return {
        "schemas": [LIST_RESPONSE],
        "totalResults": len(resources),
        "startIndex": start,
        "itemsPerPage": len(chunk),
        "Resources": chunk,
    }


def excluded_names(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip().casefold() for part in str(raw).split(",") if part.strip()}


def user_resource(user: Any, location: str) -> dict[str, Any]:
    enterprise: dict[str, Any] = {"department": user.department}
    if user.manager:
        enterprise["manager"] = {"value": user.manager}
    body: dict[str, Any] = {
        "schemas": [CORE_USER, ENTERPRISE],
        "id": user.id,
        "userName": user.user_name,
        "active": bool(user.active),
        "displayName": user.display_name,
        "name": {"givenName": user.given_name, "familyName": user.family_name},
        "emails": list(user.emails),
        ENTERPRISE: enterprise,
        "meta": {
            "resourceType": "User",
            "created": user.created,
            "lastModified": user.updated,
            "location": location,
        },
    }
    if user.external_id:
        body["externalId"] = user.external_id
    return body


def group_resource(
    group: Any, location: str, *, include_members: bool, user_location: str
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemas": [CORE_GROUP],
        "id": group.id,
        "displayName": group.display_name,
        "meta": {
            "resourceType": "Group",
            "created": group.created,
            "lastModified": group.updated,
            "location": location,
        },
    }
    if group.external_id:
        body["externalId"] = group.external_id
    if include_members:
        body["members"] = [
            {"value": uid, "$ref": f"{user_location}/{uid}"} for uid in group.members
        ]
    return body


def _attr(name: str, *, required: bool = False, multi: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "type": "string",
        "multiValued": multi,
        "description": name,
        "required": required,
        "caseExact": False,
        "mutability": "readWrite",
        "returned": "default",
        "uniqueness": "server" if name == "userName" else "none",
    }


def service_provider_config() -> dict[str, Any]:
    return {
        "schemas": [SP_CONFIG],
        "documentationUri": "https://datatracker.ietf.org/doc/html/rfc7644",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": _MAX_PAGE},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "Authentication scheme using the OAuth Bearer Token Standard",
                "specUri": "https://www.rfc-editor.org/info/rfc6750",
                "primary": True,
            }
        ],
        "meta": {
            "resourceType": "ServiceProviderConfig",
            "location": "/scim/v2/ServiceProviderConfig",
        },
    }


def resource_types() -> dict[str, Any]:
    rows: list[dict[str, Any]] = [
        {
            "schemas": [RESOURCE_TYPE],
            "id": "User",
            "name": "User",
            "endpoint": "/Users",
            "schema": CORE_USER,
            "schemaExtensions": [{"schema": ENTERPRISE, "required": False}],
            "meta": {"resourceType": "ResourceType", "location": "/scim/v2/ResourceTypes/User"},
        },
        {
            "schemas": [RESOURCE_TYPE],
            "id": "Group",
            "name": "Group",
            "endpoint": "/Groups",
            "schema": CORE_GROUP,
            "meta": {"resourceType": "ResourceType", "location": "/scim/v2/ResourceTypes/Group"},
        },
    ]
    return list_response(rows, 1, _MAX_PAGE)


def schemas() -> dict[str, Any]:
    rows = [
        {
            "id": CORE_USER,
            "name": "User",
            "description": "User Account",
            "attributes": [
                _attr("userName", required=True),
                _attr("displayName"),
                _attr("externalId"),
                _attr("active"),
                _attr("emails", multi=True),
            ],
            "meta": {"resourceType": "Schema", "location": f"/scim/v2/Schemas/{CORE_USER}"},
        },
        {
            "id": ENTERPRISE,
            "name": "EnterpriseUser",
            "description": "Enterprise User",
            "attributes": [_attr("department"), _attr("manager")],
            "meta": {"resourceType": "Schema", "location": f"/scim/v2/Schemas/{ENTERPRISE}"},
        },
        {
            "id": CORE_GROUP,
            "name": "Group",
            "description": "Group",
            "attributes": [_attr("displayName", required=True), _attr("members", multi=True)],
            "meta": {"resourceType": "Schema", "location": f"/scim/v2/Schemas/{CORE_GROUP}"},
        },
    ]
    wrapped = list_response(rows, 1, _MAX_PAGE)
    for row in rows:
        row["schemas"] = [SCHEMA]
    return wrapped
