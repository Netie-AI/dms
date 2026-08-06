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

    def create(self, name: str) -> SpaceRecord:
        """Create a Space. Raises ``ValueError('space_name_taken')`` on conflict.

        Declared on the port rather than probed with ``getattr`` at the call
        site: creating a Space is not an optional capability, and treating it as
        one meant the Postgres store shipped without it and answered 501 on the
        first write after DATABASE_URL was set.
        """
        ...
