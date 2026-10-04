-- Attacker A extension data. Synthetic, non-personal. Applied once after the seed.
-- Purpose: make one-to-many shapes real. inventory has no key, so the same SKU can sit
-- in several warehouses (stock lots); suppliers and locations grow rows with no stock.

-- A SKU held in a second warehouse from a different supplier, a SKU in a second cold
-- warehouse, a new SKU whose quantity equals SKU-BETA's (900 kg), and a stock-only SKU.
INSERT INTO inventory VALUES
  ('SKU-ALPHA', 'WH-A', 1800, 1000, 2.30, 'SUP-04', 'PACKAGING', NULL),
  ('SKU-GAMMA', 'WH-G',   90,  300, 12.00, 'SUP-04', 'CHEMICALS', '2027-03-31'),
  ('SKU-ZETA',  'WH-D',  900,  300,  7.00, 'SUP-01', 'PARTS', NULL),
  -- a stock-only SKU in a new category: no transactions, no shipments
  ('SKU-OMEGA', 'WH-F',   40,  100,  9.00, 'SUP-06', 'TOOLS', NULL);

-- Suppliers: a third Malaysian supplier with no stock, and a second Singapore one.
INSERT INTO suppliers VALUES
  ('SUP-05', 'Harbor Chemicals', 'MY', 6, 0.30, '2026-05-01'),
  ('SUP-06', 'Lakeside Foods', 'SG', 10, 0.12, '2026-06-15');

-- Warehouses: F only holds the stock-only SKU (no transactions, no shipments); G is cold
-- storage with inbound movement only.
INSERT INTO locations VALUES
  ('WH-F', 'Warehouse F', 50000, 10000, 'WH-F', FALSE, 'CAM-F-01'),
  ('WH-G', 'Warehouse G', 70000, 20000, 'WH-G', TRUE, 'CAM-G-01');

INSERT INTO transactions VALUES
  ('T016', 'SKU-ZETA',    'WH-D', 'outbound', 140.0, 7.00, '2026-07-26 09:00:00'),
  ('T017', 'SKU-EPSILON', 'WH-G', 'inbound',  300.0, 3.25, '2026-07-27 08:00:00'),
  ('T018', 'SKU-EPSILON', 'WH-E', 'outbound',  50.0, 3.25, '2026-07-27 10:00:00'),
  ('T019', 'SKU-DELTA',   'WH-D', 'outbound',  30.0, 6.40, '2026-07-28 11:00:00'),
  ('T020', 'SKU-ZETA',    'WH-D', 'outbound',  60.0, 7.00, '2026-07-29 12:00:00');

-- More than one delayed shipment for the same SKU, and a SKU shipped to a second warehouse.
INSERT INTO shipments VALUES
  ('SH-105', 'RS622XK',   'WH-A',  90, 'delayed',    210),
  ('SH-106', 'SKU-GAMMA', 'WH-C',  60, 'delayed',    150),
  ('SH-107', 'SKU-ALPHA', 'WH-A', 300, 'in_transit', 410);

-- Two open alerts on one warehouse so alerts and stock lines both fan out per location.
INSERT INTO alerts VALUES
  ('AL-6', 'high', 'WH-B', 'Temperature drift', FALSE),
  ('AL-7', 'low',  'WH-A', 'Door left open', FALSE);
