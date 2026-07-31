"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from cortex_client import CortexClient
from dms_core.ask import AskServicePort
from dms_core.control_plane.spaces import SpaceStorePort
from fastapi import Depends, Request

from dms_api.settings import Settings, get_settings
from dms_api.store.binding import StoreBinding


def get_cortex_client(request: Request) -> Generator[CortexClient | None, None, None]:
    yield getattr(request.app.state, "cortex", None)


def get_space_store(request: Request) -> SpaceStorePort:
    return request.app.state.space_store


def get_store_binding(request: Request) -> StoreBinding:
    """Which catalog backend actually bound at startup.

    Falls back to an unconfigured memory binding for the app-less test clients
    that never ran lifespan — that is the truthful answer for those, not a guess.
    """
    binding = getattr(request.app.state, "space_store_binding", None)
    if binding is None:
        return StoreBinding.memory(configured=bool(get_settings().database_url))
    return binding


def get_ask_service(request: Request) -> AskServicePort:
    svc = getattr(request.app.state, "ask_service", None)
    if svc is None:
        from dms_api.wiring import build_ask_service

        svc = build_ask_service(getattr(request.app.state, "cortex", None))
        request.app.state.ask_service = svc
    return svc


SettingsDep = Annotated[Settings, Depends(get_settings)]
CortexDep = Annotated[CortexClient | None, Depends(get_cortex_client)]
SpaceStoreDep = Annotated[SpaceStorePort, Depends(get_space_store)]
StoreBindingDep = Annotated[StoreBinding, Depends(get_store_binding)]
AskServiceDep = Annotated[AskServicePort, Depends(get_ask_service)]
