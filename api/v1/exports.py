from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.schemas.export import ExportLink
from api.services.auth import current_user
from api.services.export import (
    ExportDataError,
    ExportError,
    ExportStorageError,
    IdrNotFoundError,
    publish_idr_export,
)

# Needs a signed-in user (any role): any of them may export any IDR
router = APIRouter(prefix="/v1/idrs", tags=["Export"], dependencies=[Depends(current_user)])

# HTTP status for each export failure (Storage failing is a bad gateway, as for attachments)
ERROR_STATUS = {
    IdrNotFoundError: 404,
    ExportDataError: 500,
    ExportStorageError: 502,
}


@router.get("/{idr_id}/export", response_model=ExportLink)
def export_idr(idr_id: UUID) -> ExportLink:
    """
    Export an IDR as an .xlsx file on the DDC report-forms template (a draft's pages are marked as a draft), stored
    in the exports bucket. Takes the IDR uuid as a path parameter.
    Returns {download_url, filename}, the URL valid for 10 minutes; raises 404 (no such IDR), 500 and 502 (Storage).
    """
    try:
        published = publish_idr_export(idr_id)
    except ExportError as exc:
        raise HTTPException(status_code=ERROR_STATUS.get(type(exc), 500), detail=str(exc))
    return ExportLink(download_url=published.download_url, filename=published.filename)
