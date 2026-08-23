"""Copy Studio bronze into the DuckDB Cortex actually reads (S4 / TAS-DMS §6).

Ingest writes ``DMS_WAREHOUSE_DB`` (default ``<repo>/data/dms_demo.duckdb``).
Chat answers from ``CORTEX_WAREHOUSE_DB`` / ``DMS_ORACLE_WAREHOUSE`` /
``CORTEX_HOME/data/dms_demo.duckdb``. Those are often two files. This script
copies bronze user tables into the serving file so ``POST /v1/chat/ask`` can
see an upload.

Does not merge demo schemas (outbound vs OUT). Does not edit Cortex.

    python scripts/sync_bronze_to_serving.py           # copy
    python scripts/sync_bronze_to_serving.py --check   # exit 1 if bronze is missing from serving

If serving is locked (Cortex is up), stop Cortex, run this, start Cortex.
``Start-DMSStack.ps1`` runs --check/--sync before starting the engine.
"""

from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "executor"))
sys.path.insert(0, str(ROOT / "packages" / "core"))

# This script must not import Cortex. Loading dms_executor/__init__.py would.
_pkg = types.ModuleType("dms_executor")
_pkg.__path__ = [str(ROOT / "packages" / "executor" / "dms_executor")]
sys.modules.setdefault("dms_executor", _pkg)

from dms_executor.warehouse_identity import (  # noqa: E402
    discovered_engine_warehouse,
    identity_check,
    ingest_warehouse_path,
    sync_bronze_to_serving,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if ingest bronze is not visible in the serving warehouse",
    )
    parser.add_argument("--ingest", type=Path, default=None)
    parser.add_argument("--serving", type=Path, default=None)
    args = parser.parse_args()

    ingest = args.ingest or ingest_warehouse_path()
    serving = args.serving or discovered_engine_warehouse()

    if args.check:
        ok, detail = identity_check(ingest, serving)
        print(detail)
        return 0 if ok else 1

    result = sync_bronze_to_serving(ingest=ingest, serving=serving)
    print(f"{result.status} ingest={result.ingest} serving={result.serving}")
    if result.copied:
        print("copied: " + ", ".join(result.copied))
    if result.error:
        print(result.error, file=sys.stderr)
    if result.status == "no_engine":
        print("single warehouse — chat already reads the ingest file")
        return 0
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
