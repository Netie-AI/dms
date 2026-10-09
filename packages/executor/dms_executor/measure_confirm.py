"""Steward service for measures (ONTO-CONFIRM-01 / dms#283).

Propose, enter by hand, confirm, reject, and read measures of a SQL-source Space.
A measure is a structured spec (``measure_spec``); this module is where a spec meets
the lake and the ledger:

* ``propose_measure_drafts`` is the deterministic proposer (pure, bounded, no lake).
* ``measure_evidence`` runs a candidate through the SAME compile the ask path uses and
  executes the compiled SQL once, so what a steward sees at confirm time is the number
  the ask will compute (``compile_plan_maybe_unverified`` is the shared seam).
* ``evaluate_confirm_criteria`` is one function: its static part feeds the list view's
  ``confirm_blockers``, its full form runs at confirm. Every failure is returned together.
* ``confirm_measure`` / ``reject_measure`` are the only callers of the store's
  single write path; the ledger callable runs INSIDE it, so a ledger failure leaves the
  measure exactly as it was.

Nothing here edits a stored definition and nothing here accepts SQL. Identity (the actor)
is passed in by the caller from configuration; this module never reads a request.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from dms_core.control_plane.onto_store import (
    MeasureDraft,
    MeasureNameTaken,
    MeasureNotFound,
    MeasureTransitionError,
    OntologySnapshot,
    OntologyVersion,
    OntoMeasure,
    measure_definition_hash,
)

from dms_executor.demo_warehouse import _lock_for, connect_file, warehouse_path
from dms_executor.duckdb_scalar import scalar_int
from dms_executor.generative_ask import (
    QueryPlan,
    cached_verify_violations,
    compile_plan_maybe_unverified,
    load_verified_ontology,
    untyped_numeric_reason,
    validate_compiled_sql,
)
from dms_executor.measure_basis import MAX_CONFIRMED_MEASURES, MAX_PROPOSED_PER_RUN
from dms_executor.measure_spec import (
    MeasureSpecDraft,
    definition_hash,
    definition_text,
    is_numeric_type,
    validate_measure_spec,
)
from dms_executor.ontology import (
    CompiledQuery,
    MeasureProvenance,
    Ontology,
    Refusal,
    _ident,
    measure_expression,
)
from dms_executor.promote import _append_hash, _verify_ok
from dms_executor.space_ontology import (
    OntologyStorePort,
    load_space_ontology,
    ontology_store,
    space_uuid,
)

__all__ = [
    "MEASURE_REFUSAL_COPY",
    "EvidenceTarget",
    "Failure",
    "MeasureProbe",
    "MeasureRefusal",
    "QueryPlan",
    "SpaceState",
    "compact_measures",
    "confirm_measure",
    "create_measure",
    "evaluate_confirm_criteria",
    "measure_evidence",
    "measure_view",
    "measures_view",
    "propose_measure_drafts",
    "propose_measures",
    "reject_measure",
]

# --------------------------------------------------------------------------
# plain-language copy (codes stay for machines; the Studio shows the message)
# --------------------------------------------------------------------------

MEASURE_REFUSAL_COPY: dict[str, str] = {
    # the spec itself (measure_spec R1..R8)
    "aggregate_invalid": (
        "Pick one of: count, count of distinct values, sum, average, smallest or largest."
    ),
    "unknown_object": (
        "This table is not part of the Space's checked data, so a measure cannot be built on it."
    ),
    "grain_unverified": (
        "The table this measure is built on failed its checks, so no figure from it can be "
        "trusted yet."
    ),
    "grain_ambiguous": (
        "More than one connected source holds this table. Remove the duplicate source first."
    ),
    "column_missing": "The column this measure uses is not in the table as it was last read.",
    "column_not_numeric": (
        "SUM and AVG need a numeric column; this column holds text. "
        "Fix its type when the data is loaded."
    ),
    "column_type_unsupported": (
        "This kind of calculation does not work on this column's type. "
        "Use a number or date column, or count instead."
    ),
    "key_column_aggregate": (
        "This column identifies rows or links tables, so adding it up or averaging it "
        "gives a meaningless number."
    ),
    "name_invalid": (
        "Use lowercase letters, digits and underscores, start with a letter, 2 to 48 characters."
    ),
    "name_reserved": (
        "This name is already used by a table, a column or a built-in demo measure. "
        "Choose a different name."
    ),
    "name_taken": "Another measure in this Space already uses this name. Choose a different name.",
    "description_invalid": (
        "Write a short plain-language description of 1 to 200 characters, "
        "with no code, brackets or semicolons."
    ),
    "measure_limit": (
        "The limit was reached: 80 confirmed measures per Space and 60 proposals per run. "
        "Withdraw a confirmed measure or propose again later."
    ),
    # the row and its version
    "measure_confirmable_state": "Only a proposed or a rejected measure can be confirmed.",
    "measure_confirmed_immutable": (
        "A confirmed measure cannot be reworded. Withdraw it, then confirm it again "
        "with the new wording."
    ),
    "stale_definition": "This measure changed after you opened it. Reload and review it again.",
    "version_not_active": (
        "The data connection this measure belongs to is no longer the active one, "
        "so it cannot be confirmed. Propose it again on the active connection."
    ),
    "stale_proposal": (
        "This proposal was made against an older version of the data structure. Propose it again."
    ),
    "source_truncated": (
        "Only part of this table was copied in, so a total over it would be wrong. "
        "Load the whole table first."
    ),
    # the lake, as it is now
    "empty_grain": "The table has no rows right now, so there is nothing to measure.",
    "column_changed": (
        "The column's type changed, or the column is gone, since the data structure was "
        "last read. Re-read the source and try again."
    ),
    "column_all_null": "Every value in this column is empty, so the measure would have no figure.",
    "measure_does_not_bind": "This measure could not be computed from the data as it is now.",
    "untyped_numeric": (
        "This column was declared as numbers at the source but is stored as text here, "
        "so totals over it would be wrong. Fix its type when the data is loaded."
    ),
    # whole-request outcomes
    "measure_invalid": "This measure fails one or more checks. See the list for what to fix.",
    "measure_confirm_refused": (
        "The measure was not confirmed because the data does not support it right now. "
        "See the list for what to fix."
    ),
    "measure_not_found": "There is no such measure in this Space.",
    "space_not_found": "This Space has no ontology to hold measures.",
    "reason_invalid": "Give a reason of 1 to 200 characters, without control characters.",
    "ledger_unavailable": "The audit ledger is not reachable, so nothing was changed. Try again.",
    "ledger_append_failed": "The audit ledger did not accept the record, so nothing was changed.",
    "warehouse_unavailable": (
        "The data store is busy or unreachable, so the measure could not be checked. Try again."
    ),
    "ontology_store_unavailable": "The store that holds measures could not be reached just now.",
    "actor_not_provisioned": "The configured steward account does not exist in this workspace.",
    "steward_role_required": "Only a steward or an admin can do this.",
}

#: Failures that mean "reload and look again" (HTTP 409); everything else is a 422.
_CONFLICT_CODES = (
    "measure_confirmable_state",
    "measure_confirmed_immutable",
    "stale_definition",
    "version_not_active",
    "stale_proposal",
    "name_taken",
    "measure_limit",
)
#: The shape of the spec is wrong, so there is nothing sensible to bind or execute.
_STRUCTURAL_CODES = frozenset(
    {
        "aggregate_invalid",
        "unknown_object",
        "column_missing",
        "column_not_numeric",
        "column_type_unsupported",
        "key_column_aggregate",
    }
)
_LAKE_CODES = frozenset(
    {
        "empty_grain",
        "column_changed",
        "column_all_null",
        "measure_does_not_bind",
        "untyped_numeric",
    }
)

EVENT_CONFIRMED = "ontology.measure.confirmed"
EVENT_REJECTED = "ontology.measure.rejected"
REASON_MAX = 200


@dataclass(frozen=True)
class Failure:
    """One failed criterion. ``code`` is for machines, ``message`` for the steward."""

    code: str
    field: str | None = None

    @property
    def message(self) -> str:
        return MEASURE_REFUSAL_COPY[self.code]

    def to_dict(self) -> dict[str, str]:
        out = {"code": self.code, "message": self.message}
        if self.field:
            out["field"] = self.field
        return out


def _f(code: str, field_name: str | None = None) -> Failure:
    if code not in MEASURE_REFUSAL_COPY:  # a code without words is a bug, not a runtime value
        raise KeyError(f"no plain-language copy for measure code {code!r}")
    return Failure(code, field_name)


def _dedupe(failures: Iterable[Failure]) -> list[Failure]:
    seen: set[tuple[str, str | None]] = set()
    out: list[Failure] = []
    for f in failures:
        key = (f.code, f.field)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


class MeasureRefusal(Exception):
    """A named refusal. ``status`` is the HTTP status the API layer should answer with."""

    def __init__(
        self,
        status: int,
        code: str,
        failed: Sequence[Failure] = (),
        *,
        current: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            f"{code}: {', '.join(f.code for f in failed) or MEASURE_REFUSAL_COPY[code]}"
        )
        self.status = status
        self.code = code
        self.failed = list(failed)
        self.current = current

    @property
    def message(self) -> str:
        return MEASURE_REFUSAL_COPY[self.code]

    def detail(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "failed": [f.to_dict() for f in self.failed],
        }
        if self.current is not None:
            out["current"] = self.current
        return out


# --------------------------------------------------------------------------
# the Space as the store sees it
# --------------------------------------------------------------------------


@dataclass
class SpaceState:
    """The Space's ACTIVE versions, their latest snapshots and every measure row."""

    space_id: UUID
    store: OntologyStorePort
    versions: list[OntologyVersion]
    rows: list[OntoMeasure]
    _snapshots: dict[UUID, OntologySnapshot | None] = field(default_factory=dict)

    @property
    def active_ids(self) -> set[UUID]:
        return {v.id for v in self.versions}

    def snapshot(self, version_id: UUID) -> OntologySnapshot | None:
        if version_id not in self._snapshots:
            self._snapshots[version_id] = self.store.latest_snapshot(version_id)
        return self._snapshots[version_id]

    def confirmed_count(self) -> int:
        ids = self.active_ids
        return sum(1 for m in self.rows if m.state == "confirmed" and m.version_id in ids)

    def taken_names(self, row: OntoMeasure | None, version_id: UUID | None = None) -> set[str]:
        """Names a measure may not take: proposed|confirmed rows of active versions
        (``ontology.add_measure`` is last-wins, so a collision must be impossible), and any
        row of the same version (the database unique burns a rejected name)."""
        ids = self.active_ids
        vid = row.version_id if row is not None else version_id
        out: set[str] = set()
        for m in self.rows:
            if row is not None and m.id == row.id:
                continue
            if (m.state in ("proposed", "confirmed") and m.version_id in ids) or (
                vid is not None and m.version_id == vid
            ):
                out.add(m.name)
        return out

    def name_scope(self, version_id: UUID) -> dict[str, Any]:
        """Snapshot body used for the spec rules: the grain's own version wins, the other
        active versions only add object and column names (R6 reserves them all)."""
        own = self.snapshot(version_id)
        objects: dict[str, Any] = {}
        for v in self.versions:
            if v.id == version_id:
                continue
            snap = self.snapshot(v.id)
            if snap is not None:
                for name, spec in (snap.body.get("objects") or {}).items():
                    objects.setdefault(name, spec)
        if own is not None:
            objects.update(own.body.get("objects") or {})
        return {"objects": objects, "links": (own.body.get("links") if own else None) or {}}


def _state(space_id: str, store: OntologyStorePort | None) -> SpaceState:
    sid = space_uuid(space_id)
    if sid is None:
        raise MeasureRefusal(404, "space_not_found")
    target = store or ontology_store()
    return SpaceState(
        space_id=sid,
        store=target,
        versions=list(target.active_for_space(sid)),
        rows=list(target.measures_for_space(sid)),
    )


def _drift(state: SpaceState) -> list[dict[str, str]]:
    """A changed schema fingerprint made a 'proposed' version that nothing activates."""
    out: list[dict[str, str]] = []
    seen: set[UUID] = set()
    for active in state.versions:
        for v in state.store.list_versions(state.space_id, active.identity):
            if v.status == "proposed" and v.id not in seen:
                seen.add(v.id)
                out.append({"version_id": str(v.id), "status": "proposed"})
    return out


# --------------------------------------------------------------------------
# the proposer: deterministic, bounded, idempotent
# --------------------------------------------------------------------------

#: The closed-lane GRAIN-GUARD suffix list, defined here because that module is not in this
#: tree (a measure-looking plain column, never a key or a label)...
_MEASURE_SUFFIX = re.compile(r"_(kg|myr|usd|eur|sgd|pct|days|score|qty|amount|cost|value)$", re.I)
#: ...plus the spec's words, matched as whole underscore-separated tokens.
_MEASURE_WORD = re.compile(
    r"(?:^|_)(?:amount|total|price|cost|revenue|qty|quantity|value|balance|sales|fee|tax|"
    r"weight|budget|spend|salary|wage|income|profit)(?:_|$)",
    re.I,
)
_AVG_WORD = re.compile(r"(?:^|_)(?:price|rate|cost|score|pct)(?:_|$)", re.I)
_NON_NAME = re.compile(r"[^a-z0-9_]")


def _is_measureish(column: str) -> bool:
    return bool(_MEASURE_SUFFIX.search(column) or _MEASURE_WORD.search(column))


def _table_token(object_name: str, spec: Mapping[str, Any]) -> str:
    source = str(spec.get("source_table") or "")
    table = source.rsplit(".", 1)[-1] if source else object_name.rsplit(".", 1)[-1]
    return _NON_NAME.sub("_", table.lower())


def _col_token(column: str) -> str:
    return _NON_NAME.sub("_", column.lower())


def _subject_names(violations: Iterable[Any]) -> set[str]:
    out: set[str] = set()
    for v in violations:
        out.add(str(v.get("subject", "")) if isinstance(v, Mapping) else str(v.subject))
    return out


def _truncated(body: Mapping[str, Any], grain: str) -> bool:
    for t in (body.get("catalog") or {}).get("tables") or []:
        if str(t.get("path")) == grain:
            return bool(t.get("truncated"))
    return False


def propose_measure_drafts(
    body: Mapping[str, Any],
    violations: Iterable[Any] = (),
    *,
    taken_names: Iterable[str] = (),
    existing_names: Iterable[str] = (),
    cap: int = MAX_PROPOSED_PER_RUN,
    name_scope: Mapping[str, Any] | None = None,
) -> tuple[list[MeasureSpecDraft], list[dict[str, str]]]:
    """Candidates for one version's snapshot ``body``: ``(accepted, skipped)``.

    Pure and deterministic (sorted objects and columns; sums and averages of measure-looking
    numeric columns first, then one row count per object). For every object that is not a
    failed subject of the snapshot and whose source was not truncated. Skips keys, link
    columns, provenance and non-numeric columns. A name already stored is ``exists`` (a
    rejected or confirmed row is never resurrected); a rule failure is skipped with its
    code. At most ``cap`` new candidates; the rest are ``measure_limit``.
    """
    violations = list(violations)
    failed = _subject_names(violations)
    existing = set(existing_names)
    taken = set(taken_names)
    objects: Mapping[str, Any] = body.get("objects") or {}
    links: Mapping[str, Any] = body.get("links") or {}
    scope: Mapping[str, Any] = name_scope if name_scope is not None else body

    skipped: list[dict[str, str]] = []
    measure_cands: list[MeasureSpecDraft] = []
    count_cands: list[MeasureSpecDraft] = []
    for oname in sorted(objects):
        spec = objects[oname]
        if oname in failed:
            skipped.append({"name": oname, "why": "grain_unverified"})
            continue
        if _truncated(body, oname):
            skipped.append({"name": oname, "why": "source_truncated"})
            continue
        table = _table_token(oname, spec)
        label = str(spec.get("source_table") or oname)
        keys = {str(k) for k in spec.get("key") or []}
        link_cols = {
            str(c)
            for ln in links.values()
            if ln.get("from") == oname
            for c in ln.get("from_columns") or []
        }
        for attr in sorted(spec.get("attributes") or [], key=lambda a: str(a.get("name"))):
            col = str(attr.get("name"))
            if col in keys or col in link_cols or not is_numeric_type(str(attr.get("type", ""))):
                continue
            if not _is_measureish(col):
                continue
            ctok = _col_token(col)
            measure_cands.append(
                MeasureSpecDraft(
                    f"{table}_{ctok}_sum",
                    oname,
                    "sum",
                    col,
                    f"Sum of {col} over every row in {label}.",
                )
            )
            if _AVG_WORD.search(col):
                measure_cands.append(
                    MeasureSpecDraft(
                        f"{table}_{ctok}_avg",
                        oname,
                        "avg",
                        col,
                        f"Average {col} per row in {label}.",
                    )
                )
        count_cands.append(
            MeasureSpecDraft(
                f"{table}_count",
                oname,
                "count",
                "*",
                f"Number of rows in {label}, each row counted once.",
            )
        )

    accepted: list[MeasureSpecDraft] = []
    considered = 0
    for cand in [*measure_cands, *count_cands]:
        if cand.name in existing:
            skipped.append({"name": cand.name, "why": "exists"})
            continue
        if considered >= cap:
            skipped.append({"name": cand.name, "why": "measure_limit"})
            continue
        considered += 1
        failures = validate_measure_spec(
            cand,
            scope,
            violations,
            taken_names=taken | {a.name for a in accepted},
        )
        if failures:
            skipped.append({"name": cand.name, "why": failures[0][0]})
            continue
        accepted.append(cand)
    return accepted, skipped


# --------------------------------------------------------------------------
# evidence: the lake as it is now
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceTarget:
    """One measure definition to be checked against the lake."""

    draft: MeasureSpecDraft
    version_id: UUID
    schema_fingerprint: str
    snapshot: OntologySnapshot
    measure_id: str = ""
    #: False when the spec's shape is already wrong: stats are still read, nothing is bound.
    bind: bool = True


@dataclass
class MeasureProbe:
    evidence: dict[str, Any]
    failed: list[Failure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _norm_type(value: Any) -> str:
    return str(value or "").strip().upper()


def _attr_types(snapshot: OntologySnapshot, grain: str) -> dict[str, str]:
    obj = (snapshot.body.get("objects") or {}).get(grain) or {}
    return {str(a.get("name")): str(a.get("type", "")) for a in obj.get("attributes") or []}


def _lake_path(warehouse: Path | None) -> Path:
    lake = Path(warehouse) if warehouse is not None else warehouse_path()
    if not lake.is_file():
        raise MeasureRefusal(503, "warehouse_unavailable")
    return lake


def measure_evidence(
    targets: Sequence[EvidenceTarget],
    *,
    space_id: str,
    warehouse: Path | None = None,
    store: OntologyStorePort | None = None,
) -> list[MeasureProbe]:
    """Check each target against the lake and execute its compiled SQL once.

    The Space ontology is rebuilt and re-verified ONCE (``verify`` executes every key and
    link claim); each target then goes through the same ``compile_plan_maybe_unverified``
    the ask path uses for an ungrouped plan, so the stored ``probe_sql`` IS the SQL the
    ask will run. A warehouse that cannot be opened is ``MeasureRefusal(503)``; a target
    that cannot be checked carries its failures, nothing is raised for it.
    """
    items = list(targets)
    if not items:
        return []
    lake = _lake_path(warehouse)
    # One steward check at a time per lake file: every read below goes through the same
    # per-file attach lock the ask path uses, and a read-only registry open elsewhere in
    # THIS process would otherwise collide with our own write-mode attach.
    with _lock_for(lake):
        return _probe_locked(items, space_id=space_id, lake=lake, store=store)


def _probe_locked(
    items: list[EvidenceTarget],
    *,
    space_id: str,
    lake: Path,
    store: OntologyStorePort | None,
) -> list[MeasureProbe]:
    persisted = bool((store or ontology_store()).persistent)
    onto = load_space_ontology(space_id, store)
    verified: Ontology | None = None
    violations: list[Any] | None = None
    if onto is not None:
        try:
            verified = load_verified_ontology(lake, onto)
        except Exception as exc:  # noqa: BLE001 - lock / IO: nothing was checked
            raise MeasureRefusal(503, "warehouse_unavailable") from exc
        if verified is not None:
            violations = cached_verify_violations(verified)
    failed_subjects = _subject_names(violations or [])

    # One connection for every read of stats (never nested with validate_compiled_sql's own).
    stats: dict[str, dict[str, Any]] = {}
    non_null: dict[tuple[str, str], int] = {}
    relation_of: dict[str, str] = {}
    if verified is not None:
        for t in items:
            obj = verified.objects.get(t.draft.grain)
            if obj is not None:
                relation_of[t.draft.grain] = obj.relation
    try:
        con = connect_file(lake)
    except Exception as exc:  # noqa: BLE001
        raise MeasureRefusal(503, "warehouse_unavailable") from exc
    try:
        for relation in sorted(set(relation_of.values())):
            entry: dict[str, Any] = {"columns": None, "rows": None}
            try:
                entry["columns"] = {
                    str(r[0]): str(r[1])
                    for r in con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
                }
                entry["rows"] = scalar_int(
                    con.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()
                )
            except Exception:  # noqa: BLE001 - verify() names an unreadable relation
                entry = {"columns": None, "rows": None}
            stats[relation] = entry
        for t in items:
            relation = relation_of.get(t.draft.grain, "")
            cols = (stats.get(relation) or {}).get("columns")
            col = t.draft.column
            if col != "*" and cols and col in cols and (relation, col) not in non_null:
                try:
                    non_null[(relation, col)] = scalar_int(
                        con.execute(f"SELECT COUNT({_ident(col)}) FROM {relation}").fetchone()
                    )
                except Exception:  # noqa: BLE001
                    pass
    finally:
        con.close()

    grantable = {o.relation for o in verified.objects.values()} if verified is not None else set()
    probes: list[MeasureProbe] = []
    to_run: list[tuple[int, str]] = []  # (probe index, compiled sql) executed in one pass
    for t in items:
        d = t.draft
        failed: list[Failure] = []
        checks: list[str] = []
        evidence: dict[str, Any] = {
            "measured_at": datetime.now(UTC).isoformat(),
            "snapshot_id": str(t.snapshot.id),
            "schema_fingerprint": t.schema_fingerprint,
            "grain_rows": None,
            "non_null_rows": None,
            "column_type": "" if d.column == "*" else None,
            "probe_sql": None,
            "probe_value": None,
            "checks": checks,
        }
        probes.append(MeasureProbe(evidence, failed))
        rel = relation_of.get(d.grain)
        if verified is None or violations is None or rel is None:
            failed.append(_f("measure_does_not_bind"))
            continue
        if d.grain in failed_subjects:
            failed.append(_f("grain_unverified", "grain"))
        else:
            checks.append("grain_verified")
        entry = stats.get(rel) or {}
        cols = entry.get("columns")
        rows = entry.get("rows")
        if cols is None or rows is None:
            failed.append(_f("grain_unverified", "grain"))
            continue
        evidence["grain_rows"] = rows
        if rows <= 0:
            failed.append(_f("empty_grain", "grain"))
        else:
            checks.append("grain_nonempty")
        if d.column == "*":
            evidence["non_null_rows"] = rows
        else:
            lake_type = cols.get(d.column)
            snap_type = _attr_types(t.snapshot, d.grain).get(d.column)
            if lake_type is None or (
                snap_type is not None and _norm_type(lake_type) != _norm_type(snap_type)
            ):
                failed.append(_f("column_changed", "column"))
            else:
                checks.append("column_unchanged")
                evidence["column_type"] = lake_type
                nn = non_null.get((rel, d.column))
                evidence["non_null_rows"] = nn
                if nn == 0:
                    failed.append(_f("column_all_null", "column"))
                elif nn is not None:
                    checks.append("column_not_all_null")
        if not t.bind:
            continue
        try:
            expression = measure_expression(d.aggregate, d.column)
        except ValueError:
            failed.append(_f("measure_does_not_bind"))
            continue
        provenance = MeasureProvenance(
            measure_id=t.measure_id,
            name=d.name,
            grain=d.grain,
            aggregate=d.aggregate,
            column=d.column,
            column_type=str(evidence["column_type"] or ""),
            definition_hash=definition_hash(d),
            version_id=str(t.version_id),
            decided_at="",
            ledger_entry_id="",
            persisted=persisted,
            columns_checked=False,
        )
        previous = verified.measures.get(d.name)
        verified.add_measure(
            d.name,
            d.grain,
            expression,
            additive=d.aggregate in ("sum", "count"),
            description=d.description,
            provenance=provenance,
        )
        try:
            compiled = compile_plan_maybe_unverified(verified, QueryPlan(measure=d.name))
        finally:
            if previous is None:
                verified.measures.pop(d.name, None)
            else:
                verified.measures[d.name] = previous
        if isinstance(compiled, Refusal) or not isinstance(compiled, CompiledQuery):
            failed.append(_f("measure_does_not_bind"))
            continue
        evidence["probe_sql"] = compiled.sql
        if validate_compiled_sql(compiled.sql, grantable=grantable, warehouse=lake):
            failed.append(_f("measure_does_not_bind"))
            continue
        untyped = untyped_numeric_reason(compiled.sql, lake)
        if untyped is not None and untyped.endswith(":registry_unreadable"):
            # Could not look, which is not the same as "text declared numeric": try again.
            raise MeasureRefusal(503, "warehouse_unavailable")
        if untyped:
            failed.append(_f("untyped_numeric", "column"))
            continue
        checks.append("binds")
        if not failed:
            to_run.append((len(probes) - 1, compiled.sql))

    if to_run:
        try:
            con = connect_file(lake)
        except Exception as exc:  # noqa: BLE001
            raise MeasureRefusal(503, "warehouse_unavailable") from exc
        try:
            for idx, sql in to_run:
                probe = probes[idx]
                try:
                    got = con.execute(sql).fetchall()
                except Exception:  # noqa: BLE001 - it parsed and planned but did not run
                    probe.failed.append(_f("measure_does_not_bind"))
                    continue
                probe.evidence["probe_value"] = _json_scalar(got[0][0]) if got else None
                probe.evidence["checks"].append("executed")
        finally:
            con.close()
    return probes


# --------------------------------------------------------------------------
# criteria: one function, a static part and a lake part
# --------------------------------------------------------------------------


def _spec_failures(
    draft: MeasureSpecDraft,
    state: SpaceState,
    version_id: UUID,
    *,
    taken: Iterable[str],
    confirming: bool,
) -> list[Failure]:
    snap = state.snapshot(version_id)
    violations = snap.violations if snap is not None else ()
    body = state.name_scope(version_id)
    out = [
        _f(code, fld)
        for code, fld in validate_measure_spec(
            draft,
            body,
            violations,
            taken_names=taken,
            confirmed_count=state.confirmed_count(),
            confirming=confirming,
        )
    ]
    if snap is not None and _truncated(snap.body, draft.grain):
        out.append(_f("source_truncated", "grain"))
    return out


def evaluate_confirm_criteria(
    row: OntoMeasure,
    state: SpaceState,
    *,
    name: str | None = None,
    description: str | None = None,
    expected_hash: str | None = None,
    probe: MeasureProbe | None = None,
) -> list[Failure]:
    """Every criterion for confirming ``row``, all evaluated, all failures returned.

    Static (no lake; this is what the list view calls): the row is proposed or rejected, the
    caller's hash is current, the row's version is an active one with the row's fingerprint,
    the spec rules R1..R8 hold for the (possibly renamed) definition, the source was not
    truncated, no other measure of the Space holds the name, and fewer than 80 are confirmed.
    With a ``probe`` from ``measure_evidence``, its lake failures are appended.
    """
    out: list[Failure] = []
    if row.state not in ("proposed", "rejected"):
        out.append(_f("measure_confirmable_state", "state"))
    if expected_hash is not None and expected_hash != row.definition_hash:
        out.append(_f("stale_definition", "definition_hash"))
    if row.version_id not in state.active_ids:
        out.append(_f("version_not_active", "version"))
    elif row.version_fingerprint and row.version_fingerprint != row.schema_fingerprint:
        out.append(_f("stale_proposal", "version"))
    draft = MeasureSpecDraft(
        name=row.name if name is None else name,
        grain=row.grain,
        aggregate=row.aggregate,
        column=row.column_name,
        description=row.description if description is None else description,
    )
    out.extend(
        _spec_failures(draft, state, row.version_id, taken=state.taken_names(row), confirming=True)
    )
    if probe is not None:
        out.extend(probe.failed)
    return _dedupe(out)


def _refusal(failures: Sequence[Failure], current: dict[str, Any] | None = None) -> MeasureRefusal:
    """The top-level status and code for a set of failures (conflicts first)."""
    conflict = next((f for f in failures if f.code in _CONFLICT_CODES), None)
    if conflict is not None:
        return MeasureRefusal(
            409,
            conflict.code,
            failures,
            current=current if conflict.code == "stale_definition" else None,
        )
    only_lake = all(f.code in _LAKE_CODES or f.code == "grain_unverified" for f in failures)
    return MeasureRefusal(
        422, "measure_confirm_refused" if only_lake else "measure_invalid", failures
    )


# --------------------------------------------------------------------------
# views
# --------------------------------------------------------------------------


def _unusable_reason(row: OntoMeasure, state: SpaceState) -> str | None:
    """Why a measure cannot answer right now, from the latest snapshot. A GET never writes."""
    if (
        measure_definition_hash(
            row.name, row.grain, row.aggregate, row.column_name, row.description
        )
        != row.definition_hash
    ):
        return "integrity"
    if row.aggregate not in ("count", "count_distinct", "sum", "avg", "min", "max"):
        return "aggregate_invalid"
    if row.version_id not in state.active_ids:
        return "version_not_active"
    snap = state.snapshot(row.version_id)
    obj = (snap.body.get("objects") or {}).get(row.grain) if snap is not None else None
    if snap is None or obj is None:
        return "grain_missing"
    if row.grain in _subject_names(snap.violations):
        return "grain_unverified"
    if row.column_name != "*":
        types = _attr_types(snap, row.grain)
        if row.column_name not in types:
            return "column_missing"
        confirmed_type = _norm_type((row.evidence or {}).get("column_type"))
        if confirmed_type and _norm_type(types[row.column_name]) != confirmed_type:
            return "column_type_changed"
    return None


def _redact(evidence: Mapping[str, Any], can_decide: bool) -> dict[str, Any]:
    out = dict(evidence or {})
    if not can_decide:
        out.pop("probe_value", None)
        out.pop("probe_sql", None)
    return out


def measure_view(row: OntoMeasure, state: SpaceState, *, can_decide: bool) -> dict[str, Any]:
    reason = _unusable_reason(row, state)
    usable = reason is None
    blockers = (
        []
        if row.state == "confirmed"
        else [f.to_dict() for f in evaluate_confirm_criteria(row, state)]
    )
    return {
        "id": str(row.id),
        "version_id": str(row.version_id),
        "name": row.name,
        "description": row.description,
        "grain": row.grain,
        "aggregate": row.aggregate,
        "column": row.column_name,
        "definition": definition_text(row.aggregate, row.column_name, row.grain),
        "definition_hash": row.definition_hash,
        "source": row.source,
        "state": row.state,
        "evidence": _redact(row.evidence, can_decide),
        "decided_by": str(row.decided_by) if row.decided_by else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_reason": row.decision_reason,
        "ledger_entry_id": row.ledger_entry_id,
        "usable": usable,
        "unusable_reason": reason,
        "confirm_blockers": blockers,
        "answers_certified": row.state == "confirmed" and usable,
    }


def compact_measures(
    space_id: str, store: OntologyStorePort | None = None
) -> dict[UUID, list[dict[str, Any]]]:
    """Per version, the short form the ontology view carries: which measures exist and
    whether they answer, none of the evidence (that is the measures list's job)."""
    state = _state(space_id, store)
    out: dict[UUID, list[dict[str, Any]]] = {}
    for m in sorted(state.rows, key=lambda r: r.name):
        out.setdefault(m.version_id, []).append(
            {
                "id": str(m.id),
                "name": m.name,
                "state": m.state,
                "answers_certified": m.state == "confirmed" and _unusable_reason(m, state) is None,
            }
        )
    return out


def measures_view(
    space_id: str,
    *,
    role: str,
    can_decide: bool,
    store: OntologyStorePort | None = None,
) -> dict[str, Any]:
    """The read surface: counts, limits, drift, who is looking, every measure."""
    state = _state(space_id, store)
    rows = state.rows
    versions = []
    seen: set[UUID] = set()
    for active in state.versions:
        for v in state.store.list_versions(state.space_id, active.identity):
            if v.status != "superseded" and v.id not in seen:
                seen.add(v.id)
                versions.append(
                    {
                        "version_id": str(v.id),
                        "status": v.status,
                        "schema_fingerprint": v.schema_fingerprint,
                    }
                )
    return {
        "space_id": str(state.space_id),
        "persisted": bool(state.store.persistent),
        "versions": versions,
        "drift": _drift(state),
        "counts": {
            s: sum(1 for m in rows if m.state == s) for s in ("proposed", "confirmed", "rejected")
        },
        "limits": {"confirmed_max": MAX_CONFIRMED_MEASURES},
        "viewer": {"role": role, "can_decide": can_decide},
        "measures": [measure_view(m, state, can_decide=can_decide) for m in rows],
    }


# --------------------------------------------------------------------------
# propose / create
# --------------------------------------------------------------------------


def _target(
    draft: MeasureSpecDraft,
    version_id: UUID,
    fingerprint: str,
    snap: OntologySnapshot,
    *,
    measure_id: str = "",
    bind: bool = True,
) -> EvidenceTarget:
    return EvidenceTarget(draft, version_id, fingerprint, snap, measure_id=measure_id, bind=bind)


def propose_measures(
    space_id: str,
    *,
    actor: UUID | None,
    warehouse: Path | None = None,
    store: OntologyStorePort | None = None,
) -> dict[str, Any]:
    """Propose measures for every ACTIVE version's latest snapshot.

    Idempotent (an existing name is never touched), bounded at 60 new rows per run, and a
    candidate whose lake evidence fails is not offered (returned in ``skipped``). A proposal
    is not authority, so there is no ledger entry; there is one ``measure.propose`` audit
    row per version that received rows.
    """
    state = _state(space_id, store)
    skipped: list[dict[str, str]] = []
    created: list[UUID] = []
    cap = MAX_PROPOSED_PER_RUN
    taken = {
        m.name
        for m in state.rows
        if m.state in ("proposed", "confirmed") and m.version_id in state.active_ids
    }
    plans: list[tuple[OntologyVersion, OntologySnapshot, list[MeasureSpecDraft]]] = []
    for version in state.versions:
        snap = state.snapshot(version.id)
        if snap is None:
            continue
        existing = {m.name for m in state.rows if m.version_id == version.id}
        drafts, skip = propose_measure_drafts(
            snap.body,
            snap.violations,
            taken_names=taken,
            existing_names=existing,
            cap=cap,
            name_scope=state.name_scope(version.id),
        )
        skipped.extend(skip)
        cap = max(0, cap - len(drafts))
        taken.update(d.name for d in drafts)
        if drafts:
            plans.append((version, snap, drafts))
    targets = [
        _target(d, v.id, v.schema_fingerprint, snap) for v, snap, drafts in plans for d in drafts
    ]
    probes = measure_evidence(targets, space_id=space_id, warehouse=warehouse, store=state.store)
    cursor = 0
    for version, snap, drafts in plans:
        ok_drafts: list[MeasureDraft] = []
        ok_evidence: list[dict[str, Any]] = []
        for d in drafts:
            probe = probes[cursor]
            cursor += 1
            if probe.failed:
                skipped.append({"name": d.name, "why": probe.failed[0].code})
                continue
            ok_drafts.append(
                MeasureDraft(d.name, d.grain, d.aggregate, d.column, d.description, "derived")
            )
            ok_evidence.append(probe.evidence)
        if not ok_drafts:
            continue
        inserted, lost = state.store.add_measures(
            version.id,
            ok_drafts,
            ok_evidence,
            actor=actor,
            snapshot_id=snap.id,
            action="measure.propose",
        )
        skipped.extend({"name": m.name, "why": "exists"} for m in lost)
        created.extend(row.id for row in inserted)
    fresh = _state(space_id, state.store)
    by_id = {m.id: m for m in fresh.rows}
    return {
        "created": [measure_view(by_id[i], fresh, can_decide=True) for i in created],
        "skipped": skipped,
        "drift": _drift(fresh),
    }


def _version_for_grain(state: SpaceState, grain: str) -> tuple[OntologyVersion, OntologySnapshot]:
    holders: list[tuple[OntologyVersion, OntologySnapshot]] = []
    for v in state.versions:
        snap = state.snapshot(v.id)
        if snap is not None and grain in (snap.body.get("objects") or {}):
            holders.append((v, snap))
    if not holders:
        raise MeasureRefusal(422, "unknown_object", [_f("unknown_object", "grain")])
    if len(holders) > 1:
        raise MeasureRefusal(422, "grain_ambiguous", [_f("grain_ambiguous", "grain")])
    return holders[0]


def create_measure(
    space_id: str,
    *,
    name: str,
    grain: str,
    aggregate: str,
    column: str = "*",
    description: str,
    actor: UUID | None,
    warehouse: Path | None = None,
    store: OntologyStorePort | None = None,
) -> dict[str, Any]:
    """Manual entry. Creates a PROPOSED measure (never confirmed here), with the same rules
    and the same lake evidence as the proposer. Nothing is stored when anything fails."""
    state = _state(space_id, store)
    version, snap = _version_for_grain(state, grain)
    draft = MeasureSpecDraft(name, grain, aggregate, column, description)
    same = next((m for m in state.rows if m.version_id == version.id and m.name == name), None)
    if same is not None:
        if same.definition_hash == definition_hash(draft):
            return {"created": False, "measure": measure_view(same, state, can_decide=True)}
        raise MeasureRefusal(409, "name_taken", [_f("name_taken", "name")])
    failures = _spec_failures(
        draft, state, version.id, taken=state.taken_names(None, version.id), confirming=False
    )
    structural = any(f.code in _STRUCTURAL_CODES for f in failures)
    probe = measure_evidence(
        [_target(draft, version.id, version.schema_fingerprint, snap, bind=not structural)],
        space_id=space_id,
        warehouse=warehouse,
        store=state.store,
    )[0]
    failures = _dedupe([*failures, *probe.failed])
    if failures:
        if any(f.code == "name_taken" for f in failures):
            raise MeasureRefusal(409, "name_taken", failures)
        raise MeasureRefusal(422, "measure_invalid", failures)
    inserted, lost = state.store.add_measures(
        version.id,
        [MeasureDraft(name, grain, aggregate, column, description, "manual")],
        [probe.evidence],
        actor=actor,
        snapshot_id=snap.id,
        action="measure.create",
    )
    if lost or not inserted:
        raise MeasureRefusal(409, "name_taken", [_f("name_taken", "name")])
    fresh = _state(space_id, state.store)
    row = next(m for m in fresh.rows if m.id == inserted[0].id)
    return {"created": True, "measure": measure_view(row, fresh, can_decide=True)}


# --------------------------------------------------------------------------
# confirm / reject
# --------------------------------------------------------------------------


def _ledger_step(
    event_type: str,
    actor: UUID,
    append: Callable[..., Any],
    verify: Callable[[], Any],
) -> Callable[[dict[str, Any]], str]:
    """The callable handed to the store's single write path: append to the one ledger
    (Cortex), read the chain back, return the entry id. Any failure raises, so the store
    leaves the measure untouched."""

    def call(payload: dict[str, Any]) -> str:
        try:
            resp = append(event_type=event_type, payload=payload, actor=str(actor))
            entry_id = getattr(resp, "entry_id", None) or (
                resp.get("entry_id") if isinstance(resp, dict) else None
            )
            sig = _append_hash(resp)
            if not entry_id or not sig or sig == str(entry_id):
                raise ValueError("the ledger append returned no entry id or chain hash")
            if not _verify_ok(verify()):
                raise ValueError("the ledger chain did not verify after the append")
        except Exception as exc:  # noqa: BLE001 - one named refusal for every ledger fault
            raise MeasureRefusal(503, "ledger_append_failed") from exc
        return str(entry_id)

    return call


def _require_ledger(append: Callable[..., Any] | None, verify: Callable[[], Any] | None) -> None:
    if append is None or verify is None:
        raise MeasureRefusal(503, "ledger_unavailable")


def _row(state: SpaceState, measure_id: UUID) -> OntoMeasure:
    row = state.store.get_measure(state.space_id, measure_id)  # Space-scoped: the cross-Space guard
    if row is None:
        raise MeasureRefusal(404, "measure_not_found")
    return row


def confirm_measure(
    space_id: str,
    measure_id: UUID,
    *,
    definition_hash: str,
    name: str | None = None,
    description: str | None = None,
    actor: UUID,
    warehouse: Path | None = None,
    store: OntologyStorePort | None = None,
    ledger_append: Callable[..., Any] | None = None,
    ledger_verify: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Confirm one measure. All criteria are evaluated before any side effect and every
    failure comes back together; a refusal leaves the row unchanged and is audited as
    ``measure.confirm_refused``. On success the ledger entry, the single UPDATE, the audit
    row and the ledger pointer are one transaction (the ledger runs first, inside it)."""
    state = _state(space_id, store)
    _require_ledger(ledger_append, ledger_verify)
    assert ledger_append is not None and ledger_verify is not None
    row = _row(state, measure_id)
    new_name = row.name if name is None else name
    new_desc = row.description if description is None else description

    if row.state == "confirmed":
        reworded = (new_name, new_desc) != (row.name, row.description)
        if definition_hash != row.definition_hash:
            code = "stale_definition"
        elif reworded:
            code = "measure_confirmed_immutable"
        else:
            return {"already": True, "measure": measure_view(row, state, can_decide=True)}
        raise _refused(state, row, actor, [_f(code)])

    static = evaluate_confirm_criteria(
        row, state, name=name, description=description, expected_hash=definition_hash
    )
    snap = state.snapshot(row.version_id)
    probe: MeasureProbe | None = None
    skip_lake = (
        snap is None
        or row.version_id not in state.active_ids
        or any(f.code in ("stale_proposal", "unknown_object") for f in static)
    )
    if not skip_lake:
        assert snap is not None
        structural = any(f.code in _STRUCTURAL_CODES for f in static)
        draft = MeasureSpecDraft(new_name, row.grain, row.aggregate, row.column_name, new_desc)
        probe = measure_evidence(
            [
                _target(
                    draft,
                    row.version_id,
                    row.schema_fingerprint,
                    snap,
                    measure_id=str(row.id),
                    bind=not structural,
                )
            ],
            space_id=space_id,
            warehouse=warehouse,
            store=state.store,
        )[0]
    failures = evaluate_confirm_criteria(
        row, state, name=name, description=description, expected_hash=definition_hash, probe=probe
    )
    if failures or probe is None:
        raise _refused(state, row, actor, failures or [_f("measure_does_not_bind")])

    try:
        decided, changed = state.store.decide_measure(
            row.id,
            to_state="confirmed",
            expected_hash=definition_hash,
            name=name,
            description=description,
            reason=None,
            evidence=probe.evidence,
            actor=actor,
            ledger=_ledger_step(EVENT_CONFIRMED, actor, ledger_append, ledger_verify),
        )
    except MeasureNotFound:
        raise MeasureRefusal(404, "measure_not_found") from None
    except MeasureNameTaken:
        raise _refused(state, row, actor, [_f("name_taken", "name")]) from None
    except MeasureTransitionError as exc:
        outcome = _transition_outcome(exc, state, row, actor, new_name, new_desc, definition_hash)
        if isinstance(outcome, MeasureRefusal):
            raise outcome from None
        decided, changed = outcome, False
    fresh = _state(space_id, state.store)
    current = next((m for m in fresh.rows if m.id == decided.id), decided)
    return {"already": not changed, "measure": measure_view(current, fresh, can_decide=True)}


def _refused(
    state: SpaceState,
    row: OntoMeasure,
    actor: UUID,
    failures: Sequence[Failure],
    current: dict[str, Any] | None = None,
) -> MeasureRefusal:
    """Audit a refused confirm (``measure.confirm_refused``, the row is untouched) and build
    the refusal to raise."""
    state.store.record_refusal(
        row.id, failed=[f.code for f in failures], actor=actor, from_state=row.state
    )
    return _refusal(failures, current if current is not None else _current_view(state, row))


def _current_view(state: SpaceState, row: OntoMeasure) -> dict[str, Any]:
    return measure_view(row, state, can_decide=True)


def _transition_outcome(
    exc: MeasureTransitionError,
    state: SpaceState,
    row: OntoMeasure,
    actor: UUID,
    new_name: str,
    new_desc: str,
    expected_hash: str,
) -> MeasureRefusal | OntoMeasure:
    """Map a store transition error. The one benign case is a concurrent twin of this very
    confirm that won the row: asking for what is already there is not an error."""
    cur = exc.current
    if (
        exc.code == "definition_frozen"
        and cur is not None
        and cur.state == "confirmed"
        and cur.definition_hash == expected_hash
        and (cur.name, cur.description) == (new_name, new_desc)
    ):
        return cur
    code = {
        "stale_definition": "stale_definition",
        "concurrent_change": "stale_definition",
        "definition_frozen": "measure_confirmed_immutable",
        "illegal_transition": "measure_confirmable_state",
    }.get(exc.code, "stale_definition")
    return _refused(
        state, row, actor, [_f(code)], _current_view(state, cur) if cur is not None else None
    )


def reject_measure(
    space_id: str,
    measure_id: UUID,
    *,
    reason: str,
    actor: UUID,
    store: OntologyStorePort | None = None,
    ledger_append: Callable[..., Any] | None = None,
    ledger_verify: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Reject (a proposed measure) or withdraw (a confirmed one). A reason is mandatory and
    the ledger entry is part of the same transaction. Rejecting a rejected measure is a
    no-op ``already``."""
    state = _state(space_id, store)
    text = reason.strip() if isinstance(reason, str) else ""
    if not (1 <= len(text) <= REASON_MAX) or any(unicodedata.category(ch) == "Cc" for ch in text):
        raise MeasureRefusal(422, "reason_invalid", [_f("reason_invalid", "reason")])
    _require_ledger(ledger_append, ledger_verify)
    assert ledger_append is not None and ledger_verify is not None
    row = _row(state, measure_id)
    if row.state == "rejected":
        return {"already": True, "measure": measure_view(row, state, can_decide=True)}
    try:
        decided, changed = state.store.decide_measure(
            row.id,
            to_state="rejected",
            expected_hash=row.definition_hash,
            reason=text,
            actor=actor,
            ledger=_ledger_step(EVENT_REJECTED, actor, ledger_append, ledger_verify),
        )
    except MeasureNotFound:
        raise MeasureRefusal(404, "measure_not_found") from None
    except MeasureTransitionError as exc:
        code = (
            "measure_confirmable_state" if exc.code == "illegal_transition" else "stale_definition"
        )
        cur = measure_view(exc.current, state, can_decide=True) if exc.current else None
        raise _refusal([_f(code)], cur) from None
    fresh = _state(space_id, state.store)
    current = next((m for m in fresh.rows if m.id == decided.id), decided)
    return {"already": not changed, "measure": measure_view(current, fresh, can_decide=True)}
