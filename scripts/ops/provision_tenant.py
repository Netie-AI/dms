"""Operator command: one tenant row and its default compute pool.

Run by an operator. Never automatic. DMS_TENANT_PROVISION must be 1.
Unset, the command refuses and writes nothing. It does not create a key,
a grant, a user, a membership, or a credential. Auth and grant changes
stay on the founder 1-hour objection window and are not this command.

    DMS_TENANT_PROVISION=1 DATABASE_URL=... \\
      python scripts/ops/provision_tenant.py --tenant-id <uuid> --name "<company>"

A second run is a no-op. The database URL is read from the environment
only, and it is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _bootstrap() -> None:
    root = Path(__file__).resolve().parents[2]
    entry = str(root / "packages" / "core")
    if entry not in sys.path:
        sys.path.insert(0, entry)


def main(argv: list[str] | None = None) -> int:
    _bootstrap()
    from dms_core.control_plane.tenant_provision import (
        TenantProvisionError,
        provision_tenant,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Create one tenant row and its default compute pool. "
            "Operator-run only. Never automatic. "
            "Does not create a key, a grant, or a credential. "
            "Requires DMS_TENANT_PROVISION=1."
        )
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--slug", default=None)
    args = parser.parse_args(argv)
    try:
        out = provision_tenant(
            os.environ.get("DATABASE_URL", ""),
            args.tenant_id,
            args.name,
            slug=args.slug,
        )
    except TenantProvisionError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
