-- migrations/012_idr_exports_bucket.sql
-- IDR exports (D6a, 2026-10-02): a private Supabase Storage bucket the export endpoint
-- uploads each generated .xlsx to, then hands out as a 10-minute signed download URL.
-- Exports with photos can pass Vercel's 4.5 MB response limit, so the file no longer
-- travels through the function's response.
--
--   storage bucket idr-exports
--     private          -- only the backend (service role key) reads or writes it; the
--                      -- service role bypasses RLS, so no policies are needed
--     object path      {idr_id}/{YYYYMMDD_HHMMSS}_{IDR_<idr_id>_<report date>.xlsx}
--     file size limit  50 MB (the Free plan's per-file ceiling)
--     MIME types       .xlsx, and PDF for the planned PDF export
--
-- Not an icid table, so schema.sql is unchanged. Nothing removes old exports yet: every
-- export is a new object. Revisit when the bucket grows.
--
-- Idempotent: re-running updates the bucket's settings. Run in the Supabase SQL editor.
-- Equivalent in the dashboard: Storage -> New bucket -> name "idr-exports", Public off,
-- then under its settings restrict the file size to 50 MB and the MIME types as below.

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'idr-exports',
    'idr-exports',
    false,
    52428800,
    ARRAY[
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/pdf'
    ]
)
ON CONFLICT (id) DO UPDATE
SET public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT id, public, file_size_limit, allowed_mime_types FROM storage.buckets WHERE id = 'idr-exports';
