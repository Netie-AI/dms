-- B-family extension: synthetic, non-personal rows on the six demo tables only.
-- X1: SKU-EPSILON is also stocked at WH-A (a SKU can sit at two locations; inventory has no PK).
INSERT INTO inventory VALUES ('SKU-EPSILON', 'WH-A', 1800, 800, 3.25, 'SUP-03', 'PARTS', NULL);
-- X2: a SKU whose supplier is not yet assigned (NULL), so an INNER JOIN to suppliers drops it.
INSERT INTO inventory VALUES ('SKU-ZETA', 'WH-D', 500, 300, 7.00, NULL, 'RAW', NULL);
-- X3: a supplier on the master list that supplies no inventory SKU.
INSERT INTO suppliers VALUES ('SUP-05', 'Harbour Resins', 'VN', 14, 0.30, '2026-03-15');
-- X4: ten more delayed shipments (12 delayed in total) so a default LIMIT 10 truncates a "list all".
INSERT INTO shipments VALUES
  ('SH-110', 'SKU-ALPHA',   'WH-B', 110, 'delayed', 210),
  ('SH-111', 'SKU-BETA',    'WH-B',  90, 'delayed', 250),
  ('SH-112', 'RS622XK',     'WH-A', 130, 'delayed', 270),
  ('SH-113', 'RS622XKR',    'WH-A',  40, 'delayed', 120),
  ('SH-114', 'SKU-GAMMA',   'WH-C',  60, 'delayed', 150),
  ('SH-115', 'SKU-EPSILON', 'WH-E', 300, 'delayed', 330),
  ('SH-116', 'SKU-ALPHA',   'WH-B', 220, 'delayed', 410),
  ('SH-117', 'SKU-DELTA',   'WH-D',  75, 'delayed', 190),
  ('SH-118', 'SKU-BETA',    'WH-B', 160, 'delayed', 340),
  ('SH-119', 'RS622XK',     'WH-A',  95, 'delayed', 205);
-- X5: one shipment to a destination that is not yet on the locations master (INNER JOIN to locations drops it).
INSERT INTO shipments VALUES ('SH-120', 'SKU-ALPHA', 'WH-Z', 500, 'in_transit', 500);
-- X6: a large warehouse at half load (largest capacity, largest absolute free space).
INSERT INTO locations VALUES ('WH-F', 'Warehouse F', 200000, 100000, 'WH-F', FALSE, 'CAM-F-01');
