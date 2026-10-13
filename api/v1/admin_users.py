from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.users import (
    create_user,
    get_user_by_email,
    get_user_by_uuid,
    list_users_for_admin,
    update_user_fields,
    update_user_password,
)
from api.schemas.admin import (
    AdminResetPasswordRequest,
    AdminUserCreate,
    AdminUserListResponse,
    AdminUserResponse,
    AdminUserUpdate,
    UserConflict,
)
from api.schemas.auth import UserOut
from api.services.auth import DEFAULT_CLIENT_ID, current_admin, hash_password
from api.v1._shared import check_new_password

# Every route needs a signed-in admin. Its own router: /v1/admin also lets the scheduler's secret in
router = APIRouter(prefix="/v1/admin/users", tags=["Admin users"], dependencies=[Depends(current_admin)])

# The longest name the route takes
MAX_NAME_LENGTH = 100
# What sign-in takes: an email of 3 to 254 characters
MIN_EMAIL_LENGTH = 3
MAX_EMAIL_LENGTH = 254


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
    check_new_password(body.password, "password")

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


@router.patch("/{user_id}", response_model=AdminUserResponse, responses={409: {"model": UserConflict}})
def update_user(
    user_id: UUID, body: AdminUserUpdate, admin: UserOut = Depends(current_admin)
) -> AdminUserResponse | JSONResponse:
    """
    Change some of a user's first name, last name, email, admin role and demo flag. Admins only. A field left out, or sent as null, keeps its value. An admin can't take their own admin role away, and a real user can't be made a demo user.
    Takes the user's uuid as a path parameter, the fields to change in the body (names and email trimmed) and the signed-in admin.
    Returns an AdminUserResponse with the user as they now are; raises 400 when nothing is sent, a value doesn't fit or one of the two rules is broken, 403 for a user who isn't an admin, 404 for an unknown user, and 409 (with existing_user_uuid) when another user has the email, whatever its capitals. Nothing is changed unless everything can be.
    """
    first_name = _clean_name(body.first_name, "first_name")
    last_name = _clean_name(body.last_name, "last_name")
    email = _clean_email(body.email) if body.email is not None else None

    if first_name is None and last_name is None and email is None and body.is_admin is None and body.is_demo is None:
        raise HTTPException(status_code=400, detail="Send first_name, last_name, email, is_admin or is_demo")

    if body.is_admin is False and user_id == admin.uuid:
        raise HTTPException(status_code=400, detail="You cannot revoke your own admin role.")

    if body.is_demo:
        target = get_user_by_uuid(user_id)

        if target is None:
            raise HTTPException(status_code=404, detail="User not found")

        if not target["is_demo"]:
            raise HTTPException(status_code=400, detail="Cannot change a real user to a demo user.")

    row = update_user_fields(user_id, first_name, last_name, email, body.is_admin, body.is_demo)

    if row is None:
        # No such user, or the email is another user's
        if get_user_by_uuid(user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")

        holder = get_user_by_email(email) if email is not None else None

        if holder is None or holder["uuid"] == user_id:
            raise HTTPException(status_code=500, detail="Failed to update user")

        conflict = UserConflict(detail="A user with this email already exists", existing_user_uuid=holder["uuid"])
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    return AdminUserResponse(data=UserOut.model_validate(row))


@router.post("/{user_id}/password", response_model=AdminUserResponse)
def reset_user_password(user_id: UUID, body: AdminResetPasswordRequest) -> AdminUserResponse:
    """
    Give a user a new password to sign in with, replacing the one they had. Admins only. Any user, the admin themselves included.
    Takes the user's uuid as a path parameter and the new password (taken as typed) in the body.
    Returns an AdminUserResponse with the user; raises 400 for a password under 6 characters or over 72 bytes, 403 for a user who isn't an admin, and 404 for an unknown user.
    """
    check_new_password(body.new_password, "new_password")

    row = update_user_password(user_id, hash_password(body.new_password))

    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    return AdminUserResponse(data=UserOut.model_validate(row))
