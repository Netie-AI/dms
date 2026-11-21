"""FDE-ONBOARD-01 (dms#541): the onboarding SQL check and the ask path give one join verdict.

A proposed verified question is kept only when its SQL passes rule 16. The check must be the
ask path's own, not a copy: ``fde_ai.check_question_sql`` reads the Space ontology through
``ask_join_ontology``, which calls the resolver the ask path calls (``space_join_ontology``:
the derived ontology, else the steward-approved profile keys and links of #515).

Here the #515 fixture (a keyed child, a keyed parent, one clean link, a keyless table, a table
nothing links to) is ingested through the real route and profiled. Each SQL is posted through
``POST /v1/chat/ask`` (``assert_envelope_valid``, rows) and checked by the onboarding check,
before and after a steward approves the link, with ``DMS_ONTO_FROM_DATA`` off and on. The
verdicts must match: served <-> kept, ``unverified_join`` abstain <-> ``unverified_join`` drop.
"""

# ruff: noqa: F811 - the #515 fixtures are imported by name and requested as arguments

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_executor import fde_ai
from test_onto_profile_02 import (  # noqa: F401 - fixtures
    JOIN_Q,
    JOIN_TRUTH,
    Space,
    _reset_store,
    ask,
    join_sql,
    minter,
    oracle,
    rig,
    store,
)


def _tables(lake: Path, labels: list[str]) -> dict[str, set[str]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        out: dict[str, set[str]] = {}
        for label in labels:
            schema, _, name = label.partition(".")
            rows = con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ?",
                [schema, name],
            ).fetchall()
            out[label] = {str(r[0]) for r in rows} - {"_src", "_ingest_id"}
        return out
    finally:
        con.close()


def _cases(space: Space) -> dict[str, str]:
    return {
        "approved_link": space.join_sql,
        "unlinked_tables": (
            f"SELECT c.city, COUNT(*) AS n FROM {space.orders} o JOIN {space.customers} c "
            "ON o.customer_id = c.customer_id GROUP BY c.city ORDER BY c.city"
        ),
        "wrong_columns": join_sql(space.ships, space.orders, on="s.shipment_id = o.order_id"),
        "keyless_child": (
            f"SELECT o.status, COUNT(*) AS n FROM {space.lines} l JOIN {space.orders} o "
            "ON l.order_id = o.order_id GROUP BY o.status ORDER BY o.status"
        ),
        "one_table": space.lines_sql,
    }


def _verdicts(rig: Any, space: Space, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    tables = _tables(rig.lake, [space.orders, space.ships, space.lines, space.customers])
    out: dict[str, Any] = {}
    for name, sql in _cases(space).items():
        env = ask(rig, space.id, JOIN_Q, sql, "first_generate", monkeypatch)
        served = env["abstained"] is False
        ask_join = "unverified_join" in str(env.get("text") or "") + " ".join(
            str(a) for a in env.get("assumptions") or []
        )
        reason, _info = fde_ai.check_question_sql(
            sql, space_id=space.id, warehouse=rig.lake, tables=tables, back=lambda t: t
        )
        kept = reason is None
        check_join = bool(reason) and reason.startswith("unverified_join")
        out[name] = (served, kept, ask_join, check_join, reason)
    return out


@pytest.mark.parametrize("flag", ["", "1"])
def test_the_onboarding_check_and_the_ask_give_one_join_verdict(
    flag: str, rig: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    if flag:
        monkeypatch.setenv("DMS_ONTO_FROM_DATA", flag)
    space = Space(rig)
    assert oracle(rig.lake, space.join_sql) == JOIN_TRUTH

    before = _verdicts(rig, space, monkeypatch)
    for name, (served, kept, ask_join, check_join, reason) in before.items():
        if name == "one_table":
            continue
        # No approved item: every join is refused by both, as unverified_join.
        assert (served, kept) == (False, False), (name, reason)
        assert ask_join and check_join, (name, reason)

    space.approve_the_link()
    after = _verdicts(rig, space, monkeypatch)
    for name, (served, kept, ask_join, check_join, reason) in after.items():
        if name == "one_table":
            continue
        assert served == kept, (flag, name, served, reason)
        assert ask_join == check_join, (flag, name, reason)
    # Flag off: the approved link answers on both. Flag on: #515 clause 11 keeps the
    # parent behaviour (profile items are not read), so both refuse it, alike.
    expected = (True, True) if not flag else (False, False)
    assert after["approved_link"][:2] == expected, (flag, after["approved_link"])
    for name in ("unlinked_tables", "wrong_columns", "keyless_child"):
        assert after[name][:2] == (False, False), (name, after[name])


def test_the_onboarding_check_calls_the_ask_paths_resolver(
    rig: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One implementation: replacing the shared resolver changes both, not one."""
    import dms_executor.onto_profile as onto_profile

    space = Space(rig)
    space.approve_the_link()
    calls: list[str] = []
    real = onto_profile.space_join_ontology

    def _spy(space_id: str | None) -> Any:
        calls.append(str(space_id))
        return real(space_id)

    monkeypatch.setattr(onto_profile, "space_join_ontology", _spy)
    tables = _tables(rig.lake, [space.orders, space.ships])
    reason, _ = fde_ai.check_question_sql(
        space.join_sql, space_id=space.id, warehouse=rig.lake, tables=tables, back=lambda t: t
    )
    assert reason is None
    env = ask(rig, space.id, JOIN_Q, space.join_sql, "first_generate", monkeypatch)
    assert env["abstained"] is False
    assert calls.count(space.id) >= 2
