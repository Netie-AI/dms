"""Write the provisioning bearer to OpenVault. There is no second vault.

The plaintext is the POST body and the reveal response. Neither is logged.
DMS keeps the sha256 and the ``cred_ref`` (see ``dms_core.scim.store``).
``DMS_OV_SERVICE_TOKEN`` is the same variable ManifestMinter already reads.
"""

from __future__ import annotations

import os
from typing import Protocol

import httpx
from dms_core.control_plane.connect_secrets import key_id_for_ref

# Same env var as dms_executor.manifest.SERVICE_TOKEN_ENV. Not a second token file.
_TOKEN_ENV = "DMS_OV_SERVICE_TOKEN"


class VaultError(Exception):
    """OpenVault did not keep the bearer. The message is the code only."""

    def __init__(self) -> None:
        super().__init__("scim_vault_unavailable")


class ScimVault(Protocol):
    def put(self, tenant_id: str, token: str) -> str: ...

    def reveal(self, cred_ref: str) -> str: ...


class OpenVaultScim:
    """``POST /api/keys`` then ``GET /api/keys/{id}/secret``."""

    def __init__(self, openvault_url: str) -> None:
        self._base = openvault_url.rstrip("/")

    def put(self, tenant_id: str, token: str) -> str:
        payload = {"name": f"dms-scim-{tenant_id}", "secret": token, "purpose": "scim"}
        body = self._json("POST", "/api/keys", json_body=payload)
        kid = body.get("id") or body.get("key_id")
        if not isinstance(kid, str) or not kid or kid == token:
            raise VaultError
        ref = kid if "/" in kid else f"dms/{kid}"
        if key_id_for_ref(ref) is None:
            raise VaultError
        return ref

    def reveal(self, cred_ref: str) -> str:
        kid = key_id_for_ref(cred_ref)
        if kid is None:
            raise VaultError
        body = self._json("GET", f"/api/keys/{kid}/secret", json_body=None)
        secret = body.get("secret")
        if not isinstance(secret, str) or secret == "":
            raise VaultError
        return secret

    def _headers(self) -> dict[str, str]:
        headers = {"X-OpenVault-Reveal": "intentional"}
        service = os.environ.get(_TOKEN_ENV, "").strip()
        if service:
            headers["Authorization"] = f"Bearer {service}"
        return headers

    def _json(
        self, method: str, path: str, *, json_body: dict[str, str] | None
    ) -> dict[str, object]:
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.request(
                    method,
                    f"{self._base}{path}",
                    json=json_body,
                    headers=self._headers(),
                )
        except httpx.HTTPError:
            raise VaultError from None
        if response.status_code >= 400:
            raise VaultError
        try:
            body = response.json()
        except ValueError:
            raise VaultError from None
        if not isinstance(body, dict):
            raise VaultError
        return body


_vault: ScimVault | None = None


def get_vault() -> ScimVault:
    if _vault is not None:
        return _vault
    from dms_api.settings import get_settings

    return OpenVaultScim(get_settings().openvault_url)


def set_vault(vault: ScimVault | None) -> None:
    """Tests install a double. ``None`` restores the OpenVault client."""
    global _vault
    _vault = vault
