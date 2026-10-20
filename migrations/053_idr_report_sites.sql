-- migrations/053_idr_report_sites.sql
--
-- Slice U.
--   * U1: at most one Daily Site Patrol per IDR (uq_idr_reports_one_dsp_per_idr). Checked live on 2026-10-21: no DSP
--     report exists yet.
--   * U2: icid.idr_report_sites, the sites a report imports, each with the work done there. A site is imported once
--     per IDR (into one of its reports); the same site can be in IDRs of other days. The report and the IDR are tied
--     by a composite foreign key to (idr_reports.report_id, idr_id), so a row's idr_id is always its report's; it
--     goes with its report, and with its IDR (both ON DELETE CASCADE, as everything under an IDR does). One more
--     foreign key to users (added_by; count_user_references counts it).
--   * Backfill: an IDR's flags and curb segments are now claimed from its imported sites, not its task order. So that
--     an approved IDR that is unlocked and approved again keeps what it claimed, every approved IDR's claimed sites
--     (the properties of the flags and segments it claimed) are imported into its first report, as concrete work,
--     added by the IDR's RE. Checked live: Walk004, IDR-001 and IDR-002, 16 sites, each IDR's one report a General.
--
-- Run before deploying the Slice U code. Safe to run twice.
--
-- Rollback (after rolling the code back): DROP TABLE icid.idr_report_sites; DROP INDEX icid.uq_idr_reports_report_idr;
-- DROP INDEX icid.uq_idr_reports_one_dsp_per_idr;

BEGIN;

-- U1: one DSP per IDR
CREATE UNIQUE INDEX IF NOT EXISTS uq_idr_reports_one_dsp_per_idr
    ON icid.idr_reports (idr_id) WHERE report_type = 'DSP';

-- U2: what a site row's composite foreign key points at (report_id is already unique; the pair is, too)
CREATE UNIQUE INDEX IF NOT EXISTS uq_idr_reports_report_idr ON icid.idr_reports (report_id, idr_id);

CREATE TABLE IF NOT EXISTS icid.idr_report_sites (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id        UUID NOT NULL,
    idr_id           UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    property_id      UUID NOT NULL REFERENCES icid.properties(id),
    work_type        TEXT NOT NULL CHECK (work_type IN ('excavation', 'concrete', 'pavement_restoration', 'other')),
    work_type_other  TEXT,
    added_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by         UUID NOT NULL REFERENCES icid.users(uuid),
    CONSTRAINT fk_idr_report_sites_report FOREIGN KEY (report_id, idr_id)
        REFERENCES icid.idr_reports (report_id, idr_id) ON DELETE CASCADE,
    CONSTRAINT chk_idr_report_sites_other CHECK (work_type <> 'other' OR work_type_other IS NOT NULL),
    CONSTRAINT uq_idr_report_sites_idr_property UNIQUE (idr_id, property_id)
);
CREATE INDEX IF NOT EXISTS idx_idr_report_sites_report ON icid.idr_report_sites (report_id);
CREATE INDEX IF NOT EXISTS idx_idr_report_sites_property ON icid.idr_report_sites (property_id);

-- Backfill: each approved IDR's claimed sites, into its first report
INSERT INTO icid.idr_report_sites (report_id, idr_id, property_id, work_type, added_by)
SELECT first_report.report_id, i.idr_id, claimed.property_id, 'concrete', COALESCE(i.re_reviewer_uuid, i.reporter_uuid)
FROM icid.idrs i
JOIN LATERAL (
    SELECT r.report_id FROM icid.idr_reports r WHERE r.idr_id = i.idr_id ORDER BY r.page_number NULLS LAST, r.created_at LIMIT 1
) first_report ON true
JOIN LATERAL (
    SELECT pir.property_id FROM icid.flags f JOIN icid.pirs pir ON pir.id = f.pir_id WHERE f.claimed_by_idr_id = i.idr_id
    UNION
    SELECT pir.property_id FROM icid.segments s JOIN icid.pir_pages pg ON pg.pir_page_id = s.pir_page_id
    JOIN icid.pirs pir ON pir.id = pg.pir_id WHERE s.claimed_by_idr_id = i.idr_id
) claimed ON true
WHERE i.status = 'approved' AND i.deleted_at IS NULL
ON CONFLICT (idr_id, property_id) DO NOTHING;

COMMIT;

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT i.idr_number, count(*) FROM icid.idr_report_sites s JOIN icid.idrs i ON i.idr_id = s.idr_id
--  GROUP BY 1 ORDER BY 1;   -- IDR-001 13, IDR-002 1, Walk004 2
