from fastapi import APIRouter, Depends, HTTPException

from api.queries.users import get_all_users, update_user_password
from api.schemas.auth import UserOut
from api.schemas.user import ChangeOwnPasswordRequest, PasswordChangeResponse, UserListItem, UserListResponse
from api.services.auth import auth_provider, current_user, hash_password, no_demo_users
from api.v1._shared import check_new_password

# Every route needs a signed-in user (any role) who isn't a demo user
router = APIRouter(prefix="/v1/users", tags=["Users"], dependencies=[Depends(current_user), Depends(no_demo_users)])


@router.get("/", response_model=UserListResponse)
def list_all_users() -> UserListResponse:
    """
    Return every user in the system.
    Takes no arguments.
    Returns a UserListResponse wrapping the list of users.
    """
    rows = get_all_users()

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch users")

    data = [
        UserListItem(
            user_id=str(row["user_id"]),
            email=row["email"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            phone_number=row["phone_number"],
            employer=row["employer"],
        )
        for row in rows
    ]

    return UserListResponse(
        status="success",
        message="List of all users",
        data=data,
    )


@router.post("/me/password", response_model=PasswordChangeResponse)
def change_own_password(body: ChangeOwnPasswordRequest, user: UserOut = Depends(current_user)) -> PasswordChangeResponse:
    """
    Change the signed-in user's password. Tokens already issued stay valid until they expire.
    Takes the current password and the new one (both as typed) in the body, and the current user.
    Returns a PasswordChangeResponse; raises 400 for a blank or wrong current password, a new one under 6 characters or over 72 bytes, or one the same as the current.
    """
    if not body.current_password:
        raise HTTPException(status_code=400, detail="current_password cannot be blank")
    check_new_password(body.new_password, "new_password")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="New password must differ from current")

    verified = auth_provider.verify_credentials(user.email, body.current_password)
    if verified is None or verified.uuid != user.uuid:
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if update_user_password(user.uuid, hash_password(body.new_password)) is None:
        raise HTTPException(status_code=500, detail="Failed to change password")

    return PasswordChangeResponse(status="success", message="Password changed")
