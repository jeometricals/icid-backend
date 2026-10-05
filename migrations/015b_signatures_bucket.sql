-- migrations/015b_signatures_bucket.sql
-- Signatures (Slice I0, 2026-10-05): a private Supabase Storage bucket for signature images.
-- Run after 015_signatures.sql, whose columns hold these files' paths.
--
--   storage bucket signatures
--     private          -- only the backend (service role key) reads or writes it; the
--                      -- service role bypasses RLS, so no policies are needed
--     file size limit  500 KB (512000 bytes)
--     MIME types       PNG only
--
-- Not an icid table, so schema.sql is unchanged.
--
-- Idempotent: re-running updates the bucket's settings. Run in the Supabase SQL editor.
-- Equivalent in the dashboard: Storage -> New bucket -> name "signatures", Public off,
-- then under its settings restrict the file size to 500 KB and the MIME types to image/png.

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'signatures',
    'signatures',
    false,
    512000,
    ARRAY['image/png']::text[]
)
ON CONFLICT (id) DO UPDATE
SET public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT id, public, file_size_limit, allowed_mime_types FROM storage.buckets WHERE id = 'signatures';
-- Expected: signatures | false | 512000 | {image/png}
