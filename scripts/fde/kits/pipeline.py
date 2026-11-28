"""Pipeline kits. Each one calls ``fde_pipeline`` and does not reimplement the stage.

    python scripts/fde/kits/pipeline.py --engagement DIR
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_FDE = Path(__file__).resolve().parents[1]
if str(_FDE) not in sys.path:
    sys.path.insert(0, str(_FDE))

import fde_pipeline as fp  # noqa: E402
import report  # noqa: E402

_FIX_EXTRACT = "Land the source with a read-only login before later stages."
_FIX_PROFILE = "Profile runs after extract has written a views file."
_FIX_ONTOLOGY = "The structural draft needs a profile and the extract keys."
_FIX_PACK = "No answerable question could be generated from the draft."
_FIX_SKIPPED_PACK = "Pack runs after the ontology draft."


def _started(ctx: Any) -> dict[str, Any]:
    return report.new_report(
        kit=ctx.kit_name,
        stage=ctx.kit_stage,
        engagement=ctx.engagement_name,
        tenant_id=ctx.tenant_id,
        dms_sha_value=ctx.dms_sha,
        flags=ctx.flags,
        started_at=ctx.started_at,
    )


def _one(
    ctx: Any,
    *,
    id: str,
    title: str,
    severity: str,
    status: str,
    evidence: dict[str, Any],
    fix: str,
    source: str,
) -> dict[str, Any]:
    rep = _started(ctx)
    report.add_check(
        rep,
        report.make_check(
            id=id,
            title=title,
            severity=severity,
            status=status,
            evidence=evidence,
            fix=fix,
            source=source,
        ),
    )
    return report.finish(rep)


def run_extract(ctx: Any) -> dict[str, Any]:
    ctx.kit_name, ctx.kit_stage = "extract", "P1"
    ext = fp.stage_extract(ctx.tenant, ctx.work, None)
    ctx.state["extract"] = ext
    ok = ext.get("status") == "OK" and bool(ext.get("views"))
    return _one(
        ctx,
        id="P1.01",
        title="Extract landed rows",
        severity="critical",
        status="PASS" if ok else "SKIPPED",
        evidence={
            "engine": ext.get("engine"),
            "tables": ext.get("tables"),
            "rows": ext.get("rows"),
            "status": ext.get("status"),
        },
        fix="" if ok else _FIX_EXTRACT,
        source="scripts/fde/fde_pipeline.py:stage_extract",
    )


def run_profile(ctx: Any) -> dict[str, Any]:
    ctx.kit_name, ctx.kit_stage = "profile", "P2"
    ext = ctx.state.get("extract") or {}
    views = ext.get("views")
    if not views:
        return _one(
            ctx,
            id="P2.01",
            title="Profile columns and key candidates",
            severity="high",
            status="SKIPPED",
            evidence={"reason": "extract did not land a views file"},
            fix=_FIX_PROFILE,
            source="scripts/fde/fde_pipeline.py:stage_profile",
        )
    prof = fp.stage_profile(Path(views), ext.get("pk") or {})
    ctx.state["profile"] = prof
    ok = prof.get("status") == "OK" and isinstance(prof.get("tables"), dict)
    return _one(
        ctx,
        id="P2.01",
        title="Profile columns and key candidates",
        severity="high",
        status="PASS" if ok else "SKIPPED",
        evidence={
            "status": prof.get("status"),
            "tables": len(prof.get("tables") or {}),
            "keys_source": prof.get("keys_source"),
            "fk_candidates": len(prof.get("fk_candidates") or []),
        },
        fix="" if ok else _FIX_PROFILE,
        source="scripts/fde/fde_pipeline.py:stage_profile",
    )


def run_ontology(ctx: Any) -> dict[str, Any]:
    ctx.kit_name, ctx.kit_stage = "ontology", "P3"
    prof = ctx.state.get("profile")
    ext = ctx.state.get("extract")
    if not prof or not ext:
        return _one(
            ctx,
            id="P3.01",
            title="Structural ontology draft",
            severity="high",
            status="SKIPPED",
            evidence={"reason": "profile or extract did not finish"},
            fix=_FIX_ONTOLOGY,
            source="scripts/fde/fde_pipeline.py:stage_ontology",
        )
    onto = fp.stage_ontology(
        prof, prof.get("pk_used") or {}, ext.get("fk") or [], ctx.tenant.glossary
    )
    ctx.state["ontology"] = onto
    ok = onto.get("status") == "OK" and isinstance(onto.get("entities"), dict)
    return _one(
        ctx,
        id="P3.01",
        title="Structural ontology draft",
        severity="high",
        status="PASS" if ok else "SKIPPED",
        evidence={
            "status": onto.get("status"),
            "entities": len(onto.get("entities") or {}),
            "joins": len(onto.get("joins") or []),
        },
        fix="" if ok else _FIX_ONTOLOGY,
        source="scripts/fde/fde_pipeline.py:stage_ontology",
    )


def run_pack(ctx: Any) -> dict[str, Any]:
    ctx.kit_name, ctx.kit_stage = "pack", "P4"
    onto = ctx.state.get("ontology")
    ext = ctx.state.get("extract") or {}
    views = ext.get("views")
    if not onto or not views:
        return _one(
            ctx,
            id="P4.01",
            title="Question pack from the draft",
            severity="high",
            status="SKIPPED",
            evidence={"reason": "ontology draft or views file is missing"},
            fix=_FIX_SKIPPED_PACK,
            source="scripts/fde/fde_pipeline.py:stage_pack",
        )
    pack = fp.stage_pack(
        onto,
        Path(views),
        ctx.tenant.glossary,
        max_questions=int(ctx.max_questions),
    )
    ctx.state["pack"] = pack
    status = pack.get("status")
    if status == "OK":
        check_status, fix = "PASS", ""
    elif status == "EMPTY":
        check_status, fix = "FAIL", _FIX_PACK
    else:
        check_status, fix = "SKIPPED", _FIX_SKIPPED_PACK
    n_ans = sum(1 for item in pack.get("questions") or [] if item.get("expect") == "answer")
    return _one(
        ctx,
        id="P4.01",
        title="Question pack from the draft",
        severity="high",
        status=check_status,
        evidence={
            "status": status,
            "questions": len(pack.get("questions") or []),
            "answerable": n_ans,
            "dropped": len(pack.get("dropped") or []),
        },
        fix=fix,
        source="scripts/fde/fde_pipeline.py:stage_pack",
    )


def register_kits() -> None:
    from kits import KitSpec, register

    register(
        KitSpec(
            "extract",
            "P1",
            run_extract,
            (("P1.01", "Extract landed rows", "critical"),),
        )
    )
    register(
        KitSpec(
            "profile",
            "P2",
            run_profile,
            (("P2.01", "Profile columns and key candidates", "high"),),
        )
    )
    register(
        KitSpec(
            "ontology",
            "P3",
            run_ontology,
            (("P3.01", "Structural ontology draft", "high"),),
        )
    )
    register(
        KitSpec(
            "pack",
            "P4",
            run_pack,
            (("P4.01", "Question pack from the draft", "high"),),
        )
    )


def main(argv: list[str] | None = None) -> int:
    import kit

    args = list(sys.argv[1:] if argv is None else argv)
    if "--stage" not in args:
        args = ["--stage", "extract,profile,ontology,pack", *args]
    return kit.main(["run", *args])


if __name__ == "__main__":
    raise SystemExit(main())
