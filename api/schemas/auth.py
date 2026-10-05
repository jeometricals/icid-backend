from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class UserOut(BaseModel):
    """A signed-in user, as the API returns them and as the auth service passes them around."""

    uuid: UUID
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[str] = None
    is_demo: bool


class UserWithHash(UserOut):
    """A user with their password hash, for checking credentials only. Never returned by an endpoint."""

    password_hash: Optional[str] = None


class LoginRequest(BaseModel):
    """
    Sign-in credentials. The email is a plain string, not EmailStr: accounts live on reserved domains
    (e.g. @icid.local) that EmailStr rejects.
    """

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=6, max_length=128)


class LoginResponse(BaseModel):
    """A bearer token, its lifetime in seconds, and the user it belongs to."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut
