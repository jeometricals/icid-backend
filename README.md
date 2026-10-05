
# ICID Reporting API (Skeleton)

## Run (dev)
1) Create Postgres locally and set the required env vars in `.env` or the environment (the app won't start without
   them; on Vercel they go under the project's Environment Variables):
   - `DATABASE_URL`
   - `JWT_SECRET_KEY`: signs sign-in tokens. Generate one with
     `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Optional alongside it: `JWT_EXPIRY_SECONDS`
     (default 86400) and `JWT_ALGORITHM` (default HS256).
2) Install deps: `pip install -r requirements.txt` (includes Pillow and pillow-heif, which the IDR export uses to shrink
   attachment photos and convert HEIC / WebP; about 45 MB unzipped on Linux)
3) Start API: `uvicorn app.main:app --reload`
3.05) Start API: `uvicorn main:app --reload`
3.1) Start API: `uvicorn api.index:app --reload`
4) Open docs: http://localhost:8000/docs

## Supabase Storage setup (one-time)
Two private buckets, created by hand (the backend never creates buckets):
- `report-attachments`: report attachments (migration 008's notes).
- `idr-exports`: exported IDRs. Run `migrations/012_idr_exports_bucket.sql` in the Supabase SQL editor, or in the
  dashboard: Storage -> New bucket -> `idr-exports`, Public off. Old exports are never deleted yet.

## Notes
- Async SQLAlchemy 2.0 + asyncpg
- Tenants & Clients modeled; extend with Projects, Reports, Workflow next
- Add Alembic migrations later: `pip install alembic` and `alembic init alembic`
