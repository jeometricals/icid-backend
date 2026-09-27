from functools import lru_cache

from supabase import Client, create_client

from api.core.config import STORAGE_BUCKET_NAME, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL


@lru_cache(maxsize=1)
def get_client() -> Client:
    """
    Create the Supabase client once per process, authenticated with the service key.
    Takes nothing; reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from config.
    Returns the client, or raises RuntimeError if either setting is missing.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to use attachments.")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _bucket():
    """
    Give the Storage API for the attachments bucket.
    Takes nothing; the bucket name comes from STORAGE_BUCKET_NAME.
    Returns the bucket's file API.
    """
    return get_client().storage.from_(STORAGE_BUCKET_NAME)


def upload_file(path: str, content: bytes, content_type: str) -> None:
    """
    Store a file in the attachments bucket, refusing to overwrite an existing object.
    Takes the object path, the file bytes and its MIME type.
    Returns nothing; raises the Storage client's error on failure.
    """
    _bucket().upload(path, content, {"content-type": content_type, "upsert": "false"})


def remove_files(paths: list[str]) -> None:
    """
    Delete objects from the attachments bucket; paths that don't exist are ignored by Storage.
    Takes a list of object paths.
    Returns nothing; raises the Storage client's error on failure.
    """
    if paths:
        _bucket().remove(paths)


def create_signed_url(path: str, expires_in: int, download_name: str) -> str:
    """
    Make a time-limited URL for one object that downloads it under the given file name.
    Takes the object path, the lifetime in seconds and the name the browser should save it as.
    Returns the signed URL, or raises RuntimeError if Storage returned none.
    """
    result = _bucket().create_signed_url(path, expires_in, {"download": download_name})
    url = result.get("signedURL")
    if not url:
        raise RuntimeError(f"Storage returned no signed URL for {path}")
    return url
