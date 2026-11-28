"""Register a connected source's Space and grant only its exposed tables.

Off unless ``DMS_CONNECT_API`` is 1/true/yes/on. The book is process memory
unless ``DMS_CONNECT_V2`` is also on, in which case the connection is stored
in Postgres. The body carries an OpenVault credential reference. A raw
secret is rejected and is not logged, stored, audited, or copied into the
error.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from cortex_client import compliance_gate
from dms_core.control_plane.connect_secrets import (
    ConnectCredentialError,
    body_violation,
    key_id_for_ref,
)
from dms_core.control_plane.connect_store import ConnectStoreError, allowed_kinds
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from dms_api.auth_sso import bound_tenant_id
from dms_api.deps import (
    AskServiceDep,
    CortexDep,
    SettingsDep,
    SpaceStoreDep,
    StoreBindingDep,
)
from dms_api.gatekeeping import enforce
from dms_api.settings import Settings
from dms_api.wiring import (
    connect_audit_rows,
    connect_registered_source,
    connect_v2_cancel,
    connect_v2_close,
    connect_v2_export,
    connect_v2_list,
    connect_v2_probe,
    connect_v2_query,
    connect_v2_register,
    connect_v2_schema,
    connect_v2_set_granted,
    grant_connected_tables,
    revoke_connected_tables,
    service_bearer_id,
)

router = APIRouter(prefix="/v1/connect", tags=["connect"])

_MAX_BODY = 16_384
_DISABLED = "connect_api_disabled"
_RAW_SECRET = "connect_raw_secret_rejected"
_V2_OFF = "connect_v2_disabled"
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_MODES = frozenset({"live", "extract"})


def _enabled(settings: Settings) -> None:
    if not settings.dms_connect_api:
        raise HTTPException(status_code=404, detail=_DISABLED)


def _v2(settings: Settings) -> None:
    _enabled(settings)
    if not settings.dms_connect_v2:
        raise HTTPException(status_code=404, detail=_V2_OFF)


def _require_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="bearer_denied")
    token_id = service_bearer_id(authorization)
    if token_id is None:
        raise HTTPException(status_code=401, detail="bearer_denied")
    return token_id


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ConnectCredentialError):
        return HTTPException(status_code=400, detail="connect_credential_unresolved")
    if isinstance(exc, ConnectStoreError):
        code = exc.code if _SAFE_CODE.fullmatch(exc.code) else "bad_request"
        if code in {"connect_store_unavailable", "connect_store_failed"}:
            return HTTPException(status_code=503, detail=code)
        if code in {"source_not_found"}:
            return HTTPException(status_code=404, detail=code)
        if code == "source_mode_extract":
            return HTTPException(status_code=409, detail=code)
        return HTTPException(status_code=400, detail=code)
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="source_not_found")
    code = str(exc)
    if not _SAFE_CODE.fullmatch(code):
        return HTTPException(status_code=400, detail="bad_request")
    if code == "space_name_taken":
        return HTTPException(status_code=409, detail=code)
    if code in {"connector_unknown", "query_not_found", "space_not_found"}:
        return HTTPException(status_code=404, detail=code)
    return HTTPException(status_code=400, detail=code)


async def _object(request: Request, *, schema: str) -> dict[str, Any]:
    """Parse a JSON object against the allow-list. No echo of rejected text."""
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        raise HTTPException(status_code=400, detail="bad_request")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="bad_request") from None
    code = body_violation(payload, schema=schema)
    if code == _RAW_SECRET:
        raise HTTPException(status_code=400, detail=_RAW_SECRET)
    if code:
        raise HTTPException(status_code=400, detail=code)
    return payload  # type: ignore[return-value]


def _tables(payload: dict[str, Any], key: str, *, required: bool) -> list[str] | None:
    if key not in payload:
        if required:
            raise HTTPException(status_code=400, detail="bad_request")
        return None
    raw = payload[key]
    if not isinstance(raw, list) or not raw or len(raw) > 64:
        raise HTTPException(status_code=400, detail="bad_request")
    if not all(isinstance(item, str) for item in raw):
        raise HTTPException(status_code=400, detail="bad_request")
    return raw


@router.post("/sources", status_code=201)
async def connect_source(
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    store: SpaceStoreDep,
    binding: StoreBindingDep,
    ask: AskServiceDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Read the connector listing for an OpenVault reference and grant those tables."""
    _enabled(settings)
    schema = "sources_v2" if settings.dms_connect_v2 else "sources"
    payload = await _object(request, schema=schema)
    token_id = _require_bearer(authorization)
    connector_id = payload.get("connector_id")
    space_name = payload.get("space_name")
    credential_ref = payload.get("credential_ref")
    if not isinstance(connector_id, str) or not connector_id.strip():
        raise HTTPException(status_code=400, detail="bad_request")
    if not isinstance(space_name, str) or not space_name.strip():
        raise HTTPException(status_code=400, detail="bad_request")
    if not isinstance(credential_ref, str) or not credential_ref.strip():
        raise HTTPException(status_code=400, detail="credential_ref_required")
    if key_id_for_ref(credential_ref.strip()) is None:
        raise HTTPException(status_code=400, detail="bad_credential_ref")
    options = payload.get("options")
    if options is None:
        grant_tables = None
    else:
        grant_tables = _tables(options, "grant_tables", required=False)
    kind = host = database = mode = ""
    port = 0
    tls = False
    if settings.dms_connect_v2:
        raw_kind = payload.get("kind")
        raw_host = payload.get("host")
        raw_db = payload.get("db")
        raw_mode = payload.get("mode")
        raw_port = payload.get("port")
        kind = raw_kind if isinstance(raw_kind, str) else ""
        host = raw_host if isinstance(raw_host, str) else ""
        database = raw_db if isinstance(raw_db, str) else ""
        mode = raw_mode if isinstance(raw_mode, str) else ""
        port = raw_port if isinstance(raw_port, int) and not isinstance(raw_port, bool) else -1
        tls = payload.get("tls") is True
        if (
            kind not in allowed_kinds()
            or mode not in _MODES
            or not host.strip()
            or not database.strip()
            or isinstance(port, bool)
            or port < 0
            or port > 65535
        ):
            raise HTTPException(status_code=400, detail="bad_request")
    decision = compliance_gate(
        action="connect.register",
        actor=settings.dms_actor_user_id,
        metadata={
            "task_id": "connect.register",
            "connector_id": connector_id.strip(),
        },
        client=cortex,
    )
    enforce(decision)
    try:
        if settings.dms_connect_v2:
            registered = connect_v2_register(
                store=store,
                ask=ask,
                database_url=settings.database_url,
                tenant_id=bound_tenant_id(settings.dms_tenant_id),
                connector_id=connector_id.strip(),
                space_name=space_name.strip(),
                credential_ref=credential_ref.strip(),
                kind=kind,
                host=host.strip(),
                port=port,
                database=database.strip(),
                mode=mode,
                grant_tables=grant_tables,
                tls=tls,
                actor=settings.dms_actor_user_id,
                token_id=token_id,
            )
        else:
            registered = connect_registered_source(
                store=store,
                ask=ask,
                connector_id=connector_id.strip(),
                space_name=space_name.strip(),
                grant_tables=grant_tables,
                credential_ref=credential_ref.strip(),
                actor=settings.dms_actor_user_id,
                token_id=token_id,
            )
    except (KeyError, ValueError, ConnectCredentialError, ConnectStoreError) as exc:
        raise _map_error(exc) from None
    return {
        **registered,
        "persisted": binding.persistent,
        "storage": binding.as_dict(),
        "hint": binding.hint,
    }


@router.post("/sources/{source_id}/grants")
async def grant_source(
    source_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _enabled(settings)
    payload = await _object(request, schema="tables")
    token_id = _require_bearer(authorization)
    tables = _tables(payload, "tables", required=True)
    assert tables is not None
    decision = compliance_gate(
        action="connect.grant",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.grant", "source_id": source_id},
        client=cortex,
    )
    enforce(decision)
    try:
        if settings.dms_connect_v2:
            return connect_v2_set_granted(
                database_url=settings.database_url or "",
                tenant_id=bound_tenant_id(settings.dms_tenant_id),
                source_id=source_id,
                tables=tables,
                granted=True,
                actor=settings.dms_actor_user_id,
                token_id=token_id,
            )
        return grant_connected_tables(
            source_id=source_id,
            tables=tables,
            actor=settings.dms_actor_user_id,
            token_id=token_id,
        )
    except (KeyError, ValueError, ConnectStoreError) as exc:
        raise _map_error(exc) from None


@router.post("/sources/{source_id}/revoke")
async def revoke_source(
    source_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _enabled(settings)
    payload = await _object(request, schema="tables")
    token_id = _require_bearer(authorization)
    tables = _tables(payload, "tables", required=True)
    assert tables is not None
    decision = compliance_gate(
        action="connect.revoke",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.revoke", "source_id": source_id},
        client=cortex,
    )
    enforce(decision)
    try:
        if settings.dms_connect_v2:
            return connect_v2_set_granted(
                database_url=settings.database_url or "",
                tenant_id=bound_tenant_id(settings.dms_tenant_id),
                source_id=source_id,
                tables=tables,
                granted=False,
                actor=settings.dms_actor_user_id,
                token_id=token_id,
            )
        return revoke_connected_tables(
            source_id=source_id,
            tables=tables,
            actor=settings.dms_actor_user_id,
            token_id=token_id,
        )
    except (KeyError, ValueError, ConnectStoreError) as exc:
        raise _map_error(exc) from None


@router.get("/audit")
def list_connect_audit(
    cortex: CortexDep,
    settings: SettingsDep,
    binding: StoreBindingDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _enabled(settings)
    token_id = _require_bearer(authorization)
    decision = compliance_gate(
        action="connect.audit.read",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.audit.read", "token_id": token_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return {
        "rows": connect_audit_rows(),
        "persisted": binding.persistent,
        "storage": binding.as_dict(),
    }


def _store_url(settings: Settings) -> str:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="connect_store_unavailable")
    return settings.database_url


@router.get("/sources")
def list_sources(
    cortex: CortexDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _v2(settings)
    token_id = _require_bearer(authorization)
    decision = compliance_gate(
        action="connect.sources.list",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.sources.list", "token_id": token_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    try:
        return connect_v2_list(
            database_url=_store_url(settings),
            tenant_id=bound_tenant_id(settings.dms_tenant_id),
            actor=settings.dms_actor_user_id,
            token_id=token_id,
        )
    except (KeyError, ValueError, ConnectStoreError) as exc:
        raise _map_error(exc) from None


@router.post("/test")
async def test_source(
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    ask: AskServiceDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _v2(settings)
    payload = await _object(request, schema="test")
    token_id = _require_bearer(authorization)
    kind = payload.get("kind")
    host = payload.get("host")
    port = payload.get("port")
    database = payload.get("db")
    cred_ref = payload.get("cred_ref")
    if not isinstance(kind, str) or kind not in allowed_kinds():
        raise HTTPException(status_code=400, detail="bad_request")
    if not isinstance(host, str) or not host.strip():
        raise HTTPException(status_code=400, detail="bad_request")
    if isinstance(port, bool) or not isinstance(port, int) or port < 0 or port > 65535:
        raise HTTPException(status_code=400, detail="bad_request")
    if not isinstance(database, str) or not database.strip():
        raise HTTPException(status_code=400, detail="bad_request")
    if not isinstance(cred_ref, str) or key_id_for_ref(cred_ref.strip()) is None:
        raise HTTPException(status_code=400, detail="bad_credential_ref")
    decision = compliance_gate(
        action="connect.test",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.test", "kind": kind},
        client=cortex,
    )
    enforce(decision)
    return connect_v2_probe(
        ask,
        kind=kind,
        host=host.strip(),
        port=port,
        database=database.strip(),
        cred_ref=cred_ref.strip(),
        tls=payload.get("tls") is True,
        actor=settings.dms_actor_user_id,
        token_id=token_id,
        database_url=settings.database_url,
        tenant_id=bound_tenant_id(settings.dms_tenant_id),
    )


@router.get("/sources/{source_id}/schema")
def source_schema(
    source_id: str,
    cortex: CortexDep,
    settings: SettingsDep,
    ask: AskServiceDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _v2(settings)
    token_id = _require_bearer(authorization)
    decision = compliance_gate(
        action="connect.schema.read",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.schema.read", "source_id": source_id, "token_id": token_id},
        client=cortex,
    )
    enforce(decision, mutation=False)
    try:
        return connect_v2_schema(
            ask,
            database_url=_store_url(settings),
            tenant_id=bound_tenant_id(settings.dms_tenant_id),
            source_id=source_id,
        )
    except (KeyError, ValueError, ConnectCredentialError, ConnectStoreError) as exc:
        raise _map_error(exc) from None


def _query_fields(payload: dict[str, Any]) -> tuple[str, int, int, bool]:
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise HTTPException(status_code=400, detail="bad_request")
    offset = payload.get("offset", 0)
    page_size = payload.get("page_size", 500)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise HTTPException(status_code=400, detail="bad_request")
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise HTTPException(status_code=400, detail="bad_request")
    return sql, offset, page_size, payload.get("confirm_runaway") is True


@router.post("/sources/{source_id}/query")
async def query_source(
    source_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    ask: AskServiceDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    _v2(settings)
    payload = await _object(request, schema="query")
    token_id = _require_bearer(authorization)
    sql, offset, page_size, confirm = _query_fields(payload)
    decision = compliance_gate(
        action="connect.query",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "connect.query", "source_id": source_id, "token_id": token_id},
        client=cortex,
    )
    enforce(decision)
    database_url = _store_url(settings)
    if payload.get("export") is True:
        try:
            query_id, iterator = connect_v2_export(
                ask,
                database_url=database_url,
                tenant_id=bound_tenant_id(settings.dms_tenant_id),
                source_id=source_id,
                sql=sql,
                confirm_runaway=confirm,
            )
        except (KeyError, ValueError, ConnectCredentialError, ConnectStoreError) as exc:
            raise _map_error(exc) from None

        def _lines() -> Any:
            try:
                for item in iterator:
                    body = dict(item)
                    body["query_id"] = query_id
                    yield json.dumps(body) + "\n"
            finally:
                connect_v2_close(query_id)

        return StreamingResponse(_lines(), media_type="application/x-ndjson")
    try:
        return connect_v2_query(
            ask,
            database_url=database_url,
            tenant_id=bound_tenant_id(settings.dms_tenant_id),
            source_id=source_id,
            sql=sql,
            offset=offset,
            page_size=page_size,
            confirm_runaway=confirm,
        )
    except (KeyError, ValueError, ConnectCredentialError, ConnectStoreError) as exc:
        raise _map_error(exc) from None


@router.post("/sources/{source_id}/query/cancel")
async def cancel_query(
    source_id: str,
    request: Request,
    cortex: CortexDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    _v2(settings)
    payload = await _object(request, schema="cancel")
    token_id = _require_bearer(authorization)
    query_id = payload.get("query_id")
    if not isinstance(query_id, str) or not query_id.strip():
        raise HTTPException(status_code=400, detail="bad_request")
    decision = compliance_gate(
        action="connect.query.cancel",
        actor=settings.dms_actor_user_id,
        metadata={
            "task_id": "connect.query.cancel",
            "source_id": source_id,
            "token_id": token_id,
        },
        client=cortex,
    )
    enforce(decision)
    try:
        return connect_v2_cancel(query_id.strip())
    except (KeyError, ValueError, ConnectStoreError) as exc:
        raise _map_error(exc) from None
