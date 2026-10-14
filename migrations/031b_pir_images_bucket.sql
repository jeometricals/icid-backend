-- migrations/031b_pir_images_bucket.sql
-- PIR images (Slice R1d, 2026-10-14): a private Supabase Storage bucket for PIRs' scanned sheets.
-- Run after 031_pirs.sql, whose pirs.source_image_url holds these files' paths.
--
--   storage bucket pir-images
--     private          -- only the backend (service role key) reads or writes it; the
--                      -- service role bypasses RLS, so no policies are needed
--     file size limit  10 MB (10485760 bytes), as PIR_IMAGE_MAX_BYTES
--     MIME types       PNG, JPEG and PDF (a PDF is the raw upload; the backend stores its first page as PNG)
--
--   pirs/{pir id}/uploads/{random}          a raw upload, PUT by the client through a signed URL; removed once
--                                           the backend has stored the image
--   pirs/{pir id}/{random}.png | .jpg       the PIR's image; a new one gets a new path, and the old file is
--                                           removed after the PIR points at the new one
--
-- Not an icid table, so schema.sql is unchanged.
--
-- Idempotent: re-running updates the bucket's settings. Run in the Supabase SQL editor, BEFORE deploying the
-- R1d code. Equivalent in the dashboard: Storage -> New bucket -> name "pir-images", Public off, then under its
-- settings restrict the file size to 10 MB and the MIME types to image/png, image/jpeg and application/pdf.

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'pir-images',
    'pir-images',
    false,
    10485760,
    ARRAY['image/png', 'image/jpeg', 'application/pdf']::text[]
)
ON CONFLICT (id) DO UPDATE
SET public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT id, public, file_size_limit, allowed_mime_types FROM storage.buckets WHERE id = 'pir-images';
-- Expected: pir-images | false | 10485760 | {image/png,image/jpeg,application/pdf}
