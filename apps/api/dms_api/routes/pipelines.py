"""Pipeline promote API — mutations call compliance_gate before side effects."""

from __future__ import annotations

from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
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
    # No steward_id. The signer is resolved server-side - see DR-0004. A request field
    # that names the actor on a tamper-evident record is KB attack A-0005.


@router.post("/run")
def run_pipeline(body: RunBody, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="pipeline.promote",
        metadata={"task_id": "pipeline.promote", "pipeline": body.pipeline},
        client=cortex,
    )
    enforce(decision)
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
    enforce(decision)
    try:
        return pipeline_infer_contract(source=body.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gold/sign")
def sign_gold(body: GoldSignBody, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    actor = settings.dms_actor_user_id
    decision = compliance_gate(
        action="pipeline.gold_sign",
        actor=actor,
        metadata={"task_id": "pipeline.gold_sign", "metric_id": body.metric_id},
        client=cortex,
    )
    enforce(decision)
    try:
        return gold_sign_metric(
            metric_id=body.metric_id,
            name=body.name,
            sql=body.sql,
            actor=actor,
            cortex=cortex,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
