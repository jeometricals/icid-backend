from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from api.queries.idr_reports import get_idr_report
from api.queries.idrs import get_idr_by_id
from api.queries.projects import is_user_on_project
from api.queries.report_attachments import get_attachment
from api.queries.users import get_user_by_id
from api.schemas.report_attachment import (
    Attachment,
    AttachmentListResponse,
    AttachmentResponse,
    DownloadUrl,
    DownloadUrlResponse,
)
from api.services.attachments import (
    MAX_FILE_SIZE_BYTES,
    AttachmentError,
    AttachmentNotAllowedOnAutoGeneralError,
    EmptyFileError,
    FileTooLargeError,
    InvalidUserError,
    StorageUnavailableError,
    UPLOADER_NOT_FOUND,
    UnsupportedFileTypeError,
    delete_attachment,
    get_download_url,
    list_attachments,
    upload_attachment,
)

router = APIRouter(prefix="/v1/idrs", tags=["Attachments"])

# HTTP status for each error the attachments service can raise.
ERROR_STATUS: dict[type[AttachmentError], int] = {
    AttachmentNotAllowedOnAutoGeneralError: 400,
    EmptyFileError: 400,
    InvalidUserError: 400,
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
    Fetch one attachment on a report.
    Takes the report and attachment uuids from the path.
    Returns the attachment row; raises 404 if the report has no such attachment.
    """
    attachment = get_attachment(report_id, attachment_id)

    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    return attachment


@router.post("/{idr_id}/reports/{report_id}/attachments", response_model=AttachmentResponse, status_code=201)
def upload_report_attachment(
    idr_id: UUID,
    report_id: UUID,
    file: UploadFile = File(...),
    uploaded_by: UUID = Form(...),
) -> AttachmentResponse:
    """
    Attach a file (multipart form: file + uploaded_by) to a report on a draft IDR.
    Takes the IDR and report uuids as path parameters and the uploaded file and uploader uuid as form fields.
    Returns an AttachmentResponse; raises 404, 409 (not draft), 400 (unknown uploader, auto-General or empty), 403 (uploader not on project), 413, 415 and 502.
    """
    idr, report = _load_report(idr_id, report_id)
    _require_draft(idr)

    if get_user_by_id(str(uploaded_by)) is None:
        raise HTTPException(status_code=400, detail=UPLOADER_NOT_FOUND)

    if not is_user_on_project(uploaded_by, idr["project_id"]):
        raise HTTPException(status_code=403, detail="Uploader is not assigned to this project")

    # One byte past the limit is enough to know the file is too large without reading it all.
    content = file.file.read(MAX_FILE_SIZE_BYTES + 1)

    try:
        row = upload_attachment(report, file.filename, file.content_type, content, uploaded_by)
    except AttachmentError as exc:
        raise _http_error(exc) from exc

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save attachment")

    return AttachmentResponse(
        status="success",
        message="Attachment uploaded",
        data=Attachment.model_validate(row),
    )


@router.get("/{idr_id}/reports/{report_id}/attachments", response_model=AttachmentListResponse)
def list_report_attachments(idr_id: UUID, report_id: UUID) -> AttachmentListResponse:
    """
    List a report's attachments, oldest upload first; works on draft and submitted IDRs.
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
    Returns a DownloadUrlResponse with download_url and expires_at; raises 404 and 502.
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
    Remove an attachment from a report on a draft IDR: its Storage file (best-effort), then its record.
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
