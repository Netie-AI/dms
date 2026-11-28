# Harborline buyer walk

Ten minutes. Twelve questions. Synthetic company, not a live customer.

Harborline Distribution is a mid-size industrial wholesaler. Six regions,
two years of orders, receipts, invoices, and a nightly stock count.
Books close on 2025-12-31. Currency is USD.

Recognized revenue is fulfilled quantity times unit price
(`order_lines.line_amount`), dated by `orders.order_date`.
A late delivery is an inbound receipt with `late_days` above zero.
A stockout is a nightly snapshot with `qty_on_hand` at zero.

The figure you may say is the expected answer under each question.
That text is the SQL result. If the product abstains, show the SQL
and that answer. Do not invent a closer number.

Do not tell the buyer that receipts minus shipments equal on-hand.
The nightly count is stored. It is not a perpetual ledger.

## Before the meeting

```
python scripts/demo/seed_company.py --scale full --seed 20261128 \
  --duckdb data/demo/harborline.duckdb \
  --postgres "$DATABASE_URL"
```

Seed 20261128. Scale full. Tables 50. Rows 2393610.
Load the DuckDB file or the `harborline` schema in Postgres.
Catalog text for schema retrieval is `docs/demo/harborline_catalog.json`.
The ranker default of 480 tokens drops this catalog to an empty schema.
A budget of 4000 tokens keeps the table descriptions.
Gold rows are `tests/demo/gold.json`.

## 0:00 Open

Harborline, two years, USD. I will ask in plain language.
Every number I repeat is the query result on this database.

## 0:40 q01

Buyer: Who were our ten largest customers by revenue in 2025?

Ask it in those words. Read the top three names and figures.

```sql
SELECT c.customer_code, c.customer_name,
  ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
FROM harborline.order_lines ol
JOIN harborline.orders o ON o.order_id = ol.order_id
JOIN harborline.customers c ON c.customer_id = o.customer_id
WHERE o.order_date >= DATE '2025-01-01'
  AND o.order_date < DATE '2026-01-01'
GROUP BY c.customer_code, c.customer_name
ORDER BY revenue_usd DESC, c.customer_code
LIMIT 10
```

Expected answer:

```
Top customers by 2025 recognized revenue:
1. Pinnacle Industrial Supply (CUST-PINNACLE): 64,369,376.00 USD
2. Juniper Supply 127 (CUST-00127): 817,881.30 USD
3. Quartz Industrial 153 (CUST-00153): 810,629.55 USD
4. West Trading 239 (CUST-00239): 806,612.03 USD
5. Quartz Industrial 033 (CUST-00033): 806,567.22 USD
6. Juniper MRO 167 (CUST-00167): 805,072.41 USD
7. Quartz MRO 053 (CUST-00053): 801,301.86 USD
8. River Supply 014 (CUST-00014): 797,416.97 USD
9. Bright Industrial 260 (CUST-00260): 795,803.29 USD
10. Juniper MRO 047 (CUST-00047): 791,307.77 USD
```

## 1:20 q02

Buyer: Which ten SKUs brought in the most revenue in 2025?

Same definition of revenue. Read the top SKU code and the figure.

```sql
SELECT s.sku_code, s.sku_name,
  ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
FROM harborline.order_lines ol
JOIN harborline.orders o ON o.order_id = ol.order_id
JOIN harborline.skus s ON s.sku_id = ol.sku_id
WHERE o.order_date >= DATE '2025-01-01'
  AND o.order_date < DATE '2026-01-01'
GROUP BY s.sku_code, s.sku_name
ORDER BY revenue_usd DESC, s.sku_code
LIMIT 10
```

Expected answer:

```
Top SKUs by 2025 recognized revenue:
1. junction box 058 case (SKU-0058-CS): 9,595,871.20 USD
2. safety glasses 056 case (SKU-0056-CS): 9,529,767.20 USD
3. stretch wrap 050 case (SKU-0050-CS): 8,883,606.24 USD
4. air filter 054 case (SKU-0054-CS): 8,739,437.56 USD
5. shop towel 052 case (SKU-0052-CS): 8,602,277.28 USD
6. hex bolt 048 case (SKU-0048-CS): 7,979,062.48 USD
7. wire nut 046 case (SKU-0046-CS): 7,885,463.88 USD
8. torque bit 044 case (SKU-0044-CS): 7,462,948.36 USD
9. junction box 042 case (SKU-0042-CS): 7,222,786.40 USD
10. safety glasses 040 case (SKU-0040-CS): 6,642,780.24 USD
```

## 2:00 q03

Buyer: How did 2025 revenue break down by region?

This is the join across orders, lines, customers, and regions.

```sql
SELECT g.region_name,
  ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
FROM harborline.order_lines ol
JOIN harborline.orders o ON o.order_id = ol.order_id
JOIN harborline.customers c ON c.customer_id = o.customer_id
JOIN harborline.regions g ON g.region_id = c.region_id
WHERE o.order_date >= DATE '2025-01-01'
  AND o.order_date < DATE '2026-01-01'
GROUP BY g.region_name
ORDER BY g.region_name
```

Expected answer:

```
2025 recognized revenue by region:
Coastline: 36,901,308.17 USD
Gulfplain: 36,993,162.88 USD
Heartland: 36,979,729.50 USD
Highridge: 36,765,195.67 USD
Lakeshore: 37,238,073.36 USD
Northland: 100,381,668.07 USD
```

## 2:40 q04

Buyer: Which eight suppliers had the highest late-delivery rate in 2025?

Late means the receipt arrived after the promised date.

```sql
SELECT s.supplier_code, s.supplier_name,
  COUNT(*) AS receipts,
  SUM(CASE WHEN r.late_days > 0 THEN 1 ELSE 0 END) AS late_receipts,
  ROUND(
    100.0 * SUM(CASE WHEN r.late_days > 0 THEN 1 ELSE 0 END) / COUNT(*),
    2
  ) AS late_pct
FROM harborline.inbound_receipts r
JOIN harborline.suppliers s ON s.supplier_id = r.supplier_id
WHERE r.promised_date >= DATE '2025-01-01'
  AND r.promised_date < DATE '2026-01-01'
GROUP BY s.supplier_code, s.supplier_name
ORDER BY late_pct DESC, s.supplier_code
LIMIT 8
```

Expected answer:

```
Highest late-receipt rates in 2025:
1. Kelso Components (SUP-KELSO): 35.62 percent late (130 of 365 receipts)
2. Keystone Components 08 (SUP-0008): 8.49 percent late (31 of 365 receipts)
3. Summit Components 15 (SUP-0015): 8.49 percent late (31 of 365 receipts)
4. Elm Components 03 (SUP-0003): 8.22 percent late (30 of 365 receipts)
5. Fair Components 04 (SUP-0004): 8.22 percent late (30 of 365 receipts)
6. Juniper Components 07 (SUP-0007): 8.22 percent late (30 of 365 receipts)
7. Oak Components 11 (SUP-0011): 8.22 percent late (30 of 365 receipts)
8. Twin Components 16 (SUP-0016): 8.22 percent late (30 of 365 receipts)
```

## 3:20 q05

Buyer: Show me monthly revenue for the last two years.

Point at the winter dip and the late-year rise. Do not smooth it.

```sql
SELECT CAST(date_trunc('month', o.order_date) AS DATE) AS month_start,
  ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
FROM harborline.order_lines ol
JOIN harborline.orders o ON o.order_id = ol.order_id
GROUP BY 1
ORDER BY 1
```

Expected answer:

```
Monthly recognized revenue:
2024-01-01: 22,507,737.42 USD
2024-02-01: 21,944,752.52 USD
2024-03-01: 27,138,448.58 USD
2024-04-01: 28,394,150.09 USD
2024-05-01: 30,045,675.14 USD
2024-06-01: 24,997,914.98 USD
2024-07-01: 26,033,374.01 USD
2024-08-01: 24,625,330.06 USD
2024-09-01: 26,546,669.05 USD
2024-10-01: 30,939,140.83 USD
2024-11-01: 29,964,516.59 USD
2024-12-01: 32,282,342.44 USD
2025-01-01: 22,292,651.86 USD
2025-02-01: 21,016,729.86 USD
2025-03-01: 26,920,697.65 USD
2025-04-01: 28,278,510.19 USD
2025-05-01: 29,091,517.72 USD
2025-06-01: 25,831,557.14 USD
2025-07-01: 25,896,399.86 USD
2025-08-01: 23,686,952.80 USD
2025-09-01: 18,891,405.82 USD
2025-10-01: 20,917,554.75 USD
2025-11-01: 19,715,557.17 USD
2025-12-01: 22,719,602.83 USD
Highest month: 2024-12-01 at 32,282,342.44 USD.
Lowest month: 2025-09-01 at 18,891,405.82 USD.
```

## 4:00 q06

Buyer: Did any region fall off in the second half of 2025?

Compare July through December with the same months in 2024.

```sql
SELECT region_name, h2_2024_usd, h2_2025_usd,
  ROUND(100.0 * (h2_2025_usd - h2_2024_usd) / h2_2024_usd, 2) AS change_pct
FROM (
  SELECT g.region_name,
    ROUND(CAST(SUM(CASE
      WHEN o.order_date BETWEEN DATE '2024-07-01' AND DATE '2024-12-31'
      THEN ol.line_amount ELSE 0 END) AS NUMERIC), 2) AS h2_2024_usd,
    ROUND(CAST(SUM(CASE
      WHEN o.order_date BETWEEN DATE '2025-07-01' AND DATE '2025-12-31'
      THEN ol.line_amount ELSE 0 END) AS NUMERIC), 2) AS h2_2025_usd
  FROM harborline.order_lines ol
  JOIN harborline.orders o ON o.order_id = ol.order_id
  JOIN harborline.customers c ON c.customer_id = o.customer_id
  JOIN harborline.regions g ON g.region_id = c.region_id
  GROUP BY g.region_name
) halves
ORDER BY change_pct, region_name
```

Expected answer:

```
Second-half recognized revenue by region:
Northland: H2 2024 73,609,262.36 USD, H2 2025 35,104,786.05 USD, change -52.31 percent
Gulfplain: H2 2024 19,430,646.93 USD, H2 2025 19,190,010.64 USD, change -1.24 percent
Heartland: H2 2024 19,263,688.08 USD, H2 2025 19,158,238.16 USD, change -0.55 percent
Lakeshore: H2 2024 19,339,240.60 USD, H2 2025 19,239,327.15 USD, change -0.52 percent
Highridge: H2 2024 19,368,376.53 USD, H2 2025 19,491,540.75 USD, change 0.64 percent
Coastline: H2 2024 19,380,158.48 USD, H2 2025 19,643,570.48 USD, change 1.36 percent
Largest decline: Northland at -52.31 percent.
```

## 4:40 q07

Buyer: Why did Northland revenue drop after August 2025?

Same four months versus last year, by customer. Read the top decline.

```sql
SELECT customer_code, customer_name,
  ROUND(CAST(sep_dec_2024 AS NUMERIC), 2) AS sep_dec_2024_usd,
  ROUND(CAST(sep_dec_2025 AS NUMERIC), 2) AS sep_dec_2025_usd,
  ROUND(CAST(sep_dec_2024 - sep_dec_2025 AS NUMERIC), 2) AS decline_usd
FROM (
  SELECT c.customer_code, c.customer_name,
    SUM(CASE
      WHEN o.order_date BETWEEN DATE '2024-09-01' AND DATE '2024-12-31'
      THEN ol.line_amount ELSE 0 END) AS sep_dec_2024,
    SUM(CASE
      WHEN o.order_date BETWEEN DATE '2025-09-01' AND DATE '2025-12-31'
      THEN ol.line_amount ELSE 0 END) AS sep_dec_2025
  FROM harborline.order_lines ol
  JOIN harborline.orders o ON o.order_id = ol.order_id
  JOIN harborline.customers c ON c.customer_id = o.customer_id
  JOIN harborline.regions g ON g.region_id = c.region_id
  WHERE g.region_code = 'NL'
  GROUP BY c.customer_code, c.customer_name
) windows
ORDER BY decline_usd DESC, customer_code
LIMIT 5
```

Expected answer:

```
Largest Northland declines, Sep-Dec versus the prior year:
1. Pinnacle Industrial Supply (CUST-PINNACLE): Sep-Dec 2024 37,984,425.32 USD, Sep-Dec 2025 0.00 USD, decline 37,984,425.32 USD
2. Keystone Supply 008 (CUST-00008): Sep-Dec 2024 309,548.45 USD, Sep-Dec 2025 250,457.71 USD, decline 59,090.74 USD
3. Fair Supply 004 (CUST-00004): Sep-Dec 2024 293,432.94 USD, Sep-Dec 2025 244,936.21 USD, decline 48,496.73 USD
4. Fair Industrial 024 (CUST-00024): Sep-Dec 2024 295,581.78 USD, Sep-Dec 2025 255,163.02 USD, decline 40,418.76 USD
5. Delta Supply 002 (CUST-00002): Sep-Dec 2024 286,585.68 USD, Sep-Dec 2025 246,560.42 USD, decline 40,025.26 USD
```

## 5:20 q08

Buyer: Which supplier's deliveries have been getting later?

Compare the first half of 2024 with the second half of 2025.

```sql
SELECT supplier_code, supplier_name,
  receipts_h1_2024, late_h1_2024,
  ROUND(100.0 * late_h1_2024 / receipts_h1_2024, 2) AS late_pct_h1_2024,
  receipts_h2_2025, late_h2_2025,
  ROUND(100.0 * late_h2_2025 / receipts_h2_2025, 2) AS late_pct_h2_2025,
  ROUND(
    100.0 * late_h2_2025 / receipts_h2_2025
    - 100.0 * late_h1_2024 / receipts_h1_2024,
    2
  ) AS late_pct_point_change
FROM (
  SELECT s.supplier_code, s.supplier_name,
    SUM(CASE WHEN r.promised_date < DATE '2024-07-01' THEN 1 ELSE 0 END)
      AS receipts_h1_2024,
    SUM(CASE
      WHEN r.promised_date < DATE '2024-07-01' AND r.late_days > 0 THEN 1
      ELSE 0 END) AS late_h1_2024,
    SUM(CASE WHEN r.promised_date >= DATE '2025-07-01' THEN 1 ELSE 0 END)
      AS receipts_h2_2025,
    SUM(CASE
      WHEN r.promised_date >= DATE '2025-07-01' AND r.late_days > 0 THEN 1
      ELSE 0 END) AS late_h2_2025
  FROM harborline.inbound_receipts r
  JOIN harborline.suppliers s ON s.supplier_id = r.supplier_id
  GROUP BY s.supplier_code, s.supplier_name
) halves
ORDER BY late_pct_point_change DESC, supplier_code
LIMIT 5
```

Expected answer:

```
Suppliers whose late-receipt rate rose the most:
1. Kelso Components (SUP-KELSO): 2024 H1 9.34 percent, 2025 H2 39.67 percent, change 30.33 points
2. Keystone Components 08 (SUP-0008): 2024 H1 8.24 percent, 2025 H2 9.24 percent, change 1.00 points
3. Inland Components 06 (SUP-0006): 2024 H1 7.14 percent, 2025 H2 7.61 percent, change 0.47 points
4. West Components 19 (SUP-0019): 2024 H1 7.14 percent, 2025 H2 7.61 percent, change 0.47 points
5. Elm Components 23 (SUP-0023): 2024 H1 7.14 percent, 2025 H2 7.61 percent, change 0.47 points
```

## 6:00 q09

Buyer: Did we stock out of anything, and what did that cost us?

On-hand zero is the stockout. Dollars are units we did not ship.

```sql
SELECT s.sku_code, s.sku_name, w.warehouse_code,
  z.zero_days, z.first_day, z.last_day,
  COALESCE(m.missed_usd, 0) AS missed_usd
FROM (
  SELECT sku_id, warehouse_id,
    COUNT(*) AS zero_days,
    MIN(snapshot_date) AS first_day,
    MAX(snapshot_date) AS last_day
  FROM harborline.inventory_snapshots
  WHERE qty_on_hand = 0
  GROUP BY sku_id, warehouse_id
) z
JOIN harborline.skus s ON s.sku_id = z.sku_id
JOIN harborline.warehouses w ON w.warehouse_id = z.warehouse_id
LEFT JOIN (
  SELECT o.warehouse_id, ol.sku_id,
    ROUND(CAST(SUM(
      (ol.qty - ol.fulfilled_qty) * ol.unit_price
    ) AS NUMERIC), 2) AS missed_usd
  FROM harborline.order_lines ol
  JOIN harborline.orders o ON o.order_id = ol.order_id
  GROUP BY o.warehouse_id, ol.sku_id
) m ON m.sku_id = z.sku_id AND m.warehouse_id = z.warehouse_id
ORDER BY z.zero_days DESC, s.sku_code
LIMIT 5
```

Expected answer:

```
Longest stockouts:
Harbor Hose 25mm (HX-440) at DC-NORTH: 77 stockout days, 2025-06-02 through 2025-08-17, unfilled demand 3,778.18 USD
```

## 6:40 q10

Buyer: What should we plan for revenue next month?

Say the method before the number. Next month is January 2026. The method is January 2025 times July-December 2025 over July-December 2024.

```sql
SELECT
  ROUND(CAST(january_2025 * jul_dec_2025 / jul_dec_2024 AS NUMERIC), 2)
    AS forecast_usd,
  ROUND(CAST(january_2025 AS NUMERIC), 2) AS january_2025_usd,
  ROUND(CAST(jul_dec_2025 AS NUMERIC), 2) AS jul_dec_2025_usd,
  ROUND(CAST(jul_dec_2024 AS NUMERIC), 2) AS jul_dec_2024_usd
FROM (
  SELECT
    SUM(CASE WHEN month_start = DATE '2025-01-01' THEN revenue ELSE 0 END)
      AS january_2025,
    SUM(CASE
      WHEN month_start BETWEEN DATE '2025-07-01' AND DATE '2025-12-01'
      THEN revenue ELSE 0 END) AS jul_dec_2025,
    SUM(CASE
      WHEN month_start BETWEEN DATE '2024-07-01' AND DATE '2024-12-01'
      THEN revenue ELSE 0 END) AS jul_dec_2024
  FROM (
    SELECT CAST(date_trunc('month', o.order_date) AS DATE) AS month_start,
      SUM(ol.line_amount) AS revenue
    FROM harborline.order_lines ol
    JOIN harborline.orders o ON o.order_id = ol.order_id
    GROUP BY 1
  ) monthly
) parts
```

Expected answer:

```
January 2026 forecast: 17,247,257.97 USD. January 2025 was 22,292,651.86 USD. July-December 2025 was 131,827,473.23 USD. July-December 2024 was 170,391,372.98 USD.
```

## 7:20 q11

Buyer: Create a slide of our five biggest customers this year.

Read the title and the five bullets. This year means 2025.

```sql
SELECT customer_name, revenue_usd,
  ROUND(100.0 * revenue_usd / SUM(revenue_usd) OVER (), 2) AS share_pct
FROM (
  SELECT c.customer_name,
    ROUND(CAST(SUM(ol.line_amount) AS NUMERIC), 2) AS revenue_usd
  FROM harborline.order_lines ol
  JOIN harborline.orders o ON o.order_id = ol.order_id
  JOIN harborline.customers c ON c.customer_id = o.customer_id
  WHERE o.order_date >= DATE '2025-01-01'
    AND o.order_date < DATE '2026-01-01'
  GROUP BY c.customer_name
) ranked
ORDER BY revenue_usd DESC, customer_name
LIMIT 5
```

Expected answer:

```
Slide title: Five largest customers in 2025
Subtitle: Recognized revenue, USD
- Pinnacle Industrial Supply: 64,369,376.00 USD, 22.57 percent of 2025 revenue
- Juniper Supply 127: 817,881.30 USD, 0.29 percent of 2025 revenue
- Quartz Industrial 153: 810,629.55 USD, 0.28 percent of 2025 revenue
- West Trading 239: 806,612.03 USD, 0.28 percent of 2025 revenue
- Quartz Industrial 033: 806,567.22 USD, 0.28 percent of 2025 revenue
```

## 8:00 q12

Buyer: Which carrier delivered the most 2025 shipments, and how long did they take?

Count delivered shipments. Transit is delivered date minus ship date.

```sql
SELECT c.carrier_name,
  COUNT(*) AS shipments,
  ROUND(CAST(AVG(s.delivered_date - s.ship_date) AS NUMERIC), 2)
    AS avg_transit_days
FROM harborline.shipments s
JOIN harborline.carriers c ON c.carrier_id = s.carrier_id
WHERE s.status = 'delivered'
  AND s.ship_date >= DATE '2025-01-01'
  AND s.ship_date < DATE '2026-01-01'
GROUP BY c.carrier_name
ORDER BY shipments DESC, c.carrier_name
```

Expected answer:

```
Carriers by 2025 delivered shipments:
1. Redline Freight: 22913 shipments, average transit 2.53 days
2. Inland Parcel: 22857 shipments, average transit 3.53 days
3. Harbor Cartage: 22778 shipments, average transit 5.53 days
```

## 8:40 Close

Three things an analyst can check on this database:
- Kelso Components late receipts rise across the two years.
- SKU HX-440 is out of stock at DC-NORTH from 2025-06-02 through 2025-08-17.
- Northland falls after August 2025 because Pinnacle Industrial stops ordering.
- The stockout dollars do not explain the Northland drop.

Stop at 10:00. Offer to rerun any one question from the SQL above.
