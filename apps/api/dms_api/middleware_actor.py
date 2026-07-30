"""T5 lite — trust headers until OIDC. Not a security boundary for production."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from dms_api.settings import get_settings


class DevActorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        request.state.tenant_id = (
            request.headers.get("x-dms-tenant-id") or settings.dms_tenant_id
        )
        request.state.actor_user_id = (
            request.headers.get("x-dms-actor-id") or settings.dms_actor_user_id
        )
        request.state.actor_role = (
            request.headers.get("x-dms-role") or settings.dms_actor_role
        )
        return await call_next(request)
