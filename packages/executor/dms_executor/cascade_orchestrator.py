"""CCA-05 — constraint cascade before unverified L0 on the ask path.

Runs sense -> asset_class -> geo when the question cues those stages.
Later CERTIFIED is illegal if a prior stage abstained (CCA-01). Empty
encodings abstain rather than invent membership. Grain/ontology stay on
the existing L0/ontology_ask path; this module does not stamp fake CERTIFIED
for them.

Questions with no lease/buy/housing-rent, class, or SEA cue skip the cascade
so existing L0 asks (category sales, SKU ranks) stay on the current path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from dms_executor.asset_class_binder import bind_asset_class
from dms_executor.constraint_cascade import cascade_may_certify_numbers
from dms_executor.geo_binder import bind_geo_region
from dms_executor.sense_binder import bind_sense

_CLASS_SKIP = "no commercial/residential class in the question"
_GEO_SKIP = "no sea region in the question"
_SENSE_SKIP = "missing vocabulary binding"


def _reasons(item: dict[str, Any]) -> str:
    return " ".join(item.get("reasons") or []).lower()


def _sense_cued(item: dict[str, Any]) -> bool:
    if item["status"] == "CERTIFIED":
        return True
    return _SENSE_SKIP not in _reasons(item)


def _class_cued(item: dict[str, Any]) -> bool:
    if item["status"] == "CERTIFIED":
        return True
    return _CLASS_SKIP not in _reasons(item)


def _geo_cued(item: dict[str, Any]) -> bool:
    if item["status"] == "CERTIFIED":
        return True
    return _GEO_SKIP not in _reasons(item)


def run_constraint_cascade(
    question: str,
    *,
    class_encodings: Mapping[str, Sequence[str]] | None = None,
    landed_class_dim: Sequence[str] | None = None,
    region_members: Mapping[str, Sequence[str]] | None = None,
    landed_geo_dim: Sequence[str] | None = None,
    proposed_geo_members: Sequence[str] | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """Return (cascade_applies, trace).

    ``cascade_applies`` is False when the question has no CCA cues; the caller
    must leave the existing ask path alone.
    """
    sense = bind_sense(question)
    cls = bind_asset_class(
        question,
        encodings=class_encodings,
        landed_dim_values=landed_class_dim,
    )
    geo = bind_geo_region(
        question,
        region_members=region_members,
        landed_dim_values=landed_geo_dim,
        proposed_members=proposed_geo_members,
    )
    sense_on = _sense_cued(sense)
    class_on = _class_cued(cls)
    geo_on = _geo_cued(geo)
    # Geo cannot be CERTIFIED with a missing asset_class prior (CCA-01).
    if geo_on:
        class_on = True
    if not (sense_on or class_on or geo_on):
        return False, []

    trace: list[dict[str, Any]] = []
    # Sense always leads when any later stage is cued (CCA-01 prior order).
    trace.append(sense)
    if sense["status"] != "CERTIFIED":
        return True, trace
    if class_on:
        trace.append(cls)
        if cls["status"] != "CERTIFIED":
            return True, trace
    if geo_on:
        trace.append(geo)
    return True, trace


def cascade_allows_l0(trace: list[dict[str, Any]]) -> bool:
    return cascade_may_certify_numbers(trace)
