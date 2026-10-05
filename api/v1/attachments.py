from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from api.queries.idr_reports import get_idr_report
from api.queries.idrs import get_idr_by_id
from api.queries.projects import is_user_on_project
from api.queries.report_attachments import get_attachment
from api.schemas.auth import UserOut
from api.schemas.report_attachment import (
    Attachment,
    AttachmentListResponse,
    AttachmentResponse,
    DownloadUrl,
    DownloadUrlResponse,
    UpdateAttachmentMetadataBody,
    UploadCompleteBody,
    UploadRequest,
    UploadRequestBody,
    UploadRequestResponse,
)
from api.services.attachments import (
    AttachmentError,
    AttachmentNotAllowedOnAutoGeneralError,
    AttachmentNotUploadedError,
    EmptyFileError,
    FileTooLargeError,
    IdrNotDraftError,
    InvalidAttachmentMetadataError,
    InvalidUserError,
    StorageUnavailableError,
    UnsupportedFileTypeError,
    delete_attachment,
    get_download_url,
    list_attachments,
    update_attachment_metadata,
    upload_complete,
    upload_request,
)
from api.services.auth import current_user

# Every route needs a signed-in user (any role)
router = APIRouter(prefix="/v1/idrs", tags=["Attachments"], dependencies=[Depends(current_user)])

# HTTP status for each error the attachments service can raise.
ERROR_STATUS: dict[type[AttachmentError], int] = {
    AttachmentNotAllowedOnAutoGeneralError: 400,
    EmptyFileError: 400,
    InvalidAttachmentMetadataError: 400,
    InvalidUserError: 400,
    AttachmentNotUploadedError: 404,
    IdrNotDraftError: 409,
    FileTooLargeError: 413,
    UnsupportedFileTypeError: 415,
    StorageUnavailableError: 502,
}


def _http_error(exc: AttachmentError) -> HTTPException:
    """
    Translate an attachments service error into the HTTP error the client sees.
    Takes the raised AttachmentError subclass.
    Returns an HTTPException with that error's status (500 if unmapped) and its message as detail.
    """
    return HTTPException(status_code=ERROR_STATUS.get(type(exc), 500), detail=str(exc))


def _load_report(idr_id: UUID, report_id: UUID) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Fetch an IDR and one of its reports for an attachment request.
    Takes the IDR and report uuids from the path.
    Returns (idr, report) rows; raises 404 if the IDR doesn't exist or the report isn't in it.
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    report = get_idr_report(idr_id, report_id)

    if report is None:
        raise HTTPException(status_code=404, detail="Report not found in this IDR")

    return idr, report


def _require_draft(idr: dict[str, Any]) -> None:
    """
    Refuse changes to a submitted IDR's attachments.
    Takes the IDR row.
    Returns nothing; raises 409 unless the IDR is a draft.
    """
    if idr["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only draft IDRs can be edited")


def _load_attachment(report_id: UUID, attachment_id: UUID) -> dict[str, Any]:
    """
    Fetch one attachment on a report, pending or uploaded.
    Takes the report and attachment uuids from the path.
    Returns the attachment row; raises 404 if the report has no such attachment.
    """
    attachment = get_attachment(report_id, attachment_id)

    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    return attachment


def _single_attachment(rows: Optional[list[dict[str, Any]]], failure: str) -> Attachment:
    """
    Turn a one-row attachment query result into the response model.
    Takes the query result and the 500 message to use if it failed.
    Returns the Attachment; raises 500 if the result is None, 404 if it is empty.
    """
    if rows is None:
        raise HTTPException(status_code=500, detail=failure)

    if not rows:
        raise HTTPException(status_code=404, detail="Attachment not found")

    return Attachment.model_validate(rows[0])


@router.post(
    "/{idr_id}/reports/{report_id}/attachments/upload-request",
    response_model=UploadRequestResponse,
    status_code=201,
)
def request_attachment_upload(
    idr_id: UUID, report_id: UUID, body: UploadRequestBody, user: UserOut = Depends(current_user)
) -> UploadRequestResponse:
    """
    Start uploading a file to a report on a draft IDR: records a pending attachment, uploaded by the signed-in user, and returns a signed URL to upload the file to.
    Takes the IDR and report uuids as path parameters, an UploadRequestBody and the signed-in user.
    Returns an UploadRequestResponse; raises 404, 409 (not draft), 400 (auto-General, empty file, blank or long name/description), 403 (uploader not on project), 413, 415, 500 and 502.
    """
    idr, report = _load_report(idr_id, report_id)
    _require_draft(idr)

    if not is_user_on_project(user.uuid, idr["project_id"]):
        raise HTTPException(status_code=403, detail="Uploader is not assigned to this project")

    try:
        row = upload_request(
            report,
            user.uuid,
            body.file_name,
            body.file_type,
            body.file_size_bytes,
            body.attachment_name,
            body.attachment_description,
        )
    except AttachmentError as exc:
        raise _http_error(exc) from exc

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save attachment")

    return UploadRequestResponse(
        status="success",
        message="Upload URL created",
        data=UploadRequest.model_validate(row),
    )


@router.post("/{idr_id}/reports/{report_id}/attachments/upload-complete", response_model=AttachmentResponse)
def complete_attachment_upload(idr_id: UUID, report_id: UUID, body: UploadCompleteBody) -> AttachmentResponse:
    """
    Mark a pending attachment on a draft IDR as uploaded, once the client has stored its file at the signed URL.
    Takes the IDR and report uuids as path parameters and an UploadCompleteBody.
    Returns an AttachmentResponse; raises 404, 409 (not draft) and 500.
    """
    idr, _ = _load_report(idr_id, report_id)
    _require_draft(idr)

    rows = upload_complete(report_id, body.attachment_id)

    return AttachmentResponse(
        status="success",
        message="Attachment uploaded",
        data=_single_attachment(rows, "Failed to complete upload"),
    )


@router.put("/{idr_id}/reports/{report_id}/attachments/{attachment_id}", response_model=AttachmentResponse)
def update_report_attachment(
    idr_id: UUID,
    report_id: UUID,
    attachment_id: UUID,
    body: UpdateAttachmentMetadataBody,
) -> AttachmentResponse:
    """
    Replace an attachment's name and description on a draft IDR.
    Takes the IDR, report and attachment uuids as path parameters and an UpdateAttachmentMetadataBody.
    Returns an AttachmentResponse; raises 404, 409 (not draft), 400 (blank or long name/description) and 500.
    """
    idr, _ = _load_report(idr_id, report_id)
    _load_attachment(report_id, attachment_id)

    try:
        rows = update_attachment_metadata(
            idr, report_id, attachment_id, body.attachment_name, body.attachment_description
        )
    except AttachmentError as exc:
        raise _http_error(exc) from exc

    return AttachmentResponse(
        status="success",
        message="Attachment updated",
        data=_single_attachment(rows, "Failed to update attachment"),
    )


@router.get("/{idr_id}/reports/{report_id}/attachments", response_model=AttachmentListResponse)
def list_report_attachments(idr_id: UUID, report_id: UUID) -> AttachmentListResponse:
    """
    List a report's uploaded attachments, oldest upload first; works on draft and submitted IDRs.
    Takes the IDR and report uuids as path parameters.
    Returns an AttachmentListResponse (empty data list if none); raises 404 and 500.
    """
    _load_report(idr_id, report_id)

    rows = list_attachments(report_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to list attachments")

    return AttachmentListResponse(
        status="success",
        message=f"{len(rows)} attachment(s)",
        data=[Attachment.model_validate(row) for row in rows],
    )


@router.get(
    "/{idr_id}/reports/{report_id}/attachments/{attachment_id}/download-url",
    response_model=DownloadUrlResponse,
)
def get_attachment_download_url(idr_id: UUID, report_id: UUID, attachment_id: UUID) -> DownloadUrlResponse:
    """
    Issue a short-lived signed URL the frontend can fetch the file from; works on draft and submitted IDRs.
    Takes the IDR, report and attachment uuids as path parameters.
    Returns a DownloadUrlResponse with download_url and expires_at; raises 404 (including a pending upload) and 502.
    """
    _load_report(idr_id, report_id)
    attachment = _load_attachment(report_id, attachment_id)

    try:
        url = get_download_url(attachment)
    except AttachmentError as exc:
        raise _http_error(exc) from exc

    return DownloadUrlResponse(
        status="success",
        message="Download URL created",
        data=DownloadUrl.model_validate(url),
    )


@router.delete(
    "/{idr_id}/reports/{report_id}/attachments/{attachment_id}",
    status_code=204,
    response_class=Response,
)
def delete_report_attachment(idr_id: UUID, report_id: UUID, attachment_id: UUID) -> Response:
    """
    Remove an attachment, pending or uploaded, from a report on a draft IDR: its Storage file (best-effort), then its record.
    Takes the IDR, report and attachment uuids as path parameters.
    Returns an empty 204; raises 404, 409 (not draft) and 500.
    """
    idr, _ = _load_report(idr_id, report_id)
    _require_draft(idr)
    attachment = _load_attachment(report_id, attachment_id)

    rows = delete_attachment(report_id, attachment)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to delete attachment")

    if not rows:
        raise HTTPException(status_code=404, detail="Attachment not found")

    return Response(status_code=204)
