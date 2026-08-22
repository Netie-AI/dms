from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from cortex_client import CortexClient
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dms_api.middleware_actor import DevActorMiddleware
from dms_api.migrate import run_migrations
from dms_api.routes import (
    admin,
    amend,
    audit,
    chat,
    health,
    library,
    ontology,
    ping,
    pipelines,
    runs,
    spaces,
    studio,
    trust,
)
from dms_api.settings import get_settings
from dms_api.store.binding import StoreBinding
from dms_api.store.memory import DemoSpaceStore
from dms_api.wiring import build_ask_service

logger = logging.getLogger(__name__)


def _build_space_store(settings) -> tuple[object, StoreBinding]:
    """Bind the Spaces catalog and report which backend actually answered.

    The fallback stays — an unreachable Postgres should not stop the API from
    serving reads — but it stops being invisible. The returned binding is what
    ``/health`` and every ``persisted`` field read, so a degraded store can no
    longer present itself as a durable one.
    """
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
            return store, StoreBinding.postgres()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Postgres spaces unavailable (%s); memory fallback", exc)
            return DemoSpaceStore.seeded(), StoreBinding.memory(
                configured=True, reason=str(exc)[:200]
            )
    return DemoSpaceStore.seeded(), StoreBinding.memory(configured=False)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    migrate_error: str | None = None
    if settings.database_url:
        try:
            run_migrations(settings.database_url)
            from dms_core.control_plane.seed import seed_demo_tenant

            seed_demo_tenant(settings.database_url)
        except Exception as exc:  # noqa: BLE001
            # Still non-fatal — a bad DATABASE_URL should not make the product
            # unstartable — but the reason now reaches the binding, so /health
            # reports "Postgres asked for, not in use, here is why" instead of a
            # warning line and a boolean that says everything is fine.
            migrate_error = str(exc)[:200]
            logger.warning("migrate/seed skipped: %s", exc)

    store, binding = _build_space_store(settings)
    if migrate_error and not binding.persistent:
        binding = StoreBinding.memory(
            configured=True, reason=binding.reason or f"migrate/seed failed: {migrate_error}"
        )
    app.state.space_store = store
    app.state.space_store_binding = binding
    cortex = CortexClient(
        settings.cortex_url,
        timeout=settings.cortex_timeout_seconds,
        api_key=settings.cortex_api_key,
    )
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
    app.include_router(ontology.router)
    app.include_router(trust.router)
    app.include_router(runs.router)
    app.include_router(admin.router)
    return app


app = create_app()
