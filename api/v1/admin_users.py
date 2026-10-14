import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.users import (
    count_user_references,
    create_user,
    get_user_by_email,
    get_user_by_uuid,
    list_users_for_admin,
    permanent_delete_user,
    soft_delete_user,
    undelete_user,
    update_user_fields,
    update_user_password,
)
from api.schemas.admin import (
    AdminResetPasswordRequest,
    AdminUserCreate,
    AdminUserListResponse,
    AdminUserOut,
    AdminUserResponse,
    AdminUserUpdate,
    UserConflict,
    UserHasHistory,
    UserReferences,
)
from api.schemas.auth import UserOut
from api.services.auth import DEFAULT_CLIENT_ID, current_admin, hash_password
from api.services.signatures import remove_user_signature
from api.v1._shared import check_new_password

logger = logging.getLogger(__name__)

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
def list_users(include_deleted: bool = False) -> AdminUserListResponse:
    """
    List every user, demo users included, by last name, first name and email. Admins only. Deleted users are left out unless asked for.
    Takes include_deleted as a query parameter (true: deleted users too, with deleted_at and deleted_by).
    Returns an AdminUserListResponse; raises 403 for a user who isn't an admin and 500 if the list can't be read.
    """
    rows = list_users_for_admin(include_deleted)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch users")

    return AdminUserListResponse(data=[AdminUserOut.model_validate(row) for row in rows])


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

    return AdminUserResponse(data=AdminUserOut.model_validate(row))


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

    return AdminUserResponse(data=AdminUserOut.model_validate(row))


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

    return AdminUserResponse(data=AdminUserOut.model_validate(row))


def _has_history(references: dict[str, int]) -> JSONResponse:
    """
    Build the 409 for a user who can't be deleted for good.
    Takes the counts of the rows that name them.
    Returns the response, with those counts.
    """
    refused = UserHasHistory(
        detail="User has history and can't be permanently deleted", references=UserReferences(**references)
    )
    return JSONResponse(status_code=409, content=refused.model_dump(mode="json"))


@router.post("/{user_id}/delete", response_model=AdminUserResponse)
def delete_user(user_id: UUID, admin: UserOut = Depends(current_admin)) -> AdminUserResponse:
    """
    Soft-delete a user: they are marked deleted, with when and by whom, and can no longer sign in or use a token they hold. Nothing of theirs changes: their IDRs, reviews, roles and signature stay, and their name still shows where they acted. Admins only; an admin can't delete themselves.
    Takes the user's uuid as a path parameter and the signed-in admin.
    Returns an AdminUserResponse with the deleted user; raises 400 for the admin's own account, 403 for a user who isn't an admin, and 404 for an unknown user or one already deleted.
    """
    if user_id == admin.uuid:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")

    row = soft_delete_user(user_id, admin.uuid)

    if row is None:
        missing = get_user_by_uuid(user_id) is None
        raise HTTPException(status_code=404, detail="User not found" if missing else "User is already deleted")

    return AdminUserResponse(data=AdminUserOut.model_validate(row))


@router.post("/{user_id}/undelete", response_model=AdminUserResponse, responses={409: {"model": UserConflict}})
def undelete_user_account(user_id: UUID) -> AdminUserResponse | JSONResponse:
    """
    Bring a soft-deleted user back: they can sign in again, with the password they had. Admins only.
    Takes the user's uuid as a path parameter.
    Returns an AdminUserResponse with the restored user; raises 403 for a user who isn't an admin, 404 for an unknown user or one who isn't deleted, and 409 (with existing_user_uuid) when another user now has their email, whatever its capitals.
    """
    target = get_user_by_uuid(user_id)

    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if target["deleted_at"] is None:
        raise HTTPException(status_code=404, detail="User is not deleted")

    row = undelete_user(user_id)

    if row is None:
        # Their email went to someone else while they were deleted, or they were restored meanwhile
        holder = get_user_by_email(target["email"])

        if holder is None or holder["uuid"] == user_id:
            raise HTTPException(status_code=404, detail="User is not deleted")

        conflict = UserConflict(detail="Email is now in use by another user", existing_user_uuid=holder["uuid"])
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    return AdminUserResponse(data=AdminUserOut.model_validate(row))


@router.delete(
    "/{user_id}/permanent", status_code=204, response_model=None, responses={409: {"model": UserHasHistory}}
)
def permanently_delete_user(user_id: UUID, admin: UserOut = Depends(current_admin)) -> Optional[JSONResponse]:
    """
    Delete a soft-deleted user for good, then their signature file, best-effort. Only a user nothing names: no IDR reported, reviewed or deleted, no project role, no audit row, field edit, attachment or property created or deleted. It can't be undone. Admins only; a user must be soft-deleted first.
    Takes the user's uuid as a path parameter and the signed-in admin.
    Returns nothing (204); raises 403 for a user who isn't an admin, 404 for an unknown user or one who isn't soft-deleted, 409 (with references, the counts of what names them) for a user with history, and 500 if that can't be counted.
    """
    target = get_user_by_uuid(user_id)

    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if target["deleted_at"] is None:
        raise HTTPException(status_code=404, detail="User must be soft-deleted first")

    references = count_user_references(user_id)

    if references is None:
        raise HTTPException(status_code=500, detail="Failed to check the user's history")

    if any(references.values()):
        return _has_history(references)

    if not permanent_delete_user(user_id):
        # Restored, or named by a new row, between the checks and the delete
        references = count_user_references(user_id)

        if references is not None and any(references.values()):
            return _has_history(references)

        raise HTTPException(status_code=404, detail="User must be soft-deleted first")

    remove_user_signature(user_id)
    # Nothing in the database remembers the user now, so this line is the record of who removed them
    logger.info(
        "User permanently deleted: user_uuid=%s email=%s admin=%s at=%s",
        user_id, target["email"], admin.uuid, datetime.now(timezone.utc).isoformat(),
    )
