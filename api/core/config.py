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
