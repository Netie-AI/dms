"""ONTO-CONFIRM-01 / dms#283: migration 0007, the core measure store (step 2) and the steward
service ``measure_confirm`` (step 4, last section of this file).

No Postgres here (the PG twin is tests/control_plane/test_onto_confirm_01_pg.py).
Migration 0007 is run against a recording Alembic ``op``; the in-memory store
is exercised through the same ``decide_measure`` contract the Postgres store
implements. Nothing here is on the answer path, so there is no envelope
assertion; the envelope-level tests arrive with the steps that emit.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import duckdb
import pytest
from cortex_client.models import LedgerAppendResponse
from dms_core.control_plane.onto_store import (
    MEASURE_TRANSITIONS,
    MeasureDraft,
    MeasureNameTaken,
    MeasureNotFound,
    MeasureTransitionError,
    OntologySnapshot,
    OntologyStore,
    OntologyVersion,
    OntoMeasure,
    SourceIdentity,
    measure_definition_hash,
    measure_transition_ok,
)
from dms_executor import generative_ask as ga
from dms_executor import measure_confirm as mc
from dms_executor.measure_confirm import MEASURE_REFUSAL_COPY, MeasureRefusal
from dms_executor.measure_spec import NAME_RE, _demo_reserved, description_failures
from dms_executor.ontology import MeasureProvenance, measure_expression
from dms_executor.space_ontology import load_space_ontology, set_ontology_store
from test_ingest_fixes_277 import DATA, _oracle, _Rig
from test_onto_derive_01 import make_minter

ROOT = Path(__file__).resolve().parents[1]
REV_0004 = ROOT / "alembic" / "versions" / "0004_ontology_store.py"
REV_0007 = ROOT / "alembic" / "versions" / "0007_onto_measure_confirm.py"
STORE_PATH = ROOT / "packages" / "core" / "dms_core" / "control_plane" / "onto_store.py"

ALL_STATES = ("proposed", "confirmed", "rejected")
ACTOR = uuid4()
GRAIN = "bronze.public_schools"


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(str(sql))


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- migration


def test_migration_0007_chain_constraints_trigger_and_no_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load(REV_0007, "rev0007_onto_measure_confirm")
    assert mod.revision == "0007_onto_measure_confirm"
    assert mod.down_revision == "0006_onto_snapshot"
    rec = _Recorder()
    monkeypatch.setattr(mod, "op", rec)
    mod.upgrade()
    sql = "\n".join(rec.statements)

    for name in (
        "onto_measure_aggregate_ck",
        "onto_measure_name_ck",
        "onto_measure_star_ck",
        "onto_measure_source_ck",
        "onto_measure_hash_ck",
        "onto_measure_desc_ck",
        "onto_measure_confirmed_ck",
        "onto_measure_rejected_ck",
    ):
        assert f"ADD CONSTRAINT {name}" in sql, name
    assert "CREATE INDEX onto_measure_by_version" in sql
    assert "(tenant_id, version_id, state)" in sql
    assert "CREATE FUNCTION dms.onto_measure_guard()" in sql
    assert re.search(
        r"CREATE TRIGGER onto_measure_guard\s+BEFORE UPDATE ON dms\.onto_measure", sql
    )
    for col in (
        "description",
        "source",
        "definition_hash",
        "schema_fingerprint",
        "evidence",
        "decided_by",
        "decided_at",
        "decision_reason",
        "ledger_entry_id",
    ):
        assert re.search(rf"ADD COLUMN {col}\b", sql), col

    # ALTER/CREATE only: no new table, no GRANT, no POLICY, no RLS toggles.
    for forbidden in ("GRANT", "POLICY", "CREATE TABLE", "DROP TABLE", "ROW LEVEL SECURITY"):
        assert forbidden not in sql.upper(), forbidden

    down = _Recorder()
    monkeypatch.setattr(mod, "op", down)
    mod.downgrade()
    dsql = "\n".join(down.statements)
    assert "DROP TRIGGER IF EXISTS onto_measure_guard" in dsql
    assert "DROP FUNCTION IF EXISTS dms.onto_measure_guard" in dsql
    assert "DROP CONSTRAINT IF EXISTS onto_measure_confirmed_ck" in dsql
    assert "DROP COLUMN IF EXISTS definition_hash" in dsql


def test_migration_0004_text_untouched_by_0007() -> None:
    """0004's onto_measure CREATE TABLE is pinned by test_onto_store_01; 0007 never edits it."""
    text = REV_0004.read_text()
    assert "definition_hash" not in text and "ledger_entry_id" not in text
    assert "CREATE TABLE dms.onto_measure (" in text


def test_core_store_has_no_sql_parser_and_no_executor_import() -> None:
    text = STORE_PATH.read_text()
    assert "sqlglot" not in text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("dms_executor"), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("dms_executor"), alias.name


def test_decide_measure_has_only_the_expected_callers() -> None:
    """The single write path: only the store and the (later) measure_confirm module call it."""
    allowed = {
        "packages/core/dms_core/control_plane/onto_store.py",
        "packages/executor/dms_executor/measure_confirm.py",
        "packages/executor/dms_executor/space_ontology.py",  # the Protocol declaration
    }
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel in allowed:
                continue
            assert "decide_measure(" not in path.read_text(), rel


# --------------------------------------------------------------- the graph


def test_transition_graph_is_exactly_the_four_legal_moves() -> None:
    legal = {
        ("proposed", "confirmed"),
        ("proposed", "rejected"),
        ("confirmed", "rejected"),
        ("rejected", "confirmed"),
    }
    assert set(MEASURE_TRANSITIONS) == legal
    for old in ALL_STATES:
        for new in ALL_STATES:
            assert measure_transition_ok(old, new) is ((old, new) in legal), (old, new)


# ----------------------------------------------------------- memory store


def _store() -> tuple[OntologyStore, UUID, UUID]:
    store = OntologyStore()
    space = uuid4()
    ident = SourceIdentity("postgres", "h", "d", "public")
    version = store.reconnect(space_id=space, identity=ident, fingerprint="fp-1")
    return store, space, version.id


def _draft(name: str = "schools_budget_sum", **kw: Any) -> MeasureDraft:
    base: dict[str, Any] = {
        "name": name,
        "grain": GRAIN,
        "aggregate": "sum",
        "column": "budget",
        "description": "Budget of one school row.",
        "source": "derived",
    }
    base.update(kw)
    return MeasureDraft(**base)


def _propose(store: OntologyStore, vid: UUID, draft: MeasureDraft | None = None) -> OntoMeasure:
    inserted, skipped = store.add_measures(
        vid,
        [draft or _draft()],
        [{"snapshot_id": "snap-1", "grain_rows": 4}],
        actor=ACTOR,
        snapshot_id="snap-1",
        action="measure.propose",
    )
    assert len(inserted) == 1 and not skipped
    return inserted[0]


def _ledger(box: list[dict[str, Any]], entry: str = "L-1") -> Any:
    def call(payload: dict[str, Any]) -> str:
        box.append(payload)
        return entry

    return call


def test_persistent_is_false_in_memory() -> None:
    assert OntologyStore().persistent is False


def test_add_measures_proposes_hashes_and_skips_duplicates() -> None:
    store, space, vid = _store()
    m = _propose(store, vid)
    assert m.state == "proposed"
    assert m.decided_by is None and m.ledger_entry_id is None
    assert m.schema_fingerprint == "fp-1"
    assert m.definition_hash == measure_definition_hash(
        "schools_budget_sum", GRAIN, "sum", "budget", "Budget of one school row."
    )
    # Same name again: skipped, the row is not touched.
    inserted, skipped = store.add_measures(
        vid,
        [_draft(description="Different words.")],
        [{}],
        actor=ACTOR,
        snapshot_id=None,
        action="measure.create",
    )
    assert inserted == [] and [d.name for d in skipped] == ["schools_budget_sum"]
    assert [x.description for x in store.measures_for_space(space)] == ["Budget of one school row."]
    actions = [a.action_type for a in store._audit if a.action_type.startswith("measure.")]
    assert actions == ["measure.propose", "measure.create"]


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "Bad-Name"},
        {"name": "x"},
        {"aggregate": "median"},
        {"aggregate": "sum", "column": "*"},
        {"source": "cortex"},
        {"description": "d" * 201},
        {"column": ""},
    ],
)
def test_store_rechecks_the_shape_itself(bad: dict[str, Any]) -> None:
    store, space, vid = _store()
    with pytest.raises(ValueError):
        store.add_measures(
            vid, [_draft(**bad)], [{}], actor=ACTOR, snapshot_id=None, action="measure.propose"
        )
    assert store.measures_for_space(space) == []


def test_add_measures_rejects_unknown_action_version_and_evidence_length() -> None:
    store, _space, vid = _store()
    with pytest.raises(ValueError):
        store.add_measures(vid, [_draft()], [{}], actor=None, snapshot_id=None, action="x")
    with pytest.raises(ValueError):
        store.add_measures(
            vid, [_draft()], [], actor=None, snapshot_id=None, action="measure.propose"
        )
    with pytest.raises(KeyError):
        store.add_measures(
            uuid4(), [_draft()], [{}], actor=None, snapshot_id=None, action="measure.propose"
        )


def test_confirm_flow_ledger_first_and_audit() -> None:
    store, space, vid = _store()
    m = _propose(store, vid)
    calls: list[dict[str, Any]] = []
    new, changed = store.decide_measure(
        m.id,
        to_state="confirmed",
        expected_hash=m.definition_hash,
        actor=ACTOR,
        ledger=_ledger(calls, "L-42"),
    )
    assert changed and new.state == "confirmed"
    assert new.ledger_entry_id == "L-42" and new.decided_by == ACTOR and new.decided_at
    assert new.definition_hash == m.definition_hash  # no rename: same hash
    assert len(calls) == 1
    payload = calls[0]
    assert payload["space_id"] == str(space) and payload["measure_id"] == str(m.id)
    assert payload["from_state"] == "proposed" and payload["to_state"] == "confirmed"
    assert payload["snapshot_id"] == "snap-1"
    # Pointers only: no row values, no free text.
    assert set(payload) == {
        "space_id", "version_id", "measure_id", "name", "grain", "aggregate", "column",
        "definition_hash", "schema_fingerprint", "snapshot_id", "from_state", "to_state",
    }  # fmt: skip
    audit = [a for a in store._audit if a.action_type == "measure.confirm"]
    assert len(audit) == 1
    assert audit[0].result == {"to_state": "confirmed", "ledger_entry_id": "L-42"}
    assert audit[0].inputs["expected_hash"] == m.definition_hash


def test_same_state_is_a_no_op_with_no_ledger_and_no_audit() -> None:
    store, _space, vid = _store()
    m = _propose(store, vid)
    box: list[dict[str, Any]] = []
    first, changed = store.decide_measure(
        m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=ACTOR,
        ledger=_ledger(box, "L-1"),
    )  # fmt: skip
    assert changed
    again, changed2 = store.decide_measure(
        m.id, to_state="confirmed", expected_hash=first.definition_hash, actor=ACTOR,
        ledger=_ledger(box, "L-2"),
    )  # fmt: skip
    assert not changed2 and again == first
    assert len(box) == 1
    assert len([a for a in store._audit if a.action_type == "measure.confirm"]) == 1


def test_confirmed_definition_cannot_be_reworded_by_a_second_confirm() -> None:
    store, _space, vid = _store()
    m = _propose(store, vid)
    box: list[dict[str, Any]] = []
    first, _ = store.decide_measure(
        m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=ACTOR,
        ledger=_ledger(box),
    )
    with pytest.raises(MeasureTransitionError) as exc:
        store.decide_measure(
            m.id, to_state="confirmed", expected_hash=first.definition_hash, name="other_name",
            actor=ACTOR, ledger=_ledger(box),
        )  # fmt: skip
    assert exc.value.code == "definition_frozen"
    assert len(box) == 1


def test_rename_at_confirm_changes_the_hash_and_keeps_the_spec() -> None:
    store, space, vid = _store()
    m = _propose(store, vid)
    new, changed = store.decide_measure(
        m.id,
        to_state="confirmed",
        expected_hash=m.definition_hash,
        name="total_school_budget",
        description="Total budget across all schools.",
        actor=ACTOR,
        ledger=_ledger([]),
    )
    assert changed and new.name == "total_school_budget"
    assert new.definition_hash != m.definition_hash
    assert new.definition_hash == measure_definition_hash(
        "total_school_budget", GRAIN, "sum", "budget", "Total budget across all schools."
    )
    assert (new.aggregate, new.column_name, new.grain) == ("sum", "budget", GRAIN)
    assert [x.name for x in store.measures_for_space(space)] == ["total_school_budget"]


def test_rename_to_a_taken_name_is_refused_and_unledgered() -> None:
    store, _space, vid = _store()
    a = _propose(store, vid, _draft("schools_budget_sum"))
    b = _propose(store, vid, _draft("schools_budget_avg", aggregate="avg"))
    box: list[dict[str, Any]] = []
    with pytest.raises(MeasureNameTaken):
        store.decide_measure(
            b.id, to_state="confirmed", expected_hash=b.definition_hash, name=a.name,
            actor=ACTOR, ledger=_ledger(box),
        )  # fmt: skip
    assert box == []


def test_a_bad_rename_or_empty_description_never_reaches_the_ledger() -> None:
    store, _space, vid = _store()
    m = _propose(store, vid, _draft(description=""))
    box: list[dict[str, Any]] = []
    with pytest.raises(ValueError):  # confirmed needs a description
        store.decide_measure(
            m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=ACTOR,
            ledger=_ledger(box),
        )  # fmt: skip
    with pytest.raises(ValueError):
        store.decide_measure(
            m.id, to_state="confirmed", expected_hash=m.definition_hash, name="Bad Name",
            description="ok", actor=ACTOR, ledger=_ledger(box),
        )  # fmt: skip
    assert box == []
    assert store.get_measure(next(iter(store._versions)).space_id, m.id) == m


def test_ledger_callable_raising_leaves_state_unchanged() -> None:
    store, space, vid = _store()
    m = _propose(store, vid)
    before_audit = len(store._audit)

    def boom(_payload: dict[str, Any]) -> str:
        raise RuntimeError("ledger down")

    with pytest.raises(RuntimeError):
        store.decide_measure(
            m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=ACTOR, ledger=boom
        )
    got = store.get_measure(space, m.id)
    assert got is not None and got.state == "proposed"
    assert got.ledger_entry_id is None and got.decided_by is None
    assert len(store._audit) == before_audit
    # An empty entry id is as bad as an exception.
    with pytest.raises(ValueError):
        store.decide_measure(
            m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=ACTOR,
            ledger=lambda _p: "",
        )  # fmt: skip
    got = store.get_measure(space, m.id)
    assert got is not None and got.state == "proposed"


def test_stale_hash_is_refused_with_the_current_row() -> None:
    store, _space, vid = _store()
    m = _propose(store, vid)
    box: list[dict[str, Any]] = []
    with pytest.raises(MeasureTransitionError) as exc:
        store.decide_measure(
            m.id, to_state="confirmed", expected_hash="0" * 64, actor=ACTOR, ledger=_ledger(box)
        )
    assert exc.value.code == "stale_definition" and exc.value.current == m
    assert box == []


def test_reject_needs_a_reason_and_freezes_the_definition() -> None:
    store, space, vid = _store()
    m = _propose(store, vid)
    box: list[dict[str, Any]] = []
    with pytest.raises(ValueError):
        store.decide_measure(
            m.id, to_state="rejected", expected_hash=m.definition_hash, reason="  ", actor=ACTOR,
            ledger=_ledger(box),
        )  # fmt: skip
    with pytest.raises(MeasureTransitionError) as exc:
        store.decide_measure(
            m.id, to_state="rejected", expected_hash=m.definition_hash, reason="no",
            name="renamed_on_reject", actor=ACTOR, ledger=_ledger(box),
        )  # fmt: skip
    assert exc.value.code == "definition_frozen"
    assert box == []
    rej, changed = store.decide_measure(
        m.id, to_state="rejected", expected_hash=m.definition_hash, reason="wrong column",
        actor=ACTOR, ledger=_ledger(box, "L-9"),
    )  # fmt: skip
    assert changed and rej.state == "rejected" and rej.decision_reason == "wrong column"
    assert rej.ledger_entry_id == "L-9" and rej.definition_hash == m.definition_hash
    again, changed2 = store.decide_measure(
        m.id, to_state="rejected", expected_hash=m.definition_hash, reason="x", actor=ACTOR,
        ledger=_ledger(box, "L-10"),
    )  # fmt: skip
    assert not changed2 and again.ledger_entry_id == "L-9" and len(box) == 1
    assert store.measures_for_space(space, states=("rejected",))[0].id == m.id


def test_all_nine_pairs_through_decide_agree_with_the_graph() -> None:
    """Drive a row into each old state, attempt each new state; legal iff the graph says so."""
    for old in ALL_STATES:
        for new in ALL_STATES:
            store, _space, vid = _store()
            m = _propose(store, vid)
            box: list[dict[str, Any]] = []
            if old == "confirmed":
                m, _ = store.decide_measure(
                    m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=ACTOR,
                    ledger=_ledger(box),
                )  # fmt: skip
            elif old == "rejected":
                m, _ = store.decide_measure(
                    m.id, to_state="rejected", expected_hash=m.definition_hash, reason="r",
                    actor=ACTOR, ledger=_ledger(box),
                )  # fmt: skip
            n0 = len(box)
            if old == new:
                same, changed = store.decide_measure(
                    m.id, to_state=new, expected_hash=m.definition_hash, reason="r", actor=ACTOR,
                    ledger=_ledger(box),
                )  # fmt: skip
                assert not changed and same.state == old and len(box) == n0
            elif measure_transition_ok(old, new):
                done, changed = store.decide_measure(
                    m.id, to_state=new, expected_hash=m.definition_hash, reason="r", actor=ACTOR,
                    ledger=_ledger(box),
                )  # fmt: skip
                assert changed and done.state == new and len(box) == n0 + 1
            else:
                with pytest.raises(MeasureTransitionError) as exc:
                    store.decide_measure(
                        m.id, to_state=new, expected_hash=m.definition_hash, reason="r",
                        actor=ACTOR, ledger=_ledger(box),
                    )  # fmt: skip
                assert exc.value.code == "illegal_transition" and len(box) == n0


def test_rejected_measure_can_be_confirmed_again() -> None:
    store, space, vid = _store()
    m = _propose(store, vid)
    box: list[dict[str, Any]] = []
    r, _ = store.decide_measure(
        m.id, to_state="rejected", expected_hash=m.definition_hash, reason="hold", actor=ACTOR,
        ledger=_ledger(box, "L-1"),
    )  # fmt: skip
    c, changed = store.decide_measure(
        m.id, to_state="confirmed", expected_hash=r.definition_hash, actor=ACTOR,
        ledger=_ledger(box, "L-2"),
    )  # fmt: skip
    assert changed and c.state == "confirmed" and c.ledger_entry_id == "L-2"
    assert [x.id for x in store.measures_for_space(space, states=("confirmed",))] == [m.id]


def test_get_measure_is_space_scoped_and_unknown_ids_raise_on_decide() -> None:
    store, space, vid = _store()
    other_space = uuid4()
    store.reconnect(
        space_id=other_space,
        identity=SourceIdentity("postgres", "h2", "d2", "public"),
        fingerprint="fp-x",
    )
    m = _propose(store, vid)
    assert store.get_measure(space, m.id) == m
    assert store.get_measure(other_space, m.id) is None
    assert store.get_measure(space, uuid4()) is None
    assert store.measures_for_space(other_space) == []
    with pytest.raises(MeasureNotFound):
        store.decide_measure(
            uuid4(), to_state="confirmed", expected_hash="0" * 64, actor=ACTOR, ledger=_ledger([])
        )


def test_measures_for_space_filters_states_and_active_only() -> None:
    store, space, vid = _store()
    ident = SourceIdentity("postgres", "h", "d", "public")
    proposed_version = store.reconnect(space_id=space, identity=ident, fingerprint="fp-2")
    assert proposed_version.status == "proposed"
    a = _propose(store, vid, _draft("a_one"))
    _propose(store, proposed_version.id, _draft("b_one"))
    c, _ = store.decide_measure(
        a.id, to_state="confirmed", expected_hash=a.definition_hash, actor=ACTOR,
        ledger=_ledger([]),
    )  # fmt: skip
    assert {m.name for m in store.measures_for_space(space)} == {"a_one", "b_one"}
    assert [m.name for m in store.measures_for_space(space, active_only=True)] == ["a_one"]
    assert [m.name for m in store.measures_for_space(space, states=("confirmed",))] == ["a_one"]
    assert store.measures_for_space(space, states=("rejected",)) == []
    got = store.measures_for_space(space, states=("confirmed",), active_only=True)[0]
    assert got.version_status == "active" and got.version_fingerprint == "fp-1" and got == c


def test_record_refusal_writes_one_audit_row_and_changes_nothing() -> None:
    store, space, vid = _store()
    m = _propose(store, vid)
    store.record_refusal(m.id, failed=["column_all_null", "name_taken"], actor=ACTOR,
                         from_state="proposed")  # fmt: skip
    rows = [a for a in store._audit if a.action_type == "measure.confirm_refused"]
    assert len(rows) == 1 and rows[0].result == {"failed": ["column_all_null", "name_taken"]}
    assert store.get_measure(space, m.id) == m
    with pytest.raises(MeasureNotFound):
        store.record_refusal(uuid4(), failed=[], actor=None, from_state="proposed")


def test_concurrent_confirms_in_memory_yield_one_transition() -> None:
    store, _space, vid = _store()
    m = _propose(store, vid)
    box: list[dict[str, Any]] = []
    outcomes: list[bool] = []
    gate = threading.Barrier(4)

    def worker() -> None:
        gate.wait()
        _row, changed = store.decide_measure(
            m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=ACTOR,
            ledger=_ledger(box, "L-1"),
        )  # fmt: skip
        outcomes.append(changed)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == [False, False, False, True]
    assert len(box) == 1


# =============================================================================
# step 4: the steward service (measure_confirm) on the in-memory store rig
# =============================================================================
#
# Real ingest through POST /v1/studio/sources/sql (typed schools data: budget total 360.75,
# 4 schools), the process ontology store swapped for a fresh in-memory one, a recording
# ledger. Nothing here is on the answer path (no measure is loaded into an ask until
# step 6), so there is no envelope assertion; the figures are checked against an
# independent oracle query on the lake.

SCHOOLS = "bronze.public_schools"
DISTRICTS = "bronze.public_districts"
EXECUTOR_DIR = ROOT / "packages" / "executor" / "dms_executor"


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> Any:
    return make_minter(monkeypatch)


@pytest.fixture()
def store() -> Iterator[OntologyStore]:
    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


class _Ledger:
    """A recording Cortex ledger: append returns an entry id and a chain hash."""

    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []
        self.verifies = 0
        self.append_error: Exception | None = None
        self.verify_ok = True
        self.hash_is_entry_id = False

    def append(self, *, event_type: str, payload: dict[str, Any], actor: str | None = None) -> Any:
        if self.append_error is not None:
            raise self.append_error
        self.appended.append({"event_type": event_type, "payload": payload, "actor": actor})
        n = len(self.appended)
        entry = f"led_m{n}"
        return LedgerAppendResponse(
            entry_id=entry, hash=entry if self.hash_is_entry_id else f"h{n}x"
        )

    def verify(self) -> Any:
        self.verifies += 1
        return SimpleNamespace(ok=self.verify_ok)


@dataclass
class _Env:
    rig: _Rig
    space: str
    sid: UUID
    store: OntologyStore
    ledger: _Ledger

    @property
    def lake(self) -> Path:
        return self.rig.lake

    def version(self) -> OntologyVersion:
        return self.store.active_for_space(self.sid)[0]

    def snapshot(self, version: OntologyVersion | None = None) -> OntologySnapshot:
        snap = self.store.latest_snapshot((version or self.version()).id)
        assert snap is not None
        return snap

    def propose(self) -> dict[str, Any]:
        return mc.propose_measures(self.space, actor=ACTOR, warehouse=self.lake, store=self.store)

    def proposed(self) -> dict[str, dict[str, Any]]:
        """Propose and return the created views by name."""
        return {m["name"]: m for m in self.propose()["created"]}

    def audit(self, action: str | None = None) -> list[str]:
        names = [a.action_type for a in self.store._audit]
        return names if action is None else [n for n in names if n == action]

    def row(self, measure_id: str | UUID) -> OntoMeasure:
        got = self.store.get_measure(self.sid, UUID(str(measure_id)))
        assert got is not None
        return got

    def confirm(self, view: dict[str, Any] | OntoMeasure, **kw: Any) -> dict[str, Any]:
        mid, h = _id_hash(view)
        kw.setdefault("definition_hash", h)
        return mc.confirm_measure(
            self.space,
            mid,
            actor=ACTOR,
            warehouse=self.lake,
            store=self.store,
            ledger_append=self.ledger.append,
            ledger_verify=self.ledger.verify,
            **kw,
        )

    def reject(self, view: dict[str, Any] | OntoMeasure, reason: str = "not needed") -> Any:
        mid, _h = _id_hash(view)
        return mc.reject_measure(
            self.space,
            mid,
            reason=reason,
            actor=ACTOR,
            store=self.store,
            ledger_append=self.ledger.append,
            ledger_verify=self.ledger.verify,
        )

    def refused(self, view: dict[str, Any] | OntoMeasure, **kw: Any) -> MeasureRefusal:
        with pytest.raises(MeasureRefusal) as exc:
            self.confirm(view, **kw)
        return exc.value

    def view(self, **kw: Any) -> dict[str, Any]:
        kw.setdefault("role", "steward")
        kw.setdefault("can_decide", True)
        return mc.measures_view(self.space, store=self.store, **kw)

    def raw(
        self,
        name: str = "raw_measure",
        *,
        aggregate: str = "count",
        column: str = "*",
        description: str = "A raw proposal.",
        grain: str = SCHOOLS,
        version: OntologyVersion | None = None,
    ) -> OntoMeasure:
        """A proposal that skipped the validator (what a buggy or hostile writer could leave)."""
        v = version or self.version()
        inserted, skipped = self.store.add_measures(
            v.id,
            [MeasureDraft(name, grain, aggregate, column, description, "manual")],
            [{"snapshot_id": str(self.snapshot(v).id)}],
            actor=ACTOR,
            snapshot_id=None,
            action="measure.create",
        )
        assert inserted and not skipped
        return inserted[0]


def _id_hash(view: dict[str, Any] | OntoMeasure) -> tuple[UUID, str]:
    if isinstance(view, OntoMeasure):
        return view.id, view.definition_hash
    return UUID(view["id"]), view["definition_hash"]


def _codes(exc: MeasureRefusal) -> set[str]:
    return {f.code for f in exc.failed}


def _lake_exec(lake: Path, *statements: str) -> None:
    con = duckdb.connect(str(lake))
    try:
        for s in statements:
            con.execute(s)
    finally:
        con.close()


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore) -> _Env:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Measures")
    rig.ingest(space)
    return _Env(rig=rig, space=space, sid=UUID(space), store=store, ledger=_Ledger())


# ------------------------------------------------------------------ proposer


def test_propose_offers_the_expected_measures_with_lake_evidence(env: _Env) -> None:
    out = env.propose()
    views = {m["name"]: m for m in out["created"]}
    assert set(views) == {"schools_budget_sum", "schools_count", "districts_count"}
    assert out["skipped"] == [] and out["drift"] == []
    for v in views.values():
        assert v["state"] == "proposed" and v["source"] == "derived"
        assert v["decided_by"] is None and v["ledger_entry_id"] is None
        assert not description_failures(v["description"]), v["description"]
        assert v["answers_certified"] is False  # a proposal never answers
        assert v["usable"] is True and v["confirm_blockers"] == []
        assert set(v["evidence"]) >= {
            "measured_at", "snapshot_id", "schema_fingerprint", "grain_rows", "non_null_rows",
            "column_type", "probe_sql", "probe_value", "checks",
        }  # fmt: skip
        assert "executed" in v["evidence"]["checks"]
    # A proposal is not authority: no ledger entry, one propose audit row.
    assert env.ledger.appended == []
    assert env.audit("measure.propose") == ["measure.propose"]


def test_evidence_probe_value_equals_the_lake_oracle(env: _Env) -> None:
    views = env.proposed()
    total = _oracle(env.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")[0]["v"]
    assert total == Decimal("360.75")
    ev = views["schools_budget_sum"]["evidence"]
    assert Decimal(ev["probe_value"]) == total
    assert ev["grain_rows"] == 4 and ev["non_null_rows"] == 4
    assert ev["column_type"] == "DECIMAL(10,2)"
    n = _oracle(env.lake, "SELECT COUNT(*) AS v FROM bronze.public_schools")[0]["v"]
    assert views["schools_count"]["evidence"]["probe_value"] == n == 4
    d = _oracle(env.lake, "SELECT COUNT(*) AS v FROM bronze.public_districts")[0]["v"]
    assert views["districts_count"]["evidence"]["probe_value"] == d == 2
    # The probe SQL itself, run independently, gives the same figure.
    again = _oracle(env.lake, ev["probe_sql"])
    assert list(again[0].values())[0] == total


def test_dry_compile_sql_equals_the_sql_the_ask_later_runs(env: _Env) -> None:
    views = env.proposed()
    confirmed = env.confirm(views["schools_budget_sum"])["measure"]
    stored = env.row(confirmed["id"])
    # Rebuild exactly what the ask path will hold for this confirmed row (same expression
    # seam, provenance carrying the stored definition) and compile the plan an ask carries.
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None
    onto.add_measure(
        stored.name,
        stored.grain,
        measure_expression(stored.aggregate, stored.column_name),
        description=stored.description,
        provenance=MeasureProvenance(
            measure_id=str(stored.id),
            name=stored.name,
            grain=stored.grain,
            aggregate=stored.aggregate,
            column=stored.column_name,
            column_type=stored.evidence["column_type"],
            definition_hash=stored.definition_hash,
            version_id=str(stored.version_id),
            decided_at=stored.decided_at.isoformat() if stored.decided_at else "",
            ledger_entry_id=stored.ledger_entry_id or "",
            persisted=False,
        ),
    )
    loaded = ga.load_verified_ontology(env.lake, onto)
    assert loaded is not None
    plan = ga.plan_from_payload({"query_plan": {"measure": stored.name}})
    assert plan is not None
    compiled = ga.compile_plan_maybe_unverified(loaded, plan)
    assert not isinstance(compiled, ga.Refusal)
    assert compiled.sql == stored.evidence["probe_sql"]
    assert list(_oracle(env.lake, compiled.sql)[0].values())[0] == Decimal(
        stored.evidence["probe_value"]
    )


def test_public_compile_alias_is_the_private_compile_and_changes_nothing(env: _Env) -> None:
    onto = load_space_ontology(env.space, env.store)
    assert onto is not None
    loaded = ga.load_verified_ontology(env.lake, onto)
    assert loaded is not None
    loaded.add_measure("probe_count", SCHOOLS, "COUNT(*)")
    plan = ga.QueryPlan(measure="probe_count")
    flag = loaded.verified
    a = ga.compile_plan_maybe_unverified(loaded, plan)
    b = ga._compile_maybe_unverified(loaded, plan)
    assert not isinstance(a, ga.Refusal) and not isinstance(b, ga.Refusal)
    assert a.sql == b.sql and a.notes == b.notes
    assert loaded.verified is flag  # the caller's ontology is not mutated
    assert mc.QueryPlan is ga.QueryPlan


def _body(objects: dict[str, Any], links: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"objects": objects, "links": links or {}}


def _obj(table: str, attrs: list[tuple[str, str]], key: str = "id") -> dict[str, Any]:
    return {
        "relation": f"bronze.{table}",
        "source_table": f"public.{table}",
        "key": [key],
        "attributes": [{"name": n, "type": t} for n, t in attrs],
    }


def test_proposer_skips_keys_foreign_keys_text_and_non_measure_columns() -> None:
    body = _body(
        {
            "bronze.orders": _obj(
                "orders",
                [
                    ("amount_id", "BIGINT"),  # the key, under a measure-looking name
                    ("cost_id", "BIGINT"),  # a foreign key, under a measure-looking name
                    ("total", "DECIMAL(12,2)"),
                    ("unit_price", "DOUBLE"),
                    ("price_text", "VARCHAR"),
                    ("status_code", "INTEGER"),
                    ("note", "VARCHAR"),
                    ("placed", "DATE"),
                ],
                key="amount_id",
            )
        },
        {
            "orders_customer_fk": {
                "from": "bronze.orders",
                "from_columns": ["cost_id"],
                "to": "bronze.customers",
                "to_columns": ["id"],
            }
        },
    )
    accepted, skipped = mc.propose_measure_drafts(body, [])
    names = [d.name for d in accepted]
    assert names == [
        "orders_total_sum",
        "orders_unit_price_sum",
        "orders_unit_price_avg",
        "orders_count",
    ]
    assert not any(
        d.column in ("amount_id", "cost_id", "note", "price_text", "status_code") for d in accepted
    )
    assert all(not description_failures(d.description) for d in accepted)
    assert skipped == []


def test_proposer_skips_failed_and_truncated_objects() -> None:
    body = _body(
        {
            "bronze.good": _obj("good", [("id", "BIGINT"), ("amount", "DECIMAL(10,2)")]),
            "bronze.bad": _obj("bad", [("id", "BIGINT"), ("amount", "DECIMAL(10,2)")]),
            "bronze.cut": _obj("cut", [("id", "BIGINT"), ("amount", "DECIMAL(10,2)")]),
        }
    )
    body["catalog"] = {
        "tables": [
            {"path": "bronze.good", "truncated": False},
            {"path": "bronze.bad", "truncated": False},
            {"path": "bronze.cut", "truncated": True},
        ]
    }
    violations = [{"check": "key_unique", "subject": "bronze.bad", "detail": "dup"}]
    accepted, skipped = mc.propose_measure_drafts(body, violations)
    assert {d.grain for d in accepted} == {"bronze.good"}
    assert {(s["name"], s["why"]) for s in skipped} == {
        ("bronze.bad", "grain_unverified"),
        ("bronze.cut", "source_truncated"),
    }


def test_proposer_is_deterministic_and_bounded() -> None:
    objs = {f"bronze.t{i:02d}": _obj(f"t{i:02d}", [("id", "BIGINT"), ("amount", "DECIMAL(10,2)")])
            for i in range(70)}  # fmt: skip
    a, sa = mc.propose_measure_drafts(_body(objs), [])
    b, sb = mc.propose_measure_drafts(_body(dict(reversed(list(objs.items())))), [])
    assert a == b and sa == sb  # insertion order of the snapshot does not matter
    assert len(a) == 60  # MAX_PROPOSED_PER_RUN
    assert all(d.aggregate == "sum" for d in a)  # measure-looking columns first
    over = [s for s in sa if s["why"] == "measure_limit"]
    assert len(over) == 80 and len(a) + len(over) == 140
    # A smaller cap is honoured exactly.
    c, _ = mc.propose_measure_drafts(_body(objs), [], cap=5)
    assert len(c) == 5


def test_proposer_skips_names_taken_reserved_and_descriptions_that_fail_the_rule() -> None:
    body = _body(
        {
            "bronze.a": _obj("a", [("id", "BIGINT"), ("amount_{x}", "DECIMAL(10,2)")]),
            "bronze.items": _obj("items", [("id", "BIGINT"), ("amount", "DECIMAL(10,2)")]),
        }
    )
    accepted, skipped = mc.propose_measure_drafts(body, [], taken_names={"items_count"})
    why = {s["name"]: s["why"] for s in skipped}
    assert why["a_amount__x__sum"] == "description_invalid"  # braces never reach Cortex
    assert why["items_count"] == "name_taken"
    assert "items_amount_sum" in {d.name for d in accepted}
    assert [d.name for d in accepted if d.name.startswith("a_")] == ["a_count"]
    # Existing names are 'exists', not re-proposed.
    again, skipped2 = mc.propose_measure_drafts(body, [], existing_names={"items_amount_sum"})
    assert "items_amount_sum" not in {d.name for d in again}
    assert {"name": "items_amount_sum", "why": "exists"} in skipped2


def test_propose_is_idempotent_and_never_resurrects_a_decided_row(env: _Env) -> None:
    views = env.proposed()
    again = env.propose()
    assert again["created"] == []
    assert {s["why"] for s in again["skipped"]} == {"exists"}
    assert len(env.store.measures_for_space(env.sid)) == 3
    assert env.audit("measure.propose") == ["measure.propose"]  # the rerun wrote nothing

    rejected = env.reject(views["schools_count"], "we count differently")["measure"]
    confirmed = env.confirm(views["schools_budget_sum"])["measure"]
    third = env.propose()
    assert third["created"] == []
    assert env.row(rejected["id"]).state == "rejected"
    assert env.row(confirmed["id"]).state == "confirmed"
    assert env.row(confirmed["id"]).ledger_entry_id == confirmed["ledger_entry_id"]
    assert len(env.store.measures_for_space(env.sid)) == 3


def test_propose_surfaces_drift_and_adds_nothing_to_a_non_active_version(env: _Env) -> None:
    v1 = env.version()
    drifted = env.store.reconnect(space_id=env.sid, identity=v1.identity, fingerprint="changed")
    assert drifted.status == "proposed"
    out = env.propose()
    assert out["drift"] == [{"version_id": str(drifted.id), "status": "proposed"}]
    assert {m["version_id"] for m in out["created"]} == {str(v1.id)}


def test_propose_in_a_space_with_no_ontology_is_empty(env: _Env) -> None:
    out = mc.propose_measures(str(uuid4()), actor=ACTOR, warehouse=env.lake, store=env.store)
    assert out == {"created": [], "skipped": [], "drift": []}
    with pytest.raises(MeasureRefusal) as exc:
        mc.propose_measures("not-a-uuid", actor=ACTOR, warehouse=env.lake, store=env.store)
    assert exc.value.status == 404 and exc.value.code == "space_not_found"


def test_propose_refuses_when_the_warehouse_is_missing(env: _Env, tmp_path: Path) -> None:
    with pytest.raises(MeasureRefusal) as exc:
        mc.propose_measures(
            env.space, actor=ACTOR, warehouse=tmp_path / "gone.duckdb", store=env.store
        )
    assert exc.value.status == 503 and exc.value.code == "warehouse_unavailable"
    assert env.store.measures_for_space(env.sid) == []


# ------------------------------------------------------------------ confirm


def test_confirm_writes_the_ledger_entry_first_and_pointers_only(env: _Env) -> None:
    view = env.proposed()["schools_budget_sum"]
    out = env.confirm(view)
    assert out["already"] is False
    m = out["measure"]
    assert m["state"] == "confirmed" and m["decided_by"] == str(ACTOR)
    assert m["ledger_entry_id"] == "led_m1" and m["decision_reason"] is None
    assert m["usable"] is True and m["answers_certified"] is True
    assert m["definition_hash"] == view["definition_hash"]  # no rename: same hash
    # evidence was re-measured at confirm time, against the lake as it is now
    assert m["evidence"]["probe_sql"] == view["evidence"]["probe_sql"]
    assert Decimal(m["evidence"]["probe_value"]) == Decimal("360.75")

    (call,) = env.ledger.appended
    assert call["event_type"] == "ontology.measure.confirmed" and call["actor"] == str(ACTOR)
    assert set(call["payload"]) == {
        "space_id", "version_id", "measure_id", "name", "grain", "aggregate", "column",
        "definition_hash", "schema_fingerprint", "snapshot_id", "from_state", "to_state",
    }  # fmt: skip
    assert (
        call["payload"]["from_state"] == "proposed" and call["payload"]["to_state"] == "confirmed"
    )
    assert "360.75" not in str(call["payload"])  # no values on the ledger
    assert env.ledger.verifies == 1  # the chain was read back
    assert env.audit("measure.confirm") == ["measure.confirm"]


def test_rename_at_confirm_changes_the_hash_and_the_compiled_alias(env: _Env) -> None:
    view = env.proposed()["schools_budget_sum"]
    out = env.confirm(view, name="school_budget_total", description="Total school budget.")
    m = out["measure"]
    assert m["name"] == "school_budget_total" and m["description"] == "Total school budget."
    expect = measure_definition_hash(
        "school_budget_total", SCHOOLS, "sum", "budget", "Total school budget."
    )
    assert m["definition_hash"] == expect != view["definition_hash"]
    assert '"school_budget_total"' in m["evidence"]["probe_sql"]  # the new name was dry-compiled
    assert m["aggregate"] == "sum" and m["column"] == "budget" and m["grain"] == SCHOOLS
    assert env.ledger.appended[0]["payload"]["name"] == "school_budget_total"


def test_confirm_twice_is_one_ledger_append_and_no_second_audit_row(env: _Env) -> None:
    view = env.proposed()["schools_count"]
    first = env.confirm(view)
    second = env.confirm(view)
    assert first["already"] is False and second["already"] is True
    assert second["measure"]["ledger_entry_id"] == first["measure"]["ledger_entry_id"]
    assert len(env.ledger.appended) == 1
    assert env.audit("measure.confirm") == ["measure.confirm"]
    assert env.audit("measure.confirm_refused") == []


def test_confirmed_measure_cannot_be_reworded_or_confirmed_on_a_stale_hash(env: _Env) -> None:
    view = env.proposed()["schools_count"]
    env.confirm(view)
    exc = env.refused(view, name="another_name")
    assert (exc.status, exc.code) == (409, "measure_confirmed_immutable")
    exc2 = env.refused(view, definition_hash="0" * 64)
    assert (exc2.status, exc2.code) == (409, "stale_definition")
    assert exc2.detail()["current"]["state"] == "confirmed"  # the response carries the row
    assert len(env.ledger.appended) == 1
    assert env.audit("measure.confirm_refused") == ["measure.confirm_refused"] * 2
    assert env.row(view["id"]).name == "schools_count"


def test_confirm_returns_every_failed_criterion_together(env: _Env) -> None:
    # SUM of a text column, under a name that is a column, with SQL-looking words, on a stale
    # hash: one request, all four reasons, the row untouched.
    bad = env.raw("name", aggregate="sum", column="name", description="select total from schools")
    exc = env.refused(bad, definition_hash="f" * 64)
    assert _codes(exc) >= {
        "stale_definition", "column_not_numeric", "name_reserved", "description_invalid",
    }  # fmt: skip
    assert (exc.status, exc.code) == (409, "stale_definition")
    detail = exc.detail()
    assert detail["current"]["id"] == str(bad.id)
    assert all(f["message"] == MEASURE_REFUSAL_COPY[f["code"]] for f in detail["failed"])
    # Nothing moved: no ledger call, no state change, one refusal audit row naming every code.
    assert env.ledger.appended == [] and env.row(bad.id) == bad
    (refused,) = [a for a in env.store._audit if a.action_type == "measure.confirm_refused"]
    assert set(refused.result["failed"]) == _codes(exc)


def test_static_and_lake_failures_come_back_in_one_answer(env: _Env) -> None:
    # An all-NULL column (the lake), SQL-looking words (the spec rules) and a stale hash (the
    # row), asked for in a single confirm: all three are named, and nothing is written.
    row = env.raw(
        "gone_total", aggregate="sum", column="budget", description="select total from budget"
    )
    _lake_exec(env.lake, "UPDATE bronze.public_schools SET budget = NULL")
    exc = env.refused(row, definition_hash="a" * 64)
    assert _codes(exc) == {"stale_definition", "description_invalid", "column_all_null"}
    assert env.row(row.id) == row and env.ledger.appended == []
    (refused,) = [a for a in env.store._audit if a.action_type == "measure.confirm_refused"]
    assert set(refused.result["failed"]) == _codes(exc)


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"aggregate": "sum", "column": "name"}, "column_not_numeric"),
        ({"aggregate": "avg", "column": "name"}, "column_not_numeric"),
        ({"aggregate": "sum", "column": "school_id"}, "key_column_aggregate"),
        ({"aggregate": "max", "column": "district_id"}, "key_column_aggregate"),
        ({"aggregate": "sum", "column": "no_such_column"}, "column_missing"),
        ({"aggregate": "sum", "column": "_src"}, "column_missing"),
        ({"aggregate": "max", "column": "name"}, "column_type_unsupported"),
        ({"aggregate": "count", "column": "*", "grain": "bronze.nowhere"}, "unknown_object"),
        ({"description": "select name from schools"}, "description_invalid"),
        ({"description": "has; a semicolon"}, "description_invalid"),
    ],
)
def test_each_spec_rule_refuses_a_confirm_by_name(env: _Env, kw: dict[str, Any], code: str) -> None:
    row = env.raw("probe_rule", **kw)
    exc = env.refused(row)
    assert code in _codes(exc), _codes(exc)
    assert exc.status == 422 and exc.code == "measure_invalid"
    assert env.row(row.id).state == "proposed" and env.ledger.appended == []


def test_name_that_equals_a_table_a_column_or_a_demo_pack_id_is_refused(env: _Env) -> None:
    demo = sorted(n for n in _demo_reserved() if NAME_RE.fullmatch(n))
    assert demo, "the demo pack reserves at least one usable measure id"
    for name in ("budget", "public_schools", "opened", "school_id", "name", demo[0]):
        row = env.raw(name)
        assert "name_reserved" in _codes(env.refused(row)), name


def test_an_empty_grain_and_an_all_null_column_cannot_be_confirmed(env: _Env) -> None:
    views = env.proposed()
    _lake_exec(env.lake, "UPDATE bronze.public_schools SET budget = NULL")
    exc = env.refused(views["schools_budget_sum"])
    assert _codes(exc) == {"column_all_null"}
    assert (exc.status, exc.code) == (422, "measure_confirm_refused")

    _lake_exec(env.lake, "DELETE FROM bronze.public_schools")
    exc2 = env.refused(views["schools_budget_sum"])
    assert {"empty_grain", "column_all_null"} <= _codes(exc2)
    exc3 = env.refused(views["schools_count"])
    assert _codes(exc3) == {"empty_grain"}
    assert env.ledger.appended == []


def test_a_grain_that_fails_verify_now_cannot_be_confirmed(env: _Env) -> None:
    views = env.proposed()
    _lake_exec(
        env.lake,
        "INSERT INTO bronze.public_schools SELECT * FROM bronze.public_schools WHERE school_id = 1",
    )
    exc = env.refused(views["schools_budget_sum"])
    assert _codes(exc) == {"grain_unverified"}
    assert exc.status == 422 and exc.code == "measure_confirm_refused"
    assert (
        env.ledger.appended == [] and env.row(views["schools_budget_sum"]["id"]).state == "proposed"
    )


@pytest.mark.parametrize(
    "ddl",
    [
        "ALTER TABLE bronze.public_schools DROP COLUMN budget",
        "ALTER TABLE bronze.public_schools ALTER budget TYPE VARCHAR",
    ],
)
def test_a_column_dropped_or_retyped_since_the_snapshot_cannot_be_confirmed(
    env: _Env, ddl: str
) -> None:
    view = env.proposed()["schools_budget_sum"]
    _lake_exec(env.lake, ddl)
    exc = env.refused(view)
    assert "column_changed" in _codes(exc)
    assert env.ledger.appended == [] and env.row(view["id"]).state == "proposed"


def test_a_text_column_declared_numeric_at_the_source_cannot_be_counted_or_summed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> None:
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Dirty budget")
    rows = [list(r) for r in DATA['"public"."schools"'][1]]
    rows[2][3] = "n/a"  # a legacy text value in a column declared numeric: lands VARCHAR
    data = dict(DATA)
    data['"public"."schools"'] = (DATA['"public"."schools"'][0], rows)
    rig.ingest(space, data=data)
    e = _Env(rig=rig, space=space, sid=UUID(space), store=store, ledger=_Ledger())
    out = e.propose()
    assert "schools_budget_sum" not in {m["name"] for m in out["created"]}  # VARCHAR: not offered
    summed = e.refused(e.raw("dirty_sum", aggregate="sum", column="budget"))
    assert "column_not_numeric" in _codes(summed)  # the confirm-time twin of untyped numeric
    counted = e.refused(e.raw("dirty_count", aggregate="count", column="budget"))
    assert _codes(counted) == {"untyped_numeric"}
    assert counted.status == 422


def test_a_proposal_on_a_non_active_version_cannot_be_confirmed(env: _Env) -> None:
    v1 = env.version()
    drifted = env.store.reconnect(space_id=env.sid, identity=v1.identity, fingerprint="changed")
    env.store.record_snapshot(
        drifted.id, body=dict(env.snapshot(v1).body), violations=[], verified=True
    )
    row = env.raw("drifted_count", version=drifted)
    exc = env.refused(row)
    assert (exc.status, exc.code) == (409, "version_not_active")
    assert _codes(exc) == {"version_not_active"}
    assert env.ledger.appended == []
    got = env.view()["measures"][0]
    assert got["unusable_reason"] == "version_not_active" and got["usable"] is False


def _second_active_version(env: _Env) -> OntologyVersion:
    other = env.store.reconnect(
        space_id=env.sid,
        identity=SourceIdentity("postgresql", "db2.example.net", "other", "public"),
        fingerprint="fp-other",
    )
    assert other.status == "active"
    body = _body({"bronze.public_items": _obj("items", [("id", "BIGINT"), ("qty", "INTEGER")])})
    env.store.record_snapshot(other.id, body=body, violations=[], verified=True)
    return other


def test_a_name_held_by_another_active_version_blocks_the_confirm(env: _Env) -> None:
    views = env.proposed()
    other = _second_active_version(env)
    env.raw("schools_budget_sum", grain="bronze.public_items", version=other)  # same name
    exc = env.refused(views["schools_budget_sum"])
    assert (exc.status, exc.code) == (409, "name_taken")
    assert _codes(exc) == {"name_taken"}
    # and the proposer does not re-offer a name another version holds
    out = env.propose()
    assert "schools_budget_sum" not in {m["name"] for m in out["created"]}


def test_a_truncated_source_cannot_back_a_measure(env: _Env) -> None:
    view = env.proposed()["schools_budget_sum"]

    def cut(body: dict[str, Any]) -> None:
        cat = dict(body["catalog"])
        cat["tables"] = [
            {**t, "truncated": True} if t["path"] == SCHOOLS else t for t in cat["tables"]
        ]
        body["catalog"] = cat

    _snapshot_with(env, cut)
    exc = env.refused(view)
    assert "source_truncated" in _codes(exc)
    assert (exc.status, exc.code) == (422, "measure_invalid")
    listed = {m["name"]: m for m in env.view()["measures"]}["schools_budget_sum"]
    assert "source_truncated" in [b["code"] for b in listed["confirm_blockers"]]
    # and the proposer stops offering measures over it
    assert all(m["grain"] != SCHOOLS for m in env.propose()["created"])
    assert env.ledger.appended == []


def test_a_proposal_from_an_older_fingerprint_is_stale(env: _Env) -> None:
    view = env.proposed()["schools_count"]
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == view["id"])
    env.store._measures[idx] = replace(env.store._measures[idx], schema_fingerprint="older")
    exc = env.refused(view)
    assert (exc.status, exc.code) == (409, "stale_proposal")
    assert env.ledger.appended == []


def test_the_static_criteria_say_a_confirmed_measure_is_not_confirmable(env: _Env) -> None:
    confirmed = env.confirm(env.proposed()["schools_count"])["measure"]
    state = mc._state(env.space, env.store)
    codes = [f.code for f in mc.evaluate_confirm_criteria(env.row(confirmed["id"]), state)]
    assert "measure_confirmable_state" in codes


def test_the_eighty_first_confirmed_measure_is_refused(env: _Env) -> None:
    v = env.version()
    for i in range(80):
        m = env.raw(f"cnt_{i:02d}", version=v)
        env.store.decide_measure(
            m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=ACTOR,
            ledger=lambda _p: "led_bulk",
        )  # fmt: skip
    extra = env.raw("one_too_many")
    exc = env.refused(extra)
    assert (exc.status, exc.code) == (409, "measure_limit")
    assert env.view()["counts"]["confirmed"] == 80
    # Withdrawing one makes room.
    first = env.store.measures_for_space(env.sid, states=("confirmed",))[0]
    env.reject(first, "make room")
    assert env.confirm(extra)["measure"]["state"] == "confirmed"


def test_ledger_failures_leave_the_measure_proposed_and_unaudited(env: _Env) -> None:
    view = env.proposed()["schools_count"]
    before = env.row(view["id"])
    env.ledger.append_error = RuntimeError("cortex down")
    exc = env.refused(view)
    assert (exc.status, exc.code) == (503, "ledger_append_failed")
    assert env.row(view["id"]) == before and "measure.confirm" not in env.audit()

    env.ledger.append_error = None
    env.ledger.verify_ok = False  # appended, but the chain does not verify
    exc = env.refused(view)
    assert (exc.status, exc.code) == (503, "ledger_append_failed")
    assert env.row(view["id"]) == before and "measure.confirm" not in env.audit()

    env.ledger.verify_ok = True
    env.ledger.hash_is_entry_id = True  # a hash that is just the id is not a signature
    assert env.refused(view).code == "ledger_append_failed"
    assert env.row(view["id"]) == before

    env.ledger.hash_is_entry_id = False  # and it recovers: the failure was the ledger, not the row
    assert env.confirm(view)["measure"]["state"] == "confirmed"


def test_a_lake_that_cannot_be_read_is_a_503_not_a_verdict_on_the_measure(
    env: _Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = env.proposed()["schools_count"]
    monkeypatch.setattr(
        mc, "untyped_numeric_reason", lambda sql, lake: "untyped_numeric:registry_unreadable"
    )
    exc = env.refused(view)
    assert (exc.status, exc.code) == (503, "warehouse_unavailable")
    monkeypatch.undo()
    gone = tmp_path / "gone.duckdb"
    with pytest.raises(MeasureRefusal) as exc2:
        mc.confirm_measure(
            env.space, UUID(view["id"]), definition_hash=view["definition_hash"], actor=ACTOR,
            warehouse=gone, store=env.store, ledger_append=env.ledger.append,
            ledger_verify=env.ledger.verify,
        )
    assert (exc2.value.status, exc2.value.code) == (503, "warehouse_unavailable")
    # An outage is not a refusal of the measure: nothing was audited or ledgered.
    assert env.audit("measure.confirm_refused") == [] and env.ledger.appended == []
    assert env.row(view["id"]).state == "proposed"


def test_no_ledger_is_a_named_503_before_anything_is_checked(env: _Env) -> None:
    view = env.proposed()["schools_count"]
    with pytest.raises(MeasureRefusal) as exc:
        mc.confirm_measure(
            env.space, UUID(view["id"]), definition_hash=view["definition_hash"], actor=ACTOR,
            warehouse=env.lake, store=env.store,
        )  # fmt: skip
    assert (exc.value.status, exc.value.code) == (503, "ledger_unavailable")
    with pytest.raises(MeasureRefusal) as exc2:
        mc.reject_measure(
            env.space, UUID(view["id"]), reason="x", actor=ACTOR, store=env.store,
            ledger_append=env.ledger.append,
        )  # fmt: skip
    assert exc2.value.code == "ledger_unavailable"
    assert env.row(view["id"]).state == "proposed"


def test_a_measure_of_another_space_is_not_found(env: _Env) -> None:
    view = env.proposed()["schools_count"]
    other = str(uuid4())
    for call in (
        lambda: mc.confirm_measure(
            other, UUID(view["id"]), definition_hash=view["definition_hash"], actor=ACTOR,
            warehouse=env.lake, store=env.store, ledger_append=env.ledger.append,
            ledger_verify=env.ledger.verify,
        ),
        lambda: mc.reject_measure(
            other, UUID(view["id"]), reason="x", actor=ACTOR, store=env.store,
            ledger_append=env.ledger.append, ledger_verify=env.ledger.verify,
        ),
    ):  # fmt: skip
        with pytest.raises(MeasureRefusal) as exc:
            call()
        assert (exc.value.status, exc.value.code) == (404, "measure_not_found")
    with pytest.raises(MeasureRefusal) as exc:
        env.confirm({"id": str(uuid4()), "definition_hash": "0" * 64})
    assert exc.value.code == "measure_not_found"
    assert env.ledger.appended == [] and env.row(view["id"]).state == "proposed"


def test_two_confirms_racing_make_one_transition_and_one_already(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = env.proposed()["schools_count"]
    gate = threading.Barrier(2)
    real = mc.measure_evidence

    def synced(*a: Any, **k: Any) -> Any:
        out = real(*a, **k)
        gate.wait(timeout=20)  # both have passed every check before either decides
        return out

    monkeypatch.setattr(mc, "measure_evidence", synced)
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(env.confirm(view))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert sorted(r["already"] for r in results) == [False, True]
    assert len(env.ledger.appended) == 1
    assert env.audit("measure.confirm") == ["measure.confirm"]


# ------------------------------------------------------------------ reject


def test_reject_needs_a_reason_and_is_idempotent(env: _Env) -> None:
    view = env.proposed()["schools_count"]
    for bad in ("", "   ", "x" * 201, "line\nbreak"):
        with pytest.raises(MeasureRefusal) as exc:
            env.reject(view, bad)
        assert (exc.value.status, exc.value.code) == (422, "reason_invalid")
    assert env.ledger.appended == [] and env.row(view["id"]).state == "proposed"

    out = env.reject(view, "  we count enrolments instead  ")
    assert out["already"] is False
    m = out["measure"]
    assert m["state"] == "rejected" and m["decision_reason"] == "we count enrolments instead"
    assert m["decided_by"] == str(ACTOR) and m["ledger_entry_id"] == "led_m1"
    (call,) = env.ledger.appended
    assert call["event_type"] == "ontology.measure.rejected"
    assert "enrolments" not in str(call["payload"])  # the typed reason never reaches the ledger
    again = env.reject(view, "again")
    assert again["already"] is True and len(env.ledger.appended) == 1
    assert env.audit("measure.reject") == ["measure.reject"]


def test_a_confirmed_measure_is_withdrawn_and_can_be_confirmed_again(env: _Env) -> None:
    view = env.proposed()["schools_budget_sum"]
    env.confirm(view)
    withdrawn = env.reject(view, "wrong definition")["measure"]
    assert withdrawn["state"] == "rejected" and withdrawn["answers_certified"] is False
    assert withdrawn["definition_hash"] == view["definition_hash"]  # the definition is frozen

    # rejected -> confirmed re-runs every criterion: break one and it is refused
    _lake_exec(env.lake, "UPDATE bronze.public_schools SET budget = NULL")
    assert "column_all_null" in _codes(env.refused(withdrawn))
    _lake_exec(env.lake, "UPDATE bronze.public_schools SET budget = 10")
    back = env.confirm(withdrawn)["measure"]
    assert back["state"] == "confirmed" and Decimal(back["evidence"]["probe_value"]) == 40
    assert [e["event_type"] for e in env.ledger.appended] == [
        "ontology.measure.confirmed", "ontology.measure.rejected", "ontology.measure.confirmed",
    ]  # fmt: skip


def test_a_refused_confirm_does_not_burn_the_row(env: _Env) -> None:
    view = env.proposed()["schools_count"]
    _lake_exec(env.lake, "DELETE FROM bronze.public_schools")
    assert env.refused(view).code == "measure_confirm_refused"
    assert env.audit("measure.confirm_refused") == ["measure.confirm_refused"]
    _lake_exec(env.lake, "INSERT INTO bronze.public_schools (school_id, district_id, name, budget)"
                         " VALUES (1, 1, 'A', 1.00)")  # fmt: skip
    assert env.confirm(view)["measure"]["state"] == "confirmed"


# ------------------------------------------------------------------ manual entry


def test_manual_entry_creates_a_proposed_measure_never_a_confirmed_one(env: _Env) -> None:
    out = mc.create_measure(
        env.space, name="biggest_budget", grain=SCHOOLS, aggregate="max", column="budget",
        description="The largest school budget.", actor=ACTOR, warehouse=env.lake,
        store=env.store,
    )  # fmt: skip
    assert out["created"] is True
    m = out["measure"]
    assert m["state"] == "proposed" and m["source"] == "manual" and m["decided_by"] is None
    assert Decimal(m["evidence"]["probe_value"]) == Decimal("200.25")
    assert env.audit("measure.create") == ["measure.create"] and env.ledger.appended == []
    # the same definition again: not created twice
    same = mc.create_measure(
        env.space, name="biggest_budget", grain=SCHOOLS, aggregate="max", column="budget",
        description="The largest school budget.", actor=ACTOR, warehouse=env.lake,
        store=env.store,
    )  # fmt: skip
    assert same["created"] is False and same["measure"]["id"] == m["id"]
    assert len(env.store.measures_for_space(env.sid)) == 1
    # and a steward still has to confirm it
    assert env.confirm(m)["measure"]["state"] == "confirmed"


@pytest.mark.parametrize(
    ("kw", "status", "code"),
    [
        ({"aggregate": "sum", "column": "name"}, 422, "measure_invalid"),
        ({"aggregate": "sum", "column": "school_id"}, 422, "measure_invalid"),
        ({"aggregate": "sum", "column": "nope"}, 422, "measure_invalid"),
        ({"name": "Bad Name"}, 422, "measure_invalid"),
        ({"name": "budget"}, 422, "measure_invalid"),
        ({"description": "select a from b"}, 422, "measure_invalid"),
        ({"description": ""}, 422, "measure_invalid"),
        ({"grain": "bronze.nowhere"}, 422, "unknown_object"),
    ],
)
def test_manual_entry_refusals_store_nothing(
    env: _Env, kw: dict[str, Any], status: int, code: str
) -> None:
    args: dict[str, Any] = {
        "name": "manual_probe", "grain": SCHOOLS, "aggregate": "count", "column": "*",
        "description": "Schools.",
    }  # fmt: skip
    args.update(kw)
    with pytest.raises(MeasureRefusal) as exc:
        mc.create_measure(env.space, actor=ACTOR, warehouse=env.lake, store=env.store, **args)
    assert (exc.value.status, exc.value.code) == (status, code)
    assert env.store.measures_for_space(env.sid) == []
    assert env.audit("measure.create") == [] and env.ledger.appended == []


def test_manual_entry_with_a_taken_name_and_a_different_definition_is_a_409(env: _Env) -> None:
    views = env.proposed()
    with pytest.raises(MeasureRefusal) as exc:
        mc.create_measure(
            env.space, name="schools_count", grain=SCHOOLS, aggregate="count_distinct",
            column="name", description="Different.", actor=ACTOR, warehouse=env.lake,
            store=env.store,
        )  # fmt: skip
    assert (exc.value.status, exc.value.code) == (409, "name_taken")
    assert env.row(views["schools_count"]["id"]).aggregate == "count"


def test_manual_entry_needs_a_lake_that_supports_it(env: _Env) -> None:
    _lake_exec(env.lake, "UPDATE bronze.public_schools SET budget = NULL")
    with pytest.raises(MeasureRefusal) as exc:
        mc.create_measure(
            env.space, name="null_budget", grain=SCHOOLS, aggregate="sum", column="budget",
            description="Budget.", actor=ACTOR, warehouse=env.lake, store=env.store,
        )  # fmt: skip
    assert "column_all_null" in _codes(exc.value)
    assert env.store.measures_for_space(env.sid) == []


def test_manual_entry_on_an_ambiguous_grain_is_named(env: _Env) -> None:
    other = _second_active_version(env)
    both = _body(
        {**env.snapshot(other).body["objects"], SCHOOLS: env.snapshot().body["objects"][SCHOOLS]}
    )
    env.store.record_snapshot(other.id, body=both, violations=[], verified=True)
    with pytest.raises(MeasureRefusal) as exc:
        mc.create_measure(
            env.space, name="ambiguous", grain=SCHOOLS, aggregate="count", column="*",
            description="Schools.", actor=ACTOR, warehouse=env.lake, store=env.store,
        )  # fmt: skip
    assert (exc.value.status, exc.value.code) == (422, "grain_ambiguous")


# ------------------------------------------------------------------ views


def test_view_reports_counts_limits_persistence_and_the_viewer(env: _Env) -> None:
    views = env.proposed()
    env.confirm(views["schools_count"])
    env.reject(views["districts_count"], "later")
    v = env.view()
    assert v["space_id"] == str(env.sid) and v["persisted"] is False  # in-memory store
    assert v["counts"] == {"proposed": 1, "confirmed": 1, "rejected": 1}
    assert v["limits"] == {"confirmed_max": 80}
    assert v["viewer"] == {"role": "steward", "can_decide": True}
    assert [x["version_id"] for x in v["versions"]] == [str(env.version().id)]
    assert v["drift"] == []
    states = {m["name"]: m["state"] for m in v["measures"]}
    assert states == {
        "schools_budget_sum": "proposed",
        "schools_count": "confirmed",
        "districts_count": "rejected",
    }
    certified = {m["name"] for m in v["measures"] if m["answers_certified"]}
    assert certified == {"schools_count"}  # proposed and rejected never answer


def test_view_hides_row_values_from_anyone_who_cannot_decide(env: _Env) -> None:
    env.confirm(env.proposed()["schools_budget_sum"])
    steward = {m["name"]: m for m in env.view()["measures"]}["schools_budget_sum"]
    assert steward["evidence"]["probe_value"] == "360.75" and steward["evidence"]["probe_sql"]
    viewer = {m["name"]: m for m in env.view(role="viewer", can_decide=False)["measures"]}[
        "schools_budget_sum"
    ]
    assert "probe_value" not in viewer["evidence"] and "probe_sql" not in viewer["evidence"]
    assert viewer["evidence"]["grain_rows"] == 4  # counts and booleans only
    assert "360.75" not in str(viewer)


def test_view_is_computed_from_the_snapshot_and_never_touches_the_lake(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    views = env.proposed()
    env.confirm(views["schools_budget_sum"])

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("a GET must not read the lake")

    monkeypatch.setattr(mc, "connect_file", boom)
    monkeypatch.setattr(mc, "measure_evidence", boom)
    bad = env.raw("static_bad", aggregate="sum", column="name")
    listed = {m["name"]: m for m in env.view()["measures"]}
    assert listed["schools_budget_sum"]["usable"] is True
    blockers = [b["code"] for b in listed["static_bad"]["confirm_blockers"]]
    assert "column_not_numeric" in blockers
    assert all(b["message"] for b in listed["static_bad"]["confirm_blockers"])
    # The list's blockers ARE the static part of the confirm criteria.
    state = mc._state(env.space, env.store)
    assert listed["static_bad"]["confirm_blockers"] == [
        f.to_dict() for f in mc.evaluate_confirm_criteria(env.row(bad.id), state)
    ]
    assert listed["schools_budget_sum"]["confirm_blockers"] == []  # confirmed: nothing to confirm


def _snapshot_with(env: _Env, edit: Any, violations: list[dict[str, str]] | None = None) -> None:
    body = {k: (dict(v) if isinstance(v, dict) else v) for k, v in env.snapshot().body.items()}
    body["objects"] = {k: dict(v) for k, v in body["objects"].items()}
    edit(body)
    env.store.record_snapshot(
        env.version().id, body=body, violations=violations or [], verified=not violations
    )


@pytest.mark.parametrize(
    ("mutate", "violations", "reason"),
    [
        (lambda b: b["objects"].pop(SCHOOLS), None, "grain_missing"),
        (
            lambda b: b["objects"][SCHOOLS].update(
                attributes=[a for a in b["objects"][SCHOOLS]["attributes"] if a["name"] != "budget"]
            ),
            None,
            "column_missing",
        ),
        (
            lambda b: b["objects"][SCHOOLS].update(
                attributes=[
                    {**a, "type": "VARCHAR"} if a["name"] == "budget" else a
                    for a in b["objects"][SCHOOLS]["attributes"]
                ]
            ),
            None,
            "column_type_changed",
        ),
        (
            lambda b: None,
            [{"check": "key_unique", "subject": SCHOOLS, "detail": "dup"}],
            "grain_unverified",
        ),
    ],
)
def test_a_confirmed_measure_stops_being_usable_when_the_snapshot_moves(
    env: _Env, mutate: Any, violations: Any, reason: str
) -> None:
    confirmed = env.confirm(env.proposed()["schools_budget_sum"])["measure"]
    assert confirmed["usable"] is True and confirmed["answers_certified"] is True
    _snapshot_with(env, mutate, violations)
    now = {m["name"]: m for m in env.view()["measures"]}["schools_budget_sum"]
    assert now["state"] == "confirmed"  # a GET never writes
    assert now["usable"] is False and now["unusable_reason"] == reason
    assert now["answers_certified"] is False


def test_an_edited_row_is_reported_as_an_integrity_failure(env: _Env) -> None:
    confirmed = env.confirm(env.proposed()["schools_count"])["measure"]
    idx = next(i for i, m in enumerate(env.store._measures) if str(m.id) == confirmed["id"])
    env.store._measures[idx] = replace(env.store._measures[idx], column_name="name")
    now = {m["name"]: m for m in env.view()["measures"]}["schools_count"]
    assert now["unusable_reason"] == "integrity" and now["answers_certified"] is False


# ------------------------------------------------------------------ copy and structure


def _string_args(tree: ast.AST, func: str, index: int) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func
            and len(node.args) > index
            and isinstance(node.args[index], ast.Constant)
        ):
            out.add(str(node.args[index].value))
    return out


def test_every_code_the_service_can_emit_has_plain_english_copy() -> None:
    tree = ast.parse((EXECUTOR_DIR / "measure_confirm.py").read_text())
    emitted = _string_args(tree, "_f", 0) | _string_args(tree, "MeasureRefusal", 1)
    emitted |= set(mc._CONFLICT_CODES) | set(mc._STRUCTURAL_CODES) | set(mc._LAKE_CODES)
    emitted |= {"measure_invalid", "measure_confirm_refused"}  # picked by a conditional in _refusal
    spec_tree = ast.parse((EXECUTOR_DIR / "measure_spec.py").read_text())
    for node in ast.walk(spec_tree):  # out.append(("code", "field")) in validate_measure_spec
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and node.args
            and isinstance(node.args[0], ast.Tuple)
            and isinstance(node.args[0].elts[0], ast.Constant)
        ):
            emitted.add(str(node.args[0].elts[0].value))
    assert len(emitted) > 25
    assert emitted <= set(MEASURE_REFUSAL_COPY), emitted - set(MEASURE_REFUSAL_COPY)
    for code, text in MEASURE_REFUSAL_COPY.items():
        assert text and text[0].isupper() and text.endswith("."), code
        assert "_" not in text, f"{code}: copy must not leak identifiers: {text}"
    assert MEASURE_REFUSAL_COPY["column_not_numeric"] == (
        "SUM and AVG need a numeric column; this column holds text. "
        "Fix its type when the data is loaded."
    )
    assert MEASURE_REFUSAL_COPY["stale_definition"] == (
        "This measure changed after you opened it. Reload and review it again."
    )
    assert MEASURE_REFUSAL_COPY["name_invalid"] == (
        "Use lowercase letters, digits and underscores, start with a letter, 2 to 48 characters."
    )
    assert MEASURE_REFUSAL_COPY["measure_does_not_bind"] == (
        "This measure could not be computed from the data as it is now."
    )


def test_an_unknown_code_has_no_way_to_become_a_failure() -> None:
    with pytest.raises(KeyError):
        mc._f("made_up_code")


def test_generative_ask_does_not_import_the_steward_service_so_there_is_no_cycle() -> None:
    tree = ast.parse((EXECUTOR_DIR / "generative_ask.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not {m for m in imported if "measure_confirm" in m or "measure_use" in m}, imported


def test_decide_measure_is_called_from_the_service_and_nowhere_else_in_the_executor() -> None:
    callers = sorted(
        p.name for p in EXECUTOR_DIR.rglob("*.py") if "decide_measure(" in p.read_text()
    )
    assert callers == ["measure_confirm.py", "space_ontology.py"]  # the service + the Protocol
    text = (EXECUTOR_DIR / "measure_confirm.py").read_text()
    assert text.count(".decide_measure(") == 2  # confirm and reject, each ledger-first inside it


def test_the_service_is_exported_for_the_api_layer() -> None:
    import dms_executor

    for name in (
        "propose_measures", "create_measure", "confirm_measure", "reject_measure",
        "measures_view", "MeasureRefusal", "MEASURE_REFUSAL_COPY",
    ):  # fmt: skip
        assert name in dms_executor.__all__ and hasattr(dms_executor, name), name
