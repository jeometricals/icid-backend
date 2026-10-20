-- migrations/050_ped_ramp_photos.sql
--
-- Pedramp Phase 3, S4: photos of an inspection or a TIF, kept in the private pedramp-photos bucket (050b).
--   * A photo belongs to exactly one of an inspection (inspection_id) or a TIF (tif_id): real foreign keys, and a
--     CHECK that one is set.
--   * An inspection's photo names the ramp it shows (property_id): the DOT form's photo log pages belong to a ramp
--     slot (six photos a ramp). A TIF's photos are its ramp's, so they need none.
--   * kind 'photo' is a photo log entry, numbered by the user (unique per inspection or per TIF; the UI checks too);
--     kind 'layout' is a TIF's section 9 site design / layout, at most one a TIF, numbered 1.
--   * At most 20 photos per inspection or TIF: the INSERT itself counts (no CHECK can).
--   * Rows are deleted outright, with their file; nothing names a photo.
-- Named ped_ramp_photos, like the other ped_ramp_* tables.
--
-- One more foreign key to users: uploaded_by (count_user_references counts it).
-- Run after 049, before deploying the S4 code. Safe to run twice.
--
-- Rollback (after rolling the code back): DROP TABLE icid.ped_ramp_photos;

BEGIN;

CREATE TABLE IF NOT EXISTS icid.ped_ramp_photos (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_id   UUID REFERENCES icid.ped_ramp_inspections(id),
    tif_id          UUID REFERENCES icid.ped_ramp_tifs(id),
    property_id     UUID REFERENCES icid.properties(id),
    kind            TEXT NOT NULL DEFAULT 'photo' CHECK (kind IN ('photo', 'layout')),
    photo_number    INT NOT NULL CHECK (photo_number >= 1),
    caption         TEXT,
    storage_path    TEXT NOT NULL,
    content_type    TEXT,
    byte_size       INT CHECK (byte_size >= 0),
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    uploaded_by     UUID NOT NULL REFERENCES icid.users(uuid),
    -- Exactly one parent
    CONSTRAINT chk_ped_ramp_photos_one_parent CHECK ((inspection_id IS NULL) <> (tif_id IS NULL)),
    -- An inspection's photo names its ramp
    CONSTRAINT chk_ped_ramp_photos_inspection_ramp CHECK (inspection_id IS NULL OR property_id IS NOT NULL),
    -- Only a TIF has a site layout
    CONSTRAINT chk_ped_ramp_photos_layout_on_tif CHECK (kind = 'photo' OR tif_id IS NOT NULL)
);
-- A photo number once per inspection, and once per TIF; one layout per TIF
CREATE UNIQUE INDEX IF NOT EXISTS uq_ped_ramp_photos_inspection_number
    ON icid.ped_ramp_photos (inspection_id, photo_number) WHERE inspection_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ped_ramp_photos_tif_number
    ON icid.ped_ramp_photos (tif_id, photo_number) WHERE tif_id IS NOT NULL AND kind = 'photo';
CREATE UNIQUE INDEX IF NOT EXISTS uq_ped_ramp_photos_tif_layout
    ON icid.ped_ramp_photos (tif_id) WHERE tif_id IS NOT NULL AND kind = 'layout';

COMMIT;

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT count(*) FROM icid.ped_ramp_photos;   -- 0
