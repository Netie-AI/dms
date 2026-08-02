"""Invariant: silver pipelines must declare lineage (propagate or aggregate+reason).

INVARIANT-CHANGE required in any commit that weakens this check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from dms_executor.pipeline_loader import (
    PipelineLoadError,
    load_pipeline_file,
    validate_pipeline_dict,
)

ROOT = Path(__file__).resolve().parents[2]
PIPELINES = ROOT / "pipelines"


def _iter_pipeline_files() -> list[Path]:
    if not PIPELINES.is_dir():
        return []
    return sorted(PIPELINES.glob("*.yaml")) + sorted(PIPELINES.glob("*.yml"))


@pytest.mark.parametrize("path", _iter_pipeline_files(), ids=lambda p: p.name)
def test_checked_in_pipelines_declare_lineage(path: Path) -> None:
    pipe = load_pipeline_file(path)
    if pipe.is_silver:
        assert pipe.lineage in ("propagate", "aggregate")
        if pipe.lineage == "aggregate":
            assert pipe.lineage_reason and pipe.lineage_reason.strip()


def test_silver_yaml_without_lineage_rejected() -> None:
    data = {
        "target": "silver.evil",
        "sources": ["bronze.x"],
        "contract": {"columns": {"a": {"type": "text"}}},
    }
    with pytest.raises(PipelineLoadError, match="lineage"):
        validate_pipeline_dict(data)


def test_silver_without_src_declaration_fails_when_aggregate_undocumented() -> None:
    """Simulate a hand-authored dict that would write silver without provenance."""
    raw = yaml.safe_load(
        """
        target: silver.no_prov
        sources: [bronze.x]
        lineage: aggregate
        """
    )
    with pytest.raises(PipelineLoadError, match="lineage_reason"):
        validate_pipeline_dict(raw)
