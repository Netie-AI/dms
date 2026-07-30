from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from cortex_client import CortexClient
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dms_api.middleware_actor import DevActorMiddleware
from dms_api.migrate import run_migrations
from dms_api.routes import amend, audit, chat, health, library, ping, pipelines, spaces, studio
from dms_api.settings import get_settings
from dms_api.store.memory import DemoSpaceStore
from dms_api.wiring import build_ask_service

logger = logging.getLogger(__name__)


def _build_space_store(settings):
    if settings.database_url:
        try:
            from dms_core.control_plane.pg_spaces import PostgresSpaceStore

            store = PostgresSpaceStore(
                settings.database_url,
                tenant_id=settings.dms_tenant_id,
                role="steward",  # type: ignore[arg-type]
            )
            # Probe
            store.list_spaces()
            return store
        except Exception as exc:  # noqa: BLE001
            logger.warning("Postgres spaces unavailable (%s); memory fallback", exc)
    return DemoSpaceStore.seeded()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.database_url:
        try:
            run_migrations(settings.database_url)
            from dms_core.control_plane.seed import seed_demo_tenant

            seed_demo_tenant(settings.database_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("migrate/seed skipped: %s", exc)

    app.state.space_store = _build_space_store(settings)
    cortex = CortexClient(settings.cortex_url)
    app.state.cortex = cortex
    ask = build_ask_service(cortex)
    app.state.ask_service = ask
    try:
        yield
    finally:
        ask.close()
        cortex.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DMS API",
        version="0.1.0",
        description="DMS consumer app — Cortex via HTTP only (cortex-contract major 1)",
        lifespan=lifespan,
    )
    app.state.space_store = DemoSpaceStore.seeded()
    app.state.cortex = None
    app.state.ask_service = build_ask_service(None)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(DevActorMiddleware)
    app.include_router(health.router, tags=["health"])
    app.include_router(ping.router, tags=["skeleton"])
    app.include_router(spaces.router)
    app.include_router(chat.router)
    app.include_router(studio.router)
    app.include_router(pipelines.router)
    app.include_router(amend.router)
    app.include_router(audit.router)
    app.include_router(library.router)
    return app


app = create_app()
