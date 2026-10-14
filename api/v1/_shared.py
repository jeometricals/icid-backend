"""
What more than one router needs: reading the date and discipline filters, reading a text field off a request body,
checking a new password, shaping the rows of an IDR list, and keeping soft-deleted rows for admins.
Not a router itself.
"""

from datetime import date
from typing import Any, Optional

from fastapi import HTTPException

from api.schemas.auth import UserOut
from api.schemas.idr import IdrListItem
from api.services.auth import MAX_PASSWORD_BYTES
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
