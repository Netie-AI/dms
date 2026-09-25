"""Alembic revision: durable versioned ontology store (ONTO-STORE-01 / dms#279)."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_ontology_store"
down_revision: str | None = "0003_document_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Source identity is kind + host + database + schema. Never credentials.
_TABLES = (
    "ontology_version",
    "onto_object_type",
    "onto_property",
    "onto_link",
    "onto_measure",
    "onto_object",
    "onto_link_instance",
    "onto_audit",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dms.ontology_version (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          space_id UUID NOT NULL REFERENCES dms.spaces(id) ON DELETE CASCADE,
          source_kind TEXT NOT NULL,
          source_host TEXT NOT NULL,
          source_database TEXT NOT NULL,
          source_schema TEXT NOT NULL,
          schema_fingerprint TEXT NOT NULL,
          status TEXT NOT NULL
            CHECK (status IN ('proposed', 'active', 'superseded')),
          created_by UUID REFERENCES dms.users(id),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (
            space_id, source_kind, source_host, source_database, source_schema,
            schema_fingerprint
          )
        );

        CREATE UNIQUE INDEX ontology_version_one_active
          ON dms.ontology_version (
            space_id, source_kind, source_host, source_database, source_schema
          )
          WHERE status = 'active';

        CREATE INDEX ontology_version_by_space
          ON dms.ontology_version (tenant_id, space_id);

        CREATE TABLE dms.onto_object_type (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_id UUID NOT NULL REFERENCES dms.ontology_version(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          relation TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'proposed'
            CHECK (state IN ('proposed', 'confirmed', 'rejected')),
          UNIQUE (version_id, name)
        );

        CREATE TABLE dms.onto_property (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_id UUID NOT NULL REFERENCES dms.ontology_version(id) ON DELETE CASCADE,
          object_type_id UUID NOT NULL REFERENCES dms.onto_object_type(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          column_name TEXT NOT NULL,
          is_pk BOOLEAN NOT NULL DEFAULT false,
          state TEXT NOT NULL DEFAULT 'proposed'
            CHECK (state IN ('proposed', 'confirmed', 'rejected')),
          UNIQUE (object_type_id, name)
        );

        CREATE TABLE dms.onto_link (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_id UUID NOT NULL REFERENCES dms.ontology_version(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          from_type TEXT NOT NULL,
          to_type TEXT NOT NULL,
          from_columns TEXT[] NOT NULL,
          to_columns TEXT[] NOT NULL,
          declared_cardinality TEXT NOT NULL
            CHECK (declared_cardinality IN ('one-to-one', 'one-to-many')),
          measured_cardinality TEXT
            CHECK (
              measured_cardinality IS NULL
              OR measured_cardinality IN ('many_to_one', 'many_to_many', 'unverified')
            ),
          state TEXT NOT NULL DEFAULT 'proposed'
            CHECK (state IN ('proposed', 'confirmed', 'rejected')),
          UNIQUE (version_id, name)
        );

        CREATE TABLE dms.onto_measure (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_id UUID NOT NULL REFERENCES dms.ontology_version(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          column_name TEXT NOT NULL,
          aggregate TEXT NOT NULL,
          grain TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'proposed'
            CHECK (state IN ('proposed', 'confirmed', 'rejected')),
          UNIQUE (version_id, name)
        );

        CREATE TABLE dms.onto_object (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_id UUID NOT NULL REFERENCES dms.ontology_version(id) ON DELETE CASCADE,
          object_type_id UUID NOT NULL REFERENCES dms.onto_object_type(id) ON DELETE CASCADE,
          key_values JSONB NOT NULL DEFAULT '{}'::jsonb,
          state TEXT NOT NULL DEFAULT 'proposed'
            CHECK (state IN ('proposed', 'confirmed', 'rejected'))
        );

        CREATE TABLE dms.onto_link_instance (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_id UUID NOT NULL REFERENCES dms.ontology_version(id) ON DELETE CASCADE,
          link_id UUID NOT NULL REFERENCES dms.onto_link(id) ON DELETE CASCADE,
          from_object_id UUID NOT NULL REFERENCES dms.onto_object(id) ON DELETE CASCADE,
          to_object_id UUID NOT NULL REFERENCES dms.onto_object(id) ON DELETE CASCADE,
          state TEXT NOT NULL DEFAULT 'proposed'
            CHECK (state IN ('proposed', 'confirmed', 'rejected'))
        );

        CREATE TABLE dms.onto_audit (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES dms.tenants(id) ON DELETE CASCADE,
          version_id UUID REFERENCES dms.ontology_version(id) ON DELETE SET NULL,
          actor_user_id UUID REFERENCES dms.users(id),
          action_type TEXT NOT NULL,
          inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
          result JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX onto_audit_by_version
          ON dms.onto_audit (tenant_id, version_id, created_at);
        """
    )
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON dms.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON dms.{table} TO dms_viewer, dms_steward, dms_admin")
        op.execute(f"GRANT INSERT, UPDATE ON dms.{table} TO dms_steward, dms_admin")
        op.execute(f"GRANT DELETE ON dms.{table} TO dms_admin")
        op.execute(f"ALTER TABLE dms.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE dms.{table} FORCE ROW LEVEL SECURITY")
        for role in ("dms_viewer", "dms_steward", "dms_admin"):
            op.execute(
                f"""
                CREATE POLICY {table}_select_{role} ON dms.{table}
                  FOR SELECT TO {role}
                  USING (tenant_id::text = current_setting('dms.tenant_id', true));
                """
            )
        for role in ("dms_steward", "dms_admin"):
            op.execute(
                f"""
                CREATE POLICY {table}_insert_{role} ON dms.{table}
                  FOR INSERT TO {role}
                  WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
                """
            )
            op.execute(
                f"""
                CREATE POLICY {table}_update_{role} ON dms.{table}
                  FOR UPDATE TO {role}
                  USING (tenant_id::text = current_setting('dms.tenant_id', true))
                  WITH CHECK (tenant_id::text = current_setting('dms.tenant_id', true));
                """
            )
        op.execute(
            f"""
            CREATE POLICY {table}_delete_dms_admin ON dms.{table}
              FOR DELETE TO dms_admin
              USING (tenant_id::text = current_setting('dms.tenant_id', true));
            """
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS dms.{table} CASCADE")
