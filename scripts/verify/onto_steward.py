"""One scripted steward approval. grade52 never calls this.

``approve-version`` prints the diff (counts by kind and the object names),
then approves every proposed item of that version. The actor must be a
uuid and must not be empty or the settings default actor
(``bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb``). ``--approval-ref`` is required
and is stored on every item and in provenance.

The landing runbook passes the founder's blanket approval:
``steward=founder Jian Hong, 2026-10-28 17:16 SGT``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("onto_steward")


def _bootstrap() -> None:
    for rel in (
        "apps/api",
        "packages/core",
        "packages/cortex_client",
        "packages/executor",
        "packages/ledger",
    ):
        entry = str(ROOT / rel)
        if entry not in sys.path:
            sys.path.insert(0, entry)


def settings_default_actor_id() -> str:
    """The Settings class default, not an env override of the live process."""
    _bootstrap()
    from dms_api.settings import Settings

    return str(Settings.model_fields["dms_actor_user_id"].default)


def require_persisted_env() -> None:
    if not os.environ.get("DATABASE_URL", "").strip():
        raise SystemExit("onto_steward: REFUSED database_url_unset")
    if os.environ.get("DMS_ONTO_PG", "0").strip() != "1":
        raise SystemExit("onto_steward: REFUSED dms_onto_pg_off")
    if not os.environ.get("DMS_TENANT_ID", "").strip():
        raise SystemExit("onto_steward: REFUSED tenant_unset")


def _actor(actor: str) -> str:
    text = (actor or "").strip()
    if not text:
        raise SystemExit("onto_steward: REFUSED actor_empty")
    try:
        parsed = str(UUID(text))
    except ValueError:
        raise SystemExit("onto_steward: REFUSED actor_not_uuid") from None
    if parsed == settings_default_actor_id():
        raise SystemExit("onto_steward: REFUSED default_actor")
    return parsed


def _approval_ref(approval_ref: str) -> str:
    text = (approval_ref or "").strip()
    if not text:
        raise SystemExit("onto_steward: REFUSED approval_ref_empty")
    return text


def summarize_diff(diff: dict[str, Any]) -> dict[str, Any]:
    """Counts by kind and the object names in this version's proposal."""
    rows: list[dict[str, Any]] = []
    for key in ("proposed_only", "changed", "same"):
        block = diff.get(key) or []
        if isinstance(block, list):
            rows.extend(row for row in block if isinstance(row, dict))
    counts: dict[str, int] = {}
    names: list[str] = []
    for row in rows:
        kind = str(row.get("kind") or "")
        if not kind:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if kind != "object":
            continue
        payload = row.get("payload")
        name = payload.get("name") if isinstance(payload, dict) else None
        if isinstance(name, str) and name:
            names.append(name)
    return {
        "version_id": diff.get("version_id"),
        "counts": counts,
        "object_names": names,
    }


def approve_version_cmd(
    *,
    version_id: str,
    actor: str,
    approval_ref: str,
    store: Any = None,
) -> dict[str, Any]:
    """Print the diff, then approve the version under ``approval_ref``.

    Refuses an empty actor, a non-uuid, and the settings default actor
    before any write. A version with nothing proposed exits non-zero
    after the diff line. The approval line is always printed when items
    are written. Provenance on each item stores the ref and the actor.
    """
    _bootstrap()
    from dms_core.control_plane.onto_data import ontology_data_store

    human = _actor(actor)
    ref = _approval_ref(approval_ref)
    target = store if store is not None else ontology_data_store()
    summary = summarize_diff(target.diff(version_id))
    counts = summary["counts"]
    names = ",".join(summary["object_names"]) or "none"
    count_text = " ".join(f"{kind}={counts[kind]}" for kind in sorted(counts)) or "none"
    print(f"diff version_id={version_id} counts {count_text} objects={names}")
    approved = target.approve_version(version_id, human, approval_ref=ref)
    if approved <= 0:
        raise SystemExit("onto_steward: FAIL no proposed items")
    line = (
        f"approved version_id={version_id} actor={human} n={approved} "
        f"approval_ref={ref!r} provenance=stored"
    )
    print(line)
    log.info(line)
    return {"version_id": version_id, "actor": human, "approval_ref": ref, "n": approved}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    approve = sub.add_parser("approve-version")
    approve.add_argument("--version-id", required=True)
    approve.add_argument("--actor", required=True)
    approve.add_argument("--approval-ref", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    if args.command == "approve-version":
        require_persisted_env()
        approve_version_cmd(
            version_id=args.version_id,
            actor=args.actor,
            approval_ref=args.approval_ref,
        )
        return 0
    raise SystemExit("onto_steward: REFUSED unknown_command")


if __name__ == "__main__":
    raise SystemExit(main())
