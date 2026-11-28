"""Offline labels for the AI floor. No network and no provider key.

The BIRD corpus is not in this checkout and is not fetched. The self-check
runs three statements on the local demo warehouse: a matching SELECT, a
SELECT whose rows differ, and a DROP. Labels are correct, wrong, abstain.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "executor"),
    str(ROOT / "packages" / "core"),
    str(ROOT / "packages" / "cortex_client"),
    str(ROOT / "packages" / "ledger"),
    str(ROOT / "apps" / "api"),
]

from dms_executor.ai_floor import offline_label  # noqa: E402
from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse  # noqa: E402

GOLD = "SELECT COUNT(*) AS n FROM inventory"


def self_check() -> int:
    folder = Path(tempfile.mkdtemp(prefix="ai-floor-offline-"))
    db = folder / "warehouse.duckdb"
    ensure_demo_warehouse(db)
    grants = set(DEMO_TABLES)
    labels = [
        offline_label(db, GOLD, GOLD, grants),
        offline_label(db, GOLD, "SELECT COUNT(*) + 1 AS n FROM inventory", grants),
        offline_label(db, GOLD, "DROP TABLE inventory", grants),
    ]
    counts = {name: labels.count(name) for name in ("correct", "wrong", "abstain", "unscored")}
    print(
        "ai_floor_offline synthetic "
        + " ".join(f"{name}={counts[name]}" for name in ("correct", "wrong", "abstain", "unscored"))
    )
    print("bird_sample: not in checkout, not fetched")
    print("paid_keys: not used")
    expected = {"correct": 1, "wrong": 1, "abstain": 1, "unscored": 0}
    return 0 if counts == expected else 1


if __name__ == "__main__":
    raise SystemExit(self_check())
