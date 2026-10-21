from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from psycopg.errors import ForeignKeyViolation

from api.queries.contract_items import (
    bulk_upsert_pay_items,
    clear_project_pay_items,
    create_project_pay_item,
    delete_project_pay_item,
    get_project_pay_item,
    get_project_pay_item_by_key,
    list_project_pay_items,
    update_project_pay_item,
)
from api.queries.idr_report_site_pay_items import pay_item_site_uses
from api.queries.projects import get_project_by_id
from api.schemas.pay_item import (
    PayItemClearResponse,
    PayItemClearResult,
    PayItemConflict,
    PayItemCreate,
    PayItemImportCommitResponse,
    PayItemImportPreview,
    PayItemImportPreviewResponse,
    PayItemImportRefused,
    PayItemImportResult,
    PayItemListResponse,
    PayItemOut,
    PayItemResponse,
    PayItemUpdate,
)
from api.services.auth import current_admin
from api.services.pay_item_import import ClassifyResult, CsvFormatError, classify_rows, parse_csv
from api.services.pay_items import AMOUNT_FIELDS, TEXT_FIELDS, PayItemValueError, clean_pay_item_fields


def known_project(project_id: str) -> None:
    """
    FastAPI dependency: the project in the path must exist.
    Takes the project_id path parameter.
    Returns nothing; raises 404 for a project that doesn't exist.
    """
    if get_project_by_id(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")


# Every route needs a signed-in admin (checked first), then a project that exists
router = APIRouter(
    prefix="/v1/admin/projects/{project_id}/pay-items",
    tags=["Admin pay items"],
    dependencies=[Depends(current_admin), Depends(known_project)],
)

CONFLICT_DETAIL = "A pay item with this ref and budget code already exists for this project"
# The largest CSV an import takes
MAX_IMPORT_BYTES = 2 * 1024 * 1024


def _clean_fields(body: PayItemCreate | PayItemUpdate) -> dict[str, Any]:
    """
    Read a pay item's fields off the request, checked as clean_pay_item_fields checks them: text trimmed, the unit in its stored form, amounts checked. A field sent as null is left out; spec_section is optional, and a blank or null one is None.
    Takes the request body.
    Returns the values keyed by column, for the fields sent; raises 400 for a value that doesn't fit.
    """
    values = {name: getattr(body, name) for name, _, _ in TEXT_FIELDS + AMOUNT_FIELDS}
    if "spec_section" in body.model_fields_set:
        values["spec_section"] = body.spec_section
    try:
        return clean_pay_item_fields(values)
    except PayItemValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _read_import(project_id: str, file: UploadFile) -> ClassifyResult:
    """
    Read an uploaded pay-item CSV and sort its rows against the project's items. Writes nothing.
    Takes the project id and the uploaded file.
    Returns the ClassifyResult; raises 413 for a file over 2 MB, 400 for one that can't be read as a pay-item CSV, and 500 if the project's items can't be read.
    """
    content = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="The file cannot be larger than 2 MB")
    try:
        parsed = parse_csv(content)
    except CsvFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = classify_rows(project_id, parsed)

    if result is None:
        raise HTTPException(status_code=500, detail="Failed to fetch pay items")

    return result


def _conflict(existing: dict[str, Any]) -> JSONResponse:
    """
    Build the 409 for an item number and budget code the project already has.
    Takes the pay item that has them.
    Returns the response, naming that item.
    """
    conflict = PayItemConflict(detail=CONFLICT_DETAIL, existing_pay_item_uuid=existing["pay_item_uuid"])
    return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))


@router.get("", response_model=PayItemListResponse)
def list_pay_items(project_id: str) -> PayItemListResponse:
    """
    List every pay item of a project, by budget code then item number. Admins only.
    Takes the project id as a path parameter.
    Returns a PayItemListResponse; raises 403 for a user who isn't an admin, 404 for an unknown project and 500 if the list can't be read.
    """
    rows = list_project_pay_items(project_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch pay items")

    return PayItemListResponse(
        status="success",
        message=f"Pay items for project {project_id}",
        data=[PayItemOut.model_validate(row) for row in rows],
    )


@router.post("", response_model=PayItemResponse, status_code=201, responses={409: {"model": PayItemConflict}})
def create_pay_item(project_id: str, body: PayItemCreate) -> PayItemResponse | JSONResponse:
    """
    Add a pay item to a project. Admins only. The same item number can be under several budget codes, once each.
    Takes the project id as a path parameter and the item's ref, description, unit, quantity, unit price, budget code and optional spec section in the body.
    Returns 201 with a PayItemResponse; raises 400 for a value that doesn't fit, 403 for a user who isn't an admin, 404 for an unknown project, and 409 (with existing_pay_item_uuid) when the project has that ref under that budget code.
    """
    fields = _clean_fields(body)

    row = create_project_pay_item(
        project_id, fields["item_no"], fields["description"], fields["unit"], fields["bid_quantity"],
        fields["bid_unit_price"], fields["budget_code"], fields.get("spec_section"),
    )

    if row is None:
        existing = get_project_pay_item_by_key(project_id, fields["item_no"], fields["budget_code"])

        if existing is None:
            raise HTTPException(status_code=500, detail="Failed to create pay item")

        return _conflict(existing)

    return PayItemResponse(data=PayItemOut.model_validate(row))


@router.patch("/{pay_item_uuid}", response_model=PayItemResponse, responses={409: {"model": PayItemConflict}})
def update_pay_item(project_id: str, pay_item_uuid: UUID, body: PayItemUpdate) -> PayItemResponse | JSONResponse:
    """
    Change some of a project's pay item. Admins only. A field left out, or sent as null, keeps its value; a blank or null spec_section clears it.
    Takes the project id and the pay item's uuid as path parameters and the fields to change in the body.
    Returns a PayItemResponse with the item as it now is; raises 400 when nothing is sent or a value doesn't fit, 403 for a user who isn't an admin, 404 for an unknown project or an item the project doesn't have, and 409 (with existing_pay_item_uuid) when another item of the project has the ref and budget code this one would end up with.
    """
    fields = _clean_fields(body)

    if not fields:
        raise HTTPException(status_code=400, detail="Send at least one field to change")

    row = update_project_pay_item(project_id, pay_item_uuid, fields)

    if row is None:
        current = get_project_pay_item(project_id, pay_item_uuid)

        if current is None:
            raise HTTPException(status_code=404, detail="Pay item not found")

        existing = get_project_pay_item_by_key(
            project_id, fields.get("item_no", current["pay_item_ref"]), fields.get("budget_code", current["budget_code"])
        )

        if existing is None or existing["pay_item_uuid"] == pay_item_uuid:
            raise HTTPException(status_code=500, detail="Failed to update pay item")

        return _conflict(existing)

    return PayItemResponse(data=PayItemOut.model_validate(row))


def _in_use(project_id: str, pay_item_uuid: Optional[UUID] = None) -> Optional[JSONResponse]:
    """
    Refuse deleting pay items entered on imported sites (Slice V1): those rows name the item, and deleting it would lose them.
    Takes the project id and the pay item's uuid (None for every item of the project).
    Returns a 409 JSONResponse naming the IDRs (idrs: idr_id, idr_number, report_date, item_no, budget_code), or None when no site holds them; raises 500 when that can't be read.
    """
    uses = pay_item_site_uses(project_id, pay_item_uuid)
    if uses is None:
        raise HTTPException(status_code=500, detail="Failed to check the pay items' use")
    if not uses:
        return None
    names = sorted({use["idr_number"] or f"a draft of {use['report_date']:%m/%d/%y}" for use in uses})
    return JSONResponse(status_code=409, content={
        "detail": f"{'This pay item is' if pay_item_uuid else 'Pay items are'} entered on imported sites of "
                  f"IDR{'s' if len(names) > 1 else ''} {', '.join(names)}: remove {'it' if pay_item_uuid else 'them'} there first",
        "idrs": [{"idr_id": str(use["idr_id"]), "idr_number": use["idr_number"], "report_date": use["report_date"].isoformat(),
                  "item_no": use["item_no"], "budget_code": use["budget_code"]} for use in uses],
    })


@router.delete("/{pay_item_uuid}", status_code=204, response_model=None, responses={409: {"description": "The pay item is entered on imported sites"}})
def delete_pay_item(project_id: str, pay_item_uuid: UUID) -> Optional[JSONResponse]:
    """
    Delete one pay item of a project. Admins only. Reports that already use the item keep their copy of it; one entered on an imported site can't be deleted.
    Takes the project id and the pay item's uuid as path parameters.
    Returns nothing (204); raises 403 for a user who isn't an admin, 404 for an unknown project or an item the project doesn't have, and 409 naming the IDRs whose sites hold it.
    """
    refused = _in_use(project_id, pay_item_uuid)
    if refused is not None:
        return refused
    try:
        deleted = delete_project_pay_item(project_id, pay_item_uuid)
    except ForeignKeyViolation:  # entered on a site meanwhile
        return _in_use(project_id, pay_item_uuid) or JSONResponse(status_code=409, content={"detail": "Pay item is in use"})
    if not deleted:
        raise HTTPException(status_code=404, detail="Pay item not found")
    return None


@router.delete("", response_model=PayItemClearResponse, responses={409: {"description": "Pay items are entered on imported sites"}})
def clear_pay_items(project_id: str) -> PayItemClearResponse | JSONResponse:
    """
    Delete every pay item of a project. Admins only. Reports that already use the items keep their copies of them; while any is entered on an imported site, nothing is deleted.
    Takes the project id as a path parameter.
    Returns a PayItemClearResponse with how many were deleted; raises 403 for a user who isn't an admin, 404 for an unknown project, 409 naming the IDRs whose sites hold any, and 500 if the count doesn't come back.
    """
    refused = _in_use(project_id)
    if refused is not None:
        return refused
    try:
        deleted_count = clear_project_pay_items(project_id)
    except ForeignKeyViolation:
        return _in_use(project_id) or JSONResponse(status_code=409, content={"detail": "Pay items are in use"})

    if deleted_count is None:
        raise HTTPException(status_code=500, detail="Failed to clear pay items")

    return PayItemClearResponse(
        status="success",
        message=f"Deleted {deleted_count} pay items from project {project_id}",
        data=PayItemClearResult(deleted_count=deleted_count),
    )


@router.post("/import/preview", response_model=PayItemImportPreviewResponse)
def preview_pay_item_import(project_id: str, file: UploadFile = File(...)) -> PayItemImportPreviewResponse:
    """
    Show what importing a CSV of pay items would do. Admins only. Writes nothing.
    Takes the project id as a path parameter and the CSV as the multipart field "file": UTF-8, with a header row naming pay_item_ref, description, quantity, unit, unit_price and budget_code (spec_section optional), in any order.
    Returns a PayItemImportPreviewResponse with each row as a new item, an overwrite of an item the project has, or an error; raises 400 for a file that can't be read as a pay-item CSV, 403 for a user who isn't an admin, 404 for an unknown project and 413 for a file over 2 MB.
    """
    result = _read_import(project_id, file)

    return PayItemImportPreviewResponse(
        status="ok",
        message="Preview ready.",
        data=PayItemImportPreview(
            parsed_rows=result.parsed_rows,
            new_count=len(result.new),
            overwrite_count=len(result.overwrites),
            error_count=len(result.errors),
            ignored_columns=result.ignored_columns,
            new=result.new,
            overwrites=result.overwrites,
            errors=result.errors,
        ),
    )


@router.post(
    "/import/commit", response_model=PayItemImportCommitResponse, responses={400: {"model": PayItemImportRefused}}
)
def commit_pay_item_import(project_id: str, file: UploadFile = File(...)) -> PayItemImportCommitResponse | JSONResponse:
    """
    Import a CSV of pay items: the file is read and checked again, then every row is written in one statement, new items inserted and items the project has overwritten. Admins only.
    Takes the project id as a path parameter and the CSV as the multipart field "file", as the preview takes it.
    Returns a PayItemImportCommitResponse with how many were inserted and overwritten; raises 400 for a file that can't be read, has no rows, or has a row with an error (with the errors, and nothing written), 403 for a user who isn't an admin, 404 for an unknown project and 413 for a file over 2 MB.
    """
    result = _read_import(project_id, file)

    if result.errors:
        refused = PayItemImportRefused(
            detail=f"The file has {len(result.errors)} row(s) with errors; fix them and upload it again",
            error_count=len(result.errors),
            errors=result.errors,
        )
        return JSONResponse(status_code=400, content=refused.model_dump(mode="json"))

    if not result.to_write:
        raise HTTPException(status_code=400, detail="The file has no pay items")

    counts = bulk_upsert_pay_items(project_id, result.to_write, result.has_spec_section)

    if counts is None:
        raise HTTPException(status_code=500, detail="Failed to import pay items")

    return PayItemImportCommitResponse(
        status="ok",
        message="Imported pay items.",
        data=PayItemImportResult(inserted=counts[0], updated=counts[1]),
    )
