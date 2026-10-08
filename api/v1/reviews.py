import logging
from typing import Any, Literal, Optional, Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.idrs import (
    IdrNumberTakenError,
    UNLOCKABLE_STATUSES,
    accept_stage1,
    accept_stage2,
    admin_delete_idr,
    admin_unlock_idr,
    approve_stage1,
    approve_stage2,
    find_idr_by_number,
    get_idr_by_id,
    list_review_queue,
    return_idr,
)
from api.schemas.auth import UserOut
from api.schemas.idr import (
    Idr,
    IdrConflict,
    IdrListItem,
    IdrListResponse,
    IdrResponse,
    IdrReturn,
    StageOneAccept,
)
from api.schemas.field_edit import PayItemsUntouched
from api.services.auth import current_admin, current_user, demo_idr_fence, require_project_role
from api.services.field_edits import FieldEditError, untouched_message, untouched_pay_items
from api.services.quantities import quantity_rows_for_idr
from api.services.signatures import SignatureStorageError, snapshot_signature_for_idr

logger = logging.getLogger(__name__)

# Every route needs a signed-in user; each route under an IDR also names the project roles that may call it
router = APIRouter(prefix="/v1/idrs", tags=["Review"], dependencies=[Depends(current_user), Depends(demo_idr_fence)])

stage_one_reviewer = require_project_role("oe", "re")
resident_engineer = require_project_role("re")

NUMBER_CONFLICT = {409: {"model": IdrConflict, "description": "The IDR number is in use on this project"}}
PAY_ITEMS_WAITING = {400: {"model": PayItemsUntouched, "description": "Pay items still wait on the reviewer"}}


def _load_idr(idr_id: UUID) -> dict[str, Any]:
    """
    Fetch the IDR a review route acts on.
    Takes the IDR uuid.
    Returns the IDR dict; raises 404 if there is none.
    """
    idr = get_idr_by_id(idr_id)

    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")

    return idr


def _must_be_reviewer(idr: dict[str, Any], column: str, user: UserOut) -> bool:
    """
    Check the user is the reviewer who accepted the IDR at a stage. An admin stands in for any reviewer.
    Takes the IDR dict, the reviewer column for the stage and the signed-in user.
    Returns whether the statement must check the reviewer too (False for an admin); raises 403 for anyone else.
    """
    if user.role == "admin":
        return False

    if idr[column] != user.uuid:
        raise HTTPException(status_code=403, detail="Only the reviewer who accepted this IDR can do this")

    return True


def _moved(rows: Optional[list[dict[str, Any]]], message: str) -> IdrResponse:
    """
    Turn a review statement's result into the response.
    Takes the rows the statement returned and the success message.
    Returns an IdrResponse; raises 500 if the statement failed and 409 if the IDR changed under it.
    """
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to update IDR")

    if not rows:
        raise HTTPException(status_code=409, detail="IDR changed during review; reload and try again")

    return IdrResponse(status="success", message=message, data=Idr.model_validate(rows[0]))


def _pay_items_waiting(idr: dict[str, Any], user: UserOut) -> Optional[JSONResponse]:
    """
    Check the user has attested to every pay item at the IDR's current stage, which approving the stage requires (of an admin standing in, too).
    Takes the IDR dict (in review) and the user about to approve.
    Returns a 400 response naming the untouched items (untouched: pay_item_id, report_id, item_no, budget_code), or None when none is left; raises 500 if the check can't be made.
    """
    try:
        untouched = untouched_pay_items(idr, user)
    except FieldEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    if not untouched:
        return None

    waiting = PayItemsUntouched(detail=untouched_message(len(untouched)), untouched=untouched)
    return JSONResponse(status_code=400, content=waiting.model_dump(mode="json"))


def _number_conflict(idr: dict[str, Any], idr_number: str) -> Optional[JSONResponse]:
    """
    Look for another IDR on the project already using an IDR number.
    Takes the IDR dict and the number wanted for it.
    Returns a 409 response naming the other IDR (existing_idr_id), or None when the number is free.
    """
    existing_idr_id = find_idr_by_number(idr["project_id"], idr_number, idr["idr_id"])

    if existing_idr_id is None:
        return None

    conflict = IdrConflict(detail="This IDR number is already in use on this project", existing_idr_id=existing_idr_id)
    return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))


@router.get("/queue", response_model=IdrListResponse)
def review_queue(
    status: Literal["submitted", "stage1_review", "stage2_review"],
    user: UserOut = Depends(current_user),
) -> IdrListResponse:
    """
    List the IDRs waiting in one review queue, oldest submission first, on the projects where the signed-in user works that queue: OE or RE for submitted and stage1_review, RE for stage2_review. An admin sees every project.
    Takes the queue's status as a query parameter, and the signed-in user.
    Returns an IdrListResponse (empty for a user with no such role anywhere), or raises 500 on a query failure.
    """
    rows = list_review_queue(status, user.uuid, user.role == "admin")

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to list the review queue")

    return IdrListResponse(
        status="success",
        message=f"{len(rows)} IDR(s)",
        data=[IdrListItem.model_validate(row) for row in rows],
    )


@router.post("/{idr_id}/accept-stage1", response_model=IdrResponse, responses=NUMBER_CONFLICT)
def accept_for_stage1(
    idr_id: UUID, body: Optional[StageOneAccept] = None, user: UserOut = Depends(stage_one_reviewer)
) -> Union[IdrResponse, JSONResponse]:
    """
    Pick a submitted IDR up for Stage 1: the signed-in user becomes its Stage 1 reviewer and, the first time through, gives it its IDR number. A resubmitted IDR keeps the number it has, and the body is ignored.
    Takes the IDR uuid as a path parameter, an optional StageOneAccept body and the signed-in OE or RE.
    Returns an IdrResponse; raises 403 (not an OE or RE on the project), 404 (no IDR), 409 (not submitted, or the number is in use, with existing_idr_id) and 400 (no number given for an IDR without one).
    """
    idr = _load_idr(idr_id)

    if idr["status"] != "submitted":
        raise HTTPException(status_code=409, detail="Only a submitted IDR can be accepted for Stage 1")

    idr_number = idr["idr_number"]

    if idr_number is None:
        idr_number = ((body.idr_number if body else None) or "").strip()

        if not idr_number:
            raise HTTPException(status_code=400, detail="An IDR number is required to accept this IDR")

        conflict = _number_conflict(idr, idr_number)

        if conflict is not None:
            return conflict

    try:
        rows = accept_stage1(idr_id, user.uuid, idr_number)
    except IdrNumberTakenError:
        # Another reviewer took the number between the check above and the statement
        conflict = _number_conflict(idr, idr_number)

        if conflict is None:
            raise HTTPException(status_code=500, detail="Failed to update IDR")

        return conflict

    return _moved(rows, "IDR accepted for Stage 1")


@router.post("/{idr_id}/approve-stage1", response_model=IdrResponse, responses=PAY_ITEMS_WAITING)
def approve_at_stage1(idr_id: UUID, user: UserOut = Depends(stage_one_reviewer)) -> Union[IdrResponse, JSONResponse]:
    """
    Pass an IDR from Stage 1 on to Stage 2. Only the reviewer who accepted it at Stage 1 (or an admin) can, and only once they have approved, revised or added every pay item at this stage.
    Takes the IDR uuid as a path parameter and the signed-in OE or RE; no body.
    Returns an IdrResponse; raises 403 (not an OE or RE on the project, or not its Stage 1 reviewer), 404 (no IDR), 409 (not in Stage 1 review) and 400 with the untouched pay items (untouched) when any is left.
    """
    idr = _load_idr(idr_id)

    if idr["status"] != "stage1_review":
        raise HTTPException(status_code=409, detail="Only an IDR in Stage 1 review can be approved for Stage 2")

    as_reviewer = _must_be_reviewer(idr, "stage1_reviewer_uuid", user)

    waiting = _pay_items_waiting(idr, user)

    if waiting is not None:
        return waiting

    return _moved(approve_stage1(idr_id, user.uuid, as_reviewer), "IDR approved at Stage 1")


@router.post("/{idr_id}/accept-stage2", response_model=IdrResponse)
def accept_for_stage2(idr_id: UUID, user: UserOut = Depends(resident_engineer)) -> IdrResponse:
    """
    Pick an IDR up for Stage 2: the signed-in RE becomes its RE reviewer. Accepting again is allowed; the last to accept is the reviewer.
    Takes the IDR uuid as a path parameter and the signed-in RE; no body.
    Returns an IdrResponse; raises 403 (not an RE on the project), 404 (no IDR) and 409 (not in Stage 2 review).
    """
    idr = _load_idr(idr_id)

    if idr["status"] != "stage2_review":
        raise HTTPException(status_code=409, detail="Only an IDR in Stage 2 review can be accepted for Stage 2")

    return _moved(accept_stage2(idr_id, user.uuid), "IDR accepted for Stage 2")


@router.post("/{idr_id}/approve-stage2", response_model=IdrResponse, responses=PAY_ITEMS_WAITING)
def approve_at_stage2(idr_id: UUID, user: UserOut = Depends(resident_engineer)) -> Union[IdrResponse, JSONResponse]:
    """
    Approve an IDR for good, signed by the signed-in user: copy their signature to the IDR, then mark it approved and stamp the signature. Its pay-item quantities are written to icid.quantities in the same statement. Only the RE who accepted it at Stage 2 (or an admin) can, and only once they have approved, revised or added every pay item at this stage.
    Takes the IDR uuid as a path parameter and the signed-in RE; no body.
    Returns an IdrResponse; raises 403 (not an RE on the project, or not its RE reviewer), 404 (no IDR), 409 (not in Stage 2 review), 400 with the untouched pay items (untouched) when any is left, 400 (the user has no signature), 500 (the IDR's reports couldn't be read) and 502 (the signature couldn't be copied).
    """
    idr = _load_idr(idr_id)

    if idr["status"] != "stage2_review":
        raise HTTPException(status_code=409, detail="Only an IDR in Stage 2 review can be approved")

    as_reviewer = _must_be_reviewer(idr, "re_reviewer_uuid", user)

    waiting = _pay_items_waiting(idr, user)

    if waiting is not None:
        return waiting

    if user.signature_path is None:
        raise HTTPException(status_code=400, detail="Signature required before approving")

    # The IDR's pay items, as its reports hold them now, go to icid.quantities in the statement that approves it
    quantity_rows = quantity_rows_for_idr(idr)

    if quantity_rows is None:
        raise HTTPException(status_code=500, detail="Failed to load IDR reports")

    # As at submit, the copy comes first: an approved IDR must never point at a signature that isn't there. If the
    # approval below doesn't go through, the copy is left behind, unreferenced.
    try:
        signature_copy = snapshot_signature_for_idr(user.signature_path, idr_id, "re")
    except SignatureStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        rows = approve_stage2(idr_id, user.uuid, signature_copy, as_reviewer, quantity_rows)
    except Exception:
        logger.warning("Approval of IDR %s failed after its RE signature was copied; %s is left orphaned", idr_id, signature_copy)
        raise

    if not rows:
        logger.warning("IDR %s was not approved after its RE signature was copied; %s is left orphaned", idr_id, signature_copy)

    return _moved(rows, "IDR approved")


@router.post("/{idr_id}/return", response_model=IdrResponse)
def return_from_review(idr_id: UUID, body: IdrReturn, user: UserOut = Depends(stage_one_reviewer)) -> IdrResponse:
    """
    Send an IDR back from review with a comment. From Stage 1 it goes to its inspector, as a draft again; from Stage 2 the RE sends it to the inspector or back to the OE (Stage 1 review). Only the reviewer who accepted it at its current stage (or an admin) can.
    Takes the IDR uuid as a path parameter, an IdrReturn body (to, comment) and the signed-in OE or RE.
    Returns an IdrResponse; raises 400 (blank comment), 403 (not an OE or RE on the project, or not the stage's reviewer), 404 (no IDR) and 409 (not under review, or returned to the OE from Stage 1).
    """
    comment = body.comment.strip()

    if not comment:
        raise HTTPException(status_code=400, detail="A comment is required to return an IDR")

    idr = _load_idr(idr_id)

    if idr["status"] not in ("stage1_review", "stage2_review"):
        raise HTTPException(status_code=409, detail="Only an IDR under review can be returned")

    if idr["status"] == "stage1_review" and body.to == "oe":
        raise HTTPException(status_code=409, detail="Only an IDR in Stage 2 review can be returned to the OE")

    reviewer_column = "re_reviewer_uuid" if idr["status"] == "stage2_review" else "stage1_reviewer_uuid"
    as_reviewer = _must_be_reviewer(idr, reviewer_column, user)

    rows = return_idr(idr_id, user.uuid, idr["status"], body.to, comment, as_reviewer)

    return _moved(rows, "IDR returned to the inspector" if body.to == "inspector" else "IDR returned to the OE")


@router.post("/{idr_id}/admin/unlock", response_model=IdrResponse)
def unlock_idr(idr_id: UUID, user: UserOut = Depends(current_admin)) -> IdrResponse:
    """
    Unlock an approved IDR (or one already back in Stage 2 review) for the RE to review again: it moves to stage2_review with the RE's signature and the RE reviewer cleared, so an RE must accept it and approve it afresh. Its IDR number stays. Admins only; the admin does not approve it.
    Takes the IDR uuid as a path parameter and the signed-in admin; no body.
    Returns an IdrResponse; raises 403 (not an admin), 404 (no IDR), 400 (a draft, submitted, Stage 1 or deleted IDR: nothing to unlock) and 409 (it changed meanwhile).
    """
    idr = _load_idr(idr_id)

    if idr["deleted_at"] is not None or idr["status"] not in UNLOCKABLE_STATUSES:
        raise HTTPException(status_code=400, detail="Only an approved IDR, or one in Stage 2 review, can be unlocked")

    return _moved(admin_unlock_idr(idr_id, user.uuid), "IDR unlocked for RE review")


@router.post("/{idr_id}/admin/delete", response_model=IdrResponse)
def delete_idr(idr_id: UUID, user: UserOut = Depends(current_admin)) -> IdrResponse:
    """
    Soft-delete an IDR, whatever its status: it is marked deleted, with when and by whom, and kept. It leaves every list (an admin can still ask for deleted IDRs) and frees its day and its IDR number. Admins only. Deleting an IDR that is already deleted succeeds and changes nothing.
    Takes the IDR uuid as a path parameter and the signed-in admin; no body.
    Returns an IdrResponse with the deleted IDR; raises 403 (not an admin), 404 (no IDR) and 500 if the delete fails.
    """
    idr = _load_idr(idr_id)

    if idr["deleted_at"] is not None:
        return IdrResponse(status="success", message="IDR was already deleted", data=Idr.model_validate(idr))

    rows = admin_delete_idr(idr_id, user.uuid)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to delete IDR")

    if not rows:
        # Deleted by someone else between the read and the statement: the same outcome
        return IdrResponse(status="success", message="IDR was already deleted", data=Idr.model_validate(_load_idr(idr_id)))

    return IdrResponse(status="success", message="IDR deleted", data=Idr.model_validate(rows[0]))
