from functools import lru_cache

from storage3.exceptions import StorageApiError
from storage3.types import CreateSignedUploadUrlOptions
from supabase import Client, ClientOptions, create_client

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


# Each Storage request a download makes gives up after this long (the export fetches photos server-side)
DOWNLOAD_TIMEOUT_SECONDS = 10


@lru_cache(maxsize=1)
def _download_client() -> Client:
    """
    Create a second Supabase client once per process for downloads, whose Storage requests time out after
    DOWNLOAD_TIMEOUT_SECONDS (the main client keeps the library's default).
    Takes nothing; reads the same settings as get_client.
    Returns the client, or raises RuntimeError if a setting is missing.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to use attachments.")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
                         ClientOptions(storage_client_timeout=DOWNLOAD_TIMEOUT_SECONDS))


def _bucket(bucket: str = STORAGE_BUCKET_NAME):
    """
    Give the Storage API for one bucket.
    Takes the bucket name (the attachments bucket, STORAGE_BUCKET_NAME, unless given).
    Returns the bucket's file API.
    """
    return get_client().storage.from_(bucket)


def create_signed_upload_url(path: str, bucket: str = STORAGE_BUCKET_NAME, upsert: bool = False) -> str:
    """
    Make a signed URL the client can upload one object to directly, without other credentials.
    Takes the object path the file must be stored at, the bucket (the attachments bucket unless given) and whether
    the upload may replace an object already at that path.
    Returns the signed upload URL, or raises RuntimeError if Storage returned none.
    """
    if upsert:
        result = _bucket(bucket).create_signed_upload_url(path, CreateSignedUploadUrlOptions(upsert="true"))
    else:
        result = _bucket(bucket).create_signed_upload_url(path)
    url = result.get("signed_url") or result.get("signedUrl")
    if not url:
        raise RuntimeError(f"Storage returned no signed upload URL for {path}")
    return url


def remove_files(paths: list[str]) -> None:
    """
    Delete objects from the attachments bucket; paths that don't exist are ignored by Storage.
    Takes a list of object paths.
    Returns nothing; raises the Storage client's error on failure.
    """
    if paths:
        _bucket().remove(paths)


def create_signed_url(path: str, expires_in: int, download_name: str, bucket: str = STORAGE_BUCKET_NAME) -> str:
    """
    Make a time-limited URL for one object that downloads it under the given file name.
    Takes the object path, the lifetime in seconds, the name the browser should save it as and the bucket (the
    attachments bucket unless given).
    Returns the signed URL, or raises RuntimeError if Storage returned none.
    """
    result = _bucket(bucket).create_signed_url(path, expires_in, {"download": download_name})
    url = result.get("signedURL")
    if not url:
        raise RuntimeError(f"Storage returned no signed URL for {path}")
    return url


def upload_file(bucket: str, path: str, data: bytes, content_type: str) -> None:
    """
    Store bytes as one object, server-side, replacing any object already at that path.
    Takes the bucket, the object path, the bytes and their MIME type.
    Returns nothing; raises the Storage client's error on failure.
    """
    _bucket(bucket).upload(path, data, {"content-type": content_type, "upsert": "true"})


def download_file(path: str) -> bytes:
    """
    Fetch one object's bytes from the attachments bucket, server-side (no signed URL).
    Takes the object path.
    Returns the file's bytes; raises the Storage client's error, or a timeout, when it can't be fetched.
    """
    return _download_client().storage.from_(STORAGE_BUCKET_NAME).download(path)


def copy_file(bucket: str, from_path: str, to_path: str) -> None:
    """
    Copy one object to a new path in the same bucket, server-side. Fails if an object is already at the new path.
    Takes the bucket, the object's path and the path of the copy.
    Returns nothing; raises the Storage client's error on failure.
    """
    _bucket(bucket).copy(from_path, to_path)


def object_exists(bucket: str, path: str) -> bool:
    """
    Check whether an object is in a bucket (a HEAD request; the file isn't fetched).
    Takes the bucket and the object path.
    Returns True if it is there, False if Storage says it isn't; raises the Storage client's error on any other
    failure.
    """
    try:
        return bool(_bucket(bucket).exists(path))
    except StorageApiError as exc:
        if str(exc.status) in ("400", "404"):  # Storage answers either for a missing object
            return False
        raise
