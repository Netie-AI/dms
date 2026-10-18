"""Ask-time use of steward-confirmed measures (dms#283 / dms#284, spec step 6).

Four jobs, all inert until a confirmed row exists:

* ``attach_confirmed_measures`` loads the confirmed rows of a Space's ACTIVE ontology
  versions into the ``Ontology`` the ask path builds, with provenance. A row that cannot be
  trusted (hash that does not recompute, unknown aggregate, grain that is not an object,
  two rows with one name) is skipped and its reason recorded, never raised: an orphan must
  not turn the whole Space into ``ontology_store_unavailable``. A store that cannot be READ
  still raises, so the caller keeps that named abstain instead of silently dropping measures.
* ``prune_unusable_measures`` checks every loaded measure against the lake as it is now
  (grain still verified and granted, column still there with the type it was confirmed
  with) and removes the ones that fail. A pruned measure can only degrade an answer.
* ``measure_fit`` is DMS's own check that a confirmed measure answers THIS question. Cortex
  ranks by measure-name tokens, which is not authority to certify; this is the second key.
* ``read_source_pulls`` is what the ingest registry says about a landed relation (the table's
  name at its source, schema excluded, and whether the row cap truncated the pull); ``prune``
  and the fit check both read it.
* ``filter_values_present`` is rule 12 at ask time: a plan filter literal that does not
  exist in its column as landed (``north`` for ``North``) must not become a plausible 0.
* ``route_choice_reason`` refuses a plan whose join route is a choice among several (a direct
  foreign key beside a chain, parallel links, a ``via`` that picks one): the lane states one
  figure, so it answers only over exactly one route.

``aggregate_cues`` (the cue word sets measure_fit and the ad hoc lane both read) is pure.

Nothing here imports ``generative_ask`` or ``measure_confirm``; ``space_ontology`` and
``generative_ask`` import this module.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from dms_core.control_plane.onto_store import OntoMeasure, measure_definition_hash
from dms_core.measure_line import confirmed_line

from dms_executor.bronze import classify_source_kind
from dms_executor.demo_warehouse import connect_serving
from dms_executor.measure_basis import MEASURE_STATUS_CONFIRMED
from dms_executor.measure_spec import NAME_RE, description_failures
from dms_executor.ontology import (
    Measure,
    MeasureProvenance,
    Ontology,
    Refusal,
    Violation,
    _ident,
    _literal,
    measure_expression,
)

if TYPE_CHECKING:
    from dms_executor.space_ontology import OntologyStorePort

__all__ = [
    "CONFIRMED_ROW_CAP",
    "MAX_QUESTION_CHARS",
    "EXCLUSION_REASONS",
    "FIT_REASONS",
    "FitVerdict",
    "PullRecord",
    "REASON_AMBIGUOUS_FILTER_VALUE",
    "REASON_AMBIGUOUS_PATH",
    "REASON_EMPTY_AGGREGATE",
    "REASON_MASKED_FIGURE",
    "REASON_QUESTION_TOO_LONG",
    "REASON_TOO_MANY_GROUPS",
    "REASON_UNMATCHED_FILTER_VALUE",
    "Accounting",
    "account_for_question",
    "aggregate_cues",
    "attach_confirmed_measures",
    "confirmed_basis",
    "confirmed_measure_suggestions",
    "count_rows",
    "filter_values_present",
    "measure_exclusions",
    "measure_fit",
    "prune_unusable_measures",
    "read_source_pulls",
    "route_choice_reason",
    "via_choice_reason",
]

#: Why a confirmed row never reached the ask, or was removed from it. These are the tails
#: ``no_declared_measure`` can carry ("is not usable now: <reason>").
EXCL_NOT_CONFIRMED = "not_confirmed"
EXCL_VERSION_NOT_ACTIVE = "version_not_active"
EXCL_INTEGRITY = "integrity"
EXCL_AGGREGATE_INVALID = "aggregate_invalid"
EXCL_NAME_INVALID = "name_invalid"
EXCL_DESCRIPTION_INVALID = "description_invalid"
EXCL_GRAIN_MISSING = "grain_missing"
EXCL_NAME_COLLISION = "name_collision"
EXCL_GRAIN_UNVERIFIED = "grain_unverified"
EXCL_GRAIN_NOT_GRANTED = "grain_not_granted"
EXCL_COLUMN_MISSING = "column_missing"
EXCL_COLUMN_TYPE_CHANGED = "column_type_changed"
EXCL_LAKE_UNREADABLE = "lake_unreadable"
EXCL_SOURCE_TRUNCATED = "source_truncated"

EXCLUSION_REASONS = frozenset(
    {
        EXCL_NOT_CONFIRMED,
        EXCL_VERSION_NOT_ACTIVE,
        EXCL_INTEGRITY,
        EXCL_AGGREGATE_INVALID,
        EXCL_NAME_INVALID,
        EXCL_DESCRIPTION_INVALID,
        EXCL_GRAIN_MISSING,
        EXCL_NAME_COLLISION,
        EXCL_GRAIN_UNVERIFIED,
        EXCL_GRAIN_NOT_GRANTED,
        EXCL_COLUMN_MISSING,
        EXCL_COLUMN_TYPE_CHANGED,
        EXCL_LAKE_UNREADABLE,
        EXCL_SOURCE_TRUNCATED,
    }
)

#: Same slot pattern as ``_violations`` / ``_column_cache``: not a dataclass field.
_EXCLUSIONS_SLOT = "_measure_exclusions"

REASON_UNMATCHED_FILTER_VALUE = "unmatched_filter_value"
REASON_AMBIGUOUS_FILTER_VALUE = "ambiguous_filter_value"
REASON_EMPTY_AGGREGATE = "empty_aggregate"
REASON_AMBIGUOUS_PATH = "ambiguous_path"
REASON_TOO_MANY_GROUPS = "too_many_groups"
REASON_QUESTION_TOO_LONG = "question_too_long"
REASON_MASKED_FIGURE = "masked_figure"
#: The most characters of a question the confirmed lane reads. A longer one is refused by name:
#: no real question about one measure needs more, and the bound keeps the lane's cost fixed.
MAX_QUESTION_CHARS = 500
#: The most rows (groups) a confirmed answer states. The lane never cuts a list: it states every
#: row up to this many or abstains ``too_many_groups``. The question cannot state a row limit, so
#: neither Cortex's ``limit`` nor the default 50 of a plan is one.
CONFIRMED_ROW_CAP = 200
#: The most literals one IN list may carry and still be probed (one probe per literal).
MAX_PROBED_LITERALS = 50

_RELATION_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")


def measure_exclusions(onto: Ontology | None) -> dict[str, str]:
    """name -> reason for every confirmed measure that did not reach (or left) the ask."""
    if onto is None:
        return {}
    raw = onto.__dict__.get(_EXCLUSIONS_SLOT)
    return dict(raw) if isinstance(raw, dict) else {}


def _record(onto: Ontology, name: str, reason: str) -> None:
    slot = onto.__dict__.setdefault(_EXCLUSIONS_SLOT, {})
    slot.setdefault(name, reason)


def _norm_type(value: Any) -> str:
    return str(value or "").strip().upper()


# --------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------


def _row_exclusion(row: OntoMeasure, onto: Ontology) -> str | None:
    """Why this confirmed row cannot be trusted, or None. Pure: no lake, no raise."""
    if row.state != "confirmed":
        return EXCL_NOT_CONFIRMED
    if row.version_status not in ("", "active"):
        return EXCL_VERSION_NOT_ACTIVE
    # A confirmed row without its steward, time and ledger pointer has no authority.
    if (
        row.decided_by is None
        or row.decided_at is None
        or not str(row.ledger_entry_id or "").strip()
    ):
        return EXCL_INTEGRITY
    # A row that was never through evidence has no snapshot to point at: an INSERT that skipped
    # propose and confirm (the 0007 guard is BEFORE UPDATE only) must not answer.
    evidence = row.evidence if isinstance(row.evidence, Mapping) else {}
    if not str(evidence.get("snapshot_id") or "").strip():
        return EXCL_INTEGRITY
    recomputed = measure_definition_hash(
        row.name, row.grain, row.aggregate, row.column_name, row.description
    )
    if recomputed != row.definition_hash:
        return EXCL_INTEGRITY
    try:
        measure_expression(row.aggregate, row.column_name)
    except ValueError:
        return EXCL_AGGREGATE_INVALID
    # Cortex 4xxs the WHOLE ask on one bad name or description, so neither is sent unless
    # it passes the same rule it passed when it was confirmed.
    if not NAME_RE.fullmatch(row.name):
        return EXCL_NAME_INVALID
    if description_failures(row.description):
        return EXCL_DESCRIPTION_INVALID
    if row.grain not in onto.objects:
        return EXCL_GRAIN_MISSING
    return None


def attach_confirmed_measures(
    onto: Ontology, space_id: UUID, store: OntologyStorePort
) -> dict[str, str]:
    """Add the Space's confirmed measures (ACTIVE versions only) to ``onto``.

    ONE ``measures_for_space`` query. Returns, and records on ``onto``, name -> reason for
    each row skipped. A failing READ raises (the caller names it ``ontology_store_unavailable``);
    a bad ROW never does. Proposed and rejected rows are never read, so they cannot reach
    compile, ranking or the wire.
    """
    rows = store.measures_for_space(space_id, states=("confirmed",), active_only=True)
    persisted = bool(store.persistent)
    skipped: list[tuple[str, str]] = []
    trusted: list[OntoMeasure] = []
    for row in rows:
        why = _row_exclusion(row, onto)
        if why is not None:
            skipped.append((row.name, why))
        else:
            trusted.append(row)
    held = Counter(r.name for r in trusted)
    for row in trusted:
        if held[row.name] > 1:  # two active versions, one name: neither is "the" definition
            skipped.append((row.name, EXCL_NAME_COLLISION))
            continue
        evidence = row.evidence if isinstance(row.evidence, Mapping) else {}
        try:
            onto.add_measure(
                row.name,
                row.grain,
                measure_expression(row.aggregate, row.column_name),
                additive=row.aggregate in ("sum", "count"),
                description=row.description,
                provenance=MeasureProvenance(
                    measure_id=str(row.id),
                    name=row.name,
                    grain=row.grain,
                    aggregate=row.aggregate,
                    column=row.column_name,
                    column_type=_norm_type(evidence.get("column_type")),
                    definition_hash=row.definition_hash,
                    version_id=str(row.version_id),
                    decided_at=row.decided_at.isoformat() if row.decided_at else "",
                    ledger_entry_id=str(row.ledger_entry_id or ""),
                    persisted=persisted,
                    columns_checked=False,
                ),
            )
        except KeyError:  # the grain check above makes this unreachable; never raise out
            skipped.append((row.name, EXCL_GRAIN_MISSING))
        except ValueError:
            skipped.append((row.name, EXCL_AGGREGATE_INVALID))
    out: dict[str, str] = {}
    for name, why in skipped:
        # A name that ended up with a trusted definition is not "unusable".
        if name not in onto.measures:
            out.setdefault(name, why)
            _record(onto, name, why)
    return out


# --------------------------------------------------------------------------
# prune against the lake as it is now
# --------------------------------------------------------------------------


def _read_lake(
    lake: Path | None, relations: Iterable[str]
) -> tuple[dict[str, dict[str, str] | None], dict[str, PullRecord] | None]:
    """``(described, pulls)`` from ONE connection: DESCRIBE of each relation (None = could not
    be read, never "empty") and the ingest registry's record of each (None = the registry could
    not be read; see ``read_source_pulls``)."""
    wanted = sorted(set(relations))
    described: dict[str, dict[str, str] | None] = dict.fromkeys(wanted)
    if not wanted or lake is None or not Path(lake).is_file():
        return described, None
    try:
        con = connect_serving(Path(lake))
    except Exception:  # noqa: BLE001 - a lake we cannot open proves nothing about a measure
        return described, None
    try:
        for relation in wanted:
            if not _RELATION_RE.fullmatch(relation):
                continue
            try:
                got = con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
            except Exception:  # noqa: BLE001
                continue
            described[relation] = {str(r[0]): str(r[1]) for r in got}
        return described, _pulls_on(con, wanted)
    finally:
        con.close()


@dataclass(frozen=True)
class PullRecord:
    """What the ingest registry says about one landed relation.

    ``table`` is the table's name at its SOURCE with the schema left off (``schools`` for a
    pull of ``public.schools`` into ``bronze.public_schools``), or "" when the registry does not
    name a SQL source. ``truncated`` is whether the connector's row cap cut the pull.
    """

    table: str
    #: ``None`` is UNKNOWN (a registry row written before the column existed holds NULL): a
    #: source nobody recorded as whole is not shown whole.
    truncated: bool | None


def _source_table(filename: Any) -> str:
    """The table part of a SQL pull's credential-free source string
    (``postgresql://host:5432/db#public.schools``), or "" for anything else."""
    text = str(filename or "")
    if classify_source_kind(text) != "sql" or "#" not in text:
        return ""
    return text.rsplit("#", 1)[-1].rsplit(".", 1)[-1]


def _pulls_on(con: Any, relations: Iterable[str]) -> dict[str, PullRecord] | None:
    names: dict[str, str] = {}
    for relation in relations:
        schema, _, bare = str(relation).partition(".")
        if schema.lower() == "bronze" and bare:
            names[bare] = str(relation)
    if not names:
        return {}
    try:
        marks = ", ".join("?" for _ in names)
        rows = con.execute(
            "SELECT table_name, filename, truncated FROM bronze._ingest_registry "
            f"WHERE table_name IN ({marks})",
            list(names),
        ).fetchall()
    except Exception:  # noqa: BLE001 - no registry, or one without the column
        return None
    return {
        names[str(name)]: PullRecord(
            _source_table(filename), None if truncated is None else bool(truncated)
        )
        for name, filename, truncated in rows
        if str(name) in names
    }


def read_source_pulls(
    lake: Path | None, relations: Iterable[str]
) -> dict[str, PullRecord] | None:
    """``relation -> PullRecord`` for each ``bronze.<name>`` relation the ingest registry knows.

    A relation the registry has no row for is simply absent. ``None`` means the registry could
    not be read at all (no lake, cannot open, no registry table): a caller that needs the record
    fails closed on it, so an unreadable ingest record is never read as "whole".
    """
    if lake is None or not Path(lake).is_file():
        return None
    try:
        con = connect_serving(Path(lake))
    except Exception:  # noqa: BLE001 - a lake we cannot open proves nothing
        return None
    try:
        return _pulls_on(con, relations)
    finally:
        con.close()


def count_rows(sql: str, lake: Path | None) -> int | None:
    """The single integer ``sql`` (a ``SELECT COUNT(*)``) returns on the lake, or None when it
    cannot be read: a caller that needs the count fails closed on None. Read-only in effect, and
    the connection is closed before it returns (one live attach per file)."""
    if lake is None or not Path(lake).is_file():
        return None
    try:
        con = connect_serving(Path(lake))
    except Exception:  # noqa: BLE001 - a lake we cannot open proves nothing
        return None
    try:
        row = con.execute(sql).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        con.close()


def _violation_subjects(violations: Iterable[Violation]) -> set[str]:
    return {str(v.subject) for v in violations}


def prune_unusable_measures(
    ontologies: Sequence[Ontology | None],
    violations: Sequence[Violation],
    lake: Path | None,
    allowed: set[str],
) -> dict[str, str]:
    """Remove every loaded measure the lake no longer supports; mark the rest checked.

    A measure stays only if (U1) its grain object is not a failed subject of the verify() that
    just ran and its relation is among this turn's granted tables, (U2) the source was not cut
    by the connector's row cap, as the latest snapshot and the ingest registry as it is now both
    say (``source_truncated``; a registry that cannot be read is ``lake_unreadable``), and (U3)
    DESCRIBE of that relation still shows its column with the type it was confirmed with (``*``
    needs only a readable relation). A lake that cannot be read removes every measure
    (``lake_unreadable``). Survivors are replaced with ``columns_checked=True``. The
    ontologies are mutated in place (``onto`` and ``declared`` may share one); the removal
    reasons are returned and recorded on each ontology. No measures, no work, no lake access.
    """
    distinct: list[Ontology] = []
    for onto in ontologies:
        if onto is not None and onto.measures and not any(onto is d for d in distinct):
            distinct.append(onto)
    if not distinct:
        return {}
    granted = {a.lower() for a in allowed}
    removed: dict[str, str] = {}

    def drop(onto: Ontology, name: str, reason: str) -> None:
        onto.measures.pop(name, None)
        _record(onto, name, reason)
        removed.setdefault(name, reason)

    needs_lake: list[tuple[Ontology, str, Measure, str]] = []
    for onto in distinct:
        failed = _violation_subjects(violations) | _violation_subjects(
            onto.__dict__.get("_violations") or []
        )
        for name, m in list(onto.measures.items()):
            prov = m.provenance
            obj = onto.objects.get(m.grain)
            if prov is None:  # not a structured, confirmed definition: nothing vouches for it
                drop(onto, name, EXCL_INTEGRITY)
            elif obj is None:
                drop(onto, name, EXCL_GRAIN_MISSING)
            elif m.grain in failed:
                drop(onto, name, EXCL_GRAIN_UNVERIFIED)
            elif obj.relation.lower() not in granted:
                drop(onto, name, EXCL_GRAIN_NOT_GRANTED)
            elif onto.truncated.get(m.grain):
                # The latest snapshot of the source says the row cap cut it.
                drop(onto, name, EXCL_SOURCE_TRUNCATED)
            else:
                needs_lake.append((onto, name, m, obj.relation))

    described, pulls = _read_lake(lake, (rel for _o, _n, _m, rel in needs_lake))
    for onto, name, m, relation in needs_lake:
        prov = m.provenance
        assert prov is not None
        columns = described.get(relation)
        if columns is None:
            drop(onto, name, EXCL_LAKE_UNREADABLE)
            continue
        # The confirm refused a truncated grain; a source can also be cut AFTER the confirm (a
        # re-pull that hit the row cap, or one that grew past it). The ingest record as it is
        # NOW decides: a measure over a cut source states a smaller figure as if it were whole.
        # A record that cannot be read, or that nobody wrote, does not show the source whole.
        pull = None if pulls is None else pulls.get(relation)
        if pull is None:
            drop(onto, name, EXCL_LAKE_UNREADABLE)
            continue
        if pull.truncated is not False:  # cut, or unknown: neither shows the source whole
            drop(onto, name, EXCL_SOURCE_TRUNCATED)
            continue
        if prov.column != "*":
            if prov.column not in columns:
                drop(onto, name, EXCL_COLUMN_MISSING)
                continue
            # No recorded type means "unchanged" cannot be shown, so it is not shown.
            recorded = _norm_type(prov.column_type)
            if not recorded or _norm_type(columns[prov.column]) != recorded:
                drop(onto, name, EXCL_COLUMN_TYPE_CHANGED)
                continue
        onto.measures[name] = replace(m, provenance=replace(prov, columns_checked=True))
    return removed


def confirmed_measure_suggestions(onto: Ontology | None) -> list[str]:
    """``name: description`` of every measure that is confirmed AND checked against the lake
    this ask, in name order. What an abstain can offer instead (``no_declared_measure``)."""
    if onto is None:
        return []
    out = []
    for name in sorted(onto.measures):
        m = onto.measures[name]
        if m.provenance is not None and m.provenance.columns_checked:
            out.append(f"{name}: {m.description}" if m.description else name)
    return out


# --------------------------------------------------------------------------
# what an answer from a confirmed measure says about itself
# --------------------------------------------------------------------------


def confirmed_basis(prov: MeasureProvenance) -> tuple[dict[str, Any], str]:
    """``(measure_basis, assumption line)`` for an answer a confirmed measure produced.

    ONE helper builds both, from the provenance the confirmed row carried and the lake check
    this ask made, so the structured object and the sentence a customer reads can never
    disagree. Only a measure whose column the lake confirmed THIS ask has a basis; anything
    else is a programming error and raises (the caller abstains rather than state one).
    The wording says "steward account", not a person: under DR-0004 Option A the
    confirmer is the configured actor.
    """
    if not prov.columns_checked:
        raise ValueError(f"measure {prov.name!r} was not checked against the lake this ask")
    basis: dict[str, Any] = {
        "status": MEASURE_STATUS_CONFIRMED,
        "measure_id": prov.measure_id,
        "measure": prov.name,
        "definition_hash": prov.definition_hash,
        "version_id": prov.version_id,
        "confirmed_at": prov.decided_at,
        "ledger_entry_id": prov.ledger_entry_id,
        "grain": prov.grain,
        "columns_checked": True,
        "persisted": bool(prov.persisted),
    }
    line = confirmed_line(basis)
    return basis, line


# --------------------------------------------------------------------------
# rule 12 at ask time
# --------------------------------------------------------------------------


def _canonical_sql(expr: str) -> str:
    """DuckDB SQL for the canonical form of a text expression: every space, control, format and
    invisible character removed (tab, newline, NBSP, zero width), Unicode-composed, accents and
    combining marks dropped, lower-cased. Two texts that share it but not their bytes are two
    spellings of one value (rule 12). Used on both sides of one comparison."""
    return (
        "lower(regexp_replace(strip_accents(nfc_normalize(regexp_replace("
        f"CAST({expr} AS VARCHAR), '[\\s\\p{{Z}}\\p{{C}}]', '', 'g'))), '\\p{{M}}', '', 'g'))"
    )


def _shown_value(value: str) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def filter_values_present(
    plan: Any, onto: Ontology, lake: Path | None, question: str = ""
) -> str | None:
    """None when every ``=`` / ``IN`` literal of the plan's filters exists in its column as
    landed; otherwise ``unmatched_filter_value: <why>`` (or ``ambiguous_filter_value: <why>``).

    One ``SELECT 1 ... LIMIT 1`` per literal, case-sensitive exactly as the compiled
    predicate will be, at most ``MAX_PROBED_LITERALS`` per IN. A text literal whose column ALSO
    holds a value differing from it only in case, any space, an invisible character (tab, newline,
    zero width, NBSP) or Unicode form is refused too, as ``ambiguous_filter_value`` (the
    exact value exists) or ``unmatched_filter_value`` (only the other spelling does), naming the
    other spellings: the equality would match one spelling and silently drop the
    others. Other operators are not probed (an empty range is an honest empty range; an
    encoding mismatch is what a missing equality value is). Anything that cannot be checked
    (lake unreadable, list too long, unusable relation or value) fails CLOSED. An object or
    column the ontology does not know is left for the compiler, which refuses it by name.

    A value that is exact in its column is also checked against every other column of the
    plan's relations: when one holds it too, the value is ``ambiguous_filter_value`` unless
    ``question`` names the plan's column right before it (``_question_binds``). The question
    states the value; it does not always state the column, and the filter binds it to one.
    """
    # (object, relation, column, literal, the other values of its IN list)
    probes: list[tuple[str, str, str, Any, tuple[str, ...]]] = []
    for obj_name, column, op, value in getattr(plan, "filters", ()) or ():
        verb = str(op).strip().upper()
        if verb not in ("=", "IN"):
            continue
        resolved = onto.resolve_object(obj_name)
        if isinstance(resolved, Refusal) or resolved not in onto.objects:
            continue
        known = onto.__dict__.get("_column_cache", {}).get(resolved)
        if known is not None and column not in known:
            continue  # compile refuses an unknown column by name (unknown_column)
        many = verb == "IN" and isinstance(value, (list, tuple, set))
        literals = list(value) if many else [value]
        if len(literals) > MAX_PROBED_LITERALS:
            return (
                f"{REASON_UNMATCHED_FILTER_VALUE}: {resolved}.{column} lists "
                f"{len(literals)} values; at most {MAX_PROBED_LITERALS} can be checked "
                "against the data"
            )
        probes.extend(
            (
                resolved,
                onto.objects[resolved].relation,
                str(column),
                v,
                tuple(str(w) for w in literals if w is not v),
            )
            for v in literals
        )
    if not probes:
        return None
    # Every relation the plan names: a value is checked against the other columns of all of them.
    relations = sorted({str(p[1]) for p in probes})
    if not all(_RELATION_RE.fullmatch(r) for r in relations):
        return (
            f"{REASON_UNMATCHED_FILTER_VALUE}: a relation the plan names is not a readable "
            "table, so the filter value was not checked"
        )

    def cannot_check(what: str) -> str:
        return f"{REASON_UNMATCHED_FILTER_VALUE}: {what}, so the filter value was not checked"

    if lake is None or not Path(lake).is_file():
        return cannot_check("the data could not be read")
    try:
        con = connect_serving(Path(lake))
    except Exception:  # noqa: BLE001
        return cannot_check("the data could not be opened")
    try:
        seen: set[tuple[str, str, str]] = set()
        try:
            columns_of = {rel: _value_columns(con, rel) for rel in relations}
        except Exception:  # noqa: BLE001 - a table that cannot be read proves nothing
            return cannot_check("the tables the plan names could not be read")
        for obj_name, relation, column, literal, siblings in probes:
            rendered = _literal(literal)
            key = (relation, column, rendered)
            if key in seen:
                continue
            seen.add(key)
            if not _RELATION_RE.fullmatch(relation):
                return cannot_check(f"{obj_name} is not a readable table")
            # A Python bool is not "1": TRUE = 1 holds in SQL, so a JSON true against an
            # integer key would pass the probe and filter to key 1. Only a BOOLEAN column
            # can hold a boolean literal.
            guard = ""
            if isinstance(literal, bool):
                guard = f"typeof({_ident(column)}) = 'BOOLEAN' AND "
            shown = repr(literal) if len(repr(literal)) <= 60 else repr(literal)[:57] + "..."
            missing = (
                f"{REASON_UNMATCHED_FILTER_VALUE}: {obj_name}.{column} has no value "
                f"{shown} exactly as written"
            )
            ident = _ident(column)
            exact_sql = f"SELECT 1 FROM {relation} WHERE {guard}{ident} = {rendered} LIMIT 1"
            if isinstance(literal, str):
                # ONE query per literal. It reads the rows that equal the literal OR are the
                # same text under the canonical form (case, any space, tab, newline, zero-width
                # or other invisible character, accents, Unicode normalisation), and returns the
                # least exact of them (False sorts first): True = every such row is the literal
                # byte for byte, False = some row is another spelling, no row = nothing matches.
                sql = (
                    f"SELECT ({ident} = {rendered}) AS exact FROM {relation} "
                    f"WHERE ({ident} = {rendered}) OR "
                    f"({_canonical_sql(ident)} = {_canonical_sql(rendered)}) "
                    "ORDER BY exact ASC LIMIT 1"
                )
            else:
                sql = exact_sql
            others: list[str] = []
            try:
                row = con.execute(sql).fetchone()
                spelled = row is not None and row[0] is False
                if spelled:
                    exists = con.execute(exact_sql).fetchone() is not None
                    others = [
                        str(r[0])
                        for r in con.execute(
                            f"SELECT DISTINCT CAST({ident} AS VARCHAR) FROM {relation} "
                            f"WHERE {_canonical_sql(ident)} = {_canonical_sql(rendered)} "
                            f"AND CAST({ident} AS VARCHAR) <> CAST({rendered} AS VARCHAR) "
                            "ORDER BY 1 LIMIT 4"
                        ).fetchall()
                    ]
            except Exception:  # noqa: BLE001 - a type that cannot be compared is not a match
                return cannot_check(f"{obj_name}.{column} could not be compared")
            if row is None:
                return missing
            if spelled:
                named = ", ".join(_shown_value(o) for o in others[:3]) + (
                    ", ..." if len(others) > 3 else ""
                )
                if not exists:
                    return (
                        f"{missing}; the column holds the same text spelled differently: {named}"
                    )
                # The value exists exactly, and the column holds it under another spelling too
                # ('North' and 'north'). The equality the plan compiles matches ONE of them and
                # drops the rest without a trace, so neither reading is certified.
                return (
                    f"{REASON_AMBIGUOUS_FILTER_VALUE}: {obj_name}.{column} holds {shown} "
                    f"and also {named}, which differ from it only in case, spacing, an "
                    "invisible character or Unicode form, so the filter would drop those rows"
                )
            # The value is exact in its column. It must also be the column the question means:
            # a value that another column of the plan's relations holds too ("North" as a
            # region and as a district, "1" as a school id and a district id) is bound to the
            # plan's column only when the question names that column beside the value.
            try:
                holding = _columns_holding(con, columns_of, relation, column, literal)
            except Exception:  # noqa: BLE001 - a column that cannot be read proves nothing
                return cannot_check(f"{obj_name}.{column} could not be compared with its table")
            if holding and not _question_binds(question, str(literal), column, siblings):
                return (
                    f"{REASON_AMBIGUOUS_FILTER_VALUE}: {obj_name}.{column} holds {shown} "
                    f"and so do {', '.join(holding[:3])}; the question does not say which "
                    f"column it means (name the column beside the value, as '{column} {literal}')"
                )
    finally:
        con.close()
    return None


#: Provenance columns the ingest adds to every landed table: never a value a question states.
_PROVENANCE_COLUMNS = frozenset({"_src", "_ingest_id"})


def _value_columns(con: Any, relation: str) -> tuple[str, ...]:
    """The columns of ``relation`` a value can sit in (provenance columns excluded)."""
    return tuple(
        str(d[0])
        for d in con.execute(f"SELECT * FROM {relation} LIMIT 0").description
        if str(d[0]) not in _PROVENANCE_COLUMNS
    )


def _columns_holding(
    con: Any,
    columns_of: Mapping[str, tuple[str, ...]],
    relation: str,
    column: str,
    literal: Any,
) -> list[str]:
    """``relation.column`` for every column of the plan's relations (``columns_of``), other than
    ``column`` of ``relation``, that holds ``literal``. A text literal is compared as text; a
    number as text or as a number (a DECIMAL ``100.50`` holds ``100.5``). One scan per relation,
    a boolean per column. A boolean literal is not compared: only a BOOLEAN column holds one."""
    if literal is None or isinstance(literal, bool):
        return []
    numeric = isinstance(literal, (int, float))
    # The text side compares against the literal as TEXT (a bare number would make DuckDB cast the
    # VARCHAR column to a number and fail on any text it holds); the number side against a number.
    as_text = _literal(str(literal))
    as_number = _literal(literal)
    out: list[str] = []
    for rel, cols in columns_of.items():
        names = [c for c in cols if (rel, c) != (relation, column)]
        if not names:
            continue
        tests = []
        for name in names:
            cast = f"CAST({_ident(name)} AS VARCHAR)"
            test = f"{cast} = {as_text}"
            if numeric:
                test = f"({test} OR TRY_CAST({cast} AS DOUBLE) = {as_number})"
            tests.append(f"bool_or({test})")
        hits = con.execute(f"SELECT {', '.join(tests)} FROM {rel}").fetchone()
        out.extend(f"{rel}.{name}" for name, hit in zip(names, hits or (), strict=True) if hit)
    return out


#: Words that join the values of one list ("districts 1 and 2", "North, South").
_LIST_JOINS = frozenset({"and", "or", ","})


def _question_binds(
    question: str, literal: str, column: str, siblings: Sequence[str] = ()
) -> bool:
    """Whether the question states ``literal`` for ``column``: the column's word stands right
    before it ("region North", "kind is North", "district 2", "school id 2", "districts 1 and
    2"). Between the word and the value may sit an id or number word, and a list join with the
    other values of the same IN list (``siblings``, which share the column). Only the column's
    own words count: a table's name is shared by all its columns, so it names none."""
    value = _tokens(literal)
    toks = _tokens(question)
    if not value:
        return False
    words = _name_words(column)
    others = [t for t in (_tokens(s) for s in siblings) if t]
    width = len(value)
    for i in range(len(toks) - width + 1):
        if toks[i : i + width] != value:
            continue
        j = i - 1
        while j >= 0:
            if toks[j] in _BINDER_FILLER or toks[j] in _LIST_JOINS:
                j -= 1
                continue
            sib = next(
                (s for s in others if j - len(s) + 1 >= 0 and toks[j - len(s) + 1 : j + 1] == s),
                None,
            )
            if sib is None:
                break
            j -= len(sib)
        if j >= 0 and _in_words(toks[j], words, fold=True):
            return True
    return False


# --------------------------------------------------------------------------
# DMS's own fit check (the second key)
# --------------------------------------------------------------------------

#: Words that name the aggregate, not what is aggregated.
_AGG_WORDS = frozenset(
    {
        "sum", "total", "avg", "average", "mean", "count", "number", "num", "min", "max",
        "minimum", "maximum", "distinct", "unique",
    }
)  # fmt: skip

#: Question words that mean the number asked for is a different quantity than a plain
#: aggregate of a column. A measure answers them only if its own name says so.
_QUANTITY_WORDS = frozenset(
    {
        "rate", "ratio", "percent", "percentage", "pct", "growth", "share", "margin",
        "proportion", "fraction", "change", "trend", "versus", "vs",
    }
)  # fmt: skip
_QUANTITY_CANON = {"percentage": "percent", "pct": "percent"}

#: spec 5.3 cue word sets. A phrase is matched as consecutive words.
_CUE_WORDS: dict[str, tuple[str, ...]] = {
    "sum": ("total", "sum", "overall", "altogether", "combined"),
    "avg": ("average", "avg", "mean"),
    "min": ("minimum", "lowest", "smallest", "least"),
    "max": ("maximum", "highest", "largest", "greatest", "biggest"),
    "count": ("how many", "number of", "count", "tally"),
    "distinct": ("distinct", "unique"),
}

#: Which cues an aggregate can answer.
_COMPATIBLE_CUES: dict[str, frozenset[str]] = {
    "sum": frozenset({"sum"}),
    "avg": frozenset({"avg"}),
    "min": frozenset({"min"}),
    "max": frozenset({"max"}),
    "count": frozenset({"count"}),
    "count_distinct": frozenset({"distinct", "count"}),
}

FIT_REASONS = frozenset(
    {
        "ok",
        "ambiguous",
        "no_match",
        "aggregate_cue",
        "quantity_cue",
        "qualifier_cue",
        "unbound_constraint",
        "unstated_filter",
        "no_content_tokens",
        "ranker_disagrees",
        REASON_QUESTION_TOO_LONG,
    }
)

#: Connectives that narrow or split the number asked for ("per student", "excluding the
#: north"). A plain aggregate of one column answers none of them, so a question that carries
#: one is refused unless the measure's own name carries that word.
_QUALIFIER_WORDS = frozenset({"per", "each", "excluding", "except", "without", "only", "not"})
_OTHER_THAN = ("other", "than")
#: "by <word>" asks for a breakdown or a restriction by that dimension.
_BY = "by"
#: Qualifiers that mean "one figure per <thing>": a plan grouped by <thing> answers them.
_GROUPING_WORDS = frozenset({"per", "each"})

#: Count phrasing in which "total" is part of the count, not a request for a SUM.
_COUNT_PHRASES = (("total", "number"), ("total", "count"))

#: Punctuation that carries no condition: sentence marks, the underscore of a snake_case
#: name, and the CJK stops. Dashes, brackets and quotes are harmless by category (below).
_HARMLESS_PUNCT = frozenset(".,?!'\":;_\u2026\u3001\u3002")
_HARMLESS_CATEGORIES = frozenset({"Pd", "Ps", "Pe", "Pi", "Pf"})
_SEPARATOR, _WORD, _CONTENT = 0, 1, 2


def _char_kind(ch: str) -> int:
    """How the allowlist reads one character: a separator (space, control, format mark or
    harmless punctuation), a word character (any letter, digit or combining mark, in ANY script),
    or content. Content is everything else ("/", "&", "+", "$", "<", an emoji, a private-use
    glyph, an unassigned code point): it may state a condition, so it must be accounted for like
    a word and is never silently dropped. Whitelisting what is harmless, not listing what is not,
    is what keeps a condition written in an unexpected script from vanishing."""
    cat = unicodedata.category(ch)
    major = cat[0]
    if major in "LNM":
        return _WORD
    if cat in ("Co", "Cs", "Cn"):
        return _CONTENT
    if major in "ZC" or cat in _HARMLESS_CATEGORIES or ch in _HARMLESS_PUNCT:
        return _SEPARATOR
    return _CONTENT


def _tokens(text: str) -> list[str]:
    """The question's tokens: runs of word characters and runs of content characters, lower-case
    and NFKC-folded (a fullwidth digit is its ASCII digit). Unicode-aware: a word in any script is
    a token, so none can be invisible to a check that compares tokens."""
    out: list[str] = []
    run: list[str] = []
    kind = _SEPARATOR
    for ch in unicodedata.normalize("NFKC", str(text or "")).lower():
        k = _char_kind(ch)
        if k != kind and run:
            out.append("".join(run))
            run = []
        kind = k
        if k != _SEPARATOR:
            run.append(ch)
    if run:
        out.append("".join(run))
    return out


def _stem(token: str) -> str:
    if token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _stems(text: str) -> list[str]:
    return [_stem(t) for t in _tokens(text)]


_CUE_STEMS: dict[str, tuple[tuple[str, ...], ...]] = {
    cue: tuple(tuple(_stems(w)) for w in words) for cue, words in _CUE_WORDS.items()
}
_AGG_STEMS = frozenset(_stems(" ".join(_AGG_WORDS)))
_QUANTITY_STEMS = frozenset(_stems(" ".join(_QUANTITY_WORDS)))


def _contains_run(tokens: Sequence[str], run: Sequence[str]) -> bool:
    n = len(run)
    return n > 0 and any(tuple(tokens[i : i + n]) == tuple(run) for i in range(len(tokens) - n + 1))


def aggregate_cues(question: str) -> frozenset[str]:
    """Which aggregates the question's own words ask for: any of ``sum``, ``avg``, ``min``,
    ``max``, ``count``, ``distinct``. "total number of students" carries ``sum`` and
    ``count``, so it fits either. Pure; English words only."""
    toks = _stems(question)
    return frozenset(
        cue for cue, runs in _CUE_STEMS.items() if any(_contains_run(toks, run) for run in runs)
    )


def _fit_cues(question: str) -> frozenset[str]:
    """``aggregate_cues`` as the fit check reads it: "total number" and "total count" are
    count phrasing, so they carry ``count`` and not ``sum`` ("total number of students" is a
    COUNT question; a SUM measure does not answer it)."""
    toks = _stems(question)
    extra: set[str] = set()
    kept: list[str] = []
    i = 0
    while i < len(toks):  # one pass: a count phrase is dropped from the tokens, cost stays linear
        width = next((len(ph) for ph in _COUNT_PHRASES if tuple(toks[i : i + len(ph)]) == ph), 0)
        if width:
            extra.add("count")
            i += width
        else:
            kept.append(toks[i])
            i += 1
    toks = kept
    cues = {
        cue for cue, runs in _CUE_STEMS.items() if any(_contains_run(toks, run) for run in runs)
    }
    return frozenset(cues | extra)


def _unnamed_qualifier(
    question: str, content: frozenset[str], grouped: frozenset[str] = frozenset()
) -> str | None:
    """The first qualifier connective the question uses that neither the measure's name nor
    the plan covers, or None. ``by <word>`` is covered when the measure names <word> or the
    plan itself groups by it (``grouped``): a breakdown the plan really computes is the
    breakdown asked for. ``per`` / ``each`` followed by such a word are covered the same way.
    ``excluding``, ``except``, ``without``, ``only``, ``not`` and ``other than`` are never
    covered by a grouping: only a measure that says so can answer them."""
    toks = _stems(question)
    for i, tok in enumerate(toks):
        following = toks[i + 1 : i + 2]
        if tok in _QUALIFIER_WORDS and tok not in content:
            if tok in _GROUPING_WORDS and following and following[0] in grouped:
                continue
            return tok
        if tok == _OTHER_THAN[0] and toks[i + 1 : i + 2] == [_OTHER_THAN[1]]:
            if not set(_OTHER_THAN) <= content:
                return "other than"
        if tok == _BY:
            if not following or (following[0] not in content and following[0] not in grouped):
                return "by " + (following[0] if following else "")
    return None


def _grouped_words(
    grouped_by: Iterable[tuple[str, str]], tables: Mapping[str, str] | None = None
) -> frozenset[str]:
    """The stemmed words a plan's group-by names: its column and its table's name AT THE SOURCE
    (``tables``: ``bronze.public_districts`` -> ``districts``). What the plan computes, not what
    the question says. The landed relation's own name is not read: ``bronze.public_districts``
    holds the schema word ``public``, which is a word of nothing the plan groups by."""
    held = tables or {}
    out: set[str] = set()
    for obj, column in grouped_by:
        for part in (held.get(str(obj), ""), str(column)):
            out.update(_stem(t) for t in _tokens(part) if len(t) >= 2)
    return frozenset(out)


# --------------------------------------------------------------------------
# exact accounting: what the question says is what the plan computes
# --------------------------------------------------------------------------
#
# A confirmed measure answers under a confirmed basis, so the number must be the one the
# question asked for. That fails in two directions, and both are the same class: a condition
# that goes missing without a trace. The plan DROPS one the question states ("in the US"
# answered by the unfiltered total), or the plan ADDS one the question never stated (a filter
# on a literal the question did not say). A list of words to look for reaches a ceiling after
# a few rounds (every list is one more word, script or plural away from a gap), so this is
# exact accounting with default deny instead:
#
#   1. The plan's filter literals are found in the question as exact, case-sensitive text on
#      token boundaries and removed. A literal the question does not contain is a refusal.
#   2. Every character that remains is accounted for. A character that is not a letter, digit
#      or mark, whitespace, ``? . ,`` or a final ``!`` is a refusal; so is a token that is not
#      a CLOSED, lowercase, ASCII word the lane understands.
#
# The words the lane understands are three explicit sets, matched exactly: the aggregate
# phrasing ``measure_fit`` already validated (cues), a short closed list of frame words that
# state no condition, and the vocabulary of the plan's own names. Nothing is stemmed, and a
# token with an uppercase letter or a digit is never a vocabulary word: ``US``, ``OR``,
# ``Wills``, ``Doe``, ``2`` and ``-2`` are all conditions or values. A false refusal is safe
# (the generated-SQL path or a named abstain answers); a stated figure for a different
# question is a confidently wrong number.

#: Words that introduce a phrase and constrain nothing. CLOSED and LOWERCASE; matched
#: case-sensitively, never stemmed and never pluralised ("Cans" is not "can"). Pronouns
#: (me us my we our i it its), connectives (to be as or by per each every across where) and
#: the demonstratives are deliberately NOT here: each can name a value or a condition.
FRAME_WORDS = frozenset(
    {
        "the", "a", "an", "of", "is", "are", "was", "were", "what", "which", "how", "much",
        "many", "do", "does", "there", "and", "in", "on", "at", "for", "with", "from", "all",
        "any", "give", "show", "tell", "find", "list", "please", "can", "could", "would", "you",
    }
)  # fmt: skip
#: A request phrase at the very start of the question, stripped as a whole phrase. ``me`` is
#: a word nowhere else.
LEADING_PHRASES = frozenset(
    {("show", "me"), ("tell", "me"), ("give", "me"), ("can", "you"), ("could", "you")}
)
#: ``by X`` / ``per X`` / ``each X`` / ``every X`` are covered only when the plan groups by X.
BREAKDOWN_WORDS = frozenset({"by", "per", "each", "every"})
#: Single-word aggregate cues ``measure_fit`` validated, and the multi-word ones as phrases.
CUE_WORD_SET = frozenset(w for words in _CUE_WORDS.values() for w in words if " " not in w)
CUE_PHRASES = tuple(w.split() for words in _CUE_WORDS.values() for w in words if " " in w)
#: Punctuation that carries no condition. ``!`` counts only as the last character.
_SEPARATOR_PUNCT = frozenset("?.,")
#: The only operators a question can state ("in district 2"); ``>``, ``<>`` and ``LIKE`` are
#: a different number, so a plan that carries one is not what was asked.
_STATED_OPERATORS = frozenset({"=", "IN"})

GAP_UNSTATED_FILTER = "unstated_filter"
GAP_UNBOUND_CONSTRAINT = "unbound_constraint"


def _is_word_char(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "LNM"


def _is_space_char(ch: str) -> bool:
    return unicodedata.category(ch) in ("Zs", "Zl", "Zp") or ch in "\t\n\r\x0b\x0c"


def _content_class(ch: str) -> str:
    major = unicodedata.category(ch)[0]
    return {"S": "symbol", "P": "punctuation", "C": "control or format character"}.get(
        major, "other character"
    )


def _render_literal(value: Any) -> str | None:
    """A plan literal as it must read in the question, or None when no question can state it."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else None
    if isinstance(value, str):
        text = unicodedata.normalize("NFKC", value)
        return text if any(_is_word_char(c) for c in text) else None
    return None


def _filter_literals(
    filters: Iterable[Sequence[Any]],
) -> tuple[list[str], list[str], dict[str, set[int]]]:
    """``(literals, unstateable, users)``: the rendered literal of every ``=`` / ``IN`` filter (a
    value once per filter that uses it), what a question cannot state (an operator other than
    those two, an empty or NULL or nested value, a filter DMS cannot read), and which filters
    (by position) use each literal. Anything in the second list is a refusal."""
    literals: list[str] = []
    bad: list[str] = []
    users: dict[str, set[int]] = {}
    for position, item in enumerate(filters):
        parts = list(item)
        if len(parts) < 4:
            bad.append("an unreadable filter")
            continue
        verb = str(parts[2]).strip().upper()
        if verb not in _STATED_OPERATORS:
            bad.append(f"operator {verb.lower() or 'none'}")
            continue
        value = parts[3]
        many = isinstance(value, (list, tuple, set, frozenset))
        if many and verb != "IN":
            bad.append(repr(value))
            continue
        if isinstance(value, (set, frozenset)):
            elements = sorted(value, key=str)
        else:
            elements = list(value) if many else [value]
        if not elements:
            bad.append("an empty list")
        stated: set[str] = set()
        for element in elements:
            rendered = _render_literal(element)
            if rendered is None:
                bad.append(repr(element))
            elif rendered not in stated:  # one IN list names a value once
                stated.add(rendered)
                literals.append(rendered)
                users.setdefault(rendered, set()).add(position)
    return literals, bad, users


def _claim_literals(text: str, literals: Iterable[str]) -> tuple[list[tuple[int, int]], list[str]]:
    """The spans of ``text`` the literals occupy, and the literals it never states enough times.

    A literal is stated by an exact, case-sensitive occurrence whose neighbours are not word
    characters (so ``North`` is not ``Northern`` and ``5`` is not the tail of ``25``). A literal
    that ``k`` filters use must be stated ``k`` times: ``North`` said once is one statement, and
    a plan that filters the district AND the region on it carries a condition the question did
    not state. Longer literals claim first and a span is never claimed twice: ``5`` is not stated
    by the ``5`` in a stated ``2.5``. Whatever sits next to a span (a sign, a point, a quote)
    stays in the text and is accounted for like any other character. The scan for a literal
    stops once it has its ``k`` spans, so its cost does not grow with how often the literal
    occurs."""
    claimed: list[tuple[int, int]] = []
    missing: list[str] = []
    wanted = Counter(literals)
    taken = bytearray(len(text))  # 1 where a claimed span already sits: overlap is a slice test
    for literal in sorted(wanted, key=lambda s: (-len(s), s)):
        need = wanted[literal]
        got = 0
        at = text.find(literal)
        while at >= 0 and got < need:
            end = at + len(literal)
            clear = (at == 0 or not _is_word_char(text[at - 1])) and (
                end == len(text) or not _is_word_char(text[end])
            )
            if clear and not any(taken[at:end]):
                taken[at:end] = b"\x01" * (end - at)
                claimed.append((at, end))
                got += 1
            at = text.find(literal, at + 1)
        if got < need:
            missing.append(
                literal if need == 1 else f"{literal} (needs {need} mentions, found {got})"
            )
    return sorted(claimed), missing


def _only_joins(between: str) -> bool:
    """Whether what sits between two stated values only JOINS them: whitespace, ``? . ,`` and
    frame words, with an ``and`` or a comma as the join. Two values side by side with no join
    ("March 2024", "Q1 2024") are two conditions in ordinary speech, not a list. A word of any
    other kind (a name of the plan, a cue, an unknown word) means a phrase of its own stands
    between the values, and a symbol is refused as content by the pass that reads what is
    left."""
    words: list[str] = []
    run: list[str] = []
    comma = False
    for ch in between:
        if _is_word_char(ch):
            run.append(ch)
            continue
        if run:
            words.append("".join(run))
            run = []
        if ch == ",":
            comma = True
        elif not (_is_space_char(ch) or ch in _SEPARATOR_PUNCT):
            return False
    if run:
        words.append("".join(run))
    if any(w not in FRAME_WORDS for w in words):
        return False
    return comma or "and" in words


def _joined_values(
    text: str, spans: Sequence[tuple[int, int]], users: Mapping[str, set[int]]
) -> list[str]:
    """Pairs of stated values that the question joins as ONE list and the plan filters as
    separate conditions. "North and South" is two places; as a district filter and a region
    filter the plan ANDs them, which matches no row (``COUNT`` prints 0) or a few (a plausible
    wrong figure). Two neighbouring claimed values are one list only when ONE filter alone
    uses both (an ``IN`` list); a value that several filters use is not provably in any one
    list, so it is refused too. Two values with a phrase of their own between them ("public
    schools in North": ``schools`` and ``in``) or with no join at all ("March 2024") are not
    joined."""
    out: list[str] = []
    for (s1, e1), (s2, e2) in zip(spans, spans[1:], strict=False):
        first, second = text[s1:e1], text[s2:e2]
        mine = users.get(first, set())
        if first != second and len(mine) == 1 and mine == users.get(second, set()):
            continue
        if _only_joins(text[e1:s2]):
            out.append(f"{first} and {second} are one list in the question but two conditions")
    return out


@dataclass(frozen=True)
class _Item:
    kind: str  # "w" word run, "x" content run, "s" a claimed literal span
    text: str


def _items(text: str, spans: Sequence[tuple[int, int]]) -> list[_Item]:
    """The question as runs of word characters, runs of content characters and claimed spans.
    Whitespace, ``? . ,`` and a final ``!`` only separate."""
    starts = dict(spans)
    bang = -1
    last = len(text) - 1
    while last >= 0 and _is_space_char(text[last]):
        last -= 1
    if last >= 0 and text[last] == "!" and not any(s <= last < e for s, e in spans):
        bang = last
    out: list[_Item] = []
    run: list[str] = []
    kind = ""

    def flush() -> None:
        nonlocal run, kind
        if run:
            out.append(_Item(kind, "".join(run)))
        run, kind = [], ""

    i = 0
    while i < len(text):
        if i in starts:
            flush()
            out.append(_Item("s", text[i : starts[i]]))
            i = starts[i]
            continue
        ch = text[i]
        if _is_space_char(ch) or ch in _SEPARATOR_PUNCT or i == bang:
            flush()
        else:
            want = "w" if _is_word_char(ch) else "x"
            if want != kind:
                flush()
                kind = want
            run.append(ch)
        i += 1
    flush()
    return out


def _lowercase_ascii_word(token: str) -> bool:
    return token.isascii() and token.isalpha() and token.islower()


def _plain(item: _Item, *, first: bool) -> str | None:
    """The word the allowlist reads, or None when the token is not a plain lowercase ASCII
    word. Only the very first token of the question may be capitalised (and only as
    ``Title``); it is lower-cased here and nowhere else."""
    if item.kind != "w":
        return None
    token = item.text
    if _lowercase_ascii_word(token):
        return token
    if first and token.isascii() and token.isalpha() and token[0].isupper():
        rest = token[1:]
        if not rest or rest.islower():
            return token.lower()
    return None


def _name_words(*names: str) -> frozenset[str]:
    """The lowercase ASCII words of snake_case / dotted names. One letter is not a word."""
    out: set[str] = set()
    for name in names:
        for word in re.split(r"[^0-9A-Za-z]+", str(name or "")):
            if len(word) >= 2 and word.isalpha():
                out.add(word.lower())
    return frozenset(out)


def _is_form(token: str, word: str) -> bool:
    """``token`` is ``word`` or its plural or singular: the ONE plural rule (``-s``, and
    ``-y`` / ``-ies``). Applied to plain lowercase tokens only, and only against the words
    of the plan's own names; never to a frame word or a cue."""
    if token == word or token == word + "s":
        return True
    if word.endswith("y") and token == word[:-1] + "ies":
        return True
    if len(token) >= 3:
        if word.endswith("ies") and token == word[:-3] + "y":
            return True
        if word.endswith("s") and not word.endswith("ss") and token == word[:-1]:
            return True
    return False


def _in_words(token: str, words: Iterable[str], *, fold: bool) -> bool:
    return any(token == w or (fold and _is_form(token, w)) for w in words)


@dataclass(frozen=True)
class Accounting:
    """``reason`` is "" when everything the question says is accounted for, else
    ``unstated_filter`` (the plan carries a value the question never stated) or
    ``unbound_constraint`` (the question carries something the plan does not).
    ``unaccounted`` is what nothing accounts for, ready to show."""

    reason: str
    unaccounted: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.reason


def account_for_question(
    question: str,
    *,
    measure: str,
    grain: str,
    grouped_by: Iterable[tuple[str, str]] = (),
    filters: Iterable[Sequence[Any]] = (),
    tables: Mapping[str, str] | None = None,
) -> Accounting:
    """Whether the question says nothing that the measure and this plan do not carry, and the
    plan carries no filter value the question did not say. Pure, default deny; see the notes
    above. The raw question is used with its case, NFKC-normalised.

    ``tables`` maps a landed relation to the name of its table AT THE SOURCE, schema left off
    (``bronze.public_schools`` -> ``schools``: ``read_source_pulls``). A relation's own name is
    never read as vocabulary, because it is ``<source schema>_<table>`` and the schema word
    (``public``, ``dbo``, ``hr``) is a word of the storage, not of the data: "public schools" is
    a condition. A relation not in ``tables`` contributes no word at all."""
    text = unicodedata.normalize("NFKC", str(question or ""))
    plan_filters = [list(f) for f in filters]
    held = tables or {}
    literals, unstateable, users = _filter_literals(plan_filters)
    # Two conditions on one column are never what a question states: "North and South" is one
    # IN list. As separate equalities they AND to the empty set (COUNT would print 0), and an
    # equality beside an IN list silently narrows it.
    columns = Counter(
        (str(p[0]), str(p[1]).casefold()) for p in plan_filters if len(p) >= 2
    )
    repeated = [f"two conditions on {col}" for (_obj, col), n in sorted(columns.items()) if n > 1]
    spans, missing = _claim_literals(text, literals)
    # "North and South" over a district filter and a region filter is the same symptom across
    # two columns: a conjunction between two stated values reads as a list, the plan ANDs them.
    joined = _joined_values(text, spans, users)
    refused = [*repeated, *unstateable, *missing, *joined]
    if refused:
        return Accounting(GAP_UNSTATED_FILTER, tuple(repr(r) for r in refused))

    group_pairs = [(str(o), str(c)) for o, c in grouped_by]
    own = _name_words(measure, held.get(grain, ""))
    # "by X" names a column the plan groups by, or the table that column sits in; the measure's
    # own table is not a breakdown ("by schools" is not "by district_id of schools").
    group = _name_words(
        *(held.get(o, "") for o, _c in group_pairs if o != grain),
        *(c for _o, c in group_pairs),
    )
    group_all = _name_words(
        *(held.get(o, "") for o, _c in group_pairs), *(c for _o, c in group_pairs)
    )
    named = _name_words(
        *(held.get(str(p[0]), "") for p in plan_filters if len(p) >= 2),
        *(str(p[1]) for p in plan_filters if len(p) >= 2),
    )
    vocabulary = own | group_all | named
    # A value every word of which is one of the question's own words ("all", "the total",
    # "schools") is not a value the question states: the occurrence is the frame, the cue or a
    # name of the plan. Claiming it as the value would call a filter the question never stated
    # stated, and the sum over it would be the answer under the confirmed badge.
    own_words = FRAME_WORDS | CUE_WORD_SET | BREAKDOWN_WORDS | vocabulary
    clashing = [
        lit
        for lit in literals
        if _tokens(lit) and all(_in_words(t, own_words, fold=True) for t in _tokens(lit))
    ]
    if clashing:
        return Accounting(GAP_UNSTATED_FILTER, tuple(repr(c) for c in clashing))
    column_words = {
        position: _name_words(str(f[1]))
        for position, f in enumerate(plan_filters)
        if len(f) >= 2
    }

    items = _items(text, spans)
    plain = [_plain(item, first=(n == 0)) for n, item in enumerate(items)]
    covered = [item.kind == "s" for item in items]
    named_by, by_joins = _breakdown_words(items, plain, group_all)

    # A request phrase at the very start, as a whole phrase and by position.
    at = 0
    while at < len(items):
        pair = (plain[at], plain[at + 1]) if at + 1 < len(items) else None
        if pair in LEADING_PHRASES:
            covered[at] = covered[at + 1] = True
            at += 2
        elif plain[at] == "please":
            covered[at] = True
            at += 1
        else:
            break
    # Aggregate phrasing of several words ("number of") is covered only as the whole phrase.
    for phrase in CUE_PHRASES:
        width = len(phrase)
        for n in range(len(items) - width + 1):
            if plain[n : n + width] == phrase:
                for k in range(n, n + width):
                    covered[k] = True

    gaps: list[str] = []
    for n, item in enumerate(items):
        if covered[n]:
            continue
        token = plain[n]
        if item.kind == "x":
            gaps.append(f"{item.text!r} ({_content_class(item.text[0])})")
            continue
        if token is None:
            gaps.append(repr(item.text))
            continue
        if (
            token == "and"
            and (len(columns) >= 2 or _opens_subject(items, plain, n, vocabulary))
            and n not in by_joins
            and not _list_and(items, plain, n, users)
            and not _and_states_condition(items, plain, n, users, column_words)
        ):
            # Beside two conditions on different columns "and" can join two values (a list, an
            # OR, a conjunction the plan ANDs); anywhere, it can join two subjects ("schools
            # and districts"). Only the join of ONE filter's own IN list, or an "and" that
            # opens a condition by naming its column ("in North and region South"), is a
            # conjunction the plan represents.
            gaps.append(repr(item.text))
            continue
        if token in FRAME_WORDS or token in CUE_WORD_SET:
            continue
        # The first token is lower-cased for the tests above and below, but a word that had to
        # be changed is not pluralised: only a token written lowercase takes the plural rule.
        fold = token == item.text
        if token in BREAKDOWN_WORDS:
            following = plain[n + 1] if n + 1 < len(items) else None
            if following is not None and _in_words(following, group, fold=True):
                continue
            gaps.append(repr(item.text))
            continue
        if _in_words(token, vocabulary, fold=fold):
            continue
        gaps.append(repr(item.text))
    for obj, column in group_pairs if not gaps else ():
        words = _name_words(column, held.get(obj, "") if obj != grain else "")
        if not any(_in_words(w, words, fold=True) for w in named_by):
            gaps.append(f"a breakdown by {column!r} that the question does not name")
    numbers = _numeric_owners(plan_filters)
    if numbers:
        filter_words = {
            position: _binder_words(str(f[1]))
            for position, f in enumerate(plan_filters)
            if len(f) >= 2
        }
        for n, item in enumerate(items):
            owners = numbers.get(item.text) if item.kind == "s" else None
            if owners and not _number_is_bound(items, plain, n, owners, users, filter_words):
                gaps.append(f"{item.text!r} (no column name of the plan stands before this number)")
    if gaps:
        return Accounting(GAP_UNBOUND_CONSTRAINT, tuple(gaps))
    return Accounting("")


#: Words that may sit between a column's name and its number ("school id 2", "district number 2").
_BINDER_FILLER = frozenset({"id", "number", "no", "num", "code", "key", "is"})
_GENERIC_COLUMN_WORDS = frozenset({"id", "number", "no", "num", "code", "key"})


def _list_and(
    items: Sequence[_Item],
    plain: Sequence[str | None],
    n: int,
    users: Mapping[str, set[int]],
) -> bool:
    """Whether the ``and`` at item ``n`` joins two values of ONE filter's own IN list: the
    nearest non-frame item on each side is a claimed value, and exactly one filter uses both."""

    def side(step: int) -> _Item | None:
        i = n + step
        while 0 <= i < len(items):
            if items[i].kind == "s":
                return items[i]
            if plain[i] not in FRAME_WORDS:
                return None
            i += step
        return None

    left, right = side(-1), side(1)
    if left is None or right is None or left.text == right.text:
        return False
    mine = users.get(left.text, set())
    return len(mine) == 1 and mine == users.get(right.text, set())


def _breakdown_words(
    items: Sequence[_Item], plain: Sequence[str | None], vocabulary: frozenset[str]
) -> tuple[list[str], set[int]]:
    """The words a question names as what it breaks the number down BY: those that follow
    ``by`` / ``per`` / ``each`` / ``every``, and the ones a list of them continues with ("by
    district and region"): frame words and words of the plan's group-by names, up to the first
    word that is neither. A plan that groups by something none of these names is a finer (or
    different) breakdown than the one asked for. Also returns the positions of the ``and`` that
    sit inside such a list: they join breakdowns, not values."""
    out: list[str] = []
    joins: set[int] = set()
    for n, word in enumerate(plain):
        if word not in BREAKDOWN_WORDS:
            continue
        j = n + 1
        while j < len(items) and items[j].kind == "w":
            token = plain[j]
            if token is None:
                break
            if token not in FRAME_WORDS and not _in_words(token, vocabulary, fold=True):
                break
            if token == "and":
                joins.add(j)
            elif token not in FRAME_WORDS:
                out.append(token)
            j += 1
    return out, joins


def _opens_subject(
    items: Sequence[_Item],
    plain: Sequence[str | None],
    n: int,
    vocabulary: frozenset[str],
) -> bool:
    """Whether the ``and`` at item ``n`` is followed (past frame words) by a word of the plan's
    own names: a second subject ("schools and districts"), not a value."""
    i = n + 1
    while i < len(items) and plain[i] in FRAME_WORDS:
        i += 1
    word = plain[i] if i < len(items) else None
    return word is not None and _in_words(word, vocabulary, fold=True)


def _and_states_condition(
    items: Sequence[_Item],
    plain: Sequence[str | None],
    n: int,
    users: Mapping[str, set[int]],
    column_words: Mapping[int, frozenset[str]],
) -> bool:
    """Whether the ``and`` at item ``n`` opens a condition of its own: the next item is a word of
    the COLUMN of the filter whose value follows ("... in North and region South"). The table's
    or the measure's name does not open one ("schools in North and schools in South" repeats the
    subject), and neither does a bare value."""
    i = n + 1
    if i >= len(items) or plain[i] is None:
        return False
    word = str(plain[i])
    j = i + 1
    while j < len(items) and plain[j] in _BINDER_FILLER:
        j += 1
    if j >= len(items) or items[j].kind != "s":
        return False
    return any(
        _in_words(word, column_words.get(position, frozenset()), fold=True)
        for position in users.get(items[j].text, set())
    )


def _numeric_owners(plan_filters: Sequence[Sequence[Any]]) -> dict[str, set[int]]:
    """rendered numeric literal -> positions of the filters that carry it as a NUMBER. A text
    value that happens to be digits is a name, not a number a column word must stand before."""
    out: dict[str, set[int]] = {}
    for position, item in enumerate(plan_filters):
        if len(item) < 4:
            continue
        value = item[3]
        many = isinstance(value, (list, tuple, set, frozenset))
        for element in list(value) if many else [value]:
            if isinstance(element, (int, float)) and not isinstance(element, bool):
                rendered = _render_literal(element)
                if rendered is not None:
                    out.setdefault(rendered, set()).add(position)
    return out


def _binder_words(column: str) -> tuple[frozenset[str], frozenset[str]]:
    """``(words, generic)``: the words that may stand before a number filtered on this column
    (its own name only), and the generic ones among them. The TABLE's name is not one of them:
    it is shared by every column of that table, so "school 1" would bind a number to
    ``district_id`` on the strength of the word "school" alone."""
    words = _name_words(column)
    specific = frozenset(w for w in words if w not in _GENERIC_COLUMN_WORDS)
    return (specific or words), frozenset(w for w in words if w in _GENERIC_COLUMN_WORDS)


def _number_is_bound(
    items: Sequence[_Item],
    plain: Sequence[str | None],
    n: int,
    owners: set[int],
    users: Mapping[str, set[int]],
    filter_words: Mapping[int, tuple[frozenset[str], frozenset[str]]],
) -> bool:
    """A number the question states binds to a filter's column only by position: the word
    BEFORE it (past an ``id`` / ``number``) is a word of that column or of its table ("district
    2", "school id 2"), or the value before it is the same filter's (an IN list: "1 and 2"). A
    number after its noun ("2 schools") or after anything else counts something else."""
    i = n - 1
    while i >= 0 and plain[i] == "and":
        i -= 1
    if i >= 0 and items[i].kind == "s" and users.get(items[i].text, set()) & owners:
        return True
    i = n - 1
    skipped: set[str] = set()
    while i >= 0 and plain[i] in _BINDER_FILLER:
        skipped.add(str(plain[i]))
        i -= 1
    for position in owners:
        words, generic = filter_words.get(position, (frozenset(), frozenset()))
        if i >= 0 and plain[i] is not None and _in_words(str(plain[i]), words, fold=True):
            return True
        if skipped & generic:
            return True  # "id 1": the word "id" is the name of a column called ..._id
    return False


def _shown(items: Sequence[str]) -> str:
    return ", ".join(items[:3]) + (", ..." if len(items) > 3 else "")


def _content_tokens(name: str) -> frozenset[str]:
    out = {
        _stem(t)
        for t in str(name or "").lower().split("_")
        if len(t) >= 2 and t not in _AGG_WORDS and _stem(t) not in _AGG_STEMS
    }
    return frozenset(out)


def _canon(token: str) -> str:
    return _QUANTITY_CANON.get(token, token)


@dataclass(frozen=True)
class FitVerdict:
    ok: bool
    pick: str | None
    reason: str
    #: What the reason is about, when it names words (the question words nothing accounts for).
    detail: str = ""


def measure_fit(
    question: str,
    measures: Mapping[str, Measure],
    *,
    candidate: str | None = None,
    grouped_by: Iterable[tuple[str, str]] = (),
    filters: Iterable[Sequence[Any]] | None = None,
    keep_gt: float | None = None,
    offset: int | None = None,
    tables: Mapping[str, str] | None = None,
) -> FitVerdict:
    """Whether a confirmed measure answers this question, by DMS's own reading.

    Cortex ranks only the caller's measures, by measure-name token overlap with the
    question. L1-style authority therefore needs DMS to land on the same measure
    independently (``candidate`` is the one Cortex or the plan named):

    * a measure matches iff ALL its content tokens (name words minus aggregate words) are
      in the question; DMS's pick is the match with the most content tokens, a tie is
      ``ambiguous``; ``candidate`` must equal the pick (``ranker_disagrees``);
    * EVERY aggregate cue in the question must be one the measure's aggregate can answer
      (``aggregate_cue``): a question that asks for an average, a count, a minimum or a
      maximum is not answered by a SUM, one that asks for distinct values is not answered by
      anything but COUNT DISTINCT, and a second cue does not launder the first ("average
      total budget" is not a SUM). The one exemption is count phrasing: "total number",
      "total count" and "how many" are compatible with count and count_distinct and with
      nothing else. A COUNT measure also needs one: "show me the schools" asks for rows;
    * a question about a rate, ratio, share, percentage, change... disagrees unless the
      measure's own name says so (``quantity_cue``);
    * a question that narrows or splits the number (``per``, ``each``, ``excluding``,
      ``except``, ``without``, ``only``, ``not``, ``other than``, ``by <word>``) disagrees
      unless the measure's own name carries that word (``qualifier_cue``). ``by`` / ``per`` /
      ``each`` are also covered by a word the PLAN groups by (``grouped_by``, the plan's
      ``(object, column)`` pairs): "total budget by district" is answered by the total grouped
      by district, and by nothing else. A false refusal here is safe; a plain total offered
      as "per student" is a wrong figure;
    * when the caller passes the plan's ``filters`` (possibly empty), the question and the plan
      are accounted for exactly (``account_for_question``): every filter literal of the plan
      is stated by the question (``unstated_filter``: the plan may not add a value, an
      operator other than ``=`` / ``IN``, a ``keep_gt`` threshold or an ``offset`` the question
      never stated), and everything left in the question is an aggregate cue, a frame word,
      a word of the measure's or the plan's own names, or a breakdown the plan really groups
      by (``unbound_constraint``: "in the US" answered by the unfiltered total). Default
      deny: anything else, in any case, script or symbol, is a condition the plan dropped.
      Two filters on one column, or a literal that more filters use than the question states,
      are conditions the plan added (``unstated_filter``), and so are two stated values that
      the question joins as one list ("North and South", "North, South") while the plan
      filters them as separate conditions on different columns. ``tables`` maps each
      relation the plan names to its table's name at the source (``read_source_pulls``): a
      relation's own name carries the source schema ("public schools"), so it is never
      vocabulary.
      ``filters=None`` means no plan is being checked, and skips both tests.

    Only structured (provenance-bearing) measures take part. The cue, quantity and qualifier
    vocabularies are English; the accounting reads every script and symbol, so a non-English
    condition is refused, never ignored.
    """
    if len(str(question or "")) > MAX_QUESTION_CHARS:
        return FitVerdict(
            False, None, REASON_QUESTION_TOO_LONG, f"more than {MAX_QUESTION_CHARS} characters"
        )
    pool = {n: m for n, m in measures.items() if m.provenance is not None}
    qtoks = frozenset(_stems(question))
    if candidate is not None:
        if candidate not in pool:
            return FitVerdict(False, None, "no_match")
        if not _content_tokens(candidate):
            return FitVerdict(False, None, "no_content_tokens")
    matches = {n: ct for n in pool if (ct := _content_tokens(n)) and ct <= qtoks}
    if not matches:
        return FitVerdict(False, None, "no_match")
    best = max(len(ct) for ct in matches.values())
    top = sorted(n for n, ct in matches.items() if len(ct) == best)
    if len(top) > 1:
        return FitVerdict(False, None, "ambiguous")
    pick = top[0]
    if candidate is not None and candidate != pick:
        return FitVerdict(False, pick, "ranker_disagrees")

    prov = pool[pick].provenance
    assert prov is not None
    # Every cue the question carries must be one this aggregate can answer. A second cue
    # does not launder the first: "average total budget" asks for an AVG, not the SUM that
    # its word "total" would otherwise let through.
    cues = _fit_cues(question)
    answers = _COMPATIBLE_CUES.get(prov.aggregate, frozenset())
    if cues - answers:
        return FitVerdict(False, pick, "aggregate_cue")
    # A row count is a number the question must ASK for ("how many", "number of", "count"):
    # "show me the schools" is a request for the rows, not for the one figure a COUNT states.
    if prov.aggregate in ("count", "count_distinct") and not cues & answers:
        return FitVerdict(False, pick, "aggregate_cue")

    asked = {_canon(t) for t in qtoks if t in _QUANTITY_STEMS}
    if "%" in str(question):
        asked.add("percent")
    named = {_canon(t) for t in _content_tokens(pick)}
    if asked - named:
        return FitVerdict(False, pick, "quantity_cue")
    grouped = tuple(grouped_by)
    if (
        _unnamed_qualifier(question, _content_tokens(pick), _grouped_words(grouped, tables))
        is not None
    ):
        return FitVerdict(False, pick, "qualifier_cue")
    if filters is not None:
        plan_filters = tuple(filters)
        # A threshold on the aggregate or a row window is a condition the plan states; no
        # question the lane accepts states one, so a plan carrying either is not what was asked.
        extras = [
            repr(name)
            for name, value in (("keep_gt", keep_gt), ("offset", offset))
            if value is not None
        ]
        if extras:
            return FitVerdict(False, pick, GAP_UNSTATED_FILTER, _shown(extras))
        reading = account_for_question(
            question,
            measure=pick,
            grain=prov.grain,
            grouped_by=grouped,
            filters=plan_filters,
            tables=tables,
        )
        if not reading.ok:
            return FitVerdict(False, pick, reading.reason, _shown(reading.unaccounted))
    return FitVerdict(True, pick, "ok")


def via_choice_reason(via: Mapping[str, str] | None, onto: Ontology) -> str | None:
    """``ambiguous_path: <why>`` when a plan-supplied ``via`` picks one of several declared links.

    Two links between the same pair of tables (``district_id`` and ``mail_district_id``, both
    foreign keys to districts) are two readings, and the layer never picks between them
    (``Ontology.compile`` refuses ``ambiguous_path`` without a ``via``). A ``via`` in the typed
    plan is Cortex making that choice, and no word of a question the lane accepts states it, so
    it is refused here: the same refusal, not a different answer. A ``via`` that names the only
    link between two tables picks nothing and passes. A name the ontology does not hold is left
    to the compiler, which refuses it by name (``unknown_link``).
    """
    for dim, name in sorted((via or {}).items()):
        link = onto.links.get(name)
        if link is None or link.to_object != dim:
            continue
        siblings = sorted(
            other.name
            for other in onto.links.values()
            if other.from_object == link.from_object and other.to_object == link.to_object
        )
        if len(siblings) > 1:
            return (
                f"{REASON_AMBIGUOUS_PATH}: the plan picks {name!r} of {len(siblings)} declared "
                f"links from {link.from_object!r} to {dim!r} ({', '.join(siblings)}); the "
                "question does not say which, and the layer will not choose"
            )
    return None


def route_choice_reason(
    onto: Ontology,
    *,
    grain: str,
    via: Mapping[str, str] | None,
    group_by: Iterable[Sequence[Any]] = (),
    filters: Iterable[Sequence[Any]] = (),
) -> str | None:
    """``ambiguous_path: <why>`` when the join route a plan takes is a choice among several.

    "Enrollments in North" is the enrollment's own district or its school's district when both
    links exist: two valid joins, two different numbers, and nothing in the question picks. The
    compiler would take the shorter one in silence, and a ``via`` would take whichever Cortex
    named; either way the customer reads one green figure with no sign of the other. So the
    confirmed lane answers only when exactly ONE route joins the grain to every object the plan
    groups by, filters on, or names in its ``via``. Routes are counted without the ``via``, over
    the links the compiler may pass (many-to-one for a grouping, any verified link for a filter,
    so a filter's count is the larger and covers both). Parallel links count as routes. A
    ``via`` that names the grain itself picks the parent's reading over the row's own. A route
    count the search could not finish is not one. Unknown objects are left to the compiler,
    which refuses them by name. A false refusal is safe; one figure for one of two questions is
    not.
    """
    picked = via_choice_reason(via, onto)
    if picked:
        return picked
    resolved_grain = onto.resolve_object(grain)
    if isinstance(resolved_grain, Refusal):
        return None
    #: object -> whether the larger, filter-sized count applies (a grouping alone needs only
    #: the many-to-one routes; a filter or a ``via`` key is counted over every verified link).
    wanted: dict[str, bool] = {}
    for role, items in (("group", group_by), ("filter", filters)):
        for item in items:
            obj = onto.resolve_object(str(item[0])) if item else None
            if isinstance(obj, str):
                wanted[obj] = wanted.get(obj, False) or role == "filter"
    via_objects: set[str] = set()
    for key in via or {}:
        obj = onto.resolve_object(str(key))
        if isinstance(obj, str):
            via_objects.add(obj)
            wanted[obj] = True
    for obj, for_filter in sorted(wanted.items()):
        if obj == resolved_grain:
            if obj in via_objects:
                return (
                    f"{REASON_AMBIGUOUS_PATH}: the plan reads {obj!r} through a link to itself; "
                    "the row's own value is the other reading, and the question does not say "
                    "which"
                )
            continue
        routes = onto.routes_between(resolved_grain, obj, for_filter=for_filter)
        if routes is None:
            return (
                f"{REASON_AMBIGUOUS_PATH}: the routes from {resolved_grain!r} to {obj!r} are "
                "too many to tell apart, and the layer will not choose"
            )
        if len(routes) > 1:
            shown = "; ".join(" -> ".join(link.name for link in route) for route in routes)
            return (
                f"{REASON_AMBIGUOUS_PATH}: more than one route joins {resolved_grain!r} to "
                f"{obj!r} ({shown}); the question does not say which, and the layer will not "
                "choose"
            )
    return None
