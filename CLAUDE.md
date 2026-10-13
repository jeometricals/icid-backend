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
| `api/v1/` | HTTP endpoints, one module per resource (`auth.py`, `signatures.py`, `admin.py`, `admin_users.py`, `admin_projects.py`, `admin_pay_items.py`, `users.py`, `projects.py`, `idrs.py`, `reviews.py`, `field_edits.py`, `attachments.py`, `exports.py`, `contract_items.py`, `quantities.py`). Each exports a `router`. `_shared.py` is not a router: it holds what several of them use (reading the date and discipline filters, reading a text field off a request body (the admin project form's), checking a new password, shaping an IDR list's rows). |
| `api/queries/` | SQL functions, one module per table area. The only place SQL is written. |
| `api/schemas/` | Pydantic request/response models, one module per resource. |
| `api/db/` | Connection plumbing: `connection.py` opens the psycopg connection, `runner.py` exposes `run_query(sql, params)`. |
| `api/storage/` | Supabase Storage plumbing: `client.py` is the only module that imports `supabase`. |
| `api/services/` | Logic spanning several queries or Storage. Endpoints call it; it never builds SQL. `auto_general.py` (the auto-General's aggregation), `attachments.py` (attachment uploads and downloads), `auth.py` (sign-in: the `AuthProvider` interface, `LocalAuthProvider` with bcrypt and JWTs, the `auth_provider` singleton, the `current_user` / `current_admin` dependencies, and the demo-mode dependencies), `demo.py` (deleting a demo user at sign-out), `idr_purge.py` (deleting a soft-deleted IDR for good: its row, then its files in the three buckets), `field_edits.py` (a reviewer's edit: the field-path grammar, finding the field in a report, running the edit, keeping an auto-General in step), `signatures.py` (a user's signature upload and confirm, and the copy an IDR keeps at submit), `pay_items.py` (what a project's pay item may hold: the rules for each field, shared by the admin's form and the CSV import), `pay_item_units.py` (the units a pay item can be bid in and the short form each is stored in), `pay_item_import.py` (a pay-item CSV: reading the file, checking every row, sorting the rows into new items, overwrites and errors), `disciplines.py` (which disciplines each report type covers, and the reverse: read by the quantities reader and the archive filter), `quantities.py` (an IDR's pay items as rows of `icid.quantities`: `extract_rows` reads them off its reports, `quantity_rows_for_idr` readies them for the approving statement, `write_quantities` replaces an approved IDR's rows for the backfill), and the IDR export: `export.py` (the dispatcher: loads the IDR, allocates and orders the sheets, numbers pages, stamps the inspector's signature on an IDR's pages from submission on and the Resident Engineer's on an approved one, stores the file and signs its URL), `export_common.py` (shared layouts and stampers: headers, continuation header, pay items, work force, equipment, safety, the text cascade, the two signatures, and how a redline is drawn), `export_redlines.py` (reviewer redlines: reads the IDR's edits as each field's chain and each pay item's rows), `export_general.py` (the General onto Gen Fr / Gen Bk / Report Cont), `export_swcb.py` (SWCB onto Conc Fr / Conc Bk), `export_ac.py` (AC onto AC Fr / AC Bk), `export_conc_mix.py` (CONC_MIX addendums onto Conc Mix sheets), `export_conc_cyl.py` (CONC_CYL addendums onto Conc Cyl sheets), `export_attachments.py` (report attachments onto pages copied from Sketch Cont) and `xlsx_template.py` (`WorkbookTemplate`: edits the .xlsx package XML directly — cells, text runs, styles, sheet copies, pictures, text boxes, print setup). |
| `api/core/` | App-wide configuration — env loading: `DATABASE_URL` and `JWT_SECRET_KEY` (both required at startup), the other JWT settings, `CRON_SECRET` (what the scheduler sends; optional), and the Supabase Storage settings (attachments and `idr-exports` buckets, signed-URL lifetimes, and the signatures bucket: `SIGNATURE_BUCKET_NAME`, `SIGNATURE_URL_EXPIRY_SECONDS`). No business logic. |
| `tests/v1/` | Pytest suites mirroring `api/v1/`, one file per endpoint module. |
| `schema.sql` | Authoritative DDL for the `icid` schema. `seed.sql` holds mock data; `seed_sidewalk_pay_items.sql` seeds the pay-item catalog (`spec_items`, and `contract_items` for `HWS0023`, each row carrying its spec item's number, description, unit and section) and runs after it. `seed_auth_users.sql` seeds the admin account and its project assignments and `seed_test_project.sql` the Test Project (`DEMO01`). |
| `migrations/` | Numbered SQL migrations, run by hand in the Supabase SQL editor. A schema change ships as a migration plus the matching `schema.sql` edit. |
| `docs/` | `data-model.md`: developer reference for the tables, the Storage buckets, the auto-General and the migration history. |
| `README.md` | How to run, configure, test and deploy the backend. The one README. |
| `templates/` | `report_forms.xlsx`, the export base (built from `report_forms_source.xltx` by `scripts/clean_report_template.py`). `conc_cyl_source.xlsx` is the DDC Data Sheet for Concrete Test Cylinders, the source of the template's Conc Cyl tab. |
| `scripts/` | One-off local utilities: `clean_report_template.py`, which builds the export template, `merge_conc_cyl_template.py`, which added the Conc Cyl tab to `report_forms_source.xltx` (already run; it refuses to run twice), and `backfill_quantities.py`, which fills `icid.quantities` for the IDRs already approved (it imports the app's queries and services, and works on the database `.env` names). Not imported by the app. Seeds and ad-hoc SQL are run in the Supabase SQL editor. |

### Endpoints

<!-- Update this list when endpoints change -->
- `GET /status`
- `POST /v1/auth/login` — email (matched without regard to case) and password; returns `{access_token, token_type, expires_in, user}`, or 401 `Invalid email or password`
- `GET /v1/auth/me` — the user the bearer token belongs to, with `has_signature` and `signature_set_at`; 401 `Not authenticated`, `Token expired` or `Invalid token`
- `POST /v1/auth/demo` — public; makes a throwaway demo user on `DEMO01` and returns what login returns; 503 when `DEMO01` is missing or 200 demo users already exist
- `POST /v1/auth/logout` — 204 always; stateless, the client drops its token. A demo user signing out is deleted with everything they made

Every route below needs a bearer token (401 without a valid one); see "Sign-in" under the conventions.

- `POST` or `GET /v1/admin/cleanup-demos` — deletes demo users older than 24 hours; returns `{purged_count}`. For a signed-in admin, or the scheduler sending `CRON_SECRET` as its bearer token; 403 for anyone else signed in
- `GET /v1/admin/users` — admin only: every user, demo users included, by last name, first name and email, under `data`; each as `/v1/auth/me` returns a user (`uuid`, `email`, `first_name`, `last_name`, `role`, `is_demo`, `has_signature`, `signature_set_at`)
- `POST /v1/admin/users` — admin only: body `{email, first_name, last_name?, password, is_admin?}`; creates a user who signs in with that password and returns them under `data`, 201. 400 for a blank or malformed email, a blank or over-long name, or a password under 6 characters or over 72 bytes; 409 `{detail, existing_user_uuid}` when the email is taken, whatever its capitals
- `PATCH /v1/admin/users/{user_id}` — admin only: body `{first_name?, last_name?, email?, is_admin?, is_demo?}`; changes what is sent and returns the user under `data` (`role` is `admin` or null). Names and email are trimmed. 400 when nothing is sent (a null counts as not sent), a name is blank or over 100 characters, the email is blank or malformed, an admin sends `is_admin: false` for themselves (`You cannot revoke your own admin role.`), or `is_demo: true` is sent for a real user (`Cannot change a real user to a demo user.`); 404 for an unknown user; 409 `{detail, existing_user_uuid}` when another user has the email, whatever its capitals
- `POST /v1/admin/users/{user_id}/password` — admin only: body `{new_password}`; replaces the user's password and returns the user under `data`. Any user, the admin's own row included. 400 for a password under 6 characters or over 72 bytes, 404 for an unknown user
- `GET /v1/admin/projects` — admin only: every project, whoever is assigned to it, by project id, under `data`; each as `GET /v1/projects/{project_id}` returns one (`project_id`, `project_name`, `project_description`, `registration_code`, `borough`, `status`)
- `POST /v1/admin/projects` — admin only: body `{project_id, project_name, project_description, borough, registration_code?}`; creates the project and returns it under `data`, 201. 400 for a blank or over-long value, or a `project_id` with anything but letters, digits, hyphens and underscores; 409 `{detail, existing_project_id}` when the id is taken
- `GET /v1/admin/projects/{project_id}/pay-items` — admin only: every pay item of the project, by budget code then ref: `{status, message, data: [{pay_item_uuid, pay_item_ref, description, unit, quantity, unit_price, budget_code, spec_section}]}`. 404 for an unknown project, as on the four below
- `POST /v1/admin/projects/{project_id}/pay-items` — admin only: body `{pay_item_ref, description, unit, quantity, unit_price, budget_code, spec_section?}`; creates the pay item and returns it under `data`, 201. 400 for a blank or over-long value, an unknown unit, or a negative or over-precise amount; 409 `{detail, existing_pay_item_uuid}` when the project has that ref under that budget code
- `PATCH /v1/admin/projects/{project_id}/pay-items/{pay_item_uuid}` — admin only: any of the seven fields; returns the item under `data`. 400 when nothing is sent (a null counts as not sent, except `spec_section`, which it clears) or a value doesn't fit, 404 for an item the project doesn't have, 409 as on create
- `DELETE /v1/admin/projects/{project_id}/pay-items/{pay_item_uuid}` — admin only: deletes the item, 204; 404 for an item the project doesn't have
- `DELETE /v1/admin/projects/{project_id}/pay-items` — admin only: deletes every pay item of the project; returns `{status, message, data: {deleted_count}}`
- `POST /v1/admin/projects/{project_id}/pay-items/import/preview` — admin only: a CSV as the multipart field `file`; writes nothing and returns `{status, message, data: {parsed_rows, new_count, overwrite_count, error_count, ignored_columns, new, overwrites, errors}}`. 400 for a file that isn't UTF-8, isn't a CSV, has no header row or lacks a required column; 413 over 2 MB
- `POST /v1/admin/projects/{project_id}/pay-items/import/commit` — admin only: the same upload, read and checked again, then written in one statement; returns `{status, message, data: {inserted, updated}}`. 400 `{detail, error_count, errors}` when any row has an error (nothing is written), 400 for a file with no rows, and the preview's 400s and 413
- `POST /v1/signatures/upload-request` — body `{content_type: "image/png"}`; returns `{upload_url, storage_path, expires_in}`, a signed URL to PUT the signed-in user's signature PNG to (it replaces their current one); 403 for a demo user
- `POST /v1/signatures/confirm` — body `{signature_type: "drawn" | "uploaded"}`; records the uploaded file as the user's signature and returns the user; 400 `Upload the signature before confirming` when no file is there; 403 for a demo user
- `GET /v1/users/` — 403 for a demo user
- `POST /v1/users/me/password` — body `{current_password, new_password}`; the signed-in user changes their own password; returns `{status, message, data: null}`. 400 for a blank current password, `Current password is incorrect`, a new one under 6 characters or over 72 bytes, or `New password must differ from current`; 403 for a demo user
- `GET /v1/projects/` — the signed-in user's projects (through `project_users`), each once, with `roles` (the project roles they hold on it) and `user_role` (the label)
- `GET /v1/projects/{project_id}`
- `GET /v1/projects/{project_id}/roles` — admin only: who holds which role on the project, one entry per user and role (demo users left out)
- `POST /v1/projects/{project_id}/roles` — admin only: body `{user_uuid, role: "inspector" | "oe" | "re", action: "grant" | "revoke"}`; returns the project's roles as they now stand. Safe to repeat: granting a role already held, or revoking one not held, is a 200 that changes nothing. 404 for an unknown project or user, 400 for a demo user
- `GET /v1/projects/{project_id}/quantities?from=&to=&pay_item=&budget_code=&discipline=&inspector_uuid=` — the pay-item quantities of the project's approved IDRs, newest work date first: `{rows, totals_by_unit, row_count, truncated}` under `data`. All filters optional; `pay_item`, `budget_code` and `discipline` repeat. For a user on the project, or an admin: 403 otherwise, 404 for an unknown project, 400 for a bad date, `from` after `to`, or an unknown discipline
- `POST /v1/idrs/` — create a draft IDR for the signed-in user, its reporter (409 with `existing_idr_id` if one exists for that reporter, project and date)
- `GET /v1/idrs/?project_id=&status=&reporter_uuid=&idr_number=&work_date=&from=&to=&discipline=` — list IDRs (the archive) with `report_count`, `has_general`, `disciplines` and the names of the inspector and reviewers (all filters optional; `discipline` repeats). Deleted IDRs and other people's drafts are left out; an admin can add `include_deleted=true` and `include_all_drafts=true` (400 for anyone else who sends either as true). 400 for a bad date, `work_date` with `from` or `to`, `from` after `to`, or an unknown discipline
- `GET /v1/idrs/{idr_id}` — IDR plus all its reports, in page order, and `field_edits`: every edit reviewers made on it, oldest first, each with `editor_name` and `editor_initials`
- `PUT /v1/idrs/{idr_id}/header` — partial update of the shared header fields on a draft
- `POST /v1/idrs/{idr_id}/reports` — add a report (typed by `ReportType`; addendums may name a parent)
- `PUT /v1/idrs/{idr_id}/reports/{report_id}` — replace a report's `report_data` (any JSON object)
- `DELETE /v1/idrs/{idr_id}/reports/{report_id}` — remove a report (its addendums cascade)
- `POST /v1/idrs/{idr_id}/submit` — submit a draft, signed by the signed-in user (locks it, numbers pages, sets `total_pages`, stamps `inspector_signature_path` and `inspector_signed_at`, clears any return); 400 `Signature required before submitting` when they have no signature, 502 when it can't be copied, 403 `Demo mode: submit is disabled` for a demo user, 403 `Role required: inspector` for a user who isn't an inspector on the project
- `GET /v1/idrs/queue?status=submitted|stage1_review|stage2_review` — one review queue, oldest submission first, on the projects where the signed-in user works it (OE or RE; RE only for `stage2_review`); an admin sees every project. Rows are shaped as the IDR list's, `disciplines` included
- `POST /v1/idrs/{idr_id}/accept-stage1` — OE or RE picks a submitted IDR up: `submitted` → `stage1_review`, sets `stage1_reviewer_uuid` and `stage1_accepted_at` (and clears `stage2_accepted_at`). Body `{idr_number}`, needed the first time (400 without it); an IDR that has a number keeps it. 409 with `existing_idr_id` when the number is in use on the project
- `POST /v1/idrs/{idr_id}/approve-stage1` — `stage1_review` → `stage2_review`; only the Stage 1 reviewer (403 for another OE or RE), and only once they have approved, revised or added every pay item at this stage: otherwise 400 `{detail, untouched: [{pay_item_id, report_id, item_no, budget_code}]}`
- `POST /v1/idrs/{idr_id}/accept-stage2` — an RE becomes `re_reviewer_uuid` and `stage2_accepted_at` is stamped; the status stays `stage2_review`, and the last to accept wins
- `POST /v1/idrs/{idr_id}/approve-stage2` — final approval, signed: `stage2_review` → `approved`, stamps `re_signature_path` and `re_signed_at`, and writes the IDR's pay-item quantities to `icid.quantities`; only the RE reviewer, and only once they have attested to every pay item at Stage 2 (the same 400 with `untouched`); 400 `Signature required before approving`, 502 when the signature can't be copied
- `POST /v1/idrs/{idr_id}/return` — body `{to: "inspector" | "oe", comment}`; back to `draft` (inspector) or, from Stage 2, to `stage1_review` (OE), with `return_reason` and `returned_from`; clears `stage2_accepted_at`, and `stage1_accepted_at` too when it goes to the inspector; a return from Stage 2 also clears `re_reviewer_uuid`, so an RE has to accept the IDR again when it comes back; only the current stage's reviewer; 400 for a blank comment
- `PATCH /v1/idrs/{idr_id}/field` — the current stage's reviewer (or an admin) edits one field of an IDR in review: body `{report_id, field_path, new_value}` (`report_id` left out for `header.<column>`). The new value is written into the IDR and the old one logged. Returns the IDR with its reports and `field_edits`. 400 when the IDR isn't in review, the path isn't a field of the report, the value doesn't fit, or nothing changes; 403 for anyone but that reviewer; 409 if the field changed meanwhile
- `POST /v1/idrs/{idr_id}/pay-items/{pay_item_id}/revise` — same caller: body `{revised_quantity}`; the pay item is found by its id in whichever report holds it. 404 when no report of the IDR holds it
- `POST /v1/idrs/{idr_id}/pay-items/{pay_item_id}/approve` — same caller, no body: logs their approval of the item as it stands (nothing in the report changes) and returns the IDR with `field_edits`. Approving an item they have already attested to at this stage is a 200 that logs nothing
- `POST /v1/idrs/{idr_id}/pay-items/add` — same caller: body `{report_id, item_no, budget_code, quantity, unit, description}`; appends a pay item to that report (General, SWCB or AC), logged as added by the reviewer
- `POST /v1/idrs/{idr_id}/reports/{report_id}/trucks/add` — same caller: body is a truck with any of the keys the report form saves (`truckOrTicketNo`, `slump`, `loadSizeCy`, `endBatch`, `mixingRevs`, `startDischTime`, `endDischTime`, `airContent`, `concTemp`, `cylinderNumbers`, `inspectionSticker`); appends it to that Concrete Truck & Mix Info report's trucks, logged as added by the reviewer. 400 for a report that isn't a CONC_MIX, or a truck with neither a truck or ticket number nor a slump
- `POST /v1/idrs/{idr_id}/admin/unlock` — admin only: an approved IDR (or one in `stage2_review`) goes to `stage2_review` with `re_signature_path`, `re_signed_at`, `re_reviewer_uuid` and `stage2_accepted_at` cleared, so an RE must accept and approve again; the IDR number stays and its quantity rows are removed. 400 for a draft, submitted, Stage 1 or deleted IDR
- `POST /v1/idrs/{idr_id}/admin/delete` — admin only: soft delete at any status (`status = 'deleted'`, `deleted_at`, `deleted_by`; the row is kept, its quantity rows are removed). Deleting an IDR already deleted is a 200 that changes nothing
- `POST /v1/idrs/{idr_id}/admin/undelete` — admin only: a soft-deleted IDR comes back with the status it had when it was deleted (a draft when that isn't on record, and the message says so), `deleted_at` and `deleted_by` cleared; one that comes back approved gets its quantity rows back. 404 `IDR is not deleted` for an IDR that isn't, 409 with `existing_idr_id` when another IDR has taken its day or its IDR number
- `DELETE /v1/idrs/{idr_id}/admin/permanent` — admin only: deletes a soft-deleted IDR for good, with everything under it and its files in Storage; 204. It must be soft-deleted first: 404 `IDR must be soft-deleted first` otherwise. It can't be undone
- `POST /v1/idrs/{idr_id}/reports/{report_id}/attachments/upload-request` — start a two-step upload: records a pending attachment (name, description, file details; `uploaded_by` is the signed-in user) and returns a signed Storage upload URL plus the headers to send; draft only, not on an auto-General
- `POST /v1/idrs/{idr_id}/reports/{report_id}/attachments/upload-complete` — mark a pending attachment uploaded once its file is in Storage (`attachment_id` in the body); draft only
- `PUT /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}` — replace an attachment's name and description; draft only
- `GET /v1/idrs/{idr_id}/reports/{report_id}/attachments` — list a report's uploaded attachments (pending ones left out)
- `GET /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}/download-url` — short-lived signed URL; 404 while pending
- `DELETE /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}` — remove an attachment, pending or uploaded (Storage file, then record); draft only
- `GET /v1/idrs/{idr_id}/export` — an IDR as an .xlsx on the DDC report-forms template (General, SWCB, AC, Conc Mix and Conc Cyl pages) (a draft's pages are marked "DRAFT - Not for Submission"), stored in the `idr-exports` bucket; returns `{download_url, filename}`, the URL valid 10 minutes
- `GET /v1/contract_items/?project_id=` — a project's contract items for the pay-item picker, each with its `item_no`, `description`, `spec_section` (null when it has none) and `pay_unit`, by item number then budget code; `[]` when none

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
  - Emails are stored as entered, matched without regard to case at sign-in, and unique the same way
    (`idx_users_email_lower`, migration 016). An upsert on email is `ON CONFLICT (lower(email))`.
  - User `327d3ed2-a3d6-4235-9408-7fe721b12bed` in the live database is Genghis Khan, a seeded inspector, and
    the id the frontend sent for everyone before sign-in existed. H0 mistook the row for a placeholder and
    renamed it `legacy-demo@icid.local`; his email (`KhanG@magnoleng.pc`) has been put back, and
    `seed_auth_users.sql` no longer touches the row except to undo that rename. He has no password.
  - **No ownership checks on reading and editing (yet).** Any signed-in user can read, edit or export any IDR by
    id, and list IDRs for any reporter (`?reporter_uuid=` is a filter, not an identity), except that nobody is
    listed another person's draft. An admin lists only the projects assigned to them in `project_users`, like
    anyone else. Submitting and the review routes are the exception: they check project roles, below. So is
    the quantities route, which takes `project_member`: the caller must be assigned to the project, in any role,
    or be an admin (403 `Project access required`; 404 for a project that doesn't exist). The
    admin-only routes are `/v1/admin/cleanup-demos`, the four under `/v1/admin/users`, the two under `/v1/admin/projects`, the seven under
    `/v1/admin/projects/{project_id}/pay-items`, the two under
    `/v1/projects/{project_id}/roles`, and `/v1/idrs/{idr_id}/admin/unlock`, `/admin/delete`, `/admin/undelete` and
    `/admin/permanent`.
  - **Admin user routes** (`api/v1/admin_users.py`) are their own router, taking `current_admin`: the
    `/v1/admin` router takes `admin_or_cron`, which the scheduler's secret also passes. An admin can change a
    user's first and last name, email, admin role and demo flag, a demo user's included, and reset their password; a name can't be cleared. The route checks the values itself, so a bad one is a 400, not FastAPI's 422. Names
    are read from `icid.users` whenever they are shown, so a rename also changes the `RE: <name>` caption on
    later exports of IDRs that user approved.
  - **Editing a user** (`PATCH /v1/admin/users/{user_id}`) takes any of five fields and writes them in one
    statement (`update_user_fields`), so either all of them change or none does.
    - **Email:** checked as on create and stored as typed, trimmed. The statement itself won't write an email
      another user has, whatever its capitals (`NOT EXISTS ... lower(other.email) = lower(%s)`), and the unique
      index backs that up; the route then looks the holder up for the 409. The user's own email in other
      capitals is not a collision. Sign-in is by user id inside the token, so a user whose email changes stays
      signed in and signs in with the new one next time.
    - **`is_admin`:** `true` sets `role = 'admin'`, `false` clears it to NULL; the response shows it as `role`.
      **An admin can't revoke their own admin role** (400): another admin has to, so the last admin can't lock
      everyone out by a slip. Nothing else is checked: a demo user can be made an admin.
    - **`is_demo`: only demo to real.** `false` on a demo user makes them a real one: the demo fences no longer
      hold them, signing out no longer deletes them, and the daily cleanup passes them by (both delete only
      `is_demo = true`). They keep their `demo-<uuid>@icid.local` email, their one `DEMO01` assignment and no
      password, so an admin gives them an email and a password next. **A real user can't be made a demo user**
      (400): demo users are deleted with everything they made. The statement can only turn the flag off
      (`is_demo = u.is_demo AND COALESCE(%s, true)`), whatever the route let through. `false` on a real user and
      `true` on a demo user are 200s that change nothing.
    - A password is not part of this route, and nor is the client (`client_id`), which is still changed in SQL.
  - **Creating a user** (`POST /v1/admin/users`): the email is stored as typed, trimmed; a blank last name is
    stored as NULL; the password is hashed with bcrypt (`hash_password` in `api/services/auth.py`) and never
    trimmed or returned. `is_admin` sets `role = 'admin'`, otherwise the role is NULL; the user is never a demo
    user. `users.client_id` is required and the form asks for none, so every user made this way sits under
    `DEFAULT_CLIENT_ID` (`C00001`). The INSERT is `ON CONFLICT (lower(email)) DO NOTHING`; when it returns no
    row the route looks the email's user up for the 409. The new user is on no project until an admin gives
    them a role on one.
  - **Passwords.** A new password is at least 6 characters and at most 72 bytes (what bcrypt reads), taken as
    typed and never trimmed; `check_new_password` in `api/v1/_shared.py` is the one check, used when a user is
    created and by both routes below. Both write through `update_user_password`.
    - **A user changes their own** with `POST /v1/users/me/password`: the current password is checked as sign-in
      checks it (`auth_provider.verify_credentials` on the signed-in user's email), after the new one is checked,
      so a bad new password is refused before anything is read. A user who has no password (Genghis Khan) gets
      `Current password is incorrect`; a demo user gets 403, as on the rest of the users router.
    - **An admin resets anyone's** with `POST /v1/admin/users/{user_id}/password`, their own included, without
      the current one: this is how a forgotten password is recovered. It also gives a password to a user who had
      none, a demo user included.
    - **Known limitation: tokens already issued stay valid.** Tokens are stateless, so neither route signs anyone
      out: an old token works until it expires (`JWT_EXPIRY_SECONDS`, 24 hours). Closing that needs a token
      blacklist or short-lived tokens with refresh (Phase 2).
  - **Admin project routes** (`api/v1/admin_projects.py`) are their own router too, taking `current_admin`.
    - **The list** is every row of `icid.projects`; `GET /v1/projects/` stays the signed-in user's own.
    - **Creating a project** takes an id (trimmed, at most 32 characters, only letters, digits, hyphens and
      underscores, since it is part of page URLs and Storage paths; matched exactly, so `hws0023` and `HWS0023`
      are two projects), a name (100), a description (200), a borough (free text, 50) and an optional
      registration code (50; blank is NULL). The INSERT is `ON CONFLICT (project_id) DO NOTHING`; no row back
      is the 409.
    - **What a new project lacks:** a `status` (NULL), any `project_clients` row (so the export prints no
      contractor), any `contract_items` (so the pay-item picker is empty) and anyone in `project_users`, the
      admin who made it included. It is in no one's `GET /v1/projects/` until an admin grants a role on it.
      The status and the clients are set in SQL; the pay items through the admin pay-item routes, below.
      There is no editing or deleting a project.
  - **Admin pay-item routes** (`api/v1/admin_pay_items.py`): a project's pay items are its rows of
    `icid.contract_items`, which an admin lists, adds, edits and deletes for any project, assigned to it or
    not. Its own router, taking `current_admin` and then `known_project` (404 for a project that doesn't
    exist, on every route).
    - **A contract item carries its own number, description, unit and section** (migration 026). They used to
      be read off the shared `spec_items` catalog, where a description or a unit could only be one thing for
      every project. `spec_items` is still there and the seeded rows still point at it (`spec_item_id`), but
      nothing reads it and these routes never touch it; a row made here has no `spec_item_id`.
    - **Names.** The API calls the row a pay item: `pay_item_uuid` is `contract_item_id`, `pay_item_ref` is
      `item_no`, `quantity` is `bid_quantity` and `unit_price` is `bid_unit_price` (aliased in
      `api/queries/contract_items.py`). The picker's route keeps its own names (`item_no`, `pay_unit`).
    - **The key is `(project_id, item_no, budget_code)`**, matched exactly: a bid document lists the same item
      under several budget codes, each a row of its own. Create is `ON CONFLICT ... DO NOTHING`, and no row
      back is the 409, naming the item that has the key. An edit that would end up on another item's key
      changes nothing (the UPDATE checks for it) and is the same 409. Two edits racing for one key can still
      hit the constraint: a 500.
    - **Values.** The ref (32), description (500), unit (16), budget code (50) and optional spec section (32)
      are trimmed. The budget code is otherwise kept as typed: `SE0944(CONED-CONS`, with its one bracket, is
      real. A blank spec section is NULL. The rules live in `api/services/pay_items.py`
      (`clean_pay_item_fields`, raising `PayItemValueError`), which the form's routes and the CSV import both
      go through, so a value is refused for the same reason either way.
    - **Units are stored in one short form** (`normalize_unit` in `api/services/pay_item_units.py`), on create,
      on edit and on import. A bid document's long form and the short form are both taken, whatever their
      capitals, periods and spaces (`l.f.`, `L. F.` and `LF` are all `LF`):

      | Typed | Stored | Typed | Stored | Typed | Stored |
      |---|---|---|---|---|---|
      | `EACH` | `EA` | `L.F.` | `LF` | `S.F.` | `SF` |
      | `S.Y.` | `SY` | `C.Y.` | `CY` | `V.F.` | `VF` |
      | `F.S.` | `FS` | `L.S.` | `LS` | `MONTH` | `MONTH` |
      | `DAY` | `DAY` | `P/HR` | `HR` | `CREW/HR` | `CREWHR` |
      | `TONS`, `TON` | `TN` | `LBS.` | `LB` | `BLOCK` | `BLOCK` |
      | `UNITS` | `UNIT` | `SETS` | `SET` | | |

      Any other unit is refused (`unknown unit: 'XYZ'`): a 400 from the form, a row's error in an import. A new
      unit is a new entry in `UNIT_FORMS`. The rows seeded before this keep `L.F.`, `Ton` and `Each` until they
      are edited or imported over; the picker, the export and `icid.quantities` take both forms. This table is
      apart from `UNIT_FORMS` in `api/services/quantities.py`, which reads what inspectors typed in reports and
      keeps an unknown unit as written.
    - **Amounts.** Quantity takes up to 10 decimal places and unit price up to 6 (trailing zeros aren't
      counted); zero is allowed, a negative is 400, and so is 10^15 or more. The columns are plain NUMERIC and
      hold what was typed; responses carry them as JSON numbers. Text that isn't a number is FastAPI's 422.
    - **Deleting is a hard delete, and safe.** Nothing references a contract item: a report's pay items and
      the rows of `icid.quantities` are copies. A deleted item leaves the picker; reports that used it, and
      their quantities, are unchanged. There is no undo for a delete or a clear.
    - **Migration 026 must be in the database before this code runs**: the picker's route reads the new
      columns too. The code from before it keeps working after it.
    - **CSV import** is two routes taking the same upload (multipart field `file`): `import/preview` shows
      what would happen and `import/commit` does it. Commit reads and checks the file itself, so what is
      written is what was uploaded to it, not what an earlier preview saw.
      - **The file:** UTF-8 (a byte-order mark is fine), comma-separated, at most 2 MB (413), with a header
        row. The header names the columns, in any order and any capitals: `pay_item_ref`, `description`,
        `quantity`, `unit`, `unit_price` and `budget_code` are required, `spec_section` is optional, and any
        other column is ignored and listed in `ignored_columns`. A file that isn't that is a 400 for the whole
        file. The upload's content type is not checked: browsers send a .csv as several.
      - **Rows** are numbered as in the file, the header being row 1; a row with nothing in it is skipped but
        counted, so a number matches the spreadsheet's. Each row is checked as the form checks a pay item. An
        amount may carry commas between its thousands and a leading `$` (`"9,500.00"`, `$14.50`), as a
        spreadsheet exports them; anything else that isn't a number is the row's error.
      - **Each row is one of three things** (`classify_rows` in `api/services/pay_item_import.py`): `new`;
        an `overwrite`, when the project has an item under that ref and budget code (matched exactly), carrying
        the item as it is (`existing`) and as the row would leave it (`incoming`); or an `error`, with its
        `reason` and its cells as typed (`raw`). Only a row's first problem is reported. Rows of the file that
        share a ref and budget code are all errors (`duplicated in CSV, see also row N`).
      - **The project's items are read once**, with the list's own statement, and matched in memory; there is
        no per-row lookup.
      - **Commit refuses a file with any error** (400 with the errors) and writes nothing; there is no skipping
        of bad rows. Otherwise every row goes in one statement (`bulk_upsert_pay_items`: `INSERT ... ON
        CONFLICT (project_id, item_no, budget_code) DO UPDATE`, the rows as one JSON parameter), so all of them
        land or none. `inserted` and `updated` are counted by that statement, so an item someone added between
        the read and the write is counted as updated. A statement that fails is a 500 carrying the database's
        message.
      - **An overwrite replaces** the description, unit, quantity and unit price; the row keeps its id. Its
        spec section is replaced only when the file has a `spec_section` column (a blank cell then clears it);
        without the column it keeps the one it has.
      - Items the file doesn't mention are left alone: an import never deletes. To replace a project's list,
        clear it first.
  - **Project roles.** `project_users.role` is `inspector`, `oe` or `re`, one row per role, so a user can hold
    several on a project (migration 018). `require_project_role("oe", "re")` builds a dependency for a route with
    an `idr_id`: it passes a user holding one of those roles on the IDR's project, and any admin; 404 for an
    unknown IDR, 403 `Role required: oe/re` otherwise. Submit needs `inspector`; the review routes need `oe` or
    `re` (Stage 2: `re`). `user_role` on the same table is a display label and is never checked.
    - Roles are granted and revoked by an admin through `POST /v1/projects/{project_id}/roles`, one row per
      call. A row made that way has no `user_role` label. Revoking a user's last role on a project removes
      their only `project_users` row there, so the project leaves their list; their IDRs stay. Nothing stops a
      revoke while the user is the reviewer on an IDR in review: they then get 403 on it, and an admin (or a
      direct update) has to move it on.
  - **Review.** `api/v1/reviews.py`, a second router under `/v1/idrs`, registered before the IDRs router so
    `/v1/idrs/queue` isn't read as an `idr_id`.
    - The path: `draft` → `submitted` → `stage1_review` → `stage2_review` → `approved`. A return sends an IDR
      back to `draft` (to the inspector) or, from Stage 2, to `stage1_review` (to the OE). The `returned` status
      is unused: a returned draft is `status = 'draft'` with `return_reason` set. Submit and approve-stage1
      clear the return.
    - **Only the reviewer who accepted acts.** Approving or returning at Stage 1 takes the user in
      `stage1_reviewer_uuid`; at Stage 2, the one in `re_reviewer_uuid`. Anyone else with the role gets 403. An
      admin stands in for either, and signs a Stage 2 approval with their own signature. Reassigning a reviewer
      is a direct database update for now.
    - **The IDR number** is given at the first accept-stage1 and kept from then on, through returns and
      resubmits. It is unique per project among IDRs that aren't deleted (409 with `existing_idr_id`).
    - **One statement per move.** Each transition locks the IDR, checks its status (and its reviewer), updates it
      and writes its `icid.idr_audit` row in a single statement (`_move_idr` in `api/queries/idrs.py`, with
      `AUDIT_CTE` from `api/queries/idr_audit.py`). A move that finds the IDR changed returns 409. Submit logs
      the same way.
    - A reviewer edits an IDR through the reviewer-edit routes only (see "Reviewer edits" below); the
      inspector's edit routes still take drafts only. Admin edits outside review are not built.
    - **Admin unlock** sends an approved IDR back to `stage2_review` and clears the RE's signature and the RE
      reviewer, so the export stops printing the old signature and an RE has to accept it before approving
      again. The admin does not approve. Logged as `admin_unlock`.
    - **Admin delete is a soft delete**: `status = 'deleted'` with `deleted_at` and `deleted_by`, logged as
      `admin_delete`; nothing is removed, attachments and audit rows included, but for its rows in
      `icid.quantities` (see "Quantities"). Every review statement and
      submit match only rows with `deleted_at IS NULL`, the lists leave deleted IDRs out, and the day and the
      IDR number are free again. `GET /v1/idrs/{idr_id}` and the export still answer for
      a deleted IDR by id, for anyone signed in; the edit routes refuse it, as it isn't a draft.
    - **The status before a delete is in `icid.idr_audit`, not on the IDR.** A soft delete overwrites
      `status`, and the same statement writes its `admin_delete` audit row with `from_status`. That row is the
      record un-delete reads; there is no column for it.
    - **Admin un-delete** (`POST .../admin/undelete`) brings a soft-deleted IDR back as it was, in one
      statement (`undelete_idr`): the status of its latest `admin_delete` audit row, `deleted_at` and
      `deleted_by` cleared, logged as `admin_undelete`. Its number, reviewers, signatures, accepted times and
      return reason were never touched by the delete, so they are as they were.
      - **No record, a draft.** An IDR with no `admin_delete` row (deleted by hand in SQL) comes back as a
        draft, and the response's message says so.
      - **It needs its day and its number back.** A deleted IDR freed both. If another IDR now holds the day
        (project, reporter and date) or the IDR number, the route answers 409 with `existing_idr_id` and
        changes nothing; the partial unique indexes refuse it too if one is taken between the check and the
        statement (also 409). Delete or renumber the other IDR first.
      - **An IDR that comes back approved gets its quantities back**, in the same statement, from its reports
        as they stand (the route readies them as approve-stage2 does). Any other status gets none.
      - 404 for an IDR that isn't deleted (`IDR is not deleted`), so calling it twice is a 200 then a 404.
    - **Admin permanent delete** (`DELETE .../admin/permanent`) removes a soft-deleted IDR for good
      (`purge_idr` in `api/services/idr_purge.py`). **Two steps, on purpose:** it only takes an IDR that is
      already soft-deleted (404 `IDR must be soft-deleted first` otherwise, and the DELETE itself carries
      `deleted_at IS NOT NULL`), so no live IDR can be removed in one call.
      - **One DELETE on `icid.idrs`.** Every table under an IDR is `ON DELETE CASCADE`: `idr_reports`
        (and through it `report_attachments`), `idr_field_edits`, `idr_audit` and `quantities`. Nothing else
        is deleted by hand.
      - **Then its files, best-effort:** its attachments (their paths are read before the delete), every file
        under `idrs/{idr id}/` in the signatures bucket (the copies no row points at any more included) and
        every export under `{idr id}/` in `idr-exports`. A file that can't be removed is logged and left
        orphaned; the delete still succeeds. Users' own signatures (`users/...`) are never touched.
      - **The record of it is a log line**, not a row: the IDR's audit rows go with it. The route logs
        `IDR permanently deleted: idr_id=... idr_number=... project_id=... admin_user_uuid=... at=...` at INFO.
      - There is no undo.
  - **Unique rules on `idrs` are partial indexes** (`WHERE deleted_at IS NULL`, migration 017):
    `uq_idrs_project_reporter_date` (one IDR per reporter, project and day) and `uq_idrs_project_number`. A
    soft-deleted IDR frees its day and its number. An INSERT can't name either with a bare column list, so
    `create_idr` uses `ON CONFLICT DO NOTHING` with no target.
  - Endpoint tests use the `admin_client` fixture (signed in as `ADMIN_USER_ROW`, `tests/conftest.py`); the plain
    `client` is for testing what happens without a token.
- **Reviewer edits.** A reviewer's change to an IDR in review is applied and logged (`api/v1/field_edits.py`, `api/services/field_edits.py`).
  - **Applied and logged, not overlaid.** One statement locks the IDR, writes the new value into it (a header
    column, or the report's `report_data`), adds an `icid.idr_field_edits` row holding the old and new value, and
    adds the `idr_audit` row. The IDR always holds the current value, so the export, the auto-General and the
    lists need to know nothing about edits. The value before a field's first edit is that edit's `old_value`;
    edit rows are only ever added.
  - **A write only lands over the value the caller read** (`#> path = old`, `IS NOT DISTINCT FROM old`), and only
    while the IDR is still in that stage under that reviewer. Otherwise the statement returns no row.
  - **`field_path`** uses the keys as `report_data` stores them: `header.<column>` (no `report_id`);
    `description`, `workforce.foremen`, `safetyChecks.plates`; a list row by position, `additionalWorkforce[0].count`;
    a pay item by its id, `payItems[<id>].payQuantity` (`pay_item_revision`); `payItems[<id>]` for an item a
    reviewer added (`pay_item_add`, the whole item as `new_value`); a Conc Cyl's cylinder by its id,
    `cylinders[<id>].slump`.
  - **Pay items carry an `id`.** They are entries in `report_data`, not rows of a table. Migration 020 gave every
    existing one an id, and submit gives one to any item without it (`REPORT_DATA_WITH_PAY_ITEM_IDS`, in the same
    UPDATE that numbers the pages). A client that saves a report must send each item's `id` back.
  - **Trucks carry an `id` too, but only to find an added one.** Migration 023 gave every existing truck an id,
    and submit gives one to any truck without it (`REPORT_DATA_WITH_IDS`: trucks on a CONC_MIX, pay items on any
    other report). A truck a reviewer adds is logged as `truck_add` on `trucks[<truck id>]`, its whole self as
    `new_value` and no `old_value`, and the export finds its row by that id. A truck's own fields are still
    named by position (`trucks[0].slump`). A client that saves a Conc Mix must send each truck's `id` back.
    There is no approving, revising or removing a truck: a reviewer edits its fields like any other.
  - **Cylinders get an `id` at submit.** A Concrete Cylinder Data report (`CONC_CYL`) keeps its rows in
    `cylinders`; a draft's are saved with `"id": null`, and submit gives an id to each one that has none or a null
    one (`REPORT_DATA_WITH_CYLINDER_IDS`, through `REPORT_DATA_WITH_IDS`). Nothing assigns ids on save. A client
    that saves a Conc Cyl after submit (a returned draft) must send each cylinder's `id` back.
  - **A reviewer edits a Conc Cyl through `PATCH /field`** (N4), like any other report: `cylinders[<id>].class`,
    `.cylinderNo` and `.slump`, `deliveryCasting.dateOfDelivery`, `.cyPoured`, `.dateCast` and `.jobLocation`,
    `placementLocation`, `sheetNo` and `sheetOf`. Nothing lists those paths: the rule is still "the field must
    already exist in this report", so a Conc Cyl saved before the form wrote a key can't have that key edited. A
    cylinder is named by its id only (`ID_LISTS` in `api/services/field_edits.py`: `payItems` and `cylinders`);
    `cylinders[0].slump` is refused, as are a whole cylinder and a cylinder's `id`. There is no adding, approving
    or removing a cylinder.
  - **Known limitation: other lists are addressed by position** (`additionalWorkforce`, `additionalEquipment`,
    Conc Mix trucks, AC courses and tickets). That is exact while an IDR is in review, since nothing else can
    change it. Once it is back with its inspector (returned, or unlocked and then returned) and they insert,
    remove or reorder rows, the edit history of those lists can attach to the wrong row. The values themselves
    are never affected. Revisit if it ever bites.
  - **The audit note is a pointer**, `{"edit_id", "field_path"}`, not a copy of the values (`EDIT_AUDIT_CTE`);
    actions are `field_edit`, `pay_item_revise`, `pay_item_add`, `pay_item_approve` and `truck_add`.
  - **`stage_reviewer`** (`api/services/auth.py`) is the dependency for the edit routes: the caller must be the
    IDR's `stage1_reviewer_uuid` in `stage1_review` or its `re_reviewer_uuid` in `stage2_review`, and still hold
    a role that reviews at that stage; an admin stands in. 400 for an IDR that isn't in review, 403 otherwise.
    It is tighter than `require_project_role`, which any holder of the role passes.
  - The inspector's own changes to a returned draft are ordinary saves and are not logged as edits. A client
    can tell: the field's current value differs from the last edit's `new_value`.
  - **What can be edited:** the eight header fields (never the work date, the IDR number or a signature), and
    any single value inside a report: text, a number, true/false or nothing. A path naming a whole object, a
    list, a pay item or a cylinder is refused, as is a pay item's or a cylinder's `id`. `report_data` has no schema on the server, so the
    rule is only "the field must already exist in this report"; an edit never creates a key.
  - **An edit that changes nothing is refused** (400), so the log holds only real changes.
  - **Pay items:** editing `payItems[<id>].payQuantity` is a `pay_item_revision` whichever route it comes
    through. A reviewer-added item gets a fresh id, the keys the report form saves (`itemNo`, `budgetCode`,
    `payQuantity` as text, `unit`, `description`) and no marker of its own: that it was added, and by whom, is
    its `pay_item_add` edit row. An inspector adds items to a draft through the report form, not these routes.
  - **Pay-item attestation.** A stage can only be approved once the user approving it has attested to every pay
    item on every report but an auto-generated General (`untouched_pay_items` in `api/services/field_edits.py`).
    An item is attested to by one of their own edits, stamped with the current stage: an approval
    (`pay_item_approve`, on `payItems[<id>]`, the quantity as both `old_value` and `new_value`), a revision of
    its quantity, or the edit that added it.
    - **For the quantity the item has now.** An approval or a revision of a quantity the item no longer holds
      doesn't count, so a later change by anyone puts the item back on the list.
    - **Since the stage was last accepted.** Only edits made at or after the IDR's `stage1_accepted_at` (at
      Stage 1) or `stage2_accepted_at` (at Stage 2) count, so an IDR that went back to its inspector and was
      accepted again is attested to afresh. An IDR the RE sent back to the OE was not accepted again, so the
      OE's attestations stand where the quantities do. Both times are on every IDR response, so a client can
      work out the same list; `idr_audit` is not read for this.
    - **The two times** (migration 022): accept-stage1 stamps `stage1_accepted_at` and clears
      `stage2_accepted_at`; accept-stage2 stamps `stage2_accepted_at`; a return to the inspector clears both; a
      return to the OE and an admin unlock clear `stage2_accepted_at` only.
    - **The RE accepts again after every return.** A return from Stage 2, to the inspector or the OE, clears
      `re_reviewer_uuid` in the same statement (as admin unlock does), so when the IDR is back in Stage 2
      nobody can approve it until an RE accepts, which starts a new round. A return from Stage 1 doesn't
      touch the column.
    - **NULL means "not known", and then every attestation at the stage counts**, whenever it was made. That
      is only the case for an IDR accepted before migration 022, or one an admin approves at Stage 2 without
      any RE having accepted. The quantity rule above still applies.
    - **Per stage, per person.** The RE attests again at Stage 2 whatever the OE did. An admin standing in is
      held to the same, with their own edits; the reviewer's don't count for them.
    - Items without an `id` are skipped (there are none after submit).
    - Approving an item already attested to writes nothing. Nothing is rebuilt after an approval.
  - **The auto-General:** it can't be edited. After an edit to a main report it summarises, it is rebuilt
    (`regenerate_auto_general`) so its merged pay items and description show the reviewer's value; that
    rebuild is a second statement, not part of the edit's. Nothing is rebuilt, or created, when the IDR has an
    inspector's General or none.
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
    hours, in the same order. Vercel Cron runs it every day at 03:00 UTC (`crons` in `vercel.json`) by calling
    `/v1/admin/cleanup-demos`. Vercel Cron can only send a GET, and sends the project's `CRON_SECRET`
    environment variable as `Authorization: Bearer …`, so the route takes GET as well as POST and the
    `admin_or_cron` dependency lets in either that exact secret or a signed-in admin (the first use of
    `current_admin`). Without `CRON_SECRET` set in Vercel the daily call gets 401 and nothing is cleaned up.
    pg_cron remains an alternative (see the migration file).
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
    change what was signed, and nothing under `idrs/` is ever overwritten, or removed while the IDR exists
    (an admin's permanent delete of the IDR removes its folder).
  - **The copy comes before the UPDATE.** If the copy fails, the submit is refused (502) and the IDR stays a
    draft. If the UPDATE then fails or loses a race, the copy is left behind unreferenced, and logged.
  - **Demo users have no signatures**: both signature routes return 403 `Demo mode: signatures are not
    available`, and they can't submit anyway.
  - `UserOut` carries `signature_path` internally for the submit flow but never serialises it; responses show
    `has_signature` and `signature_set_at` only. IDR responses do carry `inspector_signature_path`.
  - The signer is whoever submits, not necessarily the IDR's reporter: any inspector on the project, or an admin.
  - **Approving signs too.** approve-stage2 copies the approver's current file to `idrs/{idr id}/re_{random}.png`
    and stamps `re_signature_path` and `re_signed_at`, with the same copy-before-UPDATE rule (400 without a
    signature, 502 when the copy fails). It also records the approver as `re_reviewer_uuid`, replacing whoever
    accepted, so the name printed with the signature is always the signer's (an admin approving in an RE's place
    included).
  - **The export prints it.** For an IDR past draft (submitted, in review or approved) with
    `inspector_signature_path`, `export.py` downloads that file once and stamps it, with the IDR's work date, on
    every printed page that has an "Inspector's Signature" line: Gen Bk, Conc Bk, AC Bk, Conc Mix, Conc Cyl, Report Cont and every attachment page, copies included. Front pages
    have no line. Each module declares its page's `SIGNATURE_LAYOUT` (Report Cont's is in `export_common.py`);
    the dispatcher maps a printed sheet to its layout by name, so a copy signs where its original does. A new
    form with a signature line adds a layout and an entry in `SIGNATURE_LAYOUTS`.
    - The image is letterboxed into the signature line's cell plus the blank row above it (209 x 34 px; Conc
      Mix 224 x 32), since the line alone is one 17 px row.
    - **The Date cell beside the signatures is the day of the latest signature printed.** A page has one, to
      the right of both lines. On an approved IDR it takes the day of `re_signed_at`; from submission until
      then, the day of `inspector_signed_at`; on a draft it is blank. Both are the day it was in
      `FORM_TIMEZONE` (America/New_York; `signed_date` in `export_common.py`), written m/d/yy, never the UTC day
      and never the work date, which is the date at the top of the page. A signature that couldn't be printed
      brings no date: an approved IDR whose RE file is missing shows the inspector's day.
    - A draft is never signed, a returned one included, and its signature isn't even fetched; only a draft
      carries the "DRAFT - Not for Submission" marker. An IDR submitted before signatures (no path), or one
      whose file can't be fetched or read, exports with blank lines; the failure is logged and the export
      still completes.
    - **The Resident Engineer's signature prints beside it, on an approved IDR only.** Each of those pages has a
      "Reviewed by" line to the right of the inspector's; each module declares its `RE_SIGNATURE_LAYOUT`
      (`RE_SIGNATURE_LAYOUTS` in `export.py`). The image is fitted to that line plus the row above, as the
      inspector's is. The page has one Date cell and it is the inspector's, so the caption under the RE's line
      ("Resident Engineer's Name") is replaced by `RE: <name>`: the approver's name (`re_reviewer_uuid`), in the
      caption's own style, set to shrink to fit. It carries no date; the Date cell does.
    - The RE's signature is stamped only when `status = 'approved'` and `re_signature_path` is set, so a path
      left on an IDR that is no longer approved never prints. Without it, or when its file can't be used, the
      printed caption stays and the line is blank.
    - Conc Mix's RE line has one narrower column (X, 11 px), so its layout lists each column's width
      (`column_widths_px`). On attachment pages the caption cells aren't merged in the template; the export
      merges them so the text shrinks to the line, not to one column.
- **Pay quantities on the export.** A front page's Pay Quantity cell holds the quantity as the inspector entered
  it, followed straight away by its unit as a second run of the same cell: Arial 8, superscript
  (`PAY_UNIT_FONT_PT`, `PAY_UNIT_SUPERSCRIPT` in `export_common.py`). The number keeps
  the cell's own font (Arial 14 on Gen Fr, Arial 10 on Conc Fr and AC Fr), and the cell is set to shrink to fit,
  so a long number is scaled down by Excel, not cut off.
  - The unit is abbreviated by `pay_unit_abbreviation` (`export_common.py`): `L.F.` → `LF`, `S.F.` → `SF`,
    `C.Y.` → `CY`, `S.Y.` → `SY`, `Ton` → `TN`, `Each` → `EA`. A unit that isn't in `PAY_UNIT_ABBREVIATIONS`
    prints without its periods and spaces, in capitals, cut to four characters (`Gal.` → `GAL`). The catalog and
    saved reports keep their units as they are; only the export abbreviates.
  - An item with no quantity prints nothing in the cell, unit or not.
  - `WorkbookTemplate.set_cell_with_suffix` writes the two runs.
  - The Item No. cell shrinks to fit too: Gen Fr's is 14 pt, and a code like `4.01 AAS` lost its first digit
    at the cell's left edge.
- **Conc Cyl on the export.** A `CONC_CYL` report prints on Conc Cyl, the DDC Data Sheet for Concrete Test
  Cylinders, one sheet per report (`export_conc_cyl.py`).
  - **What is written:** the project details (Contract No. is the project's id), the IDR's work date and its day
    of the week (the letter shaded, as on the other forms), Sheet No. and "of" as the inspector typed them, date of
    delivery, C.Y. poured, job location, date cast, the cylinders' Class, Cylinder # and Slump, and the specific
    location of placement over its three lines (cut with "continued in ICID" past them).
  - **What never is:** the testing laboratory's block and the table's five lab columns (Age Day, Date Tested,
    Total Load, PSI, Page Cyl Reg.), which the lab fills by hand; and Resident Engineer's Name and CLIENT, which
    the data model has no source for.
  - **The table holds 18 cylinders** (rows 27-44). The form caps the list there; any past it are dropped from the
    page with a logged warning.
  - **Dates** in `report_data` are ISO text (`2026-10-08`) and print m/d/yy; anything else prints as typed
    (`format_iso_as_mdy` in `export_common.py`). Quantities print as entered.
  - **The date line** is seven one-column cells with a "/" drawn as a diagonal border in two of them, so the export
    merges `AI5:AO5` and clears the slashes before writing the date, as it does on Conc Fr and AC Fr. Two header
    cells and one placement line are bold in the template and are made plain. The template file is unchanged.
  - The form has no page number or inspector field; the report still counts as a page of the IDR.
- **I.R. No. on the export.** Every page that has the field prints the IDR's `idr_number`, as text (`005` keeps
  its zeros): the header's "I.R. No." on Gen Fr, Conc Fr, AC Fr, Report Cont and the attachment pages, and
  "ATTACHMENT TO I.R. NO." on Conc Mix (`ir_number` in `export_common.py`). An IDR nobody has accepted yet has no
  number and the field stays blank; a returned draft keeps showing the one it was given.
- **Date and I.R. No. on Conc Fr and AC Fr.** Those forms lay both out for a pen: the date line is seven one-column
  cells with a "/" drawn in two of them, and the I.R. No. cell is one column in a 6 pt row, so a typed date printed
  as `30/26` and the I.R. number lost its top. The export merges `AI4:AO4` and `AH6:AO7` before writing them, as
  Gen Fr's template has them, and clears the two slashes (`HeaderLayout.merge_areas`, `date_slashes`). The template
  file is unchanged.
- **Redlines on the export.** What reviewers edited prints with its history, at any status (a returned draft
  included). `export.py` reads the IDR's edits once (`field_edits_for`) and hands each report its own
  (`Redlines.for_report`); `export_redlines.py` decides what prints and `export_common.py` draws it.
  - **A field's chain** is the value before its first edit, struck, then each edit's new value in blue
    (`PAY_REDLINE_COLOR`, `0070C0`) followed by the editor's initials as a small label. Only the last entry is
    left standing: an edit a later one replaced is struck too, and keeps its initials. An edit that emptied a
    field shows `(blank)`.
  - **Revised after return.** When the field's current value isn't the last edit's (the inspector changed it
    after a return), every edit is struck and the current value closes the chain in black, labelled `(revised)`
    in small grey italic. Values are compared as they print, quantities as amounts (`same_quantity`).
  - **Always one line, shrunk to fit.** Excel ignores shrink-to-fit on a cell that wraps, and no header box is
    tall enough to stack two values, so a chain's cell has its wrapping turned off (`shrink_on_one_line`). A label
    sharing the cell stays in front: `Low  41 45 MK`, `AM  Sunny Cloudy RM Rain MK`, `( Start 07:00 07:30 RM End … )`.
  - **Initials** are Arial 6 (`REDLINE_INITIALS_FONT_PT`), and Arial 8 for a pay item's, in Quantity Chk
    (`PAY_REDLINE_INITIALS_FONT_PT`).
  - **Runs state their font.** `WorkbookTemplate.set_cell_runs` writes a cell as `TextRun`s. Excel draws a run
    without properties in its default font, not the cell's, unless it is the first; so every other run carries
    the cell's font name and size for whatever it doesn't set.
  - **Pay items take a row per revision.** The item's own row keeps the quantity it had, struck; each revision
    follows on a row of its own with the item number, budget code and quantity in blue (the description stays on
    the first row). A quantity the inspector changed afterwards gets a last, black row. An item a reviewer added
    is one row with every cell it fills in blue: item number, budget code, quantity, description and initials.
  - **Initials go in "Quantity Chk (Initials)", never in the Pay Quantity cell**, which holds only the quantity
    and its unit. Each row's cell holds the initials of whoever added or revised that row's quantity, in blue,
    shrunk to fit. The inspector's own rows (the struck original, a quantity changed after a return) have none.
  - **Approvals** add the approver's initials to the Quantity Chk cell of the row that stands, after the
    reviser's or adder's: `AD / RM / MK`. Only an approval of the quantity the item has now prints; each person
    once. An item approved as it stands keeps its black quantity and gets the approvers' initials in blue.
  - **Revision rows are table rows.** The tables don't grow (12 rows, 10 on AC Fr), so redlines can push pay items
    onto another copy of the front page, which is numbered and counted in OF like any overflow page
    (`pay_item_slices` counts rows, not items). An item and its revisions stay on one page; an item with more
    rows than a page holds keeps its own row and its latest revisions.
  - **Description and comments** flow through the same cascade as before, the replaced text struck and then the
    new text in blue, the initials after its last line (`redline_paragraphs`, `RedlineText`). An edited text
    takes that many more lines, so it can reach the back page or Report Cont.
  - **Safety Y / N:** the box the answer left keeps a struck X, the new box gets a blue X, and the editor's
    initials open the row's Remarks. AC Bk has no remarks column, so there the initials follow the X.
  - **An auto-generated General shows the header's redlines only.** Its pay items are sums across reports and it
    can't be edited; the edits print on the reports they were made on. So does a General composed for the export.
  - **Conc Mix is redlined too** (`export_conc_mix.py`), by the keys its `report_data` stores:
    `locationOfUse.curb` / `.sidewalk` / `.concreteBase` / `.structural` (true / false), `mixerType.type`
    (`readyMix` / `other`) and `mixerType.otherLabel`, `trucks[n].<field>` (`slump`, `airContent`,
    `inspectionSticker`, ...), `concreteSpecs.<field>`, `materialUsage.<field>` and `remarks`. `locationOfUse`
    and `mixerType` on their own are objects, which the edit endpoint refuses.
    - A box a reviewer ticked gets a blue X; one they unticked keeps a struck X; their initials go in the cell
      right after the box (`LOCATION_INITIALS`, `MIXER_INITIALS`). Other's box is followed by its label, so its
      initials open the write-in cell.
    - `trucks[n]` is the truck's place in the saved list, whichever Conc Mix sheet it prints on. A sticker answer
      changes like a safety answer, the initials after the X.
    - A box the inspector changed after the last edit shows as it stands, with no marks.
    - A truck a reviewer added (`truck_add`) has every cell it fills in blue, its sticker's X included, and the
      adder's initials after its truck or ticket number. Later edits of its fields chain on top, as for any
      truck. A twelfth truck prints on another Conc Mix sheet, numbered and counted like any other.
  - **SWCB's own sections are redlined too** (`export_swcb.py`): `structural` (true / false; blue X when a
    reviewer ticked it, struck X when they unticked it, initials in the cell after the box), `subcontractor`,
    `activity.<excavation|formPrep|pour>.<fromStation|toStation|remarks>`, and
    `inspectionMatrix.<item>.<base|sidewalk|curb>`: `Y` / `N` / `NA` (each has its own box: struck X where the
    answer was, blue X followed by the initials where it is; after the struck X when a reviewer cleared it) or,
    for `otherCuringMethods`, text. The Curb, Sidewalk and Concrete Base operation boxes follow the matrix and
    are never marked. Only the columns an item takes are written (`MATRIX_ROWS`), so an edit naming
    `sidewalkFoundationPlaced.curb` or `roadwayStoneBasePlaced.sidewalk` / `.curb` prints nothing.
  - **AC's own sections are redlined too** (`export_ac.py`): `pavingContractor.<field>`, `temperature.<field>`,
    `maxDensity.top` / `.binder`, `pavementCourses[n].<field>`, `materialUsageTop.<field>` and
    `materialUsageBinder.<field>`, `tackCoat.<field>`, `deliveryTickets[n].<field>` (all chains), and
    `acRequirements.<key>.value` / `.remarks`, which print like a safety row: struck X where the answer was, blue
    X where it is, the initials opening the row's remarks, and the remarks as their own chain. `[n]` is the row's
    place in the saved list, whichever sheet it prints on. There is no adding a course or a ticket.
    - The paving contractor's name and the two max densities have no box of their own: their chain is centred
      across a run of cells and can't shrink, so a long one is cut at the run's end.
  - **Conc Cyl is redlined too** (`export_conc_cyl.py`): `sheetNo`, `sheetOf`,
    `deliveryCasting.<dateOfDelivery|cyPoured|jobLocation|dateCast>` and `cylinders[<id>].<class|cylinderNo|slump>`
    (all chains, an edited date printing each of its values m/d/yy), and `placementLocation`, the replaced text
    struck and then the new text in blue over its three lines. A cylinder is found by its `id`, never by its place,
    so a cylinder without one (a draft's) has no chain.
  - **Not redlined:** AC Bk's safety remarks, which print inside its remarks text as they stand.
  - Lists addressed by position (`additionalWorkforce[0].count`) are matched by position, with the limitation
    noted under "Reviewer edits".
- **Quantities.** `icid.quantities` (migration 025) holds the pay-item quantities of approved IDRs, one row per pay
  item, for the progress and as-built reports.
  - **Approval writes them, atomically.** approve-stage2 readies the IDR's rows from its reports
    (`quantity_rows_for_idr`) and hands them to the statement that approves it, where two more CTEs delete the
    rows the IDR had and insert the new ones. Both read the statement's `moved` CTE, so an approval that doesn't
    go through (a lost race, the wrong reviewer, the wrong status) writes nothing, and a row that can't be written
    stops the approval. An IDR without pay items is approved with no rows.
  - **Admin unlock and admin delete remove them** in their own statements, the same way
    (`delete_quantities_cte("moved")`): an IDR that is no longer approved has no quantities. Approving it again
    writes them afresh, from the reports as they then stand. So the table only ever holds approved IDRs that
    aren't deleted, and a reader needs no join to leave the others out.
  - **Admin un-delete writes them back** for an IDR that comes back approved, in its own statement
    (`replace_quantities_ctes("approved")`, a CTE that holds the IDR only when its restored status is
    `approved`). A permanent delete takes them with the IDR (the foreign key cascades).
  - **The CTEs have one source**, `api/queries/quantities.py`: `delete_quantities_cte` and
    `insert_quantities_cte` take the name of the CTE that holds the IDR, and `_move_idr` appends them after the
    audit CTE (`also`). No other move touches the table.
  - **The rows are read just before the statement runs**, not inside it. Only the IDR's RE reviewer (or an admin)
    can edit it at Stage 2, and they are the one approving, so nothing changes in between in practice.
  - **`scripts/backfill_quantities.py`** does the same for the IDRs approved before this existed: for every
    approved IDR that isn't deleted, it replaces the IDR's rows with what its reports give now
    (`replace_quantities`, one statement per IDR). Safe to run again. Run it once after migration 025.
  - **Migration 025 must be in the database before this code runs**: approve-stage2, admin unlock and admin
    delete all name `icid.quantities`, and fail without it.
  - **What is read:** every item of every General, SWCB and AC report's `payItems`, as `report_data` holds it
    (so after any reviewer's edit). A Conc Mix and a Conc Cyl have no pay items. **An auto-generated General is
    skipped**: its items are sums of the other reports', and counting them would count those twice. An
    inspector's own General counts.
  - **The amount** is `payQuantity` parsed to a number. Spaces around it, commas between thousands (`1,200`) and
    a known unit typed after it (`125.5 SF`, logged) are tolerated; anything else, or a blank, skips the item
    with a logged warning. Nothing is guessed.
  - **The unit is stored in its short form** (`L.F.` → `LF`, `Ton` → `TN`, `Each` → `EA`; `UNIT_FORMS`), so
    totals by unit add up. An unknown unit is kept as written and logged once per process. Reports keep their
    units as they are. This is its own table, not the export's `pay_unit_abbreviation`, which cuts an unknown
    unit to four characters for the cell.
  - **An item without an item number is kept**, `pay_item_ref` NULL (a reviewer can add an item by its
    description alone).
  - **An IDR's rows are replaced as a set, in one statement** (`replace_quantities` in
    `api/queries/quantities.py`): its rows are deleted and the new ones inserted from one JSON parameter.
    `project_id`, `report_date` and `reporter_uuid` on a row are copies of the IDR's own.
  - `api/services/disciplines.py` maps a report type to its disciplines (`SWCB` → Sidewalk, Curb) and back. A
    row keeps its `report_type`; its disciplines are read off that.
  - **Reading them: `GET /v1/projects/{project_id}/quantities`** (`api/v1/quantities.py`, a second router under
    `/v1/projects`; `list_quantities` in `api/queries/quantities.py`).
    - **Filters**, all optional: `from` and `to` (work dates, inclusive), `pay_item` (the item number, exact),
      `budget_code` (exact), `discipline` (by name) and `inspector_uuid`. Different filters must all hold; a
      repeated `pay_item`, `budget_code` or `discipline` matches any of its values. A discipline is resolved to
      the report types that cover it, so `Sidewalk` and `Curb` both mean SWCB rows. A discipline with no pay
      items (`Concrete (delivery)`) is a valid filter that matches nothing.
    - **Refused with 400**, before anything is read: a date that isn't `YYYY-MM-DD`, `from` after `to`, and a
      discipline the mapping doesn't have (spelled exactly as it is there). The two dates are read by the route
      as text so they get 400, not FastAPI's 422; a malformed `inspector_uuid` is still 422.
    - **Each row** carries its IDR's number and its inspector's name (joined in the one statement) and its
      `disciplines`. `amount` is a number in the JSON.
    - **`totals_by_unit`** is the sum of the returned rows' amounts per unit, signed; rows without a unit are
      summed under `(unknown)`. `row_count` is how many rows came back.
    - **At most 10,000 rows** (`MAX_ROWS`): the statement asks for one more, and when it gets it the response
      carries the first 10,000 with `truncated: true`, still 200. The totals are then of those rows only, so a
      client that sees `truncated` should narrow its filters.
    - **Order:** work date newest first, then item number (rows without one last).
    - The statement leaves out rows of a deleted IDR, though admin delete has already removed them.
- **The archive.** `GET /v1/idrs/` is the IDR list the archive page reads (`list_project_idrs` in
  `api/v1/idrs.py`, `list_idrs` in `api/queries/idrs.py`). It is not paged: every match comes back, most
  recently edited first.
  - **Filters**, all optional, on top of `project_id`, `status` and `reporter_uuid`: `idr_number` (the start of
    the number, any case; `%` and `_` in it are matched as written; blank is no filter, and an IDR without a
    number never matches), `work_date` (that day) or `from` and `to` (work dates, inclusive, either alone), and
    `discipline` (by name, repeatable). Different filters must all hold; a repeated `discipline` matches any of
    its values. They apply inside the deleted and drafts rules, `include_deleted` included.
  - **A discipline** is resolved to the report types that cover it, and an IDR matches when it has a report of
    any of them (`EXISTS` on `icid.idr_reports`), addendums and an auto-generated General included. So `Sidewalk`
    and `Curb` both mean "has an SWCB".
  - **Refused with 400**, before anything is read: a date that isn't `YYYY-MM-DD`, `work_date` sent with `from`
    or `to`, `from` after `to`, and a discipline the mapping doesn't have. The dates are read as text, as on
    the quantities route; both routes read them and the disciplines through `api/v1/_shared.py`.
  - **Each row carries `disciplines`**: what its reports cover between them, each once, in alphabetical order
    (`["Curb", "Sidewalk"]`); `[]` for an IDR with no reports. The statement reads each IDR's distinct report
    types in a subquery of `IDR_LIST_EXTRAS` (`report_types`, not in the response) and
    `disciplines_for_report_types` turns them into names. No table or column holds them.
  - **The review queue's rows carry it too**: both lists share `IDR_LIST_EXTRAS` and `idr_list_items`.
  - The permission is unchanged: any signed-in user, with the demo fence. `project_id` is a filter, not a
    scope, so this route does not take `project_member`.
- **Adding an endpoint means adding tests** under `tests/v1/`, in the file matching the
  endpoint module. Tests patch the query layer (`patch("api.queries.<module>.run_query")`)
  and return **dict** rows matching the real column names; they do not hit the database.
- **Never commit** `venv/`, `.env`, `__pycache__/`, `.pytest_cache/`, or `.xlsx` files —
  except `templates/report_forms.xlsx`, which ships with the app as the DDC export base, and
  `templates/conc_cyl_source.xlsx`, the form its Conc Cyl tab was copied from.
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

- Endpoints exist for `users`, `projects`, `idrs` (with attachments and the .xlsx export) and `contract_items` (the
  picker's read, and the admin pay-item routes). `spec_items` has queries and a schema but no endpoint, and nothing
  reads the table since migration 026. `api/schemas/` also defines models for
  clients, form templates and the join tables — those schemas run
  ahead of the endpoints and may not match `schema.sql` exactly. Verify against `schema.sql`
  before building on them.
- `api/v1/`, `api/db/`, `api/core/` and `api/schemas/` have no `__init__.py`; only
  `api/queries/` does. Imports work regardless, but don't take the inconsistency as intent.
- CORS is `allow_origins=["*"]`. Tighten before production.
- `POST /v1/auth/login` has no rate limiting, and neither has `POST /v1/users/me/password`, which checks a
  password too. A password change or reset leaves the tokens already issued valid until they expire. Both are
  Phase 2.
- Demo mode leaves files behind: attachment files of demo users purged by the daily cleanup (a SQL function can't
  reach Storage), and every demo export in `idr-exports`. Nothing points at them afterwards; a bucket sweep would
  have to find them. `POST /v1/auth/demo` is also unthrottled apart from the 200-user ceiling.
- No multi-statement transactions: `run_query` runs one statement per connection. Work that must be atomic is
  written as one statement (data-modifying CTEs), as the demo user's insert and delete are.
