"""Sign-in, session, and request-access routes.

Mounted only when ``DMS_APP_LOGIN=1``. Flag off, these paths are absent
(404) and the ask path is unchanged.

The ledger actor stays the configured deployment (DR-0004). The session
names a person for this page. It is not written into the ask path.
"""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from dms_api.app_login import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SESSION_SECONDS,
    LoginBook,
    cookie_secure,
    csrf_matches,
    new_csrf,
)
from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class SignInIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class AccessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    company: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=2000)


def _book(request: Request) -> LoginBook:
    book = getattr(request.app.state, "app_login", None)
    if book is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return book


def _json(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, **extra}
    return JSONResponse(status_code=status, content=body, headers={"cache-control": "no-store"})


def _host(request: Request) -> str:
    client = request.client
    return client.host if client is not None and client.host else "unknown"


def _email(value: str) -> str:
    return value.strip().lower()


def _refused(decision: Any) -> JSONResponse | None:
    try:
        enforce(decision)
    except HTTPException:
        return _json(
            403,
            "access_denied",
            "That action is unavailable right now. Try again shortly.",
        )
    return None


def _set_csrf(response: JSONResponse, request: Request, token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request.url.scheme),
        path="/",
    )


def _set_session(response: JSONResponse, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request.url.scheme),
        path="/",
    )


@router.get("/csrf")
def issue_csrf(request: Request) -> JSONResponse:
    _book(request)
    token = new_csrf()
    response = _json(200, "ok", "Form token issued.", token=token)
    _set_csrf(response, request, token)
    return response


@router.get("/session")
def read_session(request: Request) -> JSONResponse:
    book = _book(request)
    session = book.session_from(request.cookies.get(SESSION_COOKIE))
    if session is None:
        return _json(200, "ok", "No session.", enabled=True, authenticated=False)
    return _json(
        200,
        "ok",
        "Signed in.",
        enabled=True,
        authenticated=True,
        email=session.email,
        role=session.role,
    )


@router.post("/login")
def sign_in(
    body: SignInIn, request: Request, settings: SettingsDep, cortex: CortexDep
) -> JSONResponse:
    book = _book(request)
    email = _email(body.email)
    if not book.sign_in_attempts.allow(f"{_host(request)}|{email}"):
        return _json(
            429,
            "too_many_attempts",
            "Too many attempts. Wait a few minutes and try again.",
        )
    if not csrf_matches(
        request.cookies.get(CSRF_COOKIE, ""), request.headers.get("x-csrf-token", "")
    ):
        return _json(403, "form_expired", "The form expired. Refresh the page and try again.")
    decision = compliance_gate(
        action="app_login.sign_in",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "app_login.sign_in"},
        client=cortex,
    )
    refused = _refused(decision)
    if refused is not None:
        return refused
    session = book.open_session(
        email,
        body.password,
        database_url=settings.database_url,
        tenant_id=settings.dms_tenant_id,
    )
    if session is None:
        return _json(
            401,
            "sign_in_failed",
            "That email and password did not match. Try again, or request access.",
        )
    response = _json(200, "ok", "Signed in.", email=session.email, role=session.role)
    _set_session(response, request, session.token)
    return response


@router.post("/logout")
def sign_out(request: Request, settings: SettingsDep, cortex: CortexDep) -> JSONResponse:
    book = _book(request)
    if not csrf_matches(
        request.cookies.get(CSRF_COOKIE, ""), request.headers.get("x-csrf-token", "")
    ):
        return _json(403, "form_expired", "The form expired. Refresh the page and try again.")
    decision = compliance_gate(
        action="app_login.sign_out",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "app_login.sign_out"},
        client=cortex,
    )
    refused = _refused(decision)
    if refused is not None:
        return refused
    book.drop_session(request.cookies.get(SESSION_COOKIE))
    response = _json(200, "ok", "Signed out.")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/request-access")
def file_access_request(
    body: AccessIn, request: Request, settings: SettingsDep, cortex: CortexDep
) -> JSONResponse:
    book = _book(request)
    if not book.request_attempts.allow(_host(request)):
        return _json(
            429,
            "too_many_attempts",
            "Too many requests. Wait a while and try again.",
        )
    if not csrf_matches(
        request.cookies.get(CSRF_COOKIE, ""), request.headers.get("x-csrf-token", "")
    ):
        return _json(403, "form_expired", "The form expired. Refresh the page and try again.")
    decision = compliance_gate(
        action="app_login.request_access",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "app_login.request_access"},
        client=cortex,
    )
    refused = _refused(decision)
    if refused is not None:
        return refused
    row = book.file_request(
        name=body.name.strip(),
        email=_email(body.email),
        company=body.company.strip(),
        note=body.note.strip(),
    )
    return _json(200, "request_filed", "Request filed. A steward will review it.", id=row.id)


@router.get("/access-requests")
def list_access_requests(
    request: Request, settings: SettingsDep, cortex: CortexDep
) -> JSONResponse:
    book = _book(request)
    session = getattr(request.state, "app_login_session", None)
    if session is None:
        session = book.session_from(request.cookies.get(SESSION_COOKIE))
    if session is None or session.role not in {"steward", "admin"}:
        return _json(
            403,
            "access_denied",
            "You do not have access to that. A steward can grant it.",
        )
    decision = compliance_gate(
        action="app_login.list_requests",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "app_login.list_requests"},
        client=cortex,
    )
    refused = _refused(decision)
    if refused is not None:
        return refused
    return JSONResponse(
        status_code=200,
        content={
            "code": "ok",
            "requests": [
                {
                    "id": row.id,
                    "name": row.name,
                    "email": row.email,
                    "company": row.company,
                    "note": row.note,
                    "filed_at": row.filed_at,
                }
                for row in book.requests
            ],
        },
        headers={"cache-control": "no-store"},
    )
