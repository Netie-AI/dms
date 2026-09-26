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


def canonical_space_id(space_id: str) -> str:
    """Memory-store compat ids (``sp_q3_audit``) to the id the registry records."""
    return dms_executor.canonical_space_id(space_id)


#: Named degraded state for a read route that could not read the warehouse (an
#: ingest in another process holds its write lock, or the file is unreadable).
#: The route still answers 200 with what it could read, and says what it could not.
def _warehouse_degraded(exc: Exception) -> dict[str, str]:
    return {
        "code": str(getattr(exc, "code", "warehouse_unavailable")),
        "message": (
            "SQL-source tables could not be read from the warehouse just now "
            "(an ingest may be holding it); counts and lists omit them. Retry shortly."
        ),
        "detail": str(exc)[:200],
    }


def space_source_pulls(
    *, space_id: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """SQL-source tables landed for a Space (ingest registry), truncation included.

    Read-only. ``(pulls, degraded)``: ``degraded`` names why the registry could not
    be read (``warehouse_unavailable``) instead of raising a 5xx on a GET.
    """
    try:
        return dms_executor.list_source_pulls(space_id=space_id), None
    except dms_executor.WarehouseBusy as exc:
        return [], _warehouse_degraded(exc)


def space_source_pull_counts() -> tuple[dict[str, int], dict[str, str] | None]:
    """Landed SQL-source tables per canonical Space id, one read-only registry read."""
    pulls, degraded = space_source_pulls()
    counts: dict[str, int] = {}
    for pull in pulls:
        sid = pull.get("space_id")
        if sid:
            counts[str(sid)] = counts.get(str(sid), 0) + 1
    return counts, degraded


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
        promote_targets=[] if space_id else dms_executor.list_promote_targets(),
    )


def build_validated_envelope(**kwargs: Any) -> dict[str, Any]:
    """Composition-root wrapper so routes never import dms_executor.envelope."""
    env = dms_executor.build_answer_envelope(**kwargs)
    dms_executor.assert_envelope_valid(env)
    return env


def customer_abstain_text(reason: str) -> str:
    """Named ABSTAIN text for ``reason`` (the executor's customer wording)."""
    return dms_executor.customer_abstain_text(reason)


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


def register_verified_query(
    *,
    space_id: str,
    question: str,
    sql: str,
    synonyms: list[str] | None = None,
) -> dict[str, Any]:
    return dms_executor.register_verified_query(
        space_id=space_id,
        question=question,
        sql=sql,
        synonyms=synonyms,
    )


def list_verified_queries(*, space_id: str) -> list[dict[str, Any]]:
    return dms_executor.list_verified_queries(space_id=space_id)


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
    # EPIC-020 clause 2's last two verbs. Before SQLSRC-08 (#156) the manifest
    # the ontology consumes was built, put in this receipt, and dropped, so
    # "measure every link's cardinality against the landed rows, and refuse -
    # naming the link - any link the data violates" ran on no path a customer
    # could reach. It runs here because this is the layer they receive from
    # (R-0001); a refusal derivable only from a bench script is not delivered.
    #
    # It never raises: a link that cannot be measured is reported unverified,
    # the same outcome as one measured and found broken. The rows landed and
    # their provenance is real - only the join is in question.
    links = dms_executor.verify_source_links(extract)
    return {
        "source": extract.source,
        "tables": [
            {
                "bronze_table": p.bronze_table,
                "source": p.source,
                "row_count": p.row_count,
                "truncated": p.truncated,
                # N of M, not a bare boolean: a capped pull is a partial table,
                # and the steward has to be able to see how partial.
                "source_row_count": p.source_row_count,
                "partial": (
                    (
                        f"partial: {p.row_count:,} of "
                        + (
                            f"{p.source_row_count:,}"
                            if p.source_row_count is not None
                            else "an unknown number of"
                        )
                        + f" source rows (row cap {cap:,})"
                    )
                    if p.truncated
                    else None
                ),
                "extracted_at": p.extracted_at,
                # Source types are kept (dms#277). A column that could not be kept
                # exactly landed VARCHAR and is named here, not silently retyped.
                "column_types": dict(p.column_types),
                "type_notes": list(p.type_notes),
            }
            for p in extract.pulls
        ],
        "skipped": list(extract.skipped),
        "truncated_tables": [p.bronze_table for p in extract.pulls if p.truncated],
        "declared_primary_keys": len(extract.keys.primary_keys),
        "declared_foreign_keys": len(extract.keys.foreign_keys),
        "links": links,
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


def _ledger_verify(cortex: CortexClient | None):
    """Read the Cortex chain back — the only verify surface, no local hash chain."""

    def _verify():
        if cortex is None:
            raise RuntimeError("cortex_unavailable")
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

    # Gold must verify at the promote GATE, not only inside sign_gold_metric.
    # Silver does not read the chain.
    receipt = dms_executor.run_promote(
        pipe,
        gold_metric=metric,
        cortex_verify=_ledger_verify(cortex) if pipe.is_gold else None,
    )
    return receipt.to_dict()


def pipeline_latest_receipt(*, target: str) -> dict[str, Any]:
    """Read the latest stored promote receipt. Maps a busy lake to 503, never 500."""
    try:
        return dms_executor.latest_promote_receipt(target)
    except dms_executor.LakeBusy as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "lake_busy", "message": str(exc)},
        ) from None


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
