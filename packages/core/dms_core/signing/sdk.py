"""HTTP client for Netie Space. Space calls DMS. This repo does not call Space.

Methods: ``create_envelope``, ``add_signer``, ``sign``, ``verify``, ``disclose``.
``draft`` fills an unsigned document. It has no path that signs.

``opener(method, url, body, headers) -> (status, body_bytes)`` lets a test pass
a fake transport. The default opener is ``urllib``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

Opener = Callable[[str, str, bytes | None, dict[str, str]], tuple[int, bytes]]


def _urllib_opener(
    method: str, url: str, body: bytes | None, headers: dict[str, str]
) -> tuple[int, bytes]:
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as res:  # noqa: S310 - caller supplies the DMS base URL
            return int(res.status), res.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()


class SigningClient:
    def __init__(self, base_url: str, *, opener: Opener | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._opener = opener or _urllib_opener

    def _call(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, raw = self._opener(method, f"{self._base}{path}", body, headers)
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            parsed = {"detail": "bad_response"}
        if status >= 400:
            detail = parsed.get("detail") if isinstance(parsed, dict) else "request_failed"
            raise RuntimeError(f"sign_http_{status}:{detail}")
        if not isinstance(parsed, dict):
            raise RuntimeError("sign_http_bad_body")
        return parsed

    def draft(
        self, *, title: str, body: str, fields: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/sign/drafts",
            {"title": title, "body": body, "fields": fields or {}},
        )

    def create_envelope(
        self,
        *,
        title: str,
        body: str = "",
        fields: dict[str, str] | None = None,
        order: str = "sequential",
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/sign/envelopes",
            {"title": title, "body": body, "fields": fields or {}, "order": order},
        )

    def add_signer(
        self,
        envelope_id: str,
        *,
        email: str,
        auth_method: str,
        identity: str = "",
        position: int = 1,
        ttl_s: int = 3600,
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/sign/envelopes/{envelope_id}/signers",
            {
                "email": email,
                "auth_method": auth_method,
                "identity": identity,
                "position": position,
                "ttl_s": ttl_s,
            },
        )

    def sign(
        self,
        envelope_id: str,
        *,
        signer_id: str,
        token: str = "",
        otp: str = "",
        consent_text: str,
        intent: str,
        capture: dict[str, Any],
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/sign/envelopes/{envelope_id}/sign",
            {
                "signer_id": signer_id,
                "token": token,
                "otp": otp,
                "consent_text": consent_text,
                "intent": intent,
                "capture": capture,
            },
        )

    def verify(self, *, pdf_b64: str) -> dict[str, Any]:
        return self._call("POST", "/v1/sign/verify", {"pdf_b64": pdf_b64})

    def disclose(
        self,
        document_id: str,
        *,
        mode: str = "view",
        watermark: bool = True,
        redact_fields: list[str] | None = None,
        ttl_s: int = 3600,
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/sign/documents/{document_id}/disclose",
            {
                "mode": mode,
                "watermark": watermark,
                "redact_fields": redact_fields or [],
                "ttl_s": ttl_s,
            },
        )
