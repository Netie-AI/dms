"""A SQL-source Space's own ontology: derived, verified, stored, used (ONTO-DERIVE-01).

dms#277 CONNECT-ASK-01 change 1. Before this, nothing derived or stored an
ontology for a SQL-source Space: ``verify_source_links`` measured the declared
links once, put the result in the ingest receipt and dropped it, and the ask
path for that Space ran with no ontology at all.

What this module does, and nothing more:

* **derive** from the source catalog the ingest already read: one object per
  landed table that declares a primary key, one link per declared foreign key
  whose both ends landed (no guessed joins), attributes with their landed
  types. Measures are never derived (a sum over a numeric column is not a
  metric; NEEDS_FOUNDER). Reuses ``from_manifest`` and ``Ontology.verify``.
* **verify** against the landed bronze rows and **store** the body plus the
  violations as a new snapshot of the Space's versioned ontology
  (``dms_core.control_plane.onto_store``).
* **load** the Space's stored ontology for the ask path, never another Space's
  and never the demo ontology.
* **catalog** the verified part for Cortex (``ontology.source == "space"``):
  objects with keys, links that verified, no measures unless declared.
* **refuse** generated SQL that joins two relations on anything but a
  verified link (``unverified_join``), by sqlglot scope analysis, fail closed.

The object names are the landed bronze relations (``bronze.public_schools``),
the same names the Space grants, the manifest signs and Cortex receives. The
source names (``public.schools``) ride along as ``source_table`` for display.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import sqlglot
from dms_core.control_plane.onto_store import (
    OntologySnapshot,
    OntologyStore,
    OntologyVersion,
    SchemaColumn,
    SchemaForeignKey,
    SchemaTable,
    SourceIdentity,
    schema_fingerprint,
)
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from dms_executor.demo_warehouse import connect_file
from dms_executor.ontology import Ontology, Violation, from_manifest

#: Provenance columns every bronze row carries; not attributes of the object.
PROVENANCE_COLUMNS = frozenset({"_src", "_ingest_id"})
#: One wire identifier part (Cortex ``caller_ontology.IDENT``, used with fullmatch).
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DIALECT = "duckdb"

REASON_UNVERIFIED_JOIN = "unverified_join"
REASON_NO_DECLARED_MEASURE = "no_declared_measure"
REASON_STORE_UNAVAILABLE = "ontology_store_unavailable"


class OntologyStorePort(Protocol):
    """What ``OntologyStore`` and ``PostgresOntologyStore`` both answer."""

    def reconnect(
        self,
        *,
        space_id: UUID,
        identity: SourceIdentity,
        fingerprint: str,
        created_by: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> OntologyVersion: ...

    def active_for_space(self, space_id: UUID) -> list[OntologyVersion]: ...

    def list_versions(self, space_id: UUID, identity: SourceIdentity) -> list[OntologyVersion]: ...

    def record_snapshot(
        self,
        version_id: UUID,
        *,
        body: Mapping[str, Any],
        violations: Sequence[Mapping[str, str]],
        verified: bool,
        actor: UUID | None = None,
    ) -> OntologySnapshot: ...

    def latest_snapshot(self, version_id: UUID) -> OntologySnapshot | None: ...


_STORE_LOCK = threading.Lock()
_STORE: OntologyStorePort = OntologyStore()


def ontology_store() -> OntologyStorePort:
    """The process store. In-memory unless the app bound the Postgres one."""
    with _STORE_LOCK:
        return _STORE


def set_ontology_store(store: OntologyStorePort) -> OntologyStorePort:
    """Swap the process store; returns the previous one (tests restore it)."""
    global _STORE
    with _STORE_LOCK:
        prev, _STORE = _STORE, store
    return prev


def space_uuid(space_id: str | None) -> UUID | None:
    """The Space id as a UUID, or None (the demo aliases are not SQL-source Spaces)."""
    if not space_id:
        return None
    try:
        return UUID(str(space_id))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# derive
# --------------------------------------------------------------------------


def bronze_catalog(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The source catalog renamed onto the landed bronze relations.

    ``manifest_entry`` keys tables by their source name (``public.schools``)
    with the bronze relation in ``path``. The Space reads, grants and signs the
    bronze name, so the ontology is keyed by it. A declared key or link whose
    table did not land is kept aside, named, never silently dropped.
    """
    to_bronze: dict[str, str] = {}
    tables: list[dict[str, Any]] = []
    for t in entry.get("tables") or []:
        source = f"{t['schema']}.{t['table']}"
        bronze = str(t["path"])
        schema, _, name = bronze.partition(".")
        to_bronze[source] = bronze
        tables.append(
            {
                "schema": schema,
                "table": name,
                "path": bronze,
                "truncated": bool(t.get("truncated")),
                "source_table": source,
            }
        )
    pks = {
        to_bronze[src]: list(cols)
        for src, cols in (entry.get("primary_keys") or {}).items()
        if src in to_bronze
    }
    fks: list[dict[str, Any]] = []
    unlanded: list[dict[str, str]] = []
    for fk in entry.get("foreign_keys") or []:
        child, parent = str(fk["from_table"]), str(fk["to_table"])
        if child in to_bronze and parent in to_bronze:
            fks.append(
                {
                    "name": str(fk["name"]),
                    "from_table": to_bronze[child],
                    "from_column": str(fk["from_column"]),
                    "to_table": to_bronze[parent],
                    "to_column": str(fk["to_column"]),
                }
            )
        else:
            unlanded.append({"name": str(fk["name"]), "from_table": child, "to_table": parent})
    return {
        "source": str(entry.get("source") or ""),
        "tables": tables,
        "primary_keys": pks,
        "foreign_keys": fks,
        "unlanded_links": _dedupe(unlanded),
    }


def _dedupe(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = tuple(sorted(row.items()))
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def ontology_from_catalog(catalog: Mapping[str, Any]) -> Ontology:
    """Objects from keyed tables, links from declared FKs only. Nothing verified yet."""
    return from_manifest(dict(catalog), relation_for=lambda schema, table: f"{schema}.{table}")


def _attributes(con: Any, relation: str) -> list[dict[str, str]]:
    rows = con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    return [
        {"name": str(r[0]), "type": str(r[1])} for r in rows if str(r[0]) not in PROVENANCE_COLUMNS
    ]


def derive_and_verify(
    catalog: Mapping[str, Any], *, warehouse: Path
) -> tuple[dict[str, Any], list[Violation]]:
    """Build the ontology from ``catalog`` and measure it against bronze.

    Returns the storable body and the violations. Never raises on a bad
    source: an unreadable relation is a ``relation_readable`` violation from
    ``verify()`` itself, which leaves that subject unusable.
    """
    onto = ontology_from_catalog(catalog)
    con = connect_file(Path(warehouse))
    try:
        violations = onto.verify(con)
        attributes: dict[str, list[dict[str, str]]] = {}
        for name, obj in onto.objects.items():
            try:
                attributes[name] = _attributes(con, obj.relation)
            except Exception:  # noqa: BLE001 - verify() already named it
                attributes[name] = []
    finally:
        con.close()
    return describe_body(catalog, onto, attributes), violations


def describe_body(
    catalog: Mapping[str, Any],
    onto: Ontology,
    attributes: Mapping[str, list[dict[str, str]]],
) -> dict[str, Any]:
    source_of = {str(t["path"]): str(t.get("source_table") or "") for t in catalog["tables"]}
    keyed = set(onto.objects)
    return {
        "catalog": dict(catalog),
        "objects": {
            name: {
                "relation": obj.relation,
                "source_table": source_of.get(name, ""),
                "key": list(obj.key),
                "attributes": list(attributes.get(name, [])),
            }
            for name, obj in sorted(onto.objects.items())
        },
        "links": {
            name: {
                "from": link.from_object,
                "from_columns": list(link.from_columns),
                "to": link.to_object,
                "to_columns": list(link.to_columns),
                "one_to_one": link.one_to_one,
                "cardinality": link.cardinality,
                "max_fanout": link.max_fanout,
            }
            for name, link in sorted(onto.links.items())
        },
        # Never derived. Declared measures arrive through a steward (NEEDS_FOUNDER).
        "measures": {},
        # Landed tables that declare no primary key: not objects, so no join
        # may be verified through them. Shown, not hidden.
        "unkeyed_tables": sorted(
            str(t["path"]) for t in catalog["tables"] if t["path"] not in keyed
        ),
        "unlanded_links": list(catalog.get("unlanded_links") or []),
    }


def catalog_fingerprint(body: Mapping[str, Any]) -> str:
    tables = [
        SchemaTable(
            name=name,
            columns=tuple(SchemaColumn(a["name"], a["type"]) for a in spec.get("attributes") or []),
            primary_key=tuple(spec.get("key") or ()),
        )
        for name, spec in (body.get("objects") or {}).items()
    ]
    fks = [
        SchemaForeignKey(
            name=name,
            from_table=spec["from"],
            from_columns=tuple(spec["from_columns"]),
            to_table=spec["to"],
            to_columns=tuple(spec["to_columns"]),
        )
        for name, spec in (body.get("links") or {}).items()
    ]
    return schema_fingerprint(tables, fks)


def source_identity(
    kind: str, host: str, database: str, catalog: Mapping[str, Any]
) -> SourceIdentity:
    """Kind + host + database + the source schemas that landed. No credentials."""
    schemas = sorted(
        {str(t.get("source_table") or "").partition(".")[0] for t in catalog.get("tables") or []}
        - {""}
    )
    return SourceIdentity(kind=kind, host=host, database=database, schema=",".join(schemas) or "-")


def _violation_rows(violations: Sequence[Violation]) -> list[dict[str, str]]:
    return [{"check": v.check, "subject": v.subject, "detail": v.detail} for v in violations]


def derive_and_store(
    catalog: Mapping[str, Any],
    *,
    space_id: str,
    identity: SourceIdentity,
    warehouse: Path,
    store: OntologyStorePort | None = None,
    actor: UUID | None = None,
) -> dict[str, Any]:
    """Derive, verify, store one snapshot. Returns the customer-facing receipt."""
    sid = space_uuid(space_id)
    if sid is None:
        return {
            "derived": False,
            "reason": "space_id is not a Space this store can version (not a UUID)",
        }
    body, violations = derive_and_verify(catalog, warehouse=warehouse)
    target = store or ontology_store()
    version = target.reconnect(
        space_id=sid,
        identity=identity,
        fingerprint=catalog_fingerprint(body),
        created_by=actor,
    )
    snap = target.record_snapshot(
        version.id,
        body=body,
        violations=_violation_rows(violations),
        verified=not violations,
        actor=actor,
    )
    return {"derived": True, **ontology_view(version, snap)}


# --------------------------------------------------------------------------
# show
# --------------------------------------------------------------------------


def ontology_view(version: OntologyVersion, snap: OntologySnapshot) -> dict[str, Any]:
    """Objects and links with verified/unverified status and the violation that failed.

    A subject is ``verified`` only when the latest measurement found nothing
    against it AND (for a link) a cardinality was measured. ``status`` of a
    proposed version says plainly that it is not what answers yet.
    """
    failed: dict[str, list[dict[str, str]]] = {}
    for v in snap.violations:
        failed.setdefault(v["subject"], []).append(v)
    body = snap.body
    objects = []
    for name, spec in sorted((body.get("objects") or {}).items()):
        bad = failed.get(name, [])
        objects.append(
            {
                "name": name,
                "source_table": spec.get("source_table") or "",
                "key": list(spec.get("key") or []),
                "attributes": list(spec.get("attributes") or []),
                "status": "unverified" if bad else "verified",
                "violations": bad,
            }
        )
    for name in body.get("unkeyed_tables") or []:
        objects.append(
            {
                "name": name,
                "source_table": "",
                "key": [],
                "attributes": [],
                "status": "unverified",
                "violations": [
                    {
                        "check": "no_primary_key",
                        "subject": name,
                        "detail": "the source declares no primary key, so no row "
                        "identity and no join through this table can be verified",
                    }
                ],
            }
        )
    links = []
    for name, spec in sorted((body.get("links") or {}).items()):
        bad = failed.get(name, [])
        endpoint_bad = [v for o in (spec["from"], spec["to"]) for v in failed.get(o, [])]
        measured = spec.get("cardinality") in {"many_to_one", "many_to_many"}
        ok = not bad and not endpoint_bad and measured
        links.append(
            {
                "name": name,
                "from": spec["from"],
                "from_columns": list(spec["from_columns"]),
                "to": spec["to"],
                "to_columns": list(spec["to_columns"]),
                "cardinality": spec.get("cardinality"),
                "max_fanout": spec.get("max_fanout"),
                "status": "verified" if ok else "unverified",
                "violations": bad or endpoint_bad,
            }
        )
    for fk in body.get("unlanded_links") or []:
        links.append(
            {
                "name": fk["name"],
                "from": fk["from_table"],
                "from_columns": [],
                "to": fk["to_table"],
                "to_columns": [],
                "cardinality": "unverified",
                "max_fanout": 0,
                "status": "unverified",
                "violations": [
                    {
                        "check": "not_landed",
                        "subject": fk["name"],
                        "detail": "an end of this declared foreign key was not "
                        "ingested into the Space, so it cannot be measured",
                    }
                ],
            }
        )
    return {
        "version_id": str(version.id),
        "version_status": version.status,
        "answers_asks": version.status == "active",
        "source": {
            "kind": version.identity.kind,
            "host": version.identity.host,
            "database": version.identity.database,
            "schema": version.identity.schema,
        },
        "measured_at": snap.measured_at.isoformat(),
        "verified": snap.verified,
        "objects": objects,
        "links": links,
        "measures": [],
        "measures_note": "no measures are derived; a question needing one abstains "
        "no_declared_measure until a steward declares it",
    }


def space_ontology_views(
    space_id: str, store: OntologyStorePort | None = None
) -> list[dict[str, Any]]:
    """Every source's active and proposed ontology for one Space, latest measurement each."""
    sid = space_uuid(space_id)
    if sid is None:
        return []
    target = store or ontology_store()
    out: list[dict[str, Any]] = []
    seen: set[UUID] = set()
    for active in target.active_for_space(sid):
        for version in target.list_versions(sid, active.identity):
            if version.id in seen or version.status == "superseded":
                continue
            seen.add(version.id)
            snap = target.latest_snapshot(version.id)
            if snap is not None:
                out.append(ontology_view(version, snap))
    return out


# --------------------------------------------------------------------------
# load for the ask path
# --------------------------------------------------------------------------


def stored_catalogs(space_id: str, store: OntologyStorePort | None = None) -> list[dict[str, Any]]:
    """The catalog of every active version's latest snapshot for this Space."""
    sid = space_uuid(space_id)
    if sid is None:
        return []
    target = store or ontology_store()
    out: list[dict[str, Any]] = []
    for version in target.active_for_space(sid):
        snap = target.latest_snapshot(version.id)
        if snap is not None and isinstance(snap.body.get("catalog"), dict):
            out.append(snap.body["catalog"])
    return out


def stored_catalogs_by_source(
    space_id: str, store: OntologyStorePort | None = None
) -> list[tuple[SourceIdentity, dict[str, Any]]]:
    """``(identity, catalog)`` per active version, for a re-derive without credentials."""
    sid = space_uuid(space_id)
    if sid is None:
        return []
    target = store or ontology_store()
    out: list[tuple[SourceIdentity, dict[str, Any]]] = []
    for version in target.active_for_space(sid):
        snap = target.latest_snapshot(version.id)
        if snap is not None and isinstance(snap.body.get("catalog"), dict):
            out.append((version.identity, snap.body["catalog"]))
    return out


def merge_catalogs(catalogs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "source": "",
        "tables": [],
        "primary_keys": {},
        "foreign_keys": [],
        "unlanded_links": [],
    }
    for cat in catalogs:
        merged["tables"].extend(cat.get("tables") or [])
        merged["primary_keys"].update(cat.get("primary_keys") or {})
        merged["foreign_keys"].extend(cat.get("foreign_keys") or [])
        merged["unlanded_links"].extend(cat.get("unlanded_links") or [])
    return merged


def load_space_ontology(
    space_id: str | None, store: OntologyStorePort | None = None
) -> Ontology | None:
    """The Space's stored ontology, unverified until the ask path re-measures it.

    Rebuilt from the stored catalog and left ``verified=False`` on purpose:
    ``maybe_generative_ask`` then runs ``verify()`` against the bronze rows as
    they are now, so a link that broke after the last derive is refused today,
    not trusted from yesterday's snapshot. None when the Space has none.
    Raises when the store cannot be read; the caller names that abstain.
    """
    if space_uuid(space_id) is None:
        return None
    catalogs = stored_catalogs(str(space_id), store)
    if not catalogs:
        return None
    onto = ontology_from_catalog(merge_catalogs(catalogs))
    return onto if onto.objects else None


# --------------------------------------------------------------------------
# the catalog Cortex receives
# --------------------------------------------------------------------------


#: Cortex ``caller_ontology`` limits (MAX_ONTOLOGY_BYTES, MAX_LINKS), with headroom.
WIRE_MAX_BYTES = 60 * 1024
WIRE_MAX_LINKS = 256


def budget_space_block(space: Mapping[str, Any], *, used: int) -> dict[str, Any]:
    """Objects, then links, while the whole body stays inside Cortex's limits.

    The DMS join rule still reads the full ontology; only what Cortex is told
    shrinks, and ``space_truncated`` says so.
    """
    out: dict[str, Any] = {
        "objects": {},
        "links": {},
        "measures": dict(space.get("measures") or {}),
        "verified": bool(space.get("verified")),
    }
    size = used + len(json.dumps(out)) + 64
    cut = False
    for key, limit in (("objects", None), ("links", WIRE_MAX_LINKS)):
        for name, spec in (space.get(key) or {}).items():
            cost = len(json.dumps({name: spec})) + 2
            if size + cost > WIRE_MAX_BYTES or (limit is not None and len(out[key]) >= limit):
                cut = True
                continue
            out[key][name] = spec
            size += cost
    if cut:
        out["space_truncated"] = True
    return out


def _wire_id(name: str) -> str | None:
    """A link id Cortex will accept (one identifier part), or None."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned if _IDENT.fullmatch(cleaned or "") else None


def _table_ok(name: str) -> bool:
    parts = name.split(".")
    return 1 <= len(parts) <= 2 and all(_IDENT.fullmatch(p) for p in parts)


def usable_links(onto: Ontology, violations: Sequence[Violation]) -> dict[str, Any]:
    """Links a join may use: measured many-to-one, no violation on it or its ends."""
    failed = {v.subject for v in violations}
    return {
        name: link
        for name, link in onto.links.items()
        if link.cardinality == "many_to_one"
        and name not in failed
        and link.from_object not in failed
        and link.to_object not in failed
    }


def space_catalog(
    onto: Ontology, violations: Sequence[Violation], allowed: set[str]
) -> dict[str, Any]:
    """``objects`` (keys), verified ``links``, declared ``measures`` for Cortex.

    Only objects this turn may read (``allowed``) and only links whose both
    ends are among them: a grant narrows the ontology as it narrows the tables.
    A failed object is not sent as an object; a failed link is not sent.
    """
    failed = {v.subject for v in violations}
    granted = {t.lower() for t in allowed}
    objects = {
        name: {"key": list(obj.key)}
        for name, obj in onto.objects.items()
        if name.lower() in granted
        and name not in failed
        and _table_ok(name)
        and all(_IDENT.fullmatch(k) for k in obj.key)
    }
    links: dict[str, Any] = {}
    for name, link in usable_links(onto, violations).items():
        wire = _wire_id(name)
        if wire is None or link.from_object not in objects or link.to_object not in objects:
            continue
        base, n = wire, 2
        while wire in links:  # fk-a / fk_a / fk.a must not overwrite one another
            wire, n = f"{base}_{n}", n + 1
        links[wire] = {
            "from": link.from_object,
            "to": link.to_object,
            "cardinality": link.cardinality,
        }
    measures = {
        name: {"grain": m.grain, "description": m.description}
        for name, m in onto.measures.items()
        if m.grain in objects and _IDENT.fullmatch(name)
    }
    return {
        "objects": objects,
        "links": links,
        "measures": measures,
        "verified": bool(onto.objects) and not violations,
    }


# --------------------------------------------------------------------------
# refuse a join that is not a verified link
# --------------------------------------------------------------------------


def _source_label(src: Any) -> str | None:
    if isinstance(src, exp.Table):
        parts = [p for p in (src.db, src.name) if p]
        return ".".join(parts).lower()
    return None


def relation_columns(
    onto: Ontology, warehouse: Path | None, extra: Sequence[str] = ()
) -> dict[str, set[str]]:
    """Lower-cased columns of every object's relation (and ``extra``), read now.

    Raises when the lake cannot be opened; the caller names that abstain.
    """
    out: dict[str, set[str]] = {}
    if warehouse is None or not Path(warehouse).is_file():
        return out
    relations = {obj.name.lower(): obj.relation for obj in onto.objects.values()}
    for name in extra:
        relations.setdefault(str(name).lower(), str(name))
    con = connect_file(Path(warehouse))
    try:
        for label, relation in relations.items():
            try:
                rows = con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
            except Exception:  # noqa: BLE001 - unresolvable columns fail closed later
                continue
            out[label] = {str(r[0]).lower() for r in rows}
    finally:
        con.close()
    return out


class _Refuse(Exception):
    """One join-rule refusal; ``str()`` is the tail after ``unverified_join:``."""


#: A node is one source of one scope: (id(scope), lower-cased alias). A second
#: copy of a relation under another alias is another node (self-join fan-out).
_Node = tuple[int, str]


def _sources(scope: Scope) -> dict[str, Any]:
    """Scope sources by lower-cased alias (DuckDB binds aliases case-insensitively)."""
    return {str(k).lower(): v for k, v in scope.sources.items()}


def _chain(scope: Scope) -> list[Scope]:
    out: list[Scope] = []
    cur: Scope | None = scope
    while cur is not None:
        out.append(cur)
        cur = cur.parent
    return out


def _local(node: exp.Expression, scope: Scope) -> bool:
    """Whether ``node`` belongs to this scope's own query, not a nested one."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Query):
            return parent is scope.expression
        parent = parent.parent
    return False


def _star_table(inner: Scope) -> str | None:
    """The one real relation a ``SELECT *`` derived source exposes, else None."""
    select = inner.expression
    if not isinstance(select, exp.Select):
        return None
    if not any(isinstance(p, exp.Star) for p in select.expressions):
        return None
    srcs = list(inner.sources.values())
    if len(srcs) != 1:
        return None
    return _source_label(srcs[0])


def _may_have(src: Any, name: str, columns_of: Mapping[str, set[str]]) -> bool | None:
    """True/False when known, None when this source's columns cannot be known."""
    label = _source_label(src)
    if label is not None:
        cols = columns_of.get(label)
        return None if cols is None else name in cols
    if isinstance(src, Scope):
        select = src.expression
        if not isinstance(select, exp.Select):
            return None
        names = {str(p.alias_or_name).lower() for p in select.expressions}
        if name in names:
            return True
        star = _star_table(src)
        if any(isinstance(p, exp.Star) for p in select.expressions):
            if star is None or star not in columns_of:
                return None
            return name in columns_of[star]
        return False
    return None


def _owner(col: exp.Column, scope: Scope, columns_of: Mapping[str, set[str]]) -> tuple[Scope, str]:
    """The (scope, alias) a column reads. Raises ``_Refuse`` when not provable."""
    name = col.name.lower()
    qualifier = str(col.table or "").lower()
    for sc in _chain(scope):
        srcs = _sources(sc)
        if qualifier:
            if qualifier in srcs:
                return sc, qualifier
            continue
        known = [a for a, src in srcs.items() if _may_have(src, name, columns_of) is True]
        unknown = [a for a, src in srcs.items() if _may_have(src, name, columns_of) is None]
        if len(known) == 1 and not unknown:
            return sc, known[0]
        if known or unknown:
            if len(srcs) == 1:
                return sc, next(iter(srcs))
            raise _Refuse("column_unresolved")
    raise _Refuse("column_unresolved")


def _real(
    sc: Scope, alias: str, name: str, columns_of: Mapping[str, set[str]], depth: int = 0
) -> tuple[str, str]:
    """(real relation, column) behind ``alias.name``, through derived tables/CTEs."""
    if depth > 8:
        raise _Refuse("column_unresolved")
    src = _sources(sc)[alias]
    label = _source_label(src)
    if label is not None:
        if name not in columns_of.get(label, set()):
            raise _Refuse("column_unresolved")
        return label, name
    if isinstance(src, Scope) and isinstance(src.expression, exp.Select):
        for proj in src.expression.expressions:
            if isinstance(proj, exp.Star):
                continue
            if str(proj.alias_or_name).lower() != name:
                continue
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if not isinstance(inner, exp.Column):
                raise _Refuse("column_unresolved")
            osc, oalias = _owner(inner, src, columns_of)
            return _real(osc, oalias, inner.name.lower(), columns_of, depth + 1)
        star = _star_table(src)
        if star is not None and name in columns_of.get(star, set()):
            return star, name
    raise _Refuse("column_unresolved")


def _conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node]


def _top_queries(node: exp.Expression) -> list[exp.Expression]:
    """Subqueries directly inside ``node`` (not nested in another subquery)."""
    out: list[exp.Expression] = []
    for q in node.find_all(exp.Query):
        parent = q.parent
        nested = False
        while parent is not None and parent is not node:
            if isinstance(parent, exp.Query):
                nested = True
                break
            parent = parent.parent
        if not nested:
            out.append(q)
    return out


def _inner_select(q: exp.Expression) -> exp.Select | None:
    while isinstance(q, exp.Subquery):
        q = q.this
    return q if isinstance(q, exp.Select) else None


class _JoinRule:
    def __init__(
        self,
        onto: Ontology,
        violations: Sequence[Violation],
        columns_of: Mapping[str, set[str]],
        scopes: Sequence[Scope],
    ) -> None:
        self.onto = onto
        self.usable = usable_links(onto, violations)
        self.columns_of = columns_of
        self.scope_of = {id(s.expression): s for s in scopes}
        self.by_pair: dict[frozenset[str], list[tuple[str, Any]]] = {}
        for name, link in onto.links.items():
            key = frozenset({link.from_object.lower(), link.to_object.lower()})
            self.by_pair.setdefault(key, []).append((name, link))

    # -- one link ---------------------------------------------------------

    def link_for(self, cols: set[tuple[str, str, str, str]]) -> Any:
        """The verified link every (relA, colA, relB, colB) pair together spells."""
        rels = {c[0] for c in cols} | {c[2] for c in cols}
        pair = frozenset(rels)
        names = " x ".join(sorted(rels))
        if len(rels) != 2:
            raise _Refuse(f"{names} has no declared link")
        wanted = {frozenset({(a, b), (c, d)}) for (a, b, c, d) in cols}
        candidates = self.by_pair.get(pair, [])
        if not candidates:
            raise _Refuse(f"{names} has no declared link")
        for name, link in candidates:
            declared = {
                frozenset(
                    {(link.from_object.lower(), f.lower()), (link.to_object.lower(), t.lower())}
                )
                for f, t in zip(link.from_columns, link.to_columns, strict=True)
            }
            if wanted == declared:
                if name not in self.usable:
                    raise _Refuse(f"link {name} is not verified")
                return link
        raise _Refuse(f"{names} joined on columns no declared link names")

    # -- subquery operands ------------------------------------------------

    def _uncorrelated(self, select: exp.Select) -> bool:
        """No column inside ``select`` resolves to a source outside it."""

        def inside(node: exp.Expression | None) -> bool:
            while node is not None:
                if node is select:
                    return True
                node = node.parent
            return False

        for scope in self.scope_of.values():
            if not inside(scope.expression):
                continue
            for col in scope.expression.find_all(exp.Column):
                if not _local(col, scope):
                    continue
                owner, _alias = _owner(col, scope, self.columns_of)
                if not inside(owner.expression):
                    return False
        return True

    def _subquery_conjunct(self, c: exp.Expression, scope: Scope) -> None:
        """Allowed: EXISTS, ``col IN (SELECT col ...)`` on a link, one scalar aggregate."""
        body = c.this if isinstance(c, exp.Not) else c
        if isinstance(body, exp.Exists):
            return  # its own scope proves any correlation is a verified link
        if isinstance(c, exp.In) and c.args.get("query") is not None:
            left = c.this
            sub = _inner_select(c.args["query"])
            if not isinstance(left, exp.Column) or sub is None or len(sub.expressions) != 1:
                raise _Refuse("subquery_comparison")
            proj = sub.expressions[0]
            inner_col = proj.this if isinstance(proj, exp.Alias) else proj
            inner = self.scope_of.get(id(sub))
            if not isinstance(inner_col, exp.Column) or inner is None:
                raise _Refuse("subquery_comparison")
            if not self._uncorrelated(sub) or len(inner.sources) != 1:
                raise _Refuse("subquery_comparison")
            osc, oalias = _owner(left, scope, self.columns_of)
            a = _real(osc, oalias, left.name.lower(), self.columns_of)
            isc, ialias = _owner(inner_col, inner, self.columns_of)
            b = _real(isc, ialias, inner_col.name.lower(), self.columns_of)
            self.link_for({(a[0], a[1], b[0], b[1])})
            return
        comparisons = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
        if isinstance(c, comparisons):
            sides = [c.this, c.expression]
            subs = [x for x in sides if isinstance(x, exp.Subquery)]
            if len(subs) == 1:
                sub = _inner_select(subs[0])
                if (
                    sub is not None
                    and len(sub.expressions) == 1
                    and not sub.args.get("group")
                    and isinstance(
                        (
                            sub.expressions[0].this
                            if isinstance(sub.expressions[0], exp.Alias)
                            else sub.expressions[0]
                        ),
                        exp.AggFunc,
                    )
                    and self._uncorrelated(sub)
                ):
                    return  # one uncorrelated scalar: a constant, not a join
        raise _Refuse("subquery_comparison")

    # -- one scope --------------------------------------------------------

    def check_scope(self, scope: Scope) -> None:
        select = scope.expression
        if not isinstance(select, exp.Select):
            return
        own = _sources(scope)
        conj: list[exp.Expression] = []
        for join in select.args.get("joins") or []:
            if join.args.get("using"):
                raise _Refuse("join_using")
            conj.extend(_conjuncts(join.args.get("on")))
        where = select.args.get("where")
        conj.extend(_conjuncts(where.this if where is not None else None))

        Col = tuple[str, str]  # (real relation, column)
        edges: dict[frozenset[_Node], list[tuple[_Node, Col, _Node, Col]]] = {}
        for c in conj:
            if _top_queries(c):
                self._subquery_conjunct(c, scope)
            cols = [col for col in c.find_all(exp.Column) if _local(col, scope)]
            owners: dict[int, tuple[_Node, Scope]] = {}
            for col in cols:
                sc, alias = _owner(col, scope, self.columns_of)
                owners[id(col)] = ((id(sc), alias), sc)
            if len({o[0] for o in owners.values()}) < 2:
                continue
            if not (
                isinstance(c, exp.EQ)
                and isinstance(c.this, exp.Column)
                and isinstance(c.expression, exp.Column)
            ):
                raise _Refuse("non_link_predicate")
            (na, sa), (nb, sb) = owners[id(c.this)], owners[id(c.expression)]
            ra = _real(sa, na[1], c.this.name.lower(), self.columns_of)
            rb = _real(sb, nb[1], c.expression.name.lower(), self.columns_of)
            edges.setdefault(frozenset({na, nb}), []).append((na, ra, nb, rb))

        parents: set[_Node] = set()
        joined: list[frozenset[_Node]] = []
        for pair, rows in edges.items():
            link = self.link_for({(ra[0], ra[1], rb[0], rb[1]) for _na, ra, _nb, rb in rows})
            parent_rel = link.to_object.lower()
            for na, ra, nb, rb in rows:
                if ra[0] == parent_rel:
                    parents.add(na)
                if rb[0] == parent_rel:
                    parents.add(nb)
            joined.append(pair)

        mine = {(id(scope), a) for a in own}
        if len(mine) > 1:
            root = {n: n for n in mine}

            def find(n: _Node) -> _Node:
                while root[n] != n:
                    n = root[n]
                return n

            for pair in joined:
                if pair <= mine:
                    a, b = sorted(pair)
                    root[find(a)] = find(b)
            if len({find(n) for n in mine}) != 1:
                named = sorted(
                    {lbl for a in own if (lbl := _source_label(own[a])) is not None}
                ) or ["derived tables"]
                raise _Refuse(f"cross_product ({', '.join(named)} not joined by a verified link)")
        self._fan_trap(scope, parents & mine)

    def _fan_trap(self, scope: Scope, parents: set[_Node]) -> None:
        """An aggregate over the one side of a join counts each parent once per child."""
        if not parents:
            return
        for agg in scope.expression.find_all(exp.AggFunc):
            if not _local(agg, scope) or isinstance(agg, (exp.Min, exp.Max)):
                continue
            if isinstance(agg.this, exp.Distinct):
                continue
            for col in agg.find_all(exp.Column):
                if not _local(col, scope):
                    continue
                sc, alias = _owner(col, scope, self.columns_of)
                if (id(sc), alias) in parents:
                    label = _source_label(_sources(sc)[alias]) or "a derived table"
                    raise _Refuse(
                        f"fan_trap ({label} is the one side of the join; aggregate it "
                        "with DISTINCT or before joining)"
                    )


def unverified_join_reason(
    sql: str,
    onto: Ontology,
    violations: Sequence[Violation],
    *,
    columns_of: Mapping[str, set[str]],
) -> str | None:
    """``unverified_join:<why>`` unless every join in ``sql`` is a verified link.

    Structural, fail closed. Per SELECT scope, every top-level AND conjunct of
    JOIN ON / WHERE that relates two sources (aliases, so a second copy of a
    relation is its own source) must be a plain column equality; the
    equalities between two sources together must spell exactly one declared
    link that ``verify()`` measured many-to-one with nothing failed. OR, NOT,
    functions or any other shape relating two sources refuse. A conjunct with
    a subquery operand is allowed only as EXISTS, ``col IN (SELECT col ...)``
    on a verified link, or a comparison with one uncorrelated scalar
    aggregate. Every source of a scope must be connected by verified links,
    and an aggregate over the one side of a join (other than MIN/MAX or
    DISTINCT) is a fan trap. A column that cannot be placed refuses.
    """
    try:
        roots = [r for r in sqlglot.parse(sql, read=_DIALECT) if r is not None]
    except Exception:  # noqa: BLE001
        return f"{REASON_UNVERIFIED_JOIN}:parse"
    for root in roots:
        try:
            scopes = traverse_scope(root)
        except Exception:  # noqa: BLE001
            return f"{REASON_UNVERIFIED_JOIN}:scope"
        rule = _JoinRule(onto, violations, columns_of, scopes)
        try:
            for scope in scopes:
                rule.check_scope(scope)
        except _Refuse as why:
            return f"{REASON_UNVERIFIED_JOIN}:{why}"
        except Exception:  # noqa: BLE001 - an analysis we cannot finish proves nothing
            return f"{REASON_UNVERIFIED_JOIN}:unanalysable"
    return None


def check_sql_against_space(
    sql: str, onto: Ontology, warehouse: Path | None, extra: Sequence[str] = ()
) -> str | None:
    """Verify ``onto`` against the lake now, then the join rule. Fail closed."""
    try:
        con = connect_file(Path(warehouse)) if warehouse is not None else None
        if con is None:
            return f"{REASON_UNVERIFIED_JOIN}:check_unavailable"
        try:
            violations = onto.verify(con)
        finally:
            con.close()
        cols = relation_columns(onto, warehouse, extra)
    except Exception:  # noqa: BLE001
        return f"{REASON_UNVERIFIED_JOIN}:check_unavailable"
    return unverified_join_reason(sql, onto, violations, columns_of=cols)
