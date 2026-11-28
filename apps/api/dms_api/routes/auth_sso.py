"""Login and OIDC callbacks. Mounted only when ``DMS_SSO`` is the digit 1."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from dms_api.auth_sso import AuthError, begin_oidc, finish_oidc, login, me

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    #: Which of this user's companies. Omitted when they belong to one.
    #: Checked against membership. It is not the ledger actor.
    company: str | None = None


def _raise(exc: AuthError) -> NoReturn:
    raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from None


@router.post("/login")
def auth_login(body: LoginIn) -> dict[str, str]:
    try:
        return login(email=body.email, password=body.password, company=body.company)
    except AuthError as exc:
        _raise(exc)


@router.get("/me")
def auth_me() -> dict[str, str]:
    try:
        return me()
    except AuthError as exc:
        _raise(exc)


@router.get("/oidc/{provider}/start")
def oidc_start(
    provider: str,
    redirect_uri: str = Query(min_length=8, max_length=2048),
) -> RedirectResponse:
    try:
        url = begin_oidc(provider, redirect_uri)
    except AuthError as exc:
        _raise(exc)
    return RedirectResponse(url, status_code=302)


@router.get("/oidc/{provider}/callback")
def oidc_callback(provider: str, code: str = "", state: str = "") -> dict[str, str]:
    try:
        return finish_oidc(provider, code, state)
    except AuthError as exc:
        _raise(exc)
