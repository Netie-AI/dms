"""Composition root — only module allowed to import dms_executor (.importlinter)."""

from __future__ import annotations

from typing import Any

import dms_executor
from cortex_client import CortexClient
from dms_core.ask import AskServicePort
from dms_core.pipelines import GoldMetricDef
from dms_ledger import append_event
from fastapi import HTTPException


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


def bronze_ingest(
    *,
    filename: str,
    data: bytes,
    space_id: str | None = None,
) -> dms_executor.IngestReceipt:
    return dms_executor.ingest_csv_bytes(filename=filename, data=data, space_id=space_id)


def bronze_list(*, space_id: str | None = None):
    return dms_executor.list_bronze_tables(space_id=space_id)


def warehouse_tables(*, space_id: str | None = None):
    return dms_executor.list_warehouse_tables(space_id=space_id)


def reveal_origin_uri(path: str) -> dict[str, Any]:
    """REVEAL-01 — Explorer reveal for an allowlisted filesystem origin_uri."""
    return dms_executor.reveal_path(path)


def search_document_chunks(
    *,
    space_id: str,
    q: str,
    limit: int = 8,
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """RAG-02 — ranked chunk search. Space filter is applied in SQL, not after."""
    from dms_core.control_plane.document_chunks import search_chunks

    from dms_api.settings import get_settings

    settings = get_settings()
    if not settings.database_url:
        return []
    return search_chunks(
        settings.database_url,
        tenant_id=settings.dms_tenant_id,
        space_id=space_id,
        q=q,
        top_k=limit,
        source_ids=source_ids,
    )


def list_document_chunks(*, space_id: str) -> list[dict[str, Any]]:
    """RAG-01 — steward list of one Space's chunks. Never crosses ``space_id``."""
    from dms_core.control_plane.document_chunks import list_chunks

    from dms_api.settings import get_settings

    settings = get_settings()
    if not settings.database_url:
        return []
    return list_chunks(
        settings.database_url,
        tenant_id=settings.dms_tenant_id,
        space_id=space_id,
    )


def warehouse_preview(table: str, *, limit: int = 100, offset: int = 0):
    return dms_executor.preview_warehouse_table(table, limit=limit, offset=offset)


def bronze_preview(table: str, *, limit: int = 100, offset: int = 0):
    return dms_executor.preview_bronze_table(table, limit=limit, offset=offset)


def library_tree(
    *,
    sources: list[dict[str, Any]],
    bronze: list[dict[str, Any]],
    warehouse: list[dict[str, Any]],
    space_id: str | None = None,
    space_name: str | None = None,
) -> dict[str, Any]:
    return dms_executor.build_library_tree(
        sources=sources,
        bronze_tables=bronze,
        warehouse_tables=warehouse,
        space_id=space_id,
        space_name=space_name,
    )


def build_validated_envelope(**kwargs: Any) -> dict[str, Any]:
    """Composition-root wrapper so routes never import dms_executor.envelope."""
    env = dms_executor.build_answer_envelope(**kwargs)
    dms_executor.assert_envelope_valid(env)
    return env


def batch_ingest(files: list[tuple[str, bytes]], *, space_id: str | None = None) -> dict[str, Any]:
    return dms_executor.ingest_batch(files, space_id=space_id).to_dict()


def xlsx_orch_crosscheck(
    pack: dict[str, Any],
    *,
    workbook_path: str = "",
    pack_id: str | None = None,
) -> dict[str, Any]:
    return dms_executor.run_crosscheck(pack, workbook_path=workbook_path, pack_id=pack_id)


def xlsx_orch_extract(
    *,
    pack_id: str,
    space_id: str,
    producer: str,
    result_path: str = "",
    filename: str = "",
    data: bytes | None = None,
) -> dict[str, Any]:
    return dms_executor.run_extract(
        pack_id=pack_id,
        space_id=space_id,
        producer=producer,
        result_path=result_path,
        filename=filename,
        data=data,
    )


def sql_source_describe(
    *,
    kind: str,
    host: str,
    database: str,
    port: int | None = None,
) -> str:
    """Credential-free source label for the gate metadata. Dummy user, empty password."""
    cfg = dms_executor.SourceConfig(
        kind=kind,  # type: ignore[arg-type]
        host=host,
        database=database,
        user="gate",
        password="",
        port=port,
    )
    return cfg.describe()


def sql_source_ingest(
    *,
    kind: str,
    host: str,
    database: str,
    user: str,
    password: str,
    port: int | None = None,
    tables: list[str] | None = None,
    max_rows: int | None = None,
    space_id: str | None = None,
    encrypt: bool = True,
    trust_server_certificate: bool = False,
) -> dict[str, Any]:
    """Pull a SQL source into bronze. Receipt is built field-by-field, never asdict."""
    ceiling = dms_executor.DEFAULT_MAX_ROWS
    cap = ceiling if max_rows is None else min(int(max_rows), ceiling)
    if cap < 1:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": "max_rows must be >= 1"},
        )
    try:
        cfg = dms_executor.SourceConfig(
            kind=kind,  # type: ignore[arg-type]
            host=host,
            database=database,
            user=user,
            password=password,
            port=port,
            encrypt=encrypt,
            trust_server_certificate=trust_server_certificate,
        )
        extract = dms_executor.ingest_source_database(
            cfg, tables=tables, max_rows=cap, space_id=space_id
        )
    except dms_executor.SourceConnectionError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "source_unreachable", "message": str(exc)},
        ) from None
    except dms_executor.UnknownSourceTable as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "source_table_unknown", "message": str(exc)},
        ) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": str(exc)},
        ) from None
    return {
        "source": extract.source,
        "tables": [
            {
                "bronze_table": p.bronze_table,
                "source": p.source,
                "row_count": p.row_count,
                "truncated": p.truncated,
            }
            for p in extract.pulls
        ],
        "skipped": list(extract.skipped),
        "declared_primary_keys": len(extract.keys.primary_keys),
        "declared_foreign_keys": len(extract.keys.foreign_keys),
    }


def xlsx_orch_golden(
    *,
    pack_id: str = "",
    space_id: str = "",
    path: str = "",
    producer: str | None = None,
) -> dict[str, Any]:
    return dms_executor.run_golden(pack_id=pack_id, space_id=space_id, path=path, producer=producer)


#: Fields that constitute an ATTESTATION rather than a definition. A caller may say
#: what a metric is; it may not say that the metric was certified, because saying so
#: is the certification. Accepting these from a request body is how an unsigned metric
#: passed the promote gate without ever touching the ledger (PRD-001 F70, dms#76).
ATTESTATION_FIELDS = ("signature", "signed_at", "ledger_entry_id", "steward_id")


def _ledger_append(cortex: CortexClient):
    def _append(*, event_type: str, payload: dict[str, Any], actor: str | None = None):
        return append_event(cortex, event_type=event_type, payload=payload, actor=actor)

    return _append


def _ledger_verify(cortex: CortexClient):
    """Read the Cortex chain back — the only verify surface, no local hash chain."""

    def _verify():
        return cortex.verify_ledger()

    return _verify


def pipeline_run(
    *,
    actor: str,
    pipeline: str | None = None,
    yaml_text: str | None = None,
    gold_metric: dict[str, Any] | None = None,
    cortex: CortexClient | None = None,
) -> dict[str, Any]:
    """Run a promote pipeline. A gold metric is signed HERE, never accepted as signed.

    ``run_promote`` gates on ``GoldMetricDef.is_signed``, which was
    ``bool(signature and steward_id and signed_at)`` - three plain fields this function
    used to copy straight out of the caller's ``gold_metric`` dict. So a request could
    assert its own certification and the gate believed it, with no ledger entry
    anywhere. Distinct from A-0005: that wrote a false name *into* the chain, this
    bypassed the chain entirely.

    The definition still comes from the caller. The attestation is produced here by an
    actual append *and* a chain readback (``verify_ledger``), and a caller that tries to
    supply one is refused rather than having it silently dropped - a request that
    believes it signed something and was ignored is the same class of lie in the other
    direction (R-0011).
    """
    # Checked before the pipeline is even resolved. Two reasons: a forged attestation
    # is not a request worth doing any work for, and answering 404-not-found ahead of
    # it would tell a caller attempting forgery which pipelines exist.
    if gold_metric:
        offered = [f for f in ATTESTATION_FIELDS if gold_metric.get(f)]
        if offered:
            raise ValueError(
                "a caller may not assert a metric is signed: remove "
                + ", ".join(offered)
                + ". The signature is produced server-side by a ledger append."
            )

    if yaml_text:
        pipe = dms_executor.load_pipeline_yaml(yaml_text)
    elif pipeline:
        pipe = dms_executor.load_pipeline_by_name(pipeline)
    else:
        raise ValueError("pipeline name or yaml_text required")

    metric = None
    if gold_metric:
        if cortex is None:
            raise ValueError(
                "Cortex client required to sign a gold metric onto the ledger; "
                "a gold promote cannot proceed unsigned"
            )
        unsigned = GoldMetricDef(
            metric_id=str(gold_metric["metric_id"]),
            name=str(gold_metric["name"]),
            sql=str(gold_metric["sql"]),
            steward_id=actor,
        )
        metric = dms_executor.sign_gold_metric(
            unsigned,
            cortex_append=_ledger_append(cortex),
            cortex_verify=_ledger_verify(cortex),
            actor=actor,
        )
        if not metric.is_signed:
            raise ValueError("gold metric is not signed after ledger append+verify")

    receipt = dms_executor.run_promote(pipe, gold_metric=metric)
    return receipt.to_dict()


def pipeline_infer_contract(*, source: str) -> dict[str, Any]:
    return dms_executor.infer_contract(source).to_dict()


def gold_sign_metric(
    *,
    metric_id: str,
    name: str,
    sql: str,
    actor: str,
    cortex: CortexClient | None,
) -> dict[str, Any]:
    """Sign a gold metric onto the ledger as ``actor``, which the caller resolves server-side.

    There is deliberately no ``steward_id`` parameter. It used to arrive from the request
    body and become the ledger actor; the steward now *is* the resolved actor, so a caller
    cannot name a third party as the signer of a certified metric.
    """
    if cortex is None:
        raise ValueError("Cortex client required to sign gold metric onto the ledger")

    metric = GoldMetricDef(
        metric_id=metric_id,
        name=name,
        sql=sql,
        steward_id=actor,
    )
    signed = dms_executor.sign_gold_metric(
        metric,
        cortex_append=_ledger_append(cortex),
        cortex_verify=_ledger_verify(cortex),
        actor=actor,
    )
    if not signed.is_signed:
        raise ValueError("gold metric is not signed after ledger append+verify")
    return signed.to_dict()
