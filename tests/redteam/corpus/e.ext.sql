-- Family E (wording traps) extension data. Synthetic, non-personal. DML on the six demo tables only.
-- Purpose: make ties, boundary values, a third txn_type, an extra shipment status, a location with
-- no stock, a supplier with no stock, and a chemicals row inside a warehouse that also holds
-- non-chemicals, so that a wrong reading of the wording gives different rows than the right one.

-- locations: WH-F ties WH-E on load and utilisation and is cold; WH-G is exactly 90.0 percent full; neither holds stock.
INSERT INTO locations VALUES ('WH-F', 'Warehouse F', 90000.0, 88000.0, 'WH-F', TRUE, 'CAM-F-01');
INSERT INTO locations VALUES ('WH-G', 'Warehouse G', 100000.0, 90000.0, 'WH-G', FALSE, 'CAM-G-01');

-- suppliers: SUP-05 ties SUP-02 on lead time, SUP-04 on risk and audit date, and supplies nothing.
INSERT INTO suppliers VALUES ('SUP-05', 'Zenith Resin', 'MY', 12, 0.55, DATE '2026-09-01');

-- inventory (sku stays unique): ETA ties ALPHA on quantity; ZETA ties EPSILON on quantity/cost and is the only
-- row with a future expiry; THETA is a chemicals row at WH-A whose quantity equals its reorder level.
INSERT INTO inventory VALUES ('SKU-ETA', 'WH-D', 3400.0, 1000.0, 1.5, 'SUP-01', 'RAW', NULL);
INSERT INTO inventory VALUES ('SKU-ZETA', 'WH-E', 2100.0, 800.0, 3.25, 'SUP-03', 'PARTS', DATE '2027-03-31');
INSERT INTO inventory VALUES ('SKU-THETA', 'WH-A', 400.0, 400.0, 9.5, 'SUP-04', 'CHEMICALS', NULL);

-- shipments: a fourth status ('cancelled'), ties on cost (540, 310), four delayed shipments split 2/2 across suppliers.
INSERT INTO shipments VALUES ('SH-105', 'SKU-ALPHA', 'WH-A', 300.0, 'delayed', 540.0);
INSERT INTO shipments VALUES ('SH-106', 'SKU-EPSILON', 'WH-E', 150.0, 'cancelled', 0.0);
INSERT INTO shipments VALUES ('SH-107', 'SKU-BETA', 'WH-A', 90.0, 'delivered', 310.0);
INSERT INTO shipments VALUES ('SH-108', 'SKU-ETA', 'WH-D', 500.0, 'delayed', 700.0);

-- alerts: severity high/medium/low each appear twice; WH-F has only a resolved alert; WH-G has none.
INSERT INTO alerts VALUES ('AL-6', 'low', 'WH-F', 'Cold chain check done', TRUE);

-- transactions: a third txn_type ('adjustment') and a timestamp tie with T015.
INSERT INTO transactions VALUES ('T016', 'SKU-EPSILON', 'WH-E', 'adjustment', 50.0, 3.25, TIMESTAMP '2026-07-25 13:00:00');
