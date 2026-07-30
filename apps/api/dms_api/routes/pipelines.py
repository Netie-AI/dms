"""Pipeline promote API — mutations call compliance_gate before side effects."""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dms_api.deps import CortexDep
from dms_api.wiring import (
    gold_sign_metric,
    pipeline_infer_contract,
    pipeline_run,
)

router = APIRouter(prefix="/v1/pipelines", tags=["pipelines"])


class RunBody(BaseModel):
    pipeline: str | None = None
    yaml_text: str | None = None
    gold_metric: dict[str, Any] | None = None


class InferBody(BaseModel):
    source: str


class GoldSignBody(BaseModel):
    metric_id: str
    name: str
    sql: str
    steward_id: str


@router.post("/run")
def run_pipeline(body: RunBody, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="pipeline.promote",
        metadata={"task_id": "pipeline.promote", "pipeline": body.pipeline},
        client=cortex,
    )
    if not decision.allowed and decision.reason not in {
        "gate_unavailable",
        "gate_task_unknown",
    }:
        raise HTTPException(status_code=403, detail=decision.reason)
    try:
        receipt = pipeline_run(
            pipeline=body.pipeline,
            yaml_text=body.yaml_text,
            gold_metric=body.gold_metric,
            cortex=cortex,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return receipt


@router.post("/infer-contract")
def infer_contract(body: InferBody, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="pipeline.infer_contract",
        metadata={"task_id": "pipeline.infer_contract", "source": body.source},
        client=cortex,
    )
    if not decision.allowed and decision.reason not in {
        "gate_unavailable",
        "gate_task_unknown",
    }:
        raise HTTPException(status_code=403, detail=decision.reason)
    try:
        return pipeline_infer_contract(source=body.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gold/sign")
def sign_gold(body: GoldSignBody, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="pipeline.gold_sign",
        metadata={"task_id": "pipeline.gold_sign", "metric_id": body.metric_id},
        client=cortex,
    )
    if not decision.allowed and decision.reason not in {
        "gate_unavailable",
        "gate_task_unknown",
    }:
        raise HTTPException(status_code=403, detail=decision.reason)
    try:
        return gold_sign_metric(
            metric_id=body.metric_id,
            name=body.name,
            sql=body.sql,
            steward_id=body.steward_id,
            cortex=cortex,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
