import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set. Add it to your .env file.")

# Supabase Storage (attachments). Checked when the Storage client is first used, not at
# import, so the rest of the API runs without them.
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
STORAGE_BUCKET_NAME: str = os.getenv("STORAGE_BUCKET_NAME", "report-attachments")
STORAGE_URL_EXPIRY_SECONDS: int = int(os.getenv("STORAGE_URL_EXPIRY_SECONDS", "300"))

# IDR exports: each export is uploaded to this private bucket and handed out as a signed download URL
EXPORT_BUCKET_NAME: str = os.getenv("EXPORT_BUCKET_NAME", "idr-exports")
EXPORT_URL_EXPIRY_SECONDS: int = int(os.getenv("EXPORT_URL_EXPIRY_SECONDS", "600"))
