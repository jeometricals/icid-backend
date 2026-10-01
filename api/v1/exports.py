from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api.services.export import (
    ExportDataError,
    ExportError,
    IdrNotFoundError,
    IdrNotSubmittedError,
    generate_idr_export,
)

router = APIRouter(prefix="/v1/idrs", tags=["Export"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# HTTP status for each export failure
ERROR_STATUS = {
    IdrNotFoundError: 404,
    IdrNotSubmittedError: 409,
    ExportDataError: 500,
}


@router.get("/{idr_id}/export", response_class=Response)
def export_idr(idr_id: UUID) -> Response:
    """
    Download a submitted IDR as an .xlsx file on the DDC report-forms template.
    Takes the IDR uuid as a path parameter.
    Returns the file as an attachment; raises 404 (no such IDR), 409 (still a draft) and 500.
    """
    try:
        export = generate_idr_export(idr_id)
    except ExportError as exc:
        raise HTTPException(status_code=ERROR_STATUS.get(type(exc), 500), detail=str(exc))

    return Response(
        content=export.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )
