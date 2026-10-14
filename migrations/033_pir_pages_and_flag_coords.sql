-- migrations/033_pir_pages_and_flag_coords.sql
-- Multi-page PIRs (Slice R1e, 2026-10-14): a PIR's scan becomes pages, one row each, and every live flag sits on a
-- page of its PIR (its sketch_x / sketch_y / rotation from R1b are its place on that page).
--
--   icid.pir_pages   (new)
--     pir_page_id      UUID PK
--     pir_id           UUID NOT NULL      FK -> pirs.id ON DELETE CASCADE
--     page_number      INT NOT NULL       >= 1; unique per PIR among live pages (pir_pages_pir_page_num_live_uq)
--     image_path       TEXT NOT NULL      the page's PNG in the pir-images bucket: pirs/{pir}/{upload}/page-{n}.png
--     image_mime       TEXT NOT NULL      image/png
--     width_px, height_px INT NOT NULL   > 0
--     scale_px_per_ft  NUMERIC(10,4) NULL > 0 when set
--     rotation_deg     INT NOT NULL       0, 90, 180 or 270; default 0
--     created_at, updated_at
--     deleted_at       TIMESTAMPTZ NULL   the page was replaced (a re-upload) or the pages were deleted
--     deleted_by       UUID NULL          FK -> users.uuid ON DELETE SET NULL
--
--   icid.pirs        (changed)
--     - source_image_url, source_image_scale   R1d's single image; pages replace it (no PIR had one)
--
--   icid.flags       (changed)
--     + pir_page_id    UUID NULL          FK -> pir_pages.pir_page_id (no action: flags are never hard-deleted)
--     chk_flags_live_on_a_page             a live flag has a page; only a soft-deleted one may have none
--     flags_pir_page_idx
--
-- The 3 flags made through R1b's form (before the sketch tool) are soft-deleted here: they have no page, and the
-- CHECK lets them stay only as deleted rows. They can't be un-deleted (they have no page to come back on).
--
-- Run in the Supabase SQL editor, after 032, BEFORE deploying the R1e code (it reads pir_pages and flags.pir_page_id,
-- and the code before it reads pirs.source_image_url). Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration; stop if any differs)
-- ============================================================

-- 1. The flags as R1e found them:
--   SELECT count(*) AS flags, count(*) FILTER (WHERE deleted_at IS NULL) AS live FROM icid.flags;
--   Expected: 3 | 3

-- 2. No PIR has an R1d image (so there is nothing to carry into pages):
--   SELECT count(*) FROM icid.pirs WHERE source_image_url IS NOT NULL;
--   Expected: 0

-- 3. The two columns being dropped exist, and pir_pages doesn't:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'pirs' AND column_name IN ('source_image_url', 'source_image_scale')
--   ORDER BY column_name;
--   Expected: source_image_scale, source_image_url
--   SELECT to_regclass('icid.pir_pages');
--   Expected: NULL

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

-- 1. A PIR's pages: one row per page of its scan; a re-upload or a delete replaces (soft-deletes) them
CREATE TABLE icid.pir_pages (
    pir_page_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pir_id           UUID NOT NULL REFERENCES icid.pirs(id) ON DELETE CASCADE,
    page_number      INT NOT NULL CHECK (page_number >= 1),
    image_path       TEXT NOT NULL,
    image_mime       TEXT NOT NULL,
    width_px         INT NOT NULL CHECK (width_px > 0),
    height_px        INT NOT NULL CHECK (height_px > 0),
    scale_px_per_ft  NUMERIC(10,4) CHECK (scale_px_per_ft IS NULL OR scale_px_per_ft > 0),
    rotation_deg     INT NOT NULL DEFAULT 0 CHECK (rotation_deg IN (0, 90, 180, 270)),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at       TIMESTAMPTZ,
    deleted_by       UUID REFERENCES icid.users(uuid) ON DELETE SET NULL
);
CREATE UNIQUE INDEX pir_pages_pir_page_num_live_uq ON icid.pir_pages (pir_id, page_number) WHERE deleted_at IS NULL;
CREATE INDEX pir_pages_pir_idx ON icid.pir_pages (pir_id);

-- 2. R1d's single image moves to pages (no PIR had one: nothing to copy)
ALTER TABLE icid.pirs DROP COLUMN source_image_url, DROP COLUMN source_image_scale;

-- 3. R1e re-architecture: pre-sketch-tool flags; re-draw in the sketch workspace
UPDATE icid.flags SET deleted_at = now(), updated_at = now() WHERE deleted_at IS NULL;

ALTER TABLE icid.flags
    ADD COLUMN pir_page_id UUID REFERENCES icid.pir_pages(pir_page_id),
    ADD CONSTRAINT chk_flags_live_on_a_page CHECK (pir_page_id IS NOT NULL OR deleted_at IS NOT NULL);
CREATE INDEX flags_pir_page_idx ON icid.flags (pir_page_id);

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. pir_pages is there and empty, with its partial unique index:
--   SELECT count(*) FROM icid.pir_pages;
--   Expected: 0
--   SELECT indexdef FROM pg_indexes WHERE schemaname = 'icid' AND indexname = 'pir_pages_pir_page_num_live_uq';
--   Expected: CREATE UNIQUE INDEX ... ON icid.pir_pages USING btree (pir_id, page_number) WHERE (deleted_at IS NULL)

-- B. The R1d columns are gone:
--   SELECT count(*) FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'pirs' AND column_name LIKE 'source_image%';
--   Expected: 0

-- C. Every flag is soft-deleted, none has a page, and the CHECK and the foreign key are in place:
--   SELECT count(*) AS flags, count(*) FILTER (WHERE deleted_at IS NULL) AS live,
--          count(pir_page_id) AS on_a_page FROM icid.flags;
--   Expected: 3 | 0 | 0
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.flags'::regclass AND conname IN ('chk_flags_live_on_a_page', 'flags_pir_page_id_fkey');
--   Expected: the CHECK, and FOREIGN KEY (pir_page_id) REFERENCES icid.pir_pages(pir_page_id)

-- ============================================================
-- ROLLBACK (only while no page exists; the 3 flags stay soft-deleted, un-delete them by hand if wanted)
-- ============================================================

-- BEGIN;
-- DROP INDEX icid.flags_pir_page_idx;
-- ALTER TABLE icid.flags DROP CONSTRAINT chk_flags_live_on_a_page, DROP COLUMN pir_page_id;
-- ALTER TABLE icid.pirs ADD COLUMN source_image_url TEXT, ADD COLUMN source_image_scale REAL;
-- DROP TABLE icid.pir_pages;
-- COMMIT;
