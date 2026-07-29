from __future__ import annotations

import httpx

from settings import get_settings


async def cortex_health() -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=3.0) as client:
        r = await client.get(f"{settings.cortex_url.rstrip('/')}/health")
        return {"ok": r.status_code == 200, "body": r.text[:200]}


async def openvault_health() -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=3.0) as client:
        for path in ("/api/healthz", "/health"):
            try:
                r = await client.get(f"{settings.openvault_url.rstrip('/')}{path}")
                if r.status_code == 200:
                    return {"ok": True, "path": path}
            except Exception:
                continue
        return {"ok": False}
