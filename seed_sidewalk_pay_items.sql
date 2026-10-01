-- seed_sidewalk_pay_items.sql
-- Pay-item catalog mock data (Slice F): the 22-item NYCDOT spec catalog, and a
-- Schedule of Bid Items for the sidewalk project HWS0023 ("S/W Queens 2025").
--
-- Contract items: one row per spec item under budget code 12345, plus 4.13 AAS a
-- second time under 67890 at $19.00 — the same item under two budget codes, which
-- the pay-item picker must keep apart. 23 contract items in all.
--
-- Bid quantities and unit prices are plausible placeholders, not real bid data.
--
-- Run after schema.sql (or migrations/011) and seed.sql, which creates HWS0023:
--   psql -d icid -f seed_sidewalk_pay_items.sql

BEGIN;

------------------------------------------------------------
-- SPEC ITEMS
------------------------------------------------------------
INSERT INTO icid.spec_items (item_no, description, spec_section, pay_unit) VALUES
    ('4.02 AB-R', 'Asphaltic Concrete Wearing Course, 1-1/2" Thick',        '4.02', 'S.Y.'),
    ('4.02 AF-R', 'Asphaltic Concrete Wearing Course, 2" Thick',            '4.02', 'S.Y.'),
    ('4.02 CA',   'Binder Mixture',                                          '4.02', 'Ton'),
    ('4.02 CB',   'Asphaltic Concrete Mixture',                              '4.02', 'Ton'),
    ('4.05 A',    'Non-Reinforced Concrete Pavement',                        '4.05', 'C.Y.'),
    ('4.05 AC',   'Reinforced Concrete Pavement (Bus Stops)',                '4.05', 'C.Y.'),
    ('4.05 B',    'Reinforced Concrete Pavement (Full Width Pavement)',      '4.05', 'C.Y.'),
    ('4.08 AA',   'Concrete Curb (18" Deep)',                                '4.08', 'L.F.'),
    ('4.09 AD',   'Straight Steel Faced Concrete Curb (18" Deep)',           '4.09', 'L.F.'),
    ('4.09 BD',   'Depressed Steel Faced Concrete Curb (18" Deep)',          '4.09', 'L.F.'),
    ('4.09 CD',   'Corner Steel Faced Concrete Curb (18" Deep)',             '4.09', 'L.F.'),
    ('4.13 AAS',  '4" Concrete Sidewalk (Unpigmented)',                      '4.13', 'S.F.'),
    ('4.13 BAS',  '7" Concrete Sidewalk (Unpigmented)',                      '4.13', 'S.F.'),
    ('4.13 BR',   '7" Reinforced Concrete Sidewalk (Unpigmented)',           '4.13', 'S.F.'),
    ('4.13 DE',   'Embedded Preformed Detectable Warning Units',             '4.13', 'S.F.'),
    ('6.28 AA',   'Lighted Timber Barricades',                               '6.28', 'L.F.'),
    ('6.28 AB',   'Unlighted Timber Barricades',                             '6.28', 'L.F.'),
    ('6.44',      'Thermoplastic Reflectorized Pavement Markings (4" Wide)', '6.44', 'L.F.'),
    ('6.49',      'Temporary Pavement Markings (4" Wide)',                   '6.49', 'L.F.'),
    ('6.59 P',    'Temporary Concrete Barrier',                              '6.59', 'L.F.'),
    ('6.59 PF',   'Temporary Concrete Barrier with Fence',                   '6.59', 'L.F.'),
    ('6.87',      'Plastic Barrels',                                         '6.87', 'Each');

------------------------------------------------------------
-- CONTRACT ITEMS (HWS0023; resolve item_no to the generated spec_item_id)
------------------------------------------------------------
INSERT INTO icid.contract_items (project_id, spec_item_id, budget_code, bid_quantity, bid_unit_price)
SELECT 'HWS0023', s.spec_item_id, src.budget_code, src.bid_quantity, src.bid_unit_price
FROM (VALUES
    ('4.02 AB-R', '12345', 2500.00,  18.50),
    ('4.02 AF-R', '12345', 1800.00,  22.00),
    ('4.02 CA',   '12345',  450.00, 165.00),
    ('4.02 CB',   '12345',  600.00, 175.00),
    ('4.05 A',    '12345',  300.00, 425.00),
    ('4.05 AC',   '12345',  120.00, 520.00),
    ('4.05 B',    '12345',  250.00, 480.00),
    ('4.08 AA',   '12345', 3200.00,  45.00),
    ('4.09 AD',   '12345', 2800.00,  68.00),
    ('4.09 BD',   '12345',  900.00,  72.00),
    ('4.09 CD',   '12345',  400.00,  85.00),
    ('4.13 AAS',  '12345', 9500.00,  14.50),
    ('4.13 BAS',  '12345', 4200.00,  18.75),
    ('4.13 BR',   '12345', 1500.00,  24.00),
    ('4.13 DE',   '12345',  600.00,  55.00),
    ('6.28 AA',   '12345', 1200.00,  12.00),
    ('6.28 AB',   '12345', 1500.00,   8.50),
    ('6.44',      '12345', 6000.00,   2.25),
    ('6.49',      '12345', 5000.00,   1.75),
    ('6.59 P',    '12345',  800.00,  65.00),
    ('6.59 PF',   '12345',  400.00,  95.00),
    ('6.87',      '12345',  250.00,  35.00),
    ('4.13 AAS',  '67890', 2000.00,  19.00)
) AS src (item_no, budget_code, bid_quantity, bid_unit_price)
JOIN icid.spec_items s ON s.item_no = src.item_no;

COMMIT;
