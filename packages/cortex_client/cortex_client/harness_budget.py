"""Per-tenant budget against OpenVault. The service token is not logged."""

from __future__ import annotations

import os
import re

import httpx
from dms_core.harness_sec import demo_key_denied, set_budget_probe

_TENANT = re.compile(r"[A-Za-z0-9_-]{1,64}")


def ov_remaining(tenant: str, cost: int) -> bool:
    """True when OV reports remaining units at least ``cost``. Else False."""
    if not _TENANT.fullmatch(tenant):
        return False
    base = os.environ.get("OPENVAULT_URL", "").strip().rstrip("/")
    token = os.environ.get("DMS_OV_SERVICE_TOKEN", "").strip()
    if not base or not token or demo_key_denied(token):
        return False
    url = f"{base}/v1/tenants/{tenant}/budget"
    try:
        with httpx.Client(timeout=3.0) as http:
            res = http.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        return False
    if res.status_code != 200:
        return False
    try:
        body = res.json()
    except ValueError:
        return False
    remaining = body.get("remaining") if isinstance(body, dict) else None
    if isinstance(remaining, bool) or not isinstance(remaining, int):
        return False
    return remaining >= cost


def install_ov_budget() -> None:
    set_budget_probe(ov_remaining)
