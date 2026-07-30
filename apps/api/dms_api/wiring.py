"""Composition root — only module allowed to import dms_executor (.importlinter)."""

from __future__ import annotations

from typing import Any

import dms_executor
from cortex_client import CortexClient
from dms_core.ask import AskServicePort
from dms_core.pipelines import GoldMetricDef
from dms_ledger import append_event


def build_ask_service(cortex: CortexClient | None) -> AskServicePort:
    exe = dms_executor.Executor(cortex=cortex)
    exe.startup()
    return exe


def bronze_ingest(*, filename: str, data: bytes) -> dms_executor.IngestReceipt:
    return dms_executor.ingest_csv_bytes(filename=filename, data=data)


def bronze_list():
    return dms_executor.list_bronze_tables()


def batch_ingest(files: list[tuple[str, bytes]]) -> dict[str, Any]:
    return dms_executor.ingest_batch(files).to_dict()


def pipeline_run(
    *,
    pipeline: str | None = None,
    yaml_text: str | None = None,
    gold_metric: dict[str, Any] | None = None,
    cortex: CortexClient | None = None,
) -> dict[str, Any]:
    if yaml_text:
        pipe = dms_executor.load_pipeline_yaml(yaml_text)
    elif pipeline:
        pipe = dms_executor.load_pipeline_by_name(pipeline)
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
    receipt = dms_executor.run_promote(pipe, gold_metric=metric)
    return receipt.to_dict()


def pipeline_infer_contract(*, source: str) -> dict[str, Any]:
    return dms_executor.infer_contract(source).to_dict()


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
    signed = dms_executor.sign_gold_metric(metric, cortex_append=_append, actor=steward_id)
    if not signed.ledger_entry_id:
        raise ValueError("Cortex ledger append did not return entry_id")
    return signed.to_dict()
