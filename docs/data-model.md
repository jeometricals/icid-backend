# ICID data model

Developer reference for the `icid` schema as of migration 033 (Slice R1e). The diagram below is the ER
diagram; it was last checked against `schema.sql` in Slice R1e.
`schema.sql` is the authoritative DDL; this doc explains it. If the two ever disagree,
`schema.sql` wins and this file needs fixing.

All tables live in the `icid` schema. Every table except the three join tables has
`created_at` / `updated_at` (`TIMESTAMPTZ NOT NULL DEFAULT now()`); they're left out of the
column lists below.

## The big picture

```
clients ──< users ──< project_users >── projects ──< project_clients >── clients
                │      (one row per role)    │
                │ reporter_uuid              │ project_id
                └───────────< idrs >─────────┘
                               │
                               │ idr_id, ON DELETE CASCADE on all four
        ┌──────────────┬───────┴────────┬──────────────────┐
        ▼              ▼                ▼                  ▼
   idr_reports     idr_audit     idr_field_edits      quantities
     ▲  │  │                            ▲
     └──┘  │                            │ report_id (ON DELETE CASCADE;
  parent_  ├────────────────────────────┘  NULL for an edit of the header)
report_id  │ report_id (ON DELETE CASCADE)
(addendum  ▼
→ parent)  report_attachments

projects ──< contract_items ··· spec_items     project_id ON DELETE CASCADE; spec_item_id optional (legacy catalog)
projects ──< quantities                        project_id, a copy of the IDR's
projects ──< properties                        project_id; soft-deleted; a sidewalk (by borough, block and lot,
                                               unique per project among live ones) or a ped ramp (by intersection)
projects ──< task_orders ──< task_order_properties >── properties
                                               both FKs ON DELETE CASCADE; both sides share a project
properties ──< pirs                            property_id, no cascade; one live PIR per property
pirs ──< pir_pages ──< flags                   pir_id ON DELETE CASCADE; flags.pir_page_id no action (live flags only)
pirs ──< flags ··· contract_items              pir_id, no cascade; pay_item_id optional, ON DELETE SET NULL
flags ──< non_assessable_areas                 flag_id, no action (soft-deleted with their flag)
pir_pages ──< features ··· flags               pir_page_id, no action; flag_id optional (a 'subtract' feature's flag)
pir_pages ──< segments                         pir_page_id, no action
pir_pages ──1 pir_annotations                  pir_page_id is the primary key, no action
features, segments ··· contract_items          pay_item_id optional, ON DELETE SET NULL

users is also referenced by (none cascades):
    idrs.stage1_reviewer_uuid, idrs.re_reviewer_uuid, idrs.deleted_by
    idr_audit.actor_uuid, idr_field_edits.editor_uuid, report_attachments.uploaded_by, properties.created_by,
    task_orders.created_by, task_order_properties.added_by, pirs.digitized_by, flags.created_by,
    non_assessable_areas.created_by, features.created_by, segments.created_by
    users.deleted_by, properties.deleted_by, task_orders.deleted_by, pirs.deleted_by,
    pirs.as_built_signed_by, flags.deleted_by, pir_pages.deleted_by, non_assessable_areas.deleted_by,
    features.deleted_by, segments.deleted_by (ON DELETE SET NULL)
```

`──<` reads "has many". Deleting an IDR's row takes everything drawn under it; nothing cascades from a user, a
client or a project except a project's `contract_items`. `quantities.reporter_uuid` is a copy with no foreign key.
`form_templates` stands alone and is unused.

- A **client** is an organisation. Every **user** belongs to one client.
- **Projects** are construction contracts. Users are assigned to projects through
  **project_users**; extra organisations on a project go through **project_clients**.
- An **IDR** (Inspector Daily Diary) is one inspector's diary for one project on one day. It
  holds the header fields shared by every page of that day's paperwork.
- **idr_reports** are the typed reports filed inside an IDR: a General, a Sewer report,
  addenda, and so on. Each one's form content is a JSON blob in `report_data`.
- **report_attachments** are files (photos, PDFs) attached to a report. The row is metadata;
  the bytes live in Supabase Storage.
- **idr_audit** logs what is done to an IDR from submission on; **idr_field_edits** holds what reviewers
  changed on it. **quantities** holds an approved IDR's pay-item quantities, one row each.
- **properties** are where a project's work is done: a sidewalk (a tax lot, named by its borough, block and lot) or a pedestrian
  ramp (a corner of an intersection). Project members add and edit them; an admin soft-deletes and restores
  them. See [properties](#properties).
- **task_orders** bundle a project's properties (**task_order_properties**, the join); a property can be in
  several. See [task_orders](#task_orders).
- A **PIR** is a property's digitized DOT survey sheet; a property has at most one that isn't deleted. See
  [pirs](#pirs). Its scan is a set of **pages** ([pir_pages](#pir_pages)), and its **flags** are the marked
  pieces of sidewalk drawn on them, each with a shape and the area it comes to. See [flags](#flags). A flag's
  **non-assessable areas** are the parts of it not assessed; a page's **features** (obstructions, some taken off a
  flag's area) and **segments** (curb runs) are drawn on it too, and its **annotations** are its title block. See
  [non_assessable_areas](#non_assessable_areas), [features](#features), [segments](#segments) and
  [pir_annotations](#pir_annotations). A sketch tool's file of all of these comes in through one import
  (`POST /v1/pirs/{id}/import`).
- An IDR is **soft-deleted** by an admin (`status = 'deleted'`, `deleted_at`, `deleted_by`): the row and
  everything under it stay, and it can be brought back. Only a soft-deleted IDR can then be deleted for good,
  which removes the row and, by the cascades above, everything under it. See [idrs](#idrs).
- **contract_items** is one project's Schedule of Bid Items: its pay items, each with its own
  number, description and unit, under which budget codes, at what quantity and price.
  **spec_items** is the NYCDOT pay-item catalog the first project's items were seeded from;
  nothing reads it any more. See [Pay items](#pay-items).

## Tables

### clients

Organisations: agencies, consultants, contractors.

| Column | Type | Notes |
|---|---|---|
| `client_id` | TEXT PK | Human-assigned code |
| `client_username` | TEXT NOT NULL | |
| `client_name` | TEXT NOT NULL | |
| `client_email` | TEXT | |
| `client_phone` | TEXT | |
| `client_role` | TEXT | Free text |

No endpoint reads clients directly yet.

### users

People who sign in: inspectors, CCLs, engineers, admins. An admin creates a user and edits one's names, email,
admin role and demo flag (`/v1/admin/users`); a password is changed by its user or reset by an admin.

| Column | Type | Notes |
|---|---|---|
| `uuid` | UUID PK | `uuid_generate_v4()`. The API calls it `user_id`. |
| `email` | TEXT NOT NULL | Stored as entered; unique without regard to case among users who aren't deleted (`idx_users_email_lower` on `lower(email)`, `WHERE deleted_at IS NULL`), which is also how sign-in looks it up. A deleted user keeps their email, and a new account can take it |
| `first_name`, `last_name` | TEXT | |
| `phone_number` | TEXT | |
| `client_id` | TEXT NOT NULL | FK → `clients.client_id` (the user's employer) |
| `password_hash` | TEXT | bcrypt. NULL: the user can't sign in with a password. Written when an admin creates the user, by `POST /v1/users/me/password` and by `POST /v1/admin/users/{id}/password`; never returned |
| `role` | TEXT | `admin`, or NULL for everyone else; the admin API sets it from `is_admin`. What a user may do on a project is `project_users.role` |
| `is_demo` | BOOLEAN NOT NULL | Default `false`. Throwaway demo accounts; `idx_users_is_demo` (partial) serves their cleanup. An admin can turn it off for a user (demo to real), never on |
| `signature_path` | TEXT | The user's current signature: its object path in the `signatures` bucket. NULL: none yet. |
| `signature_type` | TEXT | `drawn` or `uploaded`; CHECK `chk_users_signature_type`. NULL with no signature. |
| `signature_set_at` | TIMESTAMPTZ | When the current signature was set |
| `deleted_at` | TIMESTAMPTZ | Soft delete: when an admin deleted the user. NULL for a user who isn't deleted. Migration 028 |
| `deleted_by` | UUID | The admin who did; FK → `users.uuid`, **ON DELETE SET NULL** |

**Soft delete, un-delete and permanent delete** (all admin only, under `/v1/admin/users/{id}`):
- `POST .../delete` sets `deleted_at` and `deleted_by`, and nothing else. The user can't sign in, and a token
  they hold is refused. Every row that names them stays, and still shows their name. There is no `deleted`
  status on a user: deleted is `deleted_at IS NOT NULL`. `GET /v1/users/` leaves deleted users out;
  `GET /v1/admin/users` does unless `include_deleted=true`.
- `POST .../undelete` clears the two columns. It is refused (409) while another user who isn't deleted has the
  same email.
- `DELETE .../permanent` deletes the row of a user who is soft-deleted, and only when no row names them: every
  foreign key to `users.uuid` (see the diagram) is `NO ACTION` and refuses the delete, except
  the `deleted_by` of users, properties, task orders, PIRs, flags, PIR pages, non-assessable areas, features and
  segments and `pirs.as_built_signed_by`, which (but for `users.deleted_by`) are counted all the same. So only a user
  who never reported, reviewed, edited, uploaded, held a role, created or deleted a property, a task order, a flag, a
  non-assessable area, a feature or a segment, bundled a property, or digitized, signed or deleted a PIR, can go;
  anyone with history stays soft-deleted. The user's signature file is then removed from Storage, best-effort.

A demo user is made by `POST /v1/auth/demo` (`demo-<uuid>@icid.local`, first name `Demo`, client `C00001`, no
password, no role) together with one `project_users` row on `DEMO01` (`user_role` `Demo`). Signing out deletes the
user with their attachments, reports, IDRs and assignments; `icid.cleanup_abandoned_demo_users()` deletes the ones
older than 24 hours who never signed out.

### projects

Construction contracts. An admin creates one through `POST /v1/admin/projects`; its status and its clients are
set in SQL.

| Column | Type | Notes |
|---|---|---|
| `project_id` | TEXT PK | The contract number, e.g. `HWS0023`. Shown as "Contract No". |
| `project_name` | TEXT NOT NULL | |
| `project_description` | TEXT | |
| `registration_code` | TEXT | Shown as "Reg. No" |
| `borough` | TEXT | |
| `status` | TEXT | Free text, e.g. `active` |
| `cost_per_sf_4in`, `cost_per_sf_7in` | NUMERIC(10,4) | The DOT assessment rates per square foot of 4" and 7" sidewalk; ≥ 0 (`chk_projects_assessment_rates`); NULL until an admin sets them (`PATCH /v1/admin/projects/{id}/assessment-rates`). Migration 034 |

There is no contractor column. The frontend's old mock data had one, but it was never in the
database, and R5 removed it from the General page.

### project_users

Assigns users to projects, one row per role. `GET /v1/projects/` reads the signed-in user's projects through it.

| Column | Type | Notes |
|---|---|---|
| `project_id` | TEXT | PK part; FK → `projects.project_id` |
| `user_uuid` | UUID | PK part; FK → `users.uuid` |
| `user_role` | TEXT | A free-text label for the user on this project, e.g. `Inspector`, `CCL`. Shown by the projects list; not checked by the API |
| `assigned_at` | TIMESTAMPTZ NOT NULL | Default `now()` |
| `role` | TEXT NOT NULL | PK part. `inspector` (default), `oe` or `re`; CHECK `chk_project_users_role`. What the API checks |

Primary key: `(project_id, user_uuid, role)`. Roles are additive: a user holding several roles on a
project has one row per role. Admin is not a project role; it is `users.role`. An admin grants and revokes
roles through `POST /v1/projects/{project_id}/roles`; a row made that way has no `user_role`.

### project_clients

Links additional organisations to a project. No endpoint writes it (rows are made in SQL).

| Column | Type | Notes |
|---|---|---|
| `project_id` | TEXT | PK part; FK → `projects.project_id` |
| `client_id` | TEXT | PK part; FK → `clients.client_id` |
| `client_role` | TEXT | That organisation's role on the project. The IDR export prints the client whose role is `Contractor` as the contractor |

Read only by the export (`get_project_contractor_name`).

### idrs

One row per inspector, per project, per day. Holds the shared header; the reports hang off it.

| Column | Type | Notes |
|---|---|---|
| `idr_id` | UUID PK | `uuid_generate_v4()` |
| `project_id` | TEXT NOT NULL | FK → `projects.project_id` |
| `reporter_uuid` | UUID NOT NULL | FK → `users.uuid` (the inspector) |
| `report_date` | DATE NOT NULL | |
| `work_start_time`, `work_end_time` | TIME | Contractor's work activity |
| `inspector_start_time`, `inspector_end_time` | TIME | Inspector's hours on site |
| `temp_low`, `temp_high` | NUMERIC(4,1) | |
| `weather_am`, `weather_pm` | TEXT | |
| `total_pages` | INTEGER | NULL while draft; set on submit to the number of reports |
| `has_dismissed_auto_general` | BOOLEAN NOT NULL | Default `false`. See [Auto-generated General](#auto-generated-general) |
| `status` | TEXT NOT NULL | `draft` (default), `submitted`, `stage1_review`, `stage2_review`, `approved`, `returned` or `deleted`; CHECK `chk_idrs_status`. `returned` is unused (a returned IDR is a `draft` with `return_reason` set); `deleted` is a soft delete by an admin |
| `submitted_at` | TIMESTAMPTZ | NULL until submitted |
| `inspector_signature_path` | TEXT | The signature stamped at submit (object path in the `signatures` bucket); not changed afterwards. NULL on drafts and on IDRs submitted before signatures. |
| `inspector_signed_at` | TIMESTAMPTZ | When that signature was stamped |
| `idr_number` | TEXT | Free text, set when a reviewer picks the IDR up at Stage 1 |
| `stage1_reviewer_uuid` | UUID | FK → `users.uuid`. Who picked it up at Stage 1 |
| `stage1_accepted_at` | TIMESTAMPTZ | When Stage 1 was last accepted: set by accept-stage1, cleared by a return to the inspector. Starts the Stage 1 round for pay-item attestations; NULL (accepted before migration 022) means not known |
| `stage1_reviewed_at` | TIMESTAMPTZ | |
| `stage2_accepted_at` | TIMESTAMPTZ | When the RE last accepted: set by accept-stage2; cleared by accept-stage1, any return and an admin unlock. Starts the Stage 2 round; NULL means not known |
| `re_reviewer_uuid` | UUID | FK → `users.uuid`. The RE who picked it up at Stage 2; set again at final approval to whoever approved, so it always names the signer. Cleared by a return from Stage 2 and by an admin unlock, so an RE accepts again |
| `re_signature_path`, `re_signed_at` | TEXT, TIMESTAMPTZ | The approver's signature stamped at final approval, like the inspector's pair. The export prints it while the IDR is `approved` |
| `return_reason` | TEXT | The latest return comment |
| `returned_from` | TEXT | `stage1` or `stage2`; CHECK `chk_idrs_returned_from` |
| `deleted_at`, `deleted_by` | TIMESTAMPTZ, UUID | Soft delete: when and by whom (FK → `users.uuid`). The row is kept |
| `task_order_id` | UUID | Its default flag scope (migration 037): FK → `task_orders.id`, no action; a live task order of its project (the API). NULL: it covers no flag but those included |

The review columns (from `idr_number` down) came with migration 017.

**Soft delete, un-delete and permanent delete** (all admin only):
- `POST /v1/idrs/{id}/admin/delete` sets `status = 'deleted'`, `deleted_at` and `deleted_by`. The row and
  everything under it stay, except its `quantities` rows, which are removed. Its day and its IDR number
  become free (the two unique indexes below are partial). The status it had is not kept on the row: it is the
  `from_status` of the `idr_audit` row (`admin_delete`) the same statement writes.
- `POST /v1/idrs/{id}/admin/undelete` clears `deleted_at` and `deleted_by` and puts back that status (the
  latest `admin_delete` row's `from_status`; `draft` when there is none). It is refused (409) while another IDR
  holds the day or the number. An IDR that comes back `approved` gets its `quantities` rows written again.
- `DELETE /v1/idrs/{id}/admin/permanent` deletes the row of an IDR that is soft-deleted, and only of one that
  is. The cascades take its reports, attachments, field edits, audit rows and quantities; the backend then
  removes its files from the three Storage buckets, best-effort. Nothing of it is left in the database, its
  audit rows included, so who removed it is in the application log only.

Constraints and indexes:
- `uq_idrs_project_reporter_date UNIQUE (project_id, reporter_uuid, report_date) WHERE deleted_at IS NULL`:
  one IDR per inspector per project per day among IDRs that aren't deleted. A partial unique index
  since migration 017 (a constraint before), so a soft delete frees the day. `POST /v1/idrs/` returns
  409 with `existing_idr_id` when it would be violated; its INSERT uses a bare `ON CONFLICT DO NOTHING`,
  since a column list alone no longer matches the index.
- `uq_idrs_project_number UNIQUE (project_id, idr_number) WHERE idr_number IS NOT NULL AND deleted_at IS NULL`:
  an IDR number is used once per project among IDRs that aren't deleted. A soft delete frees it.
- `idx_idrs_project_status (project_id, status)` and `idx_idrs_status (status)`, both partial
  (`WHERE deleted_at IS NULL`), and `idx_idrs_reporter (reporter_uuid)`.

Lifecycle: an IDR is created as a `draft`, and its header and reports can be edited freely.
`POST /v1/idrs/{id}/submit` first copies the submitting user's signature to the IDR's own path, then
locks the IDR in one statement. It sets `status = 'submitted'`, `submitted_at`, `total_pages`,
`inspector_signature_path` and `inspector_signed_at`, and numbers every report's `page_number`. After that the API
refuses edits (409).

Review: a submitted IDR is picked up at Stage 1 by an OE or RE (`accept-stage1`, which gives it its
`idr_number`), passed to Stage 2 (`approve-stage1`), picked up by an RE (`accept-stage2`) and approved
(`approve-stage2`, which stamps the RE's signature). `return` sends it back to its inspector as a `draft`, or
from Stage 2 to the OE as `stage1_review`, with `return_reason` and `returned_from`; resubmitting and
approve-stage1 clear them. The number is kept through returns. Every one of these moves is a single statement
that also writes a row to `idr_audit`.

### idr_audit

One row per thing done to an IDR from submission on. Written by the statement that changes the IDR. Un-delete
reads it, for the status an IDR had when it was deleted and the flags its delete released; nothing else does.

| Column | Type | Notes |
|---|---|---|
| `audit_id` | UUID PK | `gen_random_uuid()` |
| `idr_id` | UUID NOT NULL | FK → `idrs.idr_id`, **ON DELETE CASCADE** |
| `actor_uuid` | UUID NOT NULL | FK → `users.uuid`. Who did it |
| `action` | TEXT NOT NULL | `submit`, `accept_stage1`, `approve_stage1`, `accept_stage2`, `approve_stage2`, `return_to_inspector`, `return_to_oe`, `admin_unlock`, `admin_delete`, `admin_undelete`, and for reviewer edits `field_edit`, `pay_item_revise`, `pay_item_add`, `pay_item_approve`, `truck_add`. No CHECK |
| `from_status`, `to_status` | TEXT | The IDR's status before and after; nullable for actions that aren't a status change. An `admin_delete` row's `from_status` is the only record of what a deleted IDR was |
| `note` | TEXT | The return comment; for a reviewer edit, `{"edit_id", "field_path"}` as JSON text; for an `admin_delete` (since migration 037), `{"released_flag_ids": [...]}`, the flags whose claims the delete released |
| `created_at` | TIMESTAMPTZ NOT NULL | Default `now()` |

Index: `idx_idr_audit_idr_created (idr_id, created_at DESC)`.

**Where un-delete gets its re-claim list.** An admin delete releases every flag the IDR claimed (R1f) and records
their ids in its `admin_delete` row's `note`. Un-delete reads the latest such row: its `from_status` is the status
the IDR comes back with, and, when that is `approved`, the note's `released_flag_ids` are the flags it claims back
in the same statement (with their `from_flags` quantity rows). If another IDR has claimed any of them since, the
un-delete is refused (409, naming that IDR) and nothing changes. A delete from before 037, or of an IDR that held no
claims, records an empty list.

### idr_field_edits

What reviewers changed on an IDR in review, one row per edit. Rows are only ever added. An edit is applied when it
is logged: the same statement writes `new_value` into the IDR, so the IDR holds the current value and this table
holds the history. The value before a field's first edit is that row's `old_value`.

| Column | Type | Notes |
|---|---|---|
| `edit_id` | UUID PK | `gen_random_uuid()` |
| `idr_id` | UUID NOT NULL | FK → `idrs.idr_id`, **ON DELETE CASCADE** |
| `report_id` | UUID | FK → `idr_reports.report_id`, **ON DELETE CASCADE**. NULL for a header field |
| `field_path` | TEXT NOT NULL | Which field; see below |
| `edit_type` | TEXT NOT NULL | `field_change`, `pay_item_revision`, `pay_item_add`, `pay_item_approve` or `truck_add`; CHECK `chk_idr_field_edits_type` |
| `old_value` | JSONB | What was there. NULL only for `pay_item_add` and `truck_add` (CHECK `chk_idr_field_edits_old_value`); a field that held JSON `null` stores JSON `null` |
| `new_value` | JSONB NOT NULL | What the reviewer put; for `pay_item_add`, the whole item; for `truck_add`, the whole truck |
| `editor_uuid` | UUID NOT NULL | FK → `users.uuid` |
| `editor_stage` | TEXT NOT NULL | `stage1` or `stage2`, the stage the IDR was in; CHECK `chk_idr_field_edits_stage` |
| `edited_at` | TIMESTAMPTZ NOT NULL | Default `now()` |

Indexes: `idx_idr_field_edits_idr (idr_id, edited_at)` and `idx_idr_field_edits_field (report_id, field_path)`.

The IDR export prints these rows as redlines (`api/services/export_redlines.py`): a field's first `old_value`
struck, then each `new_value` in blue with the editor's initials; a `pay_item_revision` as a row of its own under
the item; a `pay_item_add` as a blue row; a `pay_item_approve` as initials in Quantity Chk, when its
`new_value` is still the item's quantity; a `truck_add` as a blue row of the Conc Mix trucks table. Nothing is stored for this: the export reads the rows each time.

`field_path`, with the keys as `report_data` stores them:

| Form | Example | Means |
|---|---|---|
| `header.<column>` | `header.work_start_time` | A header column of `idrs` (`report_id` NULL) |
| `<key>.<key>…` | `description`, `workforce.foremen`, `safetyChecks.plasticBarrels` | A field of the report |
| `<list>[<n>].<key>` | `additionalWorkforce[0].count` | A row of a list, by position from 0 |
| `payItems[<id>].<key>` | `payItems[3f2a…].payQuantity` | A pay item's field, by the item's `id`; `payQuantity` is a `pay_item_revision` |
| `cylinders[<id>].<key>` | `cylinders[0c3d…].slump` | A cylinder's field on a Concrete Cylinder Data report, by the cylinder's `id` (given at submit); never by position |
| `payItems[<id>]` | `payItems[3f2a…]` | A pay item a reviewer added (`pay_item_add`), or approved as it stands (`pay_item_approve`: `old_value` and `new_value` are both the quantity approved, and nothing in the report changes) |
| `trucks[<id>]` | `trucks[9c1d…]` | A truck a reviewer added to a Concrete Truck & Mix Info report (`truck_add`; `new_value` is the whole truck). Every truck carries an `id` (migration 023, and submit), used only for this: a truck's own fields are named by position, `trucks[0].slump` |

The routes that write these rows are `PATCH /v1/idrs/{id}/field`, `POST /v1/idrs/{id}/pay-items/{item id}/revise`
and `POST /v1/idrs/{id}/pay-items/add`; `GET /v1/idrs/{id}` returns them as `field_edits`.

Each edit also writes an `idr_audit` row (`field_edit`, `pay_item_revise`, `pay_item_add` or `pay_item_approve`) whose `note` is
`{"edit_id", "field_path"}`.

### idr_reports

The typed reports inside an IDR. Each entry of `report_data.payItems` carries an `id` (a uuid as text), given
by migration 020 to the items that existed and by submit to any item without one.

| Column | Type | Notes |
|---|---|---|
| `report_id` | UUID PK | `uuid_generate_v4()` |
| `idr_id` | UUID NOT NULL | FK → `idrs.idr_id`, **ON DELETE CASCADE** |
| `report_type` | TEXT NOT NULL | One of the [report type codes](#report-types). The API enforces the enum; the column doesn't. |
| `is_addendum` | BOOLEAN NOT NULL | Default `false` |
| `parent_report_id` | UUID | FK → `idr_reports.report_id`, **ON DELETE CASCADE**. The report an addendum belongs to. |
| `page_number` | INTEGER | NULL while draft; set on submit |
| `report_data` | JSONB NOT NULL | Default `{}`. The form content. Its shape depends on `report_type`, and the API accepts any JSON object but a Daily Site Patrol's (see [Daily Site Patrol report](#daily-site-patrol-report)). |
| `is_auto_generated` | BOOLEAN NOT NULL | Default `false`. `true` only for a backend-built General |

Constraints and indexes:
- `chk_idr_reports_parent`: a non-addendum can't have a parent. An addendum may have one, or
  none (a standalone addendum).
- `uq_idr_reports_one_gen_per_idr`: a partial unique index on `(idr_id)` where
  `report_type = 'GEN' AND is_addendum = false`. Each IDR has at most one General.
- `idx_idr_reports_idr (idr_id)`, and `idx_idr_reports_parent (parent_report_id)` partial on
  non-NULL.

Cascades: deleting an IDR deletes its reports, and deleting a report deletes its addenda.

**Page order on submit.** Pages are numbered in this order:
1. The General first.
2. Then each other main report, oldest first, with its addenda directly after it.
3. Standalone addenda (no parent) last.

### quantities

The pay-item quantities of approved IDRs, one row per pay item, kept apart from the reports so progress and
as-built reports can read them with plain queries. Added by migration 025. Final approval writes an IDR's rows in
the statement that approves it; admin unlock and admin delete remove them in theirs, and admin un-delete writes
them back for an IDR that comes back approved. So it only ever holds IDRs that are approved and not deleted. `scripts/backfill_quantities.py` fills it for the IDRs approved before that.

| Column | Type | Notes |
|---|---|---|
| `quantity_id` | UUID PK | `gen_random_uuid()` |
| `project_id` | TEXT NOT NULL | FK → `projects.project_id`. A copy of the IDR's. |
| `idr_id` | UUID NOT NULL | FK → `idrs.idr_id`, **ON DELETE CASCADE** |
| `report_date` | DATE NOT NULL | A copy of `idrs.report_date`, the work date |
| `reporter_uuid` | UUID NOT NULL | A copy of `idrs.reporter_uuid`, the inspector. No foreign key. |
| `report_type` | TEXT NOT NULL | The type of the report the pay item is on: `GEN`, `SWCB` or `AC`. Its disciplines are read off it (`api/services/disciplines.py`). |
| `pay_item_ref` | TEXT | The item number, `payItems[].itemNo`. NULL for an item without one. |
| `budget_code` | TEXT | `payItems[].budgetCode` |
| `description` | TEXT | `payItems[].description` |
| `amount` | NUMERIC NOT NULL | `payItems[].payQuantity`, parsed. An item whose quantity is blank or isn't a number gets no row. |
| `unit` | TEXT | `payItems[].unit` in its short form: `LF`, `SF`, `CY`, `SY`, `TN`, `EA`, `LS`; an unknown unit as written |
| `created_at` | TIMESTAMPTZ NOT NULL | `now()` |
| `source` | TEXT NOT NULL | `manual` (a report's pay item; every row before migration 037), `from_flags` (the flags the IDR claimed at approval, one row per pay item, `report_type` `PIR_FLAGS`) or `from_segments` (its claimed curb segments' lengths, in LF, `report_type` `PIR_SEGMENTS`; 044); CHECK `chk_quantities_source` |

- Indexes: `idx_quantities_project_date (project_id, report_date)`, `idx_quantities_project_item (project_id,
  pay_item_ref)`, `idx_quantities_project_budget (project_id, budget_code)`, `idx_quantities_idr (idr_id)`.
- No unique rule: the same item and budget code can be on one IDR more than once.
- An IDR's rows are always replaced as a set, in one statement (delete, then insert), so the copied columns never
  drift from the IDR that was approved.
- An auto-generated General's pay items are never written: they are sums of the other reports'.

### properties

A project's properties, one row each, of two kinds (migration 035): a **sidewalk**, the tax lot its work is done
on, named by its borough, block and lot (migration 036; a BBL before it), and a **ped ramp**, a corner of an
intersection. Each kind has its own
columns and leaves the other's NULL (CHECK `properties_kind_fields_consistent`); both have a borough. Added by
migration 029. Anyone on the project, in any role, and any admin reads, adds and changes them
(`/v1/projects/{id}/properties`, `/v1/properties/{id}`); only admins soft-delete and restore them. Demo users only
read (since S2).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `project_id` | TEXT NOT NULL | FK → `projects.project_id` |
| `kind` | `icid.property_kind` NOT NULL | `sidewalk` (the default, which every row before 035 got) or `ped_ramp`. Never changes after create (the API) |
| `boro` | `icid.boro` NOT NULL | `Manhattan`, `Bronx`, `Brooklyn`, `Queens` or `Staten Island`, sent for either kind (035 backfilled the rows before it from their BBLs' first digit) |
| `block` | TEXT | Sidewalk only, required there: at most 5 digits, no leading zeros (`0` for zero); CHECK `chk_properties_block_format` |
| `lot` | TEXT | Sidewalk only, required there: at most 4 digits, no leading zeros; CHECK `chk_properties_lot_format`. `(project_id, boro, block, lot)` is unique among live sidewalks (`uq_properties_project_sidewalk_bbl`, `WHERE kind = 'sidewalk' AND deleted_at IS NULL`) |
| `address` | TEXT | Sidewalk only, required there. Trimmed |
| `property_type` | TEXT | Sidewalk only, required there: `1_3_family`, `4_plus_family` or `expedited`; CHECK `chk_properties_type` |
| `violation_no` | TEXT | Sidewalk only, optional. Trimmed, blank is NULL |
| `community_board` | TEXT | Either kind, optional: the Community Board (CB). Trimmed, blank is NULL, at most 10 characters; CHECK `chk_properties_community_board_length` (043) |
| `intersection_street_1`, `intersection_street_2` | TEXT | Ped ramp only, required there. Trimmed |
| `quadrant` | `icid.quadrant` | Ped ramp only, required there: `NE`, `NW`, `SE` or `SW` |
| `facing_street` | `icid.facing_street` | Ped ramp only, required there: `street_1` or `street_2`, which of the two streets the ramp faces |
| `ramp_type` | `icid.ramp_type` | Ped ramp only, required there: `PERPENDICULAR`, `PARALLEL` or `CUT_THROUGH` |
| `corner_id` | UUID | Ped ramp only, optional (047): the inspection corner it is at; FK → `ped_ramp_corners.corner_id`, no action; CHECK `chk_properties_corner_kind`; `idx_properties_corner`. The API takes only a live corner of the property's project |
| `notes` | TEXT | Blank is NULL |
| `created_by` | UUID NOT NULL | The signed-in user who added it: any project member or admin; FK → `users.uuid` |
| `deleted_at` | TIMESTAMPTZ | Soft delete: when an admin deleted it. NULL for one that isn't deleted |
| `deleted_by` | UUID | The admin who did; FK → `users.uuid`, **ON DELETE SET NULL** |

A soft-deleted property keeps its row and frees its lot; un-deleting it is refused (409) while another live
sidewalk of the project is at that borough, block and lot. There is no permanent delete. Ped ramps have no
uniqueness rule: two alike can be added. There is no project index: the primary key serves reads by id, and a
project's list is small.

The five enums are Postgres types (`CREATE TYPE`), unlike the rest of the schema's TEXT-and-CHECK enums; the
statements cast their parameters (`%s::icid.quadrant`) and read them back `::text`. Nothing about a property's
label is stored: `display_address` is worked out by the API (see `CLAUDE.md`). **Rolling 036 back** is
deterministic: add `bbl` back as the borough's digit, `LPAD(block, 5, '0')` and `LPAD(lot, 4, '0')`.
**Rolling 035 back** then needs every row to have a BBL and an address again first: delete or fill the ped ramps.

### ped_ramp_corners

A corner of an intersection as NYC DOT's Pedestrian Ramp Inspection Form (version 1.6) records it: its sections 1-3
(inspection, location, construction), one row per corner inspection (migration 047, pedramp 2a). Its ramps are the
ped ramp properties whose `corner_id` names it; section 4 (the corner's ramp types) is read off them, not stored.
Members of the project and admins read, add and change corners; only admins soft-delete them (the ramps keep their
`corner_id`). Demo users only read.

| Column | Type | Notes |
|---|---|---|
| `corner_id` | UUID PK | `gen_random_uuid()` |
| `project_id` | TEXT NOT NULL | FK → `projects.project_id`; `idx_ped_ramp_corners_project` (live ones) |
| `inspected_by`, `inspection_date`, `entity` (`dob` / `other`), `entity_if_other` | TEXT, DATE, TEXT, TEXT | Section 1 |
| `boro` | `icid.boro` NOT NULL | Section 2, with `dot_corner_id` (the form's CORNER ID), `corner_number` (CORNER №), `north_south_street` and `east_west_street` (NOT NULL), `traffic_control_ns` / `_ew` (`signal` `signal_aps` `stop_yield` `none`), `corner_type` (`intersection_corner` `island_median` `mid_block` `top_of_t`), `corner_direction` (`SW` `NW` `NE` `SE` `N` `S` `E` `W`), `number_of_ramps` (1-3), `t_intersection`, `painted_roadway` |
| `contractor_name`, `permittee_id`, `fms_id`, `permit_number`, `construction_completion_date`, `contract_type` (`simple` / `complex`), `curb_material`, `sidewalk_material` | TEXT, DATE | Section 3; curb `steel_faced` `concrete` `concrete_and_gutter` `granite` `bluestone` `cobblestone` `other`, sidewalk `concrete` `pigmented_concrete` `bluestone` `cobblestone` `other` |
| `comments` | TEXT | |
| `created_at`, `updated_at` | TIMESTAMPTZ NOT NULL | |
| `created_by` | UUID NOT NULL | FK → `users.uuid` |
| `deleted_at`, `deleted_by` | TIMESTAMPTZ, UUID | Soft delete; `deleted_by` FK → `users.uuid`, **ON DELETE SET NULL** |

Every choice is TEXT with a CHECK. The text fields are trimmed, blank NULL, and capped by the API.

### ped_ramp_attributes

A ped ramp's section 5 (migration 047), one row per ped ramp property, keyed by `property_id` (FK → `properties.id`,
**ON DELETE CASCADE**). Every column is nullable: the fields every ramp has (`ramp_id_label`, `is_there_ponding`,
`maximum_curb_reveal_at_ramp_in`, the DWS fields, `construction`, `action_taken`, `comments`), then those of its ramp
type: a perpendicular ramp's flares, ramp, landing, gutter and left and right sidewalk approaches; a parallel ramp's
bottom landing and left and right ramps; a cut-through's ramp, gutter, `cut_through_length_in` and
`min_clearance_between_dws_in`. `clear_space_within_crosswalk` and `dws_aligned_with_ramp` are a perpendicular's
and a parallel's; `outside_of_parallel_travel_lane` is a perpendicular's, where the form has it. A cut-through's two
ramps are two properties with a row each; its length and clearance are kept on both. Slopes are percent, lengths,
widths, offsets and the reveal inches (NUMERIC, the lengths `>= 0`). The API refuses a field of another ramp type than
the property's; nothing in the table does. The ADA warnings are worked out by the API, not stored.

### task_orders

A project's task orders. Added by migration 030. Anyone on the project, in any role, and any admin reads them,
adds and changes them and bundles properties into them (`/v1/projects/{id}/task-orders`, `/v1/task-orders/{id}`);
only admins soft-delete and restore them. Demo users only read (since S1).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `project_id` | TEXT NOT NULL | FK → `projects.project_id` |
| `task_order_number` | TEXT NOT NULL | Trimmed; matched exactly. Unique per project among task orders that aren't deleted (`uq_task_orders_project_number`, `WHERE deleted_at IS NULL`) |
| `issued_date` | DATE | |
| `estimate_notes` | TEXT | Blank is NULL |
| `created_by` | UUID NOT NULL | The signed-in user who added it: any project member or admin; FK → `users.uuid` |
| `deleted_at` | TIMESTAMPTZ | Soft delete: when an admin deleted it. NULL for one that isn't deleted |
| `deleted_by` | UUID | The admin who did; FK → `users.uuid`, **ON DELETE SET NULL** |

### task_order_properties

Which properties a task order holds, one row per pair (PK `(task_order_id, property_id)`). A property can be in
several task orders (a re-issue is a new task order), once in each. Migration 030. No `created_at` / `updated_at`.

| Column | Type | Notes |
|---|---|---|
| `task_order_id` | UUID NOT NULL | FK → `task_orders.id`, **ON DELETE CASCADE** |
| `property_id` | UUID NOT NULL | FK → `properties.id`, **ON DELETE CASCADE**; `idx_task_order_properties_property` |
| `added_at` | TIMESTAMPTZ NOT NULL | `now()` |
| `added_by` | UUID NOT NULL | The signed-in user who bundled it: any project member or admin; FK → `users.uuid` |

- **Same project.** No constraint says the two sides share a project; the statement that bundles only inserts
  when they do (and neither is deleted). Neither row's `project_id` can change, so that holds.
- **Soft deletes leave the rows alone.** A soft-deleted task order keeps its bundle, so un-deleting it restores
  it. A soft-deleted property stays bundled but is left out of the task order's detail and its `property_count`
  (both filter `properties.deleted_at IS NULL`); un-deleting it brings it back. Only a permanent delete of either
  side (none is built yet) takes the rows, by the cascades.

### pirs

A property's PIR: the DOT survey sheet an inspector signed off, digitized by a project member or an admin. Added by
migration 031. Anyone on the property's project, in any role, and any admin reads, creates and edits them
(`/v1/properties/{id}/pir`, `/v1/properties/{id}/pirs`, `/v1/pirs/{id}`); only admins soft-delete and restore them.
Demo users only read (since S2).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `property_id` | UUID NOT NULL | FK → `properties.id`, no cascade (a PIR is a record of its own). One per property among PIRs that aren't deleted (`uq_pirs_property`, `WHERE deleted_at IS NULL`) |
| `inspector_initials` | TEXT NOT NULL | Trimmed, at most 16 characters |
| `inspector_sign_date` | DATE NOT NULL | When the inspector signed the sheet |
| `digitized_by` | UUID NOT NULL | The signed-in user who created the row: any project member or admin; FK → `users.uuid` |
| `digitized_at` | TIMESTAMPTZ NOT NULL | `now()` at creation |
| `as_built_signed_by` | UUID | FK → `users.uuid`, **ON DELETE SET NULL**. Set by a later slice's sign route; no route writes it yet |
| `as_built_signed_at` | TIMESTAMPTZ | Likewise |
| `north_bearing` | `icid.compass_point` | Which way North is on the PIR's sketch, every page alike. north_bearing is in the upright page's coordinate frame (the page as shown, after its `rotation_deg`), so the Construction Report doesn't turn it again: `N` `NE` `E` `SE` `S` `SW` `W` `NW` (migration 038). NULL: not known, and the Construction Report draws no north arrow. Set through `PATCH /v1/pirs/{id}`, where a null clears it |
| `deleted_at` | TIMESTAMPTZ | Soft delete: when an admin deleted it. NULL for one that isn't deleted |
| `deleted_by` | UUID | The admin who did; FK → `users.uuid`, **ON DELETE SET NULL** |

A soft-deleted PIR frees its property, so a replacement can be created while the old one is kept; un-deleting
the old one is then refused (409). No PIR can be created on a soft-deleted property.

### pir_pages

One page of a PIR's scan, rendered to PNG in the `pir-images` bucket. Added by migration 033, replacing R1d's
`pirs.source_image_url` / `source_image_scale` (dropped). Uploading a scan replaces the PIR's live pages: they, their
live flags (and those flags' non-assessable areas), features and segments are soft-deleted in the same statement, and
the new pages are inserted; their files, and their title blocks, are kept with them. Setting a page's scale works out
the length of every live segment on it again, in the same statement.

| Column | Type | Notes |
|---|---|---|
| `pir_page_id` | UUID PK | `gen_random_uuid()` |
| `pir_id` | UUID NOT NULL | FK → `pirs.id`, ON DELETE CASCADE; `pir_pages_pir_idx` |
| `page_number` | INT NOT NULL | ≥ 1; unique per PIR among live pages (`pir_pages_pir_page_num_live_uq`, `WHERE deleted_at IS NULL`) |
| `image_path` | TEXT NOT NULL | The PNG's **Storage path**, `pirs/{pir id}/{upload uuid}/page-{n}.png`, not a URL |
| `image_mime` | TEXT NOT NULL | `image/png` |
| `width_px`, `height_px` | INT NOT NULL | > 0; a PDF page at `PIR_IMAGE_RENDER_DPI` (150) |
| `scale_px_per_ft` | NUMERIC(10,4) | Pixels per foot, > 0; NULL until set |
| `rotation_deg` | INT NOT NULL | 0, 90, 180 or 270; default 0 |
| `deleted_at` | TIMESTAMPTZ | When the page was replaced (a re-upload) or deleted. NULL for a live page |
| `deleted_by` | UUID | Who did; FK → `users.uuid`, **ON DELETE SET NULL** |

### flags

The marked pieces of sidewalk on a PIR, one row each. Added by migration 032. Anyone on the project, and any admin,
reads, adds and edits them (`/v1/pirs/{id}/flags`, `/v1/flags/{id}`); only admins soft-delete and restore them.
Demo users only read.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `pir_id` | UUID NOT NULL | FK → `pirs.id`, no cascade; `idx_flags_pir` (live flags) |
| `pir_page_id` | UUID | The page it is drawn on; FK → `pir_pages.pir_page_id`, no action (flags are never hard-deleted); `flags_pir_page_idx`. A live flag has one (`chk_flags_live_on_a_page`); only a soft-deleted flag from before pages has none. Migration 033 |
| `display_order` | INT NOT NULL | ≥ 0 (`chk_flags_display_order`); not unique |
| `added_in_field` | BOOLEAN NOT NULL | Default false. True for a flag added on site, which has the mark `none` |
| `shape` | TEXT NOT NULL | `rectangle`, `trapezoid`, `triangle`, `polygon` or `arc`; CHECK `chk_flags_shape` |
| `dimensions` | JSONB NOT NULL | Per shape, in feet; checked by the API, not the database |
| `sketch_x`, `sketch_y` | REAL | Where it sits on its page (pixels) |
| `rotation` | REAL NOT NULL | Degrees on its page, default 0 |
| `surveyed_mark` | TEXT NOT NULL | `number_only`, `cross`, `cross_with_number`, `tick` or `none`; CHECK `chk_flags_surveyed_mark` |
| `surveyed_is_assessable` | BOOLEAN NOT NULL | |
| `surveyed_non_assessable_reason` | TEXT | Required when not assessable, empty when assessable (the API checks the pairing); CHECK `chk_flags_non_assessable_reason` on 22 values: R1b's nine (`tree_damage`, `city_tree`, `transition`, `no_driveway`, `hydrant`, `fronting_city`, `utility`, `settlement`, `other`) and the R1e.1 survey's (`tree`, `driveway`, `grass`, `dirt`, `existing_sidewalk`, `asphalt`, `tree_pit`, `landmark`, `vault`, `subway`, `scaffolding`, `other_perm_obstruction`, `other_temp_obstruction`; widened by migration 034) |
| `surveyed_thickness` | TEXT NOT NULL | `4in`, `7in` or `7in_as_4in`; CHECK `chk_flags_thickness` |
| `is_constructed` | BOOLEAN NOT NULL | Default false; true exactly while an approved IDR claims it (`chk_flags_claim_consistent`, migration 037). Set by approval, never by a flag route |
| `pay_item_id` | UUID | FK → `contract_items.contract_item_id`, **ON DELETE SET NULL**; a contract item of the PIR's project (the API checks); `idx_flags_pay_item` (live, mapped flags) |
| `computed_quantity` | NUMERIC NOT NULL | The shape's area in square feet, 2 decimal places, computed by the API. Its **net** area (in responses, not stored) is this less its non-assessable areas and the live `subtract` features naming it, never below 0 |
| `reported_quantity` | NUMERIC | An override, above 0 (`chk_flags_reported_quantity`); NULL means the net area stands |
| `defect_codes` | TEXT[] NOT NULL | The DOT defect codes marked on it, a subset of `1` `2` `3` `4` `5` `6A` `6B` `6C` (`chk_flags_defect_codes`), stored in that order, each once; default `{}`. Migration 034 |
| `quantity_delta_reason` | TEXT | Why the override differs |
| `created_by` | UUID NOT NULL | The signed-in user who added it: any project member or admin; FK → `users.uuid` |
| `deleted_at` | TIMESTAMPTZ | Soft delete: when an admin deleted it. NULL for one that isn't deleted |
| `deleted_by` | UUID | The admin who did; FK → `users.uuid`, **ON DELETE SET NULL** |
| `claimed_by_idr_id` | UUID | The approved IDR that claimed it (migration 037): FK → `idrs.idr_id`, no action (an unlock or a delete releases it first). At most one IDR claims a flag; a soft-deleted flag keeps its claim |
| `constructed_on` | DATE | The claiming IDR's `report_date`; NULL while unclaimed |

### idr_flag_attributions

An override of which flags an IDR covers (migration 037, R1f). By default an IDR covers the live flags on its task
order's properties; an `exclude` row leaves one of those out, an `include` row brings in another flag of its project.
Set by the IDR's inspector on a draft, its stage's reviewer in review, or an admin
(`/v1/idrs/{id}/attributed-flags/{flag_id}/exclude` and `/include`). Kept through returns, unlocks and deletes.

| Column | Type | Notes |
|---|---|---|
| `idr_id` | UUID NOT NULL | FK → `idrs.idr_id`, **ON DELETE CASCADE**; PK with `flag_id` |
| `flag_id` | UUID NOT NULL | FK → `flags.id`, **ON DELETE CASCADE**; `idx_idr_flag_attributions_flag` |
| `action` | TEXT NOT NULL | `exclude` or `include` (CHECK) |
| `set_by` | UUID | Who set it; FK → `users.uuid`, **ON DELETE SET NULL** (counted by `count_user_references` anyway) |
| `set_at` | TIMESTAMPTZ NOT NULL | `now()` |

### pir_match_lines

A lettered line joining a PIR's sheets (migration 046, R1h.1a), drawn on one page. Two pages pair by carrying the same
label; the Construction Report draws each at its endpoints, captioned with its partner's sheet.

| Column | Type | Notes |
|---|---|---|
| `match_line_id` | UUID PK | |
| `pir_page_id` | UUID NOT NULL | FK → `pir_pages`, no action (pages are never hard-deleted); `pir_match_lines_page_idx` |
| `label` | TEXT NOT NULL | `A`..`Z`, `AA`..`ZZ` (CHECK `chk_match_lines_label`); live once per page (`pir_match_lines_page_label_live_uq`, `WHERE deleted_at IS NULL`) and on at most two pages of a PIR (the API's statements) |
| `x1`, `y1`, `x2`, `y2` | REAL NOT NULL | The endpoints, in the page's native pixels (as flags and segments); CHECK `chk_match_lines_length` (they differ) |
| `created_at`, `updated_at`, `created_by`, `deleted_at`, `deleted_by` | | As on `features`; soft-deleted only; replacing the scan retires them |

### idr_segment_attributions

The same for curb segments (migration 044, curb billing): `idr_id`, `segment_id` (FK → `segments.segment_id`, **ON
DELETE CASCADE**; `idx_idr_segment_attributions_segment`), `action`, `set_by` and `set_at` as above, PK `(idr_id,
segment_id)`. Set through `/v1/idrs/{id}/attributed-segments/{segment_id}/exclude` and `/include`.

### non_assessable_areas

The parts of a flag that are not assessed (a driveway, grass, ...), one row each. Added by migration 034. Each is
drawn on its flag's page, and its area is taken off the flag's: a flag's net area is its own less its areas' and its
`subtract` features'. Anyone on the project, and any admin, adds, edits and deletes them
(`/v1/flags/{id}/non-assessable-areas`, `/v1/non-assessable-areas/{id}`); demo users only read.

**They go with their flag.** Soft-deleting a flag soft-deletes its live areas in the same statement, at the same
moment; un-deleting the flag brings back exactly those (the ones with its `deleted_at`), not one deleted on its own
before. Replacing or deleting a PIR's pages does the same for the flags on them.

| Column | Type | Notes |
|---|---|---|
| `non_assessable_area_id` | UUID PK | `gen_random_uuid()` |
| `flag_id` | UUID NOT NULL | FK → `flags.id`, no action (flags are never hard-deleted); `nas_flag_idx` |
| `display_order` | INT NOT NULL | ≥ 1; unique per flag among live areas (`nas_flag_order_live_uq`, `WHERE deleted_at IS NULL`) |
| `shape`, `dimensions` | TEXT, JSONB NOT NULL | A flag's shapes (`chk_nas_shape`), dimensions checked per shape by the API |
| `sketch_x`, `sketch_y`, `rotation` | REAL NOT NULL | Its place on the flag's page, as a flag's (native page pixels; rotation default 0, unbounded) |
| `reason` | TEXT NOT NULL | `driveway`, `grass`, `dirt`, `existing_sidewalk`, `asphalt`, `tree_pit`, `landmark` or `other` (`chk_nas_reason`) |
| `computed_quantity` | NUMERIC NOT NULL | Its area in square feet, computed by the API |
| `created_by` | UUID NOT NULL | FK → `users.uuid` |
| `deleted_at`, `deleted_by` | TIMESTAMPTZ, UUID | Soft delete; `deleted_by` FK → `users.uuid`, **ON DELETE SET NULL** |

### features

Point obstructions and sized features drawn on a page (a manhole, a tree pit, a hydrant, ...), one row each. Added
by migration 034. Project members and admins write them (`/v1/pir-pages/{id}/features`, `/v1/features/{id}`); demo
users only read. There is no un-delete.

| Column | Type | Notes |
|---|---|---|
| `feature_id` | UUID PK | `gen_random_uuid()` |
| `pir_page_id` | UUID NOT NULL | FK → `pir_pages`, no action; `features_page_idx`, `features_live_idx` |
| `flag_id` | UUID | FK → `flags.id`: the flag a `subtract` feature comes off, required then (`chk_features_subtract_flag`); a live flag on the same page (the API checks); `features_flag_live_idx` |
| `feature_type` | TEXT NOT NULL | `manhole`, `grate`, `utility_cover`, `hydrant`, `fire_alarm`, `tree_pit`, `meter`, `sign_or_meter`, `mail_box`, `telephone`, `cellar_door`, `pole`, `detectable_warning` or `other` |
| `area_impact` | TEXT NOT NULL | `marker_only` (default), `subtract` or `sits_on_top`; the last two need a shape (`chk_features_sized_impact`) |
| `sketch_x`, `sketch_y`, `rotation` | REAL NOT NULL | Its place on the page |
| `shape`, `dimensions` | TEXT, JSONB | Set together or not at all (`chk_features_shape_dimensions`); a flag's shapes and `circle` (`{r}`, area πr²) |
| `label` | TEXT | Trimmed, at most 200 characters |
| `pay_item_id` | UUID | FK → `contract_items`, **ON DELETE SET NULL**; one of the project's |
| `computed_quantity` | NUMERIC | Its area in square feet when it has a shape |
| `reported_quantity` | NUMERIC | An override, above 0; what a `subtract` feature takes off is this or else its area |
| `created_by`, `deleted_at`, `deleted_by` | | As on `non_assessable_areas` |

### segments

Curb runs drawn on a page, one row each. Added by migration 034. Same access as features.

| Column | Type | Notes |
|---|---|---|
| `segment_id` | UUID PK | `gen_random_uuid()` |
| `pir_page_id` | UUID NOT NULL | FK → `pir_pages`, no action; `segments_page_idx`, `segments_live_idx` |
| `segment_type` | TEXT NOT NULL | `curb_concrete`, `curb_concrete_depressed`, `curb_steel_faced`, `curb_steel_faced_corner`, `curb_granite` or `other` |
| `points` | JSONB NOT NULL | `[{x, y}, ...]` in native page pixels, two or more (the API checks; the database only that it is an array) |
| `length_ft` | NUMERIC | The sum of the runs between consecutive points over the page's scale, 2 decimal places, worked out in SQL whenever the segment is written or its page's scale is set; NULL while the page has no scale |
| `pay_item_id` | UUID | FK → `contract_items`, **ON DELETE SET NULL**; one of the project's. Billed when its unit is LF (curb billing) |
| `note` | TEXT | At most 2000 characters |
| `claimed_by_idr_id`, `constructed_on` | UUID, DATE | Set when an approved IDR claims the segment (its report date), cleared when that IDR is unlocked or deleted; FK → `idrs`, no action; CHECK `chk_segments_claim_consistent` (both or neither); `idx_segments_claimed_by_idr` (044) |
| `created_by`, `deleted_at`, `deleted_by` | | As on `non_assessable_areas` |

### pir_annotations

A page's title block, one row per page. Added by migration 034. Set with `PUT /v1/pir-pages/{id}/annotations`
(since R1h a field left out keeps its value and a null clears it; the Smart PIR import still replaces a block
whole); project members and admins, not demo users. The Construction Report prints page 1's. It has no `deleted_at`: a replaced page
keeps its title block, and the new page starts without one.

| Column | Type | Notes |
|---|---|---|
| `pir_page_id` | UUID PK | FK → `pir_pages`, no action |
| `contract_no`, `violation_no`, `property_address`, `block`, `lot`, `cross_street_left`, `cross_street_right`, `frontage_street` | TEXT | Trimmed, blank is NULL, at most 200 characters (the API; the title-block editor's route takes a street at most 60). The Construction Report prints the cross streets up the sketch's sides and the frontage under it (R1h.3) |
| `boro` | TEXT | One of the five boroughs, spelled out (`chk_pir_annotations_boro`) |
| `tax_map_width_ft`, `cc_curb_to_curb_ft` | NUMERIC(10,2) | ≥ 0. The Construction Report draws TAX MAP at scale between the PLs along the top. CC is the curb along the frontage (the column's name is a misreading, to be renamed): printed from the page's curb segments of every kind (concrete, depressed, steel-faced, granite) when it has any, and from `cc_curb_to_curb_ft` only when it has none (R1h.4) |
| `property_status` | TEXT | `all_defects_corrected`, `some_defects_corrected` or `no_work_done` |
| `corner_property_status` | TEXT | `ped_ramp_installed`, `no_ped_ramp_exists` or `existing_ped_ramp` |
| `violation_not_removed_due_to` | TEXT[] NOT NULL | Free text, default `{}`. The Construction Report ticks its nine boxes for the codes `work_already_done`, `distinctive_material`, `landmark`, `subway`, `tree`, `vault`, `scaffolding`, `other_perm_obstruction` and `other_temp_obstruction`, and drops any other text |
| `date_notice_issued`, `date_excavated`, `date_constructed` | DATE | As typed on the title block (migration 038). `date_constructed` is the pour, not `flags.constructed_on` (the claiming IDR's `report_date`, which billing and Progress use). The Construction Report prints them, blank when NULL |
| `created_at`, `updated_at` | TIMESTAMPTZ NOT NULL | |

### report_attachments

Files attached to a report. Each row describes one file; the file itself is in the private
Supabase Storage bucket `report-attachments`, which only the backend can reach (service key).

| Column | Type | Notes |
|---|---|---|
| `attachment_id` | UUID PK | `uuid_generate_v4()` |
| `report_id` | UUID NOT NULL | FK → `idr_reports.report_id`, **ON DELETE CASCADE** |
| `file_name` | TEXT NOT NULL | The name the file was uploaded with |
| `file_type` | TEXT NOT NULL | MIME type, e.g. `image/jpeg`, `application/pdf` |
| `file_size_bytes` | INTEGER NOT NULL | CHECK `chk_report_attachments_size`: more than 0, at most 10 MB (10485760) |
| `storage_path` | TEXT NOT NULL | Key in the bucket: `{report_id}/{attachment_id}_{sanitized file name}`. Not constrained unique (migration 010 dropped that); the embedded `attachment_id` keeps paths distinct. |
| `uploaded_by` | UUID NOT NULL | FK → `users.uuid` |
| `uploaded_at` | TIMESTAMPTZ NOT NULL | Default `now()`: set when the upload is requested and the row created, not when the file lands. The table has no `created_at`/`updated_at`. |
| `attachment_name` | TEXT NOT NULL | The inspector's display name for the file. CHECK `chk_report_attachments_name`: not blank, at most 200 characters |
| `attachment_description` | TEXT | Optional (migration 054). CHECK `chk_report_attachments_description`: NULL, or not blank and at most 2000 characters; the API stores a blank or missing one as NULL |
| `is_uploaded` | BOOLEAN NOT NULL | Default `false`. `false` while pending; `true` once the client reports the file is in Storage |

Index: `idx_report_attachments_report_id (report_id)`.

**Two-step upload.** `upload-request` validates the file details, inserts a pending row
(`is_uploaded = false`) and returns a signed URL the client PUTs the file to, straight to
Storage. `upload-complete` then sets `is_uploaded = true`. Pending rows are left out of the
attachment list and get no download URL; delete removes pending and uploaded rows alike.
Nothing cleans up a pending row whose upload never completes. Rows are edited in place, with
no timestamp: `upload-complete` flips `is_uploaded`, and `PUT` replaces `attachment_name` and
`attachment_description`.

Storage is not part of the database transaction. Deleting a report cascades its attachment
rows (and its addenda's), and the backend removes the matching Storage files separately, on a
best-effort basis. A file whose removal fails is left orphaned in the bucket; nothing points
at it. The same goes for an IDR an admin deletes for good: its attachment rows cascade with it, and the files
are removed afterwards.

Known polish item: in the IDR export, the "Attachment unavailable" page (a photo the export couldn't
fetch or read) still uses a small 10 pt note, unlike the PDF page's larger title and "no preview" box.

### Signature files (Storage bucket `signatures`)

Private bucket, PNG only, 500 KB per file, reached only by the backend (service key). `users.signature_path`
and `idrs.inspector_signature_path` hold object paths in it: `users/{user uuid}/signature.png` for a user's
current signature (replaced when they set a new one), `idrs/{idr id}/inspector_{random}.png` for the copy an
IDR keeps from submit, and `idrs/{idr id}/re_{random}.png` for the approver's copy from final approval
(`idrs.re_signature_path`). A copy is never replaced, and removed only when an admin deletes its IDR for good,
which empties `idrs/{idr id}/`.

### PIR page files (Storage bucket `pir-images`)

Private; only the backend reads and writes it (`migrations/031b_pir_images_bucket.sql`). 10 MB per file; PNG, JPEG
and PDF. `staging/{pir id}/{uuid}.{pdf|png|jpg}` is a raw upload, PUT by the client through a signed URL and removed
once the backend has stored its pages; `pirs/{pir id}/{upload uuid}/page-{n}.png` is one page, a PDF page rendered
to PNG (or the one image). Each upload gets a new folder. A replaced page's file is kept, for its flags' history.

### Export files (Storage bucket `idr-exports`)

Not a table. `GET /v1/idrs/{idr_id}/export` builds the IDR's `.xlsx`, uploads it to the private
bucket `idr-exports` at `{idr_id}/{YYYYMMDD_HHMMSS}_{file name}` (UTC; every export is a new object)
and returns a signed download URL valid for 10 minutes. Only the backend reaches the bucket
(service key). Nothing removes old exports, except an admin's permanent delete of the IDR, which empties
`{idr_id}/`.

### spec_items

The standard NYCDOT pay-item catalog. Added in migration 011. Since migration 026 a contract
item carries its own number, description, unit and section, so no endpoint reads this table;
the rows seeded from it still point at it through `spec_item_id`. Kept until it is cleaned up.

| Column | Type | Notes |
|---|---|---|
| `spec_item_id` | UUID PK | `uuid_generate_v4()` |
| `item_no` | TEXT NOT NULL | e.g. `4.13 AAS`. UNIQUE (`uq_spec_items_item_no`) |
| `description` | TEXT NOT NULL | e.g. `4" Concrete Sidewalk (Unpigmented)` |
| `spec_section` | TEXT NOT NULL | e.g. `4.13` |
| `pay_unit` | TEXT NOT NULL | e.g. `S.F.`, `Ton`, `Each` |

### contract_items

A project's Schedule of Bid Items. "Contract" is the project here; there is no separate
contracts table. Added in migration 011; migration 026 gave each row its own item number,
description, unit and section. `HWS0023`'s rows are seeded (`seed_sidewalk_pay_items.sql`);
an admin adds, edits and deletes any project's through
`/v1/admin/projects/{project_id}/pay-items`, or imports them from a CSV (`.../import/preview`,
`.../import/commit`); there a row is a "pay item".

| Column | Type | Notes |
|---|---|---|
| `contract_item_id` | UUID PK | `uuid_generate_v4()`. `pay_item_uuid` in the admin API |
| `project_id` | TEXT NOT NULL | FK → `projects.project_id`, **ON DELETE CASCADE** |
| `spec_item_id` | UUID | FK → `spec_items.spec_item_id`. Set on the seeded rows only; NULL on a row an admin created |
| `budget_code` | TEXT NOT NULL | Stored as typed, e.g. `SE0944(CONED-CONS` |
| `bid_quantity` | NUMERIC NOT NULL | `quantity` in the admin API, which takes up to 10 decimal places |
| `bid_unit_price` | NUMERIC NOT NULL | `unit_price` in the admin API, which takes up to 6 decimal places |
| `item_no` | TEXT NOT NULL | e.g. `4.13 AAS`. `pay_item_ref` in the admin API |
| `description` | TEXT NOT NULL | |
| `unit` | TEXT NOT NULL | The admin API and the CSV import store one short form per unit (`LF`, `EA`, `TN`; `UNIT_FORMS` in `api/services/pay_item_units.py`). The seeded rows keep the catalog's forms (`L.F.`, `Ton`) until edited or imported over |
| `spec_section` | TEXT | Optional, e.g. `4.13` |

`UNIQUE (project_id, item_no, budget_code)` (`uq_contract_items_project_item_budget`): the
same item number can appear under several budget codes in one project, but only once per
code. Indexes: `idx_contract_items_project (project_id)`, `idx_contract_items_spec_item (spec_item_id)`.

`GET /v1/contract_items/?project_id=` returns a project's rows for the pay-item picker, each
with `item_no`, `description`, `spec_section` (may be null) and `pay_unit` (the `unit` column).

Pay items in reports and rows of `quantities` are copies. The one foreign key to a contract item is
`flags.pay_item_id`, `ON DELETE SET NULL`, so deleting one takes it out of the picker and unmaps any flag that
pointed at it.

### form_templates (present, unused)

`id` UUID PK, `form_template_id` TEXT UNIQUE, `form_name`, `form_description`, `form_status`,
`mandatory_forms`, `optional_forms`. It holds one seeded row (`GENERAL`). This is the pre-IDR
form catalogue. Its only foreign key came from `completed_forms`, which migration 007 dropped,
so nothing references it and no code reads it. No migration drops it; 007's `CASCADE` removed
only `completed_forms`' own foreign key to it. It's kept until someone decides whether
templates come back.

## Report types

`report_type` codes come from the `ReportType` enum in `api/schemas/idr_report.py`. The labels
are `TYPE_LABELS` in the same file, and they must match the frontend's
`src/data/reportTypes.js`. The labels appear in the auto-generated General's description and
in the frontend dropdown.

| Code | Label | Addendum by nature |
|---|---|---|
| `GEN` | General | |
| `SWR` | Sewer | |
| `HC` | House Connection | |
| `WM_1` | Water Main (Sheet 1) | |
| `WM_2` | Water Main (Sheet 2) | ✓ |
| `WM_3` | Water Main (Sheet 3) | ✓ |
| `AC` | Asphaltic Concrete | |
| `CONC` | Concrete (Structures) | |
| `BOX` | Box Sewer | |
| `PILE` | Pile Driving | |
| `JACK` | Jacking | |
| `CCL` | Community Construction Liaison | |
| `RE` | Resident Engineer's Daily Diary | |
| `DSP` | Daily Site Patrol | Always, of its own kind (not listed; see below) |
| `OFF` | Office Report | |
| `SKETCH` | Sketch Sheet | ✓ |
| `CONT` | Report Continuation | ✓ |
| `CONC_MIX` | Concrete Truck & Mix Info | ✓ |
| `CONC_CYL` | Concrete Cylinder Data | ✓ |
| `FIELD_MEMO` | Field Memo | |
| `FIELD_ORDER` | Field Order | |

"Addendum by nature" is `ADDENDUM_TYPES`. It is only a hint for the frontend: the backend never
derives `is_addendum` from the type. The client sends it.

## Pay items

A report's pay items live in its `report_data.payItems`, as an array of
`{itemNo, budgetCode, payQuantity, unit, description}` strings. They are **copies**, not
foreign keys: the frontend's picker fills them from the project's contract items (Item No.,
Description and Unit from the spec item, plus Budget Code when the item has only one), and
the inspector can edit any of them afterwards or type a row by hand (change orders). Nothing
in the database ties a pay item to `contract_items`. `unit` was added in Slice F; rows saved
before it have none, and the frontend loads them with `unit: ''`.

## Daily Site Patrol report

A `DSP` report's `report_data` is the one report body the server checks (`DspReport`, `api/schemas/dsp_report.py`),
on every save (`PUT /v1/idrs/{idr_id}/reports/{report_id}`); a refusal is a 400 naming the first problem
(`DSP report: mptChecklist[3].answer: Input should be 'yes', 'no' or 'na'`). A new DSP is created `{}` like any
report, so the form's first save writes it whole. No migration: `report_type` is plain text.

**Top-level, one per IDR** (V4, reversing U1's addendum). A DSP is added only as a main report, else 400 `A Daily
Site Patrol is a top-level report, not an addendum`; a second is 409 `This IDR already has a Daily Site Patrol` with
`existing_report_id`. The route checks first, and `uq_idr_reports_one_dsp_per_idr` (migration 053, partial on
`(idr_id) WHERE report_type = 'DSP'`) backs it. DSPs filed as addenda under U1 are left as they are (and print after
their parent).

```json
{"timeOfPatrol": "14:30",
 "mosChecklist": [{"item": "waste_rubbish_debris_removed", "answer": "no", "details": "Trench left open"}, ...],
 "mptChecklist": [{"item": "traffic_maintained_per_plan", "answer": "yes", "details": null}, ...],
 "fieldOrderIssued": true, "fieldOrderNumber": "FO-12"}
```

| Key | Rule |
|---|---|
| `timeOfPatrol` | `HH:MM`, 24-hour, or null (blank is null) |
| `mosChecklist` | Exactly the 6 Maintenance of Site items, in the form's order, each once: `waste_rubbish_debris_removed`, `open_trenches_plated_fenced`, `stored_materials_secured`, `open_paved_areas_swept`, `trees_protected`, `temporary_pavement_maintained` |
| `mptChecklist` | Exactly the 16 Maintenance and Protection of Traffic items, the same way: `traffic_maintained_per_plan`, `advance_warning_signs`, `traffic_control_signs`, `tapers_transitions`, `barrels`, `flashing_lights`, `breakaway_barricades`, `arrow_boards`, `timber_curbs_concrete_barriers`, `steel_pedestrian_fencing`, `pedestrian_building_access`, `fire_hydrant_access`, `open_sidewalks_clean`, `closed_sidewalk_signs`, `commercial_area_access`, `flaggers_temp_signs` |
| a row's `answer` | `yes`, `no`, `na` or null (unanswered; allowed, submit doesn't check) |
| a row's `details` | Text, at most 2000 characters, or null (blank is null). A `no` without details is only a warning, in the frontend |
| `fieldOrderIssued` | true, false or null (default false) |
| `fieldOrderNumber` | At most 50 characters, or null (blank is null); required when `fieldOrderIssued` is true |

Any other key, at the top or in a row, is refused, and the types are strict (`"true"` is not a boolean). Text is
stored trimmed. The day of the week, the sheet number and the I.R. No. are not stored: the export works them out from
the IDR. A DSP has no pay items: it gives no quantities and doesn't feed the auto-General.

## Auto-generated General

Added in Phase R, Slice R4a. The General is the IDR's summary page. When an inspector files
several reports but no General, the backend builds one for them. The logic lives in
`api/services/auto_general.py` and runs after every report create, update or delete.

The two flags:

- **`idr_reports.is_auto_generated`**: `true` marks a General the backend owns. Its
  `report_data` is exactly `{description, payItems}`. The description aggregates each
  contributing report under its type label and ends with a fixed footer. The pay items are
  the contributing reports' pay items combined by `(itemNo, budgetCode)`: quantities are
  summed to two decimals when they all parse as numbers (otherwise the first non-empty value
  is kept), and `description`, `unit` and `quantityChk` take the first non-empty value. Two
  different units under one key log a warning and the first wins. The backend fully
  rewrites that `report_data` on every regeneration, and the frontend shows it read-only. A
  General the inspector created themselves is always `false`, and the backend never touches it.
- **`idrs.has_dismissed_auto_general`**: set to `true` when an inspector deletes an
  auto-generated General. From then on the backend won't re-create one for that IDR. It is
  never reset.

Rules, where "contributing reports" means the IDR's main reports that are neither addenda nor
the General, nor of a type in `NON_CONTRIBUTING_TYPES` (the addendums by nature, and `DSP`, a checklist with no
description of work or pay items):

| Situation | What the backend does |
|---|---|
| The inspector created the General | Nothing, ever |
| 2 or more contributing reports, auto-General exists | Rewrites its `report_data` |
| 2 or more contributing reports, no General, not dismissed | Creates an auto-General |
| 2 or more contributing reports, no General, dismissed | Nothing |
| Fewer than 2 contributing reports, auto-General exists | Deletes it |

AC reports count as contributing (toward the 2), but their description and pay items stay out of
the merge (`MERGE_EXCLUDED_TYPES`): the export prints them on AC Fr / AC Bk. An auto-General already
stored keeps its old content until the next regeneration.

## Migration history

Migrations live in `migrations/` and are run by hand in the Supabase SQL editor. Each one wraps
its changes in `BEGIN … COMMIT` and carries pre-check and verification queries as comments.

**Baseline (before 001).** The original `schema.sql` created `clients`, `users`, `projects`,
`project_users`, `project_clients`, `form_templates`, and the pre-IDR pair `reports`
(one row per filed report, with a TEXT `report_id`) and `completed_forms` (a report's form
content as TEXT, linked to a report and a form template).

| # | File | Slice | What it did |
|---|---|---|---|
| 001 | `001_slice1_schema.sql` | Slice 1 | Added `reports.status` (`draft`/`submitted`, CHECK). Converted `completed_forms.form_data` from TEXT to JSONB. Seeded the `GENERAL` form template. |
| 002 | `002_report_id_uuid.sql` | Slice 1 | Replaced every `reports.report_id` (`R1`, `R2`, …) with a fresh UUID and converted the column from TEXT to UUID with a `uuid_generate_v4()` default. Remapped `completed_forms.report_id` to the new ids, converted it to UUID too, and re-created its foreign key. Gave `completed_forms.completed_form_id` a UUID default. Added `UNIQUE (report_id, form_template_id)` on `completed_forms`. |
| 003 | `003_add_reports_submitted_at.sql` | Slice 3 | Added `reports.submitted_at`. |
| 004 | `004_idr_refactor.sql` | R1 | Created `idrs` and `idr_reports` with their constraints and indexes. Moved the data across: each `reports` row became one IDR (keeping its status, `submitted_at` and timestamps) plus one non-addendum `GEN` idr_report, with its `completed_forms.form_data` as `report_data` (`{}` if it had none). Header fields, `total_pages` and `page_number` were left NULL. The old tables were kept. |
| 005 | `005_one_general_per_idr.sql` | R2 | Added the partial unique index `uq_idr_reports_one_gen_per_idr`: at most one non-addendum General per IDR. |
| 006 | `006_auto_general_flags.sql` | R4a | Added `idr_reports.is_auto_generated` and `idrs.has_dismissed_auto_general`, both `BOOLEAN NOT NULL DEFAULT false`, each with a column `COMMENT` describing it. |
| 007 | `007_cleanup.sql` | R5 | Backfilled `total_pages = 1` and `page_number = 1` for the Slice 5 IDR (`8b2f887b-…`), which was submitted before page numbering existed. Dropped `completed_forms`, then `reports` (`CASCADE`). `form_templates` was not touched. |
| 008 | `008_report_attachments.sql` | A1 | Created `report_attachments` (FK to `idr_reports` with ON DELETE CASCADE, FK to `users`, UNIQUE `storage_path`, size CHECK) and `idx_report_attachments_report_id`. |
| 009 | `009_attachment_metadata_and_pending.sql` | A2a | Added `report_attachments.attachment_name` and `attachment_description` (TEXT NOT NULL, each with a not-blank / max-length CHECK) and `is_uploaded` (BOOLEAN NOT NULL DEFAULT false), each with a column `COMMENT`. Re-asserted `chk_report_attachments_size`. Required the table to be empty, since the new text columns have no default. |
| 010 | `010_drop_storage_path_unique.sql` | A2a | Dropped `report_attachments_storage_path_key`, the UNIQUE on `storage_path` from 008, which 009 had meant to drop but didn't. |
| 011 | `011_spec_items_and_contract_items.sql` | F2 | Created `spec_items` (UNIQUE `item_no`) and `contract_items` (FK to `projects` with ON DELETE CASCADE, FK to `spec_items`, UNIQUE `(project_id, spec_item_id, budget_code)`) and the two `contract_items` indexes. |
| 012 | `012_idr_exports_bucket.sql` | D6a | Created the private Storage bucket `idr-exports` (50 MB per file; `.xlsx` and PDF only). No `icid` table changes. |
| 013 | `013_auth_users.sql` | H0 | Added `users.password_hash` and `role` (TEXT, nullable) and `is_demo` (BOOLEAN NOT NULL DEFAULT false), `UNIQUE (email)` as `uq_users_email`, and the partial index `idx_users_is_demo`. |
| 014 | `014_demo_cleanup.sql` | H3 | Created the function `icid.cleanup_abandoned_demo_users()` (SECURITY DEFINER, EXECUTE revoked from PUBLIC): deletes demo users older than 24 hours with their attachments, reports, IDRs and project assignments, and returns how many. No table changes; scheduling it is a separate, manual step. |
| 015 | `015_signatures.sql` | I0 | Added `users.signature_path`, `signature_type` (CHECK `drawn` / `uploaded`) and `signature_set_at`, and `idrs.inspector_signature_path` and `inspector_signed_at`. All nullable. |
| 015b | `015b_signatures_bucket.sql` | I0 | Created the private Storage bucket `signatures` (500 KB per file; PNG only). No `icid` table changes. |
| 016 | `016_users_email_lower.sql` | Housekeeping | Created the unique index `idx_users_email_lower` on `lower(email)`; dropped `uq_users_email` (case-sensitive, from 013) and the baseline's plain `idx_users_email`. |
| 017 | `017_review_workflow.sql` | J0 | Widened `chk_idrs_status` to the review statuses (`stage1_review`, `stage2_review`, `approved`, `returned`, `deleted`). Added the review columns on `idrs` (`idr_number`, the Stage 1 and RE reviewer columns, `re_signature_path`, `re_signed_at`, `return_reason`, `returned_from` with its CHECK, `deleted_at`, `deleted_by`), all nullable. Rebuilt `idx_idrs_project_status` as a partial index and added `idx_idrs_status` and the partial unique index `uq_idrs_project_number`. Replaced the constraint `uq_idrs_project_reporter_date` with a partial unique index of the same name (`WHERE deleted_at IS NULL`). |
| 018 | `018_project_roles.sql` | J0 | Added `project_users.role` (`inspector` / `oe` / `re`, NOT NULL DEFAULT `inspector`, CHECK) and replaced the primary key `(project_id, user_uuid)` with `(project_id, user_uuid, role)`. |
| 019 | `019_idr_audit.sql` | J1 | Created `idr_audit` (FK to `idrs` with ON DELETE CASCADE, FK to `users`) and `idx_idr_audit_idr_created`. |
| 020 | `020_field_edits.sql` | K0 | Created `idr_field_edits` (FKs to `idrs` and `idr_reports` with ON DELETE CASCADE, FK to `users`, three CHECKs) and its two indexes. Gave every existing pay item in `idr_reports.report_data` an `id`; nothing else in any report changed. |
| 021 | `021_pay_item_approve.sql` | K2.5 | Widened `chk_idr_field_edits_type` to allow `pay_item_approve`. No row changed. |
| 022 | `022_stage_accepted_at.sql` | K2.6 | Added `idrs.stage1_accepted_at` and `stage2_accepted_at` (TIMESTAMPTZ, nullable, no backfill). |
| 023 | `023_truck_add.sql` | K3-fix2 | Widened `chk_idr_field_edits_type` to allow `truck_add`, and `chk_idr_field_edits_old_value` to let it (like `pay_item_add`) have no `old_value`. Gave every existing truck in `idr_reports.report_data` an `id`; nothing else in any report changed. |
| 025 | `025_quantities.sql` | L0 | Created `quantities` with its four indexes. No existing table or row changed. There is no 024. |
| 026 | `026_contract_items_own_columns.sql` | P0a | Added `contract_items.item_no`, `description`, `unit` (NOT NULL) and `spec_section` (nullable), each filled from the row's spec item. Made `spec_item_id` nullable, widened `bid_quantity` and `bid_unit_price` from NUMERIC(12,2) to NUMERIC, and replaced `uq_contract_items_project_spec_budget` with `uq_contract_items_project_item_budget` on `(project_id, item_no, budget_code)`. `spec_items` unchanged. |
| 028 | `028_users_soft_delete.sql` | P3 | Added `users.deleted_at` and `deleted_by` (FK to `users` with ON DELETE SET NULL), and made `idx_users_email_lower` partial (`WHERE deleted_at IS NULL`), so a deleted user's email can be used again. No row changed. There is no 027. |
| 029 | `029_properties.sql` | R0a | Created `properties` (FK to `projects`, FKs to `users` for `created_by` and, ON DELETE SET NULL, `deleted_by`, CHECK `chk_properties_type`) and the partial unique index `uq_properties_project_bbl`. No existing table or row changed. |
| 030 | `030_task_orders.sql` | R0b | Created `task_orders` (FK to `projects`, FKs to `users` for `created_by` and, ON DELETE SET NULL, `deleted_by`) with the partial unique index `uq_task_orders_project_number`, and `task_order_properties` (PK `(task_order_id, property_id)`, FKs to `task_orders` and `properties` both ON DELETE CASCADE, FK to `users` for `added_by`) with `idx_task_order_properties_property`. No existing table or row changed. |
| 031 | `031_pirs.sql` | R1a | Created `pirs` (FK to `properties` with no cascade; FKs to `users` for `digitized_by` and, ON DELETE SET NULL, `as_built_signed_by` and `deleted_by`) with the partial unique index `uq_pirs_property`. No existing table or row changed. |
| 031b | `031b_pir_images_bucket.sql` | R1d | Created the private Storage bucket `pir-images` (10 MB per file; PNG, JPEG and PDF). No `icid` table changes. |
| 032 | `032_flags.sql` | R1b | Created `flags` (FK to `pirs` with no cascade; FK to `contract_items` ON DELETE SET NULL; FKs to `users` for `created_by` and, ON DELETE SET NULL, `deleted_by`; CHECKs on `display_order`, `shape`, `surveyed_mark`, `surveyed_non_assessable_reason`, `surveyed_thickness` and `reported_quantity`) with the partial indexes `idx_flags_pir` and `idx_flags_pay_item`. No existing table or row changed. |
| 033 | `033_pir_pages_and_flag_coords.sql` | R1e | Created `pir_pages` (FK to `pirs` ON DELETE CASCADE, FK to `users` for `deleted_by` ON DELETE SET NULL, CHECKs on `page_number`, `width_px`, `height_px`, `scale_px_per_ft` and `rotation_deg`) with the partial unique index `pir_pages_pir_page_num_live_uq` and `pir_pages_pir_idx`. Dropped `pirs.source_image_url` and `source_image_scale` (no PIR had an image). Soft-deleted the 3 flags made through R1b's form, then added `flags.pir_page_id` (FK to `pir_pages`, no action), CHECK `chk_flags_live_on_a_page` and `flags_pir_page_idx`. |
| 035 | `035_property_subtyping.sql` | Property kinds | Created the enum types `property_kind`, `boro`, `quadrant`, `facing_street` and `ramp_type`. Added `properties.kind` (NOT NULL DEFAULT `sidewalk`), `boro` (backfilled from each BBL's first digit, then NOT NULL), `violation_no`, `intersection_street_1`, `intersection_street_2`, `quadrant`, `facing_street` and `ramp_type`; dropped NOT NULL on `bbl`, `address` and `property_type`; added CHECK `properties_kind_fields_consistent`. The 4 existing rows became sidewalks (3 Brooklyn, 1 Queens). |
| 039 | `039_move_sidewalk_from_se384_to_hws0023.sql` | M1 | Data only, no schema change. Moved SE384's live sites, task orders, PIRs and IDR 001 (renumbered `Walk004`) to HWS0023, its flags remapped to HWS0023's pay items; hard-deleted SE384's soft-deleted site history and HWS0023's empty task order "1"; renamed HWS0023's budget code `12345` to `HW-200(S)` / `HW-200(P)` (corner, detectable or preformed items) everywhere it was copied (catalog, quantity rows, report pay items, field edits), deleted the orphan `4.13 AAS` under `67890`, seeded `73.21AC` under `HW-200(P)`, and gave HWS0023 SE384's DOT rates. Run once, inside a transaction it guards and checks. |
| 040 | `040_retag_walk004_flag_quantity_descriptions.sql` | M1 | Data only, no schema change. Gave Walk004's `from_flags` quantity rows the descriptions of the HWS0023 contract items they match (039 had left SE384's text; one row, `4.13 AAS`, differed in capitals). Amounts, units and codes unchanged. |
| 041 | (none) | | Skipped: R1h.3 needed no migration. |
| 042 | `042_walk003_budget_code_3482.sql` | E | Data only, no schema change. Moved Walk003's 4.02 CA pay item (its GEN report and its manual quantity row) from the budget code an inspector typed, `3482`, which no contract item has, to `HW-200(S)`, so its 2 TN counts as budgeted. |
| 043 | `043_property_community_board.sql` | A | Added `properties.community_board` (TEXT, nullable) and CHECK `chk_properties_community_board_length` (at most 10 characters). No backfill: every existing row is NULL. |
| 044 | `044_curb_billing.sql` | Curb billing | Added `segments.claimed_by_idr_id` (FK to `idrs`, no action) and `constructed_on`, CHECK `chk_segments_claim_consistent`, `idx_segments_claimed_by_idr`; created `idr_segment_attributions` (PK `(idr_id, segment_id)`, FKs to `idrs` and `segments` ON DELETE CASCADE, to `users` for `set_by` ON DELETE SET NULL: 27 → 28 FKs to `users`); widened `chk_quantities_source` with `from_segments`. Added HWS0023's contract items 4.08 AB (Depressed Concrete Curb, $55/LF, 500 LF) and 4.08 GA (Granite Curb, $150/LF, 400 LF). |
| 045 | `045_claim_approved_segments.sql` | Curb billing | Data only, no schema change. Gave HWS0023's live curb segments the pay item of their kind (concrete 4.08 AA, depressed 4.08 AB, granite 4.08 GA: 13, 2 and 1 segments) and had IDR-001 and Walk004, approved before curb billing, claim their task orders' segments and get their `from_segments` rows (IDR-001: 281.50 / 10.00 / 81.00 LF; Walk004: 17.09 / 17.16 LF). |
| 047 | `047_ped_ramp_inspection.sql` | Pedramp 2a | Created `ped_ramp_corners` (FK to `projects`; to `users` for `created_by` and, ON DELETE SET NULL, `deleted_by`: 30 → 32 FKs to `users`; `idx_ped_ramp_corners_project`) and `ped_ramp_attributes` (PK and FK `property_id` → `properties` ON DELETE CASCADE). Added `properties.corner_id` (FK to `ped_ramp_corners`, no action), CHECK `chk_properties_corner_kind` and `idx_properties_corner`. No existing row changed. |
| 048 | `048_ped_ramp_inspections.sql` | Pedramp Phase 3 (S1) | Created `ped_ramp_inspections` (FK to `ped_ramp_corners`, no action; to `users` for `inspector_uuid`, `re_uuid`, `returned_by` and `created_by`: 32 → 36 FKs to `users`; CHECKs `chk_ped_ramp_inspections_submitted` and `_approved`; partial unique `uq_ped_ramp_inspections_live_corner` `WHERE status <> 'approved'`, `idx_ped_ramp_inspections_corner`). Replaced four choice CHECKs with DOT's lists, ONE-WAY (a value dropped can't come back without a data migration): `ped_ramp_corners_entity_check` (DOT's 13 entities), `ped_ramp_corners_curb_material_check` (cobblestone dropped), `ped_ramp_attributes_dws_color_check` (yellow dropped; dark on light, light on dark added), `ped_ramp_attributes_dws_material_check` (preformed plastic split into embedded / surface applied, cast in place surface applied, concrete tile; other dropped). Added `ped_ramp_attributes.ramp_location_code` (SMALLINT 0-22, CHECK `chk_ped_ramp_attributes_location_code`). No existing row changed: the one corner had entity and curb material NULL, and no ramp had attributes. |
| 049 | `049_ped_ramp_tifs.sql` | Pedramp Phase 3 (S3) | Created `ped_ramp_tifs` (FKs to `properties` and `ped_ramp_inspections`, no action; to `users` for `signed_by_uuid` and `created_by`: 36 → 38 FKs to `users`; CHECK `chk_ped_ramp_tifs_signed`; unique `uq_ped_ramp_tifs_ramp_inspection`, `idx_ped_ramp_tifs_inspection`). |
| 050 | `050_ped_ramp_photos.sql` | Pedramp Phase 3 (S4) | Created `ped_ramp_photos` (FKs to `ped_ramp_inspections`, `ped_ramp_tifs` and `properties`, no action; to `users` for `uploaded_by`: 38 → 39 FKs to `users`; CHECKs `chk_ped_ramp_photos_one_parent`, `_inspection_ramp`, `_layout_on_tif`; partial uniques `uq_ped_ramp_photos_inspection_number`, `_tif_number`, `_tif_layout`). |
| 050b | `050b_pedramp_buckets.sql` | Pedramp Phase 3 | Storage only: the private buckets `pedramp-photos` (10 MB; JPEG, PNG, HEIC) and `pedramp-exports` (50 MB; PDF, ZIP). |
| 051 | `051_ped_ramp_inspection_unlock.sql` | Slice T1 | Added `ped_ramp_inspections.last_unlocked_at` (TIMESTAMPTZ) and `last_unlocked_by` (FK to `users`: 39 → 40 FKs to `users`), both nullable: an admin's unlock of an approved inspection records them. No existing row changed. |
| 052 | `052_idr_unlock_columns.sql` | Slice T2 | Added `idrs.last_unlocked_at` (TIMESTAMPTZ) and `last_unlocked_by` (FK to `users`: 40 → 41 FKs to `users`), both nullable: an admin unlock records them. No existing row changed; earlier unlocks are in `idr_audit`. |
| 053 | `053_idr_report_sites.sql` | Slice U | Added `uq_idr_reports_one_dsp_per_idr` (one DSP per IDR, partial on `report_type = 'DSP'`), `uq_idr_reports_report_idr` (for the composite foreign key) and `idr_report_sites`: a report's imported sites (`report_id` with `idr_id` referencing `idr_reports (report_id, idr_id)`, `idr_id` ON DELETE CASCADE, `property_id`, `work_type` with `chk_idr_report_sites_other`, `added_at`, `added_by`: 41 → 42 FKs to `users`; one row per IDR and property, `uq_idr_report_sites_idr_property`). An IDR's flags and curb segments are now those on its imported sites. Backfill: each approved IDR's claimed flags' and segments' properties into its first report as `concrete` (IDR-001 13, IDR-002 1, Walk004 2). |
| 054 | `054_site_pay_items.sql` | Slice V (V1, V3) | Created `idr_report_site_pay_items`: pay items entered by hand on an imported site (`report_site_id` ON DELETE CASCADE, `contract_item_id` no action, so a pay item in use can't be deleted; `quantity > 0`, `note` at most 2000, `added_at`, `added_by`: 42 → 43 FKs to `users`), billed at approval as `from_sites` quantity rows (`chk_quantities_source` widened). `report_attachments.attachment_description` made nullable, its CHECK allowing NULL. |
| 055 | `055_report_site_updated.sql` | Slice V2 | Added `idr_report_sites.updated_at` (TIMESTAMPTZ) and `updated_by` (FK to `users`: 43 → 44 FKs to `users`), both nullable, set by the site PATCH when the work changes (no trigger). |
| 046 | `046_pir_match_lines.sql` | R1h.1a | Created `pir_match_lines` (FK to `pir_pages`, no action; to `users` for `created_by` and, ON DELETE SET NULL, `deleted_by`: 28 → 30 FKs to `users`; CHECKs `chk_match_lines_label` and `chk_match_lines_length`; partial unique `pir_match_lines_page_label_live_uq` and `pir_match_lines_page_idx`). No existing row changed. |
| 038 | `038_construction_report_fields.sql` | R1h | Created the enum `icid.compass_point` (`N` `NE` `E` `SE` `S` `SW` `W` `NW`) and added `pirs.north_bearing` (that type) and `pir_annotations.date_notice_issued`, `date_excavated` and `date_constructed` (DATE), all nullable. No existing row changed. |
| 037 | `037_flag_idr_attribution.sql` | R1f | Added `idrs.task_order_id` (FK to `task_orders`, no action; `idx_idrs_task_order`), `flags.claimed_by_idr_id` (FK to `idrs`, no action; `idx_flags_claimed_by_idr`) and `flags.constructed_on` with CHECK `chk_flags_claim_consistent`, `idx_flags_scope_rollup`, and `quantities.source` (`manual`, the default every existing row got, or `from_flags`; CHECK `chk_quantities_source`). Created `idr_flag_attributions` (PK `(idr_id, flag_id)`, FKs to `idrs` and `flags` ON DELETE CASCADE, to `users` for `set_by` ON DELETE SET NULL: 26 → 27 FKs to `users`). No existing row changed meaning: no flag was constructed. |
| 036 | `036_split_bbl_into_block_lot.sql` | R1e.3 | Added `properties.block` and `lot` (TEXT, CHECKs `chk_properties_block_format` and `chk_properties_lot_format`), backfilled from each sidewalk's BBL without leading zeros; replaced `properties_kind_fields_consistent` (block and lot where it had bbl); dropped `uq_properties_project_bbl` and the `bbl` column; created `uq_properties_project_sidewalk_bbl` on `(project_id, boro, block, lot)` `WHERE kind = 'sidewalk' AND deleted_at IS NULL`. The 6 rows: 5 sidewalks backfilled, 1 ped ramp untouched. |
| 034 | `034_smart_pir_data_model.sql` | Smart PIR | Added `flags.defect_codes` (TEXT[] NOT NULL DEFAULT `{}`, CHECK `chk_flags_defect_codes`) and widened `chk_flags_non_assessable_reason` to R1b's nine values plus the R1e.1 survey's (22). Created `non_assessable_areas` (FK to `flags`, no action; partial unique index `nas_flag_order_live_uq`), `features` (FKs to `pir_pages` and `flags`, no action; to `contract_items` ON DELETE SET NULL), `segments` (FK to `pir_pages`, no action; to `contract_items` ON DELETE SET NULL) and `pir_annotations` (keyed by `pir_page_id`, no action), the first three with `created_by` and, ON DELETE SET NULL, `deleted_by` FKs to `users` (20 → 26 FKs to `users`). Added `projects.cost_per_sf_4in` and `cost_per_sf_7in` (NUMERIC(10,4), ≥ 0). No existing row changed; the thickness CHECK already held `7in_as_4in`. |

Where each current column came from:

| Table | Columns | Added in |
|---|---|---|
| `clients`, `users`, `projects`, `project_users`, `project_clients`, `form_templates` | all | Baseline |
| `users` | `password_hash`, `role`, `is_demo`, `idx_users_is_demo` (index) | 013 |
| `users` | `idx_users_email_lower` (unique index; replaced 013's `uq_users_email` and the baseline's `idx_users_email`) | 016 (partial since 028) |
| `users` | `deleted_at`, `deleted_by` | 028 |
| `users` | `signature_path`, `signature_type`, `signature_set_at`, `chk_users_signature_type` | 015 |
| `idrs` | `inspector_signature_path`, `inspector_signed_at` | 015 |
| `idrs` | the review columns, `chk_idrs_returned_from`, `idx_idrs_status`, `uq_idrs_project_number`; the wider `chk_idrs_status`, the partial `idx_idrs_project_status`, and `uq_idrs_project_reporter_date` as a partial unique index | 017 |
| `idrs` | `stage1_accepted_at`, `stage2_accepted_at` | 022 |
| `quantities` | all | 025 |
| `properties` | all | 029 |
| `properties` | `kind`, `boro`, `violation_no`, `intersection_street_1`, `intersection_street_2`, `quadrant`, `facing_street`, `ramp_type`, `properties_kind_fields_consistent`; `bbl`, `address` and `property_type` nullable | 035 |
| `properties` | `block`, `lot`, `chk_properties_block_format`, `chk_properties_lot_format`, `uq_properties_project_sidewalk_bbl`, the `properties_kind_fields_consistent` of today; `bbl` and `uq_properties_project_bbl` dropped | 036 |
| `properties` | `community_board`, `chk_properties_community_board_length` | 043 |
| `idr_segment_attributions`; `segments.claimed_by_idr_id`, `constructed_on`, `chk_segments_claim_consistent`, `idx_segments_claimed_by_idr`; `chk_quantities_source` with `from_segments` | all | 044 |
| `pir_match_lines` | all | 046 |
| `ped_ramp_corners`, `ped_ramp_attributes`; `properties.corner_id`, `chk_properties_corner_kind`, `idx_properties_corner` | all | 047 |
| `ped_ramp_inspections`; `ped_ramp_attributes.ramp_location_code`; DOT's lists in `ped_ramp_corners_entity_check`, `_curb_material_check`, `ped_ramp_attributes_dws_color_check`, `_dws_material_check` | all | 048 |
| `ped_ramp_tifs` | all | 049 |
| `ped_ramp_photos` | all | 050 |
| `ped_ramp_inspections` | `last_unlocked_at`, `last_unlocked_by` | 051 |
| `idrs` | `last_unlocked_at`, `last_unlocked_by` | 052 |
| `idr_report_sites`; `idr_reports`' `uq_idr_reports_one_dsp_per_idr`, `uq_idr_reports_report_idr` | all but `updated_at`, `updated_by` | 053 |
| `idr_report_site_pay_items`; `quantities`' `from_sites` in `chk_quantities_source`; `report_attachments.attachment_description` nullable | all | 054 |
| `idr_report_sites` | `updated_at`, `updated_by` | 055 |
| `idr_flag_attributions`; `idrs.task_order_id`, `idx_idrs_task_order`; `flags.claimed_by_idr_id`, `constructed_on`, `chk_flags_claim_consistent`, `idx_flags_claimed_by_idr`, `idx_flags_scope_rollup`; `quantities.source`, `chk_quantities_source` | all | 037 |
| `task_orders`, `task_order_properties` | all | 030 |
| `pirs` | all | 031 |
| `flags` | all but `pir_page_id` | 032 |
| `pir_pages`; `flags.pir_page_id` | all | 033 |
| `non_assessable_areas`, `features`, `segments`, `pir_annotations`; `flags.defect_codes`, `chk_flags_defect_codes`, the wider `chk_flags_non_assessable_reason`; `projects.cost_per_sf_4in`, `cost_per_sf_7in`, `chk_projects_assessment_rates` | all | 034 |
| `pirs` | `north_bearing` (and the type `icid.compass_point`) | 038 |
| `pir_annotations` | `date_notice_issued`, `date_excavated`, `date_constructed` | 038 |
| `project_users` | `role`, `chk_project_users_role`, the three-column primary key | 018 |
| `idrs` | everything except the flag | 004 |
| `idrs` | `has_dismissed_auto_general` | 006 |
| `idr_reports` | everything except the flag | 004 |
| `idr_reports` | `is_auto_generated` | 006 |
| `idr_reports` | `uq_idr_reports_one_gen_per_idr` (index) | 005 |
| `report_attachments` | everything except the three below | 008 (`storage_path` UNIQUE dropped in 010) |
| `report_attachments` | `attachment_name`, `attachment_description`, `is_uploaded` | 009 |
| `spec_items`, `contract_items` | all but the four below | 011 |
| `contract_items` | `item_no`, `description`, `unit`, `spec_section`, `uq_contract_items_project_item_budget`; `spec_item_id` nullable, the two amounts unbounded | 026 |
| `idr_audit` | all | 019 |
| `idr_field_edits` | all | 020 |
| `idr_reports` | the `id` on each `report_data.payItems` entry | 020 |
