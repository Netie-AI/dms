"""Load and validate declarative pipeline YAML.

Swap scenario: PyYAML → ruamel.yaml when comment-preserving round-trip edit is required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dms_core.pipelines import ColumnContract, PipelineContract, PipelineDef

REPO_PIPELINES = Path(__file__).resolve().parents[3] / "pipelines"


class PipelineLoadError(ValueError):
    """Invalid or incomplete pipeline definition."""


def pipelines_dir(override: Path | None = None) -> Path:
    return override or REPO_PIPELINES


def _parse_columns(raw: dict[str, Any] | None) -> dict[str, ColumnContract]:
    out: dict[str, ColumnContract] = {}
    if not raw:
        return out
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise PipelineLoadError(f"column {name!r} must be a mapping")
        out[name] = ColumnContract(
            type=str(spec.get("type", "text")),
            required=bool(spec.get("required", False)),
            min=float(spec["min"]) if "min" in spec and spec["min"] is not None else None,
            max=float(spec["max"]) if "max" in spec and spec["max"] is not None else None,
            allowed_from=spec.get("allowed_from"),
        )
    return out


def validate_pipeline_dict(data: dict[str, Any], *, path: str | None = None) -> PipelineDef:
    if not isinstance(data, dict):
        raise PipelineLoadError("pipeline root must be a mapping")
    target = data.get("target")
    sources = data.get("sources")
    lineage = data.get("lineage")
    if not target or not isinstance(target, str):
        raise PipelineLoadError("target is required")
    if not sources or not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        raise PipelineLoadError("sources must be a non-empty list of strings")
    if lineage not in ("propagate", "aggregate"):
        raise PipelineLoadError(
            "lineage is required and must be 'propagate' or 'aggregate' "
            "(provenance cannot be retrofitted onto rows written without it)"
        )
    lineage_reason = data.get("lineage_reason") or data.get("reason")
    if lineage == "aggregate" and not (isinstance(lineage_reason, str) and lineage_reason.strip()):
        raise PipelineLoadError(
            "lineage: aggregate requires a non-empty lineage_reason documenting why _src is dropped"
        )

    contract_raw = data.get("contract")
    contract: PipelineContract | None = None
    if contract_raw is not None:
        if not isinstance(contract_raw, dict):
            raise PipelineLoadError("contract must be a mapping")
        cols = _parse_columns(contract_raw.get("columns"))
        dedup = contract_raw.get("dedup_key") or []
        if not isinstance(dedup, list):
            raise PipelineLoadError("dedup_key must be a list")
        expectations = contract_raw.get("expectations") or []
        if not isinstance(expectations, list):
            raise PipelineLoadError("expectations must be a list")
        norm_exp: list[dict[str, str]] = []
        for item in expectations:
            if isinstance(item, dict):
                norm_exp.append({str(k): str(v) for k, v in item.items()})
            else:
                raise PipelineLoadError("each expectation must be a mapping")
        contract = PipelineContract(
            columns=cols,
            dedup_key=[str(x) for x in dedup],
            expectations=norm_exp,
        )

    if target.startswith("silver.") and lineage == "propagate" and contract is None:
        # silver without contract is allowed for lineage-only joins, but rare
        pass

    join_on = data.get("join_on")
    if join_on is not None and not isinstance(join_on, list):
        raise PipelineLoadError("join_on must be a list")

    return PipelineDef(
        target=target,
        sources=list(sources),
        lineage=lineage,  # type: ignore[arg-type]
        contract=contract,
        lineage_reason=str(lineage_reason) if lineage_reason else None,
        join_on=[str(x) for x in join_on] if join_on else None,
        name=data.get("name"),
        path=path,
    )


def load_pipeline_yaml(text: str, *, path: str | None = None) -> PipelineDef:
    data = yaml.safe_load(text)
    if data is None:
        raise PipelineLoadError("empty pipeline document")
    return validate_pipeline_dict(data, path=path)


def load_pipeline_file(path: Path) -> PipelineDef:
    return load_pipeline_yaml(path.read_text(encoding="utf-8"), path=str(path))


def load_pipeline_by_name(name: str, *, root: Path | None = None) -> PipelineDef:
    base = pipelines_dir(root)
    candidates = [
        base / name,
        base / f"{name}.yaml",
        base / f"{name}.yml",
    ]
    for c in candidates:
        if c.is_file():
            return load_pipeline_file(c)
    raise FileNotFoundError(f"pipeline not found: {name} under {base}")


def assert_silver_lineage_ok(pipe: PipelineDef) -> None:
    """CI invariant helper — silver must propagate _src or document aggregate."""
    if not pipe.is_silver:
        return
    if pipe.lineage == "propagate":
        return
    if pipe.lineage == "aggregate" and pipe.lineage_reason:
        return
    raise PipelineLoadError(
        f"silver pipeline {pipe.target!r} missing lineage:propagate "
        "or lineage:aggregate + lineage_reason"
    )
