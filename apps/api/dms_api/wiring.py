"""Composition root — only module allowed to import dms_executor (.importlinter)."""

from __future__ import annotations

from typing import Any

import dms_executor
from cortex_client import CortexClient
from dms_core.ask import AskServicePort
from dms_core.pipelines import GoldMetricDef
from dms_ledger import append_event


def build_ask_service(
    cortex: CortexClient | None,
    *,
    openvault_url: str | None = None,
) -> AskServicePort:
    from dms_api.settings import get_settings

    settings = get_settings()
    ov_url = openvault_url or settings.openvault_url
    exe = dms_executor.Executor(cortex=cortex, openvault_url=ov_url)
    exe.startup()
    return exe


def bronze_ingest(*, filename: str, data: bytes, space_id: str | None = None) -> dms_executor.IngestReceipt:
    return dms_executor.ingest_csv_bytes(filename=filename, data=data, space_id=space_id)


def bronze_list(*, space_id: str | None = None):
    return dms_executor.list_bronze_tables(space_id=space_id)


def warehouse_tables():
    from dms_executor.warehouse_browse import list_warehouse_tables

    return list_warehouse_tables()


def warehouse_preview(table: str, *, limit: int = 100, offset: int = 0):
    from dms_executor.warehouse_browse import preview_warehouse_table

    return preview_warehouse_table(table, limit=limit, offset=offset)


def bronze_preview(table: str, *, limit: int = 100, offset: int = 0):
    from dms_executor.warehouse_browse import preview_bronze_table

    return preview_bronze_table(table, limit=limit, offset=offset)


def library_tree(
    *,
    sources: list[dict[str, Any]],
    bronze: list[dict[str, Any]],
    warehouse: list[dict[str, Any]],
    space_id: str | None = None,
    space_name: str | None = None,
) -> dict[str, Any]:
    from dms_executor.library_tree import build_library_tree

    return build_library_tree(
        sources=sources,
        bronze_tables=bronze,
        warehouse_tables=warehouse,
        space_id=space_id,
        space_name=space_name,
    )


def batch_ingest(files: list[tuple[str, bytes]], *, space_id: str | None = None) -> dict[str, Any]:
    return dms_executor.ingest_batch(files, space_id=space_id).to_dict()


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
