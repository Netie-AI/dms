"""In-memory Spaces catalog — fallback when DATABASE_URL unset."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4

from dms_core.control_plane.spaces import SpaceRecord


@dataclass
class DemoSpaceStore:
    _spaces: list[SpaceRecord] = field(default_factory=list)
    #: Old ids that still resolve on `get` but must not appear in the catalog —
    #: the Spaces page lists this store, and two "Finance" rows read as a bug.
    _aliases: list[SpaceRecord] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    @classmethod
    def seeded(cls) -> DemoSpaceStore:
        return cls(
            _spaces=[
                SpaceRecord(
                    id="cccccccc-cccc-cccc-cccc-cccccccccccc",
                    name="Finance",
                    source_count=3,
                    member_count=1,
                ),
                SpaceRecord(
                    id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                    name="Warehouse Ops",
                    source_count=2,
                    member_count=1,
                ),
            ],
            _aliases=[
                # Compat ids used by older smoke / live-ask tests: still askable.
                SpaceRecord(id="sp_q3_audit", name="Finance", source_count=4, member_count=1),
                SpaceRecord(id="sp_margin", name="Warehouse Ops", source_count=3, member_count=1),
            ],
        )

    def list_spaces(self) -> list[SpaceRecord]:
        with self._lock:
            return list(self._spaces)

    def get(self, space_id: str) -> SpaceRecord | None:
        with self._lock:
            for s in (*self._spaces, *self._aliases):
                if s.id == space_id:
                    return s
            return None

    def create(self, name: str) -> SpaceRecord:
        """Create a Space in memory. Lost on restart — the route says so."""
        record = SpaceRecord(id=str(uuid4()), name=name, source_count=0, member_count=1)
        with self._lock:
            if any(s.name.lower() == name.lower() for s in self._spaces):
                raise ValueError("space_name_taken")
            self._spaces.append(record)
        return record
