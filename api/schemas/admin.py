from typing import Optional

from pydantic import BaseModel

from api.schemas.auth import UserOut


class DemoCleanupResult(BaseModel):
    """How many abandoned demo users a cleanup run deleted."""

    purged_count: int


class AdminUserUpdate(BaseModel):
    """
    The names an admin is changing on a user. Either may be left out; the route checks their length itself, so a
    bad one is a 400.
    """

    first_name: Optional[str] = None
    last_name: Optional[str] = None


class AdminUserResponse(BaseModel):
    """One user, as an admin sees them."""

    data: UserOut


class AdminUserListResponse(BaseModel):
    """Every user, as an admin sees them."""

    data: list[UserOut]
