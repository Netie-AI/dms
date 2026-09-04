"""CCA-01 — typed constraint schema and stage-trace gating.

Stages: Sense -> Asset class -> Geo -> Grain/measure -> Ontology verify ->
SQL -> Envelope. Later stages must not be CERTIFIED unless every prior stage
is CERTIFIED. Does not invent geo/class membership encodings (CCA-03/04).
"""

from __future__ import annotations

from typing import Any

STAGES: tuple[str, ...] = (
    "sense",
    "asset_class",
    "geo",
    "grain",
    "ontology",
    "sql",
    "envelope",
)

STATUSES = frozenset({"CERTIFIED", "ABSTAIN", "REFUSE"})

_REQUIRED = (
    "constraint_id",
    "type",
    "candidate",
    "binding",
    "evidence",
    "status",
    "reasons",
)


class ConstraintSchemaError(ValueError):
    """Missing or illegal constraint schema. Fail closed; do not guess encodings."""


def parse_constraint(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConstraintSchemaError("constraint must be an object")
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise ConstraintSchemaError(f"schema missing fields: {', '.join(missing)}")
    stage = str(raw["type"] or "").strip()
    if stage not in STAGES:
        raise ConstraintSchemaError(f"unknown constraint type {stage!r}")
    status = str(raw["status"] or "").strip()
    if status not in STATUSES:
        raise ConstraintSchemaError(f"unknown status {status!r}")
    evidence = raw["evidence"]
    reasons = raw["reasons"]
    if not isinstance(evidence, list) or not all(isinstance(x, str) for x in evidence):
        raise ConstraintSchemaError("evidence must be a list of strings")
    if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
        raise ConstraintSchemaError("reasons must be a list of strings")
    cid = str(raw["constraint_id"] or "").strip()
    if not cid:
        raise ConstraintSchemaError("constraint_id required")
    return {
        "constraint_id": cid,
        "type": stage,
        "candidate": str(raw["candidate"] if raw["candidate"] is not None else ""),
        "binding": None if raw["binding"] is None else str(raw["binding"]),
        "evidence": list(evidence),
        "status": status,
        "reasons": list(reasons),
    }


def parse_trace(raw: Any) -> list[dict[str, Any]]:
    """Parse a cascade trace. None/missing is closed-fail, not an empty success."""
    if raw is None:
        raise ConstraintSchemaError("schema missing")
    if not isinstance(raw, list):
        raise ConstraintSchemaError("constraint trace must be a list")
    out = [parse_constraint(item) for item in raw]
    seen: set[str] = set()
    for item in out:
        if item["type"] in seen:
            raise ConstraintSchemaError(f"duplicate stage {item['type']}")
        seen.add(item["type"])
    gate_trace(out)
    return out


def gate_trace(trace: list[dict[str, Any]]) -> None:
    """Later stages cannot be CERTIFIED unless every prior stage is CERTIFIED."""
    by_stage = {c["type"]: c for c in trace}
    blocked = False
    for stage in STAGES:
        item = by_stage.get(stage)
        if blocked:
            if item is not None and item["status"] == "CERTIFIED":
                raise ConstraintSchemaError(
                    f"{stage} must not be CERTIFIED after a prior ABSTAIN/REFUSE/missing stage"
                )
            continue
        if item is None:
            blocked = True
            continue
        if item["status"] != "CERTIFIED":
            blocked = True


def refuse_missing_schema() -> dict[str, Any]:
    """Closed-fail payload for a cascade-path ask with no schema (before L0)."""
    return {
        "badge": "ABSTAIN",
        "abstained": True,
        "text": (
            "Constraint cascade schema is missing, so I will not certify an answer. "
            "No later stage ran."
        ),
        "constraint_trace": [],
        "assumptions": ["CCA-01: schema missing; refused before L0"],
    }
