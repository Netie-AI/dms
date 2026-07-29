from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "product": "dms",
        "version": "0.1.0",
        "contract": "1.0.0",
    }
