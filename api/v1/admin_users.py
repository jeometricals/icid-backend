from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.queries.users import list_users_for_admin, update_user_names
from api.schemas.admin import AdminUserListResponse, AdminUserResponse, AdminUserUpdate
from api.schemas.auth import UserOut
from api.services.auth import current_admin

# Every route needs a signed-in admin. Its own router: /v1/admin also lets the scheduler's secret in
router = APIRouter(prefix="/v1/admin/users", tags=["Admin users"], dependencies=[Depends(current_admin)])

# The longest name the route takes
MAX_NAME_LENGTH = 100


def _clean_name(value: Optional[str], field: str) -> Optional[str]:
    """
    Read one name off the request: trimmed, and refused when blank or too long.
    Takes the value as sent (None when the field was left out) and the field's name, for the error.
    Returns the trimmed name, or None for a field left out; raises 400 for a blank or over-long one.
    """
    if value is None:
        return None
    name = value.strip()
    if not name:
        raise HTTPException(status_code=400, detail=f"{field} cannot be blank")
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(status_code=400, detail=f"{field} cannot be longer than {MAX_NAME_LENGTH} characters")
    return name


@router.get("", response_model=AdminUserListResponse)
def list_users() -> AdminUserListResponse:
    """
    List every user, demo users included, by last name, first name and email. Admins only.
    Takes nothing.
    Returns an AdminUserListResponse; raises 403 for a user who isn't an admin and 500 if the list can't be read.
    """
    rows = list_users_for_admin()

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch users")

    return AdminUserListResponse(data=[UserOut.model_validate(row) for row in rows])


@router.patch("/{user_id}", response_model=AdminUserResponse)
def update_user(user_id: UUID, body: AdminUserUpdate) -> AdminUserResponse:
    """
    Change a user's first name, last name or both. Admins only. A field left out, or sent as null, keeps its value.
    Takes the user's uuid as a path parameter and the names in the body.
    Returns an AdminUserResponse with the user as they now are; raises 400 when neither name is sent or one is blank or over 100 characters, 403 for a user who isn't an admin, and 404 for an unknown user.
    """
    first_name = _clean_name(body.first_name, "first_name")
    last_name = _clean_name(body.last_name, "last_name")

    if first_name is None and last_name is None:
        raise HTTPException(status_code=400, detail="Send first_name, last_name or both")

    row = update_user_names(user_id, first_name, last_name)

    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    return AdminUserResponse(data=UserOut.model_validate(row))
