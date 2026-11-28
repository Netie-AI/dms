"""Require a session cookie when ``DMS_APP_LOGIN`` is on.

Identity headers stay refused by ``RejectIdentityHeadersMiddleware``. This
middleware reads only a cookie this process minted. Flag off: the middleware
is not installed.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from dms_api.app_login import SESSION_COOKIE

_PUBLIC = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/v1/auth/csrf"),
        ("GET", "/v1/auth/session"),
        ("POST", "/v1/auth/login"),
        ("POST", "/v1/auth/request-access"),
        ("POST", "/v1/auth/logout"),
    }
)

SIGN_IN_BODY = {
    "code": "sign_in_required",
    "message": ("Sign in to open Netie. This page replaces the browser password popup."),
}


class AppLoginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)
        book = getattr(request.app.state, "app_login", None)
        if book is None:
            return await call_next(request)
        path = request.url.path.rstrip("/") or "/"
        if (request.method.upper(), path) in _PUBLIC:
            return await call_next(request)
        session = book.session_from(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return JSONResponse(
                status_code=401,
                content=SIGN_IN_BODY,
                headers={"cache-control": "no-store"},
            )
        request.state.app_login_session = session
        return await call_next(request)
