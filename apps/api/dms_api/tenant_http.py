"""HTTP map for a missing tenant. The body is the code only.

Registered once on the app, so every route that lets the foreign key out
answers 409 ``tenant_not_provisioned`` instead of 500. Other foreign keys
stay 500, and their driver text is not copied into the body.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg.errors
from dms_core.control_plane.ready_flow import is_actor_fk, tenant_enabled
from dms_core.control_plane.tenant_provision import (
    TENANT_NOT_PROVISIONED,
    is_missing_tenant_fk,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

STATUS = 409


def tenant_not_provisioned_body() -> dict[str, Any]:
    return {"detail": {"code": TENANT_NOT_PROVISIONED}}


def _constraint_name(exc: BaseException) -> str:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        diag = getattr(cur, "diag", None)
        name = getattr(diag, "constraint_name", None) if diag is not None else None
        if isinstance(name, str):
            return name
        cause = cur.__cause__
        cur = cause if isinstance(cause, BaseException) else None
    return ""


def response_for_foreign_key(exc: BaseException) -> JSONResponse:
    if is_missing_tenant_fk(exc):
        logger.info("tenant_not_provisioned")
        return JSONResponse(status_code=STATUS, content=tenant_not_provisioned_body())
    # A missing user is not a crash. Flag off stays the bare 500.
    if tenant_enabled() and is_actor_fk(_constraint_name(exc)):
        logger.info("actor_not_in_tenant")
        return JSONResponse(
            status_code=STATUS,
            content={"detail": {"code": "actor_not_in_tenant"}},
        )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


def install(app: FastAPI) -> None:
    @app.exception_handler(psycopg.errors.ForeignKeyViolation)
    async def missing_tenant(
        _request: Request, exc: psycopg.errors.ForeignKeyViolation
    ) -> JSONResponse:
        return response_for_foreign_key(exc)
