"""OpenVault HTTP fetch for FreeRoute consumption. GET catalog only.

Never POST chat completions. Never send a vault reveal header. Never read a
local vault directory. Never invent LIVE_KEY.
"""

from __future__ import annotations

from typing import Any

import httpx
from dms_core.freeroute import (
    FREEROUTE_PREFERENCE,
    catalog_paths,
    empty_plan,
    plan_free_providers,
    records_from_payloads,
    redact_text,
)


def fetch_openvault_payloads(
    base_url: str,
    *,
    timeout: float = 2.5,
) -> tuple[dict[str, Any], bool]:
    """GET OpenVault FreeRoute catalog endpoints. Empty dicts on miss.

    Returns (payloads_by_path, any_ok).
    """
    base = (base_url or "").rstrip("/")
    payloads: dict[str, Any] = {}
    any_ok = False
    if not base:
        return payloads, False
    try:
        with httpx.Client(timeout=timeout) as client:
            for path in catalog_paths():
                try:
                    res = client.get(f"{base}{path}")
                except httpx.HTTPError:
                    payloads[path] = None
                    continue
                if res.status_code >= 400:
                    payloads[path] = None
                    continue
                try:
                    body = res.json()
                except ValueError:
                    payloads[path] = None
                    continue
                payloads[path] = body
                any_ok = True
    except httpx.HTTPError:
        return payloads, False
    return payloads, any_ok


def consume_freeroute_plan(
    base_url: str,
    *,
    timeout: float = 2.5,
    preference: str = FREEROUTE_PREFERENCE,
) -> dict[str, Any]:
    """Resolve free+normal providers through OpenVault API only."""
    payloads, any_ok = fetch_openvault_payloads(base_url, timeout=timeout)
    if not any_ok:
        plan = empty_plan(openvault_ok=False)
        plan["preference"] = preference or FREEROUTE_PREFERENCE
        return plan
    records = records_from_payloads(
        status=payloads.get("/api/freeroute/status"),
        onboard=payloads.get("/api/freeroute/onboard"),
        register=payloads.get("/api/tool/register"),
        keys=payloads.get("/api/keys"),
    )
    plan = plan_free_providers(records, preference=preference)
    plan["openvault_ok"] = True
    plan["openvault_base"] = redact_text(base_url)
    return plan
