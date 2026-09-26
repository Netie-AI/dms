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


def _scope_chain(scope: Scope) -> list[Scope]:
    chain: list[Scope] = []
    cur: Scope | None = scope
    while cur is not None:
        chain.append(cur)
        cur = cur.parent
    return chain


def _resolve_column(
    col: exp.Column, scope: Scope, columns_of: Mapping[str, set[str]], depth: int = 0
) -> tuple[str, str] | None:
    """``(real relation, column)`` a column reads, through derived tables/CTEs.

    Qualified: the source named by its qualifier in this scope or an outer one
    (correlation). Unqualified: the only source in scope that has the column.
    A derived source resolves through its projection when that projection is a
    plain column; anything else (an expression, an aggregate) is None.
    """
    if depth > 8:
        return None
    name = col.name.lower()
    qualifier = col.table
    candidates: list[tuple[Scope, Any]] = []
    for sc in _scope_chain(scope):
        if qualifier:
            src = sc.sources.get(qualifier)
            if src is not None:
                candidates = [(sc, src)]
                break
        else:
            hits = []
            for src in sc.sources.values():
                label = _source_label(src)
                if label is not None and name in columns_of.get(label, set()):
                    hits.append((sc, src))
                elif isinstance(src, Scope) and name in _projection_names(src):
                    hits.append((sc, src))
            if hits:
                candidates = hits
                break
            if sc is scope and len(sc.sources) == 1:
                # One source: an unqualified column can only be its column.
                candidates = list((sc, src) for src in sc.sources.values())
                break
    if len(candidates) != 1:
        return None
    _sc, src = candidates[0]
    label = _source_label(src)
    if label is not None:
        return label, name
    if isinstance(src, Scope):
        return _through_projection(src, name, columns_of, depth + 1)
    return None


def _projection_names(scope: Scope) -> set[str]:
    select = scope.expression
    if not isinstance(select, exp.Select):
        return set()
    return {str(p.alias_or_name).lower() for p in select.expressions}


def _through_projection(
    scope: Scope, name: str, columns_of: Mapping[str, set[str]], depth: int
) -> tuple[str, str] | None:
    select = scope.expression
    if not isinstance(select, exp.Select):
        return None
    for proj in select.expressions:
        if str(proj.alias_or_name).lower() != name:
            continue
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(inner, exp.Column):
            return _resolve_column(inner, scope, columns_of, depth)
        return None
    return None


def _local(node: exp.Expression, scope: Scope) -> bool:
    """Whether ``node`` belongs to this scope's own query, not a nested one."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Query):
            return parent is scope.expression
        parent = parent.parent
    return False


def _pairs_in_scope(
    scope: Scope, columns_of: Mapping[str, set[str]]
) -> tuple[list[tuple[tuple[str, str], tuple[str, str]]], str | None]:
    """Cross-relation column equalities in this scope (JOIN ON, WHERE, IN (SELECT col))."""
    select = scope.expression
    pairs: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for node in select.find_all(exp.Predicate):
        if not _local(node, scope):
            continue
        if isinstance(node, exp.In) and node.args.get("query") is not None:
            query = node.args["query"]
            sub = query.this if isinstance(query, exp.Subquery) else query
            left = node.this
            if not isinstance(left, exp.Column) or not isinstance(sub, exp.Select):
                continue
            if len(sub.expressions) != 1:
                continue
            proj = sub.expressions[0]
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            sub_scope = next((s for s in traverse_scope(sub) if s.expression is sub), None)
            if not isinstance(inner, exp.Column) or sub_scope is None:
                return [], "in_subquery_not_a_column"
            a = _resolve_column(left, scope, columns_of)
            b = _resolve_column(inner, sub_scope, columns_of)
            if a is None or b is None:
                return [], "column_unresolved"
            if a[0] != b[0]:
                pairs.append((a, b))
            continue
        cols = [c for c in (node.args.get("this"), node.args.get("expression")) if c is not None]
        refs = [c for c in node.find_all(exp.Column) if _local(c, scope) or c.parent is node]
        if not refs:
            continue
        resolved = [_resolve_column(c, scope, columns_of) for c in refs]
        if any(r is None for r in resolved):
            relations = {r[0] for r in resolved if r is not None}
            if len(refs) > 1 and len(relations) != 1:
                return [], "column_unresolved"
            continue
        relations = {r[0] for r in resolved if r is not None}
        if len(relations) < 2:
            continue
        if (
            isinstance(node, exp.EQ)
            and len(cols) == 2
            and all(isinstance(c, exp.Column) for c in cols)
        ):
            a = _resolve_column(cols[0], scope, columns_of)  # type: ignore[arg-type]
            b = _resolve_column(cols[1], scope, columns_of)  # type: ignore[arg-type]
            if a is None or b is None:
                return [], "column_unresolved"
            pairs.append((a, b))
            continue
        return [], "non_equality_join"
    return pairs, None


def relation_columns(onto: Ontology, warehouse: Path | None) -> dict[str, set[str]]:
    """Lower-cased columns of every object's relation, read from the lake now."""
    out: dict[str, set[str]] = {}
    if warehouse is None or not Path(warehouse).is_file():
        return out
    con = connect_file(Path(warehouse))
    try:
        for obj in onto.objects.values():
            try:
                rows = con.execute(f"DESCRIBE SELECT * FROM {obj.relation}").fetchall()
            except Exception:  # noqa: BLE001 - unresolvable columns fail closed later
                continue
            out[obj.name.lower()] = {str(r[0]).lower() for r in rows}
    finally:
        con.close()
    return out


def unverified_join_reason(
    sql: str,
    onto: Ontology,
    violations: Sequence[Violation],
    *,
    columns_of: Mapping[str, set[str]],
) -> str | None:
    """``unverified_join:<why>`` unless every join in ``sql`` is a verified link.

    A join is a column equality between two relations (JOIN ON, WHERE, or
    ``col IN (SELECT col ...)``). It passes only when the relation pair and
    every column pair match one link the Space declared AND ``verify()``
    measured many-to-one with nothing failed on it or its ends. Relations in
    one scope that no such predicate connects (a cross product) fail too.
    Anything the analysis cannot resolve fails closed.
    """
    try:
        roots = [r for r in sqlglot.parse(sql, read=_DIALECT) if r is not None]
    except Exception:  # noqa: BLE001
        return f"{REASON_UNVERIFIED_JOIN}:parse"
    usable = usable_links(onto, violations)
    by_pair: dict[frozenset[str], list[Any]] = {}
    for name, link in onto.links.items():
        by_pair.setdefault(
            frozenset({link.from_object.lower(), link.to_object.lower()}), []
        ).append((name, link))
    for root in roots:
        try:
            scopes = traverse_scope(root)
        except Exception:  # noqa: BLE001
            return f"{REASON_UNVERIFIED_JOIN}:scope"
        for scope in scopes:
            if not isinstance(scope.expression, exp.Select):
                continue
            pairs, why = _pairs_in_scope(scope, columns_of)
            if why:
                return f"{REASON_UNVERIFIED_JOIN}:{why}"
            grouped: dict[frozenset[str], set[tuple[str, str, str, str]]] = {}
            for (ra, ca), (rb, cb) in pairs:
                grouped.setdefault(frozenset({ra, rb}), set()).add((ra, ca, rb, cb))
            joined: list[frozenset[str]] = []
            for pair, cols in grouped.items():
                reason = _check_pair(pair, cols, by_pair, usable)
                if reason:
                    return f"{REASON_UNVERIFIED_JOIN}:{reason}"
                joined.append(pair)
            real = {
                label for src in scope.sources.values() if (label := _source_label(src)) is not None
            }
            own = real | {
                f"derived:{alias}" for alias, src in scope.sources.items() if isinstance(src, Scope)
            }
            if len(own) > 1 and not _connected(own, joined, scope, columns_of):
                # Real relation names only: a derived table's alias is model text.
                named = ", ".join(sorted(real)) or "derived tables"
                return (
                    f"{REASON_UNVERIFIED_JOIN}:cross_product "
                    f"({named} not joined by a verified link)"
                )
    return None


def _check_pair(
    pair: frozenset[str],
    cols: set[tuple[str, str, str, str]],
    by_pair: Mapping[frozenset[str], list[Any]],
    usable: Mapping[str, Any],
) -> str | None:
    names = " x ".join(sorted(pair))
    candidates = by_pair.get(pair, [])
    if not candidates:
        return f"{names} has no declared link"
    wanted = {
        (child.lower(), cc, parent.lower(), pc) for (child, cc, parent, pc) in _oriented(cols)
    }
    for name, link in candidates:
        declared = {
            (link.from_object.lower(), f.lower(), link.to_object.lower(), t.lower())
            for f, t in zip(link.from_columns, link.to_columns, strict=True)
        }
        # Either orientation of each equality is the same predicate.
        flipped = {(p, pc, c, cc) for (c, cc, p, pc) in declared}
        if wanted <= (declared | flipped) and _covers(wanted, declared, flipped):
            if name not in usable:
                return f"link {name} is not verified"
            return None
    return f"{names} joined on columns no declared link names"


def _oriented(cols: set[tuple[str, str, str, str]]) -> set[tuple[str, str, str, str]]:
    return {(ra, ca.lower(), rb, cb.lower()) for (ra, ca, rb, cb) in cols}


def _covers(
    wanted: set[tuple[str, str, str, str]],
    declared: set[tuple[str, str, str, str]],
    flipped: set[tuple[str, str, str, str]],
) -> bool:
    """Every column pair of the link is present: a partial composite key fans out."""
    norm_w = {frozenset({(a, b), (c, d)}) for (a, b, c, d) in wanted}
    norm_d = {frozenset({(a, b), (c, d)}) for (a, b, c, d) in declared}
    return norm_d <= norm_w


def _connected(
    own: set[str],
    joined: Sequence[frozenset[str]],
    scope: Scope,
    columns_of: Mapping[str, set[str]],
) -> bool:
    """Whether the scope's sources form one component over verified-link joins.

    A derived source joins through the relation its column resolved to, so a
    pair naming that relation connects the ``derived:`` node too.
    """
    alias_of: dict[str, str] = {}
    for alias, src in scope.sources.items():
        if isinstance(src, Scope):
            alias_of[f"derived:{alias}"] = alias
    parent = {n: n for n in own}

    def find(n: str) -> str:
        while parent.setdefault(n, n) != n:
            n = parent[n]
        return n

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for pair in joined:
        a, b = sorted(pair)
        union(a, b)
    # A derived source's own relations were resolved to real ones; attach it to them.
    for node, alias in alias_of.items():
        inner = scope.sources[alias]
        assert isinstance(inner, Scope)
        for src in inner.sources.values():
            label = _source_label(src)
            if label is not None:
                union(node, label)
    roots = {find(n) for n in own}
    return len(roots) == 1
