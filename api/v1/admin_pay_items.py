from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.contract_items import (
    clear_project_pay_items,
    create_project_pay_item,
    delete_project_pay_item,
    get_project_pay_item,
    get_project_pay_item_by_key,
    list_project_pay_items,
    update_project_pay_item,
)
from api.queries.projects import get_project_by_id
from api.schemas.pay_item import (
    PayItemClearResponse,
    PayItemClearResult,
    PayItemConflict,
    PayItemCreate,
    PayItemListResponse,
    PayItemOut,
    PayItemResponse,
    PayItemUpdate,
)
from api.services.auth import current_admin
from api.v1._shared import clean_text


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

# The text fields: the body's name, the column it is stored in and the longest value it takes
TEXT_FIELDS = (
    ("pay_item_ref", "item_no", 32),
    ("description", "description", 500),
    ("unit", "unit", 16),
    ("budget_code", "budget_code", 50),
)
MAX_SPEC_SECTION_LENGTH = 32
# The amounts: the body's name, the column it is stored in and the most decimal places it takes
AMOUNT_FIELDS = (("quantity", "bid_quantity", 10), ("unit_price", "bid_unit_price", 6))
# An amount is below this, so it goes out as a JSON number
AMOUNT_LIMIT = Decimal(10) ** 15

CONFLICT_DETAIL = "A pay item with this ref and budget code already exists for this project"


def _clean_amount(value: Decimal, field: str, max_places: int) -> Decimal:
    """
    Check a quantity or a price off the request. Zero is allowed.
    Takes the value, the field's name (for the error) and the most decimal places it may have (trailing zeros don't count).
    Returns the value; raises 400 for one that isn't a finite number, is negative, is too large or has too many decimal places.
    """
    if not value.is_finite():
        raise HTTPException(status_code=400, detail=f"{field} must be a number")
    if value < 0:
        raise HTTPException(status_code=400, detail=f"{field} cannot be negative")
    if value >= AMOUNT_LIMIT:
        raise HTTPException(status_code=400, detail=f"{field} is too large")
    if -value.normalize().as_tuple().exponent > max_places:
        raise HTTPException(status_code=400, detail=f"{field} cannot have more than {max_places} decimal places")
    return value


def _clean_fields(body: PayItemCreate | PayItemUpdate) -> dict[str, Any]:
    """
    Read a pay item's fields off the request: text trimmed (the unit in capitals too, the budget code otherwise as typed), amounts checked. A field sent as null is left out; spec_section is optional, and a blank or null one is None.
    Takes the request body.
    Returns the values keyed by column, for the fields sent; raises 400 for a value that doesn't fit.
    """
    fields: dict[str, Any] = {}
    for name, column, max_length in TEXT_FIELDS:
        value = getattr(body, name)
        if value is not None:
            fields[column] = clean_text(value.upper() if name == "unit" else value, name, max_length)
    for name, column, max_places in AMOUNT_FIELDS:
        value = getattr(body, name)
        if value is not None:
            fields[column] = _clean_amount(value, name, max_places)
    if "spec_section" in body.model_fields_set:
        fields["spec_section"] = clean_text(body.spec_section, "spec_section", MAX_SPEC_SECTION_LENGTH, required=False)
    return fields


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


@router.delete("/{pay_item_uuid}", status_code=204)
def delete_pay_item(project_id: str, pay_item_uuid: UUID) -> None:
    """
    Delete one pay item of a project. Admins only. Reports that already use the item keep their copy of it.
    Takes the project id and the pay item's uuid as path parameters.
    Returns nothing (204); raises 403 for a user who isn't an admin and 404 for an unknown project or an item the project doesn't have.
    """
    if not delete_project_pay_item(project_id, pay_item_uuid):
        raise HTTPException(status_code=404, detail="Pay item not found")


@router.delete("", response_model=PayItemClearResponse)
def clear_pay_items(project_id: str) -> PayItemClearResponse:
    """
    Delete every pay item of a project. Admins only. Reports that already use the items keep their copies of them.
    Takes the project id as a path parameter.
    Returns a PayItemClearResponse with how many were deleted; raises 403 for a user who isn't an admin, 404 for an unknown project and 500 if the count doesn't come back.
    """
    deleted_count = clear_project_pay_items(project_id)

    if deleted_count is None:
        raise HTTPException(status_code=500, detail="Failed to clear pay items")

    return PayItemClearResponse(
        status="success",
        message=f"Deleted {deleted_count} pay items from project {project_id}",
        data=PayItemClearResult(deleted_count=deleted_count),
    )
