"""Document signing routes. Mounted only when ``DMS_SIGN`` is the digit 1.

Space calls this HTTP surface (see ``dms_core.signing.sdk.SigningClient``):

- ``POST /v1/sign/envelopes`` create_envelope
- ``POST /v1/sign/envelopes/{id}/signers`` add_signer
- ``POST /v1/sign/envelopes/{id}/sign`` sign
- ``POST /v1/sign/verify`` and ``POST /v1/sign/verify/upload`` verify
- ``POST /v1/sign/documents/{id}/disclose`` disclose

``POST /v1/sign/drafts`` stores an unsigned document. A model may draft.
``sign`` refuses a model actor. The signing key is loaded from OpenVault
inside the request and is not accepted from the body.
"""

from __future__ import annotations

import base64
from typing import Any, Literal

from cortex_client import compliance_gate
from dms_core.signing.errors import SignError
from dms_core.signing.flag import enabled
from dms_core.signing.material import SigningMaterial
from dms_core.signing.service import Actor, SignService
from dms_core.signing.store import get_sign_store
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

router = APIRouter(prefix="/v1/sign", tags=["sign"])


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=8000)
    fields: dict[str, str] = Field(default_factory=dict)
    pdf_b64: str = ""


class EnvelopeIn(DraftIn):
    order: Literal["sequential", "parallel"] = "sequential"


class SignerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=200)
    auth_method: Literal["sso", "email_otp"]
    identity: str = Field(default="", max_length=200)
    position: int = Field(ge=1, le=20)
    ttl_s: int = Field(default=3600, ge=60, le=30 * 24 * 3600)


class CaptureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["typed", "drawn", "upload"]
    text: str = ""
    png_b64: str = ""
    svg: str = ""


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signer_id: str
    token: str = ""
    otp: str = ""
    consent_text: str = Field(min_length=1, max_length=2000)
    intent: str = Field(min_length=1, max_length=200)
    capture: CaptureIn


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_b64: str = Field(min_length=8)


class DiscloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["view", "download"] = "view"
    watermark: bool = True
    redact_fields: list[str] = Field(default_factory=list)
    ttl_s: int = Field(default=3600, ge=60, le=30 * 24 * 3600)


def _actor(request: Request, settings: Any) -> Actor:
    host = ""
    if request.client is not None and request.client.host:
        host = request.client.host
    return Actor(
        identity=str(settings.dms_actor_user_id or ""),
        ip=host,
        user_agent=request.headers.get("user-agent", ""),
    )


def _service(request: Request) -> SignService:
    store = getattr(request.app.state, "sign_store", None) or get_sign_store()
    loader = getattr(request.app.state, "sign_key_loader", None)
    if loader is None:
        def loader(tenant_id: str) -> SigningMaterial:
            from dms_api.wiring import load_sign_pems

            key, cert = load_sign_pems(tenant_id)
            return SigningMaterial(key.encode("utf-8"), cert.encode("utf-8"))

    ledger = getattr(request.app.state, "sign_ledger", None)
    if ledger is None:
        from dms_api.wiring import sign_ledger_callback

        ledger = sign_ledger_callback(getattr(request.app.state, "cortex", None))
    return SignService(
        store,
        keys=loader,
        objects=getattr(request.app.state, "sign_object_store", None),
        ledger=ledger,
    )


def _build(request: Request, settings: Any) -> SignService:
    service = _service(request)
    service.tenant = str(settings.dms_tenant_id)
    return service


def _pdf_b64(raw: str) -> bytes:
    try:
        return base64.b64decode(raw, validate=True)
    except Exception:
        raise SignError("bad_pdf") from None


def _run(action: Any) -> Any:
    try:
        return action()
    except SignError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=exc.status, detail=exc.code) from None


@router.get("/status")
def sign_status(request: Request, settings: SettingsDep, cortex: CortexDep) -> dict[str, bool]:
    enforce(
        compliance_gate(
            action="sign.status",
            actor=str(settings.dms_actor_user_id or ""),
            client=cortex,
        ),
        mutation=False,
    )
    return {"enabled": enabled()}


@router.post("/drafts")
def draft_document(
    body: DraftIn, request: Request, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.draft",
            actor=str(settings.dms_actor_user_id or ""),
            resource="sign_document",
            client=cortex,
        )
    )
    pdf = _pdf_b64(body.pdf_b64) if body.pdf_b64 else None
    return _run(
        lambda: _build(request, settings).draft(
            title=body.title,
            body=body.body,
            fields=body.fields,
            actor=_actor(request, settings),
            pdf=pdf,
        )
    )


@router.post("/envelopes")
def create_envelope(
    body: EnvelopeIn, request: Request, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.envelope",
            actor=str(settings.dms_actor_user_id or ""),
            resource="sign_envelope",
            client=cortex,
        )
    )
    pdf = _pdf_b64(body.pdf_b64) if body.pdf_b64 else None
    return _run(
        lambda: _build(request, settings).create_envelope(
            title=body.title,
            body=body.body,
            fields=body.fields,
            order=body.order,
            actor=_actor(request, settings),
            pdf=pdf,
        )
    )


@router.post("/envelopes/{envelope_id}/signers")
def add_signer(
    envelope_id: str,
    body: SignerIn,
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.add_signer",
            actor=str(settings.dms_actor_user_id or ""),
            resource=envelope_id,
            client=cortex,
        )
    )
    return _run(
        lambda: _build(request, settings).add_signer(
            envelope_id,
            email=body.email,
            auth_method=body.auth_method,
            identity=body.identity,
            position=body.position,
            ttl_s=body.ttl_s,
            actor=_actor(request, settings),
        )
    )


@router.post("/envelopes/{envelope_id}/sign")
def sign_envelope(
    envelope_id: str,
    body: SignIn,
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.sign",
            actor=str(settings.dms_actor_user_id or ""),
            resource=envelope_id,
            client=cortex,
        )
    )
    return _run(
        lambda: _build(request, settings).sign(
            envelope_id,
            signer_id=body.signer_id,
            token=body.token,
            otp=body.otp,
            consent_text=body.consent_text,
            intent=body.intent,
            capture=body.capture.model_dump(),
            actor=_actor(request, settings),
        )
    )


@router.post("/links/{token}/sign")
def sign_with_link(
    token: str,
    body: SignIn,
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    """One-time signer link. The path token is the only token that is accepted."""
    enforce(
        compliance_gate(
            action="sign.sign",
            actor=str(settings.dms_actor_user_id or ""),
            resource="sign_link",
            client=cortex,
        )
    )

    def _do() -> dict[str, Any]:
        service = _build(request, settings)
        signer = service.store.get_signer(service.tenant, body.signer_id)
        if signer is None:
            raise SignError("signer_unknown", 404)
        return service.sign(
            str(signer["envelope_id"]),
            signer_id=body.signer_id,
            token=token,
            otp=body.otp,
            consent_text=body.consent_text,
            intent=body.intent,
            capture=body.capture.model_dump(),
            actor=_actor(request, settings),
        )

    return _run(_do)


@router.get("/documents/{document_id}/file")
def document_file(
    document_id: str, request: Request, settings: SettingsDep, cortex: CortexDep
) -> Response:
    enforce(
        compliance_gate(
            action="sign.read",
            actor=str(settings.dms_actor_user_id or ""),
            resource=document_id,
            client=cortex,
        )
    )
    data = _run(
        lambda: _build(request, settings).document_file(document_id, _actor(request, settings))
    )
    return Response(content=data, media_type="application/pdf")


@router.get("/documents/{document_id}/audit")
def document_audit(
    document_id: str, request: Request, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.audit",
            actor=str(settings.dms_actor_user_id or ""),
            resource=document_id,
            client=cortex,
        ),
        mutation=False,
    )
    return _run(lambda: _build(request, settings).audit(document_id))


@router.post("/verify")
def verify_json(
    body: VerifyIn, request: Request, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.verify",
            actor=str(settings.dms_actor_user_id or ""),
            resource="sign_verify",
            client=cortex,
        )
    )
    return _run(lambda: _build(request, settings).verify(_pdf_b64(body.pdf_b64)))


@router.post("/verify/upload")
def verify_upload(
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.verify",
            actor=str(settings.dms_actor_user_id or ""),
            resource="sign_verify",
            client=cortex,
        )
    )
    data = file.file.read()
    if not isinstance(data, bytes):
        data = bytes(data)
    return _run(lambda: _build(request, settings).verify(data))


@router.post("/documents/{document_id}/disclose")
def disclose_document(
    document_id: str,
    body: DiscloseIn,
    request: Request,
    settings: SettingsDep,
    cortex: CortexDep,
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.disclose",
            actor=str(settings.dms_actor_user_id or ""),
            resource=document_id,
            client=cortex,
        )
    )
    return _run(
        lambda: _build(request, settings).disclose(
            document_id,
            mode=body.mode,
            watermark=body.watermark,
            redact_fields=body.redact_fields,
            ttl_s=body.ttl_s,
            actor=_actor(request, settings),
        )
    )


@router.post("/disclosures/{disclosure_id}/revoke")
def revoke_disclosure(
    disclosure_id: str, request: Request, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.revoke",
            actor=str(settings.dms_actor_user_id or ""),
            resource=disclosure_id,
            client=cortex,
        )
    )
    return _run(lambda: _build(request, settings).revoke(disclosure_id, _actor(request, settings)))


@router.get("/disclosures/{disclosure_id}/access")
def disclosure_access(
    disclosure_id: str, request: Request, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    enforce(
        compliance_gate(
            action="sign.access",
            actor=str(settings.dms_actor_user_id or ""),
            resource=disclosure_id,
            client=cortex,
        ),
        mutation=False,
    )
    rows = _run(lambda: _build(request, settings).access_log(disclosure_id))
    return {"disclosure_id": disclosure_id, "access": rows}


@router.get("/share/{token}")
def read_share(token: str, request: Request, settings: SettingsDep, cortex: CortexDep) -> Response:
    enforce(
        compliance_gate(
            action="sign.share",
            actor="share",
            resource="sign_share",
            client=cortex,
        ),
        mutation=False,
    )
    host = request.client.host if request.client is not None and request.client.host else ""

    def _read() -> tuple[bytes, str, bool]:
        return _build(request, settings).read_share(
            token,
            ip=host,
            user_agent=request.headers.get("user-agent", ""),
        )

    data, mode, original = _run(_read)
    disposition = "inline" if mode == "view" else "attachment"
    headers = {
        "Content-Disposition": f'{disposition}; filename="disclosure.pdf"',
        "X-DMS-Signed-Original": "true" if original else "false",
    }
    return Response(content=data, media_type="application/pdf", headers=headers)
