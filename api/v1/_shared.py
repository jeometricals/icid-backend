"""
What more than one router needs: reading the date and discipline filters, reading a text field off a request body,
checking a new password, shaping the rows of an IDR list, keeping soft-deleted rows (and their children) for
admins, the gate on a PIR's writes, and what the writes of a PIR's drawings share (the pay-item check, merging
and checking a PATCH).
Not a router itself.
"""

from datetime import date
from typing import Any, Optional, TypeVar
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from api.schemas.auth import UserOut
from api.queries.contract_items import get_project_pay_item
from api.schemas.idr import IdrListItem
from api.queries.properties import get_property
from api.services.auth import MAX_PASSWORD_BYTES, no_demo_pir_writes, pir_member
from api.services.disciplines import disciplines_for_report_types, report_types_for_discipline

# The shortest password sign-in takes
MIN_PASSWORD_LENGTH = 6


def day(value: Optional[str], name: str) -> Optional[date]:
    """
    Read a date filter.
    Takes the query parameter's text (None when it wasn't sent) and its name, for the message.
    Returns the date, or None when it wasn't sent; raises 400 for text that isn't a YYYY-MM-DD date.
    """
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be a date, YYYY-MM-DD") from exc


def day_range(date_from: Optional[str], date_to: Optional[str]) -> tuple[Optional[date], Optional[date]]:
    """
    Read the from and to date filters.
    Takes the two query parameters' text (None for one that wasn't sent).
    Returns (from, to) as dates; raises 400 for text that isn't a YYYY-MM-DD date, or from after to.
    """
    first, last = day(date_from, "from"), day(date_to, "to")

    if first is not None and last is not None and first > last:
        raise HTTPException(status_code=400, detail="from cannot be after to")

    return first, last


def report_types_for(disciplines: Optional[list[str]]) -> Optional[list[str]]:
    """
    Resolve a discipline filter to the report types that cover any of the disciplines.
    Takes the disciplines sent (None when the filter wasn't).
    Returns the report types, each once, or None without a filter; raises 400 for a discipline nothing covers.
    """
    if not disciplines:
        return None
    report_types: list[str] = []
    for discipline in disciplines:
        covered = report_types_for_discipline(discipline)
        if not covered:
            raise HTTPException(status_code=400, detail=f"Unknown discipline: {discipline}")
        report_types += [report_type for report_type in covered if report_type not in report_types]
    return report_types


def refuse_deleted_for_member(row: dict[str, Any], user: UserOut, detail: str) -> None:
    """
    Keep a soft-deleted row for admins: a project member who isn't one can't change it.
    Takes the row (anything with deleted_at), the signed-in user and the 400's detail.
    Returns nothing; raises 400 with that detail for a deleted row and a user who isn't an admin.
    """
    if row["deleted_at"] is not None and user.role != "admin":
        raise HTTPException(status_code=400, detail=detail)


# What a member who isn't an admin is told when they change something under a soft-deleted row
PIR_DELETED = "PIR is deleted"
PROPERTY_DELETED = "Property is deleted"
PAGE_DELETED = "Page is deleted"


# A page is the admin's once it, its PIR or its property is soft-deleted (read off the page itself)
PAGE_CHAIN = [("deleted_at", PAGE_DELETED), ("pir_deleted_at", PIR_DELETED), ("property_deleted_at", PROPERTY_DELETED)]
# A row drawn on a page is the admin's once its page, PIR or property is (read off the row)
PAGE_PARENTS = [
    ("page_deleted_at", PAGE_DELETED), ("pir_deleted_at", PIR_DELETED), ("property_deleted_at", PROPERTY_DELETED),
]
PAY_ITEM_ELSEWHERE = "Pay item belongs to a different project"
Checked = TypeVar("Checked", bound=BaseModel)


def check_pay_item(project_id: str, pay_item_id: Optional[UUID]) -> None:
    """
    Refuse a pay item that isn't one of the project's.
    Takes the project id and the pay item's id (None for none).
    Returns nothing; raises 400 when the project has no such pay item.
    """
    if pay_item_id is not None and get_project_pay_item(project_id, pay_item_id) is None:
        raise HTTPException(status_code=400, detail=PAY_ITEM_ELSEWHERE)


def validated(model: type[Checked], record: dict[str, Any]) -> Checked:
    """
    Check a row's whole record, as a PATCH leaves it, by the rules a new row is checked by.
    Takes the model a new row is checked as and the merged record keyed by field.
    Returns the parsed model; raises a 422 shaped as FastAPI's own when a rule is broken.
    """
    try:
        return model.model_validate(record)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors(include_url=False)]
        ) from exc


def changes_sent(body: BaseModel, clearable: set[str]) -> dict[str, Any]:
    """
    Read what a PATCH changes: the fields sent, a null counting as not sent except on the fields it clears.
    Takes the parsed body and the fields a null clears.
    Returns the changes keyed by field; raises 400 when there are none.
    """
    changes = {
        field: value for field, value in body.model_dump(exclude_unset=True).items()
        if value is not None or field in clearable
    }
    if not changes:
        raise HTTPException(status_code=400, detail="Send at least one field to change")
    return changes


def require_dimensions_with_shape(changes: dict[str, Any], stored_shape: Optional[str]) -> None:
    """
    Refuse a PATCH that changes a shape without sending its dimensions.
    Takes the changes and the shape the row has now.
    Returns nothing; raises a 422 shaped as FastAPI's own.
    """
    if changes.get("shape", stored_shape) != stored_shape and "dimensions" not in changes:
        raise RequestValidationError([{
            "type": "missing", "loc": ("body", "dimensions"), "msg": "dimensions are required when the shape changes",
            "input": None,
        }])


def refuse_deleted_chain(row: dict[str, Any], user: UserOut, checks: list[tuple[str, str]]) -> None:
    """
    Keep a row whose parents are soft-deleted for admins, by the deleted states the row was read with.
    Takes the row, the signed-in user and the checks, each (the column holding a parent's deleted_at, the 400's detail), in order.
    Returns nothing; raises 400 with the first matching detail for a user who isn't an admin.
    """
    for column, detail in checks:
        refuse_deleted_for_member({"deleted_at": row[column]}, user, detail)


def refuse_deleted_pir_for_member(pir: dict[str, Any], user: UserOut) -> None:
    """
    Keep a soft-deleted PIR, and a PIR of a soft-deleted property, for admins: a member who isn't one can't change it.
    Takes the PIR dict and the signed-in user.
    Returns nothing; raises 400 "PIR is deleted" or "Property is deleted" for a user who isn't an admin.
    """
    refuse_deleted_for_member(pir, user, PIR_DELETED)
    if user.role != "admin":
        owner = get_property(pir["property_id"])
        if owner is not None:
            refuse_deleted_for_member(owner, user, PROPERTY_DELETED)


def writable_pir(found: dict = Depends(pir_member), user: UserOut = Depends(no_demo_pir_writes)) -> dict:
    """
    FastAPI dependency for the writes to one PIR: the signed-in user must be on its property's project (or an admin), and not a demo user.
    Takes the PIR (loaded and checked by pir_member, so 404 and 403 come first) and the user (refused when a demo user).
    Returns the PIR dict; raises 403 for a demo user.
    """
    return found


def clean_text(value: Optional[str], field: str, max_length: int, required: bool = True) -> Optional[str]:
    """
    Read one text field off the request: trimmed, and refused when too long, or blank where it is required.
    Takes the value as sent, the field's name (for the error), its longest length and whether it must be given.
    Returns the trimmed text, or None for an optional field left blank; raises 400 otherwise.
    """
    text = (value or "").strip()
    if not text:
        if required:
            raise HTTPException(status_code=400, detail=f"{field} cannot be blank")
        return None
    if len(text) > max_length:
        raise HTTPException(status_code=400, detail=f"{field} cannot be longer than {max_length} characters")
    return text


def check_new_password(password: str, field: str) -> None:
    """
    Check a password a user is to sign in with, as typed (never trimmed).
    Takes the password and the body field it came in, for the message.
    Returns nothing; raises 400 for one under 6 characters or over 72 bytes, the most bcrypt reads.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"{field} must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(status_code=400, detail=f"{field} cannot be longer than {MAX_PASSWORD_BYTES} bytes")


def idr_list_items(rows: list[dict[str, Any]]) -> list[IdrListItem]:
    """
    Shape the rows of an IDR list, giving each the disciplines its reports cover.
    Takes the rows of list_idrs or list_review_queue, each with its report_types.
    Returns one IdrListItem per row, in the same order.
    """
    return [
        IdrListItem(**{**row, "disciplines": disciplines_for_report_types(row["report_types"])})
        for row in rows
    ]
