from fastapi import APIRouter, Depends, HTTPException

from api.queries.idr_report_sites import property_work_history
from api.schemas.property_work import WorkHistoryEntry, WorkHistoryResponse
from api.services.auth import current_user, property_member

# A property's work across IDRs (Slice V), under /v1/properties: what reports imported it. For the project's members.
router = APIRouter(prefix="/v1/properties", tags=["Property work"], dependencies=[Depends(current_user)])


@router.get("/{property_id}/work-history", response_model=WorkHistoryResponse)
def get_work_history(found: dict = Depends(property_member)) -> WorkHistoryResponse:
    """
    List every import of a property into a report, across the project's IDRs, newest work date first: the work done, the IDR's number (none for one nobody accepted), day, status and inspector. Deleted and returned IDRs are included (deleted_at, return_reason); a permanently deleted IDR's are gone with it. For a user on the project, or an admin.
    Takes the property (loaded and checked by property_member from the property_id path parameter).
    Returns a WorkHistoryResponse; raises 403 for a user who isn't on the project, 404 for an unknown property and 500 if the history can't be read.
    """
    rows = property_work_history(found["id"])
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch the property's work history")
    return WorkHistoryResponse(status="success", message="Work history retrieved",
                               data=[WorkHistoryEntry.model_validate(row) for row in rows])
