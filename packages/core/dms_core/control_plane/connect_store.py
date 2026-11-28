"""Postgres store for a connected SQL source (R2-CONNECT-CORE-01).

The row holds an OpenVault reference. It does not hold a password. Grant
rows are steward-editable. Audit detail is a short code.

Swap: this is the catalog port's connection book. Postgres when
``DATABASE_URL`` is set and ``DMS_CONNECT_V2`` is on. Flag off does not
construct this store.
"""

from __future__ import annotations

import os
import re
from threading import Lock
from typing import Any
from uuid import uuid4

import psycopg

from dms_core.control_plane.connect_secrets import key_id_for_ref, value_has_secret_shape
from dms_core.control_plane.session import set_tenant_context

_KINDS = frozenset({"postgresql", "mysql", "mariadb", "sqlserver", "sqlite"})
def _flag_on(name: str) -> bool:
    """On only for the digit 1. ``true``, ``yes``, and ``on`` stay off."""
    return os.environ.get(name, "").strip() == "1"


def allowed_kinds() -> frozenset[str]:
    """Kinds the connect book accepts. Gauss and Oracle stay out unless their flag is on."""
    kinds = set(_KINDS)
    if _flag_on("DMS_CONNECT_GAUSS"):
        kinds.add("gauss")
    if _flag_on("DMS_CONNECT_ORACLE"):
        kinds.add("oracle")
    return frozenset(kinds)
_MODES = frozenset({"live", "extract"})
_HOST = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")
_DB = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_DETAIL = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

_mem_lock = Lock()
_mem_audit: list[dict[str, str]] = []


class ConnectStoreError(Exception):
    """The message is the code only. It never carries a secret or a row."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def note_probe(code: str, *, actor: str, token_id: str) -> None:
    """Remember a probe outcome in this process. Detail is the code only."""
    detail = code if _DETAIL.fullmatch(code) else "bad_detail"
    row = {
        "action": "probe",
        "detail": detail,
        "actor": _plain(actor),
        "token_id": _plain(token_id),
    }
    with _mem_lock:
        _mem_audit.append(row)


def memory_audit_rows() -> list[dict[str, str]]:
    with _mem_lock:
        return [dict(row) for row in _mem_audit]


def reset_memory_audit() -> None:
    with _mem_lock:
        _mem_audit.clear()


def _plain(value: str) -> str:
    text = str(value)[:80]
    if value_has_secret_shape(text):
        return "redacted"
    return text


def _detail(code: str) -> str:
    return code if _DETAIL.fullmatch(code) else "bad_detail"


class ConnectStore:
    def __init__(self, database_url: str, tenant_id: str) -> None:
        if not database_url:
            raise ConnectStoreError("connect_store_unavailable")
        self._url = database_url
        self._tenant = str(tenant_id)

    def _conn(self) -> psycopg.Connection:
        try:
            conn = psycopg.connect(self._url)
        except Exception:
            raise ConnectStoreError("connect_store_failed") from None
        return conn

    def _validated(
        self,
        *,
        kind: str,
        host: str,
        port: int,
        database: str,
        cred_ref: str,
        mode: str,
    ) -> None:
        if kind not in allowed_kinds() or mode not in _MODES:
            raise ConnectStoreError("bad_request")
        if isinstance(port, bool) or not isinstance(port, int) or port < 0 or port > 65535:
            raise ConnectStoreError("bad_request")
        if key_id_for_ref(cred_ref) is None:
            raise ConnectStoreError("bad_credential_ref")
        for text in (host, database, cred_ref, kind, mode):
            if value_has_secret_shape(text):
                raise ConnectStoreError("connect_raw_secret_rejected")
        if kind != "sqlite":
            if _HOST.fullmatch(host) is None or _DB.fullmatch(database) is None:
                raise ConnectStoreError("bad_request")

    def save_source(
        self,
        *,
        source_id: str,
        space_id: str,
        connector_id: str,
        kind: str,
        host: str,
        port: int,
        database: str,
        cred_ref: str,
        mode: str,
        server_version: str,
        exposed: list[str],
        chosen: list[str],
        actor: str,
        token_id: str,
    ) -> dict[str, Any]:
        """Insert the source, one grant row per table, the audit code, the onto job."""
        self._validated(
            kind=kind, host=host, port=port, database=database, cred_ref=cred_ref, mode=mode
        )
        version = server_version[:200]
        if value_has_secret_shape(version):
            version = ""
        chosen_set = set(chosen)
        job_id = str(uuid4())
        try:
            with self._conn() as conn:
                set_tenant_context(conn, self._tenant, role="steward")
                conn.execute(
                    """
                    INSERT INTO dms.connect_sources (
                      id, tenant_id, space_id, connector_id, kind, host, port,
                      database_name, cred_ref, mode, read_only, server_version
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s
                    )
                    """,
                    (
                        source_id,
                        self._tenant,
                        space_id,
                        connector_id[:200],
                        kind,
                        host,
                        port,
                        database,
                        cred_ref,
                        mode,
                        version,
                    ),
                )
                for name in exposed:
                    conn.execute(
                        """
                        INSERT INTO dms.connect_grants (
                          tenant_id, source_id, table_name, granted
                        ) VALUES (%s, %s, %s, %s)
                        """,
                        (self._tenant, source_id, name, name in chosen_set),
                    )
                self._audit(
                    conn,
                    source_id=source_id,
                    action="save",
                    actor=actor,
                    token_id=token_id,
                    detail="save",
                )
                conn.execute(
                    """
                    INSERT INTO dms.connect_onto_jobs (
                      id, tenant_id, source_id, status, tables
                    ) VALUES (%s, %s, %s, 'requested', %s)
                    """,
                    (job_id, self._tenant, source_id, list(exposed)),
                )
        except ConnectStoreError:
            raise
        except Exception:
            raise ConnectStoreError("connect_store_failed") from None
        return {
            "source_id": source_id,
            "space_id": space_id,
            "onto_job_id": job_id,
            "mode": mode,
            "read_only": True,
        }

    def note_durable(self, code: str, *, actor: str, token_id: str, source_id: str | None) -> None:
        try:
            with self._conn() as conn:
                set_tenant_context(conn, self._tenant, role="steward")
                self._audit(
                    conn,
                    source_id=source_id,
                    action="probe",
                    actor=actor,
                    token_id=token_id,
                    detail=_detail(code),
                )
        except Exception:
            return

    def _audit(
        self,
        conn: psycopg.Connection,
        *,
        source_id: str | None,
        action: str,
        actor: str,
        token_id: str,
        detail: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO dms.connect_audit (
              tenant_id, source_id, action, actor, token_id, detail
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                self._tenant,
                source_id,
                _detail(action),
                _plain(actor),
                _plain(token_id),
                _detail(detail),
            ),
        )

    def list_sources(self) -> list[dict[str, Any]]:
        try:
            with self._conn() as conn:
                set_tenant_context(conn, self._tenant, role="steward")
                sources = conn.execute(
                    """
                    SELECT id::text, space_id, connector_id, kind, host, port,
                           database_name, cred_ref, mode, read_only, server_version
                    FROM dms.connect_sources
                    WHERE tenant_id::text = %s
                    ORDER BY created_at
                    """,
                    (self._tenant,),
                ).fetchall()
                grants = conn.execute(
                    """
                    SELECT source_id::text, table_name, granted
                    FROM dms.connect_grants
                    WHERE tenant_id::text = %s
                    """,
                    (self._tenant,),
                ).fetchall()
        except Exception:
            raise ConnectStoreError("connect_store_failed") from None
        by_source: dict[str, list[tuple[str, bool]]] = {}
        for source_id, table_name, granted in grants:
            by_source.setdefault(str(source_id), []).append((str(table_name), bool(granted)))
        out: list[dict[str, Any]] = []
        for row in sources:
            sid = str(row[0])
            pairs = by_source.get(sid, [])
            exposed = [name for name, _granted in pairs]
            granted_names = [name for name, is_on in pairs if is_on]
            out.append(
                {
                    "source_id": sid,
                    "space_id": str(row[1]),
                    "connector_id": str(row[2]),
                    "kind": str(row[3]),
                    "host": str(row[4]),
                    "port": int(row[5]),
                    "db": str(row[6]),
                    "cred_ref": str(row[7]),
                    "mode": str(row[8]),
                    "read_only": bool(row[9]),
                    "server_version": str(row[10]),
                    "exposed_tables": exposed,
                    "granted_tables": granted_names,
                }
            )
        return out

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        for row in self.list_sources():
            if row["source_id"] == source_id:
                return row
        return None

    def set_granted(
        self,
        source_id: str,
        tables: list[str],
        *,
        granted: bool,
        actor: str,
        token_id: str,
    ) -> list[str]:
        row = self.get_source(source_id)
        if row is None:
            raise ConnectStoreError("source_not_found")
        exposed = set(row["exposed_tables"])
        if any(name not in exposed for name in tables):
            raise ConnectStoreError("table_not_exposed")
        if not granted and any(name not in set(row["granted_tables"]) for name in tables):
            raise ConnectStoreError("table_not_granted")
        action = "grant" if granted else "revoke"
        try:
            with self._conn() as conn:
                set_tenant_context(conn, self._tenant, role="steward")
                for name in tables:
                    conn.execute(
                        """
                        UPDATE dms.connect_grants
                           SET granted = %s
                         WHERE tenant_id::text = %s
                           AND source_id = %s
                           AND table_name = %s
                        """,
                        (granted, self._tenant, source_id, name),
                    )
                self._audit(
                    conn,
                    source_id=source_id,
                    action=action,
                    actor=actor,
                    token_id=token_id,
                    detail=action,
                )
        except ConnectStoreError:
            raise
        except Exception:
            raise ConnectStoreError("connect_store_failed") from None
        return list(tables)

    def audit_rows(self) -> list[dict[str, str]]:
        try:
            with self._conn() as conn:
                set_tenant_context(conn, self._tenant, role="steward")
                rows = conn.execute(
                    """
                    SELECT action, actor, token_id, COALESCE(source_id::text, ''), detail
                    FROM dms.connect_audit
                    WHERE tenant_id::text = %s
                    ORDER BY created_at
                    """,
                    (self._tenant,),
                ).fetchall()
        except Exception:
            raise ConnectStoreError("connect_store_failed") from None
        return [
            {
                "action": str(row[0]),
                "actor": str(row[1]),
                "token_id": str(row[2]),
                "source_id": str(row[3]),
                "detail": str(row[4]),
            }
            for row in rows
        ]

    def onto_job(self, source_id: str) -> dict[str, Any] | None:
        try:
            with self._conn() as conn:
                set_tenant_context(conn, self._tenant, role="steward")
                row = conn.execute(
                    """
                    SELECT id::text, status, tables
                    FROM dms.connect_onto_jobs
                    WHERE tenant_id::text = %s AND source_id = %s
                    ORDER BY created_at
                    LIMIT 1
                    """,
                    (self._tenant, source_id),
                ).fetchone()
        except Exception:
            raise ConnectStoreError("connect_store_failed") from None
        if row is None:
            return None
        return {"id": str(row[0]), "status": str(row[1]), "tables": list(row[2] or [])}
