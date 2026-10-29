import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.assessment_clip_audits import list_property_clip_inputs, replace_property_clips
from api.queries.ped_ramp_corners import get_live_corner_of_project
from api.queries.properties import (
    create_property,
    get_property,
    get_sidewalk_at,
    list_properties,
    refresh_property_tax_map,
    soft_delete_property,
    undelete_property,
    update_property,
)
from api.schemas.auth import UserOut
from api.schemas.property import (
    Kind,
    PropertyConflict,
    PropertyCreate,
    PropertyDetail,
    PropertyDetailResponse,
    PropertyListItem,
    PropertyListResponse,
    PropertyOut,
    PropertyResponse,
    PropertyUpdate,
    ReClip,
    ReClipResponse,
    TaxMapPdf,
    TaxMapPdfResponse,
    TaxMapRefresh,
    TaxMapRefreshResponse,
    TaxMapUploadComplete,
    TaxMapUploadRequest,
    TaxMapUploadRequestResponse,
)
from api.services.auth import (
    current_admin,
    current_user,
    demo_project_fence,
    no_demo_property_writes,
    project_member,
    property_member,
)
from api.services.export_common import FORM_TIMEZONE
from api.services.dof_tax_map import (
    DofTaxMapUnavailable,
    TaxMapPdfError,
    cache_tax_map_pdf,
    complete_upload,
    map_url,
    request_upload,
    signed_pdf_url,
    store_current_map,
)
from api.services.property_line import flag_clips
from api.services.mappluto import MapPlutoUnavailable, cache_frontage, fetch_frontage_by_bbl
from api.v1._shared import clean_text, refuse_deleted_for_member, refuse_locked_corner, validated

logger = logging.getLogger(__name__)

# A second router under /v1/projects, for a project's properties. A demo user reaches only their own project.
project_router = APIRouter(
    prefix="/v1/projects", tags=["Properties"], dependencies=[Depends(current_user), Depends(demo_project_fence)]
)
# One property by its id
router = APIRouter(prefix="/v1/properties", tags=["Properties"], dependencies=[Depends(current_user)])

# The longest value each text field takes
MAX_ADDRESS_LENGTH = 200
MAX_NOTES_LENGTH = 2000
MAX_STREET_LENGTH = 200
MAX_VIOLATION_NO_LENGTH = 200
MAX_COMMUNITY_BOARD_LENGTH = 10
# The text fields a null leaves alone, and the ones it clears (blank is none), each with its longest value
_REQUIRED_TEXT = {
    "address": MAX_ADDRESS_LENGTH,
    "intersection_street_1": MAX_STREET_LENGTH, "intersection_street_2": MAX_STREET_LENGTH,
}
_OPTIONAL_TEXT = {
    "violation_no": MAX_VIOLATION_NO_LENGTH, "community_board": MAX_COMMUNITY_BOARD_LENGTH, "notes": MAX_NOTES_LENGTH,
}
# The fields the body's model checks and puts in their stored form: the choices, and a sidewalk's block and lot
_MODEL_CHECKED = ("kind", "boro", "block", "lot", "property_type", "quadrant", "facing_street", "ramp_type")
CORNER_ELSEWHERE = "Corner must be a live ped ramp corner of this project"
KIND_CHANGED = "Property kind cannot be changed. Delete and recreate."
CONFLICT = {409: {"model": PropertyConflict}}
# What a member who isn't an admin is told when they change a soft-deleted property
PROPERTY_DELETED = "Property is deleted"
NO_TAX_LOT = "A ped ramp has no tax lot to look up"
MAPPLUTO_DOWN = "MapPLUTO can't be reached right now; nothing was changed. Try again later."
DOF_DOWN = "DOF's tax map service can't be reached right now; nothing was changed. Try again later, or upload the PDF."
NO_TAX_MAP_PDF = "No tax map PDF on file for this site"


def _writable_property(
    found: dict = Depends(property_member), user: UserOut = Depends(no_demo_property_writes)
) -> dict:
    """
    FastAPI dependency for the writes to one property: the signed-in user must be on its project (or an admin), and not a demo user.
    Takes the property (loaded and checked by property_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the property dict; raises 403 for a demo user.
    """
    return found


def _clean_fields(body: PropertyCreate | PropertyUpdate) -> dict[str, Any]:
    """
    Read a property's fields off the request: text trimmed, address and streets refused when blank or too long. A field sent as null is left out, but for notes, violation_no and community_board, which are optional: blank or null ones are None.
    Takes the request body.
    Returns the values keyed by column, for the fields sent (and a create's kind); raises 400 for a value that doesn't fit.
    """
    fields: dict[str, Any] = {}
    for field, longest in _REQUIRED_TEXT.items():
        if getattr(body, field) is not None:
            fields[field] = clean_text(getattr(body, field), field, longest)
    for field, longest in _OPTIONAL_TEXT.items():
        if field in body.model_fields_set:
            fields[field] = clean_text(getattr(body, field), field, longest, required=False)
    for field in _MODEL_CHECKED:
        if getattr(body, field) is not None:
            fields[field] = getattr(body, field)
    if "corner_id" in body.model_fields_set:
        fields["corner_id"] = body.corner_id
    return fields


def _check_corner(project_id: str, corner_id: Optional[UUID]) -> None:
    """
    Refuse a ped ramp's corner that isn't a live corner of its project.
    Takes the project id and the corner's id (None for none).
    Returns nothing; raises 400 when the project has no such live corner.
    """
    if corner_id is not None and get_live_corner_of_project(corner_id, project_id) is None:
        raise HTTPException(status_code=400, detail=CORNER_ELSEWHERE)


def _site(fields: dict[str, Any]) -> str:
    """
    Name a sidewalk by where it is on the tax map, for a 409.
    Takes its values keyed by column (boro, block and lot among them).
    Returns the name, as "Queens Block 123 Lot 45".
    """
    return f"{fields['boro']} Block {fields['block']} Lot {fields['lot']}"


def _conflict(project_id: str, fields: dict[str, Any], property_id: Optional[UUID] = None) -> JSONResponse:
    """
    Build the 409 for a borough, block and lot another live sidewalk of the project is at.
    Takes the project id, the property's values keyed by column and the id of the property being saved (None for a new one).
    Returns the response, naming that other property; raises 500 when no other is there (the statement failed for another reason).
    """
    holder = None
    if fields.get("kind") == "sidewalk":
        holder = get_sidewalk_at(project_id, fields["boro"], fields["block"], fields["lot"])
    if holder is None or holder["id"] == property_id:
        raise HTTPException(status_code=500, detail="Failed to save property")
    conflict = PropertyConflict(
        detail=f"A site at {_site(fields)} already exists in this project.", existing_property_id=holder["id"]
    )
    return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))


def _load_property(property_id: UUID) -> dict[str, Any]:
    """
    Fetch a property for an admin route.
    Takes the property's id.
    Returns the property dict; raises 404 when there is none.
    """
    found = get_property(property_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return found


def _response(row: dict[str, Any], message: str) -> PropertyResponse:
    """
    Wrap one property in the response envelope.
    Takes the property dict and the message.
    Returns the PropertyResponse.
    """
    return PropertyResponse(status="success", message=message, data=PropertyOut.model_validate(row))


@project_router.get("/{project_id}/properties", response_model=PropertyListResponse)
def list_project_properties(
    project_id: str,
    include_deleted: bool = False,
    kind: Optional[Kind] = None,
    user: UserOut = Depends(project_member),
) -> PropertyListResponse:
    """
    List a project's properties, of one kind or both, by address, each with its live PIR's status, its status and its latest task order's number. For a user on the project, or an admin; only an admin gets soft-deleted ones, and only when asked (include_deleted from anyone else is ignored).
    Takes the project id as a path parameter, the include_deleted flag, the kind (sidewalk or ped_ramp; both when left out) and the signed-in user.
    Returns a PropertyListResponse; raises 403 for a user who isn't on the project, 404 for an unknown project, 422 for an unknown kind and 500 if the list can't be read.
    """
    rows = list_properties(project_id, FORM_TIMEZONE, include_deleted and user.role == "admin", kind)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch properties")

    return PropertyListResponse(
        status="success", message="Properties retrieved", data=[PropertyListItem.model_validate(row) for row in rows]
    )


@project_router.post(
    "/{project_id}/properties",
    response_model=PropertyResponse,
    status_code=201,
    responses=CONFLICT,
    dependencies=[Depends(no_demo_property_writes)],
)
def create_project_property(
    project_id: str, body: PropertyCreate, background: BackgroundTasks, user: UserOut = Depends(project_member)
) -> PropertyResponse | JSONResponse:
    """
    Add a property to a project, created by the signed-in user: a sidewalk or a ped ramp. For a user on the project, in any role, or an admin; not a demo user.
    A sidewalk's tax-map frontage (MapPLUTO, Slice Y1) and its block's DOF tax-map PDF (Slice Y1.5) are fetched after the response has gone, so it comes back without either.
    Takes the project id as a path parameter, the property in the body (its kind's fields; text trimmed; blank notes, violation_no and community_board are none), the background tasks and the user.
    Returns 201 with a PropertyResponse; raises 400 for a blank or over-long value or a corner_id that isn't a live corner of the project, 403 for a demo user or a user who isn't on the project, 404 for an unknown project, 422 for an unknown value, a block or lot that isn't digits, or fields that don't fit the kind and 409 (with existing_property_id) when a live sidewalk of the project is at the borough, block and lot. A ped ramp whose corner's inspection is submitted is the RE's (403), and one whose inspection is approved is frozen (409).
    """
    fields = _clean_fields(body)
    _check_corner(project_id, fields.get("corner_id"))
    refuse_locked_corner(fields.get("corner_id"), project_id, user)
    row = create_property(project_id, user.uuid, fields)

    if row is None:
        return _conflict(project_id, fields)

    if row["kind"] == "sidewalk":
        background.add_task(cache_frontage, row["id"], row["boro"], row["block"], row["lot"])
        background.add_task(cache_tax_map_pdf, row["id"], row["boro"], row["block"])

    return _response(row, "Property created")


@router.get("/{property_id}", response_model=PropertyDetailResponse)
def get_property_record(found: dict = Depends(property_member)) -> PropertyDetailResponse:
    """
    Fetch one property, soft-deleted or not, with the latest live task order it is in. For a user on its project, or an admin.
    Takes the property (loaded and checked by property_member from the property_id path parameter).
    Returns a PropertyDetailResponse; raises 403 for a user who isn't on its project and 404 for an unknown property.
    """
    return PropertyDetailResponse(status="success", message="Property retrieved", data=PropertyDetail.model_validate(found))


@router.patch("/{property_id}", response_model=PropertyResponse, responses=CONFLICT)
def update_property_record(
    property_id: UUID,
    body: PropertyUpdate,
    background: BackgroundTasks,
    found: dict = Depends(_writable_property),
    user: UserOut = Depends(current_user),
) -> PropertyResponse | JSONResponse:
    """
    Change some of a property, merged over it and checked whole as a new one of its kind. For a user on its project, or an admin; not a demo user. Only an admin changes a soft-deleted one. A field left out, or sent as null, keeps its value; null or blank notes, violation_no and community_board clear them. Its kind can't change. A sidewalk moved to another borough, block or lot loses its tax-map frontage, fetched again from MapPLUTO after the response has gone (Slice Y1); one moved to another borough or block loses its DOF tax-map PDF too, fetched again the same way (Slice Y1.5).
    Takes the property's id as a path parameter, the fields to change in the body, the background tasks, the property (loaded by _writable_property) and the user.
    Returns a PropertyResponse with the property as it now is; raises 400 when nothing is sent, the kind would change, a value doesn't fit, a new corner_id isn't a live corner of its project, or a user who isn't an admin edits a deleted property, 403 for a demo user or a user who isn't on its project, 404 for an unknown property, 422 for an unknown value or fields that don't fit its kind and 409 (with existing_property_id) when another live sidewalk of its project is at the borough, block and lot it would end up with. A ped ramp whose corner's inspection is submitted is the RE's (403), and one whose inspection is approved is frozen (409).
    """
    refuse_deleted_for_member(found, user, PROPERTY_DELETED)
    refuse_locked_corner(found.get("corner_id"), found["project_id"], user)

    if body.kind is not None and body.kind != found["kind"]:
        raise HTTPException(status_code=400, detail=KIND_CHANGED)

    changes = _clean_fields(body)
    changes.pop("kind", None)

    if not changes:
        raise HTTPException(status_code=400, detail="Send at least one field to change")

    merged = {**{field: found.get(field) for field in PropertyCreate.model_fields}, **changes}
    validated(PropertyCreate, merged)
    if changes.get("corner_id") is not None and changes["corner_id"] != found.get("corner_id"):
        _check_corner(found["project_id"], changes["corner_id"])
        refuse_locked_corner(changes["corner_id"], found["project_id"], user)
    row = update_property(property_id, merged)

    if row is None:
        current = _load_property(property_id)
        return _conflict(current["project_id"], merged, property_id)

    if row["kind"] == "sidewalk" and any(row[field] != found.get(field) for field in ("boro", "block", "lot")):
        background.add_task(cache_frontage, row["id"], row["boro"], row["block"], row["lot"])
    if row["kind"] == "sidewalk" and any(row[field] != found.get(field) for field in ("boro", "block")):
        background.add_task(cache_tax_map_pdf, row["id"], row["boro"], row["block"])

    return _response(row, "Property updated")


@router.post("/{property_id}/admin/soft-delete", response_model=PropertyResponse)
def delete_property(property_id: UUID, admin: UserOut = Depends(current_admin)) -> PropertyResponse:
    """
    Soft-delete a property: it is marked deleted, with when and by whom, and kept. It leaves the project's list and, a sidewalk, frees its borough, block and lot. Admins only. Deleting a property that is already deleted succeeds and changes nothing.
    Takes the property's id as a path parameter and the signed-in admin.
    Returns a PropertyResponse with the deleted property; raises 403 for a user who isn't an admin, 404 for an unknown property and 409 for a ped ramp whose corner's inspection is approved.
    """
    current = _load_property(property_id)

    if current["deleted_at"] is not None:
        return _response(current, "Property was already deleted")

    refuse_locked_corner(current.get("corner_id"), current["project_id"], admin)

    row = soft_delete_property(property_id, admin.uuid)

    if row is None:
        # Deleted by someone else between the read and the statement: the same outcome
        return _response(_load_property(property_id), "Property was already deleted")

    return _response(row, "Property deleted")


@router.post(
    "/{property_id}/admin/undelete",
    response_model=PropertyResponse,
    responses=CONFLICT,
)
def undelete_property_record(
    property_id: UUID, admin: UserOut = Depends(current_admin)
) -> PropertyResponse | JSONResponse:
    """
    Bring a soft-deleted property back, at the borough, block and lot it had (a sidewalk). Admins only.
    Takes the property's id as a path parameter and the signed-in admin.
    Returns a PropertyResponse with the restored property; raises 403 for a user who isn't an admin, 404 for an unknown property or one that isn't deleted, and 409 (with existing_property_id) when another live sidewalk of its project is now at its borough, block and lot, or for a ped ramp whose corner's inspection is approved.
    """
    current = _load_property(property_id)

    if current["deleted_at"] is None:
        raise HTTPException(status_code=404, detail="Property is not deleted")

    refuse_locked_corner(current.get("corner_id"), current["project_id"], admin)

    row = undelete_property(property_id)

    if row is None:
        holder = None
        if current["kind"] == "sidewalk":
            holder = get_sidewalk_at(current["project_id"], current["boro"], current["block"], current["lot"])

        if holder is None or holder["id"] == property_id:
            # Restored by someone else meanwhile
            raise HTTPException(status_code=404, detail="Property is not deleted")

        conflict = PropertyConflict(
            detail=f"Another site at {_site(current)} now exists in this project.", existing_property_id=holder["id"]
        )
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    return _response(row, "Property restored")


@router.post("/{property_id}/admin/refresh-tax-map", response_model=TaxMapRefreshResponse)
def refresh_tax_map(property_id: UUID, admin: UserOut = Depends(current_admin)) -> TaxMapRefreshResponse:
    """
    Fetch a sidewalk's tax-map frontage (and its lot depth, pilot sweep #2) from MapPLUTO again, now (Slice Y1), and write them onto the property (the depth there only) and onto the title blocks of its live PIR's pages, but those whose width was typed by hand; page 1 gets a title block if it has none. Admins only, a soft-deleted property included. Logged, as the record of it.
    Takes the property's id as a path parameter and the signed-in admin.
    Returns a TaxMapRefreshResponse; raises 400 for a ped ramp, 403 for a user who isn't an admin, 404 for an unknown property, 409 when the property moved to another lot meanwhile, 422 when MapPLUTO has no usable frontage for its lot and 503 when MapPLUTO can't be reached (nothing is written for any of them).
    """
    current = _load_property(property_id)

    if current["kind"] != "sidewalk":
        raise HTTPException(status_code=400, detail=NO_TAX_LOT)

    lot = (current["boro"], current["block"], current["lot"])
    try:
        found = fetch_frontage_by_bbl(*lot)
    except MapPlutoUnavailable as exc:
        logger.warning("MapPLUTO refresh of property %s failed: %s", property_id, exc)
        raise HTTPException(status_code=503, detail=MAPPLUTO_DOWN) from exc

    if found is None:
        raise HTTPException(status_code=422, detail=f"MapPLUTO has no usable frontage for {_site(current)}")

    row = refresh_property_tax_map(property_id, *lot, *found)

    if row is None:
        raise HTTPException(status_code=409, detail="The site moved to another lot meanwhile; refresh it again")

    logger.info(
        "MapPLUTO refresh: property_id=%s old_frontage=%s new_frontage=%s source=%s pages_updated=%s admin=%s at=%s",
        property_id, row["old_frontage_ft"], row["tax_map_frontage_ft"], row["tax_map_source"], row["pages_updated"],
        admin.uuid, datetime.now(timezone.utc).isoformat(),
    )
    data = TaxMapRefresh(frontage_ft=row["tax_map_frontage_ft"], source=row["tax_map_source"],
                         fetched_at=row["tax_map_fetched_at"], depth_ft=row["tax_map_depth_ft"],
                         pages_updated=row["pages_updated"])
    return TaxMapRefreshResponse(status="success", message=f"Tax-map frontage refreshed; {row['pages_updated']} title block(s) updated", data=data)


def _sidewalk(property_id: UUID) -> dict[str, Any]:
    """
    Fetch a sidewalk for a tax-map PDF route.
    Takes the property's id.
    Returns the property dict; raises 404 when there is none and 400 for a ped ramp.
    """
    current = _load_property(property_id)
    if current["kind"] != "sidewalk":
        raise HTTPException(status_code=400, detail=NO_TAX_LOT)
    return current


def _tax_map_pdf(row: dict[str, Any], message: str) -> TaxMapPdfResponse:
    """
    Wrap a site's tax-map PDF in the response envelope, with a signed URL to it.
    Takes the property dict (with its tax_map_pdf_* columns) and the message.
    Returns the TaxMapPdfResponse; raises 502 when Storage can't sign the URL.
    """
    try:
        url = signed_pdf_url(row)
    except TaxMapPdfError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    data = TaxMapPdf(url=url, source=row["tax_map_pdf_source"], map_id=row["tax_map_pdf_map_id"],
                     effective_date=row["tax_map_pdf_effective_date"], dof_url=map_url(row["tax_map_pdf_map_id"]),
                     fetched_at=row["tax_map_pdf_fetched_at"], uploaded_by=row["tax_map_pdf_uploaded_by"])
    return TaxMapPdfResponse(status="success", message=message, data=data)


def _log_tax_map_pdf(action: str, property_id: UUID, admin: UserOut, row: dict[str, Any]) -> None:
    """
    Log an admin's change of a site's tax-map PDF, the record of it.
    Takes what was done (refresh or upload), the property's id, the admin and the property as it now is.
    Returns nothing.
    """
    logger.info("Tax map PDF %s: property_id=%s source=%s map_id=%s path=%s admin=%s at=%s", action, property_id,
                row["tax_map_pdf_source"], row["tax_map_pdf_map_id"], row["tax_map_pdf_path"], admin.uuid,
                datetime.now(timezone.utc).isoformat())


@router.get("/{property_id}/tax-map-pdf", response_model=TaxMapPdfResponse)
def get_tax_map_pdf(found: dict = Depends(property_member)) -> TaxMapPdfResponse:
    """
    Hand out a sidewalk's DOF tax-map PDF (Slice Y1.5) through a signed URL, with where it came from. For a user on its project, or an admin; demo users read.
    Takes the property (loaded and checked by property_member from the property_id path parameter).
    Returns a TaxMapPdfResponse; raises 403 for a user who isn't on its project, 404 for an unknown property or one with no tax map PDF on file and 502 when Storage can't sign the URL.
    """
    if found.get("tax_map_pdf_path") is None:
        raise HTTPException(status_code=404, detail=NO_TAX_MAP_PDF)
    return _tax_map_pdf(found, "Tax map PDF retrieved")


@router.post("/{property_id}/admin/refresh-tax-map-pdf", response_model=TaxMapPdfResponse)
def refresh_tax_map_pdf(property_id: UUID, admin: UserOut = Depends(current_admin)) -> TaxMapPdfResponse:
    """
    Fetch a sidewalk's block's current DOF tax map now (Slice Y1.5) and keep it as the site's tax-map PDF, replacing the one it had. Admins only, a soft-deleted property included. Logged, as the record of it.
    Takes the property's id as a path parameter and the signed-in admin.
    Returns a TaxMapPdfResponse; raises 400 for a ped ramp, 403 for a user who isn't an admin, 404 for an unknown property, 409 when the site moved to another block meanwhile, 422 when DOF has no current map for its block, 502 when Storage fails and 503 when DOF can't be reached (nothing is changed on any of them).
    """
    current = _sidewalk(property_id)
    try:
        row = store_current_map(current)
    except DofTaxMapUnavailable as exc:
        logger.warning("Tax map PDF refresh of property %s failed: %s", property_id, exc)
        raise HTTPException(status_code=503, detail=DOF_DOWN) from exc
    except TaxMapPdfError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    _log_tax_map_pdf("refresh", property_id, admin, row)
    return _tax_map_pdf(row, "Tax map PDF refreshed from DOF")


@router.post("/{property_id}/admin/tax-map-pdf/upload-request", response_model=TaxMapUploadRequestResponse)
def request_tax_map_pdf_upload(property_id: UUID, admin: UserOut = Depends(current_admin)) -> TaxMapUploadRequestResponse:
    """
    Start an admin's upload of a sidewalk's tax-map PDF (Slice Y1.5, when DOF's can't be fetched): a signed URL to PUT the PDF to, at a fresh staging path. Admins only.
    Takes the property's id as a path parameter and the signed-in admin.
    Returns a TaxMapUploadRequestResponse; raises 400 for a ped ramp, 403 for a user who isn't an admin, 404 for an unknown property and 502 when Storage can't sign a URL.
    """
    _sidewalk(property_id)
    try:
        upload = request_upload(property_id)
    except TaxMapPdfError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return TaxMapUploadRequestResponse(status="success", message="Upload URL created", data=TaxMapUploadRequest(**upload))


@router.post("/{property_id}/admin/tax-map-pdf/upload-complete", response_model=TaxMapPdfResponse)
def complete_tax_map_pdf_upload(
    property_id: UUID, body: TaxMapUploadComplete, admin: UserOut = Depends(current_admin)
) -> TaxMapPdfResponse:
    """
    Keep an admin's staged upload as a sidewalk's tax-map PDF (Slice Y1.5), replacing the one it had: it is read back, checked to be a PDF by its bytes and stored. Admins only. Logged, as the record of it.
    Takes the property's id as a path parameter, the staging path in the body and the signed-in admin.
    Returns a TaxMapPdfResponse; raises 400 for a ped ramp, a path that isn't one of the site's uploads or a file that isn't a PDF, 403 for a user who isn't an admin, 404 for an unknown property or an upload that isn't there, 409 when the site moved to another block meanwhile, 413 for a file over TAX_MAP_MAX_BYTES and 502 when Storage fails.
    """
    current = _sidewalk(property_id)
    try:
        row = complete_upload(current, body.staging_path, admin.uuid)
    except TaxMapPdfError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    _log_tax_map_pdf("upload", property_id, admin, row)
    return _tax_map_pdf(row, "Tax map PDF uploaded")


@router.post("/{property_id}/admin/re-clip-approvals", response_model=ReClipResponse)
def re_clip_approvals(property_id: UUID, admin: UserOut = Depends(current_admin)) -> ReClipResponse:
    """
    Work the assessment clips (Slice Y2) of every flag of a property claimed by an approved IDR out again from its pages' sidewalk strips as they are now, and replace the clips those IDRs have on it, in one statement. Admins only, a soft-deleted property included. Logged, as the record of it.
    Takes the property's id as a path parameter and the signed-in admin.
    Returns a ReClipResponse; raises 403 for a user who isn't an admin, 404 for an unknown property and 500 when the flags can't be read or the clips written.
    """
    _load_property(property_id)
    flags = list_property_clip_inputs(property_id)
    if flags is None:
        raise HTTPException(status_code=500, detail="Failed to read the property's flags")
    result = replace_property_clips(property_id, flag_clips(flags, with_idr=True))
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to write the clips")
    logger.info("re_clip property_id=%s actor=%s idrs=%s before=%s after=%s", property_id, admin.uuid,
                result["idrs_reclipped"], result["rows_before"], result["rows_after"])
    data = ReClip(**{key: result[key] for key in ("idrs_reclipped", "rows_before", "rows_after")})
    return ReClipResponse(status="success", message=f"Re-clipped {data.idrs_reclipped} approved IDR(s)", data=data)
