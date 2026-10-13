from pydantic import BaseModel
from typing import Optional


class UserListItem(BaseModel):
    user_id: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    employer: Optional[str] = None


class UserListResponse(BaseModel):
    status: str
    message: str
    data: list[UserListItem]


class ChangeOwnPasswordRequest(BaseModel):
    """
    A user changing their own password: the one they have and the one they want. The route checks both itself, so
    a bad one is a 400.
    """

    current_password: str
    new_password: str


class PasswordChangeResponse(BaseModel):
    """The answer to a password change: nothing about the user or the password comes back."""

    status: str
    message: str
    data: None = None
