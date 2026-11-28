"""SCIM 2.0 protocol and the manager directory-sync API.

Mounted only when ``DMS_SCIM`` is the digit 1. The protocol bearer is checked
here and is not a DMS session. Writes call ``compliance_gate`` and fail closed.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any
from uuid import uuid4

from cortex_client import compliance_gate
from dms_core.scim.protocol import (
    MEDIA,
    ScimError,
    apply_group_ops,
    apply_user_ops,
    error_body,
    excluded_names,
    group_resource,
    list_response,
    load_user,
    matches,
    member_ids,
    operations,
    parse_filter,
    parse_page,
    require_display_name,
    require_user_name,
    resource_types,
    schemas,
    service_provider_config,
    user_resource,
)
from dms_core.scim.protocol import blank_user as blank_profile
from dms_core.scim.store import GOV_ROLES, ROLES, Group, MapRule, User
from dms_core.scim.store import get_store as scim_store
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.scim_apply import apply_directory
from dms_api.scim_vault import VaultError, get_vault
from dms_api.settings import Settings

router = APIRouter(prefix="/scim/v2", tags=["scim"])
admin = APIRouter(prefix="/v1/scim/admin", tags=["scim-admin"])


class MapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    kind: str
    source: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=100, ge=0, le=10000)
    role: str
    gov_role: str = "member"
    daily_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    monthly_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    daily_tokens: int | None = Field(default=None, ge=0, le=100_000_000_000)
    monthly_tokens: int | None = Field(default=None, ge=0, le=100_000_000_000)
    ask: bool = True
    create: bool = True
    connectors: bool = True
    spaces: list[str] = Field(default_factory=list)


def _fail(exc: ScimError) -> JSONResponse:
    headers = {"Retry-After": "60"} if exc.status == 429 else None
    return JSONResponse(
        status_code=exc.status,
        content=error_body(exc),
        headers=headers,
        media_type=MEDIA,
    )


def _ok(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, headers=headers, media_type=MEDIA)


def _presented(request: Request) -> str:
    raw = request.headers.get("authorization") or ""
    scheme, _, token = raw.partition(" ")
    if scheme.casefold() != "bearer":
        return ""
    return token.strip()


def _auth(request: Request) -> str:
    presented = _presented(request)
    key = hashlib.sha256(presented.encode("utf-8")).hexdigest() if presented else "anon"
    if not scim_store().allow(key):
        raise ScimError(429, "too_many_requests")
    if not presented:
        raise ScimError(401, "unauthorized")
    tenant = scim_store().tenant_for_token(presented)
    if tenant is None:
        raise ScimError(401, "unauthorized")
    return tenant


async def _payload(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        raise ScimError(400, "json", "invalidSyntax")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ScimError(400, "json", "invalidSyntax") from exc
    if not isinstance(data, dict):
        raise ScimError(400, "json", "invalidSyntax")
    return data


def _loc(request: Request, kind: str, resource_id: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/scim/v2/{kind}/{resource_id}"


def _copy_profile(src: User, dest: User) -> None:
    dest.external_id = src.external_id
    dest.active = src.active
    dest.display_name = src.display_name
    dest.given_name = src.given_name
    dest.family_name = src.family_name
    dest.emails = list(src.emails)
    dest.department = src.department
    dest.manager = src.manager


def _sync(user: User, settings: Settings) -> str:
    names = scim_store().group_names(user.tenant_id, user.id)
    rule = scim_store().match(user.tenant_id, department=user.department, groups=names)
    scim_store().assign(user, rule)
    return apply_directory(settings, user, rule)


def _detail(user: User, linked: str) -> str:
    spaces = "" if user.space_grant is None else ",".join(user.space_grant)
    active = "true" if user.active else "false"
    return f"role={user.role};active={active};spaces={spaces};{linked}"


def _sync_members(settings: Settings, tenant: str, user_ids: list[str]) -> None:
    for uid in user_ids:
        user = scim_store().user(tenant, uid)
        if user is not None:
            _sync(user, settings)


def _read_through(cortex: Any, action: str) -> None:
    decision = compliance_gate(
        action=action,
        metadata={"task_id": action},
        client=cortex,
    )
    enforce(decision, mutation=False)


def _field(body: dict[str, Any], name: str) -> Any:
    want = name.casefold()
    for key, value in body.items():
        if str(key).casefold() == want:
            return value
    return None


def _snap_user(user: User) -> dict[str, Any]:
    return {
        "user_name": user.user_name,
        "external_id": user.external_id,
        "active": user.active,
        "display_name": user.display_name,
        "given_name": user.given_name,
        "family_name": user.family_name,
        "emails": list(user.emails),
        "department": user.department,
        "manager": user.manager,
    }


def _restore_user(user: User, snap: dict[str, Any]) -> None:
    for key, value in snap.items():
        setattr(user, key, list(value) if key == "emails" else value)


@router.get("/ServiceProviderConfig")
def scim_service_provider(request: Request, cortex: CortexDep) -> JSONResponse:
    try:
        _auth(request)
    except ScimError as exc:
        return _fail(exc)
    _read_through(cortex, "scim.config.read")
    return _ok(200, service_provider_config())


@router.get("/ResourceTypes")
def scim_resource_types(request: Request, cortex: CortexDep) -> JSONResponse:
    try:
        _auth(request)
    except ScimError as exc:
        return _fail(exc)
    _read_through(cortex, "scim.types.read")
    return _ok(200, resource_types())


@router.get("/ResourceTypes/{type_id}")
def scim_resource_type(type_id: str, request: Request, cortex: CortexDep) -> JSONResponse:
    try:
        _auth(request)
    except ScimError as exc:
        return _fail(exc)
    _read_through(cortex, "scim.types.read")
    for row in resource_types()["Resources"]:
        if str(row.get("id", "")).casefold() == type_id.casefold():
            return _ok(200, row)
    return _fail(ScimError(404, "not_found"))


@router.get("/Schemas")
def scim_schemas(request: Request, cortex: CortexDep) -> JSONResponse:
    try:
        _auth(request)
    except ScimError as exc:
        return _fail(exc)
    _read_through(cortex, "scim.schemas.read")
    return _ok(200, schemas())


@router.get("/Schemas/{schema_id:path}")
def scim_schema(schema_id: str, request: Request, cortex: CortexDep) -> JSONResponse:
    try:
        _auth(request)
    except ScimError as exc:
        return _fail(exc)
    _read_through(cortex, "scim.schemas.read")
    for row in schemas()["Resources"]:
        if str(row.get("id", "")).casefold() == schema_id.casefold():
            return _ok(200, row)
    return _fail(ScimError(404, "not_found"))


@router.get("/Users")
def scim_list_users(
    request: Request,
    cortex: CortexDep,
    filter_expr: str | None = Query(default=None, alias="filter"),
    start_index: str | None = Query(default=None, alias="startIndex"),
    count: str | None = Query(default=None),
) -> JSONResponse:
    try:
        tenant = _auth(request)
        filt = parse_filter(filter_expr, kind="user")
        start, size = parse_page(start_index, count)
    except ScimError as exc:
        return _fail(exc)
    _read_through(cortex, "scim.user.list")
    rows = [
        user_resource(user, _loc(request, "Users", user.id))
        for user in scim_store().users(tenant)
        if matches("user", user, filt)
    ]
    return _ok(200, list_response(rows, start, size))


@router.get("/Users/{user_id}")
def scim_get_user(user_id: str, request: Request, cortex: CortexDep) -> JSONResponse:
    try:
        tenant = _auth(request)
    except ScimError as exc:
        return _fail(exc)
    _read_through(cortex, "scim.user.read")
    user = scim_store().user(tenant, user_id)
    if user is None:
        return _fail(ScimError(404, "not_found"))
    return _ok(200, user_resource(user, _loc(request, "Users", user.id)))


@router.post("/Users")
async def scim_create_user(
    request: Request, cortex: CortexDep, settings: SettingsDep
) -> JSONResponse:
    try:
        tenant = _auth(request)
        body = await _payload(request)
    except ScimError as exc:
        return _fail(exc)
    decision = compliance_gate(
        action="scim.user.create",
        metadata={"task_id": "scim.user.create"},
        client=cortex,
    )
    enforce(decision)
    try:
        draft = User(id="draft", tenant_id=tenant, user_name="")
        load_user(draft, body)
        require_user_name(draft)
        if scim_store().user_by_name(tenant, draft.user_name) is not None:
            raise ScimError(409, "userName", "uniqueness")
        user = scim_store().create_user(tenant, draft.user_name)
        _copy_profile(draft, user)
        linked = _sync(user, settings)
        scim_store().audit(
            tenant, "user.create", user.id, "scim", _detail(user, linked)
        )
        location = _loc(request, "Users", user.id)
        return _ok(201, user_resource(user, location), {"Location": location})
    except ScimError as exc:
        return _fail(exc)


@router.put("/Users/{user_id}")
async def scim_put_user(
    user_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
) -> JSONResponse:
    try:
        tenant = _auth(request)
        body = await _payload(request)
    except ScimError as exc:
        return _fail(exc)
    decision = compliance_gate(
        action="scim.user.replace",
        metadata={"task_id": "scim.user.replace"},
        client=cortex,
    )
    enforce(decision)
    try:
        user = scim_store().user(tenant, user_id)
        if user is None:
            raise ScimError(404, "not_found")
        snap = _snap_user(user)
        try:
            blank_profile(user)
            load_user(user, body)
            require_user_name(user)
            other = scim_store().user_by_name(tenant, user.user_name)
            if other is not None and other.id != user.id:
                raise ScimError(409, "userName", "uniqueness")
        except ScimError:
            _restore_user(user, snap)
            raise
        linked = _sync(user, settings)
        action = "user.deactivate" if not user.active else "user.update"
        scim_store().audit(tenant, action, user.id, "scim", _detail(user, linked))
        return _ok(200, user_resource(user, _loc(request, "Users", user.id)))
    except ScimError as exc:
        return _fail(exc)


@router.patch("/Users/{user_id}")
async def scim_patch_user(
    user_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
) -> JSONResponse:
    try:
        tenant = _auth(request)
        body = await _payload(request)
    except ScimError as exc:
        return _fail(exc)
    decision = compliance_gate(
        action="scim.user.patch",
        metadata={"task_id": "scim.user.patch"},
        client=cortex,
    )
    enforce(decision)
    try:
        user = scim_store().user(tenant, user_id)
        if user is None:
            raise ScimError(404, "not_found")
        snap = _snap_user(user)
        try:
            apply_user_ops(user, operations(body))
            require_user_name(user)
            other = scim_store().user_by_name(tenant, user.user_name)
            if other is not None and other.id != user.id:
                raise ScimError(409, "userName", "uniqueness")
        except ScimError:
            _restore_user(user, snap)
            raise
        linked = _sync(user, settings)
        action = "user.deactivate" if not user.active else "user.update"
        scim_store().audit(tenant, action, user.id, "scim", _detail(user, linked))
        return _ok(200, user_resource(user, _loc(request, "Users", user.id)))
    except ScimError as exc:
        return _fail(exc)


@router.delete("/Users/{user_id}")
def scim_delete_user(
    user_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
) -> Response:
    try:
        tenant = _auth(request)
    except ScimError as exc:
        return _fail(exc)
    decision = compliance_gate(
        action="scim.user.delete",
        metadata={"task_id": "scim.user.delete"},
        client=cortex,
    )
    enforce(decision)
    user = scim_store().user(tenant, user_id)
    if user is None:
        return _fail(ScimError(404, "not_found"))
    user.active = False
    linked = _sync(user, settings)
    scim_store().audit(tenant, "user.deactivate", user.id, "scim", _detail(user, linked))
    return Response(status_code=204)


@router.get("/Groups")
def scim_list_groups(
    request: Request,
    cortex: CortexDep,
    filter_expr: str | None = Query(default=None, alias="filter"),
    start_index: str | None = Query(default=None, alias="startIndex"),
    count: str | None = Query(default=None),
    excluded: str | None = Query(default=None, alias="excludedAttributes"),
) -> JSONResponse:
    try:
        tenant = _auth(request)
        filt = parse_filter(filter_expr, kind="group")
        start, size = parse_page(start_index, count)
    except ScimError as exc:
        return _fail(exc)
    _read_through(cortex, "scim.group.list")
    include = "members" not in excluded_names(excluded)
    users_at = _loc(request, "Users", "").rstrip("/")
    rows = [
        group_resource(
            row,
            _loc(request, "Groups", row.id),
            include_members=include,
            user_location=users_at,
        )
        for row in scim_store().groups(tenant)
        if matches("group", row, filt)
    ]
    return _ok(200, list_response(rows, start, size))


@router.get("/Groups/{group_id}")
def scim_get_group(
    group_id: str,
    request: Request,
    cortex: CortexDep,
    excluded: str | None = Query(default=None, alias="excludedAttributes"),
) -> JSONResponse:
    try:
        tenant = _auth(request)
    except ScimError as exc:
        return _fail(exc)
    _read_through(cortex, "scim.group.read")
    row = scim_store().group(tenant, group_id)
    if row is None:
        return _fail(ScimError(404, "not_found"))
    include = "members" not in excluded_names(excluded)
    return _ok(
        200,
        group_resource(
            row,
            _loc(request, "Groups", row.id),
            include_members=include,
            user_location=_loc(request, "Users", "").rstrip("/"),
        ),
    )


def _known(tenant: str):
    def check(uid: str) -> bool:
        return scim_store().user(tenant, uid) is not None

    return check


@router.post("/Groups")
async def scim_create_group(
    request: Request, cortex: CortexDep, settings: SettingsDep
) -> JSONResponse:
    try:
        tenant = _auth(request)
        body = await _payload(request)
    except ScimError as exc:
        return _fail(exc)
    decision = compliance_gate(
        action="scim.group.create",
        metadata={"task_id": "scim.group.create"},
        client=cortex,
    )
    enforce(decision)
    try:
        draft = Group(id="draft", tenant_id=tenant, display_name="")
        display = _field(body, "displayName")
        if isinstance(display, str):
            draft.display_name = display
        external = _field(body, "externalId")
        if isinstance(external, str):
            draft.external_id = external.strip()[:320]
        require_display_name(draft)
        members = member_ids(_field(body, "members"))
        for uid in members:
            if scim_store().user(tenant, uid) is None:
                raise ScimError(400, "members", "invalidValue")
        row = scim_store().create_group(tenant, draft.display_name)
        row.external_id = draft.external_id
        row.members = members
        scim_store().save_group(row)
        _sync_members(settings, tenant, members)
        scim_store().audit(tenant, "group.create", row.id, "scim", "group")
        location = _loc(request, "Groups", row.id)
        return _ok(
            201,
            group_resource(
                row,
                location,
                include_members=True,
                user_location=_loc(request, "Users", "").rstrip("/"),
            ),
            {"Location": location},
        )
    except ScimError as exc:
        return _fail(exc)


@router.patch("/Groups/{group_id}")
async def scim_patch_group(
    group_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
) -> JSONResponse:
    try:
        tenant = _auth(request)
        body = await _payload(request)
    except ScimError as exc:
        return _fail(exc)
    decision = compliance_gate(
        action="scim.group.patch",
        metadata={"task_id": "scim.group.patch"},
        client=cortex,
    )
    enforce(decision)
    try:
        row = scim_store().group(tenant, group_id)
        if row is None:
            raise ScimError(404, "not_found")
        before = list(row.members)
        previous_name = row.display_name
        previous_external = row.external_id
        try:
            apply_group_ops(row, operations(body), _known(tenant))
            require_display_name(row)
        except ScimError:
            row.members = before
            row.display_name = previous_name
            row.external_id = previous_external
            raise
        scim_store().save_group(row)
        touched = list(dict.fromkeys([*before, *row.members]))
        _sync_members(settings, tenant, touched)
        scim_store().audit(tenant, "group.patch", row.id, "scim", "members")
        return _ok(
            200,
            group_resource(
                row,
                _loc(request, "Groups", row.id),
                include_members=True,
                user_location=_loc(request, "Users", "").rstrip("/"),
            ),
        )
    except ScimError as exc:
        return _fail(exc)


@router.put("/Groups/{group_id}")
async def scim_put_group(
    group_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
) -> JSONResponse:
    try:
        tenant = _auth(request)
        body = await _payload(request)
    except ScimError as exc:
        return _fail(exc)
    decision = compliance_gate(
        action="scim.group.replace",
        metadata={"task_id": "scim.group.replace"},
        client=cortex,
    )
    enforce(decision)
    try:
        row = scim_store().group(tenant, group_id)
        if row is None:
            raise ScimError(404, "not_found")
        display = _field(body, "displayName")
        if not isinstance(display, str):
            raise ScimError(400, "displayName", "invalidValue")
        before = list(row.members)
        row.display_name = display
        require_display_name(row)
        external = _field(body, "externalId")
        row.external_id = external.strip()[:320] if isinstance(external, str) else ""
        members = member_ids(_field(body, "members"))
        for uid in members:
            if scim_store().user(tenant, uid) is None:
                raise ScimError(400, "members", "invalidValue")
        row.members = members
        scim_store().save_group(row)
        _sync_members(settings, tenant, list(dict.fromkeys([*before, *members])))
        scim_store().audit(tenant, "group.update", row.id, "scim", "group")
        return _ok(
            200,
            group_resource(
                row,
                _loc(request, "Groups", row.id),
                include_members=True,
                user_location=_loc(request, "Users", "").rstrip("/"),
            ),
        )
    except ScimError as exc:
        return _fail(exc)


@router.delete("/Groups/{group_id}")
def scim_delete_group(
    group_id: str, request: Request, cortex: CortexDep, settings: SettingsDep
) -> Response:
    try:
        tenant = _auth(request)
    except ScimError as exc:
        return _fail(exc)
    decision = compliance_gate(
        action="scim.group.delete",
        metadata={"task_id": "scim.group.delete"},
        client=cortex,
    )
    enforce(decision)
    row = scim_store().delete_group(tenant, group_id)
    if row is None:
        return _fail(ScimError(404, "not_found"))
    _sync_members(settings, tenant, list(row.members))
    scim_store().audit(tenant, "group.delete", group_id, "scim", "group")
    return Response(status_code=204)


def _manager(settings: Settings) -> tuple[Any, str]:
    from dms_api.auth_sso import bound_tenant_id
    from dms_api.gov_guard import current_subject, is_manager

    if not is_manager(settings):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "gov_manager_required",
                "message": "Directory sync is for managers.",
            },
        )
    return current_subject(settings), bound_tenant_id(settings.dms_tenant_id)


def _map_out(row: MapRule) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "source": row.source,
        "priority": row.priority,
        "role": row.role,
        "gov_role": row.gov_role,
        "daily_usd": row.daily_usd,
        "monthly_usd": row.monthly_usd,
        "daily_tokens": row.daily_tokens,
        "monthly_tokens": row.monthly_tokens,
        "ask": row.allow_ask,
        "create": row.allow_create,
        "connectors": row.allow_connectors,
        "spaces": list(row.spaces),
    }


def _check_map(body: MapIn) -> None:
    if body.kind not in {"department", "group"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "scim_map", "message": "Kind is department or group."},
        )
    if body.role not in ROLES:
        raise HTTPException(
            status_code=422,
            detail={"code": "scim_map", "message": "Role is admin, steward, analyst, or viewer."},
        )
    if body.gov_role not in GOV_ROLES:
        raise HTTPException(
            status_code=422,
            detail={"code": "scim_map", "message": "Governance role is manager or member."},
        )
    source = body.source.strip()
    if not source or any(ch in source for ch in "\r\n\x00"):
        raise HTTPException(
            status_code=422,
            detail={"code": "scim_map", "message": "Source name is empty."},
        )
    if len(body.spaces) > 50:
        raise HTTPException(
            status_code=422,
            detail={"code": "scim_map", "message": "Too many spaces."},
        )
    for space in body.spaces:
        if not isinstance(space, str) or not space.strip() or any(ch in space for ch in "\r\n\x00"):
            raise HTTPException(
                status_code=422,
                detail={"code": "scim_map", "message": "Space id is empty."},
            )


@admin.get("/maps")
def scim_maps(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    _read_through(cortex, "scim.map.read")
    _subject, tenant = _manager(settings)
    return {"maps": [_map_out(row) for row in scim_store().maps(tenant)]}


@admin.put("/maps")
def scim_put_map(body: MapIn, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="scim.map.write",
        metadata={"task_id": "scim.map.write"},
        client=cortex,
    )
    enforce(decision)
    subject, tenant = _manager(settings)
    _check_map(body)
    row = MapRule(
        id=body.id.strip() or str(uuid4()),
        tenant_id=tenant,
        kind=body.kind,
        source=body.source.strip(),
        priority=body.priority,
        role=body.role,
        gov_role=body.gov_role,
        daily_usd=body.daily_usd,
        monthly_usd=body.monthly_usd,
        daily_tokens=body.daily_tokens,
        monthly_tokens=body.monthly_tokens,
        allow_ask=body.ask,
        allow_create=body.create,
        allow_connectors=body.connectors,
        spaces=tuple(space.strip() for space in body.spaces),
    )
    scim_store().save_map(row)
    scim_store().audit(tenant, "map.write", row.id, subject.user_id, f"kind={row.kind}")
    return {"map": _map_out(row)}


@admin.delete("/maps/{map_id}")
def scim_delete_map(map_id: str, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="scim.map.delete",
        metadata={"task_id": "scim.map.delete"},
        client=cortex,
    )
    enforce(decision)
    subject, tenant = _manager(settings)
    if not scim_store().delete_map(tenant, map_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "scim_map", "message": "Mapping was not found."},
        )
    scim_store().audit(tenant, "map.delete", map_id, subject.user_id, "map")
    return {"deleted": map_id}


@admin.get("/audit")
def scim_audit(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    _read_through(cortex, "scim.audit.read")
    _subject, tenant = _manager(settings)
    rows = [
        {
            "id": row.id,
            "at": row.at,
            "action": row.action,
            "resource_id": row.resource_id,
            "actor": row.actor,
            "detail": row.detail,
        }
        for row in scim_store().audits(tenant)
    ]
    return {"events": rows}


@admin.get("/users")
def scim_admin_users(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    _read_through(cortex, "scim.admin.users")
    _subject, tenant = _manager(settings)
    rows = [
        {
            "id": user.id,
            "user_name": user.user_name,
            "active": user.active,
            "department": user.department,
            "role": user.role,
            "gov_role": user.gov_role,
            "spaces": None if user.space_grant is None else list(user.space_grant),
            "sso_user_id": user.sso_user_id,
        }
        for user in scim_store().users(tenant)
    ]
    return {"users": rows}


@admin.post("/token")
def scim_rotate_token(settings: SettingsDep, cortex: CortexDep) -> dict[str, str]:
    decision = compliance_gate(
        action="scim.token.rotate",
        metadata={"task_id": "scim.token.rotate"},
        client=cortex,
    )
    enforce(decision)
    subject, tenant = _manager(settings)
    token = secrets.token_urlsafe(32)
    try:
        ref = get_vault().put(tenant, token)
    except VaultError:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "scim_vault_unavailable",
                "message": "OpenVault did not store the token.",
            },
        ) from None
    scim_store().remember_token(tenant, cred_ref=ref, token=token)
    scim_store().audit(tenant, "token.rotate", tenant, subject.user_id, f"cred_ref={ref}")
    return {"cred_ref": ref, "token": token}


@admin.get("/token")
def scim_token_status(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    _read_through(cortex, "scim.token.read")
    _subject, tenant = _manager(settings)
    status = scim_store().token_status(tenant)
    if status is None:
        return {"issued": False}
    return {"issued": True, "cred_ref": status["cred_ref"], "rotated_at": status["rotated_at"]}
