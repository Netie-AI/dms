"""The demo warehouse's Space grants, as data (DR-0002).

``intersect_space_grants``/``resolve_session_acl`` have always been correct and
have never had a production caller, because the facts they intersect live in
``dms.data_sources``/``dms.acl_grants`` in Postgres — which is parked (P-DMS-2)
and holds zero grant rows. Wiring the boundary to Postgres would therefore
refuse 100 percent of asks, which R-0005 forbids.

So the grants are seeded here, behind the same ``SessionStore`` port. The
Postgres-backed store replaces this one without touching the serving path.

Per DR-0002 the *code* here is generic: it reads a seed table and knows nothing
about which columns are sensitive. A real customer's scoping is theirs to set,
so nothing below may grow a rule keyed on a specific table name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from dms_executor.acl import SourceGrant

#: Namespace for deriving a stable source id per table name. The demo has no
#: source registry, so ids are derived rather than stored.
_SOURCE_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

#: Readable from any Space. Reference and operational data that every role needs.
COMPANY_SCOPED: tuple[str, ...] = ("locations", "inventory")

#: The two demo Spaces and the tables each may read, per DR-0002. Company-scoped
#: tables are listed explicitly rather than merged in silently — the grant set a
#: Space actually holds should be readable here without cross-referencing.
DEMO_SPACE_GRANTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "cccccccc-cccc-cccc-cccc-cccccccccccc": (
        "Finance",
        ("locations", "inventory", "transactions", "suppliers"),
    ),
    "dddddddd-dddd-dddd-dddd-dddddddddddd": (
        "Warehouse Ops",
        ("locations", "inventory", "shipments"),
    ),
}

#: Aliases for the string ids the memory store hands the UI. The Postgres seed
#: and the in-process store disagree on Space id format, and a Space that exists
#: under one id and not the other would read as a refusal bug on demo day.
_SPACE_ALIASES: dict[str, str] = {
    "sp_finance": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "sp_q3_audit": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "sp_warehouse_ops": "dddddddd-dddd-dddd-dddd-dddddddddddd",
    "sp_margin": "dddddddd-dddd-dddd-dddd-dddddddddddd",
}


def source_id_for(table: str) -> uuid.UUID:
    """Stable id for a table-backed source."""
    return uuid.uuid5(_SOURCE_NS, table)


def canonical_space_id(space_id: str) -> str:
    return _SPACE_ALIASES.get(space_id, space_id)


def space_name(space_id: str) -> str | None:
    entry = DEMO_SPACE_GRANTS.get(canonical_space_id(space_id))
    return entry[0] if entry else None


@dataclass(frozen=True)
class DemoSessionStore:
    """``SessionStore`` over the DR-0002 seed.

    ``extra_grants`` carries sources that exist but were never seeded — an
    uploaded bronze table is grantable to the Space that uploaded it, and is not
    in the demo seed. Without it, grounding a question in your own upload would
    refuse (R-0005).
    """

    extra_grants: tuple[str, ...] = ()

    def _tables_for(self, space_id: str) -> tuple[str, ...]:
        entry = DEMO_SPACE_GRANTS.get(canonical_space_id(space_id))
        return entry[1] if entry else ()

    def is_space_member(self, space_id: str, user_id: str) -> bool:
        # The demo has one steward who belongs to every seeded Space. An id that
        # is not seeded is not a Space you are a member of.
        return canonical_space_id(space_id) in DEMO_SPACE_GRANTS

    def list_space_source_ids(self, space_id: str) -> list[uuid.UUID]:
        tables = (*self._tables_for(space_id), *self.extra_grants)
        return [source_id_for(t) for t in tables]

    def list_user_source_grants(self, tenant_id: str, user_id: str) -> list[SourceGrant]:
        seeded = {t for _, tables in DEMO_SPACE_GRANTS.values() for t in tables}
        return [
            SourceGrant(
                source_id=source_id_for(t),
                kind="sql",
                table_name=t,
                row_predicate="TRUE",
            )
            for t in sorted(seeded | set(self.extra_grants))
        ]

    def default_pool_id(self, tenant_id: str) -> str:
        return "default"
