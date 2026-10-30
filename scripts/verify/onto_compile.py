"""Compile a warehouse into a proposed ontology version. No approval.

Needs ``DATABASE_URL``, ``DMS_AO_COMPILE=1``, and ``DMS_ONTO_PG=1``.
Refuses otherwise. Opens ``--warehouse``, runs ``compile_source`` through
OpenVault, and ``store_proposal`` on the persisted store for ``--tenant``.
Prints the version id, counts by kind (object, link, unkeyed), and the number
of OpenVault model calls behind the proposal.
A model-call failure (no ``ov_`` key, a 401/403, an unreachable OpenVault, an
output that does not parse) is a named refusal: exit non-zero, nothing stored.
The credential is ``OPENVAULT_API_KEY``, else ``CORTEX_API_KEY``. It is an
``ov_`` key. The DMS signing-service token is not accepted by chat.
Writes ``onto_proposal.json`` in the grade52 run directory, beside the
grader artifacts. Does not approve.

The tenant row is inserted when it is missing. No demo ontology is written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]


def _bootstrap() -> None:
    for rel in (
        "apps/api",
        "packages/core",
        "packages/cortex_client",
        "packages/executor",
        "packages/ledger",
        "packages/ao_vendor",
    ):
        entry = str(ROOT / rel)
        if entry not in sys.path:
            sys.path.insert(0, entry)


def run_dir() -> Path:
    """Same directory ``grade52`` writes, so the proposal sits with the run."""
    root = os.environ.get("RUNNER_TEMP") or os.environ.get("TMPDIR") or tempfile.gettempdir()
    path = Path(root) / "grade52"
    path.mkdir(parents=True, exist_ok=True)
    return path


def require_compile_env() -> None:
    """The landing command persists. A missing flag is a named refusal."""
    if not os.environ.get("DATABASE_URL", "").strip():
        raise SystemExit("onto_compile: REFUSED database_url_unset")
    if os.environ.get("DMS_AO_COMPILE", "0").strip() != "1":
        raise SystemExit("onto_compile: REFUSED dms_ao_compile_off")
    if os.environ.get("DMS_ONTO_PG", "0").strip() != "1":
        raise SystemExit("onto_compile: REFUSED dms_onto_pg_off")


def _tenant_uuid(tenant: str) -> str:
    text = (tenant or "").strip()
    try:
        return str(UUID(text))
    except ValueError:
        raise SystemExit("onto_compile: REFUSED tenant_not_uuid") from None


def counts_by_kind(proposal: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, int]:
    """Stored item kinds, plus ``unkeyed`` from the proposal (those are not stored)."""
    counts = {"object": 0, "link": 0, "unkeyed": len(proposal.get("unkeyed") or [])}
    for item in items:
        kind = str(item.get("kind") or "")
        if not kind or kind == "unkeyed":
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def compile_warehouse(
    warehouse: Path,
    tenant: str,
    *,
    transport: Any = None,
    store: Any = None,
    dest: Path | None = None,
) -> dict[str, Any]:
    """Compile ``warehouse`` and store the proposal. Does not approve.

    ``store`` and ``transport`` are for tests. The CLI leaves both unset and
    uses OpenVault plus ``ontology_data_store`` after the env checks.
    """
    _bootstrap()
    from ao_vendor.llm.ov_client import AoCompileRefused
    from dms_core.control_plane.onto_data import ontology_data_store, reset_ontology_data_store
    from dms_executor.ao_compile import compile_source

    tenant_id = _tenant_uuid(tenant)
    path = Path(warehouse)
    if not path.is_file():
        raise SystemExit(f"onto_compile: REFUSED warehouse_missing: {path.name}")
    import duckdb

    conn = duckdb.connect(str(path), read_only=True)
    try:
        proposal = compile_source(conn, transport=transport)
    except AoCompileRefused as exc:
        raise SystemExit(f"onto_compile: REFUSED {exc.code}") from None
    finally:
        conn.close()
    body = proposal.to_json()
    if store is None:
        os.environ["DMS_TENANT_ID"] = tenant_id
        reset_ontology_data_store()
        store = ontology_data_store()
    created_by = str(uuid4())
    stored = store.store_proposal(body, created_by=created_by, tenant_id=tenant_id)
    items = list(stored.get("items") or [])
    counts = counts_by_kind(body, items)
    result = {
        "version_id": stored.get("version_id"),
        "tenant_id": tenant_id,
        "created_by": created_by,
        "status": stored.get("status"),
        "served": False,
        "model_calls": proposal.model_calls,
        "counts": counts,
        "proposal": body,
    }
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result["path"] = str(dest)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--tenant", required=True)
    args = parser.parse_args(argv)
    require_compile_env()
    dest = run_dir() / "onto_proposal.json"
    result = compile_warehouse(args.warehouse, args.tenant, dest=dest)
    counts = result["counts"]
    print(
        f"version_id={result['version_id']} "
        f"object={counts.get('object', 0)} "
        f"link={counts.get('link', 0)} "
        f"unkeyed={counts.get('unkeyed', 0)} "
        f"model_calls={result['model_calls']}"
    )
    print(f"onto_proposal.json {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
