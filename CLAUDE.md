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
| `api/v1/` | HTTP endpoints, one module per resource (`auth.py`, `signatures.py`, `users.py`, `projects.py`, `idrs.py`, `attachments.py`, `exports.py`, `contract_items.py`). Each exports a `router`. |
| `api/queries/` | SQL functions, one module per table area. The only place SQL is written. |
| `api/schemas/` | Pydantic request/response models, one module per resource. |
| `api/db/` | Connection plumbing: `connection.py` opens the psycopg connection, `runner.py` exposes `run_query(sql, params)`. |
| `api/storage/` | Supabase Storage plumbing: `client.py` is the only module that imports `supabase`. |
| `api/services/` | Logic spanning several queries or Storage. Endpoints call it; it never builds SQL. `auto_general.py` (the auto-General's aggregation), `attachments.py` (attachment uploads and downloads), `auth.py` (sign-in: the `AuthProvider` interface, `LocalAuthProvider` with bcrypt and JWTs, the `auth_provider` singleton, the `current_user` / `current_admin` dependencies, and the demo-mode dependencies), `demo.py` (deleting a demo user at sign-out), `signatures.py` (a user's signature upload and confirm, and the copy an IDR keeps at submit), and the IDR export: `export.py` (the dispatcher: loads the IDR, allocates and orders the sheets, numbers pages, stamps the inspector's signature on a submitted IDR's pages, stores the file and signs its URL), `export_common.py` (shared layouts and stampers: headers, continuation header, pay items, work force, equipment, safety, the text cascade, the signature), `export_general.py` (the General onto Gen Fr / Gen Bk / Report Cont), `export_swcb.py` (SWCB onto Conc Fr / Conc Bk), `export_ac.py` (AC onto AC Fr / AC Bk), `export_conc_mix.py` (CONC_MIX addendums onto Conc Mix sheets), `export_attachments.py` (report attachments onto pages copied from Sketch Cont) and `xlsx_template.py` (`WorkbookTemplate`: edits the .xlsx package XML directly — cells, styles, sheet copies, pictures, text boxes, print setup). |
| `api/core/` | App-wide configuration — env loading: `DATABASE_URL` and `JWT_SECRET_KEY` (both required at startup), the other JWT settings, and the Supabase Storage settings (attachments and `idr-exports` buckets, signed-URL lifetimes, and the signatures bucket: `SIGNATURE_BUCKET_NAME`, `SIGNATURE_URL_EXPIRY_SECONDS`). No business logic. |
| `tests/v1/` | Pytest suites mirroring `api/v1/`, one file per endpoint module. |
| `schema.sql` | Authoritative DDL for the `icid` schema. `seed.sql` holds mock data; `seed_sidewalk_pay_items.sql` seeds the pay-item catalog (`spec_items`, and `contract_items` for `HWS0023`) and runs after it. `seed_auth_users.sql` seeds the auth users (the admin account and the legacy demo user) and `seed_test_project.sql` the Test Project (`DEMO01`). |
| `migrations/` | Numbered SQL migrations, run by hand in the Supabase SQL editor. A schema change ships as a migration plus the matching `schema.sql` edit. |
| `docs/` | `data-model.md`: developer reference for the tables, the Storage buckets, the auto-General and the migration history. |
| `templates/` | `report_forms.xlsx`, the export base (built from `report_forms_source.xltx` by `scripts/clean_report_template.py`). |
| `scripts/` | One-off local utilities. Just `clean_report_template.py`, which builds the export template. Not imported by the app. Seeds and ad-hoc SQL are run in the Supabase SQL editor. |

### Endpoints

<!-- Update this list when endpoints change -->
- `GET /status`
- `POST /v1/auth/login` — email (matched without regard to case) and password; returns `{access_token, token_type, expires_in, user}`, or 401 `Invalid email or password`
- `GET /v1/auth/me` — the user the bearer token belongs to, with `has_signature` and `signature_set_at`; 401 `Not authenticated`, `Token expired` or `Invalid token`
- `POST /v1/auth/demo` — public; makes a throwaway demo user on `DEMO01` and returns what login returns; 503 when `DEMO01` is missing or 200 demo users already exist
- `POST /v1/auth/logout` — 204 always; stateless, the client drops its token. A demo user signing out is deleted with everything they made

Every route below needs a bearer token (401 without a valid one); see "Sign-in" under the conventions.

- `POST /v1/signatures/upload-request` — body `{content_type: "image/png"}`; returns `{upload_url, storage_path, expires_in}`, a signed URL to PUT the signed-in user's signature PNG to (it replaces their current one); 403 for a demo user
- `POST /v1/signatures/confirm` — body `{signature_type: "drawn" | "uploaded"}`; records the uploaded file as the user's signature and returns the user; 400 `Upload the signature before confirming` when no file is there; 403 for a demo user
- `GET /v1/users/` — 403 for a demo user
- `GET /v1/projects/` — the signed-in user's projects (through `project_users`)
- `GET /v1/projects/{project_id}`
- `POST /v1/idrs/` — create a draft IDR for the signed-in user, its reporter (409 with `existing_idr_id` if one exists for that reporter, project and date)
- `GET /v1/idrs/?project_id=&status=&reporter_uuid=` — list IDRs with `report_count` and `has_general` (all filters optional)
- `GET /v1/idrs/{idr_id}` — IDR plus all its reports, in page order
- `PUT /v1/idrs/{idr_id}/header` — partial update of the shared header fields on a draft
- `POST /v1/idrs/{idr_id}/reports` — add a report (typed by `ReportType`; addendums may name a parent)
- `PUT /v1/idrs/{idr_id}/reports/{report_id}` — replace a report's `report_data` (any JSON object)
- `DELETE /v1/idrs/{idr_id}/reports/{report_id}` — remove a report (its addendums cascade)
- `POST /v1/idrs/{idr_id}/submit` — submit a draft, signed by the signed-in user (locks it, numbers pages, sets `total_pages`, stamps `inspector_signature_path` and `inspector_signed_at`); 400 `Signature required before submitting` when they have no signature, 502 when it can't be copied, 403 `Demo mode: submit is disabled` for a demo user
- `POST /v1/idrs/{idr_id}/reports/{report_id}/attachments/upload-request` — start a two-step upload: records a pending attachment (name, description, file details; `uploaded_by` is the signed-in user) and returns a signed Storage upload URL plus the headers to send; draft only, not on an auto-General
- `POST /v1/idrs/{idr_id}/reports/{report_id}/attachments/upload-complete` — mark a pending attachment uploaded once its file is in Storage (`attachment_id` in the body); draft only
- `PUT /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}` — replace an attachment's name and description; draft only
- `GET /v1/idrs/{idr_id}/reports/{report_id}/attachments` — list a report's uploaded attachments (pending ones left out)
- `GET /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}/download-url` — short-lived signed URL; 404 while pending
- `DELETE /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}` — remove an attachment, pending or uploaded (Storage file, then record); draft only
- `GET /v1/idrs/{idr_id}/export` — an IDR as an .xlsx on the DDC report-forms template (a draft's pages are marked "DRAFT - Not for Submission"), stored in the `idr-exports` bucket; returns `{download_url, filename}`, the URL valid 10 minutes
- `GET /v1/contract_items/?project_id=` — a project's contract items, each joined to its spec item (`item_no`, `description`, `spec_section`, `pay_unit`); `[]` when none

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
- **Sign-in.** Every `/v1` router except `auth` is created with `dependencies=[Depends(current_user)]`, so each of
  its routes returns 401 without a valid bearer token; a new router does the same, and `tests/v1/test_auth.py`
  fails for any `/v1` route left open. An endpoint that needs the user takes
  `user: UserOut = Depends(current_user)` and reads `user.uuid`: the user never comes from a query parameter or a
  request body (`reporter_uuid` and `uploaded_by` are set from the session). `/status`, `/v1/auth/login`,
  `/v1/auth/demo` and `/v1/auth/logout` stay public.
  - **No admin bypass, no ownership checks (yet).** `role == "admin"` changes nothing: an admin lists only the
    projects assigned to them in `project_users`, like anyone else, and `current_admin` is applied nowhere. Any
    signed-in user can read, edit, submit or export any IDR by id, and list IDRs for any reporter
    (`?reporter_uuid=` is a filter, not an identity). Role and ownership enforcement are Phase 2.
  - Endpoint tests use the `admin_client` fixture (signed in as `ADMIN_USER_ROW`, `tests/conftest.py`); the plain
    `client` is for testing what happens without a token.
- **Demo mode.** `POST /v1/auth/demo` is public: it makes a throwaway user (`is_demo`, `demo-<uuid>@icid.local`,
  no password, no role, client `C00001`) and their one `project_users` row on `DEMO01` in a single statement, and
  signs them in. Since anyone can get a demo token, a demo user is kept to their own data:
  - **No submitting.** `require_full_user` on the submit endpoint returns 403 `Demo mode: submit is disabled`.
    Creating, editing, adding reports and attachments, and exporting all stay allowed.
  - **Their own IDRs only.** `demo_idr_fence` sits on every router under `/v1/idrs`: a route with an `idr_id`
    returns 404 unless the demo user is that IDR's reporter, and `GET /v1/idrs/` always filters to them, whatever
    `reporter_uuid` says.
  - **Their own project only.** `demo_project_fence` on the projects and contract-items routers returns 404 for a
    project they aren't assigned to. `GET /v1/users/` is 403 (`no_demo_users`).
  - A new router under an IDR or a project takes the matching fence; a route no demo user should see takes
    `no_demo_users`. Other users pass all three untouched.
  - **Deleted at sign-out.** `POST /v1/auth/logout` with a demo user's valid token removes their attachment files
    from Storage (best-effort), then deletes, in one statement and children first, their attachments, reports,
    IDRs, project assignments and the user row (`api/services/demo.py`). Every delete is tied to `is_demo = true`
    in the SQL itself. Logout returns 204 even if that fails.
  - **Daily backstop.** `icid.cleanup_abandoned_demo_users()` (migration 014) deletes demo users older than 24
    hours, in the same order. It is not scheduled by the migration; the options are pg_cron
    (`cron.schedule('cleanup-abandoned-demos', '0 3 * * *', …)`), a Vercel Cron hitting an admin-only endpoint
    (not built), or running it by hand. They are spelled out in the migration file.
  - At most `MAX_DEMO_USERS` (200) exist at once; past that the endpoint returns 503 until some are deleted.
  - Tests use the `demo_client` fixture (signed in as `DEMO_USER_ROW`).
- **Signatures.** Files live in the private `signatures` bucket; nothing but the backend reads it.
  - **Setting one takes two steps**, like an attachment: `upload-request` signs a URL for
    `users/{user uuid}/signature.png`, the client PUTs the PNG there, and `confirm` checks the file exists and
    records `signature_path`, `signature_type` and `signature_set_at` on the user. The path is fixed per user and
    the upload replaces the file, so a user has one current signature. The bucket itself enforces PNG and 500 KB.
  - **Submitting signs.** The submit endpoint needs the signed-in user to have a signature (400 otherwise). It
    copies their current file to `idrs/{idr id}/inspector_{random}.png`, then runs the one submit statement, which
    stamps that path and the time on the IDR. The copy is the IDR's own: changing the signature later doesn't
    change what was signed, and nothing under `idrs/` is ever overwritten or removed.
  - **The copy comes before the UPDATE.** If the copy fails, the submit is refused (502) and the IDR stays a
    draft. If the UPDATE then fails or loses a race, the copy is left behind unreferenced, and logged.
  - **Demo users have no signatures**: both signature routes return 403 `Demo mode: signatures are not
    available`, and they can't submit anyway.
  - `UserOut` carries `signature_path` internally for the submit flow but never serialises it; responses show
    `has_signature` and `signature_set_at` only. IDR responses do carry `inspector_signature_path`.
  - The signer is whoever submits, not necessarily the IDR's reporter (there are no ownership checks yet).
  - **The export prints it.** For a submitted IDR with `inspector_signature_path`, `export.py` downloads that
    file once and stamps it, with the signed date, on every printed page that has an "Inspector's Signature"
    line: Gen Bk, Conc Bk, AC Bk, Conc Mix, Report Cont and every attachment page, copies included. Front pages
    have no line. Each module declares its page's `SIGNATURE_LAYOUT` (Report Cont's is in `export_common.py`);
    the dispatcher maps a printed sheet to its layout by name, so a copy signs where its original does. A new
    form with a signature line adds a layout and an entry in `SIGNATURE_LAYOUTS`.
    - The image is letterboxed into the signature line's cell plus the blank row above it (209 x 34 px; Conc
      Mix 224 x 32), since the line alone is one 17 px row. The date goes in the line's Date cell as m/d/yy, the
      day it was in New York (`FORM_TIMEZONE`), not the UTC day.
    - A draft is never signed, and its signature isn't even fetched. An IDR submitted before signatures
      (no path), or one whose file can't be fetched or read, exports with blank lines; the failure is logged
      and the export still completes.
    - The Resident Engineer's line stays blank (Phase 1b).
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

- Endpoints exist for `users`, `projects`, `idrs` (with attachments and the .xlsx export) and `contract_items` (read-only;
  the catalog is seeded, with no write endpoint yet). `api/schemas/` also defines models for
  clients, form templates and the join tables — those schemas run
  ahead of the endpoints and may not match `schema.sql` exactly. Verify against `schema.sql`
  before building on them.
- `api/v1/`, `api/db/`, `api/core/` and `api/schemas/` have no `__init__.py`; only
  `api/queries/` does. Imports work regardless, but don't take the inconsistency as intent.
- CORS is `allow_origins=["*"]`. Tighten before production.
- `POST /v1/auth/login` has no rate limiting, and there is no password-change endpoint (passwords are rotated
  in SQL). Both are Phase 2.
- Demo mode leaves files behind: attachment files of demo users purged by the daily cleanup (a SQL function can't
  reach Storage), and every demo export in `idr-exports`. Nothing points at them afterwards; a bucket sweep would
  have to find them. `POST /v1/auth/demo` is also unthrottled apart from the 200-user ceiling.
- No multi-statement transactions: `run_query` runs one statement per connection. Work that must be atomic is
  written as one statement (data-modifying CTEs), as the demo user's insert and delete are.
- `uq_users_email` is case-sensitive on the stored value, while sign-in looks emails up without regard to case.
  `Reza@icid.local` and `reza@icid.local` could coexist as separate rows, and login would pick the oldest. Fix in
  the next schema migration slice: drop `uq_users_email` and add
  `UNIQUE INDEX idx_users_email_lower ON icid.users (lower(email))`. Low priority.
- Four overlapping READMEs exist (`README.md`, `README_01.md`, `README-db.md`,
  `README-api.md`) with conflicting run instructions. The one that works is
  `uvicorn api.index:app --reload`.
