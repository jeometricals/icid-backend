# CLAUDE.md

Standing instructions for anyone — human or Claude Code — working in this repo.

## 1. What this repo is

This is the backend for **ICID (Integrated Construction Information Database)**, a SaaS
platform for construction inspection reporting. Inspectors are assigned to projects, file
reports against them, and complete structured forms attached to those reports.

Stack: **Python + FastAPI + Postgres** (Supabase), accessed with **psycopg 3**, deployed on
**Vercel** as a serverless function. `api/index.py` is the entrypoint — `vercel.json` routes
every request to it.

## 2. Folder structure

| Path | What lives here |
|---|---|
| `api/index.py` | FastAPI app: CORS, global exception handler, router registration, `/status`. |
| `api/v1/` | HTTP endpoints, one module per resource (`users.py`, `projects.py`, `idrs.py`, `attachments.py`, `exports.py`, `contract_items.py`, `debug.py`). Each exports a `router`. |
| `api/queries/` | SQL functions, one module per table area. The only place SQL is written. |
| `api/schemas/` | Pydantic request/response models, one module per resource. |
| `api/db/` | Connection plumbing: `connection.py` opens the psycopg connection, `runner.py` exposes `run_query(sql, params)`. |
| `api/storage/` | Supabase Storage plumbing: `client.py` is the only module that imports `supabase`. |
| `api/services/` | Logic spanning several queries or Storage. Endpoints call it; it never builds SQL. `auto_general.py` (the auto-General's aggregation), `attachments.py` (attachment uploads and downloads), and the IDR export: `export.py` (the dispatcher: loads the IDR, allocates and orders the sheets, numbers pages, stores the file and signs its URL), `export_common.py` (shared layouts and stampers: headers, continuation header, pay items, work force, equipment, safety, the text cascade), `export_general.py` (the General onto Gen Fr / Gen Bk / Report Cont), `export_swcb.py` (SWCB onto Conc Fr / Conc Bk), `export_ac.py` (AC onto AC Fr / AC Bk; header only so far), `export_conc_mix.py` (CONC_MIX addendums onto Conc Mix sheets), `export_attachments.py` (report attachments onto pages copied from Sketch Cont) and `xlsx_template.py` (`WorkbookTemplate`: edits the .xlsx package XML directly — cells, styles, sheet copies, pictures, text boxes, print setup). |
| `api/core/` | App-wide configuration — env loading: `DATABASE_URL`, and the Supabase Storage settings (attachments and `idr-exports` buckets, signed-URL lifetimes). No business logic. |
| `tests/v1/` | Pytest suites mirroring `api/v1/`, one file per endpoint module. |
| `schema.sql` | Authoritative DDL for the `icid` schema. `seed.sql` holds mock data; `seed_sidewalk_pay_items.sql` seeds the pay-item catalog (`spec_items`, and `contract_items` for `HWS0023`) and runs after it. |
| `migrations/` | Numbered SQL migrations, run by hand in the Supabase SQL editor. A schema change ships as a migration plus the matching `schema.sql` edit. |
| `docs/` | `data-model.md`: developer reference for the tables, the Storage buckets, the auto-General and the migration history. |
| `templates/` | `report_forms.xlsx`, the export base (built from `report_forms_source.xltx` by `scripts/clean_report_template.py`). |
| `scripts/` | One-off local utilities (seeding, ad-hoc SQL). Not imported by the app. |

### Endpoints

<!-- Update this list when endpoints change -->
- `GET /status`
- `GET /v1/users/`
- `GET /v1/projects/?user_id=` (user uuid)
- `GET /v1/projects/{project_id}`
- `POST /v1/idrs/` — create a draft IDR (409 with `existing_idr_id` if one exists for that reporter, project and date)
- `GET /v1/idrs/?project_id=&status=&reporter_uuid=` — list IDRs with `report_count` and `has_general` (all filters optional)
- `GET /v1/idrs/{idr_id}` — IDR plus all its reports, in page order
- `PUT /v1/idrs/{idr_id}/header` — partial update of the shared header fields on a draft
- `POST /v1/idrs/{idr_id}/reports` — add a report (typed by `ReportType`; addendums may name a parent)
- `PUT /v1/idrs/{idr_id}/reports/{report_id}` — replace a report's `report_data` (any JSON object)
- `DELETE /v1/idrs/{idr_id}/reports/{report_id}` — remove a report (its addendums cascade)
- `POST /v1/idrs/{idr_id}/submit` — submit a draft (locks it, numbers pages, sets `total_pages`)
- `POST /v1/idrs/{idr_id}/reports/{report_id}/attachments/upload-request` — start a two-step upload: records a pending attachment (name, description, file details, `uploaded_by`) and returns a signed Storage upload URL plus the headers to send; draft only, not on an auto-General
- `POST /v1/idrs/{idr_id}/reports/{report_id}/attachments/upload-complete` — mark a pending attachment uploaded once its file is in Storage (`attachment_id` in the body); draft only
- `PUT /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}` — replace an attachment's name and description; draft only
- `GET /v1/idrs/{idr_id}/reports/{report_id}/attachments` — list a report's uploaded attachments (pending ones left out)
- `GET /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}/download-url` — short-lived signed URL; 404 while pending
- `DELETE /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}` — remove an attachment, pending or uploaded (Storage file, then record); draft only
- `GET /v1/idrs/{idr_id}/export` — an IDR as an .xlsx on the DDC report-forms template (a draft's pages are marked "DRAFT - Not for Submission"), stored in the `idr-exports` bucket; returns `{download_url, filename}`, the URL valid 10 minutes
- `GET /v1/contract_items/?project_id=` — a project's contract items, each joined to its spec item (`item_no`, `description`, `spec_section`, `pay_unit`); `[]` when none
- `GET /debug/schema` — dev-only

## 3. Modularity rules

Any change must follow these.

1. **One file, one purpose.** A file in `api/v1/` handles endpoints for exactly one resource
   type. A file in `api/queries/` handles SQL for exactly one table area. Don't mix concerns
   across files — if a new resource appears, it gets its own module in each layer.
2. **Endpoints never write SQL.** `api/v1/` calls functions in `api/queries/`; it never
   imports `run_query` and never builds a query string. No exceptions — this is what lets the
   database change without touching endpoints. If an endpoint needs data, add a query
   function for it, even a one-line one.
3. **Endpoints never format complex response shapes inline.** Response construction goes
   through a Pydantic model in `api/schemas/`, declared as `response_model=` on the route.
4. **No copy-paste.** If the same logic shows up twice, extract a helper — into the query
   module if it's SQL-shaped, into the schema module if it's response-shaped.
5. **Every function has a docstring.** 2–3 lines: what it does, what it takes, what it
   returns. Not why. Not history. Not a changelog.
6. **Tables live in the `icid` schema, not `public`.** Every query qualifies its tables as
   `icid.tablename`. An unqualified table name is a bug.

## 4. Coding conventions

- **Type hints on all function signatures**, arguments and return type.
- **psycopg (v3) for database access**, always through `api.db.runner.run_query`. Never open
  a raw connection in a query module.
- **Always parameterize SQL** with `%s` placeholders. Never f-string or concatenate values
  into a query.
- **Rows are dicts, not tuples.** `run_query` uses psycopg's `dict_row` factory, so every row
  comes back keyed by column name. Access columns by name — `row["email"]`, never `row[1]`.
  Positional access is a bug even when it happens to work.
  - The key is the column's **output** name, so a `SELECT u.client_id AS employer` is read as
    `row["employer"]`. Alias deliberately and the endpoint reads cleanly.
  - `run_query` returns `None` for statements with no result set, and for failures the
    endpoint surfaces as a 500. `None` and `[]` mean different things — don't conflate them.
- **Adding an endpoint means adding tests** under `tests/v1/`, in the file matching the
  endpoint module. Tests patch the query layer (`patch("api.queries.<module>.run_query")`)
  and return **dict** rows matching the real column names; they do not hit the database.
- **Never commit** `venv/`, `.env`, `__pycache__/`, `.pytest_cache/`, or `.xlsx` files —
  except `templates/report_forms.xlsx`, which ships with the app as the DDC export base.
  `.gitignore` covers all of these — check before every commit anyway.

## 5. Rules for Claude Code

- **Propose before you change.** When asked to add a feature, list the files you intend to
  touch and wait for confirmation. This is how scope creep gets caught early.
- **Fix one bug at a time.** If you notice unrelated bugs while fixing something, mention
  them and move on — do not touch them. Scope creep in fixes hurts more than it helps.
- **`schema.sql` is authoritative.** Any database schema change updates `schema.sql` in the
  same change. Never alter tables only through Supabase — the file must reflect reality.
- **Flag downstream docs.** If a change affects the ER diagram or the working document, say
  so explicitly and remind me to update them. Don't assume I'll remember.
- **Don't invent structure.** New resources follow the existing four-layer pattern
  (`v1` → `queries` → `db`, shaped by `schemas`). If a change seems to need a new layer or
  pattern, raise it before writing code.

## 6. Known rough edges and technical debt

Not rules — current state, documented so nobody mistakes these for the intended pattern.

- `api/v1/debug.py` exposes the live `icid` schema and is **dev-only**. Delete before
  production. It has no test coverage.
- Endpoints exist for `users`, `projects`, `idrs` (with attachments and the .xlsx export) and `contract_items` (read-only;
  the catalog is seeded, with no write endpoint yet). `api/schemas/` also defines models for
  clients, form templates and the join tables — those schemas run
  ahead of the endpoints and may not match `schema.sql` exactly. Verify against `schema.sql`
  before building on them.
- `api/v1/`, `api/db/`, `api/core/` and `api/schemas/` have no `__init__.py`; only
  `api/queries/` does. Imports work regardless, but don't take the inconsistency as intent.
- CORS is `allow_origins=["*"]`. Tighten before production.
- Four overlapping READMEs exist (`README.md`, `README_01.md`, `README-db.md`,
  `README-api.md`) with conflicting run instructions. The one that works is
  `uvicorn api.index:app --reload`.
