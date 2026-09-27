# ICID data model

Developer reference for the `icid` schema as of migration 007 (end of Phase R).
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
                              ▲        │ parent_report_id (addendum → parent,
                              └────────┘  ON DELETE CASCADE)
```

- A **client** is an organisation. Every **user** belongs to one client.
- **Projects** are construction contracts. Users are assigned to projects through
  **project_users**; extra organisations on a project go through **project_clients**.
- An **IDR** (Inspector Daily Diary) is one inspector's diary for one project on one day. It
  holds the header fields shared by every page of that day's paperwork.
- **idr_reports** are the typed reports filed inside an IDR: a General, a Sewer report,
  addenda, and so on. Each one's form content is a JSON blob in `report_data`.

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
| `AC` | Asphalt Concrete | |
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

## Auto-generated General

Added in Phase R, Slice R4a. The General is the IDR's summary page. When an inspector files
several reports but no General, the backend builds one for them. The logic lives in
`api/services/auto_general.py` and runs after every report create, update or delete.

The two flags:

- **`idr_reports.is_auto_generated`**: `true` marks a General the backend owns. Its
  `report_data` is exactly `{description, payItems}`. The description aggregates each
  contributing report under its type label and ends with a fixed footer. The backend fully
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

Where each current column came from:

| Table | Columns | Added in |
|---|---|---|
| `clients`, `users`, `projects`, `project_users`, `project_clients`, `form_templates` | all | Baseline |
| `idrs` | everything except the flag | 004 |
| `idrs` | `has_dismissed_auto_general` | 006 |
| `idr_reports` | everything except the flag | 004 |
| `idr_reports` | `is_auto_generated` | 006 |
| `idr_reports` | `uq_idr_reports_one_gen_per_idr` (index) | 005 |
