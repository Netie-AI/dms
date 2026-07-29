"""Health and dependency probes."""

from __future__ import annotations

import httpx
from fastapi import APIRouter

from settings import get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "dms-api"}


@router.get("/ready")
async def ready() -> dict:
    settings = get_settings()
    out: dict = {"status": "ok", "cortex": None, "openvault": None, "database": None}
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            r = await client.get(f"{settings.cortex_url.rstrip('/')}/health")
            out["cortex"] = {"ok": r.status_code == 200, "status_code": r.status_code}
        except Exception as exc:  # noqa: BLE001
            out["cortex"] = {"ok": False, "error": str(exc)}
        try:
            r = await client.get(f"{settings.openvault_url.rstrip('/')}/api/healthz")
            out["openvault"] = {"ok": r.status_code == 200, "status_code": r.status_code}
        except Exception as exc:  # noqa: BLE001
            try:
                r = await client.get(f"{settings.openvault_url.rstrip('/')}/health")
                out["openvault"] = {"ok": r.status_code == 200, "status_code": r.status_code}
            except Exception as exc2:  # noqa: BLE001
                out["openvault"] = {"ok": False, "error": str(exc2)}
    try:
        from adapters.postgres import ping_db

        out["database"] = {"ok": ping_db()}
    except Exception as exc:  # noqa: BLE001
        out["database"] = {"ok": False, "error": str(exc)}
    if not out["database"].get("ok"):
        out["status"] = "degraded"
    return out
