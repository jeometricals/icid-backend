-- migrations/060_assessment_clip_audits.sql
--
-- Slice Y2: the homeowner's assessment, clipped at the property line. When an approval claims a flag that crosses its
-- page's sidewalk strip (Slice Y1.5), the part of the flag outside the strip is not assessed: clipped_sf =
-- quantity x the fraction of the flag's outline outside the strip, pre_clip_sf the flag's quantity (its override, or
-- else its net area) at that moment. One row per flag and IDR, the IDR's set replaced whole at approval, un-delete and
-- an admin's re-clip, and removed by unlock and delete, as icid.quantities is. Only the Construction Report's measure
-- and the Assessment Summary read it; contractor billing (icid.quantities, Progress) is never clipped.
--
-- Touches no live data: adds one table; modifies 0 rows. Run before deploying the Slice Y2 code; the code before it
-- runs unchanged after it. Safe to run twice.
--
-- Rollback (after rolling the code back): DROP TABLE icid.assessment_clip_audits;

BEGIN;

CREATE TABLE IF NOT EXISTS icid.assessment_clip_audits (
    audit_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flag_id      UUID NOT NULL REFERENCES icid.flags(id),
    idr_id       UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    pre_clip_sf  NUMERIC(10,2) NOT NULL,
    clipped_sf   NUMERIC(10,2) NOT NULL,
    clip_reason  TEXT NOT NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_assessment_clip_audits_flag_idr UNIQUE (flag_id, idr_id),
    CONSTRAINT chk_assessment_clip_audits_amounts
        CHECK (pre_clip_sf >= 0 AND clipped_sf >= 0 AND clipped_sf <= pre_clip_sf),
    CONSTRAINT chk_assessment_clip_audits_reason CHECK (clip_reason IN ('outside_property_line'))
);
CREATE INDEX IF NOT EXISTS idx_assessment_clip_audits_idr ON icid.assessment_clip_audits (idr_id, flag_id);

COMMIT;
