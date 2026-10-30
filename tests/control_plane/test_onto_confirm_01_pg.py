"""ONTO-CONFIRM-01 / dms#283, step 2 against a real Postgres: migration 0010 and
``PostgresOntologyStore`` measure methods.

What only a real database can prove: the BEFORE UPDATE trigger agrees with the
Python transition graph on all nine (old, new) pairs, the named CHECKs fire, FORCE
RLS isolates tenants, a viewer cannot write, two connections confirming at once
produce one transition, and a ledger callable that raises rolls back the row,
the audit row and the ledger pointer together. Runs wherever the control-plane
suite runs (R-0002: a skipped test is a failing test).
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import psycopg.errors
import pytest
from cortex_client.models import LedgerAppendResponse
from dms_core.control_plane.onto_store import (
    MEASURE_TRANSITIONS,
    ActorNotProvisioned,
    MeasureDraft,
    MeasureNameTaken,
    MeasureNotFound,
    MeasureTransitionError,
    OntologyStore,
    OntoMeasure,
    PostgresOntologyStore,
    SourceIdentity,
    measure_definition_hash,
    measure_transition_ok,
)
from dms_core.control_plane.session import set_tenant_context
from dms_executor import measure_confirm as mc
from dms_executor.measure_confirm import MeasureRefusal
from dms_executor.space_ontology import set_ontology_store

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_ingest_fixes_277 import _Rig  # noqa: E402
from test_onto_derive_01 import make_minter  # noqa: E402

STATES = ("proposed", "confirmed", "rejected")
GRAIN = "bronze.public_schools"


class _World:
    def __init__(self, dsn: str, tenants: dict[str, Any], conn: psycopg.Connection) -> None:
        self.dsn = dsn
        self.tenants = tenants
        self.actor: uuid.UUID = tenants["user"]
        self.alpha = PostgresOntologyStore(dsn, tenant_id=tenants["alpha"])
        self.beta = PostgresOntologyStore(dsn, tenant_id=tenants["beta"])
        self.space = self._space(conn, tenants["alpha"])
        self.version_id = self.alpha.reconnect(
            space_id=self.space,
            identity=SourceIdentity("postgres", "h", "d", "public"),
            fingerprint=f"fp-{uuid.uuid4().hex[:8]}",
            created_by=self.actor,
        ).id

    def _space(self, conn: psycopg.Connection, tenant: uuid.UUID) -> uuid.UUID:
        sid = uuid.uuid4()
        set_tenant_context(conn, tenant, role="admin")
        conn.execute(
            "INSERT INTO dms.spaces (id, tenant_id, name, created_by) VALUES (%s, %s, %s, %s)",
            (sid, tenant, f"confirm-{sid.hex[:8]}", self.actor),
        )
        conn.commit()
        return sid

    def propose(self, name: str = "schools_budget_sum", **kw: Any) -> OntoMeasure:
        base: dict[str, Any] = {
            "name": name,
            "grain": GRAIN,
            "aggregate": "sum",
            "column": "budget",
            "description": "Budget of one school row.",
            "source": "derived",
        }
        base.update(kw)
        inserted, skipped = self.alpha.add_measures(
            self.version_id,
            [MeasureDraft(**base)],
            [{"snapshot_id": "snap-1", "grain_rows": 4}],
            actor=self.actor,
            snapshot_id="snap-1",
            action="measure.propose",
        )
        assert len(inserted) == 1 and not skipped
        return inserted[0]

    def confirm(self, m: OntoMeasure, entry: str = "L-1", **kw: Any) -> OntoMeasure:
        new, changed = self.alpha.decide_measure(
            m.id,
            to_state="confirmed",
            expected_hash=m.definition_hash,
            actor=self.actor,
            ledger=lambda _p: entry,
            **kw,
        )
        assert changed
        return new

    def reject(self, m: OntoMeasure, entry: str = "L-r") -> OntoMeasure:
        new, changed = self.alpha.decide_measure(
            m.id,
            to_state="rejected",
            expected_hash=m.definition_hash,
            reason="not right",
            actor=self.actor,
            ledger=lambda _p: entry,
        )
        assert changed
        return new

    def raw(self, role: str = "steward") -> psycopg.Connection:
        conn = psycopg.connect(self.dsn)
        set_tenant_context(conn, self.tenants["alpha"], role=role)  # type: ignore[arg-type]
        return conn

    def count(self, sql: str, params: tuple[Any, ...]) -> int:
        with self.raw() as c:
            row = c.execute(sql, params).fetchone()
            assert row is not None
            return int(row[0])


@pytest.fixture()
def world(migrated_db: str, two_tenants: dict[str, Any], conn: psycopg.Connection) -> _World:
    return _World(migrated_db, two_tenants, conn)


def _constraint(exc: psycopg.Error) -> str:
    return str(exc.diag.constraint_name)


# --------------------------------------------------------------- the trigger


@pytest.mark.parametrize("old", STATES)
@pytest.mark.parametrize("new", STATES)
def test_trigger_agrees_with_python_on_all_nine_pairs(world: _World, old: str, new: str) -> None:
    m = world.propose(f"m_{old}_{new}")
    if old == "confirmed":
        m = world.confirm(m)
    elif old == "rejected":
        m = world.reject(m)
    legal = measure_transition_ok(old, new)
    assert legal is ((old, new) in MEASURE_TRANSITIONS)
    with world.raw() as c:
        try:
            c.execute(
                """
                UPDATE dms.onto_measure
                   SET state = %s, decided_by = %s, decided_at = now(),
                       ledger_entry_id = 'raw-1', decision_reason = 'raw'
                 WHERE id = %s
                """,
                (new, str(world.actor), str(m.id)),
            )
            allowed = True
        except psycopg.errors.RaiseException as exc:
            allowed = False
            assert "onto_measure_illegal_transition" in str(exc)
        c.rollback()
    assert allowed is legal, (old, new)


def test_confirmed_definition_is_frozen_in_the_database(world: _World) -> None:
    m = world.confirm(world.propose())
    other_version = world.alpha.reconnect(
        space_id=world.space,
        identity=SourceIdentity("postgres", "h", "d", "public"),
        fingerprint="fp-other",
    )
    cases = {
        "aggregate": ("avg", "aggregate"),
        "column_name": ("other_col", "column_name"),
        "grain": ("bronze.other", "grain"),
        "source": ("manual", "source"),
        "schema_fingerprint": ("0" * 8, "schema_fingerprint"),
        "version_id": (str(other_version.id), "version_id"),
    }
    for col, (value, _) in cases.items():
        with world.raw() as c:
            with pytest.raises(psycopg.errors.RaiseException, match="definition_frozen"):
                c.execute(
                    f"UPDATE dms.onto_measure SET {col} = %s WHERE id = %s",
                    (value, str(m.id)),
                )
            c.rollback()
    # Withdrawing keeps name, description, hash and evidence frozen.
    for col, value in (("name", "renamed_sneakily"), ("description", "different words")):
        with world.raw() as c:
            with pytest.raises(psycopg.errors.RaiseException, match="definition_frozen"):
                c.execute(
                    f"""
                    UPDATE dms.onto_measure SET state = 'rejected', {col} = %s,
                           decision_reason = 'r' WHERE id = %s
                    """,
                    (value, str(m.id)),
                )
            c.rollback()
    with world.raw() as c:
        with pytest.raises(psycopg.errors.RaiseException, match="definition_frozen"):
            c.execute(
                """
                UPDATE dms.onto_measure SET state = 'rejected', decision_reason = 'r',
                       evidence = '{"x": 1}'::jsonb WHERE id = %s
                """,
                (str(m.id),),
            )
        c.rollback()
    got = world.alpha.get_measure(world.space, m.id)
    assert got is not None and got.state == "confirmed" and got.name == m.name


def test_rename_at_confirm_is_the_one_allowed_wording_change(world: _World) -> None:
    m = world.propose()
    new = world.confirm(m, name="total_school_budget", description="Total budget of schools.")
    assert new.name == "total_school_budget"
    assert new.definition_hash == measure_definition_hash(
        "total_school_budget", GRAIN, "sum", "budget", "Total budget of schools."
    )
    got = world.alpha.get_measure(world.space, m.id)
    assert got == new


# ---------------------------------------------------------------- the CHECKs


def _insert_raw(world: _World, **over: Any) -> None:
    cols: dict[str, Any] = {
        "tenant_id": str(world.tenants["alpha"]),
        "version_id": str(world.version_id),
        "name": f"raw_{uuid.uuid4().hex[:8]}",
        "column_name": "budget",
        "aggregate": "sum",
        "grain": GRAIN,
        "description": "ok",
        "source": "derived",
        "definition_hash": "a" * 64,
        "schema_fingerprint": "fp",
    }
    cols.update(over)
    with world.raw() as c:
        try:
            c.execute(
                f"INSERT INTO dms.onto_measure ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))})",
                tuple(cols.values()),
            )
        finally:
            c.rollback()


@pytest.mark.parametrize(
    ("over", "constraint"),
    [
        ({"name": "Bad-Name"}, "onto_measure_name_ck"),
        ({"name": "x"}, "onto_measure_name_ck"),
        ({"aggregate": "median"}, "onto_measure_aggregate_ck"),
        ({"aggregate": "sum", "column_name": "*"}, "onto_measure_star_ck"),
        ({"source": "cortex"}, "onto_measure_source_ck"),
        ({"definition_hash": "XYZ"}, "onto_measure_hash_ck"),
        ({"description": "d" * 201}, "onto_measure_desc_ck"),
    ],
)
def test_insert_checks_are_named_and_fire(
    world: _World, over: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        _insert_raw(world, **over)
    assert _constraint(exc.value) == constraint


def test_count_star_is_allowed_and_a_good_row_inserts(world: _World) -> None:
    _insert_raw(world, aggregate="count", column_name="*")  # rolls back; must not raise


def test_confirmed_and_rejected_checks_name_the_missing_pieces(world: _World) -> None:
    m = world.propose()
    # The trigger lets proposed -> confirmed; the CHECK demands the ledger pointer.
    with world.raw() as c:
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            c.execute(
                """
                UPDATE dms.onto_measure SET state = 'confirmed', decided_by = %s,
                       decided_at = now() WHERE id = %s
                """,
                (str(world.actor), str(m.id)),
            )
        assert _constraint(exc.value) == "onto_measure_confirmed_ck"
        c.rollback()
        set_tenant_context(c, world.tenants["alpha"], role="steward")
        with pytest.raises(psycopg.errors.CheckViolation) as exc2:
            c.execute(
                """
                UPDATE dms.onto_measure SET state = 'rejected', decided_by = %s,
                       decided_at = now() WHERE id = %s
                """,
                (str(world.actor), str(m.id)),
            )
        assert _constraint(exc2.value) == "onto_measure_rejected_ck"
        c.rollback()
    got = world.alpha.get_measure(world.space, m.id)
    assert got is not None and got.state == "proposed"


def test_a_real_decide_satisfies_both_checks(world: _World) -> None:
    c = world.confirm(world.propose("a_conf"), entry="L-c")
    r = world.reject(world.propose("a_rej"), entry="L-r")
    assert (c.state, c.ledger_entry_id, c.decided_by) == ("confirmed", "L-c", world.actor)
    assert (r.state, r.decision_reason, r.decided_by) == ("rejected", "not right", world.actor)


# ------------------------------------------------------------ RLS and roles


def test_other_tenant_cannot_read_confirm_or_reject(world: _World) -> None:
    m = world.propose()
    assert world.beta.measures_for_space(world.space) == []
    assert world.beta.get_measure(world.space, m.id) is None
    for to_state, kw in (("confirmed", {}), ("rejected", {"reason": "mine now"})):
        with pytest.raises(MeasureNotFound):
            world.beta.decide_measure(
                m.id,
                to_state=to_state,
                expected_hash=m.definition_hash,
                actor=world.actor,
                ledger=lambda _p: "L-x",
                **kw,
            )
    with pytest.raises(KeyError):
        world.beta.add_measures(
            world.version_id,
            [MeasureDraft("beta_try", GRAIN, "sum", "budget", "d")],
            [{}],
            actor=None,
            snapshot_id=None,
            action="measure.propose",
        )
    with pytest.raises(MeasureNotFound):
        world.beta.record_refusal(m.id, failed=["x"], actor=None, from_state="proposed")
    got = world.alpha.get_measure(world.space, m.id)
    assert got is not None and got.state == "proposed"


def test_viewer_can_read_but_not_write(world: _World) -> None:
    m = world.propose()
    with world.raw("viewer") as c:
        row = c.execute("SELECT count(*) FROM dms.onto_measure WHERE id = %s", (str(m.id),))
        assert row.fetchone() == (1,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE dms.onto_measure SET description = 'x' WHERE id = %s", (str(m.id),))
        c.rollback()
        # SET LOCAL ROLE ended with the transaction: bind the viewer again.
        set_tenant_context(c, world.tenants["alpha"], role="viewer")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM dms.onto_measure WHERE id = %s", (str(m.id),))
        c.rollback()
    # The Postgres store's two read methods run as viewer and still work.
    assert [x.id for x in world.alpha.measures_for_space(world.space)] == [m.id]
    assert world.alpha.persistent is True


def test_get_measure_is_space_scoped(world: _World, conn: psycopg.Connection) -> None:
    m = world.propose()
    other_space = world._space(conn, world.tenants["alpha"])
    assert world.alpha.get_measure(other_space, m.id) is None
    assert world.alpha.measures_for_space(other_space) == []


# --------------------------------------------------- the single write path


def test_confirm_writes_row_audit_and_ledger_pointer_together(world: _World) -> None:
    m = world.propose()
    seen: list[dict[str, Any]] = []

    def ledger(payload: dict[str, Any]) -> str:
        seen.append(payload)
        return "ledger-entry-77"

    new, changed = world.alpha.decide_measure(
        m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=world.actor,
        ledger=ledger,
    )  # fmt: skip
    assert changed and new.ledger_entry_id == "ledger-entry-77"
    assert seen[0]["space_id"] == str(world.space) and seen[0]["snapshot_id"] == "snap-1"
    assert (
        world.count(
            "SELECT count(*) FROM dms.onto_audit WHERE action_type = 'measure.confirm' "
            "AND inputs->>'measure_id' = %s AND result->>'ledger_entry_id' = 'ledger-entry-77'",
            (str(m.id),),
        )
        == 1
    )
    assert (
        world.count(
            "SELECT count(*) FROM dms.ledger_ref WHERE cortex_entry_id = 'ledger-entry-77'", ()
        )
        == 1
    )
    again, changed2 = world.alpha.decide_measure(
        m.id, to_state="confirmed", expected_hash=new.definition_hash, actor=world.actor,
        ledger=ledger,
    )  # fmt: skip
    assert not changed2 and again == new and len(seen) == 1


def test_failing_ledger_rolls_back_row_audit_and_pointer(world: _World) -> None:
    m = world.propose()

    def boom(_p: dict[str, Any]) -> str:
        raise RuntimeError("cortex down")

    with pytest.raises(RuntimeError):
        world.alpha.decide_measure(
            m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=world.actor,
            ledger=boom,
        )  # fmt: skip
    got = world.alpha.get_measure(world.space, m.id)
    assert got is not None and got.state == "proposed" and got.ledger_entry_id is None
    assert (
        world.count(
            "SELECT count(*) FROM dms.onto_audit WHERE action_type = 'measure.confirm' "
            "AND inputs->>'measure_id' = %s",
            (str(m.id),),
        )
        == 0
    )


def test_an_unprovisioned_actor_is_refused_before_the_ledger_is_written(world: _World) -> None:
    """The decided_by FK would refuse the UPDATE AFTER the Cortex ledger recorded a
    "confirmed" entry, so the store looks the actor up first: the ledger is never called."""
    m = world.propose()
    calls: list[str] = []

    def ledger(_p: dict[str, Any]) -> str:
        calls.append("x")
        return "L-orphan"

    for to_state, kw in (("confirmed", {}), ("rejected", {"reason": "no"})):
        with pytest.raises(ActorNotProvisioned):
            world.alpha.decide_measure(
                m.id, to_state=to_state, expected_hash=m.definition_hash, actor=uuid.uuid4(),
                ledger=ledger, **kw,
            )  # fmt: skip
    assert calls == []
    got = world.alpha.get_measure(world.space, m.id)
    assert got is not None and got.state == "proposed"
    assert (
        world.count("SELECT count(*) FROM dms.ledger_ref WHERE cortex_entry_id = 'L-orphan'", ())
        == 0
    )
    audited = "SELECT count(*) FROM dms.onto_audit WHERE action_type = 'measure.confirm'"
    assert world.count(audited, ()) == 0


def test_a_name_taken_in_the_race_after_the_pre_check_is_still_name_taken(
    world: _World,
) -> None:
    """The backstop. The ledger runs outside the transaction, so another request can take
    the new name between the pre-check and the UPDATE; the unique index still refuses it
    and the row stays proposed."""
    m = world.propose("schools_budget_sum")

    def ledger(_p: dict[str, Any]) -> str:
        world.propose("raced_name", aggregate="max")  # commits on its own connection
        return "L-race"

    with pytest.raises(MeasureNameTaken):
        world.alpha.decide_measure(
            m.id, to_state="confirmed", expected_hash=m.definition_hash, name="raced_name",
            actor=world.actor, ledger=ledger,
        )  # fmt: skip
    got = world.alpha.get_measure(world.space, m.id)
    assert got is not None and got.state == "proposed" and got.name == "schools_budget_sum"


def test_stale_hash_and_illegal_move_leave_nothing_behind(world: _World) -> None:
    m = world.propose()
    calls: list[str] = []

    def ledger(_p: dict[str, Any]) -> str:
        calls.append("x")
        return "L-1"

    with pytest.raises(MeasureTransitionError) as exc:
        world.alpha.decide_measure(
            m.id, to_state="confirmed", expected_hash="0" * 64, actor=world.actor, ledger=ledger
        )
    assert exc.value.code == "stale_definition" and exc.value.current == m
    c = world.confirm(m)
    with pytest.raises(MeasureTransitionError) as exc2:
        world.alpha.decide_measure(
            c.id, to_state="proposed", expected_hash=c.definition_hash, actor=world.actor,
            ledger=ledger,
        )  # fmt: skip
    assert exc2.value.code == "illegal_transition" and calls == []


def test_rename_collision_is_name_taken_and_the_ledger_is_never_written(world: _World) -> None:
    a = world.propose("schools_budget_sum")
    b = world.propose("schools_budget_avg", aggregate="avg")
    calls: list[dict[str, Any]] = []

    def ledger(payload: dict[str, Any]) -> str:
        calls.append(payload)
        return "L-dup"

    with pytest.raises(MeasureNameTaken):
        world.alpha.decide_measure(
            b.id, to_state="confirmed", expected_hash=b.definition_hash, name=a.name,
            actor=world.actor, ledger=ledger,
        )  # fmt: skip
    assert calls == []
    got = world.alpha.get_measure(world.space, b.id)
    assert got is not None and got.state == "proposed" and got.name == "schools_budget_avg"
    assert (
        world.count("SELECT count(*) FROM dms.ledger_ref WHERE cortex_entry_id = 'L-dup'", ()) == 0
    )


def test_a_whitespace_ledger_entry_id_is_no_entry_id(world: _World) -> None:
    m = world.propose()
    for blank in ("", "   ", "\t\n"):
        with pytest.raises(ValueError, match="no entry id"):
            world.alpha.decide_measure(
                m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=world.actor,
                ledger=lambda _p, b=blank: b,
            )  # fmt: skip
    got = world.alpha.get_measure(world.space, m.id)
    assert got is not None and got.state == "proposed"


def test_the_database_itself_refuses_a_whitespace_ledger_entry_id(world: _World) -> None:
    """The CHECK is the second line behind the store: btrim, so blanks do not slip past."""
    m = world.propose()
    with world.alpha._conn() as conn:  # noqa: SLF001
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            conn.execute(
                "UPDATE dms.onto_measure SET state = 'confirmed', decided_by = %s, "
                "decided_at = now(), ledger_entry_id = '  ' WHERE id = %s",
                (str(world.actor), str(m.id)),
            )
        assert exc.value.diag.constraint_name == "onto_measure_confirmed_ck"


def test_name_burns_per_version_and_a_rejected_row_is_not_resurrected(world: _World) -> None:
    m = world.propose()
    r = world.reject(m)
    inserted, skipped = world.alpha.add_measures(
        world.version_id,
        [MeasureDraft(m.name, GRAIN, "sum", "budget", "again")],
        [{}],
        actor=world.actor,
        snapshot_id=None,
        action="measure.propose",
    )
    assert inserted == [] and [d.name for d in skipped] == [m.name]
    got = world.alpha.get_measure(world.space, m.id)
    assert got == r and got.description == "Budget of one school row."
    back = world.confirm(r, entry="L-back")
    assert back.state == "confirmed" and back.ledger_entry_id == "L-back"


def test_concurrent_confirms_yield_one_transition(world: _World) -> None:
    m = world.propose()
    calls: list[str] = []
    outcomes: list[bool] = []
    errors: list[BaseException] = []
    start = threading.Barrier(2)

    def ledger(_p: dict[str, Any]) -> str:
        calls.append("x")
        time.sleep(0.4)  # hold the row lock long enough for the other side to queue
        return "L-race"

    def worker() -> None:
        try:
            start.wait()
            _row, changed = world.alpha.decide_measure(
                m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=world.actor,
                ledger=ledger,
            )  # fmt: skip
            outcomes.append(changed)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert sorted(outcomes) == [False, True]
    assert calls == ["x"]
    assert (
        world.count(
            "SELECT count(*) FROM dms.onto_audit WHERE action_type = 'measure.confirm' "
            "AND inputs->>'measure_id' = %s",
            (str(m.id),),
        )
        == 1
    )


def test_record_refusal_writes_one_audit_row_in_its_own_transaction(world: _World) -> None:
    m = world.propose()
    world.alpha.record_refusal(
        m.id, failed=["column_all_null", "name_taken"], actor=world.actor, from_state="proposed"
    )
    assert (
        world.count(
            "SELECT count(*) FROM dms.onto_audit WHERE action_type = 'measure.confirm_refused' "
            "AND inputs->>'measure_id' = %s AND result->'failed' = '[\"column_all_null\", "
            "\"name_taken\"]'::jsonb",
            (str(m.id),),
        )
        == 1
    )
    got = world.alpha.get_measure(world.space, m.id)
    assert got == m


def test_measures_for_space_filters_and_orders(world: _World) -> None:
    a = world.propose("b_second")
    b = world.propose("a_first")
    world.confirm(a)
    drift = world.alpha.reconnect(
        space_id=world.space,
        identity=SourceIdentity("postgres", "h", "d", "public"),
        fingerprint="fp-drifted",
    )
    assert drift.status == "proposed"
    world.alpha.add_measures(
        drift.id, [MeasureDraft("drifted_one", GRAIN, "count", "*", "d")], [{}],
        actor=None, snapshot_id=None, action="measure.create",
    )  # fmt: skip
    names = [m.name for m in world.alpha.measures_for_space(world.space)]
    assert names == ["a_first", "b_second", "drifted_one"]
    assert [m.name for m in world.alpha.measures_for_space(world.space, active_only=True)] == [
        "a_first",
        "b_second",
    ]
    conf = world.alpha.measures_for_space(world.space, states=("confirmed",), active_only=True)
    assert [m.name for m in conf] == ["b_second"]
    assert conf[0].version_status == "active" and conf[0].version_fingerprint
    assert b.id != a.id


# ------------------------------------------------- the steward service on Postgres
#
# measure_confirm is store-agnostic. These run it on PostgresOntologyStore under RLS with
# a real lake (the typed schools rig): what the in-memory rig cannot show is the JSONB
# evidence round trip, the ledger pointer row, the trigger agreeing with the service,
# tenant isolation, and the row lock under a race.


class _PgLedger:
    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []
        self.append_error: Exception | None = None

    def append(self, *, event_type: str, payload: dict[str, Any], actor: str | None = None) -> Any:
        if self.append_error is not None:
            raise self.append_error
        self.appended.append({"event_type": event_type, "payload": payload, "actor": actor})
        n = len(self.appended)
        return LedgerAppendResponse(entry_id=f"pg_led_{uuid.uuid4().hex[:8]}_{n}", hash=f"h{n}x")

    def verify(self) -> Any:
        return SimpleNamespace(ok=True)


class _Svc:
    def __init__(self, world: _World, rig: _Rig, ledger: _PgLedger) -> None:
        self.world = world
        self.rig = rig
        self.ledger = ledger
        self.space = str(world.space)

    def propose(self) -> dict[str, dict[str, Any]]:
        out = mc.propose_measures(
            self.space, actor=self.world.actor, warehouse=self.rig.lake, store=self.world.alpha
        )
        return {m["name"]: m for m in out["created"]}

    def confirm(self, view: dict[str, Any], **kw: Any) -> dict[str, Any]:
        return mc.confirm_measure(
            self.space,
            uuid.UUID(view["id"]),
            definition_hash=kw.pop("definition_hash", view["definition_hash"]),
            actor=kw.pop("actor", self.world.actor),
            warehouse=self.rig.lake,
            store=kw.pop("store", self.world.alpha),
            ledger_append=self.ledger.append,
            ledger_verify=self.ledger.verify,
            **kw,
        )

    def reject(self, view: dict[str, Any], reason: str = "no") -> dict[str, Any]:
        return mc.reject_measure(
            self.space,
            uuid.UUID(view["id"]),
            reason=reason,
            actor=self.world.actor,
            store=self.world.alpha,
            ledger_append=self.ledger.append,
            ledger_verify=self.ledger.verify,
        )

    def audit(self, action: str) -> int:
        return self.world.count(
            "SELECT count(*) FROM dms.onto_audit WHERE action_type = %s "
            "AND version_id = %s",
            (action, str(self.world.version_id)),
        )

    def state(self, view: dict[str, Any]) -> str:
        got = self.world.alpha.get_measure(self.world.space, uuid.UUID(view["id"]))
        assert got is not None
        return got.state


@pytest.fixture()
def svc(
    world: _World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_Svc]:
    mem = OntologyStore()
    prev = set_ontology_store(mem)
    try:
        rig = _Rig(tmp_path, monkeypatch, make_minter(monkeypatch))
        mem_space = rig.space("pg service")
        rig.ingest(mem_space)  # lands the tables in the lake and derives into the memory store
        v = mem.active_for_space(uuid.UUID(mem_space))[0]
        snap = mem.latest_snapshot(v.id)
        assert snap is not None and snap.verified
        # The same measurement, stored under RLS for tenant alpha's Space.
        world.alpha.record_snapshot(
            world.version_id, body=snap.body, violations=[], verified=True, actor=world.actor
        )
        yield _Svc(world, rig, _PgLedger())
    finally:
        set_ontology_store(prev)


def test_service_propose_confirm_withdraw_and_reconfirm_on_postgres(svc: _Svc) -> None:
    views = svc.propose()
    assert set(views) == {"schools_budget_sum", "schools_count", "districts_count"}
    ev = views["schools_budget_sum"]["evidence"]
    assert Decimal(ev["probe_value"]) == Decimal("360.75")  # the JSONB round trip kept the figure
    assert svc.audit("measure.propose") == 1 and svc.ledger.appended == []

    out = svc.confirm(views["schools_budget_sum"], name="school_budget_total")
    m = out["measure"]
    assert m["state"] == "confirmed" and m["name"] == "school_budget_total"
    assert m["definition_hash"] == measure_definition_hash(
        "school_budget_total", GRAIN, "sum", "budget", m["description"]
    )
    assert m["decided_by"] == str(svc.world.actor) and m["answers_certified"] is True
    (call,) = svc.ledger.appended
    assert call["payload"]["name"] == "school_budget_total"
    entry = m["ledger_entry_id"]
    assert (
        svc.world.count(
            "SELECT count(*) FROM dms.ledger_ref WHERE cortex_entry_id = %s", (entry,)
        )
        == 1
    )
    assert svc.audit("measure.confirm") == 1

    # the list reads it back from the database, with the persisted flag
    listed = mc.measures_view(svc.space, role="steward", can_decide=True, store=svc.world.alpha)
    assert listed["persisted"] is True and listed["counts"]["confirmed"] == 1

    # withdraw, then confirm again: rejected -> confirmed is allowed by the trigger
    withdrawn = svc.reject(m, "wrong definition")["measure"]
    assert withdrawn["state"] == "rejected" and withdrawn["decision_reason"] == "wrong definition"
    back = svc.confirm(withdrawn)["measure"]
    assert back["state"] == "confirmed" and back["decision_reason"] is None
    assert [e["event_type"] for e in svc.ledger.appended] == [
        "ontology.measure.confirmed",
        "ontology.measure.rejected",
        "ontology.measure.confirmed",
    ]


def test_service_ledger_failure_rolls_back_everything_on_postgres(svc: _Svc) -> None:
    view = svc.propose()["schools_count"]
    svc.ledger.append_error = RuntimeError("cortex down")
    with pytest.raises(MeasureRefusal) as exc:
        svc.confirm(view)
    assert (exc.value.status, exc.value.code) == (503, "ledger_append_failed")
    assert svc.state(view) == "proposed"
    assert svc.audit("measure.confirm") == 0
    assert svc.world.count("SELECT count(*) FROM dms.ledger_ref", ()) == 0
    svc.ledger.append_error = None
    assert svc.confirm(view)["measure"]["state"] == "confirmed"


def test_service_refusal_is_audited_and_changes_nothing_on_postgres(svc: _Svc) -> None:
    view = svc.propose()["schools_count"]
    with pytest.raises(MeasureRefusal) as exc:
        svc.confirm(view, definition_hash="0" * 64)
    assert (exc.value.status, exc.value.code) == (409, "stale_definition")
    assert svc.state(view) == "proposed" and svc.ledger.appended == []
    assert svc.audit("measure.confirm_refused") == 1


def test_service_actor_without_a_user_row_leaves_the_measure_proposed(svc: _Svc) -> None:
    """dms.users has no row for this actor: the foreign key on decided_by refuses the UPDATE.
    The API layer maps this to 503 actor_not_provisioned; here it is the raw database error,
    and nothing about the measure changed."""
    view = svc.propose()["schools_count"]
    with pytest.raises(MeasureRefusal) as exc:
        svc.confirm(view, actor=uuid.uuid4())
    assert (exc.value.status, exc.value.code) == (503, "actor_not_provisioned")
    assert svc.state(view) == "proposed" and svc.audit("measure.confirm") == 0
    assert svc.world.count("SELECT count(*) FROM dms.ledger_ref", ()) == 0
    assert svc.ledger.appended == []  # the Cortex ledger was never written


def test_the_api_layer_recognises_the_real_foreign_key_error_for_every_steward_write(
    svc: _Svc,
) -> None:
    """``actor_not_provisioned`` is decided from the database's own error (SQLSTATE 23503 and
    the constraint it names), so this asserts it against what Postgres really raises on each
    write a steward makes, not against a stand-in."""
    from dms_api.wiring import _is_actor_fk

    stranger = uuid.uuid4()
    view = svc.propose()["schools_count"]
    other = svc.propose()  # nothing new: proposing is idempotent
    assert other == {}
    # confirm and reject look the actor up first (and name it), so only the manual create
    # still reaches the foreign key itself
    with pytest.raises(MeasureRefusal) as confirm:
        svc.confirm(view, actor=stranger)
    with pytest.raises(MeasureRefusal) as reject:
        mc.reject_measure(
            svc.space, uuid.UUID(view["id"]), reason="x", actor=stranger,
            store=svc.world.alpha, ledger_append=svc.ledger.append,
            ledger_verify=svc.ledger.verify,
        )  # fmt: skip
    assert [e.value.status for e in (confirm, reject)] == [503, 503]
    assert [e.value.code for e in (confirm, reject)] == ["actor_not_provisioned"] * 2
    assert svc.ledger.appended == []
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as create:
        mc.create_measure(
            svc.space, name="stranger_count", grain=GRAIN, aggregate="count", column="*",
            description="Schools.", actor=stranger, warehouse=svc.rig.lake,
            store=svc.world.alpha,
        )  # fmt: skip
    assert _is_actor_fk(create.value), create.value.diag.constraint_name
    assert _is_actor_fk(ValueError("no")) is False
    assert svc.state(view) == "proposed"


def test_the_ontology_view_carries_the_compact_measures_from_postgres(svc: _Svc) -> None:
    from dms_executor.space_ontology import space_ontology_views

    views = svc.propose()
    svc.confirm(views["schools_count"])
    svc.reject(views["districts_count"], "later")
    [onto] = space_ontology_views(svc.space, store=svc.world.alpha)
    got = {m["name"]: m for m in onto["measures"]}
    assert {n: (m["state"], m["answers_certified"]) for n, m in got.items()} == {
        "schools_count": ("confirmed", True),
        "districts_count": ("rejected", False),
        "schools_budget_sum": ("proposed", False),
    }
    assert all(set(m) == {"id", "name", "state", "answers_certified"} for m in got.values())
    assert "confirmed" in onto["measures_note"] and "no measures are derived" not in onto[
        "measures_note"
    ]


def test_service_cannot_see_or_decide_another_tenants_measures(svc: _Svc) -> None:
    view = svc.propose()["schools_count"]
    beta = svc.world.beta
    listed = mc.measures_view(svc.space, role="steward", can_decide=True, store=beta)
    assert listed["measures"] == [] and listed["versions"] == []
    with pytest.raises(MeasureRefusal) as exc:
        svc.confirm(view, store=beta)
    assert (exc.value.status, exc.value.code) == (404, "measure_not_found")
    with pytest.raises(MeasureRefusal) as exc2:
        mc.reject_measure(
            svc.space, uuid.UUID(view["id"]), reason="x", actor=svc.world.actor, store=beta,
            ledger_append=svc.ledger.append, ledger_verify=svc.ledger.verify,
        )  # fmt: skip
    assert exc2.value.code == "measure_not_found"
    assert svc.state(view) == "proposed" and svc.ledger.appended == []


def test_service_two_confirms_racing_on_postgres_make_one_transition(
    svc: _Svc, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = svc.propose()["schools_count"]
    gate = threading.Barrier(2)
    real = mc.measure_evidence

    def synced(*a: Any, **k: Any) -> Any:
        out = real(*a, **k)
        gate.wait(timeout=30)  # both pass every check before either decides
        return out

    monkeypatch.setattr(mc, "measure_evidence", synced)
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(svc.confirm(view))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert sorted(r["already"] for r in results) == [False, True]
    assert len(svc.ledger.appended) == 1 and svc.audit("measure.confirm") == 1


def test_confirmed_measures_load_for_the_ask_from_postgres(svc: _Svc) -> None:
    """Step 6 on a real database: the ask path reads confirmed rows through the viewer role,
    the stored fields recompute to the stored hash after the round trip, and the confirm-time
    column type (JSONB evidence) is the type the lake reports."""
    from dms_executor import generative_ask as ga
    from dms_executor.measure_use import measure_exclusions, prune_unusable_measures
    from dms_executor.space_ontology import load_space_ontology

    views = svc.propose()
    svc.confirm(views["schools_budget_sum"])
    svc.confirm(views["schools_count"])
    svc.reject(views["districts_count"], "later")

    onto = load_space_ontology(svc.space, svc.world.alpha)
    assert onto is not None
    assert set(onto.measures) == {"schools_budget_sum", "schools_count"}  # no proposed, no rejected
    assert measure_exclusions(onto) == {}  # the hash recomputed from what Postgres returned
    prov = onto.measures["schools_budget_sum"].provenance
    assert prov is not None
    assert prov.persisted is True and prov.columns_checked is False
    assert prov.definition_hash == views["schools_budget_sum"]["definition_hash"]
    assert prov.column_type == "DECIMAL(10,2)" and prov.ledger_entry_id.startswith("pg_led_")
    assert prov.version_id == str(svc.world.version_id) and prov.decided_at

    loaded = ga.load_verified_ontology(svc.rig.lake, onto)
    assert loaded is not None
    violations = list(ga.cached_verify_violations(loaded) or [])
    allowed = {GRAIN, "bronze.public_districts"}
    assert prune_unusable_measures([loaded], violations, svc.rig.lake, allowed) == {}
    assert all(m.provenance and m.provenance.columns_checked for m in loaded.measures.values())

    # RLS: another tenant's store sees neither the Space's versions nor its measures
    assert load_space_ontology(svc.space, svc.world.beta) is None

    # a withdrawn measure leaves the very next load
    svc.reject(views["schools_count"], "wrong")
    again = load_space_ontology(svc.space, svc.world.alpha)
    assert again is not None and set(again.measures) == {"schools_budget_sum"}
