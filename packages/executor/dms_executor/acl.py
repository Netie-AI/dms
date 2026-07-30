"""Resolve session ACL: Space members ∩ source ACL grants → mint inputs.

Cortex enforces paths/predicates; DMS only decides what to put on the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from dms_executor.manifest import SessionAcl


@dataclass(frozen=True)
class SourceGrant:
    """One source the session may read."""

    source_id: UUID
    kind: str  # xlsx | parquet | sql | ...
    # Lake path glob for file-backed sources (omit when using table predicates only)
    parquet_glob: str | None = None
    # Governed table name for row_predicates allowlist
    table_name: str | None = None
    # SQL boolean; use TRUE when no row filter. Required if table_name set.
    row_predicate: str = "TRUE"


@dataclass
class SessionContext:
    """Minting input for one authenticated session."""

    session_id: str
    tenant_id: str  # org_id on the wire
    space_id: str | None
    user_id: str
    pool_id: str
    # Sources after space_members ∩ source_acl_grants
    grants: list[SourceGrant] = field(default_factory=list)
    ttl_seconds: int = 900


class SessionStore(Protocol):
    """Port for loading ACL facts (Postgres later; fixtures in tests)."""

    def is_space_member(self, space_id: str, user_id: str) -> bool: ...

    def list_space_source_ids(self, space_id: str) -> list[UUID]: ...

    def list_user_source_grants(self, tenant_id: str, user_id: str) -> list[SourceGrant]: ...

    def default_pool_id(self, tenant_id: str) -> str: ...


def resolve_session_acl(ctx: SessionContext) -> SessionAcl:
    """Map SessionContext grants into SessionAcl, enforcing minting layout rules.

    For a Space: caller must already have intersected membership ∩ grants into
    ``ctx.grants``. This function does not talk to the DB.
    """
    paths: list[str] = []
    predicates: dict[str, str] = {}
    for g in ctx.grants:
        if g.table_name:
            predicates[g.table_name] = g.row_predicate or "TRUE"
        if g.parquet_glob:
            # Minting invariant: do not also attach a non-TRUE predicate for the
            # same underlying table — checked later in ManifestMinter.
            paths.append(g.parquet_glob)

    return SessionAcl(
        session_id=ctx.session_id,
        org_id=ctx.tenant_id,
        space_id=ctx.space_id,
        row_predicates=predicates,
        allowed_paths=paths,
        pool_id=ctx.pool_id,
        ttl_seconds=ctx.ttl_seconds,
    )


def intersect_space_grants(
    store: SessionStore,
    *,
    tenant_id: str,
    user_id: str,
    space_id: str | None,
    session_id: str,
    pool_id: str | None = None,
    ttl_seconds: int = 900,
) -> SessionContext:
    """Load Space membership ∩ source ACL into a SessionContext."""
    user_grants = store.list_user_source_grants(tenant_id, user_id)
    if space_id is None:
        grants = user_grants
    else:
        if not store.is_space_member(space_id, user_id):
            grants = []
        else:
            allowed = set(store.list_space_source_ids(space_id))
            grants = [g for g in user_grants if g.source_id in allowed]

    return SessionContext(
        session_id=session_id,
        tenant_id=tenant_id,
        space_id=space_id,
        user_id=user_id,
        pool_id=pool_id or store.default_pool_id(tenant_id),
        grants=grants,
        ttl_seconds=ttl_seconds,
    )


def mint_manifest_for_session(minter: Any, ctx: SessionContext) -> Any:
    """Convenience: resolve ACL then mint."""
    acl = resolve_session_acl(ctx)
    return minter.mint_manifest(acl)
