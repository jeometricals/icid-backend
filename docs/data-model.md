# ICID data model

Developer reference for the `icid` schema as of migration 031 (Slice R1a). The diagram below is the ER
diagram; it was last checked against `schema.sql` in Slice R1a.
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
projects ──< properties                        project_id; soft-deleted, unique BBL per project among live ones
projects ──< task_orders ──< task_order_properties >── properties
                                               both FKs ON DELETE CASCADE; both sides share a project
properties ──< pirs                            property_id, no cascade; one live PIR per property

users is also referenced by (none cascades):
    idrs.stage1_reviewer_uuid, idrs.re_reviewer_uuid, idrs.deleted_by
    idr_audit.actor_uuid, idr_field_edits.editor_uuid, report_attachments.uploaded_by, properties.created_by,
    task_orders.created_by, task_order_properties.added_by, pirs.digitized_by
    users.deleted_by, properties.deleted_by, task_orders.deleted_by, pirs.deleted_by,
    pirs.as_built_signed_by (ON DELETE SET NULL)
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
- **properties** are the tax lots a project's work is done on, each named by its BBL. An admin adds, edits,
  soft-deletes and restores them. See [properties](#properties).
- **task_orders** bundle a project's properties (**task_order_properties**, the join); a property can be in
  several. See [task_orders](#task_orders).
- A **PIR** is a property's digitized DOT survey sheet; a property has at most one that isn't deleted. See
  [pirs](#pirs).
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
  the `deleted_by` of users, properties, task orders and PIRs and `pirs.as_built_signed_by`, which (but for
  `users.deleted_by`) are counted all the same. So only a user who never reported, reviewed, edited, uploaded,
  held a role, created or deleted a property or a task order, bundled a property, or digitized, signed or
  deleted a PIR, can go;
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
reads it, for the status an IDR had when it was deleted; nothing else does.

| Column | Type | Notes |
|---|---|---|
| `audit_id` | UUID PK | `gen_random_uuid()` |
| `idr_id` | UUID NOT NULL | FK → `idrs.idr_id`, **ON DELETE CASCADE** |
| `actor_uuid` | UUID NOT NULL | FK → `users.uuid`. Who did it |
| `action` | TEXT NOT NULL | `submit`, `accept_stage1`, `approve_stage1`, `accept_stage2`, `approve_stage2`, `return_to_inspector`, `return_to_oe`, `admin_unlock`, `admin_delete`, `admin_undelete`, and for reviewer edits `field_edit`, `pay_item_revise`, `pay_item_add`, `pay_item_approve`, `truck_add`. No CHECK |
| `from_status`, `to_status` | TEXT | The IDR's status before and after; nullable for actions that aren't a status change. An `admin_delete` row's `from_status` is the only record of what a deleted IDR was |
| `note` | TEXT | The return comment; for a reviewer edit, `{"edit_id", "field_path"}` as JSON text |
| `created_at` | TIMESTAMPTZ NOT NULL | Default `now()` |

Index: `idx_idr_audit_idr_created (idr_id, created_at DESC)`.

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
| `report_data` | JSONB NOT NULL | Default `{}`. The form content. Its shape depends on `report_type`, and the API accepts any JSON object. |
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

- Indexes: `idx_quantities_project_date (project_id, report_date)`, `idx_quantities_project_item (project_id,
  pay_item_ref)`, `idx_quantities_project_budget (project_id, budget_code)`, `idx_quantities_idr (idr_id)`.
- No unique rule: the same item and budget code can be on one IDR more than once.
- An IDR's rows are always replaced as a set, in one statement (delete, then insert), so the copied columns never
  drift from the IDR that was approved.
- An auto-generated General's pay items are never written: they are sums of the other reports'.

### properties

A project's properties: the tax lots its work is done on, one row each, named by BBL (borough, block and lot).
Added by migration 029. Anyone on the project, in any role, and any admin reads, adds and changes them
(`/v1/projects/{id}/properties`, `/v1/properties/{id}`); only admins soft-delete and restore them. Demo users only
read (since S2).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `project_id` | TEXT NOT NULL | FK → `projects.project_id` |
| `bbl` | TEXT NOT NULL | Trimmed; matched exactly. Unique per project among properties that aren't deleted (`uq_properties_project_bbl`, `WHERE deleted_at IS NULL`) |
| `address` | TEXT NOT NULL | Trimmed |
| `property_type` | TEXT NOT NULL | `1_3_family`, `4_plus_family` or `expedited`; CHECK `chk_properties_type` |
| `notes` | TEXT | Blank is NULL |
| `created_by` | UUID NOT NULL | The signed-in user who added it: any project member or admin; FK → `users.uuid` |
| `deleted_at` | TIMESTAMPTZ | Soft delete: when an admin deleted it. NULL for one that isn't deleted |
| `deleted_by` | UUID | The admin who did; FK → `users.uuid`, **ON DELETE SET NULL** |

A soft-deleted property keeps its row and frees its BBL; un-deleting it is refused (409) while another live
property of the project has that BBL. There is no permanent delete. The unique index also serves lookups by
project, so there is no separate project index.

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
| `source_image_url` | TEXT | The scanned sheet. Not written yet (R1d) |
| `source_image_scale` | REAL | Likewise |
| `inspector_initials` | TEXT NOT NULL | Trimmed, at most 16 characters |
| `inspector_sign_date` | DATE NOT NULL | When the inspector signed the sheet |
| `digitized_by` | UUID NOT NULL | The signed-in user who created the row: any project member or admin; FK → `users.uuid` |
| `digitized_at` | TIMESTAMPTZ NOT NULL | `now()` at creation |
| `as_built_signed_by` | UUID | FK → `users.uuid`, **ON DELETE SET NULL**. Set by a later slice's sign route; no route writes it yet |
| `as_built_signed_at` | TIMESTAMPTZ | Likewise |
| `deleted_at` | TIMESTAMPTZ | Soft delete: when an admin deleted it. NULL for one that isn't deleted |
| `deleted_by` | UUID | The admin who did; FK → `users.uuid`, **ON DELETE SET NULL** |

A soft-deleted PIR frees its property, so a replacement can be created while the old one is kept; un-deleting
the old one is then refused (409). No PIR can be created on a soft-deleted property.

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
| `attachment_description` | TEXT NOT NULL | CHECK `chk_report_attachments_description`: not blank, at most 2000 characters |
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

Nothing references a contract item: pay items in reports and rows of `quantities` are copies,
so deleting one only takes it out of the picker.

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
| `DSP` | Daily Site Patrol | Either (deliberately not listed) |
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
the General:

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
| `task_orders`, `task_order_properties` | all | 030 |
| `pirs` | all | 031 |
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
