from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from settings import get_settings
from dms.auth.router import router as auth_router, ensure_seed
from dms.spaces.router import router as spaces_router
from dms.query.router import router as query_router
from dms.ingest.router import router as ingest_router
from dms.proxy.router import router as proxy_router
from dms.health.router import router as health_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        ensure_seed()
    except Exception:
        pass
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Local slices first (win over proxy catch-all)
    app.include_router(health_router)
    app.include_router(auth_router, prefix="/auth", tags=["auth"])
    app.include_router(spaces_router, prefix="/spaces", tags=["spaces"])
    app.include_router(query_router, prefix="/dms", tags=["query"])
    app.include_router(ingest_router, prefix="/dms", tags=["ingest"])
    if settings.cortex_proxy:
        app.include_router(proxy_router, tags=["cortex-proxy"])
    return app


app = create_app()
