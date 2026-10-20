import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set. Add it to your .env file.")

# Auth: sign-in tokens are JWTs signed with this key. Required: the app won't start without it.
JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")

if not JWT_SECRET_KEY:
    raise ValueError(
        "JWT_SECRET_KEY is not set. Add it to your .env file (and to the deployment's environment variables). "
        'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
    )

JWT_EXPIRY_SECONDS: int = int(os.getenv("JWT_EXPIRY_SECONDS", "86400"))
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")

# Scheduled jobs: Vercel Cron sends this as its bearer token. Unset, no scheduled call is let in.
CRON_SECRET: str = os.getenv("CRON_SECRET", "")

# Supabase Storage (attachments). Checked when the Storage client is first used, not at
# import, so the rest of the API runs without them.
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
STORAGE_BUCKET_NAME: str = os.getenv("STORAGE_BUCKET_NAME", "report-attachments")
STORAGE_URL_EXPIRY_SECONDS: int = int(os.getenv("STORAGE_URL_EXPIRY_SECONDS", "300"))

# IDR exports: each export is uploaded to this private bucket and handed out as a signed download URL
EXPORT_BUCKET_NAME: str = os.getenv("EXPORT_BUCKET_NAME", "idr-exports")
EXPORT_URL_EXPIRY_SECONDS: int = int(os.getenv("EXPORT_URL_EXPIRY_SECONDS", "600"))

# Signatures: each user's signature PNG lives in this private bucket and is shown through a short-lived signed URL
SIGNATURE_BUCKET_NAME: str = os.getenv("SIGNATURE_BUCKET_NAME", "signatures")
SIGNATURE_URL_EXPIRY_SECONDS: int = int(os.getenv("SIGNATURE_URL_EXPIRY_SECONDS", "300"))

# PIR images: each PIR's scanned sheet (a PDF is rendered to PNG) lives in this private bucket, shown through a
# signed URL. Uploads larger than PIR_IMAGE_MAX_BYTES are refused; a PDF's first page is rendered at PIR_IMAGE_RENDER_DPI
PIR_IMAGE_BUCKET_NAME: str = os.getenv("PIR_IMAGE_BUCKET_NAME", "pir-images")
PIR_IMAGE_URL_EXPIRY_SECONDS: int = int(os.getenv("PIR_IMAGE_URL_EXPIRY_SECONDS", "3600"))
PIR_IMAGE_MAX_BYTES: int = int(os.getenv("PIR_IMAGE_MAX_BYTES", str(10 * 1024 * 1024)))
PIR_IMAGE_RENDER_DPI: int = int(os.getenv("PIR_IMAGE_RENDER_DPI", "150"))
# A PIR upload's caps: how many pages, and how big a page may render (megapixels at PIR_IMAGE_RENDER_DPI)
PIR_UPLOAD_MAX_PAGES: int = int(os.getenv("PIR_UPLOAD_MAX_PAGES", "15"))
PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE: float = float(os.getenv("PIR_UPLOAD_MAX_MEGAPIXELS_PER_PAGE", "25"))

# Ped ramp exports (Pedramp Phase 3): the DOT forms filled at an inspection's approval, and the listing's bulk ZIPs,
# kept in this private bucket and handed out as signed download URLs
PEDRAMP_EXPORT_BUCKET_NAME: str = os.getenv("PEDRAMP_EXPORT_BUCKET_NAME", "pedramp-exports")
PEDRAMP_EXPORT_URL_EXPIRY_SECONDS: int = int(os.getenv("PEDRAMP_EXPORT_URL_EXPIRY_SECONDS", "600"))
