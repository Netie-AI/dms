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
    ActorNotProvisioned,
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
    audits = len(store._audit)
    with pytest.raises(MeasureNameTaken):
        store.decide_measure(
            b.id, to_state="confirmed", expected_hash=b.definition_hash, name=a.name,
            actor=ACTOR, ledger=_ledger(box),
        )  # fmt: skip
    assert box == []
    assert store.get_measure(_space, b.id) == b and len(store._audit) == audits


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
    """``measure_confirm`` imports ``generative_ask`` (the one compile the ask uses), so
    ``generative_ask`` must never import ``measure_confirm``. It DOES import ``measure_use``
    since step 6 (``maybe_generative_ask`` prunes unusable measures); that edge is acyclic
    because ``measure_use`` imports neither (pinned in tests/test_measure_use.py)."""
    tree = ast.parse((EXECUTOR_DIR / "generative_ask.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not {m for m in imported if "measure_confirm" in m}, imported


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


# =============================================================================
# step 5: the API surface (routes/spaces.py -> wiring -> measure_confirm)
# =============================================================================
#
# Everything goes through the real FastAPI app (TestClient). Cortex is a recording stand-in
# that serves the ledger append and the chain read-back; the compliance gate is replaced
# by a recorder (the only seam every other route test uses) and each verdict Cortex can give
# is exercised. The in-memory ontology store is the store, so "nothing was written" is a
# comparison of every row and every audit entry before and after.

MEASURES_URL = "/ontology/measures"
ROUTE_TASKS = {
    "propose": "spaces.ontology.measures.propose",
    "create": "spaces.ontology.measures.create",
    "confirm": "spaces.ontology.measures.confirm",
    "reject": "spaces.ontology.measures.reject",
}
FORBIDDEN_BODY_FIELDS = [
    "expression", "sql", "filter", "actor", "confirmed_by", "steward_id", "tenant_id", "role",
    "state", "source", "ledger_entry_id", "user_id", "actor_user_id", "version_id", "signature",
]  # fmt: skip


class _ApiCortex:
    """What the API hands the ledger helpers: ``ledger_append`` and ``verify_ledger``."""

    base_url = "http://cortex.test"

    def __init__(self, ledger: _Ledger) -> None:
        self.ledger = ledger

    def ledger_append(self, req: Any) -> Any:
        return self.ledger.append(event_type=req.event_type, payload=req.payload, actor=req.actor)

    def verify_ledger(self) -> Any:
        return self.ledger.verify()


@dataclass
class _Api:
    env: _Env
    gate_calls: list[dict[str, Any]]
    verdict: dict[str, str | None]

    @property
    def client(self) -> Any:
        return self.env.rig.client

    @property
    def base(self) -> str:
        return f"/v1/spaces/{self.env.space}"

    def url(self, suffix: str = "") -> str:
        return f"{self.base}{MEASURES_URL}{suffix}"

    def settings(self, **over: Any) -> Any:
        from dms_api.settings import Settings, get_settings

        s = Settings(
            _env_file=None,  # type: ignore[call-arg]
            dms_ask_mode="live",
            dms_demo_fallback=False,
            dms_harness_ask_paths=True,
            **over,
        )
        self.env.rig.app.dependency_overrides[get_settings] = lambda: s
        return s

    def deny(self, reason: str | None) -> None:
        self.verdict["reason"] = reason

    def snapshot(self) -> tuple[Any, ...]:
        """Every measure row and every audit entry: equal before and after = nothing written."""
        rows = tuple(
            sorted(
                (str(m.id), m.state, m.name, m.definition_hash, m.ledger_entry_id or "")
                for m in self.env.store.measures_for_space(self.env.sid)
            )
        )
        audit = tuple(a.action_type for a in self.env.store._audit)
        return rows, audit, len(self.env.ledger.appended)

    def get(self, suffix: str = "") -> Any:
        return self.client.get(self.url(suffix))

    def post(self, suffix: str = "", body: Any = None, **kw: Any) -> Any:
        return self.client.post(self.url(suffix), json={} if body is None else body, **kw)

    def propose(self) -> dict[str, dict[str, Any]]:
        r = self.post("/propose")
        assert r.status_code == 200, r.text
        return {m["name"]: m for m in r.json()["created"]}

    def confirm(self, view: dict[str, Any], **body: Any) -> Any:
        body.setdefault("definition_hash", view["definition_hash"])
        return self.post(f"/{view['id']}/confirm", body)

    def reject(self, view: dict[str, Any], reason: str = "not needed") -> Any:
        return self.post(f"/{view['id']}/reject", {"reason": reason})

    def detail(self, r: Any) -> dict[str, Any]:
        d = r.json()["detail"]
        assert isinstance(d, dict), d
        return d


@pytest.fixture()
def api(env: _Env, monkeypatch: pytest.MonkeyPatch) -> _Api:
    import dms_api.routes.spaces as space_routes
    from cortex_client.gate import ComplianceDecision

    calls: list[dict[str, Any]] = []
    verdict: dict[str, str | None] = {"reason": None}

    def gate(*, action: str, **kw: Any) -> Any:
        calls.append({"action": action, **kw})
        reason = verdict["reason"]
        if reason is None:
            return ComplianceDecision(allowed=True, reason="test_allow", action=action)
        return ComplianceDecision(allowed=False, reason=reason, action=action)

    monkeypatch.setattr(space_routes, "compliance_gate", gate)
    env.rig.app.state.cortex = _ApiCortex(env.ledger)
    out = _Api(env=env, gate_calls=calls, verdict=verdict)
    out.settings()  # the steward config the rig already used, made explicit
    return out


def _all_four(api: _Api, view: dict[str, Any]) -> dict[str, Any]:
    """One request per mutation route, each valid on its own, keyed by route name."""
    return {
        "propose": lambda: api.post("/propose"),
        "create": lambda: api.post(
            "",
            {
                "name": "api_probe", "grain": SCHOOLS, "aggregate": "count", "column": "*",
                "description": "Schools.",
            },
        ),
        "confirm": lambda: api.confirm(view),
        "reject": lambda: api.reject(view),
    }  # fmt: skip


# ------------------------------------------------------------------ the gate and the role


@pytest.mark.parametrize("reason", ["gate_denied", "gate_task_unknown", "gate_unavailable"])
@pytest.mark.parametrize("route", sorted(ROUTE_TASKS))
def test_a_gate_that_denies_or_cannot_decide_is_a_403_and_writes_nothing(
    api: _Api, route: str, reason: str
) -> None:
    view = api.propose()["schools_count"]
    api.deny(reason)
    before = api.snapshot()
    api.gate_calls.clear()
    r = _all_four(api, view)[route]()
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == reason
    assert [c["action"] for c in api.gate_calls] == [ROUTE_TASKS[route]]
    assert api.snapshot() == before  # no row, no audit entry, no ledger append


def test_the_gate_is_asked_with_the_task_id_and_the_server_resolved_actor(api: _Api) -> None:
    s = api.settings(dms_actor_user_id="cccccccc-cccc-cccc-cccc-cccccccccccc")
    view = api.propose()["schools_count"]
    assert api.confirm(view).status_code == 200
    assert api.reject(view).status_code == 200
    calls = {c["action"]: c for c in api.gate_calls}
    assert set(calls) == {ROUTE_TASKS["propose"], ROUTE_TASKS["confirm"], ROUTE_TASKS["reject"]}
    for task, c in calls.items():
        assert c["actor"] == s.dms_actor_user_id
        assert c["metadata"]["task_id"] == task
        assert c["metadata"]["space_id"] == api.env.space
        assert c["metadata"]["actor_role"] == "steward"
    assert calls[ROUTE_TASKS["confirm"]]["metadata"]["measure_id"] == view["id"]
    # and the ledger actor is that same configured actor, never anything from the request
    assert {e["actor"] for e in api.env.ledger.appended} == {s.dms_actor_user_id}


@pytest.mark.parametrize("role", ["viewer", "analyst", "Steward", "ADMIN", "", "steward "])
@pytest.mark.parametrize("route", sorted(ROUTE_TASKS))
def test_a_viewer_or_an_unknown_role_cannot_decide_and_is_refused_before_the_gate(
    api: _Api, route: str, role: str
) -> None:
    view = api.propose()["schools_count"]
    api.settings(dms_actor_role=role)
    before = api.snapshot()
    api.gate_calls.clear()
    r = _all_four(api, view)[route]()
    assert r.status_code == 403, r.text
    d = api.detail(r)
    assert d["code"] == "steward_role_required"
    assert d["message"] == MEASURE_REFUSAL_COPY["steward_role_required"] and d["failed"] == []
    assert api.gate_calls == []  # the role is checked before Cortex is bothered
    assert api.snapshot() == before


def test_an_admin_may_decide_and_a_viewer_can_still_read(api: _Api) -> None:
    api.settings(dms_actor_role="admin")
    view = api.propose()["schools_count"]
    assert api.confirm(view).status_code == 200
    api.settings(dms_actor_role="viewer")
    r = api.get()
    assert r.status_code == 200
    assert r.json()["viewer"] == {"role": "viewer", "can_decide": False}


@pytest.mark.parametrize("header", ["x-dms-role", "x-dms-actor-id", "x-dms-tenant-id"])
@pytest.mark.parametrize("route", ["get", *sorted(ROUTE_TASKS)])
def test_a_request_that_names_its_own_identity_is_a_400(
    api: _Api, route: str, header: str
) -> None:
    view = api.propose()["schools_count"]
    before = api.snapshot()
    headers = {header: "steward" if header == "x-dms-role" else str(uuid4())}
    if route == "get":
        r = api.client.get(api.url(), headers=headers)
    else:
        r = api.client.post(
            api.url(_suffix(route, view)), json=_valid_body(route, view), headers=headers
        )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "identity_header_not_accepted"
    assert api.snapshot() == before


# ------------------------------------------------------------------ bodies


def _valid_body(route: str, view: dict[str, Any]) -> dict[str, Any]:
    return {
        "propose": {},
        "create": {
            "name": "api_probe", "grain": SCHOOLS, "aggregate": "count", "column": "*",
            "description": "Schools.",
        },
        "confirm": {"definition_hash": view["definition_hash"]},
        "reject": {"reason": "no"},
    }[route]  # fmt: skip


def _suffix(route: str, view: dict[str, Any]) -> str:
    return {
        "propose": "/propose", "create": "",
        "confirm": f"/{view['id']}/confirm", "reject": f"/{view['id']}/reject",
    }[route]  # fmt: skip


@pytest.mark.parametrize("field", FORBIDDEN_BODY_FIELDS + ["definition_hash"])
@pytest.mark.parametrize("route", ["create", "reject", "propose"])
def test_a_body_that_names_a_field_the_server_owns_is_a_422_and_stores_nothing(
    api: _Api, route: str, field: str
) -> None:
    view = api.propose()["schools_count"]
    body = {**_valid_body(route, view), field: "x"}
    before = api.snapshot()
    api.gate_calls.clear()
    r = api.post(_suffix(route, view), body)
    assert r.status_code == 422, r.text
    assert any(e["type"] == "extra_forbidden" for e in r.json()["detail"])
    assert api.gate_calls == []  # refused at the door: not even a gate call
    assert api.snapshot() == before


@pytest.mark.parametrize("field", FORBIDDEN_BODY_FIELDS)
def test_confirm_takes_only_hash_name_and_description(api: _Api, field: str) -> None:
    view = api.propose()["schools_count"]
    before = api.snapshot()
    r = api.confirm(view, **{field: "x"})
    assert r.status_code == 422, r.text
    assert any(e["type"] == "extra_forbidden" for e in r.json()["detail"])
    assert api.snapshot() == before


def test_a_body_with_the_wrong_shape_is_a_422_and_stores_nothing(api: _Api) -> None:
    view = api.propose()["schools_count"]
    before = api.snapshot()
    for suffix, body in [
        ("", {"name": "x_y"}),  # missing grain, aggregate, description
        ("", {"name": 5, "grain": SCHOOLS, "aggregate": "count", "description": "d"}),
        (f"/{view['id']}/confirm", {}),
        (f"/{view['id']}/confirm", {"definition_hash": ["a"]}),
        (f"/{view['id']}/reject", {}),
        ("/propose", {"limit": 3}),
    ]:
        assert api.post(suffix, body).status_code == 422, (suffix, body)
    assert api.snapshot() == before


# ------------------------------------------------------------------ ids and Spaces


def test_an_unknown_space_is_a_404_before_roles_and_gates(api: _Api) -> None:
    api.settings(dms_actor_role="viewer")
    bad = f"/v1/spaces/{uuid4()}{MEASURES_URL}"
    for method, suffix, body in [
        ("get", "", None),
        ("post", "/propose", {}),
        ("post", "", {"name": "a_b", "grain": SCHOOLS, "aggregate": "count", "description": "x"}),
        ("post", f"/{uuid4()}/confirm", {"definition_hash": "0" * 64}),
        ("post", f"/{uuid4()}/reject", {"reason": "x"}),
    ]:
        r = getattr(api.client, method)(bad + suffix, **({} if body is None else {"json": body}))
        assert r.status_code == 404, (suffix, r.text)
        assert api.detail(r)["code"] == "space_not_found"
    assert api.gate_calls == []


def test_a_measure_id_of_another_space_or_nowhere_is_a_404_and_nothing_changes(
    api: _Api,
) -> None:
    mine = api.propose()["schools_count"]
    other = api.env.rig.space("Another")  # a real Space, with no ontology and no measures
    before = api.snapshot()
    for mid in (mine["id"], str(uuid4()), "not-a-uuid"):
        for suffix, body in [
            (f"/{mid}/confirm", {"definition_hash": mine["definition_hash"]}),
            (f"/{mid}/reject", {"reason": "x"}),
        ]:
            r = api.client.post(f"/v1/spaces/{other}{MEASURES_URL}{suffix}", json=body)
            assert r.status_code == 404, (mid, suffix, r.text)
            assert api.detail(r)["code"] == "measure_not_found"
    r = api.client.post(api.url(f"/{uuid4()}/confirm"), json={"definition_hash": "0" * 64})
    assert r.status_code == 404 and api.detail(r)["code"] == "measure_not_found"
    r = api.client.post(api.url("/not-a-uuid/reject"), json={"reason": "x"})
    assert r.status_code == 404 and api.detail(r)["code"] == "measure_not_found"
    assert api.snapshot() == before  # not even a measure.confirm_refused audit row


# ------------------------------------------------------------------ propose and manual entry


def test_propose_over_http_is_idempotent_deterministic_and_unledgered(api: _Api) -> None:
    first = api.post("/propose")
    assert first.status_code == 200, first.text
    body = first.json()
    assert {m["name"] for m in body["created"]} == {
        "schools_budget_sum", "schools_count", "districts_count",
    }  # fmt: skip
    assert all(m["state"] == "proposed" and m["source"] == "derived" for m in body["created"])
    assert api.env.ledger.appended == []  # a proposal is not authority
    again = api.post("/propose").json()
    assert again["created"] == []
    assert len(api.env.store.measures_for_space(api.env.sid)) == 3
    assert api.env.audit("measure.propose") == ["measure.propose"]
    # an empty JSON body and no body at all are the same request
    assert api.client.post(api.url("/propose")).status_code == 200


def test_manual_entry_over_http_is_201_proposed_only_and_200_when_repeated(api: _Api) -> None:
    body = {
        "name": "biggest_budget", "grain": SCHOOLS, "aggregate": "max", "column": "budget",
        "description": "The largest school budget.",
    }  # fmt: skip
    r = api.post("", body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["created"] is True
    m = out["measure"]
    assert m["state"] == "proposed" and m["source"] == "manual" and m["decided_by"] is None
    assert m["answers_certified"] is False and m["ledger_entry_id"] is None
    assert Decimal(m["evidence"]["probe_value"]) == Decimal("200.25")  # the lake's own number
    assert api.env.ledger.appended == []
    again = api.post("", body)
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["measure"]["id"] == m["id"]
    assert len(api.env.store.measures_for_space(api.env.sid)) == 1
    # a column omitted means COUNT(*)
    star = api.post("", {"name": "all_schools", "grain": SCHOOLS, "aggregate": "count",
                         "description": "Every school."})  # fmt: skip
    assert star.status_code == 201 and star.json()["measure"]["column"] == "*"


@pytest.mark.parametrize(
    ("kw", "status", "top", "codes"),
    [
        ({"aggregate": "median", "column": "budget"}, 422, "measure_invalid",
         {"aggregate_invalid"}),
        ({"aggregate": "sum", "column": "name"}, 422, "measure_invalid",
         {"column_not_numeric"}),
        ({"aggregate": "sum", "column": "school_id"}, 422, "measure_invalid",
         {"key_column_aggregate"}),
        ({"aggregate": "sum", "column": "nope"}, 422, "measure_invalid", {"column_missing"}),
        ({"name": "Bad Name"}, 422, "measure_invalid", {"name_invalid"}),
        ({"name": "budget"}, 422, "measure_invalid", {"name_reserved"}),
        ({"description": "select a from b"}, 422, "measure_invalid", {"description_invalid"}),
        ({"description": ""}, 422, "measure_invalid", {"description_invalid"}),
        ({"description": "x" * 201}, 422, "measure_invalid", {"description_invalid"}),
        ({"grain": "bronze.nowhere"}, 422, "unknown_object", {"unknown_object"}),
        ({"name": "schools_count", "aggregate": "count_distinct", "column": "name"}, 409,
         "name_taken", {"name_taken"}),
    ],
)  # fmt: skip
def test_manual_entry_refusals_are_named_with_plain_copy_and_store_nothing(
    api: _Api, kw: dict[str, Any], status: int, top: str, codes: set[str]
) -> None:
    api.propose()
    before = api.snapshot()
    body = {
        "name": "manual_probe", "grain": SCHOOLS, "aggregate": "count", "column": "*",
        "description": "Schools.",
    }  # fmt: skip
    body.update(kw)
    r = api.post("", body)
    assert r.status_code == status, r.text
    d = api.detail(r)
    assert d["code"] == top and d["message"] == MEASURE_REFUSAL_COPY[top]
    assert codes <= {f["code"] for f in d["failed"]}
    for f in d["failed"]:
        assert f["message"] == MEASURE_REFUSAL_COPY[f["code"]]
    assert api.snapshot() == before


def test_manual_entry_cannot_smuggle_sql_through_any_declared_field(api: _Api) -> None:
    before = api.snapshot()
    for kw in (
        {"column": 'budget") FROM bronze.public_schools; DROP TABLE x; --'},
        {"column": "budget); SELECT 1"},
        {"name": "x; drop table t"},
        {"grain": "bronze.public_schools; select 1"},
        {"aggregate": "sum(budget)/count(*)", "column": "budget"},
    ):
        body = {
            "name": "sneaky_one", "grain": SCHOOLS, "aggregate": "sum", "column": "budget",
            "description": "Budget.",
        }  # fmt: skip
        body.update(kw)
        r = api.post("", body)
        assert r.status_code in (409, 422), (kw, r.text)
    assert api.snapshot() == before
    assert _oracle(api.env.lake, "SELECT COUNT(*) AS n FROM bronze.public_schools") == [{"n": 4}]


# ------------------------------------------------------------------ confirm


def test_confirm_over_http_writes_one_ledger_entry_with_pointers_and_the_view_agrees(
    api: _Api,
) -> None:
    view = api.propose()["schools_budget_sum"]
    r = api.confirm(view)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["already"] is False
    m = out["measure"]
    assert m["state"] == "confirmed" and m["ledger_entry_id"] == "led_m1"
    assert m["decided_by"] == api.settings().dms_actor_user_id
    assert m["answers_certified"] is True
    assert Decimal(m["evidence"]["probe_value"]) == Decimal(
        str(_oracle(api.env.lake, "SELECT SUM(budget) AS t FROM bronze.public_schools")[0]["t"])
    )  # the number a steward saw is the lake's own: 360.75
    [entry] = api.env.ledger.appended
    assert entry["event_type"] == "ontology.measure.confirmed"
    assert entry["actor"] == api.settings().dms_actor_user_id
    assert entry["payload"]["measure_id"] == view["id"]
    assert entry["payload"]["definition_hash"] == view["definition_hash"]
    assert entry["payload"]["to_state"] == "confirmed"
    assert "probe_value" not in str(entry["payload"]) and "360.75" not in str(entry["payload"])
    assert api.env.ledger.verifies == 1  # the chain was read back before the row changed
    assert api.env.audit("measure.confirm") == ["measure.confirm"]
    listed = {x["name"]: x for x in api.get().json()["measures"]}["schools_budget_sum"]
    assert listed["state"] == "confirmed" and listed["ledger_entry_id"] == "led_m1"


def test_confirm_twice_is_200_already_with_exactly_one_ledger_append(api: _Api) -> None:
    view = api.propose()["schools_count"]
    first = api.confirm(view)
    second = api.confirm(view)
    assert first.status_code == 200 and first.json()["already"] is False
    assert second.status_code == 200 and second.json()["already"] is True
    assert second.json()["measure"]["ledger_entry_id"] == first.json()["measure"]["ledger_entry_id"]
    assert len(api.env.ledger.appended) == 1
    assert api.env.audit("measure.confirm") == ["measure.confirm"]


def test_rename_at_confirm_changes_the_hash_and_a_confirmed_one_cannot_be_reworded(
    api: _Api,
) -> None:
    view = api.propose()["schools_budget_sum"]
    r = api.confirm(
        view, name="school_budget_total", description="The total of all school budgets."
    )
    assert r.status_code == 200, r.text
    m = r.json()["measure"]
    assert m["name"] == "school_budget_total" and m["definition_hash"] != view["definition_hash"]
    assert m["definition_hash"] == measure_definition_hash(
        "school_budget_total", SCHOOLS, "sum", "budget", "The total of all school budgets."
    )
    # the old hash is stale now; the new wording is final
    stale = api.post(f"/{view['id']}/confirm", {"definition_hash": view["definition_hash"]})
    assert stale.status_code == 409 and api.detail(stale)["code"] == "stale_definition"
    reword = api.post(
        f"/{view['id']}/confirm",
        {"definition_hash": m["definition_hash"], "name": "something_else"},
    )
    assert reword.status_code == 409
    assert api.detail(reword)["code"] == "measure_confirmed_immutable"
    assert len(api.env.ledger.appended) == 1


def test_a_stale_hash_is_a_409_that_carries_the_current_row_and_changes_nothing(
    api: _Api,
) -> None:
    view = api.propose()["schools_count"]
    before = api.snapshot()
    r = api.post(f"/{view['id']}/confirm", {"definition_hash": "0" * 64})
    assert r.status_code == 409
    d = api.detail(r)
    assert d["code"] == "stale_definition" and d["message"] == MEASURE_REFUSAL_COPY[d["code"]]
    assert d["current"]["id"] == view["id"] and d["current"]["state"] == "proposed"
    assert d["current"]["definition_hash"] == view["definition_hash"]
    rows, audit, appended = api.snapshot()
    assert (rows, appended) == (before[0], before[2]) and appended == 0
    assert audit == (*before[1], "measure.confirm_refused")  # the refusal is audited, nothing else


def test_confirm_returns_every_failed_criterion_together_and_burns_nothing(api: _Api) -> None:
    view = api.propose()["schools_budget_sum"]
    _lake_exec(api.env.lake, "UPDATE bronze.public_schools SET budget = NULL")
    r = api.confirm(view, name="Bad Name", description="select a from b")
    assert r.status_code == 422, r.text
    d = api.detail(r)
    assert d["code"] == "measure_invalid"
    codes = {f["code"] for f in d["failed"]}
    assert {"name_invalid", "description_invalid", "column_all_null"} <= codes
    assert all(f["message"] == MEASURE_REFUSAL_COPY[f["code"]] for f in d["failed"])
    assert api.env.ledger.appended == []
    assert api.env.audit("measure.confirm_refused") == ["measure.confirm_refused"]
    # fix the data and the very same measure confirms: the refusal did not burn the row
    _lake_exec(api.env.lake, "UPDATE bronze.public_schools SET budget = 1")
    assert api.confirm(view).status_code == 200


@pytest.mark.parametrize("fault", ["append_raises", "chain_does_not_verify", "no_chain_hash"])
@pytest.mark.parametrize("route", ["confirm", "reject"])
def test_a_ledger_fault_is_a_503_and_the_measure_is_exactly_as_it_was(
    api: _Api, route: str, fault: str
) -> None:
    view = api.propose()["schools_count"]
    if route == "reject":
        assert api.confirm(view).status_code == 200  # withdrawal also needs the ledger
    ledger = api.env.ledger
    if fault == "append_raises":
        ledger.append_error = RuntimeError("cortex down")
    elif fault == "chain_does_not_verify":
        ledger.verify_ok = False
    else:
        ledger.hash_is_entry_id = True
    state_before = api.env.row(view["id"]).state
    before = api.snapshot()
    r = _all_four(api, view)[route]()
    assert r.status_code == 503, r.text
    d = api.detail(r)
    assert d["code"] == "ledger_append_failed" and d["message"] == MEASURE_REFUSAL_COPY[d["code"]]
    assert api.env.row(view["id"]).state == state_before
    rows, audit, _appended = api.snapshot()
    assert (rows, audit) == before[:2]  # no decision row, no audit entry, no pointer
    assert "led_m" not in str(api.get().json()["measures"]) or state_before == "confirmed"


def test_a_ledger_that_recovers_lets_the_same_measure_through(api: _Api) -> None:
    view = api.propose()["schools_count"]
    api.env.ledger.append_error = RuntimeError("down")
    assert api.confirm(view).status_code == 503
    api.env.ledger.append_error = None
    ok = api.confirm(view)
    assert ok.status_code == 200 and ok.json()["measure"]["state"] == "confirmed"
    assert len(api.env.ledger.appended) == 1


def test_no_cortex_client_is_a_named_503_before_any_write_even_if_the_gate_passes(
    api: _Api,
) -> None:
    view = api.propose()["schools_count"]
    api.env.rig.app.state.cortex = None
    before = api.snapshot()
    for route in ROUTE_TASKS:
        r = _all_four(api, view)[route]()
        assert r.status_code == 503, (route, r.text)
        assert api.detail(r)["code"] == "ledger_unavailable"
    assert api.snapshot() == before


# ------------------------------------------------------------------ reject


def test_reject_needs_a_reason_withdraws_a_confirmed_measure_and_is_idempotent(api: _Api) -> None:
    views = api.propose()
    before = api.snapshot()
    for reason in ("", "   ", "bad\nnews", "x" * 201):
        r = api.reject(views["districts_count"], reason)
        assert r.status_code == 422, (reason, r.text)
        assert api.detail(r)["code"] == "reason_invalid"
    assert api.snapshot() == before
    # reject a proposal
    r = api.reject(views["districts_count"], "not a figure we publish")
    assert r.status_code == 200 and r.json()["already"] is False
    m = r.json()["measure"]
    assert m["state"] == "rejected" and m["decision_reason"] == "not a figure we publish"
    assert m["ledger_entry_id"] and m["answers_certified"] is False
    n = len(api.env.ledger.appended)
    again = api.reject(views["districts_count"], "different words")
    assert again.status_code == 200 and again.json()["already"] is True
    assert len(api.env.ledger.appended) == n
    assert again.json()["measure"]["decision_reason"] == "not a figure we publish"
    # withdraw a confirmed one
    assert api.confirm(views["schools_count"]).status_code == 200
    w = api.reject(views["schools_count"], "definition changed upstream")
    assert w.status_code == 200 and w.json()["measure"]["state"] == "rejected"
    events = [e["event_type"] for e in api.env.ledger.appended]
    assert events == [
        "ontology.measure.rejected",
        "ontology.measure.confirmed",
        "ontology.measure.rejected",
    ]
    # the steward's typed reason never goes to the ledger, only to the row and the audit
    assert "upstream" not in str(api.env.ledger.appended)
    # a rejected measure is not resurrected by proposing again, and can be confirmed again
    assert api.post("/propose").json()["created"] == []
    back = api.confirm(views["schools_count"])
    assert back.status_code == 200 and back.json()["measure"]["state"] == "confirmed"


# ------------------------------------------------------------------ the read surface


def test_get_lists_counts_limits_viewer_and_persistence(api: _Api) -> None:
    views = api.propose()
    api.confirm(views["schools_count"])
    api.reject(views["districts_count"], "later")
    r = api.get()
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["space_id"] == api.env.space
    assert v["persisted"] is False  # the in-memory ontology store is bound: say so
    assert v["counts"] == {"proposed": 1, "confirmed": 1, "rejected": 1}
    assert v["limits"] == {"confirmed_max": 80}
    assert v["viewer"] == {"role": "steward", "can_decide": True}
    assert len(v["versions"]) == 1 and v["drift"] == []
    by = {m["name"]: m for m in v["measures"]}
    assert by["schools_count"]["answers_certified"] is True
    assert by["schools_budget_sum"]["answers_certified"] is False
    assert by["districts_count"]["state"] == "rejected"
    assert by["schools_budget_sum"]["definition"] == (
        "SUM of budget per bronze.public_schools row"
    )


def test_evidence_values_are_hidden_from_a_deployment_that_cannot_decide(api: _Api) -> None:
    api.propose()
    steward = {m["name"]: m for m in api.get().json()["measures"]}["schools_budget_sum"]
    assert Decimal(steward["evidence"]["probe_value"]) == Decimal("360.75")
    assert "SUM" in steward["evidence"]["probe_sql"]
    api.settings(dms_actor_role="viewer")
    r = api.get()
    assert r.status_code == 200
    viewer = {m["name"]: m for m in r.json()["measures"]}["schools_budget_sum"]
    assert "probe_value" not in viewer["evidence"] and "probe_sql" not in viewer["evidence"]
    assert viewer["evidence"]["grain_rows"] == 4  # counts and booleans stay
    assert "360.75" not in r.text
    api.settings(dms_actor_role="not_a_role")
    assert "360.75" not in api.get().text


def test_the_ontology_view_carries_the_compact_measures_and_the_new_note(api: _Api) -> None:
    views = api.propose()
    api.confirm(views["schools_count"])
    api.reject(views["districts_count"], "later")
    r = api.client.get(f"{api.base}/ontology")
    assert r.status_code == 200, r.text
    [onto] = r.json()["ontologies"]
    assert onto["measures"] == sorted(onto["measures"], key=lambda m: m["name"])
    got = {m["name"]: m for m in onto["measures"]}
    assert set(got) == {"schools_count", "schools_budget_sum", "districts_count"}
    for m in got.values():
        assert set(m) == {"id", "name", "state", "answers_certified"}  # no evidence here
    assert got["schools_count"] == {
        "id": views["schools_count"]["id"], "name": "schools_count",
        "state": "confirmed", "answers_certified": True,
    }  # fmt: skip
    assert got["districts_count"]["state"] == "rejected"
    assert got["districts_count"]["answers_certified"] is False
    assert got["schools_budget_sum"] == {
        "id": views["schools_budget_sum"]["id"], "name": "schools_budget_sum",
        "state": "proposed", "answers_certified": False,
    }  # fmt: skip
    note = onto["measures_note"]
    assert "no measures are derived" not in note
    assert "no_declared_measure" in note and "proposed or rejected" in note
    assert "360.75" not in r.text  # the compact list is not an evidence surface


def test_a_confirmed_measure_whose_data_moved_is_listed_unusable_with_the_reason(
    api: _Api,
) -> None:
    view = api.propose()["schools_budget_sum"]
    api.confirm(view)
    body = api.env.snapshot().body
    edited = {**body, "objects": {**body["objects"]}}
    spec = dict(edited["objects"][SCHOOLS])
    spec["attributes"] = [a for a in spec["attributes"] if a["name"] != "budget"]
    edited["objects"][SCHOOLS] = spec
    api.env.store.record_snapshot(api.env.version().id, body=edited, violations=[], verified=True)
    m = {x["name"]: x for x in api.get().json()["measures"]}["schools_budget_sum"]
    assert (m["state"], m["usable"], m["unusable_reason"]) == ("confirmed", False, "column_missing")
    assert m["answers_certified"] is False
    [onto] = api.client.get(f"{api.base}/ontology").json()["ontologies"]
    certified = {x["name"]: x["answers_certified"] for x in onto["measures"]}
    assert certified["schools_budget_sum"] is False


# ------------------------------------------------------------------ store and lake failures


def test_a_store_that_cannot_be_read_is_a_named_503_never_an_empty_list(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = api.propose()["schools_count"]

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("db down: password=hunter2")

    monkeypatch.setattr(api.env.store, "measures_for_space", boom)
    for r in (
        api.get(),
        api.post("/propose"),
        api.confirm(view),
        api.reject(view),
        api.client.get(f"{api.base}/ontology"),
    ):
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "ontology_store_unavailable"
        assert "hunter2" not in r.text  # the cause is logged, never returned
    assert api.env.ledger.appended == []


def test_a_warehouse_that_cannot_be_read_is_a_503_and_changes_nothing(
    api: _Api, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = api.propose()["schools_count"]
    before = api.snapshot()
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "gone.duckdb"))
    # (propose would be a no-op here: every candidate already exists, so it reads no lake)
    new = {"name": "api_probe", "grain": SCHOOLS, "aggregate": "count", "description": "Schools."}
    for r in (api.post("", new), api.confirm(view)):
        assert r.status_code == 503, r.text
        assert api.detail(r)["code"] == "warehouse_unavailable"
    assert api.snapshot() == before
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(api.env.lake))
    assert api.post("", new).status_code == 201  # the same request works once the lake is back


class _FkViolation(Exception):
    """What psycopg raises for a foreign key: a sqlstate and a diag naming the constraint."""

    sqlstate = "23503"

    def __init__(self, constraint: str) -> None:
        super().__init__("insert or update violates foreign key constraint")
        self.diag = SimpleNamespace(constraint_name=constraint)


def test_an_actor_without_a_user_row_is_actor_not_provisioned_not_a_broken_store(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = api.propose()["schools_count"]
    real = api.env.store.decide_measure

    def refuses(*a: Any, **k: Any) -> Any:
        raise _FkViolation("onto_measure_decided_by_fkey")

    monkeypatch.setattr(api.env.store, "decide_measure", refuses)
    for r in (api.confirm(view), api.reject(view)):
        assert r.status_code == 503, r.text
        d = api.detail(r)
        assert d["code"] == "actor_not_provisioned"
        assert d["message"] == MEASURE_REFUSAL_COPY["actor_not_provisioned"]
    assert api.env.row(view["id"]).state == "proposed"
    # a foreign key on anything else is the store failing, not the actor
    monkeypatch.setattr(
        api.env.store,
        "decide_measure",
        lambda *a, **k: (_ for _ in ()).throw(_FkViolation("onto_measure_version_id_fkey")),
    )
    r = api.confirm(view)
    assert r.status_code == 503 and api.detail(r)["code"] == "ontology_store_unavailable"
    monkeypatch.setattr(api.env.store, "decide_measure", real)
    assert api.confirm(view).status_code == 200


def test_an_unprovisioned_actor_is_a_named_503_and_the_ledger_is_never_written(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a stand-in error: the store itself refuses an actor with no user row, and it does
    so before the ledger, so Cortex never holds a "confirmed" entry for a measure that stays
    proposed."""
    view = api.propose()["schools_count"]
    before = api.snapshot()
    monkeypatch.setattr(api.env.store, "_actors", frozenset({uuid4()}))
    for r in (api.confirm(view), api.reject(view)):
        assert r.status_code == 503, r.text
        d = api.detail(r)
        assert d["code"] == "actor_not_provisioned"
        assert d["message"] == MEASURE_REFUSAL_COPY["actor_not_provisioned"]
    assert api.env.ledger.appended == []
    assert api.snapshot() == before
    assert api.env.row(view["id"]).state == "proposed"
    configured = UUID(api.settings().dms_actor_user_id)
    monkeypatch.setattr(api.env.store, "_actors", frozenset({configured}))
    assert api.confirm(view).status_code == 200
    assert len(api.env.ledger.appended) == 1


def test_the_store_refuses_a_stranger_and_a_blank_entry_id_before_changing_anything() -> None:
    store = OntologyStore(actors={ACTOR})
    space = uuid4()
    vid = store.reconnect(
        space_id=space, identity=SourceIdentity("postgres", "h", "d", "public"), fingerprint="f"
    ).id
    m = _propose(store, vid)
    box: list[dict[str, Any]] = []
    for to_state, kw in (("confirmed", {}), ("rejected", {"reason": "no"})):
        with pytest.raises(ActorNotProvisioned):
            store.decide_measure(
                m.id, to_state=to_state, expected_hash=m.definition_hash, actor=uuid4(),
                ledger=_ledger(box), **kw,
            )  # fmt: skip
    assert box == [] and store.get_measure(space, m.id) == m
    for blank in ("", "  ", "\n\t"):
        with pytest.raises(ValueError, match="no entry id"):
            store.decide_measure(
                m.id, to_state="confirmed", expected_hash=m.definition_hash, actor=ACTOR,
                ledger=lambda _p, b=blank: b,
            )  # fmt: skip
    assert store.get_measure(space, m.id) == m


def test_a_configured_actor_that_is_not_a_uuid_cannot_be_a_user_row(api: _Api) -> None:
    view = api.propose()["schools_count"]
    api.settings(dms_actor_user_id="steward-one")
    before = api.snapshot()
    for route in ROUTE_TASKS:
        r = _all_four(api, view)[route]()
        assert r.status_code == 503, (route, r.text)
        assert api.detail(r)["code"] == "actor_not_provisioned"
    assert api.snapshot() == before


# ------------------------------------------------------------------ source-level properties


def test_the_routes_reach_the_executor_only_through_wiring_and_the_gate_is_in_each_route() -> None:
    src = (ROOT / "apps" / "api" / "dms_api" / "routes" / "spaces.py").read_text()
    tree = ast.parse(src)
    assert "dms_executor" not in src and "duckdb" not in src
    routes = {
        n.name: n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr in ("post", "get")
            for d in n.decorator_list
        )
    }  # fmt: skip
    mutations = {
        "propose_space_measures", "create_space_measure",
        "confirm_space_measure", "reject_space_measure",
    }  # fmt: skip
    assert mutations <= set(routes) and "list_space_measures" in routes
    for name in mutations:
        calls = [
            c for c in ast.walk(routes[name])
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        ]  # fmt: skip
        order = [c.func.id for c in calls if isinstance(c.func, ast.Name)]
        assert "compliance_gate" in order and "enforce" in order, name
        gate = order.index("compliance_gate")
        # nothing that touches the store is called before the gate is asked and enforced
        for later in ("measures_propose", "measures_create", "measures_confirm", "measures_reject"):
            if later in order:
                assert order.index(later) > order.index("enforce") > gate, name
    # a role check never carries a policy-sounding name (the invariants forbid local gates)
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_require_steward" in names
    assert not [n for n in names if re.search(r"(^|_)(gate|policy|authorize)(_|$)", n)]
