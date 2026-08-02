"""The Space boundary must hold in the data plane, not just the UI.

SPACES.md invariant 1 requires enforcement, not hiding. ``Executor.live_ask``
used to mint its manifest from ``demo_acl()``, which allowlisted every demo
table with predicate ``TRUE`` regardless of ``space_id`` — so the manifest
minted for "Q3 Audit", for "Margin sandbox", and for no Space at all were
byte-identical apart from the ``space_id`` label. Any user passing any Space id
that merely existed read the whole warehouse, and the envelope stamped a green
badge over it.

Confirmed live before the fix: "Margin sandbox" carries only ``inventory`` and
``locations`` sources, and a ``transactions`` question asked inside it returned
full revenue with badge ``L0_CERTIFIED``.

``intersect_space_grants``/``resolve_session_acl`` always implemented the
intersection correctly and were green in CI — they simply had no production
caller. That is the failure mode CLAUDE.md §8 warns about: a gate asserting an
intermediate artifact certifies a feature that does not exist on the serving
path.

They are now called from ``Executor.grantable_tables`` against the DR-0002 seed
in ``dms_executor.demo_grants``. The three tests below carried
``xfail(strict=True)`` markers, which XPASSed once the boundary held; the
markers are deleted and the assertions are unchanged.
"""

from __future__ import annotations

from dms_executor import Executor
from dms_executor.acl import (
    SourceGrant,
    intersect_space_grants,
    resolve_session_acl,
)

Q3_AUDIT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
MARGIN = "dddddddd-dddd-dddd-dddd-dddddddddddd"


class _FixtureStore:
    """The two seeded Spaces, with the sources the seed actually attaches."""

    _SOURCES = {
        Q3_AUDIT: [SourceGrant(source_id=1, kind="sql", table_name="transactions")],
        MARGIN: [
            SourceGrant(source_id=2, kind="sql", table_name="inventory"),
            SourceGrant(source_id=3, kind="sql", table_name="locations"),
        ],
    }

    def is_space_member(self, space_id: str, user_id: str) -> bool:
        return space_id in self._SOURCES

    def list_space_source_ids(self, space_id: str) -> list:
        return [g.source_id for g in self._SOURCES.get(space_id, [])]

    def list_user_source_grants(self, tenant_id: str, user_id: str) -> list[SourceGrant]:
        return [g for grants in self._SOURCES.values() for g in grants]

    def default_pool_id(self, tenant_id: str) -> str:
        return "default"


# ------------------------------------------------- the resolver already works


def test_the_intersection_itself_is_correct() -> None:
    """Not the bug. This is the code that exists and is never called."""
    ctx = intersect_space_grants(
        _FixtureStore(), tenant_id="t", user_id="u", space_id=MARGIN, session_id="s"
    )
    acl = resolve_session_acl(ctx)

    assert set(acl.row_predicates) == {"inventory", "locations"}
    assert "transactions" not in acl.row_predicates


def test_a_non_member_gets_nothing() -> None:
    ctx = intersect_space_grants(
        _FixtureStore(), tenant_id="t", user_id="u", space_id="sp_not_mine", session_id="s"
    )
    assert resolve_session_acl(ctx).row_predicates == {}


# ------------------------------------------------------- the serving path


def test_two_spaces_do_not_mint_the_same_acl() -> None:
    """The whole point of a Space: it changes what a question may read."""
    ex = Executor(cortex=None)

    q3 = ex.demo_acl(session_id="ses_q3", space_id=Q3_AUDIT)
    margin = ex.demo_acl(session_id="ses_margin", space_id=MARGIN)

    assert q3.row_predicates != margin.row_predicates, (
        "both Spaces minted identical table access — the Space label is decorative"
    )


def test_a_space_cannot_read_a_table_it_has_no_source_for() -> None:
    """Margin sandbox holds inventory + locations. It must not reach transactions.

    Confirmed live before this test existed: asking for revenue inside Margin
    sandbox returned the full ranking, badge L0_CERTIFIED.
    """
    acl = Executor(cortex=None).demo_acl(session_id="ses", space_id=MARGIN)

    assert "transactions" not in acl.row_predicates


def test_an_unknown_space_mints_no_access() -> None:
    """A Space id that is not yours must not widen into full warehouse access."""
    acl = Executor(cortex=None).demo_acl(session_id="ses", space_id="sp_not_a_real_space")

    assert acl.row_predicates == {}
