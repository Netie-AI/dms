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

import pytest
from dms_core.ask import GroundingRefused
from dms_executor import Executor
from dms_executor.demo_warehouse import DEMO_TABLES

#: A seeded Space (DR-0002 "Finance"). An unknown id now grants nothing, so a
#: made-up one no longer stands in for "some Space".
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


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


def test_unknown_tables_are_refused_not_dropped() -> None:
    """The list arrives from the browser and reaches manifest minting.

    Superseded by dms#5. This asserted that junk was dropped and the grantable
    remainder honoured — but dropping emptied a selection of *only* unknown
    names, and an empty selection then fell back to the whole warehouse. The
    scope is now refused as a whole and the offending name is reported, so a
    selection the system cannot honour never becomes a wider one.
    """
    with pytest.raises(GroundingRefused) as caught:
        _acl(session_id="s1", tables=["transactions", "; DROP TABLE users", "nope"])

    assert caught.value.ungrantable == ["; DROP TABLE users", "nope"]
    # Still the point of the original test: hostile input never reaches minting.
    assert "DROP TABLE" in caught.value.message


def test_a_fully_unknown_scope_is_refused_rather_than_widened() -> None:
    """Superseded by dms#5 - this asserted the defect the ticket was filed for.

    Falling back to the whole Space is what made "Grounded in 1 file" appear
    over a manifest holding all six demo tables. R-0005 still holds: refusing is
    only acceptable because the refusal names what to do next, and because
    grounding in *nothing* (below) still means no narrowing.
    """
    with pytest.raises(GroundingRefused) as caught:
        _acl(session_id="s1", tables=["nope", "also_nope"])

    assert set(caught.value.ungrantable) == {"nope", "also_nope"}


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
    """Superseded in part by dms#2: the Space id must now be a real one.

    This used to pass ``sp_q3``, which is not a seeded Space. That worked only
    because space_id was ignored entirely; an unknown Space now grants nothing.
    The assertion itself is unchanged - space_id still has to reach the ACL.
    """
    acl = _acl(session_id="s1", space_id=FINANCE, tables=["transactions"])
    assert acl.space_id == FINANCE
