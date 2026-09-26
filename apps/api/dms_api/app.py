from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from cortex_client import CortexClient
from cortex_client.insights import CORTEX_KEY_MISSING, cortex_key_missing
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from dms_api.middleware_actor import RejectIdentityHeadersMiddleware
from dms_api.migrate import run_migrations
from dms_api.routes import (
    admin,
    amend,
    audit,
    chat,
    freeroute,
    health,
    insights,
    library,
    mcp,
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


def _validation_detail_without_input(exc: RequestValidationError) -> list[dict]:
    """Drop request `input` and `ctx` so a 422 cannot echo a password (R-0004)."""
    return [{k: v for k, v in err.items() if k not in ("input", "ctx")} for err in exc.errors()]


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
    # KEY-01 (dms#273): no Cortex key means no Cortex client, never a default.
    # Live mode without the bannered demo fallback has nothing to answer with,
    # so the API refuses to start rather than reach Cortex unkeyed.
    key_missing = cortex_key_missing(settings.cortex_api_key)
    if key_missing and settings.dms_ask_mode == "live" and not settings.dms_demo_fallback:
        raise RuntimeError(
            f"{CORTEX_KEY_MISSING}: DMS_ASK_MODE=live needs CORTEX_API_KEY set to the "
            "Cortex key OpenVault issued (Cortex's published demo key does not count). "
            "DMS will not start and call Cortex without one."
        )
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
    cortex: CortexClient | None = None
    if key_missing:
        logger.warning(
            "%s: no Cortex client (DMS_ASK_MODE=%s, DMS_DEMO_FALLBACK=%s)",
            CORTEX_KEY_MISSING,
            settings.dms_ask_mode,
            settings.dms_demo_fallback,
        )
    else:
        cortex = CortexClient(
            settings.cortex_url,
            timeout=settings.cortex_timeout_seconds,
            api_key=settings.cortex_api_key,
            insights_timeout=settings.dms_insights_ask_timeout_seconds,
        )
    app.state.cortex = cortex
    ask = build_ask_service(cortex)
    app.state.ask_service = ask
    try:
        yield
    finally:
        ask.close()
        if cortex is not None:
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
    app.add_middleware(RejectIdentityHeadersMiddleware)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": _validation_detail_without_input(exc)}
        )

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
    app.include_router(insights.router)
    app.include_router(freeroute.router)
    app.include_router(trust.router)
    app.include_router(runs.router)
    app.include_router(admin.router)
    if settings.dms_mcp:
        app.include_router(mcp.router)
    return app


app = create_app()
