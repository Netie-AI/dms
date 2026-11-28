"""HTTP map for a missing tenant. The body is the code only.

Registered once on the app, so every route that lets the foreign key out
answers 409 ``tenant_not_provisioned`` instead of 500. Other foreign keys
stay 500, and their driver text is not copied into the body.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg.errors
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


def response_for_foreign_key(exc: BaseException) -> JSONResponse:
    if is_missing_tenant_fk(exc):
        logger.info("tenant_not_provisioned")
        return JSONResponse(status_code=STATUS, content=tenant_not_provisioned_body())
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


def install(app: FastAPI) -> None:
    @app.exception_handler(psycopg.errors.ForeignKeyViolation)
    async def missing_tenant(
        _request: Request, exc: psycopg.errors.ForeignKeyViolation
    ) -> JSONResponse:
        return response_for_foreign_key(exc)
