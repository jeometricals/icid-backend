-- migrations/059b_tax_maps_bucket.sql
-- Slice Y1.5: a private Supabase Storage bucket for each site's DOF Digital Tax Map PDF.
--
--   storage bucket tax-maps
--     private          -- only the backend (service role key) reads or writes it; the service role bypasses RLS,
--                      -- so no policies are needed
--     file size limit  10 MB (10485760 bytes); DOF's block maps run 0.1-2 MB
--     MIME types       PDF
--     {property id}/{DOF map id}.pdf          a map fetched from DOF's map library
--     {property id}/upload-{random}.pdf       a map an admin uploaded
--     staging/{property id}/{random}.pdf      a raw upload, PUT by the client through a signed URL; removed once
--                                             the backend has stored it
--
-- Not an icid table, so schema.sql is unchanged.
--
-- Idempotent: re-running updates the bucket's settings. Run in the Supabase SQL editor with 059, before deploying the
-- Slice Y1.5 code.

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('tax-maps', 'tax-maps', false, 10485760, ARRAY['application/pdf']::text[])
ON CONFLICT (id) DO UPDATE
SET public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT id, public, file_size_limit, allowed_mime_types FROM storage.buckets WHERE id = 'tax-maps';
-- Expected: tax-maps | false | 10485760 | {application/pdf}
