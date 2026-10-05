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
