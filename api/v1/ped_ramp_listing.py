from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from api.queries.ped_ramp_listing import InspectionFilters, list_project_inspections
from api.schemas.auth import UserOut
from api.schemas.ped_ramp_listing import (
    MAX_SEARCH,
    BulkDownloadOut,
    BulkDownloadResponse,
    InspectionListItem,
    InspectionListing,
    InspectionListingFilters,
    InspectionListingResponse,
    ListingStatus,
    YesNo,
)
from api.services.auth import current_user, demo_project_fence, project_member
from api.services.ped_ramp_bulk_download import MAX_BULK_CORNERS, BulkDownloadError, bulk_download
from api.v1._shared import day_range

# A project's ped ramp inspections, under /v1/projects; a demo user reaches only their own project
project_router = APIRouter(
    prefix="/v1/projects", tags=["Ped ramp listing"], dependencies=[Depends(current_user), Depends(demo_project_fence)]
)

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100
TOO_MANY = f"More than {MAX_BULK_CORNERS} corners match: narrow the filters to download at most {MAX_BULK_CORNERS}"
NOTHING_TO_DOWNLOAD = "No approved inspection with a DOT PDF matches these filters"


def _filters(filters: InspectionListingFilters, with_dot_pdf: bool = False) -> InspectionFilters:
    """
    Turn the filters as sent into the query's, reading the dates.
    Takes the filters and whether only approved inspections with a stored DOT PDF are wanted.
    Returns the InspectionFilters; raises 400 for a date that isn't YYYY-MM-DD, or from after to.
    """
    approved_from, approved_to = day_range(filters.date_from, filters.date_to)
    q = filters.q.strip() if filters.q else None
    return InspectionFilters(
        status=filters.status, inspector_uuid=filters.inspector_uuid, re_uuid=filters.re_uuid,
        approved_from=approved_from, approved_to=approved_to,
        has_tif=None if filters.has_tif is None else filters.has_tif == "yes", q=q or None, with_dot_pdf=with_dot_pdf,
    )


@project_router.get("/{project_id}/ped-ramp-inspections", response_model=InspectionListingResponse)
def list_ped_ramp_inspections(
    project_id: str,
    status: Optional[ListingStatus] = None,
    inspector_uuid: Optional[UUID] = None,
    re_uuid: Optional[UUID] = None,
    date_from: Optional[str] = Query(default=None, alias="from"),
    date_to: Optional[str] = Query(default=None, alias="to"),
    has_tif: Optional[YesNo] = None,
    q: Optional[str] = Query(default=None, max_length=MAX_SEARCH),
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    user: UserOut = Depends(project_member),
) -> InspectionListingResponse:
    """
    List a project's ped ramp inspections, one row per live corner that has one (its live inspection, else its latest approved), approved newest first, then by streets; 30 a page. For a member of the project, or an admin.
    Takes the project's id as a path parameter; as query parameters status, inspector_uuid, re_uuid, from and to (the approval's day in New York, inclusive), has_tif (yes / no), q (a street or a ramp's Ramp ID, any case), page and page_size (at most 100); and the user (checked by project_member).
    Returns an InspectionListingResponse with the total and the page's rows; raises 400 for a malformed date, from after to, or a page or page_size below 1 or a page_size over 100, 403 for a user off the project, 404 for an unknown project and 500 if the statement fails.
    """
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be at least 1")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise HTTPException(status_code=400, detail=f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    filters = _filters(InspectionListingFilters(
        status=status, inspector_uuid=inspector_uuid, re_uuid=re_uuid, date_from=date_from, date_to=date_to,
        has_tif=has_tif, q=q,
    ))
    found = list_project_inspections(project_id, filters, page_size, (page - 1) * page_size)
    if found is None:
        raise HTTPException(status_code=500, detail="Failed to fetch inspections")
    return InspectionListingResponse(
        status="success", message="Inspections retrieved",
        data=InspectionListing(total=found["total"], page=page, page_size=page_size,
                               rows=[InspectionListItem.model_validate(row) for row in found["rows"]]),
    )


@project_router.post("/{project_id}/ped-ramp-inspections/bulk-download", response_model=BulkDownloadResponse)
def bulk_download_ped_ramp_inspections(
    project_id: str, body: InspectionListingFilters, user: UserOut = Depends(project_member)
) -> BulkDownloadResponse:
    """
    Zip the DOT PDFs (and TIF PDFs) of the approved inspections the listing's filters leave, a folder per corner, at most 100 corners; the ZIP is stored and handed out as a signed URL. For a member of the project, or an admin.
    Takes the project's id as a path parameter, the listing's filters in the body and the user (checked by project_member).
    Returns a BulkDownloadResponse; raises 400 for a malformed date, from after to, nothing to download, or more than 100 corners (narrow the filters), 403 for a user off the project, 404 for an unknown project, 500 if the statement fails and 502 when Storage can't store or sign the ZIP.
    """
    found = list_project_inspections(project_id, _filters(body, with_dot_pdf=True), MAX_BULK_CORNERS + 1, 0)
    if found is None:
        raise HTTPException(status_code=500, detail="Failed to fetch inspections")
    if found["total"] > MAX_BULK_CORNERS:
        raise HTTPException(status_code=400, detail=TOO_MANY)
    if not found["rows"]:
        raise HTTPException(status_code=400, detail=NOTHING_TO_DOWNLOAD)
    try:
        download = bulk_download(project_id, found["rows"])
    except BulkDownloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return BulkDownloadResponse(status="success", message="Download ready", data=BulkDownloadOut(**download))
