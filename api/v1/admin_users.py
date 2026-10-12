from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.users import create_user, get_user_by_email, list_users_for_admin, update_user_names
from api.schemas.admin import (
    AdminUserCreate,
    AdminUserListResponse,
    AdminUserResponse,
    AdminUserUpdate,
    UserConflict,
)
from api.schemas.auth import UserOut
from api.services.auth import DEFAULT_CLIENT_ID, MAX_PASSWORD_BYTES, current_admin, hash_password

# Every route needs a signed-in admin. Its own router: /v1/admin also lets the scheduler's secret in
router = APIRouter(prefix="/v1/admin/users", tags=["Admin users"], dependencies=[Depends(current_admin)])

# The longest name the route takes
MAX_NAME_LENGTH = 100
# What sign-in takes: an email of 3 to 254 characters and a password of at least 6
MIN_EMAIL_LENGTH = 3
MAX_EMAIL_LENGTH = 254
MIN_PASSWORD_LENGTH = 6


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


def _clean_email(value: str) -> str:
    """
    Read a new user's email off the request: trimmed, its capitals kept, and refused when it can't be one.
    Takes the value as sent.
    Returns the trimmed email; raises 400 for a blank one, one without an @, or one too short or too long.
    """
    email = value.strip()
    if not email:
        raise HTTPException(status_code=400, detail="email cannot be blank")
    if len(email) > MAX_EMAIL_LENGTH:
        raise HTTPException(status_code=400, detail=f"email cannot be longer than {MAX_EMAIL_LENGTH} characters")
    if len(email) < MIN_EMAIL_LENGTH or "@" not in email:
        raise HTTPException(status_code=400, detail="email is not a valid email address")
    return email


def _check_password(password: str) -> None:
    """
    Check a new user's password, as typed (never trimmed).
    Takes the password.
    Returns nothing; raises 400 for one under 6 characters or over 72 bytes, the most bcrypt reads.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(status_code=400, detail=f"password cannot be longer than {MAX_PASSWORD_BYTES} bytes")


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


@router.post("", response_model=AdminUserResponse, status_code=201, responses={409: {"model": UserConflict}})
def create_user_account(body: AdminUserCreate) -> AdminUserResponse | JSONResponse:
    """
    Create a user who signs in with the password given. Admins only. The user is an admin when is_admin is true, never a demo user, and is on no project until given a role on one.
    Takes the email (stored as typed, trimmed), first name, optional last name (blank is none), password and is_admin in the body.
    Returns 201 with an AdminUserResponse; raises 400 for a value that doesn't fit, 403 for a user who isn't an admin, and 409 (with existing_user_uuid) when a user already has the email, whatever its capitals.
    """
    email = _clean_email(body.email)
    first_name = _clean_name(body.first_name, "first_name")
    last_name = _clean_name(body.last_name, "last_name") if (body.last_name or "").strip() else None
    _check_password(body.password)

    row = create_user(
        email, first_name, last_name, hash_password(body.password), "admin" if body.is_admin else None, DEFAULT_CLIENT_ID
    )

    if row is None:
        existing = get_user_by_email(email)

        if existing is None:
            raise HTTPException(status_code=500, detail="Failed to create user")

        conflict = UserConflict(detail="A user with this email already exists", existing_user_uuid=existing["uuid"])
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    return AdminUserResponse(data=UserOut.model_validate(row))


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
