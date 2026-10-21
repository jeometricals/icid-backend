from fastapi import APIRouter, Depends, HTTPException

from api.queries.idr_report_sites import property_work_history
from api.queries.property_pay_items import property_pay_item_lines
from api.schemas.property_work import PropertyPayItem, PropertyPayItemsResponse, WorkHistoryEntry, WorkHistoryResponse
from api.services.auth import current_user, property_member
from api.services.property_pay_items import pay_item_summary

# A property's work across IDRs (Slice V), under /v1/properties: what reports imported it, and the pay items IDRs
# report on it. For the project's members.
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


@router.get("/{property_id}/pay-item-summary", response_model=PropertyPayItemsResponse)
def get_pay_item_summary(found: dict = Depends(property_member)) -> PropertyPayItemsResponse:
    """
    Sum the pay items IDRs report on a property, read-only: its flags and curb segments (claimed by approved IDRs, attributed to open ones) and the pay items entered on it as an imported site, each pay item with its total (a flag or segment two open IDRs cover counted once) and each IDR's part, newest work date first. Deleted IDRs are left out. For a user on the project, or an admin.
    Takes the property (loaded and checked by property_member from the property_id path parameter).
    Returns a PropertyPayItemsResponse; raises 403 for a user who isn't on the project, 404 for an unknown property and 500 if the pay items can't be read.
    """
    lines = property_pay_item_lines(found["id"])
    if lines is None:
        raise HTTPException(status_code=500, detail="Failed to fetch the property's pay items")
    return PropertyPayItemsResponse(status="success", message="Pay items retrieved",
                                    data=[PropertyPayItem.model_validate(item) for item in pay_item_summary(lines)])
