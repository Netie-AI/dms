"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from cortex_client import CortexClient
from dms_core.ask import AskServicePort
from dms_core.control_plane.spaces import SpaceStorePort
from fastapi import Depends, Request

from dms_api.settings import Settings, get_settings


def get_cortex_client(request: Request) -> Generator[CortexClient | None, None, None]:
    yield getattr(request.app.state, "cortex", None)


def get_space_store(request: Request) -> SpaceStorePort:
    return request.app.state.space_store


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
AskServiceDep = Annotated[AskServicePort, Depends(get_ask_service)]
