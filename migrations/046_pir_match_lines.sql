-- migrations/046_pir_match_lines.sql
--
-- R1h.1a: match lines. A PIR that spans several DOT sheets joins them with lettered match lines (A..Z, AA..ZZ). Each
-- line is drawn on one page, as two endpoints in that page's native pixels (as flags and segments are); two pages pair
-- when they carry the same label in the same PIR. A label is live on at most one row per page (the partial unique
-- index) and on at most two pages of a PIR (the API's statements check it). Soft-deleted only, like features and
-- segments; replacing a PIR's scan retires its lines.
--
-- The code that reads match lines (the Construction Report, the Smart PIR import) needs this table: run it before
-- deploying that code. Adds two foreign keys to users (created_by, deleted_by). Safe to run twice.
--
-- Rollback (after rolling the code back): DROP TABLE icid.pir_match_lines;

BEGIN;

CREATE TABLE IF NOT EXISTS icid.pir_match_lines (
    match_line_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pir_page_id    UUID NOT NULL REFERENCES icid.pir_pages(pir_page_id),
    label          TEXT NOT NULL,
    x1             REAL NOT NULL,
    y1             REAL NOT NULL,
    x2             REAL NOT NULL,
    y2             REAL NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at     TIMESTAMPTZ,
    deleted_by     UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT chk_match_lines_label CHECK (label ~ '^[A-Z]{1,2}$'),
    CONSTRAINT chk_match_lines_length CHECK (x1 <> x2 OR y1 <> y2)
);
CREATE UNIQUE INDEX IF NOT EXISTS pir_match_lines_page_label_live_uq
    ON icid.pir_match_lines (pir_page_id, label) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS pir_match_lines_page_idx ON icid.pir_match_lines (pir_page_id);

COMMIT;
