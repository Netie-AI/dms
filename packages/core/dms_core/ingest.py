"""Ingest port — bronze writer lives in executor; API talks only through this."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IngestPort(Protocol):
    def ingest_csv(
        self,
        *,
        filename: str,
        data: bytes,
        space_id: str | None = None,
    ) -> dict[str, Any]: ...

    def list_bronze(self) -> list[dict[str, Any]]: ...
