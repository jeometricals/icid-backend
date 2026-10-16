from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.queries.idrs import get_idr_by_id
from api.services.auth import current_user, demo_idr_fence, property_member, require_project_role
from api.services.construction_report import (
    ReportFile,
    ReportNotFound,
    ReportReadError,
    idr_reports,
    property_report,
)
from api.services.construction_sketch import SketchError

# A property's report sits under /v1/properties (property_member fences a demo user to their own project)
property_router = APIRouter(prefix="/v1/properties", tags=["Construction reports"],
                            dependencies=[Depends(current_user)])
# An IDR's reports sit under /v1/idrs; a demo user reaches only their own IDRs
router = APIRouter(prefix="/v1/idrs", tags=["Construction reports"],
                   dependencies=[Depends(current_user), Depends(demo_idr_fence)])


def _file(report: ReportFile, media_type: str) -> Response:
    """
    Hand a report back as a download.
    Takes the file and its media type.
    Returns the response, its Content-Disposition naming the file.
    """
    return Response(content=report.content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{report.filename}"'})


def _refused(exc: Exception) -> HTTPException:
    """
    Turn a report failure into its HTTP error.
    Takes the exception.
    Returns the HTTPException: 404 when no approved IDR covers the flags, 422 when the sketch can't go on one sheet, 500 when the data can't be read.
    """
    if isinstance(exc, ReportNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, SketchError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@property_router.get("/{property_id}/construction-report", response_class=Response,
                     responses={200: {"content": {"application/pdf": {}}}})
def get_property_construction_report(
    property_id: UUID, idr_id: Optional[UUID] = None, found: dict = Depends(property_member)
) -> Response:
    """
    Download a property's Construction Report (DOT form 5/95) as a one-page PDF: of the given approved IDR, or by default the most recently approved one that claimed a flag on the property. For a user on its project, or an admin; a demo user reads their own project's.
    Takes the property's id as a path parameter, optionally idr_id as a query parameter, and the property (loaded by property_member).
    Returns the PDF as an attachment; raises 404 (no property, or no approved IDR, or not the given one, covers its flags), 403 (not on its project), 422 (the sketch is on more than one page, has no scale, or won't fit one sheet legibly) and 500.
    """
    try:
        return _file(property_report(found["id"], idr_id), "application/pdf")
    except (ReportNotFound, SketchError, ReportReadError) as exc:
        raise _refused(exc) from exc


@router.get("/{idr_id}/construction-reports", response_class=Response,
            responses={200: {"content": {"application/zip": {}}}},
            dependencies=[Depends(require_project_role("inspector", "oe", "re"))])
def get_idr_construction_reports(idr_id: UUID) -> Response:
    """
    Download an approved IDR's Construction Reports, one PDF per property it claimed flags on, in a ZIP. For a user holding a role on its project, or an admin.
    Takes the IDR's id as a path parameter.
    Returns the ZIP as an attachment; raises 404 (no IDR, one that isn't approved, or one that claimed no flags), 403 (no role on its project), 422 (a property's sketch can't go on one sheet; the message names it) and 500.
    """
    idr = get_idr_by_id(idr_id)
    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")
    if idr["status"] != "approved" or idr.get("deleted_at") is not None:
        raise HTTPException(status_code=404, detail="IDR is not approved")
    try:
        return _file(idr_reports(idr), "application/zip")
    except (ReportNotFound, SketchError, ReportReadError) as exc:
        raise _refused(exc) from exc
