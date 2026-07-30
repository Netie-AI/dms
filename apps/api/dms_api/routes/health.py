from __future__ import annotations

from fastapi import APIRouter

from dms_api.deps import SettingsDep

router = APIRouter()


@router.get("/health")
def health(settings: SettingsDep) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "product": "dms",
        "version": "0.1.0",
        "contract": settings.cortex_contract_version,
        "ask_mode": settings.dms_ask_mode,
        "demo_fallback": settings.dms_demo_fallback,
        "database_configured": bool(settings.database_url),
    }
