# ICID Backend

The API behind **ICID (Integrated Construction Information Database)**, a construction inspection reporting
platform: inspectors sign in, file Inspector Daily Reports (IDRs) against their projects, attach photos, sign and
submit them, and export them onto the DDC report forms.

**Stack:** Python, FastAPI, psycopg 3, Supabase Postgres (tables in the `icid` schema), Supabase Storage. Deployed on
Vercel as a serverless function; `api/index.py` is the entrypoint.

For how the code is organised and the rules for changing it, read [`CLAUDE.md`](CLAUDE.md). For the tables, the
Storage buckets and the migration history, read [`docs/data-model.md`](docs/data-model.md).

## Run it locally

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.index:app --reload
```

The API is then at http://localhost:8000, with interactive docs at http://localhost:8000/docs.

Settings come from the environment, or from a `.env` file in the project root (never committed). The app refuses to
start without the two required ones.

## Environment variables

| Variable | Required | Default | What it is |
|---|---|---|---|
| `DATABASE_URL` | yes | | Postgres connection string for the Supabase database (`postgresql://user:password@host:5432/dbname`) |
| `JWT_SECRET_KEY` | yes | | Signs sign-in tokens. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `JWT_EXPIRY_SECONDS` | no | `86400` | How long a sign-in token lasts |
| `JWT_ALGORITHM` | no | `HS256` | Token signing algorithm |
| `SUPABASE_URL` | for Storage | | The Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | for Storage | | Service role key; the backend is the only thing that reads or writes the buckets |
| `STORAGE_BUCKET_NAME` | no | `report-attachments` | Bucket for report attachments |
| `STORAGE_URL_EXPIRY_SECONDS` | no | `300` | Lifetime of an attachment's signed download URL |
| `EXPORT_BUCKET_NAME` | no | `idr-exports` | Bucket for exported IDRs |
| `EXPORT_URL_EXPIRY_SECONDS` | no | `600` | Lifetime of an export's signed download URL |
| `SIGNATURE_BUCKET_NAME` | no | `signatures` | Bucket for signatures |
| `SIGNATURE_URL_EXPIRY_SECONDS` | no | `300` | Lifetime of a signature's signed URL |
| `CRON_SECRET` | for the daily cleanup | | What Vercel Cron sends as its bearer token; without it the scheduled demo cleanup is refused |

The two Supabase settings are only checked when Storage is first used, so everything that doesn't touch attachments,
exports or signatures runs without them.

## Database

`schema.sql` is the authoritative definition of the `icid` schema. Changes ship as numbered files in `migrations/`,
**run by hand in the Supabase SQL editor**, each with the matching edit to `schema.sql`. Every migration carries its
own pre-checks and verification queries as comments, and is safe to re-run.

Seed data, also run in the SQL editor, in this order:

1. `seed.sql`: mock clients, users, projects and assignments
2. `seed_sidewalk_pay_items.sql`: the pay-item catalog and one project's bid items
3. `seed_test_project.sql`: the demo project, `DEMO01`
4. `seed_auth_users.sql`: the admin account and its project assignments

## Storage

Three private buckets, created by hand or by their migration (the backend never creates buckets):

- `report-attachments`: report attachments (see migration 008's notes)
- `idr-exports`: exported IDRs (`migrations/012_idr_exports_bucket.sql`)
- `signatures`: user signatures and each submitted IDR's copy (`migrations/015b_signatures_bucket.sql`)

## Tests

```bash
python -m pytest
```

The tests don't touch the database or Storage: they patch the query layer and the Storage client. The full suite
takes a few minutes, most of it the export tests; `python -m pytest --deselect tests/v1/test_exports.py` skips those.

## Deployment

Vercel deploys automatically from the `main` branch of `jeometricals/icid-backend`. `vercel.json` sends every request
to `api/index.py` and schedules the daily demo cleanup (`/v1/admin/cleanup-demos`, 03:00 UTC). Environment variables
are set in the Vercel project's settings. A database migration is not part of a deploy: run it in Supabase first
when the code depends on it.
