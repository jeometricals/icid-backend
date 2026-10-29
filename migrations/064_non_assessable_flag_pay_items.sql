-- migrations/064_non_assessable_flag_pay_items.sql
--
-- Pilot sweep #2, data only (no schema change): a non-assessable flag is built like any other, so the contractor bills
-- it. Every live, unclaimed, non-assessable flag without a pay item takes the sidewalk item of its thickness: 4in and
-- 7in_as_4in -> 4.13 AAS, 7in -> 4.13 BAS, when its project has exactly one contract item under that item number
-- (whatever its budget code) in a unit a flag is measured in (SF, SY, CY, EA). None, or several (SE384 has 4.13 AAS
-- under four budget codes), and the flag stays without one: Progress counts it as unmapped, for the OE to map.
-- The same rule as the code's default_pay_item_id (api/services/flag_pay_items.py), for flags drawn from now on.
--
-- A live bundle of such flags (a measurement carrying their classification) takes the same pay item, so it stays its
-- members' classification. Claimed flags are left alone: their IDR's quantities were written at approval.
-- As of 2026-10-29 on prod: 26 flags, all HWS0023, all 4in (313 38 Street), no bundle.
--
-- Rollback: set pay_item_id back to NULL on the flags (and bundles) listed by this statement's result.
-- Safe to run twice: the second run finds no flag without a pay item to give one to.

WITH sole AS (
    -- Each project's one contract item under an item number, in a unit a flag is measured in
    SELECT ci.project_id, ci.item_no, (array_agg(ci.contract_item_id))[1] AS contract_item_id
    FROM icid.contract_items ci
    WHERE ci.item_no IN ('4.13 AAS', '4.13 BAS')
    GROUP BY ci.project_id, ci.item_no
    HAVING count(*) = 1
       AND upper(replace(replace(min(ci.unit), '.', ''), ' ', '')) IN ('SF', 'SY', 'CY', 'EA', 'EACH')
),
picked AS (
    SELECT f.id AS flag_id, f.measurement_id, s.contract_item_id
    FROM icid.flags f
    JOIN icid.pirs pir ON pir.id = f.pir_id
    JOIN icid.properties p ON p.id = pir.property_id
    JOIN sole s ON s.project_id = p.project_id
               AND s.item_no = CASE f.surveyed_thickness WHEN '7in' THEN '4.13 BAS' ELSE '4.13 AAS' END
    WHERE f.deleted_at IS NULL AND f.surveyed_is_assessable = false AND f.pay_item_id IS NULL
      AND f.claimed_by_idr_id IS NULL
      AND f.surveyed_thickness IN ('4in', '7in', '7in_as_4in')
),
flags_set AS (
    UPDATE icid.flags f
    SET pay_item_id = pk.contract_item_id, updated_at = now()
    FROM picked pk
    WHERE f.id = pk.flag_id
    RETURNING f.id, f.pay_item_id
),
bundles_set AS (
    UPDATE icid.flag_measurements m
    SET pay_item_id = pk.contract_item_id, updated_at = now()
    FROM picked pk
    WHERE m.measurement_id = pk.measurement_id
      AND m.surveyed_is_assessable IS NOT NULL AND m.retired_at IS NULL AND m.pay_item_id IS NULL
    RETURNING m.measurement_id
)
SELECT (SELECT count(*) FROM flags_set) AS flags_given_a_pay_item,
       (SELECT count(DISTINCT measurement_id) FROM bundles_set) AS bundles_given_a_pay_item;
