"""Upload a semantic model and, unless this is a dry run, store a proposal.

The proposal status is ``proposed``. This module never approves an item.
Verified SQL is stored as a retrieval candidate, not in the replay table.
Benchmarks are stored on their own table and are not returned to the ask path.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dms_core.control_plane.onto_data import import_source, store_proposal

from dms_executor.semantic_import.flags import SemanticImportDisabled, enabled
from dms_executor.semantic_import.normalize import detect_format, load_document, text
from dms_executor.semantic_import.parse_dbt import parse_dbt
from dms_executor.semantic_import.parse_genie import parse_genie
from dms_executor.semantic_import.parse_osi import parse_osi, to_osi
from dms_executor.semantic_import.parse_snowflake import parse_snowflake
from dms_executor.semantic_import.parse_wren import parse_wren
from dms_executor.semantic_import.store import (
    approve_examples,
    benchmark_rows,
    example_rows,
    write_benchmarks,
    write_examples,
)
from dms_executor.semantic_import.validate import review

logger = logging.getLogger(__name__)

_PARSERS = {
    "snowflake": parse_snowflake,
    "osi": parse_osi,
    "genie": parse_genie,
    "dbt": parse_dbt,
    "wren": parse_wren,
}
_MAX_BYTES = 1_048_576
_AUDIT: list[dict[str, Any]] = []


def audit_records() -> list[dict[str, Any]]:
    return [dict(row) for row in _AUDIT]


def reset_audit() -> None:
    _AUDIT.clear()


def parse_bytes(raw: bytes) -> dict[str, Any]:
    return parse_document(load_document(raw))


def parse_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    kind = detect_format(doc)
    parser = _PARSERS.get(kind)
    if parser is None:
        raise ValueError("import_format_unknown")
    return parser(doc)


def import_file(
    raw: bytes,
    *,
    space_id: str,
    dry_run: bool = True,
    actor: str,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
    dialect: str | None = None,
) -> dict[str, Any]:
    if not enabled():
        raise SemanticImportDisabled
    if len(raw) > _MAX_BYTES:
        raise ValueError("file_too_large")
    started = time.perf_counter()
    model = parse_bytes(raw)
    fmt = text(model.get("format"))
    db = _warehouse(warehouse)
    dialect_name = dialect or _dialect()
    grants = _grants(space_id, db, grantable)
    reviewed = review(
        model,
        grantable=grants,
        warehouse=db,
        dialect=dialect_name,
        space_id=space_id,
    )
    version_id = None
    if not dry_run:
        version_id = _write(
            model,
            reviewed,
            space_id=space_id,
            actor=actor,
            warehouse=db,
            fmt=fmt,
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    counts = _counts(reviewed["items"])
    audit_id = uuid.uuid4().hex
    record = {
        "audit_id": audit_id,
        "action": "semantic_import",
        "actor": actor,
        "format": fmt,
        "space_id": space_id,
        "dry_run": dry_run,
        "version_id": version_id,
        "counts": counts,
        "elapsed_ms": elapsed_ms,
    }
    _AUDIT.append(record)
    logger.info(
        "semantic_import format=%s dry_run=%s items=%s elapsed_ms=%s",
        fmt,
        dry_run,
        sum(counts.values()),
        elapsed_ms,
    )
    return {
        "format": fmt,
        "dry_run": dry_run,
        "served": False,
        "version_id": version_id,
        "audit_id": audit_id,
        "elapsed_ms": elapsed_ms,
        "counts": counts,
        "items": reviewed["items"],
    }


def export_space(
    *,
    space_id: str,
    export_format: str = "osi",
    warehouse: Path | None = None,
) -> dict[str, Any]:
    if not enabled():
        raise SemanticImportDisabled
    if export_format != "osi":
        raise ValueError("export_format_unsupported")
    from dms_core.control_plane.onto_data import approved_items

    objects = []
    links = []
    measures = []
    filters = []
    instructions: list[str] = []
    onboarding: list[str] = []
    name = space_id
    description = ""
    for row in approved_items(space_id):
        if not str(row.get("source") or "").startswith("import:"):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict) or payload.get("space_id") != space_id:
            continue
        kind = row.get("kind")
        if kind == "object":
            objects.append(payload)
            if not instructions:
                instructions = [
                    line for line in payload.get("instructions") or [] if isinstance(line, str)
                ]
                onboarding = [
                    line for line in payload.get("onboarding") or [] if isinstance(line, str)
                ]
                name = text(payload.get("model_name")) or name
                description = text(payload.get("model_description"))
            for item in payload.get("filters") or []:
                if isinstance(item, dict):
                    filters.append({**item, "table": text(payload.get("name"))})
        elif kind == "link":
            links.append(payload)
        elif kind == "measure":
            measures.append(payload)
    db = _warehouse(warehouse)
    verified = [
        {
            "name": row["name"],
            "question": row["question"],
            "sql": row["sql"],
            "onboarding": False,
        }
        for row in example_rows(db, space_id)
        if row.get("status") == "approved"
    ]
    benches = [
        {"name": row["name"], "question": row["question"], "sql": row["sql"]}
        for row in benchmark_rows(db, space_id)
    ]
    model = {
        "format": "osi",
        "name": name,
        "description": description,
        "instructions": instructions,
        "entities": [_entity_from_payload(row) for row in objects],
        "relationships": [_link_from_payload(row) for row in links],
        "measures": [
            {
                "name": text(row.get("name")),
                "description": text(row.get("description")),
                "expr": text(row.get("expr")),
                "synonyms": list(row.get("synonyms") or []),
                "table": text(row.get("table")),
            }
            for row in measures
        ],
        "filters": filters,
        "verified_queries": verified,
        "onboarding": onboarding,
        "benchmarks": benches,
        "unsupported": [],
    }
    return to_osi(model)


def approve_import_examples(*, space_id: str, actor: str, warehouse: Path | None = None) -> int:
    if not enabled():
        raise SemanticImportDisabled
    return approve_examples(space_id=space_id, actor=actor, warehouse=_warehouse(warehouse))


def _write(
    model: Mapping[str, Any],
    reviewed: Mapping[str, Any],
    *,
    space_id: str,
    actor: str,
    warehouse: Path,
    fmt: str,
) -> str | None:
    source = import_source(fmt)
    _reject_stored(reviewed, warehouse)
    stored = store_proposal(
        {
            "status": "proposed",
            "served": False,
            "source": source,
            "provenance": {
                "action": "semantic_import",
                "format": fmt,
                "instructions": [line for line in rows_of(model.get("instructions"))],
                "onboarding": [line for line in rows_of(model.get("onboarding"))],
            },
            "import_items": list(reviewed.get("proposals") or []),
        },
        created_by=actor,
        space_id=space_id,
    )
    write_examples(
        list(reviewed.get("examples") or []),
        space_id=space_id,
        warehouse=warehouse,
        actor=actor,
        provenance=source,
    )
    write_benchmarks(
        list(reviewed.get("benchmarks") or []),
        space_id=space_id,
        warehouse=warehouse,
    )
    version = stored.get("version_id")
    return version if isinstance(version, str) else None


def _reject_stored(reviewed: Mapping[str, Any], warehouse: Path) -> None:
    """Listed pack SQL raises before any proposal, example, or benchmark insert."""
    from dms_executor.skills_quarantine import reject_scored_write

    for key in ("examples", "benchmarks"):
        raw_rows = reviewed.get(key) or []
        if not isinstance(raw_rows, list):
            continue
        for row in raw_rows:
            if isinstance(row, dict):
                reject_scored_write({"sql": str(row.get("sql") or "")}, warehouse=warehouse)
    proposals = reviewed.get("proposals") or []
    if isinstance(proposals, list):
        for item in proposals:
            if isinstance(item, dict):
                _reject_payload(item.get("payload"), warehouse)


def _reject_payload(payload: Any, warehouse: Path) -> None:
    from dms_executor.skills_quarantine import reject_scored_write

    if isinstance(payload, list):
        for item in payload:
            _reject_payload(item, warehouse)
        return
    if not isinstance(payload, dict):
        return
    for key in ("sql", "expr"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            reject_scored_write({"sql": raw}, warehouse=warehouse)
    for key in ("filters", "attributes"):
        _reject_payload(payload.get(key), warehouse)


def rows_of(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _entity_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    columns = []
    for attr in payload.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        columns.append(
            {
                "name": text(attr.get("source_column") or attr.get("display_name")),
                "expr": text(attr.get("expr")) or text(attr.get("source_column")),
                "description": text(attr.get("description")),
                "synonyms": list(attr.get("synonyms") or []),
                "data_type": text(attr.get("data_type")),
                "role": text(attr.get("role")) or "dimension",
            }
        )
    return {
        "name": text(payload.get("name")),
        "table": text(payload.get("relation")),
        "description": text(payload.get("description")),
        "primary_key": list(payload.get("key") or []),
        "columns": columns,
    }


def _link_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": text(payload.get("name")),
        "from_table": text(payload.get("from_object")),
        "from_columns": list(payload.get("from_columns") or []),
        "to_table": text(payload.get("to_object")),
        "to_columns": list(payload.get("to_columns") or []),
        "cardinality": text(payload.get("cardinality")) or "many_to_one",
        "join_type": text(payload.get("join_type")),
    }


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _warehouse(path: Path | None) -> Path:
    if path is not None:
        return path
    from dms_executor.demo_warehouse import warehouse_path

    return warehouse_path()


def _dialect() -> str:
    from dms_executor.demo_warehouse import SERVING_DIALECT

    return SERVING_DIALECT


def _grants(space_id: str, warehouse: Path, grantable: set[str] | None) -> set[str]:
    if grantable is not None:
        return set(grantable)
    from dms_executor import Executor

    return set(Executor(cortex=None, warehouse_path=warehouse).grantable_tables(space_id=space_id))
