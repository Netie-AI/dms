# SCALE-WAREHOUSE-01 -- TB warehouse design toward a PB roadmap

**Ticket:** [#237](https://github.com/Netie-AI/dms/issues/237) under [EPIC-INSIGHTS-UX #178](https://github.com/Netie-AI/dms/issues/178)
**Grains:** mapped from [SC-ONTOLOGY-01 #232](https://github.com/Netie-AI/dms/issues/232) (SKU, supplier, plant, lane, day)
**Status:** `ROADMAP_NOT_LIVE`
**Claim:** **NOT LIVE 1PB**. No production petabyte. No #178 COMPLETE.

Machine-readable layout: [`docs/schemas/scale_warehouse.yaml`](schemas/scale_warehouse.yaml).
This pack is docs/schemas only. It does not remount the founder lake
(`/var/cortex/data/dms_demo.duckdb`) and does not change serving runtime.

## Honest today vs design vs roadmap

| Horizon | What is true | Engine |
|---------|--------------|--------|
| **Live now** | Product served 91 rows. One DuckDB writer excludes readers ([P-DMS-34](../PARKING_LOT.md)). Thin seed = six demo tables. | Single-file DuckDB |
| **TB design** (this pack) | Hive-style Parquet facts partitioned by `day` + `plant_id`. Space ACL stays a session manifest, not a lake fork. | DuckDB over partitioned Parquet |
| **PB roadmap** | Iceberg + Postgres catalog; Trino after 20 TB ([SPACES.md](SPACES.md) §3). Extra `sku_hash_bucket` on movement facts. | **NOT LIVE 1PB** |

Do not quote the PB envelopes as a live estate. They are cardinality ceilings
for a later catalog/engine swap, not a measured warehouse.

## #232 grains in the warehouse

`locations` (WH-A) are warehouses. **Plant is not location.** Lane is not
"whatever `destination_location_id` happens to be." Day is a calendar date,
not a grantable table.

| #232 grain | Live today | TB layout | Grain key | Fact join |
|------------|------------|-----------|-----------|-----------|
| **SKU** | `product` derived `GROUP BY sku` from `inventory` | `dim.product` | `sku` | transaction/lot/shipment `many_to_one` |
| **supplier** | `suppliers` | `dim.supplier` | `supplier_id` | lot `many_to_one`; shipment only when `supplier_id` exists |
| **plant** | **missing** | `dim.plant` | `plant_id` | facts `many_to_one` after `plant_id` lands |
| **lane** | `shipments` dest-only; no lane key | `dim.lane` | `origin_node_id, dest_node_id` | shipment `many_to_one` after `lane_id` |
| **day** | `transactions.ts`; shipments have no date | `dim.day` | `day` | txn via `CAST(ts AS DATE)`; shipment needs `ship_date` |

`sku -> supplier` is **many_to_many** through lots. The compiler must keep
refusing a supplier roll-up of a SKU-grain measure until a verified
many_to_one path exists. That is ontology work (#232 / #234), not this pack.

## Partition keys

Storage partitions prune files. They are not Space ACL.

**TB (design):**

```
gold/fact_movement/day={YYYY-MM-DD}/plant={plant_id}/*.parquet
gold/fact_shipment/day={YYYY-MM-DD}/plant={origin_plant_id}/*.parquet
gold/fact_lot/plant={plant_id}/*.parquet
dim/{product,supplier,plant,lane,day}/*.parquet
```

| Fact | Live partitions | TB keys | PB roadmap keys (not live) |
|------|-----------------|---------|----------------------------|
| `fact_movement` (`transactions`) | none | `day`, `plant_id` | + `sku_hash_bucket` |
| `fact_shipment` (`shipments`) | none | `day`, `plant_id` | + `lane_id` |
| `fact_lot` (`inventory`) | none | `plant_id` | + `sku_hash_bucket` |

Dims stay unpartitioned at TB. Day has ~3650 rows for ten years; do not
Hive-partition the date dimension.

**PB roadmap (NOT LIVE 1PB):** same keys, Iceberg spec, catalog in Postgres
(DuckLake SQLite -> Postgres is already the Phase 4 control-plane move in
SPACES.md). Query engine stays DuckDB until a measured 20 TB+ scan needs
Trino. Cold blobs stay out of the analytical core
([DMS_TECHNICAL_ARCHITECTURE.md](../DMS_TECHNICAL_ARCHITECTURE.md) §3).

`space_id` is **never** a storage partition. Partitioning by Space forks the
lake and breaks company-default union.

## Space ACL

Law: [DR-0002](decisions/0002-space-scoping-for-the-demo-warehouse.md).
Enforcement: `intersect_space_grants` -> session manifest predicates/paths
(`packages/executor/dms_executor/acl.py`). Postgres `dms.acl_grants` stays
the parked store (P-DMS-2); this pack does not wire it.

| Scope | Tables (live + TB) | #232 grains readable |
|-------|--------------------|----------------------|
| **Finance** | locations, inventory, transactions, suppliers | sku, supplier, plant, day |
| **Warehouse Ops** | locations, inventory, shipments | sku, plant, lane, day |
| **Company default** | union of the two | still **no `alerts`** (A-0007) |

TB additions (query-time, optional):

- Site Space: `plant_id IN (:space_plants)`
- Lane Space: `lane_id IN (:space_lanes)`
- Manifest `allowed_paths` may name a Hive prefix the Space may read
  (`gold/fact_movement/day=*/plant=PL-01/**`). Prefix is a prune, not a grant
  substitute -- table grant still required.

Do not: copy the lake per Space; grant `alerts` on company default; treat a
missing `space_id` as unscoped; remount the founder lake to seed grants.

## Grain cardinality (design envelopes)

Not measured on a customer lake. Not a live row count.

| Grain | TB envelope | PB roadmap envelope |
|-------|-------------|---------------------|
| sku | 1e6 | 1e8 |
| supplier | 1e5 | 1e7 |
| plant | 1e3 | 1e4 |
| lane | 1e5 | 1e7 |
| day | 3650 (10y) | 36500 (100y) |

Fact envelopes (order-of-magnitude only): movement 1e10 TB / 1e12 PB;
shipment 1e9 / 1e11; lot 1e7 / 1e9. PB numbers are roadmap ceilings.
**NOT LIVE 1PB.**

## What this pack does not do

1. Does not claim 1PB LIVE or stamp #178 COMPLETE.
2. Does not remount `/var/cortex/data/dms_demo.duckdb` (Platform GO required).
3. Does not lift P-DMS-34 (91-row writer lock, pandas loader). TB load stays
   parked until that condition trips.
4. Does not add `plant` / `lane` / `day` to `demo_warehouse` or `demo_ontology`.
   Ask-path grains are #232 / #234.
5. Does not open a new scale epic. SPACES.md still declines petabyte ETL as
   a competition. This is a layout so a later TB customer does not invent
   partitions under a green badge.

Unlock for any LIVE TB/PB claim: Platform GO + P-DMS-34 lifted + this layout
implemented in serving + a measured lake. Until then the yaml `petabyte_live`
and `live_1pb` fields stay false.
