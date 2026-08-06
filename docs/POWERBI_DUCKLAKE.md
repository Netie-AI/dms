# Power BI + DuckLake — connection recipe (closes P-DMS-24)

**Symptom:** Power BI shows **992** rows for `sales_by_sku` while Cortex/DMS catalog shows **496**.

**Root cause:** DuckLake keeps prior snapshot Parquet files under
`data/lakehouse/data/gold/<table>/ducklake-*.parquet` after a re-migrate
(`DROP` + `CREATE`). The **catalog** (`catalog.sqlite`) points at the current
file only (496). A Power BI **Folder** / `read_parquet('.../*.parquet')` union
reads **every** leftover snapshot (2 files → 992).

This is not "file + SQL manifest". It is DuckLake on-disk version retention.

## Never do this

| Anti-pattern | Why |
|--------------|-----|
| Get Data → Folder → `D:\Cortex\data\lakehouse\data\` | Unions bronze+silver+gold snapshots |
| Get Data → Parquet folder → `...\gold\sales_by_sku\` | Unions all `ducklake-*.parquet` in that folder |
| `read_parquet('**/gold/**/*.parquet')` | Same double/triple count |

## Do this instead

### A. Isolated export (demo-safe, recommended)

```http
POST /dms/brain/export
Content-Type: application/json

{ "table": "inventory", "format": "parquet", "limit": 5000 }
```

Writes **one** file under `DMS_EXPORT_DIR/exports/export_<stamp>/<table>.parquet`
(default beside `dms_demo.duckdb`). Point Power BI at **that single file**.

Implementation: `CortexOS/api/brain_routes.py` → `_export_parquet_snapshot`
("Power BI-safe — never the DuckLake folder").

### B. Catalog query (authoritative count)

```sql
ATTACH 'ducklake:sqlite:D:/Cortex/data/lakehouse/catalog.sqlite'
  AS lake (DATA_PATH 'D:/Cortex/data/lakehouse/data', READ_ONLY);

SELECT COUNT(*) FROM lake.gold.sales_by_sku;  -- expect 496 on demo
```

Use DuckDB ODBC / CLI with the ATTACH above. Never folder-connect the `data/` tree.

### C. If you must use a Parquet file

Point at **one explicit** `ducklake-<uuid>.parquet` path from the catalog, not the
folder wildcard. After `python -m scripts.lakehouse_migrate`, if the gold folder
contains more than one file, folder reads will over-count until compaction.

## Repro (engineers)

```powershell
cd D:\Cortex
python -m scripts.lakehouse_migrate   # twice
# catalog COUNT(*) = 496
# read_parquet('data/lakehouse/data/gold/sales_by_sku/*.parquet') = 992
```

Regression: `tests/dms/test_powerbi_ducklake_export.py`.
