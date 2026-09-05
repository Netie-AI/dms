"""RSF-02 - typed artifact schema for research -> segment -> classify -> filter.

The cases below are the invent-green cases: CERTIFIED with nothing chosen, a
choice nobody considered, a later stage certified on top of an abstention, and a
missing schema degrading into an empty pass. Each must raise, not return.
"""

from __future__ import annotations

import pytest
from dms_core.rsf import (
    RouteDecision,
    RsfArtifact,
    RsfSchemaError,
    from_wire,
    parse_rsf_artifact,
    parse_rsf_trace,
    to_wire,
)


def _decision(
    step: str = "pick_segment_key",
    *,
    considered: list[str] | None = None,
    chosen: str | None = "region",
    rejected: dict[str, str] | None = None,
) -> dict:
    return {
        "step": step,
        "considered": ["region", "country"] if considered is None else considered,
        "chosen": chosen,
        "rejected": (
            {"country": "not a column of the granted table"} if rejected is None else rejected
        ),
        "note": "grant lists sales_fact only",
    }


def _artifact(
    stage: str = "segment",
    status: str = "CERTIFIED",
    *,
    options: list[str] | None = None,
    chosen_option: str | None = "region",
    evidence: list[str] | None = None,
) -> dict:
    return {
        "artifact_id": f"rsf_{stage}",
        "stage": stage,
        "question": "revenue by market last quarter",
        "options": ["region", "country"] if options is None else options,
        "chosen_option": chosen_option,
        "route_trace": [_decision()],
        "evidence": ["column=sales_fact.region"] if evidence is None else evidence,
        "status": status,
        "reasons": [],
    }


def test_certified_artifact_round_trips() -> None:
    parsed = parse_rsf_artifact(_artifact())
    assert parsed["status"] == "CERTIFIED"
    assert parsed["chosen_option"] == "region"

    wire = to_wire(from_wire(parsed))
    assert wire == parsed
    # JSON-safe: no tuples, no dataclasses leaking through the HTTP hop.
    assert isinstance(wire["options"], list)
    assert isinstance(wire["route_trace"][0], dict)


def test_certified_without_chosen_option_raises() -> None:
    with pytest.raises(RsfSchemaError, match="requires a chosen_option"):
        parse_rsf_artifact(_artifact(status="CERTIFIED", chosen_option=None))


def test_abstain_with_chosen_option_raises() -> None:
    with pytest.raises(RsfSchemaError, match="must not carry a chosen_option"):
        parse_rsf_artifact(_artifact(status="ABSTAIN", chosen_option="region"))


def test_chosen_option_outside_options_raises() -> None:
    with pytest.raises(RsfSchemaError, match="not one of the options"):
        parse_rsf_artifact(_artifact(options=["country"], chosen_option="region"))


def test_unknown_stage_raises() -> None:
    with pytest.raises(RsfSchemaError, match="unknown RSF stage"):
        parse_rsf_artifact(_artifact(stage="enrich"))


def test_unknown_status_raises() -> None:
    with pytest.raises(RsfSchemaError, match="unknown status"):
        parse_rsf_artifact(_artifact(status="OK"))


def test_missing_required_field_raises() -> None:
    raw = _artifact()
    del raw["chosen_option"]
    with pytest.raises(RsfSchemaError, match="schema missing fields: chosen_option"):
        parse_rsf_artifact(raw)


def test_trace_none_raises_rather_than_empty_success() -> None:
    with pytest.raises(RsfSchemaError, match="schema missing"):
        parse_rsf_trace(None)


def test_duplicate_stage_in_trace_raises() -> None:
    trace = [_artifact("research"), _artifact("research")]
    with pytest.raises(RsfSchemaError, match="duplicate stage research"):
        parse_rsf_trace(trace)


def test_certified_after_prior_abstain_raises() -> None:
    trace = [
        _artifact("research"),
        _artifact("segment", status="ABSTAIN", chosen_option=None),
        _artifact("classify"),
    ]
    with pytest.raises(RsfSchemaError, match="classify must not be CERTIFIED"):
        parse_rsf_trace(trace)


def test_certified_requires_evidence() -> None:
    with pytest.raises(RsfSchemaError, match="CERTIFIED requires evidence"):
        parse_rsf_artifact(_artifact(evidence=[]))


def test_route_trace_survives_to_wire_with_rejection_reasons() -> None:
    """The Audit/Operate requirement: why an option lost must reach the consumer."""
    artifact = RsfArtifact(
        artifact_id="rsf_classify",
        stage="classify",
        question="which SKUs are agriculture",
        status="CERTIFIED",
        options=["agri_pack", "crop_substring"],
        chosen_option="agri_pack",
        route_trace=(
            RouteDecision(
                step="choose_matcher",
                considered=("agri_pack", "crop_substring"),
                chosen="agri_pack",
                rejected={"crop_substring": "'crop' matches Crop Insurance Services"},
                note="exact match on normalised form",
            ),
        ),
        evidence=["pack=agri_pack"],
    )
    wire = to_wire(artifact)
    step = wire["route_trace"][0]
    assert step["chosen"] == "agri_pack"
    assert step["considered"] == ["agri_pack", "crop_substring"]
    assert step["rejected"]["crop_substring"] == "'crop' matches Crop Insurance Services"
    # And it survives the return leg unchanged.
    assert to_wire(from_wire(wire)) == wire


def test_rsf_module_does_not_import_executor_or_api() -> None:
    """The 'beside CCA, not inside it' rule, and the import-linter layer order.

    This proves two things and no more: the module's own source names no banned
    package, and importing it alone pulls none into ``sys.modules``. It cannot
    prove a lazy import inside an unexecuted branch is absent - ``lint-imports``
    is the gate that covers the whole package statically.
    """
    import subprocess
    import sys
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "packages/core/dms_core/rsf.py"
    text = source.read_text(encoding="utf-8")
    for banned in ("dms_executor", "dms_api", "dms_ledger", "CortexOS", "cortexos"):
        assert f"import {banned}" not in text
        assert f"from {banned}" not in text

    # A fresh interpreter with only packages/core on the path: if rsf.py needed a
    # sibling package, this import would fail outright.
    probe = (
        f"import sys; sys.path.insert(0, {str(source.parents[1])!r}); "
        "import dms_core.rsf; "
        "print([m for m in sys.modules if m.split('.')[0] in "
        "{'dms_executor', 'dms_api', 'dms_ledger', 'CortexOS', 'cortexos'}])"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
