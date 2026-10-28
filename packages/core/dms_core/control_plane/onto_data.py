"""Revocable ontology items (ONTO-DATA-01).

Rows match alembic ``0007_ontology_data`` (revision 0003 is already
``document_chunks``). ``Ontology.from_store`` reads ``approved_items``.

ponytail: in-memory, one process, gone on restart. Upgrade path: a Postgres
store with these methods over ``dms.ontology_versions`` and
``dms.ontology_items`` when ``DATABASE_URL`` is bound.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

KINDS = ("object", "link", "measure", "grain", "pii")
STATUSES = ("proposed", "approved", "revoked")

_LOCK = threading.Lock()


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
        "ts": row.ts,
    }


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
        row.ts = _now()
        return row

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
        proposed = [row for row in self._items.values() if row.status == "proposed"]
        if version_id is None and self._versions:
            version_id = max(self._versions.values(), key=lambda row: (row.ts, row.id)).id
        if version_id is not None:
            proposed = [row for row in proposed if row.version_id == version_id]
        approved = [row for row in self._items.values() if row.status == "approved"]
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


_STORE = OntologyDataStore()


def ontology_data_store() -> OntologyDataStore:
    return _STORE


def reset_ontology_data_store() -> None:
    global _STORE
    with _LOCK:
        _STORE = OntologyDataStore()


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
