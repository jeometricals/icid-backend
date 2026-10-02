# ICID data model

Developer reference for the `icid` schema as of migration 011 (Slice F, pay-item catalog).
`schema.sql` is the authoritative DDL; this doc explains it. If the two ever disagree,
`schema.sql` wins and this file needs fixing.

All tables live in the `icid` schema. Every table except the two join tables has
`created_at` / `updated_at` (`TIMESTAMPTZ NOT NULL DEFAULT now()`); they're left out of the
column lists below.

## The big picture

```
clients ──< users ──< project_users >── projects ──< project_clients >── clients
                │                          │
                │ reporter_uuid            │ project_id
                └──────────< idrs >────────┘
                              │
                              │ idr_id (ON DELETE CASCADE)
                              ▼
                         idr_reports ──┐
                              ▲  │     │ parent_report_id (addendum → parent,
                              └──┼─────┘  ON DELETE CASCADE)
                                 │ report_id (ON DELETE CASCADE)
                                 ▼
                         report_attachments >── users (uploaded_by)

projects ──< contract_items >── spec_items      (project_id ON DELETE CASCADE)
```

- A **client** is an organisation. Every **user** belongs to one client.
- **Projects** are construction contracts. Users are assigned to projects through
  **project_users**; extra organisations on a project go through **project_clients**.
- An **IDR** (Inspector Daily Diary) is one inspector's diary for one project on one day. It
  holds the header fields shared by every page of that day's paperwork.
- **idr_reports** are the typed reports filed inside an IDR: a General, a Sewer report,
  addenda, and so on. Each one's form content is a JSON blob in `report_data`.
- **report_attachments** are files (photos, PDFs) attached to a report. The row is metadata;
  the bytes live in Supabase Storage.
- **spec_items** is the shared NYCDOT pay-item catalog. **contract_items** is one project's
  Schedule of Bid Items: which spec items it pays for, under which budget codes, at what
  quantity and price. See [Pay items](#pay-items).

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

People who sign in: inspectors, CCLs, engineers.

| Column | Type | Notes |
|---|---|---|
| `uuid` | UUID PK | `uuid_generate_v4()`. The API calls it `user_id`. |
| `email` | TEXT NOT NULL | Not unique-constrained |
| `first_name`, `last_name` | TEXT | |
| `phone_number` | TEXT | |
| `client_id` | TEXT NOT NULL | FK → `clients.client_id` (the user's employer) |

### projects

Construction contracts.

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

Assigns users to projects. `GET /v1/projects/?user_id=` reads through this table.

| Column | Type | Notes |
|---|---|---|
| `project_id` | TEXT | PK part; FK → `projects.project_id` |
| `user_uuid` | UUID | PK part; FK → `users.uuid` |
| `user_role` | TEXT | The user's role on this project, e.g. `Inspector`, `CCL` |
| `assigned_at` | TIMESTAMPTZ NOT NULL | Default `now()` |

Primary key: `(project_id, user_uuid)`, so a user holds one role per project.

### project_clients

Links additional organisations to a project.

| Column | Type | Notes |
|---|---|---|
| `project_id` | TEXT | PK part; FK → `projects.project_id` |
| `client_id` | TEXT | PK part; FK → `clients.client_id` |
| `client_role` | TEXT | That organisation's role on the project |

No endpoint reads this table yet.

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
| `status` | TEXT NOT NULL | `draft` (default) or `submitted`; CHECK `chk_idrs_status` |
| `submitted_at` | TIMESTAMPTZ | NULL until submitted |

Constraints and indexes:
- `uq_idrs_project_reporter_date UNIQUE (project_id, reporter_uuid, report_date)`: one IDR per
  inspector per project per day. `POST /v1/idrs/` returns 409 with `existing_idr_id` when it
  would be violated.
- `idx_idrs_project_status (project_id, status)` and `idx_idrs_reporter (reporter_uuid)`.

Lifecycle: an IDR is created as a `draft`, and its header and reports can be edited freely.
`POST /v1/idrs/{id}/submit` locks it in one statement. It sets `status = 'submitted'`,
`submitted_at`, and `total_pages`, and numbers every report's `page_number`. After that the API
refuses edits (409).

### idr_reports

The typed reports inside an IDR.

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
at it.

Known polish item: in the IDR export, the "Attachment unavailable" page (a photo the export couldn't
fetch or read) still uses a small 10 pt note, unlike the PDF page's larger title and "no preview" box.

### Export files (Storage bucket `idr-exports`)

Not a table. `GET /v1/idrs/{idr_id}/export` builds the IDR's `.xlsx`, uploads it to the private
bucket `idr-exports` at `{idr_id}/{YYYYMMDD_HHMMSS}_{file name}` (UTC; every export is a new object)
and returns a signed download URL valid for 10 minutes. Only the backend reaches the bucket
(service key). Nothing removes old exports yet.

### spec_items

The standard NYCDOT pay-item catalog, shared by every project. Added in migration 011.

| Column | Type | Notes |
|---|---|---|
| `spec_item_id` | UUID PK | `uuid_generate_v4()` |
| `item_no` | TEXT NOT NULL | e.g. `4.13 AAS`. UNIQUE (`uq_spec_items_item_no`) |
| `description` | TEXT NOT NULL | e.g. `4" Concrete Sidewalk (Unpigmented)` |
| `spec_section` | TEXT NOT NULL | e.g. `4.13` |
| `pay_unit` | TEXT NOT NULL | e.g. `S.F.`, `Ton`, `Each` |

### contract_items

A project's Schedule of Bid Items. "Contract" is the project here; there is no separate
contracts table. Added in migration 011. Seeded only (`seed_sidewalk_pay_items.sql`, for
`HWS0023`); there is no write endpoint yet.

| Column | Type | Notes |
|---|---|---|
| `contract_item_id` | UUID PK | `uuid_generate_v4()` |
| `project_id` | TEXT NOT NULL | FK → `projects.project_id`, **ON DELETE CASCADE** |
| `spec_item_id` | UUID NOT NULL | FK → `spec_items.spec_item_id` |
| `budget_code` | TEXT NOT NULL | |
| `bid_quantity` | NUMERIC(12,2) NOT NULL | |
| `bid_unit_price` | NUMERIC(12,2) NOT NULL | |

`UNIQUE (project_id, spec_item_id, budget_code)` (`uq_contract_items_project_spec_budget`):
the same spec item can appear under several budget codes in one project, but only once per
code. Indexes: `idx_contract_items_project (project_id)`, `idx_contract_items_spec_item (spec_item_id)`.

`GET /v1/contract_items/?project_id=` returns a project's rows joined to their spec item, so
each carries `item_no`, `description`, `spec_section` and `pay_unit` inline.

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

Where each current column came from:

| Table | Columns | Added in |
|---|---|---|
| `clients`, `users`, `projects`, `project_users`, `project_clients`, `form_templates` | all | Baseline |
| `idrs` | everything except the flag | 004 |
| `idrs` | `has_dismissed_auto_general` | 006 |
| `idr_reports` | everything except the flag | 004 |
| `idr_reports` | `is_auto_generated` | 006 |
| `idr_reports` | `uq_idr_reports_one_gen_per_idr` (index) | 005 |
| `report_attachments` | everything except the three below | 008 (`storage_path` UNIQUE dropped in 010) |
| `report_attachments` | `attachment_name`, `attachment_description`, `is_uploaded` | 009 |
| `spec_items`, `contract_items` | all | 011 |
