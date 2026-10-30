"""Register curated CEO L0 questions as Studio verified queries (EPIC-019 #38).

SQL is for the lake Chat executes (DMS_WAREHOUSE_DB). Cortex pack YAML uses
a different grain (txn_type=OUT, suppliers.country, inventory.category,
locations.location_code). Seeding that text here 503s or empty-abstains.

  python scripts/seed_curated_vqs.py --clear
  python scripts/seed_curated_vqs.py --url http://127.0.0.1:8090
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import duckdb
import httpx

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
ROOT = Path(__file__).resolve().parents[1]

ASSETS: list[dict[str, Any]] = [
    {
        "id": "cq_sales_top5_value",
        "space_id": FINANCE,
        "question": "Top 5 selling SKUs by revenue",
        "need": {"transactions": ("sku", "txn_type", "quantity_kg", "unit_cost_myr")},
        "sql": (
            "SELECT sku, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS sales_value_myr "
            "FROM transactions WHERE txn_type = 'outbound' "
            "GROUP BY sku ORDER BY sales_value_myr DESC, sku ASC LIMIT 5"
        ),
        "synonyms": [
            "Top 5 selling SKUs by sales",
            "Top 5 SKUs by revenue",
            "best 5 selling SKUs by revenue",
        ],
    },
    {
        "id": "cq_sku_count",
        "space_id": FINANCE,
        "question": "How many SKUs do we have in inventory?",
        "need": {"inventory": ("sku",)},
        "sql": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
        "synonyms": [
            "How many SKUs in inventory?",
            "SKU count in inventory",
            "how many distinct SKUs",
        ],
    },
    {
        "id": "cq_capacity_utilisation",
        "space_id": FINANCE,
        "question": "Show warehouse capacity utilisation",
        "need": {"locations": ("name", "current_load_kg", "capacity_kg")},
        "sql": (
            "SELECT name, ROUND(100.0 * current_load_kg / capacity_kg, 1) AS pct_used "
            "FROM locations"
        ),
        "synonyms": ["how full are the warehouses", "warehouse capacity utilisation"],
    },
    {
        "id": "cq_sales_top3_volume",
        "space_id": FINANCE,
        "question": "Top 3 SKUs by quantity sold",
        "need": {"transactions": ("sku", "txn_type", "quantity_kg")},
        "sql": (
            "SELECT sku, SUM(quantity_kg) AS total_sold_kg "
            "FROM transactions WHERE txn_type = 'outbound' "
            "GROUP BY sku ORDER BY total_sold_kg DESC, sku ASC LIMIT 3"
        ),
        "synonyms": ["top 3 by quantity sold", "top three SKUs by kg sold"],
    },
    {
        "id": "cq_low_stock_wh_a",
        "space_id": FINANCE,
        "question": "Which SKUs are below reorder level in warehouse A?",
        "need": {
            "inventory": ("sku", "quantity_kg", "reorder_level_kg", "location_id"),
            "locations": ("location_id",),
        },
        "sql": (
            "SELECT i.sku, i.quantity_kg FROM inventory i "
            "JOIN locations l ON i.location_id = l.location_id "
            "WHERE i.quantity_kg < i.reorder_level_kg AND i.reorder_level_kg > 0 "
            "AND l.location_id = 'WH-A' ORDER BY i.quantity_kg ASC"
        ),
        "synonyms": [
            "what is running low in warehouse A",
            "which SKUs are running low in warehouse A",
        ],
    },
    {
        "id": "ops_shipment_cost",
        "space_id": OPS,
        "question": "Show shipment cost by destination",
        "need": {
            "shipments": ("cost_myr", "destination_location_id"),
            "locations": ("location_id", "name"),
        },
        "sql": (
            "SELECT l.name, SUM(s.cost_myr) AS total_cost_myr "
            "FROM shipments s JOIN locations l "
            "ON s.destination_location_id = l.location_id "
            "GROUP BY l.name"
        ),
        "synonyms": ["shipment cost by warehouse", "delivery cost by destination"],
    },
    {
        "id": "ops_delayed_count",
        "space_id": OPS,
        "question": "How many delayed shipments are there?",
        "need": {"shipments": ("status",)},
        "sql": (
            "SELECT COUNT(*) AS delayed_count FROM shipments "
            "WHERE lower(status) = 'delayed'"
        ),
        "synonyms": [
            "how many delayed shipments",
            "delayed shipment count",
        ],
    },
    {
        "id": "cq_expired_items",
        "space_id": FINANCE,
        "question": "Which items are expired?",
        "need": {"inventory": ("sku", "expiry_date")},
        "sql": (
            "SELECT sku FROM inventory "
            "WHERE expiry_date IS NOT NULL AND CAST(expiry_date AS DATE) < CURRENT_DATE"
        ),
        "synonyms": ["list expired items", "show expired stock"],
    },
    {
        "id": "cq_cold_storage",
        "space_id": FINANCE,
        "question": "Which locations are cold storage?",
        "need": {"locations": ("name", "is_cold_storage")},
        "sql": "SELECT name FROM locations WHERE is_cold_storage = true",
        "synonyms": ["which warehouses are cold storage", "cold storage locations"],
    },
    {
        "id": "cq_stock_by_bin",
        "space_id": FINANCE,
        "question": "What is total stock value by storage bin?",
        "need": {"inventory": ("storage_bin", "quantity_kg", "unit_cost_myr")},
        "sql": (
            "SELECT storage_bin, SUM(quantity_kg * unit_cost_myr) AS total_value_myr "
            "FROM inventory GROUP BY storage_bin"
        ),
        "synonyms": ["stock value by storage bin", "stock by bin"],
    },
]


def warehouse_path() -> Path:
    raw = os.environ.get("DMS_WAREHOUSE_DB")
    if raw:
        return Path(raw)
    return ROOT / "data" / "dms_demo.duckdb"


def table_cols(path: Path, table: str) -> set[str]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return {str(r[0]) for r in con.execute(f"DESCRIBE {table}").fetchall()}
    finally:
        con.close()


def schema_ok(path: Path, need: dict[str, tuple[str, ...]]) -> str | None:
    for table, cols in need.items():
        try:
            have = table_cols(path, table)
        except Exception as exc:  # noqa: BLE001
            return f"{table}: {exc}"
        missing = [c for c in cols if c not in have]
        if missing:
            return f"{table} missing {','.join(missing)}"
    return None


def clear(path: Path) -> int:
    questions = [a["question"] for a in ASSETS] + [
        "What is our total spend by supplier country?",
        "What is total stock value by category?",
        "Show SKU count by category",
        "Show top 3 categoty sales",
    ]
    con = duckdb.connect(str(path))
    try:
        before = con.execute("SELECT count(*) FROM main._verified_queries").fetchone()[0]
        placeholders = ",".join("?" * len(questions))
        con.execute(
            f"DELETE FROM main._verified_queries WHERE question IN ({placeholders})",
            questions,
        )
        after = con.execute("SELECT count(*) FROM main._verified_queries").fetchone()[0]
    finally:
        con.close()
    print(f"cleared {before} -> {after} rows in {path}")
    return 0


def seed(url: str, timeout: float, path: Path) -> int:
    base = url.rstrip("/")
    failed = 0
    skipped = 0
    for asset in ASSETS:
        reason = schema_ok(path, asset["need"])
        if reason:
            print(f"{asset['id']}\tSKIP\t{reason}")
            skipped += 1
            continue
        body = {
            "space_id": asset["space_id"],
            "question": asset["question"],
            "sql": asset["sql"],
            "synonyms": asset["synonyms"],
        }
        try:
            resp = httpx.post(
                f"{base}/v1/studio/verified-queries",
                json=body,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{asset['id']}\tERROR\t{type(exc).__name__}: {exc}")
            failed += 1
            continue
        if resp.status_code != 200:
            print(f"{asset['id']}\tHTTP {resp.status_code}\t{resp.text[:240]}")
            failed += 1
            continue
        print(f"{asset['id']}\tOK\t{resp.json().get('asset_id')}")
    print(f"seeded ok={len(ASSETS) - failed - skipped} skip={skipped} fail={failed}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=os.environ.get("DMS_URL", "http://127.0.0.1:8090"))
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--clear", action="store_true")
    p.add_argument("--warehouse", default="")
    args = p.parse_args(argv)
    path = Path(args.warehouse) if args.warehouse else warehouse_path()
    os.environ.setdefault("DMS_WAREHOUSE_DB", str(path))
    if args.clear:
        return clear(path)
    return seed(args.url, args.timeout, path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
