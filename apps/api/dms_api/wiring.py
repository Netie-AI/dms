"""Composition root — only module allowed to import dms_executor (.importlinter)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import dms_executor
from cortex_client import CortexClient
from dms_core.ask import AskServicePort
from dms_core.pipelines import GoldMetricDef
from dms_ledger import append_event
from fastapi import HTTPException

logger = logging.getLogger(__name__)


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


def sql_tables_read(sql: str | None) -> tuple[str, ...]:
    """BANK-02 - tables an executed statement reads, via the executor's one parser."""
    return dms_executor.tables_read_by_sql(sql)


def sql_tables_read_checked(sql: str | None) -> tuple[tuple[str, ...], bool]:
    """BANK-02 - ``(tables, approximate)``. Never parses an unbounded statement."""
    return dms_executor.tables_read_checked_by_sql(sql)


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


def scrub_ask_envelope(env: dict[str, Any]) -> dict[str, Any]:
    """Scrub an ask envelope built outside ``live_ask``. Opens no connection.

    Routes stay off ``dms_executor``. No turn resolved a grant set for
    these, so nothing is granted. Loop SQL and ``raw_reply`` are not
    rewritten.
    """
    if not isinstance(env, dict):
        return env
    return dms_executor.hide_echo(env, "")


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
        dialect=dms_executor.SERVING_DIALECT,
    )


def list_verified_queries(*, space_id: str) -> list[dict[str, Any]]:
    return dms_executor.list_verified_queries(space_id=space_id)


def preview_adjusted_sql(*, space_id: str, sql: str) -> dict[str, Any]:
    return dms_executor.preview_adjusted_sql(
        space_id=space_id,
        sql=sql,
        dialect=dms_executor.SERVING_DIALECT,
    )


def save_steward_adjusted(
    *,
    space_id: str,
    question: str,
    sql: str,
    actor: str,
) -> dict[str, Any] | None:
    return dms_executor.save_steward_adjusted(
        space_id=space_id,
        question=question,
        sql=sql,
        actor=actor,
        dialect=dms_executor.SERVING_DIALECT,
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


def service_bearer_id(authorization: str | None) -> str | None:
    """Id of the configured OpenVault service bearer, or None.

    Accepts ``Authorization: Bearer <token>`` only. A missing header is None
    (the SQL route keeps its old behaviour). A present header that does not
    match is also None; the route turns that into 401 when the header was sent.
    """
    raw = (authorization or "").strip()
    if not raw.lower().startswith("bearer "):
        return None
    presented = raw[7:].strip()
    if not presented:
        return None
    return dms_executor.match_service_bearer(presented)


def _space_payload(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "source_count": record.source_count,
        "member_count": record.member_count,
    }


def bind_connected_space(
    *,
    store: Any,
    space_id: str | None,
    space_name: str | None,
    connector_id: str,
    exposed: list[str],
    grant_tables: list[str] | None,
    actor: str,
    token_id: str,
) -> dict[str, Any]:
    """Register a Space and grant only names the connector already listed.

    ``grant_tables is None`` grants the whole listing. A name that is not in
    the listing is refused. The book is in-memory (Postgres grants stay parked).
    """
    from uuid import uuid4

    from dms_core.control_plane.connect_grants import (
        clean_tables,
        register_source,
    )
    from dms_core.control_plane.connect_grants import grant_tables as grant_book

    listed = clean_tables(exposed)
    if grant_tables is None:
        chosen = listed
    else:
        chosen = clean_tables(grant_tables)
        if any(table not in listed for table in chosen):
            raise ValueError("table_not_exposed")
    if space_id:
        record = store.get(space_id)
        if record is None:
            raise ValueError("space_not_found")
        bound_id = space_id
    else:
        record = store.create((space_name or "").strip() or f"connected-{uuid4().hex[:8]}")
        bound_id = str(record.id)
    source_id = str(uuid4())
    register_source(
        source_id=source_id,
        space_id=bound_id,
        connector_id=connector_id,
        exposed=list(listed),
        actor=actor,
        token_id=token_id,
    )
    granted: tuple[str, ...] = ()
    if chosen:
        granted = grant_book(
            source_id=source_id,
            tables=list(chosen),
            actor=actor,
            token_id=token_id,
        )
    return {
        "space": _space_payload(record),
        "source_id": source_id,
        "connector_id": connector_id,
        "exposed_tables": list(listed),
        "granted_tables": list(granted),
    }


def _resolve_connect_secret(ask: Any, credential_ref: str) -> str:
    """Call the OpenVault client already on the ask service. No logging."""
    from dms_core.control_plane.connect_secrets import ConnectCredentialError

    minter = getattr(ask, "_minter", None)
    resolve = getattr(minter, "resolve_credential", None)
    if not callable(resolve):
        raise ConnectCredentialError()
    try:
        secret = resolve(credential_ref)
    except ConnectCredentialError:
        raise
    except Exception:
        raise ConnectCredentialError() from None
    if not isinstance(secret, str) or secret == "":
        raise ConnectCredentialError()
    return secret


def connect_registered_source(
    *,
    store: Any,
    ask: Any,
    connector_id: str,
    space_name: str,
    grant_tables: list[str] | None,
    credential_ref: str,
    actor: str,
    token_id: str,
) -> dict[str, Any]:
    """Resolve the OpenVault reference, read the listing, grant through the book.

    The resolved secret is an argument to the connector and is then dropped.
    It is not written to the grant book or the audit row.
    """
    secret = _resolve_connect_secret(ask, credential_ref)
    try:
        try:
            exposed = dms_executor.connector_tables(connector_id, secret)
        except KeyError:
            raise ValueError("connector_unknown") from None
    finally:
        secret = ""
    return bind_connected_space(
        store=store,
        space_id=None,
        space_name=space_name,
        connector_id=connector_id,
        exposed=exposed,
        grant_tables=grant_tables,
        actor=actor,
        token_id=token_id,
    )


def grant_connected_tables(
    *,
    source_id: str,
    tables: list[str],
    actor: str,
    token_id: str,
) -> dict[str, Any]:
    from dms_core.control_plane.connect_grants import grant_tables as grant_book
    from dms_core.control_plane.connect_grants import source_for

    src = source_for(source_id)
    if src is None:
        raise KeyError(source_id)
    granted = grant_book(
        source_id=source_id, tables=tables, actor=actor, token_id=token_id
    )
    return {
        "source_id": source_id,
        "space_id": src.space_id,
        "granted_tables": list(granted),
    }


def revoke_connected_tables(
    *,
    source_id: str,
    tables: list[str],
    actor: str,
    token_id: str,
) -> dict[str, Any]:
    from dms_core.control_plane.connect_grants import revoke_tables, source_for

    src = source_for(source_id)
    if src is None:
        raise KeyError(source_id)
    revoked = revoke_tables(
        source_id=source_id, tables=tables, actor=actor, token_id=token_id
    )
    return {
        "source_id": source_id,
        "space_id": src.space_id,
        "revoked_tables": list(revoked),
    }


def connect_audit_rows() -> list[dict[str, Any]]:
    from dms_core.control_plane.connect_grants import audit_rows

    return audit_rows()


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
    # F-b: SQL-source bronze reaches Cortex's serving warehouse the same way a
    # Studio upload does; before this only batch ingest synced it.
    sync_state, sync_detail = (
        dms_executor.serving_sync_state() if extract.pulls else ("not_attempted", "nothing landed")
    )
    ontology = _derive_space_ontology(
        extract.manifest_entry, kind=kind, host=host, database=database, space_id=space_id
    )
    receipt = {
        "source": extract.source,
        "tables": [
            {
                "bronze_table": p.bronze_table,
                "source": p.source,
                "row_count": p.row_count,
                "truncated": p.truncated,
                "extracted_at": p.extracted_at,
                # F-e: landed types from the source's declared types; any column
                # left VARCHAR is named with the reason.
                "column_types": dict(p.column_types),
                "untyped_columns": dict(p.untyped_columns),
                "note": p.note,
                **(
                    {
                        "source_name": p.source_name,
                        "column_sources": dict(p.column_sources),
                    }
                    if p.column_sources
                    else {}
                ),
            }
            for p in extract.pulls
        ],
        "serving_sync": {"state": sync_state, "detail": sync_detail},
        "skipped": list(extract.skipped),
        "declared_primary_keys": len(extract.keys.primary_keys),
        "declared_foreign_keys": len(extract.keys.foreign_keys),
        "links": links,
        "ontology": ontology,
    }
    bound = _bind_sql_source(
        extract, kind=kind, host=host, database=database, port=port, space_id=space_id
    )
    if bound is not None:
        receipt["source_id"] = bound["source_id"]
        receipt["exposed_tables"] = bound["exposed_tables"]
        receipt["granted_tables"] = bound["granted_tables"]
    return receipt


def _bind_sql_source(
    extract: Any,
    *,
    kind: str,
    host: str,
    database: str,
    port: int | None,
    space_id: str | None,
) -> dict[str, Any] | None:
    """Register a landed SQL source on its Space. Tables stay proposed, not granted.

    Off unless ``DMS_SQL_SOURCE_BIND`` is on. No password is stored. The row
    matches ``register_source`` (source, space, connector, exposed). Postgres
    ``dms.data_sources`` gets the same facts when ``DATABASE_URL`` is set.
    """
    from uuid import uuid4

    from dms_core.control_plane.connect_grants import (
        register_source,
        sql_source_bind_enabled,
    )

    if not sql_source_bind_enabled() or not space_id or not extract.pulls:
        return None
    sid = dms_executor.canonical_space_id(space_id)
    names = [p.bronze_table for p in extract.pulls if p.bronze_table]
    if not names:
        return None
    label = sql_source_describe(kind=kind, host=host, database=database, port=port)
    source_id = str(uuid4())
    actor = os.environ.get("DMS_ACTOR_USER_ID", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    try:
        register_source(
            source_id=source_id,
            space_id=sid,
            connector_id=label,
            exposed=names,
            actor=actor,
            token_id="studio",
        )
    except ValueError:
        logger.warning("sql source bind skipped: source_exists")
        return None
    _persist_sql_data_source(
        source_id=source_id,
        space_id=sid,
        ref=label,
        exposed=names,
    )
    return {"source_id": source_id, "exposed_tables": names, "granted_tables": []}


def _persist_sql_data_source(
    *,
    source_id: str,
    space_id: str,
    ref: str,
    exposed: list[str],
) -> None:
    """Insert ``dms.data_sources`` when Postgres is configured. Never stores a secret."""
    import json
    from uuid import UUID

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return
    tenant = os.environ.get("DMS_TENANT_ID", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    try:
        UUID(source_id)
        UUID(space_id)
        UUID(tenant)
    except ValueError:
        return
    meta = json.dumps(
        {"connector_id": ref, "exposed_tables": exposed, "granted": False}
    )
    try:
        import psycopg
        from dms_core.control_plane.session import set_tenant_context

        with psycopg.connect(url) as conn:
            set_tenant_context(conn, tenant, role="steward")
            conn.execute(
                """
                INSERT INTO dms.data_sources
                  (id, tenant_id, space_id, kind, ref, scope, meta)
                VALUES (%s, %s, %s, 'sql', %s, 'team', %s::jsonb)
                """,
                (source_id, tenant, space_id, ref, meta),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - the land stands; the receipt names the miss
        logger.warning("sql source data_sources insert failed: %s", type(exc).__name__)


def _derive_space_ontology(
    entry: dict[str, Any],
    *,
    kind: str,
    host: str,
    database: str,
    space_id: str | None,
) -> dict[str, Any]:
    """ONTO-DERIVE-01: derive, verify, store the Space's ontology from this pull.

    Never fails the ingest (the rows landed and their provenance is real), and
    never hides a failure: the receipt says ``derived: false`` and why.
    """
    if not space_id:
        return {"derived": False, "reason": "no space_id: an ontology belongs to a Space"}
    try:
        catalog = dms_executor.bronze_catalog(entry)
        return dms_executor.derive_and_store(
            catalog,
            space_id=dms_executor.canonical_space_id(space_id),
            identity=dms_executor.source_identity(kind, host, database, catalog),
            warehouse=dms_executor.warehouse_path(),
        )
    except Exception as exc:  # noqa: BLE001 - named on the receipt, never raised
        logger.warning("ontology derive failed for %s: %s", space_id, exc)
        return {"derived": False, "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}


def space_ontology(space_id: str) -> dict[str, Any]:
    """GET view: every source's ontology for the Space, status per object and link."""
    try:
        views = dms_executor.space_ontology_views(dms_executor.canonical_space_id(space_id))
    except Exception as exc:  # noqa: BLE001 - named, never a bare 500
        raise _store_unavailable(exc) from None
    return {
        "space_id": space_id,
        "derived": bool(views),
        "ontologies": views,
        **(
            {}
            if views
            else {
                "reason": "no ontology has been derived for this Space; ingest a SQL "
                "source into it or POST .../ontology/derive"
            }
        ),
    }


def space_ontology_rederive(
    space_id: str,
    *,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-derive for an existing Space. Never pulls rows.

    With ``source`` (a SQL connection): read the source catalog (keys only) and
    match it to the tables already landed for this Space from that source.
    Without: re-measure every stored catalog against the bronze rows as they
    are now. A Space with neither is a named 409.
    """
    canon = dms_executor.canonical_space_id(space_id)
    warehouse = dms_executor.warehouse_path()
    try:
        return _rederive(space_id, canon, warehouse, source)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - the store, never a bare 500
        raise _store_unavailable(exc) from None


def _store_unavailable(exc: Exception) -> HTTPException:
    logger.warning("ontology store unavailable: %s", exc)
    return HTTPException(
        status_code=503,
        detail={
            "code": "ontology_store_unavailable",
            "message": "the Space ontology store could not be read or written just now",
        },
    )


def _rederive(
    space_id: str, canon: str, warehouse: Any, source: dict[str, Any] | None
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    if source is not None:
        try:
            cfg = dms_executor.SourceConfig(**source)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail={"code": "bad_request", "message": str(exc)}
            ) from None
        try:
            keys = dms_executor.list_source_keys(cfg)
        except Exception as exc:  # noqa: BLE001 - any driver failure is the source's
            message = (
                str(exc)
                if isinstance(exc, dms_executor.SourceConnectionError)
                else f"could not read keys from {cfg.describe()} ({type(exc).__name__})"
            )
            raise HTTPException(
                status_code=502, detail={"code": "source_unreachable", "message": message}
            ) from None
        prefix = cfg.describe() + "#"
        tables = []
        for pull in dms_executor.list_source_pulls(space_id=canon):
            ref = str(pull.get("ref") or "")
            if not ref.startswith(prefix):
                continue
            schema, _, name = ref[len(prefix):].partition(".")
            tables.append(
                {
                    "schema": schema,
                    "table": name,
                    "path": pull["bronze_table"],
                    "truncated": bool(pull.get("truncated")),
                }
            )
        if not tables:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_landed_tables",
                    "message": f"nothing from {cfg.describe()} has been ingested into this Space",
                },
            )
        catalog = dms_executor.bronze_catalog(
            {
                "source": cfg.describe(),
                "tables": tables,
                "primary_keys": {k: list(v) for k, v in keys.primary_keys.items()},
                "foreign_keys": [
                    {
                        "name": fk.name,
                        "from_table": fk.from_table,
                        "from_column": fk.from_column,
                        "to_table": fk.to_table,
                        "to_column": fk.to_column,
                    }
                    for fk in sorted(keys.foreign_keys, key=lambda f: (f.name, f.ordinal))
                ],
            }
        )
        results.append(
            dms_executor.derive_and_store(
                catalog,
                space_id=canon,
                identity=dms_executor.source_identity(
                    cfg.kind, cfg.host, cfg.database, catalog
                ),
                warehouse=warehouse,
            )
        )
    else:
        for identity, catalog in dms_executor.stored_catalogs_by_source(canon):
            results.append(
                dms_executor.derive_and_store(
                    catalog, space_id=canon, identity=identity, warehouse=warehouse
                )
            )
        if not results:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_catalog",
                    "message": "this Space has no stored source catalog; send the source "
                    "connection to read its keys",
                },
            )
    return {"space_id": space_id, "rederived": len(results), "ontologies": results}


def bind_ontology_store(database_url: str, tenant_id: str) -> None:
    """Durable ontology store for a Postgres-bound API (same tenant as Spaces)."""
    from dms_core.control_plane.onto_store import PostgresOntologyStore

    dms_executor.set_ontology_store(PostgresOntologyStore(database_url, tenant_id=tenant_id))


class _OntologyUnread(Exception):
    """The Space ontology store raised. The page must not invent a spine."""


_ONTOLOGY_REASONS = {
    "space_required": "Select a Space. Ontology is not read from a default Space.",
    "empty_store": "This Space has no approved ontology in the store.",
    "store_unreadable": "The ontology store could not be read.",
}


def read_live_ontology(section: str, *, space_id: str | None) -> dict[str, Any]:
    """The ontology page body for one Space.

    ``space_ontology`` views plus approved onto_data items for ``space_id``.
    No Space id, an empty store, or an unreadable store is ``ok: false`` with
    ``reason`` set. Never the seeded Finance id, and never ``ontology_spine.yaml``.
    No Cortex call.
    """
    resolved = (space_id or "").strip()
    if not resolved:
        return _honest_ontology(section, "", "space_required")
    try:
        views = _space_views(resolved)
        approved = _approved_for_space(resolved)
    except _OntologyUnread:
        return _honest_ontology(section, resolved, "store_unreadable")
    if not views and not approved:
        return _honest_ontology(section, resolved, "empty_store")
    return _ontology_body(
        section, resolved, _payload_from_store(views, approved), ok=True, source="store", reason=""
    )


def _honest_ontology(section: str, space_id: str, reason: str) -> dict[str, Any]:
    return _ontology_body(
        section,
        space_id,
        {
            "object_types": [],
            "link_types": [],
            "action_types": [],
            "functions": [],
            "metrics": [],
        },
        ok=False,
        source="none",
        reason=reason,
    )


def _ontology_body(
    section: str,
    space_id: str,
    payload: dict[str, Any],
    *,
    ok: bool,
    source: str,
    reason: str,
) -> dict[str, Any]:
    nodes, edges = _graph(payload)
    return {
        "ok": ok,
        "section": section or "summary",
        "space_id": space_id,
        "source": source,
        "reason": reason,
        "hint": _ONTOLOGY_REASONS.get(reason, ""),
        "counts": _ontology_counts(payload),
        "object_types": payload["object_types"],
        "link_types": payload["link_types"],
        "action_types": payload["action_types"],
        "functions": payload["functions"],
        "metrics": payload["metrics"],
        "nodes": nodes,
        "edges": edges,
    }


def _space_views(space_id: str) -> list[dict[str, Any]]:
    try:
        views = dms_executor.space_ontology_views(space_id)
    except Exception as exc:  # noqa: BLE001 - a down store is a named reason, not a 500
        logger.warning("space ontology unreadable (%s)", type(exc).__name__)
        raise _OntologyUnread from exc
    return [view for view in views if isinstance(view, dict)]


def _approved_for_space(space_id: str) -> list[dict[str, Any]]:
    """Approved onto_data items whose version belongs to this Space.

    ``DMS_ONTO_SPACE_SCOPE`` on uses ``approved_items(space_id)``, which also
    keeps a steward-marked tenant-wide version. Off, this stays the Space
    match it had before that flag.
    """
    from dms_core.control_plane.onto_data import (
        _public_item,
        onto_space_scope_enabled,
        ontology_data_store,
    )

    store = ontology_data_store()
    if onto_space_scope_enabled():
        try:
            return list(store.approved_items(space_id))
        except Exception as exc:  # noqa: BLE001 - a down store is a named reason, not a 500
            logger.warning("approved ontology items unreadable (%s)", type(exc).__name__)
            raise _OntologyUnread from exc
    versions = getattr(store, "_versions", None)
    if isinstance(versions, dict):
        try:
            items = store.approved_items()
        except Exception as exc:  # noqa: BLE001 - a down store is a named reason, not a 500
            logger.warning("approved ontology items unreadable (%s)", type(exc).__name__)
            raise _OntologyUnread from exc
        out: list[dict[str, Any]] = []
        for item in items:
            version = versions.get(item.get("version_id"))
            if version is not None and str(getattr(version, "space_id", "") or "") == space_id:
                out.append(item)
        return out
    connect = getattr(store, "_connect", None)
    tenant_of = getattr(store, "_tenant", None)
    if not callable(connect) or not callable(tenant_of):
        return []
    try:
        from dms_core.control_plane.onto_data import _item_from_db
        from dms_core.control_plane.session import set_tenant_context

        conn = connect()
        try:
            set_tenant_context(conn, tenant_of(None), role="steward")
            rows = conn.execute(
                """
                SELECT i.id::text, i.tenant_id::text, i.version_id::text, i.kind,
                       i.payload, i.status, i.source, i.provenance, i.created_by::text,
                       i.approved_by, i.approval_ref, i.ts
                  FROM dms.ontology_items i
                  JOIN dms.ontology_versions v ON v.id = i.version_id
                 WHERE i.status = 'approved' AND v.space_id = %s::uuid
                 ORDER BY i.ts, i.id
                """,
                (space_id,),
            ).fetchall()
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - missing table or a non-uuid id
        logger.warning("approved ontology items unreadable (%s)", type(exc).__name__)
        raise _OntologyUnread from exc
    return [_public_item(_item_from_db(row)) for row in rows]


def _payload_from_store(
    views: list[dict[str, Any]], approved: list[dict[str, Any]]
) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    seen_objects: set[str] = set()
    seen_links: set[str] = set()
    seen_metrics: set[str] = set()

    def add_object(row: dict[str, Any] | None) -> None:
        if row is None or row["id"] in seen_objects:
            return
        seen_objects.add(row["id"])
        objects.append(row)

    def add_link(row: dict[str, Any] | None) -> None:
        if row is None or row["id"] in seen_links:
            return
        seen_links.add(row["id"])
        links.append(row)

    def add_metric(row: dict[str, Any] | None) -> None:
        if row is None or row["id"] in seen_metrics:
            return
        seen_metrics.add(row["id"])
        metrics.append(row)

    for view in views:
        for obj in view.get("objects") or []:
            if isinstance(obj, dict):
                add_object(_object_row(obj))
        for link in view.get("links") or []:
            if isinstance(link, dict):
                add_link(_link_row(link))
    for item in approved:
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = item.get("kind")
        if kind == "object":
            add_object(_object_row(payload))
        elif kind == "link":
            add_link(_link_row(payload))
        elif kind == "measure":
            add_metric(_metric_row(payload))
    return {
        "object_types": objects,
        "link_types": links,
        "action_types": [],
        "functions": [],
        "metrics": metrics,
    }


def _object_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(raw.get("name") or raw.get("id") or "").strip()
    if not name:
        return None
    properties: list[dict[str, Any]] = []
    for attr in raw.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        prop = ""
        for key in ("name", "display_name", "source_column"):
            value = attr.get(key)
            if isinstance(value, str) and value.strip():
                prop = value.strip()
                break
        if not prop:
            continue
        properties.append(
            {"name": prop, "type": str(attr.get("type") or ""), "agent_visible": True}
        )
    return {
        "id": name,
        "description": str(raw.get("description") or ""),
        "primary_key": _key_text(raw.get("key")),
        "properties": properties,
        "property_count": len(properties),
        "sensitive_count": 0,
        "metric_ids": [],
    }


def _link_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    ident = str(raw.get("name") or raw.get("id") or "").strip()
    src = str(raw.get("from") or raw.get("from_object") or "").strip()
    dst = str(raw.get("to") or raw.get("to_object") or "").strip()
    if not ident or not src or not dst:
        return None
    cardinality = raw.get("cardinality")
    return {
        "id": ident,
        "from_object": src,
        "from_property": _first_text(raw.get("from_columns")),
        "to_object": dst,
        "to_property": _first_text(raw.get("to_columns")),
        "cardinality": "" if cardinality is None else str(cardinality),
    }


def _metric_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    ident = str(raw.get("name") or raw.get("id") or "").strip()
    if not ident:
        return None
    named = raw.get("object_types")
    if named is None:
        named = raw.get("objects") or []
    if isinstance(named, str):
        objects = [named]
    elif isinstance(named, list):
        objects = [str(item) for item in named if isinstance(item, str) and item]
    else:
        objects = []
    synonyms = raw.get("synonyms") or []
    return {
        "id": ident,
        "kind": "measure",
        "synonyms": [str(item) for item in synonyms if isinstance(item, str)],
        "result_columns": [],
        "params": [],
        "object_types": objects,
        "sql": "",
    }


def _key_text(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value if str(item).strip())
    if isinstance(value, str):
        return value
    return ""


def _first_text(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return ""


def _ontology_counts(payload: dict[str, Any]) -> dict[str, int]:
    objects = payload["object_types"]
    actions = payload["action_types"]
    tools = sum(1 for row in actions if row.get("kind") == "tool")
    return {
        "object_types": len(objects),
        "properties": sum(int(row["property_count"]) for row in objects),
        "sensitive_properties": sum(int(row["sensitive_count"]) for row in objects),
        "link_types": len(payload["link_types"]),
        "action_types": len(actions),
        "action_tools": tools,
        "action_events": len(actions) - tools,
        "functions": len(payload["functions"]),
        "metrics": len(payload["metrics"]),
    }


def _graph(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    degree = {row["id"]: 0 for row in payload["object_types"]}
    metric_count: dict[str, int] = {}
    for metric in payload["metrics"]:
        for obj in metric.get("object_types") or []:
            metric_count[obj] = metric_count.get(obj, 0) + 1
    edges: list[dict[str, Any]] = []
    for link in payload["link_types"]:
        edges.append(
            {
                "id": link["id"],
                "source": link["from_object"],
                "target": link["to_object"],
                "source_property": link["from_property"],
                "target_property": link["to_property"],
                "cardinality": link["cardinality"],
            }
        )
        for end in (link["from_object"], link["to_object"]):
            if end in degree:
                degree[end] += 1
    nodes = [
        {
            "id": row["id"],
            "label": row["id"],
            "description": row["description"],
            "primary_key": row["primary_key"],
            "property_count": row["property_count"],
            "sensitive_count": row["sensitive_count"],
            "metric_count": metric_count.get(row["id"], 0),
            "degree": degree.get(row["id"], 0),
        }
        for row in payload["object_types"]
    ]
    return nodes, edges


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


def health_pack_fields() -> dict[str, str]:
    """Pack identity for /health.

    ``curated_ceo`` when both files load. ``absent`` when a file is not on disk.
    ``unreadable`` when a file is on disk but cannot be used, with ``pack_error``
    set to the exception class. The class only: a parser message quotes the file.
    """
    try:
        status = dms_executor.curated_pack_status()
    except Exception:  # noqa: BLE001 - health must stay up
        return {"pack": "absent"}
    if status.name == "unreadable" and status.error_class:
        return {"pack": "unreadable", "pack_error": status.error_class}
    return {"pack": status.name}


def health_pack_name() -> str:
    """``curated_ceo``, ``absent``, or ``unreadable``. See ``health_pack_fields``."""
    return health_pack_fields()["pack"]


def _compile_deadline() -> float | None:
    """Monotonic deadline from ``DMS_AO_COMPILE_DEADLINE_S``. Unset means no cap."""
    raw = os.environ.get("DMS_AO_COMPILE_DEADLINE_S", "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds <= 0:
        return time.monotonic()
    return time.monotonic() + seconds


def compile_ontology_proposal(
    *,
    actor: str,
    tenant_id: str,
    space_id: str | None = None,
    tenant_wide: bool = False,
) -> dict[str, Any]:
    """Run AO-VENDOR-01 ``compile_source`` and store the proposal as proposed.

    Flag off returns status ``off`` and writes nothing. A proposal is never
    stored with ``served`` true. ``DMS_AO_COMPILE_RESILIENT`` off keeps that
    call. On, only the Space's granted tables are compiled, a gap is stored
    with whatever succeeded, and a failure is 200 or a typed 4xx/503.
    """
    import duckdb
    from dms_core.control_plane.onto_data import store_proposal

    resilient = dms_executor.ao_compile_resilient()
    path = dms_executor.warehouse_path()
    conn = dms_executor.connect_serving(path) if path.is_file() else duckdb.connect()
    try:
        if not resilient:
            proposal = dms_executor.compile_source(conn)
        else:
            space = (space_id or "").strip()
            if not space:
                raise HTTPException(status_code=400, detail="space_id_required")
            proposal = dms_executor.compile_source(
                conn,
                tables=dms_executor.space_compile_tables(space),
                deadline=_compile_deadline(),
            )
    except HTTPException:
        raise
    except Exception as exc:
        if not resilient:
            raise
        code = getattr(exc, "code", None)
        if type(exc).__name__ == "AoCompileRefused" and isinstance(code, str):
            raise HTTPException(status_code=503, detail=code) from None
        logger.error("ontology compile failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="ao_compile_failed") from None
    finally:
        conn.close()
    if proposal.status == "refused":
        return {
            "status": "refused",
            "served": False,
            "version_id": None,
            "items": [],
            "gaps": [dict(row) for row in proposal.gaps],
        }
    return store_proposal(
        proposal.to_json(),
        created_by=actor,
        space_id=space_id,
        tenant_id=tenant_id,
        tenant_wide=tenant_wide,
    )


def engine_clock_fields() -> dict[str, str]:
    """Best-effort engine date for /health. Omitted when the warehouse is down."""
    try:
        return dms_executor.read_health_engine_clock()
    except Exception:  # noqa: BLE001 - health must stay up
        return {}


def resolve_action_key(action_type: str, key: dict[str, str]) -> dict[str, Any]:
    """ACT-02: does a governed action's object key exist in its source, as encoded?

    Hard rule 12. Delegates to ``dms_executor.action_keys``; identifiers come from
    the code registry only. See that module for the result shapes.
    """
    from dms_core.control_plane.action_types import ACTION_TYPES

    at = ACTION_TYPES[action_type]
    return dms_executor.action_keys.resolve_key(
        at.source_kind, at.source_table, at.key_fields, at.field, key
    )


def prove_insights(
    ask: Any,
    cortex: CortexClient,
    *,
    space_id: str | None,
    session_id: str,
    top: int,
    actor: str | None = None,
) -> Any:
    """INS-PROOF-02 - proven insights for a Space, one Cortex ledger pointer each.

    ``ask`` is the Executor; every statement goes through its Cortex submit, and
    the pointer is appended through ``_ledger_append`` (dms_ledger -> Cortex
    ``/v1/contract/ledger/append``). No local chain. ``actor`` is
    settings.dms_actor_user_id (DR-0004 Option A), the identity the gate names.
    """
    return dms_executor.prove_for_space(
        ask, space_id=space_id, session_id=session_id, top=top,
        ledger_append=_ledger_append(cortex), actor=actor,
    )


def demote_insight(insight: Any, code: str, *, orphan_ledger_entry: str | None = None) -> Any:
    """INS-PROOF-02 - a proven insight that may not be served, as an ABSTAIN refusal."""
    return dms_executor.demote_insight(insight, code, orphan_ledger_entry=orphan_ledger_entry)


# INS-PROOF-03 - steward pins. Pass-throughs: the route never imports the executor.
# ``actor`` is settings.dms_actor_user_id (DR-0004 Option A, the deployment actor).


def preview_pin(ask: Any, *, space_id: str | None, insight_id: str) -> dict[str, Any]:
    return dms_executor.preview_pin(ask, space_id=space_id, insight_id=insight_id)


def confirm_pin(ask: Any, cortex: CortexClient, *, token: str, actor: str) -> dict[str, Any]:
    return dms_executor.confirm_pin(
        ask, token=token, actor=actor, ledger_append=_ledger_append(cortex)
    )


def replay_pin(ask: Any, cortex: CortexClient, asset_id: str, *, actor: str) -> dict[str, Any]:
    return dms_executor.replay_pin(
        ask, asset_id, actor=actor, ledger_append=_ledger_append(cortex)
    )


def demote_replay(
    res: dict[str, Any], code: str, *, orphan_ledger_entry: str | None = None
) -> dict[str, Any]:
    """A served replay whose audit row did not land, as an ABSTAIN (INS-PROOF-02 rule g)."""
    return dms_executor.demote_replay(res, code, orphan_ledger_entry=orphan_ledger_entry)


def revoke_pin(ask: Any, cortex: CortexClient, asset_id: str, *, actor: str) -> dict[str, Any]:
    return dms_executor.revoke_pin(
        ask, asset_id, actor=actor, ledger_append=_ledger_append(cortex)
    )


def list_pinned(ask: Any, *, space_id: str | None) -> list[dict[str, Any]]:
    return dms_executor.list_pinned(ask, space_id=space_id)


def pin_owner_space(ask: Any, *, token: str | None = None, asset_id: str | None = None) -> Any:
    """The Space a token or pin belongs to, for the F5 call's metadata. A read only."""
    if token is not None:
        return dms_executor.token_space(token)
    return dms_executor.pin_space(ask, asset_id or "")


def _connect_v2():
    return dms_executor.connectors


def _note_probe(
    code: str,
    *,
    actor: str,
    token_id: str,
    database_url: str | None,
    tenant_id: str,
) -> None:
    from dms_core.control_plane.connect_store import ConnectStore, note_probe

    note_probe(code, actor=actor, token_id=token_id)
    if not database_url:
        return
    try:
        ConnectStore(database_url, tenant_id).note_durable(
            code, actor=actor, token_id=token_id, source_id=None
        )
    except Exception:
        return


def _group_tables(names: list[str], granted: set[str]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for qualified in names:
        schema, dot, table = qualified.partition(".")
        if not dot:
            schema, table = "main", qualified
        buckets.setdefault(schema, []).append(
            {"name": table, "granted": qualified in granted}
        )
    return [{"name": schema, "tables": tables} for schema, tables in buckets.items()]


def _rehydrate(rows: list[dict[str, Any]], *, actor: str, token_id: str) -> None:
    from dms_core.control_plane.connect_grants import grant_tables as grant_book
    from dms_core.control_plane.connect_grants import register_source, source_for

    for row in rows:
        if source_for(row["source_id"]) is not None:
            continue
        try:
            register_source(
                source_id=row["source_id"],
                space_id=row["space_id"],
                connector_id=row["connector_id"],
                exposed=list(row["exposed_tables"]),
                actor=actor,
                token_id=token_id,
            )
            if row["granted_tables"]:
                grant_book(
                    source_id=row["source_id"],
                    tables=list(row["granted_tables"]),
                    actor=actor,
                    token_id=token_id,
                )
        except (KeyError, ValueError):
            continue


def connect_v2_probe(
    ask: Any,
    *,
    kind: str,
    host: str,
    port: int,
    database: str,
    cred_ref: str,
    tls: bool,
    actor: str,
    token_id: str,
    database_url: str | None,
    tenant_id: str,
) -> dict[str, Any]:
    """Probe a source. The returned dict has no credential and no row values."""
    secret = _resolve_connect_secret(ask, cred_ref)
    user = ""
    password = ""
    code = "network"
    try:
        user, password = _connect_v2().parse_login(secret)
        probed = _connect_v2().probe_source(
            kind=kind,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            tls=tls,
        )
    except _connect_v2().ConnectProbeError as exc:
        code = exc.code
        _note_probe(
            code, actor=actor, token_id=token_id, database_url=database_url, tenant_id=tenant_id
        )
        return {"ok": False, "error": code, "latency_ms": 0}
    except Exception:
        _note_probe(
            code, actor=actor, token_id=token_id, database_url=database_url, tenant_id=tenant_id
        )
        return {"ok": False, "error": code, "latency_ms": 0}
    finally:
        secret = ""
        user = ""
        password = ""
    _note_probe(
        "probe_ok", actor=actor, token_id=token_id, database_url=database_url, tenant_id=tenant_id
    )
    return {
        "ok": True,
        "latency_ms": int(probed.get("latency_ms") or 0),
        "server_version": str(probed.get("server_version") or ""),
        "tables_visible": int(probed.get("tables_visible") or 0),
    }


def connect_v2_register(
    *,
    store: Any,
    ask: Any,
    database_url: str | None,
    tenant_id: str,
    connector_id: str,
    space_name: str,
    credential_ref: str,
    kind: str,
    host: str,
    port: int,
    database: str,
    mode: str,
    grant_tables: list[str] | None,
    tls: bool,
    actor: str,
    token_id: str,
) -> dict[str, Any]:
    """Introspect, persist, grant every listed table (or the steward's subset)."""
    from uuid import uuid4

    from dms_core.control_plane.connect_grants import clean_tables, register_source
    from dms_core.control_plane.connect_grants import grant_tables as grant_book
    from dms_core.control_plane.connect_store import ConnectStore, ConnectStoreError

    if not database_url:
        raise ConnectStoreError("connect_store_unavailable")
    secret = _resolve_connect_secret(ask, credential_ref)
    user = ""
    password = ""
    try:
        user, password = _connect_v2().parse_login(secret)
        probed = _connect_v2().probe_source(
            kind=kind,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            tls=tls,
        )
    except _connect_v2().ConnectProbeError as exc:
        raise ValueError(exc.code) from None
    finally:
        secret = ""
        user = ""
        password = ""
    listed = clean_tables(list(probed.get("tables") or []))
    if grant_tables is None:
        chosen = listed
    else:
        chosen = clean_tables(grant_tables)
        if any(table not in listed for table in chosen):
            raise ValueError("table_not_exposed")
    record = store.create(space_name.strip())
    source_id = str(uuid4())
    saved = ConnectStore(database_url, tenant_id).save_source(
        source_id=source_id,
        space_id=str(record.id),
        connector_id=connector_id,
        kind=kind,
        host=host,
        port=port,
        database=database,
        cred_ref=credential_ref,
        mode=mode,
        server_version=str(probed.get("server_version") or ""),
        exposed=list(listed),
        chosen=list(chosen),
        actor=actor,
        token_id=token_id,
    )
    register_source(
        source_id=source_id,
        space_id=str(record.id),
        connector_id=connector_id,
        exposed=list(listed),
        actor=actor,
        token_id=token_id,
    )
    granted: tuple[str, ...] = ()
    if chosen:
        granted = grant_book(
            source_id=source_id,
            tables=list(chosen),
            actor=actor,
            token_id=token_id,
        )
    try:
        _connect_v2().fire_auto_onto(source_id, tuple(listed))
    except Exception:
        pass
    return {
        "space": _space_payload(record),
        "source_id": source_id,
        "connector_id": connector_id,
        "exposed_tables": list(listed),
        "granted_tables": list(granted),
        "mode": mode,
        "read_only": True,
        "onto_job_id": saved["onto_job_id"],
        "server_version": str(probed.get("server_version") or ""),
    }


def connect_v2_list(
    *,
    database_url: str,
    tenant_id: str,
    actor: str,
    token_id: str,
) -> dict[str, Any]:
    from dms_core.control_plane.connect_store import ConnectStore

    rows = ConnectStore(database_url, tenant_id).list_sources()
    _rehydrate(rows, actor=actor, token_id=token_id)
    return {"sources": rows}


def connect_v2_schema(
    ask: Any,
    *,
    database_url: str,
    tenant_id: str,
    source_id: str,
) -> dict[str, Any]:
    from dms_core.control_plane.connect_secrets import ConnectCredentialError
    from dms_core.control_plane.connect_store import ConnectStore

    row = ConnectStore(database_url, tenant_id).get_source(source_id)
    if row is None:
        raise KeyError(source_id)
    live = False
    error: str | None = None
    secret = ""
    user = ""
    password = ""
    try:
        secret = _resolve_connect_secret(ask, row["cred_ref"])
        user, password = _connect_v2().parse_login(secret)
        _connect_v2().probe_source(
            kind=row["kind"],
            host=row["host"],
            port=int(row["port"]),
            database=row["db"],
            user=user,
            password=password,
            tls=False,
        )
        live = True
    except _connect_v2().ConnectProbeError as exc:
        error = exc.code
    except ConnectCredentialError:
        error = "connect_credential_unresolved"
    except Exception:
        error = "network"
    finally:
        secret = ""
        user = ""
        password = ""
    return {
        "source_id": source_id,
        "live": live,
        "error": error,
        "schemas": _group_tables(list(row["exposed_tables"]), set(row["granted_tables"])),
        "truncated": False,
        "bounded": False,
    }


def _live_row(database_url: str, tenant_id: str, source_id: str) -> dict[str, Any]:
    from dms_core.control_plane.connect_store import ConnectStore, ConnectStoreError

    row = ConnectStore(database_url, tenant_id).get_source(source_id)
    if row is None:
        raise KeyError(source_id)
    if row["mode"] != "live":
        raise ConnectStoreError("source_mode_extract")
    return row


def connect_v2_query(
    ask: Any,
    *,
    database_url: str,
    tenant_id: str,
    source_id: str,
    sql: str,
    offset: int,
    page_size: int,
    confirm_runaway: bool,
) -> dict[str, Any]:
    row = _live_row(database_url, tenant_id, source_id)
    secret = _resolve_connect_secret(ask, row["cred_ref"])
    user = ""
    password = ""
    try:
        user, password = _connect_v2().parse_login(secret)
        return _connect_v2().run_live_select(
            kind=row["kind"],
            host=row["host"],
            port=int(row["port"]),
            database=row["db"],
            user=user,
            password=password,
            sql=sql,
            grantable=set(row["granted_tables"]),
            offset=offset,
            page_size=page_size,
            confirm_runaway=confirm_runaway,
        )
    except _connect_v2().ConnectProbeError as exc:
        raise ValueError(exc.code) from None
    except _connect_v2().ConnectQueryError as exc:
        raise ValueError(exc.code) from None
    finally:
        secret = ""
        user = ""
        password = ""


def connect_v2_export(
    ask: Any,
    *,
    database_url: str,
    tenant_id: str,
    source_id: str,
    sql: str,
    confirm_runaway: bool,
) -> tuple[str, Any]:
    row = _live_row(database_url, tenant_id, source_id)
    secret = _resolve_connect_secret(ask, row["cred_ref"])
    user = ""
    password = ""
    try:
        user, password = _connect_v2().parse_login(secret)
        query_id, event = _connect_v2().open_cancel()
        iterator = _connect_v2().iter_live_export(
            kind=row["kind"],
            host=row["host"],
            port=int(row["port"]),
            database=row["db"],
            user=user,
            password=password,
            sql=sql,
            grantable=set(row["granted_tables"]),
            confirm_runaway=confirm_runaway,
            cancel=event,
        )
        return query_id, iterator
    finally:
        secret = ""
        user = ""
        password = ""


def connect_v2_close(query_id: str) -> None:
    _connect_v2().close_cancel(query_id)


def connect_v2_cancel(query_id: str) -> dict[str, Any]:
    if not _connect_v2().cancel_live(query_id):
        raise ValueError("query_not_found")
    return {"ok": True, "cancelled": True}


def connect_v2_set_granted(
    *,
    database_url: str,
    tenant_id: str,
    source_id: str,
    tables: list[str],
    granted: bool,
    actor: str,
    token_id: str,
) -> dict[str, Any]:
    from dms_core.control_plane.connect_grants import grant_tables as grant_book
    from dms_core.control_plane.connect_grants import revoke_tables
    from dms_core.control_plane.connect_store import ConnectStore

    store = ConnectStore(database_url, tenant_id)
    row = store.get_source(source_id)
    if row is None:
        raise KeyError(source_id)
    _rehydrate([row], actor=actor, token_id=token_id)
    names = store.set_granted(
        source_id, tables, granted=granted, actor=actor, token_id=token_id
    )
    try:
        if granted:
            grant_book(source_id=source_id, tables=names, actor=actor, token_id=token_id)
        else:
            revoke_tables(source_id=source_id, tables=names, actor=actor, token_id=token_id)
    except (KeyError, ValueError):
        pass
    body: dict[str, Any] = {"source_id": source_id, "space_id": row["space_id"]}
    if granted:
        body["granted_tables"] = list(names)
    else:
        body["revoked_tables"] = list(names)
    return body
