-- migrations/050b_pedramp_buckets.sql
-- Pedramp Phase 3 (S2, S4, S5): two private Supabase Storage buckets.
--
--   storage bucket pedramp-photos
--     private          -- only the backend (service role key) reads or writes it; the service role bypasses RLS,
--                      -- so no policies are needed
--     file size limit  10 MB (10485760 bytes)
--     MIME types       JPEG, PNG and HEIC (an iPhone's; the backend converts it to JPEG on confirm)
--     staging/{parent id}/{random}.{ext}      a raw upload, PUT by the client through a signed URL; removed once
--                                             the backend has stored the photo
--     inspections/{inspection id}/{photo id}.jpg | .png
--     tifs/{tif id}/{photo id}.jpg | .png
--
--   storage bucket pedramp-exports
--     private, 50 MB (52428800 bytes), PDF and ZIP; handed out through signed URLs
--     inspections/{inspection id}/{random}.pdf | .zip   the DOT form (and its TIFs) filled at approval
--     bulk/{project id}/{random}.zip                    a listing's bulk download
--
-- Not icid tables, so schema.sql is unchanged.
--
-- Idempotent: re-running updates the buckets' settings. Run in the Supabase SQL editor, before deploying the S2 code.

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES
    ('pedramp-photos', 'pedramp-photos', false, 10485760, ARRAY['image/jpeg', 'image/png', 'image/heic']::text[]),
    ('pedramp-exports', 'pedramp-exports', false, 52428800, ARRAY['application/pdf', 'application/zip']::text[])
ON CONFLICT (id) DO UPDATE
SET public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT id, public, file_size_limit, allowed_mime_types FROM storage.buckets WHERE id LIKE 'pedramp-%' ORDER BY id;
-- Expected: pedramp-exports | false | 52428800 | {application/pdf,application/zip}
--           pedramp-photos  | false | 10485760 | {image/jpeg,image/png,image/heic}
