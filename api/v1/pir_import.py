from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.queries.contract_items import list_project_pay_items
from api.queries.flags import list_pir_flag_nos
from api.queries.match_lines import list_match_lines_for_pir
from api.queries.pir_pages import list_pages_for_pir
from api.schemas.auth import UserOut
from api.schemas.pir_import import PirImportResponse, SmartPirImport
from api.services.auth import current_user
from api.services.pay_item_units import flag_unit
from api.services.flag_pay_items import default_pay_items
from api.services.pir_import import run_import
from api.v1._shared import refuse_deleted_pir_for_member, writable_pir

# A PIR's Smart PIR import, under /v1/pirs (a router of its own there); writable_pir keeps demo users out
router = APIRouter(prefix="/v1/pirs", tags=["PIR import"], dependencies=[Depends(current_user)])


@router.post("/{pir_id}/import", response_model=PirImportResponse)
def import_pir(
    pir_id: UUID, body: SmartPirImport, found: dict = Depends(writable_pir), user: UserOut = Depends(current_user)
) -> PirImportResponse:
    """
    Import a Smart PIR file onto a PIR's live pages: flags (with their non-assessable areas and, optionally, the signed PIR's own flag numbers, each once per page), features, segments, match lines, title blocks and page scales and rotations, added to what the pages have (a page's title block is replaced). Every item is checked on its own; a bad one is dropped, with its reason, and the rest go in together, created by the signed-in user. For a user on its project, or an admin; not a demo user. Only an admin imports onto a soft-deleted PIR, or a PIR of a soft-deleted property.
    Takes the PIR's id as a path parameter, the file in the body (checked as SmartPirImport), the PIR (loaded by writable_pir) and the user.
    Returns a PirImportResponse with what was inserted and dropped; raises 400 when the file's pir_id isn't the path's or the PIR or its property is deleted, 403 for a demo user or a user who isn't on its project, 404 for an unknown PIR, 422 for a file that isn't a Smart PIR import, and 500 if the PIR's pages or pay items can't be read or the statement fails.
    """
    if body.pir_id != pir_id:
        raise HTTPException(status_code=400, detail="The file's pir_id is not this PIR")
    refuse_deleted_pir_for_member(found, user)

    pages = list_pages_for_pir(pir_id)
    pay_items = list_project_pay_items(found["project_id"])
    lines = list_match_lines_for_pir(pir_id)
    numbers = list_pir_flag_nos(pir_id)
    if pages is None or pay_items is None or lines is None or numbers is None:
        raise HTTPException(status_code=500, detail="Failed to read the PIR's pages and pay items")
    live_labels: dict[str, set[int]] = {}
    for line in lines:
        live_labels.setdefault(line["label"], set()).add(line["page_number"])
    live_flag_nos: dict[int, set[str]] = {}
    for row in numbers:
        live_flag_nos.setdefault(row["page_number"], set()).add(row["pir_flag_no"])

    result = run_import(
        pir_id, user.uuid, body, {page["page_number"]: page["pir_page_id"] for page in pages},
        {item["pay_item_uuid"] for item in pay_items},
        {item["pay_item_uuid"] for item in pay_items if flag_unit(item["unit"]) is not None},
        {page["page_number"]: page.get("scale_px_per_ft") for page in pages}, live_labels, live_flag_nos,
        default_pay_items(pay_items),
    )
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to import the PIR")

    total = sum(result.inserted.model_dump().values())
    dropped = sum(result.dropped.model_dump().values())
    return PirImportResponse(status="success", message=f"Imported {total} items, dropped {dropped}", data=result)
