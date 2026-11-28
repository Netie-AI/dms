"""Alembic revision: document signing (DMS_SIGN).

Documents, versions, envelopes, signers, signatures, the audit evidence
chain, disclosures, and the disclosure access log. No cross-schema foreign
key. ``cortex_entry_id`` on the audit row is a pointer. The hash columns on
``sign_audit_events`` are the e-sign certificate chain the founder asked for
on 2026-11-28. ``dms.ledger_ref`` is unchanged and still has no hash columns.

Chain: ``0012_sign`` on ``0011_space_bench``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_sign"
down_revision: str | None = "0011_space_bench"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = ("dms_viewer", "dms_steward", "dms_admin")
_WRITERS = ("dms_steward", "dms_admin")
_TENANT = "tenant_id::text = current_setting('dms.tenant_id', true)"


def _rls(table: str, *, update: bool) -> None:
    op.execute(f"REVOKE ALL ON dms.{table} FROM PUBLIC")
    op.execute(f"GRANT SELECT ON dms.{table} TO {', '.join(_ROLES)}")
    privs = "INSERT, UPDATE" if update else "INSERT"
    op.execute(f"GRANT {privs} ON dms.{table} TO {', '.join(_WRITERS)}")
    op.execute(f"ALTER TABLE dms.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE dms.{table} FORCE ROW LEVEL SECURITY")
    for role in _ROLES:
        op.execute(
            f"""
            CREATE POLICY {table}_select_{role} ON dms.{table}
              FOR SELECT TO {role}
              USING ({_TENANT});
            """
        )
    for role in _WRITERS:
        op.execute(
            f"""
            CREATE POLICY {table}_insert_{role} ON dms.{table}
              FOR INSERT TO {role}
              WITH CHECK ({_TENANT});
            """
        )
        if update:
            op.execute(
                f"""
                CREATE POLICY {table}_update_{role} ON dms.{table}
                  FOR UPDATE TO {role}
                  USING ({_TENANT})
                  WITH CHECK ({_TENANT});
                """
            )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.sign_documents (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          envelope_id UUID,
          title TEXT NOT NULL,
          body TEXT NOT NULL DEFAULT '',
          fields_json TEXT NOT NULL DEFAULT '{}',
          sha256 TEXT NOT NULL,
          blob BYTEA,
          object_key TEXT NOT NULL DEFAULT '',
          storage TEXT NOT NULL CHECK (storage IN ('db', 'object_store')),
          version INTEGER NOT NULL,
          created_at TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE dms.sign_versions (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          document_id UUID NOT NULL REFERENCES dms.sign_documents(id) ON DELETE CASCADE,
          version INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          blob BYTEA,
          object_key TEXT NOT NULL DEFAULT '',
          storage TEXT NOT NULL CHECK (storage IN ('db', 'object_store')),
          created_at TIMESTAMPTZ NOT NULL,
          UNIQUE (document_id, version)
        );

        CREATE TABLE dms.sign_envelopes (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          document_id UUID NOT NULL REFERENCES dms.sign_documents(id) ON DELETE CASCADE,
          order_mode TEXT NOT NULL CHECK (order_mode IN ('sequential', 'parallel')),
          status TEXT NOT NULL CHECK (status IN ('pending', 'partial', 'completed')),
          created_at TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE dms.sign_signers (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          envelope_id UUID NOT NULL REFERENCES dms.sign_envelopes(id) ON DELETE CASCADE,
          position INTEGER NOT NULL,
          email TEXT NOT NULL,
          auth_method TEXT NOT NULL CHECK (auth_method IN ('sso', 'email_otp')),
          identity TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('pending', 'signed')),
          token_hash TEXT NOT NULL DEFAULT '',
          otp_hash TEXT NOT NULL DEFAULT '',
          expires_at TIMESTAMPTZ NOT NULL,
          consent_text TEXT NOT NULL DEFAULT '',
          intent TEXT NOT NULL DEFAULT '',
          signed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL,
          UNIQUE (envelope_id, position)
        );

        CREATE TABLE dms.sign_signatures (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          document_id UUID NOT NULL REFERENCES dms.sign_documents(id) ON DELETE CASCADE,
          signer_id UUID NOT NULL REFERENCES dms.sign_signers(id) ON DELETE CASCADE,
          sha256_before TEXT NOT NULL,
          sha256_after TEXT NOT NULL,
          cert_pem TEXT NOT NULL,
          cert_sha256 TEXT NOT NULL,
          capture_kind TEXT NOT NULL CHECK (capture_kind IN ('typed', 'drawn', 'upload')),
          capture_svg TEXT NOT NULL DEFAULT '',
          tsa_present BOOLEAN NOT NULL DEFAULT false,
          signed_at TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE dms.sign_audit_events (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          document_id UUID NOT NULL REFERENCES dms.sign_documents(id) ON DELETE CASCADE,
          envelope_id UUID,
          event_type TEXT NOT NULL,
          identity TEXT NOT NULL DEFAULT '',
          auth_method TEXT NOT NULL DEFAULT '',
          ip TEXT NOT NULL DEFAULT '',
          user_agent TEXT NOT NULL DEFAULT '',
          consent_text TEXT NOT NULL DEFAULT '',
          intent TEXT NOT NULL DEFAULT '',
          sha256_before TEXT NOT NULL DEFAULT '',
          sha256_after TEXT NOT NULL DEFAULT '',
          prev_hash TEXT NOT NULL,
          event_hash TEXT NOT NULL,
          cortex_entry_id TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE dms.sign_disclosures (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          document_id UUID NOT NULL REFERENCES dms.sign_documents(id) ON DELETE CASCADE,
          token_hash TEXT NOT NULL,
          mode TEXT NOT NULL CHECK (mode IN ('view', 'download')),
          watermark BOOLEAN NOT NULL,
          redact_json TEXT NOT NULL DEFAULT '[]',
          expires_at TIMESTAMPTZ NOT NULL,
          revoked_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE dms.sign_access_log (
          id UUID PRIMARY KEY,
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          disclosure_id UUID NOT NULL REFERENCES dms.sign_disclosures(id) ON DELETE CASCADE,
          ip TEXT NOT NULL DEFAULT '',
          user_agent TEXT NOT NULL DEFAULT '',
          outcome TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL
        );

        CREATE INDEX sign_audit_by_document
          ON dms.sign_audit_events (tenant_id, document_id, created_at);
        CREATE INDEX sign_disclosure_by_token
          ON dms.sign_disclosures (token_hash);
        """
    )
    for table in (
        "sign_documents",
        "sign_envelopes",
        "sign_signers",
        "sign_disclosures",
    ):
        _rls(table, update=True)
    for table in (
        "sign_versions",
        "sign_signatures",
        "sign_audit_events",
        "sign_access_log",
    ):
        _rls(table, update=False)


def downgrade() -> None:
    for table in (
        "sign_access_log",
        "sign_disclosures",
        "sign_audit_events",
        "sign_signatures",
        "sign_signers",
        "sign_envelopes",
        "sign_versions",
        "sign_documents",
    ):
        op.execute(f"DROP TABLE IF EXISTS dms.{table} CASCADE")
