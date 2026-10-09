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
* ``filter_values_present`` is rule 12 at ask time: a plan filter literal that does not
  exist in its column as landed (``north`` for ``North``) must not become a plausible 0.

``aggregate_cues`` (the cue word sets measure_fit and the ad hoc lane both read) is pure.

Nothing here imports ``generative_ask`` or ``measure_confirm``; ``space_ontology`` and
``generative_ask`` import this module.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from dms_core.control_plane.onto_store import OntoMeasure, measure_definition_hash

from dms_executor.demo_warehouse import connect_file
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
    "EXCLUSION_REASONS",
    "FIT_REASONS",
    "FitVerdict",
    "REASON_UNMATCHED_FILTER_VALUE",
    "aggregate_cues",
    "attach_confirmed_measures",
    "confirmed_measure_suggestions",
    "filter_values_present",
    "measure_exclusions",
    "measure_fit",
    "prune_unusable_measures",
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
    }
)

#: Same slot pattern as ``_violations`` / ``_column_cache``: not a dataclass field.
_EXCLUSIONS_SLOT = "_measure_exclusions"

REASON_UNMATCHED_FILTER_VALUE = "unmatched_filter_value"
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


def _describe(lake: Path | None, relations: Iterable[str]) -> dict[str, dict[str, str] | None]:
    """DESCRIBE each relation once. None = could not be read (never "empty")."""
    wanted = sorted(set(relations))
    out: dict[str, dict[str, str] | None] = dict.fromkeys(wanted)
    if not wanted or lake is None or not Path(lake).is_file():
        return out
    try:
        con = connect_file(Path(lake))
    except Exception:  # noqa: BLE001 - a lake we cannot open proves nothing about a measure
        return out
    try:
        for relation in wanted:
            if not _RELATION_RE.fullmatch(relation):
                continue
            try:
                got = con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
            except Exception:  # noqa: BLE001
                continue
            out[relation] = {str(r[0]): str(r[1]) for r in got}
    finally:
        con.close()
    return out


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
    just ran and its relation is among this turn's granted tables, and (U2) DESCRIBE of that
    relation still shows its column with the type it was confirmed with (``*`` needs only a
    readable relation). A lake that cannot be read removes every measure
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
            else:
                needs_lake.append((onto, name, m, obj.relation))

    described = _describe(lake, (rel for _o, _n, _m, rel in needs_lake))
    for onto, name, m, relation in needs_lake:
        prov = m.provenance
        assert prov is not None
        columns = described.get(relation)
        if columns is None:
            drop(onto, name, EXCL_LAKE_UNREADABLE)
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
# rule 12 at ask time
# --------------------------------------------------------------------------


def filter_values_present(plan: Any, onto: Ontology, lake: Path | None) -> str | None:
    """None when every ``=`` / ``IN`` literal of the plan's filters exists in its column as
    landed; otherwise ``unmatched_filter_value: <why>``.

    One ``SELECT 1 ... LIMIT 1`` per literal, case-sensitive exactly as the compiled
    predicate will be, at most ``MAX_PROBED_LITERALS`` per IN. Other operators are not
    probed (an empty range is an honest empty range; an encoding mismatch is what a missing
    equality value is). Anything that cannot be checked (lake unreadable, list too long,
    unusable relation or value) fails CLOSED. An object or column the ontology does not know
    is left for the compiler, which refuses it by name.
    """
    probes: list[tuple[str, str, str, Any]] = []  # (object, relation, column, literal)
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
        probes.extend((resolved, onto.objects[resolved].relation, str(column), v) for v in literals)
    if not probes:
        return None

    def cannot_check(what: str) -> str:
        return f"{REASON_UNMATCHED_FILTER_VALUE}: {what}, so the filter value was not checked"

    if lake is None or not Path(lake).is_file():
        return cannot_check("the data could not be read")
    try:
        con = connect_file(Path(lake))
    except Exception:  # noqa: BLE001
        return cannot_check("the data could not be opened")
    try:
        seen: set[tuple[str, str, str]] = set()
        for obj_name, relation, column, literal in probes:
            rendered = _literal(literal)
            key = (relation, column, rendered)
            if key in seen:
                continue
            seen.add(key)
            if not _RELATION_RE.fullmatch(relation):
                return cannot_check(f"{obj_name} is not a readable table")
            try:
                hit = con.execute(
                    f"SELECT 1 FROM {relation} WHERE {_ident(column)} = {rendered} LIMIT 1"
                ).fetchone()
            except Exception:  # noqa: BLE001 - a type that cannot be compared is not a match
                return cannot_check(f"{obj_name}.{column} could not be compared")
            if hit is None:
                shown = repr(literal) if len(repr(literal)) <= 60 else repr(literal)[:57] + "..."
                return (
                    f"{REASON_UNMATCHED_FILTER_VALUE}: {obj_name}.{column} has no value "
                    f"{shown} exactly as written"
                )
    finally:
        con.close()
    return None


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
        "no_content_tokens",
        "ranker_disagrees",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _stem(token: str) -> str:
    if token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _stems(text: str) -> list[str]:
    return [_stem(t) for t in _WORD_RE.findall(str(text or "").lower())]


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


def measure_fit(
    question: str, measures: Mapping[str, Measure], *, candidate: str | None = None
) -> FitVerdict:
    """Whether a confirmed measure answers this question, by DMS's own reading.

    Cortex ranks only the caller's measures, by measure-name token overlap with the
    question. L1-style authority therefore needs DMS to land on the same measure
    independently (``candidate`` is the one Cortex or the plan named):

    * a measure matches iff ALL its content tokens (name words minus aggregate words) are
      in the question; DMS's pick is the match with the most content tokens, a tie is
      ``ambiguous``; ``candidate`` must equal the pick (``ranker_disagrees``);
    * the question's aggregate cue must not conflict with the measure's aggregate
      (``aggregate_cue``): a question that asks for an average, a count, a minimum or a
      maximum is not answered by a SUM, and one that asks for distinct values is not
      answered by anything but COUNT DISTINCT;
    * a question about a rate, ratio, share, percentage, change... disagrees unless the
      measure's own name says so (``quantity_cue``).

    Only structured (provenance-bearing) measures take part. English tokens only.
    """
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
    cues = aggregate_cues(question)
    if cues and not (cues & _COMPATIBLE_CUES.get(prov.aggregate, frozenset())):
        return FitVerdict(False, pick, "aggregate_cue")
    if "distinct" in cues and prov.aggregate != "count_distinct":
        return FitVerdict(False, pick, "aggregate_cue")

    asked = {_canon(t) for t in qtoks if t in _QUANTITY_STEMS}
    if "%" in str(question):
        asked.add("percent")
    named = {_canon(t) for t in _content_tokens(pick)}
    if asked - named:
        return FitVerdict(False, pick, "quantity_cue")
    return FitVerdict(True, pick, "ok")
