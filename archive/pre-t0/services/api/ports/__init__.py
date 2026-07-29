"""Port interfaces — adapters implement these."""

from __future__ import annotations

from typing import Any, Protocol


class ControlStore(Protocol):
    def ping(self) -> bool: ...


class EngineClient(Protocol):
    async def health(self) -> dict[str, Any]: ...


class VaultClient(Protocol):
    async def health(self) -> dict[str, Any]: ...
