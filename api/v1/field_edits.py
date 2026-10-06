from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.queries.idr_reports import list_reports_for_idr
from api.queries.idrs import get_idr_by_id
from api.schemas.auth import UserOut
from api.schemas.field_edit import FieldEditRequest, PayItemAdd, PayItemRevision
from api.schemas.idr import IdrWithReports, IdrWithReportsResponse
from api.services.auth import current_user, demo_idr_fence, stage_reviewer
from api.services.field_edits import (
    FieldEditError,
    add_pay_item,
    edit_field,
    editable_idr,
    field_edits_for,
    revise_pay_item,
)

# Every route needs a signed-in user; each one is for the reviewer who accepted the IDR at its current stage (or an
# admin), which the stage_reviewer dependency checks
router = APIRouter(prefix="/v1/idrs", tags=["Reviewer edits"],
                   dependencies=[Depends(current_user), Depends(demo_idr_fence)])


def _edited(idr_id: UUID, edit: Callable[[], dict[str, Any]], message: str) -> IdrWithReportsResponse:
    """
    Run one edit and answer with the IDR as it now stands, its reports and all its edits.
    Takes the IDR uuid, the edit to run (it returns the edit row or raises FieldEditError) and the success message.
    Returns an IdrWithReportsResponse; raises the edit's own HTTP error, or 500 if the IDR can't be read back.
    """
    try:
        edit()
    except FieldEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    idr, reports, edits = get_idr_by_id(idr_id), list_reports_for_idr(idr_id), field_edits_for(idr_id)

    if idr is None or reports is None or edits is None:
        raise HTTPException(status_code=500, detail="The edit was saved, but the IDR could not be read back")

    return IdrWithReportsResponse(
        status="success",
        message=message,
        data=IdrWithReports.model_validate({**idr, "reports": reports, "field_edits": edits}),
    )


@router.patch("/{idr_id}/field", response_model=IdrWithReportsResponse)
def edit_idr_field(idr_id: UUID, body: FieldEditRequest, user: UserOut = Depends(stage_reviewer)) -> IdrWithReportsResponse:
    """
    Record a reviewer's edit of one field of an IDR in review: the new value is written into the IDR and the old one kept in the edit log, with the reviewer and the stage. A header field is named "header.<column>" with no report_id; a field of a report by its path in that report, with report_id. Editing a pay item's payQuantity is a pay-item revision.
    Takes the IDR uuid as a path parameter, a FieldEditRequest body (report_id, field_path, new_value) and the stage's reviewer (or an admin).
    Returns an IdrWithReportsResponse with the IDR, its reports and every edit; raises 400 (not in review, no such field, a value the field can't take, no change, an auto-generated General), 403 (not the stage's reviewer), 404 (no IDR, or the report isn't in it) and 409 (the IDR or the field changed meanwhile).
    """
    return _edited(idr_id, lambda: edit_field(idr_id, body.report_id, body.field_path, body.new_value, user),
                   "Field edited")


@router.post("/{idr_id}/pay-items/add", response_model=IdrWithReportsResponse)
def add_idr_pay_item(idr_id: UUID, body: PayItemAdd, user: UserOut = Depends(stage_reviewer)) -> IdrWithReportsResponse:
    """
    Add a pay item to one report of an IDR in review, on the reviewer's behalf: it joins the end of that report's pay items and is logged as added by them. (An inspector adds pay items to a draft through the report form.)
    Takes the IDR uuid as a path parameter, a PayItemAdd body (report_id, item_no, budget_code, quantity, unit, description) and the stage's reviewer (or an admin).
    Returns an IdrWithReportsResponse with the IDR, its reports and every edit; raises 400 (not in review, a report without pay items, an auto-generated General, a blank quantity, neither an item number nor a description), 403 (not the stage's reviewer), 404 (no IDR, or the report isn't in it) and 409 (the IDR changed meanwhile).
    """
    return _edited(idr_id, lambda: add_pay_item(editable_idr(idr_id), body.report_id, body.item_no, body.budget_code,
                                                body.quantity, body.unit, body.description, user),
                   "Pay item added")


@router.post("/{idr_id}/pay-items/{pay_item_id}/revise", response_model=IdrWithReportsResponse)
def revise_idr_pay_item(idr_id: UUID, pay_item_id: str, body: PayItemRevision,
                        user: UserOut = Depends(stage_reviewer)) -> IdrWithReportsResponse:
    """
    Record a reviewer's new quantity for one pay item of an IDR in review. The item is found by its id in whichever report holds it; its quantity becomes the revised one and the inspector's is kept in the edit log.
    Takes the IDR uuid and the pay item's id as path parameters, a PayItemRevision body (revised_quantity) and the stage's reviewer (or an admin).
    Returns an IdrWithReportsResponse with the IDR, its reports and every edit; raises 400 (not in review, a blank quantity, the same quantity, an auto-generated General), 403 (not the stage's reviewer), 404 (no IDR, or no such pay item in it) and 409 (the IDR or the quantity changed meanwhile).
    """
    return _edited(idr_id, lambda: revise_pay_item(editable_idr(idr_id), pay_item_id, body.revised_quantity, user),
                   "Pay item revised")
