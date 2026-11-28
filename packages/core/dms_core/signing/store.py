"""Signing rows. Postgres when ``DATABASE_URL`` is set, else process memory.

Swap: the blob column is the default. An object-store key on the same row
replaces the blob (catalog port's neighbour, the object_store port). Not a
sixth port. The audit chain lives only on ``sign_audit_events``.
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Any

from dms_core.signing.chain import GENESIS, link
from dms_core.signing.errors import SignError

DEFAULT_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

_MEMORY: MemorySignStore | None = None
_MEMORY_LOCK = threading.Lock()


class MemorySignStore:
    """One process lock. Upgrade: PostgresSignStore, which locks per document."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.versions: list[dict[str, Any]] = []
        self.envelopes: dict[str, dict[str, Any]] = {}
        self.signers: dict[str, dict[str, Any]] = {}
        self.signatures: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []
        self.disclosures: dict[str, dict[str, Any]] = {}
        self.access: list[dict[str, Any]] = []
        self._tip: dict[str, str] = {}
        self._lock = threading.Lock()

    def save_document(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.documents[row["id"]] = dict(row)
            return dict(row)

    def get_document(self, tenant_id: str, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.documents.get(document_id)
            if row is None or row["tenant_id"] != tenant_id:
                return None
            return dict(row)

    def add_version(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.versions.append(dict(row))
            return dict(row)

    def versions_for(self, tenant_id: str, document_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                dict(row)
                for row in self.versions
                if row["tenant_id"] == tenant_id and row["document_id"] == document_id
            ]
        rows.sort(key=lambda item: int(item["version"]))
        return rows

    def find_sha(self, tenant_id: str, digest: str) -> dict[str, Any] | None:
        with self._lock:
            for row in self.versions:
                if row["tenant_id"] == tenant_id and row["sha256"] == digest:
                    return dict(row)
            for row in self.documents.values():
                if row["tenant_id"] == tenant_id and row["sha256"] == digest:
                    return {
                        "document_id": row["id"],
                        "version": row["version"],
                        "sha256": digest,
                        "tenant_id": tenant_id,
                    }
        return None

    def save_envelope(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.envelopes[row["id"]] = dict(row)
            return dict(row)

    def get_envelope(self, tenant_id: str, envelope_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.envelopes.get(envelope_id)
            if row is None or row["tenant_id"] != tenant_id:
                return None
            return dict(row)

    def save_signer(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.signers[row["id"]] = dict(row)
            return dict(row)

    def get_signer(self, tenant_id: str, signer_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.signers.get(signer_id)
            if row is None or row["tenant_id"] != tenant_id:
                return None
            return dict(row)

    def signers_for(self, tenant_id: str, envelope_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                dict(row)
                for row in self.signers.values()
                if row["tenant_id"] == tenant_id and row["envelope_id"] == envelope_id
            ]
        rows.sort(key=lambda item: int(item["position"]))
        return rows

    def add_signature(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.signatures.append(dict(row))
            return dict(row)

    def signatures_for(self, tenant_id: str, document_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row)
                for row in self.signatures
                if row["tenant_id"] == tenant_id and row["document_id"] == document_id
            ]

    def trust_pems(self, tenant_id: str, document_id: str | None) -> list[bytes]:
        with self._lock:
            pems = []
            for row in self.signatures:
                if row["tenant_id"] != tenant_id:
                    continue
                if document_id and row["document_id"] != document_id:
                    continue
                pem = row.get("cert_pem") or ""
                if isinstance(pem, str) and pem:
                    pems.append(pem.encode("utf-8"))
                elif isinstance(pem, bytes) and pem:
                    pems.append(pem)
            return pems

    def append_prepared(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            prev = self._tip.get(row["document_id"], GENESIS)
            if row.get("prev_hash") != prev or row.get("event_hash") != link(prev, row):
                raise SignError("audit_conflict", 409)
            stored = dict(row)
            self.audits.append(stored)
            self._tip[row["document_id"]] = str(stored["event_hash"])
            return dict(stored)

    def audits_for(self, tenant_id: str, document_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                dict(row)
                for row in self.audits
                if row["tenant_id"] == tenant_id and row["document_id"] == document_id
            ]
        rows.sort(key=lambda item: (str(item["created_at"]), str(item["id"])))
        return rows

    def save_disclosure(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.disclosures[row["id"]] = dict(row)
            return dict(row)

    def get_disclosure(self, tenant_id: str, disclosure_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.disclosures.get(disclosure_id)
            if row is None or row["tenant_id"] != tenant_id:
                return None
            return dict(row)

    def disclosure_by_token(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            for row in self.disclosures.values():
                if row.get("token_hash") == token_hash:
                    return dict(row)
        return None

    def add_access(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.access.append(dict(row))
            return dict(row)

    def access_for(self, tenant_id: str, disclosure_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row)
                for row in self.access
                if row["tenant_id"] == tenant_id and row["disclosure_id"] == disclosure_id
            ]


def reset_memory_sign() -> MemorySignStore:
    global _MEMORY
    with _MEMORY_LOCK:
        _MEMORY = MemorySignStore()
        return _MEMORY


def memory_sign() -> MemorySignStore:
    global _MEMORY
    with _MEMORY_LOCK:
        if _MEMORY is None:
            _MEMORY = MemorySignStore()
        return _MEMORY


class PostgresSignStore:
    """Alembic ``0012_sign`` tables. Errors do not include row values."""

    def __init__(self, database_url: str, tenant_id: str) -> None:
        if not database_url:
            raise SignError("sign_store_unavailable", 503)
        self._url = database_url
        self._tenant = tenant_id or DEFAULT_TENANT

    def _conn(self) -> Any:
        import psycopg

        from dms_core.control_plane.session import set_tenant_context

        try:
            from psycopg.rows import dict_row

            conn: Any = psycopg.connect(self._url, row_factory=dict_row)
            set_tenant_context(conn, self._tenant, role="steward")
        except Exception:
            raise SignError("sign_store_failed", 503) from None
        return conn

    def _one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
            conn.commit()
        return None if row is None else dict(row)

    def _all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            conn.commit()
        return [dict(row) for row in rows]

    def _uuids(self, row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        out = dict(row)
        for key in keys:
            if out.get(key) == "":
                out[key] = None
        return out

    def save_document(self, row: dict[str, Any]) -> dict[str, Any]:
        row = self._uuids(row, ("envelope_id",))
        sql = """
            INSERT INTO dms.sign_documents (
              id, tenant_id, envelope_id, title, body, fields_json, sha256,
              blob, object_key, storage, version, created_at
            ) VALUES (
              %(id)s, %(tenant_id)s, %(envelope_id)s, %(title)s, %(body)s,
              %(fields_json)s, %(sha256)s, %(blob)s, %(object_key)s, %(storage)s,
              %(version)s, %(created_at)s
            )
            ON CONFLICT (id) DO UPDATE SET
              envelope_id = EXCLUDED.envelope_id,
              sha256 = EXCLUDED.sha256,
              blob = EXCLUDED.blob,
              object_key = EXCLUDED.object_key,
              storage = EXCLUDED.storage,
              version = EXCLUDED.version
        """
        with self._conn() as conn:
            conn.execute(sql, row)
            conn.commit()
        return dict(row)

    def get_document(self, tenant_id: str, document_id: str) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT * FROM dms.sign_documents
             WHERE tenant_id = %s AND id = %s
            """,
            (tenant_id, document_id),
        )

    def add_version(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO dms.sign_versions (
                  id, tenant_id, document_id, version, sha256, blob,
                  object_key, storage, created_at
                ) VALUES (
                  %(id)s, %(tenant_id)s, %(document_id)s, %(version)s, %(sha256)s,
                  %(blob)s, %(object_key)s, %(storage)s, %(created_at)s
                )
                """,
                row,
            )
            conn.commit()
        return dict(row)

    def versions_for(self, tenant_id: str, document_id: str) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT * FROM dms.sign_versions
             WHERE tenant_id = %s AND document_id = %s
             ORDER BY version
            """,
            (tenant_id, document_id),
        )

    def find_sha(self, tenant_id: str, digest: str) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT document_id, version, sha256, tenant_id
              FROM dms.sign_versions
             WHERE tenant_id = %s AND sha256 = %s
             LIMIT 1
            """,
            (tenant_id, digest),
        )

    def save_envelope(self, row: dict[str, Any]) -> dict[str, Any]:
        sql = """
            INSERT INTO dms.sign_envelopes (
              id, tenant_id, document_id, order_mode, status, created_at
            ) VALUES (
              %(id)s, %(tenant_id)s, %(document_id)s, %(order_mode)s,
              %(status)s, %(created_at)s
            )
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status
        """
        with self._conn() as conn:
            conn.execute(sql, row)
            conn.commit()
        return dict(row)

    def get_envelope(self, tenant_id: str, envelope_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM dms.sign_envelopes WHERE tenant_id = %s AND id = %s",
            (tenant_id, envelope_id),
        )

    def save_signer(self, row: dict[str, Any]) -> dict[str, Any]:
        sql = """
            INSERT INTO dms.sign_signers (
              id, tenant_id, envelope_id, position, email, auth_method, identity,
              status, token_hash, otp_hash, expires_at, consent_text, intent,
              signed_at, created_at
            ) VALUES (
              %(id)s, %(tenant_id)s, %(envelope_id)s, %(position)s, %(email)s,
              %(auth_method)s, %(identity)s, %(status)s, %(token_hash)s, %(otp_hash)s,
              %(expires_at)s, %(consent_text)s, %(intent)s, %(signed_at)s, %(created_at)s
            )
            ON CONFLICT (id) DO UPDATE SET
              status = EXCLUDED.status,
              token_hash = EXCLUDED.token_hash,
              otp_hash = EXCLUDED.otp_hash,
              consent_text = EXCLUDED.consent_text,
              intent = EXCLUDED.intent,
              signed_at = EXCLUDED.signed_at
        """
        with self._conn() as conn:
            conn.execute(sql, row)
            conn.commit()
        return dict(row)

    def get_signer(self, tenant_id: str, signer_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM dms.sign_signers WHERE tenant_id = %s AND id = %s",
            (tenant_id, signer_id),
        )

    def signers_for(self, tenant_id: str, envelope_id: str) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT * FROM dms.sign_signers
             WHERE tenant_id = %s AND envelope_id = %s
             ORDER BY position
            """,
            (tenant_id, envelope_id),
        )

    def add_signature(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO dms.sign_signatures (
                  id, tenant_id, document_id, signer_id, sha256_before, sha256_after,
                  cert_pem, cert_sha256, capture_kind, capture_svg, tsa_present,
                  signed_at
                ) VALUES (
                  %(id)s, %(tenant_id)s, %(document_id)s, %(signer_id)s,
                  %(sha256_before)s, %(sha256_after)s, %(cert_pem)s, %(cert_sha256)s,
                  %(capture_kind)s, %(capture_svg)s, %(tsa_present)s, %(signed_at)s
                )
                """,
                row,
            )
            conn.commit()
        return dict(row)

    def signatures_for(self, tenant_id: str, document_id: str) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT * FROM dms.sign_signatures
             WHERE tenant_id = %s AND document_id = %s
             ORDER BY signed_at
            """,
            (tenant_id, document_id),
        )

    def trust_pems(self, tenant_id: str, document_id: str | None) -> list[bytes]:
        if document_id:
            rows = self._all(
                """
                SELECT cert_pem FROM dms.sign_signatures
                 WHERE tenant_id = %s AND document_id = %s AND cert_pem IS NOT NULL
                """,
                (tenant_id, document_id),
            )
        else:
            rows = self._all(
                """
                SELECT cert_pem FROM dms.sign_signatures
                 WHERE tenant_id = %s AND cert_pem IS NOT NULL
                """,
                (tenant_id,),
            )
        out: list[bytes] = []
        for row in rows:
            pem = row.get("cert_pem") or ""
            if isinstance(pem, str) and pem:
                out.append(pem.encode("utf-8"))
            elif isinstance(pem, bytes) and pem:
                out.append(pem)
        return out

    def append_prepared(self, row: dict[str, Any]) -> dict[str, Any]:
        lock_key = int(hashlib.sha256(str(row["document_id"]).encode()).hexdigest()[:15], 16)
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            prev_row = conn.execute(
                """
                SELECT event_hash FROM dms.sign_audit_events
                 WHERE tenant_id = %s AND document_id = %s
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (row["tenant_id"], row["document_id"]),
            ).fetchone()
            prev = GENESIS if prev_row is None else str(prev_row["event_hash"])
            if row.get("prev_hash") != prev or row.get("event_hash") != link(prev, row):
                raise SignError("audit_conflict", 409)
            stored = self._uuids(row, ("envelope_id",))
            conn.execute(
                """
                INSERT INTO dms.sign_audit_events (
                  id, tenant_id, document_id, envelope_id, event_type, identity,
                  auth_method, ip, user_agent, consent_text, intent, sha256_before,
                  sha256_after, prev_hash, event_hash, cortex_entry_id, created_at
                ) VALUES (
                  %(id)s, %(tenant_id)s, %(document_id)s, %(envelope_id)s, %(event_type)s,
                  %(identity)s, %(auth_method)s, %(ip)s, %(user_agent)s, %(consent_text)s,
                  %(intent)s, %(sha256_before)s, %(sha256_after)s, %(prev_hash)s,
                  %(event_hash)s, %(cortex_entry_id)s, %(created_at)s
                )
                """,
                stored,
            )
            conn.commit()
        return stored

    def audits_for(self, tenant_id: str, document_id: str) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT * FROM dms.sign_audit_events
             WHERE tenant_id = %s AND document_id = %s
             ORDER BY created_at, id
            """,
            (tenant_id, document_id),
        )

    def save_disclosure(self, row: dict[str, Any]) -> dict[str, Any]:
        sql = """
            INSERT INTO dms.sign_disclosures (
              id, tenant_id, document_id, token_hash, mode, watermark,
              redact_json, expires_at, revoked_at, created_at
            ) VALUES (
              %(id)s, %(tenant_id)s, %(document_id)s, %(token_hash)s, %(mode)s,
              %(watermark)s, %(redact_json)s, %(expires_at)s, %(revoked_at)s,
              %(created_at)s
            )
            ON CONFLICT (id) DO UPDATE SET revoked_at = EXCLUDED.revoked_at
        """
        with self._conn() as conn:
            conn.execute(sql, row)
            conn.commit()
        return dict(row)

    def get_disclosure(self, tenant_id: str, disclosure_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM dms.sign_disclosures WHERE tenant_id = %s AND id = %s",
            (tenant_id, disclosure_id),
        )

    def disclosure_by_token(self, token_hash: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM dms.sign_disclosures WHERE token_hash = %s",
            (token_hash,),
        )

    def add_access(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO dms.sign_access_log (
                  id, tenant_id, disclosure_id, ip, user_agent, outcome, created_at
                ) VALUES (
                  %(id)s, %(tenant_id)s, %(disclosure_id)s, %(ip)s, %(user_agent)s,
                  %(outcome)s, %(created_at)s
                )
                """,
                row,
            )
            conn.commit()
        return dict(row)

    def access_for(self, tenant_id: str, disclosure_id: str) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT * FROM dms.sign_access_log
             WHERE tenant_id = %s AND disclosure_id = %s
             ORDER BY created_at
            """,
            (tenant_id, disclosure_id),
        )


def get_sign_store() -> MemorySignStore | PostgresSignStore:
    """Postgres when ``DATABASE_URL`` is set. Tests leave it unset."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return memory_sign()
    tenant = os.environ.get("DMS_TENANT_ID", "").strip() or DEFAULT_TENANT
    return PostgresSignStore(url, tenant)
