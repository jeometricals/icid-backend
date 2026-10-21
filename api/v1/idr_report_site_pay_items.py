from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.queries.idr_report_site_pay_items import (
    add_site_pay_item,
    delete_site_pay_item,
    idr_site_pay_items,
    list_site_pay_items,
    update_site_pay_item,
)
from api.queries.idr_report_sites import get_report_site
from api.queries.idr_reports import list_reports_for_idr
from api.schemas.auth import UserOut
from api.schemas.idr_report_site_pay_item import (
    PayItemSourcesData,
    PayItemSourcesResponse,
    SitePayItemCreate,
    SitePayItemListResponse,
    SitePayItemOut,
    SitePayItemResponse,
    SitePayItemUpdate,
)
from api.services.auth import current_user, demo_idr_fence
from api.services.idr_flags import IdrFlagError
from api.services.idr_segments import IdrSegmentError
from api.services.pay_item_sources import pay_item_sources
from api.v1.idr_flags import attribution_editor, load_idr
from api.v1.idr_report_sites import SITE_NOT_FOUND, report_of

# A fourth router under /v1/idrs (Slice V1): the pay items entered by hand on a report's imported sites, and an IDR's
# pay items by source. A demo user reaches only their own IDRs.
router = APIRouter(prefix="/v1/idrs", tags=["IDR site pay items"], dependencies=[Depends(current_user), Depends(demo_idr_fence)])

PAY_ITEM_NOT_FOUND = "Pay item not found on this site"
NOT_ON_PROJECT = "That pay item isn't one of this IDR's project"


def _site(idr_id: UUID, report_id: UUID, site_id: UUID) -> dict[str, Any]:
    """
    Fetch the site a pay-items route acts on, only if it is the report's, and the report the IDR's.
    Takes the IDR's, the report's and the site's uuids.
    Returns the site dict; raises 404 when the IDR has no such report or the report no such site.
    """
    report_of(idr_id, report_id)
    site = get_report_site(report_id, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail=SITE_NOT_FOUND)
    return site


def _response(row: dict[str, Any], message: str) -> SitePayItemResponse:
    """
    Wrap one site pay item in the response envelope.
    Takes the pay item dict and the message.
    Returns the SitePayItemResponse.
    """
    return SitePayItemResponse(status="success", message=message, data=SitePayItemOut.model_validate(row))


@router.get("/{idr_id}/reports/{report_id}/sites/{site_id}/pay-items", response_model=SitePayItemListResponse)
def list_pay_items(idr_id: UUID, report_id: UUID, site_id: UUID) -> SitePayItemListResponse:
    """
    List the pay items entered on a report's site, oldest first. For anyone who can read the IDR.
    Takes the IDR's, the report's and the site's uuids as path parameters.
    Returns a SitePayItemListResponse; raises 404 for an unknown IDR, a report that isn't its or a site that isn't the report's, and 500 if they can't be read.
    """
    load_idr(idr_id)
    _site(idr_id, report_id, site_id)
    rows = list_site_pay_items(report_id, site_id)
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch the site's pay items")
    return SitePayItemListResponse(status="success", message="Pay items retrieved",
                                   data=[SitePayItemOut.model_validate(row) for row in rows])


@router.post("/{idr_id}/reports/{report_id}/sites/{site_id}/pay-items", response_model=SitePayItemResponse, status_code=201)
def add_pay_item(
    report_id: UUID, site_id: UUID, body: SitePayItemCreate, idr: dict = Depends(attribution_editor),
    user: UserOut = Depends(current_user),
) -> SitePayItemResponse:
    """
    Enter a pay item on a report's site: a contract item of the IDR's project and its quantity, billed when the IDR is approved. Not checked against the site's flags or curb segments. Who may: as for importing the site (the IDR's inspector on a draft, the stage's reviewer in review, an admin).
    Takes the report's and the site's uuids as path parameters, the pay item in the body, the IDR (loaded and checked by attribution_editor) and the user.
    Returns 201 with a SitePayItemResponse; raises 400 for a contract item that isn't the project's, 403 or 409 as attribution_editor does, 404 for a report or site that isn't the IDR's, and 422 for a quantity not above 0.
    """
    _site(idr["idr_id"], report_id, site_id)
    row = add_site_pay_item(report_id, site_id, body.contract_item_id, body.quantity, body.note, user.uuid)
    if row is None:
        raise HTTPException(status_code=400, detail=NOT_ON_PROJECT)
    return _response(row, "Pay item added")


@router.patch("/{idr_id}/reports/{report_id}/sites/{site_id}/pay-items/{item_id}", response_model=SitePayItemResponse)
def update_pay_item(
    report_id: UUID, site_id: UUID, item_id: UUID, body: SitePayItemUpdate, idr: dict = Depends(attribution_editor)
) -> SitePayItemResponse:
    """
    Change a site pay item's quantity, its note (null clears it), or both. Who may: as for entering one.
    Takes the report's, the site's and the pay item's uuids as path parameters, the change in the body and the IDR (loaded and checked by attribution_editor).
    Returns a SitePayItemResponse; raises 403 or 409 as attribution_editor does, 404 for a report, site or pay item that isn't the IDR's, and 422 for a body naming nothing or a quantity not above 0.
    """
    _site(idr["idr_id"], report_id, site_id)
    row = update_site_pay_item(report_id, site_id, item_id, body.quantity, body.note, "note" in body.model_fields_set)
    if row is None:
        raise HTTPException(status_code=404, detail=PAY_ITEM_NOT_FOUND)
    return _response(row, "Pay item updated")


@router.delete("/{idr_id}/reports/{report_id}/sites/{site_id}/pay-items/{item_id}", status_code=204, response_class=Response)
def remove_pay_item(report_id: UUID, site_id: UUID, item_id: UUID, idr: dict = Depends(attribution_editor)) -> Response:
    """
    Remove a pay item from a report's site. Who may: as for entering one.
    Takes the report's, the site's and the pay item's uuids as path parameters and the IDR (loaded and checked by attribution_editor).
    Returns 204; raises 403 or 409 as attribution_editor does, 404 for a report, site or pay item that isn't the IDR's, and 500 if it can't be removed.
    """
    _site(idr["idr_id"], report_id, site_id)
    rows = delete_site_pay_item(report_id, site_id, item_id)
    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to remove the pay item")
    if not rows:
        raise HTTPException(status_code=404, detail=PAY_ITEM_NOT_FOUND)
    return Response(status_code=204)


@router.get("/{idr_id}/pay-item-sources", response_model=PayItemSourcesResponse)
def get_pay_item_sources(
    idr_id: UUID, report_id: UUID | None = Query(default=None, description="Take hand-entered pay items from this report only"),
) -> PayItemSourcesResponse:
    """
    Sum an IDR's pay items over their sources: its flags, its curb segments, the pay items entered on its sites and its reports' own (every report's, or report_id's only), each pay item with what each source adds and the sites and reports behind it. Display only. For anyone who can read the IDR.
    Takes the IDR's uuid as a path parameter and an optional report_id query parameter.
    Returns a PayItemSourcesResponse; raises 404 for an unknown IDR or a report that isn't its, and 500 if anything can't be read.
    """
    idr = load_idr(idr_id)
    if report_id is not None:
        report_of(idr_id, report_id)
    reports, site_items = list_reports_for_idr(idr_id), idr_site_pay_items(idr_id)
    if reports is None or site_items is None:
        raise HTTPException(status_code=500, detail="Failed to read the IDR's pay items")
    try:
        data = pay_item_sources(idr, reports, site_items, report_id)
    except (IdrFlagError, IdrSegmentError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return PayItemSourcesResponse(status="success", message="Pay item sources retrieved",
                                  data=PayItemSourcesData.model_validate(data))
