"""Space catalog port — Postgres when DATABASE_URL set; memory fallback for tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SpaceRecord:
    id: str
    name: str
    source_count: int
    member_count: int


@runtime_checkable
class SpaceStorePort(Protocol):
    def list_spaces(self) -> list[SpaceRecord]: ...

    def get(self, space_id: str) -> SpaceRecord | None: ...
