"""In-memory Spaces catalog — fallback when DATABASE_URL unset."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from dms_core.control_plane.spaces import SpaceRecord


@dataclass
class DemoSpaceStore:
    _spaces: list[SpaceRecord] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    @classmethod
    def seeded(cls) -> DemoSpaceStore:
        return cls(
            _spaces=[
                SpaceRecord(
                    id="cccccccc-cccc-cccc-cccc-cccccccccccc",
                    name="Q3 Audit",
                    source_count=3,
                    member_count=1,
                ),
                SpaceRecord(
                    id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                    name="Margin sandbox",
                    source_count=2,
                    member_count=1,
                ),
                # Compat aliases used by older smoke / live-ask tests
                SpaceRecord(id="sp_q3_audit", name="Q3 Audit", source_count=3, member_count=1),
                SpaceRecord(id="sp_margin", name="Margin sandbox", source_count=2, member_count=1),
            ]
        )

    def list_spaces(self) -> list[SpaceRecord]:
        with self._lock:
            return list(self._spaces)

    def get(self, space_id: str) -> SpaceRecord | None:
        with self._lock:
            for s in self._spaces:
                if s.id == space_id:
                    return s
            return None
