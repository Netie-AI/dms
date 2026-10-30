"""Revocable ontology items (ONTO-DATA-01, ONTO-PG-01).

Rows match alembic ``0007_ontology_data`` (revision 0003 is already
``document_chunks``) plus ``approval_ref`` from ``0008_ontology_approval_ref``.
``Ontology.from_store`` reads ``ontology_data_store().approved_items``.

Swap: in-memory, or Postgres over ``dms.ontology_versions`` and
``dms.ontology_items``. Not a new port. ``DMS_ONTO_PG`` defaults off. On, and
only when ``DATABASE_URL`` is set, the factory returns
``PostgresOntologyDataStore``. Otherwise the in-memory store.

ponytail: ``deny_steward`` is process-local on either store. Upgrade path: a
membership read.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg

from dms_core.control_plane.session import set_tenant_context

KINDS = ("object", "link", "measure", "grain", "pii")
STATUSES = ("proposed", "approved", "revoked")

_LOCK = threading.Lock()
#: Same default as ``Settings.dms_tenant_id``. Used only when the flag is on.
_DEFAULT_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_CONNECT_TIMEOUT_S = 3

_ITEM_COLUMNS = (
    "id::text, tenant_id::text, version_id::text, kind, payload, status, "
    "source, provenance, created_by::text, approved_by, approval_ref, ts"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canon(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def item_identity(kind: str, payload: Mapping[str, Any]) -> str:
    name = payload.get("name")
    if isinstance(name, str) and name:
        return f"{kind}:{name}"
    return f"{kind}:{_canon(payload)}"


def _link_payload(
    row: Mapping[str, Any], *, origin: str, by_table: Mapping[str, str]
) -> dict[str, Any]:
    if "left_table" in row:
        src_t = str(row.get("left_table") or "")
        src_c = str(row.get("left_column") or "")
        dst_t = str(row.get("right_table") or "")
        dst_c = str(row.get("right_column") or "")
    else:
        src_t = str(row.get("source_table") or "")
        src_c = str(row.get("source_column") or "")
        dst_t = str(row.get("target_table") or "")
        dst_c = str(row.get("target_column") or "")
    return {
        "name": f"{origin}:{src_t}.{src_c}->{dst_t}.{dst_c}",
        "from_object": by_table.get(src_t, src_t),
        "from_columns": [src_c] if src_c else [],
        "to_object": by_table.get(dst_t, dst_t),
        "to_columns": [dst_c] if dst_c else [],
        "one_to_one": bool(row.get("one_to_one")),
    }


def items_from_proposal(body: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Map a ``compile_source`` JSON body onto item kinds. No invented measures."""
    terms = [dict(row) for row in body.get("terms") or [] if isinstance(row, dict)]
    by_table: dict[str, str] = {}
    for term in terms:
        table = str(term.get("table") or "")
        name = str(term.get("name") or table)
        if table and name:
            by_table[table] = name
    attrs_by_table: dict[str, list[dict[str, Any]]] = {}
    for attr in body.get("attributes") or []:
        if isinstance(attr, dict):
            attrs_by_table.setdefault(str(attr.get("table") or ""), []).append(dict(attr))
    rows: list[tuple[str, dict[str, Any]]] = []
    for term in terms:
        table = str(term.get("table") or "")
        nested = [dict(a) for a in (term.get("attributes") or []) if isinstance(a, dict)]
        seen = {(a.get("source_column"), a.get("display_name")) for a in nested}
        attributes = list(nested)
        for attr in attrs_by_table.get(table, []):
            mark = (attr.get("source_column"), attr.get("display_name"))
            if mark not in seen:
                attributes.append(attr)
                seen.add(mark)
        raw_key = term.get("key")
        key_cols = [str(c) for c in raw_key if str(c).strip()] if isinstance(raw_key, list) else []
        if not key_cols:
            continue
        rows.append(
            (
                "object",
                {
                    "name": str(term.get("name") or table),
                    "relation": table,
                    "key": key_cols,
                    "description": str(term.get("description") or ""),
                    "attributes": attributes,
                },
            )
        )
    for origin, field in (("inferred_fk", "inferred_fks"), ("declared_fk", "declared_fks")):
        for fk in body.get(field) or []:
            if isinstance(fk, dict):
                rows.append(("link", _link_payload(fk, origin=origin, by_table=by_table)))
    for join in body.get("joins") or []:
        if isinstance(join, dict):
            rows.append(("link", _link_payload(join, origin="join", by_table=by_table)))
    for kind, field in (("measure", "measures"), ("grain", "grains"), ("pii", "pii")):
        raw = body.get(field) or []
        if isinstance(raw, list):
            for row in raw:
                if isinstance(row, dict):
                    rows.append((kind, dict(row)))
    return rows


@dataclass
class VersionRow:
    id: str
    tenant_id: str
    space_id: str | None
    source: str
    provenance: dict[str, Any]
    created_by: str
    ts: str


@dataclass
class ItemRow:
    id: str
    tenant_id: str
    version_id: str
    kind: str
    payload: dict[str, Any]
    status: str
    source: str
    provenance: dict[str, Any]
    created_by: str
    approved_by: str | None
    ts: str
    approval_ref: str | None = None


def _public_item(row: ItemRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "version_id": row.version_id,
        "kind": row.kind,
        "payload": dict(row.payload),
        "status": row.status,
        "source": row.source,
        "provenance": dict(row.provenance),
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "approval_ref": row.approval_ref,
        "ts": row.ts,
    }


def _diff_rows(
    versions: Mapping[str, VersionRow],
    items: Mapping[str, ItemRow],
    version_id: str | None,
) -> dict[str, Any]:
    proposed = [row for row in items.values() if row.status == "proposed"]
    if version_id is None and versions:
        version_id = max(versions.values(), key=lambda row: (row.ts, row.id)).id
    if version_id is not None:
        proposed = [row for row in proposed if row.version_id == version_id]
    approved = [row for row in items.values() if row.status == "approved"]
    approved_by_id = {item_identity(row.kind, row.payload): row for row in approved}
    proposed_ids: set[str] = set()
    proposed_only: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    same: list[dict[str, Any]] = []
    for row in proposed:
        ident = item_identity(row.kind, row.payload)
        proposed_ids.add(ident)
        other = approved_by_id.get(ident)
        public = _public_item(row)
        if other is None:
            proposed_only.append(public)
        elif _canon(other.payload) != _canon(row.payload):
            changed.append(public)
        else:
            same.append(public)
    approved_only = [
        _public_item(row)
        for row in approved
        if item_identity(row.kind, row.payload) not in proposed_ids
    ]
    return {
        "version_id": version_id,
        "proposed_only": proposed_only,
        "approved_only": approved_only,
        "changed": changed,
        "same": same,
    }


def _uuid_text(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return str(UUID(raw))
    except ValueError:
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    return str(value)


def _approval_ref(approval_ref: str | None) -> str:
    """The caller's text when it is non-empty, otherwise one generated id."""
    text = (approval_ref or "").strip()
    return text or str(uuid4())


def _approval_provenance(ref: str, actor: str) -> dict[str, str]:
    return {"approval_ref": ref, "approved_by": actor}


def ensure_tenant_row(conn: psycopg.Connection, tenant_id: str) -> str:
    """Insert ``dms.tenants`` when this uuid is missing. Return the uuid.

    The slug is the uuid with a prefix, so two tenants cannot share it.
    This writes the tenant row only. No user, space, or ontology payload.
    """
    tenant = str(UUID(str(tenant_id)))
    conn.execute(
        """
        INSERT INTO dms.tenants (id, slug, name)
        VALUES (%s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (tenant, "t-" + tenant, tenant),
    )
    return tenant


class OntologyDataStore:
    def __init__(self) -> None:
        self._versions: dict[str, VersionRow] = {}
        self._items: dict[str, ItemRow] = {}
        self._denied: set[str] = set()

    def deny_steward(self, actor_id: str) -> None:
        self._denied.add(actor_id)

    def holds_steward_grant(self, *, role: str, actor_id: str) -> bool:
        """Deployment steward is granted until ``deny_steward`` removes them."""
        if (role or "").strip().lower() != "steward":
            return False
        if not (actor_id or "").strip():
            return False
        return actor_id not in self._denied

    def store_proposal(
        self,
        body: Mapping[str, Any],
        *,
        created_by: str,
        space_id: str | None = None,
        tenant_id: str = "",
    ) -> dict[str, Any]:
        if body.get("served"):
            raise ValueError("an ontology proposal is never served")
        status = str(body.get("status") or "")
        if status != "proposed":
            return {
                "status": status or "off",
                "served": False,
                "version_id": None,
                "items": [],
            }
        version = VersionRow(
            id=str(uuid4()),
            tenant_id=tenant_id,
            space_id=space_id,
            source="ao-vendor",
            provenance={
                "vendor_sha": str(body.get("vendor_sha") or ""),
                "served": False,
            },
            created_by=created_by,
            ts=_now(),
        )
        self._versions[version.id] = version
        stored: list[dict[str, Any]] = []
        for kind, payload in items_from_proposal(body):
            if kind not in KINDS:
                continue
            row = ItemRow(
                id=str(uuid4()),
                tenant_id=tenant_id,
                version_id=version.id,
                kind=kind,
                payload=dict(payload),
                status="proposed",
                source="ao-vendor",
                provenance={"vendor_sha": str(body.get("vendor_sha") or "")},
                created_by=created_by,
                approved_by=None,
                ts=_now(),
            )
            self._items[row.id] = row
            stored.append(_public_item(row))
        return {
            "status": "proposed",
            "served": False,
            "version_id": version.id,
            "items": stored,
        }

    def get_item(self, item_id: str) -> ItemRow | None:
        return self._items.get(item_id)

    def approve(self, item_id: str, *, actor: str) -> ItemRow | None:
        row = self._items.get(item_id)
        if row is None:
            return None
        row.status = "approved"
        row.approved_by = actor
        row.approval_ref = str(uuid4())
        row.ts = _now()
        return row

    def approve_version(
        self, version_id: str, actor: str, approval_ref: str | None = None
    ) -> int:
        """Approve every proposed item of one version. One in-memory pass.

        Unknown version raises ``KeyError``. A version with no proposed items
        returns 0. Every item written here shares one ``approval_ref``.
        A non-empty ``approval_ref`` is stored as given. Provenance records
        the same text and the actor.
        """
        if version_id not in self._versions:
            raise KeyError(version_id)
        ref = _approval_ref(approval_ref)
        now = _now()
        stamp = _approval_provenance(ref, actor)
        count = 0
        for row in self._items.values():
            if row.version_id == version_id and row.status == "proposed":
                row.status = "approved"
                row.approved_by = actor
                row.approval_ref = ref
                row.provenance = {**row.provenance, **stamp}
                row.ts = now
                count += 1
        return count

    def revoke(self, item_id: str, *, actor: str) -> ItemRow | None:
        row = self._items.get(item_id)
        if row is None:
            return None
        row.status = "revoked"
        row.provenance = {**row.provenance, "revoked_by": actor}
        row.ts = _now()
        return row

    def approved_items(self) -> list[dict[str, Any]]:
        rows = [row for row in self._items.values() if row.status == "approved"]
        rows.sort(key=lambda row: (row.ts, row.id))
        return [_public_item(row) for row in rows]

    def revoked_items(self) -> list[dict[str, Any]]:
        rows = [row for row in self._items.values() if row.status == "revoked"]
        rows.sort(key=lambda row: (row.ts, row.id))
        return [_public_item(row) for row in rows]

    def diff(self, version_id: str | None = None) -> dict[str, Any]:
        return _diff_rows(self._versions, self._items, version_id)


class PostgresOntologyDataStore:
    """``dms.ontology_versions`` and ``dms.ontology_items`` under RLS.

    Each method opens its own connection, sets ``dms.tenant_id`` for that
    transaction, and commits. A proposal stored on one connection is visible
    to ``approve_version`` and ``approved_items`` on later connections.
    """

    def __init__(self, conninfo: str, *, tenant_id: UUID | str) -> None:
        self._conninfo = conninfo
        self._tenant_id = str(UUID(str(tenant_id)))
        self._denied: set[str] = set()

    def deny_steward(self, actor_id: str) -> None:
        self._denied.add(actor_id)

    def holds_steward_grant(self, *, role: str, actor_id: str) -> bool:
        if (role or "").strip().lower() != "steward":
            return False
        if not (actor_id or "").strip():
            return False
        return actor_id not in self._denied

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._conninfo, connect_timeout=_CONNECT_TIMEOUT_S)

    def _tenant(self, tenant_id: str | None) -> str:
        return _uuid_text(tenant_id) or self._tenant_id

    def store_proposal(
        self,
        body: Mapping[str, Any],
        *,
        created_by: str,
        space_id: str | None = None,
        tenant_id: str = "",
    ) -> dict[str, Any]:
        if body.get("served"):
            raise ValueError("an ontology proposal is never served")
        status = str(body.get("status") or "")
        if status != "proposed":
            return {
                "status": status or "off",
                "served": False,
                "version_id": None,
                "items": [],
            }
        tenant = self._tenant(tenant_id)
        created = _uuid_text(created_by)
        space = _uuid_text(space_id)
        now = datetime.now(UTC)
        ts = _iso(now)
        version = VersionRow(
            id=str(uuid4()),
            tenant_id=tenant,
            space_id=space,
            source="ao-vendor",
            provenance={
                "vendor_sha": str(body.get("vendor_sha") or ""),
                "served": False,
            },
            created_by=created_by,
            ts=ts,
        )
        rows: list[ItemRow] = []
        for kind, payload in items_from_proposal(body):
            if kind not in KINDS:
                continue
            rows.append(
                ItemRow(
                    id=str(uuid4()),
                    tenant_id=tenant,
                    version_id=version.id,
                    kind=kind,
                    payload=dict(payload),
                    status="proposed",
                    source="ao-vendor",
                    provenance={"vendor_sha": str(body.get("vendor_sha") or "")},
                    created_by=created_by,
                    approved_by=None,
                    ts=ts,
                )
            )
        conn = self._connect()
        try:
            ensure_tenant_row(conn, tenant)
            set_tenant_context(conn, tenant, role="steward")
            conn.execute(
                """
                INSERT INTO dms.ontology_versions (
                  id, tenant_id, space_id, source, provenance, created_by, ts
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    version.id,
                    tenant,
                    space,
                    version.source,
                    json.dumps(version.provenance),
                    created,
                    now,
                ),
            )
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO dms.ontology_items (
                      id, tenant_id, version_id, kind, payload, status, source,
                      provenance, created_by, approved_by, approval_ref, ts
                    ) VALUES (
                      %s, %s, %s, %s, %s::jsonb, 'proposed', %s,
                      %s::jsonb, %s, NULL, NULL, %s
                    )
                    """,
                    (
                        row.id,
                        tenant,
                        row.version_id,
                        row.kind,
                        json.dumps(row.payload),
                        row.source,
                        json.dumps(row.provenance),
                        created,
                        now,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            "status": "proposed",
            "served": False,
            "version_id": version.id,
            "items": [_public_item(row) for row in rows],
        }

    def approve(self, item_id: str, *, actor: str) -> ItemRow | None:
        item = _uuid_text(item_id)
        if item is None:
            return None
        tenant = self._tenant(None)
        ref = str(uuid4())
        now = datetime.now(UTC)
        conn = self._connect()
        try:
            set_tenant_context(conn, tenant, role="steward")
            fetched = conn.execute(
                f"""
                UPDATE dms.ontology_items
                   SET status = 'approved',
                       approved_by = %s,
                       approval_ref = %s,
                       ts = %s
                 WHERE id = %s
                RETURNING {_ITEM_COLUMNS}
                """,
                (actor, ref, now, item),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if fetched is None:
            return None
        return _item_from_db(fetched)

    def approve_version(
        self, version_id: str, actor: str, approval_ref: str | None = None
    ) -> int:
        """Approve every proposed item of one version in one transaction.

        Unknown version raises ``KeyError``. A version with no proposed items
        returns 0. The version row is locked, then every proposed item is
        updated with the same ``approved_by`` and ``approval_ref``.
        A non-empty ``approval_ref`` is stored as given. Provenance records
        the same text and the actor.
        """
        try:
            version = str(UUID(str(version_id)))
        except ValueError:
            raise KeyError(version_id) from None
        tenant = self._tenant(None)
        ref = _approval_ref(approval_ref)
        now = datetime.now(UTC)
        stamp = json.dumps(_approval_provenance(ref, actor))
        conn = self._connect()
        try:
            set_tenant_context(conn, tenant, role="steward")
            found = conn.execute(
                """
                SELECT id FROM dms.ontology_versions
                 WHERE id = %s
                   FOR UPDATE
                """,
                (version,),
            ).fetchone()
            if found is None:
                raise KeyError(version_id)
            updated = conn.execute(
                """
                UPDATE dms.ontology_items
                   SET status = 'approved',
                       approved_by = %s,
                       approval_ref = %s,
                       provenance = COALESCE(provenance, '{}'::jsonb) || %s::jsonb,
                       ts = %s
                 WHERE version_id = %s
                   AND tenant_id = %s
                   AND status = 'proposed'
                """,
                (actor, ref, stamp, now, version, tenant),
            )
            count = int(updated.rowcount)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return count

    def revoke(self, item_id: str, *, actor: str) -> ItemRow | None:
        item = _uuid_text(item_id)
        if item is None:
            return None
        tenant = self._tenant(None)
        now = datetime.now(UTC)
        conn = self._connect()
        try:
            set_tenant_context(conn, tenant, role="steward")
            current = conn.execute(
                f"""
                SELECT {_ITEM_COLUMNS}
                  FROM dms.ontology_items
                 WHERE id = %s
                """,
                (item,),
            ).fetchone()
            if current is None:
                conn.rollback()
                return None
            row = _item_from_db(current)
            provenance = {**row.provenance, "revoked_by": actor}
            fetched = conn.execute(
                f"""
                UPDATE dms.ontology_items
                   SET status = 'revoked',
                       provenance = %s::jsonb,
                       ts = %s
                 WHERE id = %s
                RETURNING {_ITEM_COLUMNS}
                """,
                (json.dumps(provenance), now, row.id),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if fetched is None:
            return None
        return _item_from_db(fetched)

    def approved_items(self) -> list[dict[str, Any]]:
        return [_public_item(row) for row in self._items_by_status("approved")]

    def revoked_items(self) -> list[dict[str, Any]]:
        return [_public_item(row) for row in self._items_by_status("revoked")]

    def diff(self, version_id: str | None = None) -> dict[str, Any]:
        tenant = self._tenant(None)
        conn = self._connect()
        try:
            set_tenant_context(conn, tenant, role="steward")
            versions = {
                row.id: row
                for row in (
                    _version_from_db(record)
                    for record in conn.execute(
                        """
                        SELECT id::text, tenant_id::text, space_id::text, source,
                               provenance, created_by::text, ts
                          FROM dms.ontology_versions
                        """
                    ).fetchall()
                )
            }
            items = {
                row.id: row
                for row in (
                    _item_from_db(record)
                    for record in conn.execute(
                        f"SELECT {_ITEM_COLUMNS} FROM dms.ontology_items"
                    ).fetchall()
                )
            }
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return _diff_rows(versions, items, version_id)

    def _items_by_status(self, status: str) -> list[ItemRow]:
        tenant = self._tenant(None)
        conn = self._connect()
        try:
            set_tenant_context(conn, tenant, role="steward")
            fetched = conn.execute(
                f"""
                SELECT {_ITEM_COLUMNS}
                  FROM dms.ontology_items
                 WHERE status = %s
                 ORDER BY ts, id
                """,
                (status,),
            ).fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return [_item_from_db(record) for record in fetched]


def _version_from_db(record: tuple[Any, ...]) -> VersionRow:
    return VersionRow(
        id=str(record[0]),
        tenant_id=str(record[1] or ""),
        space_id=None if record[2] is None else str(record[2]),
        source=str(record[3]),
        provenance=_as_dict(record[4]),
        created_by=str(record[5] or ""),
        ts=_iso(record[6]),
    )


def _item_from_db(record: tuple[Any, ...]) -> ItemRow:
    approved_by = record[9]
    approval_ref = record[10]
    return ItemRow(
        id=str(record[0]),
        tenant_id=str(record[1] or ""),
        version_id=str(record[2]),
        kind=str(record[3]),
        payload=_as_dict(record[4]),
        status=str(record[5]),
        source=str(record[6]),
        provenance=_as_dict(record[7]),
        created_by=str(record[8] or ""),
        approved_by=None if approved_by is None else str(approved_by),
        ts=_iso(record[11]),
        approval_ref=None if approval_ref is None else str(approval_ref),
    )


_MEMORY = OntologyDataStore()
_PG: PostgresOntologyDataStore | None = None
_PG_KEY: tuple[str, str] | None = None


def onto_pg_enabled() -> bool:
    """True only when ``DMS_ONTO_PG`` is the digit 1. Unset is off."""
    return os.environ.get("DMS_ONTO_PG", "0").strip() == "1"


def ontology_data_store() -> OntologyDataStore | PostgresOntologyDataStore:
    """In-memory store, or Postgres when the flag is on and a URL is bound.

    ``DMS_ONTO_PG`` defaults off. A set ``DATABASE_URL`` alone does not switch
    the store. With the flag on and no URL, the store stays in memory.
    """
    global _PG, _PG_KEY
    url = os.environ.get("DATABASE_URL", "").strip()
    if not onto_pg_enabled() or not url:
        return _MEMORY
    tenant = os.environ.get("DMS_TENANT_ID", "").strip() or _DEFAULT_TENANT
    key = (url, tenant)
    if _PG is None or _PG_KEY != key:
        _PG = PostgresOntologyDataStore(url, tenant_id=tenant)
        _PG_KEY = key
    return _PG


def reset_ontology_data_store() -> None:
    global _MEMORY, _PG, _PG_KEY
    with _LOCK:
        _MEMORY = OntologyDataStore()
        _PG = None
        _PG_KEY = None


def approved_items() -> list[dict[str, Any]]:
    return ontology_data_store().approved_items()


def revoked_items() -> list[dict[str, Any]]:
    return ontology_data_store().revoked_items()


def store_proposal(
    body: Mapping[str, Any],
    *,
    created_by: str,
    space_id: str | None = None,
    tenant_id: str = "",
) -> dict[str, Any]:
    return ontology_data_store().store_proposal(
        body,
        created_by=created_by,
        space_id=space_id,
        tenant_id=tenant_id,
    )
