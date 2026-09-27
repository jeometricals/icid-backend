from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class Attachment(BaseModel):
    attachment_id: UUID
    file_name: str
    file_type: str
    file_size_bytes: int
    uploaded_by: UUID
    uploaded_at: datetime


class AttachmentResponse(BaseModel):
    status: str
    message: str
    data: Attachment


class AttachmentListResponse(BaseModel):
    status: str
    message: str
    data: list[Attachment]


class DownloadUrl(BaseModel):
    download_url: str
    expires_at: datetime


class DownloadUrlResponse(BaseModel):
    status: str
    message: str
    data: DownloadUrl
