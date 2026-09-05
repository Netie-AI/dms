"""RSF-02 - typed artifact schema for research -> segment -> classify -> filter.

Why this module exists
----------------------
An RSF run narrows a question in four steps: research the field, segment it,
classify the segments, filter to the ones that answer the ask. Each step throws
away candidates. If the step that discarded them leaves no record, the answer
arrives as a bare number with a route nobody can reconstruct, and Audit/Operate
cannot tell a considered rejection from a candidate the pipeline never saw. So
every stage emits an artifact carrying the options it weighed, the one it chose,
and an ordered ``route_trace`` naming what was rejected and why.

The failure this prevents is invent-green: a stage that reports CERTIFIED while
having chosen nothing, or having chosen something that was never a candidate.
Both read downstream as a settled decision. Both are refused here at parse time,
in-process and on the wire, rather than being carried into an envelope.

Where this lives, and why it does not import CCA
------------------------------------------------
RSF is homed *beside* EPIC-CCA, not inside it. The status vocabulary
CERTIFIED/ABSTAIN/REFUSE is deliberately duplicated from
``dms_executor.constraint_cascade`` instead of imported. That duplication is the
correct call twice over: RSF must not force a CCA edit when its own schema moves
(and the reverse), and ``.importlinter`` layers ``dms_core`` beneath
``dms_executor``, so a ``dms_core -> dms_executor`` import breaks the boundary
contract outright. A reader who "fixes" the duplication by importing CCA turns a
green build red and couples two schemas that were separated on purpose.

Cortex consumer types
---------------------
The Cortex-side consumer types are NOT in this repo and are NOT written by this
module. This module is the schema of record they adapt: ``RSF_WIRE_SCHEMA``
describes the JSON shape field by field, ``to_wire`` produces exactly that shape,
and ``parse_rsf_artifact`` is the validating entry point a consumer runs an
inbound payload through. DMS never imports CortexOS, and nothing here reaches
back into the engine, so the adaptation happens on the Cortex side against this
description. That half of RSF-02 is not done by this commit.

Primary representation
----------------------
The frozen dataclass ``RsfArtifact`` is primary: it validates in ``__post_init__``,
so an invent-green artifact cannot exist as a Python object even before anyone
serialises it. The ``TypedDict``s are the wire shape only, for a consumer that
type-checks JSON it did not construct.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypedDict

#: Fixed order. Stage gating below reads it, so it is the RSF pipeline order,
#: not merely a set of legal names.
RSF_STAGES: tuple[str, ...] = ("research", "segment", "classify", "filter")

#: Duplicated from CCA on purpose. See the module docstring before importing.
RSF_STATUSES = frozenset({"CERTIFIED", "ABSTAIN", "REFUSE"})

_REQUIRED_ARTIFACT = (
    "artifact_id",
    "stage",
    "question",
    "options",
    "chosen_option",
    "route_trace",
    "evidence",
    "status",
    "reasons",
)

_REQUIRED_DECISION = ("step", "considered", "chosen", "rejected", "note")


class RsfSchemaError(ValueError):
    """Missing or illegal RSF artifact. Fail closed; never degrade to an empty pass."""


class RouteDecisionWire(TypedDict):
    """One decision record as JSON. Ordered within an artifact's ``route_trace``."""

    step: str
    considered: list[str]
    chosen: str | None
    rejected: dict[str, str]
    note: str


class RsfArtifactWire(TypedDict):
    """One RSF stage artifact as JSON. This is what Cortex receives over HTTP."""

    artifact_id: str
    stage: str
    question: str
    options: list[str]
    chosen_option: str | None
    route_trace: list[RouteDecisionWire]
    evidence: list[str]
    status: str
    reasons: list[str]


#: Field -> type, as plain data a consumer can assert against without importing
#: this module. Kept next to the TypedDicts so the two cannot drift silently.
RSF_WIRE_SCHEMA: dict[str, str] = {
    "artifact_id": "str (non-empty, unique per stage emission)",
    "stage": f"str, one of {list(RSF_STAGES)}",
    "question": "str (non-empty; the ask or an input reference)",
    "options": "list[str] (every candidate this stage weighed)",
    "chosen_option": "str | None (non-None iff status == CERTIFIED; must be in options)",
    "route_trace": (
        "list of {step: str, considered: list[str], chosen: str | None, "
        "rejected: {option: reason}, note: str}"
    ),
    "evidence": "list[str] (non-empty when CERTIFIED)",
    "status": f"str, one of {sorted(RSF_STATUSES)}",
    "reasons": "list[str] (why the status is what it is)",
}


def _str_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise RsfSchemaError(f"{label} must be a list of strings")
    return list(value)


def _rejected_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RsfSchemaError("rejected must be a mapping of option -> reason")
    out: dict[str, str] = {}
    for option, reason in value.items():
        if not isinstance(option, str) or not isinstance(reason, str):
            raise RsfSchemaError("rejected must map string option to string reason")
        if not reason.strip():
            raise RsfSchemaError(f"rejected option {option!r} carries no reason")
        out[option] = reason
    return out


@dataclass(frozen=True)
class RouteDecision:
    """One step of the route: what was on the table, what survived, what did not.

    ``rejected`` is the Audit/Operate payload. A discarded option with no reason
    is indistinguishable from an option the pipeline never saw, so an empty
    reason is refused rather than stored.
    """

    step: str
    considered: tuple[str, ...] = ()
    chosen: str | None = None
    rejected: Mapping[str, str] = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "considered", tuple(self.considered))
        object.__setattr__(self, "rejected", dict(self.rejected))
        if not str(self.step).strip():
            raise RsfSchemaError("route decision step required")
        if self.chosen is not None and self.chosen not in self.considered:
            raise RsfSchemaError(
                f"route step {self.step!r} chose {self.chosen!r}, which it never considered"
            )

    def to_wire(self) -> RouteDecisionWire:
        return {
            "step": self.step,
            "considered": list(self.considered),
            "chosen": self.chosen,
            "rejected": dict(self.rejected),
            "note": self.note,
        }


@dataclass(frozen=True)
class RsfArtifact:
    """One RSF stage's verdict plus the route that produced it.

    Invariants are checked on construction, not only on serialisation, so a
    fabricated route cannot exist in memory and leak through some other path.
    """

    artifact_id: str
    stage: str
    question: str
    status: str
    options: tuple[str, ...] = ()
    chosen_option: str | None = None
    route_trace: tuple[RouteDecision, ...] = ()
    evidence: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))
        object.__setattr__(self, "route_trace", tuple(self.route_trace))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if not str(self.artifact_id).strip():
            raise RsfSchemaError("artifact_id required")
        if not str(self.question).strip():
            raise RsfSchemaError("question required; an artifact with no input is unauditable")
        if self.stage not in RSF_STAGES:
            raise RsfSchemaError(f"unknown RSF stage {self.stage!r}")
        if self.status not in RSF_STATUSES:
            raise RsfSchemaError(f"unknown status {self.status!r}")
        _require_choice_matches_status(self.status, self.chosen_option, self.options)
        if self.status == "CERTIFIED" and not self.evidence:
            raise RsfSchemaError("CERTIFIED requires evidence; a bare claim is not a certification")

    @property
    def certified(self) -> bool:
        return self.status == "CERTIFIED"

    def to_wire(self) -> dict[str, Any]:
        return to_wire(self)


def _require_choice_matches_status(
    status: str, chosen_option: str | None, options: tuple[str, ...] | list[str]
) -> None:
    """CERTIFIED means something was chosen; anything else means nothing was.

    "CERTIFIED with nothing chosen" is invent-green: a downstream stage reads the
    badge, not the empty field. "Chosen but not considered" is a fabricated
    route: the option appears in the answer without ever having been weighed.
    """
    if status == "CERTIFIED":
        if chosen_option is None:
            raise RsfSchemaError("CERTIFIED requires a chosen_option; nothing was chosen")
    elif chosen_option is not None:
        raise RsfSchemaError(f"{status} must not carry a chosen_option ({chosen_option!r})")
    if chosen_option is not None and chosen_option not in tuple(options):
        raise RsfSchemaError(
            f"chosen_option {chosen_option!r} is not one of the options considered"
        )


def parse_route_decision(raw: Any) -> RouteDecisionWire:
    """Validate one route decision record. Unknown shape is an error, not a skip."""
    if not isinstance(raw, dict):
        raise RsfSchemaError("route decision must be an object")
    missing = [k for k in _REQUIRED_DECISION if k not in raw]
    if missing:
        raise RsfSchemaError(f"route decision missing fields: {', '.join(missing)}")
    step = str(raw["step"] or "").strip()
    if not step:
        raise RsfSchemaError("route decision step required")
    considered = _str_list(raw["considered"], "considered")
    chosen = raw["chosen"]
    if chosen is not None and not isinstance(chosen, str):
        raise RsfSchemaError("route decision chosen must be a string or null")
    if chosen is not None and chosen not in considered:
        raise RsfSchemaError(f"route step {step!r} chose {chosen!r}, which it never considered")
    note = raw["note"]
    if not isinstance(note, str):
        raise RsfSchemaError("route decision note must be a string")
    return {
        "step": step,
        "considered": considered,
        "chosen": chosen,
        "rejected": _rejected_map(raw["rejected"]),
        "note": note,
    }


def parse_rsf_artifact(raw: Any) -> dict[str, Any]:
    """Validate one RSF artifact and return its normalised wire dict.

    Every rejection path raises. None, a missing field, an unknown stage and an
    unknown status are all errors: an empty success here would let a stage that
    never ran read as a stage that found nothing.
    """
    if raw is None:
        raise RsfSchemaError("schema missing")
    if not isinstance(raw, dict):
        raise RsfSchemaError("RSF artifact must be an object")
    missing = [k for k in _REQUIRED_ARTIFACT if k not in raw]
    if missing:
        raise RsfSchemaError(f"schema missing fields: {', '.join(missing)}")

    artifact_id = str(raw["artifact_id"] or "").strip()
    if not artifact_id:
        raise RsfSchemaError("artifact_id required")
    stage = str(raw["stage"] or "").strip()
    if stage not in RSF_STAGES:
        raise RsfSchemaError(f"unknown RSF stage {stage!r}")
    status = str(raw["status"] or "").strip()
    if status not in RSF_STATUSES:
        raise RsfSchemaError(f"unknown status {status!r}")
    question = str(raw["question"] or "").strip()
    if not question:
        raise RsfSchemaError("question required; an artifact with no input is unauditable")

    options = _str_list(raw["options"], "options")
    evidence = _str_list(raw["evidence"], "evidence")
    reasons = _str_list(raw["reasons"], "reasons")
    chosen = raw["chosen_option"]
    if chosen is not None and not isinstance(chosen, str):
        raise RsfSchemaError("chosen_option must be a string or null")
    _require_choice_matches_status(status, chosen, options)
    if status == "CERTIFIED" and not evidence:
        raise RsfSchemaError("CERTIFIED requires evidence; a bare claim is not a certification")

    route_raw = raw["route_trace"]
    if not isinstance(route_raw, list):
        raise RsfSchemaError("route_trace must be a list")
    route_trace = [parse_route_decision(item) for item in route_raw]

    return {
        "artifact_id": artifact_id,
        "stage": stage,
        "question": question,
        "options": options,
        "chosen_option": chosen,
        "route_trace": route_trace,
        "evidence": evidence,
        "status": status,
        "reasons": reasons,
    }


def parse_rsf_trace(raw: Any) -> list[dict[str, Any]]:
    """Parse a run's stage artifacts. None/missing is closed-fail, not an empty success."""
    if raw is None:
        raise RsfSchemaError("schema missing")
    if not isinstance(raw, list):
        raise RsfSchemaError("RSF trace must be a list")
    out = [parse_rsf_artifact(item) for item in raw]
    seen: set[str] = set()
    for item in out:
        if item["stage"] in seen:
            raise RsfSchemaError(f"duplicate stage {item['stage']}")
        seen.add(item["stage"])
    require_certified_priors(out)
    return out


def require_certified_priors(trace: list[dict[str, Any]]) -> None:
    """A later stage cannot be CERTIFIED once a prior stage abstained, refused or is absent.

    Filtering a classification that was never certified produces a confident
    answer standing on an uncertified segment. The order in ``RSF_STAGES`` is
    what "prior" means.
    """
    by_stage = {a["stage"]: a for a in trace}
    blocked = False
    for stage in RSF_STAGES:
        item = by_stage.get(stage)
        if blocked:
            if item is not None and item["status"] == "CERTIFIED":
                raise RsfSchemaError(
                    f"{stage} must not be CERTIFIED after a prior ABSTAIN/REFUSE/missing stage"
                )
            continue
        if item is None or item["status"] != "CERTIFIED":
            blocked = True


def to_wire(artifact: RsfArtifact | Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe dict for the HTTP hop: no dataclasses, no tuples, no sets.

    Accepts either the dataclass or an already-parsed dict, because both sides of
    the hop use this one function and a second serialiser is a second place for
    the shape to drift.
    """
    if isinstance(artifact, RsfArtifact):
        wire: RsfArtifactWire = {
            "artifact_id": artifact.artifact_id,
            "stage": artifact.stage,
            "question": artifact.question,
            "options": list(artifact.options),
            "chosen_option": artifact.chosen_option,
            "route_trace": [d.to_wire() for d in artifact.route_trace],
            "evidence": list(artifact.evidence),
            "status": artifact.status,
            "reasons": list(artifact.reasons),
        }
        return dict(wire)
    # A mapping re-enters through the validator: serialising an unchecked dict is
    # how an invalid artifact reaches Cortex looking well formed.
    return parse_rsf_artifact(dict(artifact))


def from_wire(raw: Any) -> RsfArtifact:
    """Validated dict -> dataclass. The inbound counterpart of ``to_wire``."""
    parsed = parse_rsf_artifact(raw)
    return RsfArtifact(
        artifact_id=parsed["artifact_id"],
        stage=parsed["stage"],
        question=parsed["question"],
        status=parsed["status"],
        options=tuple(parsed["options"]),
        chosen_option=parsed["chosen_option"],
        route_trace=tuple(
            RouteDecision(
                step=d["step"],
                considered=tuple(d["considered"]),
                chosen=d["chosen"],
                rejected=dict(d["rejected"]),
                note=d["note"],
            )
            for d in parsed["route_trace"]
        ),
        evidence=tuple(parsed["evidence"]),
        reasons=tuple(parsed["reasons"]),
    )


def refuse_missing_schema(stage: str = "research") -> dict[str, Any]:
    """Closed-fail payload for an RSF stage asked to report with no schema."""
    named = stage if stage in RSF_STAGES else RSF_STAGES[0]
    return {
        "artifact_id": f"rsf_missing_{named}",
        "stage": named,
        "question": "(missing)",
        "options": [],
        "chosen_option": None,
        "route_trace": [],
        "evidence": [],
        "status": "ABSTAIN",
        "reasons": [f"RSF-02: schema missing at {named}; nothing was chosen"],
    }


__all__ = [
    "RSF_STAGES",
    "RSF_STATUSES",
    "RSF_WIRE_SCHEMA",
    "RouteDecision",
    "RouteDecisionWire",
    "RsfArtifact",
    "RsfArtifactWire",
    "RsfSchemaError",
    "from_wire",
    "parse_route_decision",
    "parse_rsf_artifact",
    "parse_rsf_trace",
    "refuse_missing_schema",
    "require_certified_priors",
    "to_wire",
]
