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
    attachment_name: str
    attachment_description: str


class AttachmentResponse(BaseModel):
    status: str
    message: str
    data: Attachment


class AttachmentListResponse(BaseModel):
    status: str
    message: str
    data: list[Attachment]


class UploadRequestBody(BaseModel):
    file_name: str
    file_type: str
    file_size_bytes: int
    attachment_name: str
    attachment_description: str


class UploadCompleteBody(BaseModel):
    attachment_id: UUID


class UpdateAttachmentMetadataBody(BaseModel):
    attachment_name: str
    attachment_description: str


class UploadRequest(Attachment):
    storage_path: str
    upload_url: str
    upload_url_expires_at: datetime
    upload_headers: dict[str, str]


class UploadRequestResponse(BaseModel):
    status: str
    message: str
    data: UploadRequest


class DownloadUrl(BaseModel):
    download_url: str
    expires_at: datetime


class DownloadUrlResponse(BaseModel):
    status: str
    message: str
    data: DownloadUrl
