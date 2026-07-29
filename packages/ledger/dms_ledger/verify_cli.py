"""CLI: verify Cortex ledger chain; report first break.

Usage: dms-verify-ledger [--base-url URL]
"""

from __future__ import annotations

import argparse
import os
import sys

from cortex_client import CortexClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Cortex ledger chain")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CORTEX_URL", "http://127.0.0.1:8010"),
        help="Cortex HTTP base URL",
    )
    args = parser.parse_args(argv)
    client = CortexClient(args.base_url)
    try:
        result = client.verify_ledger()
    except NotImplementedError as exc:
        print(f"verify-ledger unavailable until sync-contract: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI surface
        print(f"verify-ledger failed: {exc}", file=sys.stderr)
        return 1

    if result.get("ok"):
        print("ledger ok")
        return 0
    first_break = result.get("first_break")
    print(f"ledger break at: {first_break}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
