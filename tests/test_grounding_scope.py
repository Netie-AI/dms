"""Grounding a question in chosen files narrows the manifest, not just the prompt.

Studio lets a user tick files and ask about those. The scope has to be enforced
by the engine, not suggested to the model, or it is decoration: a model told
"only look at transactions" that writes a query against ``suppliers`` would be
answered anyway.

``row_predicates`` keys *are* the readable set on the manifest Cortex enforces,
so the whole feature is "mint a smaller manifest". A question grounded in
``transactions`` cannot read ``suppliers`` because ``enforce_manifest`` refuses
the SQL.
"""

from __future__ import annotations

from dms_executor import Executor
from dms_executor.demo_warehouse import DEMO_TABLES


def _acl(**kw):
    return Executor(cortex=None).demo_acl(**kw)


def test_no_scope_reads_the_whole_space() -> None:
    """R-0005: the default must not become narrower by accident."""
    acl = _acl(session_id="s1")
    assert set(acl.row_predicates) == set(DEMO_TABLES)


def test_a_scope_narrows_the_readable_set() -> None:
    acl = _acl(session_id="s1", tables=["transactions"])
    assert set(acl.row_predicates) == {"transactions"}
    assert "suppliers" not in acl.row_predicates, "an unticked table stayed readable"


def test_several_files_are_all_readable() -> None:
    acl = _acl(session_id="s1", tables=["transactions", "inventory"])
    assert set(acl.row_predicates) == {"transactions", "inventory"}


def test_unknown_tables_are_dropped_not_trusted() -> None:
    """The list arrives from the browser and reaches manifest minting."""
    acl = _acl(session_id="s1", tables=["transactions", "; DROP TABLE users", "nope"])
    assert set(acl.row_predicates) == {"transactions"}


def test_a_fully_unknown_scope_falls_back_to_the_whole_space() -> None:
    """"Ground in nothing" must not mint an ACL that can read nothing (R-0005)."""
    acl = _acl(session_id="s1", tables=["nope", "also_nope"])
    assert set(acl.row_predicates) == set(DEMO_TABLES)


def test_an_empty_list_is_the_same_as_no_scope() -> None:
    assert set(_acl(session_id="s1", tables=[]).row_predicates) == set(DEMO_TABLES)


def test_a_different_scope_is_a_different_bound_session() -> None:
    """Sessions cache their binding, so one id must not serve two manifests.

    Without this, asking a scoped question after an unscoped one in the same
    session would reuse the wider manifest already bound and silently ignore the
    scope.
    """
    wide = _acl(session_id="s1")
    narrow = _acl(session_id="s1", tables=["transactions"])
    other = _acl(session_id="s1", tables=["inventory"])

    assert wide.session_id != narrow.session_id
    assert narrow.session_id != other.session_id


def test_scope_identity_is_order_independent() -> None:
    """Ticking the same two files in a different order is the same scope."""
    a = _acl(session_id="s1", tables=["transactions", "inventory"])
    b = _acl(session_id="s1", tables=["inventory", "transactions"])
    assert a.session_id == b.session_id


def test_space_id_survives_scoping() -> None:
    acl = _acl(session_id="s1", space_id="sp_q3", tables=["transactions"])
    assert acl.space_id == "sp_q3"
