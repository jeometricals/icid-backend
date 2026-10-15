from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from api.schemas.auth import UserOut


class DemoCleanupResult(BaseModel):
    """How many abandoned demo users a cleanup run deleted."""

    purged_count: int


class AdminUserUpdate(BaseModel):
    """
    What an admin is changing on a user. Any field may be left out, and a null counts as left out; the route
    checks the values itself, so a bad one is a 400.
    """

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    # True makes the user an admin, False takes the role away.
    is_admin: Optional[bool] = None
    # False makes a demo user a real one. True is only ever a no-op, on a user who is already a demo user.
    is_demo: Optional[bool] = None


class AdminUserCreate(BaseModel):
    """
    A user an admin is creating. The email is a plain string, as at sign-in: accounts live on reserved domains
    (e.g. @icid.local). The route checks every value itself, so a bad one is a 400.
    """

    email: str
    first_name: str
    last_name: Optional[str] = None
    password: str
    is_admin: bool = False


class UserConflict(BaseModel):
    """The 409 body when an email is taken: the reason, and the user who has it."""

    detail: str
    existing_user_uuid: UUID


class AdminUserOut(UserOut):
    """A user as an admin sees them: with when they were deleted and by whom, both None for a user who isn't."""

    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class AdminUserResponse(BaseModel):
    """One user, as an admin sees them."""

    data: AdminUserOut


class AdminUserListResponse(BaseModel):
    """Every user, as an admin sees them."""

    data: list[AdminUserOut]


class UserReferences(BaseModel):
    """How many rows name a user: what stops the user being deleted for good."""

    idrs: int
    project_users: int
    audit: int
    field_edits: int
    attachments: int
    properties: int
    task_orders: int
    task_order_properties: int
    pirs: int
    flags: int
    pir_pages: int
    non_assessable_areas: int
    features: int
    segments: int
    idr_flag_attributions: int


class UserHasHistory(BaseModel):
    """The 409 body when a user can't be deleted for good: the reason, and what still names them."""

    detail: str
    references: UserReferences


class AdminResetPasswordRequest(BaseModel):
    """The password an admin is giving a user. The route checks its length itself, so a bad one is a 400."""

    new_password: str
