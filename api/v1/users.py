from fastapi import APIRouter, HTTPException

from api.queries.users import get_all_users
from api.schemas.user import UserListItem, UserListResponse

router = APIRouter(prefix="/v1/users", tags=["Users"])


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
