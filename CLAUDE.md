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
| `api/v1/` | HTTP endpoints, one module per resource (`auth.py`, `signatures.py`, `admin.py`, `admin_users.py`, `admin_projects.py`, `admin_pay_items.py`, `users.py`, `projects.py`, `idrs.py`, `reviews.py`, `field_edits.py`, `attachments.py`, `exports.py`, `contract_items.py`, `quantities.py`, `properties.py`, `task_orders.py`, `pirs.py`, `pir_pages.py`, `flags.py`, `non_assessable_areas.py`, `features.py`, `segments.py`, `match_lines.py`, `pir_import.py`, `idr_flags.py`, `idr_segments.py`, `progress.py`, `construction_reports.py`, `assessment_summary.py`, `pir_title_block.py`). Each exports a `router`; one with routes under another resource's prefix exports a second router named for it (`project_router`, `property_router`, `pir_router`, `page_router`, `flag_router`). `api/index.py` registers them all, in order. `_shared.py` is not a router: it holds what several of them use (reading the date and discipline filters, reading a text field off a request body (the admin project form's), checking a new password, shaping an IDR list's rows, and, for a PIR's drawings, the deleted-parent checks, the pay-item check and merging and checking a PATCH). |
| `api/queries/` | SQL functions, one module per table area. The only place SQL is written. `_shared.py` is not a table area: it holds the SQL pieces the PIR drawings' modules share (typed placeholders, the pay-item check, the joins from a page to its PIR and property). `pir_import.py` is the Smart PIR import's one statement, across the drawings' tables. `idr_flags.py` is which flags an IDR covers: its overrides and the claim and release CTEs the IDR statements carry; `idr_segments.py` the same for curb segments, with their quantity CTE. `progress.py` is the progress rollup's one statement: a project's flags and curb segments by property, pay item and state. `construction_report.py` reads a Construction Report's property, title block, IDR, flags and drawings; `assessment_summary.py` a project's claimed flags for the Assessment Summary. |
| `api/schemas/` | Pydantic request/response models, one module per resource. |
| `api/db/` | Connection plumbing: `connection.py` opens the psycopg connection, `runner.py` exposes `run_query(sql, params)`. |
| `api/storage/` | Supabase Storage plumbing: `client.py` is the only module that imports `supabase`. |
| `api/services/` | Logic spanning several queries or Storage. Endpoints call it; it never builds SQL. **Sign-in and users:** `auth.py` (the `AuthProvider` interface, `LocalAuthProvider` with bcrypt and JWTs, the `auth_provider` singleton, `current_user` / `current_admin`, and the role, member and demo-mode dependencies), `demo.py` (deleting a demo user at sign-out), `signatures.py` (a user's signature upload and confirm, and the copy an IDR keeps at submit). **IDRs:** `auto_general.py`, `field_edits.py` (a reviewer's edit: the field-path grammar, finding the field, running the edit, keeping an auto-General in step), `idr_purge.py` (a permanent delete: the row, then its files in the three buckets), `attachments.py`, `quantities.py` (an IDR's pay items as `icid.quantities` rows: `extract_rows`, `quantity_rows_for_idr`, `write_quantities`), `disciplines.py` (report type to discipline and back). **Pay items:** `pay_items.py` (a pay item's field rules, for the form and the CSV), `pay_item_units.py` (bid units and their short forms; `FLAG_UNITS`, `SEGMENT_UNITS`), `pay_item_import.py` (a pay-item CSV), `budget_rollup.py` (budget against actual). **PIRs and flags:** `pir_image.py` (a PIR's scan as pages: the signed upload, the file's first bytes, rendering within the caps, replacing the pages, signing a page's URL), `flag_area.py` (an area from its shape, in square feet), `pir_import.py` (the Smart PIR import's checks), `idr_flags.py` (an IDR's flags, their rollup, the pay items they fence off and the export's "PIR Flags" lines), `idr_segments.py` (the same for its curb segments), `progress_rollup.py` (progress from flags and segments), `construction_measure.py`, `construction_sketch.py`, `construction_slabs.py` and `construction_report.py` (the Construction Report: its measure, its sketch's geometry, its slabs, and the PDF and ZIP), `area_containment.py` (how much of a non-assessable area lies outside its flag), `assessment_summary.py` (the monthly .xlsx). **The export:** `export.py` (the dispatcher: loads the IDR, allocates and orders the sheets, numbers pages, stamps both signatures, stores the file and signs its URL), `export_common.py` (shared layouts and stampers, and how a redline is drawn), `export_redlines.py` (the IDR's edits as each field's chain and each pay item's rows), one module per form (`export_general.py`: Gen Fr / Gen Bk / Report Cont; `export_swcb.py`: Conc Fr / Conc Bk; `export_ac.py`: AC Fr / AC Bk; `export_conc_mix.py`, `export_conc_cyl.py`; `export_attachments.py`: pages copied from Sketch Cont) and `xlsx_template.py` (`WorkbookTemplate`: edits the .xlsx package XML directly — cells, text runs, styles, sheet copies, pictures, text boxes, print setup). |
| `api/core/` | App-wide configuration: env loading. `DATABASE_URL` and `JWT_SECRET_KEY` are required at startup; every setting (JWT, `CRON_SECRET`, the Storage buckets, URL lifetimes and the PIR upload caps) is listed in `README.md`. No business logic. |
| `tests/v1/` | Pytest suites mirroring `api/v1/`, one file per endpoint module. Service tests sit in `tests/` (`test_*.py`), files they read in `tests/fixtures/` (`smart_pir_sample.json` is a valid Smart PIR import). |
| `schema.sql` | Authoritative DDL for the `icid` schema. `seed.sql` holds mock data; `seed_sidewalk_pay_items.sql` seeds the pay-item catalog (`spec_items`, and `contract_items` for `HWS0023`, each row carrying its spec item's number, description, unit and section) and runs after it. `seed_auth_users.sql` seeds the admin account and its project assignments and `seed_test_project.sql` the Test Project (`DEMO01`). |
| `migrations/` | Numbered SQL migrations, run by hand in the Supabase SQL editor. A schema change ships as a migration plus the matching `schema.sql` edit. Every migration through 046 is in the live database (041 was skipped). A data migration (039 and 040, M1: SE384's site data moved to HWS0023; 042, Walk003's budget code; 045, the curb segments' pay items and claims) has no `schema.sql` edit. When new code needs a migration run first (or the code before it breaks once it is), say so in that feature's section until it has been run, then drop the note; `docs/data-model.md` keeps the migration history. |
| `docs/` | `data-model.md`: developer reference for the tables, the Storage buckets, the auto-General and the migration history. `endpoints.md`: every route, its body, responses and refusals (CLAUDE.md keeps an index). |
| `README.md` | How to run, configure, test and deploy the backend. The one README. |
| `templates/` | `report_forms.xlsx`, the export base (built from `report_forms_source.xltx` by `scripts/clean_report_template.py`). `conc_cyl_source.xlsx` is the DDC Data Sheet for Concrete Test Cylinders, the source of the template's Conc Cyl tab. |
| `scripts/` | One-off local utilities: `clean_report_template.py`, which builds the export template, `merge_conc_cyl_template.py`, which added the Conc Cyl tab to `report_forms_source.xltx` (already run; it refuses to run twice), and `backfill_quantities.py`, which fills `icid.quantities` for the IDRs already approved (it imports the app's queries and services, and works on the database `.env` names). Not imported by the app. Seeds and ad-hoc SQL are run in the Supabase SQL editor. |

### Endpoints

The full list, each route's body, responses and refusals, is **`docs/endpoints.md`**: update it when endpoints change
(and the count in `tests/v1/test_auth.py`). By resource, every `/v1` route needing a bearer token but the three public
auth routes:

- **Auth:** `GET /status`; `POST /v1/auth/login`, `/demo`, `/logout` (public); `GET /v1/auth/me`
- **Admin** (admin only; cleanup also the scheduler): `/v1/admin/cleanup-demos`; `/v1/admin/users` (list, create,
  edit, password, soft delete, un-delete, permanent delete); `/v1/admin/projects` (list, create, assessment rates,
  pay items with their CSV import)
- **Users and signatures:** `/v1/users/`, `/v1/users/me/password`; `/v1/signatures/upload-request`, `/confirm`
- **Projects:** `/v1/projects/` and `/{project_id}`, `/roles` (admin), `/quantities`, `/progress-rollup`, `/assessment-summary` (with `.xlsx` and `/tailored.xlsx`),
  `/properties`, `/task-orders`; `/v1/contract_items/`
- **Sites:** `/v1/properties/{id}` (with `/admin/soft-delete`, `/admin/undelete`, `/pir`, `/pirs`,
  `/construction-report`); `/v1/task-orders/{id}` (with its admin routes, `/properties/{property_id}`,
  `/progress-rollup`)
- **PIRs and drawings:** `/v1/pirs/{id}` (with its admin routes, `/image/upload-request`, `/image/complete`, `/pages`,
  `/flags`, `/features`, `/segments`, `/match-lines`, `/import`, and the deprecated R1d routes); `/v1/pir-pages/{id}`
  (`/scale`, `/rotation`, `/flags`, `/features`, `/segments`, `/match-lines`, `/annotations`); `/v1/flags/{id}` (with
  its admin routes and `/non-assessable-areas`); `/v1/non-assessable-areas/{id}`, `/v1/features/{id}`,
  `/v1/segments/{id}`, `/v1/match-lines/{id}`
- **IDRs:** `/v1/idrs/` and `/{idr_id}` (header, reports, submit, export, attachments); the review queue and moves
  (`/queue`, accept / approve at both stages, `/return`); reviewer edits (`/field`, pay items, trucks); the admin
  moves (`/admin/unlock`, `/delete`, `/undelete`, `/permanent`); `/attributed-flags`; `/attributed-segments`; `/construction-reports`

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
  - Emails are stored as entered, matched without regard to case at sign-in, and unique the same way among
    users who aren't deleted (`idx_users_email_lower`, migration 016; partial, `WHERE deleted_at IS NULL`, since
    028). An upsert on email has to name that predicate: `ON CONFLICT (lower(email)) WHERE deleted_at IS NULL`.
    A bare `ON CONFLICT (lower(email))` no longer matches any index and fails.
  - User `327d3ed2-a3d6-4235-9408-7fe721b12bed` is Genghis Khan (`KhanG@magnoleng.pc`), a real seeded inspector
    with no password, not a placeholder: leave the row alone (`seed_auth_users.sql` only undoes H0's mistaken rename
    of it to `legacy-demo@icid.local`).
  - **No ownership checks on reading and editing IDRs (yet).** Any signed-in user can read, edit or export any IDR
    by id, and list IDRs for any reporter (`?reporter_uuid=` is a filter, not an identity), except that nobody is
    listed another person's draft. An admin lists only the projects assigned to them in `project_users`, like
    anyone else. The exceptions check the project. Submitting and the review routes check project roles (below).
    The quantities and progress routes and every site and PIR route take a user assigned to the project, in any
    role, or an admin (403 `Project access required`; 404 for a project that doesn't exist): `project_member`, or,
    by id, `property_member`, `task_order_member`, `pir_member`, `pir_page_member`, `flag_member`,
    `non_assessable_area_member`, `feature_member` and `segment_member` (all through `_readable_on_project`; a page
    and a flag reach their project through their PIR, an area through its flag, a feature and a segment through
    their page). `docs/endpoints.md` marks every admin-only route.
  - **Project members write properties, task orders and PIRs** (S1, S2). Creating and editing each, and
    bundling and unbundling, take a user on the project in any role, or an admin; deleting and restoring stay
    admin only. Each write route checks membership first (404 / 403), then refuses a demo user with 403
    (`no_demo_property_writes`, `no_demo_task_order_writes`, `no_demo_pir_writes`: their rows would block
    deleting them, and `cleanup_abandoned_demo_users()` with them). A soft-deleted row is the admin's: anyone
    else gets 400 on changing it (`refuse_deleted_for_member` in `api/v1/_shared.py`). `created_by`,
    `added_by` and `digitized_by` are the signed-in user.
  - **Admin user routes** (`api/v1/admin_users.py`) are their own router, taking `current_admin`: the
    `/v1/admin` router takes `admin_or_cron`, which the scheduler's secret also passes. Names
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
  - **Deleting a user is a soft delete** (`POST /v1/admin/users/{user_id}/delete`, migration 028):
    `deleted_at` and `deleted_by` on the user's row, and nothing else. Their IDRs, reviews, project roles,
    signature and audit rows stay, and their name still shows wherever they acted (names are joined from
    `icid.users` whatever `deleted_at` says). Unlike an IDR, a user has no `deleted` status: deleted is
    `deleted_at IS NOT NULL`.
    - **A deleted user is locked out.** Sign-in looks an email up only among users who aren't deleted
      (`_BY_EMAIL` in `api/queries/users.py`), so they get the same 401 `Invalid email or password` as a wrong
      password, after the same work. `current_user` refuses a token they already hold (401 `Invalid token`, as
      for a user who no longer exists), so they are out at once, not when the token expires.
    - **An admin can't delete their own account** (400), so the last admin can't remove themselves. Deleting a
      user already deleted is 404 `User is already deleted`; the first deletion's time and admin stand.
    - **The lists leave deleted users out** (`GET /v1/admin/users` keeps them for `include_deleted=true`, its rows
      `AdminUserOut`); `GET /v1/projects/{id}/roles` still lists a deleted user's roles, unmarked: that is where an
      admin sees what to revoke.
    - **A deleted user can't be given a project role** (400 on a grant); a revoke still works. Their name,
      email, role and password can still be changed through the other admin routes.
    - **Their email is free.** The unique index covers only users who aren't deleted, so a new account can take
      a deleted account's email (the deleted row keeps it too), and create and edit look for a clash only among
      users who aren't deleted.
    - **Un-delete** (`POST .../undelete`) clears `deleted_at` and `deleted_by`; the user signs in again with
      the password they had. **If another user now has their email it is refused** (409 with
      `existing_user_uuid`): the statement checks, and the index backs it. Change one of the two emails (the
      deleted user's can be edited while deleted) and try again.
    - **Permanent delete** (`DELETE .../permanent`) takes only a user who is already soft-deleted (404 `User
      must be soft-deleted first`), and only one nothing names. `count_user_references` counts what does: IDRs
      they reported, reviewed at either stage or deleted; project roles; audit rows; field edits; attachments
      they uploaded; properties and task orders they created or deleted; properties they bundled into task
      orders; PIRs they digitized, signed as built or deleted; flags they created or deleted; PIR pages they
      replaced or deleted; non-assessable areas, features, segments and match lines they created or deleted; IDR flag
      and segment attributions they set. Any of those is a 409 with the counts under `references`. Those are all the
      foreign keys to `users.uuid` (30 in `schema.sql`,
      `users.deleted_by` the one not counted; the `deleted_by` of a property, a task order, a PIR, a flag, a PIR
      page, an area, a feature or a segment, an attribution's `set_by` and a PIR's
      `as_built_signed_by` are `ON DELETE SET NULL`, but are counted anyway); none
      cascades, and the DELETE itself is refused by them if a row appears at the last moment
      (also 409). So a user who ever did anything stays soft-deleted for good, which is the usual case; a
      project role is the one thing an admin can clear first (revoke it).
      - Then the user's own signature file (`users/{uuid}/signature.png`) is removed from Storage, best-effort.
        Signature copies under `idrs/` belong to IDRs, and such a user has none.
      - **The record of it is a log line**: `User permanently deleted: user_uuid=... email=... admin=... at=...`
        at INFO. `users.deleted_by` is `ON DELETE SET NULL`, so deleting an admin for good doesn't block on the
        users they deleted.
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
    - **Deleting is a hard delete, and safe.** A report's pay items and the rows of `icid.quantities` are
      copies, and the one foreign key to a contract item, `flags.pay_item_id`, is `ON DELETE SET NULL`: a flag
      mapped to a deleted item keeps its geometry and quantity and loses only its mapping. A deleted item leaves
      the picker; reports that used it, and their quantities, are unchanged. There is no undo for a delete or a
      clear.
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
      their only `project_users` row there, so the project leaves their list; their IDRs stay. A role can be
      revoked from a deleted user, not granted to one. Nothing stops a
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
  - **A reviewer edits a Conc Cyl through `PATCH /field`** (N4), like any other report, on the paths "Conc Cyl is
    redlined too" lists. Nothing lists those paths in code: the rule is still "the field must
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
  - **Three sources** (`quantities.source`, migrations 037 and 044): `manual`, the rows below, read off the reports;
    `from_flags`, one row per pay item from the flags the IDR claimed (see "IDR flags"); and `from_segments`, one row
    per pay item from the curb segments it claimed, in LF (see "Curb billing"). The backfill replaces only an IDR's
    manual rows.
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
  - **Budget against actual** (Q0) rides on the same response: `range`, `pay_items`, `totals_by_budget_code`,
    `project_total` and `unbudgeted_total`, beside `rows` and its three companions, which are unchanged.
    `compute_rollup` in `api/services/budget_rollup.py` builds it from two more reads and reads nothing itself:
    `list_quantities_with_budget` (`api/queries/quantities.py`) and `list_project_contract_totals`
    (`api/queries/contract_items.py`). So the route runs three statements.
    - **By contract value.** A quantity is worth its amount times the unit price of the contract item with its
      key, `(project_id, pay_item_ref = item_no, budget_code)`, matched exactly. A contract item is worth
      `bid_quantity * bid_unit_price`.
    - **`pay_items`** is one entry per ref and budget code that has quantities, by budget code then ref:
      `{pay_item_ref, description, unit, budget_code, actual_quantity, actual_value, budgeted_quantity,
      budgeted_value, unit_price, percent, unbudgeted}`. The quantities are summed in SQL (a LEFT JOIN to
      `icid.contract_items`, grouped), so the 10,000-row cap on `rows` never touches them. The description and
      unit are the contract item's. A contract item nothing was booked against has no entry.
    - **`totals_by_budget_code`** is one entry per budget code the project has contract items under:
      `{budget_code, actual_value, budgeted_value, percent, has_unbudgeted}`. **`project_total`** is
      `{actual_value, budgeted_value, percent}`.
    - **Every contract item counts toward the budget**, at the code's level and the project's, booked against
      or not. Otherwise the percentage would climb as the catalog shrank.
    - **`percent`** is actual over budgeted value, times 100, to 1 decimal place; null when the budgeted value
      is 0. It can pass 100 (over budget) and is returned as it is. An item bid at no quantity is still
      budgeted: its `percent` is null and its `unbudgeted` false.
    - **Unbudgeted** is a quantity no contract item matches: a ref the project doesn't have, a ref filed under
      another budget code, or an item with no ref or no budget code. It is in `pay_items` with
      `unbudgeted: true`, its `actual_quantity`, the description and unit as the reports gave them, and nulls
      for the rest; such rows are summed per unit, so two units make two entries. Without a unit price it has
      no value, so it is in no total. `unbudgeted_total` is `{actual_value: null, row_count}`, the count being
      of those entries. `has_unbudgeted` marks a budget code some of them were filed under; one filed under a
      code with no contract items shows only in `pay_items`.
    - **The filters narrow the actuals only**, the dates and the other four alike; the budget is always every
      contract item of the project. So a filter on `budget_code` still lists every code, the others at 0%.
    - **`cumulative=true`** leaves `from` and `to` unapplied, for `rows` as well as the roll-up; the other
      filters still hold, and a malformed date is still a 400. `range` echoes what was asked: `{from, to,
      cumulative}`.
    - **Rounding.** Sums are kept exact and rounded once, where a figure is handed out: values to 2 decimal
      places, halves up. So a total can differ by a cent from the sum of the rounded entries under it.
      Quantities and unit prices are not rounded. All are JSON numbers.
    - **Units are not compared.** A quantity filed in another unit than its contract item's is still multiplied
      by that item's price.
- **Properties.** `icid.properties` (migration 029) holds where a project's work is done, one row per property
  (`api/v1/properties.py`, `api/queries/properties.py`, `api/schemas/property.py`).
  - **Two kinds** (migration 035): a **sidewalk**, a tax lot named by its borough, block and lot, and a **ped
    ramp**, a corner of an intersection. `kind` is `sidewalk` or `ped_ramp`; a create without it is a sidewalk (as
    every property was before). A sidewalk has `block`, `lot`, `address` and `property_type` (all required) and an
    optional `violation_no`; a
    ped ramp has `intersection_street_1`, `intersection_street_2`, `quadrant` (`NE` `NW` `SE` `SW`),
    `facing_street` (`street_1` or `street_2`) and `ramp_type` (`PERPENDICULAR` `PARALLEL` `CUT_THROUGH`), all
    required. Both have `boro` (the five boroughs spelled out). `PropertyCreate` refuses a field the kind needs
    left out and any field of the other kind (422, before the database's CHECK `properties_kind_fields_consistent`
    can); the text is trimmed, a blank street is 400, streets and `violation_no` at most 200 characters. Either kind
    takes an optional `community_board` (CB, migration 043): trimmed, blank or null is NULL, at most 10 characters
    (400), cleared by a PATCH null as `notes` is.
  - **Borough, block and lot, no BBL** (migration 036 dropped `bbl`). The borough is sent, for either kind, and
    can be changed. A block is at most 5 digits and a lot at most 4, sent as text and stored without their leading
    zeros (`"00123"` is `"123"`, `"0000"` is `"0"`; `_tax_map_number` in `api/schemas/property.py`, a 422 for
    anything but digits or for too many; the table's CHECKs `chk_properties_block_format` and
    `chk_properties_lot_format` hold the same). Responses carry `boro`, `block` and `lot` as stored (null for a
    ped ramp). A BBL, if an export to a city system ever needs one, is the borough's digit and the two padded
    (`LPAD(block, 5, '0')`, `LPAD(lot, 4, '0')`).
  - **The kind never changes.** PATCH with another `kind` is 400 `Property kind cannot be changed. Delete and
    recreate.`; the same kind counts as nothing sent. PATCH merges the fields sent over the property and checks
    the whole as `PropertyCreate` (a 422 in FastAPI's own shape), then writes the record whole.
  - **Labels are worked out, not stored.** Every property response (task orders' `properties` included) carries
    `display_address`: a sidewalk's `address`, or a ped ramp's `{street 1} & {street 2} — {quadrant} Quadrant
    (Facing {the street facing_street names})`. The frontend shows `display_address` wherever it showed `address`.
  - **The list's `status`** (worked out in its statement): `{state: "constructed_on", date}` once the live PIR is
    signed as built (`as_built_signed_at`; `date` its day in `FORM_TIMEZONE`, as the export dates signatures);
    else `{state: "task_ordered", date: null}` when the property is in a live task order; else `{state: "none",
    date: null}`. `task_order_id` and `task_order_no` are the id and number of the latest live task order it is
    in (`issued_date` newest, undated last, then newest created; `_LATEST_TASK_ORDER`), or null; `GET
    /v1/properties/{id}` carries both too (`PropertyDetail`), for the Site Info page's Back to task order. `pir_status` stays as it was. The list orders sidewalks by
    address and ped ramps by their intersection, then quadrant, together (`PROPERTY_ORDER`, shared with a task
    order's `properties`).
  - The five enums are Postgres enum types, unlike the rest of the schema: the statements cast their
    parameters (`%s::icid.boro`) and read them back as text.
  - **Fields:** `address` (200) and `notes` (2000, optional; blank is NULL), both trimmed; a blank address is
    400. `property_type` is one of `1_3_family`, `4_plus_family` and
    `expedited` (a Pydantic `Literal`, so anything else is FastAPI's 422, unlike the admin forms' 400s; the table
    has the same CHECK). The response also carries `id`, `project_id`, `created_at`, `updated_at`, `created_by`,
    `deleted_at` and `deleted_by`. Every write sets `updated_at`.
  - **`pir_status`** (`PropertyListItem`): `none` without a live PIR, `dot_signed` with one, `as_built_signed`
    once its `as_built_signed_at` is set; read in the list's statement (a LEFT JOIN to the live PIR, which
    `uq_pirs_property` keeps to one). Only the list has it, and `status`.
  - **A borough, block and lot is unique per project among live sidewalks** (`uq_properties_project_sidewalk_bbl`
    on `(project_id, boro, block, lot)`, partial, `WHERE kind = 'sidewalk' AND deleted_at IS NULL`); the same lot
    in another project, or in another borough, is fine, and ped ramps have no such rule. Create is
    `ON CONFLICT DO NOTHING` (no target, as for `create_idr`); edit and un-delete check with `NOT EXISTS` in the
    statement, and the index backs them. Each answers a clash with 409 `{detail, existing_property_id}`, the
    holder found by `get_sidewalk_at`: `A site at {boro} Block {block} Lot {lot} already exists in this project.`
    (un-delete: `Another site at ... now exists in this project.`).
  - **Who** (the rules: "Sites and PIR drawings" in `docs/endpoints.md`): `project_member` for the list and create,
    `property_member` by id and inside `_writable_property` for edit (S2). `include_deleted=true` from anyone but
    an admin is ignored, not refused (the IDR list refuses it).
  - **Soft delete, IDR-style:** `POST .../admin/soft-delete` sets `deleted_at` and `deleted_by` and frees a
    sidewalk's lot; again is a 200 that changes nothing. `POST .../admin/undelete` clears both; 404 `Property is not
    deleted` for one that isn't. There is no permanent delete. A deleted property can still be read by id, and
    edited by an admin; an edit of a deleted one isn't checked against the live sidewalks' lots (un-deleting it is).
  - `created_by` is NOT NULL and `NO ACTION`, so a user who added a property can't be permanently deleted:
    `count_user_references` counts properties they created or deleted.
- **Task orders.** `icid.task_orders` and `icid.task_order_properties` (migration 030): a project's task
  orders, each a bundle of the project's properties (`api/v1/task_orders.py`, `api/queries/task_orders.py`,
  `api/schemas/task_order.py`).
  - **A task order** follows Properties in everything: `task_order_number` (at most 50 characters, trimmed, a
    blank one 400) is unique per project among live task orders (`uq_task_orders_project_number`, partial),
    matched exactly; `issued_date` is a date (malformed is 422) and `estimate_notes` text (2000, blank is
    NULL). Create, edit and un-delete answer a taken number with 409 `{detail, existing_task_order_id}`.
    `include_deleted` from anyone but an admin is ignored. Soft delete again is a 200 no-op; un-deleting a
    live one is 404.
  - **Who (S1).** Reading, **creating, editing, bundling and unbundling** take a user on the project in any
    role (`inspector`, `oe`, `re`), or an admin: `project_member` for create, `task_order_member` (inside
    `_writable_task_order`) for the rest, so a user on another project gets 403. **Soft delete and un-delete
    stay admin only**, as for properties, PIRs and IDRs. The check on a deleted row is the shared
    `refuse_deleted_for_member`.
    - **Demo users only read** (`no_demo_task_order_writes`; why, under "Sign-in": their rows would block deleting
      them, and the daily cleanup with them). A demo user on another project still gets 404 first.
    - **A soft-deleted task order is the admin's**: anyone else gets 400 `Task order is deleted` on PATCH and
      unbundle, and bundling into one is 400 for everyone, so nobody changes what an un-delete would restore.
  - **Two shapes:** `TaskOrderListItem` (the list, with `property_count`) and `TaskOrderDetail` (every other route,
    its `properties` read in the same statement, `jsonb_agg(to_jsonb(p))`).
  - **Bundling** (`POST .../properties/{property_id}`) adds a row to `task_order_properties`, with `added_by`
    the signed-in user (any project member, not necessarily an admin). The property must be of the task order's project and neither may be soft-deleted (400); the
    INSERT itself only matches when that holds (`INSERT ... SELECT ... JOIN ... ON p.project_id =
    t.project_id`), so the checks before it can't be raced past. A property already in the task order is 409.
    A property can be in several task orders, once in each. **Unbundling** deletes the row, 204, whatever the
    property's state; an admin can also unbundle from a soft-deleted task order, so they can always clean up.
  - **Soft deletes never touch the bundle.** A soft-deleted task order keeps its rows, so un-deleting it
    restores the full bundle. **A soft-deleted property stays bundled but hides**: the detail's `properties`
    and the list's `property_count` both filter `properties.deleted_at IS NULL`, so it disappears from every
    task order it is in and reappears, unchanged, when it is un-deleted. Not even an admin sees the hidden rows
    for now (backlog). Only a permanent delete of either side, not built yet, takes the rows (`ON DELETE
    CASCADE` on both foreign keys).
  - `created_by`, `deleted_by` and `added_by` name users, so `count_user_references` counts them.
- **PIRs.** `icid.pirs` (migration 031): a property's digitized PIR, the DOT survey sheet an inspector signed
  off (`api/v1/pirs.py`, `api/queries/pirs.py`, `api/schemas/pir.py`).
  - **One live PIR per property** (`uq_pirs_property`, partial, `WHERE deleted_at IS NULL`). Soft-deleting a
    PIR frees the property, so a replacement can be created while the old one is kept; un-deleting the old
    one is then a 409 `{detail, existing_pir_id}`, as is creating a second live one. No PIR can be created on
    a soft-deleted property (400); the INSERT itself selects only a live property.
  - **Who made it is the server's.** `digitized_by` is the signed-in user and `digitized_at` is `now()`; the
    body can't carry either. The body is `inspector_initials` (trimmed, at most 16 characters, blank is 400)
    and `inspector_sign_date` (a date; malformed is 422).
  - **The as-built signature is not editable.** `as_built_signed_by` and `as_built_signed_at` are a
    signature: a later slice adds sign and unsign routes for them, as approve-stage2 signs an IDR. PATCH
    takes only the initials, the sign date and the north bearing; a null counts as not sent, as on the other
    PATCHes, but for `north_bearing` (migration 038, `icid.compass_point`), which it clears.
  - **DEPRECATED (R1e):** `source_image_url` and `source_image_scale` are still in every response, from page 1 (see
    "PIR pages").
  - **Finding a deleted PIR.** `GET /v1/properties/{id}/pir` gives only the live one; an admin finds the
    soft-deleted ones (to un-delete) with `GET /v1/properties/{id}/pirs?include_deleted=true`.
  - Each response carries the property's `project_id` (read in the same statement), which `pir_member` checks.
    **Creating and editing** go through `property_member` inside `_pir_writer_on_property` (create) and
    `pir_member` inside `_writable_pir` (edit); soft delete and un-delete stay admin only.
  - **A deleted PIR, or a live PIR of a deleted property, is the admin's.** A user who isn't an admin gets
    400 `PIR is deleted` or `Property is deleted` on PATCH, so a deleted property's children are no
    loophole; an admin can edit either, to clean up.
  - `property_id` doesn't cascade: a permanent delete of a property, if one is ever built, must refuse while it
    has PIRs.
- **Flags.** `icid.flags` (migration 032): the marked pieces of sidewalk on a PIR, one row each, with their
  shape, the DOT survey's classification and the quantity they come to (`api/v1/flags.py`, `api/queries/flags.py`,
  `api/schemas/flag.py`, `api/services/flag_area.py`).
  - **The body** (`FlagCreate`): `display_order` (an integer ≥ 0; not unique, the list sorts by it then by
    creation), `shape`, `dimensions`, `sketch_x` / `sketch_y` (optional), `rotation` (default 0),
    `surveyed_mark`, `surveyed_is_assessable`, `surveyed_non_assessable_reason`, `surveyed_thickness`,
    `added_in_field` (default false), `pay_item_id`, `reported_quantity` and `quantity_delta_reason` (optional),
    and `defect_codes` (default `[]`).
    The values each enum takes: shape `rectangle` `trapezoid` `triangle` `polygon` `arc`; mark `number_only`
    `cross` `cross_with_number` `tick` `none`; reason R1b's `tree_damage` `city_tree` `transition` `no_driveway`
    `hydrant` `fronting_city` `utility` `settlement` `other` and the R1e.1 survey's `tree` `driveway` `grass`
    `dirt` `existing_sidewalk` `asphalt` `tree_pit` `landmark` `vault` `subway` `scaffolding`
    `other_perm_obstruction` `other_temp_obstruction` (migration 034 widened the CHECK; every old value stays);
    thickness `4in` `7in` `7in_as_4in`; defect codes `1` `2` `3` `4` `5` `6A` `6B` `6C`, stored in that order,
    each once (the table's CHECKs hold the same). `is_constructed` is not the API's to write: an IDR's approval sets it (see "IDR flags").
  - **Dimensions, by shape** (feet; finite numbers, lengths above 0, no other keys): rectangle `{L, W}`;
    trapezoid `{a, b, w}` (the parallel sides and the width between them); triangle `{a, b}` (a right
    triangle's legs); polygon `{vertices: [{x, y}, ...]}` (three or more, in order, enclosing some area: a
    collinear set is refused); arc `{r, theta}` (a circular segment, `0 < theta ≤ 2π` radians). Checked by the
    `DIMENSIONS_BY_SHAPE` models; the database only knows `dimensions` is JSON.
  - **The area** (`compute_area`) is computed by the server on create and on every PATCH and stored as
    `computed_quantity`, square feet to 2 decimal places, halves up: rectangle `L × W`; trapezoid
    `(a + b) / 2 × w`; triangle `a × b / 2`; polygon the shoelace formula, its absolute value; arc
    `r² / 2 × (θ − sin θ)`. Lengths are taken as decimals as written (`0.1 × 0.3` is `0.03`).
  - **Rules** (422, from `FlagCreate`): an assessable flag has no `surveyed_non_assessable_reason`, one that
    isn't has one; `surveyed_mark = 'none'` if and only if `added_in_field` (a flag added on site carries no PIR
    mark); `reported_quantity`, when set, is above 0.
  - **Net area.** `computed_quantity` is the shape's own area. Every response also carries
    `non_assessable_areas_count`, `non_assessable_quantity` (the sum of its areas'), `subtracted_quantity` (the sum
    of its live `subtract` features', each its override or else its area) and `net_quantity`: the area less those
    two, never below 0, worked out in the statement (two `LATERAL` subqueries), not stored. A flag's areas are its
    live ones, and, once the flag is deleted, the ones deleted with it.
  - **Overrides.** `reported_quantity` NULL means there is none: the net area stands. Every response
    carries `quantity` (`COALESCE(reported_quantity, net_quantity)`) and `is_overridden`. A PATCH with
    `reported_quantity: null` reverts to the net area and clears `quantity_delta_reason`.
  - **PATCH merges, then checks the whole.** The body is laid over the stored flag and the result is checked
    as `FlagCreate` checks a new one (a 422 in FastAPI's own shape), so a change that breaks a rule only
    together with the stored values is caught. A new `shape` needs its `dimensions` in the same PATCH. A null
    clears `sketch_x`, `sketch_y`, `surveyed_non_assessable_reason`, `pay_item_id` and `quantity_delta_reason`;
    on any other field (but `reported_quantity`, above) it counts as not sent. The statement writes the merged
    record whole.
  - **Pay items.** `pay_item_id` is a contract item of the PIR's project: the route checks it (400 `Pay item
    belongs to a different project`, an unknown id included) and the INSERT and UPDATE match only when it
    holds. Mapping flags to pay items automatically is R1c. A deleted pay item leaves its flags unmapped
    (`ON DELETE SET NULL`).
  - **On a page (R1e).** Every live flag sits on a page of its PIR (`pir_page_id`; the database's
    `chk_flags_live_on_a_page` lets only a soft-deleted flag have none), and `sketch_x` / `sketch_y` / `rotation`
    are its place on that page, in the page's native, unrotated pixel space whatever its `rotation_deg`: x right
    and y down from the stored PNG's top-left corner (0..`width_px`, 0..`height_px`). `sketch_x` / `sketch_y` is
    the centre of the shape's bounding box and `rotation` the shape's own turn about it, in degrees clockwise. The
    frontend draws the overlay with the page's rotation, so a page's rotation never moves its flags; the API stores
    the three as sent and checks none of them against the page. Flags are created through `POST /v1/pir-pages/{id}/flags` (the R1b route on the
    PIR is a deprecated wrapper onto page 1). The PIR's list shows the live flags on its live pages; a page's,
    its live flags. A flag keeps `pir_id` too, which the statement copies from the page.
  - **Who** (the rules: "Sites and PIR drawings" in `docs/endpoints.md`): `pir_member`, `pir_page_member`, and `flag_member` by id, which
    reads the project and the page's, PIR's and property's deleted states through the flag's PIR in the same
    statement (responses carry `property_id` and `project_id`); writes add `no_demo_flag_writes`. An admin can
    still write under anything soft-deleted, but onto a replaced page, which nobody adds to.
  - **Deleting is a soft delete, for anyone on the project** (`DELETE /v1/flags/{id}`; the admin route does the
    same). Flags are never hard-deleted. **Un-delete is an admin's, and only onto a live page:** a flag with no
    page (the 3 R1b form flags migration 033 retired) or whose page was replaced is a 400.
  - **A flag's non-assessable areas go with it.** Its soft delete soft-deletes its live areas in the same
    statement, at the same moment (`now()`); its un-delete brings back the areas whose `deleted_at` is the flag's,
    so one deleted on its own before stays deleted. The route reads the restored flag again, since the statement's
    own read sees the areas as they were before it.
  - `pir_id` has no cascade: a flag is a record of its own. `created_by` is `NO ACTION`, so
    `count_user_references` counts flags created or deleted.
- **PIR pages** (R1e). A PIR's scan is a set of pages, `icid.pir_pages` (migration 033), each a PNG in the private
  `pir-images` bucket (`api/services/pir_image.py`, `api/v1/pir_pages.py`, `api/queries/pir_pages.py`,
  `api/schemas/pir_page.py`). R1d's single image on the PIR (`source_image_url`, `source_image_scale`) is gone.
  - **One-time setup:** the bucket from `migrations/031b_pir_images_bucket.sql` (private, 10 MB, PNG, JPEG and
    PDF).
  - **Two steps, like signatures and attachments**, so the file never passes through the backend's request (Vercel
    refuses a body over 4.5 MB). `upload-request` signs a URL for `staging/{pir_id}/{uuid}.{pdf|png|jpg}`; the
    client PUTs the file there; `complete` does the rest.
  - **Complete** reads the upload back (only a path in this PIR's staging folder, and only if the file is there),
    refuses one over `PIR_IMAGE_MAX_BYTES` (413) and **tells its type by its first bytes** (`%PDF`, the PNG
    signature, JPEG's `FF D8 FF`). **Every page of a PDF is rendered to PNG** at `PIR_IMAGE_RENDER_DPI` (150) with
    `pypdfium2` (Apache-2.0, bundling PDFium; PyMuPDF was passed over for its AGPL licence); a PNG is kept and a
    JPEG converted, as page 1. **The caps are checked before rendering:** at most `PIR_UPLOAD_MAX_PAGES` pages
    (15) and `PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE` (25) per page at that resolution, a 400 naming the cap that
    tripped. The pages go to a **new folder per upload**, `pirs/{pir_id}/{upload uuid}/page-{n}.png`; a scale sent
    with the upload is set on every page.
  - **Replacing is one statement** (`replace_pages`): the PIR's live flags on its live pages, those flags' live
    non-assessable areas, and the pages' live features, segments and match lines are soft-deleted (`deleted_at`, `deleted_by`;
    `_RETIRE_DRAWINGS` in `api/queries/pir_pages.py`), those pages are marked replaced the same way (their title
    blocks stay with them), and the new pages are inserted,
    numbered 1..N. The INSERT first reads the count of replaced pages, so the old pages are out of the partial
    unique index on `(pir_id, page_number)` before the new ones go in. **Pages are never hard-deleted**, so the
    flags' foreign key (no action) never blocks a re-upload. **The old pages' files are kept**: a flag's history
    still has its image (cleaning up files nothing live uses is a future housekeeping job). The staged upload is
    removed, best-effort. A page that can't be stored removes the pages stored so far (502), and the PIR's pages
    are unchanged.
  - `DELETE /v1/pirs/{id}/pages` does the same without inserting. **Per page:** scale (pixels per foot,
    recalibrating; above 0) and rotation (a quarter turn) are set one page at a time, a replaced page not at all.
    Setting the scale works out the length of every live segment on the page again, in the same statement.
  - **Responses carry a signed URL per page** (`image_url`, `image_url_expires_at`, good for
    `PIR_IMAGE_URL_EXPIRY_SECONDS`); the stored value is the path.
  - **Who:** reading takes `pir_member` or `pir_page_member`; writes take `writable_pir` (in `api/v1/_shared.py`,
    with `refuse_deleted_pir_for_member`) or the page's equivalent, which also refuses a replaced page. A demo
    user only reads.
  - **DEPRECATED (R1e), still served and unused:** the R1d routes, bodies and keys `docs/endpoints.md` names, and a
    PIR's `source_image_url` / `source_image_scale`, read from page 1 (see "Known rough edges").
- **Non-assessable areas, features and segments** (migration 034): what else is drawn on a PIR's page
  (`api/v1/non_assessable_areas.py`, `features.py`, `segments.py`; their query and schema modules of the same names).
  - **Non-assessable areas** are the parts of a flag that are not assessed (`reason`: `driveway` `grass` `dirt`
    `existing_sidewalk` `asphalt` `tree_pit` `landmark` `other`). Shape, dimensions and placement as a flag's (the
    same shapes and `DIMENSIONS_BY_SHAPE`, native page pixels); `display_order` ≥ 1 and unique per flag among live
    areas (`nas_flag_order_live_uq`: create is `ON CONFLICT DO NOTHING`, and edit checks with `NOT EXISTS`, each a
    409). Nobody adds one to a deleted flag or a flag on a replaced page. Their area is taken off the flag's (see
    "Flags", net area). **An area lies within its flag** (R1h.4, `api/services/area_containment.py`): create and edit
    (the merged area) place both on their page as the sketch does (`placed_outline`, the flag's page scale read by
    `get_flag_placement`) and refuse with 400 one more than 0.01 SF outside (`Non-assessable area <n> extends past flag
    <id>'s bounds by X SF; ...`); the flag is cut into triangles, so a concave polygon is exact. Without a page scale or
    a place it can't be told, and isn't refused. Changing a flag's own shape doesn't re-check its areas.
  - **Features** are point obstructions and sized features (`feature_type`: `manhole` `grate` `utility_cover`
    `hydrant` `fire_alarm` `tree_pit` `meter` `sign_or_meter` `mail_box` `telephone` `cellar_door` `pole`
    `detectable_warning` `other`). `area_impact` is `marker_only` (default), `subtract` or `sits_on_top`; the last
    two need a shape, and `subtract` names the flag its area comes off (`flag_id`, a live flag on the same page:
    the route checks, the statement matches only then, and the table's CHECK requires the id). A shape and its
    dimensions come together; a feature takes a flag's shapes and `circle` (`{r}`, area `π r²`, `compute_area`).
    `label` is trimmed, at most 200.
  - **Segments** are curb runs (`segment_type`: `curb_concrete` `curb_concrete_depressed` `curb_steel_faced`
    `curb_steel_faced_corner` `curb_granite` `other`): `points`, two or more `{x, y}` in page pixels, and
    `note` (2000). **`length_ft` is the server's:** the sum of the runs between consecutive points over the page's
    scale, to 2 decimal places, worked out in SQL (`segment_length_sql` in `api/queries/segments.py`, the one
    formula) when a segment is written, when its page's scale is set, and by the import; NULL while the page has no
    scale.
  - **Who:** reads take the project's members or an admin; writes the same, not a demo user (areas
    `no_demo_flag_writes`, features and segments `no_demo_pir_writes`). Under anything soft-deleted a user who
    isn't an admin gets 400 (`refuse_deleted_chain` with `PAGE_PARENTS`, and an area's `flag_deleted_at` first).
    `pay_item_id` is one of the project's (400, `check_pay_item` in `api/v1/_shared.py`), as on a flag.
  - **Deleting is a soft delete**, for anyone on the project; there is no un-delete route for any of the three
    (areas come back with their flag). `created_by` and `deleted_by` name users, so `count_user_references`
    counts them. All three foreign keys up (to `flags` or `pir_pages`) have no action: neither is ever
    hard-deleted.
- **PIR annotations.** `icid.pir_annotations` (migration 034): a page's title block, one row per page, keyed by
  `pir_page_id` (`api/queries/pir_annotations.py`, `api/schemas/pir_annotation.py`, the routes in
  `api/v1/pir_pages.py`). `PUT` writes the fields sent and leaves the rest (R1h; an upsert that matches only a live
  page, a page without a block getting one); the Smart PIR import still replaces a block whole. Text is trimmed, blank is
  NULL, `boro` is one of the five boroughs spelled out, the widths are ≥ 0 and come back as JSON numbers. It has no
  `deleted_at`: a replaced page keeps its title block, and its replacement starts without one.
- **The title-block editor's route** (R1h.2; `PATCH /v1/pirs/{pir_id}/title-block`, `api/v1/pir_title_block.py`,
  `api/queries/pir_title_block.py`): the fields the Construction Report prints, page 1's `contract_no`, three dates,
  streets (`cross_street_left`, `cross_street_right`, `frontage_street`; R1h.3, at most 60) and widths
  (`tax_map_width_ft`, `cc_curb_to_curb_ft`, NUMERIC(10,2)) and the PIR's `north_bearing`, written in one statement (two data-modifying CTEs) so a save is never half
  applied. Only fields sent are written, and only where they differ (`IS DISTINCT FROM`), so a save that changes
  nothing writes nothing, `updated_at` included; a page without a title block gets one only when a value is sent. The
  route reads the body itself, so a bad value is a 400 naming the field. Writers as for the PIR (`writable_pir`).
  **No audit trail:** nothing records who changed a PIR's title block or when, beyond `updated_at` (backlog: an
  `icid.pir_audit` table, written by every PIR write route).
- **Assessment rates.** `projects.cost_per_sf_4in` and `cost_per_sf_7in` (migration 034, NUMERIC(10,4), ≥ 0): the
  DOT's rates per square foot, set by an admin (`PATCH /v1/admin/projects/{id}/assessment-rates`, checked by the
  route: 400s, not 422s, but for text that isn't a number) and returned wherever a project's detail is
  (`ProjectDetail`). Nothing computes with them yet.
- **Smart PIR import** (`POST /v1/pirs/{pir_id}/import`; `api/v1/pir_import.py`, `api/services/pir_import.py`,
  `api/queries/pir_import.py`, `api/schemas/pir_import.py`): a sketch tool's file of what it drew on a PIR.
  - **The file:** `{schema_version: "1.0", pir_id, annotations?, pages: [{page_number, scale_px_per_ft?,
    rotation_deg?, annotations?, flags, features, segments, match_lines?}]}`. `pir_id` must be the path's (400). A flag is a flag's
    body plus `non_assessable_areas`; a feature names its flag by `flag_index` (its place in the page's `flags`
    list, 0 first), never by `flag_id`. The file's own `annotations` go to page 1, unless page 1 brings its own.
    `tests/fixtures/smart_pir_sample.json` is a valid one.
  - **Match lines** (R1h.1a): `{label, points: [{x, y}, {x, y}]}` per page, checked as `MatchLineCreate`. Dropped item
    by item: a line that fails its check, a label listed twice in one page entry (the second), a label its page already
    has live, and every occurrence of a label that would end up on three pages or more (the PIR's live lines' pages
    and the file's together). The statement's `new_match_lines` CTE skips a label taken meanwhile.
  - **Additive, item by item.** Each flag, area, feature, segment and title block is checked on its own, as its
    route checks it (and a pay item must be the project's); one that fails is **dropped** and listed in
    `dropped_items` with its kind, page, place and reason, and the rest go in. A page number the PIR has no live page
    for, or one listed twice, drops everything in that entry. An area whose flag is dropped goes with it; a feature
    whose `flag_index` names a dropped flag is dropped. An area reaching past its flag is dropped (`extends past its
    flag's bounds by X SF`), checked at the entry's scale, else the live page's. Nothing already on the pages changes, but a page's title block
    is replaced and its scale and rotation set (re-working out its existing segments' lengths).
  - **One statement** (`import_pir_drawings`): data-modifying CTEs over one JSON parameter per table, as
    `bulk_upsert_pay_items` does, so what passed lands together or not at all. Flags get their ids in Python so
    their areas and features can name them; an area joins the CTE of new flags, a feature's flag must be in it, and
    only live pages of the PIR take anything. A row the statement can't write (its page replaced meanwhile) is
    counted in `dropped`, without an item.
  - **Who:** `writable_pir` (members and admins, not demo users) and `refuse_deleted_pir_for_member`.
- **IDR flags** (R1f, migration 037; `api/v1/idr_flags.py`, `api/services/idr_flags.py`, `api/queries/idr_flags.py`,
  `api/schemas/idr_flag.py`): an IDR's pay items for sidewalk work come from the PIR flags it covers, as a live rollup.
  - **Scope.** An IDR may name a task order (`idrs.task_order_id`, set on create or through the header; a live one of
    its project). By default it covers the live flags on that task order's properties (a live flag on a live page of
    a live PIR of a live property); without a task order, none. **Overrides** (`icid.idr_flag_attributions`, one per
    IDR and flag) change that: `exclude` leaves a default flag out, `include` brings one in from elsewhere in the
    project. Including a default flag, or excluding an included one, just drops the override. Overrides survive
    returns, unlocks and deletes.
  - **States.** Each listed flag is `attributed`, `excluded`, `claimed` (by this IDR, once approved) or
    `claimed_elsewhere` (by another approved IDR: it can't be attributed; including it is a 409).
  - **Quantities by unit.** A flag's quantity is its override or else its net area, in square feet; in its pay item's
    unit (`FLAG_AMOUNT` in `api/queries/flags.py`) that is SF as is, SY over 9, CY at its thickness (7 in for `7in`,
    else 4 in) over 27, and 1 for EA. Any other unit can't be measured from a flag: a flag create or edit that gives one
    is a 422 (`A flag can't be measured in LF: give it a pay item in SF, SY, CY, EA`; a flag keeping a pay item keeps
    it), and the Smart PIR import drops such a flag. The rollup sums per pay item, rounded to 2 places, halves up.
  - **Live until approved, frozen after.** Before approval the list and the rollup read the flags as they are now;
    once approved, they are the flags it claimed and the `from_flags` quantity rows written at approval (a later
    change to a flag doesn't change them).
  - **Approval claims.** approve-stage2 reads the attributed flags just before (as it reads the report rows) and its
    one statement claims each still unclaimed (`claimed_by_idr_id`, `is_constructed = true`, `constructed_on` the
    IDR's `report_date`; `claim_flags_cte`) and inserts one `from_flags` quantity row per pay item from the claimed
    ones (`report_type` `PIR_FLAGS`, which the quantities filters count as Sidewalk; `insert_flag_quantities_cte`).
    A flag another IDR claimed meanwhile is left out: the response's `flags_dropped` and message say how many. A
    flag is claimed by at most one IDR. Approval is the only thing that marks a flag constructed.
  - **Releases.** Admin unlock and admin delete release every claim of the IDR in their statements
    (`release_flags_cte`); the flags can be attributed again. A delete records the released flags in its
    `admin_delete` audit note (`{"released_flag_ids": [...]}`); an un-delete back to approved claims those back and
    rewrites their quantity rows, and is a 409 if another IDR has claimed one since (unlock that IDR first). The
    database's `chk_flags_claim_consistent` keeps a flag constructed, with a date, exactly when it is claimed;
    `claimed_by_idr_id` has no action, so an IDR is never purged holding claims (a delete released them first).
  - **A claimed flag stays claimed** when it is soft-deleted (a page replaced included): the history and the
    quantities stand.
  - **Who.** Reading takes any user who can read the IDR. Excluding and including take its inspector while it is a
    draft, the reviewer who accepted it at the stage it is in (`stage_reviewer`), or an admin; never a demo user, an
    approved, deleted or submitted (not yet accepted) IDR.
  - **No hand entry of a flag-filled pay item.** A report save (and a reviewer's pay-items/add) holding an item whose
    item number and budget code are a counting flag's pay item's is a 400 (`refuse_flag_filled_pay_items` in
    `api/v1/_shared.py`), so nothing is counted twice. Flag lines aren't report pay items, so the reviewers'
    attestation gate passes them by: the attribution panel is their check.
  - **Export.** The rollup prints after the General's own pay items under a "PIR Flags" heading row
    (`_with_flag_lines` in `api/services/export.py`), live for a draft, frozen for an approved IDR.
  - Flag responses carry `claimed_by_idr_id`, `claimed_by_idr_number` and `constructed_on`; IDR responses carry
    `task_order_id`.
- **Curb billing** (migrations 044 and 045; `api/v1/idr_segments.py`, `api/services/idr_segments.py`,
  `api/queries/idr_segments.py`, `api/schemas/idr_segment.py`): an IDR's curb segments are attributed, claimed and
  billed exactly as its flags are (see "IDR flags"), side by side with them.
  - **Scope and overrides.** By default the live segments on its task order's properties; `icid.idr_segment_attributions`
    excludes one or includes one from elsewhere in the project. `GET /v1/idrs/{id}/attributed-segments`, and `POST
    .../{segment_id}/exclude` and `/include`, for the same people as the flag routes (`attribution_editor`, shared).
  - **Measured in LF.** A segment's amount is its `length_ft` when its pay item's unit is LF (`SEGMENT_AMOUNT`); a
    segment without a pay item, with one in another unit, or without a length (no page scale) is unmapped. Nothing
    refuses another unit on a segment's pay item.
  - **Approval claims them** in the same statement as the flags (`claim_segments_cte`, `segments.claimed_by_idr_id` and
    `constructed_on`, CHECK `chk_segments_claim_consistent`) and writes one `from_segments` row per pay item, report
    type `PIR_SEGMENTS` (`insert_segment_quantities_cte`). Unlock and delete release them (`release_segments_cte`); a
    delete's audit note adds `released_segment_ids`, and an un-delete back to approved claims those back (409 when
    another IDR has one since). Responses carry `segments_claimed`, `segments_dropped` and `segments_released`.
  - **Downstream.** Progress lists LF pay items too (`SEGMENT_UNITS`; a segment counts in `flag_count`), the
    quantities route and budget against actual read the `from_segments` rows, the export prints the segment lines
    after the flags' under "PIR Flags", and a report can't hold a segment-filled pay item by hand. The Assessment
    Summary and the Construction Report don't include curbs: both are the property owner's sidewalk assessment.
  - **HWS0023's curb items** (044): 4.08 AA Concrete Curb ($45/LF, already there), 4.08 AB Depressed Concrete Curb
    ($55/LF, 500 LF) and 4.08 GA Granite Curb ($150/LF, 400 LF). 045 mapped its live segments by kind and had IDR-001
    and Walk004, approved before this, claim theirs.
- **Construction Report** (R1h; `api/v1/construction_reports.py`, `api/queries/construction_report.py`, and
  `construction_measure.py`, `construction_sketch.py`, `construction_report.py` in `api/services/`): a property's
  as-built sheets for one approved IDR, DOT form 5/95, a letter-landscape PDF drawn with ReportLab.
  - **One sheet per PIR page** (R1h.1a) that holds flags the IDR claimed there, in page order, "Sheet k of N"; a page
    without claimed flags doesn't print. Each sheet is fitted on its own, in its own page's scale and rotation (313 38
    Street's sheet 2 is turned the other way and uses its page's `rotation_deg`). Every sheet repeats the whole form:
    the header, box 1 (page 1's title block), boxes 3 to 5, the KEY, the perimeter from page 1's title block (CC from
    that sheet's own curb segments) and the certification. **Box 2** (the whole property's Construction Measure) is
    filled on sheet 1 only; later sheets leave it blank. **Calculations:** each sheet lists its own flags; the last of
    several adds `All Sheets ... Assessable` per thickness (`total_lines`), which equals box 2. **Numbering** runs on
    across sheets (`number_items`, page after page). Per-stretch quantities ("B - C 45.00 LF") are deferred.
  - **Match lines** (`icid.pir_match_lines`, migration 046; `api/v1/match_lines.py`, `api/queries/match_lines.py`,
    `api/schemas/match_line.py`): a lettered line (A..Z, AA..ZZ) drawn on one page as two endpoints in its native
    pixels. Two pages pair by carrying the same label; a label is live once per page and on at most two pages of a PIR
    (409 `Match line "A" is already on this page` / `... on pages 1 and 2`; the statements check it too). The report
    places both endpoints with the sheet's transform and draws the dashed line, a circled letter at each end and the
    caption `MATCH LINE "A" — SEE SHEET 2 OF 2`, or `— SEE PIR PAGE 3` when the partner page doesn't print, or the label
    alone (`match_line_caption`). Routes: `GET /v1/pirs/{id}/match-lines`, `GET`/`POST /v1/pir-pages/{id}/match-lines`,
    `GET`/`PATCH`/`DELETE /v1/match-lines/{id}`, for the project's members as features are (`match_line_member`; demo
    users read). Soft delete only; replacing the scan retires them. There is no drawing tool yet (R1h.1b): lines come
    from the Smart PIR import or these routes. Progress, the Assessment Summary, the export, quantities and IDR claims
    read flags one by one and don't see pages or lines, so they are unchanged.
  - **Which IDR:** the one in `?idr_id=`, or by default the most recently approved (`re_signed_at`) that claimed a flag
    on the property; it must be approved, not deleted, and have claimed a flag there (404 otherwise). The IDR route
    (`require_project_role` on all three roles, and the demo fence) zips one PDF per property it claimed flags on.
  - **What prints.** Above the title, the property's class from its `property_type` (`property_class`: `1-3 FAMILY`,
    `4+ FAMILY`, `EXPEDITED`; `CITY PROPERTY` for a property without one, a ped ramp). Page 1's title block of the
    live PIR (contract and violation numbers, the streets, the three
    dates, blank when NULL; the status boxes), the property's address, block and lot, and box 1's Survey (the PIR's
    initials and sign date), Computerized by (`digitized_by`, `digitized_at`) and Checked by (the IDR's RE,
    `re_signed_at`). Box 4 ticks `work_already_done`, `distinctive_material`, `landmark`, `subway`, `tree`,
    `vault`, `scaffolding`, `other_perm_obstruction`, `other_temp_obstruction` and drops any other reason. Dates
    print ISO, a time as its day in `FORM_TIMEZONE`.
  - **Construction Measure** (`compute_measure`): per flag the IDR claimed there, Total SF = `computed_quantity`;
    Assessable = 0 for a flag surveyed non-assessable, else its quantity (override, or net area: billing's);
    Non-Assessable = the rest, never below 0. Rows by `surveyed_thickness`; cost = assessable × `cost_per_sf_4in` (4"
    and 7" assessed as 4") or `cost_per_sf_7in`; no rate, no cost. Kept as `Decimal`, rounded for display. The
    header's Estimated Sq.Ft is the assessable total, as DOT's sheets print it (R1h.4; the gross total before).
  - **Sketch** (`construction_sketch.py`): outlines exactly as the frontend's `lib/flagGeometry.js`, the page's
    rotation applied, fitted with 4% padding (R1h.4; 10% before) to the left of the band, room left round it. 422 when
    a sheet would print below `MIN_POINTS_PER_FOOT` (4) (`too_large_message`: split it with a match line onto another
    page) or its page has no scale (`Set the scale of PIR page 2 first` on a report of several sheets). North arrow from `pirs.north_bearing`, read on the upright page (not turned again by its
    rotation); none when unset.
  - **DOT parity (R1h.3).** Numbers (`number_items`): every flag 1..n and A1..An for a flag surveyed non-assessable
    (which keeps its number too) and an area on an assessable flag, both in reading order (`reading_order`: rows top
    to bottom, a row taking the shapes whose middle lies within its topmost shape's height, then left to right).
    Fills by `material`: 4" stippled, 7" and 7" as 4" in diagonal lines, a non-assessable flag or area tinted blue
    and cross-hatched (a non-assessable flag crossed through too). Labels (`4" Conc.`, `7" Conc.`, the reason in words,
    `Non-Ass.` for none or other) only where they fit and clear every number and earlier label, areas' first. The
    perimeter from page 1's title block, no migration (041 was skipped): PL along the top, `TAX MAP` at scale between
    the PLs when `tax_map_width_ft` is set, `CC=` (the curb along the frontage) along the bottom: the summed length of the
    page's curb segments of every kind (`CC_SEGMENT_TYPES`: concrete, depressed, steel-faced and granite, not `other`;
    DOT totals them, HWS2014Q's CC= 34.25 being 17.16 depressed + 17.09 concrete), under the stretch they span (R1h.4), else `cc_curb_to_curb_ft` at scale, the frontage
    street under it and the cross streets up the sides; the fit makes room for either width.
  - **Calculations panel** (the band's right third; `calc_groups` in `construction_measure.py`, `calc_lines` in
    `construction_report.py`): per thickness with flags, each flag's Length × Width = Sub-Total from its stored
    dimensions (`shape_terms`: a trapezoid `0.5(a+b)` × w, a triangle `0.5(a)` × b, a polygon or an arc the Sub-Total
    alone), the non-assessable pieces, then Total − Non-Assessable = Assessable. Its totals are box 2's; when the pieces
    don't add up to it (an override, a subtracted feature, an area larger than its flag) an `Adjustment to box 2` row
    carries the difference. More lines than fit at 6 pt on a sheet is a 422 (`CALC_TOO_LONG_MESSAGE`, naming the PIR page on a report of
    several sheets; calc-only overflow sheets are deferred). The 4" (with 7" assessed
    as 4") and the 7" tables stand side by side when both have flags (`report_layout`: the panel `CALC_WIDTH_TWO`
    wide, the sketch narrower), else one column `CALC_WIDTH` wide.
  - **Sketch polish (R1h.4).** Flags that abut (an edge along another's, within 0.1 ft) and share thickness and
    assessment are one slab (`slab_groups` in `construction_slabs.py`): drawn with one border and thin grey lines where
    its flags meet (`edge_runs`), labelled once; a group whose flags overlap by more than 0.5 SF is drawn flag by flag.
    Each slab is dimensioned (`_straps`): its width on a horizontal strap above (else below) and its height on a
    vertical one left (else right), wherever it clears every piece, label and dimension; TAX MAP and CC stand a lane
    (`STRAP_LANE`) off the drawing so a strap fits between. A driveway (an area, or a flag surveyed non-assessable,
    for `driveway`) is its own material: dense 30-degree stripes, labelled `17.16' DW` (its width across the sheet). A
    `curb_concrete_depressed` segment (a curb cut) is ticked at its ends and its length printed. No schema change:
    there is no driveway feature or curb-cut segment type; these are the data that already model them.
  - **Certification:** the IDR's own inspector signature copy (`inspector_signature_path`, fetched through
    `fetch_signature_file` in `api/services/signatures.py`, as the IDR export does; best-effort, blank when it can't
    be), its reporter as Inspected By, `idr_number`, and their initials with the day the IDR was approved. The
    signature is fitted to its own Print Sign Date cell at the strip's right end (`signature_box`, R1h.4), at most
    `SIGNATURE_MAX_WIDTH` (94 pt, about 1.3 in, DOT's sign-date box) wide, its proportions kept, centred. The PIR's `as_built_signed_*` are never set, so they aren't used.
  - Downloads carry `Content-Disposition` (`{address}_construction_report.pdf`, `IDR_{number}_construction_reports.zip`),
    which CORS exposes (`expose_headers`). ~40–60 KB a page (`pageCompression`).
- **Assessment Summary** (R1i; `api/v1/assessment_summary.py`, `api/queries/assessment_summary.py`,
  `api/services/assessment_summary.py`): a project's monthly .xlsx for DOT, built with openpyxl in memory.
  - **The same measure as the Construction Report,** flag by flag (`flag_areas`), over every flag a project's approved
    IDRs claimed, so a property's row adds up to its PDFs. 7" assessed as 4" counts in the 4" columns at the 4" rate,
    with `N SF built at 7" billed at 4"` in Remarks; a rate not set leaves its amounts blank, says so in Remarks and
    reads `—` in the tab's header.
  - **The month** is the property's `date_constructed` (page 1's title block of its live PIR, as the report prints it),
    else the day its IDR was approved (`re_signed_at`, in `FORM_TIMEZONE`); the tab's footer counts the rows that went
    by approval. A property approved twice in a month is one row.
  - **The workbook (the accumulative report):** a tab per month with rows (named `Nov 2026`, oldest first; Slice C
    renamed them from `YYYY-MM`), rows by address any case, the property's
    `community_board` in a CB column after its address (Slice A), a bold grey totals row, panes frozen under the column titles; SF `#,##0.00` (none reads `—`), money `$#,##0.00`; values written whole.
    Nothing (or nothing in `from`..`to`) is one `No activity` tab, a 200. The file name carries today in New York.
  - **A row per site** (Slice C, `summary_sites`): the As-Builts & Assessments page's `GET .../assessment-summary`
    (JSON) and its `tailored.xlsx` sum each property's flags over a range of **days** (`from`, `to`, `YYYY-MM-DD`,
    inclusive, read by `day_range` in `api/v1/_shared.py`; a flag's day is the one its month comes from), one row per
    site whatever months they span. Each row carries the property's kind, block, lot, `violation_no` and
    `community_board` for the page's search; figures are rounded to the cent (`summary_payload`), the totals summed
    before rounding. `tailored.xlsx` takes `property_id` (repeated) for the rows the page shows, none meaning every
    site, and writes them on one tab named for the range (`2026-10-01 to 2026-10-31`, `From …`, `Through …`, `All
    dates`; `range_label`), with the same columns and totals row as a month's; no rows is the tab with `No constructed
    sites in this view.` The monthly workbook and the per-site view share one measure (`_rows_by`).
- **Progress** (R1g; `api/v1/progress.py`, `api/services/progress_rollup.py`, `api/queries/progress.py`,
  `api/schemas/progress.py`): how much of the sidewalk work the PIR flags describe is approved, in flight or not yet
  claimed, for a project, a task order or a property. No table of its own: two reads, `list_flag_pay_items`
  (`api/queries/contract_items.py`) and `list_flag_progress`, and `compute_progress` builds the rest.
  - **The states.** A flag is **approved** once an approved IDR claimed it (`claimed_by_idr_id`), whatever has happened
    to it since, a soft delete included, as `icid.quantities` keeps it; **in flight** while unclaimed and attributed
    to any open IDR of the project (a draft, or submitted, or in either stage; `IN_FLIGHT_FLAG_IDS` in
    `api/queries/idr_flags.py`, R1f's scope rule, so an IDR on another task order that includes the flag counts, and a
    flag in two drafts counts once); **unclaimed** otherwise. Only live flags are in flight or unclaimed. A live flag
    that comes to nothing in a pay item's unit (none mapped, or a unit `FLAG_AMOUNT` can't measure) is **unmapped**: in
    no column, counted per property in `unmapped_by_property`.
  - **Approved is summed from the claimed flags, not from `icid.quantities`.** The `from_flags` rows carry no flag or
    property, so they can't be split by task order or property. A claimed flag can still be edited, so Progress shows
    what the claimed flags say now and the Quantities route what was signed; the two can differ.
  - **Which pay items:** those of the contract items in a unit a flag measures (`FLAG_UNITS`: SF, SY, CY, EA) that a
    flag in the rollup's scope is on, in any state; each property's rollup on a task order lists its own. With
    `include_empty=true`, every such contract item, at zero or not (SE384 has 265). By budget code then item number;
    the unit is given in that short form.
  - **Figures.** Quantities in the pay item's unit (`FLAG_AMOUNT`), unrounded. Value is quantity times the contract
    item's `bid_unit_price`, to the cent. `approved_pct` is approved over the three states together, to 1 decimal
    place: per pay item by quantity, overall by value (so units combine); null when the total is 0. The denominator is
    the live flags, not `bid_quantity`.
  - **Scope.** The project route reads every property of the project; `task_order_id` narrows to a live task order's
    live bundled properties, `property_id` to one live property, both to the property if it is in the task order (else
    an empty rollup). The task order route reads its bundle as it stands, a deleted task order's included, and adds
    each property's rollup; `sites_started` counts those with an approved flag in a pay item.
  - **Who:** `project_member` and `task_order_member`; demo users read their own project's.
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

- `spec_items` has queries and a schema but no endpoint, and nothing reads the table since migration 026.
  `api/schemas/` also defines models for
  clients, form templates and the join tables — those schemas run
  ahead of the endpoints and may not match `schema.sql` exactly. Verify against `schema.sql`
  before building on them.
- `api/v1/`, `api/db/`, `api/core/` and `api/schemas/` have no `__init__.py`; only
  `api/queries/` does. Imports work regardless, but don't take the inconsistency as intent.
- **R1d upload routes unused since R1f; remove in a cleanup slice:** the DEPRECATED (R1e) routes, bodies and keys
  `docs/endpoints.md` names, and a PIR's `source_image_url` / `source_image_scale`. The frontend calls none of them.
- **`pir_annotations.cc_curb_to_curb_ft` is misnamed.** On DOT's sheets CC is the curb along the frontage,
  not the street's curb-to-curb width. The Construction Report prints CC from the page's curb segments (every kind) and
  uses the column only as a manual figure when there are none (R1h.4). Rename it in a cosmetic slice (the column,
  the title-block route's field and the editor's label).
- CORS is `allow_origins=["*"]`. Tighten before production.
- `POST /v1/auth/login` has no rate limiting, and neither has `POST /v1/users/me/password`, which checks a
  password too. A password change or reset leaves the tokens already issued valid until they expire. Both are
  Phase 2.
- Demo mode leaves files behind: attachment files of demo users purged by the daily cleanup (a SQL function can't
  reach Storage), and every demo export in `idr-exports`. Nothing points at them afterwards; a bucket sweep would
  have to find them. `POST /v1/auth/demo` is also unthrottled apart from the 200-user ceiling.
- No multi-statement transactions: `run_query` runs one statement per connection. Work that must be atomic is
  written as one statement (data-modifying CTEs), as the demo user's insert and delete are.
