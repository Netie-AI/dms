"""Drain logged measure gaps. The ask does not wait on this.

Needs ``DMS_ONTO_GAP_PROPOSE=1`` and ``DMS_ONTO_PG=1``. ``--since`` is an ISO
timestamp, or ``0`` to drain every pending gap. Prints the gap id, status,
reason code, and version id. Question text and row values are not printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

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


def _enabled() -> bool:
    return (
        os.environ.get("DMS_ONTO_GAP_PROPOSE", "0").strip() == "1"
        and os.environ.get("DMS_ONTO_PG", "0").strip() == "1"
    )


def _line(row: dict[str, Any]) -> str:
    reason = row.get("reason_code") or row.get("drop_reason") or ""
    version = row.get("version_id") or ""
    return (
        f"gap_id={row.get('gap_id')} status={row.get('status')} "
        f"reason={reason} version_id={version}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    args = parser.parse_args(argv)
    if not _enabled():
        print("onto_gap: REFUSED flag_off", file=sys.stderr)
        return 2
    _bootstrap()
    from dms_executor.onto_gap import drain_since

    rows = drain_since(since=str(args.since))
    for row in rows:
        print(_line(row))
    print(f"drained {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
