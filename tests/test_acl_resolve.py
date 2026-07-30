"""ACL intersection: Space members ∩ source grants."""

from __future__ import annotations

from uuid import uuid4

from dms_executor.acl import (
    SessionContext,
    SourceGrant,
    intersect_space_grants,
    resolve_session_acl,
)


class _MemStore:
    def __init__(self) -> None:
        self.space_id = str(uuid4())
        self.user = "u1"
        self.in_space = {self.user}
        self.src_a = uuid4()
        self.src_b = uuid4()
        self.space_sources = [self.src_a]
        self.grants = [
            SourceGrant(
                source_id=self.src_a,
                kind="parquet",
                table_name="silver.orders",
                row_predicate="region = 'N'",
            ),
            SourceGrant(
                source_id=self.src_b,
                kind="parquet",
                parquet_glob="lake/bronze/other/*.parquet",
            ),
        ]

    def is_space_member(self, space_id: str, user_id: str) -> bool:
        return space_id == self.space_id and user_id in self.in_space

    def list_space_source_ids(self, space_id: str):
        return list(self.space_sources)

    def list_user_source_grants(self, tenant_id: str, user_id: str):
        return list(self.grants)

    def default_pool_id(self, tenant_id: str) -> str:
        return "pool-read"


def test_space_intersection_drops_non_space_sources() -> None:
    store = _MemStore()
    ctx = intersect_space_grants(
        store,
        tenant_id="t1",
        user_id=store.user,
        space_id=store.space_id,
        session_id="s1",
    )
    assert len(ctx.grants) == 1
    assert ctx.grants[0].source_id == store.src_a
    acl = resolve_session_acl(ctx)
    assert "silver.orders" in acl.row_predicates
    assert acl.allowed_paths == []


def test_company_scope_keeps_all_user_grants() -> None:
    store = _MemStore()
    ctx = intersect_space_grants(
        store,
        tenant_id="t1",
        user_id=store.user,
        space_id=None,
        session_id="s2",
    )
    assert len(ctx.grants) == 2
