"""ONTO-CONFIRM-01 / dms#283, step 2: migration 0006 and the core measure store.

No Postgres here (the PG twin is tests/control_plane/test_onto_confirm_01_pg.py).
Migration 0006 is run against a recording Alembic ``op``; the in-memory store
is exercised through the same ``decide_measure`` contract the Postgres store
implements. Nothing here is on the answer path, so there is no envelope
assertion; the envelope-level tests arrive with the steps that emit.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import threading
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from dms_core.control_plane.onto_store import (
    MEASURE_TRANSITIONS,
    MeasureDraft,
    MeasureNameTaken,
    MeasureNotFound,
    MeasureTransitionError,
    OntologyStore,
    OntoMeasure,
    SourceIdentity,
    measure_definition_hash,
    measure_transition_ok,
)

ROOT = Path(__file__).resolve().parents[1]
REV_0004 = ROOT / "alembic" / "versions" / "0004_ontology_store.py"
REV_0006 = ROOT / "alembic" / "versions" / "0006_onto_measure_confirm.py"
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


def test_migration_0006_chain_constraints_trigger_and_no_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load(REV_0006, "rev0006_onto_measure_confirm")
    assert mod.revision == "0006_onto_measure_confirm"
    assert mod.down_revision == "0005_onto_snapshot"
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


def test_migration_0004_text_untouched_by_0006() -> None:
    """0004's onto_measure CREATE TABLE is pinned by test_onto_store_01; 0006 never edits it."""
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
