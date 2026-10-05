-- Family D extension: a customer SQL source ingested as all-VARCHAR, plus hand-keyed legacy columns.
-- Synthetic, non-personal rows only. Only INSERT / UPDATE / ALTER on the six demo tables.
--
-- Modelled on main: SQL-source rows are stringified with str(v) and every column is created VARCHAR
-- (db_connector.py:471, bronze.py:491). So money / integers / booleans / timestamps from a SQL source
-- arrive as text, and legacy free-text columns carry whatever a clerk typed.

-- ---------------------------------------------------------------- suppliers (finance, company)
-- lead_time_days arrives as VARCHAR holding integers ('7', '12', ...): '9' sorts above '12'.
ALTER TABLE suppliers ALTER COLUMN lead_time_days TYPE VARCHAR;
-- next_audit_due: day-first text (Malaysian locale). Rows with day > 12 prove the format.
ALTER TABLE suppliers ADD COLUMN next_audit_due VARCHAR;
UPDATE suppliers SET next_audit_due = '05/11/2026' WHERE supplier_id = 'SUP-01';
UPDATE suppliers SET next_audit_due = '25/08/2026' WHERE supplier_id = 'SUP-02';
UPDATE suppliers SET next_audit_due = '03/12/2026' WHERE supplier_id = 'SUP-03';
UPDATE suppliers SET next_audit_due = '30/11/2026' WHERE supplier_id = 'SUP-04';
INSERT INTO suppliers VALUES ('SUP-05', 'Harbour Freight Sdn', 'MY ', '10', NULL, NULL, '12/01/2027');
INSERT INTO suppliers VALUES ('SUP-06', 'Selatan Coatings', 'my', '6', 0.30, NULL, NULL);
INSERT INTO suppliers VALUES ('SUP-07', 'Utara Resin', NULL, '14', 0.60, DATE '2026-09-15', '15/10/2026');
INSERT INTO suppliers VALUES ('SUP-08', 'Timur Sealants', '', '3', NULL, DATE '2026-03-01', '01/12/2026');

-- ---------------------------------------------------------------- transactions (finance, company)
-- unit_cost_myr arrives as text; a few hand-keyed cells carry a currency prefix, padding, or 'TBC'.
ALTER TABLE transactions ALTER COLUMN unit_cost_myr TYPE VARCHAR;
UPDATE transactions SET unit_cost_myr = 'RM 5.20' WHERE txn_id = 'T003';
UPDATE transactions SET unit_cost_myr = ' 12.00 ' WHERE txn_id = 'T007';
UPDATE transactions SET unit_cost_myr = 'TBC' WHERE txn_id = 'T012';
-- Four extra movements with dirty keys: capitalised / padded txn_type, CHAR(n)-style padded location_id.
INSERT INTO transactions VALUES ('T016', 'SKU-ALPHA', 'WH-B', 'Outbound', 100.0, '2.1', TIMESTAMP '2026-07-02 09:00:00');
INSERT INTO transactions VALUES ('T017', 'SKU-BETA', 'WH-B', 'OUTBOUND ', 50.0, '8.75', TIMESTAMP '2026-07-03 10:00:00');
INSERT INTO transactions VALUES ('T018', 'RS622XK', 'WH-A ', 'outbound', 70.0, '4.5', TIMESTAMP '2026-07-26 09:00:00');
INSERT INTO transactions VALUES ('T019', 'SKU-DELTA', 'WH-D ', 'outbound ', 30.0, '6.4', TIMESTAMP '2026-07-27 11:00:00');
-- A late-July inbound receipt on the last day of the month, in the afternoon (a DATE-vs-TIMESTAMP boundary).
INSERT INTO transactions VALUES ('T020', 'SKU-ALPHA', 'WH-B', 'inbound', 60.0, '2.1', TIMESTAMP '2026-07-31 14:00:00');

-- ---------------------------------------------------------------- alerts (company only)
-- resolved arrives as VARCHAR with mixed spellings of a flag; every spelling still casts to BOOLEAN.
ALTER TABLE alerts ALTER COLUMN resolved TYPE VARCHAR;
UPDATE alerts SET resolved = 'N' WHERE alert_id = 'AL-1';
UPDATE alerts SET resolved = 'No' WHERE alert_id = 'AL-2';
UPDATE alerts SET resolved = 'Y' WHERE alert_id = 'AL-3';
UPDATE alerts SET resolved = 'false' WHERE alert_id = 'AL-4';
UPDATE alerts SET resolved = '0' WHERE alert_id = 'AL-5';
INSERT INTO alerts VALUES ('AL-6', 'low', 'WH-A', 'Dock sensor offline', 'n');
INSERT INTO alerts VALUES ('AL-7', 'low', 'WH-B', 'Door ajar cleared', 'TRUE');
INSERT INTO alerts VALUES ('AL-8', 'medium', 'WH-C', 'Temperature spike cleared', 'Yes');

-- ---------------------------------------------------------------- shipments (ops, company)
-- cost_myr: hand-keyed text (prefix, thousands separator, accounting negative, padding, 'TBC', NULL).
-- shipped_at: timestamptz stringified from a UTC session ('YYYY-MM-DD HH:MM:SS+00:00'); the only timestamp here.
-- invoice_total: forwarder invoice text in whatever currency was invoiced; no rate table exists.
-- gross_weight: weight with the unit typed in ('kg' or 'tonne'); no typed counterpart.
ALTER TABLE shipments ALTER COLUMN cost_myr TYPE VARCHAR;
ALTER TABLE shipments ADD COLUMN shipped_at VARCHAR;
ALTER TABLE shipments ADD COLUMN invoice_total VARCHAR;
ALTER TABLE shipments ADD COLUMN gross_weight VARCHAR;
UPDATE shipments SET cost_myr = '820.00', shipped_at = '2026-06-30 16:30:00+00:00', invoice_total = 'RM 820.00', gross_weight = '415 kg' WHERE shipment_id = 'SH-100';
UPDATE shipments SET cost_myr = 'RM 2,310.50', shipped_at = '2026-07-05 02:00:00+00:00', invoice_total = 'USD 450.00', gross_weight = '0.14 tonne' WHERE shipment_id = 'SH-101';
UPDATE shipments SET cost_myr = '1,540.00', shipped_at = '2026-07-07 23:00:00+00:00', invoice_total = 'RM 1,540.00', gross_weight = '262 kg' WHERE shipment_id = 'SH-102';
UPDATE shipments SET cost_myr = '(190.00)', shipped_at = '2026-07-12 05:00:00+00:00', invoice_total = 'SGD 60.00', gross_weight = '85 kg' WHERE shipment_id = 'SH-103';
UPDATE shipments SET cost_myr = '460.00', shipped_at = '2026-07-14 09:15:00+00:00', invoice_total = 'RM 460.00', gross_weight = '0.21 tonne' WHERE shipment_id = 'SH-104';
INSERT INTO shipments VALUES ('SH-105', 'SKU-EPSILON', 'WH-E', 60.0, 'delivered', 'TBC', '2026-07-18 04:00:00+00:00', NULL, '63 kg');
INSERT INTO shipments VALUES ('SH-106', NULL, 'WH-A', 10.0, NULL, NULL, '2026-07-20 12:00:00+00:00', NULL, '11 kg');
INSERT INTO shipments VALUES ('SH-107', 'SKU-ALPHA', 'WH-B', 15.0, 'DELIVERED', '95', '2026-07-01 03:00:00+00:00', 'RM 95.00', '16 kg');
INSERT INTO shipments VALUES ('SH-108', 'SKU-BETA', 'WH-D', 40.0, 'delivered ', ' 1200 ', '2026-07-22 15:30:00+00:00', 'RM 1,200.00', '42 kg');

-- ---------------------------------------------------------------- inventory (finance, ops, company)
-- freight_surcharge_myr: the column NAME claims ringgit but hand-keyed cells carry other currencies
-- ('USD 30.00'). No currency column and no rate table exist, so the unit suffix is the only evidence.
ALTER TABLE inventory ADD COLUMN freight_surcharge_myr VARCHAR;
UPDATE inventory SET freight_surcharge_myr = 'RM 120.00' WHERE sku = 'RS622XK';
UPDATE inventory SET freight_surcharge_myr = 'RM 45.50' WHERE sku = 'RS622XKR';
UPDATE inventory SET freight_surcharge_myr = 'USD 30.00' WHERE sku = 'SKU-ALPHA';
UPDATE inventory SET freight_surcharge_myr = 'RM 80.00' WHERE sku = 'SKU-BETA';
UPDATE inventory SET freight_surcharge_myr = 'RM 15.00' WHERE sku = 'SKU-DELTA';
UPDATE inventory SET freight_surcharge_myr = 'USD 22.00' WHERE sku = 'SKU-EPSILON';

-- Dirty join keys on inventory, as a CHAR(n) source returns them: a right-padded location_id and supplier_id.
-- A clean second low-stock lot at WH-A keeps the governed low-stock answer non-empty; its category is hand-keyed 'Raw '.
UPDATE inventory SET location_id = 'WH-A ' WHERE sku = 'RS622XKR';
UPDATE inventory SET supplier_id = 'SUP-02 ' WHERE sku = 'SKU-DELTA';
INSERT INTO inventory (sku, location_id, quantity_kg, reorder_level_kg, unit_cost_myr, supplier_id, category, expiry_date, freight_surcharge_myr) VALUES ('SKU-ZETA', 'WH-A', 50.0, 300.0, 3.0, 'SUP-01', 'Raw ', NULL, NULL);

-- ---------------------------------------------------------------- locations (finance, ops, company)
-- capacity and load arrive as VARCHAR ('100000.0', '72000.0'): a column-to-column comparison is then a
-- string comparison.
ALTER TABLE locations ALTER COLUMN capacity_kg TYPE VARCHAR;
ALTER TABLE locations ALTER COLUMN current_load_kg TYPE VARCHAR;
