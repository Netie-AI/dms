"""Composition root — only module allowed to import dms_executor (.importlinter)."""

from __future__ import annotations

from typing import Any

from cortex_client import CortexClient
from dms_core.ask import AskServicePort
from dms_core.pipelines import GoldMetricDef
from dms_executor import (
    Executor,
    IngestReceipt,
    ingest_csv_bytes,
    list_bronze_tables,
)
from dms_executor.contract_infer import infer_contract
from dms_executor.pipeline_loader import load_pipeline_by_name, load_pipeline_yaml
from dms_executor.promote import run_promote, sign_gold_metric
from dms_ledger import append_event


def build_ask_service(cortex: CortexClient | None) -> AskServicePort:
    exe = Executor(cortex=cortex)
    exe.startup()
    return exe


def bronze_ingest(*, filename: str, data: bytes) -> IngestReceipt:
    return ingest_csv_bytes(filename=filename, data=data)


def bronze_list():
    return list_bronze_tables()


def pipeline_run(
    *,
    pipeline: str | None = None,
    yaml_text: str | None = None,
    gold_metric: dict[str, Any] | None = None,
    cortex: CortexClient | None = None,
) -> dict[str, Any]:
    if yaml_text:
        pipe = load_pipeline_yaml(yaml_text)
    elif pipeline:
        pipe = load_pipeline_by_name(pipeline)
    else:
        raise ValueError("pipeline name or yaml_text required")
    metric = None
    if gold_metric:
        metric = GoldMetricDef(
            metric_id=str(gold_metric["metric_id"]),
            name=str(gold_metric["name"]),
            sql=str(gold_metric["sql"]),
            steward_id=str(gold_metric["steward_id"]),
            signed_at=gold_metric.get("signed_at"),
            ledger_entry_id=gold_metric.get("ledger_entry_id"),
            signature=gold_metric.get("signature"),
        )
    receipt = run_promote(pipe, gold_metric=metric)
    return receipt.to_dict()


def pipeline_infer_contract(*, source: str) -> dict[str, Any]:
    return infer_contract(source).to_dict()


def gold_sign_metric(
    *,
    metric_id: str,
    name: str,
    sql: str,
    steward_id: str,
    cortex: CortexClient | None,
) -> dict[str, Any]:
    if cortex is None:
        raise ValueError("Cortex client required to sign gold metric onto the ledger")

    def _append(*, event_type: str, payload: dict[str, Any], actor: str | None = None):
        return append_event(cortex, event_type=event_type, payload=payload, actor=actor)

    metric = GoldMetricDef(
        metric_id=metric_id,
        name=name,
        sql=sql,
        steward_id=steward_id,
    )
    signed = sign_gold_metric(metric, cortex_append=_append, actor=steward_id)
    if not signed.ledger_entry_id:
        # Soft offline: still mark signed with local attestation for demo when Cortex down
        # Prefer failure — caller needs ledger proof
        raise ValueError("Cortex ledger append did not return entry_id")
    return signed.to_dict()
