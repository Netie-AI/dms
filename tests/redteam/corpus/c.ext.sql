-- Family C extension: date / timezone / period-boundary rows. Synthetic, non-personal.
-- Seed transactions run 2026-06-02 .. 2026-07-25 (naive TIMESTAMP). Everything below is added on top.

-- ---------- 2024: leap-year edge (Feb 2024 has 29 days) and a 2024 year
INSERT INTO transactions VALUES
  ('T301', 'SKU-ALPHA', 'WH-B', 'outbound', 7,  2.10, '2024-02-29 12:00:00'),
  ('T302', 'SKU-ALPHA', 'WH-B', 'outbound', 5,  2.10, '2024-02-28 23:59:59'),
  ('T303', 'SKU-ALPHA', 'WH-B', 'outbound', 9,  2.10, '2024-03-01 00:00:00'),
  ('T304', 'SKU-ALPHA', 'WH-B', 'outbound', 3,  2.10, '2024-02-10 10:00:00'),
  ('T305', 'SKU-BETA',  'WH-B', 'outbound', 17, 8.75, '2024-11-15 10:00:00');

-- ---------- 2025: same months as 2026 (month/quarter-without-year mixing), year-end second
INSERT INTO transactions VALUES
  ('T311', 'SKU-ALPHA', 'WH-B', 'outbound', 1000, 2.10, '2025-09-05 10:00:00'),
  ('T312', 'SKU-BETA',  'WH-B', 'outbound', 300,  8.75, '2025-09-20 15:00:00'),
  ('T313', 'RS622XK',   'WH-A', 'outbound', 150,  4.50, '2025-10-10 09:00:00'),
  ('T314', 'SKU-ALPHA', 'WH-B', 'outbound', 40,   2.10, '2025-12-31 23:59:59'),
  ('T315', 'SKU-GAMMA', 'WH-C', 'outbound', 60,   12.00, '2025-07-15 08:00:00'),
  ('T316', 'SKU-ALPHA', 'WH-B', 'outbound', 25,   2.10, '2025-03-14 10:00:00');

-- ---------- 2026: year edge, August/September/October boundaries (30 Sep 23:59:59, 1 Oct 00:00:00)
INSERT INTO transactions VALUES
  ('T321', 'SKU-ALPHA', 'WH-B', 'outbound', 13, 2.10, '2026-01-01 00:00:00'),
  ('T322', 'SKU-ALPHA', 'WH-B', 'outbound', 11, 2.10, '2026-08-31 23:59:59'),
  ('T323', 'SKU-ALPHA', 'WH-B', 'outbound', 22, 2.10, '2026-09-01 00:00:00'),
  ('T324', 'SKU-BETA',  'WH-B', 'outbound', 33, 8.75, '2026-09-15 12:00:00'),
  ('T325', 'SKU-GAMMA', 'WH-C', 'outbound', 44, 12.00, '2026-09-30 00:00:00'),
  ('T326', 'SKU-ALPHA', 'WH-B', 'outbound', 55, 2.10, '2026-09-30 23:59:59'),
  ('T327', 'RS622XK',   'WH-A', 'outbound', 66, 4.50, '2026-10-01 00:00:00'),
  ('T328', 'SKU-DELTA', 'WH-D', 'outbound', 77, 6.40, '2026-10-01 18:30:00'),
  ('T329', 'SKU-BETA',  'WH-B', 'outbound', 88, 8.75, '2026-09-29 18:00:00'),
  ('T330', 'SKU-ALPHA', 'WH-B', 'outbound', 99, 2.10, '2026-09-08 10:00:00');

-- ---------- time-of-day edges (9am-5pm window): after 17:00, before 09:00, exactly 09:00, 16:59:59
INSERT INTO transactions VALUES
  ('T340', 'SKU-ALPHA', 'WH-B', 'outbound', 5, 2.10, '2026-08-10 17:30:00'),
  ('T341', 'SKU-ALPHA', 'WH-B', 'outbound', 6, 2.10, '2026-08-10 08:59:59'),
  ('T342', 'SKU-BETA',  'WH-B', 'outbound', 7, 8.75, '2026-08-11 09:00:00'),
  ('T343', 'SKU-BETA',  'WH-B', 'outbound', 9, 8.75, '2026-08-11 16:59:59');

-- ---------- rows relative to today (CURRENT_DATE at build time): week / yesterday / today edges
INSERT INTO transactions VALUES
  ('X101', 'SKU-ALPHA', 'WH-B', 'outbound', 1,   2.10, CAST(CURRENT_DATE AS TIMESTAMP) - INTERVAL 6 DAY + INTERVAL 10 HOUR),
  ('X102', 'SKU-ALPHA', 'WH-B', 'outbound', 2,   2.10, CAST(CURRENT_DATE AS TIMESTAMP) - INTERVAL 5 DAY + INTERVAL 10 HOUR),
  ('X103', 'SKU-BETA',  'WH-B', 'outbound', 4,   8.75, CAST(CURRENT_DATE AS TIMESTAMP) - INTERVAL 4 DAY),
  ('X104', 'SKU-BETA',  'WH-B', 'outbound', 8,   8.75, CAST(CURRENT_DATE AS TIMESTAMP) - INTERVAL 3 DAY + INTERVAL 12 HOUR),
  ('X105', 'SKU-ALPHA', 'WH-B', 'outbound', 16,  2.10, CAST(CURRENT_DATE AS TIMESTAMP) - INTERVAL 1 DAY - INTERVAL 1 SECOND),
  ('X106', 'SKU-ALPHA', 'WH-B', 'outbound', 32,  2.10, CAST(CURRENT_DATE AS TIMESTAMP) - INTERVAL 1 DAY),
  ('X107', 'SKU-BETA',  'WH-B', 'outbound', 64,  8.75, CAST(CURRENT_DATE AS TIMESTAMP) - INTERVAL 1 DAY + INTERVAL 12 HOUR),
  ('X108', 'SKU-ALPHA', 'WH-B', 'outbound', 128, 2.10, CAST(CURRENT_DATE AS TIMESTAMP) - INTERVAL 1 SECOND),
  ('X109', 'SKU-BETA',  'WH-B', 'outbound', 256, 8.75, CAST(CURRENT_DATE AS TIMESTAMP));

-- ---------- suppliers: one never audited, two audited in spring 2026 (absolute dates)
INSERT INTO suppliers VALUES
  ('SUP-05', 'Nilai Freight Ltd',     'MY', 14, 0.30, NULL),
  ('SUP-06', 'Kinta Components',      'MY', 6,  0.12, '2026-04-25'),
  ('SUP-07', 'Selangor Resin Works',  'MY', 8,  0.27, '2026-03-16');

-- ---------- inventory expiry dates (absolute; the seed run date is 2026-10-02)
UPDATE inventory SET expiry_date = '2026-10-05' WHERE sku = 'SKU-ALPHA';
UPDATE inventory SET expiry_date = '2026-11-16' WHERE sku = 'SKU-BETA';
UPDATE inventory SET expiry_date = '2026-10-01' WHERE sku = 'SKU-DELTA';
UPDATE inventory SET expiry_date = '2026-10-22' WHERE sku = 'SKU-EPSILON';
UPDATE inventory SET expiry_date = '2026-10-09' WHERE sku = 'RS622XK';
INSERT INTO inventory VALUES
  ('SKU-ZETA', 'WH-A', 100, 50, 3.00, 'SUP-01', 'RAW', '2026-10-02');

-- ---------- ambiguous numeric date 04/05/2026 (4 May or 5 April): rows on both days, different totals
INSERT INTO transactions VALUES
  ('T350', 'SKU-ALPHA', 'WH-B', 'outbound', 61, 2.10, '2026-04-05 10:00:00'),
  ('T351', 'SKU-ALPHA', 'WH-B', 'outbound', 62, 2.10, '2026-05-04 10:00:00');

-- ---------- a 2023 row (stale "two years ago" hard-code), and a day whose total only shows when grouped by DATE
INSERT INTO transactions VALUES
  ('T306', 'SKU-ALPHA', 'WH-B', 'outbound', 300, 2.10, '2023-06-10 10:00:00'),
  ('T360', 'SKU-ALPHA', 'WH-B', 'outbound', 900, 2.10, '2026-08-20 10:00:00'),
  ('T361', 'SKU-ALPHA', 'WH-B', 'outbound', 800, 2.10, '2026-08-20 15:00:00');

-- ---------- same-month-other-year expiries (this month in 2025 and 2027)
INSERT INTO inventory VALUES
  ('SKU-ETA',   'WH-B', 200, 100, 2.00, 'SUP-02', 'PARTS', '2027-10-15'),
  ('SKU-THETA', 'WH-D', 120, 60,  2.50, 'SUP-03', 'PARTS', '2025-10-20');

-- ---------- sub-second end-of-day row (23:59:59.5) for the "23:59:59 fix" boundary
INSERT INTO transactions VALUES
  ('T370', 'SKU-ALPHA', 'WH-B', 'outbound', 3, 2.10, '2026-09-30 23:59:59.500');
