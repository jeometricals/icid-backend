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
| `api/v1/` | HTTP endpoints, one module per resource (`auth.py`, `signatures.py`, `admin.py`, `users.py`, `projects.py`, `idrs.py`, `reviews.py`, `field_edits.py`, `attachments.py`, `exports.py`, `contract_items.py`). Each exports a `router`. |
| `api/queries/` | SQL functions, one module per table area. The only place SQL is written. |
| `api/schemas/` | Pydantic request/response models, one module per resource. |
| `api/db/` | Connection plumbing: `connection.py` opens the psycopg connection, `runner.py` exposes `run_query(sql, params)`. |
| `api/storage/` | Supabase Storage plumbing: `client.py` is the only module that imports `supabase`. |
| `api/services/` | Logic spanning several queries or Storage. Endpoints call it; it never builds SQL. `auto_general.py` (the auto-General's aggregation), `attachments.py` (attachment uploads and downloads), `auth.py` (sign-in: the `AuthProvider` interface, `LocalAuthProvider` with bcrypt and JWTs, the `auth_provider` singleton, the `current_user` / `current_admin` dependencies, and the demo-mode dependencies), `demo.py` (deleting a demo user at sign-out), `field_edits.py` (a reviewer's edit: the field-path grammar, finding the field in a report, running the edit, keeping an auto-General in step), `signatures.py` (a user's signature upload and confirm, and the copy an IDR keeps at submit), and the IDR export: `export.py` (the dispatcher: loads the IDR, allocates and orders the sheets, numbers pages, stamps the inspector's signature on an IDR's pages from submission on and the Resident Engineer's on an approved one, stores the file and signs its URL), `export_common.py` (shared layouts and stampers: headers, continuation header, pay items, work force, equipment, safety, the text cascade, the two signatures, and how a redline is drawn), `export_redlines.py` (reviewer redlines: reads the IDR's edits as each field's chain and each pay item's rows), `export_general.py` (the General onto Gen Fr / Gen Bk / Report Cont), `export_swcb.py` (SWCB onto Conc Fr / Conc Bk), `export_ac.py` (AC onto AC Fr / AC Bk), `export_conc_mix.py` (CONC_MIX addendums onto Conc Mix sheets), `export_conc_cyl.py` (CONC_CYL addendums onto Conc Cyl sheets), `export_attachments.py` (report attachments onto pages copied from Sketch Cont) and `xlsx_template.py` (`WorkbookTemplate`: edits the .xlsx package XML directly — cells, text runs, styles, sheet copies, pictures, text boxes, print setup). |
| `api/core/` | App-wide configuration — env loading: `DATABASE_URL` and `JWT_SECRET_KEY` (both required at startup), the other JWT settings, `CRON_SECRET` (what the scheduler sends; optional), and the Supabase Storage settings (attachments and `idr-exports` buckets, signed-URL lifetimes, and the signatures bucket: `SIGNATURE_BUCKET_NAME`, `SIGNATURE_URL_EXPIRY_SECONDS`). No business logic. |
| `tests/v1/` | Pytest suites mirroring `api/v1/`, one file per endpoint module. |
| `schema.sql` | Authoritative DDL for the `icid` schema. `seed.sql` holds mock data; `seed_sidewalk_pay_items.sql` seeds the pay-item catalog (`spec_items`, and `contract_items` for `HWS0023`) and runs after it. `seed_auth_users.sql` seeds the admin account and its project assignments and `seed_test_project.sql` the Test Project (`DEMO01`). |
| `migrations/` | Numbered SQL migrations, run by hand in the Supabase SQL editor. A schema change ships as a migration plus the matching `schema.sql` edit. |
| `docs/` | `data-model.md`: developer reference for the tables, the Storage buckets, the auto-General and the migration history. |
| `README.md` | How to run, configure, test and deploy the backend. The one README. |
| `templates/` | `report_forms.xlsx`, the export base (built from `report_forms_source.xltx` by `scripts/clean_report_template.py`). `conc_cyl_source.xlsx` is the DDC Data Sheet for Concrete Test Cylinders, the source of the template's Conc Cyl tab. |
| `scripts/` | One-off local utilities: `clean_report_template.py`, which builds the export template, and `merge_conc_cyl_template.py`, which added the Conc Cyl tab to `report_forms_source.xltx` (already run; it refuses to run twice). Not imported by the app. Seeds and ad-hoc SQL are run in the Supabase SQL editor. |

### Endpoints

<!-- Update this list when endpoints change -->
- `GET /status`
- `POST /v1/auth/login` — email (matched without regard to case) and password; returns `{access_token, token_type, expires_in, user}`, or 401 `Invalid email or password`
- `GET /v1/auth/me` — the user the bearer token belongs to, with `has_signature` and `signature_set_at`; 401 `Not authenticated`, `Token expired` or `Invalid token`
- `POST /v1/auth/demo` — public; makes a throwaway demo user on `DEMO01` and returns what login returns; 503 when `DEMO01` is missing or 200 demo users already exist
- `POST /v1/auth/logout` — 204 always; stateless, the client drops its token. A demo user signing out is deleted with everything they made

Every route below needs a bearer token (401 without a valid one); see "Sign-in" under the conventions.

- `POST` or `GET /v1/admin/cleanup-demos` — deletes demo users older than 24 hours; returns `{purged_count}`. For a signed-in admin, or the scheduler sending `CRON_SECRET` as its bearer token; 403 for anyone else signed in
- `POST /v1/signatures/upload-request` — body `{content_type: "image/png"}`; returns `{upload_url, storage_path, expires_in}`, a signed URL to PUT the signed-in user's signature PNG to (it replaces their current one); 403 for a demo user
- `POST /v1/signatures/confirm` — body `{signature_type: "drawn" | "uploaded"}`; records the uploaded file as the user's signature and returns the user; 400 `Upload the signature before confirming` when no file is there; 403 for a demo user
- `GET /v1/users/` — 403 for a demo user
- `GET /v1/projects/` — the signed-in user's projects (through `project_users`), each once, with `roles` (the project roles they hold on it) and `user_role` (the label)
- `GET /v1/projects/{project_id}`
- `GET /v1/projects/{project_id}/roles` — admin only: who holds which role on the project, one entry per user and role (demo users left out)
- `POST /v1/projects/{project_id}/roles` — admin only: body `{user_uuid, role: "inspector" | "oe" | "re", action: "grant" | "revoke"}`; returns the project's roles as they now stand. Safe to repeat: granting a role already held, or revoking one not held, is a 200 that changes nothing. 404 for an unknown project or user, 400 for a demo user
- `POST /v1/idrs/` — create a draft IDR for the signed-in user, its reporter (409 with `existing_idr_id` if one exists for that reporter, project and date)
- `GET /v1/idrs/?project_id=&status=&reporter_uuid=` — list IDRs with `report_count`, `has_general` and the names of the inspector and reviewers (all filters optional). Deleted IDRs and other people's drafts are left out; an admin can add `include_deleted=true` and `include_all_drafts=true` (400 for anyone else who sends either as true)
- `GET /v1/idrs/{idr_id}` — IDR plus all its reports, in page order, and `field_edits`: every edit reviewers made on it, oldest first, each with `editor_name` and `editor_initials`
- `PUT /v1/idrs/{idr_id}/header` — partial update of the shared header fields on a draft
- `POST /v1/idrs/{idr_id}/reports` — add a report (typed by `ReportType`; addendums may name a parent)
- `PUT /v1/idrs/{idr_id}/reports/{report_id}` — replace a report's `report_data` (any JSON object)
- `DELETE /v1/idrs/{idr_id}/reports/{report_id}` — remove a report (its addendums cascade)
- `POST /v1/idrs/{idr_id}/submit` — submit a draft, signed by the signed-in user (locks it, numbers pages, sets `total_pages`, stamps `inspector_signature_path` and `inspector_signed_at`, clears any return); 400 `Signature required before submitting` when they have no signature, 502 when it can't be copied, 403 `Demo mode: submit is disabled` for a demo user, 403 `Role required: inspector` for a user who isn't an inspector on the project
- `GET /v1/idrs/queue?status=submitted|stage1_review|stage2_review` — one review queue, oldest submission first, on the projects where the signed-in user works it (OE or RE; RE only for `stage2_review`); an admin sees every project
- `POST /v1/idrs/{idr_id}/accept-stage1` — OE or RE picks a submitted IDR up: `submitted` → `stage1_review`, sets `stage1_reviewer_uuid` and `stage1_accepted_at` (and clears `stage2_accepted_at`). Body `{idr_number}`, needed the first time (400 without it); an IDR that has a number keeps it. 409 with `existing_idr_id` when the number is in use on the project
- `POST /v1/idrs/{idr_id}/approve-stage1` — `stage1_review` → `stage2_review`; only the Stage 1 reviewer (403 for another OE or RE), and only once they have approved, revised or added every pay item at this stage: otherwise 400 `{detail, untouched: [{pay_item_id, report_id, item_no, budget_code}]}`
- `POST /v1/idrs/{idr_id}/accept-stage2` — an RE becomes `re_reviewer_uuid` and `stage2_accepted_at` is stamped; the status stays `stage2_review`, and the last to accept wins
- `POST /v1/idrs/{idr_id}/approve-stage2` — final approval, signed: `stage2_review` → `approved`, stamps `re_signature_path` and `re_signed_at`; only the RE reviewer, and only once they have attested to every pay item at Stage 2 (the same 400 with `untouched`); 400 `Signature required before approving`, 502 when the signature can't be copied
- `POST /v1/idrs/{idr_id}/return` — body `{to: "inspector" | "oe", comment}`; back to `draft` (inspector) or, from Stage 2, to `stage1_review` (OE), with `return_reason` and `returned_from`; clears `stage2_accepted_at`, and `stage1_accepted_at` too when it goes to the inspector; a return from Stage 2 also clears `re_reviewer_uuid`, so an RE has to accept the IDR again when it comes back; only the current stage's reviewer; 400 for a blank comment
- `PATCH /v1/idrs/{idr_id}/field` — the current stage's reviewer (or an admin) edits one field of an IDR in review: body `{report_id, field_path, new_value}` (`report_id` left out for `header.<column>`). The new value is written into the IDR and the old one logged. Returns the IDR with its reports and `field_edits`. 400 when the IDR isn't in review, the path isn't a field of the report, the value doesn't fit, or nothing changes; 403 for anyone but that reviewer; 409 if the field changed meanwhile
- `POST /v1/idrs/{idr_id}/pay-items/{pay_item_id}/revise` — same caller: body `{revised_quantity}`; the pay item is found by its id in whichever report holds it. 404 when no report of the IDR holds it
- `POST /v1/idrs/{idr_id}/pay-items/{pay_item_id}/approve` — same caller, no body: logs their approval of the item as it stands (nothing in the report changes) and returns the IDR with `field_edits`. Approving an item they have already attested to at this stage is a 200 that logs nothing
- `POST /v1/idrs/{idr_id}/pay-items/add` — same caller: body `{report_id, item_no, budget_code, quantity, unit, description}`; appends a pay item to that report (General, SWCB or AC), logged as added by the reviewer
- `POST /v1/idrs/{idr_id}/reports/{report_id}/trucks/add` — same caller: body is a truck with any of the keys the report form saves (`truckOrTicketNo`, `slump`, `loadSizeCy`, `endBatch`, `mixingRevs`, `startDischTime`, `endDischTime`, `airContent`, `concTemp`, `cylinderNumbers`, `inspectionSticker`); appends it to that Concrete Truck & Mix Info report's trucks, logged as added by the reviewer. 400 for a report that isn't a CONC_MIX, or a truck with neither a truck or ticket number nor a slump
- `POST /v1/idrs/{idr_id}/admin/unlock` — admin only: an approved IDR (or one in `stage2_review`) goes to `stage2_review` with `re_signature_path`, `re_signed_at`, `re_reviewer_uuid` and `stage2_accepted_at` cleared, so an RE must accept and approve again; the IDR number stays. 400 for a draft, submitted, Stage 1 or deleted IDR
- `POST /v1/idrs/{idr_id}/admin/delete` — admin only: soft delete at any status (`status = 'deleted'`, `deleted_at`, `deleted_by`; the row is kept). Deleting an IDR already deleted is a 200 that changes nothing
- `POST /v1/idrs/{idr_id}/reports/{report_id}/attachments/upload-request` — start a two-step upload: records a pending attachment (name, description, file details; `uploaded_by` is the signed-in user) and returns a signed Storage upload URL plus the headers to send; draft only, not on an auto-General
- `POST /v1/idrs/{idr_id}/reports/{report_id}/attachments/upload-complete` — mark a pending attachment uploaded once its file is in Storage (`attachment_id` in the body); draft only
- `PUT /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}` — replace an attachment's name and description; draft only
- `GET /v1/idrs/{idr_id}/reports/{report_id}/attachments` — list a report's uploaded attachments (pending ones left out)
- `GET /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}/download-url` — short-lived signed URL; 404 while pending
- `DELETE /v1/idrs/{idr_id}/reports/{report_id}/attachments/{attachment_id}` — remove an attachment, pending or uploaded (Storage file, then record); draft only
- `GET /v1/idrs/{idr_id}/export` — an IDR as an .xlsx on the DDC report-forms template (General, SWCB, AC, Conc Mix and Conc Cyl pages) (a draft's pages are marked "DRAFT - Not for Submission"), stored in the `idr-exports` bucket; returns `{download_url, filename}`, the URL valid 10 minutes
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
  - Emails are stored as entered, matched without regard to case at sign-in, and unique the same way
    (`idx_users_email_lower`, migration 016). An upsert on email is `ON CONFLICT (lower(email))`.
  - User `327d3ed2-a3d6-4235-9408-7fe721b12bed` in the live database is Genghis Khan, a seeded inspector, and
    the id the frontend sent for everyone before sign-in existed. H0 mistook the row for a placeholder and
    renamed it `legacy-demo@icid.local`; his email (`KhanG@magnoleng.pc`) has been put back, and
    `seed_auth_users.sql` no longer touches the row except to undo that rename. He has no password.
  - **No ownership checks on reading and editing (yet).** Any signed-in user can read, edit or export any IDR by
    id, and list IDRs for any reporter (`?reporter_uuid=` is a filter, not an identity), except that nobody is
    listed another person's draft. An admin lists only the projects assigned to them in `project_users`, like
    anyone else. Submitting and the review routes are the exception: they check project roles, below. The
    admin-only routes are `/v1/admin/cleanup-demos`, the two under `/v1/projects/{project_id}/roles`, and
    `/v1/idrs/{idr_id}/admin/unlock` and `/admin/delete`.
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
      `admin_delete`; nothing is removed, attachments and audit rows included. Every review statement and
      submit match only rows with `deleted_at IS NULL`, the lists leave deleted IDRs out, and the day and the
      IDR number are free again. There is no undelete. `GET /v1/idrs/{idr_id}` and the export still answer for
      a deleted IDR by id, for anyone signed in; the edit routes refuse it, as it isn't a draft.
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
    reviewer added (`pay_item_add`, the whole item as `new_value`).
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
    that saves a Conc Cyl after submit (a returned draft) must send each cylinder's `id` back. The edit routes
    don't name a cylinder by its id yet.
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
    list or a pay item is refused, as is a pay item's `id`. `report_data` has no schema on the server, so the
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
    change what was signed, and nothing under `idrs/` is ever overwritten or removed.
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
    so a cylinder without one (a draft's) has no chain. The edit routes don't accept `cylinders[<id>]` paths yet.
  - **Not redlined:** AC Bk's safety remarks, which print inside its remarks text as they stand.
  - Lists addressed by position (`additionalWorkforce[0].count`) are matched by position, with the limitation
    noted under "Reviewer edits".
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
