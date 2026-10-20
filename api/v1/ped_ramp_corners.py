from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from api.queries.ped_ramp_corners import (
    create_corner,
    create_pedramp_site,
    get_corner,
    list_corners,
    soft_delete_corner,
    update_corner,
)
from api.schemas.auth import UserOut
from api.schemas.ped_ramp import (
    CORNER_REQUIRED,
    CornerCreate,
    CornerListResponse,
    CornerOut,
    CornerResponse,
    CornerUpdate,
    InspectionReport,
    InspectionReportResponse,
    PedrampSiteCreate,
    PedrampSiteOut,
    PedrampSiteResponse,
)
from api.services.auth import (
    corner_member,
    current_admin,
    current_user,
    demo_project_fence,
    no_demo_property_writes,
    project_member,
)
from api.services.ped_ramp_report import file_name, inspection_report, render_pdf
from api.v1._shared import changes_sent, refuse_deleted_for_member, validated

# A second router under /v1/projects, for a project's corners. A demo user reaches only their own project.
project_router = APIRouter(
    prefix="/v1/projects", tags=["Ped ramp corners"], dependencies=[Depends(current_user), Depends(demo_project_fence)]
)
# One corner by its id
router = APIRouter(prefix="/v1/ped-ramp-corners", tags=["Ped ramp corners"], dependencies=[Depends(current_user)])

# What a member who isn't an admin is told when they change a soft-deleted corner
CORNER_DELETED = "Corner is deleted"
# The fields a null clears on an edit: all but the required ones
_CLEARABLE = set(CornerUpdate.model_fields) - set(CORNER_REQUIRED)
# A corner's fields its ramps copy: an edit of one rewrites its live ramps
_RAMP_LOCATION = ("boro", "north_south_street", "east_west_street")


def _writable_corner(found: dict = Depends(corner_member), user: UserOut = Depends(no_demo_property_writes)) -> dict:
    """
    FastAPI dependency for the writes to one corner: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the corner (loaded and checked by corner_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the corner dict; raises 403 for a demo user.
    """
    return found


def _response(row: dict[str, Any], message: str) -> CornerResponse:
    """
    Wrap one corner in the response envelope.
    Takes the corner dict and the message.
    Returns the CornerResponse.
    """
    return CornerResponse(status="success", message=message, data=CornerOut.model_validate(row))


def _report(found: dict[str, Any]) -> InspectionReport:
    """
    Gather a corner's inspection report.
    Takes the corner dict.
    Returns the InspectionReport; raises 500 when its ramps can't be read.
    """
    report = inspection_report(found)
    if report is None:
        raise HTTPException(status_code=500, detail="Failed to fetch the corner's ramps")
    return report


@project_router.get("/{project_id}/ped-ramp-corners", response_model=CornerListResponse)
def list_project_corners(
    project_id: str, include_deleted: bool = False, user: UserOut = Depends(project_member)
) -> CornerListResponse:
    """
    List a project's ped ramp corners by their streets, each with its live ramps. For a user on the project, or an admin; only an admin gets soft-deleted ones, and only when asked (include_deleted from anyone else is ignored).
    Takes the project id as a path parameter, the include_deleted flag and the signed-in user.
    Returns a CornerListResponse; raises 403 for a user who isn't on the project, 404 for an unknown project and 500 if the list can't be read.
    """
    rows = list_corners(project_id, include_deleted and user.role == "admin")

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch corners")

    return CornerListResponse(
        status="success", message="Corners retrieved", data=[CornerOut.model_validate(row) for row in rows]
    )


@project_router.post(
    "/{project_id}/ped-ramp-corners",
    response_model=CornerResponse,
    status_code=201,
    dependencies=[Depends(no_demo_property_writes)],
)
def create_project_corner(project_id: str, body: CornerCreate, user: UserOut = Depends(project_member)) -> CornerResponse:
    """
    Add a ped ramp corner to a project, created by the signed-in user. For a user on the project, in any role, or an admin; not a demo user.
    Takes the project id as a path parameter, the corner in the body (its borough and two streets required; text trimmed, blank is none) and the user.
    Returns 201 with a CornerResponse; raises 403 for a demo user or a user who isn't on the project, 404 for an unknown project, 422 for a missing, unknown or over-long value and 500 if it can't be saved.
    """
    row = create_corner(project_id, user.uuid, body.model_dump())

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save corner")

    return _response(row, "Corner created")


@project_router.post(
    "/{project_id}/pedramp-sites",
    response_model=PedrampSiteResponse,
    status_code=201,
    dependencies=[Depends(no_demo_property_writes)],
)
def create_project_pedramp_site(
    project_id: str, body: PedrampSiteCreate, user: UserOut = Depends(project_member)
) -> PedrampSiteResponse:
    """
    Add a ped ramp site, its corner and the ramp at it, in one statement, created by the signed-in user. A live corner of the project at the same borough, streets (any case) and direction is reused as it stands (the corner fields sent are then ignored). For a user on the project, in any role, or an admin; not a demo user.
    Takes the project id as a path parameter, the corner's fields (borough, two streets and direction required) and the ramp's (quadrant, facing_street, ramp_type, optional ramp_id_label and notes) in the body, and the user.
    Returns 201 with a PedrampSiteResponse ({corner_id, property_id, corner_reused}); raises 403 for a demo user or a user who isn't on the project, 404 for an unknown project, 422 for a missing, unknown or over-long value and 500 if it can't be saved.
    """
    row = create_pedramp_site(project_id, user.uuid, body.model_dump())

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to save ped ramp site")

    message = "Ramp added at an existing corner" if row["corner_reused"] else "Corner and ramp created"
    return PedrampSiteResponse(status="success", message=message, data=PedrampSiteOut.model_validate(row))


@router.get("/{corner_id}", response_model=CornerResponse)
def get_corner_record(found: dict = Depends(corner_member)) -> CornerResponse:
    """
    Fetch one corner, soft-deleted or not, with its live ramps. For a user on its project, or an admin.
    Takes the corner (loaded and checked by corner_member from the corner_id path parameter).
    Returns a CornerResponse; raises 403 for a user who isn't on its project and 404 for an unknown corner.
    """
    return _response(found, "Corner retrieved")


@router.patch("/{corner_id}", response_model=CornerResponse)
def update_corner_record(
    corner_id: UUID,
    body: CornerUpdate,
    found: dict = Depends(_writable_corner),
    user: UserOut = Depends(current_user),
) -> CornerResponse:
    """
    Change some of a corner, merged over it and checked whole as a new one; a new borough or street is written onto its live ramps too. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted one. A field left out keeps its value; a null clears it, but for the borough and the two streets, where it counts as left out.
    Takes the corner's id as a path parameter, the fields to change in the body, the corner (loaded by _writable_corner) and the user.
    Returns a CornerResponse with the corner as it now is; raises 400 when nothing is sent or a user who isn't an admin edits a deleted corner, 403 for a demo user or a user who isn't on its project, 404 for an unknown corner and 422 for an unknown or over-long value.
    """
    refuse_deleted_for_member(found, user, CORNER_DELETED)
    changes = changes_sent(body, _CLEARABLE)
    merged = {**{field: found.get(field) for field in CornerCreate.model_fields}, **changes}
    record = validated(CornerCreate, merged)
    row = update_corner(corner_id, record.model_dump())

    if row is None:
        raise HTTPException(status_code=404, detail="Corner not found")

    if any(row[field] != found[field] for field in _RAMP_LOCATION):
        # The statement's own read saw its ramps before it rewrote their streets
        row = get_corner(corner_id) or row

    return _response(row, "Corner updated")


@router.delete("/{corner_id}", response_model=CornerResponse)
def delete_corner(found: dict = Depends(corner_member), admin: UserOut = Depends(current_admin)) -> CornerResponse:
    """
    Soft-delete a corner: it is marked deleted, with when and by whom, and kept; it leaves the project's list, and its ramps keep it as their corner. Admins only. Deleting a corner that is already deleted succeeds and changes nothing.
    Takes the corner (loaded by corner_member from the corner_id path parameter) and the signed-in admin.
    Returns a CornerResponse with the deleted corner; raises 403 for a user who isn't an admin and 404 for an unknown corner.
    """
    if found["deleted_at"] is not None:
        return _response(found, "Corner was already deleted")

    row = soft_delete_corner(found["corner_id"], admin.uuid)

    if row is None:
        # Deleted by someone else between the read and the statement: the same outcome
        return _response(get_corner(found["corner_id"]) or found, "Corner was already deleted")

    return _response(row, "Corner deleted")


@router.get("/{corner_id}/inspection-report", response_model=InspectionReportResponse)
def get_inspection_report(found: dict = Depends(corner_member)) -> InspectionReportResponse:
    """
    Fetch a corner's inspection report as data: the corner, its ramp types, and each live ramp with its attributes and their ADA warnings. For a user on its project, or an admin.
    Takes the corner (loaded and checked by corner_member from the corner_id path parameter).
    Returns an InspectionReportResponse; raises 403 for a user who isn't on its project, 404 for an unknown corner and 500 if its ramps can't be read.
    """
    return InspectionReportResponse(status="success", message="Inspection report retrieved", data=_report(found))


@router.get(
    "/{corner_id}/inspection-report.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The inspection report as a PDF"}},
)
def download_inspection_report(found: dict = Depends(corner_member)) -> Response:
    """
    Download a corner's inspection report as a PDF: the corner page, then a page per ramp (a cut-through's two on one), ADA warnings marked. For a user on its project, or an admin.
    Takes the corner (loaded and checked by corner_member from the corner_id path parameter).
    Returns the PDF with a Content-Disposition naming it; raises 403 for a user who isn't on its project, 404 for an unknown corner and 500 if its ramps can't be read.
    """
    report = _report(found)
    return Response(
        content=render_pdf(report),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_name(report.corner)}"'},
    )
