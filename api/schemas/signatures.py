from typing import Literal

from pydantic import BaseModel


class UploadRequest(BaseModel):
    """What the client is about to upload as its signature: only PNG is taken."""

    content_type: Literal["image/png"]


class UploadResponse(BaseModel):
    """Where to PUT the signature PNG (with Content-Type image/png), the path it lands at, and the URL's lifetime in seconds."""

    upload_url: str
    storage_path: str
    expires_in: int


class ConfirmRequest(BaseModel):
    """How the uploaded signature was made."""

    signature_type: Literal["drawn", "uploaded"]
